"""Unit tests for the shared preference checkers P1-P9 (spec 03 s4.5).

All examples are invented. The larger example set (20 passing and 20 failing
per checker) is in test_fixtures/quality/checker_examples.jsonl and is run by
tests/test_edat_quality_checker_examples.py.
"""
from __future__ import annotations

import pytest

from edat.quality.checkers import PREF_IDS, SCOPE, Meta, check, check_all, pref_check_rate

Y = 2027  # case year for date examples; 9 Mar 2027 is a Tuesday

PASS = [
    ("P1", "Your ferry code is FX-20913.", {"answer_key": ["FX-20913"], "request": "What's my ferry code?"}),
    ("P1", "Four things on Tuesday:\n- 09:30 Dentist\n- 12:00 Lunch with Ines", {"answer_key": ["09:30"]}),
    ("P1", "Yes, the pharmacy opens at 10:00 on Sunday.", {"answer_key": ["10:00"]}),
    ("P1", "Based on your calendar, the dentist is at 09:30.", {"answer_key": ["09:30"]}),
    ("P1", "- **Ines:** 42.10 EUR by Fri 12 Mar", {"answer_key": ["42.10"]}),
    ("P2", "The dentist is at 09:30. Bring your card.", {}),
    ("P2", "Three things today:\n- 09:30 Dentist\n- 12:00 Lunch\n- 19:00 Choir", {}),
    ("P2", "Yes. It opens at 10:00.", {}),
    ("P2", "```\nline one. line two. line three. line four.\n```\nThat is the log.", {}),
    ("P3", "- Dentist\n- Lunch\n- Choir", {"expected_items": 3}),
    ("P3", "Today:\n* Dentist\n* Lunch\n* Choir\n* Gym", {"expected_items": 4}),
    ("P3", "1. Unplug it\n2. Wait\n3. Plug it in", {"expected_items": 3, "qtype": "steps"}),
    ("P3", "- **Ines:** 42.10 EUR\n- **Tomas:** 18.00 EUR\n- **Aiko:** 7.50 EUR", {"expected_items": 3}),
    ("P4", "- **Ines:** 42.10 EUR\n- **Tomas:** 18.00 EUR", {}),
    ("P4", "The rate is 3 | 4 depending on the plan.", {}),
    ("P4", "Plain prose with  two  spaces in one line only.", {}),
    ("P5", "All done, see you at 18:00.", {}),
    ("P5", "Thanks :)", {}),
    ("P5", 'She wrote "great news \U0001F389" in the chat.', {"source_text": "Ines: great news \U0001F389"}),
    ("P6", "The train leaves at 15:40.", {"source_times": ["15:40"]}),
    ("P6", "From 09:00 to 17:30.", {}),
    ("P6", "Leave at 0:45 for the night bus.", {}),
    ("P7", "The review is on Tue 9 Mar.", {"case_year": Y}),
    ("P7", "It moved to Thu 11 Mar, see you tomorrow.", {"case_year": Y}),
    ("P7", "The lease ends Fri 31 Mar 2028.", {"case_year": Y}),
    ("P7", "Choir is every Tuesday, next on Tue 9 Mar.", {"case_year": Y}),
    ("P8", "It is 8 km away and weighs 2 kg.", {}),
    ("P8", "That costs 12 pounds (£12).", {}),
    ("P8", "About 8.0 km.", {"source_quantities": [{"value": 5, "unit": "mi"}]}),
    ("P8", "Room 12 is in the east wing.", {}),
    ("P9", "Hi Ines,\nThanks for the update.\n\nBest,\nTest", {"signoff": "Best,\nTest"}),
    ("P9", "Hello,\nSee you then.\nCheers, Test", {"signoff": "Cheers, Test"}),
]

