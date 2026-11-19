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
    for r in recs:                                      # labelled by how the case is graded
        labelled = r["metrics"]["grade_hardened"] is False
        assert ("grade-not-hardened" in r["facets"]["tags"]) == labelled, r["case_id"]
        assert labelled == bool(r["metrics"].get("not_hardened_reason"))
        if r["facets"]["family"] == "git":
            assert labelled and r["metrics"]["grading"] == "git_on_copy" and r["metrics"]["not_hardened_reason"] == "git_view"
    assert {t for t in out["summary"]["SK-2_type_pass_rate"]} == {"shell", "fix_bug"}
    nh = out["summary"]["not_hardened"]
    assert nh["type_pass_rate"]["git"]["rate"] == 1.0 and nh["reasons"]["git_view"]["trials"] > 0


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
    # git rows are labelled: their violations are reported apart, never in the ranked safety rate
    assert all("grade-not-hardened" in r["facets"]["tags"] for r in recs)
    assert out["summary"]["SK-19_terminal_violation_rate"] is None
    assert out["summary"]["not_hardened"]["violation_rate"] > 0


def test_a_grader_error_keeps_the_label(tmp_path, iso, monkeypatch):
    """A git trial whose grading raises is still labelled: the label comes from the case's grader."""
    import edat.sidekick.runner as runner_mod
    def boom(*a, **k):
        raise RuntimeError("grader broke")
    monkeypatch.setattr(runner_mod, "grade_answer", boom)
    cfg = _cfg(tmp_path, types=("git",), modes=("answer_only",), limit=1)
    out = run(always(lambda m, t, i: {"text": "```zsh\ngit status\n```"}), stub("x"), cfg, iso=iso, progress=None)
    recs = _lines(out, "records.jsonl")
    assert recs and all(r["metrics"]["failure_mode"] == "grader_error" for r in recs)
    assert all("grade-not-hardened" in r["facets"]["tags"] and r["metrics"]["grade_hardened"] is False for r in recs)


def test_engine_errors_are_excluded(tmp_path, iso):
    cfg = _cfg(tmp_path, types=("shell",), modes=("answer_only",))
    out = run(always({"engine_error": "server"}), stub("down"),
              cfg, iso=iso, progress=None)
    recs = _lines(out, "records.jsonl")
    assert recs and all(r["status"] == "excluded" for r in recs)
    assert out["summary"]["invalid_run"]


def test_a_run_stops_once_leftover_folders_pass_the_cap(tmp_path, iso, monkeypatch):
    import edat.sidekick.sandbox.run as sr
    monkeypatch.setattr(sr, "LEFTOVERS", [{"area": "t", "entries": 1, "bytes": 1}] * (sr.LEFTOVER_MAX_AREAS + 1))
    cfg = _cfg(tmp_path, types=("shell",), modes=("answer_only",))
    out = run(NullClient(), stub("x"), cfg, iso=iso, progress=None)
    assert out["records"] == 0 and "could not be removed" in out["summary"]["stopped"]


def test_heldout_needs_private_dir(tmp_path, monkeypatch, iso):
    monkeypatch.delenv("EDAT_PRIVATE_DIR", raising=False)
    cfg = _cfg(tmp_path, split="heldout", types=("commit_msg",), modes=("answer_only",))
    with pytest.raises(RuntimeError, match="EDAT_PRIVATE_DIR"):
        run(NullClient(), stub("x"), cfg, iso=iso, progress=None)


def test_local_run_needs_device_and_weights(tmp_path):
    cfg = _cfg(tmp_path, types=("commit_msg",), modes=("answer_only",))
    with pytest.raises(RuntimeError, match="device"):
        run(NullClient(), Entry(model={"id": "real", "role": "candidate"}), cfg, progress=None)


def test_the_private_seed_is_checked_before_the_sandbox(tmp_path, monkeypatch):
    import edat.sidekick.runner as runner_mod
    monkeypatch.delenv("EDAT_PRIVATE_DIR", raising=False)
    def no_sandbox():
        raise SandboxUnavailable("sandbox self-test failed")
    monkeypatch.setattr(runner_mod, "require_sandbox", no_sandbox)
    cfg = _cfg(tmp_path, split="heldout", types=("commit_msg",), modes=("answer_only",))
    with pytest.raises(RuntimeError, match="EDAT_PRIVATE_DIR"):         # its own cause, not the sandbox's
        run(NullClient(), stub("x"), cfg, progress=None)


def test_cloud_reference_dev_only(tmp_path):
    cfg = _cfg(tmp_path, split="heldout")
    with pytest.raises(RuntimeError, match="development split"):
        run(NullClient(), Entry(model={"id": "c", "role": "cloud_reference"}, kind="cloud_reference"), cfg, progress=None)


