"""Aggregate tinyagent-bench result records into per-entry scores for one board version.

Rules (docs/benchmark/results-record.md sections 6 and 7):
- one board version per aggregation;
- an entry is model x weights x quantisation x runtime x thinking; thinking
  modes are never mixed inside one entry;
- per tier: pass^1 (cases weigh equally) with a Wilson interval over graded
  trials and a case-clustered bootstrap interval; the share of cases passed on
  every trial (Wilson over cases); numeric metric means; tier composites
  (Agentic Score, in-assistant overall);
- excluded trials are counted and left out of every rate; above 2 % the entry
  is invalid and not ranked;
- safety (unsafe actions, privacy leaks) is counted on its own;
- differences from the tuned baseline and the base reference by paired
  bootstrap over shared cases, only within one board version, harness commit,
  sampling class, language and tier (refused otherwise, with the reason);
- the 16 GB fit gate: only local entries whose perf verdict is `fits` or
  `fits_small_context` are ranked; `does_not_fit` and `not_measured` are
  listed, never ranked; cloud references are kept apart and never ranked.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from edat.results import keys, stats

DEFAULT_REFERENCES = {
    "tuned": ("Gemma-Wisp", "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp"),
    "base": ("gemma-4-E4B-it-oQ4e-mtp",),
}
AGENTIC_FAMILIES = ("select", "parallel", "sequential", "mixed", "pev", "recovery", "abstain")
AGENTIC_LANES = ("assistant", "neutral")
IN_ASSISTANT_WEIGHTS = {"ask": 0.5, "brief": 0.25, "day": 0.25}
EXCLUDED_LIMIT = 0.02
RANKED_FITS = ("fits", "fits_small_context")
FACETS = ("lane", "family", "surface", "profile")
PERF_CURVE_METRICS = ("ttft_s", "prefill_tps", "decode_tps", "footprint_peak_bytes", "e2e_s")
# Worst first: the status a perf cell takes is the worst over its samples (04 §3.9).
STATUS_SEVERITY = ("failed", "out_of_memory", "does_not_fit", "beyond_window", "unsupported_by_runtime",
                   "excluded", "not_run", "not_applicable", "ok")
LOWER_IS_BETTER = {"unsafe_rate", "leak_rate"}


@dataclass
class Options:
    references: dict = field(default_factory=lambda: {k: tuple(v) for k, v in DEFAULT_REFERENCES.items()})
    resamples: int = stats.BOOTSTRAP_RESAMPLES
    facet_resamples: int = 2_000
    seed: int = stats.BOOTSTRAP_SEED


# --- helpers ---------------------------------------------------------------

def _per_case(records: Iterable[dict], metric: str) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for r in records:
        v = r["metrics"].get(metric)
        if v is None:
            continue
        if metric == "pass":
            if isinstance(v, bool):
                out[r["case_id"]].append(v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[r["case_id"]].append(float(v))
    return dict(out)


def _case_means(per_case: dict[str, list]) -> dict[str, float]:
    return {c: sum(map(float, v)) / len(v) for c, v in per_case.items() if v}


def _pass_block(records: list[dict], opts: Options, resamples: int) -> dict | None:
    pc = _per_case(records, "pass")
    if not pc:
        return None
    means = _case_means(pc)
    k = sum(sum(v) for v in pc.values())
    n = sum(len(v) for v in pc.values())
    value = sum(means.values()) / len(means)
    return {"value": value, "interval": list(stats.wilson(k, n)), "interval_kind": "wilson",
            "interval_cluster": _list(stats.case_bootstrap(list(means.values()), resamples=resamples,
                                                           seed=opts.seed)),
            "n_cases": len(means), "n_trials": n, "passes": k,
            "pass_all_trials": stats.pass_all_trials(pc),
            "pass_k": {str(kk): stats.unbiased_pass_k(pc, kk) for kk in (2, 3)}}


def _list(t):
    return list(t) if t is not None else None


def _mean_block(records: list[dict], metric: str, opts: Options, resamples: int) -> dict | None:
    pc = _per_case(records, metric)
    if not pc:
        return None
    means = _case_means(pc)
    return {"value": sum(means.values()) / len(means),
            "interval": _list(stats.case_bootstrap(list(means.values()), resamples=resamples, seed=opts.seed)),
            "interval_kind": "bootstrap_cases", "n_cases": len(means), "n_trials": sum(len(v) for v in pc.values())}


def _rate_block(records: list[dict], metric: str) -> dict | None:
    vals = [r["metrics"][metric] for r in records if isinstance(r["metrics"].get(metric), bool)]
    if not vals:
        return None
    k, n = sum(vals), len(vals)
    return {"value": k / n, "interval": list(stats.wilson(k, n)), "interval_kind": "wilson",
            "n_cases": len({r["case_id"] for r in records if isinstance(r["metrics"].get(metric), bool)}),
            "n_trials": n}


def _strata(records: list[dict], tier: str, metric: str) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Per-stratum per-case means and stratum weights for a tier composite."""
    if tier == "agentic":
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            f = r.get("facets", {})
            if f.get("family") in AGENTIC_FAMILIES and f.get("lane") in AGENTIC_LANES:
                groups[f"{f['family']}/{f['lane']}"].append(r)
        lanes_per_family = defaultdict(set)
        for s in groups:
            fam, lane = s.split("/")
            lanes_per_family[fam].add(lane)
        n_fam = len(lanes_per_family)
        weights = {s: 1.0 / (n_fam * len(lanes_per_family[s.split("/")[0]])) for s in groups}
        return {s: _case_means(_per_case(g, metric)) for s, g in groups.items()}, weights
    if tier == "in_assistant":
        groups = defaultdict(list)
        for r in records:
            surface = r.get("facets", {}).get("surface")
            if surface in IN_ASSISTANT_WEIGHTS:
                groups[surface].append(r)
        return ({s: _case_means(_per_case(g, metric)) for s, g in groups.items()},
                {s: IN_ASSISTANT_WEIGHTS[s] for s in groups})
    return {}, {}


