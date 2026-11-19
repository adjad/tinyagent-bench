"""Rung 9: a multi-step task with tools (read, edit, run tests, fix).

The model is told only what is wrong ("the tests in X fail"), not shown the
code: it has to read the files, change the target, run the visible tests and
try again if they still fail. Hidden tests decide the grade. Each case is also
a valid answer-only probe (its `prompt` shows the files), which the validator
uses; the run uses tool mode only.
"""
from __future__ import annotations

import random

from edat.sidekick.ceiling.rungs import answer_files, fill, probe_instance, probe_template, show_file
from edat.sidekick.gen.core import pick

# ------------------------------------------------------------------ restock plan

_RESTOCK_BUGGY = '''"""Work out how many packs to order so every item gets back to its minimum level."""


def packs_needed(on_hand, minimum, pack_size):
    short = minimum - on_hand
    if short < 0:
        return 0
    return short // pack_size


def restock_plan(stock, minimums, pack_size=@PACK@):
    """stock and minimums map item -> count. Items missing from stock count as 0 on hand."""
    plan = {}
    for item, minimum in minimums.items():
        packs = packs_needed(stock[item], minimum, pack_size)
        if packs:
            plan[item] = packs
    return plan
'''

_RESTOCK_FIXED = '''"""Work out how many packs to order so every item gets back to its minimum level."""


def packs_needed(on_hand, minimum, pack_size):
    short = minimum - on_hand
    if short <= 0:
        return 0
    return -(-short // pack_size)


def restock_plan(stock, minimums, pack_size=@PACK@):
    """stock and minimums map item -> count. Items missing from stock count as 0 on hand."""
    plan = {}
    for item, minimum in minimums.items():
        packs = packs_needed(stock.get(item, 0), minimum, pack_size)
        if packs:
            plan[item] = packs
    return plan
'''

_RESTOCK_VISIBLE = '''from inventory import restock_plan


def test_partial_pack_rounds_up():
    assert restock_plan({"@A@": 1}, {"@A@": 1 + @PACK@ + 1}) == {"@A@": 2}


def test_missing_item_counts_as_zero():
    assert restock_plan({}, {"@B@": @PACK@}) == {"@B@": 1}
'''

_RESTOCK_HIDDEN = '''import random

from inventory import packs_needed, restock_plan


def _ref(stock, minimums, pack_size):
    out = {}
    for item, m in minimums.items():
        short = m - stock.get(item, 0)
        if short > 0:
            out[item] = -(-short // pack_size)
    return out


def test_exact_fill_needs_no_extra_pack():
    assert restock_plan({"x": 0}, {"x": @PACK@ * 2}) == {"x": 2}


def test_at_or_over_minimum_orders_nothing():
    assert restock_plan({"x": 5, "y": 9}, {"x": 5, "y": 3}) == {}


def test_packs_needed():
    assert packs_needed(0, 1, @PACK@) == 1
    assert packs_needed(3, 3, @PACK@) == 0


def test_default_pack_size():
    assert restock_plan({}, {"z": @PACK@ + 1}) == {"z": 2}


def test_random():
    rng = random.Random(99)
    for _ in range(200):
        items = [f"i{k}" for k in range(rng.randint(1, 6))]
        stock = {i: rng.randint(0, 30) for i in items if rng.random() < 0.7}
        mins = {i: rng.randint(0, 30) for i in items}
        size = rng.randint(1, 12)
        assert restock_plan(stock, mins, size) == _ref(stock, mins, size)
'''


def _restock(rng: random.Random):
    v = dict(PACK=pick(rng, [6, 8, 10, 12]), A=pick(rng, ["batteries", "tape", "gloves"]),
             B=pick(rng, ["labels", "toner", "cups"]))
    buggy, fixed = fill(_RESTOCK_BUGGY, **v), fill(_RESTOCK_FIXED, **v)
    visible = {"test_inventory.py": fill(_RESTOCK_VISIBLE, **v)}
    task = ("The tests in `test_inventory.py` fail. Find out why and fix `inventory.py` so that every item "
            "gets enough whole packs to reach its minimum level. Do not change the tests.")
    prompt = task + "\n\n" + show_file("inventory.py", buggy) + "\n" + show_file("test_inventory.py", visible["test_inventory.py"])
    half_fix = fixed.replace("stock.get(item, 0)", "stock[item]")
    other_half = buggy.replace("stock[item]", "stock.get(item, 0)")
    return probe_instance(
        prompt=prompt, targets=["inventory.py"], files={"inventory.py": buggy}, visible_tests=visible,
        hidden_tests=fill(_RESTOCK_HIDDEN, **v),
        references=[answer_files({"inventory.py": fixed})],
        wrong=[answer_files({"inventory.py": half_fix}), answer_files({"inventory.py": other_half})],
        tool={"kind": "probe", "prompt": task, "max_calls": 10, "fixed": {"inventory.py": fixed}})


# ------------------------------------------------------------------ shared builder

def _edit(src: str, old: str, new: str) -> str:
    assert old in src, old
    return src.replace(old, new)


def _make(v: dict, *, task: str, buggy: dict, fixed: dict, visible: dict, hidden: str, wrongs: list[dict]):
    """Build one agent instance. buggy/fixed hold every given file (template text); a target is a file that
    differs. Each wrong answer gives every target whole (template text)."""
    buggy = {k: fill(s, **v) for k, s in buggy.items()}
    fixed = {k: fill(s, **v) for k, s in fixed.items()}
    visible = {k: fill(s, **v) for k, s in visible.items()}
    targets = [k for k in buggy if buggy[k] != fixed[k]]
    assert targets
    wrong_answers = []
    for w in wrongs:
        w = {k: fill(s, **v) for k, s in w.items()}
        assert set(w) == set(targets) and any(w[t] != fixed[t] for t in targets)
        wrong_answers.append(answer_files(w))
    end_state = {t: fixed[t] for t in targets}
    prompt = task + "\n\n" + "\n".join(show_file(k, s) for k, s in buggy.items())
    prompt += "\n" + "\n".join(show_file(k, s) for k, s in visible.items())
    return probe_instance(
        prompt=prompt, targets=targets, files=buggy, visible_tests=visible, hidden_tests=fill(hidden, **v),
        references=[answer_files(end_state), answer_files(end_state, style="label")], wrong=wrong_answers,
        tool={"kind": "probe", "prompt": task, "max_calls": 10, "fixed": end_state})


# ------------------------------------------------------------------ invoice rounding (helper module)

_INV_HELPERS_BUGGY = r'''"""Small money helpers shared by the billing code."""


def round_money(amount):
    """Round an amount to cents. Halves go away from zero: 2.675 -> 2.68 and -2.675 -> -2.68."""
    return round(amount, 2)
'''

_INV_HELPERS_FIXED = r'''"""Small money helpers shared by the billing code."""
from decimal import Decimal, ROUND_HALF_UP


def round_money(amount):
    """Round an amount to cents. Halves go away from zero: 2.675 -> 2.68 and -2.675 -> -2.68."""
    return float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
'''

_INVOICE = r'''from helpers import round_money


def line_total(qty, unit_price):
    return round_money(qty * unit_price)


def invoice_total(lines, tax_rate=@TAX@):
    """lines: list of (qty, unit_price). Every line is rounded to cents, then the tax on the subtotal is rounded."""
    subtotal = round_money(sum(line_total(q, p) for q, p in lines))
    return round_money(subtotal + round_money(subtotal * tax_rate))
'''

_INV_VISIBLE = r'''from invoice import invoice_total, line_total


def test_half_cent_rounds_up():
    assert line_total(1, 0.125) == 0.13


def test_single_line_no_tax():
    assert invoice_total([(1, @PRICE@)], 0) == @EXPECT@
'''

