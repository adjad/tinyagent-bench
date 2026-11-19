"""Vision items: rendering, ground truth, the validator, the scorer and the committed sample."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

from edat.media.common import (canon_date, canon_money, canon_phone, canon_text, canon_time,
                               canonical)
from edat.media.vision import generate as gen
from edat.media.vision.canvas import Canvas, unsupported_chars
from edat.media.vision.items import ABSTAIN_ANSWER, Question
from edat.media.vision.score import anls, extract_answer, is_abstention, score_answer, score_item
from edat.media.vision.validate import validate_item

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "test_fixtures" / "media" / "vision" / "dev"
FAMILIES = gen.available_families()
PER_FAMILY = 16          # items checked per family here (the generator checks every item it writes)


def test_all_eleven_families_are_present():
    assert sorted(FAMILIES) == sorted(gen.FAMILY_MODULES) and len(FAMILIES) == 11


# ------------------------------------------------------------------ canonical forms

@pytest.mark.parametrize("raw, want", [
    ("2027-03-04", "2027-03-04"), ("03/04/2027", "2027-03-04"), ("Mar 4, 2027", "2027-03-04"),
    ("March 4th 2027", "2027-03-04"), ("4 March 2027", "2027-03-04"),
    ("Thursday, March 4, 2027", "2027-03-04"), ("Feb 30, 2027", None), ("soon", None)])
def test_canon_date(raw, want):
    assert canon_date(raw) == want


def test_canon_date_year_less_uses_the_item_year():
    assert canon_date("Mar 4") is None
    assert canon_date("Mar 4", default_year=2027) == "2027-03-04"


@pytest.mark.parametrize("raw, want", [
    ("4:30 PM", "16:30"), ("16:30", "16:30"), ("4.30pm", "16:30"), ("4 pm", "16:00"),
    ("12:15 AM", "00:15"), ("noon", "12:00"), ("25:00", None), ("13 pm", None)])
def test_canon_time(raw, want):
    assert canon_time(raw) == want


def test_canon_money_phone_text():
    assert canon_money("$1,204.5") == "1204.50 USD"
    assert canon_money("12.50") == "12.50 USD"
    assert canon_money("EUR 3") == "3.00 EUR"
    assert canon_phone("+1 (415) 555-0142") == "4155550142"
    assert canon_text("  “Ｈello”  World. ") == "“hello” world"
    assert canonical("number", "1,250 items") == "1250"


# ------------------------------------------------------------------ scoring

def _q(kind="field", canon="text", answer="x", **kw):
    return Question("q", kw.pop("qtype", "lookup"), "?", answer, kind=kind, canon=canon, **kw).to_json()


@pytest.mark.parametrize("q, output, ok", [
    (_q(canon="money", answer="12.50"), "The total is $12.50.", True),    # the amount is read out of prose
    (_q(canon="money", answer="12.50"), "Subtotal $10.00\nTotal $12.50", False),   # first line only
    (_q(canon="money", answer="12.50"), "$12.50", True),
    (_q(canon="money", answer="12.50"), "Answer: 12.5 USD", True),
    (_q(canon="date", answer="2027-03-04"), "March 4, 2027", True),
    (_q(canon="time", answer="16:30"), "4:30 pm", True),
    (_q(canon="state", answer="on"), "Enabled", True),
    (_q(canon="number", answer="7"), "7 messages", True),
    (_q(answer="Quilora Market"), "quilora market", True),
    (_q(answer="Quilora Market"), "Quilora Markets", False),
    (_q(kind="relaxed", canon="number", answer="40"), "41.9", True),
    (_q(kind="relaxed", canon="number", answer="40"), "42.5", False),
    (_q(kind="anls", answer="Payment declined"), "Payment declned", True),
    (_q(kind="abstain", answer=ABSTAIN_ANSWER, qtype="absence"), "Not shown.", True),
    (_q(kind="abstain", answer=ABSTAIN_ANSWER, qtype="absence"), "$12.50", False),
])
def test_score_answer(q, output, ok):
    s = score_answer(q, output)
    assert (s.score > 0) == ok


def test_abstention_and_false_answer():
    q = _q(kind="abstain", answer=ABSTAIN_ANSWER, qtype="absence")
    assert score_answer(q, "unreadable").abstained
    wrong = score_answer(q, "Amara Calford")
    assert wrong.false_answer and wrong.score == 0
    blank = score_answer(q, "")
    assert blank.score == 0 and not blank.false_answer
    # A value question answered "not shown" is wrong, not a false answer.
    s = score_answer(_q(canon="money", answer="3.00"), "not shown")
    assert s.score == 0 and s.abstained


def test_blank_answers_score_zero_for_every_kind():
    for q in (_q(), _q(canon="date", answer="2027-01-01"), _q(kind="anls", answer="abc"),
              _q(kind="relaxed", canon="number", answer="10"),
              _q(kind="abstain", answer=ABSTAIN_ANSWER, qtype="absence"),
              Question("r", "reading", "Read", "", kind="reading").to_json()):
        assert score_answer(q, "", reading_text="some text").score == 0
        assert score_answer(q, None, reading_text="some text").score == 0


def test_reading_score_is_one_minus_cer():
    q = Question("r", "reading", "Read", "", kind="reading").to_json()
    s = score_answer(q, "TEXT:\nhello world\nDESCRIPTION: a card", reading_text="hello world")
    assert s.cer == 0 and s.passed
    s = score_answer(q, "hello wurld", reading_text="hello world")
    assert s.cer == pytest.approx(1 / 11) and not s.passed


def test_extract_answer_and_anls():
    assert extract_answer("<think>hmm</think>\n\n**Quilora**\nmore") == "Quilora"
    assert is_abstention("Not shown in the image")
    assert anls("abcdef", ["abcxyz"]) == 0.0        # distance 0.5 is at the threshold: 0
    assert anls("abcdef", ["abcdeg"]) == pytest.approx(5 / 6)


# ------------------------------------------------------------------ canvas and validator

def test_font_coverage_check():
    assert unsupported_chars("Total $12.50 @ 4:30 PM #7 50%") == []
    assert unsupported_chars("Café — €5") == ["é", "—", "€"]


def _one_field_item(draw_over=False, absent_drawn=False):
    c = Canvas(200, 80, scale=1)
    c.text(10, 10, "Total $4.20", size=14, field="total", kind="money", value="4.20")
    if draw_over:
        c.rect(10, 10, 120, 30, fill="fg")
    if absent_drawn:
        c.text(10, 40, "Amara Calford", size=12)
    c.absent("cashier", "Amara Calford")
    meta = {"image": {"width": 200, "height": 80}, "variant": {}, "default_year": 2027,
            "fields": {k: f.to_json() for k, f in c.fields.items()},
            "text_runs": c.text_runs(), "reading_text": c.reading_text(),
            "questions": [_q(canon="money", answer="4.20", fields=["total"]),
                          {**_q(kind="abstain", answer=ABSTAIN_ANSWER, qtype="absence"),
                           "qid": "a", "fields": ["cashier"]}]}
    return meta, c.image


def test_validator_accepts_a_clean_item():
    meta, image = _one_field_item()
    assert validate_item(meta, image) == []


def test_validator_catches_overdrawn_fields_and_drawn_absent_values():
    meta, image = _one_field_item(draw_over=True)
    assert any("overdrawn" in p for p in validate_item(meta, image))
    meta, image = _one_field_item(absent_drawn=True)
    assert any("marked absent but drawn" in p for p in validate_item(meta, image))


def test_validator_catches_low_contrast_and_wrong_value():
    c = Canvas(200, 60, scale=1)
    c.text(10, 10, "Total $4.20", size=14, color=(250, 250, 250), field="t", kind="money",
           value="4.20")
    c.text(10, 30, "Due Mar 5, 2027", size=14, field="d", kind="date", value="2027-03-06")
    meta = {"image": {"width": 200, "height": 60}, "variant": {}, "default_year": 2027,
            "fields": {k: f.to_json() for k, f in c.fields.items()}, "text_runs": c.text_runs(),
            "reading_text": c.reading_text(),
            "questions": [_q(canon="money", answer="4.20", fields=["t"]),
                          {**_q(canon="date", answer="2027-03-06", fields=["d"]), "qid": "d"}]}
    problems = validate_item(meta, c.image)
    assert any("contrast" in p for p in problems)
    assert any("reads as" in p for p in problems)


# ------------------------------------------------------------------ generated items

@pytest.mark.parametrize("family", FAMILIES)
def test_family_items_validate(family):
    abstained = 0
    t = len(gen.family_module(family).TEMPLATES)
    indices = list(range(PER_FAMILY)) + [9 * t, 9 * t + 1, 14 * t]    # include abstention items
    for i in indices:
        meta, data, rendered = gen.build(family, i, split="dev")
        assert validate_item(meta, rendered.image, clean_image=rendered.clean_image) == [], meta["item_id"]
        assert meta["image"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert json.loads(json.dumps(meta)) == meta          # plain JSON
        kinds = [q["kind"] for q in meta["questions"]]
        assert kinds.count("reading") == 1
        abstained += "abstain" in kinds
        outputs = {q["qid"]: (meta["reading_text"] if q["kind"] == "reading" else q["answer"])
                   for q in meta["questions"]}
        assert all(s.score == 1.0 for s in score_item(meta, outputs))
        assert all(s.score == 0.0 for s in score_item(meta, {}))
    assert abstained >= 1


@pytest.mark.parametrize("family", FAMILIES)
def test_same_seed_same_pixels(family):
    a = gen.build(family, 5, split="dev")[1]
    b = gen.build(family, 5, split="dev")[1]
    assert a == b
    assert gen.build(family, 6, split="dev")[1] != a


@pytest.mark.parametrize("family", FAMILIES)
def test_v5_rerender_at_a_long_side(family):
    meta, _, rendered = gen.build(family, 2, split="dev", long_side=1024)
    assert max(meta["image"]["width"], meta["image"]["height"]) in range(1000, 1300)
    base = gen.build(family, 2, split="dev")[0]
    assert [q["answer"] for q in meta["questions"]] == [q["answer"] for q in base["questions"]]


def test_invented_values_only():
    """No item may contain real-looking contact data outside the reserved ranges."""
    import re
    for family in FAMILIES:
        for i in range(6):
            meta = gen.build(family, i, split="dev")[0]
            text = meta["reading_text"]
            for phone in re.findall(r"\(\d{3}\) \d{3}-\d{4}", text):
                assert "555-01" in phone, (family, phone)
            for mail in re.findall(r"[\w.]+@[\w.]+", text):
                assert mail.endswith(("example.com", "example.org", "mail.test")) or \
                    ".example" in mail, (family, mail)
            assert text.isascii()


def test_heldout_is_refused_inside_the_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="no held-out seed"):
        gen.seed_for("heldout")
    assert gen.main(["init-heldout-seed"]) == 0
    assert (tmp_path / "media" / "heldout_seed").exists()
    with pytest.raises(RuntimeError, match="inside the repository"):
        gen.write_items("documents", [0], gen.REPO_ROOT / "test_fixtures" / "nope", split="heldout")
    out = gen.write_items("documents", [0], tmp_path / "items", split="heldout")
    assert out[0]["seed"] == "private" and out[0]["split"] == "heldout"
    dev = gen.build("documents", 0, split="dev")[0]
    assert dev["data"] != out[0]["data"]


# ------------------------------------------------------------------ committed sample

def _sample_items():
    return sorted(SAMPLE_DIR.glob("*/*.json"))


def test_committed_sample_matches_the_generator():
    items = _sample_items()
    assert items, "the committed development sample is missing"
    assert {p.parent.name for p in items} == set(FAMILIES)
    total_bytes = 0
    for p in items:
        meta = json.loads(p.read_text())
        png = (p.parent / meta["image"]["file"]).read_bytes()
        total_bytes += len(png)
        assert hashlib.sha256(png).hexdigest() == meta["image"]["sha256"]
        image = Image.open(io.BytesIO(png)).convert("RGB")
        regen, data, rendered = gen.build(meta["family"], meta["index"], split="dev",
                                          long_side=meta["variant"].get("long_side"))
        assert regen["data"] == meta["data"] and regen["questions"] == meta["questions"]
        if meta["render"] == regen["render"]:
            assert data == png, f"{p.name}: same renderer versions but different pixels"
        assert validate_item(meta, image, clean_image=rendered.clean_image) == []
    assert total_bytes < 3_000_000
