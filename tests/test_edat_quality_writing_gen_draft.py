"""Tests for the W1, W2 and W6 case generators (edat.quality.writing_gen_draft)."""
from __future__ import annotations

import functools
import json
import re

import pytest

from edat.quality import hygiene, normalize as N, textutil as T, writing as W
from edat.quality import writing_gen_draft as G

KEYS = [(s, k) for s, k, _, _ in W.PLAN if s in ("W1", "W2", "W6")]
PROFILES = [None, *W.PROFILE_IDS]


def _gen(key, seed, index, profile):
    out = G.GENERATORS[key](seed, index=index, profile=profile)
    return out if isinstance(out, list) else [out]


def _cases(key, n=10, base=2024):
    out = []
    for i in range(n):
        out += _gen(key, W.case_seed(base, key[0], key[1], i), i, W.profile_for(key[0], i))
    return out


@functools.lru_cache(maxsize=None)
def _all_cases():
    return tuple(c for key in KEYS for c in _cases(key))


# ---- helpers: the rules of WRITING.md, checked here independently -------------

def _split(case):
    """(subject, body, title, description) of the reference draft."""
    d = case["reference_draft"].strip()
    lines = d.split("\n")
    subj = title = desc = None
    if case["surface"] == "invite":
        title = lines[0].split(":", 1)[1].strip()
        desc = lines[1].split(":", 1)[1].strip()
        return None, desc, title, desc
    if case["constraints"]["subject"]:
        assert lines[0].startswith("Subject: ")
        subj = lines[0][9:].strip()
        d = "\n".join(lines[1:]).strip()
    return subj, d, title, desc


def _request_without_thread(case):
    t = case["turns"][0]
    return t.split("\nThe email thread so far:")[0]


# ---- coverage --------------------------------------------------------------------

def test_generators_cover_every_w1_w2_w6_kind():
    assert set(G.GENERATORS) == set(KEYS)
    for key in KEYS:
        assert callable(G.GENERATORS[key])


def test_at_least_six_scenarios_per_kind():
    for key, scen in G.SCENARIOS.items():
        assert len(scen) >= 6, key
    assert len(G.BRIEFS) == 10


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("profile", PROFILES)
def test_every_kind_generates_for_every_profile(key, profile):
    for i in range(10):
        for c in _gen(key, 100 + i, i, profile):
            assert c["sub"] == key[0] and c["kind"] == key[1]
            assert c["generator"] == "writing_gen_draft/1"
            assert W.brief_errors(c) == []
            assert c["profile"] == profile


@pytest.mark.parametrize("key", KEYS)
def test_deterministic_in_seed(key):
    for i in (0, 3, 7):
        pr = W.profile_for(key[0], i)
        a = _gen(key, 555 + i, i, pr)
        b = _gen(key, 555 + i, i, pr)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        c = _gen(key, 556 + i, i, pr)
        assert json.dumps(a, sort_keys=True) != json.dumps(c, sort_keys=True)


# ---- sender and recipients -------------------------------------------------------

@pytest.mark.parametrize("key", [k for k in KEYS if k != ("W6", "register")])
def test_profile_bound_versus_neutral_sender(key):
    for i in range(10):
        pr = W.profile_for(key[0], i)
        c = _gen(key, 31 + i, i, pr)[0]
        if pr:
            assert c["sender"] == W.profile_sender(pr)
            assert c["today"] == W.profile_today(pr).isoformat()
            if c["recipient"]:
                rel = {x["relation"] for x in W.contacts(pr)}
                assert c["recipient"]["relation"] in rel
        else:
            assert c["sender"]["name"] != "Test User"
            assert c["sender"]["name"] not in {W.profile_sender(p)["name"] for p in W.PROFILE_IDS}
            assert c["case_year"] == 2027
            if c["recipient"]:
                assert c["recipient"]["name"] != c["sender"]["name"]