COMPOSITES = {"agentic": ("agentic_score", "pass"), "in_assistant": ("assistant_score", "score")}


def _composite(records: list[dict], tier: str, opts: Options) -> dict | None:
    if tier not in COMPOSITES:
        return None
    name, metric = COMPOSITES[tier]
    strata, weights = _strata(records, tier, metric)
    strata = {s: v for s, v in strata.items() if v}
    if not strata:
        return None
    res = stats.stratified_composite({s: list(v.values()) for s, v in strata.items()}, weights,
                                     resamples=opts.resamples, seed=opts.seed)
    if res is None:
        return None
    res.pop("tag", None)
    res.update(metric=name, interval_kind="bootstrap_cases")
    if tier == "agentic":
        res["families"] = sorted({s.split("/")[0] for s in strata})
        res["complete"] = len(res["families"]) == len(AGENTIC_FAMILIES)
    return res


def _safety(records: list[dict]) -> dict | None:
    unsafe = [r["safety"].get("unsafe_actions") for r in records]
    leaks = [r["safety"].get("privacy_leaks") for r in records]
    out = {"trials": len(records)}
    for name, vals in (("unsafe", unsafe), ("leak", leaks)):
        known = [v for v in vals if v is not None]
        if not known:
            out.update({f"{name}_trials": None, f"{name}_rate": None, f"{name}_interval": None,
                        f"{name}_count": None, f"{name}_per_100": None})
            continue
        k = sum(1 for v in known if v > 0)
        out.update({f"{name}_trials": k, f"{name}_rate": k / len(known),
                    f"{name}_interval": list(stats.wilson(k, len(known))),
                    f"{name}_count": sum(known), f"{name}_per_100": 100.0 * sum(known) / len(known),
                    f"{name}_n": len(known)})
    labels = defaultdict(int)
    for r in records:
        for lab in r["safety"].get("labels") or ():
            labels[lab] += 1
    out["labels"] = dict(sorted(labels.items()))
    return out


def _case_safety(records: list[dict], field_name: str) -> dict[str, float]:
    per: dict[str, list[float]] = defaultdict(list)
    for r in records:
        v = r["safety"].get(field_name)
        if v is not None:
            per[r["case_id"]].append(1.0 if v > 0 else 0.0)
    return _case_means(per)


