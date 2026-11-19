"""The Harbor-loop column: assistant-lane cases through Harbor's real Ask turn.

No model and no network: Toolbench's ScriptedClient plays each case's oracle
in place of oMLX, every socket connect fails the test, and Harbor's home is a
throwaway directory. Run on its own: `python -m pytest tests/test_edat_agentic_harbor_loop.py -q`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if "service" not in sys.modules and not os.environ.get("HARBOR_HOME"):
    os.environ["HARBOR_HOME"] = str(Path(tempfile.mkdtemp(prefix="edat-harbor-test-")) / ".moe")

MODEL = "sample-harbor-model"


@pytest.fixture(scope="module")
def harbor():
    import socket

    from service import config

    from edat.cases.harbor_loop import HarborLoop, open_session
    attempts = []
    real = (socket.socket.connect, socket.socket.connect_ex)

    def refuse(sock, address):
        attempts.append(address)
        raise PermissionError(f"test: network to {address!r} is blocked")
    socket.socket.connect = socket.socket.connect_ex = refuse
    saved = config.OMLX_MODEL_SETTINGS
    ms = Path(tempfile.mkdtemp()) / "model_settings.json"
    ms.write_text(json.dumps({"models": {MODEL: {"max_context_window": 16384}}}))
    config.OMLX_MODEL_SETTINGS = ms
    try:
        hm = open_session(MODEL, scripted=True)
        yield {"model": hm, "loop": HarborLoop(turn_timeout_s=60), "net": attempts}
    finally:
        from scripts.toolbench import runner
        runner._restore_aliases()
        config.OMLX_MODEL_SETTINGS = saved
        socket.socket.connect, socket.socket.connect_ex = real
    assert attempts == [], f"network was attempted: {attempts}"


def _case(case_id: str) -> dict:
    from edat.cases.loader import load_split
    return next(c for c in load_split("dev") if c["id"] == case_id)


def _oracle_plan(case: dict, world_seed: int = 1) -> list[dict]:
    from edat.cases.bind import bind
    from edat.cases.oracle import oracle_turns
    return [{"calls": [(x["name"], x.get("arguments") or {}) for x in st["calls"]]} if st.get("calls")
            else {"text": st.get("text", "")} for st in oracle_turns(bind(case, world_seed))]


def _run(harbor, case: dict, plan: list[dict] | None = None, **kw):
    from edat.cases.runner import run_trial
    hm = harbor["model"]
    hm.client.plan = list(_oracle_plan(case) if plan is None else plan)
    hm.client.failure = ""
    return run_trial(case, hm, trial=1, loop=harbor["loop"], **kw)


# ------------------------------------------------------------------ pure parts

def test_split_messages_builds_toolbench_history():
    from edat.cases.harbor_loop import split_messages
    msgs = [{"role": "system", "content": "ignored"},
            {"role": "user", "content": "Find Ana's number"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "lookup_contact", "arguments": '{"name": "Ana"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "lookup_contact", "content": "Ana: +1 555 0199"},
            {"role": "assistant", "content": "It's +1 555 0199."},
            {"role": "user", "content": "Text her that I'm late"}]
    prompt, history = split_messages(msgs)
    assert prompt == "Text her that I'm late"
    assert history == [{"user": "Find Ana's number", "assistant": "It's +1 555 0199.", "tools": "lookup_contact",
                        "calls": [{"name": "lookup_contact", "args": {"name": "Ana"}, "result": "Ana: +1 555 0199"}]}]
    with pytest.raises(ValueError):
        split_messages([{"role": "user", "content": [{"type": "text", "text": "x"}]}])


def test_harbor_model_cannot_be_used_by_the_reference_loop():
    from edat.cases.harbor_loop import HarborModel
    from edat.models.base import ConfigurationError
    with pytest.raises(ConfigurationError):
        HarborModel("m", client=None).chat([], max_tokens=1, thinking=False)


def test_suite_and_gap_aggregate():
    from edat.grading.aggregate import harbor_gap
    from edat.grading.records import suite_for
    assert suite_for("edat-reference-v1") == "reference-loop" and suite_for("harbor-ask-v1") == "harbor-loop"
    row = lambda cid, ok, fam="select", **kw: {"case_id": cid, "trial": 1, "lane": "assistant", "family": fam,
                                               "tags": ["core"], "success": ok, "excluded": False, **kw}
    ref = [row("a", False), row("b", True), row("c", True), row("d", True, "abstain"), row("e", True, "abstain")]
    har = [row("a", True), row("b", True), row("c", None, excluded=True, not_applicable="host"),
           row("d", False, "abstain"), row("e", True, "abstain")]
    g = harbor_gap(har, ref, resamples=500)
    assert g["pairs"] == 4 and g["not_applicable"] == 1
    assert g["families"]["select"]["delta"] == 0.5 and g["families"]["abstain"]["delta"] == -0.5
    assert g["repair_gain"]["rg"] == 0.0 and g["repair_gain"]["families"] == 2   # equal family weights


# ------------------------------------------------------------------ Harbor's real turn, scripted model

def test_not_applicable_cases_never_reach_the_host(harbor):
    from edat.cases.loader import load_split
    cases = load_split("dev")
    neutral = next(c for c in cases if c["lane"] == "neutral")
    prefs = next(c for c in cases if c["lane"] == "assistant" and c.get("stored_preferences"))
    for c in (neutral, prefs):
        r = _run(harbor, c, plan=[])
        assert r.verdict.excluded and r.verdict.not_applicable and r.verdict.success is False
        assert r.run.turns[0].trace.steps == []


def test_oracle_passes_through_harbors_turn_and_records_pair(harbor, tmp_path):
    from edat.grading.records import RunWriter
    case = _case("sel_ast_cal_update_002")
    r = _run(harbor, case)
    assert r.loop == "harbor-ask-v1"
    assert r.verdict.success, r.verdict.reasons
    calls = r.run.turns[0].trace.calls
    assert calls and all(c.executed for c in calls if c.origin == "model" and c.outcome != "schema_loaded")
    with RunWriter(tmp_path, "2026-11-18T10-00-00Z-ef56") as w:
        rec = w.append(r, board_version="sample", harness_commit="0000000",
                       model={"id": MODEL, "role": "candidate", "weights_fingerprint": "0" * 64},
                       runtime={"name": "harbor-scripted"},
                       device={"id": "sample-mac", "class": "mac", "ram_bytes": 0, "os": "sample"}, sample=True)
    assert rec["suite"] == "harbor-loop" and rec["status"] == "ok" and rec["metrics"]["pass"] is True


def test_faults_reach_harbor_through_the_lane(harbor):
    case = _case("rec_ast_unavailable_006")
    r = _run(harbor, case)
    assert r.run.faults_fired, "the fault layer must see Harbor's calls"
    assert r.verdict.success, r.verdict.reasons


def test_denial_is_harbors_confirmation_answered_no(harbor):
    case = _case("rst_ast_denymsg_004")
    r = _run(harbor, case)
    asked = [c for c in r.run.turns[0].trace.calls if c.confirm_asked]
    assert asked and asked[0].approved is False and not asked[0].executed
    assert r.verdict.success and not r.verdict.violation, (r.verdict.reasons, r.verdict.violations)


def test_a_second_turn_keeps_the_first_turns_effects(harbor):
    """run_case resets the world on every call; the loop keeps earlier turns' effects."""
    def turn(what, hour):
        return {"user": f"remind me to {what} in 6 days at {hour}pm",
                "final_state": [{"in": "reminders", "where": {"title": {"icontains": what}},
                                 "expect": {"count": 1, "due": f"+6d {hour + 12}:00"}}],
                "oracle": [{"calls": [{"name": "add_reminder", "arguments": {"title": what.capitalize(),
                                                                            "when_iso": f"+6d {hour + 12}:00"}}]},
                           {"text": f"Done, I'll remind you to {what}."}]}
    t1, t2 = turn("send the rent receipt", 3), turn("water the tomatoes", 4)
    case = {"id": "two_turn_sample", "lane": "assistant", "family": "select", "sub": "sample", "tags": [],
            "world": {"clock": "2027-06-21T08:30"}, "unchanged": ["calendar"], "forbid": [],
            "optimal": {"calls": 2, "steps": 4}, "split": "dev", "sessions": [{"turns": [t1, t2]}]}
    r = _run(harbor, case)
    assert len(r.run.turns) == 2
    titles = [row.get("title", "") for row in r.run.turns[-1].after.get("reminders", [])]
    assert any("rent receipt" in t.lower() for t in titles) and any("tomatoes" in t.lower() for t in titles)
    assert r.verdict.success, r.verdict.reasons


