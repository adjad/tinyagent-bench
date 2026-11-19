"""Tests for the seeded noisy-request variants (spec 03 section 7.8). All data invented."""
from __future__ import annotations

import re

import pytest

from edat.quality.noise import NOISE_KINDS, casual, make_noisy, self_correction, typos, voice
from edat.quality.world import CITIES

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]

REQUEST = ("What's the booking reference for my trip on Thursday 18 March at 15:30, the code FX-25086 and the "
           "1,250.40 EUR deposit, please tell me about their plans right now with my little friends because "
           "it all matters, thanks.")
CODE_RE = re.compile(r"[A-Z]{1,6}-\d+")
NUMBER_RE = re.compile(r"\d[\d:.,]*")


def words(text: str) -> list[str]:
    return text.split(" ")


# ---------------------------------------------------------------------------
# Seeding and determinism
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["typos", "casual", "voice"])
def test_kind_is_deterministic_for_a_seed(kind):
    assert make_noisy(REQUEST, kind, 7) == make_noisy(REQUEST, kind, 7)
    assert make_noisy(REQUEST, kind, 8) == make_noisy(REQUEST, kind, 8)


@pytest.mark.parametrize("kind", ["typos", "voice"])
def test_kind_varies_with_the_seed(kind):
    outs = {make_noisy(REQUEST, kind, s) for s in range(12)}
    assert len(outs) > 3


def test_casual_has_no_randomness_but_still_takes_a_seed():
    assert make_noisy(REQUEST, "casual", 1) == make_noisy(REQUEST, "casual", 99)


def test_self_correction_is_deterministic_for_a_seed():
    q = "How much did we spend on groceries in March?"
    assert make_noisy(q, "self_correction", 3) == make_noisy(q, "self_correction", 3)
    assert len({make_noisy(q, "self_correction", s) for s in range(20)}) > 1


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError):
        make_noisy("hello there", "shouting", 1)


def test_noise_kinds_are_the_four_the_spec_names():
    assert set(NOISE_KINDS) == {"typos", "casual", "voice", "self_correction"}


def test_noise_does_not_mutate_global_random_state():
    import random
    random.seed(123)
    before = random.random()
    random.seed(123)
    for kind in ("typos", "casual", "voice"):
        make_noisy(REQUEST, kind, 4)
    assert random.random() == before


# ---------------------------------------------------------------------------
# Typos
# ---------------------------------------------------------------------------

def changed_words(clean: str, noisy: str) -> list[tuple[str, str]]:
    a, b = words(clean), words(noisy)
    assert len(a) == len(b), "typos must not add or remove words"
    return [(x, y) for x, y in zip(a, b) if x != y]


def test_typos_edit_at_least_one_word_and_keep_the_word_count():
    for s in range(30):
        assert changed_words(REQUEST, typos(REQUEST, __import__("random").Random(s)))


def test_typos_edit_one_word_in_eight_at_most():
    k = len(words(REQUEST)) // 8
    assert k >= 3
    for s in range(60):
        n = len(changed_words(REQUEST, make_noisy(REQUEST, "typos", s)))
        assert 1 <= n <= k


def test_typos_make_exactly_one_edit_per_eight_words():
    k = len(words(REQUEST)) // 8
    for s in range(60):
        assert len(changed_words(REQUEST, make_noisy(REQUEST, "typos", s))) == k


def test_short_request_still_gets_one_typo():
    q = "How many days remain?"
    assert len(changed_words(q, make_noisy(q, "typos", 5))) == 1


def test_typos_never_touch_codes_numbers_or_names():
    protected = [w for w in words(REQUEST) if CODE_RE.fullmatch(w.strip("?,.!")) or NUMBER_RE.fullmatch(w.strip("?,.!"))
                 or w in ("Thursday", "March")]
    assert protected
    for s in range(80):
        noisy = words(make_noisy(REQUEST, "typos", s))
        clean = words(REQUEST)
        for i, w in enumerate(clean):
            if w in protected:
                assert noisy[i] == w, (s, w, noisy[i])


def test_typos_leave_a_code_only_request_alone_when_nothing_is_editable():
    q = "FX-25086 18 March 1,250.40"
    assert make_noisy(q, "typos", 1) == q


def test_typos_edits_are_single_character_changes():
    import difflib
    for s in range(40):
        for old, new in changed_words(REQUEST, make_noisy(REQUEST, "typos", s)):
            o, n = old.strip("?,.!"), new.strip("?,.!")
            # swap shows as two substitutions, every other edit as one operation
            ops = [op for op in difflib.SequenceMatcher(None, o, n).get_opcodes() if op[0] != "equal"]
            assert 1 <= len(ops) <= 2, (old, new)
            assert abs(len(o) - len(n)) <= 1


def test_typos_keep_the_currency_code_of_an_amount():
    for s in range(60):
        assert "EUR" in make_noisy(REQUEST, "typos", s)