_INV_HIDDEN = r'''import random
from decimal import Decimal, ROUND_HALF_UP

from helpers import round_money
from invoice import invoice_total, line_total


def _r(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _ref_total(lines, tax):
    sub = _r(sum(_r(q * p) for q, p in lines))
    return _r(sub + _r(sub * tax))


def test_positive_ties():
    assert round_money(0.125) == 0.13
    assert round_money(2.675) == 2.68
    assert round_money(1.005) == 1.01


def test_negative_ties_go_away_from_zero():
    assert round_money(-2.675) == -2.68
    assert round_money(-0.125) == -0.13
    assert round_money(-1.005) == -1.01


def test_plain_values():
    assert round_money(3) == 3.0
    assert round_money(10.4) == 10.4
    assert round_money(0.004) == 0.0
    assert round_money(-0.004) == 0.0


def test_invoice_examples():
    assert invoice_total([(1, 2.675)], 0) == 2.68
    assert invoice_total([(3, 0.335), (2, 1.005)], 0) == _ref_total([(3, 0.335), (2, 1.005)], 0)
    assert invoice_total([(1, 100.0), (1, -2.675)], 0.1) == _ref_total([(1, 100.0), (1, -2.675)], 0.1)
    assert line_total(4, 0.125) == 0.5


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        lines = [(rng.randint(1, 9), round(rng.uniform(-40, 80), 3)) for _ in range(rng.randint(1, 4))]
        tax = rng.choice([0, 0.05, 0.07, 0.1])
        assert invoice_total(lines, tax) == _ref_total(lines, tax)
        assert round_money(lines[0][1] / 8) == _r(lines[0][1] / 8)
'''


def _invoice(rng: random.Random):
    price = pick(rng, [(2.675, 2.68), (1.005, 1.01), (0.455, 0.46), (3.115, 3.12)])
    v = dict(TAX=pick(rng, [0.05, 0.07, 0.08, 0.1]), PRICE=price[0], EXPECT=price[1])
    task = ("The tests in `test_invoice.py` fail: invoice totals are a cent off. Find the cause and fix it. "
            "Amounts must round to cents with halves going away from zero, for credits (negative amounts) too. "
            "Do not change the tests.")
    floor = _edit(_INV_HELPERS_FIXED, 'return float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))',
                  "return math.floor(amount * 100 + 0.5) / 100").replace("from decimal import Decimal, ROUND_HALF_UP", "import math")
    even = _edit(_INV_HELPERS_FIXED, "ROUND_HALF_UP))", "ROUND_HALF_EVEN))").replace("ROUND_HALF_UP\n", "ROUND_HALF_EVEN\n")
    even = even.replace("import Decimal, ROUND_HALF_UP", "import Decimal, ROUND_HALF_EVEN")
    return _make(v, task=task,
                 buggy={"helpers.py": _INV_HELPERS_BUGGY, "invoice.py": _INVOICE},
                 fixed={"helpers.py": _INV_HELPERS_FIXED, "invoice.py": _INVOICE},
                 visible={"test_invoice.py": _INV_VISIBLE}, hidden=_INV_HIDDEN,
                 wrongs=[{"helpers.py": floor}, {"helpers.py": even}])


# ------------------------------------------------------------------ log levels (two files)

_LOGPARSE_BUGGY = r'''"""Parse one log line: `YYYY-MM-DD HH:MM:SS LEVEL message text`."""


def parse_line(line):
    """Return {"time": "HH:MM:SS", "level": ..., "message": ...}; a line with fewer than three fields gives None.
    The message is everything after the level (it may contain spaces) and is "" when there is none."""
    parts = line.strip().split(" ")
    if len(parts) < 3:
        return None
    return {"time": parts[1], "level": parts[2], "message": parts[3]}
'''

_LOGPARSE_FIXED = r'''"""Parse one log line: `YYYY-MM-DD HH:MM:SS LEVEL message text`."""


def parse_line(line):
    """Return {"time": "HH:MM:SS", "level": ..., "message": ...}; a line with fewer than three fields gives None.
    The message is everything after the level (it may contain spaces) and is "" when there is none."""
    parts = line.strip().split(" ", 3)
    if len(parts) < 3:
        return None
    return {"time": parts[1], "level": parts[2], "message": parts[3] if len(parts) == 4 else ""}
'''

_LOG_REPORT_BUGGY = r'''from logparse import parse_line

SEVERE = ("ERROR", "CRITICAL")


def hourly_errors(lines):
    """Count the severe lines (levels in SEVERE) per hour: {"09": 3, ...}. Only hours that have any appear."""
    counts = {}
    for line in lines:
        rec = parse_line(line)
        if rec is None:
            continue
        if rec["level"] == "ERROR":
            hour = rec["time"][:2]
            counts[hour] = counts.get(hour, 0) + 1
    return counts


def severe_messages(lines):
    """Messages of the severe lines, in order."""
    out = []
    for line in lines:
        rec = parse_line(line)
        if rec and rec["level"] in SEVERE:
            out.append(rec["message"])
    return out
'''

_LOG_REPORT_FIXED = _LOG_REPORT_BUGGY.replace('if rec["level"] == "ERROR":', 'if rec["level"] in SEVERE:')

_LOG_VISIBLE = r'''from report import hourly_errors, severe_messages

LINES = [
    "@DATE@ 09:05:01 INFO service started",
    "@DATE@ 09:15:30 ERROR @MSG@",
    "@DATE@ 10:01:09 CRITICAL out of memory",
]


def test_messages_keep_all_words():
    assert severe_messages(LINES)[0] == "@MSG@"


def test_critical_counts_as_severe():
    assert hourly_errors(LINES) == {"09": 1, "10": 1}
'''

_LOG_HIDDEN = r'''import random

from logparse import parse_line
from report import hourly_errors, severe_messages

D = "@DATE@"


def _ref_rec(line):
    parts = line.strip().split(" ", 3)
    if len(parts) < 3:
        return None
    return parts[1], parts[2], parts[3] if len(parts) == 4 else ""


def test_parse_keeps_message():
    assert parse_line(D + " 09:15:02 ERROR disk is full on /data") == {
        "time": "09:15:02", "level": "ERROR", "message": "disk is full on /data"}
    assert parse_line(D + " 09:15:02 WARN a  b")["message"] == "a  b"


def test_parse_no_message():
    assert parse_line(D + " 09:15:02 INFO") == {"time": "09:15:02", "level": "INFO", "message": ""}
    assert parse_line(D + " 09:15:02 INFO\n")["message"] == ""


def test_parse_short_lines():
    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line(D + " 09:15:02") is None


def test_hourly_counts():
    lines = [D + " 08:00:01 ERROR a", D + " 08:30:00 CRITICAL b", D + " 08:31:00 WARN c", "",
             D + " 23:59:59 ERROR", D + " 07:00:00 INFO ok"]
    assert hourly_errors(lines) == {"08": 2, "23": 1}
    assert hourly_errors([]) == {}


def test_severe_messages_in_order():
    lines = [D + " 08:00:01 ERROR first one", D + " 08:00:02 DEBUG no", D + " 08:00:03 CRITICAL", D + " 08:00:04 ERROR last  one"]
    assert severe_messages(lines) == ["first one", "", "last  one"]


def test_random():
    rng = random.Random(1234)
    words = ["disk", "net", "db", "timeout", "retry", "ok"]
    for _ in range(200):
        lines = []
        for _ in range(rng.randint(0, 12)):
            r = rng.random()
            if r < 0.08:
                lines.append("")
                continue
            msg = " ".join(rng.choice(words) for _ in range(rng.randint(0, 4)))
            lines.append("%s %02d:%02d:00 %s %s" % (D, rng.randint(0, 23), rng.randint(0, 59),
                                                   rng.choice(["INFO", "WARN", "ERROR", "CRITICAL", "DEBUG"]), msg))
        counts, msgs = {}, []
        for line in lines:
            rec = _ref_rec(line)
            if rec and rec[1] in ("ERROR", "CRITICAL"):
                counts[rec[0][:2]] = counts.get(rec[0][:2], 0) + 1
                msgs.append(rec[2])
        assert hourly_errors(lines) == counts
        assert severe_messages(lines) == msgs
'''


