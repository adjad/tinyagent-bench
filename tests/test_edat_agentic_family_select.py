"""Family Select (spec 02 §3.1): counts, structure, a sampled oracle check, and wrong behaviours that fail."""
from __future__ import annotations

import copy
import random
from collections import Counter

import pytest

from edat.cases.bind import bind
from edat.cases.generators import select
from edat.cases.menu import needed_tools
from edat.cases.oracle import check_case, oracle_turns
from edat.cases.runner import run_trial
from edat.cases.schema import problems
from edat.models.stub import ScriptedClient

SUBS = ("easy", "near_miss", "paraphrase")
DOMAINS = {"assistant": {"mail", "messages", "calendar", "reminders", "notes", "files", "device", "web", "contacts",
                         "memory", "utility"},
           "neutral": {"mail", "messages", "calendar", "reminders", "notes", "files", "device", "home", "web",
                       "contacts", "memory", "utility"}}


@pytest.fixture(scope="module")
def cases():
    return select.generate("dev", random.Random("select|20261118"))


def case(cases, cid):
    return next(c for c in cases if c["id"] == cid)


def run(c, turns, ws=1):
    return run_trial(c, ScriptedClient(turns, name="wrong"), world_seed=ws)


def test_counts_per_lane_and_sub_family(cases):
    counts = Counter((c["lane"], c["sub"]) for c in cases)
    assert sum(1 for c in cases if c["lane"] == "assistant") == 74
    assert sum(1 for c in cases if c["lane"] == "neutral") == 44
    assert counts[("assistant", "easy")] == 10
    assert counts[("assistant", "near_miss")] == 39
    assert counts[("assistant", "paraphrase")] == 25
    assert counts[("neutral", "easy")] == 5
    assert counts[("neutral", "near_miss")] == 28
    assert counts[("neutral", "paraphrase")] == 11
    for lane in ("assistant", "neutral"):
        domains = {c["notes"].removeprefix("domain: ") for c in cases if c["lane"] == lane}
        assert domains == DOMAINS[lane], (lane, domains ^ DOMAINS[lane])


