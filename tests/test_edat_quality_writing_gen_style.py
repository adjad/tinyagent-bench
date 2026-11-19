"""Tests for the W5 (style control) and W7 (longer pieces) case generators."""
from __future__ import annotations

import functools
import re

import pytest

from edat.quality import checkers as C
from edat.quality import hygiene as H
from edat.quality import normalize as N
from edat.quality import textutil as T
from edat.quality import writing as W
from edat.quality import writing_gen_style as G

BASE = 20260
KINDS = [(s, k, n) for s, k, n, _ in W.PLAN if s in ("W5", "W7")]
IDS = [f"{s}/{k}" for s, k, _ in KINDS]


def _profile(sub: str, i: int):
    return W.profile_for(sub, i) if sub == "W5" else None


@functools.lru_cache(maxsize=None)
def _cases(sub: str, kind: str, base: int = BASE) -> tuple:
    n = next(t for s, k, t, _ in W.PLAN if (s, k) == (sub, kind))
    return tuple(G.GENERATORS[(sub, kind)](W.case_seed(base, sub, kind, i), index=i, profile=_profile(sub, i))
                 for i in range(n))


def _all(sub: str, kind: str):
    return _cases(sub, kind)


def _text_of(case: dict) -> str:
    return "\n".join(case["turns"])


def _anchor_hit(text: str, anchor, year) -> bool:
    t, v, al = N.parse_anchor(anchor)
    return N.anchor_present(text, t, v, aliases=al, case_year=year)


def test_every_w5_and_w7_kind_has_a_generator_and_enough_scenarios():
    assert set(G.GENERATORS) == {(s, k) for s, k, _ in KINDS}
    pools = {"standing_pref": G.EMAIL_SC, "one_off": G.ONEOFF_SC, "voice": G.MSG_SC, "conventions": G.CONV_SC,
             "exact_count": G.COUNT_SC, "consistency": G.CONS_SC, "outline": G.OUT_SC, "title": G.TITLE_SC}
    for kind, pool in pools.items():
        assert len(pool) >= 6, kind
    assert len(G.VOICES) >= 6 and len(G.COMBOS) >= 10 and len(G.ONEOFF_VARIANTS) == 10


@pytest.mark.parametrize("sub,kind,n", KINDS, ids=IDS)
def test_generates_and_follows_contract(sub, kind, n):
    cases = _all(sub, kind)
    assert len(cases) == n
    for i, c in enumerate(cases):
        assert W.brief_errors(c) == []
        assert c["generator"] == "writing_gen_style/1"
        assert (c["sub"], c["kind"]) == (sub, kind)
        assert c["profile"] == _profile(sub, i)
        assert c["reference_draft"].strip()
        assert any(p["kind"] == "anchored" for p in c["points"])
        for t in c["turns"] + [c["reference_draft"]]:
            assert not re.search(r"\[[A-Za-z ]+\]|\{|\}|/Users/|XX|TBD", t), t[:80]
        assert c["recipient"] is None or c["recipient"]["name"] in _text_of(c) or sub == "W7"


@pytest.mark.parametrize("sub,kind,n", KINDS, ids=IDS)
def test_deterministic_and_distinct(sub, kind, n):
    gen = G.GENERATORS[(sub, kind)]
    for i in (0, 1, 5):
        seed = W.case_seed(BASE, sub, kind, i)
        a = gen(seed, index=i, profile=_profile(sub, i))
        b = gen(seed, index=i, profile=_profile(sub, i))
        assert a == b
        c = gen(seed + 1, index=i, profile=_profile(sub, i))
        assert c["turns"] != a["turns"]
    cases = _all(sub, kind)
    assert len({c["case_id"] for c in cases}) == n
    assert len({tuple(c["turns"]) for c in cases}) == n
    assert len({c["reference_draft"] for c in cases}) == n


@pytest.mark.parametrize("sub,kind,n", KINDS, ids=IDS)
def test_reference_makes_every_anchored_point(sub, kind, n):
    for c in _all(sub, kind):
        for p in c["points"]:
            for a in p["anchors"]:
                assert _anchor_hit(c["reference_draft"], a, c["case_year"]), (c["case_id"], p["text"], a)


@pytest.mark.parametrize("sub,kind,n", KINDS, ids=IDS)
def test_reference_hygiene(sub, kind, n):
    for c in _all(sub, kind):
        for fn in (H.d6_no_leaks, H.d7_no_filler, H.d9_no_repetition):
            status, why = fn(c["reference_draft"], {"request": c["turns"][-1]}, {})
            assert status != H.FAIL, (c["case_id"], fn.__name__, why)
        if c["surface"] in W.NO_MARKDOWN_SURFACES:
            from edat.quality.hygiene import has_markdown
            assert not has_markdown(c["reference_draft"])


