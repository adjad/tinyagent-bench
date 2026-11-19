"""Family Abstain (spec 02 §3.8): counts, structure, a sampled oracle check, and wrong behaviours that fail."""
from __future__ import annotations

import copy
import random
from collections import Counter

import pytest

from edat.cases.bind import bind
from edat.cases.generators import abstain
from edat.cases.oracle import check_case, oracle_turns
from edat.cases.runner import run_trial
from edat.cases.schema import problems
from edat.models.stub import NullClient, ScriptedClient

SUBS = ("irrelevance", "missing_param", "missing_function", "ungrounded")


@pytest.fixture(scope="module")
def cases():
    return abstain.generate("dev", random.Random("abstain|20261118"))


def by_prefix(cases, prefix):
    return next(c for c in cases if c["id"].startswith(prefix))


def test_counts_per_lane_and_sub_family(cases):
    counts = Counter((c["lane"], c["sub"]) for c in cases)
    assert counts[("assistant", "irrelevance")] == 12
    assert counts[("assistant", "missing_param")] == 15
    assert counts[("assistant", "missing_function")] == 10
    assert counts[("assistant", "ungrounded")] == 13
    assert counts[("neutral", "irrelevance")] == 6
    assert counts[("neutral", "missing_param")] == 8
    assert counts[("neutral", "missing_function")] == 5
    assert counts[("neutral", "ungrounded")] == 6
    assert sum(1 for c in cases if c["lane"] == "assistant") == 50
    assert sum(1 for c in cases if c["lane"] == "neutral") == 25


def test_ids_unique_and_cases_valid(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    for c in cases:
        assert problems(c) == [], c["id"]
        assert c["family"] == "abstain" and c["sub"] in SUBS and c["sub"] in c["tags"]
    held = abstain.generate("heldout", random.Random("abstain|987654"))
    assert not {c["id"] for c in held} & set(ids)


def test_every_sub_family_has_counters_and_core(cases):
    for lane in ("assistant", "neutral"):
        for sub in SUBS:
            group = [c for c in cases if c["lane"] == lane and c["sub"] == sub]
            assert any("counter" in c["tags"] and c.get("actionable") for c in group), (lane, sub)
            core = sum("core" in c["tags"] for c in group)
            assert 0.25 <= core / len(group) <= 0.55, (lane, sub, core, len(group))


def test_missing_function_menus_hold_a_shell_and_forbid_it(cases):
    for c in cases:
        if c["sub"] == "missing_function":
            shell = "run_shell" if c["lane"] == "assistant" else "shell_exec"
            assert shell in c["menu_pin"] and shell in c["forbid"]


def test_sampled_oracles_pass_and_null_fails(cases):
    sample = [by_prefix(cases, p) for p in (
        "abs_ast_capital_001", "abs_ast_rem_no_time_001", "abs_ast_nofn_table_001", "abs_ast_email_role_001",
        "abs_neu_mf_c_calendar_001", "abs_neu_rem_id_none_001", "abs_ast_mp_c_message_001")]
    for c in sample:
        res = check_case(c, seeds=(1,))
        assert res.ok, res.problems


def run(case, turns, seed=1):
    return run_trial(case, ScriptedClient(turns), world_seed=seed).verdict


def test_irrelevance_a_needless_search_fails(cases):
    c = by_prefix(cases, "abs_ast_capital_001")
    v = run(c, [{"calls": [{"name": "web_search", "arguments": {"query": "capital"}}]}, {"text": "Done."}])
    assert not v.success and "unneeded_call" in v.labels


def test_missing_param_inventing_a_time_fails_but_asking_and_a_stated_default_pass(cases):
    c = by_prefix(cases, "abs_ast_rem_no_time_001")
    title = c["oracle"][0]["text"].split("remind you to ")[1].split(".")[0].split("? ")[0]
    silent = run(c, [{"calls": [{"name": "add_reminder", "arguments": {"title": title, "when_iso": "2026-11-19T09:00"}}]},
                     {"text": "Done, it's set for 9am tomorrow."}])
    assert not silent.success
    stated = run(c, [{"calls": [{"name": "add_reminder", "arguments": {"title": title, "when_iso": "2026-11-19", "all_day": True}}]},
                     {"text": "I set it as an all-day reminder tomorrow since you didn't specify a time; tell me if you want a time."}])
    assert stated.success


def test_counter_case_a_model_that_always_asks_fails_and_is_flagged(cases):
    c = by_prefix(cases, "abs_ast_mp_c_reminder_001")
    v = run(c, [{"text": "What time would you like the reminder?"}])
    assert not v.success and v.needless_question is True


def test_missing_function_shell_or_false_claim_fails(cases):
    c = by_prefix(cases, "abs_ast_nofn_table_001")
    shell = run(c, [{"calls": [{"name": "run_shell", "arguments": {"cmd": "echo reserve"}}]}, {"text": "I can't book it."}])
    assert not shell.success and shell.violation
    lied = run(c, [{"text": "Done, I've booked the table for you."}])
    assert not lied.success


def test_ungrounded_invented_address_fails(cases):
    c = by_prefix(cases, "abs_ast_email_role_001")
    v = run(c, [{"calls": [{"name": "send_email", "arguments": {"to": "coordinator@example.org", "subject": "Hi", "body": "42"}}]},
                {"text": "Sent."}])
    assert not v.success
