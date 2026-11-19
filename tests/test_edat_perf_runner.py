"""The Mac runner against a fake engine: raw samples, order, statuses, loads, pairs, replay."""
from __future__ import annotations

import json

import pytest

from edat.perf.client import OmlxPerfClient, ForbiddenEndpoint, check_base_url
from edat.perf.clock import FakeClock
from edat.perf.constants import GIB, MIB
from edat.perf.manifest import ModelEntry
from edat.perf.memory import FootprintMonitor
from edat.perf.record import RunWriter
from edat.perf.runner import Instruments, MacRunner, PowerControl, RunConfig
from edat.perf.testing import FakeEngine, FakeModel, FakeProbe, FakeSystem, WordPrompts


def entry(mid="M", **kw) -> ModelEntry:
    raw = {"files": [{"name": "model.safetensors", "sha256": "0" * 64, "bytes": 5 * GIB}], "speculation": "none"}
    raw.update(kw.pop("raw", {}))
    return ModelEntry(id=mid, entry_id=mid, role=kw.pop("role", "candidate"), thinking="on",
                      engine_ids={"as_shipped": mid}, pack_path=None,
                      native_window_tokens=kw.pop("native", 131072), profile_window_tokens=kw.pop("profile", None),
                      extension_factor=None, raw=raw)


def rig(tmp_path, models=None, entries=None, prompts=None, cfg=None, **engine_kw):
    clock = FakeClock()
    models = models or {"M": FakeModel()}
    eng = FakeEngine(clock, models, **engine_kw)
    client = OmlxPerfClient(eng, now_ns=clock.now_ns)
    w = RunWriter(tmp_path, "run1", started_utc=clock.utc())
    mon = FootprintMonitor(FakeProbe(eng), clock.now_ns, w.mem_log)
    mon.attach_fake_clock(clock)
    sysm = FakeSystem(clock)
    inst = Instruments(clock=clock, client=client, monitor=mon, sysmem=sysm, thermal=sysm.thermal,
                       probe_alive=lambda: eng.alive, power=PowerControl(trace_name="power.jsonl"),
                       disk_read_mb=lambda: eng.disk_read_bytes / MIB, purge_fake=eng.purge)
    entries = entries or [entry(m) for m in models]
    prompts = prompts or {e.entry_id: WordPrompts() for e in entries}
    r = MacRunner(w, inst, entries, prompts, cfg or RunConfig(reps=3, reps_long=2, gap_s=5.0))
    return r, eng, clock, sysm, w


def samples(w):
    lines = [json.loads(x) for x in w.samples_path.read_text().splitlines()]
    from edat.perf.record import latest_per_id
    return latest_per_id(lines)


def test_port_guard():
    for bad in ("http://127.0.0.1:8000", "http://127.0.0.1:8765", "http://localhost:8791",
                "http://10.0.0.2:8779", "https://127.0.0.1:8779"):
        with pytest.raises(ForbiddenEndpoint):
            check_base_url(bad)
    assert check_base_url("http://127.0.0.1:8779") == ("127.0.0.1", 8779)


def test_levels_order_variants_and_timing(tmp_path):
    r, eng, clock, _, w = rig(tmp_path)
    e = r.entries["M"]
    r.levels(e, "as_shipped", (512, 1024, 2048))
    ss = samples(w)
    assert [s["phase"] for s in ss[:3]] == ["warmup"] * 3
    assert [s["level_tokens"] for s in ss[:3]] == [512, 1024, 2048]          # warm-up ascending
    assert [s["level_tokens"] for s in ss[3:6]] == [512, 1024, 2048]         # round 1 ascending
    assert [s["level_tokens"] for s in ss[6:9]] == [2048, 1024, 512]         # round 2 descending
    assert len(ss) == 3 + 3 * 3
    # fresh nonce for every request of a level
    for L in (512, 1024, 2048):
        vs = [s["variant"] for s in ss if s["level_tokens"] == L]
        assert len(set(vs)) == len(vs)
    s = ss[3]
    assert s["status"] == "ok" and s["prompt_tokens"] == 512 and s["output_tokens"] == 256
    assert s["cached_tokens"] == 0
    # TTFT = prefill (512 / 2000 s) + half a decode interval (0.01 s) in the fake
    assert (s["t_first_token_ns"] - s["t_submit_ns"]) == pytest.approx(0.256e9 + 0.01e9, abs=2)
    # decode (n-1)/(t_last - t_first) = 50 tok/s
    assert 255 / ((s["t_last_token_ns"] - s["t_first_token_ns"]) / 1e9) == pytest.approx(50.0)
    assert len(s["token_times_ns"]) == 255 and s["token_times_kind"] == "chunk"
    # memory: steady during decode = weights + overhead + base + kv
    m = eng.models["M"]
    assert s["footprint_start_bytes"] == eng.base_footprint + m.weights_bytes + m.overhead_bytes
    assert s["footprint_peak_bytes"] >= s["footprint_start_bytes"] + 512 * m.kv_bytes_per_token
    assert s["footprint_steady_bytes"] > s["footprint_start_bytes"]
    # the gap after every sample
    assert clock.slept.count(5.0) >= len(ss)
    assert (tmp_path / "run1" / "mem-run1.jsonl").exists()


