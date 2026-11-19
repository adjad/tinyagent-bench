"""The simulated day: battery and energy for a day of use (spec 04 §9.1, D16; script from 02 §11.4).

Script (``edat-sim-day-v1.json``, written by the agentic spec's exporter)::

    {schema: "edat-sim-day-v1", lang: "en", profile_id, day_start: "08:00", day_end: "18:00", tz,
     mode: "forced", modes: [...], jobs: {...}, totals: {...}, missing: [...], compressed: {...},
     events: [{t: "08:22:00", kind: brief | task | mail_digest | message_digest | review,
               ref: <case id> | "job:<kind>" | null, family: "select" | ... | "abstain",
               cluster, world_events_before: [...]}]}

Mapping choices (recorded in every loaded script as ``mapping``)
-----------------------------------------------------------------
* 02 kind → 04 ``event_kind``: ``brief`` → ``brief``; ``review`` → ``brief`` (an end-of-day
  summary is a brief in 04's vocabulary); ``mail_digest`` / ``message_digest`` → ``digest``;
  ``task`` → ``question`` when its ``family`` (or ``task_kind``) is ``abstain`` (02's plain
  questions), else ``action``.
* → §9.4 budget kind: ``brief``/``review`` → ``brief``; digests → ``digest``; ``task`` with
  family ``abstain`` → ``question``, ``select`` → ``one_tool``, every other family (or none)
  → ``multi_step``.
* ``ref: null`` means the case does not exist yet: the job is still scheduled and run (the
  stand-in executor does not need the case), and the event carries ``ref_missing: true``.
* ``task_id`` = the event's ``task_id`` if given, else ``"<index>:<ref>"`` (``"<index>:<kind>"``
  when ``ref`` is null), so repeated refs (``job:mail_digest``) stay distinct.
* ``day_end`` defaults to ``day_start`` + 10 h (02 §11.4's 08:00–18:00).

Measurement choices
-------------------
* **Compressed**: events in script order, each in its own 20 Hz energy window. §9.1 says
  "idle bracketed per §4.3" (60 s each side) and also "gaps cut to a fixed 30 s"; those
  contradict. Here the 30 s gaps **are** the brackets: the gap after event *i* is both event
  *i*'s after-bracket and event *i+1*'s before-bracket (window ``idle_window_s`` = 30), with
  one 30 s gap before the first event and after the last. Then the two 30-minute idle
  windows (``P_idle_resident`` with the model loaded, ``P_idle_unloaded`` with no model loaded
  and the engine running) in ABBA order at 1 Hz, each after a ``settle_s`` wait outside the
  window. Samples use test ``sim_day_event`` / ``sim_day_idle``.
* **Realtime**: the runner waits until each event's scripted time on the run clock (1 Hz
  while idle, 20 Hz from 60 s before an event to 60 s after it, which are its bracket), runs
  it (test ``sim_day_realtime``), then waits out the day. The battery is read once a minute;
  at or below 15 % the day stops and is marked ``partial``.
* The cooldown gate is off inside the day (the schedule is the experiment).
* ``task_start`` / ``task_end`` events carry ``t_ns`` on the run clock so power samples line
  up; every sample carries ``event_kind``, ``script_time_s`` and ``task_id``; after a job its
  samples are rewritten with ``completed``, ``wall_budget_s`` and ``within_budget``.
* ``sleep_prevented: true``, ``unload_policy`` (and ``idle_unload_minutes``) go into
  ``runner.conditions_extra``. Each day's record (event list, window ids, idle windows,
  partial flag) is appended to ``simday-<run_id>.jsonl`` (attachment kind ``sim_day_record``)
  and to ``runner.conditions_extra["sim_days"]``.
* **Completion** (02 §11.3): a job counts as completed only if the same model passed that kind
  of task in the agentic suite. The caller passes a table ``{case id | family | 02 kind |
  event_kind | budget kind: bool}``; the most specific key found wins; else ``null``.

Composition (:func:`compose_day`), following §9.1 literally
-----------------------------------------------------------
For one day with events ``e`` (scripted start ``s_e``, measured wall time ``w_e``, active
energy ``E_e``, of which aux ``A_e``) and the day's length ``D``:

* gaps: before the first event ``s_0``; between events ``s_{i+1} − (s_i + w_i)``; after the
  last ``D − (s_n + w_n)`` (negative gaps, overlapping events, are clamped to 0 and flagged);
* ``dP = P_idle_resident − P_idle_unloaded``;
* ``resident``: idle energy = Σ g × dP;
* ``unload_after_idle`` (T = 5 min): a gap g > T contributes T × dP, plus ``E_reload`` when an
  event follows it (the last gap of the day has no reload); a gap ≤ T contributes g × dP;
* ``day_wh_model`` = (Σ E_e + idle energy + reload energy) / 3600;
* ``day_wh_total`` = ``day_wh_model`` + ``P_idle_unloaded`` × D / 3600 (the unloaded baseline over
  the whole scripted day). Read literally, event time adds only its active energy, so the
  resident premium during events, dP × Σ w_e, is in neither; it is reported apart as
  ``event_time_resident_premium_Wh``;
* ``battery_pct_used_model`` = ``day_wh_model`` / full-charge Wh × 100;
  ``share_of_full_battery_pct`` = ``day_wh_total`` / full-charge Wh × 100 (the whole Mac's day,
  the field §3.12 lists beside it without a definition: this is the choice made);
* ``idle_share``, ``active_share`` (and ``reload_share``) = parts of ``day_wh_model`` in %;
  ``aux_share`` = Σ A_e / model energy in % (a part of ``active_share``, §5.7 rule 4);
* ``j_per_completed_task`` = (Σ E_e − Σ A_e) / tasks completed (every scripted job counts as a
  task event, §9.1's "active energy of the task events ÷ completed tasks");
* repetitions: the median over days of each figure, with ``range`` [min, max];
* ``validation`` (resident only, when a real-time day is given): ``error_pct`` = (composed
  ``day_wh_total`` − measured total) / measured × 100, ``pass`` = |error| ≤ 10 %. A partial
  real-time day gives ``error_pct: null, pass: null, partial: true`` (§9.1: then the composed
  method is used).
* ``sentence``: §9.1's reader quote with N, M, N′ filled in (plain text; ``sentence_md`` has the
  quote's bold). N < 10 is printed with one decimal, else rounded to a whole number.
"""
from __future__ import annotations

