"""Rung 7: find and fix two bugs in a 60 to 100 line file.

Each case gives one realistic module (several functions, sometimes a small
class) with two independent bugs in different functions, described only by
their symptoms. Hidden tests hold the correct module as a trusted reference
(`REF`, built from the module source kept in the test file), a few hand-picked
checks for the two bugs, and a fixed-seed differential test over the whole
module, so fixing one bug, or breaking another function, fails.

A case is written as the CORRECT module plus two bugs; each bug is a list of
(bad, good) text replacements. The buggy file is the correct one with both
bugs put in; wrong answers fix only one of the two.
"""
from __future__ import annotations

import random

from edat.sidekick.ceiling.rungs import answer_files, fill, probe_instance, probe_template, show_file
from edat.sidekick.gen.core import pick

_HEAD = '''import copy
import random

import pytest

import @MOD@ as M

'''

_TAIL = '''
REF = {}
exec(compile(SRC, "reference", "exec"), REF)


def _call(fn, args):
    args = copy.deepcopy(args)
    try:
        return ("ok", fn(*args), args)
    except Exception as exc:
        return ("error", type(exc).__name__)


def same(name, *args):
    got = _call(getattr(M, name), args)
    want = _call(REF[name], args)
    assert got == want, (name, args, got, want)


'''


def _case(rng, v, *, mod, src, bugs, intro, symptoms, tests):
    fixed = fill(src, **v).strip("\n") + "\n"
    pairs = [[(fill(b, **v), fill(g, **v)) for b, g in bug] for bug in bugs]
    for bug in pairs:
        for bad, good in bug:
            assert fixed.count(good) == 1, good
            assert bad != good

    def with_bug(text, bug):
        for bad, good in bug:
            text = text.replace(good, bad)
        return text

    buggy = with_bug(with_bug(fixed, pairs[0]), pairs[1])
    only_first = with_bug(fixed, pairs[1])      # bug 0 fixed, bug 1 still there
    only_second = with_bug(fixed, pairs[0])     # bug 1 fixed, bug 0 still there
    for text in (fixed, buggy):
        assert 60 <= len(text.splitlines()) <= 100, len(text.splitlines())
    assert len({fixed, buggy, only_first, only_second}) == 4
    target = mod + ".py"
    prompt = (intro + "\n\n" + show_file(target, buggy) + "\nTwo things go wrong:\n\n"
              + "\n".join(f"{i}. {s}" for i, s in enumerate(symptoms, 1))
              + f"\n\nFind and fix both bugs in `{target}`. Do not change anything else: every other "
              "function must keep behaving exactly as its docstring says, with the same names and signatures.")
    hidden = fill(_HEAD, MOD=mod) + "SRC = " + repr(fixed) + "\n" + _TAIL + fill(tests, **v)
    return probe_instance(
        prompt=prompt, targets=[target], files={target: buggy}, hidden_tests=hidden,
        references=[answer_files({target: fixed}), answer_files({target: fixed}, style="label")],
        wrong=[answer_files({target: only_first}), answer_files({target: only_second})])


# ------------------------------------------------------------------ 1. hosting invoices

_INVOICING = '''"""Invoicing helpers for a small hosting company. Money is in whole cents."""

PERIOD_DAYS = @DAYS@
TAX_PERCENT = @TAX@


def periods_billed(days):
    """Billing periods charged for a stay of `days` days.

    Any period that has been started is charged in full: exactly PERIOD_DAYS
    days is one period, PERIOD_DAYS + 1 days is two. Zero or fewer days is zero.
    """
    if days <= 0:
        return 0
    return -(-days // PERIOD_DAYS)


def line_total(unit_cents, qty, discount_pct=0):
    """Cents for one invoice line after a percentage discount, rounded to a cent."""
    gross = unit_cents * qty
    return round(gross * (100 - discount_pct) / 100)


def add_tax(cents):
    """Cents including TAX_PERCENT percent tax (the tax itself is rounded to a cent)."""
    return cents + round(cents * TAX_PERCENT / 100)


class Invoice:
    """A customer's invoice: a list of (description, cents) lines."""

    def __init__(self, customer):
        self.customer = customer
        self.lines = []

    def add(self, description, unit_cents, qty=1, discount_pct=0):
        """Append a line and return its total in cents."""
        cents = line_total(unit_cents, qty, discount_pct)
        self.lines.append((description, cents))
        return cents

    def add_hosting(self, description, cents_per_period, days):
        """Append a hosting line charged once per billing period for `days` days."""
        return self.add(description, cents_per_period, periods_billed(days))

    def subtotal(self):
        return sum(cents for _, cents in self.lines)

    def total(self):
        return add_tax(self.subtotal())


def customer_summary(customers, invoices):
    """Summarise invoice totals per customer.

    Returns {customer: {"count": n, "total": cents, "average": cents}} with one
    entry for every name in `customers`, in that order. The average is the
    total divided by the count, rounded to whole cents. A customer with no
    invoices has count 0, total 0 and average 0.
    """
    summary = {}
    for name in customers:
        mine = [inv.total() for inv in invoices if inv.customer == name]
        total = sum(mine)
        average = round(total / len(mine)) if mine else 0
        summary[name] = {"count": len(mine), "total": total, "average": average}
    return summary


def biggest_invoice(invoices):
    """The invoice with the highest total (the first one on a tie), or None if there are none."""
    best = None
    for inv in invoices:
        if best is None or inv.total() > best.total():
            best = inv
    return best
'''

_INVOICING_TESTS = '''
def _make(ns, spec):
    out = []
    for customer, lines in spec:
        inv = ns["Invoice"](customer)
        for line in lines:
            if line[0] == "host":
                inv.add_hosting("host", line[1], line[2])
            else:
                inv.add(*line)
        out.append(inv)
    return out


def test_period_boundaries():
    d = @DAYS@
    assert [M.periods_billed(x) for x in (-5, 0, 1, d - 1, d, d + 1, 2 * d, 2 * d + 1)] == [0, 0, 1, 1, 1, 2, 2, 3]


def test_customer_without_invoices():
    empty = {"count": 0, "total": 0, "average": 0}
    assert M.customer_summary(["amy", "bo"], []) == {"amy": empty, "bo": empty}
    inv = M.Invoice("amy")
    inv.add("setup", 1000)
    out = M.customer_summary(["bo", "amy"], [inv])
    assert list(out) == ["bo", "amy"] and out["bo"] == empty and out["amy"]["count"] == 1


def test_hosting_line_total():
    inv = M.Invoice("amy")
    assert inv.add_hosting("plan", 700, @DAYS@) == 700
    assert inv.add_hosting("plan", 700, @DAYS@ + 1) == 1400


def test_money_helpers():
    for args in [(999, 3, 15), (250, 1, 0), (1, 1, 50), (0, 5, 10), (1234, 7, 33)]:
        same("line_total", *args)
    for c in (0, 1, 99, 1001, 123456):
        same("add_tax", c)


def test_summary_and_biggest_random():
    rng = random.Random(1234)
    names = ["amy", "bo", "cy", "di"]
    for _ in range(150):
        spec = []
        for _ in range(rng.randint(0, 6)):
            lines = []
            for _ in range(rng.randint(0, 3)):
                if rng.random() < 0.4:
                    lines.append(("host", rng.randint(100, 3000), rng.randint(-2, 100)))
                else:
                    lines.append(("item", rng.randint(0, 5000), rng.randint(1, 5), rng.choice([0, 0, 10, 25, 33])))
            spec.append((rng.choice(names), lines))
        custs = rng.sample(names, rng.randint(1, 4))
        got = M.customer_summary(custs, _make(vars(M), spec))
        want = REF["customer_summary"](custs, _make(REF, spec))
        assert got == want and list(got) == list(want), spec
        g = M.biggest_invoice(_make(vars(M), spec))
        w = REF["biggest_invoice"](_make(REF, spec))
        assert (g is None) == (w is None)
        if g is not None:
            assert (g.customer, g.total(), g.lines) == (w.customer, w.total(), w.lines)
'''


def _invoicing(rng):
    v = dict(DAYS=pick(rng, [28, 30, 31]), TAX=pick(rng, [7, 8, 10, 19]))
    return _case(rng, v, mod="invoicing", src=_INVOICING, tests=_INVOICING_TESTS,
                 bugs=[[("    return days // PERIOD_DAYS + 1", "    return -(-days // PERIOD_DAYS)")],
                       [("        average = round(total / len(mine))", "        average = round(total / len(mine)) if mine else 0")]],
                 intro="`invoicing.py` builds invoices for a small hosting company.",
                 symptoms=["A hosting stay of exactly one billing period (for example exactly "
                           f"{v['DAYS']} days) is charged as two periods.",
                           "Asking for a summary that includes a customer who has no invoices crashes instead of "
                           "listing that customer with zeros."])


