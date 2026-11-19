"""The simulated day: script loading and mapping, compressed and real-time runs, composition."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from edat.perf import simday
from edat.perf.assistant import FakeAssistantSession, InAssistantExecutor, StubAssistantSession
from edat.perf.simday import ReplayExecutor, compose_day, load_script, run_day
from tests.test_edat_perf_sustained import NS, events, power_rig, samples, windows

FIXTURE = Path(__file__).resolve().parents[1] / "test_fixtures" / "perf" / "sim-day-synthetic.json"


def short_script(times=("08:00:00", "08:10:00", "08:20:00")) -> dict:
    doc = json.loads(FIXTURE.read_text())
    doc["events"] = [{"t": t, "kind": k, "ref": f"job:{k}", "world_events_before": []}
                     for t, k in zip(times, ("brief", "mail_digest", "review"))]
    doc["day_end"] = "08:30"
    return doc


# ----------------------------------------------------------------------------- script

def test_load_script_mapping_and_checks():
    s = load_script(FIXTURE)
    ev = s["events"]
    assert s["day_length_s"] == 3600.0 and len(ev) == 7
    assert [e["script_time_s"] for e in ev] == [0, 600, 660, 720, 1200, 2700, 3300]
    assert [(e["kind"], e["event_kind"], e["budget_kind"]) for e in ev] == [
        ("brief", "brief", "brief"), ("task", "action", "one_tool"), ("task", "question", "question"),
        ("task", "action", "multi_step"), ("mail_digest", "digest", "digest"),
        ("message_digest", "digest", "digest"), ("review", "brief", "brief")]
    assert [e["ref_missing"] for e in ev] == [False, False, False, True, False, False, False]
    assert ev[3]["task_id"] == "03:task" and ev[1]["task_id"] == "01:synthetic_select_001"
    assert len(ev[4]["world_events_before"]) == 1
    assert s["n_ref_missing"] == 1 and s["mapping"]["event_kind"]["review"] == "brief"
    bad = json.loads(FIXTURE.read_text())
    bad["lang"] = "de"
    with pytest.raises(ValueError):
        load_script(bad)
    bad = json.loads(FIXTURE.read_text())
    bad["schema"] = "edat-sim-day-v0"
    with pytest.raises(ValueError):
        load_script(bad)
    bad = json.loads(FIXTURE.read_text())
    bad["events"][0]["kind"] = "lunch"
    with pytest.raises(ValueError):
        load_script(bad)


def test_completion_lookup_most_specific_first():
    ev = load_script(FIXTURE)["events"]
    table = {"synthetic_select_001": True, "abstain": False, "task": True, "digest": True}
    assert [simday.lookup_completion(table, e) for e in ev] == [None, True, False, True, True, True, None]
    assert simday.lookup_completion(None, ev[1]) is None


# ----------------------------------------------------------------------------- compressed day

def test_compressed_day_order_gaps_windows_and_idle(tmp_path):
    r, eng, clock, _, w, samp = power_rig(tmp_path, prefix_cache=True)
    e = r.entries["M"]
    script = load_script(FIXTURE)
    ex = ReplayExecutor(completion={"task": True, "brief": True, "digest": False})
    rec = run_day(r, e, "as_shipped", script, ex, gap_s=30.0, idle_window_s=120.0, settle_s=5.0)
    # events in script order, each its own window with the 30 s gaps as shared brackets
    ew = [x for x in windows(w) if x.get("purpose") == "sim_day_event"]
    assert [x["task_id"] for x in ew] == [ev["task_id"] for ev in script["events"]]
    assert all(x["idle_window_s"] == 30.0 for x in ew)
    assert all(b["t_start_ns"] - a["t_end_ns"] == 30 * NS for a, b in zip(ew, ew[1:]))
    assert ew[0]["t_start_ns"] - rec["t_day_start_ns"] == 30 * NS
    # task_start / task_end bracket each job, on the run clock
    ev = [x for x in events(w) if x["kind"] in ("task_start", "task_end")]
    assert [x["kind"] for x in ev] == ["task_start", "task_end"] * 7
    assert [x["t_ns"] for x in ev[::2]] == [x["t_start_ns"] for x in ew]
    # samples: 6 replay steps per job, tagged with the event's fields
    ss = [s for s in samples(w) if s["test"] == "sim_day_event"]
    assert len(ss) == 42 and {s["executor"] for s in ss} == {"replay-standin"}
    first = [s for s in ss if s["task_id"] == script["events"][2]["task_id"]]
    assert {(s["event_kind"], s["script_time_s"]) for s in first} == {("question", 660)}
    assert {s["completed"] for s in first} == {True} and {s["wall_budget_s"] for s in first} == {10.0}
    dig = [s for s in ss if s["event_kind"] == "digest"]
    assert {s["completed"] for s in dig} == {False} and {s["within_budget"] for s in dig} == {True}
    assert len({s["variant"] for s in ss}) == 7                      # one replay variant per job
    # idle part: ABBA, 1 Hz, model loaded / no model loaded
    iw = [x for x in windows(w) if x.get("purpose") == "sim_day_idle"]
    assert [x["tag"] for x in iw] == ["P_idle_resident", "P_idle_unloaded", "P_idle_unloaded", "P_idle_resident"]
    assert [x["loaded_models_end"] for x in iw] == [["M"], [], [], ["M"]]
    assert all(x["rate_hz"] == 1.0 and x["t_end_ns"] - x["t_start_ns"] == 120 * NS for x in iw)
    assert [s["event_kind"] for s in samples(w) if s["test"] == "sim_day_idle"] == ["idle"] * 4
    # conditions and the side record
    assert r.conditions_extra["sleep_prevented"] is True and r.conditions_extra["unload_policy"] == "resident"
    side = [json.loads(x) for x in (w.dir / "simday-run1.jsonl").read_text().splitlines()]
    assert side[0]["day_id"] == rec["day_id"] and len(side[0]["events"]) == 7 and side[0]["partial"] is False
    assert w.attachments["simday-run1.jsonl"] == "sim_day_record"
    assert [t["kind"] for t in rec["tasks"]] == [ev["budget_kind"] for ev in script["events"]]


def test_compressed_day_unload_policy_recorded(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path)
    run_day(r, r.entries["M"], "as_shipped", short_script(), ReplayExecutor(), gap_s=30.0, idle_window_s=60.0,
            settle_s=1.0, unload_policy="unload_after_idle")
    assert r.conditions_extra["unload_policy"] == "unload_after_idle"
    assert r.conditions_extra["idle_unload_minutes"] == 5


# ----------------------------------------------------------------------------- real-time day

def test_realtime_day_waits_for_script_times_and_rates(tmp_path):
    r, eng, clock, _, w, samp = power_rig(tmp_path)
    e = r.entries["M"]
    rec = run_day(r, e, "as_shipped", short_script(), ReplayExecutor(), mode_="realtime")
    t0 = rec["t_day_start_ns"]
    starts = [x["t_ns"] for x in events(w) if x["kind"] == "task_start"]
    assert starts == [t0, t0 + 600 * NS, t0 + 1200 * NS]           # each at its scripted time
    assert rec["t_day_end_ns"] == t0 + 1800 * NS and rec["partial"] is False
    assert {s["test"] for s in samples(w) if s.get("task_id")} == {"sim_day_realtime"}
    # 1 Hz while idle; 20 Hz from 60 s before an event
    assert (t0 + 540 * NS, 20.0) in samp.rates
    assert any(hz == 1.0 and t0 < t < t0 + 540 * NS for t, hz in samp.rates)
    assert all(x["idle_window_s"] == 60.0 for x in windows(w) if x.get("purpose") == "sim_day_realtime")


def test_realtime_day_stops_at_15_percent(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path)
    t_start = clock.t
    reader = lambda: {"level": 0.30 - 0.01 * (clock.t - t_start) / (60 * NS)}  # noqa: E731
    rec = run_day(r, r.entries["M"], "as_shipped", short_script(), ReplayExecutor(), mode_="realtime",
                  read_battery=reader)
    # 0.30 - 0.01/min reaches 0.15 at minute 15: events at 0 and 10 min ran, the one at 20 min did not
    assert rec["partial"] is True and len(rec["events"]) == 2
    rows = [json.loads(x) for x in (w.dir / "battery-run1.jsonl").read_text().splitlines()]
    assert rows[-1]["level"] <= 0.15 + 1e-9
    assert any("partial" in str(x["detail"]) for x in events(w) if x["kind"] == "operator_note")


# ----------------------------------------------------------------------------- in-assistant executor

def test_in_assistant_executor_samples_and_aux(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path)
    r.conditions_extra["aux_footprint_bytes"] = {"aux-embedder": 700_000_000}
    sess = FakeAssistantSession(clock, steps=2, aux=1, completed=lambda job: job["event_kind"] != "digest")
    rec = run_day(r, r.entries["M"], "as_shipped", short_script(), InAssistantExecutor(sess), gap_s=30.0,
                  idle_window_s=60.0, settle_s=1.0)
    ss = [s for s in samples(w) if s["test"] == "sim_day_event"]
    assert len(ss) == 9 and [s["aux"] for s in ss[:3]] == [True, False, False]
    assert ss[0]["aux_models_resident"] == [{"id": "aux-embedder", "aux_footprint_bytes": 700_000_000}]
    assert ss[1]["t_first_token_ns"] - ss[1]["t_submit_ns"] == int(0.4 * NS)
    ew = [x for x in windows(w) if x.get("purpose") == "sim_day_event"]
    assert ew[0]["aux_sample_ids"] == [ss[0]["sample_id"]] and ew[0]["sample_ids"] == [s["sample_id"] for s in ss[:3]]
    assert [e["completed"] for e in rec["events"]] == [True, False, True]
    # wall 0.05 + 2 x 1.4 = 2.85 s per job
    assert rec["events"][0]["wall_s"] == pytest.approx(2.85)
    assert sess.jobs[0]["kind"] == "brief" and sess.jobs[0]["mode"] == "forced"
    with pytest.raises(NotImplementedError, match="AssistantSession"):
        StubAssistantSession().run_job({})


# ----------------------------------------------------------------------------- composition

def hand_day(p_res=5.0) -> dict:
    return {"run_id": "r1", "day_length_s": 3600.0, "p_idle_resident_W": p_res, "p_idle_unloaded_W": 4.0,
            "events": [
                {"task_id": "e1", "script_time_s": 0.0, "wall_s": 100.0, "active_J": 500.0, "aux_J": 0.0, "completed": True},
                {"task_id": "e2", "script_time_s": 600.0, "wall_s": 50.0, "active_J": 300.0, "aux_J": 20.0, "completed": True},
                {"task_id": "e3", "script_time_s": 1200.0, "wall_s": 100.0, "active_J": 400.0, "aux_J": 0.0, "completed": False},
                {"task_id": "e4", "script_time_s": 2000.0, "wall_s": 100.0, "active_J": 200.0, "aux_J": 0.0, "completed": None}]}


def test_compose_day_hand_arithmetic():
    out = compose_day([hand_day()], model_label="Model-X", full_charge_Wh=50.0,
                      reload={"e_reload_J": 100.0, "reload_s": 2.0})
    res, unl = out["sim_day"], out["sim_day_unload_policy"]
    # gaps: 0 | 600-100=500 | 1200-650=550 | 2000-1300=700 | 3600-2100=1500 -> 3250 s idle, dP = 1 W
    # resident: idle 3250 J + events 1400 J = 4650 J = 1.291667 Wh
    assert res["day_wh_model"] == pytest.approx(4650 / 3600)
    assert res["day_wh_total"] == pytest.approx(4650 / 3600 + 4.0)       # + 4 W x 1 h unloaded baseline
    assert res["battery_pct_used_model"] == pytest.approx(100 * 4650 / 3600 / 50)        # 2.5833 %
    assert res["share_of_full_battery_pct"] == pytest.approx(100 * (4650 / 3600 + 4) / 50)
    assert res["idle_share"] == pytest.approx(100 * 3250 / 4650)                       # 69.89 %
    assert res["active_share"] == pytest.approx(100 * 1400 / 4650)
    assert res["aux_share"] == pytest.approx(100 * 20 / 4650)
    assert res["j_per_completed_task"] == pytest.approx((1400 - 20) / 2)              # 690 J
    assert (res["tasks_completed"], res["tasks_attempted"], res["reloads"], res["reps"]) == (2, 4, 0, 1)
    assert res["p_idle_resident_W"] == pytest.approx(1.0) and res["p_idle_resident_raw_W"] == 5.0
    assert res["event_time_resident_premium_Wh"] == pytest.approx(350 / 3600)
    # unload after 5 min: gaps 500, 550, 700 -> 300 J + reload each (3 reloads); final 1500 -> 300 J, no reload
    # idle 4 x 300 = 1200 J, reloads 300 J, events 1400 J -> 2900 J
    assert unl["policy"] == "unload_after_idle" and unl["reloads"] == 3
    assert unl["day_wh_model"] == pytest.approx(2900 / 3600)
    assert unl["battery_pct_used_model"] == pytest.approx(100 * 2900 / 3600 / 50)       # 1.6111 %
    assert unl["reload_share"] == pytest.approx(100 * 300 / 2900)
    wta = dict(unl["per_rep"][0]["wall_time_to_answer_s"])
    assert wta == {"e1": 100.0, "e2": 52.0, "e3": 102.0, "e4": 102.0}
    assert res["sentence"] == ("One simulated day with Model-X uses about 2.6 % of a full battery on the reference "
                               "MacBook Pro, beyond what the Mac uses with no model loaded. 70 % of that is idle "
                               "time with the model resident. Under Harbor's unload-after-5-minutes setting it "
                               "would use about 1.6 %.")
    assert res["sentence_md"].startswith("**One simulated day with Model-X uses about 2.6 % of a full battery**")


def test_compose_day_reps_validation_and_missing_inputs():
    out = compose_day([hand_day(5.0), hand_day(6.0), hand_day(7.0)], model_label="M", full_charge_Wh=50.0,
                      realtime={"run_id": "rt", "measured_total_Wh": 5.0, "battery_pct_used_measured": 11.0})
    res = out["sim_day"]
    # dP 1, 2, 3 W -> idle 3250, 6500, 9750 J; median rep is dP = 2: (1400 + 6500) / 3600 Wh
    assert res["reps"] == 3 and res["day_wh_model"] == pytest.approx(7900 / 3600)
    assert res["range"]["day_wh_model"] == [pytest.approx(4650 / 3600), pytest.approx(11150 / 3600)]
    # composed total 7900/3600 + 4 = 6.1944 Wh vs 5.0 measured -> +23.9 % -> fails the 10 % check
    assert res["validation"]["error_pct"] == pytest.approx(100 * (7900 / 3600 + 4 - 5) / 5)
    assert res["validation"]["pass"] is False and "composed_method_failed_validation" in res["flags"]
    assert res["battery_pct_used_measured"] == 11.0
    ok = compose_day([hand_day()], model_label="M", full_charge_Wh=50.0,
                     realtime={"run_id": "rt", "measured_total_Wh": 5.0})["sim_day"]["validation"]
    assert ok["pass"] is True and ok["error_pct"] == pytest.approx(100 * (4650 / 3600 + 4 - 5) / 5)   # +5.83 %
    part = compose_day([hand_day()], model_label="M", full_charge_Wh=50.0,
                       realtime={"run_id": "rt", "measured_total_Wh": 2.0, "partial": True})["sim_day"]["validation"]
    assert part == {"run_id": "rt", "error_pct": None, "pass": None, "partial": True, "measured_total_Wh": 2.0}
    # no E_reload: the unload line is null with a reason; no full charge: no battery % and no sentence
    out = compose_day([hand_day()], model_label="M", full_charge_Wh=None)
    assert out["sim_day_unload_policy"]["day_wh_model"] is None
    assert "e_reload_missing" in out["sim_day_unload_policy"]["reason"]
    assert out["sim_day"]["battery_pct_used_model"] is None and out["sim_day"]["sentence"] is None
    d = hand_day()
    d["events"][0]["active_J"] = None
    assert "event_energy_missing" in compose_day([d], model_label="M")["sim_day"]["reason"]
    d = copy.deepcopy(hand_day())
    for e in d["events"]:
        e["completed"] = None
    r = compose_day([d], model_label="M", full_charge_Wh=50.0)["sim_day"]
    assert r["j_per_completed_task"] is None and r["per_rep"][0]["j_per_completed_task_reason"] == "completion_unknown"