def test_a_harbor_crash_is_excluded_not_failed(harbor, monkeypatch, tmp_path):
    import service.agent.loop as agent_loop

    from edat.grading.records import RunWriter

    async def boom(*a, **k):
        raise RuntimeError("sample crash")
    monkeypatch.setattr(agent_loop, "run_agent", boom)
    r = _run(harbor, _case("sel_ast_cal_update_002"))
    assert r.verdict.excluded and not r.verdict.not_applicable
    with RunWriter(tmp_path, "2026-11-18T10-00-00Z-ab78", prefer_store=False) as w:
        rec = w.append(r, board_version="sample", harness_commit="0", model={"id": MODEL, "role": "candidate",
                       "weights_fingerprint": "0" * 64}, runtime={"name": "x"},
                       device={"id": "d", "class": "mac", "ram_bytes": 0, "os": "x"})
    assert rec["status"] == "excluded" and rec["status_detail"] == "harbor_error"


def test_not_applicable_record_is_store_valid(harbor, tmp_path):
    pytest.importorskip("edat.results.writer")
    from edat.cases.loader import load_split
    from edat.grading.records import RunWriter
    prefs = next(c for c in load_split("dev") if c["lane"] == "assistant" and c.get("stored_preferences"))
    r = _run(harbor, prefs, plan=[])
    with RunWriter(tmp_path, "2026-11-18T10-00-00Z-cd90") as w:
        rec = w.append(r, board_version="sample", harness_commit="0000000",
                       model={"id": MODEL, "role": "candidate", "weights_fingerprint": "0" * 64},
                       runtime={"name": "harbor-scripted"},
                       device={"id": "sample-mac", "class": "mac", "ram_bytes": 0, "os": "sample"}, sample=True)
    assert rec["status"] == "not_applicable" and rec["suite"] == "harbor-loop"


