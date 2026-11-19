"""Writing generators for W3 (rewriting) and W4 (proofreading): contract, determinism, planted facts and errors."""
from __future__ import annotations

import re

import pytest

from edat.quality import hygiene, normalize as N, writing as W
from edat.quality import writing_gen_edit as G

KINDS = [(s, k) for s, k, _, _ in W.PLAN if s in ("W3", "W4")]
SEEDS = list(range(1, 13))


def _cases(sub, kind, seeds=SEEDS):
    return [G.GENERATORS[(sub, kind)](s, index=i, profile=None) for i, s in enumerate(seeds)]


def _words(t):
    return len(t.split())


def _plain_words(t):
    return len(re.sub(r"^- ", "", t, flags=re.M).split())


def _present(text, anchor, year):
    t, v, al = N.parse_anchor(anchor)
    return N.anchor_present(text, t, v, aliases=al, case_year=year)


def test_every_plan_kind_has_a_generator():
    assert set(G.GENERATORS) == set(KINDS)


@pytest.mark.parametrize("sub,kind", KINDS)
def test_contract_and_determinism(sub, kind):
    for i, s in enumerate(SEEDS[:4]):
        a = G.GENERATORS[(sub, kind)](s, index=i, profile=None)
        b = G.GENERATORS[(sub, kind)](s, index=i, profile=None)
        assert a == b
        assert W.brief_errors(a) == []
        assert a["generator"] == "writing_gen_edit/1" and a["profile"] is None
        assert a["sub"] == sub and a["kind"] == kind
        assert a["context"]["source_text"] in a["turns"][0]


@pytest.mark.parametrize("sub,kind", KINDS)
def test_cases_are_pairwise_distinct(sub, kind):
    cs = _cases(sub, kind)
    assert len({c["context"]["source_text"] for c in cs}) == len(cs)
    assert len({c["case_id"] for c in cs}) == len(cs)
    assert len({c["reference_draft"] for c in cs}) == len(cs)


@pytest.mark.parametrize("kind", [k for s, k in KINDS if s == "W3"])
def test_w3_reference_keeps_the_facts(kind):
    for c in _cases("W3", kind):
        src, ref, year = c["context"]["source_text"], c["reference_draft"], c["case_year"]
        assert 120 <= _plain_words(src) <= 260
        anchored = [f for f in c["facts"] if f["anchors"]]
        assert len(anchored) >= 3 and len(anchored) == len(c["facts"])
        for f in c["facts"]:
            for a in f["anchors"]:
                assert _present(src, a, year), (f, "source")
        required = [f for f in c["facts"] if f["core"]] if kind == "shorten" else c["facts"]
        assert required
        for f in required:
            for a in f["anchors"]:
                assert _present(ref, a, year), (kind, f, ref)
        # nothing invented: every atom of the reference is in the source or the request
        known = N.atom_keys(src + "\n" + c["turns"][0] + "\n" + c["allowed_text"])
        invented = [a.surface for a in N.find_atoms(ref) if N.atom_key(a, year) not in known]
        assert invented == [], (kind, invented)
        cons = c["constraints"]
        n_ref = _plain_words(ref)
        if cons["max_words"]:
            assert n_ref <= cons["max_words"]
        if cons["min_words"]:
            assert n_ref >= cons["min_words"]


def test_w3_kind_specifics():
    for c in _cases("W3", "shorten"):
        assert 3 <= sum(f["core"] for f in c["facts"]) <= 5
        assert c["constraints"]["max_words"] <= 0.5 * _plain_words(c["context"]["source_text"])
        assert str(c["constraints"]["max_words"]) in c["turns"][0]
    for c in _cases("W3", "expand"):
        n = _plain_words(c["context"]["source_text"])
        assert c["constraints"]["min_words"] > n and c["constraints"]["max_words"] > c["constraints"]["min_words"]
    for c in _cases("W3", "to_bullets"):
        assert c["target_shape"] == "bullets" and hygiene.classify_shape(c["reference_draft"]) in ("bullets", "numbered")
    for c in _cases("W3", "to_prose"):
        assert c["target_shape"] == "paragraphs" and hygiene.classify_shape(c["reference_draft"]) == "paragraphs"
        assert hygiene.classify_shape(c["context"]["source_text"]) in ("bullets", "numbered")
    for c in _cases("W3", "restructure"):
        assert c["context"]["source_text"] != c["reference_draft"]
    for k, word in (("tone_formal", "formal"), ("tone_casual", "casual"), ("tone_warm", "warm"), ("tone_firm", "firm")):
        for c in _cases("W3", k):
            assert word in c["turns"][0] and c["recipient"]
    for c in _cases("W3", "tone_firm"):
        assert "no new threats" in c["turns"][0] and "no new dates" in c["turns"][0]
    for c in _cases("W3", "simplify"):
        assert c["reference_draft"] != c["context"]["source_text"]
    for c in _cases("W3", "shorten") + _cases("W3", "expand"):
        assert "facts" in c["turns"][0]


