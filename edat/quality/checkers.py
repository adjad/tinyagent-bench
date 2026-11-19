"""Shared deterministic checkers for the nine format and style preferences.

Spec 03 section 4.5 (P1-P9) and 14-standing-preferences.md items 1-9. Every
checker is a pure function of the answer text, the surface it appears on and
a small settings object (`Meta`). No model, no network, no clock.

    from edat.quality.checkers import check, Meta
    r = check("P6", "Your train leaves at 15:40.", "answer", Meta(source_times=["15:40"]))
    r.status   # "pass" | "fail" | "n/a"

Exact rules are in edat/quality/CHECKERS.md. The agentic and in-assistant
tiers turn these results into their own scores; this module only decides
pass, fail or n/a for one text.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

from edat.quality import normalize as N
from edat.quality import textutil as T

CHECKERS_VERSION = "edat-prefs/1.0"

PREF_IDS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9")
PREF_NAMES = {
    "P1": "Lead with the answer",
    "P2": "At most three sentences unless asked for detail",
    "P3": "Bullets for three or more items",
    "P4": "No tables",
    "P5": "No emoji",
    "P6": "24-hour times",
    "P7": 'Dates written like "Tue 19 Nov"',
    "P8": "Metric units",
    "P9": "Drafted emails end with a fixed sign-off",
}
SURFACES = ("answer", "draft_email", "draft_message", "json", "tool_args")
SCOPE = {
    "P1": {"answer"}, "P2": {"answer"}, "P3": {"answer"},
    "P4": {"answer", "draft_email", "draft_message"},
    "P5": {"answer", "draft_email", "draft_message"},
    "P6": {"answer", "draft_email", "draft_message"},
    "P7": {"answer", "draft_email", "draft_message"},
    "P8": {"answer", "draft_email", "draft_message"},
    "P9": {"draft_email"},
}


@dataclass
class Meta:
    """The case labels a checker may read (spec 03 s4.5). All optional.

    answer_key        anchors of the answer: strings ("15:40", "QX-4471") or
                      {"type", "value", "aliases"} dicts
    request           the user's request text (P1 restatement test)
    detail_requested  set by the case author; P2 is n/a when true
    expected_items    parallel items the answer should list (P3)
    qtype             spec 03 s4.1 qtype; numbered lists count for P3 only in "steps"
    source_times      "HH:MM" times in the source (P6 value check)
    source_dates      ISO dates in the source (P7 reference)
    source_quantities [{"value": 5, "unit": "mi"}] imperial quantities in the source (P8)
    case_year         the case's year (P7)
    active            False on a turn where the user overrode the preference
    signoff           the person's sign-off (P9), may hold a newline
    source_text       the source; quoted spans that reproduce it are exempt from P5-P8
    needs_time        P6 fails when no time is written but one was needed
    needs_date        P7 likewise
    """

    answer_key: list = field(default_factory=list)
    request: str | None = None
    detail_requested: bool = False
    expected_items: int | None = None
    qtype: str | None = None
    source_times: list[str] = field(default_factory=list)
    source_dates: list[str] = field(default_factory=list)
    source_quantities: list[dict] = field(default_factory=list)
    case_year: int | None = None
    active: bool = True
    signoff: str | None = None
    source_text: str | None = None
    needs_time: bool = False
    needs_date: bool = False

    @classmethod
    def of(cls, meta: "Meta | Mapping[str, Any] | None") -> "Meta":
        if meta is None:
            return cls()
        if isinstance(meta, cls):
            return meta
        known = {k: v for k, v in dict(meta).items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class CheckResult:
    pref_id: str
    status: str                       # pass | fail | n/a
    reason: str = ""
    spans: list[tuple[int, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # reported, never failing (e.g. emoticons)

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spans"] = [list(s) for s in self.spans]
        return d


def _na(pid: str, reason: str) -> CheckResult:
    return CheckResult(pid, "n/a", reason)


def _ok(pid: str, reason: str = "", notes: list[str] | None = None) -> CheckResult:
    return CheckResult(pid, "pass", reason, notes=notes or [])


def _fail(pid: str, reason: str, spans: Iterable[tuple[int, int]] = ()) -> CheckResult:
    return CheckResult(pid, "fail", reason, list(spans))


# --------------------------------------------------------------------------
# Quoted-source exemption (P5-P8)
# --------------------------------------------------------------------------

_QUOTE_SPAN = re.compile(r"\"([^\"\n]{2,})\"|“([^”\n]{2,})”")


def exempt_mask(text: str, source_text: str | None) -> list[tuple[int, int]]:
    """Spans of quoted text or block quotes that reproduce the source verbatim."""
    if not source_text:
        return []
    src = T.fold(source_text)
    spans = []
    for m in _QUOTE_SPAN.finditer(text):
        inner = m.group(1) or m.group(2)
        if T.fold(inner) in src:
            spans.append(m.span())
    pos = 0
    for line in text.split("\n"):
        if line.lstrip().startswith(">"):
            body = line.lstrip()[1:].strip()
            if body and T.fold(body) in src:
                spans.append((pos, pos + len(line)))
        pos += len(line) + 1
    return spans


def _outside(span: tuple[int, int], mask: list[tuple[int, int]]) -> bool:
    return all(span[1] <= a or span[0] >= b for a, b in mask)


def _code_mask(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(r"```.*?(?:```|$)|`[^`\n]*`", text, re.S)]


# --------------------------------------------------------------------------
# P1 / D4: lead with the answer
# --------------------------------------------------------------------------

def first_unit(text: str) -> tuple[str, int]:
    """The first sentence, or the first list item if the answer opens with a list.

    Returns (unit_text, offset in words of the unit's start, always 0 here).
    A lead-in line ending in ":" directly above a list is joined with the
    first item ("Four things on Tuesday:\\n- 09:30 Dentist").
    """
    blocks = T.parse_blocks(text)
    if not blocks:
        return "", 0
    b0 = blocks[0]
    if b0.kind in ("bullets", "numbered") and b0.items:
        return b0.items[0].text, 0
    if b0.kind == "paragraph":
        sents = T.split_sentences(b0.text)
        first = sents[0] if sents else b0.text
        if first.rstrip().endswith(":") and len(blocks) > 1 and blocks[1].kind in ("bullets", "numbered") and blocks[1].items:
            return first + " " + blocks[1].items[0].text, 0
        return first, 0
    if b0.kind == "heading":
        rest = blocks[1:]
        return (b0.text + " " + first_unit("\n".join(b.text for b in rest))[0]) if rest else b0.text, 0
    return b0.text, 0


def answer_first(text: str, answer_key: list, *, request: str | None = None,
                 case_year: int | None = None, max_words: int = 30) -> tuple[bool, str]:
    """The shared P1 / D4 rule. Returns (passed, reason)."""
    unit, _ = first_unit(text)
    if not unit.strip():
        return False, "empty answer"
    if T.is_filler_opener(unit):
        return False, "first sentence is a filler opener"
    for anchor in answer_key:
        atype, value, aliases = N.parse_anchor(anchor)
        spans = N.anchor_spans(unit, atype, value, aliases=aliases, case_year=case_year)
        if not spans:
            continue
        # within the first `max_words` words of the whole answer
        pre = T.strip_markdown(text)
        stripped_unit = T.strip_markdown(unit)
        idx = pre.find(stripped_unit[:20]) if stripped_unit else 0
        offset_words = len(T.words(pre[:max(idx, 0)]))
        anchor_words = len(T.words(unit[:spans[0][0]]))
        if offset_words + anchor_words < max_words:
            return True, f"anchor {value!r} in the first unit"
        return False, f"anchor {value!r} after word {max_words}"
    if T.restates_request(unit, request):
        return False, "first sentence restates the request"
    return False, "no answer-key anchor in the first sentence or list item"


def check_p1(text: str, surface: str, meta: Meta) -> CheckResult:
    if not meta.answer_key:
        return _na("P1", "case has no answer key")
    ok, reason = answer_first(text, meta.answer_key, request=meta.request, case_year=meta.case_year)
    return _ok("P1", reason) if ok else _fail("P1", reason)


# --------------------------------------------------------------------------
# P2: at most three sentences unless asked for detail
# --------------------------------------------------------------------------

def sentence_units(text: str) -> int:
    """Prose sentences outside lists + 1 per list block (if every item <= 20 words;
    a longer item counts as its own sentences). Code blocks are not counted."""
    units = 0
    for b in T.parse_blocks(text):
        if b.kind in ("code", "hr", "table", "html"):
            if b.kind == "table":
                units += 1
            continue
        if b.kind in ("bullets", "numbered"):
            long_items = [it for it in b.items if T.word_count(it.text) > 20]
            if not long_items:
                units += 1
            else:
                units += 1 if len(long_items) < len(b.items) else 0
                for it in long_items:
                    units += max(1, len(T.split_sentences(T.strip_markdown(it.text))))
            continue
        body = T.strip_markdown(b.text) if b.kind != "quote" else re.sub(r"^\s*>\s?", "", b.text, flags=re.M)
        units += len(T.split_sentences(body))
    return units


def check_p2(text: str, surface: str, meta: Meta) -> CheckResult:
    if meta.detail_requested:
        return _na("P2", "detail requested")
    if not text.strip():
        return _na("P2", "empty answer")
    u = sentence_units(text)
    return _ok("P2", f"{u} units") if u <= 3 else _fail("P2", f"{u} units (> 3)")


# --------------------------------------------------------------------------
# P3: bullets for three or more items
# --------------------------------------------------------------------------

def check_p3(text: str, surface: str, meta: Meta) -> CheckResult:
    if not meta.expected_items or meta.expected_items < 3:
        return _na("P3", "fewer than 3 expected items")
    for b in T.list_blocks(T.parse_blocks(text)):
        top = [it for it in b.items if it.depth == 0]
        if b.kind == "numbered" and meta.qtype != "steps":
            continue
        if len(top) >= meta.expected_items:
            return _ok("P3", f"list with {len(top)} items")
    has_numbered = any(b.kind == "numbered" for b in T.parse_blocks(text))
    if has_numbered and meta.qtype != "steps":
        return _fail("P3", "numbered list where bullets were expected")
    return _fail("P3", f"no bullet list with >= {meta.expected_items} items")


# --------------------------------------------------------------------------
# P4: no tables
# --------------------------------------------------------------------------

def _column_starts(line: str) -> set[int]:
    starts = set()
    for m in re.finditer(r"(?<=\S) {2,}(?=\S)|\t+(?=\S)", line):
        starts.add(m.end())
    return starts


def find_tables(text: str) -> list[tuple[str, int]]:
    """(kind, line_index) for every table form P4 forbids."""
    found = []
    lines = text.replace("\r\n", "\n").split("\n")
    for b in T.parse_blocks(text):
        if b.kind == "table":
            found.append(("gfm", 0))
    # pipe lines with a separator row, even if the parser missed them
    for i in range(len(lines) - 1):
        if lines[i].count("|") >= 2 and lines[i + 1].count("|") >= 2:
            window = lines[max(0, i - 1): i + 3]
            if any(re.fullmatch(r"\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*", w) for w in window):
                found.append(("pipe", i))
                break
    if re.search(r"<table\b", text, re.I):
        found.append(("html", 0))
    for i in range(len(lines) - 2):
        a, b, c = (_column_starts(lines[i]), _column_starts(lines[i + 1]), _column_starts(lines[i + 2]))
        if len(a & b & c) >= 2:
            found.append(("aligned", i))
            break
    return found


def check_p4(text: str, surface: str, meta: Meta) -> CheckResult:
    t = find_tables(text)
    if t:
        return _fail("P4", "table: " + ", ".join(sorted({k for k, _ in t})))
    return _ok("P4")


# --------------------------------------------------------------------------
# P5: no emoji
# --------------------------------------------------------------------------

def check_p5(text: str, surface: str, meta: Meta) -> CheckResult:
    mask = exempt_mask(text, meta.source_text)
    hits = [(a, b) for a, b, _k in T.find_emoji(text) if _outside((a, b), mask)]
    notes = [f"emoticon {text[a:b]!r}" for a, b in T.find_emoticons(text)]
    if hits:
        return _fail("P5", f"{len(hits)} emoji", hits)
    return _ok("P5", notes=notes)


# --------------------------------------------------------------------------
# P6: 24-hour times
# --------------------------------------------------------------------------

def check_p6(text: str, surface: str, meta: Meta) -> CheckResult:
    mask = exempt_mask(text, meta.source_text) + _code_mask(text)
    times = [a for a in N.find_times(text) if _outside((a.start, a.end), mask)]
    if not times:
        if meta.needs_time:
            return _fail("P6", "no time written where one was needed")
        return _na("P6", "no time in the answer")
    bad = []
    reasons = []
    src = {s if len(s) == 5 else "0" + s for s in meta.source_times}
    for a in times:
        if a.form == "12h":
            bad.append(a); reasons.append(f"12-hour form {a.surface!r}")
        elif a.form == "oclock":
            bad.append(a); reasons.append(f"o'clock form {a.surface!r}")
        elif a.form == "word":
            bad.append(a); reasons.append(f"{a.surface!r} used as a time")
        elif a.form == "invalid":
            bad.append(a); reasons.append(f"hour out of range {a.surface!r}")
        elif src and a.value not in src:
            h, m = int(a.value[:2]), a.value[3:]
            alt = f"{(h + 12) % 24:02d}:{m}"
            if alt in src:
                bad.append(a); reasons.append(f"{a.surface!r} where the source says {alt}")
    if bad:
        return _fail("P6", "; ".join(reasons), [(a.start, a.end) for a in bad])
    return _ok("P6", f"{len(times)} times")


# --------------------------------------------------------------------------
# P7: dates like "Tue 19 Nov"
# --------------------------------------------------------------------------

_P7_FORM = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?: (\d{4}))?$")


def check_p7(text: str, surface: str, meta: Meta) -> CheckResult:
    mask = exempt_mask(text, meta.source_text) + _code_mask(text)
    dates = [a for a in N.find_dates(text) if _outside((a.start, a.end), mask)]
    bad: list[tuple[int, int]] = []
    reasons: list[str] = []
    date_spans = [(a.start, a.end) for a in dates]
    for a in dates:
        m = _P7_FORM.match(a.surface)
        if not m:
            bad.append((a.start, a.end)); reasons.append(f"date form {a.surface!r}")
            continue
        wd, day, mon, year = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        y = int(year) if year else meta.case_year
        if year and meta.case_year is not None and int(year) == meta.case_year:
            bad.append((a.start, a.end)); reasons.append(f"year written for the case year in {a.surface!r}")
            continue
        if y is None:
            continue
        try:
            real = _dt.date(y, N.MONTHS[mon.lower()], day).weekday()
        except ValueError:
            bad.append((a.start, a.end)); reasons.append(f"no such date {a.surface!r}")
            continue
        if real != N.WEEKDAYS[wd.lower()]:
            bad.append((a.start, a.end)); reasons.append(f"wrong weekday in {a.surface!r} (is {N.WD_ABBR[real]})")
    # a weekday alone standing for a date
    for w in N.find_weekdays(text):
        if not _outside((w.start, w.end), mask) or not _outside((w.start, w.end), date_spans):
            continue
        tail = text[w.end:w.end + 1]
        before = text[max(0, w.start - 8):w.start].lower()
        if w.surface.rstrip(".").lower().endswith("s") and w.surface.rstrip(".").lower() not in N.WEEKDAYS:
            continue  # plural "Tuesdays": a recurrence, not a date
        if re.search(r"\bevery\s*$", before):
            continue
        bad.append((w.start, w.end)); reasons.append(f"weekday alone {w.surface!r}")
        del tail
    if bad:
        return _fail("P7", "; ".join(reasons), bad)
    if not dates:
        if meta.needs_date:
            return _fail("P7", "no date written where one was needed")
        return _na("P7", "no date in the answer")
    return _ok("P7", f"{len(dates)} dates")


# --------------------------------------------------------------------------
# P8: metric units
# --------------------------------------------------------------------------

_IMPERIAL = (
    r"mi|miles?|mph|ft|foot|feet|yd|yards?|lbs?|pounds?|oz|ounces?|°\s?F|fahrenheit|gallons?|"
    r"gal|pints?|acres?|stone|inch(?:es)?"
)
_IMP_RX = re.compile(rf"(?P<n>\d+(?:[.,]\d+)?)\s?(?:-\s?)?(?P<u>{_IMPERIAL})\b", re.I)
_DEG_F = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s?(?:°\s?F\b|degrees?\s+(?:fahrenheit|F)\b)", re.I)
_IN_RX = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s?in\b(?=\s*(?:$|[.,;:)!?]|x\b|by\b|wide\b|long\b|tall\b|high\b|deep\b))", re.I)
_INCH_MARK = re.compile(r"(?<![\w\"])(?P<n>\d+(?:\.\d+)?)(?:\"|″)")

_TO_METRIC = {  # unit -> (dimension, factor, metric unit)
    "mi": ("length", 1.609344, "km"), "mile": ("length", 1.609344, "km"), "miles": ("length", 1.609344, "km"),
    "ft": ("length", 0.3048, "m"), "foot": ("length", 0.3048, "m"), "feet": ("length", 0.3048, "m"),
    "yd": ("length", 0.9144, "m"), "yard": ("length", 0.9144, "m"), "yards": ("length", 0.9144, "m"),
    "in": ("length", 2.54, "cm"), "inch": ("length", 2.54, "cm"), "inches": ("length", 2.54, "cm"),
    "lb": ("mass", 0.45359237, "kg"), "lbs": ("mass", 0.45359237, "kg"), "pound": ("mass", 0.45359237, "kg"),
    "pounds": ("mass", 0.45359237, "kg"), "oz": ("mass", 28.349523125, "g"), "ounce": ("mass", 28.349523125, "g"),
    "ounces": ("mass", 28.349523125, "g"), "stone": ("mass", 6.35029318, "kg"),
    "gallon": ("volume", 3.785411784, "l"), "gallons": ("volume", 3.785411784, "l"),
    "pint": ("volume", 0.473176473, "l"), "pints": ("volume", 0.473176473, "l"),
    "mph": ("speed", 1.609344, "km/h"), "acre": ("area", 0.40468564224, "ha"), "acres": ("area", 0.40468564224, "ha"),
}
_METRIC_RX = re.compile(r"(?P<n>\d+(?:[.,]\d+)?)\s?(?P<u>km/h|kph|km|kilometres?|kilometers?|cm|mm|m|metres?|meters?|kg|kilos?|kilograms?|g|grams?|ml|l|litres?|liters?|ha|hectares?|°\s?C)\b", re.I)
_METRIC_UNIT = {"km": ("length", 1000), "kilometre": ("length", 1000), "kilometres": ("length", 1000),
                "kilometer": ("length", 1000), "kilometers": ("length", 1000), "m": ("length", 1),
                "metre": ("length", 1), "metres": ("length", 1), "meter": ("length", 1), "meters": ("length", 1),
                "cm": ("length", 0.01), "mm": ("length", 0.001), "kg": ("mass", 1000), "kilo": ("mass", 1000),
                "kilos": ("mass", 1000), "kilogram": ("mass", 1000), "kilograms": ("mass", 1000), "g": ("mass", 1),
                "gram": ("mass", 1), "grams": ("mass", 1), "l": ("volume", 1), "litre": ("volume", 1),
                "litres": ("volume", 1), "liter": ("volume", 1), "liters": ("volume", 1), "ml": ("volume", 0.001),
                "km/h": ("speed", 1), "kph": ("speed", 1), "ha": ("area", 1), "hectare": ("area", 1),
                "hectares": ("area", 1)}


def _is_currency_pound(text: str, start: int, end: int) -> bool:
    toks_before = re.findall(r"\S+", text[:start])[-3:]
    toks_after = re.findall(r"\S+", text[end:])[:3]
    return any("£" in t or "GBP" in t.upper() for t in toks_before + toks_after) or "£" in text[max(0, start - 2):start + 1]


def check_p8(text: str, surface: str, meta: Meta) -> CheckResult:
    mask = exempt_mask(text, meta.source_text) + _code_mask(text)
    bad: list[tuple[int, int]] = []
    reasons: list[str] = []
    for rx in (_IMP_RX, _IN_RX, _INCH_MARK, _DEG_F):
        for m in rx.finditer(text):
            span = m.span()
            if not _outside(span, mask) or not all(span[1] <= a or span[0] >= b for a, b in bad):
                continue
            u = (m.groupdict().get("u") or "").lower()
            if u.startswith("pound") and _is_currency_pound(text, *span):
                continue
            if rx is _INCH_MARK:
                if text[:span[0]].count('"') % 2 == 1:
                    continue  # closing quote of a quoted number
            bad.append(span)
            reasons.append(f"imperial {m.group(0)!r}")
    if bad:
        return _fail("P8", "; ".join(reasons), bad)
    # converted quantities: metric value within +-2 % of the exact conversion
    for q in meta.source_quantities or []:
        unit = str(q.get("unit", "")).lower()
        if unit not in _TO_METRIC:
            continue
        dim, factor, munit = _TO_METRIC[unit]
        exact_metric = float(q["value"]) * factor * _METRIC_UNIT.get(munit, (dim, 1))[1] if munit in _METRIC_UNIT else float(q["value"]) * factor
        cands = []
        for m in _METRIC_RX.finditer(text):
            mu = m.group("u").lower().replace(" ", "")
            if mu.startswith("°"):
                continue
            if mu not in _METRIC_UNIT or _METRIC_UNIT[mu][0] != dim:
                continue
            raw = m.group("n").replace(",", ".")
            val = float(raw) * _METRIC_UNIT[mu][1]
            decimals = len(raw.split(".")[1]) if "." in raw else 0
            target = round(exact_metric / _METRIC_UNIT[mu][1], decimals) * _METRIC_UNIT[mu][1]
            cands.append((abs(val - target) <= 0.02 * abs(exact_metric), m.span()))
        if cands and not any(ok for ok, _ in cands):
            return _fail("P8", f"converted value for {q['value']} {unit} off by more than 2 %", [s for _, s in cands])
    return _ok("P8")


# --------------------------------------------------------------------------
# P9: fixed sign-off on drafted emails
# --------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\[\s*(?:your|my)?\s*name\s*\]|<\s*(?:your\s*)?name\s*>|\{\s*name\s*\}|\[\s*sender\s*\]", re.I)


def _norm_lines(s: str) -> list[str]:
    return [re.sub(r"\s+", " ", T.nfkc(x)).strip() for x in s.split("\n") if x.strip()]


def check_p9(text: str, surface: str, meta: Meta) -> CheckResult:
    if not meta.signoff:
        return _na("P9", "no sign-off configured")
    m = _PLACEHOLDER.search(text)
    if m:
        return _fail("P9", f"placeholder {m.group(0)!r}", [m.span()])
    want = _norm_lines(meta.signoff)
    have = _norm_lines(text)
    if len(have) < len(want) or have[-len(want):] != want:
        return _fail("P9", "last lines are not the sign-off")
    k = len(want)
    count = sum(1 for i in range(len(have) - k + 1) if have[i:i + k] == want)
    if count != 1:
        return _fail("P9", f"sign-off appears {count} times")
    return _ok("P9")


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

_FUNCS: dict[str, Callable[[str, str, Meta], CheckResult]] = {
    "P1": check_p1, "P2": check_p2, "P3": check_p3, "P4": check_p4, "P5": check_p5,
    "P6": check_p6, "P7": check_p7, "P8": check_p8, "P9": check_p9,
}


def check(pref_id: str, text: str, surface: str = "answer", meta: Meta | Mapping | None = None) -> CheckResult:
    """check(pref_id, text, surface, meta) -> CheckResult (spec 03 s4.5)."""
    pid = pref_id.upper()
    if pid not in _FUNCS:
        raise KeyError(f"unknown preference id {pref_id!r}")
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}")
    m = Meta.of(meta)
    if not m.active:
        return _na(pid, "preference inactive on this turn")
    if surface not in SCOPE[pid]:
        return _na(pid, f"surface {surface} out of scope")
    return _FUNCS[pid](text or "", surface, m)


def check_all(text: str, surface: str = "answer", meta: Meta | Mapping | None = None,
              prefs: Iterable[str] = PREF_IDS) -> dict[str, CheckResult]:
    return {p: check(p, text, surface, meta) for p in prefs}


def pref_check_rate(results: Iterable[CheckResult]) -> dict[str, dict]:
    """Diagnostic pass rate per checker over applicable results (spec 03 s4.5)."""
    out: dict[str, dict] = {}
    for r in results:
        d = out.setdefault(r.pref_id, {"pass": 0, "fail": 0, "n/a": 0})
        d[r.status] += 1
    for d in out.values():
        app = d["pass"] + d["fail"]
        d["rate"] = d["pass"] / app if app else None
    return out
