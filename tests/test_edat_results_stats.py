"""Unit tests for edat.results.stats: known values, tolerances and determinism."""
from __future__ import annotations

import math
import random
import statistics

import pytest

from edat.results import stats


# --- wilson ---------------------------------------------------------------

def test_wilson_8_of_10():
    lo, hi = stats.wilson(8, 10)
    assert round(lo, 4) == 0.4902
    assert round(hi, 4) == 0.9433


def test_wilson_zero_successes():
    lo, hi = stats.wilson(0, 10)
    assert lo == 0.0
    assert hi == pytest.approx(0.2775, abs=5e-5)


def test_wilson_all_successes():
    lo, hi = stats.wilson(10, 10)
    assert hi == pytest.approx(1.0, abs=1e-9)
    assert hi <= 1.0
    assert lo == pytest.approx(0.7225, abs=5e-5)


def test_wilson_50_of_100():
    lo, hi = stats.wilson(50, 100)
    assert round(lo, 4) == 0.4038
    assert round(hi, 4) == 0.5962


def test_wilson_symmetric_around_half():
    lo, hi = stats.wilson(50, 100)
    assert lo + hi == pytest.approx(1.0)


def test_wilson_empty_returns_none():
    assert stats.wilson(0, 0) is None


def test_wilson_k_out_of_range_raises():
    with pytest.raises(ValueError):
        stats.wilson(11, 10)
    with pytest.raises(ValueError):
        stats.wilson(-1, 10)


def test_wilson_interval_stays_in_unit_range():
    for n in (1, 2, 5, 30):
        for k in range(n + 1):
            lo, hi = stats.wilson(k, n)
            eps = 1e-12
            assert 0.0 <= lo <= k / n + eps
            assert k / n - eps <= hi <= 1.0


# --- pass_all_trials / unbiased_pass_k ------------------------------------

def test_pass_all_trials_value_and_wilson_over_cases():
    per_case = {
        "a": [True, True, True],
        "b": [True, True, False],
        "c": [True, True, True],
        "d": [False, False, False],
    }
    res = stats.pass_all_trials(per_case)
    assert res["k"] == 2
    assert res["n_cases"] == 4
    assert res["value"] == 0.5
    assert res["interval"] == pytest.approx(list(stats.wilson(2, 4)))
    assert res["trials_per_case"] == [3]
    assert res["flag"] is None


def test_pass_all_trials_flags_fewer_than_three():
    res = stats.pass_all_trials({"a": [True, True], "b": [True, False]})
    assert res["flag"] == "fewer_than_3_trials"
    assert res["value"] == 0.5
    assert res["trials_per_case"] == [2]


def test_pass_all_trials_flags_unequal_trials():
    res = stats.pass_all_trials({"a": [True, True, True], "b": [True, True]})
    assert res["flag"] == "unequal_trials"
    assert res["trials_per_case"] == [2, 3]


def test_pass_all_trials_empty_is_none():
    assert stats.pass_all_trials({}) is None


def test_unbiased_pass_k_two_of_three():
    cases = {"a": [True, True, False]}
    assert stats.unbiased_pass_k(cases, 1) == pytest.approx(2 / 3)
    assert stats.unbiased_pass_k(cases, 2) == pytest.approx(1 / 3)
    assert stats.unbiased_pass_k(cases, 3) == 0.0


def test_unbiased_pass_k_means_over_cases():
    cases = {"a": [True, True, False], "b": [True, True, True], "c": [False, False, False]}
    assert stats.unbiased_pass_k(cases, 2) == pytest.approx((1 / 3 + 1 + 0) / 3)
    assert stats.unbiased_pass_k(cases, 3) == pytest.approx(1 / 3)


def test_unbiased_pass_k_skips_cases_with_fewer_than_k_trials():
    cases = {"a": [True], "b": [True, True, False]}
    assert stats.unbiased_pass_k(cases, 2) == pytest.approx(1 / 3)