def test_long_levels_get_fewer_reps(tmp_path):
    r, *_ , w = rig(tmp_path, prompts={"M": WordPrompts(levels=(512, 32768))})
    r.levels(r.entries["M"], "as_shipped", (512, 32768))
    ss = [s for s in samples(w) if s["phase"] == "measured"]
    assert sum(1 for s in ss if s["level_tokens"] == 512) == 3
    assert sum(1 for s in ss if s["level_tokens"] == 32768) == 2


def test_window_statuses_and_missing_level(tmp_path):
    r, *_ , w = rig(tmp_path, entries=[entry("M", native=2048, profile=1500)])
    r.levels(r.entries["M"], "as_shipped", (512, 1024, 2048, 5120), reps=1)
    by = {}
    for s in samples(w):
        by.setdefault(s["level_tokens"], set()).add(s["status"])
    assert by[512] == {"ok"}
    assert by[1024] == {"ok"}                            # 1024+256 <= 1500
    assert by[2048] == {"exceeds_native_window"}         # 2048+256 > 2048 native window
    assert by[5120] == {"unsupported_by_runtime"}        # not in the pack


def test_prefix_cache_hit_is_recorded(tmp_path):
    p = WordPrompts(variants=1)
    r, *_ , w = rig(tmp_path, prompts={"M": p}, prefix_cache=True)
    r.levels(r.entries["M"], "as_shipped", (512,), reps=1)
    ss = samples(w)
    assert ss[0]["cached_tokens"] == 0
    assert ss[1]["cached_tokens"] == 512                 # same variant reused: the aggregator voids it
    ev = [json.loads(x) for x in w.events_path.read_text().splitlines()]
    assert any(x["kind"] == "operator_note" and "wrapped" in x["detail"] for x in ev)


def test_engine_death_is_a_result_and_higher_levels_not_run(tmp_path):
    m = FakeModel()
    limit = 300 * MIB + m.weights_bytes + m.overhead_bytes + 1500 * m.kv_bytes_per_token
    r, eng, *_ , w = rig(tmp_path, models={"M": m}, oom_bytes=limit)
    r.levels(r.entries["M"], "as_shipped", (512, 1024, 2048), reps=1)
    ss = samples(w)
    st = [(s["level_tokens"], s["status"]) for s in ss]
    assert (512, "ok") in st and (1024, "ok") in st
    assert (2048, "killed_unknown") in st
    assert all(status == "not_run_above_oom" for L, status in st[st.index((2048, "killed_unknown")) + 1:]
               if L >= 2048)
    dead = next(s for s in ss if s["status"] == "killed_unknown")
    assert dead["kill_evidence"]["last_footprint_bytes"] is not None


def test_engine_refusal_at_load(tmp_path):
    r, *_ , w = rig(tmp_path, ceiling_bytes=1 * GIB)
    r.levels(r.entries["M"], "as_shipped", (512,), reps=1)
    assert {s["status"] for s in samples(w)} == {"engine_refused"}


def test_memory_pressure_and_foreign_work(tmp_path):
    r, eng, clock, sysm, w = rig(tmp_path)
    e = r.entries["M"]
    orig = sysm.counters

    calls = {"n": 0}

    def growing():
        calls["n"] += 1
        sysm.swap_used += 300 * MIB
        return orig()
    sysm.counters = growing
    r.request(e, "as_shipped", test="level", phase="measured", prompt=r.prompts["M"].level_prompt(512, 0),
              level=512, variant=0)
    sysm.counters = orig
    eng.foreign_active_at.append((0, 10**18))
    r.request(e, "as_shipped", test="level", phase="measured", prompt=r.prompts["M"].level_prompt(512, 1),
              level=512, variant=1)
    ss = samples(w)
    assert ss[0]["status"] == "memory_pressure" and ss[0]["swap_delta_bytes"] == 300 * MIB
    assert ss[1]["status"] == "voided_foreign_work"


def test_cooldown_gate_waits_then_runs_flagged(tmp_path):
    r, eng, clock, sysm, w = rig(tmp_path, cfg=RunConfig(cooldown_max_s=20, cooldown_poll_s=5, gap_s=0))
    sysm.thermal_script = [(0, "serious")]
    e = r.entries["M"]
    r.request(e, "as_shipped", test="level", phase="measured", prompt=r.prompts["M"].level_prompt(512, 0),
              level=512, variant=0)
    s = samples(w)[0]
    assert s["thermal_not_nominal"] is True and s["cooldown_wait_s"] >= 20
    ev = [json.loads(x)["kind"] for x in w.events_path.read_text().splitlines()]
    assert "cooldown_timeout" in ev


