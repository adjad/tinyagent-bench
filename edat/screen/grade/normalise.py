"""Answer normalisers for Read tasks (spec 11 §3.7: normalised exact match).

Spec 03 owns the shared date, time and number normalisers; until they are
shared, these follow the same rules: dates to ISO, times to 24-hour `HH:MM`,
money to two decimals, numbers without thousands separators, text case-folded
with whitespace and punctuation collapsed.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                         "august", "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
NOT_SHOWN = re.compile(r"\b(not (shown|visible|listed|displayed|available|on (the |this )?screen|there|mentioned|"
                       r"stated|given|found)|no(t| such)? .{0,30}\b(shown|listed|visible|available)|"
                       r"(can'?t|cannot|could ?n[o']t|unable to) (find|see|tell|locate)|isn'?t (shown|listed|there|"
                       r"visible|available)|doesn'?t (show|say|list|mention)|does not (show|say|list|mention)|"
                       r"no information|unknown)\b", re.IGNORECASE)
WORD_NUMS = {"zero": 0, "none": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def text(s: str) -> str:
    s = re.sub(r"[^\w\s:.%-]", " ", str(s).casefold())
    return " ".join(s.split())


def times(s: str) -> set[str]:
    out = set()
    for m in re.finditer(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?![\d%])", str(s), re.IGNORECASE):
        h, mi, ap = int(m.group(1)), m.group(2), (m.group(3) or "").lower().replace(".", "")
        if mi is None and not ap:
            continue
        mi = int(mi or 0)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= mi < 60:
            out.add(f"{h:02d}:{mi:02d}")
    if re.search(r"\bnoon\b", str(s), re.I):
        out.add("12:00")
    if re.search(r"\bmidnight\b", str(s), re.I):
        out.add("00:00")
    return out


def numbers(s: str) -> set[str]:
    out = set()
    for m in re.finditer(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", str(s)):
        raw = m.group(0).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.add(f"{v:g}")
    for w, v in WORD_NUMS.items():
        if re.search(rf"\b{w}\b", str(s), re.I):
            out.add(f"{v:g}")
    return out


def money(s: str) -> set[str]:
    out = set()
    for m in re.finditer(r"(?<![\w.])\d[\d,]*(?:\.\d{1,2})?", str(s)):
        try:
            out.add(f"{float(m.group(0).replace(',', '')):.2f}")
        except ValueError:
            pass
    return out


def dates(s: str, year: int | None = None) -> set[str]:
    out = set()
    st = str(s)
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", st):
        out.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    for m in re.finditer(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?(?:\s+(\d{4}))?\b", st):
        mo = MONTHS.get(m.group(2).lower())
        if mo and year:
            out.add(_iso(int(m.group(3) or year), mo, int(m.group(1))))
    for m in re.finditer(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", st):
        mo = MONTHS.get(m.group(1).lower())
        if mo and year:
            out.add(_iso(int(m.group(3) or year), mo, int(m.group(2))))
    return {d for d in out if d}


def _iso(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def contains(kind: str, answer: str, want: str, year: int | None = None) -> bool:
    if kind == "time":
        w = times(want) or {want}
        return bool(w & times(answer))
    if kind == "money":
        w = money(want)
        return bool(w) and w <= money(answer)
    if kind == "number":
        w = numbers(want)
        return bool(w) and w <= numbers(answer)
    if kind == "date":
        w = dates(want, year) or {want}
        return bool(w & dates(answer, year))
    return text(want) in text(answer)


def check_answer(spec: dict, answer: str | None, year: int | None = None) -> tuple[bool, str]:
    """(ok, why-not) for a Read task's `answer` block."""
    if answer is None:
        return False, "no answer given"
    kind = spec.get("normalise", "text")
    if spec.get("abstain"):
        if not NOT_SHOWN.search(answer):
            return False, "the value is not on screen; the answer should say so"
        for bad in spec.get("not_contains") or []:
            if contains(kind, answer, str(bad), year):
                return False, f"answered with {bad!r}, which is not shown"
        return True, ""
    for w in spec.get("contains_all") or []:
        if not contains(kind, answer, str(w), year):
            return False, f"missing {w!r}"
    anys = spec.get("contains_any") or []
    if anys and not any(contains(kind, answer, str(w), year) for w in anys):
        return False, f"none of {anys!r}"
    for bad in spec.get("not_contains") or []:
        if contains(kind if kind != "text" else "text", answer, str(bad), year):
            return False, f"contains {bad!r}"
    if spec.get("regex") and not re.search(spec["regex"], answer, re.IGNORECASE):
        return False, f"does not match /{spec['regex']}/"
    return True, ""