# ------------------------------------------------------------------ 2. room bookings

_SCHEDULE = '''"""Room booking helpers. Times are minutes since midnight.

A slot is a (start, end) tuple and covers the half-open range start..end.
"""


def overlaps(a, b):
    """True if two slots share any time. Back-to-back slots do not overlap."""
    return a[0] < b[1] and b[0] < a[1]


def add_booking(slot, bookings=None):
    """Return a new sorted list of bookings with `slot` added.

    The given list is not changed. With no list, start from an empty one.
    Raises ValueError if the slot is empty (end <= start) or overlaps an
    existing booking.
    """
    existing = [] if bookings is None else list(bookings)
    if slot[1] <= slot[0]:
        raise ValueError("empty slot")
    for other in existing:
        if overlaps(slot, other):
            raise ValueError("slot is taken")
    existing.append(slot)
    existing.sort()
    return existing


def free_slots(bookings, day_start, day_end, min_len=@MIN@):
    """Gaps inside the day as (start, end) slots at least `min_len` minutes long.

    Bookings may come in any order and may stick out of the day; they are
    clipped to it. Gaps are returned in time order.
    """
    gaps = []
    cursor = day_start
    for start, end in sorted(bookings):
        start, end = max(start, day_start), min(end, day_end)
        if end <= start:
            continue
        if start - cursor >= min_len:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if day_end - cursor >= min_len:
        gaps.append((cursor, day_end))
    return gaps


def busy_minutes(bookings):
    """Total minutes covered by the bookings, counting overlapped time once."""
    total, cursor = 0, None
    for start, end in sorted(bookings):
        if cursor is not None:
            start = max(start, cursor)
        if end > start:
            total += end - start
            cursor = end
    return total


def format_slot(slot):
    """Text such as '09:00-10:30' for a slot."""
    return "%02d:%02d-%02d:%02d" % (slot[0] // 60, slot[0] % 60, slot[1] // 60, slot[1] % 60)


def longest_gap(bookings, day_start, day_end):
    """The longest free slot of the day (the earliest on a tie), or None if the day is full."""
    best = None
    for gap in free_slots(bookings, day_start, day_end, 1):
        if best is None or gap[1] - gap[0] > best[1] - best[0]:
            best = gap
    return best
'''

_SCHEDULE_TESTS = '''
def test_back_to_back_is_allowed():
    assert M.overlaps((540, 600), (600, 660)) is False
    assert M.overlaps((540, 601), (600, 660)) is True
    assert M.add_booking((600, 660), [(540, 600)]) == [(540, 600), (600, 660)]
    assert M.add_booking((480, 540), [(540, 600)]) == [(480, 540), (540, 600)]


def test_no_state_kept_between_calls():
    assert M.add_booking((60, 120)) == [(60, 120)]
    assert M.add_booking((180, 240)) == [(180, 240)]
    given = [(60, 120)]
    out = M.add_booking((120, 180), given)
    assert given == [(60, 120)] and out == [(60, 120), (120, 180)]


def test_rejections():
    for slot, existing in [((100, 100), []), ((120, 90), []), ((100, 200), [(150, 160)]), ((100, 200), [(50, 101)])]:
        with pytest.raises(ValueError):
            M.add_booking(slot, existing)


def test_day_views():
    day = (540, 1020)
    b = [(600, 660), (630, 700), (900, 960)]
    for name in ("free_slots", "longest_gap"):
        same(name, b, *day)
    same("free_slots", [], *day)
    same("free_slots", [(0, 2000)], *day)
    same("free_slots", [(600, 660)], 540, 1020, 60)
    same("busy_minutes", b)
    same("format_slot", (545, 1035))


def test_random():
    rng = random.Random(1234)
    for _ in range(250):
        b = []
        for _ in range(rng.randint(0, 6)):
            s = rng.randint(400, 1100)
            b.append((s, s + rng.randint(0, 150)))
        rng.shuffle(b)
        same("free_slots", b, 540, 1020)
        same("free_slots", b, 480, 1000, rng.randint(1, 90))
        same("longest_gap", b, 540, 1020)
        same("busy_minutes", b)
        slot = (rng.randint(400, 1100), rng.randint(400, 1100))
        same("overlaps", slot, rng.choice(b) if b else (0, 1))
        same("add_booking", slot, b)
        same("format_slot", slot)
'''


def _schedule(rng):
    v = dict(MIN=pick(rng, [15, 20, 30]))
    return _case(rng, v, mod="schedule", src=_SCHEDULE, tests=_SCHEDULE_TESTS,
                 bugs=[[("    return a[0] <= b[1] and b[0] <= a[1]", "    return a[0] < b[1] and b[0] < a[1]")],
                       [("def add_booking(slot, bookings=[]):", "def add_booking(slot, bookings=None):"),
                        ("    existing = bookings\n", "    existing = [] if bookings is None else list(bookings)\n")]],
                 intro="`schedule.py` holds the booking helpers for a meeting-room calendar.",
                 symptoms=["A meeting that starts at the exact minute the previous one ends (10:00 right after a "
                           "meeting that finishes at 10:00) is refused as a conflict.",
                           "Calling `add_booking` without a list should start from an empty calendar every time, but "
                           "bookings from earlier calls keep turning up in later ones, and a list passed in gets modified."])


# ------------------------------------------------------------------ 3. grade book

_GRADES = '''"""Grade book helpers for a short course."""

CUTOFFS = [(@A@, "A"), (@B@, "B"), (@C@, "C"), (@D@, "D")]


def letter(score):
    """Letter for a percentage score.

    A score equal to a cutoff earns that cutoff's letter. Below the lowest
    cutoff the letter is "F".
    """
    for cutoff, grade in CUTOFFS:
        if score >= cutoff:
            return grade
    return "F"


def weighted_average(scores, weights):
    """Weighted mean of the scores.

    Both arguments are dicts keyed by component name. A component missing from
    `scores` counts as 0. Weights need not add up to 1. If the weights add up
    to 0 the result is 0.0.
    """
    total_weight = sum(weights.values())
    if total_weight == 0:
        return 0.0
    got = sum(scores.get(name, 0) * w for name, w in weights.items())
    return got / total_weight


def drop_lowest(values, count=1):
    """Copy of `values` without its `count` lowest entries, keeping the order of the rest.

    Only as many entries as exist are dropped, and a count of 0 or less drops
    nothing. When several entries are equally low the earliest are dropped first.
    """
    if count <= 0:
        return list(values)
    ranked = sorted(range(len(values)), key=lambda i: values[i])
    dropped = set(ranked[:count])
    return [v for i, v in enumerate(values) if i not in dropped]


def ranking(finals):
    """Student names, best final score first. Equal scores are ordered by name, A to Z."""
    return sorted(finals, key=lambda name: (-finals[name], name))


class Gradebook:
    """Scores per student, combined with fixed component weights."""

    def __init__(self, weights):
        self.weights = dict(weights)
        self.scores = {}

    def record(self, student, component, score):
        self.scores.setdefault(student, {})[component] = score

    def final(self, student):
        """Weighted average rounded to one decimal; 0.0 for an unknown student."""
        return round(weighted_average(self.scores.get(student, {}), self.weights), 1)

    def report(self):
        """[(student, final, letter)] for every student with a score, best first."""
        finals = {s: self.final(s) for s in self.scores}
        return [(s, finals[s], letter(finals[s])) for s in ranking(finals)]
'''

_GRADES_TESTS = '''
def _book(ns, rows):
    book = ns["Gradebook"]({"quiz": 1, "exam": 3, "lab": 2})
    for student, comp, score in rows:
        book.record(student, comp, score)
    return book


def test_letters_on_the_cutoffs():
    cuts = [@A@, @B@, @C@, @D@]
    assert [M.letter(c) for c in cuts] == ["A", "B", "C", "D"]
    assert [M.letter(c - 0.5) for c in cuts] == ["B", "C", "D", "F"]
    assert M.letter(100) == "A" and M.letter(0) == "F"


def test_ranking_best_first():
    assert M.ranking({"cy": 70, "amy": 91.5, "bo": 70, "di": 88}) == ["amy", "di", "bo", "cy"]
    assert M.ranking({}) == []


def test_weighted_and_dropped():
    for args in [({"quiz": 80, "exam": 90}, {"quiz": 1, "exam": 3, "lab": 2}), ({}, {"a": 0}), ({"a": 5}, {"a": 2})]:
        same("weighted_average", *args)
    for args in [([5, 3, 9, 3], 1), ([5, 3, 9, 3], 2), ([4], 3), ([], 1), ([1, 2], 0), ([1, 2], -1)]:
        same("drop_lowest", *args)


def test_report():
    rows = [("amy", "quiz", 90), ("amy", "exam", @A@), ("bo", "exam", @B@), ("cy", "lab", 100), ("bo", "lab", 70)]
    got = _book(vars(M), rows).report()
    assert got == _book(REF, rows).report()
    assert [name for name, _, _ in got][-1] == "cy"


def test_random():
    rng = random.Random(1234)
    names = ["amy", "bo", "cy", "di", "ed"]
    for _ in range(200):
        rows = [(rng.choice(names), rng.choice(["quiz", "exam", "lab"]), rng.choice([rng.randint(0, 100), @A@, @B@, @C@, @D@]))
                for _ in range(rng.randint(0, 12))]
        assert _book(vars(M), rows).report() == _book(REF, rows).report(), rows
        finals = {n: rng.choice([50, 60, 75, 90]) for n in rng.sample(names, rng.randint(0, 5))}
        same("ranking", finals)
        vals = [rng.randint(0, 5) for _ in range(rng.randint(0, 7))]
        same("drop_lowest", vals, rng.randint(-1, 4))
        same("letter", rng.choice([rng.uniform(0, 100), rng.randint(0, 100)]))
'''