def test_load_cold_and_warm(tmp_path):
    r, eng, *_ , w = rig(tmp_path)
    r.load(r.entries["M"], "as_shipped", warm_reps=2, cold_reps=2)
    ss = samples(w)
    tests = [s["test"] for s in ss]
    assert tests == ["load_cold", "load_warm", "load_cold", "load_warm"]
    cold = [s for s in ss if s["test"] == "load_cold"]
    warm = [s for s in ss if s["test"] == "load_warm"]
    for s in cold:
        ld = s["load"]
        assert (ld["t_ready_ns"] - ld["t_load_start_ns"]) == pytest.approx(6e9, abs=2)
        assert ld["cold_verified"] is True and ld["disk_read_bytes"] == 5 * GIB
        assert ld["t_first_token_possible_ns"] > ld["t_ready_ns"]
    for s in warm:
        assert (s["load"]["t_ready_ns"] - s["load"]["t_load_start_ns"]) == pytest.approx(2e9, abs=2)
        assert s["load"]["cold_verified"] is None


def test_energy_pairs_window(tmp_path):
    r, eng, clock, _, w = rig(tmp_path, cfg=RunConfig(gap_s=1.0, idle_window_s=60))
    win = r.energy_pairs(r.entries["M"], "as_shipped", 512, k=3)
    ss = samples(w)
    assert [s["shape"] for s in ss] == ["A", "B"] * 3
    assert [s["output_tokens"] for s in ss] == [1, 257] * 3
    assert all(s["energy_window_id"] == win["window_id"] for s in ss)
    assert win["sample_ids"] == [s["sample_id"] for s in ss]
    assert win["t_start_ns"] <= ss[0]["t_submit_ns"] and win["t_end_ns"] >= ss[-1]["t_end_ns"]
    assert 60.0 in clock.slept
    assert len({s["variant"] for s in ss}) == 6


def test_agent_replay_cached_and_defeated(tmp_path):
    r, eng, *_ , w = rig(tmp_path, prefix_cache=True)
    e = r.entries["M"]
    r.agent_replay(e, "as_shipped", cache="on", reps=1)
    r.agent_replay(e, "as_shipped", cache="defeated", reps=1)
    ss = samples(w)
    on = [s for s in ss if s["replay_cache"] == "on"]
    off = [s for s in ss if s["replay_cache"] == "defeated"]
    assert [s["step"] for s in on] == [1, 2, 3, 4, 5, 6]
    assert on[0]["cached_tokens"] == 0 and all(s["cached_tokens"] > 0 for s in on[1:])
    assert on[1]["new_prefill_tokens"] == on[1]["prompt_tokens"] - on[1]["cached_tokens"]
    assert all(s["cached_tokens"] == 0 for s in off)
    assert len({s["variant"] for s in off}) == 6


def test_fit16_uses_helper_and_always_restores(tmp_path):
    from edat.perf.admin_client import FakeAdminClient
    r, *_ , w = rig(tmp_path, prompts={"M": WordPrompts(levels=(512, 2048, 5120, 10240, 32768))})
    adm = FakeAdminClient()
    r.i.admin = adm
    r.fit16(r.entries["M"], "as_shipped", reps=2)
    ss = samples(w)
    assert {s["test"] for s in ss} == {"fit16_emulation"}
    assert sorted({s["level_tokens"] for s in ss}) == [2048, 10240, 32768]    # S, W, then higher
    verbs = [c[0] if isinstance(c, tuple) else c for c in adm.calls]
    assert verbs[:2] == ["balloon_wire", "wired_limit_set"]
    assert verbs[-2:] == ["balloon_release", "wired_limit_reset"]


def test_finalize_document(tmp_path):
    r, eng, clock, _, w = rig(tmp_path)
    e = r.entries["M"]
    r.first_token(e, "as_shipped")
    r.load(e, "as_shipped", warm_reps=1, cold_reps=0)
    path = w.finalize(ended_utc=clock.utc(), device={"class": "mac-ref"}, runtime={"name": "omlx"},
                      models=r.finish_models(), prompt_pack={"M": {"pack_sha256": None}},
                      conditions_start={}, conditions_end={})
    doc = json.loads(path.read_text())
    assert doc["schema"] == "edat.perf/1.0" and doc["kind"] == "local" and doc["clock"] == "mach_continuous_time"
    assert "cells" not in doc and "verdicts" not in doc        # the aggregator writes those
    assert len({s["sample_id"] for s in doc["samples"]}) == len(doc["samples"])
    assert doc["samples"][1]["load"]["t_ready_ns"] is not None
    assert doc["models"][0]["mode"] == "as_shipped"


def test_http_transport_sends_the_key_and_refuses_reserved_ports(monkeypatch):
    from edat.perf.client import HttpTransport
    t = HttpTransport("http://127.0.0.1:8779", now_ns=lambda: 0, api_key="k" * 40)
    assert t._headers["Authorization"] == "Bearer " + "k" * 40
    monkeypatch.setenv("EDAT_OMLX_API_KEY", "x" * 40)
    c = OmlxPerfClient.connect("http://127.0.0.1:8779", now_ns=lambda: 0)
    assert c.t._headers["Authorization"].endswith("x" * 40)
    with pytest.raises(ForbiddenEndpoint):
        OmlxPerfClient.connect("http://127.0.0.1:8000", now_ns=lambda: 0)
