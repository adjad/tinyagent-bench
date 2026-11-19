"""The routine-run tool (edat/cases/agentic_run.py), with stub clients only.

No engine, no model, no isolate(): the run is given NullClient for both loops,
the run guard's fake engine (tests/test_edat_runtime_run_guard.py), fake host
checks, and neutral-lane cases (which Harbor's loop records as not applicable
without a model call).
"""
import json
import os

import pytest

from edat.cases import agentic_run as ar
from edat.models import EngineError, NullClient
from edat.runtime import run_guard as rg
from tests.test_edat_runtime_run_guard import URL, FakeEngine

SHA = "0" * 64


def neutral_cases(n=2):
    from edat.cases.loader import load_split
    from edat.cases.variants import single_turn
    cs = [c for c in load_split("dev", lanes=["neutral"], families=["select"]) if single_turn(c)]
    return sorted(cs, key=lambda c: c["id"])[:n]


TUNED = "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp"
BASE = "gemma-4-E4B-it-oQ4e-mtp"


class Faulting(NullClient):
    def chat(self, *a, **k):
        raise EngineError("server", "boom", retryable=False)


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    cases = neutral_cases()
    monkeypatch.setattr(ar, "select", lambda plan, splits=ar.SPLITS: list(cases))
    fake = FakeEngine(models=(TUNED, BASE))
    out = tmp_path / "out"

    def go(**kw):
        args = dict(plan="smoke", board_version="test-board", lock=str(tmp_path / "lock"), isolate=False,
                    clients=lambda m, loop: NullClient(),
                    engine=rg.Engine(URL, transport=fake.transport, sleep=lambda s: None),
                    weights=lambda m: SHA, device_id="test-mac", progress=None,
                    free_pct=lambda: 80, harbor=lambda: None,
                    column=lambda c: True)    # neutral cases through Harbor's loop: recorded not_applicable
        args.update(kw)
        return ar.run(out, **args)
    return go, out, fake, cases


def records(out, plan="smoke"):
    rows = []
    for f in (out / plan / "store").glob("runs/*/records.jsonl"):
        rows += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return rows


def test_references_tuned_first_shipped_thinking_and_author_sampling():
    refs = ar.references()
    assert [m.engine_id for m in refs] == ["gemma-4-E4B-it-r5-step-01800-oQ4e-mtp", "gemma-4-E4B-it-oQ4e-mtp"]
    assert [m.role for m in refs] == ["baseline", "reference"]
    assert all(m.thinking is False for m in refs)
    s = ar.sampling_for(refs[0])
    assert s.to_record() == {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "source": "author_recommended"}


