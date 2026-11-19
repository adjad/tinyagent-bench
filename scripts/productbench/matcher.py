"""Find ledger facts in free text: clock times, dates, amounts and plain anchors.

Pure functions, no model. Anchors (see profile.py):

  "@15:30"       a clock time; matches 3:30 pm, 3.30pm, 15:30, "half past three" is
                 not attempted. A time written without am/pm ("at 3:30") matches
                 both 3:30 and 15:30.
  "#2026-03-12"  a date; matches "Thursday" (the next one on or after the anchor
                 day, within a week), "tomorrow", "March 12", "Mar 12th", "12 March", "3/12".
  anything else  a case-insensitive phrase match on word boundaries, after
                 folding curly quotes and whitespace.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_MER = r"(a\.?m\.?|p\.?m\.?)"
_RANGE = re.compile(
    rf"\b(\d{{1,2}})(?:[:.](\d{{2}}))?\s*{_MER}?\s*(?:-|–|—|to|until|till)\s*"
    rf"(\d{{1,2}})(?:[:.](\d{{2}}))?\s*{_MER}?", re.I)
_CLOCK = re.compile(rf"\b(\d{{1,2}})[:.](\d{{2}})\s*{_MER}?", re.I)
_HOUR_MER = re.compile(rf"\b(\d{{1,2}})\s*{_MER}", re.I)
_AT_HOUR = re.compile(r"\b(?:at|by|around|from)\s+(\d{1,2})\b(?![:.\d])", re.I)
_WORDS = {"noon": 720, "midday": 720, "midnight": 0}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
_MONTH_RE = "|".join(m[:3] + r"[a-z]*\.?" for m in MONTHS)
_MONTH_DAY = re.compile(rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b", re.I)
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})\b(?!/)")
_AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")


def fold(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().casefold()


def _to_minutes(h: int, m: int, mer: str | None) -> list[int]:
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return []
    if mer:
        if h < 1 or h > 12:
            return []
        pm = mer.lower().startswith("p")
        h = (h % 12) + (12 if pm else 0)
        return [h * 60 + m]
    if 1 <= h <= 11:
        return [h * 60 + m, (h + 12) * 60 + m]
    if h == 12:
        return [12 * 60 + m]
    return [h * 60 + m]


def time_mentions(text: str) -> list[list[int]]:
    """Every clock time in `text`, each as its candidate minutes-after-midnight."""
    out: list[list[int]] = []
    spans: list[tuple[int, int]] = []
    for mt in _RANGE.finditer(text):
        h1, m1, mer1, h2, m2, mer2 = mt.groups()
        if not (mer1 or mer2 or m1 or m2):
            continue                     # "1-2" alone is not a time
        end = _to_minutes(int(h2), int(m2 or 0), mer2)
        start = _to_minutes(int(h1), int(m1 or 0), mer1 or mer2)
        if mer2 and not mer1 and end and start and start[0] > end[0]:
            start = _to_minutes(int(h1), int(m1 or 0), "am")   # 11-1pm
        if start:
            out.append(start)
        if end:
            out.append(end)
        spans.append(mt.span())

    def free(span):
        return not any(a <= span[0] < b for a, b in spans)

    for mt in _CLOCK.finditer(text):
        if free(mt.span()):
            c = _to_minutes(int(mt.group(1)), int(mt.group(2)), mt.group(3))
            if c:
                out.append(c)
                spans.append(mt.span())
    for mt in _HOUR_MER.finditer(text):
        if free(mt.span()):
            c = _to_minutes(int(mt.group(1)), 0, mt.group(2))
            if c:
                out.append(c)
                spans.append(mt.span())
    for mt in _AT_HOUR.finditer(text):
        if free(mt.span(1)):
            c = _to_minutes(int(mt.group(1)), 0, None)
            if c:
                out.append(c)
    low = text.casefold()
    for word, minute in _WORDS.items():
        if re.search(rf"\b{word}\b", low):
            out.append([minute])
    return out


def times(text: str) -> set[int]:
    return {m for cands in time_mentions(text) for m in cands}


def _month(name: str) -> int:
    key = name.casefold().rstrip(".")[:3]
    return [m[:3] for m in MONTHS].index(key) + 1


def dates(text: str, today: date) -> set[date]:
    """Every date `text` names, resolved against `today` (the anchor's day)."""
    low = text.casefold()
    out: set[date] = set()
    if re.search(r"\btoday\b|\btonight\b|\bthis (?:morning|afternoon|evening)\b", low):
        out.add(today)
    if re.search(r"\btomorrow\b", low):
        out.add(today + timedelta(days=1))
    if re.search(r"\byesterday\b", low):
        out.add(today - timedelta(days=1))
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}s?\b", low):
            ahead = (i - today.weekday()) % 7
            out.add(today + timedelta(days=ahead))
    for mt in _MONTH_DAY.finditer(text):
        _add(out, today.year, _month(mt.group(1)), int(mt.group(2)))
    for mt in _DAY_MONTH.finditer(text):
        _add(out, today.year, _month(mt.group(2)), int(mt.group(1)))
    for mt in _SLASH.finditer(text):
        _add(out, today.year, int(mt.group(1)), int(mt.group(2)))
    return out


def _add(out: set[date], year: int, month: int, day: int) -> None:
    try:
        out.add(date(year, month, day))
    except ValueError:
        pass


def amounts(text: str) -> set[str]:
    return {a.replace(",", "") for a in _AMOUNT.findall(text)}


def phrase_hit(phrase: str, text: str) -> bool:
    p = fold(phrase)
    if not p:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", fold(text)) is not None


def anchor_hit(anchor: str, text: str, today: date) -> bool:
    if anchor.startswith("@"):
        h, m = anchor[1:].split(":")
        return int(h) * 60 + int(m) in times(text)
    if anchor.startswith("#"):
        return date.fromisoformat(anchor[1:]) in dates(text, today)
    return phrase_hit(anchor, text)


def any_hit(anchors: list[str], text: str, today: date) -> str | None:
    """The first anchor found in `text`, or None."""
    for a in anchors:
        if anchor_hit(a, text, today):
            return a
    return None