def _grades(rng):
    a = pick(rng, [90, 92, 93])
    v = dict(A=a, B=a - 10, C=a - 20, D=a - 30 if a == 90 else a - 32)
    return _case(rng, v, mod="grades", src=_GRADES, tests=_GRADES_TESTS,
                 bugs=[[("        if score > cutoff:", "        if score >= cutoff:")],
                       [("    return sorted(finals, key=lambda name: (finals[name], name))",
                         "    return sorted(finals, key=lambda name: (-finals[name], name))")]],
                 intro="`grades.py` is the grade book for a short course.",
                 symptoms=[f"A student who scores exactly {a}% gets a B, and in general a score that lands exactly "
                           "on a cutoff falls to the next letter down.",
                           "The class ranking comes out upside down: the lowest scorer is listed first."])


# ------------------------------------------------------------------ 4. warehouse stock

_WAREHOUSE = '''"""Warehouse stock helpers. Stock is a dict of item name -> units on hand."""


def apply_shipment(stock, shipment):
    """Return a copy of `stock` with a delivery (dict of item -> units) added.

    Items that are not in stock yet are created. Every quantity must be
    positive; otherwise ValueError is raised and nothing is applied.
    """
    updated = dict(stock)
    for item, units in shipment.items():
        if units <= 0:
            raise ValueError("units must be positive: " + item)
        updated[item] = updated.get(item, 0) + units
    return updated


def remove_stock(stock, order):
    """Return a copy of `stock` with an order (dict of item -> units) taken out.

    If any item is short, raise ValueError and change nothing. An item that
    reaches zero stays in the dict with 0.
    """
    for item, units in order.items():
        if stock.get(item, 0) < units:
            raise ValueError("not enough " + item)
    updated = dict(stock)
    for item, units in order.items():
        updated[item] -= units
    return updated


def low_stock(stock, minimums):
    """Sorted names of the items whose units are below their minimum.

    An item missing from `stock` counts as 0 units.
    """
    return sorted(item for item, least in minimums.items() if stock.get(item, 0) < least)


def average_daily_use(moves, days):
    """Average units taken out per day for each item, rounded to one decimal.

    `moves` is a list of (item, units_out) pairs covering `days` days.
    ValueError if `days` is not positive.
    """
    if days <= 0:
        raise ValueError("days must be positive")
    totals = {}
    for item, units in moves:
        totals[item] = totals.get(item, 0) + units
    return {item: round(total / days, 1) for item, total in totals.items()}


def days_left(stock, usage):
    """Whole days each item will last at its daily usage rate.

    Items whose usage is zero or less are left out; items not in stock last 0 days.
    """
    return {item: int(stock.get(item, 0) // rate) for item, rate in usage.items() if rate > 0}


class Warehouse:
    """Keeps current stock and a log of what came in and went out."""

    def __init__(self, stock=None):
        self.stock = dict(stock or {})
        self.history = []

    def receive(self, shipment):
        self.stock = apply_shipment(self.stock, shipment)
        self.history.append(("in", dict(shipment)))

    def ship(self, order):
        self.stock = remove_stock(self.stock, order)
        self.history.append(("out", dict(order)))

    def units(self, item):
        return self.stock.get(item, 0)
'''

_WAREHOUSE_TESTS = '''
def test_whole_delivery_is_added():
    stock = {"@A@": 4}
    out = M.apply_shipment(stock, {"@B@": 3, "@A@": 2, "@C@": 9})
    assert out == {"@A@": 6, "@B@": 3, "@C@": 9}
    assert stock == {"@A@": 4}
    with pytest.raises(ValueError):
        M.apply_shipment(stock, {"@B@": 3, "@C@": 0})


def test_fractional_daily_use():
    assert M.average_daily_use([("@A@", 5)], 2) == {"@A@": 2.5}
    assert M.average_daily_use([("@A@", 1), ("@B@", 7), ("@A@", 1)], 3) == {"@A@": 0.7, "@B@": 2.3}
    with pytest.raises(ValueError):
        M.average_daily_use([], 0)


def test_other_helpers():
    stock = {"@A@": 4, "@B@": 0, "@C@": 10}
    same("remove_stock", stock, {"@A@": 4, "@C@": 1})
    same("remove_stock", stock, {"@A@": 5})
    same("remove_stock", stock, {"@D@": 1})
    same("low_stock", stock, {"@A@": 5, "@B@": 0, "@C@": 10, "@D@": 1})
    same("days_left", stock, {"@A@": 1.5, "@B@": 2, "@C@": 0, "@D@": 3})


def test_warehouse_class():
    w = M.Warehouse({"@A@": 1})
    w.receive({"@A@": 2, "@B@": 5})
    w.ship({"@B@": 5})
    assert w.units("@A@") == 3 and w.units("@B@") == 0 and w.units("@C@") == 0
    assert w.history == [("in", {"@A@": 2, "@B@": 5}), ("out", {"@B@": 5})]


def test_random():
    rng = random.Random(1234)
    items = ["@A@", "@B@", "@C@", "@D@"]
    for _ in range(250):
        stock = {i: rng.randint(0, 20) for i in rng.sample(items, rng.randint(0, 4))}
        batch = {i: rng.randint(-1, 9) for i in rng.sample(items, rng.randint(0, 4))}
        same("apply_shipment", stock, batch)
        same("remove_stock", stock, {i: rng.randint(0, 12) for i in rng.sample(items, rng.randint(0, 3))})
        same("low_stock", stock, {i: rng.randint(0, 15) for i in rng.sample(items, rng.randint(0, 4))})
        same("average_daily_use", [(rng.choice(items), rng.randint(0, 9)) for _ in range(rng.randint(0, 8))], rng.randint(-1, 9))
        same("days_left", stock, {i: rng.choice([0, 1, 2.5, 4]) for i in rng.sample(items, rng.randint(0, 4))})
'''


def _warehouse(rng):
    names = pick(rng, [["bolts", "nuts", "washers", "screws"], ["resin", "pigment", "solvent", "filler"],
                       ["mugs", "plates", "bowls", "forks"]])
    v = dict(A=names[0], B=names[1], C=names[2], D=names[3])
    return _case(rng, v, mod="warehouse", src=_WAREHOUSE, tests=_WAREHOUSE_TESTS,
                 bugs=[[("        updated[item] = updated.get(item, 0) + units\n        return updated\n",
                         "        updated[item] = updated.get(item, 0) + units\n    return updated\n")],
                       [("    return {item: round(total // days, 1) for item, total in totals.items()}",
                         "    return {item: round(total / days, 1) for item, total in totals.items()}")]],
                 intro="`warehouse.py` keeps track of stock for a small warehouse.",
                 symptoms=["Receiving a delivery that lists several items only adds the first item to the stock.",
                           "The daily-use report rounds down to whole numbers: an item that leaves at 5 units over "
                           "2 days shows 2 instead of 2.5."])


# ------------------------------------------------------------------ 5. log summaries

