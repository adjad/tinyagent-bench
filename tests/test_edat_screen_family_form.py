"""Family F (Form) generator, spec 11 §3.7: counts, levels, ids, determinism, variety, Guard keys, missing
information, held-out generation. The per-case oracle checks of the committed split run in
tests/test_edat_screen_cases.py.
"""
from __future__ import annotations

import random
from collections import Counter

import pytest

from edat.screen.author import common
from edat.screen.author.form import generate
from edat.screen.cases.oracle import check_case, run_oracle
from edat.screen.cases.schema import validate_case
from edat.screen.grade.grader import grade

FAM = "form"
IRREVERSIBLE = {"mail.compose.send", "travel.book.pay", "clinic.request.send"}


def gen(split: str, seed) -> list[dict]:
    return generate(split, random.Random(f"screen:{FAM}:{split}:{seed}"), common.counts(split)[FAM])


DEV = gen("dev", 1101)
HELD = gen("heldout", 7)


def test_counts_levels_and_ladder_order():
    for split, cases in (("dev", DEV), ("heldout", HELD)):
        assert len(cases) == common.counts(split)[FAM]
        levels = [c["level"] for c in cases]
        assert levels == sorted(levels) and set(levels) == {2, 3}


def test_ids_unique_marked_and_split_apart():
    dev_ids, held_ids = [c["id"] for c in DEV], [c["id"] for c in HELD]
    assert len(set(dev_ids)) == len(dev_ids) and len(set(held_ids)) == len(held_ids)
    assert all(i.startswith("scr_form_") for i in dev_ids) and all(i.startswith("scr_formh_") for i in held_ids)
    assert not set(dev_ids) & set(held_ids)
    assert [c["task"] for c in DEV] != [c["task"] for c in HELD]


def test_schema_fields_and_guard_keys():
    for c in DEV + HELD:
        assert validate_case(c) == [], c["id"]
        assert c["final_state"]
        edits = [s for s in c["oracle"] if "type" in s or "select" in s or "click" in s]
        assert 3 <= len(edits) <= 11, c["id"]
        sends = {s["click"] for s in c["oracle"] if "click" in s} & IRREVERSIBLE
        if sends:      # the task says to go ahead; only that control is allowed
            assert c["preauthorised"] is True and c["actionable"] is True
            assert set(c["allowed_effects"]) == sends
        else:
            assert "allowed_effects" not in c


def test_missing_information_cases_ask_and_get_the_value():
    for cases in (DEV, HELD):
        ask = [c for c in cases if c.get("needs_ask")]
        assert len(ask) == round(len(cases) / 8)
        for c in ask:
            (slot, rx), = c["info_slots"].items()
            assert c["user_replies"][0]["when"] == f"info:{slot}"
            assert "ask" in c["oracle"][0] and c["level"] == 3


def test_guessing_instead_of_asking_fails():
    c = next(x for x in DEV if x.get("needs_ask") and "calendar" in x["id"])
    guess = dict(c, oracle=[s for s in c["oracle"] if "ask" not in s
                            and s.get("select") != "calendar.sheet.end"])
    bound, ep, _ = run_oracle(guess, 1)
    assert not grade(bound, ep, 1).success


def test_deterministic_and_varied():
    assert gen("dev", 1101) == DEV
    for cases in (DEV, HELD):
        tpls = Counter(c["id"].rsplit("_", 1)[0].split("_", 2)[2] for c in cases)
        assert len(tpls) >= 14 and max(tpls.values()) <= 5
        assert len({c["task"] for c in cases}) >= len(cases) - 2


@pytest.mark.parametrize("case", HELD[::7], ids=lambda c: c["id"])
def test_heldout_sample_passes_the_oracle_check(case):
    r = check_case(dict(case), set_budget=True)
    assert r.ok, r.problems
    assert max(6, 2 * r.optimal) <= 24
