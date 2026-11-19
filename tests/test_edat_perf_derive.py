"""Derivers for the aggregator: hand-made documents with hand-checked numbers, then end to end."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edat.perf import derive, sustained
from edat.perf.power import TraceWriter
from edat.perf.simday import ReplayExecutor, load_script, run_day
from tests.test_edat_perf_sustained import NS, power_rig

T = 1000 * NS          # every hand-made document starts here
TRACE = "power-run1-smc_pstr.jsonl"


def write_trace(path: Path, t0_s: float, t1_s: float, fn, hz: float = 20.0) -> None:
    tw = TraceWriter(path, source="smc_pstr", rate_hz=hz)
    n = int(round((t1_s - t0_s) * hz))
    for i in range(n + 1):
        t = t0_s + i / hz
        tw.append(T + int(round(t * NS)), fn(t))
    tw.close()


def doc_for(tmp_path, samples=(), windows=(), attachments=None, **extra) -> dict:
    d = {"run_id": "run1", "_run_dir": str(tmp_path), "samples": list(samples), "energy_windows": list(windows),
         "events": [], "attachments": attachments if attachments is not None else
         [{"name": TRACE, "kind": "power_trace", "sha256": None}], "device": {}}
    d.update(extra)
    return d


def ns(s: float) -> int:
    return T + int(round(s * NS))


# ----------------------------------------------------------------------------- S1

def s1_samples():
    out = []
    for k in range(30):                                    # one request every 30 s for 15 min
        m = (k * 30) // 60
        tps = 50.0 if m < 4 else (47.0 if m < 6 else 40.0)
        t0 = k * 30.0
        out.append({"sample_id": f"s{k}", "entry_id": "M", "mode": "as_shipped", "test": "sustained", "status": "ok",
                    "power_state": "battery", "t_submit_ns": ns(t0), "t_first_token_ns": ns(t0 + 0.5),
                    "t_last_token_ns": ns(t0 + 0.5 + 100 / tps), "t_end_ns": ns(t0 + 0.5 + 100 / tps),
                    "output_tokens": 101, "thermal_end": "nominal" if m < 6 else "fair"})
    return out


def test_s1_initial_sustained_stability_throttle_and_power(tmp_path):
    write_trace(tmp_path / TRACE, 0, 900, lambda t: 30.0 if t <= 450 else 25.0)
    v = {"M": {"entry_id": "M"}}
    derive.derive_s1(doc_for(tmp_path, s1_samples()), v)
    out = v["M"]["s1_sustained"]["battery"]
    # initial: requests submitted in the first 2 min (0, 30, 60, 90 s) all at 50 tok/s
    assert out["initial_decode_tps"] == pytest.approx(50.0)
    # last request ends at 870 + 0.5 + 2.5 = 873 s; last 5 min = submitted at or after 573 s -> 40 tok/s
    assert out["sustained_decode_tps"] == pytest.approx(40.0)
    assert out["stability"] == pytest.approx(0.8)
    # minute 4-5 at 47 (94 % of 50, not a throttle); minute 6 at 40 < 45 -> throttle at 360 s
    assert out["time_to_throttle_s"] == 360.0
    pm = {x["minute"]: x for x in out["per_minute"]}
    assert pm[0]["power_W"] == pytest.approx(30.0) and pm[9]["power_W"] == pytest.approx(25.0)
    assert pm[6]["thermal"] == "fair" and out["thermal_trace"] == [[0.0, "nominal"], [360.0, "fair"]]
    assert out["flags"] == []


# ----------------------------------------------------------------------------- S2

def test_s2_rates_control_and_per_100_tasks(tmp_path):
    rows = [{"t_ns": ns(0), "phase": "test", "level": 1.00, "remaining_Wh": 70.0},
            {"t_ns": ns(1800), "phase": "test", "level": 0.975, "remaining_Wh": 68.25},
            {"t_ns": ns(3600), "phase": "test", "level": 0.95, "remaining_Wh": 66.5},
            {"t_ns": ns(3600), "phase": "control", "level": 0.95, "remaining_Wh": 66.5},
            {"t_ns": ns(7200), "phase": "control", "level": 0.94, "remaining_Wh": 65.8}]
    with open(tmp_path / "battery-run1.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps({**r, "entry_id": "M", "mode": "as_shipped"}) + "\n")
    ss = [{"sample_id": f"b{k}", "entry_id": "M", "mode": "as_shipped", "test": "battery", "rep": k, "step": 1,
           "status": "ok"} for k in range(1, 21)]
    ss.append({"sample_id": "b21", "entry_id": "M", "mode": "as_shipped", "test": "battery", "rep": 21, "step": 1,
               "status": "timeout"})
    v = {"M": {"entry_id": "M"}}
    derive.derive_s2(doc_for(tmp_path, ss, attachments=[{"name": "battery-run1.jsonl", "kind": "battery_readings"}]), v)
    o = v["M"]["s2_battery"]
    # test: 5 % and 3.5 Wh in 1 h; control: 1 % and 0.7 Wh in 1 h
    assert o["pct_per_h_test"] == pytest.approx(5.0) and o["pct_per_h_control"] == pytest.approx(1.0)
    assert o["pct_per_h"] == pytest.approx(4.0) and o["wh_per_h"] == pytest.approx(2.8)
    # 20 complete bursts (the 21st failed): (5 - 1 x 1 h) / 20 x 100 = 20 % per 100 tasks
    assert o["tasks"] == 20 and o["pct_per_100_tasks"] == pytest.approx(20.0)


# ----------------------------------------------------------------------------- S3

def test_s3_abba_delta_footprint_and_wake(tmp_path):
    # R [0,100] 6 W | U [110,210] 4 W | U [220,320] 4 W | R [330,430] 6 W
    write_trace(tmp_path / TRACE, 0, 440, lambda t: 4.0 if 105 <= t <= 325 else 6.0)
    ws = []
    for k, (a, b, st) in enumerate([(0, 100, "resident"), (110, 210, "unloaded"), (220, 320, "unloaded"),
                                    (330, 430, "resident")]):
        fp = 6_000_000_000 if st == "resident" else 300_000_000
        ws.append({"window_id": f"w{k}", "purpose": "s3", "state": st, "entry_id": "M", "mode": "as_shipped",
                   "t_start_ns": ns(a), "t_end_ns": ns(b), "footprint_start_bytes": fp, "footprint_end_bytes": fp + 2})
    wake = {"sample_id": "k", "entry_id": "M", "mode": "as_shipped", "test": "wake", "status": "ok",
            "t_submit_ns": ns(431), "t_first_token_ns": ns(432.25), "t_first_answer_ns": ns(432.5)}
    v = {"M": {"entry_id": "M"}}
    derive.derive_s3(doc_for(tmp_path, [wake], ws), v)
    o = v["M"]["s3_idle_residency"]
    assert o["p_idle_resident_W"] == pytest.approx(6.0) and o["p_idle_unloaded_W"] == pytest.approx(4.0)
    assert o["delta_W"] == pytest.approx(2.0) and o["abba_order"] is True and o["order"] == "RUUR"
    assert o["delta_footprint_bytes"] == pytest.approx(5_700_000_000)
    assert o["wake_ttft_s"] == pytest.approx(1.25) and o["wake_ttfa_s"] == pytest.approx(1.5)
    v2 = {"M": {"entry_id": "M"}}
    derive.derive_s3(doc_for(tmp_path, [wake], ws, attachments=[]), v2)
    assert v2["M"]["s3_idle_residency"]["delta_W"] is None
    assert v2["M"]["s3_idle_residency"]["power_reason"] == "no_power_trace_attachment"


# ----------------------------------------------------------------------------- reload after idle

def reload_doc(tmp_path):
    reqs = {"warm": (104, 106), "r1": (302, 306), "r2": (502, 506), "r3": (702, 706)}
    write_trace(tmp_path / TRACE, 0, 800, lambda t: 30.0 if any(a <= t <= b for a, b in reqs.values()) else 5.0)
    ws, ss = [], []
    for k, (sid, (a, b)) in enumerate(reqs.items()):
        ws.append({"window_id": f"w{k}", "t_start_ns": ns(a - 4), "t_end_ns": ns(b + 4), "idle_window_s": 60.0})
        ss.append({"sample_id": sid, "entry_id": "M", "mode": "as_shipped", "test": "reload_after_idle",
                   "phase": "warmup" if sid == "warm" else "measured", "status": "ok", "energy_window_id": f"w{k}",
                   "t_submit_ns": ns(a), "t_end_ns": ns(b), "reloaded": sid != "warm"})
    for s, ms, ttfa, cached in zip(ss[1:], (2000.0, 2200.0, 1800.0), (2.5, 2.7, 2.3), (True, True, False)):
        s.update({"reload_ms": ms, "t_first_answer_ns": s["t_submit_ns"] + int(ttfa * NS), "weights_cached": cached})
    return doc_for(tmp_path, ss, ws)


def test_reload_verdict(tmp_path):
    v = {"M": {"entry_id": "M"}}
    derive.derive_reload(reload_doc(tmp_path), v)
    o = v["M"]["reload_after_idle"]
    assert o["reload_ttfa_s"] == pytest.approx(2.5) and o["reload_s"] == pytest.approx(2.0)
    # reload request (30 - 5) W x 4 s = 100 J; warm (30 - 5) x 2 s = 50 J -> E_reload 50 J
    assert o["e_reload_request_J"] == pytest.approx(100.0, rel=1e-3)
    assert o["e_warm_request_J"] == pytest.approx(50.0, rel=1e-3)
    assert o["e_reload_J"] == pytest.approx(50.0, rel=1e-3)
    assert o["weights_cached_share"] == pytest.approx(2 / 3) and o["reloads_confirmed"] == 3


# ----------------------------------------------------------------------------- simulated day + time budget

def sim_doc(tmp_path, *, with_realtime=False, attachments=None):
    def fn(t):
        if 100 <= t <= 110:
            return 25.0
        if 140 <= t <= 150:
            return 15.0
        if 305 <= t <= 525:
            return 4.0
        return 5.0
    write_trace(tmp_path / TRACE, 0, 700, fn)
    ws = [{"window_id": "w001-simday", "t_start_ns": ns(100), "t_end_ns": ns(110), "idle_window_s": 30.0,
           "purpose": "sim_day_event"},
          {"window_id": "w002-simday", "t_start_ns": ns(140), "t_end_ns": ns(150), "idle_window_s": 30.0,
           "purpose": "sim_day_event"}]
    idle = [{"window_id": f"i{k}", "state": st, "t_start_ns": ns(a), "t_end_ns": ns(b)}
            for k, (a, b, st) in enumerate([(200, 300, "resident"), (310, 410, "unloaded"), (420, 520, "unloaded"),
                                            (530, 630, "resident")])]
    rec = {"day_id": "day01-compressed", "run_id": "run1", "entry_id": "M", "mode": "as_shipped",
           "day_mode": "compressed", "day_length_s": 3600.0, "idle_windows": idle, "partial": False,
           "events": [{"task_id": "e1", "script_time_s": 0.0, "wall_s": 10.0, "completed": True,
                       "window_id": "w001-simday", "aux_sample_ids": [], "event_kind": "brief"},
                      {"task_id": "e2", "script_time_s": 1800.0, "wall_s": 10.0, "completed": True,
                       "window_id": "w002-simday", "aux_sample_ids": ["a1"], "event_kind": "digest"}],
           "tasks": [{"task_id": "e1", "kind": "brief", "completed": True, "wall_s": 10.0},
                     {"task_id": "e2", "kind": "digest", "completed": True, "wall_s": 10.0}]}
    recs = [rec]
    if with_realtime:
        recs.append({"day_id": "day02-realtime", "run_id": "run1", "entry_id": "M", "mode": "as_shipped",
                     "day_mode": "realtime", "day_length_s": 3600.0, "t_day_start_ns": ns(0), "t_day_end_ns": ns(700),
                     "events": [], "partial": False, "tasks": []})
        with open(tmp_path / "battery-run1.jsonl", "w") as f:
            for t, lv in ((0, 0.80), (350, 0.79), (700, 0.78)):
                f.write(json.dumps({"t_ns": ns(t), "phase": "sim_day_realtime", "day_id": "day02-realtime",
                                    "level": lv}) + "\n")
    with open(tmp_path / "simday-run1.jsonl", "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    aux = {"sample_id": "a1", "entry_id": "M", "test": "sim_day_event", "aux": True, "status": "ok",
           "t_submit_ns": ns(142), "t_end_ns": ns(144)}
    screen = {"sample_id": "x1", "entry_id": "M", "mode": "as_shipped", "test": "screen_task", "screen_level": "L1",
              "completed": True, "t_submit_ns": ns(0), "t_end_ns": ns(20)}
    side = {"sample_id": "x2", "entry_id": "M", "mode": None, "test": "sidekick_task", "sidekick_mode": "answer_only",
            "completed": True, "wall_s": 5.0}
    atts = attachments if attachments is not None else [
        {"name": TRACE, "kind": "power_trace"}, {"name": "simday-run1.jsonl", "kind": "sim_day_record"},
        {"name": "battery-run1.jsonl", "kind": "battery_readings"}]
    return doc_for(tmp_path, [aux, screen, side], ws, attachments=atts, device={"battery_nominal_Wh": 50.0})


def test_sim_day_from_document(tmp_path):
    v = {"M": {"entry_id": "M", "model_id": "Model-X", "reload_after_idle": {"e_reload_J": 50.0, "reload_s": 2.0}}}
    derive.derive_sim_day(sim_doc(tmp_path, with_realtime=True), v)
    res, unl = v["M"]["sim_day"], v["M"]["sim_day_unload_policy"]
    # events: (25 - 5) x 10 = 200 J and (15 - 5) x 10 = 100 J; aux (15 - 5) x 2 = 20 J
    # idle: P_res 5 W, P_unl 4 W; gaps 0, 1800 - 10 = 1790, 3600 - 1810 = 1790 -> 3580 J
    # resident: 300 + 3580 = 3880 J = 1.07778 Wh -> 2.1556 % of 50 Wh
    assert res["day_wh_model"] == pytest.approx(3880 / 3600, rel=1e-3)
    assert res["day_wh_total"] == pytest.approx(3880 / 3600 + 4.0, rel=1e-3)
    assert res["battery_pct_used_model"] == pytest.approx(100 * 3880 / 3600 / 50, rel=1e-3)
    assert res["aux_share"] == pytest.approx(100 * 20 / 3880, rel=1e-2)
    assert res["j_per_completed_task"] == pytest.approx((300 - 20) / 2, rel=1e-2)
    assert res["p_idle_resident_W"] == pytest.approx(1.0, rel=1e-3)
    # unload after 5 min: 300 + 50 (reload before e2) + 300 (last gap, no reload) + 300 events = 950 J
    assert unl["day_wh_model"] == pytest.approx(950 / 3600, rel=1e-3) and unl["reloads"] == 1
    assert res["sentence"].startswith("One simulated day with Model-X uses about 2.2 % of a full battery")
    # validation against the real-time day: measured is only 700 s of trace, so the check fails
    assert res["validation"]["run_id"] == "run1" and res["validation"]["pass"] is False
    assert res["battery_pct_used_measured"] == pytest.approx(2.0)


def test_sim_day_without_trace_gives_reasons(tmp_path):
    v = {"M": {"entry_id": "M"}}
    doc = sim_doc(tmp_path, attachments=[{"name": "simday-run1.jsonl", "kind": "sim_day_record"}])
    derive.derive_sim_day(doc, v)
    assert v["M"]["sim_day"]["day_wh_model"] is None
    assert v["M"]["sim_day"]["energy_reason"] == "no_power_trace_attachment"
    assert "event_energy_missing" in v["M"]["sim_day"]["reason"]


def test_time_budget_per_test(tmp_path):
    v = {"M": {"entry_id": "M", "mode": "as_shipped"}}
    derive.derive_time_budget(sim_doc(tmp_path), v)
    o = v["M"]
    # sim-day jobs: brief 10 s <= 30, digest 10 s <= 120 -> 2/2; screen L1 20 s > 15 -> 0/1; sidekick 5 <= 10 -> 1/1
    assert o["within_budget_by_test"]["sim_day_event"]["within_budget_rate"] == 1.0
    assert o["within_budget_by_test"]["screen_task"]["within_budget_rate"] == 0.0
    assert o["within_budget_by_test"]["sidekick_task"]["within_budget_rate"] == 1.0
    assert o["within_budget_rate"] == pytest.approx(3 / 4)
    assert o["median_wall_s_correct"] == pytest.approx(10.0)          # median(10, 10, 20, 5) = 10


# ----------------------------------------------------------------------------- end to end on the fake rig

def test_end_to_end_drivers_then_derivers(tmp_path):
    r, eng, clock, _, w, samp = power_rig(tmp_path, prefix_cache=True)
    eng.unload_after_idle_s = 300.0
    e = r.entries["M"]
    sustained.s1_sustained(r, e, "as_shipped", minutes=1)
    sustained.s3_idle_residency(r, e, "as_shipped", minutes=1, settle_s=5)
    sustained.reload_after_idle(r, e, "as_shipped", reps=1)
    eng.unload_after_idle_s = None
    script = load_script(Path(__file__).resolve().parents[1] / "test_fixtures" / "perf" / "sim-day-synthetic.json")
    run_day(r, e, "as_shipped", script, ReplayExecutor(completion={"task": True, "brief": True, "digest": True}),
            idle_window_s=60.0, settle_s=5.0)
    samp.flush()
    path = w.finalize(ended_utc=clock.utc(), device={"class": "mac-ref", "battery_nominal_Wh": 70.0},
                      runtime={}, models=r.finish_models(), prompt_pack={}, conditions_start={},
                      conditions_end=dict(r.conditions_extra))
    doc = json.loads(path.read_text())
    doc["_run_dir"] = str(w.dir)
    v = {"M": {"entry_id": "M", "model_id": "M"}}
    for fn in derive.EXTRA_DERIVERS:
        fn(doc, v)
    o = v["M"]
    s1 = o["s1_sustained"]["ac"]
    assert s1["initial_decode_tps"] == pytest.approx(50.0) and s1["stability"] == pytest.approx(1.0)
    assert s1["time_to_throttle_s"] is None and s1["per_minute"][0]["power_W"] > 4.0
    s3 = o["s3_idle_residency"]
    assert s3["delta_W"] == pytest.approx(1.0, abs=0.01)             # the fake's resident idle adds 1 W
    assert s3["delta_footprint_bytes"] == pytest.approx(eng.models["M"].weights_bytes + eng.models["M"].overhead_bytes)
    rl = o["reload_after_idle"]
    assert rl["reload_s"] == pytest.approx(2.0, abs=1e-3)
    # the reload adds 2 s at the load power (15 W) over an idle of about 4.5 W: about 21 J, plus edge effects
    assert 15.0 < rl["e_reload_J"] < 30.0
    sd = o["sim_day"]
    assert sd["tasks_attempted"] == 7 and sd["tasks_completed"] == 7 and sd["day_wh_model"] > 0
    assert sd["p_idle_resident_W"] == pytest.approx(1.0, abs=0.01)
    # scripted starts 0, 600, 660, 720, 1200, 2700, 3300 s with jobs of ~20 s: the gaps before 600, 1200,
    # 2700 and 3300 exceed 5 min (4 reloads); the last gap (3600 - ~3320 s) is shorter than 5 min
    assert o["sim_day_unload_policy"]["reloads"] == 4
    assert sd["sentence"].startswith("One simulated day with M uses about")
    assert o["within_budget_rate"] is not None