_LOGS = '''"""Parse and summarise application log lines.

A line looks like: 2026-03-01 12:00:01 ERROR db: connection timed out
That is a date, a time, a level, a component name followed by a colon, and
then the message.
"""

LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]


def parse_line(line):
    """Parse one line into a dict with stamp, level, component and message.

    The stamp is "date time" as written. The message is everything after the
    first ": " and may itself contain colons. Returns None for a line that is
    not in the format: blank, unknown level, or no component.
    """
    parts = line.strip().split(" ", 3)
    if len(parts) < 4 or parts[2] not in LEVELS:
        return None
    component, sep, message = parts[3].partition(": ")
    if not sep or not component or " " in component:
        return None
    return {"stamp": parts[0] + " " + parts[1], "level": parts[2], "component": component, "message": message}


def parse_all(text):
    """Parse every line of a multi-line text, skipping lines that do not parse."""
    entries = []
    for line in text.splitlines():
        entry = parse_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


def count_by_level(entries):
    """{level: count} with every level in LEVELS present, 0 if unseen."""
    counts = dict.fromkeys(LEVELS, 0)
    for entry in entries:
        counts[entry["level"]] += 1
    return counts


def top_components(entries, n, min_level="@MIN@"):
    """Up to n (component, count) pairs, the busiest component first.

    Only entries at `min_level` or worse are counted. Components with the
    same count are ordered by name, A to Z.
    """
    floor = LEVELS.index(min_level)
    counts = {}
    for entry in entries:
        if LEVELS.index(entry["level"]) >= floor:
            counts[entry["component"]] = counts.get(entry["component"], 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return ranked[:n]


def between(entries, start, end):
    """Entries whose stamp is at or after `start` and before `end` (stamps compare as text)."""
    return [e for e in entries if start <= e["stamp"] < end]


def first_error_per_component(entries):
    """{component: message of its earliest ERROR entry in the order given}."""
    first = {}
    for entry in entries:
        if entry["level"] == "ERROR" and entry["component"] not in first:
            first[entry["component"]] = entry["message"]
    return first
'''

_LOGS_TESTS = '''
LINE = "2026-03-01 12:00:01 ERROR db: connection failed: host=@HOST@ port=5432: timed out"


def test_message_keeps_its_colons():
    got = M.parse_line(LINE)
    assert got == {"stamp": "2026-03-01 12:00:01", "level": "ERROR", "component": "db",
                   "message": "connection failed: host=@HOST@ port=5432: timed out"}
    assert M.parse_line("2026-03-01 12:00:01 INFO web: a: b")["message"] == "a: b"


def test_bad_lines():
    for line in ["", "   ", "2026-03-01 12:00:01 LOUD db: x", "2026-03-01 12:00:01 INFO db", "2026-03-01 12:00:01 INFO two words: x",
                 "2026-03-01 12:00:01 INFO : x", "garbage"]:
        same("parse_line", line)


def test_busiest_first():
    text = "\\n".join(["2026-03-01 10:00:0%d %s %s: m" % (i, lvl, comp) for i, (lvl, comp) in enumerate(
        [("ERROR", "api"), ("WARN", "db"), ("ERROR", "db"), ("INFO", "cache"), ("WARN", "api"), ("ERROR", "db"), ("WARN", "zed")])])
    entries = M.parse_all(text)
    assert M.top_components(entries, 2) == [("db", 3), ("api", 2)]
    assert M.top_components(entries, 5, "ERROR") == [("db", 2), ("api", 1)]
    assert M.top_components(entries, 0) == []
    assert M.top_components(entries, 10, "DEBUG")[0] == ("db", 3)


def test_other_summaries():
    text = "\\n".join([LINE, "junk", "2026-03-01 12:00:09 WARN web: slow", "2026-03-02 00:00:00 ERROR db: again", ""])
    entries = M.parse_all(text)
    assert entries == REF["parse_all"](text)
    same("count_by_level", entries)
    same("between", entries, "2026-03-01 12:00:05", "2026-03-02 00:00:00")
    same("first_error_per_component", entries)


def test_random():
    rng = random.Random(1234)
    comps = ["api", "db", "web", "cache", "auth"]
    msgs = ["ok", "a: b", "retry: 3: failed", "x y z", ""]
    for _ in range(150):
        lines = []
        for _ in range(rng.randint(0, 14)):
            lines.append("2026-03-0%d 1%d:00:0%d %s %s: %s" % (rng.randint(1, 3), rng.randint(0, 9), rng.randint(0, 9),
                         rng.choice(["DEBUG", "INFO", "WARN", "ERROR", "LOUD"]), rng.choice(comps), rng.choice(msgs)))
        text = "\\n".join(lines)
        same("parse_all", text)
        entries = REF["parse_all"](text)
        same("count_by_level", entries)
        same("top_components", entries, rng.randint(0, 5), rng.choice(LEVELS))
        same("between", entries, "2026-03-01 15", "2026-03-03 12")
        same("first_error_per_component", entries)


LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]
'''


def _logs(rng):
    v = dict(MIN=pick(rng, ["WARN", "INFO"]), HOST=pick(rng, ["db1", "cache-2", "pg-main"]))
    return _case(rng, v, mod="logs", src=_LOGS, tests=_LOGS_TESTS,
                 bugs=[[("    component, sep, rest = parts[3].partition(\": \")\n    message = rest.split(\": \")[0]\n",
                         "    component, sep, message = parts[3].partition(\": \")\n")],
                       [("    ranked = sorted(counts.items(), key=lambda pair: (pair[1], pair[0]))",
                         "    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))")]],
                 intro="`logs.py` parses application log lines and summarises them.",
                 symptoms=["Parsed messages that contain a colon are cut short: for a line whose message is "
                           "`connection failed: timed out`, only `connection failed` is kept.",
                           "`top_components` lists the quietest components instead of the busiest ones."])


# ------------------------------------------------------------------ 6. settings

_CONFIG = '''"""Load app settings from defaults, a settings dict and environment variables."""
import copy

TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off")


def parse_bool(text):
    """Turn an environment-style value into a bool.

    Surrounding spaces and letter case are ignored: " True " and "YES" are
    True, "Off" is False. Anything else raises ValueError.
    """
    word = text.strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise ValueError("not a boolean: " + repr(text))


def merge(base, override):
    """A new dict with `override` laid over `base`.

    Nested dicts are merged key by key. Any other value in `override` replaces
    the one in `base`, including None, 0 and False. Neither input is changed.
    """
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def get_path(cfg, path, default=None):
    """The value at a dotted path such as "db.port", or `default` if any part is missing.

    A value that is present is returned as it is, even if it is 0, False,
    an empty string or None.
    """
    node = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def from_env(env, prefix, schema):
    """Read settings named PREFIX_KEY from `env`, a dict of strings.

    `schema` maps each setting name to int, float, bool or str. Settings that
    are absent from `env` are left out. Returns {name: converted value}.
    """
    out = {}
    for key, kind in schema.items():
        raw = env.get(prefix + "_" + key.upper())
        if raw is None:
            continue
        out[key] = parse_bool(raw) if kind is bool else kind(raw)
    return out


def flatten(cfg, prefix=""):
    """{"db.port": 5432, ...}: every non-dict value keyed by its dotted path, in order."""
    out = {}
    for key, value in cfg.items():
        path = prefix + key
        if isinstance(value, dict):
            out.update(flatten(value, path + "."))
        else:
            out[path] = value
    return out


def load(defaults, file_settings, env, prefix, schema):
    """Defaults, then the settings dict, then environment values on top (top-level names only)."""
    return merge(merge(defaults, file_settings), from_env(env, prefix, schema))
'''

_CONFIG_TESTS = '''
DEFAULTS = {"debug": False, "port": @PORT@, "db": {"host": "localhost", "pool": 5, "ssl": True}, "name": "app"}


def test_booleans_any_case():
    for word, want in [("True", True), ("TRUE", True), (" Yes ", True), ("On", True), ("1", True),
                       ("False", False), ("NO", False), (" off", False), ("0", False)]:
        assert M.parse_bool(word) is want
    for word in ["", "maybe", "2", "tru"]:
        with pytest.raises(ValueError):
            M.parse_bool(word)


def test_falsy_values_are_real_values():
    cfg = {"db": {"pool": 0, "ssl": False, "note": "", "proxy": None}, "retries": 0}
    assert M.get_path(cfg, "db.pool", 9) == 0
    assert M.get_path(cfg, "db.ssl", True) is False
    assert M.get_path(cfg, "db.note", "x") == ""
    assert M.get_path(cfg, "db.proxy", "x") is None
    assert M.get_path(cfg, "retries", 3) == 0
    assert M.get_path(cfg, "db.missing", 7) == 7 and M.get_path(cfg, "db.pool.deeper", 8) == 8


def test_merge_and_flatten():
    over = {"port": 0, "db": {"pool": 20, "extra": {"a": 1}}, "debug": True}
    same("merge", DEFAULTS, over)
    same("merge", {}, over)
    same("flatten", DEFAULTS)
    same("flatten", over)
    base = copy.deepcopy(DEFAULTS)
    out = M.merge(base, over)
    assert base == DEFAULTS and out["db"]["host"] == "localhost" and out["db"]["pool"] == 20
    out["db"]["extra"]["a"] = 2
    assert over["db"]["extra"]["a"] == 1


def test_env_and_load():
    env = {"APP_PORT": "9000", "APP_DEBUG": "Yes", "APP_NAME": "x", "OTHER_PORT": "1"}
    schema = {"port": int, "debug": bool, "name": str, "ratio": float}
    assert M.from_env(env, "APP", schema) == {"port": 9000, "debug": True, "name": "x"}
    same("load", DEFAULTS, {"port": 1}, env, "APP", schema)
    same("from_env", {"APP_DEBUG": "perhaps"}, "APP", schema)


def test_random():
    rng = random.Random(1234)
    vals = [0, 1, "", "s", False, True, None, 2.5]

    def tree(depth):
        return {k: (tree(depth - 1) if depth and rng.random() < 0.4 else rng.choice(vals))
                for k in rng.sample(["a", "b", "c", "d"], rng.randint(0, 4))}
    for _ in range(250):
        a, b = tree(2), tree(2)
        same("merge", a, b)
        same("flatten", a)
        flat = REF["flatten"](a)
        for path in list(flat) + ["a.b.c", "zz"]:
            same("get_path", a, path, "dflt")
        same("parse_bool", rng.choice(["TRUE", "no", " Off ", "x", "On", "1", "YES"]))
'''


