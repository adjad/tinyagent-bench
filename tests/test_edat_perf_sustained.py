"""Sustained-use drivers on the fake rig: idle windows, S1, S2, S3, reload after idle, time budget."""
from __future__ import annotations

import json
from bisect import bisect_right
from pathlib import Path

import pytest

from edat.perf import sustained
from edat.perf.constants import GIB, PSTR_RATE_HZ
from edat.perf.power import TraceWriter
from edat.perf.record import RunWriter, latest_per_id, read_jsonl
from edat.perf.runner import PowerControl, RunConfig
from tests.test_edat_perf_runner import rig

NS = 1_000_000_000


# ----------------------------------------------------------------------------- shared fake rig

class FakeSampler:
    """Records rate switches as they happen and writes the trace afterwards from the fake
    engine's activity (the engine only logs a decode once it has ended, so readings taken live
    would miss it). Inclusive interval ends, so a window edge reads the window's own power."""

    def __init__(self, clock, eng, path: Path):
        self.clock, self.eng, self.path = clock, eng, path
        self.t0 = clock.t
        self.rates: list[tuple[int, float]] = [(clock.t, PSTR_RATE_HZ)]
        self.loaded: list[tuple[int, bool]] = [(clock.t, any(eng.loaded.values()))]
        clock.listeners.append(self._on_advance)

    def _on_advance(self, old: int, new: int) -> None:
        state = any(self.eng.loaded.values())
        if state != self.loaded[-1][1]:
            self.loaded.append((old, state))

    def set_rate(self, hz: float) -> None:
        self.rates.append((self.clock.t, float(hz)))

    def rate_at(self, t: int) -> float:
        hz = self.rates[0][1]
        for tc, r in self.rates:
            if tc <= t:
                hz = r
        return hz

    def flush(self) -> Path:
        acts = sorted(self.eng.activity, key=lambda a: a.t0)
        starts = [a.t0 for a in acts]
        watts = {"prefill": self.eng.prefill_W, "decode": self.eng.decode_W, "load": self.eng.load_W}

        lt = [t for t, _ in self.loaded]

        def power(t: int) -> float:
            i = bisect_right(starts, t) - 1
            if i >= 0 and acts[i].t0 <= t < acts[i].t1:
                return watts[acts[i].kind]
            j = bisect_right(lt, t) - 1
            return self.eng.idle_W + (1.0 if self.loaded[max(j, 0)][1] else 0.0)

        changes = sorted(self.rates)
        tw = TraceWriter(self.path, source="smc_pstr", rate_hz=PSTR_RATE_HZ)
        t, end, k = self.t0, self.clock.t, 0
        while t <= end:
            while k + 1 < len(changes) and changes[k + 1][0] <= t:
                k += 1
            tw.append(t, power(t))
            step = int(NS / changes[k][1])
            nxt = t + step
            if k + 1 < len(changes) and changes[k + 1][0] < nxt:
                nxt = max(changes[k + 1][0], t + 1)
            t = nxt
        tw.close()
        return self.path


def power_rig(tmp_path, **kw):
    r, eng, clock, sysm, w = rig(tmp_path, **kw)
    name = f"power-{w.run_id}-smc_pstr.jsonl"
    samp = FakeSampler(clock, eng, w.dir / name)
    r.i.power = PowerControl(trace_name=name, set_rate=samp.set_rate)
    w.attach(name, "power_trace")
    return r, eng, clock, sysm, w, samp


def samples(w) -> list[dict]:
    return latest_per_id(read_jsonl(w.samples_path))


def windows(w) -> list[dict]:
    return read_jsonl(w.windows_path)


def events(w) -> list[dict]:
    return read_jsonl(w.events_path)


# ----------------------------------------------------------------------------- idle window

def test_idle_window_rate_switch_fields_and_sample(tmp_path):
    r, eng, clock, _, w, samp = power_rig(tmp_path)
    e = r.entries["M"]
    r.select(e, "as_shipped")
    t0 = clock.t
    win = sustained.idle_window(r, 120.0, "idle_resident", sample_test="idle_resident", entry=e, mode="as_shipped",
                                extra={"state": "resident"})
    assert clock.t - t0 == 120 * NS                               # one idle wait, nothing else
    assert samp.rates[-2:] == [(t0, 1.0), (t0 + 120 * NS, 20.0)]   # 1 Hz for the window, back to 20 Hz
    assert win["tag"] == "idle_resident" and win["rate_hz"] == 1.0 and win["idle_window_s"] == 0.0
    for k in ("window_id", "source", "scope", "is_estimate", "t_start_ns", "t_end_ns", "idle_before_W",
              "idle_after_W", "sample_ids", "trace_attachment", "energy_total_J", "energy_active_J"):
        assert k in win
    m = eng.models["M"]
    assert win["footprint_start_bytes"] == eng.base_footprint + m.weights_bytes + m.overhead_bytes
    assert win["loaded_models_start"] == ["M"] and win["loaded_models_end"] == ["M"]
    (s,) = samples(w)
    assert s["test"] == "idle_resident" and s["idle_state"] == "resident"
    assert s["t_end_ns"] - s["t_submit_ns"] == 120 * NS and s["energy_window_id"] == win["window_id"]
    assert windows(w)[0]["window_id"] == win["window_id"]