def _logs(rng: random.Random):
    v = dict(DATE=pick(rng, ["2026-03-04", "2026-07-19", "2027-01-02"]),
             MSG=pick(rng, ["disk is full on /data", "cannot reach the queue broker", "payment worker timed out twice"]))
    task = ("The tests in `test_report.py` fail. Find out why and fix the code so that the report counts every "
            "severe line (ERROR and CRITICAL) per hour and returns each severe line's full message. "
            "Do not change the tests.")
    wrong_parser_only = {"logparse.py": _LOGPARSE_FIXED, "report.py": _LOG_REPORT_BUGGY}
    wrong_report_only = {"logparse.py": _LOGPARSE_BUGGY, "report.py": _LOG_REPORT_FIXED}
    return _make(v, task=task,
                 buggy={"logparse.py": _LOGPARSE_BUGGY, "report.py": _LOG_REPORT_BUGGY},
                 fixed={"logparse.py": _LOGPARSE_FIXED, "report.py": _LOG_REPORT_FIXED},
                 visible={"test_report.py": _LOG_VISIBLE}, hidden=_LOG_HIDDEN,
                 wrongs=[wrong_parser_only, wrong_report_only])


# ------------------------------------------------------------------ settings values (helper module)

_CFG_BUGGY = r'''"""Turn the text after `=` in a settings line into a Python value."""


def coerce(raw):
    """Surrounding spaces are ignored. true/yes/on give True and false/no/off give False, in any letter case.
    Text that is a whole number (an optional minus sign allowed) gives an int, other numbers give a float,
    and anything else stays a string."""
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    if raw in ("true", "yes", "on"):
        return True
    if raw in ("false", "no", "off"):
        return False
    return raw
'''

_CFG_FIXED = r'''"""Turn the text after `=` in a settings line into a Python value."""


def coerce(raw):
    """Surrounding spaces are ignored. true/yes/on give True and false/no/off give False, in any letter case.
    Text that is a whole number (an optional minus sign allowed) gives an int, other numbers give a float,
    and anything else stays a string."""
    raw = raw.strip()
    low = raw.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    for kind in (int, float):
        try:
            return kind(raw)
        except ValueError:
            pass
    return raw
'''

_CFG_APP = r'''from cfg import coerce

DEFAULTS = {"retries": 3, "verbose": False, "ratio": 0.5, "name": "@NAME@"}


def load_settings(text, defaults=DEFAULTS):
    """Read `key = value` lines over a copy of the defaults. Blank lines, lines starting with # and lines
    without an = are skipped. Only the first = splits a line. Later lines win."""
    settings = dict(defaults)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip()] = coerce(value)
    return settings
'''

_CFG_VISIBLE = r'''from app import load_settings


def test_negative_number():
    assert load_settings("retries = @NEG@")["retries"] == @NEG@


def test_capitalised_yes():
    assert load_settings("verbose = Yes")["verbose"] is True
'''

_CFG_HIDDEN = r'''import random

from app import DEFAULTS, load_settings
from cfg import coerce


def _same(a, b):
    assert a == b and type(a) is type(b), (a, b)


def _ref(raw):
    raw = raw.strip()
    if raw.lower() in ("true", "yes", "on"):
        return True
    if raw.lower() in ("false", "no", "off"):
        return False
    for kind in (int, float):
        try:
            return kind(raw)
        except ValueError:
            pass
    return raw


def test_bool_words_any_case():
    for t in ["true", "TRUE", "Yes", "on", " ON "]:
        _same(coerce(t), True)
    for f in ["false", "False", "NO", "Off", " off"]:
        _same(coerce(f), False)


def test_numbers():
    _same(coerce("42"), 42)
    _same(coerce("-3"), -3)
    _same(coerce("007"), 7)
    _same(coerce("2.5"), 2.5)
    _same(coerce("-0.25"), -0.25)
    _same(coerce(" 7 "), 7)


def test_strings():
    _same(coerce("hello world "), "hello world")
    _same(coerce(""), "")
    _same(coerce("12abc"), "12abc")
    _same(coerce("onward"), "onward")


def test_load_settings():
    text = "# comment\n\nretries = 5\nratio= 0.25\nverbose =OFF\nname = a = b\nnoequals\n  extra  =  -1  \n"
    got = load_settings(text)
    assert got == {"retries": 5, "verbose": False, "ratio": 0.25, "name": "a = b", "extra": -1}
    _same(got["ratio"], 0.25)
    assert load_settings("retries = 1\nretries = 2")["retries"] == 2


def test_defaults_untouched():
    before = dict(DEFAULTS)
    load_settings("retries = 9\nnew = x")
    assert DEFAULTS == before
    assert load_settings("") == before


def test_random():
    rng = random.Random(1234)
    toks = ["true", "False", "YES", "no", "On", "OFF", "7", "-12", "0", "3.75", "-0.5", "abc", "a b", "x1", "", "  9 "]
    for _ in range(300):
        raw = rng.choice(toks)
        _same(coerce(raw), _ref(raw))
'''


def _config(rng: random.Random):
    v = dict(NAME=pick(rng, ["nightly", "staging", "intake"]), NEG=pick(rng, [-2, -5, -11]))
    task = ("The tests in `test_app.py` fail: some settings come back as strings instead of numbers or booleans. "
            "Find the cause and fix it. true/yes/on and false/no/off (any letter case) are booleans, whole numbers "
            "(possibly negative) are ints, other numbers are floats. Do not change the tests.")
    no_float = _edit(_CFG_FIXED, "for kind in (int, float):", "for kind in (int,):")
    case_sensitive = _edit(_edit(_CFG_FIXED, 'if low in ("true", "yes", "on"):', 'if raw in ("true", "yes", "on", "True", "Yes"):'),
                           'if low in ("false", "no", "off"):', 'if raw in ("false", "no", "off", "False", "No"):')
    return _make(v, task=task,
                 buggy={"cfg.py": _CFG_BUGGY, "app.py": _CFG_APP},
                 fixed={"cfg.py": _CFG_FIXED, "app.py": _CFG_APP},
                 visible={"test_app.py": _CFG_VISIBLE}, hidden=_CFG_HIDDEN,
                 wrongs=[{"cfg.py": no_float}, {"cfg.py": case_sensitive}])


# ------------------------------------------------------------------ booking overlap (helper module)

_TIME_BUGGY = r'''"""Time helpers for the room booking code."""


def to_minutes(text):
    """"09:30" -> 570."""
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def overlaps(a_start, a_end, b_start, b_end):
    """True when two [start, end) ranges (minutes) share any time. A range that ends exactly when the
    other starts does not overlap it."""
    return a_start <= b_end and b_start <= a_end
'''

_TIME_FIXED = _TIME_BUGGY.replace("return a_start <= b_end and b_start <= a_end", "return a_start < b_end and b_start < a_end")

_BOOKING = r'''from timeutil import overlaps, to_minutes

OPEN, CLOSE = to_minutes("@OPEN@"), to_minutes("@CLOSE@")


def can_book(existing, start, end):
    """existing: list of ("HH:MM", "HH:MM") bookings. The new booking must end after it starts, lie inside
    opening hours and not overlap an existing booking."""
    s, e = to_minutes(start), to_minutes(end)
    if e <= s or s < OPEN or e > CLOSE:
        return False
    return not any(overlaps(s, e, to_minutes(a), to_minutes(b)) for a, b in existing)
'''

