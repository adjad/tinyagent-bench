"""Plans, the example manifest, and a pack-to-record run through the fake engine."""
from __future__ import annotations

import json
from pathlib import Path

from edat.perf import plans
from edat.perf.cli import execute, main
from edat.perf.manifest import PackPrompts, load_manifest, verify_pinned

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "test_fixtures" / "perf"
MANIFEST = FIX / "models-manifest.example.json"


def test_example_manifest_loads_and_pins_gemma_wisp():
    entries = load_manifest(MANIFEST)
    by = {e.entry_id: e for e in entries}
    assert by["Gemma-Wisp"].role == "baseline" and by["gemma-4-E4B-it-oQ4e-mtp"].role == "reference"
    from service.setup.catalog import GEMMA_HARBOR_FILES
    pinned = [{"name": n, "sha256": h, "bytes": b} for n, h, b in GEMMA_HARBOR_FILES]
    assert verify_pinned(by["Gemma-Wisp"].raw["files"], pinned) == []
    assert by["Gemma-Wisp"].engine_id("plain") == "Gemma-Wisp-plain"
    assert by["Gemma-Wisp"].record("plain")["speculation"] == "none"
    assert by["example-candidate-4bit"].engine_id("plain") == "example-candidate-4bit"   # no drafter


def test_speed_plan_is_bracketed_abba():
    steps = plans.build("speed", load_manifest(MANIFEST))
    order = [(s.entry.entry_id, s.bracket) for s in steps]
    assert order[0] == ("Gemma-Wisp", "start") and order[-1] == ("Gemma-Wisp", "end")
    starts = [e for e, b in order if b == "start"]
    ends = [e for e, b in order if b == "end"]
    assert starts[-1] == "gemma-4-E4B-it-oQ4e-mtp" and ends[0] == "gemma-4-E4B-it-oQ4e-mtp"
    ref_steps = [s for s in steps if s.entry.role == "reference"]
    assert all(s.mode == "plain" and s.args["levels"] == (512, 2048, 10240) and s.args["reps"] == 10
               for s in ref_steps)


def test_energy_plan_and_estimate():
    steps = plans.build("energy", load_manifest(MANIFEST), levels=(512, 2048, 32768))
    cand = [s for s in steps if s.bracket == "candidate"]
    assert [s.args["level"] for s in cand] == [512, 2048, 32768]
    assert all(s.args["level"] == 2048 for s in steps if s.bracket != "candidate")
    assert plans.estimate_s(steps) > 0


def test_plan_command_prints_without_contacting_anything(capsys):
    assert main(["plan", "--manifest", str(MANIFEST), "--plan", "smoke"]) == 0
    out = capsys.readouterr().out
    assert "first_token" in out and "rough duration" in out


def test_stub_pack_through_runner_to_record(tmp_path):
    """Build a pack with the stub tokenizer, run it through the fake engine, finalize."""
    from edat.perf.pack import build_pack, write_pack
    from edat.perf.tokenizer import StubTokenizer
    from tests.test_edat_perf_runner import entry, rig
    tok = StubTokenizer()
    corpus = [json.loads(x) for x in (FIX / "agent-corpus-synthetic.jsonl").read_text().splitlines() if x.strip()]
    traj = json.loads((FIX / "agent-trajectory-synthetic.json").read_text())
    pack = build_pack(corpus, traj, tok, model_id="M", entry_id="M", thinking=True, levels=(512, 1024, 2048),
                      n_variants=24, replay_variants=8, built_utc="2026-01-01T00:00:00Z")
    p = tmp_path / "M.pack.json"
    write_pack(pack, p)
    prompts = PackPrompts.load(str(p))
    r, eng, clock, _, w = rig(tmp_path / "runs", entries=[entry("M")], prompts={"M": prompts},
                              tokenize=tok.encode)
    e = r.entries["M"]
    for step in plans.build("smoke", [e], bracket=False):
        execute(step, r)
    r.agent_replay(e, "as_shipped", cache="defeated", reps=1)
    path = w.finalize(ended_utc=clock.utc(), device={"class": "mac-ref"}, runtime={"name": "omlx"},
                      models=r.finish_models(), prompt_pack={"M": prompts.info()},
                      conditions_start={}, conditions_end={})
    doc = json.loads(path.read_text())
    lv = [s for s in doc["samples"] if s["test"] == "level"]
    assert lv and all(s["status"] == "ok" and s["prompt_tokens"] == s["level_tokens"] for s in lv)
    rp = [s for s in doc["samples"] if s["test"] == "agent_replay"]
    assert [s["step"] for s in rp] == [1, 2, 3, 4, 5, 6]
    assert doc["prompt_pack"]["M"]["pack_sha256"] == pack["pack_sha256"]


def test_sustained_plan_steps_dispatch(tmp_path, monkeypatch):
    """Each sustained plan reaches its driver (drivers replaced by recorders here)."""
    from types import SimpleNamespace
    from edat.perf import sustained, sustained_steps
    called = []
    for name in ("s1_sustained", "s2_battery", "s3_idle_residency", "reload_after_idle"):
        monkeypatch.setattr(sustained, name, lambda *a, _n=name, **k: called.append(_n))
    runner = SimpleNamespace(i=SimpleNamespace(power=None))
    for w in ("s1", "s2", "s3", "reload"):
        sustained_steps.execute(w, runner, None, "plain", SimpleNamespace())
    assert called == ["s1_sustained", "s2_battery", "s3_idle_residency", "reload_after_idle"]
    import pytest
    with pytest.raises(SystemExit):
        sustained_steps.execute("sim_day", runner, None, "plain", SimpleNamespace(sim_day_script=None))
