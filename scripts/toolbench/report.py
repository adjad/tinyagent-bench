"""Scorecard, per-category table, failure labels, and run-vs-run comparison.

Input is the list of result rows a run writes to results.jsonl — one row per
(model, case, trial) with the scorer's verdict under "v". Pure functions;
`render()` returns text.

STATISTICS. Trials of one case are correlated (same prompt, same world), so
every interval resamples CASES, never trials: a case-clustered percentile
bootstrap (`rate_ci`), deterministic (fixed RNG seed). Two models are compared
only on the cases both scored (`paired`): per-case pass-rate differences, a
sign-flip permutation test on their mean (exact up to 2^16 sign patterns,
else Monte Carlo with a fixed seed), and a bootstrap interval on the mean
difference. "Every-trial share" is the share of cases passed on every one of
their trials (pass^k); it is only comparable between models with the same
number of trials per case, which the scorecard flags. pass^k is the unbiased
estimator mean_c C(passes_c, k) / C(trials_c, k), with a Wilson interval
(cases are independent, so a case-level share may use one). A model with more
than EXCLUDED_WARN of its trials excluded as engine errors is marked invalid.
"""
from __future__ import annotations

import itertools
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

# tinyagent-bench's references (policy.DEFAULT_REFERENCES; literal so this module stays import-free).
DEFAULT_REFERENCES = ("Gemma-Wisp", "gemma-4-E4B-it-oQ4e-mtp")
REFERENCE_ALIASES = {"gemma-4-E4B-it-r5-step-01800-oQ4e-mtp": "Gemma-Wisp"}
BOOT_REPS = 2000
PERM_REPS = 10000
_RNG_SEED = 20261118
# Above this share of excluded (engine_error) trials a model's run is invalid (tinyagent-bench §9 P0.6).
EXCLUDED_WARN = 0.02


def load_rows(run_dir: Path) -> list[dict]:
    path = run_dir / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


_NOT_OFFERED = re.compile(r"(\S+) was not offered")