_BOOK_VISIBLE = r'''from booking import can_book


def test_starting_when_another_ends():
    assert can_book([("@H1@:00", "@H2@:00")], "@H2@:00", "@H3@:00") is True


def test_real_overlap_is_refused():
    assert can_book([("@H1@:00", "@H2@:00")], "@H1@:30", "@H3@:00") is False
'''

_BOOK_HIDDEN = r'''import random

from booking import can_book
from timeutil import overlaps, to_minutes


def test_back_to_back_both_sides():
    assert can_book([("10:00", "11:00")], "09:00", "10:00") is True
    assert can_book([("10:00", "11:00")], "11:00", "12:00") is True
    assert can_book([("10:00", "11:00"), ("12:00", "13:00")], "11:00", "12:00") is True


def test_overlaps_refused():
    existing = [("10:00", "12:00")]
    assert can_book(existing, "09:00", "10:01") is False
    assert can_book(existing, "11:59", "13:00") is False
    assert can_book(existing, "10:30", "11:00") is False
    assert can_book(existing, "09:30", "13:00") is False
    assert can_book(existing, "10:00", "12:00") is False


def test_hours_and_order():
    assert can_book([], "@OPEN@", "@CLOSE@") is True
    assert can_book([], "07:59", "09:00") is False
    assert can_book([], "16:00", "18:01") is False
    assert can_book([], "10:00", "10:00") is False
    assert can_book([], "11:00", "10:00") is False


def test_overlaps_function():
    assert overlaps(0, 10, 10, 20) is False
    assert overlaps(10, 20, 0, 10) is False
    assert overlaps(0, 10, 9, 20) is True
    assert overlaps(9, 20, 0, 10) is True
    assert overlaps(5, 6, 0, 10) is True
    assert overlaps(0, 10, 5, 6) is True
    assert to_minutes("09:30") == 570


def test_random():
    rng = random.Random(1234)
    for _ in range(400):
        a = rng.randint(0, 100)
        b = a + rng.randint(1, 30)
        c = rng.randint(0, 100)
        d = c + rng.randint(1, 30)
        expected = max(a, c) < min(b, d)
        assert overlaps(a, b, c, d) is expected
        assert overlaps(c, d, a, b) is expected
'''


def _booking(rng: random.Random):
    hours = pick(rng, [(8, 18), (9, 17)])
    h1 = pick(rng, [10, 11])
    v = dict(OPEN="%02d:00" % hours[0], CLOSE="%02d:00" % hours[1], H1=h1, H2=h1 + 1, H3=h1 + 2)
    task = ("The tests in `test_booking.py` fail. Find out why and fix the code so that a booking which starts "
            "exactly when another ends (or ends exactly when another starts) is accepted, while real overlaps are "
            "still refused. Do not change the tests.")
    one_sided = _edit(_TIME_FIXED, "a_start < b_end and b_start < a_end", "a_start < b_end and b_start <= a_end")
    either = _edit(_TIME_FIXED, "a_start < b_end and b_start < a_end", "a_start < b_end or b_start < a_end")
    return _make(v, task=task,
                 buggy={"timeutil.py": _TIME_BUGGY, "booking.py": _BOOKING},
                 fixed={"timeutil.py": _TIME_FIXED, "booking.py": _BOOKING},
                 visible={"test_booking.py": _BOOK_VISIBLE}, hidden=_BOOK_HIDDEN,
                 wrongs=[{"timeutil.py": one_sided}, {"timeutil.py": either}])


# ------------------------------------------------------------------ weighted grades (helper module)

_STATS_BUGGY = r'''"""Averages for the gradebook."""


def weighted_mean(pairs):
    """pairs: list of (score, weight). The weighted average. When nothing counts yet (no pairs, or the
    weights add up to 0) the mean is 0.0."""
    return sum(s * w for s, w in pairs) / len(pairs)
'''

_STATS_FIXED = r'''"""Averages for the gradebook."""


def weighted_mean(pairs):
    """pairs: list of (score, weight). The weighted average. When nothing counts yet (no pairs, or the
    weights add up to 0) the mean is 0.0."""
    total = sum(w for _, w in pairs)
    if total == 0:
        return 0.0
    return sum(s * w for s, w in pairs) / total
'''

_GRADES = r'''from stats import weighted_mean

WEIGHTS = {"homework": @HW@, "quiz": @QZ@, "exam": @EX@}


def final_grade(scores):
    """scores: dict category -> list of marks (0-100). A category counts with its weight in WEIGHTS; one with
    no marks yet is left out. Result is rounded to one decimal."""
    pairs = [(sum(marks) / len(marks), WEIGHTS[cat]) for cat, marks in scores.items() if marks]
    return round(weighted_mean(pairs), 1)
'''

_GRADES_VISIBLE = r'''from grades import final_grade


def test_all_categories():
    assert final_grade({"homework": [@A@, @B@], "quiz": [@C@], "exam": [@D@]}) == @EXPECT@


def test_missing_category_is_left_out():
    assert final_grade({"homework": [80], "exam": [60]}) == @EXPECT2@
'''

_GRADES_HIDDEN = r'''import random

from grades import WEIGHTS, final_grade
from stats import weighted_mean


def _ref_mean(pairs):
    total = sum(w for _, w in pairs)
    return sum(s * w for s, w in pairs) / total if total else 0.0


def test_weighted_mean():
    assert weighted_mean([(80, 1), (100, 3)]) == 95.0
    assert weighted_mean([(90, 2)]) == 90.0
    assert abs(weighted_mean([(50, 1), (70, 2), (90, 3)]) - 76.6666666666) < 1e-6


def test_nothing_counts_yet():
    assert weighted_mean([]) == 0.0
    assert weighted_mean([(50, 0), (70, 0)]) == 0.0


def test_final_grade_empty():
    assert final_grade({}) == 0.0
    assert final_grade({"homework": [], "exam": []}) == 0.0


def test_final_grade_weights():
    got = final_grade({"homework": [100], "quiz": [0], "exam": [50]})
    want = round((100 * WEIGHTS["homework"] + 0 * WEIGHTS["quiz"] + 50 * WEIGHTS["exam"])
                 / (WEIGHTS["homework"] + WEIGHTS["quiz"] + WEIGHTS["exam"]), 1)
    assert got == want


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        pairs = [(rng.randint(0, 100), rng.randint(0, 5)) for _ in range(rng.randint(0, 6))]
        assert abs(weighted_mean(pairs) - _ref_mean(pairs)) < 1e-9
        scores = {c: [rng.randint(0, 100) for _ in range(rng.randint(0, 3))] for c in WEIGHTS if rng.random() < 0.8}
        pairs = [(sum(m) / len(m), WEIGHTS[c]) for c, m in scores.items() if m]
        assert abs(final_grade(scores) - _ref_mean(pairs)) < 0.11
'''


