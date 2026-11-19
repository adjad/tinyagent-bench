"""The case pipeline end to end on both real lanes: generate, bind, oracle, null model."""
from __future__ import annotations

import random

from edat.cases.generators import _example, case_id
from edat.cases.oracle import check_case
from edat.grading import pref_checks


def test_example_generator_cases_pass_the_oracle_and_fail_the_null_model():
    cases = _example.generate("dev", random.Random(1))
    assert {c["lane"] for c in cases} == {"assistant", "neutral"}
    for c in cases:
        res = check_case(c)
        assert res.ok, res.problems


def test_heldout_ids_differ_from_dev_ids():
    assert case_id("sel", "assistant", "dev", "x", 1) == "sel_ast_x_001"
    assert case_id("sel", "assistant", "heldout", "x", 1) == "selh_ast_x_001"


def test_preference_seam_stub_checks():
    assert pref_checks.check(6, "See you at 3pm") is not None
    assert pref_checks.check(6, "See you at 15:00") is None
    assert pref_checks.check(9, "Thanks.\nBest, Ines", sign_off="Best, Ines") is None
    assert pref_checks.check_items([4, 5], "| a | b |\n| - | - |") != []