FAIL = [
    ("P1", "Great question! Your ferry code is FX-20913.", {"answer_key": ["FX-20913"]}),
    ("P1", "You asked about your ferry. I looked through your emails. The code is FX-20913.", {"answer_key": ["FX-20913"]}),
    ("P1", "What is my ferry code? Let me see.", {"answer_key": ["FX-20913"], "request": "What is my ferry code?"}),
    ("P1", "", {"answer_key": ["FX-20913"]}),
    ("P2", "One. Two. Three. Four.", {}),
    ("P2", "First line. Second line.\n- a\n- b\nThird. Fourth.", {}),
    ("P2", "- " + "word " * 25 + ". Another sentence here.\n- short\nEnd. More.", {}),
    ("P3", "You have the dentist, lunch and choir.", {"expected_items": 3}),
    ("P3", "- Dentist\n- Lunch", {"expected_items": 3}),
    ("P3", "1. Dentist\n2. Lunch\n3. Choir", {"expected_items": 3, "qtype": "set"}),
    ("P4", "| Name | Amount |\n|---|---|\n| Ines | 42.10 |", {}),
    ("P4", "<table><tr><td>Ines</td></tr></table>", {}),
    ("P4", "Name    Amount    Due\nInes    42.10     Fri\nTomas   18.00     Mon", {}),
    ("P4", "```\nName    Amount    Due\nInes    42.10     Fri\nTomas   18.00     Mon\n```", {}),
    ("P5", "All done ✅", {}),
    ("P5", "Have fun \U0001F389", {}),
    ("P5", "Trip to \U0001F1F3\U0001F1F4 booked", {}),
    ("P5", "Press 1️⃣ to confirm", {}),
    ("P5", "Love it ❤️", {}),
    ("P6", "The call is at 3pm.", {}),
    ("P6", "The call is at 3:00 p.m.", {}),
    ("P6", "Lunch at noon.", {}),
    ("P6", "Meet at 3 o'clock.", {}),
    ("P6", "The call is at 3:00.", {"source_times": ["15:00"]}),
    ("P7", "The review is on Wed 9 Mar.", {"case_year": Y}),
    ("P7", "The review is on 2027-03-09.", {"case_year": Y}),
    ("P7", "The review is on March 9.", {"case_year": Y}),
    ("P7", "The review is on 09/03.", {"case_year": Y}),
    ("P7", "The review is on Tuesday, 9 March.", {"case_year": Y}),
    ("P7", "See you on Tuesday.", {"case_year": Y}),
    ("P7", "Rent is due on the 12th.", {"case_year": Y}),
    ("P7", "The review is on Tue 9 Mar 2027.", {"case_year": Y}),
    ("P8", "It is 5 miles away.", {}),
    ("P8", "It weighs 3 lbs.", {}),
    ("P8", "Set the oven to 350 °F.", {}),
    ("P8", "The screen is 13 in.", {}),
    ("P8", "About 9 km.", {"source_quantities": [{"value": 5, "unit": "mi"}]}),
    ("P9", "Hi Ines,\nThanks.\n\nBest,\n[Your name]", {"signoff": "Best,\nTest"}),
    ("P9", "Hi Ines,\nThanks.\n\nKind regards,\nTest", {"signoff": "Best,\nTest"}),
    ("P9", "Best,\nTest\n\nHi Ines,\nThanks.\n\nBest,\nTest", {"signoff": "Best,\nTest"}),
    ("P9", "Hi Ines,\nBest,\nTest\nP.S. see you", {"signoff": "Best,\nTest"}),
]


def _surface(pid: str) -> str:
    return "draft_email" if pid == "P9" else "answer"


@pytest.mark.parametrize("pid,text,meta", PASS)
def test_passing_examples(pid, text, meta):
    r = check(pid, text, _surface(pid), meta)
    assert r.status == "pass", (pid, text, r.reason)


@pytest.mark.parametrize("pid,text,meta", FAIL)
def test_failing_examples(pid, text, meta):
    r = check(pid, text, _surface(pid), meta)
    assert r.status == "fail", (pid, text, r.reason)


def test_every_checker_has_examples_both_ways():
    for pid in PREF_IDS:
        assert any(p == pid for p, _, _ in PASS), pid
        assert any(p == pid for p, _, _ in FAIL), pid


@pytest.mark.parametrize("pid", PREF_IDS)
def test_out_of_scope_surfaces_are_na(pid):
    for surface in ("answer", "draft_email", "draft_message", "json", "tool_args"):
        r = check(pid, "Done at 3pm ✅ | x |\n|---|---|", surface, {"answer_key": ["x"], "expected_items": 3,
                                                                         "signoff": "Best"})
        if surface not in SCOPE[pid]:
            assert r.status == "n/a", (pid, surface)


@pytest.mark.parametrize("pid", PREF_IDS)
def test_inactive_preference_is_na(pid):
    assert check(pid, "Meet at 3pm ✅", _surface(pid), {"active": False}).status == "n/a"


def test_na_when_nothing_to_check():
    assert check("P1", "Anything.", "answer", {}).status == "n/a"
    assert check("P2", "One. Two. Three. Four.", "answer", {"detail_requested": True}).status == "n/a"
    assert check("P3", "a, b", "answer", {"expected_items": 2}).status == "n/a"
    assert check("P6", "No times here.", "answer", {}).status == "n/a"
    assert check("P7", "No dates here.", "answer", {"case_year": Y}).status == "n/a"
    assert check("P9", "Hi", "draft_email", {}).status == "n/a"


def test_needs_time_and_date_fail_when_missing():
    assert check("P6", "Soon.", "answer", {"needs_time": True}).status == "fail"
    assert check("P7", "Soon.", "answer", {"needs_date": True, "case_year": Y}).status == "fail"


def test_emoticon_is_reported_not_failed():
    r = check("P5", "Nice :)", "answer", {})
    assert r.status == "pass" and r.notes


def test_meta_accepts_dict_and_ignores_unknown_keys():
    r = check("P6", "At 15:00.", "answer", {"source_times": ["15:00"], "unrelated": 1})
    assert r.status == "pass"
    assert Meta.of(None) == Meta()


def test_check_all_and_rate():
    res = check_all("Meet at 3pm on Tue 9 Mar.", "answer", {"case_year": Y})
    assert res["P6"].status == "fail" and res["P7"].status == "pass"
    rate = pref_check_rate(res.values())
    assert rate["P6"]["rate"] == 0.0 and rate["P7"]["rate"] == 1.0


def test_unknown_ids_raise():
    with pytest.raises(KeyError):
        check("P10", "x")
    with pytest.raises(ValueError):
        check("P1", "x", "email")