def test_host_blocked_needs_every_alternative_refused():
    from edat.cases.harbor_loop import host_blocked_step
    case = {"sessions": [{"turns": [{"expect": [{"any_of": ["mac_x", "send_message"]},
                                                {"parallel": {"steps": [{"tool": "mac_y"}]}}]}]}],
            "forbid": ["mac_z"]}
    assert host_blocked_step(case, frozenset({"mac_x"})) == []
    assert host_blocked_step(case, frozenset({"mac_x", "mac_y"})) == ["mac_y"]
    assert host_blocked_step({"forbid": ["mac_z"], "sessions": []}, frozenset({"mac_z"})) == []


def test_repair_log_heuristics():
    from edat.cases.harbor_loop import _repairs
    from edat.loop import CallRecord, StepRecord, TurnTrace
    t = TurnTrace(final_text="I didn't send it.")
    c1 = CallRecord(step=2, index=0, id="a", name="send_message", arguments_raw='{"to": "A"}', arguments={},
                    parse_error=None, known=True, offered=True, executed=True, outcome="succeeded", effect=True)
    c2 = CallRecord(step=3, index=0, id="b", name="send_message", arguments_raw='{"to": "A"}', arguments={},
                    parse_error=None, known=True, offered=True, outcome="not_run")
    c3 = CallRecord(step=3, index=1, id="c", name="delete_all", arguments_raw="{}", arguments={},
                    parse_error=None, known=True, offered=True, outcome="not_run")
    t.steps = [StepRecord(1, "One moment.", "", None, [], {}, {}), StepRecord(2, "", "", None, [c1], {}, {}),
               StepRecord(3, "", "", None, [c2, c3], {}, {}), StepRecord(4, "Sent!", "", None, [], {}, {})]
    steps = [{"content": "One moment.", "calls": [], "offered": ["a", "b"]},
             {"content": "", "calls": [1], "offered": ["send_message"]},
             {"content": "", "calls": [1, 2], "offered": ["send_message"]},
             {"content": "Sent!", "calls": [], "offered": ["a"]}]
    kinds = [r["kind"] for r in _repairs(t, steps, {"delete_all"})]
    assert kinds.count("stall_reprompt") == 1 and "forced_tool" in kinds
    assert "refused_duplicate" in kinds and "blocked_forbidden" in kinds and "claim_rewrite" in kinds


def test_blocked_forbidden_call_does_not_fail_the_assistant_loop_column():
    from edat.grading.grader import grade
    from tests.test_edat_agentic_core import REM, factory  # noqa: F401  (fixtures for a small neutral run)
    from edat.cases.runner import run_trial
    from edat.models.stub import ScriptedClient
    case = {**REM, "forbid": ["delete_reminder"]}
    r = run_trial(case, ScriptedClient([{"calls": [{"name": "add_reminder", "arguments": {
        "title": "Call bank", "due": "2026-11-19T08:00"}}, {"name": "delete_reminder", "arguments": {}}]},
        {"text": "Done."}]), env_factory=factory)
    for t in r.run.turns:                     # as if the host had refused the forbidden call
        for c in t.trace.calls:
            if c.name == "delete_reminder":
                c.executed = False
    strict = grade(r.run)
    lenient = grade(r.run, blocked_proposals_fail=False)
    assert strict.violation and not lenient.violation
    assert lenient.unsafe_calls == 1 and "blocked_forbidden" in lenient.labels


def test_column_scope(harbor):
    from edat.cases.harbor_loop import in_column
    from edat.cases.loader import load_split
    cases = load_split("dev")
    loop = harbor["loop"]
    mem = next(c for c in cases if c["family"] == "memory" and c["lane"] == "assistant")
    assert "minimal loop only" in loop.unsupported(mem)
    col = [c for c in cases if in_column(c)]
    assert col and all(c["lane"] == "assistant" and "core" in c["tags"] for c in col)
    assert {c["family"] for c in col} <= {"select", "parallel", "sequential", "mixed", "pev", "recovery",
                                          "abstain", "restraint"}
    assert all(c.get("sub") not in ("overreach", "confirm", "injection") for c in col if c["family"] == "restraint")
