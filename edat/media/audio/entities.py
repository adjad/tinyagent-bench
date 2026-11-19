"""Entity extraction and scoring for transcripts (spec 05 section 3.4).

Rule-based, with no model and no download. From a reference transcript this finds:

* **numbers, amounts, percentages**: from the normaliser's digit output
  (``twenty five dollars`` -> ``$25``, ``three point five percent`` -> ``3.5%``);
  canonical forms ``num:25``, ``usd:25.50``, ``pct:3.5``, ``cents:10``;
* **dates**, as ISO dates, given an anchor date: month names with a day and optional year
  (``march third twenty twenty seven``), numeric ``M/D/YYYY`` (US order) and ISO
  ``YYYY-MM-DD``, ``today`` / ``tomorrow`` / ``yesterday``, weekdays, ``in N days|weeks``;
* **times**, as ``HH:MM`` (24-hour): ``4:30 pm``, ``four thirty p m``, ``5 o'clock``,
  ``half past three``, ``quarter to six``, ``noon``, ``midnight``, ``1600 hours``;
* **domain terms** from a term list (small generic finance and meeting lists ship here;
  :func:`load_terms` reads a bigger one from a file);
* **names**: no tagger is pinned offline, so a name list per item is accepted (A3 plants
  them) and ``name_tagger`` is a hook for a pinned tagger run on the reference text.

Date rules (a bare month-day with no year takes the anchor's year, or the next year when that
date is already past; a bare weekday is its next occurrence strictly after the anchor;
``next <weekday>`` is that weekday in the following Monday-to-Sunday week). Times without
am/pm keep their face value (``four thirty`` -> 04:30), so a reference of ``four thirty`` and
an output of ``4:30 pm`` disagree; that ambiguity is the model's to resolve and is counted.

An entity is right when its canonical form appears in the hypothesis's canonical extractions
(a multiset match, so one output number cannot cover two reference numbers).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable

from edat.media.audio.normalize import normalize

ENTITY_TYPES = ("name", "number", "date", "time", "term")

FINANCE_TERMS = (
    "revenue", "gross margin", "operating margin", "net income", "free cash flow", "guidance",
    "year over year", "quarter over quarter", "basis points", "earnings per share", "ebitda",
    "headwinds", "tailwinds", "run rate", "backlog", "churn", "capital expenditure",
    "working capital", "balance sheet", "dividend", "buyback", "forecast",
)
MEETING_TERMS = (
    "agenda", "action item", "action items", "roadmap", "stand up", "sprint", "backlog grooming",
    "deliverable", "milestone", "kpi", "okr", "stakeholder", "follow up", "minutes", "retro",
    "blocker", "deadline", "owner", "demo", "budget",
)
DEFAULT_TERMS = tuple(dict.fromkeys(FINANCE_TERMS + MEETING_TERMS))


def load_terms(path: str | Path) -> tuple[str, ...]:
    """Terms from a file: a JSON list, or one term per line (blank lines and ``#`` comments skipped)."""
    p = Path(path)
    raw = p.read_text()
    if p.suffix == ".json":
        return tuple(str(t) for t in json.loads(raw))
    return tuple(ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))


NameTagger = Callable[[str], Iterable[str]]
"""A hook: reference text -> person, place and organisation names found in it."""


@dataclass(frozen=True)
class Entity:
    type: str
    canonical: str
    text: str = ""

    def to_json(self) -> dict:
        return {"type": self.type, "canonical": self.canonical, "text": self.text}


MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"))}
MONTH_RE = "|".join(MONTHS)
WEEKDAYS = {d: i for i, d in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
WEEKDAY_RE = "|".join(WEEKDAYS)


def _dec(s: str) -> str:
    try:
        d = Decimal(s).normalize()
    except InvalidOperation:
        return s
    return format(d, "f")


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _month_day(anchor: date, month: int, day: int, year: int | None) -> str | None:
    if year is not None:
        return _iso(year, month, day)
    iso = _iso(anchor.year, month, day)
    if iso and date.fromisoformat(iso) < anchor:
        return _iso(anchor.year + 1, month, day)
    return iso


def _next_weekday(anchor: date, wd: int, following_week: bool) -> date:
    if following_week:
        monday = anchor - timedelta(days=anchor.weekday()) + timedelta(days=7)
        return monday + timedelta(days=wd)
    delta = (wd - anchor.weekday()) % 7 or 7
    return anchor + timedelta(days=delta)


def _hhmm(h: int, m: int, marker: str | None) -> str | None:
    if m > 59 or h > 24:
        return None
    if marker:
        mk = marker.replace(" ", "").replace(".", "")
        if mk == "pm" and h < 12:
            h += 12
        elif mk == "am" and h == 12:
            h = 0
    if h == 24:
        h = 0
    return f"{h:02d}:{m:02d}"


_MARK = r"(?:a m|p m|am|pm)"
_PLACEHOLDER = "zz{kind}{i}zz"


def _mask_punctuated(raw: str, anchor: date) -> tuple[str, dict[str, Entity]]:
    """Pull out forms the normaliser would split ("4:30 PM", "3/4/2027", "2027-03-04")."""
    masked: dict[str, Entity] = {}
    text = raw.lower()

    def put(kind: str, ent: Entity | None, original: str) -> str:
        if ent is None:
            return original
        key = _PLACEHOLDER.format(kind=kind, i=len(masked))
        masked[key] = ent
        return f" {key} "

    def time_sub(m: re.Match) -> str:
        c = _hhmm(int(m.group(1)), int(m.group(2)), m.group(3))
        return put("t", Entity("time", c, m.group(0)) if c else None, m.group(0))

    text = re.sub(r"\b(\d{1,2})[:.](\d{2})\s*(a\.?\s?m\.?|p\.?\s?m\.?)?(?![\d])",
                  lambda m: time_sub(m) if ":" in m.group(0) or m.group(3) else m.group(0), text)

    def iso_sub(m: re.Match) -> str:
        iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return put("d", Entity("date", iso, m.group(0)) if iso else None, m.group(0))

    text = re.sub(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", iso_sub, text)

    def us_sub(m: re.Match) -> str:
        y = int(m.group(3)) if m.group(3) else None
        if y is not None and y < 100:
            y += 2000
        iso = _month_day(anchor, int(m.group(1)), int(m.group(2)), y) if 1 <= int(m.group(1)) <= 12 else None
        return put("d", Entity("date", iso, m.group(0)) if iso else None, m.group(0))

    text = re.sub(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", us_sub, text)
    return text, masked


def _extract_dates(s: str, anchor: date, out: list[Entity]) -> str:
    def add(iso: str | None, original: str) -> str:
        if iso:
            out.append(Entity("date", iso, original))
            return " "
        return original

    def year_of(g: str | None) -> int | None:
        return int(g) if g and len(g) == 4 else None

    # March 3rd [2027] / march 3
    s = re.sub(rf"\b({MONTH_RE}) (\d{{1,2}})(?:st|nd|rd|th)?(?: (\d{{4}}))?\b",
               lambda m: add(_month_day(anchor, MONTHS[m.group(1)], int(m.group(2)), year_of(m.group(3))),
                             m.group(0)), s)
    # the 3rd of March [2027]
    s = re.sub(rf"\b(\d{{1,2}})(?:st|nd|rd|th)? of ({MONTH_RE})(?: (\d{{4}}))?\b",
               lambda m: add(_month_day(anchor, MONTHS[m.group(2)], int(m.group(1)), year_of(m.group(3))),
                             m.group(0)), s)
    s = re.sub(r"\bday after tomorrow\b", lambda m: add((anchor + timedelta(days=2)).isoformat(), m.group(0)), s)
    s = re.sub(r"\btoday\b", lambda m: add(anchor.isoformat(), m.group(0)), s)
    s = re.sub(r"\btomorrow\b", lambda m: add((anchor + timedelta(days=1)).isoformat(), m.group(0)), s)
    s = re.sub(r"\byesterday\b", lambda m: add((anchor - timedelta(days=1)).isoformat(), m.group(0)), s)
    s = re.sub(r"\bin (\d+) (day|days|week|weeks)\b",
               lambda m: add((anchor + timedelta(days=int(m.group(1)) * (7 if m.group(2).startswith("week") else 1))
                              ).isoformat(), m.group(0)), s)
    s = re.sub(rf"\b(?:(next|this) )?({WEEKDAY_RE})\b",
               lambda m: add(_next_weekday(anchor, WEEKDAYS[m.group(2)], m.group(1) == "next").isoformat(),
                             m.group(0)), s)
    return s


def _extract_times(s: str, out: list[Entity]) -> str:
    def add(c: str | None, original: str) -> str:
        if c:
            out.append(Entity("time", c, original))
            return " "
        return original

    s = re.sub(rf"\b(\d{{1,2}}) (\d{{2}}) ({_MARK})\b",
               lambda m: add(_hhmm(int(m.group(1)), int(m.group(2)), m.group(3)), m.group(0)), s)
    s = re.sub(rf"\b(\d{{1,2}}) ({_MARK})\b",
               lambda m: add(_hhmm(int(m.group(1)), 0, m.group(2)), m.group(0)), s)
    s = re.sub(r"\b(\d{1,2}) o ?clock\b", lambda m: add(_hhmm(int(m.group(1)), 0, None), m.group(0)), s)
    s = re.sub(r"\bhalf past (\d{1,2})\b", lambda m: add(_hhmm(int(m.group(1)), 30, None), m.group(0)), s)
    s = re.sub(r"\bquarter past (\d{1,2})\b", lambda m: add(_hhmm(int(m.group(1)), 15, None), m.group(0)), s)
    s = re.sub(r"\bquarter to (\d{1,2})\b",
               lambda m: add(_hhmm((int(m.group(1)) - 1) % 24 or 24, 45, None), m.group(0)), s)
    s = re.sub(r"\b(\d{3,4}) hours\b",
               lambda m: add(_hhmm(int(m.group(1)) // 100, int(m.group(1)) % 100, None), m.group(0)), s)
    s = re.sub(r"\bnoon\b", lambda m: add("12:00", m.group(0)), s)
    s = re.sub(r"\bmidnight\b", lambda m: add("00:00", m.group(0)), s)
    return s


def _extract_numbers(s: str, out: list[Entity]) -> str:
    def add(kind_prefix: str, val: str, original: str) -> str:
        out.append(Entity("number", f"{kind_prefix}:{_dec(val)}", original))
        return " "

    s = re.sub(r"\$(\d+(?:\.\d+)?)", lambda m: add("usd", m.group(1), m.group(0)), s)
    s = re.sub(r"¢(\d+)", lambda m: add("cents", m.group(1), m.group(0)), s)
    s = re.sub(r"(\d+(?:\.\d+)?)%", lambda m: add("pct", m.group(1), m.group(0)), s)
    s = re.sub(r"\b(\d+(?:\.\d+)?)(?:st|nd|rd|th)?\b", lambda m: add("num", m.group(1), m.group(0)), s)
    return s


def _phrase(s: str) -> str:
    return normalize(s)


def _find_phrases(text: str, phrases: Iterable[str], etype: str, out: list[Entity]) -> None:
    """Whole-word matches of each phrase (longest first, matched text blanked so shorter terms
    inside a longer one do not count twice)."""
    t = f" {text} "
    seen = sorted({_phrase(p) for p in phrases if _phrase(p)}, key=lambda p: (-len(p.split()), p))
    for p in seen:
        pat = re.compile(rf"(?<![\w']){re.escape(p)}(?![\w'])")
        for m in pat.finditer(t):
            out.append(Entity(etype, p, m.group(0).strip()))
        t = pat.sub(lambda m: " " * len(m.group(0)), t)


def extract_entities(text: str, *, anchor_date: date, names: Iterable[str] = (),
                     terms: Iterable[str] = DEFAULT_TERMS, name_tagger: NameTagger | None = None
                     ) -> list[Entity]:
    """All entities in ``text`` (a reference or a hypothesis), in type order.

    ``names`` are the planted names (checked as whole words after case-folding); with none given
    and a ``name_tagger``, the tagger's output on ``text`` is used.
    """
    masked_text, masked = _mask_punctuated(text or "", anchor_date)
    ents: list[Entity] = list(masked.values())
    norm = normalize(masked_text)
    for key in masked:
        norm = norm.replace(key, " ")
    names = list(names)
    if not names and name_tagger is not None:
        names = list(name_tagger(text))
    _find_phrases(norm, names, "name", ents)
    rest = _extract_dates(norm, anchor_date, ents)
    rest = _extract_times(rest, ents)
    _find_phrases(rest, terms, "term", ents)
    _extract_numbers(rest, ents)
    order = {t: i for i, t in enumerate(ENTITY_TYPES)}
    return sorted(ents, key=lambda e: order[e.type])


def entity_scores(reference: str, hypothesis: str, *, anchor_date: date, names: Iterable[str] = (),
                  terms: Iterable[str] = DEFAULT_TERMS, name_tagger: NameTagger | None = None) -> dict:
    """Per-type right/total and the entity error rate for one reference/hypothesis pair.

    The reference's entities are extracted; the hypothesis is searched with the same rules and
    the *same* name list (so a mis-heard name is wrong, not unseen) and term list. An entity
    is right when its canonical form is among the hypothesis's, matched as a multiset.

    ``by_type`` maps type -> ``{"right", "total"}`` (all five types present), ``total`` is the
    sum, ``error_rate`` is ``1 - right / total`` (None when the reference has no entities).
    """
    names = list(names)
    ref_ents = extract_entities(reference, anchor_date=anchor_date, names=names, terms=terms,
                                name_tagger=name_tagger)
    ref_names = names or [e.canonical for e in ref_ents if e.type == "name"]
    hyp_ents = extract_entities(hypothesis, anchor_date=anchor_date, names=ref_names, terms=terms)
    pool = Counter((e.type, e.canonical) for e in hyp_ents)
    by_type = {t: {"right": 0, "total": 0} for t in ENTITY_TYPES}
    detail = []
    for e in ref_ents:
        by_type[e.type]["total"] += 1
        ok = pool[(e.type, e.canonical)] > 0
        if ok:
            pool[(e.type, e.canonical)] -= 1
            by_type[e.type]["right"] += 1
        detail.append({**e.to_json(), "right": ok})
    right = sum(v["right"] for v in by_type.values())
    total = sum(v["total"] for v in by_type.values())
    return {"by_type": by_type, "total": {"right": right, "total": total},
            "error_rate": (1 - right / total) if total else None, "entities": detail}


def aggregate_entity_scores(scores: Iterable[dict]) -> dict:
    """Sum :func:`entity_scores` results: per-type accuracy and the overall entity error rate."""
    by_type = {t: {"right": 0, "total": 0} for t in ENTITY_TYPES}
    for s in scores:
        for t in ENTITY_TYPES:
            by_type[t]["right"] += s["by_type"][t]["right"]
            by_type[t]["total"] += s["by_type"][t]["total"]
    for v in by_type.values():
        v["accuracy"] = v["right"] / v["total"] if v["total"] else None
    right = sum(v["right"] for v in by_type.values())
    total = sum(v["total"] for v in by_type.values())
    return {"by_type": by_type, "total": {"right": right, "total": total},
            "error_rate": (1 - right / total) if total else None}
