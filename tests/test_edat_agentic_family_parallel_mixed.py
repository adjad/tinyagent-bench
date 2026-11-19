"""Parallel and Mixed families (spec 02 §3.2, §3.4): counts, schema, oracle sample, and wrong behaviours."""
from __future__ import annotations

import copy
import random
from collections import Counter

import pytest

from edat.cases.bind import bind
from edat.cases.generators import parallel_mixed as pm
from edat.cases.oracle import check_case, oracle_turns
from edat.cases.runner import run_trial
from edat.cases.schema import problems
from edat.models.stub import NullClient, ScriptedClient


def _dev() -> list[dict]:
    return pm.generate("dev", random.Random("parallel_mixed|20261118"))


CASES = _dev()
BY_ID = {c["id"]: c for c in CASES}


def _run(case: dict, turns: list[dict], seed: int = 1):
    return run_trial(case, ScriptedClient(turns, name="script"), world_seed=seed).verdict


def _oracle(case: dict, seed: int = 1) -> list[dict]:
    return copy.deepcopy(oracle_turns(bind(case, seed)))


def _counts(family: str, lane: str, sub: str | None = None) -> int:
    return sum(1 for c in CASES if c["family"] == family and c["lane"] == lane and (sub is None or c["sub"] == sub))


# ----------------------------------------------------------------- shape of the dev split
def test_counts_per_lane_family_and_sub_family():
    assert _counts("parallel", "assistant") == 50
    assert _counts("parallel", "neutral") == 25
    assert _counts("mixed", "assistant") == 30
    assert _counts("mixed", "neutral") == 15
    assert (_counts("parallel", "assistant", "same_tool"), _counts("parallel", "assistant", "multi_source"),
            _counts("parallel", "assistant", "independent_writes")) == (17, 17, 16)
    assert (_counts("parallel", "neutral", "same_tool"), _counts("parallel", "neutral", "multi_source"),
            _counts("parallel", "neutral", "independent_writes")) == (9, 8, 8)


def test_ids_are_unique_and_use_the_family_prefix():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) == 120
    for c in CASES:
        assert c["id"].startswith("par_" if c["family"] == "parallel" else "mix_")
        assert ("_ast_" in c["id"]) == (c["lane"] == "assistant")


def test_every_case_is_structurally_valid_and_has_the_parallel_group():
    for c in CASES:
        assert problems(c) == [], c["id"]
        first = c["expect"][0]["parallel"]
        assert first["same_step"] is True and 2 <= len(first["steps"]) <= 4, c["id"]
        assert c["oracle"][0]["calls"] and len(c["oracle"][0]["calls"]) == len(first["steps"]), c["id"]
        assert c["trap"] and c["forbid"], c["id"]
        if c["family"] == "parallel":
            assert len(c["expect"]) == 1 and c["optimal"] == {"calls": len(first["steps"]), "steps": 2}, c["id"]
        else:
            assert len(c["expect"]) == 2 and c["optimal"] == {"calls": len(first["steps"]) + 1, "steps": 3}, c["id"]
            assert c["final_state"] and c["facts"], c["id"]


def test_group_sizes_split_about_a_third_each_in_every_parallel_sub_family():
    sizes = Counter((c["lane"], c["sub"], len(c["expect"][0]["parallel"]["steps"]))
                    for c in CASES if c["family"] == "parallel")
    for lane, sub in {(c["lane"], c["sub"]) for c in CASES if c["family"] == "parallel"}:
        n = sum(sizes[(lane, sub, k)] for k in (2, 3, 4))
        for k in (2, 3, 4):
            assert sizes[(lane, sub, k)] >= n // 3 - 1, (lane, sub, k, sizes)


def test_about_forty_percent_of_each_sub_family_is_core():
    for key_of in (lambda c: (c["family"], c["lane"]), lambda c: (c["family"], c["lane"], c["sub"])):
        groups: dict[tuple, list[bool]] = {}
        for c in CASES:
            groups.setdefault(key_of(c), []).append("core" in c["tags"])
        for key, flags in groups.items():
            if len(flags) >= 8:
                assert 0.3 <= sum(flags) / len(flags) <= 0.5, (key, sum(flags), len(flags))


def test_generation_is_deterministic_and_heldout_ids_do_not_collide():
    assert [c["id"] for c in _dev()] == [c["id"] for c in CASES]
    assert _dev()[5]["prompt"] == CASES[5]["prompt"]
    held = pm.generate("heldout", random.Random("parallel_mixed|987654321"))
    assert len(held) == len(CASES)
    assert not {c["id"] for c in held} & {c["id"] for c in CASES}
    assert all(c["id"].startswith(("parh_", "mixh_")) for c in held)


def test_the_assistant_lane_never_reads_seeded_weather_web_or_stocks():
    for c in CASES:
        if c["lane"] != "assistant":
            continue
        assert not set(c.get("world", {}).get("seed_records", {})) & {"weather", "web", "stocks"}, c["id"]
        called = {call["name"] for step in c["oracle"] for call in step.get("calls", [])}
        assert not called & {"get_weather", "web_search", "web_fetch", "get_stock_price"}, c["id"]


def test_mixed_joins_depend_on_per_trial_facts():
    for c in CASES:
        if c["family"] != "mixed":
            continue
        a, b = bind(c, 1)["_facts"], bind(c, 2)["_facts"]
        assert a != b, c["id"]
        dumped = repr(c["final_state"]) + repr(c.get("intended_recipients"))
        assert "{" in dumped, c["id"]          # the join check names a seeded fact, not a fixed value