def _config(rng):
    v = dict(PORT=pick(rng, [8000, 8080, 5000, 3000]))
    return _case(rng, v, mod="config", src=_CONFIG, tests=_CONFIG_TESTS,
                 bugs=[[("    word = text.strip()\n", "    word = text.strip().lower()\n")],
                       [("        node = node[part]\n    return node or default\n", "        node = node[part]\n    return node\n")]],
                 intro="`config.py` combines default settings, a settings file and environment variables.",
                 symptoms=["Environment values written as `True`, `YES` or `On` are rejected as not being booleans; "
                           "only all-lowercase words work.",
                           "A setting that is deliberately set to 0, False or an empty string is ignored by "
                           "`get_path`, which hands back the default instead."])


# ------------------------------------------------------------------ 7. shopping cart

_CART = '''"""Shopping cart pricing. All money is in whole cents.

A cart is a list of item dicts with the keys name, category, price (cents per
unit) and qty.
"""

FREE_SHIPPING_OVER = @FREE@
FLAT_SHIPPING = @FLAT@


def add_item(cart, name, category, price, qty=1):
    """Add to the cart in place; an item with the same name just gets more quantity."""
    for item in cart:
        if item["name"] == name:
            item["qty"] += qty
            return
    cart.append({"name": name, "category": category, "price": price, "qty": qty})


def subtotal(cart):
    """Cents for everything in the cart before any coupon."""
    return sum(item["price"] * item["qty"] for item in cart)


def totals_by_category(cart):
    """{category: cents} with the categories in order of first appearance."""
    out = {}
    for item in cart:
        key = item["category"]
        out[key] = out.get(key, 0) + item["price"] * item["qty"]
    return out


def most_expensive(cart):
    """Name of the item with the highest unit price (the first on a tie), or None for an empty cart."""
    best = None
    for item in cart:
        if best is None or item["price"] > best["price"]:
            best = item
    return None if best is None else best["name"]


def apply_coupon(total, coupon):
    """The total after a coupon, never below 0.

    `coupon` is None, {"kind": "percent", "value": 15} or
    {"kind": "fixed", "value": 500} (cents off). A percentage discount is
    rounded to whole cents. Any other kind raises ValueError.
    """
    if coupon is None:
        return total
    if coupon["kind"] == "percent":
        discount = round(total * coupon["value"] / 100)
    elif coupon["kind"] == "fixed":
        discount = coupon["value"]
    else:
        raise ValueError("unknown coupon kind: " + str(coupon["kind"]))
    return max(0, total - discount)


def shipping(total):
    """Flat shipping, free for totals of FREE_SHIPPING_OVER or more and for an empty (zero) total."""
    if total <= 0 or total >= FREE_SHIPPING_OVER:
        return 0
    return FLAT_SHIPPING


def checkout(cart, coupon=None):
    """Dict with subtotal, discount, shipping and total (all cents).

    Shipping is decided by the total after the coupon.
    """
    before = subtotal(cart)
    after = apply_coupon(before, coupon)
    fee = shipping(after)
    return {"subtotal": before, "discount": before - after, "shipping": fee, "total": after + fee}
'''

_CART_TESTS = '''
def _cart(ns, rows):
    cart = []
    for row in rows:
        ns["add_item"](cart, *row)
    return cart


ROWS = [("lamp", "home", 2500, 1), ("mug", "kitchen", 800, 3), ("pan", "kitchen", 2200, 1), ("lamp", "home", 2500, 1)]


def test_totals_by_category():
    cart = _cart(vars(M), ROWS)
    assert M.totals_by_category(cart) == {"home": 5000, "kitchen": 4600}
    assert list(M.totals_by_category(cart)) == ["home", "kitchen"]
    assert M.totals_by_category([]) == {}


def test_coupon_cannot_go_negative():
    assert M.apply_coupon(1500, {"kind": "fixed", "value": 2000}) == 0
    assert M.apply_coupon(1500, {"kind": "fixed", "value": 1500}) == 0
    assert M.apply_coupon(1500, {"kind": "fixed", "value": 500}) == 1000
    assert M.apply_coupon(1999, {"kind": "percent", "value": 15}) == 1999 - round(1999 * 0.15)
    assert M.apply_coupon(100, None) == 100
    with pytest.raises(ValueError):
        M.apply_coupon(100, {"kind": "bogus", "value": 1})


def test_checkout_and_helpers():
    cart = _cart(vars(M), ROWS)
    assert len(cart) == 3 and cart[0]["qty"] == 2
    for coupon in [None, {"kind": "fixed", "value": 99999}, {"kind": "percent", "value": 100}, {"kind": "percent", "value": 10}]:
        same("checkout", cart, coupon)
    same("most_expensive", cart)
    same("most_expensive", [])
    same("shipping", @FREE@)
    same("shipping", @FREE@ - 1)
    same("shipping", 0)
    same("subtotal", [])


def test_random():
    rng = random.Random(1234)
    names = ["lamp", "mug", "pan", "rug", "fan"]
    cats = ["home", "kitchen", "garden"]
    for _ in range(250):
        rows = [(rng.choice(names), rng.choice(cats), rng.choice([100, 800, 2500, 2500]), rng.randint(1, 4))
                for _ in range(rng.randint(0, 7))]
        cart = _cart(REF, rows)
        got = _cart(vars(M), rows)
        assert got == cart
        for name in ("subtotal", "totals_by_category", "most_expensive"):
            same(name, cart)
        coupon = rng.choice([None, {"kind": "fixed", "value": rng.randint(0, 9000)}, {"kind": "percent", "value": rng.randint(0, 100)}])
        same("apply_coupon", rng.randint(0, 9000), coupon)
        same("checkout", cart, coupon)
'''


def _cart(rng):
    free = pick(rng, [5000, 7500, 10000])
    v = dict(FREE=free, FLAT=pick(rng, [499, 595, 799]))
    return _case(rng, v, mod="cart", src=_CART, tests=_CART_TESTS,
                 bugs=[[("        key = item[\"name\"]\n", "        key = item[\"category\"]\n")],
                       [("    return total - discount\n", "    return max(0, total - discount)\n")]],
                 intro="`cart.py` prices a shopping cart.",
                 symptoms=["The per-category report has one row for every product instead of one row per category.",
                           "A fixed-amount coupon that is bigger than the cart (a $20 coupon on a $15 cart) makes the "
                           "total negative instead of zero."])


# ------------------------------------------------------------------ 8. time tracking

_TIMEUTIL = '''"""Time-tracking helpers. Durations are whole seconds."""

UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """Seconds in text such as "1h30m", "45m", "2H 5s" or "90s".

    Spaces and letter case are ignored and the units may come in any order.
    Raises ValueError for empty text, for a number without a unit (such as
    "15"), and for any character that is not a digit, h, m or s.
    """
    cleaned = text.replace(" ", "").lower()
    if not cleaned:
        raise ValueError("empty duration")
    total, number = 0, ""
    for ch in cleaned:
        if ch.isdigit():
            number += ch
        elif ch in UNITS and number:
            total += int(number) * UNITS[ch]
            number = ""
        else:
            raise ValueError("bad duration: " + repr(text))
    if number:
        raise ValueError("missing unit: " + repr(text))
    return total


def format_duration(seconds):
    """Short text for a duration of 0 or more seconds.

    An hour or more: "1h 05m". A minute or more: "5m 07s". Otherwise "7s".
    The trailing parts are zero padded to two digits.
    """
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


def billable_seconds(seconds, step=@STEP@):
    """Round up to the next multiple of `step` seconds.

    A duration that already is an exact multiple stays as it is, and 0 stays 0.
    """
    return -(-seconds // step) * step


def total_billable(entries, rate_per_hour_cents):
    """Cents to charge for a list of (label, seconds) entries.

    Each entry is rounded up with billable_seconds first, the rounded
    seconds are added up, and the result is hours * rate rounded to whole cents.
    """
    seconds = sum(billable_seconds(secs) for _, secs in entries)
    return round(seconds / 3600 * rate_per_hour_cents)


class Timesheet:
    """A day's entries, each added as a label and a duration text."""

    def __init__(self):
        self.entries = []

    def add(self, label, text):
        self.entries.append((label, parse_duration(text)))

    def worked(self):
        """Total worked time, formatted with format_duration."""
        return format_duration(sum(secs for _, secs in self.entries))

    def invoice(self, rate_per_hour_cents):
        return total_billable(self.entries, rate_per_hour_cents)
'''

