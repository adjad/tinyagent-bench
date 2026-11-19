"""Unit tests for the readability hygiene layer D1-D11 (spec 03 s4.2, s4.5).

All examples are invented (persona "Test User", .example domains), English only.
"""
from __future__ import annotations

import pytest

from edat.quality import hygiene as H
from edat.quality.hygiene import (
    CHECKS, EXPECTED_SHAPES, HYGIENE_WEIGHTS, LEAK_FAMILIES, classify_shape, expected_shapes,
    hygiene, prose_proxies,
)

CTX = {"active_prefs": frozenset()}


def run(cid, text, case=None, prefs=()):
    ctx = {"active_prefs": frozenset(p.upper() for p in prefs)}
    return CHECKS[cid](text, case or {}, ctx)


def status(cid, text, case=None, prefs=()):
    return run(cid, text, case, prefs)[0]


# --------------------------------------------------------------------------
# classify_shape
# --------------------------------------------------------------------------

SHAPE_CASES = [
    ("sentence", "The dentist is at 09:30."),
    ("sentence", "Yes. The pharmacy opens at 10:00 on Sunday."),
    ("paragraphs", "Your bill rose because the plan changed. The new rate started in March. A one-off fee also applied."),
    ("paragraphs", "First paragraph here.\n\nSecond paragraph follows. It has two sentences. And a third."),
    ("bullets", "- Dentist\n- Lunch\n- Choir"),
    ("bullets", "Three things today:\n* Dentist\n* Lunch\n* Choir"),
    ("bullets", "Overall it went fine.\n\n- Item one\n- Item two\n- Item three\n\nThat is all."),
    ("numbered", "1. Unplug the router\n2. Wait ten seconds\n3. Plug it back in"),
    ("numbered", "Do this:\n1. Open settings\n2. Tap reset"),
    ("label-list", "- **Ines:** 42.10 EUR\n- **Tomas:** 18.00 EUR\n- **Aiko:** 7.50 EUR"),
    ("label-list", "Who owes what:\n- **Ines** - 42.10 EUR\n- **Tomas** - 18.00 EUR"),
    ("mixed", "## Plan\n- One\n- Two"),
    ("mixed", "- One\n- Two\n\n1. Three\n2. Four"),
    ("mixed", "| a | b |\n| --- | --- |\n| 1 | 2 |"),
    ("mixed", "Intro.\n\n- One\n- Two\n\nMiddle text.\n\n- Three\n- Four"),
    ("mixed", "```\ncode\n```"),
]


@pytest.mark.parametrize("label,text", SHAPE_CASES)
def test_classify_shape(label, text):
    assert classify_shape(text) == label


def test_every_label_is_covered():
    assert {l for l, _ in SHAPE_CASES} == set(H.SHAPES)


def test_label_list_threshold_is_70_percent():
    seven = "\n".join(["- **A:** x"] * 7 + ["- plain"] * 3)      # exactly 70 %
    six = "\n".join(["- **A:** x"] * 6 + ["- plain"] * 4)        # 60 %
    assert classify_shape(seven) == "label-list"
    assert classify_shape(six) == "bullets"


def test_expected_shapes_table_and_window_variations():
    assert EXPECTED_SHAPES["steps"] == (frozenset({"numbered"}), frozenset())
    assert "label-list" in EXPECTED_SHAPES["records"][0]
    assert "table" not in EXPECTED_SHAPES["compare"][1]
    assert "table" in expected_shapes("compare", "window")[1]
    assert "table" in expected_shapes("records", "window")[1]
    assert "table" not in expected_shapes("set", "window")[1]
    assert expected_shapes("format") is None


# --------------------------------------------------------------------------
# D1 .. D11: three passing and three failing invented answers each
# --------------------------------------------------------------------------

