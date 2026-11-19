"""edat.perf.stats: nearest-rank percentiles, summaries, bootstrap CI, geometric mean, line fit."""
from __future__ import annotations

import math
import random

import pytest

from edat.perf import stats as st


def test_nearest_rank_n10():
    vals = [7, 3, 10, 1, 9, 2, 8, 4, 6, 5]          # 1..10 shuffled
    # rank = ceil(p*n/100): p25 -> ceil(2.5)=3 -> 3; p50 -> 5 -> 5; p75 -> ceil(7.5)=8 -> 8; p95 -> ceil(9.5)=10
    assert st.nearest_rank(vals, 25) == 3
    assert st.median(vals) == 5                       # nearest-rank lower median, not 5.5
    assert st.nearest_rank(vals, 75) == 8
    assert st.iqr(vals) == 5
    assert st.nearest_rank(vals, 95) == 10            # at n = 10, p95 is the maximum
    s = st.summary(vals)
    assert "p95" not in s                             # p95 only from n >= 20
    assert (s["n"], s["min"], s["max"], s["mean"]) == (10, 1, 10, 5.5)


def test_nearest_rank_n20():
    vals = list(range(1, 21))
    random.Random(1).shuffle(vals)
    # p25 -> rank 5; p50 -> 10; p75 -> 15; p95 -> 19
    assert st.nearest_rank(vals, 25) == 5
    assert st.median(vals) == 10
    assert st.nearest_rank(vals, 75) == 15
    assert st.iqr(vals) == 10
    assert st.summary(vals)["p95"] == 19


def test_matches_release_performance_definition():
    # scripts/release_performance.py: rank = max(1, (p*n + 99) // 100)
    for n in range(1, 41):
        vals = list(range(n))
        for p in (1, 5, 25, 50, 75, 95, 99, 100):
            assert st.nearest_rank(vals, p) == vals[max(1, (p * n + 99) // 100) - 1]
    with pytest.raises(ValueError):
        st.nearest_rank([], 50)


def test_mean_stdev_sample():
    vals = [2, 4, 4, 4, 5, 5, 7, 9]
    # mean 40/8 = 5; squared deviations sum 32; sample stdev = sqrt(32/7)
    assert st.mean(vals) == 5
    assert st.stdev(vals) == pytest.approx(math.sqrt(32 / 7))
    assert st.stdev([3]) is None


def test_bootstrap_deterministic_and_order_free():
    vals = [10.0, 11.0, 9.5, 12.0, 10.5, 9.0, 13.0, 10.2, 11.7, 9.9]
    a = st.bootstrap_ci_median(vals)
    b = st.bootstrap_ci_median(list(reversed(vals)))
    assert a == b                                    # fixed seed, sorted before resampling
    assert min(vals) <= a[0] <= st.median(vals) <= a[1] <= max(vals)
    assert st.summary(vals)["ci95_median"] == a
    assert st.bootstrap_ci_median([4.0]) == [4.0, 4.0]
    assert st.bootstrap_ci_median([]) is None
    assert st.STATS_METADATA["ci95_median"] == {"method": "percentile", "resamples": 2000,
                                                "seed": st.BOOTSTRAP_SEED, "level": 0.95}
    # a different seed is allowed to differ, and is what the metadata records
    assert st.bootstrap_ci_median(vals, seed=st.BOOTSTRAP_SEED) == a


def test_empty_summary_and_none_dropped():
    assert st.summary([])["n"] == 0 and st.summary([])["median"] is None
    s = st.summary([None, 1.0, None, 3.0])
    assert s["n"] == 2 and s["median"] == 1.0


def test_geometric_mean():
    assert st.geometric_mean([1.6, 0.9]) == pytest.approx(1.2)   # sqrt(1.44)
    assert st.geometric_mean([2, 8]) == pytest.approx(4)
    assert st.geometric_mean([]) is None
    with pytest.raises(ValueError):
        st.geometric_mean([1.0, 0.0])


def test_linear_fit():
    xs = [512, 2048, 10240]
    ys = [3 * x + 2 for x in xs]
    f = st.linear_fit(xs, ys)
    assert f.slope == pytest.approx(3) and f.intercept == pytest.approx(2) and f.r2 == pytest.approx(1)
    # y = x with one point off: x=(0,1,2), y=(0,2,2): slope 1, intercept 1/3, r2 = 0.75
    g = st.linear_fit([0, 1, 2], [0, 2, 2])
    assert g.slope == pytest.approx(1) and g.intercept == pytest.approx(1 / 3) and g.r2 == pytest.approx(0.75)
    with pytest.raises(ValueError):
        st.linear_fit([1], [1])
    with pytest.raises(ValueError):
        st.linear_fit([1, 1], [1, 2])