_TIMEUTIL_TESTS = '''
def test_exact_multiples_are_not_rounded_up():
    s = @STEP@
    assert [M.billable_seconds(x) for x in (0, 1, s - 1, s, s + 1, 3 * s)] == [0, s, s, s, 2 * s, 3 * s]
    assert M.total_billable([("a", 3 * @STEP@)], 6000) == round(3 * @STEP@ / 3600 * 6000)


def test_formatting_over_an_hour():
    assert M.format_duration(3725) == "1h 02m"
    assert M.format_duration(3600) == "1h 00m"
    assert M.format_duration(7384) == "2h 03m"
    assert M.format_duration(367) == "6m 07s"
    assert M.format_duration(59) == "59s" and M.format_duration(0) == "0s"


def test_parsing():
    assert M.parse_duration("1h30m") == 5400
    assert M.parse_duration(" 2H 5s") == 7205
    assert M.parse_duration("90s") == 90 and M.parse_duration("5s2m") == 125
    for bad in ["", "   ", "15", "1h 30", "m", "1x", "h1", "1.5h"]:
        with pytest.raises(ValueError):
            M.parse_duration(bad)


def test_timesheet():
    def run(ns):
        sheet = ns["Timesheet"]()
        sheet.add("a", "1h10m")
        sheet.add("b", "20m")
        sheet.add("c", "45s")
        return sheet.worked(), sheet.invoice(8000)
    assert run(vars(M)) == run(REF)


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        secs = rng.choice([rng.randint(0, 100000), rng.randint(0, 5) * 3600 + rng.randint(0, 3) * 60])
        same("format_duration", secs)
        same("billable_seconds", secs)
        same("billable_seconds", secs, rng.choice([60, 300, 1800]))
        text = "".join(str(rng.randint(0, 99)) + rng.choice("hmsHMS ") for _ in range(rng.randint(0, 4)))
        same("parse_duration", text)
        same("parse_duration", rng.choice(["1h", "12", "3m4", "x", "7s 8m", "0s"]))
        entries = [("e", rng.randint(0, 5000)) for _ in range(rng.randint(0, 5))]
        same("total_billable", entries, rng.choice([5000, 7500, 12000]))
'''


def _timeutil(rng):
    v = dict(STEP=pick(rng, [900, 600, 1800]))
    return _case(rng, v, mod="timeutil", src=_TIMEUTIL, tests=_TIMEUTIL_TESTS,
                 bugs=[[("    minutes, secs = divmod(seconds, 60)\n", "    minutes, secs = divmod(rest, 60)\n")],
                       [("    return (seconds // step + 1) * step\n", "    return -(-seconds // step) * step\n")]],
                 intro="`timeutil.py` parses, formats and bills tracked time.",
                 symptoms=["Durations over an hour show far too many minutes: one hour and two minutes is printed as `1h 62m`.",
                           f"Time that already is an exact multiple of the billing step ({v['STEP'] // 60} minutes) is "
                           "billed for one more step; for example exactly "
                           f"{v['STEP'] // 60} minutes is billed as {v['STEP'] // 30}."])


# ------------------------------------------------------------------ 9. catalog browsing

_CATALOG = '''"""Catalog browsing helpers: search, sort and pages. Pages are numbered from 1."""

SORT_KEYS = ("name", "price", "stock")


def page_count(total_items, per_page):
    """How many pages `total_items` fill when each page holds `per_page`.

    The last page may be partly filled. An empty catalog still has one (empty)
    page. ValueError if per_page is not positive.
    """
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return max(1, -(-total_items // per_page))


def get_page(items, page, per_page):
    """The items on one page. ValueError if the page does not exist."""
    if page < 1 or page > page_count(len(items), per_page):
        raise ValueError("no such page")
    start = (page - 1) * per_page
    return items[start:start + per_page]


def search(items, query):
    """Items whose name contains the query, ignoring letter case.

    A blank query (empty or only spaces) matches everything. The order is kept.
    """
    needle = query.strip().lower()
    return [item for item in items if needle in item["name"].lower()]


def sort_items(items, key="name", descending=False):
    """A new list sorted by `key`; ValueError for a key not in SORT_KEYS.

    Names compare ignoring letter case. Items that compare equal keep their
    original order, also when sorting descending.
    """
    if key not in SORT_KEYS:
        raise ValueError("cannot sort by " + str(key))
    if key == "name":
        return sorted(items, key=lambda item: item["name"].lower(), reverse=descending)
    return sorted(items, key=lambda item: item[key], reverse=descending)


def browse(items, query="", sort="name", descending=False, page=1, per_page=@PER@):
    """One page of search results.

    Search first, then sort, then cut out the page. Returns a dict with
    "items" (the page), "page", "pages" (how many pages the results fill)
    and "total" (the number of matches).
    """
    found = sort_items(search(items, query), sort, descending)
    return {"items": get_page(found, page, per_page), "page": page,
            "pages": page_count(len(found), per_page), "total": len(found)}


def in_stock(items):
    """Only the items with stock above 0, in the original order."""
    return [item for item in items if item["stock"] > 0]


def price_range(items):
    """(cheapest, dearest) price among the items, or None for no items."""
    if not items:
        return None
    prices = [item["price"] for item in items]
    return (min(prices), max(prices))
'''

_CATALOG_TESTS = '''
ITEMS = [{"name": n, "price": p, "stock": s} for n, p, s in [
    ("Hand Pump", 25, 4), ("pump seal", 3, 0), ("Garden Hose", 18, 7), ("hose clip", 3, 12), ("Bucket", 9, 0),
    ("Watering Can", 18, 2), ("pumice stone", 5, 1), ("Spade", 22, 3), ("Rake", 15, 6), ("Gloves", 7, 9), ("Trowel", 12, 5)]]


def test_partial_last_page_counts():
    assert [M.page_count(n, 10) for n in (0, 1, 10, 11, 25, 30)] == [1, 1, 1, 2, 3, 3]
    assert M.page_count(7, 3) == 3
    with pytest.raises(ValueError):
        M.page_count(5, 0)
    assert M.browse(ITEMS, per_page=5)["pages"] == 3
    assert len(M.browse(ITEMS, page=3, per_page=5)["items"]) == 1


def test_search_ignores_case_on_both_sides():
    assert [i["name"] for i in M.search(ITEMS, "PUMP")] == ["Hand Pump", "pump seal"]
    assert [i["name"] for i in M.search(ITEMS, "pump")] == ["Hand Pump", "pump seal"]
    assert [i["name"] for i in M.search(ITEMS, "Hose")] == ["Garden Hose", "hose clip"]
    assert len(M.search(ITEMS, "   ")) == len(ITEMS) == len(M.search(ITEMS, ""))
    assert M.search(ITEMS, "zzz") == []


def test_sorting_and_pages():
    for key in ("name", "price", "stock"):
        for desc in (False, True):
            same("sort_items", ITEMS, key, desc)
    same("sort_items", ITEMS, "colour", False)
    same("get_page", ITEMS, 0, 4)
    same("get_page", ITEMS, 4, 4)
    same("get_page", ITEMS, 2, 4)
    same("in_stock", ITEMS)
    same("price_range", ITEMS)
    same("price_range", [])


def test_random():
    rng = random.Random(1234)
    words = ["Pump", "pump", "Hose", "rake", "Seal", "CAN", "can", "kit"]
    for _ in range(250):
        items = [{"name": rng.choice(words) + " " + rng.choice(words), "price": rng.randint(1, 5), "stock": rng.randint(0, 3)}
                 for _ in range(rng.randint(0, 25))]
        query = rng.choice(["", " ", "pump", "PUMP", "Can", "kit", "se", "zz"])
        per = rng.randint(1, 8)
        same("search", items, query)
        same("page_count", len(items), per)
        same("browse", items, query, rng.choice(SORT), rng.random() < 0.5, rng.randint(0, 4), per)
        same("price_range", items)
        same("in_stock", items)


SORT = ["name", "price", "stock"]
'''


