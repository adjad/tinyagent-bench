"""Fit verdicts for the aggregator (spec 04 §3.9, §3.10, §5.4–§5.7).

The functions here take plain facts that :mod:`edat.perf.aggregate` has already
computed, so they hold no sample arithmetic of their own:

- ``unconstrained``: ``{level: {"status", "peak_bytes", "decode_tps"}}`` from the
  model's ordinary ``level`` cells on the same device. ``peak_bytes`` is the median
  over the cell's ok reps of ``footprint_model_bytes`` (when the runner recorded it,
  §5.7 rule 3) or else ``footprint_peak_bytes``, plus ``neural_bytes``. It does **not**
  include the helper-model allowance; the verdict adds it.
- ``observations``: one dict per constrained sample (``fit16_emulation``,
  ``fit16_under_load`` or ``pressure_sweep``) with ``level``, ``status``,
  ``swap_delta_bytes``, ``compressed_delta_bytes``, ``decode_tps`` and, where
  relevant, ``app_load_gib``, ``app_load_swapped_bytes``, ``ttft_s``, ``swap_in_bytes``.

Choices recorded here (the spec leaves them open):

- **Levels that were not emulated.** The emulation runs S, W and the levels above W
  until the first failure, so 512, 1,024 and 5,120 are never emulated. Such a level
  counts toward ``max_level_fits_16gb`` only if its own footprint check passes and the
  next emulated level above it passed (for levels below S: only if the S emulation
  passed; for 5,120: only if W passed).
- **Kills and refusals.** A kill or an engine refusal under emulation at S, at W or
  during the load makes the verdict ``does_not_fit`` (§5.5 table). Above W a kill or
  refusal is the expected end of the "every higher level until the first failure"
  sweep: it only caps ``max_level_fits_16gb``.
- **Status for the footprint check** is ``ok`` or ``ok_extended``. ``memory_pressure``
  at a level on the unconstrained 24 GB Mac already means swap growth, so it fails.
- **Swap growth** of one sample is ``max(swap_delta, 0) + max(compressed_delta, 0)``;
  a level's growth is the largest over its reps and must be under 256 MiB. A level
  whose samples recorded neither counter fails with reason ``swap_not_recorded``: the
  pass rule cannot be checked.
- **Decode slowdown** compares the median emulated decode tok/s at a level with the
  unconstrained median at that level; a level passes at ≥ 0.9 × (inclusive). With no
  unconstrained median the level fails (``no_unconstrained_decode``).
- **Peaks reported** as ``peak_bytes_at_2048`` / ``_10240`` are the model's peaks
  without the allowance; ``compared_bytes_at_*`` add it.
- **Pressure knee**: the first ``app_load_gib`` (ascending) at which any level slows by
  more than 10 % against the unconstrained median **or** ends in a non-ok status.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import constants as C
from .stats import median

FIT_OK_STATUSES = ("ok", "ok_extended")
KILL_STATUSES = ("oom_killed", "oom_killed_at_load", "killed_unknown")
REFUSAL_STATUSES = ("engine_refused",)
LOAD_FAIL_STATUSES = ("oom_killed_at_load",)
VERDICTS = ("fits", "fits_small_context", "does_not_fit", "not_measured")
RANKED_VERDICTS = ("fits", "fits_small_context")
IPHONE_FIT_FRACTION = 0.9          # §2.9: the board uses budget_bytes × 0.9
EPS = 1e-9

_SEV = {s: i for i, s in enumerate(C.STATUS_SEVERITY)}


def worst_status(statuses: Iterable[str]) -> str | None:
    """Worst by ``STATUS_SEVERITY``; a status the table does not list counts as the worst."""
    vals = [s for s in statuses if s]
    if not vals:
        return None
    return min(vals, key=lambda s: _SEV.get(s, -1))


def _growth(o: Mapping) -> int | None:
    sw, comp = o.get("swap_delta_bytes"), o.get("compressed_delta_bytes")
    if sw is None and comp is None:
        return None
    return max(int(sw or 0), 0) + max(int(comp or 0), 0)


def _by_level(observations: Iterable[Mapping]) -> dict:
    out: dict = {}
    for o in observations:
        out.setdefault(o.get("level"), []).append(o)
    return out


def evaluate_constrained_levels(unconstrained: Mapping[int, Mapping], observations: Sequence[Mapping]) -> list[dict]:
    """The §5.5 rule 5 pass rule per level: ``[{level, status, swap_delta_bytes, decode_slowdown_pct, pass, reasons}]``."""
    rows = []
    for level, obs in sorted(((k, v) for k, v in _by_level(observations).items() if k is not None)):
        statuses = [o.get("status") or "ok" for o in obs]
        worst = worst_status(statuses)
        growths = [g for g in (_growth(o) for o in obs) if g is not None]
        growth = max(growths) if growths else None
        emu = median([o.get("decode_tps") for o in obs if (o.get("status") or "ok") in FIT_OK_STATUSES])
        base = (unconstrained.get(level) or {}).get("decode_tps")
        reasons = []
        if any(s in KILL_STATUSES for s in statuses):
            reasons.append("killed")
        if any(s in REFUSAL_STATUSES for s in statuses):
            reasons.append("engine_refused")
        if worst not in FIT_OK_STATUSES and not reasons:
            reasons.append(f"status_{worst}")
        if growth is None:
            reasons.append("swap_not_recorded")
        elif growth >= C.MEMORY_PRESSURE_BYTES:
            reasons.append("memory_pressure")
        slowdown = None
        if base and emu is not None:
            slowdown = (1.0 - emu / base) * 100.0
            if emu < (1.0 - C.EMULATION_SLOWDOWN_LIMIT) * base * (1 - EPS):
                reasons.append("decode_slowdown")
        elif not base:
            reasons.append("no_unconstrained_decode")
        else:
            reasons.append("no_constrained_decode")
        rows.append({"level": level, "status": worst, "swap_delta_bytes": growth,
                     "decode_slowdown_pct": slowdown, "pass": not reasons, "reasons": reasons,
                     "n": len(obs)})
    return rows


def _footprint(unconstrained: Mapping[int, Mapping], threshold: float, budget: float, allowance: int) -> dict:
    out = {}
    for level in C.LEVELS:
        f = unconstrained.get(level)
        if not f:
            continue
        peak = f.get("peak_bytes")
        status = f.get("status")
        compared = None if peak is None else int(peak) + int(allowance)
        ok = status in FIT_OK_STATUSES and compared is not None
        out[level] = {"status": status, "peak_bytes": peak, "compared_bytes": compared,
                      "pass": bool(ok and compared <= threshold),
                      "borderline": bool(ok and threshold < compared <= budget)}
    return out


def _level_fits(footprint: Mapping[int, Mapping], emu_rows: Sequence[Mapping]) -> dict:
    emu = {r["level"]: r["pass"] for r in emu_rows}
    out = {}
    for level in C.LEVELS:
        fp = footprint.get(level)
        if not fp:
            continue
        if level in emu:
            out[level] = bool(fp["pass"] and emu[level])
        else:
            above = [lv for lv in sorted(emu) if lv > level]
            out[level] = bool(fp["pass"] and above and emu[above[0]])
    return out


def _decide(fp: Mapping, emu_rows: Sequence[Mapping], observations: Sequence[Mapping], reasons: list) -> str:
    S, W = C.SMALL_LEVEL, C.WORKING_LEVEL
    emu = {r["level"]: r for r in emu_rows}
    hard = False
    for o in observations:
        st = o.get("status")
        lv = o.get("level")
        if st in LOAD_FAIL_STATUSES or (st in REFUSAL_STATUSES and lv is None):
            reasons.append("load_failed_under_emulation")
            hard = True
        elif st in KILL_STATUSES + REFUSAL_STATUSES and (lv is None or lv <= W):
            reasons.append(f"{'killed' if st in KILL_STATUSES else 'engine_refused'}_at_{lv}")
            hard = True
    s_fp = (fp.get(S) or {}).get("pass", False)
    w_fp = (fp.get(W) or {}).get("pass", False)
    s_emu = emu[S]["pass"] if S in emu else None
    w_emu = emu[W]["pass"] if W in emu else None
    if (fp.get(W) or {}).get("borderline"):
        reasons.append("peak_between_0.9_and_1.0_of_budget_at_W")
    if hard:
        return "does_not_fit"
    if not s_fp:
        reasons.append("footprint_fails_at_S")
        return "does_not_fit"
    if s_emu is False:
        reasons.append("emulation_fails_at_S")
        return "does_not_fit"
    if w_fp and w_emu:
        return "fits"
    if not w_fp:
        reasons.append("footprint_fails_at_W")
    if w_emu is False:
        reasons.append("emulation_fails_at_W")
    if w_emu is None:
        reasons.append("W_not_emulated")
    if s_emu:
        return "fits_small_context"
    reasons.append("S_not_emulated")
    return "not_measured"


def _budget(budget_bytes: int, wired_limit_mb) -> tuple[int, str, int | None]:
    wired_bytes = int(wired_limit_mb) * C.MIB if wired_limit_mb else None
    if wired_bytes is not None and wired_bytes < budget_bytes:
        return wired_bytes, "wired_limit", wired_bytes
    return int(budget_bytes), "b16", wired_bytes


def _constrained_verdict(unconstrained, observations, *, run_id, wired_limit_mb, balloon_gib,
                         aux_allowance_bytes, aux_allowance_source, budget_bytes, budget_source):
    budget, limited_by, wired_bytes = _budget(budget_bytes, wired_limit_mb)
    threshold = (1.0 - C.FIT_MARGIN) * budget
    fp = _footprint(unconstrained, threshold, budget, aux_allowance_bytes)
    rows = evaluate_constrained_levels(unconstrained, observations)
    reasons: list = []
    if not observations:
        verdict = "not_measured"
        reasons.append("emulation_not_run")
    else:
        verdict = _decide(fp, rows, observations, reasons)
    level_fits = _level_fits(fp, rows)
    emu = {r["level"]: r for r in rows}
    max_level = None
    if verdict in RANKED_VERDICTS:
        ok_levels = [lv for lv, v in level_fits.items() if v]
        max_level = max(ok_levels) if ok_levels else None
    S, W = C.SMALL_LEVEL, C.WORKING_LEVEL
    detail = {
        "budget_bytes": budget, "budget_source": budget_source, "budget_limited_by": limited_by,
        "b16_bytes": int(budget_bytes), "wired_limit_mb": wired_limit_mb, "wired_limit_bytes": wired_bytes,
        "balloon_gib": balloon_gib, "margin": C.FIT_MARGIN, "threshold_bytes": threshold,
        "aux_allowance_bytes": int(aux_allowance_bytes), "aux_allowance_source": aux_allowance_source,
        "peak_bytes_at_2048": (fp.get(S) or {}).get("peak_bytes"),
        "peak_bytes_at_10240": (fp.get(W) or {}).get("peak_bytes"),
        "compared_bytes_at_2048": (fp.get(S) or {}).get("compared_bytes"),
        "compared_bytes_at_10240": (fp.get(W) or {}).get("compared_bytes"),
        "footprint": {str(k): v for k, v in fp.items()},
        "emulation": {"run_id": run_id, "levels": rows,
                      "pass_small": emu[S]["pass"] if S in emu else None,
                      "pass_working": emu[W]["pass"] if W in emu else None},
        "max_level_fits_16gb": max_level,
        "level_fits": {str(k): v for k, v in level_fits.items()} if observations else {},
        "reasons": list(dict.fromkeys(reasons)),
    }
    return verdict, detail


def fit16_verdict(unconstrained: Mapping[int, Mapping], emulation: Sequence[Mapping], *, run_id: str | None = None,
                  wired_limit_mb: int | None = None, balloon_gib: float | None = C.BALLOON_GIB,
                  aux_allowance_bytes: int = C.AUX_ALLOWANCE_BYTES, aux_allowance_source: str = "fixed-0.4GiB",
                  budget_bytes: int = C.B16_BYTES, budget_source: str = C.B16_SOURCE) -> tuple[str, dict]:
    """``fits_16gb_mac`` and ``fits_16gb_mac_detail`` (§3.10, §5.5, §5.7 rule 5).

    budget = min(B16, recorded emulated wired limit): "the tighter of the two applies".
    threshold = (1 − FIT_MARGIN) × budget. Compared peak = model peak (+ neural) + aux allowance.
    """
    return _constrained_verdict(unconstrained, emulation, run_id=run_id, wired_limit_mb=wired_limit_mb,
                                balloon_gib=balloon_gib, aux_allowance_bytes=aux_allowance_bytes,
                                aux_allowance_source=aux_allowance_source, budget_bytes=budget_bytes,
                                budget_source=budget_source)


def fit16_under_load_verdict(unconstrained: Mapping[int, Mapping], under_load: Sequence[Mapping], *,
                             run_id: str | None = None, wired_limit_mb: int | None = None,
                             balloon_gib: float | None = C.BALLOON_UNDER_LOAD_GIB,
                             app_load_gib: float | None = C.APP_LOAD_GIB,
                             aux_allowance_bytes: int = C.AUX_ALLOWANCE_BYTES,
                             aux_allowance_source: str = "fixed-0.4GiB",
                             budget_bytes: int = C.B16_BYTES, budget_source: str = C.B16_SOURCE) -> tuple[str, dict]:
    """``fits_16gb_mac_under_load`` (§5.6): the §5.5 scale and pass rule with ``L_apps`` running.

    If the app load itself was compressed or swapped (any sample's ``app_load_swapped_bytes``
    above 256 MiB) the verdict is at best ``fits_small_context``, with reason ``app_load_swapped``.
    """
    verdict, detail = _constrained_verdict(unconstrained, under_load, run_id=run_id, wired_limit_mb=wired_limit_mb,
                                           balloon_gib=balloon_gib, aux_allowance_bytes=aux_allowance_bytes,
                                           aux_allowance_source=aux_allowance_source, budget_bytes=budget_bytes,
                                           budget_source=budget_source)
    swapped = [int(o["app_load_swapped_bytes"]) for o in under_load if o.get("app_load_swapped_bytes") is not None]
    app_swapped = bool(swapped) and max(swapped) > C.MEMORY_PRESSURE_BYTES
    if app_swapped:
        detail["reasons"].append("app_load_swapped")
        if verdict == "fits":
            verdict = "fits_small_context"
    loads = sorted({o["app_load_gib"] for o in under_load if o.get("app_load_gib") is not None})
    detail["app_load_gib"] = loads[0] if len(loads) == 1 else (loads or app_load_gib)
    detail["app_load_swapped_bytes_max"] = max(swapped) if swapped else None
    detail["app_load_swapped"] = app_swapped
    return verdict, detail


def pressure_curve(unconstrained: Mapping[int, Mapping], sweep: Sequence[Mapping]) -> tuple[list[dict], float | None]:
    """``pressure_curve`` and ``pressure_knee_gib`` (§5.6 speed under memory pressure)."""
    groups: dict = {}
    for o in sweep:
        groups.setdefault((o.get("app_load_gib"), o.get("level")), []).append(o)
    curve = []
    for (gib, level), obs in sorted(groups.items(), key=lambda kv: (kv[0][0] is None, kv[0][0] or 0, kv[0][1] or 0)):
        ok = [o for o in obs if (o.get("status") or "ok") in FIT_OK_STATUSES]
        swap_in = [o.get("swap_in_bytes", o.get("swap_delta_bytes")) for o in obs]
        dec = median([o.get("decode_tps") for o in ok])
        base = (unconstrained.get(level) or {}).get("decode_tps")
        curve.append({"app_load_gib": gib, "level": level, "decode_tps": dec,
                      "ttft_s": median([o.get("ttft_s") for o in ok]),
                      "swap_in_bytes": max([s for s in swap_in if s is not None], default=None),
                      "status": worst_status([o.get("status") or "ok" for o in obs]),
                      "decode_slowdown_pct": (1.0 - dec / base) * 100.0 if dec is not None and base else None,
                      "n": len(obs)})
    knee = None
    for row in curve:
        if row["app_load_gib"] is None:
            continue
        slowed = row["decode_slowdown_pct"] is not None and row["decode_slowdown_pct"] > C.EMULATION_SLOWDOWN_LIMIT * 100 + EPS
        failed = row["status"] not in FIT_OK_STATUSES
        if slowed or failed:
            knee = row["app_load_gib"]
            break
    return curve, knee


def fits_iphone_air_verdict(unconstrained: Mapping[int, Mapping], *, balloon: Mapping | None,
                            balloon_run_id: str | None = None, load_killed: bool = False) -> tuple[str, dict]:
    """``fits_iphone_air`` (§2.9, §5.4, §5.5 last bullet): threshold = balloon ``budget_bytes`` × 0.9, levels S and W."""
    budget = (balloon or {}).get("budget_bytes")
    S, W = C.SMALL_LEVEL, C.WORKING_LEVEL
    reasons: list = []
    detail: dict[str, Any] = {"budget_bytes": budget, "budget_source": "balloon" if budget else None,
                              "fit_fraction": IPHONE_FIT_FRACTION,
                              "threshold_bytes": IPHONE_FIT_FRACTION * budget if budget else None,
                              "balloon_run_id": balloon_run_id,
                              "peak_bytes_at_2048": (unconstrained.get(S) or {}).get("peak_bytes"),
                              "peak_bytes_at_10240": (unconstrained.get(W) or {}).get("peak_bytes"),
                              "max_level_fits": None, "level_fits": {}, "reasons": reasons}
    if not budget:
        reasons.append("no_balloon_budget")
        return "not_measured", detail
    thr = IPHONE_FIT_FRACTION * budget
    lf = {}
    for level in C.LEVELS:
        f = unconstrained.get(level)
        if not f:
            continue
        peak = f.get("peak_bytes")
        lf[level] = bool(f.get("status") in FIT_OK_STATUSES and peak is not None and peak <= thr)
    detail["level_fits"] = {str(k): v for k, v in lf.items()}
    if load_killed:
        reasons.append("killed_at_load")
        return "does_not_fit", detail
    if S not in lf:
        reasons.append("S_not_run")
        return "not_measured", detail
    if not lf[S]:
        reasons.append("fails_at_S")
        return "does_not_fit", detail
    ok_levels = [lv for lv, v in lf.items() if v]
    detail["max_level_fits"] = max(ok_levels) if ok_levels else None
    if lf.get(W):
        return "fits", detail
    reasons.append("fails_at_W" if W in lf else "W_not_run")
    return "fits_small_context", detail


def estimate_8gb_mac(weight_bytes: int | None, ram_bytes: int = 8 * C.GIB) -> dict | None:
    """Harbor's admission estimate (§5.4, informational): weights × 1.05 + 0.5 GiB < RAM − 3 GiB."""
    if not weight_bytes:
        return None
    need = weight_bytes * 1.05 + 0.5 * C.GIB
    ceiling = ram_bytes - 3 * C.GIB
    return {"fits": need < ceiling, "need_bytes": need, "ceiling_bytes": ceiling,
            "rule": "weights x 1.05 + 0.5 GiB < RAM - 3 GiB (Harbor admission estimate)"}