def _grades(rng: random.Random):
    hw, qz, ex = pick(rng, [(20, 20, 60), (30, 10, 60), (25, 25, 50)])
    a, b, c, d = pick(rng, [(90, 70, 100, 80), (60, 80, 70, 90), (100, 50, 80, 60)])
    expected = round(((a + b) / 2 * hw + c * qz + d * ex) / (hw + qz + ex), 1)
    expected2 = round((80 * hw + 60 * ex) / (hw + ex), 1)
    v = dict(HW=hw, QZ=qz, EX=ex, A=a, B=b, C=c, D=d, EXPECT=expected, EXPECT2=expected2)
    task = ("The tests in `test_grades.py` fail: final grades come out wrong. Find the cause and fix it. "
            "A weighted mean divides by the total weight, and with nothing graded yet (no marks at all) "
            "the grade is 0.0, not an error. Do not change the tests.")
    no_guard = _edit(_STATS_FIXED, "    if total == 0:\n        return 0.0\n", "")
    empty_only = _edit(_STATS_FIXED, "if total == 0:", "if not pairs:")
    return _make(v, task=task,
                 buggy={"stats.py": _STATS_BUGGY, "grades.py": _GRADES},
                 fixed={"stats.py": _STATS_FIXED, "grades.py": _GRADES},
                 visible={"test_grades.py": _GRADES_VISIBLE}, hidden=_GRADES_HIDDEN,
                 wrongs=[{"stats.py": no_guard}, {"stats.py": empty_only}])


# ------------------------------------------------------------------ retry with backoff (two files)

_BACKOFF_BUGGY = r'''"""How long to wait between retries."""


def delay(attempt, base, cap):
    """Seconds to wait after failed attempt number `attempt` (1 for the first failure): base, then 2*base,
    4*base, ... and never more than cap."""
    return base * 2 ** attempt
'''

_BACKOFF_FIXED = r'''"""How long to wait between retries."""


def delay(attempt, base, cap):
    """Seconds to wait after failed attempt number `attempt` (1 for the first failure): base, then 2*base,
    4*base, ... and never more than cap."""
    return min(cap, base * 2 ** (attempt - 1))
'''

_CLIENT_BUGGY = r'''from backoff import delay


def fetch_with_retry(call, attempts, sleep, base=@BASE@, cap=@CAP@):
    """Call `call()` up to `attempts` times. A ConnectionError is retried after sleep(delay(...)); there is
    no sleep after the last attempt, whose ConnectionError is raised. Other errors are not retried."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except ConnectionError as exc:
            last = exc
            sleep(delay(attempt, base, cap))
    raise last
'''

_CLIENT_FIXED = _CLIENT_BUGGY.replace("            sleep(delay(attempt, base, cap))",
                                      "            if attempt < attempts:\n                sleep(delay(attempt, base, cap))")

_RETRY_VISIBLE = r'''import pytest

from client import fetch_with_retry


def _flaky(failures):
    state = {"n": 0}

    def call():
        state["n"] += 1
        if state["n"] <= failures:
            raise ConnectionError("down")
        return "ok"
    return call


def test_waits_double_between_tries():
    sleeps = []
    assert fetch_with_retry(_flaky(2), 4, sleeps.append) == "ok"
    assert sleeps == [@BASE@, @BASE@ * 2]


def test_no_sleep_after_the_last_attempt():
    sleeps = []
    with pytest.raises(ConnectionError):
        fetch_with_retry(_flaky(9), 3, sleeps.append)
    assert sleeps == [@BASE@, @BASE@ * 2]
'''

_RETRY_HIDDEN = r'''import random

import pytest

from backoff import delay
from client import fetch_with_retry


def _flaky(failures, exc=ConnectionError):
    state = {"n": 0}

    def call():
        state["n"] += 1
        if state["n"] <= failures:
            raise exc("down")
        return state["n"]
    return call


def _d(attempt, base, cap):
    return min(cap, base * 2 ** (attempt - 1))


def test_delay_values():
    assert delay(1, 1, 5) == 1
    assert delay(2, 1, 5) == 2
    assert delay(3, 1, 5) == 4
    assert delay(4, 1, 5) == 5
    assert delay(10, 1, 5) == 5
    assert delay(3, 3, 100) == 12


def test_cap_applies_to_the_waits():
    sleeps = []
    with pytest.raises(ConnectionError):
        fetch_with_retry(_flaky(99), 8, sleeps.append)
    assert sleeps == [_d(i, @BASE@, @CAP@) for i in range(1, 8)]
    assert max(sleeps) == @CAP@


def test_explicit_base_and_cap():
    sleeps = []
    with pytest.raises(ConnectionError):
        fetch_with_retry(_flaky(99), 6, sleeps.append, base=1, cap=5)
    assert sleeps == [1, 2, 4, 5, 5]


def test_single_attempt_and_success_result():
    sleeps = []
    with pytest.raises(ConnectionError):
        fetch_with_retry(_flaky(1), 1, sleeps.append)
    assert sleeps == []
    assert fetch_with_retry(_flaky(0), 3, sleeps.append) == 1
    assert fetch_with_retry(_flaky(2), 3, sleeps.append) == 3
    assert sleeps == [@BASE@, @BASE@ * 2]


def test_other_errors_are_not_retried():
    sleeps = []
    with pytest.raises(ValueError):
        fetch_with_retry(_flaky(5, ValueError), 4, sleeps.append)
    assert sleeps == []


def test_random():
    rng = random.Random(1234)
    for _ in range(200):
        attempts, failures = rng.randint(1, 8), rng.randint(0, 9)
        base, cap = rng.randint(1, 4), rng.randint(3, 30)
        sleeps = []
        call = _flaky(failures)
        if failures >= attempts:
            with pytest.raises(ConnectionError):
                fetch_with_retry(call, attempts, sleeps.append, base, cap)
        else:
            assert fetch_with_retry(call, attempts, sleeps.append, base, cap) == failures + 1
        assert sleeps == [_d(i, base, cap) for i in range(1, min(failures, attempts - 1) + 1)]
'''


def _retry(rng: random.Random):
    base, cap = pick(rng, [(1, 10), (2, 20), (3, 20)])
    v = dict(BASE=base, CAP=cap)
    task = ("The tests in `test_client.py` fail: the retry code waits the wrong amounts. Find out why and fix it. "
            "The waits double (base, 2*base, 4*base, ...) but never exceed the cap, and there is no wait "
            "after the final failed attempt. Do not change the tests.")
    no_cap = _edit(_BACKOFF_FIXED, "return min(cap, base * 2 ** (attempt - 1))", "return base * 2 ** (attempt - 1)")
    return _make(v, task=task,
                 buggy={"backoff.py": _BACKOFF_BUGGY, "client.py": _CLIENT_BUGGY},
                 fixed={"backoff.py": _BACKOFF_FIXED, "client.py": _CLIENT_FIXED},
                 visible={"test_client.py": _RETRY_VISIBLE}, hidden=_RETRY_HIDDEN,
                 wrongs=[{"backoff.py": no_cap, "client.py": _CLIENT_FIXED},
                         {"backoff.py": _BACKOFF_FIXED, "client.py": _CLIENT_BUGGY}])


# ------------------------------------------------------------------ cart bulk discount (helper module)

_PRICING_BUGGY = r'''"""Bulk pricing for cart lines."""

TIERS = ((@T2@, @P2@), (@T1@, @P1@))   # (minimum quantity, percent off), highest tier first


def line_cents(unit_cents, qty):
    """Price of one cart line in whole cents. The percent off of the highest tier whose minimum quantity is
    reached applies to every unit. The line total is rounded to the nearest cent, halves up."""
    pct = 0
    for min_qty, off in TIERS:
        if qty >= min_qty:
            pct = off
    total = unit_cents * qty * (100 - pct) / 100
    return int(total)
'''

_PRICING_FIXED = r'''"""Bulk pricing for cart lines."""

TIERS = ((@T2@, @P2@), (@T1@, @P1@))   # (minimum quantity, percent off), highest tier first


def line_cents(unit_cents, qty):
    """Price of one cart line in whole cents. The percent off of the highest tier whose minimum quantity is
    reached applies to every unit. The line total is rounded to the nearest cent, halves up."""
    pct = 0
    for min_qty, off in TIERS:
        if qty >= min_qty:
            pct = off
            break
    return (unit_cents * qty * (100 - pct) + 50) // 100
'''

