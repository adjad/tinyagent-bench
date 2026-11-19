"""Tests for the Writing checks WD1-WD11 (spec 03 section 9.3). All content is invented."""
from __future__ import annotations

import datetime as dt

from edat.quality import writing as W
from edat.quality import writing_checks as K

TODAY = dt.date(2030, 11, 18)
SENDER = {"name": "Marcus Chen", "preferred_name": "Marcus"}
RCP = {"name": "Dana Okafor", "relation": "manager"}


def mk(sub="W1", kind="email", *, points=(), must_not=(), constraints=None, ref="Hello there.", seed=1, **fields):
    fields.setdefault("sender", SENDER)
    fields.setdefault("recipient", RCP)
    profile = fields.pop("profile", None)
    c = W.new_case(sub=sub, kind=kind, seed=seed, generator="test/1", today=TODAY, profile=profile,
                   purpose=fields.pop("purpose", "move the design review"), points=list(points),
                   must_not=list(must_not), constraints=constraints or {}, reference_draft=ref, **fields)
    return W.finish(c)


def st(res, wd):
    return res["checks"][wd]["status"]


MON = W.date_anchor(dt.date(2030, 11, 25))
P_DATE = W.point("p1", "review moves to Mon 25 Nov", [MON])
P_LOAD = W.point("p2", "load-test results not ready", ["load-test"])
GOOD = ("Subject: Design review moves\n\nHi Dana,\n\nThe design review moves to Mon 25 Nov because the "
        "load-test results are not ready.\n\nThanks,\nMarcus")


def email_case(**kw):
    cons = {"max_words": 60, "subject": True, "greeting": "Hi Dana,", "signoff": "Thanks,\nMarcus"}
    cons.update(kw.pop("constraints", {}))
    return mk(points=[P_DATE, P_LOAD], must_not=[W.forbid("f1", "vendor Brightline", ["Brightline"])],
              constraints=cons, ref=GOOD, **kw)


# ---------------------------------------------------------------- parsing

def test_parse_subject_body_questions():
    c = mk("W4", "minimal", edit={"gold": "Fine.", "errors": [], "protected": [], "ambiguous": []},
           constraints={"questions_list": True}, points=[P_DATE])
    p = K.parse_draft("Fine.\n\nQuestions:\n- Sam: whose copy?\n* other", c)
    assert p["body"] == "Fine." and p["questions"] == ["Sam: whose copy?", "other"]
    e = email_case()
    p = K.parse_draft(GOOD, e)
    assert p["subject"] == "Design review moves" and p["body"].startswith("Hi Dana,")
    assert p["header"] == ["subject"]


def test_parse_invite():
    c = mk("W1", "invite", points=[P_DATE], constraints={"title_max_words": 8, "description_max_words": 20})
    p = K.parse_draft("Title: Review\nDescription: Mon 25 Nov at the usual room.", c)
    assert p["title"] == "Review" and p["description"].startswith("Mon 25 Nov") and p["words"] == 7


# ---------------------------------------------------------------- WD1

def test_wd1_all_points_pass_and_dates_by_value():
    c = email_case()
    assert st(K.score_writing(GOOD, c), "WD1") == "pass"
    other = GOOD.replace("Mon 25 Nov", "25 November 2030")
    r = K.score_writing(other, c)
    assert st(r, "WD1") == "pass"
    iso = GOOD.replace("Mon 25 Nov", "2030-11-25")
    assert st(K.score_writing(iso, c), "WD1") == "pass"


def test_wd1_missing_point_fails_and_recall():
    c = email_case()
    r = K.score_writing(GOOD.replace("load-test results are not ready", "numbers are late"), c)
    assert st(r, "WD1") == "fail"
    assert r["metrics"]["point_recall"] == 0.5 and r["metrics"]["points_made"] == 1
    assert not r["gate_pass"]


def test_wd1_na_without_anchored_points():
    c = mk(points=[W.point("p1", "offer to help")], ref="Hello.")
    assert st(K.score_writing("Hello.", c), "WD1") == "n/a"


# ---------------------------------------------------------------- WD2

def test_wd2_invented_date_in_commitment_fails():
    c = email_case(constraints={"max_words": 80})
    d = GOOD.replace("Thanks,", "I'll send it by Wed 27 Nov.\n\nThanks,")
    r = K.score_writing(d, c)
    assert st(r, "WD2") == "fail" and r["metrics"]["invented_atoms"] >= 1
    assert r["metrics"]["invented_rate"] > 0