def cell_fits(*, device_class: str | None, status: str | None, swap_growth_bytes: int | None,
              level: int | None, peak_bytes: int | None, fit16_level_fits: Mapping | None,
              iphone_threshold_bytes: float | None, weight_bytes: int | None) -> tuple[dict, dict]:
    """``fits`` per device class for one level cell (§3.9, §5.4): ``yes`` / ``no`` / ``estimated``.

    Returns ``(fits, fits_estimate)``; the 8 GB Mac answer behind ``estimated`` is in the second.
    """
    fits: dict = {}
    est: dict = {}
    ok = status in FIT_OK_STATUSES
    is_iphone = bool(device_class) and device_class.startswith("iphone")
    if is_iphone:
        if iphone_threshold_bytes:
            fits["iphone-ref"] = "yes" if ok and peak_bytes is not None and peak_bytes <= iphone_threshold_bytes else "no"
        return fits, est
    no_swap = swap_growth_bytes is None or swap_growth_bytes < C.MEMORY_PRESSURE_BYTES
    fits["mac-ref"] = "yes" if ok and no_swap else "no"
    if fit16_level_fits and level is not None and str(level) in fit16_level_fits:
        fits["mac-16gb"] = "yes" if fit16_level_fits[str(level)] else "no"
    e = estimate_8gb_mac(weight_bytes)
    if e is not None:
        fits["mac-8gb"] = "estimated"
        est["mac-8gb"] = e
    return fits, est
