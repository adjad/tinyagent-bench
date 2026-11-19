"""Family G (Ground) generator, spec 11 §3.7: counts, levels, ids, determinism, variety, the held-out split.

The per-case oracle checks of the committed split run in tests/test_edat_screen_cases.py; these check the
generator's properties and a sample of `check_case` on the held-out split, which is never written.
"""
from __future__ import annotations

import random
from collections import Counter

import pytest

from edat.screen.author import common
from edat.screen.author.ground import generate
from edat.screen.cases.bind import bind
from edat.screen.cases.oracle import check_case, run_oracle
from edat.screen.cases.schema import validate_case

FAM = "ground"


def gen(split: str, seed) -> list[dict]:
    return generate(split, random.Random(f"screen:{FAM}:{split}:{seed}"), common.counts(split)[FAM])


DEV = gen("dev", 1101)
HELD = gen("heldout", 7)


def test_counts_levels_and_ladder_order():
    for split, cases in (("dev", DEV), ("heldout", HELD)):
        assert len(cases) == common.counts(split)[FAM]
        levels = [c["level"] for c in cases]
        assert levels == sorted(levels) and set(levels) == {1, 2}
        assert abs(levels.count(1) - levels.count(2)) <= 1


def test_ids_unique_marked_and_split_apart():
    dev_ids, held_ids = [c["id"] for c in DEV], [c["id"] for c in HELD]
    assert len(set(dev_ids)) == len(dev_ids) and len(set(held_ids)) == len(held_ids)
    assert all(i.startswith("scr_ground_") for i in dev_ids)
    assert all(i.startswith("scr_groundh_") for i in held_ids)
    assert not set(dev_ids) & set(held_ids)
    assert [c["task"] for c in DEV] != [c["task"] for c in HELD]


def test_schema_and_ground_keys():
    for c in DEV + HELD:
        assert validate_case(c) == [], c["id"]
        fa = c["first_action"]
        assert fa["tool"] in ("click", "select_option")
        first = c["oracle"][0]
        assert fa["target"] == (first.get("click") or first.get("select"))
        assert len(c["oracle"]) == 2 and c["oracle"][-1] == {"finish": True}     # one action, then finish
        assert c["final_state"], c["id"]
        for k in ("allowed_effects", "preauthorised", "optimal", "max_steps", "split", "core"):
            assert k not in c


def test_deterministic():
    assert gen("dev", 1101) == DEV


def test_template_variety_and_app_spread():
    for cases in (DEV, HELD):
        tpls = Counter(c["id"].rsplit("_", 1)[0].split("_", 2)[2] for c in cases)
        assert len(tpls) >= 25
        assert max(tpls.values()) <= 5
        apps = {c["start"]["app"] for c in cases}
        assert apps >= {"settings", "mail", "messages", "calendar", "notes", "files", "browser"}
        assert sum(1 for c in cases if "probe" in c["tags"]) < len(cases) * 0.6   # most targets change data


@pytest.mark.parametrize("case", [c for c in DEV if c["level"] == 2][::6], ids=lambda c: c["id"])
def test_level_two_screens_are_long_or_need_a_section(case):
    b = bind(case, 1)
    vis = b.world.observe()
    assert vis.total <= 120
    assert vis.tokens < 3000


@pytest.mark.parametrize("case", [c for c in DEV if c["level"] == 1], ids=lambda c: c["id"])
def test_level_one_screens_are_small(case):
    assert bind(case, 1).world.observe().total <= 30


def test_no_ground_case_touches_an_irreversible_control():
    for c in DEV[::3]:
        _, _, oracle = run_oracle(c, 1)
        assert oracle.bound.world.effects == [], c["id"]


def test_probe_holds_only_after_the_target():
    c = next(x for x in DEV if "probe" in x["tags"])
    b = bind(c, 2)
    assert b.world.state["probe"] == {"reached": False}
    bound, ep, _ = run_oracle(c, 2)
    assert bound.world.state["probe"]["reached"] is True


@pytest.mark.parametrize("case", HELD[::12], ids=lambda c: c["id"])
def test_heldout_sample_passes_the_oracle_check(case):
    r = check_case(dict(case), set_budget=True)
    assert r.ok, r.problems
    assert r.optimal <= 6