def test_wd2_brief_atoms_and_weekday_words_pass():
    c = email_case()
    r = K.score_writing(GOOD.replace("Mon 25 Nov", "Monday, 25 November"), c)
    assert st(r, "WD2") == "pass" and r["metrics"]["atoms"] >= 1


def test_wd2_invented_name_and_number():
    c = email_case(constraints={"max_words": 80})
    d = GOOD.replace("Thanks,", "I spoke with Priya about the 4471 ticket.\n\nThanks,")
    r = K.score_writing(d, c)
    assert st(r, "WD2") == "fail"
    assert any("Priya" in s or "4471" in s for s in r["checks"]["WD2"]["invented"])


def test_wd2_allowed_text_and_thread():
    c = mk(points=[P_DATE], constraints={"max_words": 60}, allowed_text="Quay Lane depot",
           context={"thread": [{"from": "Ines Park", "time": "09:00", "text": "Bring the Hobart file."}],
                    "thread_kind": "email"}, ref="x")
    r = K.score_writing("Hi Dana, I will bring the Hobart file to Quay Lane on Mon 25 Nov. Ines Park knows.", c)
    assert st(r, "WD2") == "pass"


def test_wd2_cache_is_per_case_content():
    a = mk(points=[P_DATE], allowed_text="Zorbal", seed=5)
    b = mk(points=[P_DATE], allowed_text="Other", seed=5)
    assert a["case_id"] == b["case_id"]
    t = "Mon 25 Nov with Zorbal."
    assert st(K.score_writing(t, a), "WD2") == "pass"
    assert st(K.score_writing(t, b), "WD2") == "fail"


# ---------------------------------------------------------------- WD3

def test_wd3_forbidden_present_fails():
    c = email_case(constraints={"max_words": 80})
    r = K.score_writing(GOOD.replace("Thanks,", "Brightline was slow.\n\nThanks,"), c)
    assert st(r, "WD3") == "fail" and r["metrics"]["forbidden"] is True
    assert not r["gate_pass"]


def test_wd3_quoted_line_exempt():
    c = email_case(constraints={"max_words": 80})
    d = GOOD.replace("Thanks,", "> Brightline said it would slip.\n\nThanks,")
    r = K.score_writing(d, c)
    assert st(r, "WD3") == "pass" and r["metrics"]["forbidden"] is False


def test_wd3_na_without_must_not():
    c = mk(points=[P_DATE], ref="x")
    assert st(K.score_writing("Mon 25 Nov", c), "WD3") == "n/a"


# ---------------------------------------------------------------- WD4

def test_wd4_name_case_variant_fails():
    c = email_case()
    r = K.score_writing(GOOD.replace("Hi Dana,", "Hi Dana,").replace("moves", "moves for Dana okafor"), c)
    assert st(r, "WD4") == "fail"


def test_wd4_diacritic_variant():
    c = mk(points=[W.point("p1", "meet", ["Zoë Lindqvist"])], recipient={"name": "Zoë Lindqvist", "relation": "friend"},
           ref="x")
    assert st(K.score_writing("See Zoe Lindqvist at noon.", c), "WD4") == "fail"
    assert st(K.score_writing("See Zoë Lindqvist at noon.", c), "WD4") == "pass"


def test_wd4_exact_names_pass_and_common_word_ok():
    c = mk(sender={"name": "Will Hart", "preferred_name": "Will"}, points=[P_DATE], ref="x")
    assert st(K.score_writing("I will come Mon 25 Nov. Will", c), "WD4") == "pass"


def test_wd4_preference_formats():
    c = mk(points=[P_DATE], active_prefs=["P6", "P7"], pref_meta={"source_times": ["15:00"], "case_year": 2030},
           ref="x", constraints={"max_words": 40})
    bad = K.score_writing("Meet at 3 pm on 25 November.", c)
    assert st(bad, "WD4") == "fail"
    good = K.score_writing("Meet at 15:00 on Mon 25 Nov.", c)
    assert st(good, "WD4") == "pass"


# ---------------------------------------------------------------- WD5

def test_wd5_word_limit_one_over():
    c = mk(points=[P_DATE], constraints={"max_words": 8}, ref="x")
    ok = "Review moves to Mon 25 Nov now."
    assert K.parse_draft(ok, c)["words"] == 7
    assert st(K.score_writing(ok, c), "WD5") == "pass"
    over = "Review moves to Mon 25 Nov now, sorry sorry."
    assert K.parse_draft(over, c)["words"] == 9
    assert st(K.score_writing(over, c), "WD5") == "fail"