def test_unbiased_pass_k_none_when_no_case_has_k_trials():
    assert stats.unbiased_pass_k({"a": [True]}, 2) is None
    assert stats.unbiased_pass_k({}, 1) is None


# --- paired_bootstrap -----------------------------------------------------

def test_paired_bootstrap_identical_inputs():
    a = {f"c{i}": (i % 3) / 2 for i in range(30)}
    res = stats.paired_bootstrap(a, dict(a), resamples=500)
    assert res["delta"] == 0
    assert list(res["interval"]) == [0, 0]
    assert res["tag"] == "no_clear_difference"
    assert (res["wins"], res["losses"], res["ties"]) == (0, 0, 30)
    assert res["n_cases"] == 30


def test_paired_bootstrap_clear_shift():
    cases = [f"c{i:02d}" for i in range(40)]
    a = {c: 1.0 for c in cases}
    b = {c: (0.0 if i < 20 else 1.0) for i, c in enumerate(cases)}
    res = stats.paired_bootstrap(a, b, resamples=2000)
    assert res["delta"] == pytest.approx(0.5)
    assert res["tag"] == "better"
    assert res["interval"][0] > 0
    assert res["interval"][0] < 0.5 < res["interval"][1]
    assert (res["wins"], res["losses"], res["ties"]) == (20, 0, 20)
    assert res["n_cases"] == 40


def test_paired_bootstrap_worse_direction():
    cases = [f"c{i:02d}" for i in range(40)]
    a = {c: (0.0 if i < 20 else 1.0) for i, c in enumerate(cases)}
    b = {c: 1.0 for c in cases}
    res = stats.paired_bootstrap(a, b, resamples=2000)
    assert res["delta"] == pytest.approx(-0.5)
    assert res["tag"] == "worse"
    assert res["interval"][1] < 0
    assert (res["wins"], res["losses"], res["ties"]) == (0, 20, 20)


def test_paired_bootstrap_wins_losses_ties_mixed():
    a = {"w1": 1.0, "w2": 0.5, "l1": 0.0, "t1": 1.0}
    b = {"w1": 0.0, "w2": 0.0, "l1": 1.0, "t1": 1.0}
    res = stats.paired_bootstrap(a, b, resamples=200)
    assert (res["wins"], res["losses"], res["ties"]) == (2, 1, 1)
    assert res["delta"] == pytest.approx((1 + 0.5 - 1 + 0) / 4)


def test_paired_bootstrap_uses_only_shared_cases():
    a = {"x": 1.0, "y": 1.0, "only_a": 0.0}
    b = {"x": 0.0, "y": 0.0, "only_b": 1.0}
    res = stats.paired_bootstrap(a, b, resamples=200)
    assert res["n_cases"] == 2
    assert res["delta"] == 1.0
    assert res["ties"] == 0


def test_paired_bootstrap_no_shared_cases_is_none():
    assert stats.paired_bootstrap({"a": 1.0}, {"b": 1.0}) is None


def test_paired_bootstrap_is_deterministic():
    a = {f"c{i}": random.Random(i).random() for i in range(25)}
    b = {f"c{i}": random.Random(100 + i).random() for i in range(25)}
    r1 = stats.paired_bootstrap(a, b, resamples=1000)
    r2 = stats.paired_bootstrap(a, b, resamples=1000)
    assert r1 == r2


def test_paired_bootstrap_seed_changes_interval_but_not_delta():
    a = {f"c{i}": random.Random(i).random() for i in range(25)}
    b = {f"c{i}": random.Random(100 + i).random() for i in range(25)}
    r1 = stats.paired_bootstrap(a, b, resamples=500, seed=1)
    r2 = stats.paired_bootstrap(a, b, resamples=500, seed=2)
    assert r1["delta"] == r2["delta"]
    assert r1["interval"] != r2["interval"]


# --- case_bootstrap -------------------------------------------------------