def test_ids_unique_cases_valid_and_held_out_differs(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    for c in cases:
        assert problems(c) == [], c["id"]
        assert c["family"] == "select" and c["sub"] in SUBS and c["lane"] in ("assistant", "neutral")
        assert c.get("trap") and c.get("optimal") and c.get("oracle") and c.get("expect")
        assert c.get("final_state") or c.get("answer") or c.get("expect"), c["id"]
    held = select.generate("heldout", random.Random("select|987654"))
    assert not {c["id"] for c in held} & set(ids)
    assert all(c["id"].startswith("selh_") for c in held)
    assert len(held) == len(cases)
    assert {c["prompt"] for c in held} != {c["prompt"] for c in cases}


def test_deterministic_for_a_given_rng():
    a = select.generate("dev", random.Random("select|20261118"))
    b = select.generate("dev", random.Random("select|20261118"))
    assert a == b


def test_core_share_per_group(cases):
    for lane in ("assistant", "neutral"):
        for sub in SUBS:
            group = [c for c in cases if c["lane"] == lane and c["sub"] == sub]
            share = sum("core" in c["tags"] for c in group) / len(group)
            assert 0.25 <= share <= 0.55, (lane, sub, share)


def test_tools_named_by_cases_exist_in_their_lane(cases):
    from edat.state.assistant_lane import AssistantEnvironment
    from edat.state.clock import Clock
    from edat.tools_generic import GenericEnvironment
    names = {"assistant": set(AssistantEnvironment(Clock.parse(), world_seed=1).tool_names()),
             "neutral": set(GenericEnvironment(clock=Clock.parse()).tool_names())}
    for c in cases:
        used = set(needed_tools(bind(c, 1))) | set(c.get("forbid") or []) | set(c.get("menu_extra") or [])
        assert used <= names[c["lane"]], (c["id"], used - names[c["lane"]])


def test_near_miss_siblings_are_forbidden_or_offered(cases):
    """Every near-miss case lists a sibling tool, so the menu always holds the distractor (unless the
    near miss is another action of the same tool, such as email_triage archive vs mark_read)."""
    for c in cases:
        if c["sub"] == "near_miss":
            same_tool = any("action" in (step.get("args") or {}) for step in c["expect"])
            assert c.get("forbid") or c.get("menu_extra") or same_tool, c["id"]


def test_sampled_cases_pass_the_oracle_and_fail_the_null_model(cases):
    sample = cases[:: max(1, len(cases) // 6)][:6]
    assert len(sample) == 6
    for c in sample:
        res = check_case(c, seeds=(1,))
        assert res.ok, (c["id"], res.problems)


def test_near_miss_draft_instead_of_send_fails(cases):
    c = case(cases, "sel_ast_msg_send_001")
    turns = copy.deepcopy(oracle_turns(bind(c, 1)))
    turns[0]["calls"][0]["name"] = "draft_message"
    r = run(c, turns).verdict
    assert not r.success and not r.execute
    assert any("collateral" in v for v in r.violations)          # a draft nobody asked for
    # the right route still passes
    assert run(c, oracle_turns(bind(c, 1))).verdict.success


def test_near_miss_permanent_delete_instead_of_trash_fails(cases):
    c = case(cases, "sel_neu_file_trash_001")
    turns = copy.deepcopy(oracle_turns(bind(c, 1)))
    turns[0]["calls"][0]["name"] = "files_delete_permanent"
    r = run(c, turns).verdict
    assert not r.success and r.violation
    assert "forbidden call files_delete_permanent" in r.violations


def test_easy_guessing_or_wrong_effect_fails(cases):
    c = case(cases, "sel_ast_contact_lookup_001")
    guess = run(c, [{"text": "Their number is +1 555 0100."}]).verdict
    assert not guess.success and not guess.execute
    first = bind(c, 1)["world"]["seed_records"]["contacts"][0]["name"].split()[0]
    texted = run(c, [{"calls": [{"name": "send_message", "arguments": {"to": first, "text": "hi"}}]},
                     {"text": "Done."}]).verdict
    assert not texted.success and texted.violation


def test_paraphrase_reminder_instead_of_alarm_fails(cases):
    c = case(cases, "sel_ast_alarm_001")
    b = bind(c, 1)
    t = b["_facts"]["t"]
    wrong = run(c, [{"calls": [{"name": "add_reminder",
                               "arguments": {"title": "Wake up", "when_iso": f"{b['_facts']['tomorrow_iso']}T{t}:00"}}]},
                    {"text": f"Reminder set for {t}."}]).verdict
    assert not wrong.success and not wrong.execute
    right = run(c, oracle_turns(b)).verdict
    assert right.success


def test_paraphrase_neutral_free_time_needs_the_seeded_calendar(cases):
    c = case(cases, "sel_neu_cal_free_001")
    guess = run(c, [{"text": "You could start at 9:00."}]).verdict
    assert not guess.success and not guess.execute


def test_view_vs_summarize_alternative_route_with_a_query_passes(cases):
    """summarize_emails with a query returns the raw message, so the answer is still reachable."""
    c = case(cases, "sel_ast_mail_view_001")
    b = bind(c, 1)
    first = oracle_turns(b)[0]["calls"][0]["arguments"]["query"]
    ok = run(c, [{"calls": [{"name": "summarize_emails", "arguments": {"query": first}}]},
                 {"text": f"It is at {b['_facts']['h']}:30."}]).verdict
    assert ok.success
    bare = run(c, [{"calls": [{"name": "summarize_emails", "arguments": {}}]},
                   {"text": "The digest lists the email but gives no time."}]).verdict
    assert not bare.success      # a bare digest carries no body, so the time is never read