import json
import os
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

from .constants import (IDLE_RATE_HZ, IDLE_UNLOAD_MINUTES, IDLE_WINDOW_S, PSTR_RATE_HZ, SIM_DAY_GAP_S,
                        SIM_DAY_IDLE_WINDOW_S, SIM_DAY_SCHEMA, SIM_DAY_VALIDATION_TOLERANCE)
from . import schedule, sustained
from .sustained import BatteryLog, begin_window, end_window, idle_window, replay_job, rewrite, time_budget

_NS = 1_000_000_000
DEVICE_LABEL = "the reference MacBook Pro"
REALTIME_STOP_LEVEL = 0.15
COMPRESSED_STOP_LEVEL = 0.20

MAPPING = {
    "event_kind": {"brief": "brief", "review": "brief", "mail_digest": "digest", "message_digest": "digest",
                   "task": "question if family == 'abstain' else action"},
    "budget_kind": {"brief": "brief", "review": "brief", "mail_digest": "digest", "message_digest": "digest",
                    "task": "abstain -> question, select -> one_tool, other families -> multi_step"},
    "note": "choices of the 04 runner (edat/perf/simday.py); review counts as a brief",
}
KINDS_02 = ("brief", "task", "mail_digest", "message_digest", "review")


def _hms(t: str) -> float:
    parts = [float(x) for x in str(t).split(":")]
    while len(parts) < 3:
        parts.append(0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def map_event(kind: str, family: str | None) -> tuple[str, str]:
    """(04 event_kind, §9.4 budget kind) for a 02 event kind and task family."""
    if kind not in KINDS_02:
        raise ValueError(f"unknown sim-day event kind {kind!r}")
    if kind in ("brief", "review"):
        return "brief", "brief"
    if kind in ("mail_digest", "message_digest"):
        return "digest", "digest"
    fam = (family or "").strip().lower()
    if fam in ("abstain", "question"):
        return "question", "question"
    if fam in ("select", "one_tool"):
        return "action", "one_tool"
    return "action", "multi_step"


def load_script(path_or_doc) -> dict:
    """Read and normalise an ``edat-sim-day-v1`` script (schema and ``lang: "en"`` checked)."""
    if isinstance(path_or_doc, dict):
        doc = path_or_doc
    else:
        with open(path_or_doc, encoding="utf-8") as f:
            doc = json.load(f)
    if doc.get("schema") != SIM_DAY_SCHEMA:
        raise ValueError(f"not an {SIM_DAY_SCHEMA} script: schema {doc.get('schema')!r}")
    if doc.get("lang") != "en":
        raise ValueError(f"sim-day script lang must be 'en', got {doc.get('lang')!r}")
    day0 = _hms(doc.get("day_start") or "08:00")
    day_end = _hms(doc["day_end"]) if doc.get("day_end") else day0 + 10 * 3600
    events = []
    for i, e in enumerate(doc.get("events") or ()):
        kind = e.get("kind")
        family = e.get("family") or e.get("task_kind")
        ek, bk = map_event(kind, family)
        ref = e.get("ref")
        events.append({
            "index": i, "t": e.get("t"), "script_time_s": _hms(e["t"]) - day0, "kind": kind,
            "event_kind": ek, "budget_kind": bk, "family": family, "ref": ref, "ref_missing": ref is None,
            "cluster": e.get("cluster"), "task_id": e.get("task_id") or f"{i:02d}:{ref if ref else kind}",
            "world_events_before": list(e.get("world_events_before") or ()),
        })
    events.sort(key=lambda x: (x["script_time_s"], x["index"]))
    if any(ev["script_time_s"] < 0 for ev in events):
        raise ValueError("an event is scheduled before day_start")
    return {"schema": doc["schema"], "lang": doc["lang"], "profile_id": doc.get("profile_id"),
            "day_start": doc.get("day_start") or "08:00", "day_end": doc.get("day_end"), "tz": doc.get("tz"),
            "script_mode": doc.get("mode"), "day_length_s": day_end - day0, "events": events,
            "totals": doc.get("totals"), "missing": doc.get("missing"), "mapping": MAPPING,
            "n_ref_missing": sum(1 for ev in events if ev["ref_missing"])}


# ----------------------------------------------------------------------------- executors

@dataclass
class TaskResult:
    task_id: str
    kind: str                      # the §9.4 budget kind
    completed: bool | None
    sample_ids: list[str]
    wall_s: float | None
    aux_sample_ids: list[str] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)
    executor: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("samples")
        return d


