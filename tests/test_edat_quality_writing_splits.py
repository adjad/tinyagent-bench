"""The Writing family's splits: counts, validator, reproducibility and the overlap check.

The held-out split is built into a temporary EDAT_PRIVATE_DIR here, from a fixed base seed, so the
test is repeatable and never touches the real private folder.
"""
from __future__ import annotations

import copy
import json

import pytest

from edat.quality import splits as S
from edat.quality import writing as W
from edat.quality.writing_checks import score_writing


@pytest.fixture(scope="module")
def dev():
    return S.load_writing("dev")


@pytest.fixture(scope="module")
def held(tmp_path_factory):
    root = tmp_path_factory.mktemp("private")
    mp = pytest.MonkeyPatch()
    mp.setenv("EDAT_PRIVATE_DIR", str(root))
    (root / "quality").mkdir()
    (root / "quality" / "heldout-writing-seed.json").write_text(json.dumps({"base": 1_234_567_891}))
    cases = S.build_writing("heldout")
    S.write_writing(cases, "heldout")
    yield {"cases": cases, "root": root}
    mp.undo()


def test_plan_totals_match_the_spec():
    assert W.TOTAL_CASES == 342
    sub_totals = {}
    for sub, _, n, _ in W.PLAN:
        sub_totals[sub] = sub_totals.get(sub, 0) + n
    assert sub_totals == {"W1": 70, "W2": 48, "W3": 60, "W4": 40, "W5": 54, "W6": 40, "W7": 30}


def test_dev_split_counts_and_manifest(dev):
    counts = W.split_counts()
    want = {k: v["dev"] for k, v in counts.items()}
    got = {}
    for c in dev:
        got[f"{c['sub']}/{c['kind']}"] = got.get(f"{c['sub']}/{c['kind']}", 0) + 1
    assert got == want and len(dev) == 89
    man = json.loads((S.WRITING_DEV_DIR / "manifest.json").read_text())
    assert man["counts"]["cases"] == 89 and man["seeds"]["base"] == S.PUBLIC_WRITING_DEV_BASE


def test_dev_split_regenerates_identically(dev):
    again = W.build_writing_split("dev", S.PUBLIC_WRITING_DEV_BASE)
    assert [json.dumps(c, sort_keys=True) for c in again] == [json.dumps(c, sort_keys=True) for c in dev]


def test_every_dev_case_is_valid(dev):
    rep = S.validate_writing_split(dev)
    assert rep["invalid"] == 0, list(rep["problems"].items())[:3]


def test_profiles_bind_about_half_of_w1_w2_w5_w6_and_all_three_appear(dev, held):
    for cases in (dev, held["cases"]):
        bound = [c for c in cases if c["sub"] in ("W1", "W2", "W5", "W6")]
        share = sum(1 for c in bound if c["profile"]) / len(bound)
        assert 0.3 <= share <= 0.7, share
        assert {c["profile"] for c in cases if c["profile"]} == {"student", "tech", "mixed"}
        assert not any(c["profile"] for c in cases if c["sub"] in ("W3", "W4", "W7"))


def test_w6_keeps_whole_groups_of_four_audiences(dev, held):
    for cases in (dev, held["cases"]):
        groups = {}
        for c in cases:
            if c["sub"] == "W6":
                groups.setdefault(c["group_id"], set()).add(c["audience"])
        assert all(v == set(W.AUDIENCES) for v in groups.values())
    assert len({c["group_id"] for c in dev if c["sub"] == "W6"}) == 3
    assert len({c["group_id"] for c in held["cases"] if c["sub"] == "W6"}) == 7


def test_heldout_split_counts_validity_and_location(held):
    cases = held["cases"]
    assert len(cases) == 253
    assert S.validate_writing_split(cases)["invalid"] == 0
    assert (held["root"] / "quality" / "heldout" / "writing" / "writing.jsonl").exists()
    assert sum(len(c["turns"]) - 1 for c in cases) == 6       # outline cases: 6 held-out + 2 dev = 8


def test_overlap_is_clean_and_catches_a_shared_case(dev, held):
    rep = S.writing_overlap_report(dev, held["cases"])
    assert rep["ok"], rep
    tainted = held["cases"] + [copy.deepcopy(dev[0])]
    bad = S.writing_overlap_report(dev, tainted)
    assert not bad["ok"] and bad["seeds_shared"] == 1 and bad["case_ids_shared"] == 1


def test_validator_rejects_a_broken_case(dev):
    c = copy.deepcopy(next(x for x in dev if x["sub"] == "W1" and x["kind"] == "email"))
    c["reference_draft"] = c["reference_draft"] + "\n\nLet me know if you need anything else!"
    assert any("reference draft fails" in e for e in S.validate_writing_case(c))
    c = copy.deepcopy(next(x for x in dev if x["must_not"]))
    f = c["must_not"][0]
    f["stated"] = False
    anchor = next(a for a in f["anchors"] if isinstance(a, str))
    c["turns"] = [c["turns"][0] + f"\nMention {anchor}."]
    assert any("unstated forbidden" in e for e in S.validate_writing_case(c))


def test_empty_and_off_brief_drafts_score_zero_on_every_dev_case(dev):
    for i, c in enumerate(dev):
        assert score_writing("", c)["score"] == 0, c["case_id"]