def traj_from_row(row: dict, meta: dict | None = None) -> dict:
    """The trajectory scorer.score() consumes, rebuilt from a stored result row.

    Rows keep everything the scorer reads except two things: rounded timings
    (taken from the stored verdict) and, in rows written before `offered` was
    stored, the per-step offered tool names. For those the stored verdict's own
    "X was not offered" notes stand in for them, so a rescore keeps an
    `unoffered_tool` the old scorer saw instead of silently dropping it.
    """
    v = row.get("v") or {}
    steps = [dict(s) for s in row.get("steps") or ()]
    if "unoffered_tool" in (v.get("labels") or ()) and not any(s.get("offered") for s in steps):
        gone = set(_NOT_OFFERED.findall("; ".join(v.get("notes") or ())))
        for s in steps:
            called = [c["name"] for c in s.get("calls") or ()]
            if gone & set(called):
                s["offered"] = [n for n in called if n not in gone] + ["(stored: not offered)"]
    return {"route": row.get("route"), "steps": steps, "final": row.get("final") or "",
            "effects": row.get("effects") or [], "asked": row.get("asked") or [],
            "escapes": row.get("escapes"), "error": row.get("error"), "timeout": row.get("timeout"),
            "message_draft": row.get("message_draft"),
            "access": row.get("access"), "path": row.get("path"),
            "seconds": v.get("seconds") or 0.0, "model_seconds": v.get("model_seconds") or 0.0,
            "guard_retries": v.get("guard_retries") or 0,
            "max_steps": (meta or {}).get("max_steps") or 8}


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def _q(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def _scored(rows: list[dict]) -> list[dict]:
    """Rows that count: an engine_error trial is the engine's, not the model's,
    and a report-only case (case key `report_only`) is never scored."""
    return [r for r in rows if not r["v"].get("excluded") and not r.get("report_only")]


def report_only(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("report_only")]


# ------------------------------------------------------------------ statistics

def case_outcomes(rows: list[dict], model: str) -> dict[str, tuple[int, int]]:
    """case -> (passed trials, scored trials) for `model`."""
    out: dict[str, list[int]] = {}
    for r in _scored(rows):
        if r["model"] == model:
            n = out.setdefault(r["case"], [0, 0])
            n[0] += bool(r["v"]["passed"])
            n[1] += 1
    return {c: (a, b) for c, (a, b) in out.items()}


def rate_ci(pairs: list[tuple[int, int]], *, reps: int = BOOT_REPS, level: float = 0.95,
            seed: int = _RNG_SEED) -> tuple[float, float]:
    """Case-clustered percentile bootstrap interval for sum(passed)/sum(trials)."""
    pairs = [p for p in pairs if p[1]]
    if not pairs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    k = len(pairs)
    stats = []
    for _ in range(reps):
        a = b = 0
        for _ in range(k):
            x, y = pairs[rng.randrange(k)]
            a += x
            b += y
        stats.append(a / b)
    stats.sort()
    lo = stats[int((1 - level) / 2 * reps)]
    hi = stats[min(reps - 1, int((1 + level) / 2 * reps))]
    return (lo, hi)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for k successes in n independent trials."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def pass_hat_k(pairs: list[tuple[int, int]], k: int) -> float | None:
    """Unbiased pass^k: mean over cases of C(passes, k) / C(trials, k). Cases
    with fewer than k trials are left out; None when none qualifies."""
    from math import comb
    vals = [comb(c, k) / comb(n, k) for c, n in pairs if n >= k]
    return sum(vals) / len(vals) if vals else None


def paired(a: dict[str, tuple[int, int]], b: dict[str, tuple[int, int]], *, reps: int = PERM_REPS,
           seed: int = _RNG_SEED, drop: set[str] = frozenset()) -> dict:
    """b minus a over the cases both scored: per-case pass-rate differences,
    better/worse/tied counts, the mean difference with a case bootstrap
    interval, and a two-sided sign-flip permutation p-value."""
    shared = sorted(c for c in a.keys() & b.keys() if a[c][1] and b[c][1] and c not in drop)
    d = [b[c][0] / b[c][1] - a[c][0] / a[c][1] for c in shared]
    out = {"n": len(shared), "better": sum(x > 0 for x in d), "worse": sum(x < 0 for x in d),
           "tied": sum(x == 0 for x in d), "mean_diff": statistics.mean(d) if d else 0.0,
           "ci": (0.0, 0.0), "p": 1.0, "exact": True}
    nz = [x for x in d if x != 0]
    if not nz:
        return out
    n = len(d)
    obs = abs(sum(nz))
    if len(nz) <= 16:
        hits = sum(abs(sum(s * x for s, x in zip(signs, nz))) >= obs - 1e-12
                   for signs in itertools.product((1, -1), repeat=len(nz)))
        out["p"] = hits / 2 ** len(nz)
    else:
        rng = random.Random(seed)
        hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in nz)) >= obs - 1e-12 for _ in range(reps))
        out["p"], out["exact"] = (hits + 1) / (reps + 1), False
    rng = random.Random(seed + 1)
    boots = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT_REPS))
    out["ci"] = (boots[int(0.025 * BOOT_REPS)], boots[min(BOOT_REPS - 1, int(0.975 * BOOT_REPS))])
    return out


def _route_fault_cases(rows: list[dict], model: str) -> set[str]:
    return {r["case"] for r in _scored(rows) if r["model"] == model and "route_fault" in r["v"]["labels"]}


def _fmt_ci(ci) -> str:
    return f"{100 * ci[0]:.0f}–{100 * ci[1]:.0f}%"


def _fmt_paired(p: dict) -> str:
    sig = "" if p["p"] >= 0.05 else " *" if p["p"] >= 0.01 else " **"
    return (f"{100 * p['mean_diff']:+.1f} pts [{100 * p['ci'][0]:+.1f}, {100 * p['ci'][1]:+.1f}]  "
            f"p={p['p']:.3g}{sig}  cases better/worse/tied {p['better']}/{p['worse']}/{p['tied']} of {p['n']}")