def test_run_writes_both_suites_with_selection_and_restores_the_engine(stubbed):
    go, out, fake, cases = stubbed
    rep = go(trials=2)
    rows = records(out)
    assert len(rows) == len(cases) * 2 * 2
    assert {r["suite"] for r in rows} == {"reference-loop", "harbor-loop"}
    assert {r["status"] for r in rows if r["suite"] == "harbor-loop"} == {"not_applicable"}
    r = next(r for r in rows if r["suite"] == "reference-loop")
    assert r["model"] == {"id": "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp", "role": "baseline",
                          "weights_sha256": SHA, "headline_entry": True}
    assert r["settings"]["thinking"] == "off"
    assert r["settings"]["sampling"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
    assert r["settings"]["sampling_source"] == "author_recommended"
    assert {(x["trial"], x["settings"]["world_seed"], x["settings"]["seed"]) for x in rows} == {(1, 1, 1001), (2, 2, 1002)}
    sel = json.loads((out / "smoke" / "selection.json").read_text())
    assert r["selection"]["manifest_sha256"] == sel["manifest_sha256"] and r["selection"]["tier"] == "smoke"
    assert f"POST /v1/models/{TUNED}/load" in fake.calls
    assert rep["engine"].startswith("engine restored") and rep["counts"]["ran"] == len(rows)
    assert rep["outcome"] == "finished" and fake.loaded() == ["user-model", "user-pinned"]
    assert not (out.parent / "lock").exists()
    timing = [json.loads(x) for x in (out / "smoke" / "timing.jsonl").read_text().splitlines()]
    assert len(timing) == len(rows) and all(t["turns"] == 1 for t in timing)


def test_resume_skips_recorded_trials_and_damaged_lines(stubbed):
    go, out, fake, cases = stubbed
    go(trials=1)
    n1 = len(records(out))
    rep = go(trials=2)
    assert len(records(out)) == 2 * n1
    assert rep["counts"]["skipped_done"] == n1 and rep["counts"]["ran"] == n1
    rep = go(trials=2)                # nothing left: no model is loaded
    assert rep["counts"]["ran"] == 0 and fake.calls.count(f"POST /v1/models/{TUNED}/load") == 2
    f = next((out / "smoke" / "store").glob("runs/*/records.jsonl"))
    lines = f.read_text().splitlines()
    f.write_text("\n".join(lines[:-1] + [lines[-1][:40]]) + "\n")     # killed mid-write
    rep = go(trials=2)
    assert rep["counts"]["damaged_record_lines"] == 1 and rep["counts"]["ran"] == 1


def test_stop_file_time_limit_memory_and_harbor_stop_between_trials(stubbed, tmp_path):
    go, out, fake, cases = stubbed
    (out / "smoke").mkdir(parents=True)
    (out / "smoke" / "STOP").write_text("")
    rep = go()
    assert rep["stopped"] == "the STOP file exists" and rep["outcome"] == "stopped" and records(out) == []
    assert fake.loaded() == ["user-model", "user-pinned"]
    (out / "smoke" / "STOP").unlink()
    assert "minute limit" in go(max_minutes=1e-9)["stopped"]
    rep = go(free_pct=lambda: 9)
    assert "free memory" in rep["stopped"] and rep["outcome"] == "failed"
    opened = []

    def client(m, loop):              # Harbor opens once the first trial has run
        opened.append(1)
        return NullClient()
    with pytest.raises(rg.GuardError, match="not restoring"):                # restore refused, re-raised
        go(clients=client, harbor=lambda: "process matching x" if opened else None, trials=3)  # 12 trials
    rep = json.loads((out / "smoke" / "run-report.json").read_text())        # ...after the report
    assert "Harbor" in rep["stopped"] and rep["outcome"] == "failed"
    assert rep["engine"].startswith("restore FAILED") and "run_guard restore" in rep["restore_with"]
    assert TUNED in fake.loaded() and not (tmp_path / "lock").exists()


def test_failure_restores_the_engine_and_releases_the_lock(stubbed, tmp_path):
    go, out, fake, cases = stubbed

    def boom(m, loop):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        go(clients=boom)
    assert fake.loaded() == ["user-model", "user-pinned"]
    assert not (tmp_path / "lock").exists()
    rep = json.loads((out / "smoke" / "run-report.json").read_text())
    assert rep["failed"].startswith("KeyboardInterrupt") and rep["engine"].startswith("engine restored")


def test_a_smoke_of_only_engine_faults_fails(stubbed):
    """The reviewer's case: every trial an engine fault reported 'finished' and exited 0."""
    go, out, fake, cases = stubbed
    rep = go(clients=lambda m, loop: Faulting(), column=lambda c: False)
    assert {r["status"] for r in records(out)} == {"excluded"}
    assert rep["outcome"] == "failed" and rep["exit_code"] == 1 and "every model trial" in rep["why"]


def test_results_folder_rules(stubbed, tmp_path, monkeypatch):
    go, out, fake, cases = stubbed
    with pytest.raises(rg.GuardError, match="inside the repository"):
        ar.run(ar.REPO / "results-here", plan="smoke", board_version="b", clients=lambda m, l: None,
               lock=str(tmp_path / "l"))
    assert not (ar.REPO / "results-here").exists()
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(tmp_path / "private"))
    with pytest.raises(rg.GuardError, match="EDAT_PRIVATE_DIR"):
        ar.run(tmp_path / "elsewhere", plan="routine", board_version="b", clients=lambda m, l: None)
    with pytest.raises(rg.GuardError, match="Harbor"):
        go(harbor=lambda: "process matching x")
    with pytest.raises(rg.GuardError, match="lock path"):
        go(lock="")
    with pytest.raises(rg.GuardError, match="loopback"):
        go(base_url="http://192.168.1.2:8000")
    assert not (tmp_path / "out").exists() and fake.calls == []