def test_profile_cases_use_the_profile_signoff_and_neutral_ones_name_the_sender():
    for c in _all_cases():
        so = c["constraints"]["signoff"]
        if not so:
            continue
        if c["profile"]:
            assert so == W.PROFILE_SIGNOFF[c["profile"]]
        else:
            assert so.split("\n")[-1] == c["sender"]["preferred_name"]


def test_formal_greeting_for_professors():
    for c in _all_cases():
        r = c["recipient"]
        if r and c["constraints"]["greeting"] and re.search(r"professor|supervisor|instructor", r["relation"]):
            assert c["constraints"]["greeting"].startswith("Dear ")


def test_profile_replies_use_a_real_thread():
    seen = 0
    for pid in W.PROFILE_IDS:
        for i in range(0, 10, 2):
            c = _gen(("W1", "reply"), 91 + i, i, pid)[0]
            th = c["context"]["thread"]
            assert c["context"]["thread_kind"] == "email" and 1 <= len(th) <= 3
            real = {e["from"]["name"] for t in W.email_threads(pid) for e in t}
            assert th[-1]["from"] in real
            assert th[-1]["from"] != c["sender"]["name"]
            assert c["recipient"]["name"].split()[-1] in th[-1]["from"]
            assert not any(m["text"].lstrip().startswith(">") for m in th)
            seen += 1
    assert seen == 15


def test_neutral_replies_carry_a_thread_that_ends_with_the_planted_detail():
    for c in _cases(("W1", "reply")):
        if c["profile"]:
            continue
        th = c["context"]["thread"]
        assert th and all({"from", "time", "text"} <= set(m) for m in th)
        blob = " ".join(m["text"] for m in th)
        for f in c["must_not"]:
            assert any(N.anchor_present(blob, *N.parse_anchor(a)[:2]) for a in f["anchors"]) or f["stated"]


# ---- points, forbidden items, constraints ---------------------------------------

def test_points_and_forbidden_items():
    for c in _all_cases():
        assert 2 <= len(c["points"]) <= 4, c["case_id"]
        assert any(p["kind"] == "anchored" for p in c["points"]), c["case_id"]
        assert len(c["must_not"]) <= 2
        for f in c["must_not"]:
            assert f["anchors"], c["case_id"]
        for p in c["points"]:
            assert (p["kind"] == "anchored") == bool(p["anchors"])
        assert c["constraints"]["output_only"] is True
        assert c["constraints"]["max_words"] or c["constraints"]["description_max_words"]


def test_w2_hard_messages_always_have_a_forbidden_item():
    for key in KEYS:
        if key[0] == "W2":
            for c in _cases(key):
                assert c["must_not"], c["case_id"]


def test_surface_conventions():
    for c in _all_cases():
        cons = c["constraints"]
        if c["surface"] == "message":
            assert cons["max_chars"] and not cons["subject"] and not cons["greeting"]
        if c["surface"] == "email":
            assert cons["greeting"] and cons["signoff"] and cons["subject"]
        if c["surface"] == "invite":
            assert cons["title_max_words"] and cons["description_max_words"] and not cons["subject"]
        if c["kind"] == "section":
            assert (cons["min_words"], cons["max_words"]) == (150, 300)
        assert cons["no_markdown"] == (c["surface"] in W.NO_MARKDOWN_SURFACES)


def test_feedback_names_three_problems_and_bans_absolutes():
    for c in _cases(("W2", "feedback")):
        assert sum(1 for p in c["points"] if p["kind"] == "anchored") >= 3
        banned = {a for f in c["must_not"] for a in f["anchors"]}
        assert {"always", "never"} <= banned
        assert not re.search(r"\b(always|never)\b", c["reference_draft"], re.I)


# ---- W6 ----------------------------------------------------------------------------

