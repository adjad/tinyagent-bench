"""Family Sequential (spec 02 §3.3): counts, structure, oracle sample, and wrong-behaviour checks."""
from __future__ import annotations

import copy
import random
from collections import Counter

import pytest

from edat.cases.bind import bind
from edat.cases.generators import sequential
from edat.cases.oracle import check_case, oracle_turns
from edat.cases.runner import run_trial
from edat.cases.schema import problems
from edat.models.stub import ScriptedClient

WS = 1


@pytest.fixture(scope="module")
def dev_cases() -> list[dict]:
    return sequential.generate("dev", random.Random("sequential|20261118"))


@pytest.fixture(scope="module")
def by_id(dev_cases) -> dict[str, dict]:
    return {c["id"]: c for c in dev_cases}


def run(case: dict, turns: list[dict], ws: int = WS):
    return run_trial(case, ScriptedClient(turns, name="script", on_exhaust="stop"), world_seed=ws)


def oracle(case: dict, ws: int = WS) -> list[dict]:
    return copy.deepcopy(oracle_turns(bind(case, ws)))


# ------------------------------------------------------------------ shape
def test_counts_per_lane_and_sub_family(dev_cases):
    counts = Counter((c["lane"], c["sub"]) for c in dev_cases)
    assert counts == {("assistant", "depth2"): 20, ("assistant", "depth3"): 20, ("assistant", "depth4"): 20,
                      ("neutral", "depth2"): 6, ("neutral", "depth3"): 9, ("neutral", "depth4"): 9,
                      ("neutral", "depth5"): 6}
    assert {c["family"] for c in dev_cases} == {"sequential"}


def test_ids_unique_cases_valid_and_chain_depth_matches_sub(dev_cases):
    ids = [c["id"] for c in dev_cases]
    assert len(ids) == len(set(ids))
    for c in dev_cases:
        assert problems(c) == [], c["id"]
        assert c["trap"] and c["oracle"] and c["final_state"] and c["unchanged"]
        calls = sum(len(s.get("calls") or []) for s in c["oracle"])
        depth = int(c["sub"][-1])
        # the oracle makes at least `depth` dependent calls (a few chains carry one extra arithmetic call)
        assert depth <= calls <= depth + 1, (c["id"], calls)
        assert c["optimal"] == {"calls": calls, "steps": calls + 1}
    # depth-5 chains exist only in the neutral lane
    assert not [c for c in dev_cases if c["lane"] == "assistant" and c["sub"] == "depth5"]


def test_about_forty_percent_core(dev_cases):
    groups: dict[tuple, list[bool]] = {}
    for c in dev_cases:
        groups.setdefault((c["lane"], c["sub"]), []).append("core" in c["tags"])
    for key, flags in groups.items():
        share = sum(flags) / len(flags)
        assert 0.3 <= share <= 0.5, (key, share)


def test_heldout_ids_do_not_collide_with_dev(dev_cases):
    held = sequential.generate("heldout", random.Random("sequential|private-test-seed"))
    assert len(held) == len(dev_cases)
    assert not {c["id"] for c in held} & {c["id"] for c in dev_cases}
    assert all(c["id"].startswith("seqh_") for c in held)
    # same templates, different draws
    assert [c["prompt"] for c in held] != [c["prompt"] for c in dev_cases]


def test_passed_down_values_are_facts_or_gen_time_ids(dev_cases):
    """Every chain has a seeded fact, and every case that checks a path, address or id grounds it."""
    for c in dev_cases:
        assert c["facts"] or c["lane"] == "neutral", c["id"]   # id-only neutral chains draw ids per case
        for g in c.get("grounded_args") or []:
            assert g["tool"] in {k["name"] for s in c["oracle"] for k in (s.get("calls") or [])}, c["id"]


SAMPLE = ["seq_ast_emailevent_001", "seq_ast_flight_002", "seq_ast_viewing_001", "seq_ast_quote_003",
          "seq_neu_calmove_001", "seq_neu_invoice_002", "seq_neu_agenda_001", "seq_neu_web_002"]


def test_oracle_passes_and_null_model_fails_on_a_sample(by_id):
    for cid in SAMPLE:
        res = check_case(by_id[cid], seeds=(1,))
        assert res.ok, res.problems


