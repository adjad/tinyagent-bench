"""The Writing family end to end: runner, records, headline and vote items, with scripted stubs.

No model is loaded and nothing is sent anywhere. The stubs implement the model
adapter's `chat()` signature and answer with each case's own reference draft
(a "perfect" writer), with an empty string, or with fixed off-brief text.
"""
from __future__ import annotations

import pytest

from edat.quality import votes as V
from edat.quality.headline import BASE_ID, BASELINE_ID, headline_report, writing_score
from edat.quality.records import read_records
from edat.quality.runner import DEFAULT_SEEDS, Entry, HostedSplitError, run_writing, writing_vote_items
from edat.quality.splits import load_writing

DEVICE = {"id": "mac-test", "class": "mac-test", "ram_bytes": 16 * 2**30, "os": "macOS"}


@pytest.fixture(scope="module")
def cases():
    all_ = load_writing("dev")
    assert all_, "the Writing dev split is missing: python -m edat.quality.splits build-writing-dev"
    seen, out = set(), []
    for c in all_:
        if (c["sub"], c["kind"]) not in seen:
            seen.add((c["sub"], c["kind"]))
            out.append(c)
    return out


class _Res:
    def __init__(self, text):
        self.text, self.reasoning, self.finish_reason = text, "", "stop"
        self.usage = {"completion_tokens": max(1, len(text.split())), "reasoning_tokens": None}
        self.timing, self.request, self.meta = None, {}, {}


class Perfect:
    """Answers each turn with the case's reference text for that turn."""

    def __init__(self, cases):
        self.idx = {}
        for c in cases:
            refs = c["reference_turns"] or [c["reference_draft"]]
            for i, t in enumerate(c["turns"]):
                self.idx[t] = refs[i]
        self.calls = []

    def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
        self.calls.append({"n": len(messages), "seed": seed, "roles": [m["role"] for m in messages]})
        return _Res(self.idx[messages[-1]["content"]])


class Fixed:
    def __init__(self, text):
        self.text = text

    def chat(self, messages, **kw):
        return _Res(self.text)


def _run(client, model_id, cases, **kw):
    role = "baseline" if model_id == BASELINE_ID else "reference" if model_id == BASE_ID else "candidate"
    entry = Entry(model_id, role=role, device=DEVICE, weights_sha256="cd" * 32)
    return run_writing(client, entry, cases, split="dev", board_version="2027.1", harness_commit="abcdef1",
                       run_id=f"w-{model_id}", **kw)


def test_dev_split_covers_every_kind(cases):
    from edat.quality import writing as WR
    assert {(c["sub"], c["kind"]) for c in cases} == {(s, k) for s, k, _, _ in WR.PLAN}


def test_perfect_writer_passes_every_case(cases):
    sink = _run(Perfect(cases), BASELINE_ID, cases)
    assert {r["trial"] for r in sink.records} == {1, 2, 3}
    bad = [r["case_id"] for r in sink.records if not (r["status"] == "ok" and r["metrics"]["pass"])]
    assert not bad, bad[:5]
    assert all(r["metrics"]["score"] == pytest.approx(1.0) and r["metrics"]["gate_pass"] for r in sink.records)
    rep = headline_report(sink.records, n_boot=20)["writing"]
    e = rep["entries"][0]
    assert e["writing_label"] == "checks only"
    assert e["writing"] == pytest.approx(100.0)
    assert set(e["by_sub"]) == {"W1", "W2", "W3", "W4", "W5", "W6", "W7"}


def test_outline_cases_take_two_turns_with_the_model_s_own_outline(cases):
    outline = [c for c in cases if c["kind"] == "outline"]
    client = Perfect(outline)
    _run(client, "cand-o", outline)
    assert len(client.calls) == 2 * len(DEFAULT_SEEDS) * len(outline)
    assert client.calls[1]["roles"] == ["system", "user", "assistant", "user"]


@pytest.mark.parametrize("text", ["", "Thanks for the update, I will look at it next week."])
def test_empty_and_off_brief_drafts_score_zero(cases, text):
    sink = _run(Fixed(text), "cand-zero", cases)
    assert all(r["metrics"]["score"] == 0 for r in sink.records), \
        [r["case_id"] for r in sink.records if r["metrics"]["score"] != 0][:5]
    assert not any(r["metrics"]["pass"] for r in sink.records)


def test_records_validate_in_the_results_store(cases, tmp_path):
    sink = _run(Perfect(cases), "cand-w", cases[:6])
    sink.store = tmp_path
    run_dir = sink.write()
    recs = read_records(run_dir)
    assert len(recs) == len(sink.records) and all(r["suite"] == "writing" for r in recs)
    for r in recs:
        assert (run_dir / r["raw"]["path"]).exists()
        assert r["facets"]["family"] == "writing" and r["case_id"].startswith(("W1", "W2", "W3", "W4", "W5", "W6", "W7"))


def test_cloud_reference_refuses_heldout(cases):
    with pytest.raises(HostedSplitError):
        run_writing(Perfect(cases), Entry("anthropic/claude-haiku-5.5", kind="cloud_reference", seedless=True),
                    cases, split="heldout", board_version="2027.1", harness_commit="abcdef1")


def test_bad_writer_loses_against_both_references(cases):
    recs = (_run(Perfect(cases), BASELINE_ID, cases).records + _run(Perfect(cases), BASE_ID, cases).records
            + _run(Fixed("Sure! Here's a draft: [Name], see you soon."), "cand-bad", cases).records)
    rep = headline_report(recs, n_boot=200)["writing"]
    bad = next(e for e in rep["entries"] if e["model_id"] == "cand-bad")
    assert bad["deltas"]["tuned"]["writing_checks"]["label"] == "worse"
    assert bad["deltas"]["base"]["writing_checks"]["label"] == "worse"
    assert rep["tuning_effect"]["label"] == "approx_baseline"


def test_writing_score_weights():
    c = {"points": 80.0, "faithfulness": 90.0, "constraints": 70.0, "editing": 60.0}
    checks, label = writing_score(c)
    assert label == "checks only"
    assert checks == pytest.approx((0.25 * 80 + 0.20 * 90 + 0.15 * 70 + 0.10 * 60) / 0.70)
    judged, label = writing_score(c, 40.0, True)
    assert label == "judge †"
    assert judged == pytest.approx(0.25 * 80 + 0.20 * 90 + 0.15 * 70 + 0.10 * 60 + 0.30 * 40)


def test_vote_items_feed_the_writing_pairs(cases):
    good = _run(Perfect(cases), BASELINE_ID, cases)
    bad = _run(Fixed(""), "cand-empty", cases)
    items = writing_vote_items(good, cases) + writing_vote_items(bad, cases)
    assert items and {i["model_id"] for i in items} == {BASELINE_ID, "cand-empty"}
    assert all(i["gate_pass"] for i in items if i["model_id"] == BASELINE_ID)
    assert not any(i["gate_pass"] for i in items if i["model_id"] == "cand-empty")
    for it in items:                       # mark them held-out so the validation block can draw them
        it["split"] = "heldout"
    pairs, report = V.build_writing_pairs(items, seed=1, return_report=True)
    assert isinstance(report, dict)
    assert not any((p.get("case") or {}).get("sub") == "W4" for p in pairs)
    # the empty draft fails the gate, so every pair it is in is decided automatically, never shown
    assert all(p.get("auto") for p in pairs if "cand-empty" in (p.get("a", {}).get("model_id"),
                                                                 p.get("b", {}).get("model_id")))
