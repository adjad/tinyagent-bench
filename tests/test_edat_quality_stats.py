"""Tests for edat.quality.stats (Bradley-Terry, style control, bootstrap, Wilson, kappa)."""
from __future__ import annotations

import math
import random

import pytest

from edat.quality import stats as S

ANCHOR = "Gemma-Wisp"


def _simulate(strengths, n_per_pair, seed):
    rng = random.Random(seed)
    models = sorted(strengths)
    comps = []
    for i, a in enumerate(models):
        for b in models[i + 1:]:
            p = 1 / (1 + math.exp(-(strengths[a] - strengths[b])))
            for _ in range(n_per_pair):
                comps.append((a, b, "A" if rng.random() < p else "B"))
    return comps


def test_bt_recovers_known_strengths():
    true = {ANCHOR: 0.0, "m1": 0.8, "m2": -0.6, "m3": 1.5}
    est = S.bradley_terry(_simulate(true, 600, seed=1))
    assert est[ANCHOR] == 0.0
    for m, v in true.items():
        assert est[m] == pytest.approx(v, abs=0.2), m


def test_bt_ties_count_as_half():
    comps = [("x", ANCHOR, "A")] * 10 + [("x", ANCHOR, "B")] * 10 + [("x", ANCHOR, "tie")] * 40
    est = S.bradley_terry(comps)
    assert est["x"] == pytest.approx(0.0, abs=0.01)
    # all ties: equal, whichever tie label is used
    comps = [("x", ANCHOR, "tie_good")] * 20 + [("x", ANCHOR, "tie_bad")] * 20
    assert S.bradley_terry(comps)["x"] == pytest.approx(0.0, abs=0.01)
    # 30 wins + 10 losses + 40 ties = 50 vs 30 wins: strength ln(5/3)
    comps = [("x", ANCHOR, "A")] * 30 + [("x", ANCHOR, "B")] * 10 + [("x", ANCHOR, "tie")] * 40
    assert S.bradley_terry(comps)["x"] == pytest.approx(math.log(5 / 3), abs=0.02)


def test_bt_anchor_is_fifty():
    true = {ANCHOR: 0.0, "m1": 1.0}
    est = S.bradley_terry(_simulate(true, 300, seed=2))
    rates = S.bt_win_rates(est)
    assert rates[ANCHOR] == pytest.approx(50.0)
    assert rates["m1"] > 60.0
    # maximum likelihood: 3:1 wins gives strength ln 3
    est = S.bradley_terry([("x", ANCHOR, "A")] * 75 + [("x", ANCHOR, "B")] * 25)
    assert est["x"] == pytest.approx(math.log(3), abs=0.03)
    assert S.strength_to_win_rate(est["x"]) == pytest.approx(75.0, abs=0.7)


def test_bt_bootstrap_deterministic_and_brackets():
    comps = _simulate({ANCHOR: 0.0, "m1": 0.7}, 200, seed=3)
    a = S.bt_bootstrap(comps, n=40, seed=5)
    b = S.bt_bootstrap(comps, n=40, seed=5)
    assert a == b
    assert a["m1"]["lo"] < a["m1"]["strength"] < a["m1"]["hi"]
    assert a[ANCHOR]["lo"] == a[ANCHOR]["hi"] == 0.0


def test_style_control_removes_pure_length_effect():
    rng = random.Random(4)
    comps = []
    for _ in range(400):
        # model "long" is usually longer than the anchor, but quality is equal:
        # the longer answer always wins.
        longer = rng.random() < 0.8
        x = math.log(rng.uniform(1.3, 2.0)) * (1 if longer else -1)
        longer_wins = rng.random() < 0.95  # the longer answer (almost) always wins
        a_wins = (x > 0) == longer_wins
        comps.append(("long", ANCHOR, "A" if a_wins else "B", {"log_len": x}))
    raw = S.bradley_terry([(a, b, o) for a, b, o, _ in comps])
    raw_rate = S.strength_to_win_rate(raw["long"])
    ctrl = S.style_controlled_win_rate(comps)
    assert raw_rate > 70.0
    assert ctrl[ANCHOR] == pytest.approx(50.0)
    assert ctrl["long"] == pytest.approx(50.0, abs=8.0)
    fit = S.style_controlled_bt(comps)
    assert fit["coef"]["log_len"] > 1.0
    assert set(fit["coef"]) == set(S.STYLE_FEATURES)


