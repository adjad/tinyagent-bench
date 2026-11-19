"""Family M (Flow) generator: counts, levels, ids, determinism, variety, held-out split, Guard behaviour.

The per-case oracle checks of the committed split run in `tests/test_edat_screen_cases.py`; here a few
cases are checked end to end and the confirmation rule at Place order / Book and pay is exercised.
"""
from __future__ import annotations

import copy
import random
from collections import Counter
from pathlib import Path

import pytest

from edat.screen.author import common, flow
from edat.screen.cases.oracle import check_case, run_oracle
from edat.screen.cases.schema import validate_case
from edat.screen.grade.grader import grade

FAM = "flow"
DEV_FILE = Path(__file__).resolve().parents[1] / "test_fixtures" / "screen" / "dev" / f"{FAM}.yaml"


def _gen(split="dev", seed=1101):
    n = common.counts(split)[FAM]
    return flow.generate(split, random.Random(f"screen:{FAM}:{split}:{seed}"), n)


@pytest.fixture(scope="module")
def dev():
    return _gen()


@pytest.fixture(scope="module")
def heldout():
    return _gen("heldout", 7)


def _by_tpl(cases, tpl, level):
    return next(c for c in cases if c["template"] == tpl and c["level"] == level)


def test_counts_levels_and_ladder_order(dev, heldout):
    assert len(dev) == common.DEV[FAM] and len(heldout) == common.HELDOUT[FAM]
    for cases in (dev, heldout):
        levels = [c["level"] for c in cases]
        assert levels == sorted(levels) and set(levels) == {3, 4}
        assert abs(levels.count(3) - levels.count(4)) <= 1


def test_ids_schema_and_no_build_keys(dev):
    ids = [c["id"] for c in dev]
    assert len(set(ids)) == len(ids)
    for c in dev:
        assert c["id"].startswith("scr_flow_") and validate_case(c) == [], (c["id"], validate_case(c))
        assert not {"optimal", "max_steps", "split", "core"} & set(c)
        assert c["oracle"][-1].get("finish") is not None
        if c["level"] >= 4:
            assert c["world"]["noise"]


def test_deterministic_from_the_rng(dev):
    assert _gen() == dev


def test_template_variety(dev):
    l3 = Counter(c["template"] for c in dev if c["level"] == 3)
    l4 = Counter(c["template"] for c in dev if c["level"] == 4)
    assert set(l3) == {t.__name__[2:] for t in flow.TEMPLATES[3]}
    assert set(l4) == {t.__name__[2:] for t in flow.TEMPLATES[4]}
    assert max(l3.values()) - min(l3.values()) <= 1 and max(l4.values()) - min(l4.values()) <= 1
    assert len({c["task"] for c in dev}) >= 40


def test_heldout_ids_carry_the_marker_and_differ(dev, heldout):
    assert all(c["id"].startswith("scr_flowh_") for c in heldout)
    assert not {c["id"] for c in dev} & {c["id"] for c in heldout}
    assert [c["task"] for c in dev] != [c["task"] for c in heldout]


def test_heldout_sample_passes_the_oracle_check(heldout):
    rng = random.Random(3)
    for lv in (3, 4):
        for c in rng.sample([c for c in heldout if c["level"] == lv], k=2):
            c = copy.deepcopy(c)
            r = check_case(c, set_budget=True)
            assert r.ok, (c["id"], r.problems)
            assert r.optimal <= 12, c["id"]


def test_checkout_and_booking_stop_for_confirmation(dev):
    for tpl, lv in (("checkout", 3), ("travel", 4)):
        c = copy.deepcopy(_by_tpl(dev, tpl, lv))
        assert c["needs_ask"] and not c["preauthorised"] and c["confirm_regex"]
        r = check_case(c, set_budget=True)
        assert r.ok, r.problems
        c["oracle"] = [s for s in c["oracle"] if "ask" not in s]
        bound, ep, _ = run_oracle(c, 1)
        v = grade(bound, ep, 1)
        assert v.final_state_ok and not v.success
        assert [e["kind"] for e in v.events] == ["unconfirmed_effect"]


def test_nested_setting_is_graded_on_the_seeded_app(dev):
    c = copy.deepcopy(_by_tpl(dev, "privacy", 3))
    bound, ep, _ = run_oracle(c, 2)
    assert grade(bound, ep, 1).success
    cam = bound.slots["cam_app"]
    assert bound.world.state["settings"]["privacy"]["access"]["camera"][cam] is False
    assert bound.initial["settings"]["privacy"]["access"]["camera"][cam] is True


def test_calendar_case_fails_when_the_same_titled_event_next_week_moves(dev):
    c = copy.deepcopy(_by_tpl(dev, "calendar", 3))
    bound, ep, _ = run_oracle(c, 1)
    assert grade(bound, ep, 1).success
    dist = next(e for e in bound.world.state["calendar"]["events"] if e["id"] == bound.slots["distractor_id"])
    assert dist["title"] == bound.slots["ev_title"]
    dist["start"] = bound.slots["ev_new_start"]
    assert not grade(bound, ep, 1).success


@pytest.mark.skipif(not DEV_FILE.exists(), reason="development split not generated")
def test_committed_oracles_stay_within_twelve_steps():
    from edat.screen.cases.store import load_cases
    cases = load_cases(DEV_FILE)
    assert len(cases) == common.DEV[FAM]
    assert all(c["optimal"]["steps"] <= 12 and c["max_steps"] <= 24 for c in cases)
