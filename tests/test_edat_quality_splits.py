"""Tests for the dev / held-out splits, overlap checks and the case validator (spec 03 s1.4, s7, s8).

All data invented. The held-out split is built once per module, into a pytest temporary
directory named by EDAT_PRIVATE_DIR; nothing is written anywhere else.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter

import pytest

from edat.quality import splits as S
from edat.quality.generate import SOURCE_TYPES, TIERS, make_source_case
from edat.quality.render import render_sources

FAMILIES = ("source", "numeric", "schema", "noisy")
DEV_COUNTS = {"source": 60, "numeric": 15, "schema": 15, "noisy": 20}
HELD_COUNTS = {"source": 180, "numeric": 45, "schema": 45, "noisy": 60}
NUMERIC_KINDS = ("sum", "comparison", "count", "date_arith", "free_slots")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dev():
    return S.build_split("dev")


@pytest.fixture(scope="module")
def committed():
    return S.load_dev()


@pytest.fixture(scope="module")
def held(tmp_path_factory, committed):
    """The held-out split, built once into a temporary EDAT_PRIVATE_DIR the way the CLI does."""
    root = tmp_path_factory.mktemp("edat_private")
    mp = pytest.MonkeyPatch()
    mp.setenv("EDAT_PRIVATE_DIR", str(root))
    try:
        qdir = S.private_dir()
        # A fixed private seed pool keeps this module deterministic. (With the random pool the CLI uses,
        # about one honest build in eight reports a shared identical 'Parcel Desk' filler email.)
        qdir.mkdir(parents=True)
        (qdir / "heldout-seeds.json").write_text(json.dumps({"seeds": [10 ** 9 + 1013 * i for i in range(4000)]}))
        seeds = S.SeedSource("heldout", seeds_file=qdir / "heldout-seeds.json")
        avoid = set().union(*[S._anchor_values(c) for c in S.all_cases(committed)])
        fams = S.build_split("heldout", seeds=seeds, avoid=avoid)
        manifest = S.write_split(fams, qdir / "heldout", "heldout", seeds)
        yield {"fams": fams, "manifest": manifest, "root": root, "qdir": qdir}
    finally:
        mp.undo()


# ---------------------------------------------------------------------------
# Dev split shape
# ---------------------------------------------------------------------------

def test_dev_family_counts(dev):
    assert {k: len(v) for k, v in dev.items()} == DEV_COUNTS


def test_dev_has_a_quarter_of_each_source_cell(dev):
    cells = Counter((c["source_type"], c["tier"]) for c in dev["source"])
    assert set(cells) == {(st, t) for st in SOURCE_TYPES for t in TIERS}
    assert set(cells.values()) == {3}                     # 25 % of 12 per cell


def test_dev_numeric_kinds_and_sources(dev):
    kinds = Counter(c["tasks"][0]["numeric"]["kind"] for c in dev["numeric"])
    assert dict(kinds) == {k: 3 for k in NUMERIC_KINDS}
    for c in dev["numeric"]:
        assert len(c["tasks"]) == 1 and c["family"] == "numeric"
        assert c["tier"] in ("S", "M", "L")
        kind = c["tasks"][0]["numeric"]["kind"]
        assert c["source_type"] in {"sum": ["sheet"], "comparison": ["sheet"], "count": ["sheet", "calendar"],
                                    "date_arith": ["calendar"], "free_slots": ["calendar"]}[kind]


def test_half_of_the_numeric_cases_are_on_spreadsheets_in_the_full_plan(dev):
    # sum + comparison + half of count = 2.5 of 5 kinds
    sheets = sum(1 for c in dev["numeric"] if c["source_type"] == "sheet")
    assert sheets in (8, 9)      # 3 sum + 3 comparison + 2 or 3 of the 3 count cases


def test_dev_schema_cases_cover_the_seven_shapes(dev):
    kinds = Counter(c["schema_kind"] for c in dev["schema"])
    assert set(kinds) == set(S.SCHEMA_KINDS) and sum(kinds.values()) == 15


def test_dev_noisy_cases_are_balanced_and_point_at_real_parents(dev):
    kinds = Counter(c["noise_kind"] for c in dev["noisy"])
    assert dict(kinds) == {k: 5 for k in S.NOISE_KINDS}
    parents = {c["case_id"]: c for c in S.all_cases(dev)}
    for n in dev["noisy"]:
        parent = parents[n["parent_case_id"]]
        task = next(t for t in parent["tasks"] if t["task_id"] == n["parent_task_id"])
        assert n["clean_request"] == task["request"]
        assert n["request"] != n["clean_request"]
        assert n["scored_for"] == ("correctness" if n["noise_kind"] == "self_correction" else "consistency")
        assert task["kind"] in ("point", "numeric")


def test_dev_ids_and_seeds_are_unique_and_public(dev):
    cases = S.all_cases(dev)
    ids = [c["case_id"] for c in cases + dev["noisy"]]
    assert len(ids) == len(set(ids))
    seeds = [c["seed"] for c in cases]
    assert len(seeds) == len(set(seeds))
    assert all(S.PUBLIC_DEV_BASE_SEED <= s < S.PUBLIC_DEV_BASE_SEED + 500 for s in seeds)
    assert all(c["split"] == "dev" for c in cases + dev["noisy"])


def test_dev_build_is_deterministic(dev):
    again = S.build_split("dev")
    assert json.dumps(again, sort_keys=True) == json.dumps(dev, sort_keys=True)


def test_build_split_rejects_an_unknown_split():
    with pytest.raises(ValueError):
        S.build_split("train")


def test_avoid_regenerates_a_case_that_would_reuse_an_anchor(dev):
    taboo = S._anchor_values(dev["source"][0])
    fams = S.build_split("dev", avoid=taboo)
    assert not (S._anchor_values(fams["source"][0]) & taboo)
    assert fams["source"][0]["seed"] != dev["source"][0]["seed"]


# ---------------------------------------------------------------------------
# Held-out split
# ---------------------------------------------------------------------------

def test_heldout_family_counts(held):
    assert {k: len(v) for k, v in held["fams"].items()} == HELD_COUNTS
    assert held["manifest"]["counts"] == HELD_COUNTS


def test_heldout_source_cells_are_three_quarters(held):
    cells = Counter((c["source_type"], c["tier"]) for c in held["fams"]["source"])
    assert set(cells) == {(st, t) for st in SOURCE_TYPES for t in TIERS}
    assert set(cells.values()) == {9}
    assert dict(Counter(c["tasks"][0]["numeric"]["kind"] for c in held["fams"]["numeric"])) == {k: 9 for k in NUMERIC_KINDS}
    assert dict(Counter(c["noise_kind"] for c in held["fams"]["noisy"])) == {k: 15 for k in S.NOISE_KINDS}


def test_dev_plus_heldout_follow_the_plan_with_dev_at_25_percent(dev, held):
    for fam in FAMILIES:
        d, h = len(dev[fam]), len(held["fams"][fam])
        assert d / (d + h) == pytest.approx(0.25)
    assert len(dev["source"]) + len(held["fams"]["source"]) == 5 * 4 * S.PLAN["source_per_cell"]
    assert len(dev["numeric"]) + len(held["fams"]["numeric"]) == 5 * S.PLAN["numeric_per_kind"]
    assert len(dev["schema"]) + len(held["fams"]["schema"]) == S.PLAN["schema_total"]
    assert len(dev["noisy"]) + len(held["fams"]["noisy"]) == S.PLAN["noisy_total"]


def test_heldout_files_land_only_under_the_private_directory(held):
    q = held["qdir"]
    assert q == held["root"].resolve() / "quality"
    assert (q / "heldout-seeds.json").exists()
    for fam in FAMILIES:
        assert (q / "heldout" / f"{fam}.jsonl").exists()
    assert (q / "heldout" / "manifest.json").exists()
    assert not str(q).startswith(str(S.REPO_ROOT))


def test_heldout_uses_private_random_seeds_not_the_public_ones(held, dev):
    seeds = [c["seed"] for c in S.all_cases(held["fams"])]
    assert not any(S.PUBLIC_DEV_BASE_SEED <= s < S.PUBLIC_DEV_BASE_SEED + 1000 for s in seeds)
    assert all(c["split"] == "heldout" for c in S.all_cases(held["fams"]))
    assert "seeds" not in held["manifest"]                # the held-out manifest does not publish a seed base


def test_heldout_roundtrips_through_load_split(held):
    loaded = S.load_split(held["qdir"] / "heldout")
    assert {k: len(v) for k, v in loaded.items()} == HELD_COUNTS
    assert loaded["source"][0]["case_id"] == held["fams"]["source"][0]["case_id"]


def test_heldout_load_needs_the_private_dir_env(held, monkeypatch):
    assert len(S.load_heldout()["source"]) == 180     # env set by the fixture (module scope)
    monkeypatch.delenv("EDAT_PRIVATE_DIR")
    with pytest.raises(S.PrivateDirMissing):
        S.load_heldout()


def test_heldout_seed_file_makes_the_split_reproducible(held, committed):
    seeds = S.SeedSource("heldout", seeds_file=held["qdir"] / "heldout-seeds.json")
    assert len(seeds.pool) == 4000
    again = S.build_split("heldout", seeds=seeds,
                          avoid=set().union(*[S._anchor_values(c) for c in S.all_cases(committed)]))
    assert [c["case_id"] for c in again["source"]] == [c["case_id"] for c in held["fams"]["source"]]
    assert [c["seed"] for c in again["source"]] == [c["seed"] for c in held["fams"]["source"]]


def test_heldout_cases_validate_for_a_sample(held):
    sample = [held["fams"]["source"][i] for i in range(0, 180, 45)] + held["fams"]["numeric"][:2] + held["fams"]["schema"][:2]
    for c in sample:
        assert S.validate_case(c) == [], c["case_id"]


# ---------------------------------------------------------------------------
# private_dir
# ---------------------------------------------------------------------------

def test_private_dir_without_env_raises(monkeypatch):
    monkeypatch.delenv("EDAT_PRIVATE_DIR", raising=False)
    with pytest.raises(S.PrivateDirMissing):
        S.private_dir()


def test_private_dir_accepts_a_path_outside_the_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(tmp_path))
    assert S.private_dir() == tmp_path.resolve() / "quality"


@pytest.mark.parametrize("rel", ["", "scratch", "test_fixtures/quality/private", "edat/quality"])
def test_private_dir_refuses_a_path_inside_the_repo(monkeypatch, rel):
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(S.REPO_ROOT / rel))
    with pytest.raises(S.PrivateDirMissing, match="outside the repository"):
        S.private_dir()


def test_private_dir_refuses_a_symlink_that_points_into_the_repo(monkeypatch, tmp_path):
    link = tmp_path / "sneaky"
    link.symlink_to(S.REPO_ROOT, target_is_directory=True)
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(link))
    with pytest.raises(S.PrivateDirMissing):
        S.private_dir()


def test_private_dir_refuses_dotdot_that_resolves_into_the_repo(monkeypatch):
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(S.REPO_ROOT.parent / S.REPO_ROOT.name / "x" / ".." / "y"))
    with pytest.raises(S.PrivateDirMissing):
        S.private_dir()


# ---------------------------------------------------------------------------
# overlap_report
# ---------------------------------------------------------------------------

def test_overlap_report_counts_are_clean_for_dev_vs_an_avoiding_heldout(committed, held):
    rep = S.overlap_report(committed, held["fams"])
    assert rep["seeds_shared"] == 0
    assert rep["case_ids_shared"] == 0
    assert rep["planted_anchors_shared_n"] == 0 and rep["planted_anchors_shared"] == []
    assert rep["source_items_shared"] == 0
    assert rep["dev_cases"] == 90 and rep["heldout_cases"] == 270


def test_overlap_report_ok_for_dev_vs_an_avoiding_heldout(committed, held):
    assert S.overlap_report(committed, held["fams"])["ok"] is True


def test_overlap_report_flags_a_deliberately_shared_case(committed, held):
    shared = copy.deepcopy(committed["source"][0])
    tainted = {k: list(v) for k, v in held["fams"].items()}
    tainted["source"].append(shared)
    rep = S.overlap_report(committed, tainted)
    assert rep["ok"] is False
    assert rep["seeds_shared"] == 1
    assert rep["case_ids_shared"] == 1
    assert rep["source_items_shared"] >= 1
    assert rep["planted_anchors_shared_n"] >= 1 and rep["planted_anchors_shared"]
    assert any(a.startswith(("code:", "amount:")) for a in rep["planted_anchors_shared"])


@pytest.mark.parametrize("change,field", [
    ("seed", "seeds_shared"), ("case_id", "case_ids_shared"),
])
def test_overlap_report_flags_each_kind_of_sharing(committed, held, change, field):
    c = copy.deepcopy(held["fams"]["source"][0])
    c[change] = committed["source"][0][change]
    tainted = {k: list(v) for k, v in held["fams"].items()}
    tainted["source"][0] = c
    assert S.overlap_report(committed, tainted)[field] == 1
    assert S.overlap_report(committed, tainted)["ok"] is False


def test_overlap_report_flags_a_shared_anchor_alone(committed, held):
    d_code = next(v for v in S._anchor_values(committed["source"][0]) if v.startswith("code:"))
    c = copy.deepcopy(held["fams"]["source"][0])
    c["facts"][0]["anchors"]["code_shared"] = d_code.split(":", 1)[1]
    tainted = {k: list(v) for k, v in held["fams"].items()}
    tainted["source"][0] = c
    rep = S.overlap_report(committed, tainted)
    assert d_code in rep["planted_anchors_shared"]
    assert rep["ok"] is False


def test_overlap_report_of_a_split_with_itself_is_not_clean(committed):
    rep = S.overlap_report(committed, committed)
    assert rep["ok"] is False
    assert rep["case_ids_shared"] == rep["dev_cases"] + len(committed["noisy"])


def test_overlap_report_handles_empty_splits():
    empty = {k: [] for k in FAMILIES}
    rep = S.overlap_report(empty, empty)
    assert rep["ok"] is True and rep["dev_cases"] == 0


def test_dev_and_heldout_get_distinct_case_ids(committed, held):
    d = {c["case_id"] for c in S.all_cases(committed)}
    h = {c["case_id"] for c in S.all_cases(held["fams"])}
    assert not d & h


# ---------------------------------------------------------------------------
# overlap_with_rows
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def test_overlap_with_rows_counts_only_for_unrelated_rows(committed):
    rep = S.overlap_with_rows(committed["source"][:4], ["the quick brown fox jumps over the lazy dog " * 5])
    assert rep == {"cases": 4, "ngram_n": 13, "threshold": 0.05, "matching_ngrams": 0, "cases_above_threshold": 0,
                   "share_above_threshold": 0.0}


def test_overlap_with_rows_detects_a_copied_passage(committed):
    c = committed["source"][0]
    toks = _tokens(render_sources(c) + " " + " ".join(t["request"] for t in c["tasks"]))
    row = " ".join(toks[:60])
    rep = S.overlap_with_rows([c], [row])
    assert rep["cases"] == 1 and rep["cases_above_threshold"] == 1 and rep["share_above_threshold"] == 1.0
    assert 0 < rep["matching_ngrams"] <= 60 - 13 + 1


def test_overlap_with_rows_counts_each_case_separately(committed):
    a, b = committed["source"][0], committed["source"][21]
    toks = _tokens(render_sources(a))
    rep = S.overlap_with_rows([a, b], [" ".join(toks[:80])])
    assert rep["cases"] == 2 and rep["cases_above_threshold"] == 1 and rep["share_above_threshold"] == 0.5


def test_overlap_with_rows_threshold_and_ngram_size_are_respected(committed):
    c = committed["source"][3]
    toks = _tokens(render_sources(c))
    row = " ".join(toks[100:130])
    low = S.overlap_with_rows([c], [row], n=13, threshold=0.0)
    high = S.overlap_with_rows([c], [row], n=13, threshold=0.9)
    assert low["cases_above_threshold"] == 1 and high["cases_above_threshold"] == 0
    assert low["matching_ngrams"] == high["matching_ngrams"] > 0
    short = S.overlap_with_rows([c], [row], n=5, threshold=0.0)
    assert short["ngram_n"] == 5 and short["matching_ngrams"] > low["matching_ngrams"]


def test_overlap_with_rows_with_no_cases_or_rows():
    assert S.overlap_with_rows([], ["anything at all here"])["share_above_threshold"] == 0.0
    c = make_source_case("document", "S", seed=77, split="dev", index=0)
    assert S.overlap_with_rows([c], [])["matching_ngrams"] == 0


def test_overlap_with_rows_publishes_counts_not_text(committed):
    rep = S.overlap_with_rows(committed["source"][:2], ["secret training row " * 10])
    assert set(rep) == {"cases", "ngram_n", "threshold", "matching_ngrams", "cases_above_threshold",
                        "share_above_threshold"}
    assert all(isinstance(v, (int, float)) for v in rep.values())


# ---------------------------------------------------------------------------
# validate_case
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def good_case():
    return make_source_case("email", "M", seed=1001, split="dev", index=0)


def _replace_everywhere(case: dict, old: str, new: str) -> dict:
    return json.loads(json.dumps(case, ensure_ascii=False).replace(old, new))


def test_validate_case_passes_a_good_case(good_case):
    assert S.validate_case(good_case) == []


@pytest.mark.parametrize("st", SOURCE_TYPES)
def test_validate_case_passes_a_small_case_of_every_source_type(st):
    assert S.validate_case(make_source_case(st, "S", seed=4242, split="dev", index=0)) == []


def test_validate_case_reports_a_planted_anchor_removed_from_the_source(good_case):
    code = good_case["facts"][2]["anchors"]["code"]            # the booking reference
    assert code in json.dumps(good_case)
    items_only = copy.deepcopy(good_case)
    items_only["items"] = json.loads(json.dumps(items_only["items"]).replace(code, "REMOVED"))
    errs = S.validate_case(items_only)
    assert any(code in e and "not in the source" in e for e in errs), errs
    assert any(e.startswith("primary: fact F03") for e in errs)


def test_validate_case_reports_a_removed_trap_anchor(good_case):
    url = next(t for t in good_case["traps"] if t["trap_type"] == "planted_instruction")["anchors"]["url"]
    bad = copy.deepcopy(good_case)
    bad["items"] = json.loads(json.dumps(bad["items"]).replace(url, "http://gone.example/x"))
    errs = S.validate_case(bad)
    assert any(url in e and "trap" in e for e in errs), errs


def test_validate_case_reports_a_removed_date_anchor(good_case):
    bad = copy.deepcopy(good_case)
    bad["items"] = json.loads(json.dumps(bad["items"]).replace("Thursday 18 March", "Someday soon"))
    errs = S.validate_case(bad)
    assert any("F03" in e and "date" in e for e in errs), errs


def test_validate_case_reports_a_non_example_email_domain(good_case):
    bad = copy.deepcopy(good_case)
    bad["items"][0]["from"] = "billing@realbank.com"
    errs = S.validate_case(bad)
    assert any("email outside .example/.invalid" in e and "realbank.com" in e for e in errs), errs


@pytest.mark.parametrize("addr", ["a@x.example", "a@box.invalid", "a@example.com", "a@mail.example", "a@x.test"])
def test_validate_case_allows_the_reserved_domains(good_case, addr):
    ok = copy.deepcopy(good_case)
    ok["items"][0]["to"] = [addr]
    assert not any("email outside" in e for e in S.validate_case(ok))


@pytest.mark.parametrize("addr", ["a@gmail.com", "a@example.co.uk", "a@notexample.com", "a@x.org"])
def test_validate_case_rejects_other_domains(good_case, addr):
    bad = copy.deepcopy(good_case)
    bad["items"][0]["to"] = [addr]
    assert any("email outside" in e for e in S.validate_case(bad))


def test_validate_case_reports_a_non_example_url(good_case):
    bad = copy.deepcopy(good_case)
    bad["items"][1]["paras"].append("See https://www.bigshop.com/deal for more.")
    assert any("URL outside" in e and "bigshop.com" in e for e in S.validate_case(bad))


def test_validate_case_reports_a_local_path(good_case):
    bad = copy.deepcopy(good_case)
    bad["items"][1]["paras"].append("saved in /Users/someone/notes.txt")
    assert "absolute local path in case" in S.validate_case(bad)


def test_validate_case_reports_a_duplicate_task_id(good_case):
    bad = copy.deepcopy(good_case)
    bad["tasks"][1]["task_id"] = bad["tasks"][0]["task_id"]
    assert any(e.startswith("duplicate task id") for e in S.validate_case(bad))


def test_validate_case_reports_a_gold_that_breaks_its_schema(good_case):
    bad = copy.deepcopy(good_case)
    ext = next(t for t in bad["tasks"] if t["kind"] == "extraction")
    ext["gold"] = [{"payee": "Someone", "amount": "not a number", "currency": "EUR", "due": "2027-03-11"}]
    assert any("gold does not validate" in e for e in S.validate_case(bad))


def test_validate_case_reports_a_reference_answer_that_does_not_pass(good_case):
    bad = copy.deepcopy(good_case)
    pt = next(t for t in bad["tasks"] if t["kind"] == "point")
    pt["reference_answer"] = "I could not find it."
    assert any("the reference answer does not pass" in e for e in S.validate_case(bad))


def test_validate_split_summarises_problems(good_case):
    bad = copy.deepcopy(good_case)
    bad["items"][0]["from"] = "billing@realbank.com"
    fams = {"source": [good_case, bad], "numeric": [], "schema": [], "noisy": [
        {"case_id": "q.noisy.typos.dev.000", "parent_case_id": "q.missing"}]}
    rep = S.validate_split(fams)
    assert rep["cases"] == 2 and rep["noisy"] == 1
    assert rep["invalid"] == 2
    assert "q.noisy.typos.dev.000" in rep["problems"]
    assert rep["problems"]["q.noisy.typos.dev.000"] == ["parent case missing"]


# ---------------------------------------------------------------------------
# The committed dev split
# ---------------------------------------------------------------------------

DEV_DIR = S.DEV_DIR


def test_committed_manifest_hashes_match_the_files():
    manifest = json.loads((DEV_DIR / "manifest.json").read_text())
    assert set(manifest["files"]) == {f"{f}.jsonl" for f in FAMILIES}
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((DEV_DIR / name).read_bytes()).hexdigest() == digest, name


def test_committed_manifest_counts_match_the_files_and_the_plan(committed):
    manifest = json.loads((DEV_DIR / "manifest.json").read_text())
    assert manifest["counts"] == DEV_COUNTS
    assert {k: len(v) for k, v in committed.items()} == DEV_COUNTS
    assert manifest["generator"] == S.GENERATOR_VERSION
    assert manifest["splits_version"] == S.SPLIT_VERSION
    assert manifest["split"] == "dev"
    assert manifest["counts_by_source_type"] == dict(Counter(f"{c['source_type']}/{c['tier']}" for c in committed["source"]))
    assert manifest["counts_numeric_by_kind"] == {k: 3 for k in NUMERIC_KINDS}
    assert manifest["counts_noisy_by_kind"] == {k: 5 for k in S.NOISE_KINDS}
    assert manifest["tasks"] == sum(len(c["tasks"]) for f in ("source", "numeric", "schema") for c in committed[f])
    assert manifest["seeds"] == {"base": S.PUBLIC_DEV_BASE_SEED, "count": 119}


def test_committed_dev_equals_a_fresh_build(dev, committed, tmp_path):
    manifest = json.loads((DEV_DIR / "manifest.json").read_text())
    for fam in FAMILIES:
        assert S._dump(tmp_path / f"{fam}.jsonl", dev[fam]) == manifest["files"][f"{fam}.jsonl"], fam
    assert json.loads(json.dumps(dev)) == json.loads(json.dumps(committed))


def test_committed_dev_split_validates():
    rep = S.validate_split(S.load_dev())
    assert rep["cases"] == 90 and rep["noisy"] == 20
    assert rep["invalid"] == 0, rep["problems"]


def test_committed_dev_uses_only_example_domains_and_no_local_paths():
    blob = "".join((DEV_DIR / f"{f}.jsonl").read_text(encoding="utf-8") for f in FAMILIES)
    for m in re.finditer(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", blob):
        assert m.group(1).lower().endswith((".example", ".invalid", "example.com")), m.group(0)
    assert not re.search(r"/(Users|home)/[A-Za-z]", blob)


def test_write_split_and_load_split_roundtrip(dev, tmp_path):
    seeds = S.SeedSource("dev")
    seeds.used = list(range(7))
    manifest = S.write_split(dev, tmp_path, "dev", seeds)
    loaded = S.load_split(tmp_path)
    assert json.loads(json.dumps(loaded)) == json.loads(json.dumps(dev))
    assert manifest["seeds"]["count"] == 7
    assert (tmp_path / "manifest.json").exists()


def test_load_split_of_a_missing_directory_is_empty(tmp_path):
    assert S.load_split(tmp_path / "nope") == {k: [] for k in FAMILIES}