def test_style_control_keeps_real_quality_gap():
    rng = random.Random(6)
    comps = []
    for _ in range(600):
        x = rng.gauss(0, 0.5)
        p = 1 / (1 + math.exp(-(1.0 + 1.5 * x)))
        comps.append(("good", ANCHOR, "A" if rng.random() < p else "B", [x, 0, 0, 0, 0]))
    ctrl = S.style_controlled_win_rate(comps)
    assert ctrl["good"] == pytest.approx(100 / (1 + math.exp(-1.0)), abs=5.0)


def test_wilson_known_values():
    lo, hi = S.wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    lo, hi = S.wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3)
    assert hi == pytest.approx(0.5962, abs=1e-3)
    assert S.wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = S.wilson_interval(0, 20)
    assert lo == 0.0 and hi == pytest.approx(0.1611, abs=1e-3)


def test_win_rate():
    assert S.win_rate(["win", "loss", "tie", "tie"]) == pytest.approx(50.0)
    assert S.win_rate(["win", "win", "tie"]) == pytest.approx(100 * 2.5 / 3)
    assert math.isnan(S.win_rate([]))


def test_paired_bootstrap_deterministic_and_labels():
    ref = {f"c{i}": 50.0 + (i % 5) for i in range(40)}
    better = {k: v + 3.0 + ((int(k[1:]) % 3) - 1) for k, v in ref.items()}
    worse = {k: v - 3.0 for k, v in ref.items()}
    same = {k: v + (1.0 if int(k[1:]) % 2 else -1.0) for k, v in ref.items()}
    a = S.paired_bootstrap(better, ref, n=500, seed=7)
    assert a == S.paired_bootstrap(better, ref, n=500, seed=7)
    assert a["label"] == "better" and a["n_cases"] == 40
    assert a["delta"] == pytest.approx(3.0, abs=0.1)
    assert a["lo"] <= a["delta"] <= a["hi"]
    assert S.paired_bootstrap(worse, ref, n=500, seed=7)["label"] == "worse"
    assert S.paired_bootstrap(same, ref, n=500, seed=7)["label"] == "approx_baseline"
    # only shared cases count
    part = dict(list(better.items())[:10])
    assert S.paired_bootstrap(part, ref, n=100, seed=1)["n_cases"] == 10


def test_bootstrap_mean_and_ratio_median():
    vals = {f"c{i}": float(i) for i in range(20)}
    r = S.bootstrap_mean(vals, n=300, seed=3)
    assert r == S.bootstrap_mean(vals, n=300, seed=3)
    assert r["mean"] == pytest.approx(9.5)
    assert r["lo"] < 9.5 < r["hi"]
    cand = {"a": 50.0, "b": 80.0, "c": 30.0, "d": 7.0}
    ref = {"a": 100.0, "b": 100.0, "c": 100.0, "d": 0.0, "e": 5.0}
    assert S.paired_ratio_median(cand, ref) == pytest.approx(0.5)
    assert math.isnan(S.paired_ratio_median({}, ref))


def test_cohen_kappa_known_tables():
    a = ["A", "A", "B", "B"]
    assert S.cohen_kappa(a, a) == pytest.approx(1.0)
    # classic 2x2: 20 agree yes, 15 agree no, 5 + 10 disagree -> kappa 0.4
    la = ["A"] * 25 + ["B"] * 25
    lb = ["A"] * 20 + ["B"] * 5 + ["A"] * 10 + ["B"] * 15
    assert S.cohen_kappa(la, lb) == pytest.approx(0.4)
    # chance agreement only
    assert S.cohen_kappa(["A", "A", "B", "B"], ["A", "B", "A", "B"]) == pytest.approx(0.0)
    four = ["A", "B", "tie_good", "tie_bad"]
    assert S.cohen_kappa(four, four) == pytest.approx(1.0)
    assert S.cohen_kappa(["A"] * 4, ["A"] * 4) == 1.0


def test_tuning_pref_and_style_share():
    outs = ["win"] * 90 + ["loss"] * 30
    t = S.tuning_pref(outs)
    assert t["tuning_pref"] == pytest.approx(25.0)
    assert t["excludes_zero"] and t["lo"] > 50.0
    assert S.style_share(t, 10.0) == pytest.approx(0.6)
    assert S.style_share(t, {"tuning_pref": 25.0}) == pytest.approx(0.0)
    weak = S.tuning_pref(["win"] * 6 + ["loss"] * 4)
    assert not weak["excludes_zero"]
    assert S.style_share(weak, 1.0) is None
    assert S.tuning_pref([])["n"] == 0