@pytest.mark.parametrize("profile", PROFILES)
def test_w6_returns_four_audiences_with_the_same_facts(profile):
    for g in range(10):
        cases = _gen(("W6", "register"), 700 + g, g, profile)
        assert len(cases) == 4
        assert [c["audience"] for c in cases] == ["manager", "friend", "customer", "professor"]
        assert len({c["group_id"] for c in cases}) == 1 and cases[0]["group_id"]
        assert len({c["case_id"] for c in cases}) == 4
        assert len({json.dumps([(p["text"], p["anchors"]) for p in c["points"]]) for c in cases}) == 1
        for c in cases:
            assert sum(1 for p in c["points"] if p["lead"]) == 1
            assert next(p for p in c["points"] if p["lead"])["anchors"]
            assert c["surface"] == "email" and c["constraints"]["subject"]
        g_ = {c["audience"]: c["constraints"]["greeting"] for c in cases}
        assert g_["professor"].startswith("Dear ") and g_["customer"].startswith("Dear ")
        assert not g_["friend"].startswith("Dear ")


def test_w6_ten_distinct_briefs():
    groups = [tuple(_gen(("W6", "register"), 40 + g, g, None)) for g in range(10)]
    purposes = {c[0]["purpose"] for c in groups}
    assert len(purposes) == 10
    assert len({c[0]["subject"] if "subject" in c[0] else c[0]["reference_draft"].split("\n")[0] for c in groups}) == 10


def test_w6_register_rules_in_the_reference_drafts():
    slang = re.compile(r"\b(gonna|wanna|yeah|hey|guys|lol|btw)\b", re.I)
    for c in (x for x in _all_cases() if x["sub"] == "W6"):
        _, body, _, _ = _split(c)
        n = len(body.split())
        if c["audience"] in ("professor", "customer"):
            assert body.startswith("Dear ")
            assert not slang.search(body)
            assert len(re.findall(r"\w'\w", body)) <= n / 50
        if c["audience"] == "friend":
            assert "Dear" not in body and "Sincerely" not in body
        if c["audience"] == "manager":
            lead = next(p for p in c["points"] if p["lead"])
            core = "\n".join(body.split("\n")[2:]).rsplit("\n\n", 1)[0]
            first_two = " ".join(T.split_sentences(core)[:2])
            for a in lead["anchors"]:
                t, v, al = N.parse_anchor(a)
                assert N.anchor_present(first_two, t, v, aliases=al, case_year=c["case_year"]), c["case_id"]


# ---- the reference drafts ------------------------------------------------------------

def test_reference_drafts_meet_every_rule():
    for c in _all_cases():
        cons = c["constraints"]
        subj, body, title, desc = _split(c)
        d = c["reference_draft"]
        n = len(body.split())
        if cons["max_words"]:
            assert n <= cons["max_words"], (c["case_id"], n)
        if cons["min_words"]:
            assert n >= cons["min_words"], (c["case_id"], n)
        if cons["max_chars"]:
            assert len(body) <= cons["max_chars"], c["case_id"]
        if cons["description_max_words"]:
            assert n <= cons["description_max_words"] and len(title.split()) <= cons["title_max_words"]
        if subj is not None:
            assert len(subj.split()) <= 10 and d.startswith("Subject: ")
        if cons["greeting"]:
            assert body.startswith(cons["greeting"])
        if cons["signoff"]:
            assert body.endswith(cons["signoff"])
            assert body.count(cons["signoff"]) == 1
        if cons["no_markdown"]:
            assert not hygiene.has_markdown(d), c["case_id"]
        assert not re.search(r"\[[A-Za-z ]+\]|<[a-z ]+>|\bXX\b|\bTBD\b|\{\{", d)
        for s in T.split_sentences(body)[:1] + T.split_sentences(body)[-2:]:
            assert not T.is_filler_opener(s) or s == T.split_sentences(body)[-1]
        assert not any(T.is_filler_closer(s) for s in T.split_sentences(body))
        assert not T.is_filler_opener(T.split_sentences(body)[0])
        assert hygiene.d9_no_repetition(d, {}, {"active_prefs": []})[0] != "fail"
        assert not re.match(r"(here'?s|sure|of course|below is)", d, re.I)