D1_PASS = [
    "The dentist is at **09:30** on Tuesday.",
    "- **Ines:** 42.10 EUR\n- **Tomas:** 18.00 EUR\n- **Aiko:** 7.50 EUR",
    "Run `reset` first.\n\n```\nreset --all\n```\n\nThen use my_file_name.txt and 3 * 4.",
    "1. Unplug it\n2. Wait\n3. Plug it in",
]
D1_FAIL = [
    "```\nreset --all\nThen wait.",
    "The dentist is at **09:30 on Tuesday.",
    "This is *odd and unclosed.",
    "Run `reset first.",
    "1. Unplug it\n2. Wait\n4. Plug it in",
    "- one\n* two\n+ three",
]


@pytest.mark.parametrize("t", D1_PASS)
def test_d1_pass(t):
    assert status("D1", t) == "pass"


@pytest.mark.parametrize("t", D1_FAIL)
def test_d1_fail(t):
    assert status("D1", t) == "fail"


D2_PASS = [
    ("Plain sentence.", {}),
    ("- one\n- two\n- three", {"qtype": "set"}),
    ("| a | b |\n| --- | --- |\n| 1 | 2 |", {"qtype": "compare", "profile": "window"}),
    ("# Heading\n\nText", {"qtype": "explain", "profile": "window"}),
    ("Dear Test User, see you at 10:00.", {"qtype": "draft"}),
    ("Take the 8:15 train and arrive by nine.", {"qtype": "spoken"}),
]
D2_FAIL = [
    ("| a | b |\n| --- | --- |\n| 1 | 2 |", {"qtype": "compare"}),
    ("# Heading\n\nText", {"qtype": "explain"}),
    ("- one\n  - nested\n- two", {"qtype": "set"}),
    ("Text\n\n---\n\nMore", {}),
    ("<div>hi</div>", {"profile": "window"}),
    ("![alt](https://pic.example/x.png)", {}),
    ("Dear Test User, **see** you at 10:00.", {"qtype": "draft"}),
    ("- one\n- two", {"qtype": "spoken"}),
    ("| a | b |\n| --- | --- |\n| 1 | 2 |", {"qtype": "set", "profile": "window"}),
]


@pytest.mark.parametrize("t,c", D2_PASS)
def test_d2_pass(t, c):
    assert status("D2", t, c) == "pass"


@pytest.mark.parametrize("t,c", D2_FAIL)
def test_d2_fail(t, c):
    assert status("D2", t, c) == "fail"


D3_PASS = [
    ("The dentist is at 09:30.", {"qtype": "fact"}),
    ("- Dentist\n- Lunch\n- Choir", {"qtype": "set"}),
    ("1. Open it\n2. Press reset", {"qtype": "steps"}),
    ("- **Ines:** 42.10 EUR\n- **Tomas:** 18.00 EUR", {"qtype": "records"}),
    ("Take the train.\n\n- Faster\n- Cheaper", {"qtype": "compare"}),
    ("Dear Test User, can we move the visit to Friday? Thanks for your patience.", {"qtype": "draft"}),
]
D3_FAIL = [
    ("- one\n- two\n- three", {"qtype": "fact"}),
    ("- Open it\n- Press reset", {"qtype": "steps"}),
    ("Ines owes 42, Tomas owes 18, Aiko owes 7. They pay on Friday. Please settle soon.", {"qtype": "set"}),
    ("| a | b |\n| --- | --- |\n| 1 | 2 |", {"qtype": "compare"}),
    ("Dear Test User, see you.\n\nBest,\nTest User", {"qtype": "draft"}),
    ("Hello,\n\nPlease come at 10.\n\n[Your name]", {"qtype": "draft"}),
    ("Subject: Visit\n\nHello, please come at 10.", {"qtype": "draft"}),
]


@pytest.mark.parametrize("t,c", D3_PASS)
def test_d3_pass(t, c):
    assert status("D3", t, c) == "pass"


@pytest.mark.parametrize("t,c", D3_FAIL)
def test_d3_fail(t, c):
    assert status("D3", t, c) == "fail"