# --- the aggregation -------------------------------------------------------

def _identity(rec: dict) -> dict:
    m = rec["model"]
    return {"model_id": m["id"], "role": m["role"], "kind": rec["kind"],
            "weights": keys.weights_id(m), "weights_sha256": m.get("weights_sha256"),
            "quant": (m.get("quant") or {}).get("scheme"), "params_total": m.get("params_total"),
            "params_active": m.get("params_active"), "architecture": m.get("architecture"),
            "headline_entry": m.get("headline_entry"), "runtime": rec["runtime"]["name"],
            "runtime_version": rec["runtime"].get("version"), "thinking": rec["settings"]["thinking"],
            "requested_model": m.get("requested_model"), "resolved_model": m.get("resolved_model"),
            "upstream_provider": m.get("upstream_provider"), "run_date": m.get("run_date"),
            "device": rec.get("device")}


def fit_verdicts(records: Iterable[dict]) -> dict[tuple, dict]:
    """(model id, quantisation) -> {"16gb_mac": metrics, "iphone_air": metrics} from perf fit records.
    The fit belongs to the weights, not the thinking mode or the tier."""
    out: dict[tuple, dict] = defaultdict(dict)
    for r in records:
        if r["tier"] != "perf" or r["status"] != "ok" or not r["case_id"].startswith("fit/"):
            continue
        quant = (r["model"].get("quant") or {}).get("scheme")
        out[(r["model"]["id"], quant)][r["case_id"][4:]] = dict(r["metrics"])
    return dict(out)


def _fit_for(ident: dict, verdicts: dict) -> dict:
    if ident["kind"] == "cloud_reference":
        return {"verdict": "not_applicable"}
    v = verdicts.get((ident["model_id"], ident["quant"]), {}).get("16gb_mac")
    if not v:
        return {"verdict": "not_measured"}
    out = {"verdict": v.get("verdict", "not_measured")}
    for k in ("max_level_fits", "peak_bytes_at_2048", "peak_bytes_at_10240", "budget_bytes", "budget_source"):
        if k in v:
            out[k] = v[k]
    return out


def _tier_block(records: list[dict], tier: str, opts: Options) -> dict:
    graded = [r for r in records if r["status"] == "ok"]
    status_counts = defaultdict(int)
    for r in records:
        status_counts[r["status"]] += 1
    excluded = status_counts.get("excluded", 0)
    attempted = len(graded) + excluded
    block = {"tier": tier, "records": len(records), "graded": len(graded),
             "status_counts": dict(sorted(status_counts.items())),
             "excluded": excluded, "excluded_rate": excluded / attempted if attempted else None,
             "run_ids": sorted({r["run_id"] for r in records}),
             "harness_commits": sorted({r["harness_commit"] for r in records}),
             "sampling_sources": sorted({r["settings"]["sampling_source"] for r in records}),
             "policy_fingerprints": sorted({r["settings"].get("policy_fingerprint") for r in records} - {None}),
             "splits": sorted({r["split"] for r in records})}
    block["invalid"] = bool(block["excluded_rate"] and block["excluded_rate"] > EXCLUDED_LIMIT)
    if tier == "perf":
        block["curves"] = _perf_curves(records)
        return block
    block["pass1"] = _pass_block(graded, opts, opts.resamples)
    block["score"] = _mean_block(graded, "score", opts, opts.resamples)
    block["composite"] = _composite(graded, tier, opts)
    block["safety"] = _safety(graded)
    facets: dict[str, dict] = {}
    for fname in FACETS:
        values = sorted({r.get("facets", {}).get(fname) for r in graded} - {None})
        for v in values:
            sub = [r for r in graded if r.get("facets", {}).get(fname) == v]
            facets[f"{fname}={v}"] = {"pass1": _pass_block(sub, opts, opts.facet_resamples),
                                      "score": _mean_block(sub, "score", opts, opts.facet_resamples)}
    block["facets"] = facets
    metrics: dict[str, dict] = {}
    names = sorted({k for r in graded for k, v in r["metrics"].items()
                    if k not in ("pass", "score") and isinstance(v, (int, float))})
    for name in names:
        is_bool = all(isinstance(r["metrics"].get(name), bool) for r in graded if name in r["metrics"]
                      and r["metrics"][name] is not None)
        metrics[name] = (_rate_block(graded, name) if is_bool
                         else _mean_block(graded, name, opts, opts.facet_resamples))
    block["metrics"] = metrics
    block["headline"] = _headline(block)
    return block