# ------------------------------------------------------------------ wrong behaviour
def test_guessed_value_without_reading_fails_assistant_chain(by_id):
    """depth3: calendar event and text built from a guessed landing time instead of the booking email."""
    case = by_id["seq_ast_flight_001"]
    good = oracle(case)
    add = good[1]["calls"][0]["arguments"]
    msg = good[2]["calls"][0]["arguments"]
    guess = [{"calls": [{"name": "add_calendar_event", "arguments": {**add, "when_iso": "2026-11-19T09:05"}}]},
             {"calls": [{"name": "send_message", "arguments": {**msg, "text": "My flight lands at 9:05 tomorrow."}}]},
             {"text": "Done."}]
    v = run(case, guess).verdict
    assert not v.success and not v.execute
    assert v.progress < 1.0
    # and the genuine chain, run once, passes
    assert run(case, good).verdict.success


def test_guessed_id_or_path_fails_neutral_chain(by_id):
    """depth2: updating an event by an id that was never read; depth4: reading a guessed file path."""
    case = by_id["seq_neu_calmove_001"]
    good = oracle(case)
    upd = good[1]["calls"][0]["arguments"]
    bad = [{"calls": [{"name": "calendar_update_event", "arguments": {**upd, "event_id": "ev-0000"}}]}, {"text": "Moved."}]
    v = run(case, bad).verdict
    assert not v.success and v.progress < 1.0
    # right event is the one on the later day; moving the decoy twin (found by searching) is wrong too
    decoy_id = case["world"]["seed_records"]["calendar"][0]["id"]
    assert decoy_id != upd["event_id"]
    wrong = copy.deepcopy(good)
    wrong[1]["calls"][0]["arguments"]["event_id"] = decoy_id
    v2 = run(case, wrong).verdict
    assert not v2.success

    inv = by_id["seq_neu_invoice_001"]
    steps = oracle(inv)
    steps[2]["calls"][0]["arguments"]["path"] = "~/Documents/inv-0000.txt"
    v3 = run(inv, steps[:3] + [{"text": "Could not read it."}]).verdict
    assert not v3.success


def test_decoy_value_or_stale_value_from_an_earlier_hop_fails(by_id):
    """A figure taken from the decoy file, or the old time instead of the new one, does not satisfy the chain."""
    case = by_id["seq_neu_msgfile_001"]
    steps = oracle(case)
    facts = bind(case, WS)["_facts"]
    steps[2]["calls"][0]["arguments"]["text"] = f"The figure is {facts['oth']} dollars."
    assert not run(case, steps).verdict.success

    shift = by_id["seq_neu_calshift_001"]
    steps = oracle(shift)
    f = bind(shift, WS)["_facts"]
    steps[2]["calls"][0]["arguments"]["text"] = f"My meeting is at {f['s_h']}:{f['s_m']}."   # the OLD start
    assert not run(shift, steps).verdict.success


def test_correct_chain_with_an_extra_harmless_read_still_passes(by_id):
    # assistant: an extra calendar read in the middle of the email -> event -> text chain
    case = by_id["seq_ast_flight_003"]
    steps = oracle(case)
    steps.insert(1, {"calls": [{"name": "get_upcoming", "arguments": {"days": 3}}]})
    v = run(case, steps).verdict
    assert v.success and not v.violation
    assert v.call_eff is not None and v.call_eff < 1.0      # the extra call costs efficiency, not success

    # neutral: an extra list read between the two reads of a depth-5 chain
    deep = by_id["seq_neu_receipt_001"]
    steps = oracle(deep)
    steps.insert(2, {"calls": [{"name": "reminders_list", "arguments": {}}]})
    v = run(deep, steps).verdict
    assert v.success and not v.violation


def test_destructive_shortcut_is_a_violation(by_id):
    """Deleting and recreating the event instead of moving it trips `forbid` even if the end state matches."""
    case = by_id["seq_neu_calshift_001"]
    steps = oracle(case)
    steps.insert(1, {"calls": [{"name": "calendar_delete_event", "arguments": {"event_id": steps[1]["calls"][0]["arguments"]["event_id"]}}]})
    v = run(case, steps).verdict
    assert v.violation and not v.success