_CART = r'''from pricing import line_cents


def cart_total(items, shipping_cents=@SHIP@):
    """items: list of (unit_cents, qty). Goods plus shipping; an empty cart costs 0."""
    if not items:
        return 0
    return sum(line_cents(unit, qty) for unit, qty in items) + shipping_cents
'''

_CART_VISIBLE = r'''from cart import cart_total


def test_top_tier_discount():
    assert cart_total([(1000, @T2@)]) == 1000 * @T2@ * (100 - @P2@) // 100 + @SHIP@


def test_no_discount_below_first_tier():
    assert cart_total([(250, @T1M@)]) == 250 * @T1M@ + @SHIP@
'''

_CART_HIDDEN = r'''import random

from cart import cart_total
from pricing import TIERS, line_cents


def _ref(unit, qty):
    pct = 0
    for m, off in sorted(TIERS):
        if qty >= m:
            pct = off
    return (unit * qty * (100 - pct) + 50) // 100


def test_tier_boundaries():
    (t2, p2), (t1, p1) = TIERS
    assert line_cents(100, t1 - 1) == 100 * (t1 - 1)
    assert line_cents(100, t1) == 100 * t1 * (100 - p1) // 100
    assert line_cents(100, t2 - 1) == 100 * (t2 - 1) * (100 - p1) // 100
    assert line_cents(100, t2) == 100 * t2 * (100 - p2) // 100


def test_halves_round_up_not_down():
    t1 = min(TIERS)[0]
    assert line_cents(7, t1 + 2) == _ref(7, t1 + 2)
    assert line_cents(15, t1 + 1) == _ref(15, t1 + 1)
    assert line_cents(1, 1) == 1


def test_cart():
    t1 = min(TIERS)[0]
    items = [(7, t1 + 2), (333, 3), (99, 1)]
    assert cart_total(items, 0) == sum(_ref(u, q) for u, q in items)
    assert cart_total(items) == sum(_ref(u, q) for u, q in items) + @SHIP@
    assert cart_total([]) == 0
    assert cart_total([], 500) == 0


def test_random():
    rng = random.Random(1234)
    for _ in range(400):
        unit, qty = rng.randint(1, 999), rng.randint(0, 130)
        assert line_cents(unit, qty) == _ref(unit, qty)
'''


def _cart(rng: random.Random):
    t1, p1 = pick(rng, [(10, 10), (12, 5), (20, 10)])
    t2, p2 = pick(rng, [(50, 20), (60, 15), (100, 25)])
    v = dict(T1=t1, P1=p1, T2=t2, P2=p2, T1M=t1 - 1, SHIP=pick(rng, [0, 499, 599]))
    task = ("The tests in `test_cart.py` fail: bulk discounts are wrong. Find the cause and fix it. "
            "Each cart line gets the discount of the highest quantity tier it reaches, and every line total is "
            "rounded to the nearest whole cent with halves rounding up. Do not change the tests.")
    truncating = _edit(_PRICING_FIXED, "return (unit_cents * qty * (100 - pct) + 50) // 100",
                       "return unit_cents * qty * (100 - pct) // 100")
    lowest_tier = _edit(_PRICING_FIXED, "            break\n", "")
    return _make(v, task=task,
                 buggy={"pricing.py": _PRICING_BUGGY, "cart.py": _CART},
                 fixed={"pricing.py": _PRICING_FIXED, "cart.py": _CART},
                 visible={"test_cart.py": _CART_VISIBLE}, hidden=_CART_HIDDEN,
                 wrongs=[{"pricing.py": truncating}, {"pricing.py": lowest_tier}])


# ------------------------------------------------------------------ page slugs (helper module)

_SLUGS_BUGGY = r'''"""URL slugs for page titles."""
import re

MAX_LEN = 40


def slugify(title):
    """Lower-case slug: each run of characters other than a-z and 0-9 becomes one "-", with no "-" at either
    end. At most 40 characters, and never ending in "-". A title with nothing usable gives "untitled"."""
    return re.sub(r"[^a-z0-9]", "-", title.lower())
'''

_SLUGS_FIXED = r'''"""URL slugs for page titles."""
import re

MAX_LEN = 40


def slugify(title):
    """Lower-case slug: each run of characters other than a-z and 0-9 becomes one "-", with no "-" at either
    end. At most 40 characters, and never ending in "-". A title with nothing usable gives "untitled"."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:MAX_LEN].rstrip("-")
    return slug or "untitled"
'''

_SITE = r'''from slugs import slugify


def page_urls(titles, prefix="/@SECTION@/"):
    """One URL per title, in order. A slug that was already used gets -2, -3, ... on its later pages."""
    seen = {}
    urls = []
    for title in titles:
        slug = slugify(title)
        seen[slug] = seen.get(slug, 0) + 1
        urls.append(prefix + (slug if seen[slug] == 1 else slug + "-" + str(seen[slug])))
    return urls
'''

_SITE_VISIBLE = r'''from pages import page_urls


def test_punctuation_and_repeats():
    assert page_urls(["@T@!", "@T@"]) == ["/@SECTION@/@S@", "/@SECTION@/@S@-2"]


def test_messy_separators():
    assert page_urls(["  Hello,   World -- today  "]) == ["/@SECTION@/hello-world-today"]
'''

_SITE_HIDDEN = r'''import random
import re

from pages import page_urls
from slugs import slugify


def _ref(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-")
    return s or "untitled"


def test_basic():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  --Q3 -- Report__final  ") == "q3-report-final"
    assert slugify("100% Pure") == "100-pure"


def test_nothing_usable():
    assert slugify("") == "untitled"
    assert slugify("!!! ???") == "untitled"
    assert slugify("café") == "caf"


def test_length_limit():
    assert slugify("a" * 60) == "a" * 40
    assert slugify("a" * 39 + " bbb") == "a" * 39
    assert slugify("a" * 38 + " bb ccc") == "a" * 38 + "-b"
    assert len(slugify("word " * 30)) <= 40 and not slugify("word " * 30).endswith("-")


def test_urls():
    got = page_urls(["News", "news!", "NEWS", "Other"], prefix="/p/")
    assert got == ["/p/news", "/p/news-2", "/p/news-3", "/p/other"]
    assert page_urls([]) == []
    assert page_urls(["???", "!!!"], prefix="/") == ["/untitled", "/untitled-2"]


def test_random():
    rng = random.Random(1234)
    alphabet = "abcXYZ019 -_.,!?é&"
    for _ in range(400):
        title = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 70)))
        assert slugify(title) == _ref(title)
'''


def _slugs(rng: random.Random):
    t = pick(rng, ["Release Notes", "Spring Menu", "Team Update"])
    v = dict(SECTION=pick(rng, ["blog", "news", "docs"]), T=t, S=t.lower().replace(" ", "-"))
    task = ("The tests in `test_pages.py` fail: page URLs come out with stray dashes. Find the cause and fix it. "
            "A slug has single dashes between words, no dash at either end, at most 40 characters and never "
            "ends in a dash; a title with nothing usable becomes \"untitled\". Do not change the tests.")
    no_limit = _edit(_SLUGS_FIXED, '    slug = slug[:MAX_LEN].rstrip("-")\n', "")
    cut_late = _edit(_SLUGS_FIXED, 'slug = slug[:MAX_LEN].rstrip("-")', "slug = slug[:MAX_LEN]")
    no_untitled = _edit(_SLUGS_FIXED, 'return slug or "untitled"', "return slug")
    return _make(v, task=task,
                 buggy={"slugs.py": _SLUGS_BUGGY, "pages.py": _SITE},
                 fixed={"slugs.py": _SLUGS_FIXED, "pages.py": _SITE},
                 visible={"test_pages.py": _SITE_VISIBLE}, hidden=_SITE_HIDDEN,
                 wrongs=[{"slugs.py": no_limit}, {"slugs.py": cut_late}, {"slugs.py": no_untitled}])