# ----------------------------------------------------------------------------- S1

def test_s1_back_to_back_no_gap_gate_first_only(tmp_path):
    r, eng, clock, sysm, w, samp = power_rig(tmp_path, cfg=RunConfig(gap_s=5.0, cooldown_max_s=20,
                                                                    cooldown_poll_s=5))
    e = r.entries["M"]
    sysm.thermal_script = [(clock.t + 70 * NS, "serious")]       # heats up after ~1 min
    out = sustained.s1_sustained(r, e, "as_shipped", minutes=2, level=2048, power_source_="battery")
    assert all(s["test"] == "sustained" for s in out)
    # 2048-level request: 1.024 s prefill + 0.01 + 255/50 s decode = 6.144 s; back to back in 120 s
    assert len(out) == 20                                          # ceil(120 / 6.144)
    assert out[0]["cooldown_wait_s"] is not None and all(s["cooldown_wait_s"] is None for s in out[1:])
    assert 5.0 not in clock.slept                                  # no gap inside the run
    gaps = [(b["t_submit_ns"] - a["t_end_ns"]) for a, b in zip(out, out[1:])]
    assert max(gaps) == 0
    assert {s["power_state"] for s in out} == {"battery"}
    assert any(s["thermal_start"] == "serious" for s in out)       # recorded, not gated
    assert all(s["thermal_not_nominal"] is None for s in out[1:])
    run = r.conditions_extra["sustained_runs"][0]
    assert run["gap_s"] == 0.0 and run["cooldown_gate"] == "first request only" and run["power_source"] == "battery"
    assert r.cfg.gap_s == 5.0                                      # restored
    win = windows(w)[0]
    assert win["sample_ids"] == [s["sample_id"] for s in out]


# ----------------------------------------------------------------------------- S2

def _reader(clock, start=1.0, per_min=0.001):
    t0 = clock.t
    return lambda: {"level": start - per_min * (clock.t - t0) / (60 * NS), "remaining_Wh": 70.0}


def test_s2_bursts_readings_and_control(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path, prefix_cache=True)
    e = r.entries["M"]
    t0 = clock.t
    rec = sustained.s2_battery(r, e, "as_shipped", minutes=6, every_s=180, read_battery=_reader(clock))
    ss = rec["samples"]
    assert [s["rep"] for s in ss] == [1] * 6 + [2] * 6 and {s["test"] for s in ss} == {"battery"}
    assert [s["step"] for s in ss[:6]] == [1, 2, 3, 4, 5, 6]
    assert ss[0]["variant"] != ss[6]["variant"]                    # each burst its own variant
    assert all(s["cached_tokens"] > 0 for s in ss[1:6])            # cache on inside a burst
    # burst 2 starts exactly 180 s after burst 1 (idle between)
    assert ss[6]["t_submit_ns"] - ss[0]["t_submit_ns"] == 180 * NS
    rows = [json.loads(x) for x in (w.dir / "battery-run1.jsonl").read_text().splitlines()]
    test = [x for x in rows if x["phase"] == "test"]
    ctrl = [x for x in rows if x["phase"] == "control"]
    assert len(test) == 7 and len(ctrl) == 7                       # minutes 0..6
    assert test[-1]["t_ns"] - test[0]["t_ns"] == 360 * NS
    assert ctrl[0]["t_ns"] == rec["t_control_start_ns"]
    assert rec["t_test_start_ns"] == t0 + 6 * NS                   # after the 6 s cold load
    assert rec["control_loaded_models"] == []                      # model unloaded for the control
    assert w.attachments["battery-run1.jsonl"] == "battery_readings"