@pytest.mark.parametrize("sub,kind,n", KINDS, ids=IDS)
def test_reference_has_no_invented_atom(sub, kind, n):
    wc = pytest.importorskip("edat.quality.writing_checks")
    for c in _all(sub, kind):
        _, inv = wc.invented_atoms(c["reference_draft"], c)
        assert not inv, (c["case_id"], [a.surface for a in inv])
        if c["reference_turns"]:
            for t in c["reference_turns"]:
                _, inv = wc.invented_atoms(t, c)
                assert not inv, (c["case_id"], [a.surface for a in inv])


@pytest.mark.parametrize("sub,kind,n", KINDS, ids=IDS)
def test_reference_passes_the_deterministic_checks(sub, kind, n):
    wc = pytest.importorskip("edat.quality.writing_checks")
    for c in _all(sub, kind):
        outline = c["reference_turns"][0] if c["reference_turns"] else None
        r = wc.score_writing(c["reference_draft"], c, outline=outline)
        bad = {k: v for k, v in r["checks"].items() if v["status"] == "fail"}
        assert r["pass"] and r["gate_pass"], (c["case_id"], bad)


# ---------------------------------------------------------------- W5 standing_pref

def test_standing_pref_references_pass_each_active_preference():
    seen = set()
    for c in _all("W5", "standing_pref"):
        assert c["active_prefs"] and len(c["active_prefs"]) <= 2
        assert set(c["active_prefs"]) <= {"P5", "P6", "P7", "P8", "P9"}
        assert len(c["pref_meta"]["pref_texts"]) == len(c["active_prefs"])
        sys_prompt = W.system_prompt(c)
        for t in c["pref_meta"]["pref_texts"]:
            assert t in sys_prompt
        for pid in c["active_prefs"]:
            seen.add(pid)
            r = C.check(pid, c["reference_draft"], "draft_email", c["pref_meta"])
            assert r.status in ("pass",) or (pid in ("P5",) and r.status == "pass"), (c["case_id"], pid, r)
        if "P9" in c["active_prefs"]:
            assert c["pref_meta"]["signoff"]
            if c["profile"]:
                assert c["pref_meta"]["signoff"] == W.PROFILE_SIGNOFF[c["profile"]]
    assert seen == {"P5", "P6", "P7", "P8", "P9"}


def test_standing_pref_brief_tempts_the_violation():
    for c in _all("W5", "standing_pref"):
        req = c["turns"][0]
        meta = c["pref_meta"]
        if "P6" in c["active_prefs"]:
            assert re.search(r"\b\d{1,2}(:\d{2})? [ap]m\b", req)
            assert C.check("P6", req, "draft_email", meta).status == "fail"
            assert not re.search(r"\d [ap]m", c["reference_draft"])
        if "P7" in c["active_prefs"]:
            assert C.check("P7", req, "draft_email", meta).status == "fail"
        if "P8" in c["active_prefs"]:
            assert C.check("P8", req, "draft_email", meta).status == "fail"
        if "P9" in c["active_prefs"]:
            assert meta["signoff"].split("\n")[0] not in req       # the sign-off is only in the system prompt
        assert "emoji" not in req.lower()


# ---------------------------------------------------------------- W5 one_off

def test_one_off_references_satisfy_their_rule():
    seen = set()
    for c in _all("W5", "one_off"):
        d = c["reference_draft"]
        cons = c["constraints"]
        meta = c["pref_meta"]
        seen.add(meta["variant"])
        if cons["exact_sentences"]:
            assert len(T.split_sentences(d)) == cons["exact_sentences"]
        if cons["max_sentences"]:
            assert len(T.split_sentences(d)) <= cons["max_sentences"]
        if cons["max_words"]:
            assert len(d.split()) <= cons["max_words"]
        if cons["max_chars"]:
            assert len(d) <= cons["max_chars"]
        rule = meta.get("one_off")
        if rule is None:
            continue
        assert rule in G.ONE_OFF_KEYS and meta["one_off_text"] == G.ONE_OFF_KEYS[rule]
        if rule == "no_exclamation":
            assert "!" not in d
        elif rule == "starts_with_date":
            assert d.startswith(meta["lead_date"]) and meta["lead_date"] in c["turns"][0]
        elif rule == "all_lowercase":
            assert d == d.lower()
        elif rule == "ends_with_question":
            assert d.rstrip().endswith("?")
        elif rule == "no_contractions":
            assert not re.search(r"\w'(t|s|m|ll|re|ve|d)\b", d)
        elif rule == "no_greeting_or_signoff":
            assert not re.match(r"(hi|hello|dear)\b", d, re.I)
    assert seen == set(G.ONEOFF_VARIANTS)


# ---------------------------------------------------------------- W5 voice