def _catalog(rng):
    v = dict(PER=pick(rng, [5, 8, 10]))
    return _case(rng, v, mod="catalog", src=_CATALOG, tests=_CATALOG_TESTS,
                 bugs=[[("    return max(1, total_items // per_page)\n", "    return max(1, -(-total_items // per_page))\n")],
                       [("    needle = query.strip()\n", "    needle = query.strip().lower()\n")]],
                 intro="`catalog.py` powers the product list of a small shop.",
                 symptoms=["With 25 products and 10 per page the list says there are 2 pages, so the last 5 products "
                           "can never be reached.",
                           "Searching for `pump` finds `Hand Pump`, but searching for `Pump` or `PUMP` finds nothing; "
                           "the search should ignore letter case in what the user types as well."])


# ------------------------------------------------------------------ 10. latency metrics

_METRICS = '''"""Latency metrics for a service dashboard. Values are milliseconds."""
import bisect


def median(values):
    """The middle value; for an even count, the mean of the two middle values.

    Raises ValueError for an empty list.
    """
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def percentile(values, p):
    """Nearest-rank percentile for a whole number p from 1 to 100.

    Sort the values and take the one at rank ceil(p * n / 100), counting from 1.
    Raises ValueError for an empty list.
    """
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = -(-(p * len(ordered)) // 100)
    return ordered[rank - 1]


def moving_average(values, window):
    """The average of every run of `window` consecutive values, rounded to 2 decimals.

    Only full windows count, so n values give n - window + 1 results, and
    fewer values than the window give []. ValueError if window < 1.
    """
    if window < 1:
        raise ValueError("window must be at least 1")
    return [round(sum(values[i:i + window]) / window, 2) for i in range(len(values) - window + 1)]


def bucket_counts(values, edges):
    """How many values fall in each bucket, given ascending bucket edges.

    The result has len(edges) + 1 counts: below edges[0], from edges[0] up to
    (not including) edges[1], and so on, with the last count for values at or
    above the last edge.
    """
    counts = [0] * (len(edges) + 1)
    for value in values:
        counts[bisect.bisect_right(edges, value)] += 1
    return counts


def summarize(values):
    """Dict with count, mean (2 decimals), median, p95 and max; just {"count": 0} when empty."""
    if not values:
        return {"count": 0}
    return {"count": len(values), "mean": round(sum(values) / len(values), 2), "median": median(values),
            "p95": percentile(values, 95), "max": max(values)}


class Series:
    """A growing list of measurements with a cap on how many are kept."""

    def __init__(self, keep=@KEEP@):
        self.keep = keep
        self.values = []

    def add(self, value):
        """Append a value; once there are more than `keep`, the oldest are forgotten."""
        self.values.append(value)
        if len(self.values) > self.keep:
            del self.values[:len(self.values) - self.keep]

    def summary(self):
        return summarize(self.values)

    def slowest(self, n):
        """The n largest values, largest first."""
        return sorted(self.values, reverse=True)[:n]
'''

_METRICS_TESTS = '''
def test_even_count_median():
    assert M.median([4, 1, 3, 2]) == 2.5
    assert M.median([10, 20]) == 15
    assert M.median([5, 1, 9]) == 5
    assert M.median([7]) == 7
    with pytest.raises(ValueError):
        M.median([])


def test_last_window_is_included():
    assert M.moving_average([1, 2, 3, 4, 5], 2) == [1.5, 2.5, 3.5, 4.5]
    assert M.moving_average([1, 2, 3], 3) == [2.0]
    assert M.moving_average([1, 2], 3) == []
    assert M.moving_average([4], 1) == [4.0]
    with pytest.raises(ValueError):
        M.moving_average([1], 0)


def test_other_helpers():
    vals = [12, 7, 40, 19, 3, 25, 31, 8, 15, 22]
    for p in (1, 10, 50, 90, 95, 100):
        same("percentile", vals, p)
    same("percentile", [], 50)
    same("bucket_counts", vals, [10, 20, 30])
    same("bucket_counts", [], [5])
    same("summarize", vals)
    same("summarize", [])


def test_series():
    def run(ns):
        s = ns["Series"](4)
        for v in (5, 9, 1, 7, 3, 8):
            s.add(v)
        return s.values, s.summary(), s.slowest(2)
    assert run(vars(M)) == run(REF)


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        vals = [rng.choice([rng.randint(0, 100), rng.randint(0, 5)]) for _ in range(rng.randint(0, 15))]
        same("median", vals)
        same("percentile", vals, rng.randint(1, 100))
        same("moving_average", vals, rng.randint(0, 6))
        same("bucket_counts", vals, sorted(rng.sample(range(0, 100, 5), rng.randint(0, 4))))
        same("summarize", vals)
'''


def _metrics(rng):
    v = dict(KEEP=pick(rng, [50, 100, 200]))
    return _case(rng, v, mod="metrics", src=_METRICS, tests=_METRICS_TESTS,
                 bugs=[[("    return ordered[mid]\n", "    return (ordered[mid - 1] + ordered[mid]) / 2\n")],
                       [("range(len(values) - window)]", "range(len(values) - window + 1)]")]],
                 intro="`metrics.py` computes latency statistics for a dashboard.",
                 symptoms=["The median of an even number of measurements (for example 1, 2, 3, 4) shows the upper "
                           "middle value instead of the average of the two middle ones.",
                           "The moving average is always one value short: the final window of the data never "
                           "appears in the result."])


# ------------------------------------------------------------------ 11. job scheduler

_JOBS = '''"""A tiny job scheduler.

A job is a dict with: id, priority (a higher number runs first), created (an
int, lower is older), run_at (an int time, or None meaning "as soon as
possible") and attempts (how many times it has failed so far).
"""

MAX_ATTEMPTS = @MAX@


def due_jobs(jobs, now):
    """The jobs that may run at time `now`: those with no run_at, or a run_at that has arrived."""
    return [job for job in jobs if job["run_at"] is None or job["run_at"] <= now]


def next_job(jobs, now):
    """The due job with the highest priority; on equal priority the oldest (lowest created).

    Returns None when nothing is due.
    """
    best = None
    for job in due_jobs(jobs, now):
        if best is None or job["priority"] > best["priority"]:
            best = job
        elif job["priority"] == best["priority"] and job["created"] < best["created"]:
            best = job
    return best


def backoff_delay(attempts, base=@BASE@, cap=@CAP@):
    """Seconds to wait before retry number `attempts` (1 for the first retry).

    The wait doubles with each retry, starting from `base`, and never exceeds `cap`.
    """
    return min(cap, base * 2 ** (attempts - 1))


def after_failure(job, now):
    """The job as it should be queued again after a failure at time `now`.

    Returns a new dict (the input is not changed) with attempts one higher and
    run_at set to now plus the backoff delay for that attempt, or None when
    it has failed MAX_ATTEMPTS times and should be given up on.
    """
    attempts = job["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        return None
    return dict(job, attempts=attempts, run_at=now + backoff_delay(attempts))


class JobQueue:
    """Holds jobs and hands them out in the order next_job chooses."""

    def __init__(self):
        self.jobs = []

    def push(self, job):
        self.jobs.append(job)

    def pop(self, now):
        """Remove and return the next due job, or None."""
        job = next_job(self.jobs, now)
        if job is not None:
            self.jobs.remove(job)
        return job

    def failed(self, job, now):
        """Re-queue a failed job; returns False if it was given up on."""
        retry = after_failure(job, now)
        if retry is None:
            return False
        self.push(retry)
        return True


def count_by_priority(jobs):
    """{priority: how many jobs}, highest priority first."""
    counts = {}
    for job in jobs:
        counts[job["priority"]] = counts.get(job["priority"], 0) + 1
    return dict(sorted(counts.items(), reverse=True))
'''