def test_wd5_exact_words():
    c = mk("W5", "exact_count", points=[P_DATE], constraints={"exact_words": 6}, ref="x", recipient=None)
    assert st(K.score_writing("Review moves to Mon 25 Nov", c), "WD5") == "pass"
    assert st(K.score_writing("Review moves to Mon 25 Nov now", c), "WD5") == "fail"


def test_wd5_sentence_and_char_limits():
    c = mk("W5", "exact_count", points=[P_DATE], constraints={"max_sentences": 2, "max_chars": 60}, ref="x")
    assert st(K.score_writing("Review moves on Mon 25 Nov 2030. Sorry.", c), "WD5") == "pass"
    assert st(K.score_writing("Review moves on Mon 25 Nov 2030. Sorry. Really.", c), "WD5") == "fail"
    assert st(K.score_writing("Review moves Mon 25 Nov 2030. " + "x" * 50, c), "WD5") == "fail"


def test_wd5_missing_greeting_subject_signoff():
    c = email_case()
    assert st(K.score_writing(GOOD, c), "WD5") == "pass"
    assert st(K.score_writing(GOOD.replace("Hi Dana,", "Hello Dana,"), c), "WD5") == "fail"
    assert st(K.score_writing(GOOD.replace("Subject: Design review moves\n\n", ""), c), "WD5") == "fail"
    assert st(K.score_writing(GOOD.replace("Subject: Design review moves", "Subject: " + "word " * 11), c), "WD5") == "fail"
    assert st(K.score_writing(GOOD.replace("Thanks,\nMarcus", "Regards,\nMarcus"), c), "WD5") == "fail"
    assert st(K.score_writing(GOOD + "\nPS soon", c), "WD5") == "fail"      # sign-off no longer last


def test_wd5_markdown_and_placeholder():
    c = email_case()
    md = GOOD.replace("The design review", "**The** design review")
    assert st(K.score_writing(md, c), "WD5") == "fail"
    assert st(K.score_writing(GOOD.replace("Hi Dana,", "Hi [Name],"), c), "WD5") == "fail"
    assert "placeholder" in K.score_writing(GOOD.replace("Hi Dana,", "Hi [Name],"), c)["checks"]["WD5"]["reason"] \
        or "greeting" in K.score_writing(GOOD.replace("Hi Dana,", "Hi [Name],"), c)["checks"]["WD5"]["reason"]
    c2 = mk(points=[P_DATE], constraints={"max_words": 40}, ref="x")
    assert st(K.score_writing("Review on <date> was Mon 25 Nov.", c2), "WD5") == "fail"
    assert st(K.score_writing("Review on TBD or Mon 25 Nov.", c2), "WD5") == "fail"


def test_wd5_title_and_description_invite():
    c = mk("W1", "invite", points=[W.point("p1", "when", [MON])],
           constraints={"title_max_words": 3, "description_max_words": 8}, ref="x", recipient=None)
    ok = "Title: Design review\nDescription: Moves to Mon 25 Nov, same room."
    assert st(K.score_writing(ok, c), "WD5") == "pass"
    assert st(K.score_writing("Title: The long design review title\nDescription: Mon 25 Nov.", c), "WD5") == "fail"
    assert st(K.score_writing("Title: Review\nDescription: Mon 25 Nov at the room we always use for it.", c), "WD5") == "fail"
    assert st(K.score_writing("Title: Review\nMon 25 Nov.", c), "WD5") == "fail"
    assert st(K.score_writing("Title: Review\nDescription: **Mon 25 Nov**", c), "WD5") == "fail"


def test_wd5_one_off_rules():
    c = mk("W5", "one_off", points=[P_DATE], pref_meta={"one_off": "no_exclamation"}, ref="x")
    assert st(K.score_writing("see you Mon 25 Nov", c), "WD5") == "pass"
    assert st(K.score_writing("see you Mon 25 Nov!", c), "WD5") == "fail"
    c = mk("W5", "one_off", points=[P_DATE], pref_meta={"one_off": "all_lowercase"}, ref="x")
    assert st(K.score_writing("see you mon 25 nov", c), "WD5") == "pass"
    assert st(K.score_writing("See you mon 25 nov", c), "WD5") == "fail"
    c = mk("W5", "one_off", points=[P_DATE], pref_meta={"one_off": "start_with_date"}, ref="x")
    assert st(K.score_writing("Mon 25 Nov: review moves", c), "WD5") == "pass"
    assert st(K.score_writing("Review moves Mon 25 Nov", c), "WD5") == "fail"
    c = mk("W5", "one_off", points=[P_DATE], pref_meta={"one_off": "mystery"}, ref="x")
    r = K.score_writing("Review moves Mon 25 Nov", c)
    assert st(r, "WD5") == "pass" and r["checks"]["WD5"]["one_off"]["mystery"]["status"] == "n/a"