# ----------------------------------------------------------------- the oracle and the null model
def test_oracle_passes_and_null_fails_on_a_sample_of_each_kind():
    picks = ["par_ast_contacts_001", "par_ast_w_times_001", "par_neu_weather_001", "par_neu_sources_001",
             "mix_ast_far_001", "mix_neu_warm_001", "mix_ast_event_email_001"]
    for cid in picks:
        res = check_case(BY_ID[cid], seeds=(1,))
        assert res.ok, (cid, res.problems)


def test_oracle_emits_the_whole_group_in_one_step_so_the_strict_rate_is_true():
    for cid in ("par_ast_contacts_001", "par_neu_weather_001", "mix_neu_warm_001"):
        case = BY_ID[cid]
        v = _run(case, _oracle(case))
        assert v.success and v.parallel_strict is True, (cid, v.reasons)


# ----------------------------------------------------------------- targeted wrong behaviours
@pytest.mark.parametrize("cid", ["par_ast_contacts_001", "par_neu_weather_001", "mix_neu_warm_001",
                                 "par_ast_w_times_001"])
def test_same_calls_over_two_steps_still_succeed_but_are_not_strictly_parallel(cid):
    case = BY_ID[cid]
    turns = _oracle(case)
    calls = turns[0]["calls"]
    assert len(calls) >= 2
    split = [{"calls": calls[:1]}, {"calls": calls[1:]}] + turns[1:]
    v = _run(case, split)
    assert v.success and not v.violation, (cid, v.reasons, v.violations)
    assert v.parallel_strict is False
    assert v.step_eff is not None and v.step_eff < 1.0       # the efficiency metric, not success, pays for it


def test_same_tool_one_lookup_instead_of_the_group_fails():
    case = BY_ID["par_ast_contacts_001"]
    turns = _oracle(case)
    turns[0]["calls"] = turns[0]["calls"][:1]
    turns[1]["text"] = turns[1]["text"].split(". ")[0]      # an honest answer from the one lookup
    v = _run(case, turns)
    assert not v.success


def test_same_tool_guessed_values_without_reading_fail():
    case = BY_ID["par_neu_weather_001"]
    v = _run(case, [{"text": "It is 11 degrees in each of them."}])
    assert not v.success


def test_independent_writes_one_combined_reminder_fails():
    case = BY_ID["par_ast_w_times_001"]
    turns = _oracle(case)
    first = turns[0]["calls"][0]["arguments"]
    combined = {"name": "add_reminder",
                "arguments": {"title": "Errands: " + "; ".join(c["arguments"]["title"] for c in turns[0]["calls"]),
                              "when_iso": first["when_iso"]}}
    v = _run(case, [{"calls": [combined]}, {"text": "Done."}])
    assert not v.success


def test_independent_writes_wrong_time_fails():
    case = BY_ID["par_neu_w_times_001"]
    turns = _oracle(case)
    turns[0]["calls"][0]["arguments"]["due"] = "2026-11-19T23:00"
    assert not _run(case, turns).success


def test_multi_source_answering_from_one_source_fails():
    case = BY_ID["par_ast_sources_001"]
    turns = _oracle(case)
    turns[0]["calls"] = turns[0]["calls"][:1]
    turns[1]["text"] = turns[1]["text"].split(". ")[0]      # the answer covers only the source it read
    assert not _run(case, turns).success


def test_mixed_join_with_the_wrong_winner_fails():
    case = BY_ID["mix_neu_warm_001"]
    turns = _oracle(case)
    facts = bind(case, 1)["_facts"]
    turns[1]["calls"][0]["arguments"]["text"] = f"{facts['lose1']} is the warmer right now."
    v = _run(case, turns)
    assert not v.success


def test_mixed_join_before_reading_is_a_guess_that_fails_on_some_seed():
    case = BY_ID["mix_neu_warm_001"]
    results = []
    for seed in (1, 2, 3, 4, 5, 6):
        facts = bind(case, seed)["_facts"]
        oracle = _oracle(case, seed)
        join = copy.deepcopy(oracle[1])
        guess = oracle[0]["calls"][0]["arguments"]["location"]           # always the first city
        join["calls"][0]["arguments"]["text"] = f"{guess} is the warmer right now."
        results.append(_run(case, [join, {"text": "Texted."}], seed).success)
    assert not all(results)


def test_mixed_texting_every_friend_instead_of_the_winner_fails():
    case = BY_ID["mix_ast_far_001"]
    turns = _oracle(case)
    names = [c["arguments"]["query" if "query" in c["arguments"] else "conversation"] for c in turns[0]["calls"]]
    texts = [{"name": "send_message", "arguments": {"to": n, "text": "We'll save you a plate."}} for n in names]
    v = _run(case, [turns[0], {"calls": texts}, {"text": "Texted."}])
    assert not v.success


def test_mixed_read_then_remind_needs_the_emailed_reference():
    case = BY_ID["mix_ast_event_email_001"]
    turns = _oracle(case)
    args = turns[1]["calls"][0]["arguments"]
    args["title"] = "Bring the booking reference"
    assert not _run(case, turns).success


def test_null_model_fails_every_dev_case_on_seed_one():
    for c in CASES:
        v = run_trial(c, NullClient(), world_seed=1).verdict
        assert not v.success, c["id"]
