"""Normalizer and anchor matcher (spec 03 section 7.1, 7.6).

Detects dates, times, amounts, numbers, durations, codes, phones, emails,
URLs and capitalized names in English text, normalizes each to a canonical
value, and matches planted-fact anchors against an answer. Everything is
deterministic and stdlib-only.

Canonical forms:
    date      -> (year | None, month, day)   ISO "YYYY-MM-DD" when the year is known
    time      -> "HH:MM" (24-hour)
    amount    -> Decimal (thousands separators removed)
    number    -> Decimal (number words zero..ninety-nine, "a dozen" normalized)
    duration  -> minutes (int)
    weekday   -> 0..6 (Mon = 0)
    code      -> upper-case with unified dashes
    text      -> fold()ed substring
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from edat.quality.textutil import fold, nfkc

NORMALIZER_VERSION = "edat-normalize/1"

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_FULL = ["", "January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]
WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5,
    "sat": 5, "sunday": 6, "sun": 6,
}
WD_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WD_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_MON_RX = r"(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)"
_WD_RX = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues|tue|wed|thurs|thur|thu|fri|sat|sun)"
_ORD = r"(?:st|nd|rd|th)"


@dataclass(frozen=True)
class Atom:
    kind: str           # date, time, amount, number, duration, weekday, code, phone, email, url, name
    value: object
    start: int
    end: int
    surface: str
    form: str = ""      # detector form label (e.g. "iso", "dmy-slash", "wd-d-mon")


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_DATE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("iso", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
    # Weekday + day + month [+ year]: "Tue 19 Nov", "Tuesday, 19 November 2027"
    ("wd-d-mon", re.compile(rf"\b({_WD_RX})\.?(,?)\s+(\d{{1,2}}){_ORD}?\s+({_MON_RX})\.?(?:,?\s+(\d{{4}}))?\b", re.I)),
    # Weekday + month + day: "Tuesday, March 12", "Fri Mar 12th, 2027"
    ("wd-mon-d", re.compile(rf"\b({_WD_RX})\.?,?\s+({_MON_RX})\.?\s+(\d{{1,2}}){_ORD}?(?:,?\s+(\d{{4}}))?\b", re.I)),
    # Day + month [+ year]: "12 March", "12th of March 2027"
    ("d-mon", re.compile(rf"\b(\d{{1,2}})({_ORD})?(?:\s+of)?\s+({_MON_RX})\.?(?:,?\s+(\d{{4}}))?\b", re.I)),
    # Month + day [+ year]: "March 12", "Mar 12th, 2027"
    ("mon-d", re.compile(rf"\b({_MON_RX})\.?\s+(\d{{1,2}})({_ORD})?(?![\d:])(?:,?\s+(\d{{4}}))?\b", re.I)),
    # Numeric day-first: "12/03", "12/03/2027", "12.03.2027"
    ("dmy-slash", re.compile(r"(?<![\d/.:])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")),
    ("dmy-dot", re.compile(r"(?<![\d.:])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d.])")),
    # Bare ordinal day: "the 21st", "on the 12th"
    ("ordinal", re.compile(rf"\b(\d{{1,2}})({_ORD})\b", re.I)),
]


def _valid(y: int | None, m: int, d: int) -> bool:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return False
    try:
        _dt.date(y or 2028, m, d)  # 2028 is a leap year, so 29 Feb is allowed without a year
    except ValueError:
        return False
    return True


def find_dates(text: str) -> list[Atom]:
    """Every specific date the detector recognizes, longest match first, no overlaps."""
    t = nfkc(text)
    found: list[Atom] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for form, rx in _DATE_PATTERNS:
        for m in rx.finditer(t):
            a, b = m.span()
            if not free(a, b):
                continue
            g = m.groups()
            label = form
            y = mo = d = None
            wd = None
            if form == "iso":
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            elif form == "wd-d-mon":
                wd = WEEKDAYS[g[0].lower()]
                d, mo = int(g[2]), MONTHS[g[3].lower()]
                y = int(g[4]) if g[4] else None
                if g[1] or len(g[0]) > 3 or g[0][:1].islower() or len(g[3]) > 3 or g[3][:1].islower():
                    label = "wd-d-mon-long"
            elif form == "wd-mon-d":
                wd = WEEKDAYS[g[0].lower()]
                mo, d = MONTHS[g[1].lower()], int(g[2])
                y = int(g[3]) if g[3] else None
            elif form == "d-mon":
                d, mo = int(g[0]), MONTHS[g[2].lower()]
                y = int(g[3]) if g[3] else None
            elif form == "mon-d":
                # "may 3" as a verb ("you may 3 times") is unlikely; require capital for "May"
                if g[0].lower() == "may" and not g[0][0].isupper():
                    continue
                mo, d = MONTHS[g[0].lower()], int(g[1])
                y = int(g[3]) if g[3] else None
            elif form in ("dmy-slash", "dmy-dot"):
                d, mo = int(g[0]), int(g[1])
                if g[2]:
                    y = int(g[2])
                    if y < 100:
                        y += 2000
            elif form == "ordinal":
                d = int(g[0])
                if not 1 <= d <= 31:
                    continue
                found.append(Atom("date", (None, None, d), a, b, m.group(0), form))
                taken.append((a, b))
                continue
            if d is None or mo is None or not _valid(y, mo, d):
                continue
            found.append(Atom("date", (y, mo, d, wd), a, b, m.group(0), label))
            taken.append((a, b))
    found.sort(key=lambda x: x.start)
    return found


def weekday_of(y: int, m: int, d: int) -> int:
    return _dt.date(y, m, d).weekday()


def format_p7(date: _dt.date, case_year: int | None = None) -> str:
    """The P7 form: "Tue 19 Nov" (+ " 2027" when the year differs from case_year)."""
    s = f"{WD_ABBR[date.weekday()]} {date.day} {MONTH_ABBR[date.month]}"
    if case_year is not None and date.year != case_year:
        s += f" {date.year}"
    return s


def date_aliases(date: _dt.date) -> list[str]:
    """Every English surface form the generator writes, so alias lists are complete."""
    d, m, y = date.day, date.month, date.year
    wd = date.weekday()
    sfx = "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return [
        date.isoformat(), f"{d} {MONTH_FULL[m]}", f"{d} {MONTH_ABBR[m]}", f"{MONTH_FULL[m]} {d}",
        f"{MONTH_ABBR[m]} {d}", f"{WD_ABBR[wd]} {d} {MONTH_ABBR[m]}", f"{WD_FULL[wd]} {d} {MONTH_FULL[m]}",
        f"{WD_FULL[wd]}, {MONTH_FULL[m]} {d}", f"{d:02d}/{m:02d}", f"{d}/{m}/{y}", f"{d}{sfx} {MONTH_FULL[m]}",
        f"{MONTH_FULL[m]} {d}{sfx}", f"{d} {MONTH_FULL[m]} {y}",
    ]


# --------------------------------------------------------------------------
# Times
# --------------------------------------------------------------------------

_T12 = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s?m\b\.?", re.I)
_T24 = re.compile(r"(?<![\d:.,/])(\d{1,2}):(\d{2})(?![\d:])")
_OCLOCK = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+o'?\s?clock\b", re.I)
_NOONMID = re.compile(r"\b(noon|midday|midnight)\b", re.I)


def find_times(text: str) -> list[Atom]:
    t = nfkc(text).replace("\u2019", "'")
    out: list[Atom] = []
    taken: list[tuple[int, int]] = []
    for m in _T12.finditer(t):
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        if not (1 <= h <= 12 and mi < 60):
            continue
        h24 = (h % 12) + (12 if m.group(3).lower() == "p" else 0)
        out.append(Atom("time", f"{h24:02d}:{mi:02d}", m.start(), m.end(), m.group(0), "12h"))
        taken.append(m.span())
    for m in _OCLOCK.finditer(t):
        out.append(Atom("time", None, m.start(), m.end(), m.group(0), "oclock"))
        taken.append(m.span())
    for m in _NOONMID.finditer(t):
        val = "00:00" if m.group(1).lower() == "midnight" else "12:00"
        out.append(Atom("time", val, m.start(), m.end(), m.group(0), "word"))
        taken.append(m.span())
    for m in _T24.finditer(t):
        if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
            continue
        h, mi = int(m.group(1)), int(m.group(2))
        if mi >= 60:
            continue
        form = "24h" if h <= 23 else "invalid"
        out.append(Atom("time", f"{h:02d}:{mi:02d}" if h <= 23 else None, m.start(), m.end(), m.group(0), form))
    out.sort(key=lambda a: a.start)
    return out


# --------------------------------------------------------------------------
# Numbers, amounts, durations
# --------------------------------------------------------------------------

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "a dozen": 12, "dozen": 12, "none": 0, "no": 0, "single": 1, "a single": 1,
}
_NUMWORD_RX = re.compile(
    r"\b(?:(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[-\s](one|two|three|four|five|six|seven|eight|nine)"
    r"|(a dozen|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety))\b", re.I)

_CUR_SYM = r"(?:€|\$|£|EUR|USD|GBP|CHF|SEK|NOK|DKK)"
_NUM = r"[-\u2212]?(?:\d{1,3}(?:[,\u202f\u00a0](?:\d{3}))+|\d+)(?:\.\d+)?"
_NUM_EU = r"[-\u2212]?\d{1,3}(?:\.\d{3})+,\d{2}"
_AMOUNT_RX = re.compile(
    rf"(?:(?P<pre>{_CUR_SYM})\s?(?P<n1>{_NUM_EU}|{_NUM}))|(?:(?P<n2>{_NUM_EU}|{_NUM})\s?(?P<post>{_CUR_SYM}|euros?|dollars?|pounds?)\b)",
    re.I)
_PLAIN_NUM_RX = re.compile(rf"(?<![\w.,:/-])(?P<n>{_NUM_EU}|{_NUM})(?![\w:/]|[.,]\d)")


def parse_decimal(s: str) -> Decimal | None:
    s = s.strip().replace("\u2212", "-")
    if re.fullmatch(_NUM_EU, s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = re.sub(r"[,\u202f\u00a0 ]", "", s)
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def find_amounts(text: str) -> list[Atom]:
    t = nfkc(text)
    out = []
    for m in _AMOUNT_RX.finditer(t):
        raw = m.group("n1") or m.group("n2")
        cur = (m.group("pre") or m.group("post") or "").upper()
        cur = {"€": "EUR", "$": "USD", "£": "GBP", "EUROS": "EUR", "EURO": "EUR", "DOLLARS": "USD",
               "DOLLAR": "USD", "POUNDS": "GBP", "POUND": "GBP"}.get(cur, cur)
        val = parse_decimal(raw)
        if val is not None:
            out.append(Atom("amount", (val, cur), m.start(), m.end(), m.group(0), "amount"))
    return out


def find_numbers(text: str, *, words_too: bool = True) -> list[Atom]:
    """Plain numbers (not parts of dates or times) plus number words."""
    t = nfkc(text)
    blocked = [(a.start, a.end) for a in find_dates(t) + find_times(t)]
    out = []
    for m in _PLAIN_NUM_RX.finditer(t):
        a, b = m.span("n")
        if any(not (b <= s or a >= e) for s, e in blocked):
            continue
        val = parse_decimal(m.group("n"))
        if val is not None:
            out.append(Atom("number", val, a, b, m.group("n"), "digits"))
    if words_too:
        for m in _NUMWORD_RX.finditer(t):
            if m.group(1):
                v = _NUM_WORDS[m.group(1).lower()] + _NUM_WORDS[m.group(2).lower()]
            else:
                v = _NUM_WORDS[m.group(3).lower()]
            out.append(Atom("number", Decimal(v), m.start(), m.end(), m.group(0), "words"))
    out.sort(key=lambda x: x.start)
    return out


_DUR_RX = re.compile(
    r"(?:(?P<h>\d+(?:\.\d+)?|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:h|hrs?|hours?)\b\.?"
    r"(?:\s*(?:and\s*)?(?P<hm>\d{1,2})\s*(?:m|mins?|minutes?)\b)?)"
    r"|(?:(?P<m>\d+)\s*(?:m|mins?|minutes?)\b)"
    r"|(?:(?P<hh>\d+)h(?P<mm>\d{2})\b)", re.I)
_DAYS_RX = re.compile(r"\b(?P<d>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                      r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty(?:[-\s]\w+)?|thirty(?:[-\s]\w+)?)"
                      r"\s+(?:calendar\s+)?days?\b", re.I)


def _wordnum(s: str) -> Decimal | None:
    s = s.lower().strip()
    if s in ("a", "an"):
        return Decimal(1)
    if s in _NUM_WORDS:
        return Decimal(_NUM_WORDS[s])
    m = re.fullmatch(r"(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[-\s](\w+)", s)
    if m and m.group(2) in _NUM_WORDS:
        return Decimal(_NUM_WORDS[m.group(1)] + _NUM_WORDS[m.group(2)])
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def find_durations(text: str) -> list[Atom]:
    """Durations in minutes ("2 h 30 min", "90 minutes", "1.5 hours", "2h30")."""
    t = nfkc(text)
    out = []
    for m in _DUR_RX.finditer(t):
        if m.group("hh"):
            mins = int(m.group("hh")) * 60 + int(m.group("mm"))
        elif m.group("h"):
            h = _wordnum(m.group("h"))
            if h is None:
                continue
            mins = int(h * 60) + int(m.group("hm") or 0)
        else:
            mins = int(m.group("m"))
        out.append(Atom("duration", mins, m.start(), m.end(), m.group(0), "duration"))
    return out


def find_day_counts(text: str) -> list[Atom]:
    out = []
    for m in _DAYS_RX.finditer(nfkc(text)):
        v = _wordnum(m.group("d"))
        if v is not None:
            out.append(Atom("days", int(v), m.start(), m.end(), m.group(0), "days"))
    return out


def find_weekdays(text: str) -> list[Atom]:
    out = []
    for m in re.finditer(rf"\b({_WD_RX})s?\b\.?", nfkc(text), re.I):
        w = m.group(1)
        if len(w) <= 4 and w[0].islower():   # "sat", "wed", "sun" as ordinary words
            continue
        out.append(Atom("weekday", WEEKDAYS[w.lower()], m.start(), m.end(), m.group(0), "weekday"))
    return out


# --------------------------------------------------------------------------
# Codes, phones, emails, URLs, names
# --------------------------------------------------------------------------

_CODE_RX = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z]{1,6}-?\d{2,7}(?:-[A-Za-z0-9]{1,6})?\b"
                      r"|\b\d{2,6}-[A-Z]{1,4}\b")
_PHONE_RX = re.compile(r"(?<![\w])\+?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}(?![\w])")
_EMAIL_RX = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_URL_RX = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+[^\s<>()\"'.,;:!?]")


def norm_code(s: str) -> str:
    return re.sub(r"[\u2010-\u2015\u2212]", "-", nfkc(s)).upper()


def find_codes(text: str) -> list[Atom]:
    t = nfkc(text)
    blocked = [(a.start, a.end) for a in find_dates(t)]
    out = []
    for m in _CODE_RX.finditer(t):
        if any(not (m.end() <= s or m.start() >= e) for s, e in blocked):
            continue
        s = m.group(0)
        if re.fullmatch(r"\d+(st|nd|rd|th|h|am|pm|km|kg|m|g|ml|cm|mm|min|s)", s, re.I):
            continue
        if re.fullmatch(r"[A-Za-z]{1,3}\d{1,2}", s) is None and not re.search(r"\d{2,}", s):
            continue
        out.append(Atom("code", norm_code(s), m.start(), m.end(), s, "code"))
    return out


def find_phones(text: str) -> list[Atom]:
    out = []
    for m in _PHONE_RX.finditer(nfkc(text)):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 7 and ("555" in digits or m.group(0).startswith("+") or len(digits) >= 9):
            out.append(Atom("phone", digits, m.start(), m.end(), m.group(0), "phone"))
    return out


def find_emails(text: str) -> list[Atom]:
    return [Atom("email", m.group(0).lower(), m.start(), m.end(), m.group(0), "email")
            for m in _EMAIL_RX.finditer(nfkc(text))]


def find_urls(text: str) -> list[Atom]:
    return [Atom("url", m.group(0).lower().rstrip("/"), m.start(), m.end(), m.group(0), "url")
            for m in _URL_RX.finditer(nfkc(text))]


COMMON_CAPITALIZED = {
    "i", "i'm", "i've", "i'll", "i'd", "ok", "okay", "yes", "no", "the", "a", "an", "and", "but", "or",
    "you", "your", "we", "they", "he", "she", "it", "this", "that", "these", "those", "there", "here",
    "today", "tomorrow", "yesterday", "tonight", "eur", "usd", "gbp", "json", "csv", "pdf", "am", "pm",
    "ai", "faq", "mr", "mrs", "ms", "dr", "prof", "me", "my", "our", "note", "total", "subject", "re",
    "fwd", "from", "to", "cc", "date", "also", "then", "next", "last", "first", "second", "third",
    "key", "summary", "action", "actions", "decision", "decisions", "owner", "due", "paid", "unpaid",
}
_CAP_RX = re.compile(r"(?<![\w@./-])([A-Z][a-z\u00e0-\u00ff]+(?:[-'][A-Z]?[a-z\u00e0-\u00ff]+)*)\b")


def find_names(text: str) -> list[Atom]:
    """Capitalized words that are not the first word of a sentence or list item.

    A deliberately simple person/place detector for the fabrication check.
    Weekdays, months and a list of common capitalized words are skipped.
    """
    t = nfkc(text)
    out = []
    for m in _CAP_RX.finditer(t):
        w = m.group(1)
        lw = w.lower()
        if lw in COMMON_CAPITALIZED or lw in MONTHS or lw in WEEKDAYS or lw.rstrip("s") in WEEKDAYS:
            continue
        before = t[:m.start()].rstrip(" *_\"'\u201c(")
        if not before or before[-1] in ".!?:\n-\u2022" or re.search(r"(^|\n)\s*(?:[-*+]|\d+[.)])?\s*$", t[:m.start()]):
            continue
        out.append(Atom("name", fold(w), m.start(), m.end(), w, "name"))
    return out


# --------------------------------------------------------------------------
# All atoms (fabrication check)
# --------------------------------------------------------------------------

def find_atoms(text: str) -> list[Atom]:
    """Every checkable atom: dates, times, amounts, 2+-digit numbers, codes,
    phones, emails, URLs and capitalized names (spec 03 s7.6)."""
    atoms: list[Atom] = []
    atoms += find_urls(text)
    atoms += find_emails(text)
    atoms += find_phones(text)
    atoms += find_dates(text)
    atoms += find_times(text)
    atoms += find_amounts(text)
    atoms += find_codes(text)
    taken = [(a.start, a.end) for a in atoms]

    def free(a: Atom) -> bool:
        return all(a.end <= s or a.start >= e for s, e in taken)

    for a in find_numbers(text, words_too=False):
        if free(a) and len(re.sub(r"\D", "", a.surface)) >= 2:
            atoms.append(a)
            taken.append((a.start, a.end))
    for a in find_names(text):
        if free(a):
            atoms.append(a)
    # drop atoms nested inside URLs/emails
    outer = [(a.start, a.end) for a in atoms if a.kind in ("url", "email")]
    atoms = [a for a in atoms if a.kind in ("url", "email") or all(a.end <= s or a.start >= e for s, e in outer)]
    atoms.sort(key=lambda a: a.start)
    return atoms


def atom_key(a: Atom, case_year: int | None = None) -> tuple:
    """A hashable canonical key used to compare atoms across texts."""
    if a.kind == "date":
        v = a.value
        if v[1] is None:
            return ("dayonly", v[2])
        return ("date", v[1], v[2])
    if a.kind == "amount":
        return ("num", a.value[0].normalize())
    if a.kind == "number":
        return ("num", a.value.normalize())
    return (a.kind, a.value)


def atom_keys(text: str) -> set[tuple]:
    keys = set()
    for a in find_atoms(text):
        keys.add(atom_key(a))
    # numbers inside amounts/codes also count as present in the source
    for a in find_numbers(text, words_too=True):
        keys.add(("num", a.value.normalize()))
    for a in find_names(text):
        keys.add(("name", a.value))
    f = fold(text)
    for w in re.findall(r"[a-z\u00e0-\u00ff][a-z\u00e0-\u00ff'-]+", f):
        keys.add(("name", w))
    for a in find_durations(text):
        keys.add(("duration", a.value))
    return keys


# --------------------------------------------------------------------------
# Anchor matching
# --------------------------------------------------------------------------

def _text_present(text: str, needle: str) -> bool:
    n = fold(needle)
    if not n:
        return False
    hay = fold(text)
    hay = re.sub(r"[*_`]", "", hay)
    pat = r"(?<![\w])" + re.escape(n) + r"(?![\w])"
    return re.search(pat, hay) is not None


def infer_anchor_type(value: str) -> str:
    v = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return "date"
    if re.fullmatch(r"\d{1,2}:\d{2}", v):
        return "time"
    if re.fullmatch(rf"(?:{_CUR_SYM}\s?)?{_NUM}(?:\s?{_CUR_SYM})?", v, re.I) and re.search(r"\d\.\d{2}\b|€|EUR|USD|GBP|\$|£", v):
        return "amount"
    if re.fullmatch(_NUM, v):
        return "number"
    if _CODE_RX.fullmatch(v):
        return "code"
    return "text"


def anchor_spans(text: str, atype: str, value, *, aliases=(), case_year: int | None = None) -> list[tuple[int, int]]:
    """Spans in `text` where the anchor (or one of its aliases) appears, after normalization."""
    spans: list[tuple[int, int]] = []
    if atype == "date":
        d = _dt.date.fromisoformat(str(value))
        for a in find_dates(text):
            y, m, day = a.value[0], a.value[1], a.value[2]
            if m is None:
                continue
            if m == d.month and day == d.day and (y is None or y == d.year):
                spans.append((a.start, a.end))
    elif atype == "time":
        want = str(value)
        if len(want) == 4:
            want = "0" + want
        for a in find_times(text):
            if a.value == want:
                spans.append((a.start, a.end))
    elif atype == "amount":
        m = re.search(_NUM_EU + "|" + _NUM, str(value))
        want = parse_decimal(m.group(0)) if m else None
        if want is not None:
            for a in find_amounts(text):
                if a.value[0] == want:
                    spans.append((a.start, a.end))
            for a in find_numbers(text, words_too=False):
                if a.value == want and all(not (a.start >= s and a.end <= e) for s, e in spans):
                    spans.append((a.start, a.end))
    elif atype == "number":
        want = parse_decimal(str(value)) if not isinstance(value, (int, Decimal)) else Decimal(value)
        for a in find_numbers(text):
            if a.value == want:
                spans.append((a.start, a.end))
        for a in find_amounts(text):
            if a.value[0] == want:
                spans.append((a.start, a.end))
    elif atype == "duration":
        want = int(value)
        for a in find_durations(text):
            if a.value == want:
                spans.append((a.start, a.end))
    elif atype == "days":
        want = int(value)
        for a in find_day_counts(text):
            if a.value == want:
                spans.append((a.start, a.end))
    elif atype == "weekday":
        want = WEEKDAYS[str(value).lower()] if not isinstance(value, int) else value
        for a in find_weekdays(text):
            if a.value == want:
                spans.append((a.start, a.end))
        for a in find_dates(text):
            if len(a.value) > 3 and a.value[3] == want:
                spans.append((a.start, a.end))
    elif atype == "code":
        want = norm_code(str(value))
        for a in find_codes(text):
            if a.value == want:
                spans.append((a.start, a.end))
        if not spans:
            nt = norm_code(text)
            for m in re.finditer(r"(?<![A-Z0-9])" + re.escape(want) + r"(?![A-Z0-9])", nt):
                spans.append(m.span())
    elif atype == "phone":
        want = re.sub(r"\D", "", str(value))
        for a in find_phones(text):
            if a.value == want or a.value.endswith(want[-7:]):
                spans.append((a.start, a.end))
    elif atype in ("email", "url"):
        finder = find_emails if atype == "email" else find_urls
        want = str(value).lower().rstrip("/")
        for a in finder(text):
            if a.value == want:
                spans.append((a.start, a.end))
    else:
        if _text_present(text, str(value)):
            idx = fold(text).find(fold(str(value)))
            spans.append((max(idx, 0), max(idx, 0) + len(str(value))))
    if not spans:
        for al in aliases or ():
            if _text_present(text, str(al)):
                idx = fold(text).find(fold(str(al)))
                spans.append((max(idx, 0), max(idx, 0) + len(str(al))))
                break
    return spans


def anchor_present(text: str, atype: str, value, *, aliases=(), case_year: int | None = None) -> bool:
    return bool(anchor_spans(text, atype, value, aliases=aliases, case_year=case_year))


def parse_anchor(anchor) -> tuple[str, object, tuple]:
    """Accept "QX-4471", {"type": "code", "value": ...} or ("code", "QX-4471")."""
    if isinstance(anchor, dict):
        return anchor.get("type") or infer_anchor_type(str(anchor["value"])), anchor["value"], tuple(anchor.get("aliases", ()))
    if isinstance(anchor, (tuple, list)) and len(anchor) >= 2:
        return anchor[0], anchor[1], tuple(anchor[2]) if len(anchor) > 2 else ()
    return infer_anchor_type(str(anchor)), anchor, ()