def test_s2_duty_cycle_file(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path)
    e = r.entries["M"]
    dc = {"schema": "edat-duty-cycle-v1", "lang": "en", "period_minutes": 1, "duration_minutes": 3,
          "bursts": [{"burst": 1, "t": "00:00:00", "nonce_variant": 5, "nonce_reused": False, "fresh_cache": True},
                     {"burst": 2, "t": "00:01:00", "nonce_variant": 6, "nonce_reused": False, "fresh_cache": True},
                     {"burst": 3, "t": "00:02:00", "nonce_variant": 5, "nonce_reused": True, "fresh_cache": True}]}
    rec = sustained.s2_battery(r, e, "as_shipped", read_battery=_reader(clock), duty_cycle=dc, control=False)
    ss = rec["samples"]
    assert [s["variant"] for s in ss[::6]] == [5, 6, 5]
    assert [s["rep"] for s in ss[::6]] == [1, 2, 3]
    assert ss[12]["t_submit_ns"] - ss[0]["t_submit_ns"] == 120 * NS
    notes = [x["detail"] for x in events(w) if x["kind"] == "operator_note"]
    assert any("reuses nonce variant 5" in n for n in notes)
    assert rec["t_test_end_ns"] - rec["t_test_start_ns"] == 180 * NS
    with pytest.raises(ValueError):
        sustained.load_duty_cycle({"schema": "other"})


# ----------------------------------------------------------------------------- S3

def test_s3_abba_footprints_and_wake(tmp_path):
    r, eng, clock, _, w, samp = power_rig(tmp_path)
    e = r.entries["M"]
    out = sustained.s3_idle_residency(r, e, "as_shipped", minutes=1, settle_s=10)
    ws = out["windows"]
    assert [x["state"] for x in ws] == ["resident", "unloaded", "unloaded", "resident"]
    m = eng.models["M"]
    loaded_fp = eng.base_footprint + m.weights_bytes + m.overhead_bytes
    assert [x["footprint_start_bytes"] for x in ws] == [loaded_fp, eng.base_footprint, eng.base_footprint, loaded_fp]
    assert [x["loaded_models_end"] for x in ws] == [["M"], [], [], ["M"]]
    assert all(x["t_end_ns"] - x["t_start_ns"] == 60 * NS for x in ws)
    # settle outside the window: window 2 starts 10 s after window 1 ends (unload is instant in the fake)
    assert ws[1]["t_start_ns"] - ws[0]["t_end_ns"] == 10 * NS
    wake = out["wake"]
    assert wake["test"] == "wake" and wake["cooldown_wait_s"] is None and wake["level_tokens"] == 2048
    assert wake["t_submit_ns"] == ws[3]["t_end_ns"]                 # right after the last resident window
    idle_samples = [s for s in samples(w) if s["test"] == "idle_resident"]
    assert [s["idle_state"] for s in idle_samples] == ["resident", "unloaded", "unloaded", "resident"]


# ----------------------------------------------------------------------------- reload after idle

def test_reload_after_idle_confirms_unload_and_reload_ms(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path)
    eng.unload_after_idle_s = 300.0
    e = r.entries["M"]
    out = sustained.reload_after_idle(r, e, "as_shipped", reps=2, idle_minutes=6)
    assert len(out) == 2 and all(s["test"] == "reload_after_idle" and s["phase"] == "measured" for s in out)
    assert all(s["reloaded"] is True for s in out)
    # TTFA after the reload = 2 s warm load + 1.024 s prefill + 0.01 s; warm TTFA = 1.034 s
    for s in out:
        assert s["reload_ms"] == pytest.approx(2000.0, abs=1e-3)
        assert s["reload_ms_basis"]["basis"] == "reload_warm_requests"
        assert s["reload_ms_basis"]["median_warm_ttfa_s"] == pytest.approx(1.034, abs=1e-6)
        assert s["weights_cached"] is True and s["load"]["disk_read_bytes"] == 0
    ev = [x for x in events(w) if x["kind"] == "engine_unload"]
    assert len(ev) == 2 and all(x["detail"]["confirmed"] for x in ev)
    kinds = [x.get("tag") or x["window_id"].split("-", 1)[1] for x in windows(w)]
    assert kinds == ["reload_warm", "reload_idle", "reload"] * 2
    idle = [x for x in windows(w) if x.get("tag") == "reload_idle"]
    assert all(x["t_end_ns"] - x["t_start_ns"] == 360 * NS and x["rate_hz"] == 1.0 for x in idle)
    warm = [s for s in samples(w) if s["phase"] == "warmup"]
    assert len(warm) == 2 and all(s["reloaded"] is False for s in warm)


