"""The committed development split (spec 11 §3.11, S5): counts, schema, solvability, the do-nothing check.

Every case must be solved by its oracle within its step limit on world seeds
1-3, and fail under a model that does nothing and under one that calls
`finish` at once. The held-out split is generated from the same code with a
private seed and is checked by the build where it is written, never here.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from edat.screen.author import common
from edat.screen.cases.bind import max_steps_for
from edat.screen.cases.oracle import check_case
from edat.screen.cases.schema import LEVELS, validate_case
from edat.screen.cases.store import load_cases

DEV = Path(__file__).resolve().parents[1] / "test_fixtures" / "screen" / "dev"
COUNTS = DEV.parent / "counts.json"
ALL = load_cases(DEV) if DEV.exists() else []
CASES = [c for c in ALL if c["family"] != "pixel"]
PIXEL = [c for c in ALL if c["family"] == "pixel"]
pytestmark = pytest.mark.skipif(not CASES, reason="development split not generated")


def test_counts_per_family_and_guard_kind_match_the_spec():
    by = Counter(c["family"] for c in CASES)
    assert dict(by) == common.DEV
    kinds = Counter(c["guard_kind"] for c in CASES if c["family"] == "guard")
    assert dict(kinds) == common.GUARD_DEV
    core = Counter(c["family"] for c in CASES if c.get("core"))
    assert dict(core) == common.DEV_CORE
    gcore = Counter(c["guard_kind"] for c in CASES if c["family"] == "guard" and c.get("core"))
    assert dict(gcore) == common.GUARD_DEV_CORE
    assert all(c["split"] == "dev" for c in CASES)


def test_levels_stay_in_each_family_range_and_the_ladder_is_covered():
    for c in CASES:
        lo, hi = LEVELS[c["family"]]
        assert lo <= c["level"] <= hi, c["id"]
    for fam, (lo, hi) in LEVELS.items():
        if fam == "pixel":
            continue
        seen = {c["level"] for c in CASES if c["family"] == fam}
        assert seen == set(range(lo, hi + 1)), (fam, seen)


def test_schema_ids_budgets_and_tracks():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for c in CASES:
        assert validate_case(c) == [], (c["id"], validate_case(c))
        assert c["max_steps"] == max_steps_for(c["optimal"]["steps"]) <= 24
        assert "ax_text" in c["tracks"]
    shot = [c for c in CASES if "screenshot_som" in c["tracks"]]
    assert sum(1 for c in shot if c.get("core")) == (sum(1 for c in CASES if c.get("core")) + 1) // 2


def test_injection_cases_come_in_attacked_and_clean_pairs():
    inj = [c for c in CASES if c.get("guard_kind") == "injection"]
    attacked = {c["id"]: c for c in inj if c.get("inject")}
    clean = [c for c in inj if c.get("twin_of")]
    assert len(attacked) == len(clean) == len(inj) // 2
    for c in clean:
        twin = attacked[c["twin_of"]]
        assert c["task"] == twin["task"] and c.get("core") == twin.get("core")


def test_counts_file_matches_the_cases():
    data = json.loads(COUNTS.read_text())
    dev = data["splits"]["dev"]
    assert dev["_total"] == len(CASES) == sum(common.DEV.values())
    for fam, n in common.DEV.items():
        assert dev[fam]["total"] == n


def test_pixel_diagnostic_items():
    assert len(PIXEL) == common.PIXEL_SPLIT
    assert all(c["tracks"] == ["screenshot_raw"] and c["first_action"]["tool"] == "click" for c in PIXEL)
    assert {c["level"] for c in PIXEL} == {1, 2}


@pytest.mark.parametrize("case", ALL, ids=[c["id"] for c in ALL])
def test_every_case_is_solvable_and_not_passable_by_doing_nothing(case):
    r = check_case(case)
    assert r.ok, r.problems
    assert r.optimal <= case["optimal"]["steps"]