def summarize(rows: list[dict], *, latency_budget: float = 180.0) -> dict:
    by_model: dict[str, list[dict]] = defaultdict(list)
    excluded: Counter = Counter()
    for r in rows:
        if r.get("report_only"):
            continue                       # recorded, never in any rate (report.report_only)
        if r["v"].get("excluded"):
            excluded[r["model"]] += 1
            by_model.setdefault(r["model"], [])
        else:
            by_model[r["model"]].append(r)
    out = {}
    for model, rs in by_model.items():
        if not rs:
            out[model] = _empty(excluded[model])
            continue
        cases: dict[str, list[dict]] = defaultdict(list)
        for r in rs:
            cases[r["case"]].append(r)
        n = len(rs)
        no_tool = [r for r in rs if r.get("no_tool")]
        secs = [r["v"]["seconds"] for r in rs]
        model_secs = [float(r["v"].get("model_seconds") or 0.0) for r in rs]
        step_counts = [int(r["v"].get("steps") or 0) for r in rs]
        think = [int(r["v"].get("reasoning_chars") or 0) for r in rs]
        outcomes = case_outcomes(rs, model)
        consistent = [(int(all(x["v"]["passed"] for x in xs)), 1) for xs in cases.values()]
        per_case = {len(xs) for xs in cases.values()}
        route = [r for r in rs if "route_fault" in r["v"]["labels"]]
        out[model] = {
            "trials": n,
            "cases": len(cases),
            "pass_ci": rate_ci(list(outcomes.values())),
            "consistent_ci": wilson(sum(x for x, _ in consistent), len(consistent)),
            "pass_k": {k: pass_hat_k(list(outcomes.values()), k) for k in (1, 2, 3, 4)
                       if any(t >= k for _, t in outcomes.values())},
            "trials_per_case": sorted(per_case),
            "excluded_share": excluded[model] / (n + excluded[model]),
            "pass_ex_route": (sum(r["v"]["passed"] for r in rs if "route_fault" not in r["v"]["labels"]),
                              n - len(route)),
            "within_budget": sum(bool(r["v"]["passed"]) and float(r["v"].get("seconds") or 0) <= latency_budget
                                 for r in rs),
            "raw_clean": sum(bool(r["v"].get("format_ok", True)) and "raw_markup" not in r["v"]["labels"]
                             for r in rs),
            "context_overflow": sum("context_overflow" in r["v"]["labels"] for r in rs),
            "pass": sum(r["v"]["passed"] for r in rs),
            "consistent": sum(all(x["v"]["passed"] for x in xs) for xs in cases.values()),
            "selection": sum(r["v"]["selection_ok"] for r in rs),
            "args": sum(r["v"]["args_ok"] for r in rs),
            "unsafe": sum(not r["v"]["safe"] for r in rs),
            "leak_trials": sum(bool(r["v"].get("leaks")) for r in rs),
            # Outbound calls per leak kind (spec #53/#54 count calls, not matches).
            "leaks": Counter(k for r in rs for k, _ in {(x["kind"], x.get("call")) for x in r["v"].get("leaks") or ()}),
            "outbound_calls": sum(int(r["v"].get("outbound_calls") or 0) for r in rs),
            "format_bad": sum(not r["v"]["format_ok"] for r in rs),
            "answer": sum(r["v"]["answer_ok"] for r in rs),
            "no_tool_ok": sum("unneeded_call" not in r["v"]["labels"] for r in no_tool),
            "no_tool_n": len(no_tool),
            "route_faults": sum("route_fault" in r["v"]["labels"] for r in rs),
            "escapes": sum(bool(r.get("escapes")) for r in rs),
            "median_s": round(statistics.median(secs), 2) if secs else 0.0,
            "p90_s": round(_q(secs, 0.9), 2),
            "model_median_s": round(statistics.median(model_secs), 2) if model_secs else 0.0,
            "model_p90_s": round(_q(model_secs, 0.9), 2),
            "steps_p50": int(statistics.median(step_counts)) if step_counts else 0,
            "discovery": sum(int(r["v"].get("discovery_calls") or 0) for r in rs),
            "excluded": excluded[model],
            "timeouts": sum("timeout" in r["v"]["labels"] for r in rs),
            "guard_retries": sum(int(r["v"].get("guard_retries") or 0) for r in rs),
            "by_split": {
                sp: (sum(r["v"]["passed"] for r in xs), len(xs))
                for sp, xs in _group(rs, "split").items()},
            "think_p50": int(statistics.median(think)) if think else 0,
            "think_p90": int(_q(think, 0.9)),
            "think_max": max(think) if think else 0,
            "mean_steps": round(statistics.mean(r["v"]["steps"] for r in rs), 2) if rs else 0.0,
            "mean_prompt_tok": int(statistics.mean(r["v"]["prompt_tokens_est"] for r in rs)) if rs else 0,
            "labels": Counter(l for r in rs for l in r["v"]["labels"]),
            "by_category": {
                cat: (sum(r["v"]["passed"] for r in xs), len(xs))
                for cat, xs in _group(rs, "category").items()},
        }
    return out