def test_d3_na_for_format_and_empty():
    assert status("D3", "- a\n- b", {"qtype": "format"}) == "n/a"
    assert status("D3", "", {"qtype": "fact"}) == "n/a"


D4_PASS = [
    ("The dentist is at 09:30.", {"answer_key": ["09:30"]}),
    ("Based on your calendar, the dentist is at 09:30.", {"answer_key": ["09:30"]}),
    ("Four things on Tuesday:\n- 09:30 Dentist\n- 12:00 Lunch", {"answer_key": ["09:30"]}),
]
D4_FAIL = [
    ("You have a long and busy day with plenty going on across the whole town, including calls, errands, visits, deliveries and meetings with several people, and later the dentist is at 09:30.", {"answer_key": ["09:30"]}),
    ("Great question! The dentist is at 09:30.", {"answer_key": ["09:30"]}),
    ("The dentist is later today.", {"answer_key": ["09:30"]}),
]


@pytest.mark.parametrize("t,c", D4_PASS)
def test_d4_pass(t, c):
    assert status("D4", t, c) == "pass"


@pytest.mark.parametrize("t,c", D4_FAIL)
def test_d4_fail(t, c):
    assert status("D4", t, c) == "fail"


def test_d4_na_without_key():
    assert status("D4", "Anything.", {}) == "n/a"


D5_PASS = [
    ("one two three four five six", {"reference_words": 6}),
    ("one two three", {"reference_words": 6}),          # exactly 0.5x
    ("one two three four five six seven eight nine ten eleven twelve", {"reference_words": 6}),  # 2.0x
]
D5_FAIL = [
    ("one two", {"reference_words": 6}),
    ("one two three four five six seven eight nine ten eleven twelve thirteen", {"reference_words": 6}),
    ("", {"reference_words": 6}),
]


@pytest.mark.parametrize("t,c", D5_PASS)
def test_d5_pass(t, c):
    assert status("D5", t, c) == "pass"


@pytest.mark.parametrize("t,c", D5_FAIL)
def test_d5_fail(t, c):
    assert status("D5", t, c) == "fail"


def test_d5_na_without_reference():
    assert status("D5", "words here", {}) == "n/a"


LEAK_EXAMPLES = {
    "reasoning_tags": "<think>The date is Tuesday.</think>The dentist is on Tuesday.",
    "gemma_channel": "<|channel>thought\nCheck the date<channel|>The dentist is on Tuesday.",
    "tool_markup": "<|tool_call>call:get_events{day:\"tue\"}<tool_call|>",
    "tool_json": 'I will look. {"name": "get_events", "arguments": {"day": "tue"}}',
    "planning_prose": "The user wants to know the dentist time. The dentist is at 09:30.",
    "template_gemma": "The dentist is at 09:30.<end_of_turn>",
    "template_chatml": "The dentist is at 09:30.<|im_end|>",
    "template_llama": "The dentist is at 09:30.<|eot_id|>",
    "template_phi": "The dentist is at 09:30.<|end|>",
    "template_mistral": "[INST] When is the dentist? [/INST] At 09:30.</s>",
    "template_granite": "The dentist is at 09:30.<|end_of_text|>",
    "template_deepseek": "The dentist is at 09:30.<｜end▁of▁sentence｜>",
    "escaped_text": "Today:\\n- Dentist\\n- Lunch",
}


def test_every_leak_family_has_an_example():
    assert set(LEAK_EXAMPLES) == set(LEAK_FAMILIES)


@pytest.mark.parametrize("fam", sorted(LEAK_EXAMPLES))
def test_d6_one_leak_per_family(fam):
    s, why = run("D6", LEAK_EXAMPLES[fam])
    assert s == "fail", why
    assert fam in why
    assert hygiene(LEAK_EXAMPLES[fam])["leak"] is True