_JOBS_TESTS = '''
def _job(i, priority, created, run_at=None, attempts=0):
    return {"id": i, "priority": priority, "created": created, "run_at": run_at, "attempts": attempts}


def test_jobs_without_a_run_time_are_due():
    jobs = [_job("a", 1, 1), _job("b", 1, 2, run_at=50), _job("c", 1, 3, run_at=200)]
    assert [j["id"] for j in M.due_jobs(jobs, 100)] == ["a", "b"]
    assert M.next_job([_job("x", 1, 1)], 0)["id"] == "x"
    assert M.next_job([], 5) is None and M.next_job([_job("c", 1, 3, run_at=9)], 5) is None


def test_oldest_goes_first_on_equal_priority():
    jobs = [_job("late", 2, 30), _job("early", 2, 10), _job("mid", 2, 20), _job("low", 1, 1), _job("high", 3, 99)]
    assert M.next_job(jobs, 0)["id"] == "high"
    assert M.next_job(jobs[:3], 0)["id"] == "early"
    q = M.JobQueue()
    for j in jobs[:4]:
        q.push(j)
    assert [q.pop(0)["id"] for _ in range(4)] == ["early", "mid", "late", "low"]
    assert q.pop(0) is None


def test_retries():
    job = _job("r", 1, 1, attempts=1)
    got = M.after_failure(job, 1000)
    assert got == REF["after_failure"](job, 1000) and job["attempts"] == 1
    same("after_failure", _job("r", 1, 1, attempts=@MAX@ - 2), 5)
    same("after_failure", _job("r", 1, 1, attempts=@MAX@ - 1), 5)
    for n in range(1, 9):
        same("backoff_delay", n)
        same("backoff_delay", n, 3, 100)


def test_queue_and_counts():
    jobs = [_job("a", 1, 1), _job("b", 5, 2), _job("c", 1, 3), _job("d", 3, 4)]
    same("count_by_priority", jobs)
    q = M.JobQueue()
    job = _job("f", 1, 1, attempts=@MAX@ - 1)
    assert q.failed(job, 0) is False and q.jobs == []
    assert q.failed(_job("f", 1, 1), 0) is True and len(q.jobs) == 1


def test_random():
    rng = random.Random(1234)
    for _ in range(250):
        jobs = [_job(i, rng.randint(1, 3), rng.randint(1, 6), rng.choice([None, rng.randint(0, 20)]), rng.randint(0, @MAX@))
                for i in range(rng.randint(0, 8))]
        now = rng.randint(0, 20)
        same("due_jobs", jobs, now)
        same("next_job", jobs, now)
        same("count_by_priority", jobs)
        if jobs:
            same("after_failure", rng.choice(jobs), now)
'''


def _jobs(rng):
    v = dict(MAX=pick(rng, [4, 5, 6]), BASE=pick(rng, [2, 5, 10]), CAP=pick(rng, [60, 120, 300]))
    return _case(rng, v, mod="jobs", src=_JOBS, tests=_JOBS_TESTS,
                 bugs=[[("    return [job for job in jobs if job[\"run_at\"] <= now]\n",
                         "    return [job for job in jobs if job[\"run_at\"] is None or job[\"run_at\"] <= now]\n")],
                       [("job[\"created\"] > best[\"created\"]", "job[\"created\"] < best[\"created\"]")]],
                 intro="`jobs.py` is the core of a small background-job scheduler.",
                 symptoms=["Picking the next job crashes with a TypeError as soon as the queue contains a job that has no "
                           "run time (run_at is None), although such a job should run right away.",
                           "Among jobs with the same priority the newest one runs first; the oldest should run first."])


# ------------------------------------------------------------------ 12. text tables

_TABLE = '''"""Plain-text tables for terminal reports."""

SEPARATOR = " | "


def truncate(text, width):
    """Fit text into `width` characters.

    Text that is at most `width` long is kept whole, including text that is
    exactly `width` long. Longer text is cut and ends with "..." so that the
    result is exactly `width` long. When width is under 4 there is no room
    for dots, so the text is simply cut.
    """
    if len(text) <= width:
        return text
    if width < 4:
        return text[:width]
    return text[:width - 3] + "..."


def column_widths(header, rows, max_width=@MAX@):
    """The width of each column: its longest cell, header included, but at most max_width."""
    widths = [len(name) for name in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    return [min(w, max_width) for w in widths]


def render(header, rows, max_width=@MAX@):
    """The table as text: a header line, a rule of dashes, then one line per row.

    Cells are cut with truncate to the column width. Numbers (int or float)
    are right-aligned and everything else left-aligned. Columns are joined
    with SEPARATOR, the rule joins its dashes with "-+-", and no line ends
    in spaces.
    """
    widths = column_widths(header, rows, max_width)

    def line(cells):
        parts = []
        for cell, width in zip(cells, widths):
            text = truncate(str(cell), width)
            parts.append(text.rjust(width) if isinstance(cell, (int, float)) else text.ljust(width))
        return SEPARATOR.join(parts).rstrip()

    rule = "-+-".join("-" * width for width in widths)
    return "\\n".join([line(header), rule] + [line(row) for row in rows])


def sort_rows(rows, column, descending=False):
    """A new list of rows ordered by one column; equal rows keep their original order."""
    return sorted(rows, key=lambda row: row[column], reverse=descending)


def add_totals(header, rows):
    """The rows plus a final "Total" row.

    Numeric columns (every row has an int or float there) are summed; the
    first column says "Total"; other columns are left empty. With no rows the
    total row has 0 in every column after the first.
    """
    total = ["Total"]
    for i in range(1, len(header)):
        column = [row[i] for row in rows]
        numeric = all(isinstance(c, (int, float)) for c in column)
        total.append(sum(column) if numeric else "")
    return list(rows) + [total]


def report(header, rows, descending=True):
    """Rows sorted by their last column, with totals, rendered."""
    ordered = sort_rows(rows, len(header) - 1, descending)
    return render(header, add_totals(header, ordered))
'''

_TABLE_TESTS = '''
HEADER = ["Customer", "Orders", "Revenue"]
ROWS = [["Amy Lopez", 3, 120.5], ["Bo Chen", 12, 99], ["Cy", 1, 5000], ["Quarterly Report", 0, 0]]


def test_exact_width_text_is_not_cut():
    assert M.truncate("abcdefgh", 8) == "abcdefgh"
    assert M.truncate("abcdefghi", 8) == "abcde..."
    assert M.truncate("abc", 8) == "abc" and M.truncate("", 3) == ""
    assert M.truncate("abcdef", 3) == "abc" and M.truncate("abcdef", 0) == ""
    assert M.truncate("abcd", 4) == "abcd" and M.truncate("abcde", 4) == "a..."


def test_header_sets_the_minimum_width():
    assert M.column_widths(["Customer", "N"], [["ab", 1]]) == [8, 1]
    assert M.column_widths(["Customer", "N"], []) == [8, 1]
    text = M.render(["Customer", "Orders"], [["ab", 1]])
    assert text.splitlines()[0] == "Customer | Orders"
    assert text.splitlines()[2] == "ab       |      1"


def test_render_and_report():
    same("render", HEADER, ROWS)
    same("render", HEADER, ROWS, 9)
    same("render", HEADER, [])
    same("report", HEADER, ROWS)
    same("report", HEADER, ROWS, False)
    same("add_totals", HEADER, [["x", 1, 2.5], ["y", 2, 1]])
    same("add_totals", HEADER, [["x", "n/a", 2.5]])
    same("add_totals", HEADER, [])
    same("sort_rows", ROWS, 1, True)
    same("sort_rows", ROWS, 0)


def test_random():
    rng = random.Random(1234)
    words = ["a", "Quarterly Report", "x" * @MAX@, "y" * (@MAX@ + 1), "y" * (@MAX@ - 1), "mid size", ""]
    for _ in range(250):
        header = [rng.choice(["Name", "N", "Customer Name", "Revenue"]) for _ in range(rng.randint(1, 4))]
        rows = [[rng.choice(words) if rng.random() < 0.6 else rng.choice([0, 7, 12.5, 100000]) for _ in header]
                for _ in range(rng.randint(0, 5))]
        same("column_widths", header, rows)
        same("render", header, rows)
        same("render", header, rows, rng.randint(2, 12))
        same("truncate", rng.choice(words), rng.randint(0, @MAX@ + 2))
        same("add_totals", header, rows)
'''


def _table(rng):
    v = dict(MAX=pick(rng, [12, 15, 20]))
    return _case(rng, v, mod="table", src=_TABLE, tests=_TABLE_TESTS,
                 bugs=[[("    if len(text) < width:\n        return text\n", "    if len(text) <= width:\n        return text\n")],
                       [("    widths = [0] * len(header)\n", "    widths = [len(name) for name in header]\n")]],
                 intro="`table.py` prints plain-text tables for terminal reports.",
                 symptoms=["A cell whose text is exactly as wide as its column gets cut and ends in `...` even though "
                           "it fits.",
                           "A column whose header is longer than every cell in it (a column of short numbers under "
                           "`Customer`) is squeezed down and the header is cut off."])


TEMPLATES = [
    probe_template("twobugs", "twobugs_hosting_invoices", _invoicing),
    probe_template("twobugs", "twobugs_room_bookings", _schedule),
    probe_template("twobugs", "twobugs_gradebook", _grades),
    probe_template("twobugs", "twobugs_warehouse_stock", _warehouse),
    probe_template("twobugs", "twobugs_log_summaries", _logs),
    probe_template("twobugs", "twobugs_settings_loader", _config),
    probe_template("twobugs", "twobugs_cart_pricing", _cart),
    probe_template("twobugs", "twobugs_time_billing", _timeutil),
    probe_template("twobugs", "twobugs_catalog_pages", _catalog),
    probe_template("twobugs", "twobugs_latency_metrics", _metrics),
    probe_template("twobugs", "twobugs_job_scheduler", _jobs),
    probe_template("twobugs", "twobugs_text_tables", _table),
]