# ------------------------------------------------------------------ latency percentiles (helper module)

_METRICS_BUGGY = r'''"""Statistics helpers for the latency report."""


def percentile(values, p):
    """Nearest-rank percentile of a list of numbers in any order, p an integer 0-100: sort the values, take
    rank = ceil(p / 100 * n) (at least 1) and return the value at that rank (1 is the smallest). The list
    itself is not changed. No values gives None."""
    if not values:
        return None
    idx = p * len(values) // 100
    return values[min(idx, len(values) - 1)]
'''

_METRICS_FIXED = r'''"""Statistics helpers for the latency report."""


def percentile(values, p):
    """Nearest-rank percentile of a list of numbers in any order, p an integer 0-100: sort the values, take
    rank = ceil(p / 100 * n) (at least 1) and return the value at that rank (1 is the smallest). The list
    itself is not changed. No values gives None."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-p * len(ordered) // 100))
    return ordered[rank - 1]
'''

_LAT_REPORT = r'''from metrics import percentile


def latency_summary(samples_ms):
    """Headline numbers for a batch of request times."""
    return {"count": len(samples_ms), "p50": percentile(samples_ms, 50), "p90": percentile(samples_ms, 90),
            "max": max(samples_ms) if samples_ms else None}
'''

_LAT_VISIBLE = r'''from report import latency_summary


def test_summary_of_unsorted_samples():
    got = latency_summary([@S1@, @S2@, @S3@, @S4@, @S5@])
    assert got == {"count": 5, "p50": @P50@, "p90": @P90@, "max": @P90@}
'''

_LAT_HIDDEN = r'''import random

from metrics import percentile
from report import latency_summary


def _ref(values, p):
    if not values:
        return None
    s = sorted(values)
    rank = max(1, -(-p * len(s) // 100))
    return s[rank - 1]


def test_even_count():
    data = [40, 10, 30, 20, 60, 50, 90, 80, 70, 100]
    assert percentile(data, 50) == 50
    assert percentile(data, 90) == 90
    assert percentile(data, 95) == 100
    assert percentile(data, 10) == 10
    assert percentile(data, 11) == 20


def test_edges():
    assert percentile([5, 1, 3], 0) == 1
    assert percentile([5, 1, 3], 100) == 5
    assert percentile([7], 50) == 7
    assert percentile([7], 0) == 7
    assert percentile([], 50) is None
    assert percentile([2.5, 1.5, 2.5, 9.0], 75) == 2.5


def test_input_untouched():
    data = [3, 1, 2]
    percentile(data, 50)
    assert data == [3, 1, 2]


def test_summary():
    assert latency_summary([]) == {"count": 0, "p50": None, "p90": None, "max": None}
    got = latency_summary([12, 10, 18, 11])
    assert got == {"count": 4, "p50": 11, "p90": 18, "max": 18}


def test_random():
    rng = random.Random(1234)
    for _ in range(400):
        values = [rng.randint(1, 500) for _ in range(rng.randint(0, 25))]
        p = rng.randint(0, 100)
        assert percentile(values, p) == _ref(values, p)
'''


def _percentile(rng: random.Random):
    s = sorted(rng.sample(range(20, 400), 5))
    order = [s[2], s[4], s[0], s[3], s[1]]
    v = dict(S1=order[0], S2=order[1], S3=order[2], S4=order[3], S5=order[4], P50=s[2], P90=s[4])
    task = ("The tests in `test_report.py` fail: the latency summary shows the wrong percentiles. Find the cause and "
            "fix it. Percentiles use the nearest-rank method on the sorted samples; samples arrive in any order. "
            "Do not change the tests.")
    floor = _edit(_METRICS_FIXED, "rank = max(1, -(-p * len(ordered) // 100))", "rank = min(len(ordered), p * len(ordered) // 100 + 1)")
    no_sort = _edit(_METRICS_FIXED, "ordered = sorted(values)", "ordered = values")
    return _make(v, task=task,
                 buggy={"metrics.py": _METRICS_BUGGY, "report.py": _LAT_REPORT},
                 fixed={"metrics.py": _METRICS_FIXED, "report.py": _LAT_REPORT},
                 visible={"test_report.py": _LAT_VISIBLE}, hidden=_LAT_HIDDEN,
                 wrongs=[{"metrics.py": floor}, {"metrics.py": no_sort}])


# ------------------------------------------------------------------ CSV quoting (helper module)

_CSVUTIL_BUGGY = r'''"""Quoting for CSV output."""


def quote_field(value):
    """One CSV field. None is an empty field; anything else is str(value). A field containing a comma, a double
    quote or a newline is wrapped in double quotes, with every inner double quote doubled."""
    text = str(value)
    if "," in text:
        return '"' + text + '"'
    return text
'''

_CSVUTIL_FIXED = r'''"""Quoting for CSV output."""


def quote_field(value):
    """One CSV field. None is an empty field; anything else is str(value). A field containing a comma, a double
    quote or a newline is wrapped in double quotes, with every inner double quote doubled."""
    if value is None:
        return ""
    text = str(value)
    if "," in text or '"' in text or "\n" in text:
        return '"' + text.replace('"', '""') + '"'
    return text
'''

_EXPORT = r'''from csvutil import quote_field


def render_csv(header, rows):
    """The header line, then one line per row, each line ending in a newline."""
    lines = [",".join(quote_field(c) for c in header)]
    lines += [",".join(quote_field(c) for c in row) for row in rows]
    return "\n".join(lines) + "\n"
'''

_CSV_VISIBLE = r'''from export import render_csv


def test_inner_quotes_are_doubled():
    assert render_csv(["@H1@", "@H2@"], [["@W@", 'said "hi"']]) == '@H1@,@H2@\n@W@,"said ""hi"""\n'


def test_comma_is_quoted():
    assert render_csv(["@H1@"], [["a, b"]]) == '@H1@\n"a, b"\n'
'''

_CSV_HIDDEN = r'''import random

from csvutil import quote_field
from export import render_csv


def _ref(value):
    if value is None:
        return ""
    t = str(value)
    if "," in t or '"' in t or "\n" in t:
        return '"' + t.replace('"', '""') + '"'
    return t


def test_plain_fields():
    assert quote_field("abc") == "abc"
    assert quote_field(12) == "12"
    assert quote_field(0) == "0"
    assert quote_field(2.5) == "2.5"
    assert quote_field("") == ""


def test_none_is_empty():
    assert quote_field(None) == ""
    assert render_csv(["a", "b"], [[None, 1]]) == "a,b\n,1\n"


def test_newline_and_quote_and_comma():
    assert quote_field("two\nlines") == '"two\nlines"'
    assert quote_field('"') == '""""'
    assert quote_field('a,"b"') == '"a,""b"""'


def test_render():
    assert render_csv(["x"], []) == "x\n"
    assert render_csv(["id", "note"], [[1, "ok"], [2, "a\nb"], [3, None]]) == 'id,note\n1,ok\n2,"a\nb"\n3,\n'


def test_random():
    rng = random.Random(1234)
    alphabet = ['a', 'b', ' ', ',', '"', '\n']
    for _ in range(400):
        r = rng.random()
        if r < 0.1:
            value = None
        elif r < 0.25:
            value = rng.randint(-5, 5)
        else:
            value = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))
        assert quote_field(value) == _ref(value)
'''


