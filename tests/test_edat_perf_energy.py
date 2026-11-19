"""Arithmetic of every derived energy number, on hand-made SYNTHETIC inputs (no real measurements)."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from edat.perf.constants import SHAPE_B_TOKENS
from edat.perf.energy import (active_J, battery_crosscheck, drain_estimate, error_bound, idle_bracket,
                              idle_resident, integrate_J, j_per_task, mean_power_W, median_power_W,
                              pair_cell, pair_k, request_energies, void_foreign, window_energy)
from edat.perf.power import PowerTrace, read_trace

S = 1_000_000_000
FIX = Path(__file__).resolve().parent.parent / "test_fixtures" / "perf" / "power"


def mk(points, rate=20.0, source="smc_pstr", scope="whole_device_internal"):
    return PowerTrace(source, scope, rate, False, [int(t * S) for t, _ in points], [w for _, w in points])


def step_trace(segments, idle=4.0, end_s=50):
    """Idle baseline readings every second, with constant-power segments (t0_s, t1_s, W) laid over
    them. Edge readings sit 1 ns outside each segment so the ramps are negligible."""
    pts = {}
    for k in range(end_s + 1):
        if not any(a <= k <= b for a, b, _ in segments):
            pts[k * S] = idle
    for a, b, w in segments:
        a, b = int(a * S), int(b * S)
        pts[a - 1], pts[a], pts[b], pts[b + 1] = idle, w, w, idle
    ts = sorted(pts)
    return PowerTrace("smc_pstr", "whole_device_internal", 20.0, False, ts, [pts[t] for t in ts])


# ---------------------------------------------------------------------------- integration

def test_integrate_constant_and_active():
    # 4 W idle, 10 W for 20 s..30 s: total 10 s x 10 W = 100 J; active = 100 - 4 W x 10 s = 60 J.
    tr = step_trace([(20, 30, 10.0)])
    assert integrate_J(tr, 20 * S, 30 * S) == pytest.approx(100.0, abs=1e-6)
    assert active_J(tr, 20 * S, 30 * S, 4.0) == pytest.approx(60.0, abs=1e-6)
    assert mean_power_W(tr, 20 * S, 30 * S) == pytest.approx(10.0, abs=1e-6)


def test_integrate_interpolates_window_edges():
    # fixture: 1 s readings, 4 W at 0-2 s, 10 W at 3-5 s, 4 W after. Window 3.5 s..5.5 s:
    #   3.5->4: 10 x 0.5 = 5; 4->5: 10 x 1 = 10; 5->5.5: P(5.5) = 7, mean (10+7)/2 x 0.5 = 4.25; total 19.25 J.
    tr = read_trace(FIX / "synthetic_pstr_trace.jsonl")
    t = tr.t_ns[0]
    assert integrate_J(tr, t + int(3.5 * S), t + int(5.5 * S)) == pytest.approx(19.25)
    assert mean_power_W(tr, t + int(3.5 * S), t + int(5.5 * S)) == pytest.approx(9.625)
    # readings 2..6 s are 4,10,10,10,4 W: median 10
    assert median_power_W(tr, t + 2 * S, t + 6 * S) == 10.0


def test_integrate_window_between_two_readings_interpolates_both_edges():
    tr = mk([(0, 0.0), (10, 10.0)])
    # P(t) = t: from 2 s to 4 s is the area (2+4)/2 x 2 = 6 J
    assert integrate_J(tr, 2 * S, 4 * S) == pytest.approx(6.0)


def test_integrate_zero_order_hold_one_side_and_empty_overlap():
    tr = mk([(10, 5.0), (12, 5.0)])
    # window 8..11: held 5 W from 8 to 10, then 5 W: 3 s x 5 = 15 J
    assert integrate_J(tr, 8 * S, 11 * S) == pytest.approx(15.0)
    # window 11..14: 5 W held beyond the last reading: 15 J
    assert integrate_J(tr, 11 * S, 14 * S) == pytest.approx(15.0)
    with pytest.raises(ValueError):
        integrate_J(tr, 0, 5 * S)            # entirely before
    with pytest.raises(ValueError):
        integrate_J(tr, 12 * S, 20 * S)      # entirely after
    with pytest.raises(ValueError):
        integrate_J(tr, 11 * S, 11 * S)      # empty window
    with pytest.raises(ValueError):
        integrate_J(mk([]), 0, S)


def test_median_needs_readings():
    with pytest.raises(ValueError):
        median_power_W(mk([(1, 5.0)]), 5 * S, 6 * S)


# ---------------------------------------------------------------------------- idle

def test_idle_bracket_flat():
    tr = step_trace([(20, 30, 10.0)])
    b = idle_bracket(tr, 20 * S, 30 * S, idle_s=10)
    assert (b["idle_before_W"], b["idle_after_W"], b["p_idle_W"]) == (4.0, 4.0, 4.0)
    assert b["idle_drift_frac"] == 0.0 and not b["idle_drift_flag"] and not b["idle_one_sided"]


def test_idle_drift_flag_at_15_percent():
    def tr(after):
        return mk([(i, 4.0) for i in range(10, 21)] + [(25, 9.0)] + [(i, after) for i in range(30, 41)])
    # before 4, after 4.6: p_idle 4.3, drift 0.6/4.3 = 0.1395 -> no flag
    b = idle_bracket(tr(4.6), 20 * S, 30 * S, idle_s=10)
    assert b["p_idle_W"] == pytest.approx(4.3) and b["idle_drift_frac"] == pytest.approx(0.6 / 4.3)
    assert not b["idle_drift_flag"]
    # after 5: p_idle 4.5, drift 1/4.5 = 0.2222 -> flag
    b = idle_bracket(tr(5.0), 20 * S, 30 * S, idle_s=10)
    assert b["idle_drift_frac"] == pytest.approx(1 / 4.5) and b["idle_drift_flag"]
    assert "idle_drift" in b["flags"]


def test_idle_one_sided():
    tr = mk([(i, 4.0) for i in range(10, 21)] + [(25, 9.0)])
    b = idle_bracket(tr, 20 * S, 30 * S, idle_s=10)
    assert b["idle_after_W"] is None and b["p_idle_W"] == 4.0
    assert b["idle_one_sided"] and "idle_one_sided" in b["flags"]
    with pytest.raises(ValueError):
        idle_bracket(mk([(25, 9.0)]), 20 * S, 30 * S, idle_s=10)


def test_window_energy_fields():
    tr = step_trace([(20, 30, 10.0)])
    w = window_energy(tr, {"t_start_ns": 20 * S, "t_end_ns": 30 * S, "idle_window_s": 10})
    assert w["energy_total_J"] == pytest.approx(100.0, abs=1e-6)
    assert w["energy_active_J"] == pytest.approx(60.0, abs=1e-6)
    assert (w["idle_before_W"], w["idle_after_W"], w["idle_window_s"]) == (4.0, 4.0, 10)
    assert w["flags"] == []


# ---------------------------------------------------------------------------- error bound

def test_error_bound_components():
    # P 50 W, 20 Hz (dt 0.05 s), K 20, E_req 100 J: edge = 2 x 50 x 0.05 / (20 x 100) = 5/2000 = 0.0025
    # before 4 W, after 4.6 W, window 100 s, E_active 300 J: idle = 0.6 x 100 / 2 / 300 = 0.1
    # source 0.01; total = sqrt(0.0025^2 + 0.1^2 + 0.01^2) = sqrt(0.01010625) = 0.100529...
    e = error_bound(50.0, 20.0, 20, 100.0, 4.0, 4.6, 100.0, 300.0)
    assert e["edge_rel"] == pytest.approx(0.0025)
    assert e["idle_drift_rel"] == pytest.approx(0.1)
    assert e["source_rel"] == 0.01
    assert e["error_bound_rel"] == pytest.approx(math.sqrt(0.01010625))
    assert e["error_bound_pct"] == pytest.approx(10.0529, abs=1e-3)


def test_error_bound_shrinks_with_k_and_validates():
    a = error_bound(50.0, 20.0, 5, 100.0, 4.0, 4.0, 100.0, 300.0)
    b = error_bound(50.0, 20.0, 20, 100.0, 4.0, 4.0, 100.0, 300.0)
    assert a["edge_rel"] == pytest.approx(4 * b["edge_rel"])
    assert b["idle_drift_rel"] == 0.0
    with pytest.raises(ValueError):
        error_bound(50.0, 20.0, 0, 100.0, 4.0, 4.0, 100.0, 300.0)
    with pytest.raises(ValueError):
        error_bound(50.0, 20.0, 5, 100.0, 4.0, 4.0, 100.0, 0.0)


# ---------------------------------------------------------------------------- pair method

def scenario(k=3, c_energy=None, a_tokens=1, b_tokens=SHAPE_B_TOKENS):
    """Idle 4 W. Each pair: A for 1 s at 10 W (active 6 J), B for 2 s at 13.4 W (active 2 x 9.4 = 18.8 J).
    So E_B - E_A = 12.8 J = 256 x 0.05: e_dec = 0.05 J/token exactly. Optional C for 1.5 s whose active
    energy is c_energy."""
    period = 8 if c_energy is not None else 5
    segs, samples = [], []
    for i in range(k):
        s = 20 + i * period
        segs += [(s, s + 1, 10.0), (s + 2, s + 4, 13.4)]
        samples += [dict(shape="A", t_submit_ns=s * S, t_end_ns=(s + 1) * S, status="ok", output_tokens=a_tokens,
                         cached_tokens=0),
                    dict(shape="B", t_submit_ns=(s + 2) * S, t_end_ns=(s + 4) * S, status="ok",
                         output_tokens=b_tokens, cached_tokens=0)]
        if c_energy is not None:
            segs.append((s + 5, s + 6.5, 4.0 + c_energy / 1.5))
            samples.append(dict(shape="C", t_submit_ns=(s + 5) * S, t_end_ns=int((s + 6.5) * S), status="ok",
                                output_tokens=65, cached_tokens=0))
    window = {"t_start_ns": 20 * S, "t_end_ns": (20 + k * period) * S, "idle_window_s": 10}
    return samples, step_trace(segs, end_s=20 + k * period + 12), window


def test_pair_cell_exact_decode_and_prefill():
    samples, tr, window = scenario()
    r = pair_cell(samples, tr, window, level_tokens=2048)
    assert r["E_A_J"] == pytest.approx(6.0, abs=1e-5)
    assert r["E_B_J"] == pytest.approx(18.8, abs=1e-5)
    # e_dec = (18.8 - 6) / 256 = 0.05 J/token = 50 mJ/token
    assert r["e_dec_mJ_per_tok"] == pytest.approx(50.0, abs=1e-4)
    # E_prefill = 6 - 0.05 = 5.95 J; e_pre = 5.95 / 2048 J = 2.9053 mJ/token
    assert r["E_prefill_J"] == pytest.approx(5.95, abs=1e-5)
    assert r["e_pre_mJ_per_tok"] == pytest.approx(5.95 / 2048 * 1000, abs=1e-5)
    assert (r["k_A"], r["k_B"], r["n_voided"]) == (3, 3, 0)
    assert r["tokens_basis"] == "nominal"
    assert (r["scope"], r["source"], r["is_estimate"]) == ("whole_device_internal", "smc_pstr", False)
    assert r["p_idle_W"] == pytest.approx(4.0)
    assert r["error_bound_pct"] is not None and r["error_bound_pct"] > 1.0   # source term alone is 1 %
    assert r["flags"] == []


def test_pair_cell_uses_actual_output_tokens_when_they_differ():
    samples, tr, window = scenario(b_tokens=200)
    r = pair_cell(samples, tr, window, level_tokens=1000)
    # actual difference 199: e_dec = 12.8 / 199 ; E_prefill = 6 - 1 x e_dec
    assert r["tokens_basis"] == "actual"
    assert r["e_dec_mJ_per_tok"] == pytest.approx(12.8 / 199 * 1000, abs=1e-4)
    assert r["E_prefill_J"] == pytest.approx(6.0 - 12.8 / 199, abs=1e-5)


def test_pair_cell_excludes_voided_requests():
    samples, tr, window = scenario()
    # a timed-out A, an A with a prefix-cache hit, and a B with a foreign-work status: all excluded
    samples += [dict(shape="A", t_submit_ns=20 * S, t_end_ns=21 * S, status="timeout", output_tokens=1),
                dict(shape="A", t_submit_ns=20 * S, t_end_ns=21 * S, status="ok", output_tokens=1, cached_tokens=5),
                dict(shape="B", t_submit_ns=22 * S, t_end_ns=24 * S, status="voided_foreign_work",
                     output_tokens=257)]
    r = pair_cell(samples, tr, window, level_tokens=2048)
    assert (r["k_A"], r["k_B"], r["n_voided"]) == (3, 3, 3)
    assert r["n_voided_by_shape"] == {"A": 2, "B": 1, "C": 0}
    assert "voided_requests" in r["flags"]
    assert r["e_dec_mJ_per_tok"] == pytest.approx(50.0, abs=1e-4)


def test_pair_cell_insufficient_requests():
    samples, tr, window = scenario()
    r = pair_cell([s for s in samples if s["shape"] == "A"], tr, window, level_tokens=2048)
    assert r["e_dec_mJ_per_tok"] is None and "insufficient_requests" in r["flags"] and r["k_B"] == 0


def test_shape_c_linear_has_no_residual_flag():
    # C is on the line E = 6 + 0.05 (n - 1): at n = 65, 9.2 J
    samples, tr, window = scenario(c_energy=9.2)
    r = pair_cell(samples, tr, window, level_tokens=2048)
    assert r["k_C"] == 3
    assert r["shape_c_residual_frac"] == pytest.approx(0.0, abs=1e-5)
    assert r["e_dec_regression_mJ_per_tok"] == pytest.approx(50.0, abs=1e-3)
    assert "shape_c_residual" not in r["flags"]


def test_shape_c_residual_flag():
    # C at 12 J, well above the 9.2 J line. OLS over (1, 6) x3, (65, 12) x3, (257, 18.8) x3:
    #   mean n = 107.667, mean E = 12.2667 ; fit at n = 65 is below 12 by far more than 5 % of 12.
    samples, tr, window = scenario(c_energy=12.0)
    r = pair_cell(samples, tr, window, level_tokens=2048)
    assert r["shape_c_residual_frac"] > 0.05
    assert "shape_c_residual" in r["flags"]


def test_pair_k_by_level():
    assert pair_k(512) == 20 and pair_k(2048) == 20 and pair_k(10240) == 20
    assert pair_k(32768) == 5 and pair_k(65536) == 5


def test_request_energies_aligned_with_none_for_gaps():
    tr = step_trace([(20, 30, 10.0)])
    out = request_energies([{"t_submit_ns": 20 * S, "t_end_ns": 25 * S},     # 50 - 4 x 5 = 30
                            {"t_submit_ns": None, "t_end_ns": 25 * S},
                            {"t_submit_ns": 900 * S, "t_end_ns": 901 * S}], tr, 4.0)
    assert out[0] == pytest.approx(30.0, abs=1e-6) and out[1] is None and out[2] is None


# ---------------------------------------------------------------------------- tasks

def test_j_per_task_medians_and_aux_exclusion():
    tasks = [{"task_id": "a", "completed": True, "active_J": 10.0},
             {"task_id": "b", "completed": True, "active_J": 30.0},
             {"task_id": "c", "completed": False, "active_J": 100.0},
             {"task_id": "d", "completed": True, "active_J": 20.0}]
    r = j_per_task(tasks)
    # completed {10, 30, 20}: median 20 ; attempted {10, 30, 100, 20}: median (20 + 30) / 2 = 25
    assert r["j_per_completed_task"] == 20.0 and r["j_per_attempted_task"] == 25.0
    assert (r["n_completed"], r["n_attempted"], r["aux_energy_J"]) == (3, 4, 0.0)
    r = j_per_task([{"task_id": "e", "completed": True,
                     "calls": [{"active_J": 5.0}, {"active_J": 2.0, "aux": True}, {"active_J": 3.0, "aux": False}]},
                    {"task_id": "f", "completed": True, "active_J": 20.0, "aux_energy_J": 0.5}])
    # task e main = 5 + 3 = 8, aux 2 ; task f main 20, aux 0.5 ; median {8, 20} = 14 ; aux total 2.5
    assert r["j_per_completed_task"] == 14.0 and r["aux_energy_J"] == 2.5


def test_j_per_task_none_completed():
    r = j_per_task([{"task_id": "x", "completed": False, "active_J": 4.0}])
    assert r["j_per_completed_task"] is None and r["j_per_attempted_task"] == 4.0 and r["n_completed"] == 0
    assert j_per_task([])["j_per_attempted_task"] is None


# ---------------------------------------------------------------------------- idle residency

def test_idle_resident_abba():
    # resident (5.0 + 5.2) / 2 = 5.1 W ; unloaded (4.0 + 4.2) / 2 = 4.1 W ; delta 1.0 W = 1000 mW = 1.0 Wh/h
    ws = [{"state": "resident", "mean_W": 5.0, "t_start_ns": 0},
          {"state": "unloaded", "mean_W": 4.0, "t_start_ns": 1},
          {"state": "unloaded", "mean_W": 4.2, "t_start_ns": 2},
          {"state": "resident", "mean_W": 5.2, "t_start_ns": 3}]
    r = idle_resident(ws)
    assert r["p_idle_resident_W"] == pytest.approx(5.1) and r["p_idle_unloaded_W"] == pytest.approx(4.1)
    assert r["delta_W"] == pytest.approx(1.0) and r["delta_mW"] == pytest.approx(1000.0)
    assert r["delta_Wh_per_h"] == pytest.approx(1.0)
    assert r["abba_order"] and r["order"] == "RUUR"
    assert (r["n_resident"], r["n_unloaded"]) == (2, 2)


def test_idle_resident_from_energy_and_missing_state():
    # 9000 J over 1800 s = 5 W resident ; 7200 J over 1800 s = 4 W unloaded
    r = idle_resident([{"state": "resident", "energy_total_J": 9000.0, "t_start_ns": 0, "t_end_ns": 1800 * S},
                       {"state": "unloaded", "energy_total_J": 7200.0, "t_start_ns": 2000 * S,
                        "t_end_ns": 3800 * S}])
    assert r["delta_W"] == pytest.approx(1.0) and not r["abba_order"]
    with pytest.raises(ValueError):
        idle_resident([{"state": "resident", "mean_W": 5.0}])


# ---------------------------------------------------------------------------- cross-checks

def test_battery_crosscheck_flag_at_3_percent():
    const = lambda w: mk([(0, w), (100, w)])    # noqa: E731
    # pstr 10.2 W vs battery 10.0 W: |2.0| / 100 J = 2 % -> no flag
    r = battery_crosscheck(const(10.2), const(10.0), 10 * S, 20 * S)
    assert r["pstr_J"] == pytest.approx(102.0) and r["battery_J"] == pytest.approx(100.0)
    assert r["rel_diff"] == pytest.approx(0.02) and not r["flag"]
    # pstr 10.0 vs battery 10.4: 4 / 104 = 3.85 % -> flag
    r = battery_crosscheck(const(10.0), const(10.4), 10 * S, 20 * S)
    assert r["rel_diff"] == pytest.approx(0.4 / 10.4) and r["flag"]


def test_battery_crosscheck_on_fixtures():
    p = read_trace(FIX / "synthetic_pstr_trace.jsonl")
    b = read_trace(FIX / "synthetic_battery_trace.jsonl")
    t = p.t_ns[0]
    # over 2..6 s: pstr 7 + 10 + 10 + 7 = 34 J ; battery (4+7)/2 x 2 + 7 x 2 = 25 J ; |34 - 25| / 25 = 0.36
    r = battery_crosscheck(p, b, t + 2 * S, t + 6 * S)
    assert r["pstr_J"] == pytest.approx(34.0) and r["battery_J"] == pytest.approx(25.0)
    assert r["rel_diff"] == pytest.approx(0.36) and r["flag"]


def test_drain_estimate():
    # test: 0.90 -> 0.80 in 0.5 h (20 %/h); control 0.90 -> 0.88 in 0.5 h (4 %/h): difference 16 %/h
    # net drop = 0.10 - control rate 0.04 /h x 0.5 h = 0.08 ; 0.08 x 50 Wh x 3600 = 14400 J ; / 7200 tok = 2 J
    r = drain_estimate(0.90, 0.80, 0.90, 0.88, 50.0, 7200, duration_h=0.5)
    assert r["battery_pct_per_h"] == pytest.approx(16.0)
    assert r["net_drop_frac"] == pytest.approx(0.08)
    assert r["energy_J"] == pytest.approx(14400.0) and r["e_dec_drain_J_per_tok"] == pytest.approx(2.0)
    assert r["quantisation_rel_bound"] == pytest.approx(2 * 0.01 / 0.08)   # 0.25
    assert r["is_estimate"] is True and r["energy_scope"] == "whole_device_battery_drain"
    assert "level_step_quantisation" in r["flags"]


def test_drain_estimate_control_duration_scaling_and_negative():
    # control 0.02 over 1 h = 0.02 /h -> over the 0.5 h test 0.01 ; net = 0.10 - 0.01 = 0.09
    r = drain_estimate(0.90, 0.80, 0.90, 0.88, 50.0, 100, duration_h=0.5, control_duration_h=1.0)
    assert r["net_drop_frac"] == pytest.approx(0.09)
    assert r["battery_pct_per_h"] == pytest.approx(20.0 - 2.0)
    bad = drain_estimate(0.90, 0.89, 0.90, 0.80, 50.0, 100, duration_h=1.0)
    assert bad["e_dec_drain_J_per_tok"] is None and "non_positive_net_drop" in bad["flags"]
    with pytest.raises(ValueError):
        drain_estimate(0.9, 0.8, 0.9, 0.9, 50.0, 0, duration_h=1.0)


# ---------------------------------------------------------------------------- foreign work

def test_void_foreign():
    w = {"t_start_ns": 100 * S, "t_end_ns": 200 * S, "idle_window_s": 10}
    assert void_foreign(w, [{"t_ns": 150 * S, "kind": "foreign_request"}])
    assert not void_foreign(w, [{"t_ns": 50 * S, "kind": "foreign_request"}])
    assert not void_foreign(w, [{"t_ns": 150 * S, "kind": "thermal_change"}])
    ev = [{"t_ns": 95 * S, "kind": "foreign_request"}]
    assert not void_foreign(w, ev) and void_foreign(w, ev, include_idle=True)
    assert not void_foreign(w, [])