D6_FAIL_EXTRA = [
    "<think>still working on it and never closed",                    # unclosed think
    "",                                                                # empty
    "   \n ",                                                          # blank
    "One moment, let me check.",                                       # stall
    '{"status": "ok", "events": [{"title": "Dentist"}]}',              # raw JSON
    'He said \\"hello\\" to me.',                                      # escaped quotes
    "Let me check your calendar for that.",                            # planning opener
    "We need to find the dentist time. It is 09:30.",
    "I will now call the calendar tool.",
]
D6_PASS = [
    "The dentist is at 09:30.",
    "Let me know if that works. The dentist is at 09:30.",
    "Use `\\n` to start a new line in the file.",
    "- 09:30 Dentist\n- 12:00 Lunch",
    "The user guide says the dentist opens at 09:00.",
]


@pytest.mark.parametrize("t", D6_FAIL_EXTRA)
def test_d6_fail(t):
    assert status("D6", t) == "fail"


@pytest.mark.parametrize("t", D6_PASS)
def test_d6_pass(t):
    assert status("D6", t) == "pass"


def test_d6_json_allowed_when_requested():
    case = {"format_constraints": [{"kind": "json"}]}
    assert status("D6", '{"time": "09:30"}', case) == "pass"


D7_PASS = [
    ("The dentist is at 09:30.", {}),
    ("Yes, it opens at 10:00 on Sunday.", {"request": "Is the pharmacy open Sunday?"}),
    ("- Dentist\n- Lunch", {}),
]
D7_FAIL = [
    ("Great question! The dentist is at 09:30.", {}),
    ("Certainly! The dentist is at 09:30.", {}),
    ("The dentist is at 09:30. Let me know if you need anything else.", {}),
    ("The dentist is at 09:30. I hope this helps.", {}),
    ("Sure! Here's the time: 09:30.", {}),
    ("The dentist appointment is when.", {"request": "When is the dentist appointment?"}),
]


@pytest.mark.parametrize("t,c", D7_PASS)
def test_d7_pass(t, c):
    assert status("D7", t, c) == "pass"


@pytest.mark.parametrize("t,c", D7_FAIL)
def test_d7_fail(t, c):
    assert status("D7", t, c) == "fail"


D8_PASS = [
    ("The dentist is at 09:30.", {}),
    ("- **Ines:** 42.10 EUR\n- **Tomas:** 18.00 EUR\n- **Aiko:** 7.50 EUR", {}),
    ("Dear Test User, the visit is moved to Friday! See you then.", {"qtype": "draft"}),
    ("The trip is booked. " * 30 + "One smiling face \U0001F600 only.", {}),
]
D8_FAIL = [
    ("Done \U0001F600", {}),
    ("Dear Test User, see you \U0001F600 on Friday.", {"qtype": "draft"}),
    ("The **dentist** is at **09:30** on **Tuesday**.", {}),
    ("Great! Wow! The dentist is at 09:30!", {}),
    ("This is URGENT, please act now.", {}),
    ("STOP and read this twice.", {}),
]


@pytest.mark.parametrize("t,c", D8_PASS)
def test_d8_pass(t, c):
    assert status("D8", t, c) == "pass"


@pytest.mark.parametrize("t,c", D8_FAIL)
def test_d8_fail(t, c):
    assert status("D8", t, c) == "fail"


D9_PASS = [
    "The dentist is at 09:30. Lunch is at 12:00. Choir starts at 19:00.",
    "- Dentist at 09:30\n- Lunch at 12:00\n- Choir at 19:00",
    "Yes. Yes.",
]
D9_FAIL = [
    "The dentist is at 09:30. The dentist is at 09:30.",
    "ok fine\nok fine\nok fine\nsomething else",
    ("Your bill went up because the plan changed this month and the new rate applies to every line. "
     "Your bill went up because the plan changed this month and the new rate applies to all lines too, "
     "so the plan changed this month and the new rate applies to every line again and again."),
]


@pytest.mark.parametrize("t", D9_PASS)
def test_d9_pass(t):
    assert status("D9", t) == "pass"


