"""Energy maths on power traces and samples (spec 04 section 4, 5.7 rule 4, 9, 9.1, 2.7).

Pure functions. A *trace* is a ``edat.perf.power.PowerTrace``; a *sample* is a dict with the section 3.6
fields; a *window* is a dict with the section 3.8 fields. Times are mach_continuous_time ns.

Spec ambiguities settled here
-----------------------------
* ``error_bound``: spec 04 section 4.3 writes ``sqrt(edge^2 + idle_drift^2 + source^2)`` with
  ``edge = 2*P*dt/(K*window)`` and ``idle_drift = |P_before - P_after|*window/2``. The terms mix units
  (a fraction, joules, a fraction). They are implemented as RELATIVE errors:
  ``edge_rel = 2*P*dt / (K*E_req)`` (energy misplaced at the two edges of a request, averaged over K),
  ``idle_rel = |P_before - P_after|*window_s/2 / E_active`` and ``source_rel = source_frac``.
* The idle drift is ``|before - after| / p_idle`` (relative to the mean of the two medians).
* A request is usable when its status is in OK_STATUSES and ``cached_tokens`` is 0 or null.
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right
from typing import Any, Iterable, Sequence

from edat.perf.constants import (BATTERY_CROSSCHECK_FLAG, ENERGY_PAIR_K, ENERGY_PAIR_K_LONG,
                                 ENERGY_PAIR_K_LONG_ABOVE, IDLE_DRIFT_FLAG, IDLE_WINDOW_S,
                                 OK_STATUSES, PSTR_SOURCE_ERROR, SHAPE_A_TOKENS, SHAPE_B_TOKENS,
                                 SHAPE_C_RESIDUAL_FLAG, SHAPE_C_TOKENS)
from edat.perf.power import PowerTrace

_NS = 1e9


# --------------------------------------------------------------------------- integration

def _power_at(ts: Sequence[int], ws: Sequence[float], t: int) -> float:
    if t <= ts[0]:
        return ws[0]
    if t >= ts[-1]:
        return ws[-1]
    i = bisect_right(ts, t)          # ts[i-1] <= t < ts[i]
    t_a, t_b = ts[i - 1], ts[i]
    if t == t_a:
        return ws[i - 1]
    return ws[i - 1] + (ws[i] - ws[i - 1]) * (t - t_a) / (t_b - t_a)


def integrate_J(trace: PowerTrace, t0_ns: int, t1_ns: int) -> float:
    """Trapezoid integral of the trace over [t0, t1] in joules, linearly interpolated at both edges.
    If a window edge lies beyond the trace's first or last reading, the nearest reading is held
    (zero-order hold). Raises ValueError when the window and the trace do not overlap."""
    if t1_ns <= t0_ns:
        raise ValueError("window end must be after its start")
    ts, ws = trace.t_ns, trace.watts
    if not ts or t1_ns <= ts[0] or t0_ns >= ts[-1]:
        raise ValueError("window does not overlap the trace")
    pts = [(t0_ns, _power_at(ts, ws, t0_ns))]
    lo, hi = bisect_right(ts, t0_ns), bisect_left(ts, t1_ns)
    pts.extend((ts[i], ws[i]) for i in range(lo, hi))
    pts.append((t1_ns, _power_at(ts, ws, t1_ns)))
    return sum(0.5 * (a[1] + b[1]) * (b[0] - a[0]) / _NS for a, b in zip(pts, pts[1:]))


def mean_power_W(trace: PowerTrace, t0_ns: int, t1_ns: int) -> float:
    """Time-weighted mean power over the window."""
    return integrate_J(trace, t0_ns, t1_ns) / ((t1_ns - t0_ns) / _NS)


def median_power_W(trace: PowerTrace, t0_ns: int, t1_ns: int) -> float:
    """Median of the readings inside [t0, t1]. Raises ValueError if there are none."""
    lo, hi = bisect_left(trace.t_ns, t0_ns), bisect_right(trace.t_ns, t1_ns)
    if hi <= lo:
        raise ValueError("no readings in the window")
    return statistics.median(trace.watts[lo:hi])


# --------------------------------------------------------------------------- idle

def idle_bracket(trace: PowerTrace, t_start_ns: int, t_end_ns: int,
                 idle_s: float = IDLE_WINDOW_S) -> dict[str, Any]:
    """Idle power from ``idle_s`` before and after a window (spec 04 section 4.3). ``p_idle_W`` is the
    mean of the two medians; if they differ by more than 15 % of it, ``idle_drift_flag`` is set. If one
    side has no readings the other is used alone and ``idle_one_sided`` is flagged."""
    span = int(idle_s * _NS)
    sides: dict[str, float | None] = {}
    for name, (a, b) in (("before", (t_start_ns - span, t_start_ns)),
                         ("after", (t_end_ns, t_end_ns + span))):
        try:
            sides[name] = median_power_W(trace, a, b)
        except ValueError:
            sides[name] = None
    before, after = sides["before"], sides["after"]
    if before is None and after is None:
        raise ValueError("no idle readings before or after the window")
    flags: list[str] = []
    if before is None or after is None:
        p_idle = before if after is None else after
        drift = 0.0
        flags.append("idle_one_sided")
    else:
        p_idle = (before + after) / 2.0
        drift = abs(before - after) / p_idle if p_idle else math.inf
    drift_flag = drift > IDLE_DRIFT_FLAG
    if drift_flag:
        flags.append("idle_drift")
    return {"idle_before_W": before, "idle_after_W": after, "p_idle_W": p_idle,
            "idle_drift_frac": drift, "idle_drift_flag": drift_flag,
            "idle_one_sided": "idle_one_sided" in flags, "idle_window_s": idle_s, "flags": flags}


def active_J(trace: PowerTrace, t0_ns: int, t1_ns: int, p_idle_W: float) -> float:
    """Energy over the window minus ``p_idle_W`` x duration."""
    return integrate_J(trace, t0_ns, t1_ns) - p_idle_W * (t1_ns - t0_ns) / _NS


def window_energy(trace: PowerTrace, window: dict[str, Any]) -> dict[str, Any]:
    """The section 3.8 energy fields for a window (``t_start_ns``, ``t_end_ns``, optional
    ``idle_window_s``), plus ``p_idle_W``, ``idle_drift_frac``, ``duration_s`` and ``flags``."""
    t0, t1 = window["t_start_ns"], window["t_end_ns"]
    idle = idle_bracket(trace, t0, t1, window.get("idle_window_s", IDLE_WINDOW_S))
    total = integrate_J(trace, t0, t1)
    return {"energy_total_J": total,
            "energy_active_J": total - idle["p_idle_W"] * (t1 - t0) / _NS,
            "idle_before_W": idle["idle_before_W"], "idle_after_W": idle["idle_after_W"],
            "idle_window_s": idle["idle_window_s"], "p_idle_W": idle["p_idle_W"],
            "idle_drift_frac": idle["idle_drift_frac"], "duration_s": (t1 - t0) / _NS,
            "flags": list(idle["flags"])}


# --------------------------------------------------------------------------- error bound

def error_bound(P_W: float, rate_hz: float, K: int, E_req_J: float, p_before_W: float,
                p_after_W: float, window_s: float, E_active_J: float,
                source_frac: float = PSTR_SOURCE_ERROR) -> dict[str, float]:
    """Relative error bound of a pair cell (see the module docstring for the unit interpretation).

    edge_rel = 2*P*dt / (K*E_req), dt = 1/rate; idle_rel = |P_before - P_after|*window_s/2 / E_active;
    source_rel = source_frac; ``error_bound_pct`` = 100 x their quadrature sum."""
    if rate_hz <= 0 or K <= 0 or E_req_J <= 0 or E_active_J <= 0:
        raise ValueError("rate, K, request energy and active energy must be positive")
    edge = 2.0 * P_W * (1.0 / rate_hz) / (K * E_req_J)
    idle = abs(p_before_W - p_after_W) * window_s / 2.0 / E_active_J
    total = math.sqrt(edge ** 2 + idle ** 2 + source_frac ** 2)
    return {"edge_rel": edge, "idle_drift_rel": idle, "source_rel": source_frac,
            "error_bound_rel": total, "error_bound_pct": 100.0 * total}


# --------------------------------------------------------------------------- pair method

def pair_k(level: int) -> int:
    """Requests per shape: 20, or 5 for levels strictly above 10,240 (spec 04 section 4.2)."""
    return ENERGY_PAIR_K_LONG if level > ENERGY_PAIR_K_LONG_ABOVE else ENERGY_PAIR_K


def _usable(sample: dict[str, Any]) -> bool:
    return sample.get("status", "ok") in OK_STATUSES and not (sample.get("cached_tokens") or 0) > 0


def request_energies(samples: Sequence[dict[str, Any]], trace: PowerTrace,
                     p_idle_W: float) -> list[float | None]:
    """Active energy of each sample over [t_submit_ns, t_end_ns], aligned with ``samples``. None where
    the sample has no usable times or does not overlap the trace."""
    out: list[float | None] = []
    for s in samples:
        t0, t1 = s.get("t_submit_ns"), s.get("t_end_ns")
        if t0 is None or t1 is None:
            out.append(None)
            continue
        try:
            out.append(active_J(trace, t0, t1, p_idle_W))
        except ValueError:
            out.append(None)
    return out


def _ols(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if sxx == 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in points) / sxx
    return my - slope * mx, slope          # intercept, slope


def pair_cell(samples: Sequence[dict[str, Any]], trace: PowerTrace, window: dict[str, Any], *,
              level_tokens: int, out_A: int = SHAPE_A_TOKENS, out_B: int = SHAPE_B_TOKENS) -> dict[str, Any]:
    """Prefill and decode energy per token at one level from one window of A/B (and C) requests
    (spec 04 section 4.2). Per-request active energy = integral over [t_submit, t_end] minus
    ``P_idle`` x duration, with ``P_idle`` from the window's idle bracket. Unusable requests (status not
    ok, cached_tokens > 0, or no trace overlap) are excluded and counted.

    e_dec = (E_B - E_A) / (out_B - out_A) J/token; E_prefill = E_A - out_A x e_dec; e_pre = E_prefill /
    level_tokens. If every ok A and B request reports ``output_tokens`` and their mean difference is not
    the nominal one, the mean difference is used (``tokens_basis`` "actual"), else "nominal".
    With shape C requests, E is regressed on output tokens over all shapes; a C residual above 5 % of its
    energy flags ``shape_c_residual``. With A or B empty the numbers are None and ``insufficient_requests``
    is flagged."""
    idle = idle_bracket(trace, window["t_start_ns"], window["t_end_ns"],
                        window.get("idle_window_s", IDLE_WINDOW_S))
    p_idle = idle["p_idle_W"]
    flags = list(idle["flags"])
    per: dict[str, list[dict[str, float]]] = {"A": [], "B": [], "C": []}
    voided = {"A": 0, "B": 0, "C": 0}
    for s in samples:
        shape = s.get("shape")
        if shape not in per:
            continue
        t0, t1 = s.get("t_submit_ns"), s.get("t_end_ns")
        if not _usable(s) or t0 is None or t1 is None:
            voided[shape] += 1
            continue
        try:
            total = integrate_J(trace, t0, t1)
        except ValueError:
            voided[shape] += 1
            continue
        per[shape].append({"E_active": total - p_idle * (t1 - t0) / _NS, "E_total": total,
                           "tokens": s.get("output_tokens")})
    n_voided = sum(voided.values())
    if n_voided:
        flags.append("voided_requests")
    k_A, k_B, k_C = len(per["A"]), len(per["B"]), len(per["C"])
    result: dict[str, Any] = {
        "e_pre_mJ_per_tok": None, "e_dec_mJ_per_tok": None, "E_A_J": None, "E_B_J": None,
        "E_prefill_J": None, "k_A": k_A, "k_B": k_B, "k_C": k_C, "n_voided": n_voided,
        "n_voided_by_shape": voided, "p_idle_W": p_idle, "error_bound_pct": None,
        "scope": trace.scope, "source": trace.source, "is_estimate": trace.is_estimate,
        "tokens_basis": "nominal", "flags": flags}
    if not k_A or not k_B:
        flags.append("insufficient_requests")
        return result
    E_A = statistics.fmean(r["E_active"] for r in per["A"])
    E_B = statistics.fmean(r["E_active"] for r in per["B"])
    tok_A, tok_B = out_A, out_B
    ta = [r["tokens"] for r in per["A"]]
    tb = [r["tokens"] for r in per["B"]]
    if all(isinstance(v, (int, float)) for v in ta + tb):
        act_A, act_B = statistics.fmean(ta), statistics.fmean(tb)
        if act_B - act_A > 0 and abs((act_B - act_A) - (out_B - out_A)) > 1e-9:
            tok_A, tok_B = act_A, act_B
            result["tokens_basis"] = "actual"
        elif act_B - act_A <= 0:
            flags.append("output_tokens_inconsistent")
    e_dec = (E_B - E_A) / (tok_B - tok_A)
    E_prefill = E_A - tok_A * e_dec
    result.update({"e_dec_mJ_per_tok": e_dec * 1000.0, "e_pre_mJ_per_tok": E_prefill / level_tokens * 1000.0,
                   "E_A_J": E_A, "E_B_J": E_B, "E_prefill_J": E_prefill})
    # error bound: P = mean window power, E_req = mean total energy of the usable A/B requests
    try:
        we = window_energy(trace, window)
        E_req = statistics.fmean(r["E_total"] for r in per["A"] + per["B"])
        eb = error_bound(we["energy_total_J"] / we["duration_s"], trace.rate_hz, min(k_A, k_B), E_req,
                         idle["idle_before_W"] if idle["idle_before_W"] is not None else p_idle,
                         idle["idle_after_W"] if idle["idle_after_W"] is not None else p_idle,
                         we["duration_s"], we["energy_active_J"])
        result["error_bound_pct"] = eb["error_bound_pct"]
        result["error_bound_parts"] = eb
    except ValueError:
        flags.append("error_bound_unavailable")
    if k_C:
        pts = [(float(r["tokens"] if isinstance(r["tokens"], (int, float)) else n), r["E_active"])
               for shape, n in (("A", out_A), ("C", SHAPE_C_TOKENS), ("B", out_B)) for r in per[shape]]
        fit = _ols(pts)
        if fit is not None:
            c_tokens = statistics.fmean(
                r["tokens"] if isinstance(r["tokens"], (int, float)) else SHAPE_C_TOKENS for r in per["C"])
            E_C = statistics.fmean(r["E_active"] for r in per["C"])
            resid = abs(E_C - (fit[0] + fit[1] * c_tokens)) / abs(E_C) if E_C else math.inf
            result["shape_c_residual_frac"] = resid
            result["e_dec_regression_mJ_per_tok"] = fit[1] * 1000.0
            if resid > SHAPE_C_RESIDUAL_FLAG:
                flags.append("shape_c_residual")
    return result


# --------------------------------------------------------------------------- tasks

def j_per_task(tasks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Median active energy per completed and per attempted task (spec 04 section 4.1). A task is
    ``{task_id, completed, active_J}`` (aux already excluded), or carries ``calls`` =
    ``[{active_J, aux}]``, in which case aux calls are excluded and summed into ``aux_energy_J``
    (section 5.7 rule 4). An ``aux_energy_J`` key on a task is added to the aux total."""
    done: list[float] = []
    every: list[float] = []
    aux_total = 0.0
    for t in tasks:
        if "calls" in t:
            main = sum(c["active_J"] for c in t["calls"] if not c.get("aux"))
            aux_total += sum(c["active_J"] for c in t["calls"] if c.get("aux"))
        else:
            main = t["active_J"]
        aux_total += t.get("aux_energy_J", 0.0) or 0.0
        every.append(main)
        if t.get("completed"):
            done.append(main)
    return {"j_per_completed_task": statistics.median(done) if done else None,
            "j_per_attempted_task": statistics.median(every) if every else None,
            "n_completed": len(done), "n_attempted": len(every), "aux_energy_J": aux_total}