def test_wd5_other_active_pref_no_emoji():
    c = mk(points=[P_DATE], active_prefs=["P5"], ref="x")
    assert st(K.score_writing("See you Mon 25 Nov", c), "WD5") == "pass"
    assert st(K.score_writing("See you Mon 25 Nov \U0001F600", c), "WD5") == "fail"


def test_wd5_profile_signoff():
    c = mk(points=[P_DATE], constraints={"signoff": "profile"}, ref="x")
    assert K._signoff(c) is None
    c["profile"] = "tech"
    assert K._signoff(c) == "Thanks,\nMarcus"


# ---------------------------------------------------------------- WD6

SRC = "We move on Sat 14 Dec to 18 Quay Lane. The parking code is 4471. Ask Ines Park. We are very excited about it."


def w3_case(kind="shorten", **kw):
    facts = [{"id": "f1", "text": "move date", "anchors": [W.date_anchor(dt.date(2030, 12, 14))], "core": True},
             {"id": "f2", "text": "address", "anchors": ["18 Quay Lane"], "core": True},
             {"id": "f3", "text": "code", "anchors": [W.number_anchor("4471")], "core": True},
             {"id": "f4", "text": "excited", "anchors": ["excited"], "core": False}]
    return mk("W3", kind, facts=facts, context={"source_text": SRC}, constraints=kw.pop("constraints", {"max_words": 20}),
              ref="x", **kw)


def test_wd6_core_facts_kept_for_shorten():
    c = w3_case()
    r = K.score_writing("Move Sat 14 Dec to 18 Quay Lane, parking code 4471.", c)
    assert st(r, "WD6") == "pass" and r["metrics"]["fact_keep_ok"] is True
    assert r["gate_pass"] and r["score"] == 1.0
    r = K.score_writing("Move Sat 14 Dec to 18 Quay Lane.", c)
    assert st(r, "WD6") == "fail" and r["metrics"]["fact_keep_ok"] is False and not r["gate_pass"]
    assert abs(r["metrics"]["fact_keep_rate"] - 2 / 3) < 1e-9


def test_wd6_new_atom_fails_and_expand_needs_all_facts():
    c = w3_case()
    r = K.score_writing("Move Sat 14 Dec to 18 Quay Lane, code 4471, call Priya.", c)
    assert st(r, "WD6") == "fail" and "Priya" in r["checks"]["WD6"]["new_atoms"]
    c = w3_case("expand", constraints={"min_words": 5})
    assert st(K.score_writing("Move Sat 14 Dec to 18 Quay Lane, code 4471.", c), "WD6") == "fail"      # excited lost
    assert st(K.score_writing("Move Sat 14 Dec to 18 Quay Lane, code 4471, and we are excited.", c), "WD6") == "pass"


def test_wd6_shape_bullets_and_prose():
    c = w3_case("to_bullets", target_shape="bullets", constraints={})
    bullets = "- Move Sat 14 Dec\n- 18 Quay Lane\n- Code 4471\n- Excited"
    assert st(K.score_writing(bullets, c), "WD6") == "pass"
    prose = "Move Sat 14 Dec to 18 Quay Lane. Code 4471. Excited. Really."
    assert st(K.score_writing(prose, c), "WD6") == "fail"
    c = w3_case("to_prose", target_shape="paragraphs", constraints={})
    assert st(K.score_writing(prose, c), "WD6") == "pass"
    assert st(K.score_writing(bullets, c), "WD6") == "fail"


# ---------------------------------------------------------------- WD7

def test_wd7_preamble_fails_under_output_only():
    c = email_case()
    r = K.score_writing("Here's a draft:\n\n" + GOOD, c)
    assert st(r, "WD7") == "fail" and not r["gate_pass"]
    c2 = email_case(constraints={"output_only": False})
    assert "preamble" not in K.score_writing("Here's a draft:\n\n" + GOOD, c2)["checks"]["WD7"]["reason"]


