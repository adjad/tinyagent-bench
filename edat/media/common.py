"""Shared pieces for the media tests: seeded randomness, invented data, canonical forms.

Nothing here touches the network, a model or a real person's data. Every name,
brand, address, phone number and email comes from :class:`Inventor`, which
builds them from syllables and reserved ranges (555-01xx phone numbers,
``example.com`` / ``.test`` mail domains), so no value is copied from anywhere.

Canonicalisers turn a model's answer and a planted value into one comparable
form (spec 05 §7.1 "Scoring"): Unicode NFKC, case-fold and collapsed spaces for
text; ISO 8601 dates; 24-hour ``HH:MM`` times; money as amount plus currency;
phone numbers as digits.
"""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from datetime import date, timedelta

# ------------------------------------------------------------------ randomness


def derive_seed(seed: int | str, *labels: object) -> int:
    """A 64-bit seed derived from a base seed and labels, stable across runs and versions."""
    text = "\x1f".join([str(seed), *map(str, labels)])
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def rng_for(seed: int | str, *labels: object) -> random.Random:
    """A ``random.Random`` for one (seed, labels) stream. Same inputs, same draws."""
    return random.Random(derive_seed(seed, *labels))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ invented data

# Given names are ordinary and of many origins; surnames and brands are built
# from syllables so that no full name or brand is a real one. ASCII only: the
# committed renderer font covers ASCII (see vision/canvas.py).
GIVEN_NAMES = (
    "Amara", "Bilal", "Chen", "Dalia", "Emeka", "Farah", "Goran", "Hana", "Ines", "Jonas",
    "Kavya", "Lior", "Mateo", "Nadia", "Oskar", "Priya", "Quinn", "Rafael", "Sana", "Tomas",
    "Uma", "Viktor", "Wen", "Ximena", "Yusuf", "Zofia", "Aiko", "Bram", "Carmen", "Dmitri",
    "Elif", "Femi", "Gita", "Hugo", "Ilse", "Jun", "Kofi", "Lena", "Mehdi", "Noor",
)
_SYL_A = ("bra", "cal", "dor", "fen", "gal", "hol", "jar", "kel", "lun", "mor", "nev", "or",
          "pel", "quin", "ros", "sal", "tav", "ul", "ver", "wyn", "zan", "mar", "tel", "bel")
_SYL_B = ("dane", "ford", "wick", "mere", "ton", "vale", "row", "by", "sen", "ley", "ard",
          "quist", "holt", "more", "wood", "ridge", "stead", "lin", "ham", "ova", "ero", "anu")
_BRAND_A = ("Quil", "Tav", "Bren", "Corv", "Lum", "Maro", "Nexo", "Pell", "Ruva", "Sorr",
            "Trel", "Vanta", "Wex", "Yarr", "Zell", "Oaken", "Fenn", "Gild")
_BRAND_B = ("ora", "ix", "ent", "ella", "ock", "ura", "ane", "isto", "elle", "ova", "um", "ar")
_BRAND_KIND = ("Market", "Cafe", "Books", "Hardware", "Pharmacy", "Grocers", "Bakery",
               "Outfitters", "Kitchen", "Supply", "Deli", "Florist")
_STREET_KIND = ("St", "Ave", "Rd", "Lane", "Way", "Court", "Place")
_TOWNS = ("Fenwick Bay", "Marrowdale", "Quillton", "Ostervale", "Brindlemoor", "Corvan Hills",
          "Lunhaven", "Tavistead", "Rosmere", "Wynford Cross")
EVENT_WORDS = ("Planning", "Review", "Sync", "Workshop", "Lunch", "Dentist", "Yoga", "Standup",
               "Recital", "Book club", "Pottery", "Team dinner", "Interview", "Demo", "Tutoring")
PRODUCTS = ("Oat milk", "Rye loaf", "Lentils", "Olive oil", "Green tea", "Rice 2kg", "Apples",
            "Pasta", "Tomatoes", "Batteries AA", "Notebook", "Dish soap", "Coffee beans",
            "Yogurt", "Spinach", "Light bulb", "Tape", "Honey", "Granola", "Sponges")