def test_reload_after_idle_cold_and_level_basis(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path)
    eng.unload_after_idle_s = 300.0
    e = r.entries["M"]
    r.levels(e, "as_shipped", (2048,), reps=1)                    # level samples at 2048 in the same run
    clock.listeners.append(lambda old, new: eng.purge() if new - old >= 300 * NS else None)  # cache evicted in idle
    out = sustained.reload_after_idle(r, e, "as_shipped", reps=1, idle_minutes=6)
    s = out[0]
    assert s["reload_ms_basis"]["basis"] == "level_cell_same_run"
    assert s["weights_cached"] is False and s["load"]["disk_read_bytes"] == 5 * GIB
    # cold load 6 s instead of 2 s
    assert s["reload_ms"] == pytest.approx(6000.0, abs=1e-3)


# ----------------------------------------------------------------------------- time budget

def test_time_budget_rates_and_fields():
    tasks = [{"task_id": "q1", "kind": "question", "completed": True, "wall_s": 8.0},
             {"task_id": "q2", "kind": "question", "completed": True, "wall_s": 12.0},
             {"task_id": "a1", "kind": "action", "completed": True, "wall_s": 50.0},
             {"task_id": "d1", "kind": "digest", "completed": False, "wall_s": 100.0},
             {"task_id": "b1", "kind": "brief", "completed": None, "wall_s": 20.0},
             {"task_id": "x1", "kind": "unknown", "completed": True, "wall_s": 5.0}]
    out = sustained.time_budget(tasks)
    # known completion: q1 q2 a1 d1 x1 = 5; completed and within budget: q1 (8<=10), a1 (50<=60) -> 2/5
    assert out["within_budget_rate"] == pytest.approx(0.4)
    # completed walls 8, 12, 50, 5 -> median (8 + 12) / 2 = 10
    assert out["median_wall_s_correct"] == pytest.approx(10.0)
    assert [t["wall_budget_s"] for t in tasks] == [10.0, 10.0, 60.0, 120.0, 30.0, None]
    assert [t["within_budget"] for t in tasks] == [True, False, True, True, True, None]
    assert out["unbudgeted"] == 1 and out["per_kind"]["multi_step"]["completed_within_budget"] == 1


def test_time_budget_screen_and_sidekick_tasks_and_sample(tmp_path):
    tasks = [{"task_id": "s1", "test": "screen_task", "level": "L1", "completed": True, "wall_s": 14.0},
             {"task_id": "s2", "test": "screen_task", "level": "L3", "completed": True, "wall_s": 61.0},
             {"task_id": "s3", "test": "screen_task", "level": 2, "completed": True, "wall_s": 30.0},
             {"task_id": "k1", "test": "sidekick_task", "mode": "answer_only", "completed": True, "wall_s": 9.0},
             {"task_id": "k2", "test": "sidekick_task", "mode": "tool_mode", "completed": True, "wall_s": 70.0},
             {"task_id": "q1", "kind": "question", "completed": False, "wall_s": 3.0}]
    out = sustained.time_budget(tasks)
    assert [t["wall_budget_s"] for t in tasks] == [15.0, 60.0, 30.0, 10.0, 60.0, 10.0]
    # screen: s1 and s3 within (14<=15, 30<=30), s2 not (61>60) -> 2/3; sidekick: k1 only -> 1/2
    assert out["by_test"]["screen_task"]["within_budget_rate"] == pytest.approx(2 / 3)
    assert out["by_test"]["sidekick_task"]["within_budget_rate"] == pytest.approx(0.5)
    assert out["by_test"]["agentic"]["within_budget_rate"] == 0.0
    assert out["within_budget_rate"] == pytest.approx(3 / 6)
    w = RunWriter(tmp_path, "r", started_utc="2026-01-01T00:00:00Z")
    row = {"task_id": "s9", "test": "screen_task", "level": "L2", "completed": True,
           "t_submit_ns": 10 * NS, "t_end_ns": 35 * NS}
    s = sustained.task_sample(row, sample_id="s00001", model_id="M", entry_id="M", mode="as_shipped")
    assert s["wall_budget_s"] == 30.0 and s["within_budget"] is True and s["wall_s"] == 25.0
    assert s["test"] == "screen_task" and s["task_id"] == "s9" and s["screen_level"] == "L2"
    w.sample(s)
    k = sustained.task_sample({"task_id": "k9", "test": "sidekick_task", "mode": "tool_mode", "completed": False,
                               "wall_s": 12.0}, sample_id="s00002")
    assert k["within_budget"] is True and k["sidekick_mode"] == "tool_mode" and k["mode"] is None
    w.sample(k)
