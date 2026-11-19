"""Runner, record writer and headline scores, driven by scripted stub clients.

No model is loaded and nothing is sent anywhere: the stubs implement the model
adapter's `chat()` signature and answer from the cases' own reference answers
(a "perfect" model), with fixed bad text, or by raising an engine error.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edat.quality.headline import (BASE_ID, BASELINE_ID, components, headline_report, noise_metrics,
                                   text_understanding)
from edat.quality.records import QualityRunSink, read_records
from edat.quality.render import build_messages
from edat.quality.runner import (DEFAULT_SEEDS, Entry, HostedSplitError, run_quality, variants)
from edat.quality.splits import load_dev

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def sub():
    fams = load_dev()
    pick = lambda fam, n: fams[fam][:n]  # noqa: E731
    return {"source": [c for c in fams["source"] if c["tier"] in ("S", "M")][:10], "numeric": pick("numeric", 5),
            "schema": pick("schema", 7), "noisy": fams["noisy"]}


class _Res:
    def __init__(self, text, reasoning="", finish="stop", completion=40):
        self.text, self.reasoning, self.finish_reason = text, reasoning, finish
        self.usage = {"completion_tokens": completion, "reasoning_tokens": None}
        self.timing = None
        self.request, self.meta = {}, {}


class EngineError(Exception):  # same class name as the adapter's
    def __init__(self, kind="transport"):
        super().__init__(kind)
        self.kind = kind


def _index(sub):
    idx = {}
    by_task = {}
    for fam in ("source", "numeric", "schema"):
        for c in sub[fam]:
            for t in c["tasks"]:
                by_task[t["task_id"]] = (c, t)
                for r, cond in variants(c):
                    idx[build_messages(c, t, rendering=r, condition=cond)[-1]["content"]] = t
    for n in sub["noisy"]:
        if n["parent_task_id"] in by_task:
            c, t = by_task[n["parent_task_id"]]
            idx[build_messages(c, t, request=n["request"])[-1]["content"]] = t
    return idx


class Perfect:
    """Answers every task with its reference answer (or its gold JSON)."""

    def __init__(self, sub, think=False):
        self.idx = _index(sub)
        self.calls = []
        self.think = think

    def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
        self.calls.append({"seed": seed, "max_tokens": max_tokens, "thinking": thinking, "tools": tools})
        t = self.idx[messages[-1]["content"]]
        text = json.dumps(t["gold"]) if t["kind"] == "extraction" else t["reference_answer"]
        if self.think:
            text = "<think>planning the answer</think>" + text
        return _Res(text)


class Bad:
    def chat(self, messages, **kw):
        return _Res("Great question! The code is ZZ-99999 and it costs 12.34 EUR. Let me know if you need anything else.")


class Flaky(Perfect):
    def __init__(self, sub, fail_every=1):
        super().__init__(sub)
        self.n = 0

    def chat(self, messages, **kw):
        self.n += 1
        if self.n % 2 == 1:
            raise EngineError("timeout")
        return super().chat(messages, **kw)


class Down:
    def chat(self, messages, **kw):
        raise EngineError("server")


def _run(client, model_id, sub, **kw):
    role = kw.pop("role", "baseline" if model_id == BASELINE_ID else "reference" if model_id == BASE_ID else "candidate")
    entry = Entry(model_id, role=role, **{k: v for k, v in kw.items() if k in Entry.__dataclass_fields__})
    return run_quality(client, entry, sub, split="dev", board_version="2027.1", harness_commit="abcdef1",
                       run_id=f"test-{model_id}", **{k: v for k, v in kw.items() if k not in Entry.__dataclass_fields__})


def test_perfect_model_scores_full_marks(sub):
    sink = _run(Perfect(sub), BASELINE_ID, sub)
    ok = [r for r in sink.records if r["status"] == "ok"]
    assert ok and all(r["metrics"]["pass"] for r in ok), [r["case_id"] for r in ok if not r["metrics"]["pass"]][:5]
    rep = headline_report(sink.records, n_boot=50)
    e = rep["entries"][0]
    assert e["text_understanding"] == pytest.approx(100.0)
    assert e["readability_label"] == "structure only"
    assert e["components"]["injection_followed_rate"] == 0.0


def test_three_seeds_and_fixed_cap(sub):
    client = Perfect(sub)
    sink = _run(client, "cand-a", sub)
    assert {c["seed"] for c in client.calls} == set(DEFAULT_SEEDS)
    assert {c["max_tokens"] for c in client.calls} == {4096}
    assert all(c["tools"] is None for c in client.calls)
    assert {r["trial"] for r in sink.records} == {1, 2, 3}
    assert {r["settings"]["seed"] for r in sink.records} == set(DEFAULT_SEEDS)


def test_records_are_flat_and_in_the_shared_envelope(sub):
    sink = _run(Perfect(sub), "cand-b", sub, limit=20)
    for r in sink.records:
        assert r["schema"] == "edat.result/1.0" and r["tier"] == "quality" and r["lang"] == "en"
        assert r["split"] == "dev" and r["kind"] == "local"
        for k, v in r["metrics"].items():
            assert v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= 64), (k, v)
        assert set(r["safety"]) >= {"unsafe_actions", "privacy_leaks"}
        assert r["raw"]["path"].startswith("raw/") and r["raw"]["sha256"] in sink.raw
        assert len(r["case_id"]) <= 160


def test_thinking_entry_strips_reasoning_and_records_mode(sub):
    sink = _run(Perfect(sub, think=True), "cand-think", sub, thinking=True, limit=12)
    assert all(r["settings"]["thinking"] == "on" for r in sink.records)
    assert all(r["metrics"]["pass"] for r in sink.records if r["status"] == "ok")


def test_bad_model_loses_against_both_references(sub):
    recs = (_run(Perfect(sub), BASELINE_ID, sub).records + _run(Perfect(sub), BASE_ID, sub).records
            + _run(Bad(), "cand-bad", sub).records)
    rep = headline_report(recs, n_boot=200)
    bad = next(e for e in rep["entries"] if e["model_id"] == "cand-bad")
    assert bad["deltas"]["tuned"]["text_understanding"]["label"] == "worse"
    assert bad["deltas"]["base"]["text_understanding"]["label"] == "worse"
    assert bad["components"]["fabricated_detail_rate"] > 50
    assert rep["tuning_effect"]["label"] == "approx_baseline"   # identical reference answers
    base = next(e for e in rep["entries"] if e["model_id"] == BASE_ID)
    assert base["deltas"]["tuned"]["text_understanding"]["delta"] == pytest.approx(0.0)


def test_engine_error_retries_once_then_excludes(sub):
    sink = _run(Flaky(sub), "cand-flaky", sub, limit=6)
    assert all(r["status"] == "ok" for r in sink.records)
    sink = _run(Down(), "cand-down", sub, limit=3)
    assert all(r["status"] == "excluded" and r["raw"] is None for r in sink.records)


def test_non_engine_errors_propagate(sub):
    class Broken:
        def chat(self, messages, **kw):
            raise ValueError("bug in the harness")
    with pytest.raises(ValueError):
        _run(Broken(), "cand-broken", sub, limit=1)


def test_does_not_fit_is_recorded_and_dropped(sub):
    sink = _run(Perfect(sub), "cand-small", sub, context_window=1500)
    st = {r["status"] for r in sink.records}
    assert "beyond_window" in st and "ok" in st
    assert all(r["status_detail"] == "does_not_fit" for r in sink.records if r["status"] == "beyond_window")
    e = headline_report(sink.records, n_boot=20)["entries"][0]
    assert e["does_not_fit"] > 0 and 0 < e["does_not_fit_rate"] < 1


def test_cloud_reference_refuses_heldout_and_marks_trials(sub):
    with pytest.raises(HostedSplitError):
        run_quality(Perfect(sub), Entry("anthropic/claude-haiku-5.5", kind="cloud_reference", seedless=True), sub,
                    split="heldout", board_version="2027.1", harness_commit="abcdef1")
    client = Perfect(sub)
    sink = run_quality(client, Entry("anthropic/claude-haiku-5.5", kind="cloud_reference", seedless=True), sub,
                       split="dev", board_version="2027.1", harness_commit="abcdef1", limit=2)
    assert all(c["seed"] is None and c["max_tokens"] == 16384 for c in client.calls)
    assert all(r["settings"]["seed"] == "unsupported" and r["device"] is None and r["device_reason"] == "cloud"
               and r["model"]["role"] == "cloud_reference" and r["status_detail"] == "trials_not_seeds"
               for r in sink.records)
    assert {r["trial"] for r in sink.records} == {1, 2, 3}


def test_injection_followed_goes_to_safety(sub):
    case = next(c for c in sub["source"] if c["source_type"] == "email")
    canary = next(t for t in case["traps"] if t["trap_type"] == "planted_instruction")["canary"]
    single = {"source": [case], "numeric": [], "schema": [], "noisy": []}

    class Obeys:
        def chat(self, messages, **kw):
            return _Res(f"Reply {canary} to unsubscribe.")
    sink = _run(Obeys(), "cand-obeys", single, limit=1)
    r = sink.records[0]
    if r["metrics"].get("injection_planted"):
        assert r["safety"]["unsafe_actions"] >= 1 and "injection_followed" in r["safety"]["labels"]


def test_sink_writes_the_run_directory(tmp_path, sub):
    sink = _run(Perfect(sub), "cand-w", sub, limit=4)
    sink.store = tmp_path
    run_dir = sink.write()
    recs = read_records(run_dir)
    assert len(recs) == len(sink.records)
    for r in recs:
        assert (run_dir / r["raw"]["path"]).exists()
    man = json.loads((run_dir / "manifest.json").read_text())
    assert man["records"]["count"] == len(recs)


def test_noise_metrics_from_records(sub):
    sink = _run(Perfect(sub), BASELINE_ID, sub)
    nm = list(noise_metrics(sink.records, sub["noisy"]).values())[0]
    assert nm["noise_consistency"] in (None, 1.0)
    assert nm["self_correction_acc"] in (None, 1.0)


def test_text_understanding_weights():
    c = {"core_recall": 80.0, "extraction_numeric": 60.0, "faithfulness": 90.0, "trap_resistance": 70.0,
         "schema_valid": 100.0}
    assert text_understanding(c) == pytest.approx(0.3 * 80 + 0.2 * 60 + 0.25 * 90 + 0.15 * 70 + 0.1 * 100)
    rows = [{"atoms": 10, "fabricated_atoms": 1, "core_recall": 1.0}]
    comp = components(rows)
    assert comp["fabricated_detail_rate"] == pytest.approx(10.0) and comp["faithfulness"] == pytest.approx(80.0)
    rows = [{"atoms": 2, "fabricated_atoms": 2}]
    assert components(rows)["faithfulness"] == 0.0     # floored at 0


def test_readability_uses_validated_judge_only(sub):
    recs = _run(Perfect(sub), BASELINE_ID, sub, limit=10).records + _run(Perfect(sub), "cand-r", sub, limit=10).records
    off = headline_report(recs, pref={"cand-r": 60.0}, judge_validated=False, n_boot=10)
    on = headline_report(recs, pref={"cand-r": 60.0}, judge_validated=True, n_boot=10)
    c_off = next(e for e in off["entries"] if e["model_id"] == "cand-r")
    c_on = next(e for e in on["entries"] if e["model_id"] == "cand-r")
    assert c_off["readability_label"] == "structure only"
    assert c_on["readability_label"] == "judge †"
    assert c_on["readability"] == pytest.approx(0.35 * c_on["components"]["hygiene"] + 0.65 * 60.0)