class TaskExecutor(Protocol):
    def run(self, event: dict, runner, entry, mode: str, *, test: str = "sim_day_event") -> TaskResult: ...


def event_fields(event: dict) -> dict:
    """Sample fields every sample of a scripted job carries."""
    return {"event_kind": event.get("event_kind"), "script_time_s": event.get("script_time_s"),
            "task_id": event.get("task_id")}


def lookup_completion(table: dict | None, event: dict) -> bool | None:
    if not table:
        return None
    for key in (event.get("ref"), event.get("family"), event.get("kind"), event.get("event_kind"),
                event.get("budget_kind")):
        if key is not None and key in table:
            v = table[key]
            return None if v is None else bool(v)
    return None


def wall_s(samples: list[dict]) -> float | None:
    t0 = [s["t_submit_ns"] for s in samples if s.get("t_submit_ns") is not None]
    t1 = [s["t_end_ns"] for s in samples if s.get("t_end_ns") is not None]
    return (max(t1) - min(t0)) / _NS if t0 and t1 else None


class ReplayExecutor:
    """Stand-in job: one teacher-forced 6-step agent replay from the pack (02's ``forced``
    mode) with the cache on and its own replay variant. Labelled ``executor: "replay-standin"``."""

    name = "replay-standin"

    def __init__(self, completion: dict | None = None):
        self.completion = completion

    def run(self, event: dict, runner, entry, mode: str, *, test: str = "sim_day_event") -> TaskResult:
        ss = replay_job(runner, entry, mode, test=test, rep=event.get("index", 0) + 1,
                        extra={**event_fields(event), "executor": self.name})
        return TaskResult(task_id=event["task_id"], kind=event["budget_kind"],
                          completed=lookup_completion(self.completion, event),
                          sample_ids=[s["sample_id"] for s in ss], wall_s=wall_s(ss), samples=ss,
                          executor=self.name)