def test_case_bootstrap_constant_has_zero_width():
    assert stats.case_bootstrap([0.5] * 12) == (0.5, 0.5)
    assert stats.case_bootstrap([1.0] * 3, resamples=50) == (1.0, 1.0)


def test_case_bootstrap_empty_is_none():
    assert stats.case_bootstrap([]) is None


def test_case_bootstrap_interval_contains_mean():
    values = [0, 1 / 3, 2 / 3, 1, 1, 0, 2 / 3, 1 / 3, 1, 1, 0, 0]
    lo, hi = stats.case_bootstrap(values, resamples=2000)
    assert lo < statistics.mean(values) < hi
    assert 0.0 <= lo and hi <= 1.0


def test_case_bootstrap_is_deterministic():
    values = [0, 1, 1, 0, 1, 1, 1, 0, 1, 1]
    assert stats.case_bootstrap(values, resamples=1000) == stats.case_bootstrap(values, resamples=1000)


def test_case_bootstrap_matches_analytic_width_roughly():
    values = [0.0, 1.0] * 50
    lo, hi = stats.case_bootstrap(values, resamples=4000)
    expected_half = 1.96 * 0.5 / math.sqrt(100)
    assert (hi - lo) / 2 == pytest.approx(expected_half, rel=0.15)


# --- the multinomial resampler -------------------------------------------

def test_boot_means_is_unbiased_and_has_the_right_spread():
    values = [0, 1 / 3, 2 / 3, 1] * 25
    means = stats._boot_means(values, 20000, random.Random(1), sort=False)
    assert len(means) == 20000
    sample_mean = statistics.mean(values)
    assert abs(statistics.mean(means) - sample_mean) < 0.01
    analytic_se = statistics.pstdev(values) / math.sqrt(len(values))
    assert statistics.pstdev(means) == pytest.approx(analytic_se, rel=0.10)


def test_boot_means_skewed_values_unbiased():
    values = [0.0] * 90 + [1.0] * 10
    means = stats._boot_means(values, 20000, random.Random(7), sort=False)
    assert abs(statistics.mean(means) - 0.1) < 0.01
    analytic_se = statistics.pstdev(values) / math.sqrt(len(values))
    assert statistics.pstdev(means) == pytest.approx(analytic_se, rel=0.10)


def test_boot_means_sorted_by_default_and_stay_in_range():
    values = [0, 1 / 3, 2 / 3, 1] * 5
    means = stats._boot_means(values, 500, random.Random(3))
    assert means == sorted(means)
    assert 0.0 <= means[0] and means[-1] <= 1.0


def test_boot_means_fallback_path_for_many_distinct_values():
    values = [i / 100 for i in range(100)]  # more than 64 distinct values
    means = stats._boot_means(values, 5000, random.Random(5), sort=False)
    assert abs(statistics.mean(means) - statistics.mean(values)) < 0.01
    analytic_se = statistics.pstdev(values) / math.sqrt(len(values))
    assert statistics.pstdev(means) == pytest.approx(analytic_se, rel=0.10)


def test_boot_means_single_distinct_value():
    assert all(m == pytest.approx(0.4) for m in stats._boot_means([0.4] * 6, 20, random.Random(1)))


# --- stratified_composite -------------------------------------------------

def test_stratified_composite_value_is_weighted_mean():
    strata = {"a": [1.0, 0.0, 1.0, 1.0], "b": [0.0, 0.5]}
    res = stats.stratified_composite(strata, {"a": 0.75, "b": 0.25}, resamples=500)
    assert res["value"] == pytest.approx(0.75 * 0.75 + 0.25 * 0.25)
    assert res["n_cases"] == 6
    assert res["strata"] == 2
    assert res["weights"] == {"a": 0.75, "b": 0.25}
    assert res["interval"][0] <= res["value"] <= res["interval"][1]