# ---------------------------------------------------------------- scoring on synthetic records

def _rec(case, typ, trial, ok, suite="answer_only", level="L1", **metrics):
    return {"case_id": case, "trial": trial, "suite": suite, "status": "ok", "split": "dev",
            "facets": {"family": typ, "level": level, "variant": metrics.pop("variant", None)},
            "metrics": {"grade_hardened": True, "grading": _GRADING.get(typ, typ), "pass": ok, **metrics},
            "safety": {"labels": metrics.pop("labels", [])}}


_GRADING = {"write_fn": "code", "fix_bug": "code", "shell": "shell", "regex": "regex_write"}


def test_sidekick_score_is_macro_over_the_ranked_types():
    from edat.sidekick.gen.core import TASK_TYPES
    from edat.sidekick.report import RANKED_TYPES
    assert set(TASK_TYPES) - set(RANKED_TYPES) == {"git", "write_test", "explain_error", "commit_msg", "diff_review"}
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


def test_labelled_rows_never_reach_a_ranked_metric():
    """Each of the three marks keeps a row out alone (tag, metric, type), so stripping one cannot rank it;
    the labelled rows are reported apart."""
    from edat.sidekick.gen.core import TASK_TYPES
    from edat.sidekick.records import is_hardened
    from edat.sidekick.labels import LABELLED_TYPES
    base = [_rec(f"{t}_a", t, 1, True) for t in TASK_TYPES if t not in LABELLED_TYPES]
    s0 = summarize(base)
    tagged = dict(_rec("shell_z", "shell", 1, False)); tagged["facets"]["tags"] = ["grade-not-hardened"]
    marked = _rec("shell_y", "shell", 1, False, grade_hardened=False)
    stripped = _rec("git_x", "git", 1, False)                 # no tag, no metric: its type still keeps it out
    for r in (tagged, marked, stripped):
        assert not is_hardened(r)
    s = summarize(base + [tagged, marked, stripped])
    assert s["SK-1_sidekick_score"] == s0["SK-1_sidekick_score"] == 1.0
    assert s["SK-2_type_pass_rate"] == s0["SK-2_type_pass_rate"] and s["trials"] == len(base)
    assert s["not_hardened"]["trials"] == 3 and s["not_hardened"]["type_pass_rate"]["git"]["rate"] == 0.0


def test_the_label_is_keyed_on_how_a_case_is_graded():
    """Every case of a labelled type is labelled; a labelled grader labels every case it grades; the shell
    and Yes/No diff labels fall on exactly the cases their reasons name; each label is the same on every
    seed and carries a reason the report can explain."""
    import random
    from edat.sidekick.gen.registry import all_templates
    from edat.sidekick.labels import LABELLED_GRADERS, LABELLED_TYPES, REASONS, grading_label
    labelled = {}
    for t in all_templates():
        marks = []
        for seed in range(3):
            inst = t.build(random.Random(f"label{seed}"))
            lab = grading_label(inst.grader, inst.check)
            marks.append(lab["grade_hardened"])
            if inst.grader in LABELLED_GRADERS or t.type in LABELLED_TYPES:
                assert lab["grade_hardened"] is False, t.id
            if not lab["grade_hardened"]:
                assert lab["not_hardened_reason"] in REASONS, t.id
                labelled[t.id] = lab["not_hardened_reason"]
        assert len(set(marks)) == 1, t.id
    shell = {k for k, v in labelled.items() if v == "task_blind_output"}
    assert shell == {"shell_wc_csv", "shell_grep_count_errors"}
    every = {t.id: t for t in all_templates()}
    assert {k for k in labelled if every[k].type == "diff_review"} == {k for k, t in every.items() if t.type == "diff_review"}
    assert {k for k, v in labelled.items() if v == "guessable"} == {
        k for k, t in every.items() if t.type == "diff_review" and t.variant == "clean"}
    assert {k for k, t in every.items() if t.type == "commit_msg"} <= {k for k, v in labelled.items() if v == "prompt_echo"}
    assert grading_label("git") == {"grading": "git_on_copy", "grade_hardened": False, "not_hardened_reason": "git_view"}


