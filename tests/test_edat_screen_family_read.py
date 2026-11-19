"""Family R (Read) generator, spec 11 §3.7: counts, levels, ids, determinism, variety, abstain share, held-out.

The per-case oracle checks of the committed split run in tests/test_edat_screen_cases.py.
"""
from __future__ import annotations

import random
from collections import Counter

import pytest

from edat.screen.author import common
from edat.screen.author.read import generate
from edat.screen.cases.bind import bind
from edat.screen.cases.oracle import check_case, run_oracle
from edat.screen.cases.schema import validate_case
from edat.screen.grade.grader import grade
from edat.screen.grade.normalise import NOT_SHOWN

FAM = "read"


def gen(split: str, seed) -> list[dict]:
    return generate(split, random.Random(f"screen:{FAM}:{split}:{seed}"), common.counts(split)[FAM])


DEV = gen("dev", 1101)
HELD = gen("heldout", 7)


def test_counts_levels_and_ladder_order():
    for split, cases in (("dev", DEV), ("heldout", HELD)):
        assert len(cases) == common.counts(split)[FAM]
        levels = [c["level"] for c in cases]
        assert levels == sorted(levels) and set(levels) == {1, 2, 3}
        assert min(Counter(levels).values()) >= len(cases) // 4


def test_ids_unique_marked_and_split_apart():
    dev_ids, held_ids = [c["id"] for c in DEV], [c["id"] for c in HELD]
    assert len(set(dev_ids)) == len(dev_ids) and len(set(held_ids)) == len(held_ids)
    assert all(i.startswith("scr_read_") for i in dev_ids) and all(i.startswith("scr_readh_") for i in held_ids)
    assert not set(dev_ids) & set(held_ids)
    assert [c["task"] for c in DEV] != [c["task"] for c in HELD]


def test_schema_answer_and_nothing_changes():
    for c in DEV + HELD:
        assert validate_case(c) == [], c["id"]
        assert c["unchanged"] == "all" and c["answer"]
        assert "final_state" not in c and "allowed_effects" not in c
        assert c["answer"]["normalise"] in ("date", "time", "money", "number", "text")
        assert "finish" in c["oracle"][-1] and isinstance(c["oracle"][-1]["finish"], str)


def test_about_ten_percent_abstain_with_a_tempting_value():
    for cases in (DEV, HELD):
        ab = [c for c in cases if c["answer"].get("abstain")]
        assert len(ab) == round(len(cases) / 10)
        for c in ab:
            assert c["answer"]["not_contains"] and NOT_SHOWN.search(c["oracle"][-1]["finish"])
            assert c["level"] in (1, 2)


def test_deterministic_and_varied():
    assert gen("dev", 1101) == DEV
    for cases in (DEV, HELD):
        tpls = Counter(c["id"].rsplit("_", 1)[0].split("_", 2)[2] for c in cases)
        assert len(tpls) >= 22 and max(tpls.values()) <= 4
        assert {c["start"]["app"] for c in cases} >= {"settings", "mail", "messages", "calendar", "notes", "files",
                                                       "browser"}


def test_a_seeded_value_changes_with_the_world_seed():
    c = next(x for x in DEV if x["id"].split("_", 2)[2].startswith("settings_battery_level"))
    vals = {bind(c, s).case["answer"]["contains_all"][0] for s in (1, 2, 3, 4, 5)}
    assert len(vals) > 1


def test_a_wrong_answer_fails_and_the_tempting_value_fails_an_abstain_case():
    c = next(x for x in DEV if x["answer"].get("abstain"))
    tempting = c["answer"]["not_contains"][0]
    probe = dict(c, oracle=[*c["oracle"][:-1], {"finish": f"It isn't shown, maybe {tempting}"}])
    bound, ep, _ = run_oracle(probe, 1)
    assert not grade(bound, ep, 1).success
    plain = next(x for x in DEV if not x["answer"].get("abstain") and x["answer"]["normalise"] == "text")
    wrong = dict(plain, oracle=[*plain["oracle"][:-1], {"finish": "I think it's Zebra Crossing."}])
    bound, ep, _ = run_oracle(wrong, 1)
    assert not grade(bound, ep, 1).success


@pytest.mark.parametrize("case", HELD[::8], ids=lambda c: c["id"])
def test_heldout_sample_passes_the_oracle_check(case):
    r = check_case(dict(case), set_budget=True)
    assert r.ok, r.problems
    assert r.optimal <= 8