def test_harbor_column_excludes_neutral_lane_and_variants():
    neutral = neutral_cases(1)[0]
    col = {"id": "a1", "lane": "assistant", "family": "select", "tags": ["core"]}
    assert not ar.harbor_column(neutral)
    assert ar.harbor_column(col) and not ar.harbor_column({**col, "variant_of": "a0"})
    us = ar.units([neutral, col], ar.references()[:1], 1)
    assert [(u.suite, u.case_id) for u in us] == [("reference-loop", neutral["id"]), ("reference-loop", "a1"),
                                                  ("harbor-loop", "a1")]


def test_manifest_and_selection_field_are_stable():
    cases = neutral_cases(3)
    a, b = ar.manifest(cases, "routine"), ar.manifest(list(reversed(cases)), "routine")
    assert a["manifest_sha256"] == b["manifest_sha256"] and a["tier"] == "core"
    sel = ar.selection_field(a, cases[0])
    assert sel["n_items"] == 3 and sel["ci95_half_width_pts"] == pytest.approx(56.6, abs=0.1)


def test_interleave_rotates_families_within_each_split():
    cs = [{"id": f"{f}{i}", "family": f, "lane": "neutral", "split": s}
          for s in ("dev", "heldout") for f in ("a", "b") for i in range(2)]
    order = [(c["split"], c["id"]) for c in ar.interleave(cs)]
    assert order == [("dev", "a0"), ("dev", "b0"), ("dev", "a1"), ("dev", "b1"),
                     ("heldout", "a0"), ("heldout", "b0"), ("heldout", "a1"), ("heldout", "b1")]


def test_estimate_assumed_measured_and_resume(tmp_path):
    cases = neutral_cases(2)
    models = ar.references()
    est = ar.estimate(cases, models, 3, harbor_applicable=lambda c: False, column=lambda c: True)
    tuned = est["models"][models[0].engine_id]
    assert tuned["reference-loop"]["case_trials"] == 6 and tuned["reference-loop"]["hours"] == round(60 / 3600, 2)
    assert tuned["harbor-loop"]["case_trials"] == 0
    (tmp_path / "timing.jsonl").write_text("\n".join(json.dumps(t) for t in [
        {"suite": "reference-loop", "status": "ok", "seconds": 4.0, "turns": 1},
        {"suite": "reference-loop", "status": "ok", "seconds": 8.0, "turns": 2},
        {"suite": "harbor-loop", "status": "not_applicable", "seconds": 0.01, "turns": 1}]))
    rates = ar.measured_rates(tmp_path)
    assert rates == {"reference-loop": 4.0}
    est = ar.estimate(cases, models[:1], 3, rates=rates, harbor_applicable=lambda c: True, column=lambda c: True)
    assert est["models"][models[0].engine_id]["by_split_hours"] == {"dev": round((6 * 4 + 6 * 10) / 3600, 2)}
    assert est["rates_source"] == "measured" and est["seconds_per_turn"]["harbor-loop"] == 10.0
    done = {(models[0].engine_id, "reference-loop", "dev", cases[0]["id"], 1)}
    est2 = ar.estimate(cases, models[:1], 3, rates=rates, harbor_applicable=lambda c: True, done=done,
                       column=lambda c: True)
    assert est2["models"][models[0].engine_id]["reference-loop"]["case_trials"] == 5


def test_trial_record_carries_selection():
    from edat.cases.runner import run_trial
    from edat.grading.records import trial_record
    r = run_trial(neutral_cases(1)[0], NullClient())
    sel = {"tier": "core", "manifest_sha256": SHA, "n_items": 4, "ci95_half_width_pts": 49.0}
    rec = trial_record(r, run_id="x", board_version="b", harness_commit="c", model={"id": "m", "role": "candidate"},
                       runtime={"name": "stub"}, device=None, selection=sel)
    assert rec["selection"] == sel
    assert "selection" not in trial_record(r, run_id="x", board_version="b", harness_commit="c",
                                           model={"id": "m", "role": "candidate"}, runtime={"name": "stub"},
                                           device=None)