def test_wd7_closing_offer_and_leaks_and_repetition():
    c = email_case(constraints={"max_words": 100, "signoff": None})
    assert st(K.score_writing(GOOD + "\n\nWould you like me to make it shorter?", c), "WD7") == "fail"
    assert st(K.score_writing("<think>hmm</think>" + GOOD, c), "WD7") == "fail"
    rep = GOOD.replace("Thanks,", "The review moves to Mon 25 Nov for sure.\n\nThe review moves to Mon 25 Nov for sure.\n\nThanks,")
    assert st(K.score_writing(rep, c), "WD7") == "fail"
    assert st(K.score_writing(GOOD, c), "WD7") == "pass"


def test_wd7_filler_opener():
    c = email_case(constraints={"signoff": None, "greeting": None, "max_words": 100})
    d = "Subject: Moved\n\nCertainly, the review moves to Mon 25 Nov because the load-test is late."
    assert st(K.score_writing(d, c), "WD7") == "fail"


def test_wd7_empty_fails():
    assert st(K.score_writing("", email_case()), "WD7") == "fail"


# ---------------------------------------------------------------- WD8

GOLD = "I will receive the files tomorrow. Its a big job, and the team is gonna help. Sam and Lee's team ships Friday."
SRC4 = "I will recieve the files tomorrow. It's a big job, and the team is gonna help. Sam and Lee's team ships Friday."
ERR = [{"id": "e1", "type": "spelling", "wrong": "recieve", "right": "receive", "alternatives": []}]


def w4_case(kind="minimal", **edit):
    e = {"gold": GOLD, "errors": ERR, "protected": ["gonna"], "ambiguous": []}
    e.update(edit)
    return mk("W4", kind, edit=e, context={"source_text": SRC4},
              constraints={"questions_list": bool(e["ambiguous"])}, ref=GOLD,
              points=[], recipient=None)


def test_wd8_fix_and_protected_pass():
    c = w4_case()
    r = K.score_writing(GOLD, c)
    assert st(r, "WD8") == "pass"
    m = r["metrics"]
    assert m["fix_rate"] == 1.0 and m["overedit"] == 0.0 and m["protected_ok"] is True
    assert r["score"] == 1.0 and r["gate_pass"]


def test_wd8_unfixed_error_fails():
    c = w4_case()
    r = K.score_writing(SRC4, c)
    assert st(r, "WD8") == "fail" and r["metrics"]["fix_rate"] == 0.0 and r["score"] == 0.0


def test_wd8_overedit_fails_and_threshold():
    c = w4_case()
    over = GOLD.replace("a big job", "a very large job indeed")
    r = K.score_writing(over, c)
    assert st(r, "WD8") == "fail" and r["metrics"]["overedit"] > 0.02 and r["metrics"]["fix_rate"] == 1.0
    small = GOLD.replace("Friday.", "Friday!")
    assert K.score_writing(small, c)["metrics"]["overedit"] < 0.02
    assert st(K.score_writing(small, c), "WD8") == "pass"


def test_wd8_protected_changed_fails():
    c = w4_case()
    r = K.score_writing(GOLD.replace("gonna", "going to"), c)
    assert r["metrics"]["protected_ok"] is False and st(r, "WD8") == "fail"


def test_wd8_alternative_accepted():
    errs = [{"id": "e1", "type": "x", "wrong": "recieve", "right": "receive", "alternatives": ["get"]}]
    c = w4_case(errors=errs)
    r = K.score_writing(GOLD.replace("receive", "get"), c)
    assert r["metrics"]["fix_rate"] == 1.0


def test_wd8_ambiguous_both_ways():
    amb = [{"span": "Sam and Lee's team", "key": "Sam"}]
    gold = "Send the files to Sam and Lee's team by Friday."
    c = mk("W4", "ambiguous", edit={"gold": gold, "errors": [], "protected": [], "ambiguous": amb},
           context={"source_text": gold}, constraints={"questions_list": True}, ref=gold, recipient=None)
    good = gold + "\n\nQuestions:\n- Sam: his own copy, or the shared team?"
    r = K.score_writing(good, c)
    assert st(r, "WD8") == "pass" and r["metrics"]["flag_rate"] == 1.0 and r["metrics"]["fix_rate"] is None
    assert r["score"] == 1.0
    changed = good.replace("Sam and Lee's team", "Sam's team")
    assert st(K.score_writing(changed, c), "WD8") == "fail"
    not_flagged = gold + "\n\nQuestions:\n- Which Friday?"
    r = K.score_writing(not_flagged, c)
    assert st(r, "WD8") == "fail" and r["metrics"]["flag_rate"] == 0.0 and r["score"] == 0.0
    assert K.score_writing(gold, c)["metrics"]["flag_rate"] == 0.0