@pytest.mark.parametrize("kind", [k for s, k in KINDS if s == "W4"])
def test_w4_planting_is_exact(kind):
    seeds = list(range(1, 31))
    for c in _cases("W4", kind, seeds):
        e, src = c["edit"], c["context"]["source_text"]
        gold = e["gold"]
        assert 100 <= _words(gold) <= 180
        assert e["errors"] and 1 <= len(e["errors"]) <= 7
        if kind == "minimal":
            assert 4 <= len(e["errors"]) <= 7
        fixed = src
        for er in e["errors"]:
            assert src.count(er["wrong"]) == 1 and gold.count(er["right"]) == 1
            assert er["wrong"] not in gold
            fixed = fixed.replace(er["wrong"], er["right"])
        assert fixed == gold and src != gold
        assert c["points"] == [] and c["facts"] == []
        for p in e["protected"]:
            assert src.count(p) == 1 and gold.count(p) == 1
        for a in e["ambiguous"]:
            assert src.count(a["span"]) == 1 and gold.count(a["span"]) == 1 and a["key"] in a["span"]
        if kind == "protected":
            assert len(e["protected"]) >= 4
        if kind == "ambiguous":
            assert 1 <= len(e["ambiguous"]) <= 2 and c["constraints"]["questions_list"]
            ref = c["reference_draft"]
            assert ref.startswith(gold) and "\nQuestions:\n" in ref
            block = ref.split("Questions:", 1)[1]
            for a in e["ambiguous"]:
                assert a["key"] in block
        else:
            assert not c["constraints"]["questions_list"] and c["reference_draft"] == gold
        if kind != "protected":
            assert e["protected"] == []
        if kind != "ambiguous":
            assert e["ambiguous"] == []


def test_w4_error_types_are_varied_and_pool_is_large():
    types, golds = set(), {}
    for kind in ("minimal", "protected", "ambiguous"):
        golds[kind] = {c["edit"]["gold"] for c in _cases("W4", kind, list(range(1, 41)))}
        for c in _cases("W4", kind):
            types |= {er["type"] for er in c["edit"]["errors"]}
    assert len(golds["minimal"]) >= 8 and len(golds["protected"]) >= 5 and len(golds["ambiguous"]) >= 5
    assert {"spelling", "apostrophe", "agreement", "doubled_word", "comma_splice", "homophone"} <= types
    splice = [er for c in _cases("W4", "minimal", list(range(1, 80))) for er in c["edit"]["errors"]
              if er["type"] == "comma_splice"]
    assert splice and all(er["alternatives"] for er in splice)


def test_w4_reference_has_no_overedit_and_source_differs():
    for kind in ("minimal", "protected", "ambiguous"):
        for c in _cases("W4", kind):
            assert c["context"]["source_text"] != c["edit"]["gold"]
            assert c["edit"]["gold"] in c["reference_draft"]
            assert c["context"]["source_text"] in c["turns"][0]


def test_reference_scores_when_checker_exists():
    wc = pytest.importorskip("edat.quality.writing_checks")
    for sub, kind in KINDS:
        for c in _cases(sub, kind):
            r = wc.score_writing(c["reference_draft"], c)
            assert r["pass"], (c["case_id"], {k: v for k, v in r["checks"].items()})
            if sub == "W4":
                assert not wc.score_writing(c["context"]["source_text"], c)["checks"]["WD8"]["status"] == "pass"