# --------------------------------------------------------------------------- idle residency

def idle_resident(windows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """S3 (spec 04 sections 9 and 9.1): ABBA idle windows tagged ``state`` "resident" or "unloaded".
    Each window gives ``mean_W`` (or ``energy_total_J`` with ``t_start_ns``/``t_end_ns``). Returns the
    raw mean powers and ``delta_W`` = resident - unloaded, in W, mW and Wh per hour (numerically equal to
    W). ``abba_order`` says whether the windows (sorted by ``t_start_ns`` when present) follow R U U R
    or U R R U."""
    def power(w: dict[str, Any]) -> float:
        if w.get("mean_W") is not None:
            return float(w["mean_W"])
        return w["energy_total_J"] / ((w["t_end_ns"] - w["t_start_ns"]) / _NS)

    ws = sorted(windows, key=lambda w: w.get("t_start_ns", 0))
    res = [power(w) for w in ws if w["state"] == "resident"]
    unl = [power(w) for w in ws if w["state"] == "unloaded"]
    if not res or not unl:
        raise ValueError("need at least one resident and one unloaded window")
    p_res, p_unl = statistics.fmean(res), statistics.fmean(unl)
    order = "".join("R" if w["state"] == "resident" else "U" for w in ws)
    delta = p_res - p_unl
    return {"p_idle_resident_W": p_res, "p_idle_unloaded_W": p_unl, "delta_W": delta,
            "delta_mW": delta * 1000.0, "delta_Wh_per_h": delta,
            "n_resident": len(res), "n_unloaded": len(unl),
            "abba_order": order in ("RUUR", "URRU"), "order": order}


# --------------------------------------------------------------------------- cross-checks

def battery_crosscheck(pstr_trace: PowerTrace, battery_trace: PowerTrace,
                       t0_ns: int, t1_ns: int) -> dict[str, Any]:
    """PSTR against the battery accumulator over one window (spec 04 section 4.3). The battery is the
    reference: ``rel_diff = |E_pstr - E_battery| / E_battery``; flagged above 3 %."""
    e_p = integrate_J(pstr_trace, t0_ns, t1_ns)
    e_b = integrate_J(battery_trace, t0_ns, t1_ns)
    if e_b <= 0:
        raise ValueError("battery energy over the window is not positive")
    rel = abs(e_p - e_b) / e_b
    return {"pstr_J": e_p, "battery_J": e_b, "rel_diff": rel, "flag": rel > BATTERY_CROSSCHECK_FLAG}


def drain_estimate(level_start: float, level_end: float, control_start: float, control_end: float,
                   nominal_Wh: float, tokens: int, *, duration_h: float,
                   control_duration_h: float | None = None, level_step: float = 0.01) -> dict[str, Any]:
    """Battery-drain energy (spec 04 section 2.7). Levels are fractions 0-1. The control's drain rate is
    subtracted over the same time as the test run: ``net_drop = test_drop - control_rate x duration_h``.
    ``battery_pct_per_h`` = test minus control. ``e_dec_drain_J_per_tok`` = net_drop x nominal_Wh x 3600 /
    tokens. Levels read in steps (``level_step``, UNVERIFIED), so each drop can be off by one step:
    ``quantisation_rel_bound`` = 2 x step / net_drop is the worst case."""
    if tokens <= 0 or duration_h <= 0:
        raise ValueError("tokens and duration_h must be positive")
    c_dur = control_duration_h if control_duration_h is not None else duration_h
    test_drop = level_start - level_end
    ctrl_drop = control_start - control_end
    ctrl_rate = ctrl_drop / c_dur
    net_drop = test_drop - ctrl_rate * duration_h
    flags: list[str] = ["level_step_quantisation"]
    out: dict[str, Any] = {
        "battery_pct_per_h": 100.0 * (test_drop / duration_h - ctrl_rate),
        "net_drop_frac": net_drop, "e_dec_drain_J_per_tok": None, "energy_J": None,
        "quantisation_rel_bound": None, "is_estimate": True,
        "energy_scope": "whole_device_battery_drain", "flags": flags}
    if net_drop <= 0:
        flags.append("non_positive_net_drop")
        return out
    energy = net_drop * nominal_Wh * 3600.0
    out.update({"energy_J": energy, "e_dec_drain_J_per_tok": energy / tokens,
                "quantisation_rel_bound": 2.0 * level_step / net_drop})
    return out


def void_foreign(window: dict[str, Any], foreign_events: Iterable[dict[str, Any]], *,
                 include_idle: bool = False) -> bool:
    """True if another client used the engine during the window (spec 04 section 4.3): any event of kind
    ``foreign_request`` (or no kind) with t_start <= t_ns <= t_end. With ``include_idle`` the idle
    brackets count too."""
    pad = int(window.get("idle_window_s", IDLE_WINDOW_S) * _NS) if include_idle else 0
    lo, hi = window["t_start_ns"] - pad, window["t_end_ns"] + pad
    return any(e.get("kind", "foreign_request") == "foreign_request" and lo <= e["t_ns"] <= hi
               for e in foreign_events)