def test_wd8_unrelated_text_gets_no_fix_credit():
    c = w4_case()
    r = K.score_writing("We should receive nothing at all about anything else in this long and different text.", c)
    assert r["metrics"]["fix_rate"] == 0.0


def test_lev_basic():
    assert K.lev("kitten", "sitting") == 3
    assert K.lev("", "abc") == 3 and K.lev("same", "same") == 0
    assert K.lev("abc" * 100 + "x", "abc" * 100 + "y") == 1


def test_wd8_grammar_introduced_errors_with_fakes():
    from edat.quality import grammar as G

    class Fake:
        def __init__(self, name, issues):
            self.name, self.version, self.issues, self.available = name, "t", issues, True

        def check(self, text):
            return [i for i in self.issues if i.end <= len(text) and "bad" in text[i.start:i.end]]
    lt = Fake("languagetool", [G.GrammarIssue(0, 3, "R1", "GRAMMAR", "m", "languagetool")])
    hp = Fake("harper", [G.GrammarIssue(0, 3, "h", "X", "m", "harper")])
    c = w4_case()
    r = K.score_writing(GOLD, c, grammar=[lt, hp])
    assert r["metrics"]["introduced_errors"] == 0 and r["metrics"]["grammar_errors_per_100w"] == 0.0
    r = K.score_writing("bad " + GOLD, c, grammar=[lt, hp])
    assert r["metrics"]["introduced_errors"] == 1 and r["metrics"]["grammar_errors_per_100w"] > 0
    assert K.score_writing(GOLD, c)["metrics"]["introduced_errors"] is None


# ---------------------------------------------------------------- WD9

def w6(aud, lead=True):
    pts = [W.point("p1", "report is 2 days late", [W.number_anchor("2")], lead=lead),
           W.point("p2", "dataset arrived", [MON])]
    if aud == "manager":
        pts[0] = W.point("p1", "report is late", [MON], lead=True)
        pts[1] = W.point("p2", "reason", ["dataset"])
    return mk("W6", "register", points=pts, audience=aud, group_id="g1", ref="x",
              recipient={"name": "Dana Okafor", "relation": aud})


def test_wd9_professor_and_customer():
    for aud in ("professor", "customer"):
        c = w6(aud)
        good = "Dear Dana Okafor,\n\nMy report will be 2 days late because the dataset arrived on Mon 25 Nov. I apologise."
        r = K.score_writing(good, c)
        assert st(r, "WD9") == "pass" and r["metrics"]["register_ok"] is True
        assert st(K.score_writing(good.replace("Dear", "Hi"), c), "WD9") == "fail"
        assert st(K.score_writing(good.replace("I apologise", "yeah sorry"), c), "WD9") == "fail"
        assert st(K.score_writing(good + " \U0001F600", c), "WD9") == "fail"
        assert st(K.score_writing(good.replace("will be", "won't be").replace("I apologise", "I can't help it"), c), "WD9") == "fail"
        one = good.replace("will be", "won't be")
        assert st(K.score_writing(one, c), "WD9") == "pass"
    assert st(K.score_writing("Hello Dana Okafor,\n\nMy report will be 2 days late. Dataset Mon 25 Nov.", w6("customer")), "WD9") == "pass"


def test_wd9_friend():
    c = w6("friend")
    assert st(K.score_writing("Hey, my report is 2 days late, dataset arrived Mon 25 Nov.", c), "WD9") == "pass"
    assert st(K.score_writing("Dear friend, 2 days late. Mon 25 Nov.", c), "WD9") == "fail"
    assert st(K.score_writing("Late by 2 days. Mon 25 Nov.\n\nSincerely,\nMarcus", c), "WD9") == "fail"
    assert st(K.score_writing("I sincerely think 2 days late. Mon 25 Nov", c), "WD9") == "pass"


def test_wd9_manager_lead_in_first_two_sentences():
    c = w6("manager")
    good = "Hi Dana,\n\nThe report slips to Mon 25 Nov. A dataset arrived late. More soon."
    assert st(K.score_writing(good, c), "WD9") == "pass"
    late = "Hi Dana,\n\nA dataset arrived late. We looked into it. The report slips to Mon 25 Nov."
    r = K.score_writing(late, c)
    assert st(r, "WD9") == "fail" and r["metrics"]["register_ok"] is False


