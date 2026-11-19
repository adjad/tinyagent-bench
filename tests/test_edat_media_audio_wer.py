"""Transcript scorer: normalisation rules and WER/CER on hand-made pairs with known counts."""
from __future__ import annotations

import pytest

from edat.media.audio.normalize import EnglishNormalizer, normalize
from edat.media.audio.wer import (corpus_score, has_content, invented_words, is_answer_leak,
                                  score_corpus, score_utterance)
from edat.media.textdist import align


@pytest.mark.parametrize("raw, want", [
    ("Hello, World!", "hello world"),
    ("I won't go [laughs] (inaudible) um there", "i will not go there"),
    ("Mr. Smith's dog", "mister smith is dog"),          # Whisper's 's -> is rule, applied to both sides
    ("It's twenty five past", "it is 25 past"),
    ("one hundred and five people", "105 people"),
    ("two thousand twenty seven", "2027"),
    ("nineteen eighty four", "1984"),
    ("twenty twenty seven", "2027"),
    ("four thirty pm", "4 30 pm"),
    ("4:30 PM", "4 30 pm"),
    ("the twenty third of May", "the 23rd of may"),
    ("first prize", "1st prize"),
    ("two point five percent", "2.5%"),
    ("five dollars and twenty cents", "$5.20"),
    ("$5.20", "$5.20"),
    ("twelve dollars", "$12"),
    ("1,000 metres of colour", "1000 meters of color"),
    ("Café naïve", "cafe naive"),
    ("hmm, uh, okay", "okay"),
    ("call one two three", "call 1 2 3"),
])
def test_normaliser_rules(raw, want):
    assert normalize(raw) == want


def test_normaliser_id_names_the_spelling_map(tmp_path):
    assert EnglishNormalizer().id == "edat-en-norm/1+builtin-spellings"
    p = tmp_path / "english.json"
    p.write_text('{"colour": "color", "grey": "gray"}')
    n = EnglishNormalizer(spelling_map=p)
    assert n.id.startswith("edat-en-norm/1+spellings-sha256:")
    assert n("grey colour") == "gray color"


# (reference, hypothesis, substitutions, deletions, insertions, reference words)
PAIRS = [
    ("the cat sat on the mat", "the cat sat on the mat", 0, 0, 0, 6),
    ("the cat sat on the mat", "the cat sat on mat", 0, 1, 0, 6),
    ("the cat sat on the mat", "the bat sat on the mat", 1, 0, 0, 6),
    ("the cat sat on the mat", "the cat sat down on the mat", 0, 0, 1, 6),
    ("call mom", "", 0, 2, 0, 2),
    ("", "thank you", 0, 0, 2, 0),
    ("set a timer for ten minutes", "set timer for 10 minutes please", 0, 1, 1, 6),
    ("remind me at four thirty", "remind me at 4:30", 0, 0, 0, 5),        # normalised away
    ("text amara that I'll be late", "text tamara that I will be late", 1, 0, 0, 7),
    ("one two three", "three two one", 2, 0, 0, 3),
    ("a b c d", "x y", 2, 2, 0, 4),
]


@pytest.mark.parametrize("ref, hyp, s, d, i, n", PAIRS)
def test_hand_made_pairs_have_known_counts(ref, hyp, s, d, i, n):
    u = score_utterance(ref, hyp)
    assert (u.words.substitutions, u.words.deletions, u.words.insertions) == (s, d, i)
    assert u.words.ref_len == n
    if n:
        assert u.wer == pytest.approx((s + d + i) / n)


def test_corpus_wer_sums_errors_before_dividing():
    pairs = [(f"u{k}", r, h) for k, (r, h, *_ ) in enumerate(PAIRS)]
    score, utts = score_corpus(pairs, bootstrap=0)
    errors = sum(s + d + i for _, _, s, d, i, _ in PAIRS)
    words = sum(n for *_, n in PAIRS)
    ins = sum(i for _, _, _, _, i, _ in PAIRS)
    assert score.wer == pytest.approx(errors / words)
    assert score.insertion_rate == pytest.approx(ins / words)
    assert score.n == len(PAIRS)
    assert utts[1].ref_raw == PAIRS[1][0] and utts[1].hyp_norm == "the cat sat on mat"


def test_cer_counts_characters():
    u = score_utterance("abc", "abd")
    assert (u.chars.substitutions, u.chars.ref_len) == (1, 3)
    c = align(list("kitten"), list("sitting"))
    assert c.errors == 3


def test_bootstrap_interval_is_fixed_and_brackets_the_estimate():
    pairs = [(f"u{k}", r, h) for k, (r, h, *_ ) in enumerate(PAIRS) if r]
    a, _ = score_corpus(pairs, bootstrap=500)
    b, _ = score_corpus(pairs, bootstrap=500)
    assert a.wer_ci95 == b.wer_ci95
    lo, hi = a.wer_ci95
    assert lo <= a.wer <= hi


def test_empty_corpus_and_blank_hypothesis():
    assert corpus_score([], bootstrap=0).wer == 0.0
    assert score_utterance("hello there", "").wer == 1.0


def test_non_speech_hallucination_detector():
    assert not has_content("")
    assert not has_content("  [silence] ")
    assert not has_content("(no speech)")
    assert has_content("Thank you.")
    assert has_content("[silence] 5")
    assert invented_words("Thank you for watching") == 4


def test_answer_leak_detector():
    question = "what is the capital of france"
    assert is_answer_leak(question, "The capital of France is Paris.") is False   # shares bigrams
    assert is_answer_leak(question, "Paris is lovely in spring, I think.") is True
    assert is_answer_leak(question, "What is the capital of France?") is False
    assert is_answer_leak(question, "") is False