# ----------------------------------------------------------------------------- running a day

class _Rate:
    def __init__(self, runner):
        self.runner = runner
        self.cur: float | None = None

    def set(self, hz: float) -> None:
        if hz != self.cur:
            self.runner.i.power.set_rate(hz)
            self.cur = hz


def _finish_job(runner, ev: dict, res: TaskResult, window: dict, t_start: int) -> dict:
    tb = {"task_id": res.task_id, "kind": res.kind, "completed": res.completed, "wall_s": res.wall_s}
    time_budget([tb])
    for s in res.samples:
        s.update({"completed": res.completed, "wall_budget_s": tb["wall_budget_s"],
                  "within_budget": tb["within_budget"]})
        rewrite(runner, s)
    rec = {**{k: ev.get(k) for k in ("index", "t", "script_time_s", "event_kind", "budget_kind", "family",
                                     "ref", "ref_missing", "cluster")},
           **res.to_dict(), "script_kind": ev.get("kind"), "window_id": window["window_id"], "t_start_ns": t_start,
           "wall_budget_s": tb["wall_budget_s"], "within_budget": tb["within_budget"]}
    runner.w.event(runner.now(), "task_end", {"task_id": res.task_id, "completed": res.completed,
                                              "wall_s": res.wall_s, "sample_ids": res.sample_ids,
                                              "window_id": window["window_id"]})
    return rec


def _run_event(runner, entry, mode, ev: dict, executor, *, test: str, idle_window_s: float, day_id: str) -> dict:
    t0 = runner.now()
    runner.w.event(t0, "task_start", {"task_id": ev["task_id"], "event_kind": ev["event_kind"],
                                      "script_time_s": ev["script_time_s"], "day_id": day_id})
    begin_window(runner, "simday")
    res = executor.run(ev, runner, entry, mode, test=test)
    w = end_window(runner, idle_window_s=idle_window_s,
                   extra={"purpose": test, "day_id": day_id, "task_id": ev["task_id"],
                          "event_kind": ev["event_kind"], "script_time_s": ev["script_time_s"],
                          "entry_id": entry.entry_id, "mode": mode,
                          "aux_sample_ids": list(res.aux_sample_ids)})
    return _finish_job(runner, ev, res, w, t0)