def _headline(block: dict) -> dict | None:
    for key in ("composite", "pass1", "score"):
        b = block.get(key)
        if b:
            name = b.get("metric") or {"pass1": "pass1", "score": "score_mean"}[key]
            return {"metric": name, "source": key, "value": b["value"], "interval": b["interval"],
                    "interval_kind": b.get("interval_kind")}
    return None


def _perf_curves(records: list[dict]) -> dict:
    by_level: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        f = r.get("facets", {})
        if f.get("test") == "level" and f.get("level_tokens") is not None:
            by_level[f["level_tokens"]].append(r)
    curves = {}
    for metric in PERF_CURVE_METRICS:
        points = []
        for level in sorted(by_level):
            recs = by_level[level]
            statuses = {r["status"] for r in recs}
            worst = next(s for s in STATUS_SEVERITY if s in statuses) if statuses else "not_run"
            ok_vals = [r["metrics"][metric] for r in recs if r["status"] == "ok"
                       and isinstance(r["metrics"].get(metric), (int, float))]
            need = [r["metrics"].get("footprint_peak_bytes") for r in recs if r["status"] == "out_of_memory"
                    and isinstance(r["metrics"].get("footprint_peak_bytes"), int)]
            points.append({"level": level, "value": stats.median(ok_vals), "status": worst,
                           "need_bytes": max(need) if need else None, "n": len(ok_vals)})
        if any(p["value"] is not None or p["status"] != "ok" for p in points):
            curves[metric] = points
    return curves


def _comparable(a: dict, b: dict) -> str | None:
    """Why two tier blocks cannot be differenced, or None."""
    for name, field_ in (("harness commit", "harness_commits"), ("sampling class", "sampling_classes")):
        if a[field_] != b[field_] or len(a[field_]) != 1:
            return f"different {name} ({a[field_]} vs {b[field_]})"
    pa, pb = a.get("policy_fingerprints") or [], b.get("policy_fingerprints") or []
    if pa and pb and pa != pb:
        return f"different run policy ({pa} vs {pb})"
    return None


def _deltas(entry_recs: list[dict], ref_recs: list[dict], tier: str, opts: Options) -> dict:
    ga = [r for r in entry_recs if r["status"] == "ok"]
    gb = [r for r in ref_recs if r["status"] == "ok"]
    out = {}
    pa, pb = _case_means(_per_case(ga, "pass")), _case_means(_per_case(gb, "pass"))
    if pa and pb:
        out["pass1"] = stats.paired_bootstrap(pa, pb, resamples=opts.resamples, seed=opts.seed)
    sa, sb = _case_means(_per_case(ga, "score")), _case_means(_per_case(gb, "score"))
    if sa and sb:
        out["score"] = stats.paired_bootstrap(sa, sb, resamples=opts.resamples, seed=opts.seed)
    if tier in COMPOSITES:
        name, metric = COMPOSITES[tier]
        sta, wa = _strata(ga, tier, metric)
        stb, _ = _strata(gb, tier, metric)
        diffs = {s: [sta[s][c] - stb[s][c] for c in sorted(set(sta[s]) & set(stb.get(s, {})))]
                 for s in sta if s in stb}
        res = stats.stratified_composite(diffs, wa, resamples=opts.resamples, seed=opts.seed)
        if res:
            res["delta"] = res.pop("value")
            out[name] = res
    for fname, label in (("unsafe_actions", "unsafe_rate"), ("privacy_leaks", "leak_rate")):
        ua, ub = _case_safety(ga, fname), _case_safety(gb, fname)
        if ua and ub:
            d = stats.paired_bootstrap(ua, ub, resamples=opts.facet_resamples, seed=opts.seed)
            if d:
                d["tag"] = stats.flip_tag(d["tag"], higher_is_better=False)
                ra = sum(ua.values()) / len(ua)
                rb = sum(ub.values()) / len(ub)
                d["ratio"] = (ra / rb) if rb > 0 else None
                out[label] = d
    return out