@pytest.mark.parametrize("t", D9_FAIL)
def test_d9_fail(t):
    assert status("D9", t) == "fail"


D10_PASS = [
    ("1. a\n2. b\n3. c", [{"kind": "item_count", "n": 3}, {"kind": "numbered"}, {"kind": "no_intro"}, {"kind": "no_outro"}]),
    ("- red\n- blue", [{"kind": "max_words_per_item", "n": 2}, {"kind": "no_markdown"}][:1]),
    ("It is Tuesday. Bring a coat.", [{"kind": "sentence_count", "n": 2}, {"kind": "no_list"}, {"kind": "max_words", "n": 8}]),
    ("Tuesday at nine.", [{"kind": "one_line"}, {"kind": "no_markdown"}]),
    ('{"time": "09:30"}', [{"kind": "json"}]),
    ("- a\n- b", [{"kind": "item_count", "n": 3, "mode": "at_most"}]),
]
D10_FAIL = [
    ("1. a\n2. b", [{"kind": "item_count", "n": 3}]),
    ("- a\n- b\n- c", [{"kind": "numbered"}]),
    ("Here you go:\n- a\n- b", [{"kind": "no_intro"}]),
    ("- a\n- b\n\nHope that helps.", [{"kind": "no_outro"}]),
    ("It is Tuesday. Bring a coat. Bye.", [{"kind": "sentence_count", "n": 2}]),
    ("- one two three\n- four", [{"kind": "max_words_per_item", "n": 2}]),
    ("one two three four five", [{"kind": "max_words", "n": 4}]),
    ("line one\nline two", [{"kind": "one_line"}]),
    ("Time: 09:30", [{"kind": "json"}]),
    ("- a\n- b", [{"kind": "no_list"}]),
    ("**bold** text", [{"kind": "no_markdown"}]),
]


@pytest.mark.parametrize("t,cons", D10_PASS)
def test_d10_pass(t, cons):
    assert status("D10", t, {"format_constraints": cons}) == "pass"


@pytest.mark.parametrize("t,cons", D10_FAIL)
def test_d10_fail(t, cons):
    assert status("D10", t, {"format_constraints": cons}) == "fail"


def test_d10_na_without_constraints():
    assert status("D10", "anything", {}) == "n/a"
    assert status("D10", "anything", {"format_constraints": []}) == "n/a"


D11_PASS = [
    ("The dentist is at 09:30 on Tuesday.", {}),
    ("Ines asked for a café near the station, and the naïve plan worked.", {}),
    ("The note says \"Le rendez-vous est confirmé pour demain matin avec vous\", so it is fine.",
     {"source_text": "Le rendez-vous est confirmé pour demain matin avec vous"}),
]
D11_FAIL = [
    ("Врач принимает в девять часов утра.", {}),
    ("歯医者の予約は火曜日の午前九時です。", {}),
    ("Le rendez-vous est confirmé pour demain matin avec vous.", {}),
    ("El dentista es en la mañana y los pacientes con cita son para las nueve.", {}),
    ("Der Zahnarzt ist nicht heute und wir sind mit den Kindern auf dem Weg.", {}),
]


@pytest.mark.parametrize("t,c", D11_PASS)
def test_d11_pass(t, c):
    assert status("D11", t, c) == "pass"


@pytest.mark.parametrize("t,c", D11_FAIL)
def test_d11_fail(t, c):
    assert status("D11", t, c) == "fail"


# --------------------------------------------------------------------------
# Conflict rules (spec 4.5)
# --------------------------------------------------------------------------

def test_p1_drops_d4():
    case = {"answer_key": ["09:30"], "qtype": "fact"}
    text = "You have a long and busy day with plenty going on across the whole town, including calls, errands, visits, deliveries and meetings with several people, and later the dentist is at 09:30."
    base = hygiene(text, case)
    assert base["checks"]["D4"]["status"] == "fail" and base["dropped"] == []
    out = hygiene(text, case, active_prefs=["P1"])
    assert "D4" not in out["checks"] and out["dropped"] == ["D4"]