class Inventor:
    """Invents people, brands, places and numbers from one seeded stream."""

    def __init__(self, rng: random.Random) -> None:
        self.r = rng

    def surname(self) -> str:
        return (self.r.choice(_SYL_A) + self.r.choice(_SYL_B)).capitalize()

    def person(self) -> str:
        return f"{self.r.choice(GIVEN_NAMES)} {self.surname()}"

    def given(self) -> str:
        return self.r.choice(GIVEN_NAMES)

    def brand(self) -> str:
        return self.r.choice(_BRAND_A) + self.r.choice(_BRAND_B)

    def merchant(self) -> str:
        return f"{self.brand()} {self.r.choice(_BRAND_KIND)}"

    def town(self) -> str:
        return self.r.choice(_TOWNS)

    def street_address(self) -> str:
        return f"{self.r.randint(2, 980)} {self.surname()} {self.r.choice(_STREET_KIND)}"

    def phone(self) -> str:
        """A reserved fictional US number: 555-0100 to 555-0199."""
        area = self.r.choice((201, 312, 415, 503, 617, 720, 808, 919))
        return f"({area}) 555-01{self.r.randint(0, 99):02d}"

    def email(self, name: str | None = None) -> str:
        local = (name or self.person()).lower().replace(" ", ".")
        return f"{local}@{self.r.choice(('example.com', 'example.org', 'mail.test'))}"

    def code(self, letters: int = 2, digits: int = 4) -> str:
        abc = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        return "".join(self.r.choice(abc) for _ in range(letters)) + \
            "".join(str(self.r.randint(0, 9)) for _ in range(digits))

    def booking_ref(self) -> str:
        abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(self.r.choice(abc) for _ in range(6))

    def money(self, lo: float = 1.0, hi: float = 200.0) -> float:
        return round(self.r.uniform(lo, hi), 2)

    def day(self, start: date, span_days: int = 60) -> date:
        return start + timedelta(days=self.r.randint(0, span_days))

    def clock(self, lo_h: int = 7, hi_h: int = 20, step_min: int = 15) -> str:
        h = self.r.randint(lo_h, hi_h)
        m = self.r.choice(range(0, 60, step_min))
        return f"{h:02d}:{m:02d}"


# ------------------------------------------------------------------ display forms

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def time_12h(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def date_long(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {d.year}"


def date_short(d: date) -> str:
    return f"{MONTHS[d.month - 1][:3]} {d.day}, {d.year}"


def money_str(amount: float, currency: str = "USD") -> str:
    sym = {"USD": "$"}.get(currency)
    return f"{sym}{amount:,.2f}" if sym else f"{amount:,.2f} {currency}"


# ------------------------------------------------------------------ canonical forms

_SPACE = re.compile(r"\s+")
_EDGE_PUNCT = " \t\n\"'`.,;:!?()[]{}<>*_"


def canon_text(value: object) -> str:
    """NFKC, case-folded, whitespace collapsed, edge punctuation and quotes stripped."""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = _SPACE.sub(" ", text).strip(_EDGE_PUNCT)
    return _SPACE.sub(" ", text).strip()


def canon_number(value: object) -> str | None:
    text = str(value).replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    num = float(m.group(0))
    return str(int(num)) if num == int(num) else f"{num:.10g}"


_MONTH_IDX = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
_MONTH_IDX.update({m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)})
_MONTH_IDX["sept"] = 9


def canon_date(value: object, *, default_year: int | None = None) -> str | None:
    """An ISO date (YYYY-MM-DD) from common English written forms, or None.

    Numeric slash dates are read month first (US), the convention the renderers use.
    """
    text = canon_text(value)
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = map(int, m.groups())
        return _iso(y, mo, d)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", text)
    if m:
        mo, d, y = map(int, m.groups())
        y = y + 2000 if y < 100 else y
        return _iso(y, mo, d)
    names = "|".join(sorted(_MONTH_IDX, key=len, reverse=True))
    m = re.search(rf"\b({names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", text)
    if m:
        y = int(m.group(3)) if m.group(3) else default_year
        return _iso(y, _MONTH_IDX[m.group(1)], int(m.group(2))) if y else None
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({names})\.?(?:,?\s+(\d{{4}}))?\b", text)
    if m:
        y = int(m.group(3)) if m.group(3) else default_year
        return _iso(y, _MONTH_IDX[m.group(2)], int(m.group(1))) if y else None
    return None


def _iso(y: int, mo: int, d: int) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def canon_time(value: object) -> str | None:
    """A 24-hour HH:MM time from '4:30 PM', '16:30', '4.30pm', '4 pm', 'noon', or None."""
    text = canon_text(value).replace(".", ":")
    if re.search(r"\bnoon\b|\bmidday\b", text):
        return "12:00"
    if re.search(r"\bmidnight\b", text):
        return "00:00"
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        pm = m.group(3).startswith("p")
        if not (1 <= h <= 12 and 0 <= mi < 60):
            return None
        h = (h % 12) + (12 if pm else 0)
        return f"{h:02d}:{mi:02d}"
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None
    return None


def canon_money(value: object, *, default_currency: str = "USD") -> str | None:
    """'12.50 USD' from '$12.50', '12.5', 'USD 12.50', '12.50 dollars', or None."""
    text = canon_text(value)
    cur = default_currency
    if "$" in text or "usd" in text or "dollar" in text:
        cur = "USD"
    elif "eur" in text:
        cur = "EUR"
    elif "gbp" in text or "pound" in text:
        cur = "GBP"
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    if not m:
        return None
    amount = float(m.group(0).replace(",", ""))
    return f"{amount:.2f} {cur}"


def canon_phone(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


CANONICALISERS = {
    "text": canon_text,
    "number": canon_number,
    "date": canon_date,
    "time": canon_time,
    "money": canon_money,
    "phone": canon_phone,
}


def canonical(kind: str, value: object, **kw) -> str | None:
    """The canonical form of ``value`` for an answer kind; None when it cannot be read."""
    if value is None:
        return None
    fn = CANONICALISERS.get(kind, canon_text)
    if fn is canon_date:
        return fn(value, default_year=kw.get("default_year"))
    out = fn(value)
    return out if out != "" else None
