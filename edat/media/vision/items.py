"""Vision items: one image, its ground truth and its questions (spec 05 §7.1).

An item is written as ``<item_id>.png`` beside ``<item_id>.json``. The JSON is
the ground truth: the data the image was drawn from, every planted field with
its pixel box, the text in reading order, and the questions with answers.

Question kinds (how :mod:`edat.media.vision.score` scores them):

``field``     normalised exact match of one value, canonicalised as ``canon``
              (text, number, date, time, money, phone, state)
``anls``      free text that cannot be canonicalised: ANLS, threshold 0.5
``relaxed``   a value read off a plot (not printed): within 5 %
``reading``   the full reading: character error rate against ``reading_text``
``abstain``   the asked field is absent, blurred or cut off: the right answer
              is "not shown" (or "unreadable"); any value is a false answer
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

ITEM_SCHEMA = "edat.media.vision-item/1"
QUESTION_KINDS = ("field", "anls", "relaxed", "reading", "abstain")
QUESTION_TYPES = ("lookup", "who_said", "state", "count", "compare", "absence", "sum", "multi",
                  "cell", "max", "min", "trend", "difference", "free_slot", "event", "reading")

ANSWER_INSTRUCTION = (
    "Answer the question about the image. Reply with the answer only, no explanation. "
    "If the answer is not shown in the image or cannot be read, reply exactly: not shown")
READING_INSTRUCTION = (
    "Read all the text in the image in reading order. Reply with the text only, "
    "one line of the image per line.")
ABSTAIN_ANSWER = "not shown"


@dataclass
class Question:
    qid: str
    qtype: str
    question: str
    answer: str                         # canonical-readable answer ("not shown" for abstain)
    kind: str = "field"
    canon: str = "text"
    fields: list[str] = field(default_factory=list)
    accept: list[str] = field(default_factory=list)   # other answers that are also right

    def __post_init__(self) -> None:
        if self.kind not in QUESTION_KINDS:
            raise ValueError(f"unknown question kind {self.kind!r}")
        if self.qtype not in QUESTION_TYPES:
            raise ValueError(f"unknown question type {self.qtype!r}")

    def prompt(self) -> str:
        if self.kind == "reading":
            return READING_INSTRUCTION
        return f"{ANSWER_INSTRUCTION}\nQuestion: {self.question}"

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Rendered:
    """What a family's ``render`` returns: the canvas plus questions and source data."""

    canvas: Any                         # vision.canvas.Canvas
    template: str
    questions: list[Question]
    data: dict
    default_year: int | None = None     # for year-less dates in answers
    clean_image: Any = None             # the undamaged image (photo family), for validation
    damage: dict | None = None          # {"level", "ops", "field_boxes": {name: box in final image}}
    final_image: Any = None             # the image to save when it differs from the canvas

    @property
    def image(self):
        return self.final_image if self.final_image is not None else self.canvas.image


def item_json(*, item_id: str, family: str, split: str, seed_label: str, index: int,
              rendered: Rendered, image_file: str, image_sha256: str, stamp: dict,
              variant: dict) -> dict:
    c = rendered.canvas
    return {
        "schema": ITEM_SCHEMA,
        "item_id": item_id,
        "family": family,
        "template": rendered.template,
        "split": split,
        "seed": seed_label,
        "index": index,
        "variant": variant,                     # theme, scale, long side, abstain, damage
        "image": {"file": image_file, "sha256": image_sha256, "width": rendered.image.size[0],
                  "height": rendered.image.size[1], "logical": list(c.logical), "scale": c.scale},
        "render": stamp,
        "default_year": rendered.default_year,
        "data": rendered.data,
        "fields": {k: f.to_json() for k, f in c.fields.items()},
        "elements": [e.to_json() for e in getattr(c, "elements", [])],
        "text_runs": c.text_runs(),
        "reading_text": c.reading_text(),
        "damage": rendered.damage,
        "questions": [q.to_json() for q in rendered.questions],
    }