def _csv(rng: random.Random):
    h = pick(rng, [("name", "note"), ("item", "comment"), ("user", "remark")])
    v = dict(H1=h[0], H2=h[1], W=pick(rng, ["Ana", "Bo", "Kit"]))
    task = ("The tests in `test_export.py` fail: the CSV output is malformed for some values. Find the cause and fix "
            "it. A field with a comma, a double quote or a newline is wrapped in double quotes with inner quotes "
            "doubled, and None becomes an empty field. Do not change the tests.")
    no_double = _edit(_CSVUTIL_FIXED, """text.replace('"', '""')""", "text")
    comma_quote_only = _edit(_CSVUTIL_FIXED, ' or "\\n" in text', "")
    falsy_empty = _edit(_CSVUTIL_FIXED, "if value is None:", "if not value:")
    comma_quote_only = _edit(comma_quote_only, 'if value is None:\n        return ""\n    ', "")
    return _make(v, task=task,
                 buggy={"csvutil.py": _CSVUTIL_BUGGY, "export.py": _EXPORT},
                 fixed={"csvutil.py": _CSVUTIL_FIXED, "export.py": _EXPORT},
                 visible={"test_export.py": _CSV_VISIBLE}, hidden=_CSV_HIDDEN,
                 wrongs=[{"csvutil.py": comma_quote_only}, {"csvutil.py": no_double}, {"csvutil.py": falsy_empty}])


# ------------------------------------------------------------------ payroll with overnight shifts (two files)

_SHIFTS_BUGGY = r'''"""Shift lengths for the payroll code."""


def shift_minutes(start, end):
    """Minutes worked in a shift given as "HH:MM" strings. An end earlier than the start means the shift runs
    past midnight into the next day; equal times mean an empty shift (0 minutes)."""
    sh, sm = start.split(":")
    eh, em = end.split(":")
    return (int(eh) * 60 + int(em)) - (int(sh) * 60 + int(sm))
'''

_SHIFTS_FIXED = r'''"""Shift lengths for the payroll code."""


def shift_minutes(start, end):
    """Minutes worked in a shift given as "HH:MM" strings. An end earlier than the start means the shift runs
    past midnight into the next day; equal times mean an empty shift (0 minutes)."""
    sh, sm = start.split(":")
    eh, em = end.split(":")
    s, e = int(sh) * 60 + int(sm), int(eh) * 60 + int(em)
    if e < s:
        e += 24 * 60
    return e - s
'''

_PAYROLL_BUGGY = r'''from shifts import shift_minutes

OVERTIME_AFTER = 40 * 60   # minutes per week


def week_pay_cents(shifts, rate_cents):
    """shifts: list of (start, end) for one week; rate_cents is the hourly rate. Only the minutes beyond
    40 hours are paid at 1.5 times the rate. The pay is rounded down to whole cents."""
    total = sum(shift_minutes(s, e) for s, e in shifts)
    if total > OVERTIME_AFTER:
        return total * rate_cents * 3 // 120
    return total * rate_cents // 60
'''

_PAYROLL_FIXED = r'''from shifts import shift_minutes

OVERTIME_AFTER = 40 * 60   # minutes per week


def week_pay_cents(shifts, rate_cents):
    """shifts: list of (start, end) for one week; rate_cents is the hourly rate. Only the minutes beyond
    40 hours are paid at 1.5 times the rate. The pay is rounded down to whole cents."""
    total = sum(shift_minutes(s, e) for s, e in shifts)
    regular = min(total, OVERTIME_AFTER)
    extra = total - regular
    return (regular * rate_cents * 2 + extra * rate_cents * 3) // 120
'''

_PAY_VISIBLE = r'''from payroll import week_pay_cents


def test_night_shift_runs_past_midnight():
    assert week_pay_cents([("22:00", "06:00")], @RATE@) == 8 * @RATE@


def test_overtime_only_on_the_extra_hours():
    shifts = [("08:00", "17:00")] * 5
    assert week_pay_cents(shifts, @RATE@) == 40 * @RATE@ + 5 * @RATE@ * 3 // 2
'''

_PAY_HIDDEN = r'''import random

from payroll import week_pay_cents
from shifts import shift_minutes


def _mins(s, e):
    a = int(s[:2]) * 60 + int(s[3:])
    b = int(e[:2]) * 60 + int(e[3:])
    return b - a if b >= a else b + 1440 - a


def _ref(shifts, rate):
    total = sum(_mins(s, e) for s, e in shifts)
    reg = min(total, 2400)
    return (reg * rate * 2 + (total - reg) * rate * 3) // 120


def test_shift_minutes():
    assert shift_minutes("09:00", "17:30") == 510
    assert shift_minutes("22:00", "06:00") == 480
    assert shift_minutes("23:59", "00:00") == 1
    assert shift_minutes("09:00", "09:00") == 0
    assert shift_minutes("00:00", "23:59") == 1439


def test_exactly_forty_hours_has_no_overtime():
    shifts = [("09:00", "17:00")] * 5
    assert week_pay_cents(shifts, @RATE@) == 40 * @RATE@


def test_one_minute_over():
    shifts = [("09:00", "17:00")] * 4 + [("09:00", "17:01")]
    assert week_pay_cents(shifts, 6000) == 40 * 6000 + 150


def test_rounds_down_to_whole_cents():
    assert week_pay_cents([("10:00", "10:01")], 1999) == 33
    assert week_pay_cents([("10:00", "10:07")], 1999) == 233


def test_overtime_with_night_shifts():
    shifts = [("20:00", "06:00")] * 5
    assert week_pay_cents(shifts, @RATE@) == _ref(shifts, @RATE@)
    assert week_pay_cents([], @RATE@) == 0


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        shifts = []
        for _ in range(rng.randint(0, 7)):
            shifts.append(("%02d:%02d" % (rng.randint(0, 23), rng.randint(0, 59)),
                           "%02d:%02d" % (rng.randint(0, 23), rng.randint(0, 59))))
        rate = rng.randint(900, 4000)
        assert week_pay_cents(shifts, rate) == _ref(shifts, rate)
'''


def _payroll(rng: random.Random):
    v = dict(RATE=pick(rng, [1500, 1800, 2100, 2350]))
    task = ("The tests in `test_payroll.py` fail: weekly pay is wrong for night shifts and for overtime. Find out why "
            "and fix the code. A shift whose end is earlier than its start runs past midnight, and only the minutes "
            "beyond 40 hours in the week are paid at 1.5 times the rate. Do not change the tests.")
    return _make(v, task=task,
                 buggy={"shifts.py": _SHIFTS_BUGGY, "payroll.py": _PAYROLL_BUGGY},
                 fixed={"shifts.py": _SHIFTS_FIXED, "payroll.py": _PAYROLL_FIXED},
                 visible={"test_payroll.py": _PAY_VISIBLE}, hidden=_PAY_HIDDEN,
                 wrongs=[{"shifts.py": _SHIFTS_FIXED, "payroll.py": _PAYROLL_BUGGY},
                         {"shifts.py": _SHIFTS_BUGGY, "payroll.py": _PAYROLL_FIXED}])


TEMPLATES = [
    probe_template("agent", "agent_restock_plan", _restock),
    probe_template("agent", "agent_invoice_rounding", _invoice),
    probe_template("agent", "agent_log_severity", _logs),
    probe_template("agent", "agent_settings_values", _config),
    probe_template("agent", "agent_room_booking", _booking),
    probe_template("agent", "agent_weighted_grades", _grades),
    probe_template("agent", "agent_retry_backoff", _retry),
    probe_template("agent", "agent_cart_bulk_discount", _cart),
    probe_template("agent", "agent_page_slugs", _slugs),
    probe_template("agent", "agent_latency_percentiles", _percentile),
    probe_template("agent", "agent_csv_quoting", _csv),
    probe_template("agent", "agent_night_shift_pay", _payroll),
]