def _empty(excluded: int) -> dict:
    """A model whose every trial was an engine error."""
    return {"trials": 0, "cases": 0, "pass_ci": (0.0, 0.0), "consistent_ci": (0.0, 0.0), "pass_k": {},
            "trials_per_case": [],
            "excluded_share": 1.0 if excluded else 0.0, "pass_ex_route": (0, 0), "within_budget": 0,
            "raw_clean": 0, "context_overflow": 0,
            "pass": 0, "consistent": 0, "selection": 0, "args": 0, "unsafe": 0,
            "leak_trials": 0, "leaks": Counter(), "outbound_calls": 0,
            "format_bad": 0, "answer": 0, "no_tool_ok": 0, "no_tool_n": 0, "route_faults": 0, "escapes": 0,
            "median_s": 0.0, "p90_s": 0.0, "model_median_s": 0.0, "model_p90_s": 0.0, "steps_p50": 0,
            "discovery": 0, "excluded": excluded, "timeouts": 0, "guard_retries": 0, "by_split": {},
            "think_p50": 0, "think_p90": 0, "think_max": 0, "mean_steps": 0.0, "mean_prompt_tok": 0,
            "labels": Counter(), "by_category": {}}


def _group(rows, key) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[str(r.get(key) or "")].append(r)
    return dict(sorted(g.items()))