def _write_record(runner, rec: dict) -> None:
    name = f"simday-{runner.w.run_id}.jsonl"
    with open(runner.w.dir / name, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
    runner.w.attach(name, "sim_day_record")
    runner.conditions_extra.setdefault("sim_days", []).append(rec)


def run_day(runner, entry, mode: str, script, executor: TaskExecutor, *, mode_: str = "compressed",
            gap_s: float = SIM_DAY_GAP_S, idle_window_s: float = SIM_DAY_IDLE_WINDOW_S,
            unload_policy: str = "resident", idle_unload_minutes: float = IDLE_UNLOAD_MINUTES,
            read_battery: Callable[[], dict | float] | None = None, settle_s: float = IDLE_WINDOW_S,
            bracket_s: float = IDLE_WINDOW_S, rep: int = 1, stop_level: float | None = None) -> dict:
    """Run one simulated day (see the module docstring). Returns the day record."""
    if mode_ not in ("compressed", "realtime"):
        raise ValueError(mode_)
    if unload_policy not in ("resident", "unload_after_idle"):
        raise ValueError(unload_policy)
    if not isinstance(script, dict) or "day_length_s" not in script:
        script = load_script(script)
    if runner.i.power is None:
        raise RuntimeError("the simulated day needs a power sampler")
    n = len(runner.conditions_extra.get("sim_days", ())) + 1
    day_id = f"day{n:02d}-{mode_}"
    runner.conditions_extra["sleep_prevented"] = True
    runner.conditions_extra["unload_policy"] = unload_policy
    if unload_policy == "unload_after_idle":
        runner.conditions_extra["idle_unload_minutes"] = idle_unload_minutes
    log = BatteryLog(runner, read_battery) if read_battery is not None else None
    ctx = {"entry_id": entry.entry_id, "mode": mode, "day_id": day_id}
    rec: dict[str, Any] = {"day_id": day_id, "run_id": runner.w.run_id, "entry_id": entry.entry_id,
                           "mode": mode, "rep": rep, "day_mode": mode_, "unload_policy": unload_policy,
                           "executor": getattr(executor, "name", type(executor).__name__),
                           "profile_id": script.get("profile_id"), "script_mode": script.get("script_mode"),
                           "day_length_s": script["day_length_s"], "gap_s": gap_s if mode_ == "compressed" else None,
                           "events": [], "idle_windows": [], "partial": False, "battery_log": log.name if log else None}
    runner.select(entry, mode)
    rate = _Rate(runner)
    if mode_ == "compressed":
        stop = COMPRESSED_STOP_LEVEL if stop_level is None else stop_level
        rate.set(PSTR_RATE_HZ)
        rec["t_day_start_ns"] = runner.now()
        sustained._sleep(runner, gap_s)
        for ev in script["events"]:
            if log is not None:
                lv = log.read("sim_day", **ctx).get("level")
                if lv is not None and lv < stop:
                    rec["partial"] = True
                    runner.w.event(runner.now(), "operator_note", f"{day_id}: battery {lv:.2f} below {stop}, stopped")
                    break
            rec["events"].append(_run_event(runner, entry, mode, ev, executor, test="sim_day_event",
                                            idle_window_s=gap_s, day_id=day_id))
            sustained._sleep(runner, gap_s)
        rec["t_active_end_ns"] = runner.now()
        for k, state in enumerate(schedule.abba("resident", "unloaded")):
            if state == "resident":
                runner.select(entry, mode)
            else:
                sustained.unload_all(runner)
            sustained._sleep(runner, settle_s)
            w = idle_window(runner, idle_window_s, f"P_idle_{state}", sample_test="sim_day_idle", entry=entry,
                            mode=mode, extra={"purpose": "sim_day_idle", "state": state, "abba_index": k,
                                              "day_id": day_id, "event_kind": "idle"})
            rec["idle_windows"].append({"window_id": w["window_id"], "state": state, "t_start_ns": w["t_start_ns"],
                                        "t_end_ns": w["t_end_ns"]})
            if log is not None:
                log.read("sim_day_idle", **ctx)
        rec["t_day_end_ns"] = runner.now()
    else:
        stop = REALTIME_STOP_LEVEL if stop_level is None else stop_level
        t_day0 = runner.now()
        rec["t_day_start_ns"] = t_day0
        state = {"next_read": t_day0, "minute": 0}
        post_until = t_day0

        def wait_until(target: int, hi_from: int) -> bool:
            while True:
                t = runner.now()
                if log is not None and t >= state["next_read"]:
                    lv = log.read("sim_day_realtime", minute=state["minute"], **ctx).get("level")
                    state["minute"] += 1
                    state["next_read"] = t_day0 + state["minute"] * 60 * _NS
                    if lv is not None and lv <= stop:
                        return True
                if t >= target:
                    return False
                rate.set(PSTR_RATE_HZ if (t < post_until or t >= hi_from) else IDLE_RATE_HZ)
                bounds = [target] + [b for b in (post_until, hi_from) if b > t]
                if log is not None:
                    bounds.append(state["next_read"])
                sustained._sleep(runner, (min(bounds) - t) / _NS)

        for ev in script["events"]:
            target = t_day0 + int(ev["script_time_s"] * _NS)
            if wait_until(target, target - int(bracket_s * _NS)):
                rec["partial"] = True
                break
            rate.set(PSTR_RATE_HZ)
            late = (runner.now() - target) / _NS
            r = _run_event(runner, entry, mode, ev, executor, test="sim_day_realtime", idle_window_s=bracket_s,
                           day_id=day_id)
            r["late_s"] = late
            rec["events"].append(r)
            post_until = runner.now() + int(bracket_s * _NS)
        if not rec["partial"]:
            end = t_day0 + int(script["day_length_s"] * _NS)
            if wait_until(end, end + 1):
                rec["partial"] = True
        rate.set(PSTR_RATE_HZ)
        rec["t_day_end_ns"] = runner.now()
        if rec["partial"]:
            runner.w.event(runner.now(), "operator_note", f"{day_id}: stopped at battery <= {stop:.0%}, partial day")
    rec["tasks"] = [{"task_id": e["task_id"], "kind": e["budget_kind"],
                     "completed": e["completed"], "wall_s": e["wall_s"]} for e in rec["events"]]
    _write_record(runner, rec)
    return rec


# ----------------------------------------------------------------------------- composition

def _gaps(day: dict) -> tuple[list[float], bool]:
    ev = sorted(day["events"], key=lambda e: e["script_time_s"])
    D = day["day_length_s"]
    gaps, overlap = [], False
    prev_end = 0.0
    for e in ev:
        g = e["script_time_s"] - prev_end
        if g < 0:
            overlap = True
        gaps.append(max(0.0, g))
        prev_end = e["script_time_s"] + (e.get("wall_s") or 0.0)
    g = D - prev_end
    if g < 0:
        overlap = True
    gaps.append(max(0.0, g))
    return gaps, overlap


def compose_one(day: dict, *, policy: str, T_s: float, e_reload_J: float | None, reload_s: float | None,
                full_charge_Wh: float | None) -> dict:
    """One day under one policy (see the module docstring for every formula)."""
    flags: list[str] = []
    ev = sorted(day["events"], key=lambda e: e["script_time_s"])
    p_res, p_unl = day.get("p_idle_resident_W"), day.get("p_idle_unloaded_W")
    gaps, overlap = _gaps(day)
    if overlap:
        flags.append("events_overlap_script_times")
    energies = [e.get("active_J") for e in ev]
    out: dict[str, Any] = {"policy": policy, "flags": flags, "reloads": 0, "tasks_attempted": len(ev),
                           "tasks_completed": sum(1 for e in ev if e.get("completed")),
                           "idle_s": sum(gaps), "day_length_s": day["day_length_s"]}
    wall_answer = []
    reloads = 0
    idle_J = reload_J = 0.0
    dP = (p_res - p_unl) if (p_res is not None and p_unl is not None) else None
    for i, g in enumerate(gaps):
        has_next = i < len(ev)
        if policy == "unload_after_idle" and g > T_s:
            if dP is not None:
                idle_J += T_s * dP
            if has_next:
                reloads += 1
                reload_J += e_reload_J if e_reload_J is not None else 0.0
        elif dP is not None:
            idle_J += g * dP
        if has_next:
            w = ev[i].get("wall_s")
            add = reload_s if (policy == "unload_after_idle" and g > T_s) else 0.0
            wall_answer.append([ev[i]["task_id"], (w + (add or 0.0)) if w is not None else None])
    out["reloads"] = reloads
    out["wall_time_to_answer_s"] = wall_answer
    reasons = []
    if any(x is None for x in energies):
        reasons.append("event_energy_missing")
    if dP is None:
        reasons.append("idle_power_missing")
    if policy == "unload_after_idle" and reloads and e_reload_J is None:
        reasons.append("e_reload_missing")
    if policy == "unload_after_idle" and reloads and reload_s is None:
        flags.append("reload_s_missing")
    if reasons:
        out.update({k: None for k in ("day_wh_model", "day_wh_total", "battery_pct_used_model",
                                      "share_of_full_battery_pct", "j_per_completed_task", "idle_share",
                                      "active_share", "aux_share", "reload_share", "event_time_resident_premium_Wh")})
        out["reason"] = ",".join(reasons)
    else:
        events_J = sum(energies)
        aux_J = sum(e.get("aux_J") or 0.0 for e in ev)
        model_J = events_J + idle_J + reload_J
        wh_model = model_J / 3600.0
        wh_total = wh_model + p_unl * day["day_length_s"] / 3600.0
        out.update({
            "day_wh_model": wh_model, "day_wh_total": wh_total,
            "event_energy_J": events_J, "idle_energy_J": idle_J, "reload_energy_J": reload_J, "aux_energy_J": aux_J,
            "idle_share": 100.0 * idle_J / model_J if model_J else None,
            "active_share": 100.0 * events_J / model_J if model_J else None,
            "reload_share": 100.0 * reload_J / model_J if model_J else None,
            "aux_share": 100.0 * aux_J / model_J if model_J else None,
            "j_per_completed_task": (events_J - aux_J) / out["tasks_completed"] if out["tasks_completed"] else None,
            "event_time_resident_premium_Wh": dP * sum(e.get("wall_s") or 0.0 for e in ev) / 3600.0,
            "battery_pct_used_model": 100.0 * wh_model / full_charge_Wh if full_charge_Wh else None,
            "share_of_full_battery_pct": 100.0 * wh_total / full_charge_Wh if full_charge_Wh else None,
        })
        if not out["tasks_completed"]:
            out["j_per_completed_task_reason"] = ("completion_unknown" if all(e.get("completed") is None for e in ev)
                                                  else "no_task_completed")
        if not full_charge_Wh:
            out["battery_pct_reason"] = "full_charge_Wh_unavailable"
    out["p_idle_resident_W"] = dP
    out["p_idle_resident_raw_W"] = p_res
    out["p_idle_unloaded_W"] = p_unl
    return out


_MEDIAN_FIELDS = ("day_wh_model", "day_wh_total", "battery_pct_used_model", "share_of_full_battery_pct",
                  "j_per_completed_task", "p_idle_resident_W", "p_idle_resident_raw_W", "p_idle_unloaded_W",
                  "idle_share", "active_share", "aux_share", "reload_share", "tasks_completed", "tasks_attempted",
                  "reloads", "event_time_resident_premium_Wh", "aux_energy_J", "idle_s")


def _about(x: float) -> str:
    return f"{x:.0f}" if abs(x) >= 10 else f"{x:.1f}"


def _merge(per_day: list[dict], policy: str) -> dict:
    out: dict[str, Any] = {"policy": policy, "reps": len(per_day), "range": {}, "per_rep": per_day}
    for k in _MEDIAN_FIELDS:
        vals = [d.get(k) for d in per_day if d.get(k) is not None]
        out[k] = statistics.median(vals) if vals else None
        if vals and k in ("day_wh_model", "day_wh_total", "battery_pct_used_model", "share_of_full_battery_pct"):
            out["range"][k] = [min(vals), max(vals)]
    reasons = sorted({d["reason"] for d in per_day if d.get("reason")})
    if reasons and out["day_wh_model"] is None:
        out["reason"] = ",".join(reasons)
    out["flags"] = sorted({f for d in per_day for f in d.get("flags", ())})
    out["battery_pct_used_measured"] = None
    out["validation"] = None
    return out


def compose_day(days: list[dict], *, model_label: str, full_charge_Wh: float | None = None,
                T_s: float = IDLE_UNLOAD_MINUTES * 60, reload: dict | None = None, realtime: dict | None = None,
                device_label: str = DEVICE_LABEL) -> dict:
    """The §3.12 ``sim_day`` (policy ``resident``) and ``sim_day_unload_policy`` verdict objects.

    ``days``: compressed repetitions, each ``{run_id, day_length_s, p_idle_resident_W (raw),
    p_idle_unloaded_W, events: [{task_id, script_time_s, wall_s, active_J, aux_J, completed}]}``.
    ``reload``: the §3.12 ``reload_after_idle`` verdict (``e_reload_J``, ``reload_s``).
    ``realtime``: ``{run_id, measured_total_Wh, battery_pct_used_measured, partial}``."""
    e_rel = (reload or {}).get("e_reload_J")
    rel_s = (reload or {}).get("reload_s")
    res = _merge([compose_one(d, policy="resident", T_s=T_s, e_reload_J=None, reload_s=None,
                              full_charge_Wh=full_charge_Wh) for d in days], "resident")
    unl = _merge([compose_one(d, policy="unload_after_idle", T_s=T_s, e_reload_J=e_rel, reload_s=rel_s,
                              full_charge_Wh=full_charge_Wh) for d in days], "unload_after_idle")
    unl["idle_unload_minutes"] = T_s / 60.0
    unl["e_reload_J"] = e_rel
    if realtime is not None:
        meas = realtime.get("measured_total_Wh")
        partial = bool(realtime.get("partial"))
        res["battery_pct_used_measured"] = realtime.get("battery_pct_used_measured")
        if partial or not meas or res["day_wh_total"] is None:
            res["validation"] = {"run_id": realtime.get("run_id"), "error_pct": None, "pass": None,
                                 "partial": partial, "measured_total_Wh": meas}
        else:
            err = 100.0 * (res["day_wh_total"] - meas) / meas
            res["validation"] = {"run_id": realtime.get("run_id"), "error_pct": err,
                                 "pass": abs(err) <= 100.0 * SIM_DAY_VALIDATION_TOLERANCE, "partial": False,
                                 "measured_total_Wh": meas}
            if not res["validation"]["pass"]:
                res["flags"] = sorted(set(res["flags"]) | {"composed_method_failed_validation"})
    N, M, N2 = res["battery_pct_used_model"], res["idle_share"], unl["battery_pct_used_model"]
    if N is None or M is None:
        sentence = md = None
        res["sentence_reason"] = res.get("reason") or res.get("battery_pct_reason") or "battery_pct_unavailable"
    else:
        tail = (f" Under Harbor's unload-after-{T_s / 60:g}-minutes setting it would use about {_about(N2)} %."
                if N2 is not None else "")
        sentence = (f"One simulated day with {model_label} uses about {_about(N)} % of a full battery on "
                    f"{device_label}, beyond what the Mac uses with no model loaded. {_about(M)} % of that is "
                    f"idle time with the model resident.{tail}")
        md = (f"**One simulated day with {model_label} uses about {_about(N)} % of a full battery** on "
              f"{device_label}, beyond what the Mac uses with no model loaded. **{_about(M)} %** of that is "
              f"idle time with the model resident.{tail}")
    res["sentence"] = unl["sentence"] = sentence
    res["sentence_md"] = unl["sentence_md"] = md
    return {"sim_day": res, "sim_day_unload_policy": unl}