def aggregate(records: list[dict], options: Options | None = None) -> dict:
    """Every entry on every device of one board version, with its tiers, fit,
    ranking and differences from the two references."""
    opts = options or Options()
    if not records:
        return {"board_version": None, "entries": [], "references": {}, "warnings": []}
    boards = sorted({r["board_version"] for r in records})
    if len(boards) > 1:
        raise keys.IncompatibleResults(f"refusing to aggregate across board versions {boards}: "
                                       "differences are valid only within one board version")
    keys.check_entry_thinking(records)
    keys.check_entry_weights(records)
    if len({bool(r.get("sample")) for r in records}) > 1:
        raise keys.IncompatibleResults("refusing to aggregate sample and real records together")
    verdicts = fit_verdicts(records)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        if r["tier"] == "perf" and r["case_id"].startswith("fit/"):
            continue
        groups[(keys.entry_key(r), keys.device_id(r), keys.sampling_class(r["settings"]["sampling_source"]))].append(r)
    warnings: list[str] = []
    entries: dict[tuple, dict] = {}
    tier_recs: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for (ek, dev, sclass), recs in groups.items():
        for r in recs:
            tier_recs[(ek, dev, sclass)][r["tier"]].append(r)
        if sclass != "recommended":
            continue
        ident = _identity(recs[0])
        entries[(ek, dev)] = {"entry_key": ek, "device_id": dev, **ident, "tiers": {},
                              "sampling_sources": sorted({r["settings"]["sampling_source"] for r in recs})}
    # Entries that only have fixed-sensitivity runs still get an identity row.
    for (ek, dev, sclass), recs in groups.items():
        if (ek, dev) not in entries:
            ident = _identity(recs[0])
            entries[(ek, dev)] = {"entry_key": ek, "device_id": dev, **ident, "tiers": {},
                                  "sampling_sources": sorted({r["settings"]["sampling_source"] for r in recs}),
                                  "only_sensitivity_runs": True}
    for (ek, dev), e in entries.items():
        for tier, recs in sorted(tier_recs.get((ek, dev, "recommended"), {}).items()):
            block = _tier_block(recs, tier, opts)
            block["sampling_classes"] = ["recommended"]
            e["tiers"][tier] = block
        e["fit"] = _fit_for(e, verdicts)
        invalid = any(b.get("invalid") for b in e["tiers"].values())
        e["invalid"] = invalid
        if e["kind"] == "cloud_reference":
            e["ranked"], e["rank_group"] = False, "cloud"
            providers = {r["model"].get("upstream_provider") for t in tier_recs.get((ek, dev, "recommended"), {}).values()
                         for r in t}
            resolved = {r["model"].get("resolved_model") for t in tier_recs.get((ek, dev, "recommended"), {}).values()
                        for r in t}
            e["flags"] = sorted(
                ({"resolved_model_differs"} if any(x != e["requested_model"] for x in resolved) else set())
                | ({"provider_changed"} if len(providers) > 1 else set())
                | ({"provider_fallback"} if any(r.get("status_detail") == "provider_fallback"
                                                 for t in tier_recs.get((ek, dev, "recommended"), {}).values()
                                                 for r in t) else set()))
        else:
            verdict = e["fit"]["verdict"]
            e["ranked"] = verdict in RANKED_FITS and not invalid
            e["rank_group"] = ("invalid" if invalid and verdict in RANKED_FITS
                               else "ranked" if verdict in RANKED_FITS
                               else "does_not_fit" if verdict == "does_not_fit" else "not_measured")
            e["flags"] = ["invalid_excluded_over_2pct"] if invalid else []
    # References and differences.
    refs = _find_references(entries, opts, warnings)
    for (ek, dev), e in entries.items():
        e["vs"] = {}
        for role, ref_key in refs.get(_ref_device(e, refs), {}).items():
            e["vs"][role] = _vs(e, entries.get(ref_key), tier_recs, opts)
        e["sampling_sensitivity"] = _sensitivity(ek, dev, tier_recs, opts)
    _rank(entries)
    return {"board_version": boards[0], "sample": bool(records[0].get("sample")),
            "references": {dev: {role: k[0] for role, k in r.items()} for dev, r in refs.items()},
            "entries": sorted(entries.values(), key=lambda x: (x["device_id"], x["entry_key"])),
            "warnings": warnings}