def test_reference_drafts_contain_every_anchor_and_no_forbidden_one():
    for c in _all_cases():
        _, body, _, _ = _split(c)
        full = c["reference_draft"]
        for p in c["points"]:
            for a in p["anchors"]:
                t, v, al = N.parse_anchor(a)
                assert N.anchor_present(full, t, v, aliases=al, case_year=c["case_year"]), (c["case_id"], p["text"])
        for f in c["must_not"]:
            for a in f["anchors"]:
                t, v, al = N.parse_anchor(a)
                assert not N.anchor_present(full, t, v, aliases=al, case_year=c["case_year"]), (c["case_id"], f["text"])


def test_reference_drafts_invent_nothing():
    for c in _all_cases():
        _, body, _, _ = _split(c)
        lines = body.split("\n")
        if c["constraints"]["greeting"]:
            lines = lines[1:]
        if c["constraints"]["signoff"]:
            lines = lines[: -len(c["constraints"]["signoff"].split("\n"))]
        text = "\n".join(lines) + (f"\n{c['reference_draft'].split(chr(10))[0]}" if c["constraints"]["subject"] else "")
        thread = " ".join(m["text"] + " " + m["time"] for m in (c["context"]["thread"] or []))
        source = "\n".join(c["turns"] + [c["allowed_text"], thread, c["sender"]["name"],
                                          (c["recipient"] or {}).get("name", "")])
        keys = N.atom_keys(source)
        for a in N.find_atoms(text):
            assert N.atom_key(a) in keys, (c["case_id"], a.surface)


def test_scorer_accepts_the_references_when_available():
    checks = pytest.importorskip("edat.quality.writing_checks")
    for c in _all_cases():
        res = checks.score_writing(c["reference_draft"], c)
        assert res["pass"], (c["case_id"], {k: v for k, v in res["checks"].items() if v.get("status") == "fail"})
        assert checks.score_writing("", c)["score"] == 0


# ---- the request ---------------------------------------------------------------------

def test_request_hides_unstated_forbidden_items_and_states_the_rest():
    for c in _all_cases():
        req = _request_without_thread(c)
        for f in c["must_not"]:
            hit = any(N.anchor_present(req, *N.parse_anchor(a)[:2]) for a in f["anchors"])
            if f["stated"]:
                assert f["text"] in c["turns"][0]
            else:
                assert not hit, (c["case_id"], f["text"])
        assert c["turns"][0].rstrip().endswith("Reply with the text only.")


@functools.lru_cache(maxsize=None)
def _ledger_strings():
    out = set()

    def walk(x):
        if isinstance(x, str):
            if len(x) >= 40:
                out.add(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for pid in W.PROFILE_IDS:
        walk(json.loads((W.PROFILES_DIR / pid / "ledger.json").read_text(encoding="utf-8")))
    return out


def test_ledger_text_never_reaches_a_request_or_draft():
    ledger = _ledger_strings()
    assert ledger
    for c in _all_cases():
        blob = "\n".join(c["turns"] + [c["reference_draft"]])
        assert not any(s in blob for s in ledger), c["case_id"]


# ---- variety -------------------------------------------------------------------------

@pytest.mark.parametrize("key", KEYS)
def test_ten_cases_of_a_kind_are_pairwise_distinct(key):
    cases = _cases(key)
    if key[0] == "W6":
        cases = [c for c in cases if c["audience"] == "friend"]
    assert len(cases) == 10
    assert len({c["case_id"] for c in cases}) == 10
    assert len({c["reference_draft"] for c in cases}) == 10
    assert len({c["turns"][0] for c in cases}) == 10
    assert len({c["purpose"] for c in cases}) >= 6


def test_dates_follow_today_for_profile_cases():
    for c in (x for x in _all_cases() if x["profile"]):
        today = W.profile_today(c["profile"]).isoformat()
        assert c["today"] == today
        for p in c["points"]:
            for a in p["anchors"]:
                if isinstance(a, dict) and a["type"] == "date" and p["lead"]:
                    assert a["value"] > today, (c["case_id"], p["text"])
