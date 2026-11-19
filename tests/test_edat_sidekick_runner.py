"""Runner, records, Restraint export and scoring (spec 12 §3.4, §4, S9, S10)."""
from __future__ import annotations

import json

import pytest

from edat.sidekick.gen.core import PUBLIC_MASTER_SEED, materialize
from edat.models import NullClient, ScriptedClient
from edat.sidekick.report import macro, summarize, type_rates, wilson
from edat.sidekick.restraint import validate_event
from edat.sidekick.runner import Entry, ReferenceClient, RunConfig, run, select_cases
from edat.sidekick.sandbox.run import SandboxUnavailable, SeatbeltIsolation


@pytest.fixture(scope="module")
def iso():
    i = SeatbeltIsolation()
    try:
        i.check()
    except SandboxUnavailable as exc:
        if "sandbox_apply" in str(exc) or "Operation not permitted" in str(exc):
            pytest.skip("nested Seatbelt sandbox")
        raise
    return i


def always(turn, n=5000):
    """A ScriptedClient that gives the same (callable) turn every time."""
    return ScriptedClient([turn] * n, name="always")


def stub(model_id, **kw):
    """An entry for a scripted client: stand-in device and weights, records marked sample."""
    return Entry(model={"id": model_id, "role": "candidate"}, sample=True, **kw)


def _lines(out, name):
    rows = []
    for d in out["run_dirs"].values():
        rows += [json.loads(l) for l in open(f"{d}/{name}") if l.strip()]
    return rows


def _cfg(tmp_path, **kw):
    base = dict(split="dev", set="core", trials=1, out_root=tmp_path, types=("shell", "git", "fix_bug", "commit_msg"))
    base.update(kw)
    return RunConfig(**base)


def test_reference_client_passes_and_records_validate(tmp_path, iso):
    cfg = _cfg(tmp_path)
    cases = select_cases(cfg)
    prompts = {}
    for c in cases:
        inst = materialize(c, 1, PUBLIC_MASTER_SEED)
        prompts[inst.prompt] = inst
    out = run(ReferenceClient(prompts), stub("oracle"), cfg, iso=iso, progress=None)
    recs = _lines(out, "records.jsonl")
    assert recs and all(r["metrics"]["pass"] for r in recs), [(r["case_id"], r["suite"]) for r in recs if not r["metrics"]["pass"]]
    assert {r["suite"] for r in recs} == {"answer_only", "tool_mode"}
    for r in recs:
        assert r["schema"] == "edat.result/1.0" and r["tier"] == "sidekick" and r["status"] == "ok"
        assert r["raw"]["path"].startswith("raw/") and "answer" not in r["metrics"]
        assert r["safety"] == {"unsafe_actions": 0, "privacy_leaks": 0, "labels": []}
        assert r["sample"] is True and r["device"]["id"] == "stub-host" and r["model"]["weights_fingerprint"]
    trials = _lines(out, "restraint-trials.jsonl")
    assert all(t["schema"] == "edat-restraint-trials/v1" for t in trials)
    assert sum(t["trials"] for t in trials) == len(recs)
    for d in out["run_dirs"].values():
        man = json.load(open(f"{d}/manifest.json"))
        assert man["schema"] == "edat.run-manifest/1.0" and man["suite"] in ("answer_only", "tool_mode")
        assert man["records"]["count"] == sum(1 for r in recs if r["suite"] == man["suite"])
        assert d.startswith(str(tmp_path / "runs"))       # the store's layout: <store>/runs/<run_id>/
        assert {a["name"] for a in man["attachments"]} >= {"sidekick-run-key.json", "restraint-events.jsonl",
                                                           "restraint-trials.jsonl", "sidekick-summary.json"}
    assert out["events"] == 0


def test_null_model_fails_everything(tmp_path, iso):
    cfg = _cfg(tmp_path, types=("shell", "regex", "explain_error"), modes=("answer_only",))
    out = run(NullClient(), stub("null"), cfg, iso=iso, progress=None)
    recs = _lines(out, "records.jsonl")
    assert recs and not any(r["metrics"]["pass"] for r in recs)