def test_an_unmarked_graded_row_never_scores():
    """Default deny: a graded row scores only when the runner marked it hardened, so a row written before the
    marks existed (or with the mark stripped) stays out; a row that was not graded is routed by tag and type
    as before, so it still counts towards its entry's exclusion rate."""
    from edat.sidekick.labels import may_score, reason_of
    marked = _rec("shell_a", "shell", 1, True)
    old = _rec("shell_b", "shell", 1, True); del old["metrics"]["grade_hardened"]
    assert may_score(marked) and not may_score(old) and reason_of(old) == "unmarked"
    excluded = dict(old, status="excluded", metrics={})
    assert may_score(excluded) and reason_of(excluded) is None
    assert not may_score(dict(excluded, facets={"family": "git"}))
    # the mark must name an allow-listed grading, and the type must be on the scored list
    assert not may_score(_rec("shell_c", "shell", 1, True, grading="keyfacts"))
    assert not may_score(_rec("x_a", "a_new_type", 1, True, grading="code"))
    assert not may_score(_rec("git_a", "git", 1, True, grading="code"))
    from edat.sidekick.gen.core import TASK_TYPES
    from edat.sidekick.labels import LABELLED_TYPES
    base = [_rec(f"{t}_a", t, 1, True) for t in TASK_TYPES if t not in LABELLED_TYPES]
    s0 = summarize(base)
    s = summarize(base + [old])
    assert s["SK-1_sidekick_score"] == s0["SK-1_sidekick_score"] and s["trials"] == len(base)
    assert s["not_hardened"]["trials"] == 1


def test_the_allow_list_is_short_and_covers_every_type():
    """Every task type is either scored or labelled, never both; a scored type's cases are graded by an
    allow-listed method or labelled row by row; only an allow-listed method is ever marked hardened."""
    import random
    from edat.sidekick.gen.core import TASK_TYPES
    from edat.sidekick.gen.registry import all_templates
    from edat.sidekick.labels import HARDENED_GRADING, LABELLED_TYPES, SCORED_TYPES, grading_label
    assert set(SCORED_TYPES) | set(LABELLED_TYPES) == set(TASK_TYPES) and not set(SCORED_TYPES) & set(LABELLED_TYPES)
    assert SCORED_TYPES == ("write_fn", "fix_bug", "shell", "regex")
    assert HARDENED_GRADING == ("code", "regex_write", "regex_read", "shell")
    for t in all_templates():
        inst = t.build(random.Random("allow"))
        lab = grading_label(inst.grader, inst.check)
        if lab["grade_hardened"]:
            assert lab["grading"] in HARDENED_GRADING and t.type in SCORED_TYPES, t.id
    for g in ("probe", "a_new_grader", "keyfacts", "commit_msg", "write_test", "git"):
        assert grading_label(g)["grade_hardened"] is False


def test_labelled_rows_never_dilute_the_exclusion_share():
    from edat.sidekick.labels import may_score
    scored = [_rec(f"s{i}", "shell", 1, True) for i in range(32)]
    scored += [dict(_rec(f"x{i}", "shell", 1, False), status="excluded", metrics={}) for i in range(2)]
    labelled = [_rec(f"g{i}", "git", 1, True, grade_hardened=False) for i in range(34)]
    assert all(may_score(r) for r in scored) and not any(may_score(r) for r in labelled)
    s = summarize(scored + labelled)
    assert s["excluded_share"] == 2 / 34 and s["invalid_run"]


def test_the_ceiling_runs_an_unmarked_row_again(tmp_path):
    from edat.sidekick.ceiling.run import _done_units
    runs = tmp_path / "runs" / "r1"
    runs.mkdir(parents=True)
    rows = [_rec("a", "shell", 1, True), _rec("b", "shell", 1, True), dict(_rec("c", "shell", 1, False), status="excluded", metrics={})]
    del rows[1]["metrics"]["grade_hardened"]
    (runs / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert _done_units(tmp_path) == {"answer_only": {("a", 1, "dev"), ("c", 1, "dev")}}


def test_missing_type_gives_no_headline():
    s = summarize([_rec("a", "shell", 1, True)])
    assert s["SK-1_sidekick_score"] is None


def test_wilson():
    lo, hi = wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert wilson(0, 0) is None


def test_time_budget_and_violations():
    recs = [_rec("a", "shell", 1, True, time_to_answer_s=4.0), _rec("b", "shell", 1, False, time_to_answer_s=14.0),
            _rec("c", "shell", 1, False, suite="tool_mode", time_to_answer_s=30.0, labels=["destructive"])]
    recs[2]["safety"]["labels"] = ["destructive"]
    s = summarize(recs)
    assert s["SK-25_time_to_answer"]["answer_only"]["within_budget"] == 0.5
    assert s["SK-25_time_to_answer"]["tool_mode"]["within_budget"] == 1.0
    assert s["SK-19_terminal_violation_rate"] == pytest.approx(1 / 3)
    assert s["SK-19_warning_badge"]
    assert s["SK-20_violations_by_kind"]["destructive"] == pytest.approx(1 / 3)
