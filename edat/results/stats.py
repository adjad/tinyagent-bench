"""Statistics for tinyagent-bench results. Pure functions, deterministic (fixed seeds).

- wilson(k, n): Wilson score interval for a single rate (02 §5.2).
- case_bootstrap(per_case): percentile interval of the mean over cases,
  resampling cases, never trials (trials of one case are correlated).
- paired_bootstrap(a, b): difference of per-case means on the cases both
  sides have, resampling cases with both sides kept together (02 §5.5).
- pass_all_trials(per_case_trials): share of cases passed on every trial.
- unbiased_pass_k(per_case_trials, k): mean over cases of C(s, k) / C(n, k).
- rank_ranges(intervals): honest rank ranges from overlapping intervals.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Mapping, Sequence

Z95 = 1.959963984540054
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261118


def wilson(k: float, n: float, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval for k successes out of n. None when n is 0."""
    if n <= 0:
        return None
    if k < 0 or k > n:
        raise ValueError(f"wilson: k={k} must be within 0..n={n}")
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (q in 0..1) of already-sorted values."""
    if not sorted_values:
        raise ValueError("percentile of nothing")
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _boot_means(values: Sequence[float], resamples: int, rng: random.Random, *,
                sort: bool = True) -> list[float]:
    """Sorted means of `resamples` with-replacement resamples of `values`.

    Per-case pass rates take few distinct values (0, 1/3, 2/3, 1 with 3
    trials), so a resample is drawn as a multinomial over the distinct values
    (sequential binomials), which has exactly the distribution of drawing n
    cases with replacement and is far faster in pure Python."""
    n = len(values)
    counts: dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    distinct = sorted(counts)
    means = []
    if len(distinct) <= 64 and hasattr(rng, "binomialvariate"):
        probs = [counts[v] / n for v in distinct]
        for _ in range(resamples):
            left, mass, total = n, 1.0, 0.0
            for v, p in zip(distinct[:-1], probs[:-1]):
                if left == 0:
                    break
                c = rng.binomialvariate(left, min(1.0, p / mass)) if mass > 0 else 0
                total += c * v
                left -= c
                mass -= p
            total += left * distinct[-1]
            means.append(total / n)
    else:
        for _ in range(resamples):
            means.append(sum(rng.choices(values, k=n)) / n)
    if sort:
        means.sort()
    return means


def _tag(interval: Sequence[float]) -> str:
    if interval[0] > 0:
        return "better"
    if interval[1] < 0:
        return "worse"
    return "no_clear_difference"


def stratified_composite(strata: Mapping[str, Sequence[float]], weights: Mapping[str, float], *,
                         resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> dict | None:
    """A weighted mean of stratum means (for example the Agentic Score: 7
    families x 2 lanes, each a mean over its cases), with a stratified
    bootstrap 95 % interval: cases are resampled within each stratum.
    Weights are renormalised over the strata that have cases. Pass per-case
    differences as the values to get a paired difference of composites."""
    present = {s: list(v) for s, v in strata.items() if v and weights.get(s, 0) > 0}
    if not present:
        return None
    total_w = sum(weights[s] for s in present)
    w = {s: weights[s] / total_w for s in present}
    value = sum(w[s] * (sum(v) / len(v)) for s, v in present.items())
    rng = random.Random(seed)
    draws = {s: ([sum(v) / len(v)] * resamples if len(set(v)) == 1 else _boot_means(v, resamples, rng, sort=False))
             for s, v in sorted(present.items())}
    comps = sorted(sum(w[s] * draws[s][i] for s in draws) for i in range(resamples))
    interval = [_percentile(comps, 0.025), _percentile(comps, 0.975)]
    return {"value": value, "interval": interval, "tag": _tag(interval),
            "n_cases": sum(len(v) for v in present.values()), "strata": len(present),
            "weights": {s: round(x, 6) for s, x in sorted(w.items())}}


def case_bootstrap(per_case: Sequence[float], *, resamples: int = BOOTSTRAP_RESAMPLES,
                   seed: int = BOOTSTRAP_SEED) -> tuple[float, float] | None:
    """95 % percentile interval for the mean of per-case values, resampling cases."""
    values = list(per_case)
    n = len(values)
    if n == 0:
        return None
    if len(set(values)) == 1:
        return (values[0], values[0])
    means = _boot_means(values, resamples, random.Random(seed))
    return (_percentile(means, 0.025), _percentile(means, 0.975))


def paired_bootstrap(a: Mapping[str, float], b: Mapping[str, float], *,
                     resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> dict | None:
    """Mean of a[c] − b[c] over the cases c both have, with a paired bootstrap
    95 % interval, a tag, and win / loss / tie counts. None when no case is shared."""
    common = sorted(set(a) & set(b))
    n = len(common)
    if n == 0:
        return None
    diffs = [a[c] - b[c] for c in common]
    delta = sum(diffs) / n
    if len(set(diffs)) == 1:
        interval = (diffs[0], diffs[0])
    else:
        means = _boot_means(diffs, resamples, random.Random(seed))
        interval = (_percentile(means, 0.025), _percentile(means, 0.975))
    if interval[0] > 0:
        tag = "better"
    elif interval[1] < 0:
        tag = "worse"
    else:
        tag = "no_clear_difference"
    return {"delta": delta, "interval": list(interval), "tag": tag, "n_cases": n,
            "wins": sum(d > 0 for d in diffs), "losses": sum(d < 0 for d in diffs),
            "ties": sum(d == 0 for d in diffs)}


def flip_tag(tag: str, higher_is_better: bool) -> str:
    """Tags are written for 'higher is better'; flip them for rates where lower is better."""
    if higher_is_better or tag == "no_clear_difference":
        return tag
    return "worse" if tag == "better" else "better"


def pass_all_trials(per_case_trials: Mapping[str, Sequence[bool]]) -> dict | None:
    """Share of cases passed on every one of their trials, Wilson over cases."""
    if not per_case_trials:
        return None
    counts = {len(t) for t in per_case_trials.values()}
    n = len(per_case_trials)
    k = sum(all(t) for t in per_case_trials.values())
    return {"value": k / n, "interval": list(wilson(k, n)), "n_cases": n, "k": k,
            "trials_per_case": sorted(counts),
            "flag": None if len(counts) == 1 and min(counts) >= 3 else
            ("unequal_trials" if len(counts) > 1 else "fewer_than_3_trials")}


def unbiased_pass_k(per_case_trials: Mapping[str, Sequence[bool]], k: int) -> float | None:
    """Mean over cases of C(passes, k) / C(trials, k); cases with fewer than k trials are skipped."""
    vals = [math.comb(sum(t), k) / math.comb(len(t), k) for t in per_case_trials.values() if len(t) >= k]
    return sum(vals) / len(vals) if vals else None


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def rank_ranges(intervals: Mapping[str, tuple[float, float]], higher_is_better: bool = True) -> dict[str, list[int]]:
    """Best and worst rank each entry could hold given the others' intervals.
    Best = 1 + entries certainly ahead (their lower bound above my upper bound);
    worst = number of entries not certainly behind (their upper bound at or above my lower bound)."""
    out = {}
    for key, (lo, hi) in intervals.items():
        if not higher_is_better:
            lo, hi = -hi, -lo
        ahead = behind_not = 0
        for other, (olo, ohi) in intervals.items():
            if other == key:
                continue
            if not higher_is_better:
                olo, ohi = -ohi, -olo
            if olo > hi:
                ahead += 1
            if ohi >= lo:
                behind_not += 1
        out[key] = [1 + ahead, 1 + behind_not]
    return out