def test_voice_cases_have_samples_and_a_neutral_reference():
    for i, c in enumerate(_all("W5", "voice")):
        s = c["context"]["samples"]
        assert 4 <= len(s) <= 6
        assert all(x in c["turns"][0] for x in s)
        ref = c["pref_meta"]["neutral_reference"]
        assert ref and ref != c["reference_draft"]
        for p in c["points"]:
            for a in p["anchors"]:
                assert _anchor_hit(ref, a, c["case_year"])
        if c["profile"]:
            assert set(s) <= set(W.own_messages(c["profile"]))
        else:
            assert "Test User" not in c["turns"][0]


# ---------------------------------------------------------------- W5 conventions / exact_count

def test_conventions_follow_the_brief_exactly():
    for c in _all("W5", "conventions"):
        d = c["reference_draft"].split("\n")
        cons = c["constraints"]
        assert d[0].startswith("Subject: " + c["pref_meta"]["subject_prefix"])
        assert len(d[0][len("Subject: "):].split()) <= 10
        assert d[1] == cons["greeting"]
        assert c["reference_draft"].endswith(cons["signoff"])
        assert c["pref_meta"]["subject_prefix"] in c["turns"][0]
        if c["profile"]:
            assert cons["signoff"] == W.PROFILE_SIGNOFF[c["profile"]]
        assert C.check("P9", c["reference_draft"], "draft_email", {"signoff": cons["signoff"]}).status == "pass"


def test_exact_count_references_hit_the_count():
    modes = set()
    for c in _all("W5", "exact_count"):
        d = c["reference_draft"]
        cons = c["constraints"]
        if cons["exact_words"]:
            modes.add("words")
            assert len(d.split()) == cons["exact_words"]
            assert f"exactly {cons['exact_words']} words" in c["turns"][0]
        else:
            modes.add("sentences")
            assert len(T.split_sentences(d)) == cons["exact_sentences"]
            assert f"exactly {cons['exact_sentences']} sentences" in c["turns"][0]
    assert modes == {"words", "sentences"}


# ---------------------------------------------------------------- W7

def test_consistency_entities_are_spelled_the_same_and_used_twice():
    for c in _all("W7", "consistency"):
        d = c["reference_draft"]
        ents = c["entities"]
        assert len(ents) == 9 and len(set(ents)) == 9
        assert len(c["points"]) >= 7
        for e in ents:
            assert d.count(e) >= 2, (c["case_id"], e)
            assert e in c["turns"][0]
        for e in ents[:5]:                                  # names and places: no folded variant
            assert len(re.findall(re.escape(e.lower()), d.lower())) == d.count(e)
        assert 300 <= len(d.split()) <= 520


def test_outline_pieces_follow_the_outline_order():
    for c in _all("W7", "outline"):
        outline, piece = c["reference_turns"]
        assert c["reference_draft"] == piece and len(c["turns"]) == 2
        assert 4 <= len(c["points"]) <= 6
        assert 400 <= len(piece.split()) <= 600
        pos_o, pos_p = {}, {}
        for p in c["points"]:
            a = p["anchors"][0]
            t, v, al = N.parse_anchor(a)
            so = N.anchor_spans(outline, t, v, aliases=al, case_year=c["case_year"])
            sp = N.anchor_spans(piece, t, v, aliases=al, case_year=c["case_year"])
            assert so and sp, (c["case_id"], p["text"])
            pos_o[p["id"]], pos_p[p["id"]] = so[0][0], sp[0][0]
        assert sorted(pos_o, key=pos_o.get) == sorted(pos_p, key=pos_p.get)
        assert [p["id"] for p in c["points"]] != sorted(pos_o, key=pos_o.get)     # not the brief's order
        assert "outline" in c["turns"][0].lower() and "outline" in c["turns"][1].lower()


def test_title_cases():
    for c in _all("W7", "title"):
        src = c["context"]["source_text"]
        assert 300 <= len(src.split()) <= 500
        m = re.fullmatch(r"Title: (.+)\nSubject: (.+)", c["reference_draft"])
        assert m
        assert len(m.group(1).split()) <= c["constraints"]["title_max_words"] == 8
        assert len(m.group(2).split()) <= 10
        assert 1 <= len(c["points"]) <= 2
        for p in c["points"]:
            assert p["anchors"][0] in src
        assert src in c["turns"][0]
        assert "Subject:" in c["turns"][0] and "at most 10 words" in c["turns"][0]


def test_w7_cases_are_neutral():
    for kind in ("consistency", "outline", "title"):
        for c in _all("W7", kind):
            assert c["profile"] is None and c["recipient"] is None
            assert c["case_year"] == 2027


def test_other_seeds_still_pass_checks():
    wc = pytest.importorskip("edat.quality.writing_checks")
    for base in (3, 4242):
        for sub, kind, n in KINDS:
            for c in _cases(sub, kind, base):
                outline = c["reference_turns"][0] if c["reference_turns"] else None
                r = wc.score_writing(c["reference_draft"], c, outline=outline)
                assert r["pass"], (c["case_id"], {k: v for k, v in r["checks"].items() if v["status"] == "fail"})