# ---------------------------------------------------------------------------
# Casual typing
# ---------------------------------------------------------------------------

def test_casual_is_lowercase_except_codes_and_has_no_trailing_punctuation():
    out = make_noisy(REQUEST, "casual", 1)
    assert "FX-25086" in out
    rest = out.replace("FX-25086", "")
    assert rest == rest.lower()
    for w in words(out):
        assert not re.search(r"[?!.,]$", w), w
    assert not any(w == "" for w in words(out))


def test_casual_keeps_every_code_exact_even_with_punctuation_around_it():
    q = "Is VERIFY-7731 real? Check INV-22427, then QX-4471."
    out = casual(q, __import__("random").Random(0))
    for code in ("VERIFY-7731", "INV-22427", "QX-4471"):
        assert code in words(out)


def test_casual_keeps_numbers_and_times_intact():
    out = make_noisy("Meet at 15:30 for 1,250.40 EUR, please.", "casual", 1)
    assert "15:30" in out and "1,250.40" in out


def test_casual_applies_common_shortenings():
    out = make_noisy("Please tell me about it tomorrow because thanks", "casual", 1)
    assert out == "pls tell me abt it tmrw bc thx"


# ---------------------------------------------------------------------------
# Voice transcript
# ---------------------------------------------------------------------------

def test_voice_speaks_clock_times_and_small_numbers():
    out = make_noisy("Call me at 3:30 or 9:00 and then 45 minutes later", "voice", 2)
    assert "three thirty" in out
    assert "nine" in out and "9:00" not in out
    assert "forty-five" in out
    assert "3:30" not in out


def test_voice_speaks_the_afternoon_time_of_a_request():
    assert "fifteen thirty" in make_noisy(REQUEST, "voice", 2)


def test_voice_adds_fillers_run_on_ending_and_drops_final_punctuation():
    seen = set()
    for s in range(15):
        out = make_noisy(REQUEST, "voice", s)
        assert out.endswith("and yeah that's it")
        assert "?" not in out.split("and yeah")[0][-3:]
        seen |= {f for f in (" um ", " uh ", " like ", " so ", " you know ") if f in out}
    assert len(seen) >= 3


def test_voice_leaves_codes_untouched():
    for s in range(10):
        assert "FX-25086" in make_noisy(REQUEST, "voice", s)


def test_voice_swaps_homophones_only_from_the_table():
    outs = " ".join(make_noisy("their plans are right for your friends, to know", "voice", s) for s in range(30))
    assert "there plans" in outs or "write" in outs or "four" in outs
    assert "banana" not in outs


# ---------------------------------------------------------------------------
# Self-correction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("month", MONTHS)
def test_self_correction_month_keeps_the_original_month_last(month):
    q = f"How much did we spend on groceries in {month}?"
    for s in range(6):
        out = make_noisy(q, "self_correction", s)
        m = re.fullmatch(r"How much did we spend on groceries in (\w+), no wait, (\w+)\?", out)
        assert m, out
        assert m.group(2) == month          # the corrected (original, gold) value comes last
        assert m.group(1) != month and m.group(1) in MONTHS


def test_self_correction_last_month_mentioned_is_the_gold_value():
    out = make_noisy("Did we spend more on energy in January or in March?", "self_correction", 3)
    assert out is not None and "no wait" in out
    assert out.index("no wait") < out.rindex("January") or out.index("no wait") < out.rindex("March")
    after = out.split("no wait,")[1]
    assert after.strip().split("?")[0].split()[0] in ("January",)


def test_self_correction_returns_none_when_nothing_is_correctable():
    for q in ("How many evenings am I free?", "What is my booking code?", "When is it on Tuesday?", ""):
        assert make_noisy(q, "self_correction", 1) is None


def test_self_correction_destination_city():
    out = make_noisy("How many days until my flight to Lisbon?", "self_correction", 1)
    m = re.fullmatch(r"How many days until my flight to (\w+), no wait, to Lisbon\?", out)
    assert m and m.group(1) in CITIES and m.group(1) != "Lisbon"


def test_self_correction_named_alternatives():
    out = self_correction("Which is bigger, Gas or Electricity?", __import__("random").Random(1),
                          alternatives=["Gas", "Electricity"])
    assert out is not None
    assert out.count("no wait") == 1
    assert "Electricity, no wait, Gas" in out


def test_self_correction_alternatives_need_a_second_value():
    assert self_correction("Which is bigger, Gas?", __import__("random").Random(1), alternatives=["Gas"]) is None


def test_self_correction_does_not_change_the_rest_of_the_request():
    q = "How much did we spend on groceries in March?"
    out = make_noisy(q, "self_correction", 4)
    assert out.startswith("How much did we spend on groceries in ")
    assert out.endswith(", no wait, March?")
