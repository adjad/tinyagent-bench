"""Scoring for image extraction, with no judge model (spec 05 §7.1 "Scoring").

``score_answer(question, output)`` returns one :class:`Score`. Every kind gives
0 for a blank answer, and the validator checks that on every item.

* ``field``: normalised exact match after :func:`edat.media.common.canonical`
  for the question's ``canon`` (dates ISO 8601, times 24-hour, money amount and
  currency, phone digits, text NFKC + case-fold + collapsed spaces; ``state``
  maps on/off words).
* ``anls``: average normalised Levenshtein similarity, threshold 0.5.
* ``relaxed``: numeric answer within 5 % of the plotted value.
* ``reading``: character error rate against the rendered text in reading order;
  score is ``max(0, 1 - CER)``, pass when CER ≤ 0.02.
* ``abstain``: right only when the model says the value is not shown or
  unreadable. Any value given is a *false answer* (the V1 false-answer rate).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from edat.media.common import canon_number, canon_text, canonical
from edat.media.textdist import align, levenshtein

SCORER_ID = "edat-media-vision-score/1"
ANLS_THRESHOLD = 0.5
RELAXED_TOLERANCE = 0.05
READING_PASS_CER = 0.02

ABSTAIN_PHRASES = (
    "not shown", "unreadable", "not visible", "not readable", "cannot be read", "can't be read",
    "not legible", "illegible", "not in the image", "not present", "not available",
    "no answer", "unknown", "n/a", "none shown", "not stated", "not provided", "not listed",
)
_STATE = {"on": "on", "enabled": "on", "enable": "on", "true": "on", "yes": "on", "active": "on",
          "off": "off", "disabled": "off", "disable": "off", "false": "off", "no": "off",
          "inactive": "off"}
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


@dataclass
class Score:
    qid: str
    kind: str
    score: float
    passed: bool
    extracted: str
    pred: str | None = None
    gold: str | None = None
    abstained: bool = False
    false_answer: bool = False
    cer: float | None = None
    line_recall: float | None = None

    def to_json(self) -> dict:
        return asdict(self)


def extract_answer(output: str | None, *, reading: bool = False) -> str:
    """The answer part of a model's reply: thinking removed, 'Answer:' prefix and quotes dropped.

    For a short answer only the first non-empty line counts; a full reading keeps
    every line (and drops a leading ``TEXT:`` header, Harbor's reading format).
    """
    text = _THINK.sub("", output or "").strip()
    if reading:
        text = re.sub(r"^\s*TEXT:\s*", "", text, flags=re.I)
        text = re.split(r"\n\s*DESCRIPTION:", text, flags=re.I)[0]
        return text.strip()
    m = re.search(r"(?:^|\n)\s*(?:final\s+)?answer\s*[:\-]\s*(.+)", text, re.I)
    if m:
        text = m.group(1)
    for line in text.splitlines():
        line = line.strip().strip("*").strip()
        if line:
            return line.strip("\"'`").rstrip(".").strip()
    return ""


def is_abstention(answer: str) -> bool:
    a = canon_text(answer)
    return bool(a) and any(a == p or a.startswith(p) for p in ABSTAIN_PHRASES)


def anls(pred: str, golds: list[str]) -> float:
    best = 0.0
    p = canon_text(pred)
    for g in golds:
        g2 = canon_text(g)
        if not p and not g2:
            return 1.0
        nl = levenshtein(p, g2) / max(len(p), len(g2), 1)
        best = max(best, 1.0 - nl if nl < ANLS_THRESHOLD else 0.0)
    return best


def _canon(kind: str, value: str, default_year: int | None) -> str | None:
    if kind == "state":
        return _STATE.get(canon_text(value))
    return canonical(kind, value, default_year=default_year)


def score_answer(q: dict, output: str | None, *, default_year: int | None = None,
                 reading_text: str = "") -> Score:
    """Score one model output for one question (a question JSON dict)."""
    kind = q["kind"]
    extracted = extract_answer(output, reading=(kind == "reading"))
    golds = [q["answer"], *q.get("accept", [])]
    if kind == "reading":
        ref = reading_text
        hyp = extracted
        counts = align(list(_reading_norm(ref)), list(_reading_norm(hyp)))
        c = counts.rate() if hyp else 1.0
        s = max(0.0, 1.0 - c)
        return Score(q["qid"], kind, s, c <= READING_PASS_CER and bool(hyp), extracted[:200],
                     cer=c, line_recall=line_recall(ref, hyp))
    if kind == "abstain":
        abst = is_abstention(extracted)
        return Score(q["qid"], kind, 1.0 if abst else 0.0, abst, extracted, abstained=abst,
                     false_answer=bool(extracted) and not abst)
    if not extracted or is_abstention(extracted):
        return Score(q["qid"], kind, 0.0, False, extracted, abstained=is_abstention(extracted))
    if kind == "anls":
        s = anls(extracted, golds)
        return Score(q["qid"], kind, s, s >= 1.0, extracted, pred=canon_text(extracted),
                     gold=canon_text(golds[0]))
    if kind == "relaxed":
        p = canon_number(extracted)
        ok = False
        for g in golds:
            gv = canon_number(g)
            if p is not None and gv is not None:
                gf, pf = float(gv), float(p)
                ok = ok or abs(pf - gf) <= RELAXED_TOLERANCE * abs(gf) or (gf == 0 and pf == 0)
        return Score(q["qid"], kind, 1.0 if ok else 0.0, ok, extracted, pred=p,
                     gold=canon_number(golds[0]))
    canon_kind = q.get("canon", "text")
    pred = _canon(canon_kind, extracted, default_year)
    ok = False
    for g in golds:
        gc = _canon(canon_kind, g, default_year)
        ok = ok or (pred is not None and gc is not None and pred == gc)
    return Score(q["qid"], kind, 1.0 if ok else 0.0, ok, extracted, pred=pred,
                 gold=_canon(canon_kind, golds[0], default_year))


def line_recall(ref: str, hyp: str) -> float:
    """Share of reference lines found whole (spaces collapsed, case kept) in the reading."""
    lines = [_reading_norm(l) for l in ref.splitlines() if l.strip()]
    if not lines:
        return 1.0
    got = _reading_norm(hyp)
    return sum(1 for l in lines if l in got) / len(lines)


def _reading_norm(text: str) -> str:
    """Reading CER compares lines' text with spaces collapsed; line breaks count as one space."""
    return re.sub(r"\s+", " ", text).strip()


def score_item(item: dict, outputs: dict[str, str | None]) -> list[Score]:
    """Score every question of an item given ``{qid: output}``; a missing output is blank."""
    return [score_answer(q, outputs.get(q["qid"]), default_year=item.get("default_year"),
                         reading_text=item.get("reading_text", ""))
            for q in item["questions"]]