def test_wd9_na_without_audience():
    r = K.score_writing(GOOD, email_case())
    assert st(r, "WD9") == "n/a" and r["metrics"]["register_ok"] is None


# ---------------------------------------------------------------- WD10

SAMPLES = ["ok sure. see you at 8 lol", "cant make it tonight, tomorrow?", "yeah that works for me", "on my way now"]


def w5_voice(**pm):
    return mk("W5", "voice", points=[W.point("p1", "bill", [W.amount_anchor("96.40")])],
              context={"samples": SAMPLES}, pref_meta=pm, ref="x", recipient=None)


def test_wd10_is_na_with_number():
    c = w5_voice(neutral_reference="Dear flatmate, I would like to propose that we divide the electricity bill of 96.40.")
    r = K.score_writing("ok so the bill is 96.40, can you send half? lol", c)
    assert st(r, "WD10") == "n/a"
    assert isinstance(r["metrics"]["voice_delta"], float)
    assert r["metrics"]["voice_delta"] < 0          # closer to the samples than the neutral text
    formal = K.score_writing("Dear flatmate, I would like to propose that we divide the bill of 96.40 evenly.", c)
    assert formal["metrics"]["voice_delta"] > r["metrics"]["voice_delta"]
    assert r["pass"] in (True, False)               # WD10 never blocks


def test_wd10_none_without_neutral_or_samples():
    assert K.score_writing("the bill is 96.40", w5_voice())["metrics"]["voice_delta"] is None
    assert K.score_writing("x", email_case())["metrics"]["voice_delta"] is None


# ---------------------------------------------------------------- WD11

ENT = ["Okafor", "Ines Park", "12,480"]


def w7_case(**kw):
    return mk("W7", "consistency", points=[W.point("p1", "total", [W.amount_anchor("12480")])], entities=ENT,
              turns=["Write a project update naming Okafor, Ines Park and a total of 12,480."], ref="x",
              recipient=None, **kw)


def test_wd11_consistent_passes_variant_fails():
    c = w7_case()
    ok = "Okafor and Ines Park agreed. The total is 12,480. Okafor will report."
    r = K.score_writing(ok, c)
    assert st(r, "WD11") == "pass" and r["metrics"]["consistency_ok"] is True
    for bad in ("Okafor agreed. Later okafor spoke. Total 12,480.",
                "Okafor agreed. Okafer spoke. Total 12,480.",
                "Okafor and Ines Park. Total 12,480 then 12,840 again.",
                "Okafor and Ines Park. Total 12,480 then 12.480 again.",
                "Okafor and Ines Park. Okafors said so. Total 12,480."):
        r = K.score_writing(bad, c)
        assert st(r, "WD11") == "fail", bad
        assert r["metrics"]["consistency_ok"] is False


def test_wd11_na_without_entities():
    assert st(K.score_writing(GOOD, email_case()), "WD11") == "n/a"


def test_wd11_outline_order():
    pts = [W.point("p1", "budget", ["budget"]), W.point("p2", "travel", ["travel"]), W.point("p3", "claims", ["claims"])]
    c = mk("W7", "outline", points=pts, turns=["Outline it.", "Now write it."], reference_turns=["o", "x"], ref="x",
           recipient=None)
    outline = "1. Budget\n2. Travel\n3. Claims"
    good = "The budget is set. Travel comes next. Claims close it."
    assert st(K.score_writing(good, c, outline=outline), "WD11") == "pass"
    swapped = "Travel comes first. The budget is set. Claims close it."
    assert st(K.score_writing(swapped, c, outline=outline), "WD11") == "fail"
    assert st(K.score_writing("The budget is set. Travel comes next.", c, outline=outline), "WD11") == "fail"
    # without an outline argument the brief's point order is used
    assert st(K.score_writing(good, c), "WD11") == "pass"
    assert st(K.score_writing(swapped, c), "WD11") == "fail"
    # the model's own outline order governs
    outline2 = "1. Claims\n2. Budget\n3. Travel"
    assert st(K.score_writing("Claims first. Budget next. Travel last.", c, outline=outline2), "WD11") == "pass"


# ---------------------------------------------------------------- score, gate, helpers

def test_score_empty_and_off_brief_zero():
    a, b = email_case(seed=1), mk(points=[W.point("p1", "x", ["Zebra Lane"])], ref="Zebra Lane is where we meet.", seed=2)
    assert K.score_writing("", a)["score"] == 0
    assert K.score_writing(b["reference_draft"], a)["score"] == 0
    assert K.empty_or_off_brief_scores_zero(a, b)
    w4 = w4_case()
    assert K.empty_or_off_brief_scores_zero(w4, a) and K.empty_or_off_brief_scores_zero(w3_case(), a)