def test_omlx_client_sends_the_key_only_when_given():
    from tests.test_edat_models_adapter import FakeTransport, body
    from edat.models import OmlxClient
    from edat.models.openai_compat import HttpResponse
    for key, want in ((None, None), ("k-test", "Bearer k-test")):
        t = FakeTransport([HttpResponse(200, body("hi"))])
        c = OmlxClient("m", stream=False, transport=t, api_key=key)
        c.chat([{"role": "user", "content": "hi"}], max_tokens=8, thinking=False)
        assert t.sent[0]["headers"].get("Authorization") == want
        assert "k-test" not in repr(c) and "k-test" not in json.dumps(c.describe())


def test_preflight_rows_without_engine(tmp_path, monkeypatch):
    monkeypatch.delenv("EDAT_PRIVATE_DIR", raising=False)
    import edat.sidekick.ceiling.models as cm
    import edat.sidekick.ceiling.run as cr
    monkeypatch.setattr(cm, "model_dir_from_settings", lambda: None)     # the host's model folder is not read
    monkeypatch.setattr(cr, "engine_context", lambda eid: (None, "not read"))
    rows = {name: ok for name, ok, _ in ar.preflight(tmp_path / "out", lock=str(tmp_path / "lock"),
                                                        harbor=lambda: None, free_pct=lambda: 50)}
    assert rows["results folder outside the repository"] is True
    assert rows["results folder under $EDAT_PRIVATE_DIR (held-out records)"] is False
    assert rows["held-out cases present"] is False
    assert rows["heavy-job lock free"] is True
    assert not any(k.startswith("engine") for k in rows)


def test_trials_per_split_and_the_three_sizes():
    cs = [{"id": "d1", "lane": "neutral", "family": "select", "split": "dev"},
          {"id": "h1", "lane": "neutral", "family": "select", "split": "heldout"}]
    m = ar.references()[:1]
    us = ar.units(cs, m, {"dev": 3, "heldout": 1}, column=lambda c: False)
    assert sorted((u.split, u.trial) for u in us) == [("dev", 1), ("dev", 2), ("dev", 3), ("heldout", 1)]
    sc = ar.scenarios()
    assert sc["a_both_splits"]["trials"] == {"dev": 3, "heldout": 3}
    assert sc["b_dev3_heldout1"]["trials"] == {"dev": 3, "heldout": 1}
    assert sc["c_dev_only"]["splits"] == ("dev",)
    assert [(u.split, u.trial) for u in ar.units(cs, m, sc["c_dev_only"]["trials"], column=lambda c: False)] == \
        [("dev", 1), ("dev", 2), ("dev", 3)]


def test_long_sessions_are_timed_and_estimated_apart(tmp_path):
    (tmp_path / "timing.jsonl").write_text("\n".join(json.dumps(t) for t in [
        {"suite": "reference-loop", "status": "ok", "seconds": 3.0, "turns": 1, "family": "select"},
        {"suite": "reference-loop", "status": "ok", "seconds": 300.0, "turns": 60, "family": "long_session"}]))
    rates = ar.measured_rates(tmp_path)
    assert rates == {"reference-loop": 3.0, "reference-loop:long_session": 5.0}
    assert ar.rate_for(rates, "reference-loop", "long_session") == 5.0
    assert ar.rate_for(rates, "reference-loop", "select") == 3.0
    assert ar.rate_for({}, "harbor-loop", "select") == ar.ASSUMED_S_PER_TURN


def test_smoke_long_is_one_assistant_long_session():
    (c,) = ar.smoke_long_cases()
    assert c["family"] == "long_session" and c["lane"] == "assistant" and ar.user_turns(c) > 10
    assert not ar.harbor_column(c)          # reference loop only


def test_timing_rows_carry_the_family(stubbed):
    go, out, prep, cases = stubbed
    go()
    rows = [json.loads(x) for x in (out / "smoke" / "timing.jsonl").read_text().splitlines()]
    assert {r["family"] for r in rows} == {"select"}