def test_p5_drops_only_d8_emoji_clause():
    t = "The dentist is at 09:30 \U0001F600"
    assert hygiene(t)["checks"]["D8"]["status"] == "fail"
    assert hygiene(t, active_prefs=["P5"])["checks"]["D8"]["status"] == "pass"
    # bold, exclamation marks and all-caps still count under P5
    assert hygiene("Wow! Great! It is **now** time.", active_prefs=["P5"])["checks"]["D8"]["status"] == "fail"
    assert hygiene("Please read this NOW.", active_prefs=["P5"])["checks"]["D8"]["status"] == "fail"


def test_p4_label_list_expected_and_d2_does_not_charge_table():
    table = "Take the train.\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
    case = {"qtype": "compare", "profile": "window"}
    assert status("D2", table, case) == "pass"                      # window, compare: table allowed
    assert status("D3", table, case) == "pass"
    assert status("D3", table, case, prefs=["P4"]) == "fail"         # P4: tables are wrong everywhere
    panel = {"qtype": "compare", "profile": "panel"}
    assert status("D2", table, panel) == "fail"
    assert status("D2", table, panel, prefs=["P4"]) == "pass"        # P4 charges it, not D2
    exp, acc = expected_shapes("records", "window", ["P4"])
    assert exp == frozenset({"label-list"}) and "table" not in acc
    assert status("D3", "- **A:** 1\n- **B:** 2", {"qtype": "records"}, prefs=["P4"]) == "pass"
    assert status("D3", "- A 1\n- B 2", {"qtype": "records"}, prefs=["P4"]) == "pass"   # still acceptable


def test_p2_tightens_d5_with_unit_limit():
    four = "Open the app. Tap settings. Choose reset. Confirm it."          # 4 units, 9 words
    case = {"reference_words": 9}
    assert status("D5", four, case) == "pass"
    assert status("D5", four, case, prefs=["P2"]) == "fail"
    three = "Open the app. Tap settings. Choose reset."
    assert status("D5", three, {"reference_words": 7}, prefs=["P2"]) == "pass"
    assert status("D5", four, {}, prefs=["P2"]) == "fail"                  # no reference: units clause alone


def test_p9_turns_off_draft_signoff_clause_only():
    d = "Dear Test User,\n\nPlease come at 10:00.\n\nBest regards,\nTest Sender"
    case = {"qtype": "draft"}
    assert status("D3", d, case) == "fail"
    assert status("D3", d, case, prefs=["P9"]) == "pass"
    ph = "Dear Test User,\n\nPlease come at 10:00.\n\nBest regards,\n[Your name]"
    assert status("D3", ph, case, prefs=["P9"]) == "fail"                   # placeholder ban stays


@pytest.mark.parametrize("qtype", ["draft", "spoken"])
def test_draft_and_spoken_fail_d2_on_any_markdown(qtype):
    for t in ["This is *important* news.", "See `this`.", "- one\n- two", "Read [it](https://a.example/x)."]:
        assert status("D2", t, {"qtype": qtype}) == "fail"
    assert status("D2", "No markup here at all.", {"qtype": qtype}) == "pass"


# --------------------------------------------------------------------------
# Score arithmetic
# --------------------------------------------------------------------------

def test_weights_table():
    assert HYGIENE_WEIGHTS == {"D1": 1, "D2": 2, "D3": 2, "D4": 2, "D5": 1, "D6": 3,
                               "D7": 1, "D8": 1, "D9": 2, "D10": 2, "D11": 1}


def test_clean_answer_scores_100_with_na_checks_renormalized():
    out = hygiene("The dentist is at 09:30.", {"qtype": "fact"})
    st = {k: v["status"] for k, v in out["checks"].items()}
    assert st["D4"] == "n/a" and st["D5"] == "n/a" and st["D10"] == "n/a"
    assert out["score"] == 100.0 and out["leak"] is False and out["dropped"] == []