def test_reference_scores_one_and_metrics_are_flat():
    c = email_case()
    r = K.score_writing(c["reference_draft"], c)
    assert r["score"] == 1.0 and r["pass"] and r["gate_pass"] and r["version"] == K.WRITING_CHECKS_VERSION
    for k, v in r["metrics"].items():
        assert v is None or isinstance(v, (bool, int, float, str)), k
    need = {"point_recall", "points_anchored", "points_made", "atoms", "invented_atoms", "invented_rate", "forbidden",
            "constraint_ok", "fact_keep_ok", "fix_rate", "overedit", "flag_rate", "protected_ok", "register_ok",
            "voice_delta", "consistency_ok", "word_count", "grammar_errors_per_100w", "introduced_errors"}
    assert need <= set(r["metrics"])
    assert r["metrics"]["constraint_ok"] is True and r["metrics"]["grammar_errors_per_100w"] is None


def test_gate_pass_semantics():
    c = email_case()
    # form failure outside the gate (WD2 invented date) keeps the gate open, fails pass, lowers score
    inv = GOOD.replace("Thanks,", "I'll send it by Wed 27 Nov.\n\nThanks,")
    c80 = email_case(constraints={"max_words": 80})
    r = K.score_writing(inv, c80)
    assert r["gate_pass"] and not r["pass"] and 0 < r["score"] < 1
    # WD5 failure closes the gate
    r = K.score_writing(GOOD.replace("Hi Dana,", "Hello Dana,"), c)
    assert not r["gate_pass"] and not r["pass"]
    # WD4 failure alone does not close the gate
    r = K.score_writing(GOOD.replace("moves", "moves for Dana okafor"), c)
    assert r["gate_pass"] and not r["pass"] and st(r, "WD4") == "fail"
    assert r["metrics"]["constraint_ok"] is False


def test_constraint_ok_includes_wd4_wd5_wd7():
    c = email_case()
    assert K.score_writing("Here's a draft:\n" + GOOD, c)["metrics"]["constraint_ok"] is False


def test_case_anchor_keys_excludes_allowed_text():
    c = mk(points=[P_DATE, W.point("p2", "who", ["Brightline"]), W.point("p3", "n", [W.number_anchor("12480")])],
           must_not=[W.forbid("f1", "x", ["Zorbal"])], allowed_text="Zorbal and the profile", ref="x")
    keys = K.case_anchor_keys(c)
    assert ("text", "brightline") in keys and ("text", "zorbal") not in keys
    assert any(k[0] == "date" for k in keys) and ("number", "12480") in keys
    w4 = w4_case()
    assert ("text", "recieve") in K.case_anchor_keys(w4)
    d = mk(points=[P_DATE], ref="x")
    same = mk(points=[W.point("p9", "other", [W.date_anchor(dt.date(2030, 11, 25))])], ref="y", seed=3)
    assert K.case_anchor_keys(d) & K.case_anchor_keys(same)


def test_case_anchor_keys_facts():
    keys = K.case_anchor_keys(w3_case())
    assert ("text", "18 quay lane") in keys and ("number", "4471") in keys


def test_empty_draft_never_crashes_on_any_shape():
    for c in (email_case(), w4_case(), w3_case(), w7_case(), w6("manager"), w5_voice()):
        r = K.score_writing("", c)
        assert r["score"] == 0 and not r["gate_pass"] or c["sub"] in ("W4",)


def test_wd4_lowercase_common_noun_inside_a_place_name_is_not_a_name_error():
    c = mk(points=[W.point("p1", "meet at Bramble Market on Mon 25 Nov", ["Bramble Market", MON])])
    assert st(K.score_writing("Meet me at Bramble Market, by the market gate, Mon 25 Nov.", c), "WD4") == "pass"
    assert st(K.score_writing("Meet me at Bramble market on Mon 25 Nov.", c), "WD4") == "fail"


def test_wd4_function_words_inside_a_title_are_not_name_words():
    c = mk(points=[W.point("p1", "the book is The Salt Road", ["The Salt Road"])])
    assert st(K.score_writing("I finished the book you lent me, The Salt Road.", c), "WD4") == "pass"


def test_wd4_lowercase_recipient_name_fails():
    c = email_case()
    assert st(K.score_writing("Hi dana, the review moves to Mon 25 Nov.", c), "WD4") == "fail"