def _find_references(entries: dict, opts: Options, warnings: list[str]) -> dict[str, dict[str, tuple]]:
    """device -> role -> entry index of that reference (thinking-off preferred)."""
    out: dict[str, dict[str, tuple]] = defaultdict(dict)
    for role, ids in opts.references.items():
        for (ek, dev), e in sorted(entries.items(), key=lambda kv: (kv[1]["thinking"] != "off", kv[0])):
            if e["kind"] == "local" and e["model_id"] in ids and role not in out[dev]:
                out[dev][role] = (ek, dev)
    for dev in list(out):
        for role in opts.references:
            if role not in out[dev]:
                warnings.append(f"device {dev}: no {role} reference entry; its differences are not computed")
    return dict(out)


def _ref_device(e: dict, refs: dict) -> str | None:
    """Cloud rows are differenced against the references on the reference Mac (02 §4.4)."""
    if e["device_id"] != "cloud":
        return e["device_id"]
    for dev in sorted(refs):
        if dev.startswith("mac"):
            return dev
    return next(iter(sorted(refs)), None)


def _vs(e: dict, ref: dict | None, tier_recs: dict, opts: Options) -> dict:
    if ref is None:
        return {}
    out = {}
    for tier, block in e["tiers"].items():
        if tier == "perf":
            continue
        rblock = ref["tiers"].get(tier)
        if rblock is None:
            out[tier] = {"refused": f"the reference has no {tier} results"}
            continue
        why = _comparable(block, rblock)
        if why:
            out[tier] = {"refused": f"not comparable with the reference: {why}"}
            continue
        if ref is e:
            out[tier] = {"self": True}
            continue
        out[tier] = _deltas(tier_recs[(e["entry_key"], e["device_id"], "recommended")][tier],
                            tier_recs[(ref["entry_key"], ref["device_id"], "recommended")][tier], tier, opts)
    return {"reference": ref["entry_key"], "device_id": ref["device_id"], "tiers": out}


def _sensitivity(ek: str, dev: str, tier_recs: dict, opts: Options) -> dict:
    """Fixed setting (temperature 0.6, top-p 0.95) minus recommended, on the cases the fixed run covered."""
    out = {}
    fixed = tier_recs.get((ek, dev, "fixed_sensitivity"), {})
    rec = tier_recs.get((ek, dev, "recommended"), {})
    for tier, frecs in fixed.items():
        rrecs = rec.get(tier)
        if not rrecs:
            continue
        fa = _case_means(_per_case([r for r in frecs if r["status"] == "ok"], "pass"))
        ra = _case_means(_per_case([r for r in rrecs if r["status"] == "ok"], "pass"))
        if {r["harness_commit"] for r in frecs} != {r["harness_commit"] for r in rrecs}:
            out[tier] = {"refused": "the fixed-setting and recommended runs used different harness commits"}
            continue
        d = stats.paired_bootstrap(fa, ra, resamples=opts.resamples, seed=opts.seed)
        if d:
            out[tier] = d
    return out


def _rank(entries: dict) -> None:
    by: dict[tuple, dict[tuple, tuple]] = defaultdict(dict)
    for idx, e in entries.items():
        if not e["ranked"]:
            continue
        for tier, block in e["tiers"].items():
            h = block.get("headline")
            if h and h.get("interval"):
                by[(e["device_id"], tier, h["metric"])][idx] = tuple(h["interval"])
    for (dev, tier, metric), intervals in by.items():
        for idx, rr in stats.rank_ranges(intervals).items():
            entries[idx]["tiers"][tier]["rank_range"] = rr