def test_score_is_weighted_pass_rate_over_non_na():
    # D4 fails (2), D5 fails (1); everything else that applies passes.
    case = {"qtype": "fact", "answer_key": ["09:30"], "reference_words": 5}
    text = "You have a long and busy day with plenty going on across the whole town, including calls, errands, visits, deliveries and meetings with several people, and later the dentist is at 09:30."
    out = hygiene(text, case)
    st = {k: v["status"] for k, v in out["checks"].items()}
    assert st["D4"] == "fail" and st["D5"] == "fail"
    counted = {k: HYGIENE_WEIGHTS[k] for k, s in st.items() if s != "n/a"}
    passed = sum(w for k, w in counted.items() if st[k] == "pass")
    assert out["score"] == round(100.0 * passed / sum(counted.values()), 1)
    assert 0 < out["score"] < 100


def test_dropped_check_is_renormalized_out():
    case = {"qtype": "fact", "answer_key": ["09:30"]}
    text = "You have a long and busy day with plenty going on across the whole town, including calls, errands, visits, deliveries and meetings with several people, and later the dentist is at 09:30."
    with_d4 = hygiene(text, case)
    without = hygiene(text, case, active_prefs=["P1"])
    assert with_d4["checks"]["D4"]["status"] == "fail"
    assert without["score"] == 100.0 and with_d4["score"] < without["score"]


def test_leak_zero_weight_three():
    # Only D6 fails: 3 of the counted weight is lost.
    out = hygiene("The dentist is at 09:30.<|im_end|>", {"qtype": "fact"})
    counted = sum(HYGIENE_WEIGHTS[k] for k, v in out["checks"].items() if v["status"] != "n/a")
    assert out["leak"] and out["score"] == round(100.0 * (counted - 3) / counted, 1)


def test_empty_answer_fails_d6_and_scores_zero():
    out = hygiene("", {"qtype": "fact", "answer_key": ["09:30"], "reference_words": 5})
    assert out["checks"]["D6"]["status"] == "fail" and out["leak"] is True
    assert out["checks"]["D4"]["status"] == "fail" and out["checks"]["D5"]["status"] == "fail"
    assert out["score"] == 0.0
    assert hygiene("   ")["score"] == 0.0


def test_unclosed_think_is_leak_but_closed_think_is_stripped_for_other_checks():
    out = hygiene("<think>checking the date</think>The dentist is at 09:30.", {"qtype": "fact"})
    assert out["leak"] is True
    assert out["checks"]["D3"]["status"] == "pass" and out["shape"] == "sentence"
    out2 = hygiene("<think>never closed", {"qtype": "fact"})
    assert out2["leak"] is True and out2["score"] < 100.0


# --------------------------------------------------------------------------
# Prose proxies
# --------------------------------------------------------------------------

def test_prose_proxies_basic():
    text = ("The dentist is at nine. Lunch is possibly at noon, and perhaps later. "
            "There are various things to do and a number of errands.")
    p = prose_proxies(text)
    assert p["sentences"] == 3
    assert p["mean_sentence_words"] > 5 and p["p90_sentence_words"] >= p["mean_sentence_words"]
    assert p["share_over_30_words"] == 0.0
    assert p["vague_phrases"] == 2
    assert p["hedges_per_100_words"] > 0
    assert p["flesch_reading_ease"] is not None
    assert 0.0 <= p["redundancy"] <= 1.0


def test_prose_proxies_redundancy_and_long_sentences():
    rep = prose_proxies("The meeting starts at nine today. The meeting starts at nine today!")
    assert rep["redundancy"] >= 0.5
    long_s = " ".join(["word"] * 35) + "."
    assert prose_proxies(long_s + " Short one.")["share_over_30_words"] == 0.5
    empty = prose_proxies("")
    assert empty["sentences"] == 0 and empty["flesch_reading_ease"] is None