def latency(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """case -> model -> median turn seconds, model seconds and steps over its trials."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for (model, case), rs in _group_pairs(_scored(rows)).items():
        out[case][model] = {
            "turn_s": round(statistics.median(r["v"]["seconds"] for r in rs), 2),
            "model_s": round(statistics.median(float(r["v"].get("model_seconds") or 0.0) for r in rs), 2),
            "steps": round(statistics.median(int(r["v"].get("steps") or 0) for r in rs), 1)}
    return dict(sorted(out.items()))


def guard_retries(rows: list[dict]) -> dict[str, dict[str, int]]:
    """case -> model -> HTTP-400 guard retries summed over trials (excluded trials too:
    the retries happened). Only cases with at least one retry."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for (model, case), rs in _group_pairs(rows).items():
        n = sum(int(r["v"].get("guard_retries") or 0) for r in rs)
        if n:
            out[case][model] = n
    return dict(sorted(out.items()))


def _group_pairs(rows) -> dict[tuple[str, str], list[dict]]:
    g: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        g[(r["model"], r["case"])].append(r)
    return g


def _short(model: str) -> str:
    base, _, variant = model.partition(" +")
    base = base.replace("Ling-3.0-tiny-", "Ling-").replace("-merged", "")
    if not variant:
        return base[:26]
    return f"{base[:25 - len(variant)]} +{variant}"


CLOUD_KIND = "cloud_reference"


def kind_of(rows: list[dict]) -> str:
    """"local" or "cloud_reference"; refuses rows that mix the two."""
    kinds = {((r.get("key") or {}).get("kind") or "local") for r in rows}
    if len(kinds) > 1:
        raise ValueError("cloud-reference and local rows are never rendered, ranked or pooled together")
    return kinds.pop() if kinds else "local"


def cloud_summary(rows: list[dict]) -> dict[str, dict]:
    """Per model of a cloud_reference run: what OpenRouter metered on every
    request (cloud.usage_tap), excluded trials included (they were paid for)."""
    out: dict[str, dict] = {}
    for model, rs in _group(rows, "model").items():
        recs = [c for r in rs for st in r.get("steps") or () for c in st.get("cloud") or ()]
        n = max(1, len(rs))

        def total(key):
            return sum(int(c.get(key) or 0) for c in recs)
        first = [float(c["t_first_byte_s"]) for c in recs if c.get("t_first_byte_s") is not None]
        whole = [float(c["t_total_s"]) for c in recs if c.get("t_total_s") is not None]
        cost = sum(float(c.get("charged_usd") or c.get("cost") or 0.0) for c in recs)
        out[model] = {
            "requests": len(recs), "in_per_trial": total("prompt_tokens") // n,
            "out_per_trial": total("completion_tokens") // n, "reasoning_per_trial": total("reasoning_tokens") // n,
            "cached_per_trial": total("cached_tokens") // n, "usd": cost, "usd_per_trial": cost / n,
            "estimated": sum(bool(c.get("cost_estimated")) for c in recs),
            "first_byte_p50": round(statistics.median(first), 2) if first else None,
            "total_p50": round(statistics.median(whole), 2) if whole else None,
            "providers": Counter(str(c.get("provider") or "?") for c in recs),
            "resolved": sorted({str(c.get("model")) for c in recs if c.get("model")}),
            "http_errors": sum(int(c.get("status") or 0) != 200 for c in recs),
            "resolved_differs": sum(bool(c.get("resolved_differs")) for c in recs),
            "unsupported": sorted({p for r in rs for p in ((r.get("key") or {}).get("params_unsupported") or ())})}
    return out


def render(rows: list[dict], *, failures: int = 15, per_case_latency: bool = True,
           latency_budget: float = 180.0, references=()) -> str:
    s = summarize(rows, latency_budget=latency_budget)
    models = list(s)
    if not models:
        return "(no results)"
    cloud_run = kind_of(rows) == CLOUD_KIND
    lines = []
    w = max(18, *(len(_short(m)) for m in models)) + 2

    def row(label, cells):
        lines.append(f"{label:<22}" + "".join(f"{c:>{w}}" for c in cells))

    lines.append("tinyagent-bench · CLOUD REFERENCE SCORECARD (OpenRouter, public dev split; a separate kind of result: "
                 "never ranked with local models or pooled into their statistics)" if cloud_run else
                 "tinyagent-bench · TOOLBENCH SCORECARD (per trial; consistent = case passed every trial)")
    row("", [_short(m) for m in models])
    row("pass", [f"{_pct(s[m]['pass'], s[m]['trials'])} ({s[m]['pass']}/{s[m]['trials']})" for m in models])
    row("pass 95% CI (cases)", [_fmt_ci(s[m]["pass_ci"]) for m in models])
    row("consistent cases", [f"{s[m]['consistent']}/{s[m]['cases']}" for m in models])
    row("every-trial share", [f"{_pct(s[m]['consistent'], s[m]['cases'])} ({_fmt_ci(s[m]['consistent_ci'])})"
                              for m in models])
    for k in sorted({k for m in models for k in s[m]["pass_k"]} - {1}):
        row(f"pass^{k} (unbiased)", [f"{100 * s[m]['pass_k'][k]:.0f}%" if s[m]["pass_k"].get(k) is not None
                                     else "—" for m in models])
    row(f"pass ≤ {latency_budget:g}s", [_pct(s[m]["within_budget"], s[m]["trials"]) for m in models])
    row("pass excl. route flt", [_pct(*s[m]["pass_ex_route"]) for m in models])
    row("raw format clean", [_pct(s[m]["raw_clean"], s[m]["trials"]) for m in models])
    row("tool selection", [_pct(s[m]["selection"], s[m]["trials"]) for m in models])
    row("selection + args", [_pct(s[m]["args"], s[m]["trials"]) for m in models])
    row("answer grounded", [_pct(s[m]["answer"], s[m]["trials"]) for m in models])
    row("no-tool restraint", [f"{s[m]['no_tool_ok']}/{s[m]['no_tool_n']}" for m in models])
    row("UNSAFE calls", [str(s[m]["unsafe"]) for m in models])
    row("PRIVACY LEAKS (trials)", [f"{s[m]['leak_trials']} ({_pct(s[m]['leak_trials'], s[m]['trials'])})"
                                   for m in models])

    def per100(m, kind):
        n = s[m]["outbound_calls"]
        return f"{100 * s[m]['leaks'][kind] / n:.1f}" if n else "-"
    row("  wrong recipient /100", [per100(m, "wrong_recipient") for m in models])
    row("  private detail /100", [per100(m, "private_detail") for m in models])
    row("  web exfiltration /100", [per100(m, "web_exfiltration") for m in models])
    row("  outbound calls", [str(s[m]["outbound_calls"]) for m in models])
    row("format errors", [str(s[m]["format_bad"]) for m in models])
    row("format valid", [_pct(s[m]["trials"] - s[m]["format_bad"], s[m]["trials"]) for m in models])
    row("route faults*", [str(s[m]["route_faults"]) for m in models])
    row("turn s p50 / p90", [f"{s[m]['median_s']} / {s[m]['p90_s']}" for m in models])
    row("model s p50 / p90", [f"{s[m]['model_median_s']} / {s[m]['model_p90_s']}" for m in models])
    row("steps p50 / mean", [f"{s[m]['steps_p50']} / {s[m]['mean_steps']}" for m in models])
    row("schema loads", [str(s[m]["discovery"]) for m in models])
    row("timeouts", [str(s[m]["timeouts"]) for m in models])
    row("guard 400 retries", [str(s[m]["guard_retries"]) for m in models])
    row("EXCLUDED (engine)", [f"{s[m]['excluded']} ({100 * s[m]['excluded_share']:.0f}%)" for m in models])
    if any(s[m]["context_overflow"] for m in models):
        row("context overflow", [str(s[m]["context_overflow"]) for m in models])
    row("think chars p50/p90", [f"{s[m]['think_p50']} / {s[m]['think_p90']}" for m in models])
    row("think chars max", [str(s[m]["think_max"]) for m in models])
    row("mean prompt tok (est)", [str(s[m]["mean_prompt_tok"]) for m in models])
    if any(s[m]["escapes"] for m in models):
        row("guard refusals", [str(s[m]["escapes"]) for m in models])
    if cloud_run:
        c = cloud_summary(rows)
        cm = [c.get(m) or {} for m in models]
        row("tokens in / trial", [str(x.get("in_per_trial", 0)) for x in cm])
        row("tokens out / trial", [str(x.get("out_per_trial", 0)) for x in cm])
        row("  of which reasoning", [str(x.get("reasoning_per_trial", 0)) for x in cm])
        row("  cached in / trial", [str(x.get("cached_per_trial", 0)) for x in cm])
        row("USD / trial", [f"{x.get('usd_per_trial', 0.0):.4f}" for x in cm])
        row("USD total", [f"{x.get('usd', 0.0):.2f}" + ("*" if x.get("estimated") else "") for x in cm])
        row("net 1st byte p50 s", [str(x.get("first_byte_p50")) for x in cm])
        row("net request p50 s", [str(x.get("total_p50")) for x in cm])
        row("HTTP errors", [str(x.get("http_errors", 0)) for x in cm])
        row("resolved != requested", [str(x.get("resolved_differs", 0)) for x in cm])
        for m, x in zip(models, cm):
            lines.append(f"  {_short(m)}: answered by {dict(x.get('providers') or {})}; resolved model "
                         f"{', '.join(x.get('resolved') or ['?'])}; not sent (unsupported): "
                         f"{', '.join(x.get('unsupported') or []) or 'none'}"
                         + ("  FLAG: a resolved id differs from the requested one" if x.get("resolved_differs")
                            else ""))
        lines.append("  Efficiency (tokens, cost, latency) is NOT comparable with on-device numbers.")
        lines.append("  * cost priced from the list (a response carried no usage.cost). Network times include "
                     "OpenRouter's routing and any 429 retry; whole-reply models (reasoning replay) report "
                     "first byte = whole reply.")
    lines.append("* route faults are the host's (a needed tool forbidden, never offered, or refused by the "
                 "loop for every model: no completion contract), identical for every model; they still "
                 "count as fails.")
    lines.append("  CI = 95% case-clustered bootstrap (pass) / Wilson (every-trial share). raw format clean = no "
                 "tool-call/think markup in any step's raw output, before the host's (Gemma-only) repair.")
    if len({tuple(s[m]["trials_per_case"]) for m in models}) > 1:
        lines.append("  NOTE: models ran different numbers of trials per case; every-trial shares are not "
                     "comparable between them.")
    for m in models:
        if s[m]["excluded_share"] > EXCLUDED_WARN:
            lines.append(f"  WARNING: {_short(m)}: {100 * s[m]['excluded_share']:.1f}% of trials were excluded "
                         f"as engine errors (over {100 * EXCLUDED_WARN:.0f}%): this entry is INVALID; check the "
                         "probe and the engine and rerun.")

    if ro := report_only(rows):
        ids = sorted({r["case"] for r in ro})
        lines.append(f"  REPORT-ONLY (not in any rate): {len(ids)} case(s), "
                     f"{sum(r['v']['passed'] for r in ro)}/{len(ro)} trials passed: {', '.join(ids)}")
    if not cloud_run:
        lines += _paired_sections(rows, rows, models, references, title="same run")

    for title, key in (("BY CATEGORY (pass / trials)", "by_category"), ("BY SPLIT (pass / trials)", "by_split")):
        groups = sorted({c for m in models for c in s[m][key]})
        if key == "by_split" and len(groups) < 2:
            continue
        lines.append("")
        lines.append(title)
        row("", [_short(m) for m in models])
        for c in groups:
            row(c, [("{}/{}".format(*s[m][key][c]) if c in s[m][key] else "—") for m in models])

    if per_case_latency:
        lines.append("")
        lines.append("LATENCY BY CASE (median over trials: turn s / model s / steps)")
        row("", [_short(m) for m in models])
        for case, per in latency(rows).items():
            row(case[:21], [(f"{per[m]['turn_s']} / {per[m]['model_s']} / {per[m]['steps']:g}"
                             if m in per else "—") for m in models])

    retries = guard_retries(rows)
    if retries:
        lines.append("")
        lines.append("GUARD 400 RETRIES BY CASE (sum over trials)")
        row("", [_short(m) for m in models])
        for case, per in retries.items():
            row(case[:21], [str(per.get(m, 0)) for m in models])

    lines.append("")
    lines.append("FAILURE LABELS (trials carrying each)")
    labels = sorted({l for m in models for l in s[m]["labels"]})
    row("", [_short(m) for m in models])
    for l in labels:
        row(l, [str(s[m]["labels"].get(l, 0)) for m in models])

    fails = [r for r in _scored(rows) if not r["v"]["passed"]]
    engine = [r for r in rows if r["v"].get("excluded")]
    if engine:
        lines.append("")
        lines.append(f"EXCLUDED ENGINE ERRORS ({len(engine)}; not scored)")
        for r in engine[:failures or len(engine)]:
            lines.append(f"  {_short(r['model'])} · {r['case']} t{r['trial']}: "
                         f"{'; '.join(r['v']['notes'])[:160]}")
    if fails and failures:
        lines.append("")
        lines.append(f"FAILURES (first {failures})")
        for r in fails[:failures]:
            calls = [c["name"] for st in r.get("steps", ()) for c in st.get("calls", ())]
            lines.append(f"  {_short(r['model'])} · {r['case']} t{r['trial']}: "
                         f"{','.join(r['v']['labels'])} — {'; '.join(r['v']['notes'])[:160]}")
            lines.append(f"      calls={calls} final={r.get('final', '')[:100]!r}")
    return "\n".join(lines)


def _base(label: str) -> str:
    name = label.partition(" +")[0]
    return REFERENCE_ALIASES.get(name, name)


def _variant(label: str) -> str:
    return label.partition(" +")[2]


def _paired_sections(ref_rows: list[dict], rows: list[dict], models: list[str], references=(), *,
                     title: str) -> list[str]:
    """PAIRED vs each reference: every model in `rows` against the reference's
    rows in `ref_rows` (the same run, or a pinned baseline run), within one
    thinking variant, plus the gap between two references (what tuning adds).
    References: `references`, else tinyagent-bench's defaults present, else the first model."""
    ref_labels = list(dict.fromkeys(r["model"] for r in ref_rows))
    names = [r for r in (list(references) or list(DEFAULT_REFERENCES))
             if any(_base(m) == REFERENCE_ALIASES.get(r, r) for m in ref_labels)]
    if not names and ref_rows is rows:
        names = [_base(models[0])]
    names = [REFERENCE_ALIASES.get(r, r) for r in names]
    out: list[str] = []
    for ref in names:
        pairs = []
        for m in models:
            base_label = next((x for x in ref_labels if _base(x) == ref and _variant(x) == _variant(m)), None)
            if base_label is None or _base(m) in names:
                continue
            drop = _route_fault_cases(ref_rows, base_label) & _route_fault_cases(rows, m)
            pairs.append(f"  {_short(m)}: {_fmt_paired(paired(case_outcomes(ref_rows, base_label), case_outcomes(rows, m), drop=drop))}")
        if pairs:
            out += ["", f"PAIRED vs {_short(ref)} ({title}; cases both scored; route faults for both dropped; "
                        "* p<0.05, ** p<0.01)", *pairs]
    if len(names) >= 2:
        tuned, base = names[0], names[1]
        for v in sorted({_variant(x) for x in ref_labels}):
            t = next((x for x in ref_labels if _base(x) == tuned and _variant(x) == v), None)
            b = next((x for x in ref_labels if _base(x) == base and _variant(x) == v), None)
            if t and b:
                out += ["", f"REFERENCE GAP {_short(t)} − {_short(b)} (what Harbor tuning adds; {title})",
                        f"  {_fmt_paired(paired(case_outcomes(ref_rows, b), case_outcomes(ref_rows, t)))}"]
    return out


def versus(baseline: list[dict], cur: list[dict], references=(), *, title: str) -> str:
    if kind_of(baseline) != kind_of(cur):
        raise ValueError("a cloud reference is never paired with local models")
    """Every model in `cur` as a paired difference from the reference models
    of a pinned baseline run (`baseline`)."""
    models = list(dict.fromkeys(r["model"] for r in _scored(cur)))
    lines = _paired_sections(baseline, cur, models, references, title=title)
    return "\n".join(lines).strip("\n") or f"(baseline run {title}: no reference model to pair against)"


def compare(base: list[dict], cur: list[dict]) -> str:
    """Per (model, case) pass-rate changes from `base` to `cur`."""
    if kind_of(base) != kind_of(cur):
        raise ValueError("a cloud reference is never compared with local models")
    def rates(rows):
        g: dict[tuple[str, str], list[bool]] = defaultdict(list)
        for r in _scored(rows):
            g[(r["model"], r["case"])].append(r["v"]["passed"])
        return {k: sum(v) / len(v) for k, v in g.items()}
    b, c = rates(base), rates(cur)
    lines = ["COMPARISON (pass rate, baseline -> current)"]
    worse = sorted(k for k in b.keys() & c.keys() if c[k] < b[k])
    better = sorted(k for k in b.keys() & c.keys() if c[k] > b[k])
    for title, keys in (("REGRESSED", worse), ("IMPROVED", better)):
        lines.append(f"{title}: {len(keys)}")
        for m, case in keys:
            lines.append(f"  {_short(m)} · {case}: {b[(m, case)]:.0%} -> {c[(m, case)]:.0%}")
    for m in sorted({k[0] for k in c}):
        bm = [v for k, v in b.items() if k[0] == m]
        cm = [v for k, v in c.items() if k[0] == m]
        if bm and cm:
            lines.append(f"{_short(m)} overall: {statistics.mean(bm):.0%} -> {statistics.mean(cm):.0%}")
            lines.append(f"  paired (current - baseline): "
                         f"{_fmt_paired(paired(case_outcomes(base, m), case_outcomes(cur, m)))}")
    return "\n".join(lines)