def test_stratified_composite_renormalises_over_present_strata():
    strata = {"ask": [1.0, 0.0], "brief": [1.0, 1.0], "day": []}
    weights = {"ask": 0.5, "brief": 0.25, "day": 0.25}
    res = stats.stratified_composite(strata, weights, resamples=300)
    assert res["strata"] == 2
    assert res["weights"] == {"ask": pytest.approx(2 / 3, abs=1e-5), "brief": pytest.approx(1 / 3, abs=1e-5)}
    assert res["value"] == pytest.approx((0.5 * 0.5 + 0.25 * 1.0) / 0.75)


def test_stratified_composite_ignores_zero_weight_strata():
    res = stats.stratified_composite({"a": [1.0], "b": [0.0]}, {"a": 1.0, "b": 0}, resamples=100)
    assert res["strata"] == 1
    assert res["value"] == 1.0


def test_stratified_composite_nothing_present_is_none():
    assert stats.stratified_composite({}, {}) is None
    assert stats.stratified_composite({"a": []}, {"a": 1.0}) is None


def test_stratified_composite_constant_strata_zero_width():
    res = stats.stratified_composite({"a": [1.0, 1.0], "b": [0.0, 0.0]}, {"a": 0.5, "b": 0.5}, resamples=200)
    assert res["value"] == 0.5
    assert res["interval"] == [0.5, 0.5]


def test_stratified_composite_is_deterministic_and_tagged():
    strata = {"a": [0.2, 0.4, 0.9, 1.0], "b": [0.1, 0.5, 0.6]}
    w = {"a": 0.5, "b": 0.5}
    assert stats.stratified_composite(strata, w, resamples=400) == stats.stratified_composite(strata, w, resamples=400)
    clearly_positive = stats.stratified_composite({"a": [0.5, 0.6, 0.7, 0.5]}, {"a": 1.0}, resamples=300)
    assert clearly_positive["tag"] == "better"


# --- rank_ranges ----------------------------------------------------------

def test_rank_ranges_separated_intervals():
    out = stats.rank_ranges({"a": (0.8, 0.9), "b": (0.5, 0.6), "c": (0.1, 0.2)})
    assert out == {"a": [1, 1], "b": [2, 2], "c": [3, 3]}


def test_rank_ranges_overlapping_intervals_share_ranges():
    out = stats.rank_ranges({"a": (0.6, 0.9), "b": (0.5, 0.8), "c": (0.1, 0.2)})
    assert out["a"] == [1, 2]
    assert out["b"] == [1, 2]
    assert out["c"] == [3, 3]


def test_rank_ranges_all_overlapping():
    out = stats.rank_ranges({"a": (0.4, 0.8), "b": (0.5, 0.9), "c": (0.45, 0.85)})
    assert all(r == [1, 3] for r in out.values())


def test_rank_ranges_lower_is_better_flips_order():
    ivs = {"a": (0.8, 0.9), "b": (0.5, 0.6), "c": (0.1, 0.2)}
    out = stats.rank_ranges(ivs, higher_is_better=False)
    assert out == {"a": [3, 3], "b": [2, 2], "c": [1, 1]}


def test_rank_ranges_single_entry():
    assert stats.rank_ranges({"only": (0.1, 0.2)}) == {"only": [1, 1]}


def test_rank_ranges_touching_intervals_count_as_overlap():
    out = stats.rank_ranges({"a": (0.5, 0.7), "b": (0.3, 0.5)})
    assert out["a"] == [1, 2]
    assert out["b"] == [1, 2]


# --- flip_tag -------------------------------------------------------------

@pytest.mark.parametrize("tag,higher,expected", [
    ("better", True, "better"),
    ("worse", True, "worse"),
    ("better", False, "worse"),
    ("worse", False, "better"),
    ("no_clear_difference", True, "no_clear_difference"),
    ("no_clear_difference", False, "no_clear_difference"),
])
def test_flip_tag(tag, higher, expected):
    assert stats.flip_tag(tag, higher) == expected


def test_median_helper():
    assert stats.median([3, 1, 2]) == 2
    assert stats.median([]) is None
