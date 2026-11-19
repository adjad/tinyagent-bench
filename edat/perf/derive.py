"""Derivations for the sustained-use tests, for the aggregator's ``extra_derivers``
(spec 04 §9, §9.1–§9.4, §3.12).

Every deriver has the signature ``fn(doc, verdicts_by_entry) -> None`` and adds fields to the
verdict dicts in place:

* ``doc`` is the §3 run document (``samples`` already one line per id, ``events``,
  ``energy_windows``, ``attachments``), plus ``_run_dir``: the directory that holds the
  attachments (power traces, ``battery-<run_id>.jsonl``, ``simday-<run_id>.jsonl``).
* ``verdicts_by_entry`` maps a key to a verdict dict. The entry is the verdict's
  ``entry_id`` (else the key), and when the verdict has a ``mode`` only that mode's samples
  are used.

Energy comes from the run's power trace: the attachment of kind ``power_trace`` (an
``smc_pstr`` one when there are several) next to ``_run_dir``. With no trace every energy
field is null and a ``*_reason`` says why.

Fields added (all additive to §3.12):

* ``s1_sustained``: ``{<power_state>: {initial_decode_tps, sustained_decode_tps, stability,
  time_to_throttle_s, per_minute: [{minute, decode_tps, n, thermal, power_W}], thermal_trace,
  n, duration_s}}``. Initial = median decode tok/s of requests submitted in the first 2 min;
  sustained = of requests submitted in the last 5 min before the run's last ``t_end``;
  stability = sustained / initial; time to throttle = the start (s) of the first minute
  whose median is below 90 % of initial (null if none); per-minute power = trace mean.
* ``s2_battery``: %/h and Wh/h of the test period, the control period and their difference
  (test − control), and % per 100 tasks = (test drop − control rate × test hours) / tasks ×
  100, tasks = bursts whose steps all have an ok status.
* ``s3_idle_residency``: ``energy.idle_resident`` over the ABBA windows (trace mean power per
  window, one sample period trimmed from each end: :func:`idle_mean_W`), Δ footprint (median of each window's start/end mean, resident − unloaded) and the
  wake request's TTFT/TTFA.
* ``reload_after_idle``: ``{reload_ttfa_s, reload_s, e_reload_J, weights_cached_share, …}``;
  ``e_reload_J`` = median active energy of the reload requests − median of the warm requests
  (each from its own window's idle bracket).
* ``long_session``: :func:`edat.perf.longsession.derive_long_session` per session, medians
  over sessions (``leak_flag`` true if any session leaks), the sessions under ``sessions``.
* ``sim_day`` / ``sim_day_unload_policy``: :func:`edat.perf.simday.compose_day` over the
  compressed days, validated by a real-time day when there is one. Full-charge Wh comes from
  ``device.battery_nominal_Wh`` or a ``full_charge_Wh`` in the conditions.
* ``within_budget_rate``, ``median_wall_s_correct`` and ``within_budget_by_test``
  (``sim_day_event`` / ``sim_day_realtime`` jobs, ``screen_task``, ``sidekick_task``, and
  ``doc["tasks"]`` rows when present).

Medians are ``statistics.median`` (the mean of the middle two at even n), not the
nearest-rank median of ``edat.perf.stats``; the counts here are small and mostly odd.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Iterable

from .constants import OK_STATUSES, S1_INITIAL_MIN, S1_SUSTAINED_LAST_MIN, THERMAL_STATES, THROTTLE_FRACTION
from .energy import active_J, idle_bracket, idle_resident, integrate_J, mean_power_W, window_energy
from .longsession import derive_long_session, turn_energies
from .power import PowerTrace, read_trace
from .simday import compose_day
from .sustained import time_budget, ttfa_s, ttft_s

_NS = 1_000_000_000
_TRACE_CACHE: dict[tuple, PowerTrace] = {}


# ----------------------------------------------------------------------------- inputs

def _run_dir(doc: dict) -> Path | None:
    d = doc.get("_run_dir")
    return Path(d) if d else None


def load_trace(doc: dict) -> tuple[PowerTrace | None, str | None]:
    d = _run_dir(doc)
    if d is None:
        return None, "no_run_dir"
    names = [a["name"] for a in doc.get("attachments") or () if a.get("kind") == "power_trace"]
    if not names:
        return None, "no_power_trace_attachment"
    names.sort(key=lambda n: (0 if "smc_pstr" in n else 1, n))
    for n in names:
        p = d / n
        if p.exists():
            key = (str(p), p.stat().st_mtime_ns, p.stat().st_size)
            if key not in _TRACE_CACHE:
                _TRACE_CACHE.clear()
                _TRACE_CACHE[key] = read_trace(p)
            return _TRACE_CACHE[key], None
    return None, "power_trace_file_missing"


def side_rows(doc: dict, kind: str, prefix: str) -> list[dict]:
    d = _run_dir(doc)
    if d is None:
        return []
    names = [a["name"] for a in doc.get("attachments") or () if a.get("kind") == kind]
    if not names and doc.get("run_id"):
        names = [f"{prefix}-{doc['run_id']}.jsonl"]
    out: list[dict] = []
    for n in names:
        p = d / n
        if p.exists():
            for ln in p.read_text().splitlines():
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    break
    return out


def targets(verdicts_by_entry: dict) -> Iterable[tuple[dict, str, str | None]]:
    for key, v in verdicts_by_entry.items():
        yield v, v.get("entry_id", key), v.get("mode")


def samples_for(doc: dict, eid: str, mode: str | None, test: str) -> list[dict]:
    return [s for s in doc.get("samples") or () if s.get("test") == test and s.get("entry_id") == eid
            and (mode is None or s.get("mode") == mode)]


def _windows(doc: dict) -> dict[str, dict]:
    return {w["window_id"]: w for w in doc.get("energy_windows") or () if w.get("window_id")}


def _ok(s: dict) -> bool:
    return s.get("status") in OK_STATUSES


def decode_tps(s: dict) -> float | None:
    a, b, n = s.get("t_first_token_ns"), s.get("t_last_token_ns"), s.get("output_tokens")
    if not (isinstance(a, int) and isinstance(b, int) and isinstance(n, int)) or b <= a or n < 2:
        return None
    return (n - 1) / ((b - a) / _NS)


def _median(xs: Iterable) -> float | None:
    v = [x for x in xs if x is not None]
    return statistics.median(v) if v else None


def sample_active_J(trace: PowerTrace | None, s: dict, window: dict | None) -> float | None:
    """A request's active energy over [t_submit, t_end] above its window's idle bracket."""
    if trace is None or window is None or s.get("t_submit_ns") is None or s.get("t_end_ns") is None:
        return None
    if s["t_end_ns"] <= s["t_submit_ns"]:
        return None
    try:
        p = idle_bracket(trace, window["t_start_ns"], window["t_end_ns"], window.get("idle_window_s") or 60.0)
        return active_J(trace, s["t_submit_ns"], s["t_end_ns"], p["p_idle_W"])
    except ValueError:
        return None


def idle_mean_W(trace: PowerTrace, w: dict) -> float:
    """Mean power of an idle window, one sample period (``1 / rate_hz``) trimmed from each end so a
    reading taken exactly at an edge (the load or request that follows) does not leak in."""
    pad = int(_NS / float(w.get("rate_hz") or 1.0))
    a, b = w["t_start_ns"], w["t_end_ns"]
    if b - a > 4 * pad:
        a, b = a + pad, b - pad
    return mean_power_W(trace, a, b)


# ----------------------------------------------------------------------------- S1

def s1_summary(group: list[dict], trace: PowerTrace | None) -> dict:
    ss = sorted((s for s in group if _ok(s) and s.get("t_submit_ns") is not None), key=lambda s: s["t_submit_ns"])
    if not ss:
        return {"n": 0, "reason": "no_ok_samples"}
    t0 = ss[0]["t_submit_ns"]
    t_end = max(s.get("t_end_ns") or s["t_submit_ns"] for s in ss)
    rel = [(s["t_submit_ns"] - t0) / _NS for s in ss]
    tps = [decode_tps(s) for s in ss]
    initial = _median(x for r, x in zip(rel, tps) if r < S1_INITIAL_MIN * 60)
    cut = t_end - S1_SUSTAINED_LAST_MIN * 60 * _NS
    sustained = _median(x for s, x in zip(ss, tps) if s["t_submit_ns"] >= cut)
    minutes: dict[int, list[int]] = {}
    for k, r in enumerate(rel):
        minutes.setdefault(int(r // 60), []).append(k)
    per_minute, throttle = [], None
    for m in sorted(minutes):
        ks = minutes[m]
        med = _median(tps[k] for k in ks)
        states = [ss[k].get("thermal_end") for k in ks if ss[k].get("thermal_end") in THERMAL_STATES]
        pw = None
        if trace is not None:
            a, b = t0 + m * 60 * _NS, min(t0 + (m + 1) * 60 * _NS, t_end)
            if b > a:
                try:
                    pw = mean_power_W(trace, a, b)
                except ValueError:
                    pw = None
        per_minute.append({"minute": m, "decode_tps": med, "n": len(ks), "power_W": pw,
                           "thermal": max(states, key=THERMAL_STATES.index) if states else None})
        if throttle is None and initial and med is not None and med < THROTTLE_FRACTION * initial:
            throttle = m * 60.0
    trace_th, last = [], None
    for r, s in zip(rel, ss):
        st = s.get("thermal_end")
        if st != last:
            trace_th.append([r, st])
            last = st
    dur = (t_end - t0) / _NS
    out = {"initial_decode_tps": initial, "sustained_decode_tps": sustained,
           "stability": sustained / initial if (initial and sustained is not None) else None,
           "time_to_throttle_s": throttle, "per_minute": per_minute, "thermal_trace": trace_th, "n": len(ss),
           "duration_s": dur, "flags": []}
    if dur < (S1_INITIAL_MIN + S1_SUSTAINED_LAST_MIN) * 60:
        out["flags"].append("initial_and_sustained_overlap")
    if trace is None:
        out["power_reason"] = "no_power_trace"
    return out


def derive_s1(doc: dict, verdicts_by_entry: dict) -> None:
    trace, _ = load_trace(doc)
    for v, eid, mode in targets(verdicts_by_entry):
        ss = samples_for(doc, eid, mode, "sustained")
        if not ss:
            continue
        groups: dict[str, list[dict]] = {}
        for s in ss:
            groups.setdefault(s.get("power_state") or "unknown", []).append(s)
        v["s1_sustained"] = {k: s1_summary(g, trace) for k, g in sorted(groups.items())}


# ----------------------------------------------------------------------------- S2

def _period(rows: list[dict]) -> dict:
    rows = sorted((r for r in rows if r.get("level") is not None), key=lambda r: r["t_ns"])
    if len(rows) < 2:
        return {"hours": None, "drop_pct": None, "pct_per_h": None, "wh_per_h": None, "n_readings": len(rows)}
    a, b = rows[0], rows[-1]
    h = (b["t_ns"] - a["t_ns"]) / (3600 * _NS)
    drop = (a["level"] - b["level"]) * 100.0
    wh = None
    if a.get("remaining_Wh") is not None and b.get("remaining_Wh") is not None and h > 0:
        wh = (a["remaining_Wh"] - b["remaining_Wh"]) / h
    return {"hours": h, "drop_pct": drop, "pct_per_h": drop / h if h > 0 else None, "wh_per_h": wh,
            "n_readings": len(rows)}


def derive_s2(doc: dict, verdicts_by_entry: dict) -> None:
    rows = side_rows(doc, "battery_readings", "battery")
    for v, eid, mode in targets(verdicts_by_entry):
        ss = samples_for(doc, eid, mode, "battery")
        mine = [r for r in rows if r.get("entry_id") == eid and (mode is None or r.get("mode") == mode)]
        test = _period([r for r in mine if r.get("phase") == "test"])
        ctrl = _period([r for r in mine if r.get("phase") == "control"])
        if not ss and test["n_readings"] == 0:
            continue
        bursts: dict[Any, list[dict]] = {}
        for s in ss:
            bursts.setdefault(s.get("rep"), []).append(s)
        n_tasks = sum(1 for b in bursts.values() if b and all(_ok(s) for s in b))
        out = {"pct_per_h_test": test["pct_per_h"], "pct_per_h_control": ctrl["pct_per_h"],
               "wh_per_h_test": test["wh_per_h"], "wh_per_h_control": ctrl["wh_per_h"],
               "hours_test": test["hours"], "hours_control": ctrl["hours"], "tasks": n_tasks,
               "source": "battery_accumulator", "flags": ["level_step_quantisation"]}
        out["pct_per_h"] = (test["pct_per_h"] - ctrl["pct_per_h"]) if (
            test["pct_per_h"] is not None and ctrl["pct_per_h"] is not None) else None
        out["wh_per_h"] = (test["wh_per_h"] - ctrl["wh_per_h"]) if (
            test["wh_per_h"] is not None and ctrl["wh_per_h"] is not None) else None
        if test["drop_pct"] is not None and ctrl["pct_per_h"] is not None and n_tasks:
            out["pct_per_100_tasks"] = (test["drop_pct"] - ctrl["pct_per_h"] * test["hours"]) / n_tasks * 100.0
        else:
            out["pct_per_100_tasks"] = None
            out["pct_per_100_tasks_reason"] = "no_control" if ctrl["pct_per_h"] is None else "no_tasks_or_readings"
        if out["wh_per_h"] is None:
            out["wh_per_h_reason"] = "remaining_Wh_missing"
        v["s2_battery"] = out


# ----------------------------------------------------------------------------- S3

def derive_s3(doc: dict, verdicts_by_entry: dict) -> None:
    trace, reason = load_trace(doc)
    for v, eid, mode in targets(verdicts_by_entry):
        ws = [w for w in doc.get("energy_windows") or () if w.get("purpose") == "s3" and w.get("entry_id") == eid
              and (mode is None or w.get("mode") == mode)]
        if not ws:
            continue
        out: dict[str, Any] = {}
        if trace is not None:
            rows = []
            for w in ws:
                try:
                    rows.append({"state": w["state"], "t_start_ns": w["t_start_ns"], "mean_W": idle_mean_W(trace, w)})
                except ValueError:
                    pass
            try:
                out.update(idle_resident(rows))
            except ValueError:
                out["power_reason"] = "windows_outside_trace"
        else:
            out.update({"delta_W": None, "p_idle_resident_W": None, "p_idle_unloaded_W": None,
                        "power_reason": reason})
        fp: dict[str, list[float]] = {}
        for w in ws:
            vals = [x for x in (w.get("footprint_start_bytes"), w.get("footprint_end_bytes")) if x is not None]
            if vals:
                fp.setdefault(w["state"], []).append(statistics.fmean(vals))
        r, u = _median(fp.get("resident", ())), _median(fp.get("unloaded", ()))
        out.update({"footprint_resident_bytes": r, "footprint_unloaded_bytes": u,
                    "delta_footprint_bytes": (r - u) if (r is not None and u is not None) else None,
                    "order": "".join("R" if w["state"] == "resident" else "U"
                                     for w in sorted(ws, key=lambda w: w["t_start_ns"]))})
        wake = [s for s in samples_for(doc, eid, mode, "wake") if _ok(s)]
        out["wake_ttft_s"] = _median(ttft_s(s) for s in wake)
        out["wake_ttfa_s"] = _median(ttfa_s(s) for s in wake)
        v["s3_idle_residency"] = out


# ----------------------------------------------------------------------------- reload after idle

def reload_verdict(samples: list[dict], trace: PowerTrace | None, windows: dict[str, dict]) -> dict:
    rel = [s for s in samples if s.get("phase") == "measured" and _ok(s)]
    warm = [s for s in samples if s.get("phase") == "warmup" and _ok(s)]
    confirmed = [s for s in rel if s.get("reloaded")]
    e_rel = [sample_active_J(trace, s, windows.get(s.get("energy_window_id"))) for s in confirmed]
    e_warm = [sample_active_J(trace, s, windows.get(s.get("energy_window_id"))) for s in warm]
    mr, mw = _median(e_rel), _median(e_warm)
    cached = [s["weights_cached"] for s in confirmed if s.get("weights_cached") is not None]
    out = {"reload_ttfa_s": _median(ttfa_s(s) for s in confirmed),
           "reload_s": None,
           "e_reload_J": (mr - mw) if (mr is not None and mw is not None) else None,
           "e_reload_request_J": mr, "e_warm_request_J": mw,
           "e_reload_basis": "reload request active energy minus median warm request, same level",
           "weights_cached_share": (sum(cached) / len(cached)) if cached else None,
           "reps": len(rel), "reloads_confirmed": len(confirmed),
           "reload_ms_basis": next((s.get("reload_ms_basis") for s in confirmed if s.get("reload_ms_basis")), None)}
    rm = _median(s.get("reload_ms") for s in confirmed)
    out["reload_s"] = rm / 1000.0 if rm is not None else None
    if out["e_reload_J"] is None:
        out["e_reload_reason"] = "no_power_trace" if trace is None else "missing_window_energy"
    return out


def derive_reload(doc: dict, verdicts_by_entry: dict) -> None:
    trace, _ = load_trace(doc)
    wins = _windows(doc)
    for v, eid, mode in targets(verdicts_by_entry):
        ss = samples_for(doc, eid, mode, "reload_after_idle")
        if ss:
            v["reload_after_idle"] = reload_verdict(ss, trace, wins)


# ----------------------------------------------------------------------------- long session

def derive_long_session_verdicts(doc: dict, verdicts_by_entry: dict) -> None:
    trace, reason = load_trace(doc)
    for v, eid, mode in targets(verdicts_by_entry):
        ss = samples_for(doc, eid, mode, "long_session_turn")
        if not ss:
            continue
        ws = [w for w in doc.get("energy_windows") or () if w.get("purpose") == "long_session"
              and w.get("entry_id") == eid and (mode is None or w.get("mode") == mode)]
        en = turn_energies(trace, ws) if trace is not None else {}
        by_rep: dict[Any, list[dict]] = {}
        for s in ss:
            by_rep.setdefault(s.get("rep"), []).append(s)
        sessions = [derive_long_session(g, en) for _, g in sorted(by_rep.items(), key=lambda kv: str(kv[0]))]
        if len(sessions) == 1:
            out = dict(sessions[0])
        else:
            out = {k: _median(x.get(k) for x in sessions) for k in
                   ("trim_ttft_cost_s", "trim_energy_cost_J", "plateau_footprint_bytes")}
            flags = [x["leak_flag"] for x in sessions if x.get("leak_flag") is not None]
            out.update({"leak_flag": any(flags) if flags else None, "window_tokens": sessions[0]["window_tokens"],
                        "turns": sum(x["turns"] for x in sessions), "trims": sum(x["trims"] for x in sessions),
                        "ttft_by_context": [p for x in sessions for p in x["ttft_by_context"]],
                        "decode_by_context": [p for x in sessions for p in x["decode_by_context"]]})
        out["sessions"] = sessions
        if trace is None:
            out["energy_reason"] = reason
        v["long_session"] = out


# ----------------------------------------------------------------------------- simulated day

def _full_charge(doc: dict) -> float | None:
    dev = doc.get("device") or {}
    if dev.get("battery_nominal_Wh"):
        return float(dev["battery_nominal_Wh"])
    for k in ("conditions_start", "conditions_end", "conditions_extra"):
        c = doc.get(k) or {}
        if c.get("full_charge_Wh"):
            return float(c["full_charge_Wh"])
    return None


def sim_day_records(doc: dict) -> list[dict]:
    rows = side_rows(doc, "sim_day_record", "simday")
    if rows:
        return rows
    for k in ("conditions_extra", "conditions_end", "conditions_start"):
        recs = (doc.get(k) or {}).get("sim_days")
        if recs:
            return list(recs)
    return []


def day_from_record(rec: dict, doc: dict, trace: PowerTrace | None) -> dict:
    """The input :func:`edat.perf.simday.compose_day` takes, measured from the trace."""
    wins = _windows(doc)
    by_id = {s["sample_id"]: s for s in doc.get("samples") or ()}
    events = []
    for e in rec.get("events") or ():
        w = wins.get(e.get("window_id"))
        act = aux = None
        if trace is not None and w is not None:
            try:
                act = window_energy(trace, w)["energy_active_J"]
            except ValueError:
                act = None
            aux = sum(x for x in (sample_active_J(trace, by_id[i], w) for i in e.get("aux_sample_ids") or ()
                                  if i in by_id) if x is not None)
        events.append({"task_id": e["task_id"], "script_time_s": e["script_time_s"], "wall_s": e.get("wall_s"),
                       "active_J": act, "aux_J": aux, "completed": e.get("completed"),
                       "event_kind": e.get("event_kind")})
    p_res = p_unl = None
    if trace is not None and rec.get("idle_windows"):
        rows = []
        for iw in rec["idle_windows"]:
            try:
                rate = (wins.get(iw.get("window_id")) or {}).get("rate_hz") or 1.0
                rows.append({"state": iw["state"], "t_start_ns": iw["t_start_ns"],
                             "mean_W": idle_mean_W(trace, {**iw, "rate_hz": rate})})
            except ValueError:
                pass
        try:
            ir = idle_resident(rows)
            p_res, p_unl = ir["p_idle_resident_W"], ir["p_idle_unloaded_W"]
        except ValueError:
            pass
    return {"run_id": rec.get("run_id"), "day_id": rec.get("day_id"), "day_length_s": rec["day_length_s"],
            "p_idle_resident_W": p_res, "p_idle_unloaded_W": p_unl, "events": events,
            "partial": rec.get("partial")}


def realtime_from_record(rec: dict, doc: dict, trace: PowerTrace | None) -> dict:
    meas = None
    if trace is not None and rec.get("t_day_start_ns") and rec.get("t_day_end_ns"):
        try:
            meas = integrate_J(trace, rec["t_day_start_ns"], rec["t_day_end_ns"]) / 3600.0
        except ValueError:
            meas = None
    rows = sorted((r for r in side_rows(doc, "battery_readings", "battery")
                   if r.get("day_id") == rec.get("day_id") and r.get("level") is not None), key=lambda r: r["t_ns"])
    pct = (rows[0]["level"] - rows[-1]["level"]) * 100.0 if len(rows) >= 2 else None
    return {"run_id": rec.get("run_id"), "measured_total_Wh": meas, "battery_pct_used_measured": pct,
            "partial": bool(rec.get("partial"))}


def derive_sim_day(doc: dict, verdicts_by_entry: dict) -> None:
    recs = sim_day_records(doc)
    if not recs:
        return
    trace, reason = load_trace(doc)
    full = _full_charge(doc)
    for v, eid, mode in targets(verdicts_by_entry):
        mine = [r for r in recs if r.get("entry_id") == eid and (mode is None or r.get("mode") == mode)]
        comp = [day_from_record(r, doc, trace) for r in mine if r.get("day_mode") == "compressed"]
        rt = [realtime_from_record(r, doc, trace) for r in mine if r.get("day_mode") == "realtime"]
        if not comp:
            continue
        out = compose_day(comp, model_label=v.get("model_id") or eid, full_charge_Wh=full,
                          reload=v.get("reload_after_idle"), realtime=rt[0] if rt else None)
        for k in out:
            if trace is None:
                out[k]["energy_reason"] = reason
            if full is None:
                out[k]["full_charge_reason"] = "full_charge_Wh_unavailable"
        v.update(out)


# ----------------------------------------------------------------------------- time budget

def budget_rows(doc: dict, eid: str, mode: str | None) -> list[dict]:
    rows: list[dict] = []
    for rec in sim_day_records(doc):
        if rec.get("entry_id") != eid or (mode is not None and rec.get("mode") != mode):
            continue
        test = "sim_day_realtime" if rec.get("day_mode") == "realtime" else "sim_day_event"
        rows += [{**t, "test": test} for t in rec.get("tasks") or ()]
    for s in doc.get("samples") or ():
        if s.get("test") not in ("screen_task", "sidekick_task") or s.get("entry_id") != eid:
            continue
        if mode is not None and s.get("mode") not in (None, mode):
            continue
        wall = s.get("wall_s")
        if wall is None and s.get("t_submit_ns") is not None and s.get("t_end_ns") is not None:
            wall = (s["t_end_ns"] - s["t_submit_ns"]) / _NS
        rows.append({"task_id": s.get("task_id"), "test": s["test"], "level": s.get("screen_level"),
                     "mode": s.get("sidekick_mode"), "completed": s.get("completed"), "wall_s": wall})
    for t in doc.get("tasks") or ():
        if t.get("entry_id") in (None, eid):
            rows.append(dict(t))
    return rows


def derive_time_budget(doc: dict, verdicts_by_entry: dict) -> None:
    for v, eid, mode in targets(verdicts_by_entry):
        rows = budget_rows(doc, eid, mode)
        if not rows:
            continue
        tb = time_budget(rows)
        v["within_budget_rate"] = tb["within_budget_rate"]
        v["median_wall_s_correct"] = tb["median_wall_s_correct"]
        v["within_budget_by_test"] = tb["by_test"]
        v["within_budget_detail"] = {k: tb[k] for k in ("n_tasks", "n_completion_known", "n_completed_within_budget",
                                                        "unbudgeted", "per_kind", "budget_scope")}


EXTRA_DERIVERS = (derive_s1, derive_s2, derive_s3, derive_reload, derive_long_session_verdicts, derive_sim_day,
                  derive_time_budget)
