"""The real-run wrapper (edat/screen/live.py), with stub clients only: no engine, no model, no host checks."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from edat.screen import live
from edat.screen.adapter import EngineError
from edat.screen.cases.store import load_cases
from edat.sidekick.ceiling.models import LocalModel

DEV = load_cases(live.DEV_DIR)
# CI has no private folder: the dev split stands in for the held-out one, renamed so ids do not clash.
HELDOUT = [dict(c, id=c["id"].replace("scr_", "scr_x", 1), split="heldout") for c in DEV]


def fake_model(engine_id: str = live.REFERENCES[0], loader: str = "mlx-vlm") -> LocalModel:
    return LocalModel(engine_id=engine_id, folder=engine_id, label="test", role="baseline", model_type="gemma4",
                      family="gemma4", loader=loader, disk_gb=5.5, quant={"scheme": "oQ4e", "bits": 4},
                      bits_per_weight=5.4, thinking=(False, True), standard=True, group=engine_id, decode_tok_s=47.0)


def go(tmp_path: Path, **kw):
    kw.setdefault("plan", "smoke")
    kw.setdefault("client_kind", "oracle")
    kw.setdefault("models", [fake_model()])
    kw.setdefault("lock", str(tmp_path / "heavy.lock"))
    kw.setdefault("max_minutes", 5)
    return live.run(tmp_path / "out", dev=DEV, heldout=HELDOUT, progress=None, **kw)


def records(tmp_path: Path, plan: str = "smoke") -> list[dict]:
    return [json.loads(x) for f in sorted((tmp_path / "out" / plan).glob("runs/*/records.jsonl"))
            for x in f.read_text().splitlines() if x.strip()]


def test_core_cases_are_every_core_case_of_both_splits_on_each_track():
    text = live.plan_cases("core", "ax_text", dev=DEV, heldout=HELDOUT)
    shots = live.plan_cases("core", "screenshot_som", dev=DEV, heldout=HELDOUT)
    core = [c for c in DEV if c.get("core")]
    assert len(text) == 2 * len(core)
    assert len(shots) == 2 * sum(1 for c in core if "screenshot_som" in c["tracks"])
    smoke = live.plan_cases("smoke", "ax_text", dev=DEV)
    assert len(smoke) == live.SMOKE_CASES["ax_text"] and len({c["family"] for c in smoke}) >= 5
    assert all(c["split"] == "dev" for c in smoke)


def test_a_smoke_run_writes_valid_records_with_sampling_selection_and_seeds(tmp_path):
    log = go(tmp_path)
    assert [u["result"] for u in log["units"]] == ["done", "done"]
    recs = records(tmp_path)
    assert len(recs) == sum(live.SMOKE_CASES.values())
    r = recs[0]
    assert r["settings"]["thinking"] == "off"                      # the shipped mode
    assert r["settings"]["sampling"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "max_output_tokens": 256}
    assert r["settings"]["sampling_source"] == "author_recommended"
    assert r["selection"]["tier"] == "smoke" and len(r["selection"]["manifest_sha256"]) == 64
    seeds = {(x["case_id"], x["trial"]): x["settings"]["seed"] for x in recs}     # one seed per case and trial,
    assert len(set(seeds.values())) == len(seeds)                                # shared by the two tracks
    assert not (tmp_path / "heavy.lock").exists()                    # created and removed
    run = next((tmp_path / "out" / "smoke").glob("runs/*screenshot_som"))
    names = {a["name"] for a in json.loads((run / "manifest.json").read_text())["attachments"]}
    assert names == {"restraint-events.jsonl", "restraint-trials.json", "perf-samples.jsonl"}
    perf = [json.loads(x) for x in (run / "perf-samples.jsonl").read_text().splitlines()]
    assert all({"entry_id", "completed", "level", "wall_s"} <= set(p) for p in perf)


def test_a_rerun_resumes_without_redoing_trials(tmp_path):
    go(tmp_path)
    before = records(tmp_path)
    log = go(tmp_path)
    assert all(u["written"] == 0 and u["result"] == "done" for u in log["units"])
    assert records(tmp_path) == before


def test_the_stop_file_and_the_minute_limit_stop_between_trials(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "STOP").write_text("")
    log = go(tmp_path)
    assert "STOP" in log["stopped"] and not records(tmp_path) and not (tmp_path / "heavy.lock").exists()
    (tmp_path / "out" / "STOP").unlink()
    log = go(tmp_path, max_minutes=0)
    assert "minute limit" in log["stopped"] and not records(tmp_path)


def test_an_existing_lock_refuses_to_start_and_is_left_alone(tmp_path):
    os.mkdir(tmp_path / "heavy.lock")
    with pytest.raises(SystemExit, match="another heavy job"):
        go(tmp_path)
    assert (tmp_path / "heavy.lock").exists() and not records(tmp_path)


def test_results_inside_the_repository_are_refused():
    with pytest.raises(SystemExit, match="outside the repository"):
        live.run(live.REPO / "results", plan="smoke", client_kind="oracle", models=[fake_model()], dev=DEV,
                 progress=None)


def test_harbor_open_refuses_a_run_that_manages_the_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "harbor_running", lambda: True)
    with pytest.raises(SystemExit, match="Harbor"):
        go(tmp_path, engine=True, base_url="http://127.0.0.1:9")
    assert not (tmp_path / "heavy.lock").exists() and not (tmp_path / "out" / "engine-state.json").exists()


class NoImages:
    """Answers text requests like the oracle would not; fails every request that carries an image."""

    def describe(self):
        return {"kind": "local", "runtime": "stub"}

    def chat(self, messages, **kw):
        if any(isinstance(m.get("content"), list) for m in messages):
            raise EngineError("http_400", "image input is not supported", retryable=False)
        return live.STUBS["null"].chat(messages, **kw)


def test_a_screenshot_track_the_engine_refuses_is_skipped_after_three_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "_client", lambda kind, m, url: (lambda: NoImages()))
    log = go(tmp_path, tracks=("screenshot_som",))
    u = log["units"][0]
    assert u["result"] == "skipped" and "images" in u["skipped"] and u["written"] == live.IMAGE_PROBE_TRIALS


def test_a_model_without_a_vision_loader_skips_the_screenshot_track(tmp_path):
    log = go(tmp_path, models=[fake_model(loader="mlx-lm")])
    assert log["units"][0]["result"] == "done" and log["units"][1]["result"].startswith("skipped")


def test_the_estimate_reproduces_the_spec_basis_and_takes_measured_pace():
    e = live.estimate("core", dev=DEV, heldout=HELDOUT)
    assert e["tracks"]["ax_text"]["basis"] == "assumed" and e["tracks"]["ax_text"]["sec_per_step"] == 2.5
    m = live.estimate("core", dev=DEV, heldout=HELDOUT,
                      measured={"ax_text": {"sec_per_step": 5.0, "step_factor": 1.7}})
    assert m["tracks"]["ax_text"]["basis"] == "measured"
    assert m["tracks"]["ax_text"]["hours_per_model"] > 1.9 * e["tracks"]["ax_text"]["hours_per_model"]
    assert m["tracks"]["screenshot_som"]["basis"] == "assumed"
