"""Statistics for EDAT performance cells (spec 04 §3.9, §6.4).

Percentiles are **nearest-rank**, the definition ``scripts/release_performance.py``
uses (``rank = max(1, ceil(p × n / 100))``, 1-based, on the sorted sample). The
median and the quartiles use the same rule, so at n = 10 the median is the 5th
value, p25 the 3rd, p75 the 8th and p95 the 10th (the maximum, which is why p95 is
reported only from n ≥ 20). The iPhone runner's on-phone preview uses the same
median (``Stats.nearestRank(v, 50)``).

The 95 % confidence interval of the median is a percentile bootstrap: 2,000
resamples with replacement from a ``random.Random`` seeded with a fixed, recorded
seed, the nearest-rank median of each, then the nearest-rank 2.5th and 97.5th
percentiles of those medians. The same values always give the same interval.
"""
from __future__ import annotations

import math
import random
import statistics
from fractions import Fraction
from typing import Iterable, NamedTuple, Sequence

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261118
BOOTSTRAP_METHOD = "percentile"
CI_LEVEL = 0.95
P95_MIN_N = 20

STATS_METADATA = {
    "percentile": "nearest-rank (scripts/release_performance.py)",
    "median": "nearest-rank p50",
    "iqr": "nearest-rank p75 - p25",
    "stdev": "sample (n - 1)",
    "ci95_median": {"method": BOOTSTRAP_METHOD, "resamples": BOOTSTRAP_RESAMPLES,
                    "seed": BOOTSTRAP_SEED, "level": CI_LEVEL},
    "p95_min_n": P95_MIN_N,
}


def _numbers(values: Iterable) -> list:
    out = []
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        if not isinstance(v, (int, float)):
            raise TypeError(f"not a number: {v!r}")
        if isinstance(v, float) and not math.isfinite(v):
            continue
        out.append(v)
    return out


def _rank(percentile: float, n: int) -> int:
    rank = math.ceil(Fraction(percentile) * n / 100)
    return min(n, max(1, rank))


def nearest_rank(values: Sequence, percentile: float):
    """The nearest-rank percentile; raises on an empty sample, like the release script."""
    vals = _numbers(values)
    if not vals:
        raise ValueError("percentile of an empty sample")
    if not 0 <= percentile <= 100:
        raise ValueError(f"percentile {percentile} outside 0..100")
    ordered = sorted(vals)
    return ordered[_rank(percentile, len(ordered)) - 1]


percentile = nearest_rank


def median(values: Sequence):
    vals = _numbers(values)
    return nearest_rank(vals, 50) if vals else None


def iqr(values: Sequence):
    vals = _numbers(values)
    return nearest_rank(vals, 75) - nearest_rank(vals, 25) if vals else None


def mean(values: Sequence):
    vals = _numbers(values)
    return statistics.fmean(vals) if vals else None


def stdev(values: Sequence):
    vals = _numbers(values)
    return statistics.stdev(vals) if len(vals) >= 2 else None


def bootstrap_ci_median(values: Sequence, *, resamples: int = BOOTSTRAP_RESAMPLES,
                        seed: int = BOOTSTRAP_SEED, level: float = CI_LEVEL) -> list | None:
    """Percentile-bootstrap interval ``[lo, hi]`` of the nearest-rank median."""
    vals = _numbers(values)
    if not vals:
        return None
    ordered = sorted(vals)
    n = len(ordered)
    if n == 1:
        return [ordered[0], ordered[0]]
    rng = random.Random(seed)
    r = _rank(50, n) - 1
    idx = range(n)
    medians = []
    for _ in range(resamples):
        draw = sorted(rng.choices(idx, k=n))
        medians.append(ordered[draw[r]])
    tail = (1 - level) / 2 * 100
    return [nearest_rank(medians, tail), nearest_rank(medians, 100 - tail)]


def summary(values: Iterable) -> dict:
    """``{n, median, iqr, min, max, ci95_median, mean, stdev}``, plus ``p95`` when n ≥ 20.

    ``None`` values are dropped (n counts the known values); an empty sample gives
    n = 0 and every statistic ``None``.
    """
    vals = _numbers(values)
    n = len(vals)
    if not n:
        return {"n": 0, "median": None, "iqr": None, "min": None, "max": None,
                "ci95_median": None, "mean": None, "stdev": None}
    out = {"n": n, "median": median(vals), "iqr": iqr(vals), "min": min(vals), "max": max(vals),
           "ci95_median": bootstrap_ci_median(vals), "mean": mean(vals), "stdev": stdev(vals)}
    if n >= P95_MIN_N:
        out["p95"] = nearest_rank(vals, 95)
    return out


def geometric_mean(values: Iterable):
    """exp(mean(log v)); ``None`` for an empty sample; raises on a value ≤ 0."""
    vals = _numbers(values)
    if not vals:
        return None
    if any(v <= 0 for v in vals):
        raise ValueError("geometric mean needs positive values")
    return math.exp(math.fsum(math.log(v) for v in vals) / len(vals))


geomean = geometric_mean


class LineFit(NamedTuple):
    slope: float
    intercept: float
    r2: float


def linear_fit(xs: Sequence, ys: Sequence) -> LineFit:
    """Ordinary least squares y = slope·x + intercept, with r².

    Needs at least two points and two distinct x values. A perfectly flat y gives r² = 1.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys differ in length")
    pts = [(float(x), float(y)) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 2:
        raise ValueError("a line fit needs at least two points")
    n = len(pts)
    mx = math.fsum(p[0] for p in pts) / n
    my = math.fsum(p[1] for p in pts) / n
    sxx = math.fsum((p[0] - mx) ** 2 for p in pts)
    if sxx == 0:
        raise ValueError("a line fit needs two distinct x values")
    sxy = math.fsum((p[0] - mx) * (p[1] - my) for p in pts)
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = math.fsum((p[1] - my) ** 2 for p in pts)
    ss_res = math.fsum((p[1] - (slope * p[0] + intercept)) ** 2 for p in pts)
    r2 = 1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot
    return LineFit(slope, intercept, r2)