def test_destructive_answer_becomes_restraint_event(tmp_path, iso):
    cfg = _cfg(tmp_path, types=("git",), modes=("answer_only",), limit=12)
    client = always(lambda m, t, i: {"text": "```zsh\ngit reset --hard HEAD~1\ngit clean -fdx\n```"})
    out = run(client, stub("reckless"), cfg, iso=iso, progress=None)
    events = _lines(out, "restraint-events.jsonl")
    assert events
    for e in events:
        assert validate_event(e) == [] and e["spec"] == "12"
        assert e["detail"]["mode"] == "answer_only" and e["detail"]["executed"] is True
    recs = _lines(out, "records.jsonl")
    flagged = [r for r in recs if r["safety"]["labels"]]
    assert flagged and all(not r["metrics"]["pass"] for r in flagged)
    assert out["summary"]["SK-19_terminal_violation_rate"] > 0


def test_engine_errors_are_excluded(tmp_path, iso):
    cfg = _cfg(tmp_path, types=("commit_msg",), modes=("answer_only",))
    out = run(always({"engine_error": "server"}), stub("down"),
              cfg, iso=iso, progress=None)
    recs = _lines(out, "records.jsonl")
    assert recs and all(r["status"] == "excluded" for r in recs)
    assert out["summary"]["invalid_run"]


def test_heldout_needs_private_dir(tmp_path, monkeypatch, iso):
    monkeypatch.delenv("EDAT_PRIVATE_DIR", raising=False)
    cfg = _cfg(tmp_path, split="heldout", types=("commit_msg",), modes=("answer_only",))
    with pytest.raises(RuntimeError, match="EDAT_PRIVATE_DIR"):
        run(NullClient(), stub("x"), cfg, iso=iso, progress=None)


def test_local_run_needs_device_and_weights(tmp_path):
    cfg = _cfg(tmp_path, types=("commit_msg",), modes=("answer_only",))
    with pytest.raises(RuntimeError, match="device"):
        run(NullClient(), Entry(model={"id": "real", "role": "candidate"}), cfg, progress=None)


def test_cloud_reference_dev_only(tmp_path):
    cfg = _cfg(tmp_path, split="heldout")
    with pytest.raises(RuntimeError, match="development split"):
        run(NullClient(), Entry(model={"id": "c", "role": "cloud_reference"}, kind="cloud_reference"), cfg, progress=None)


# ---------------------------------------------------------------- scoring on synthetic records

def _rec(case, typ, trial, ok, suite="answer_only", level="L1", **metrics):
    return {"case_id": case, "trial": trial, "suite": suite, "status": "ok", "split": "dev",
            "facets": {"family": typ, "level": level, "variant": metrics.pop("variant", None)},
            "metrics": {"pass": ok, **metrics}, "safety": {"labels": metrics.pop("labels", [])}}


def test_sidekick_score_is_macro_over_nine_types():
    from edat.sidekick.gen.core import TASK_TYPES
    recs = []
    for i, t in enumerate(TASK_TYPES):
        for tr in (1, 2, 3):
            recs.append(_rec(f"{t}_a", t, tr, True))
            recs.append(_rec(f"{t}_b", t, tr, tr == 1 if i % 2 else False))
    s = summarize(recs)
    rates = type_rates(recs)
    assert s["SK-1_sidekick_score"] == pytest.approx(macro(rates))
    assert s["SK-3_reliability_pass3"] == pytest.approx(0.5)
    assert s["SK-4_consistency_gap"] == pytest.approx(s["SK-1_sidekick_score"] - 0.5)
    lo, hi = s["SK-1_bootstrap95"]
    assert lo <= s["SK-1_sidekick_score"] <= hi


def test_missing_type_gives_no_headline():
    s = summarize([_rec("a", "shell", 1, True)])
    assert s["SK-1_sidekick_score"] is None


def test_wilson():
    lo, hi = wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert wilson(0, 0) is None


def test_time_budget_and_violations():
    recs = [_rec("a", "shell", 1, True, time_to_answer_s=4.0), _rec("b", "shell", 1, False, time_to_answer_s=14.0),
            _rec("c", "git", 1, False, suite="tool_mode", time_to_answer_s=30.0, labels=["destructive"])]
    recs[2]["safety"]["labels"] = ["destructive"]
    s = summarize(recs)
    assert s["SK-25_time_to_answer"]["answer_only"]["within_budget"] == 0.5
    assert s["SK-25_time_to_answer"]["tool_mode"]["within_budget"] == 1.0
    assert s["SK-19_terminal_violation_rate"] == pytest.approx(1 / 3)
    assert s["SK-19_warning_badge"]
    assert s["SK-20_violations_by_kind"]["destructive"] == pytest.approx(1 / 3)
