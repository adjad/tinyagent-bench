"""Rung 5: a small class that keeps track of state.

Each case asks for one class in one file, with a constructor and 3-5 methods
whose effects interact over time (time is always passed in as a number). The
hidden tests run hand-written operation sequences and seeded random sequences
on the model's class and on a trusted reference class, and compare every
return value and the type of every exception raised.
"""
from __future__ import annotations

import random
import re

from edat.sidekick.ceiling.rungs import answer_files, fill, probe_instance, probe_template
from edat.sidekick.gen.core import pick

_HEAD = '''import copy
import random


def o(name, *args):
    return (name, args)


'''

_HARNESS = '''

def _norm(v):
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    return v


def _run(make, ops):
    obj = make()
    out = []
    for name, args in ops:
        try:
            out.append(("ok", _norm(getattr(obj, name)(*args))))
        except Exception as e:
            out.append(("err", type(e).__name__))
    return out


def _same(ops):
    got = _run(lambda: SUT(*copy.deepcopy(ARGS)), ops)
    want = _run(lambda: Ref(*copy.deepcopy(ARGS)), ops)
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == w, (i, ops[: i + 1], g, w)


def test_hand_written_sequences():
    for ops in HAND:
        _same(ops)


def test_random_sequences():
    rng = random.Random(1234)
    for _ in range(250):
        _same(gen(rng))
'''


def _build(v, *, module, solution, prompt, tests, wrongs, extra=()):
    """`extra`: the hand conversion of every test besides the two shared ones (each an op sequence with
    "init", "steps", "test", "covers" and optionally "raises"); the completeness gate refuses a case where
    one is missing."""
    sol = fill(solution, **v)
    ws = []
    for old, new in wrongs:
        old, new = fill(old, **v), fill(new, **v)
        assert old in sol, old
        w = sol.replace(old, new, 1)
        assert w != sol
        ws.append(w)
    assert len(set(ws)) == len(ws) >= 2
    f = module + ".py"
    return probe_instance(
        prompt=fill(prompt, **v), targets=[f], hidden_tests=fill(_HEAD + tests + _HARNESS, **v),
        references=[answer_files({f: sol}), answer_files({f: sol}, style="label")],
        wrong=[answer_files({f: w}) for w in ws],
        probes=state_probes(fill(_HEAD + tests, **v), extra=extra), solution={f: sol})


_SUT = re.compile(r"^from (\w+) import (\w+) as SUT\s*$", re.M)


def state_probes(tests: str, *, seed: int = 1234, n_random: int = 250, extra=()) -> list[dict]:
    """The hidden tests as host-driven op sequences: the hand-written ones (`test_hand_written_sequences`)
    and `n_random` seeded random ones (`test_random_sequences`), drawn here, on the host, by the template's
    own generator; then `extra`, the template's own other tests, converted by hand. Each generated sequence
    repeats its test's one assertion (`_same`) on one sequence, so it covers "all" of that test. The old
    `_norm` compared tuples and lists alike, so these probes do too ("seq": "loose"). The reference class
    stays on the host side (unused: the expected results come from the template's solution, run apart)."""
    m = _SUT.search(tests)
    assert m, "no `from <module> import <Class> as SUT` line"
    mod, cls = m.group(1), m.group(2)
    ns: dict = {"__name__": "state_probes"}
    exec(_SUT.sub("", tests), ns)                                          # noqa: S102 - the template's own code
    seqs = [list(ops) for ops in ns["HAND"]]
    rng = random.Random(seed)
    seqs += [ns["gen"](rng) for _ in range(n_random)]
    init = list(ns["ARGS"])
    n_hand = len(ns["HAND"])
    out = [{"kind": "ops", "mod": mod, "cls": cls, "init": init, "seq": "loose", "covers": "all",
            "test": "test_hand_written_sequences" if i < n_hand else "test_random_sequences",
            "steps": [[name, list(args)] for name, args in ops]} for i, ops in enumerate(seqs)]
    for e in extra:
        out.append({"kind": "ops", "mod": mod, "cls": cls, "seq": "loose", **e})
    return out


# ------------------------------------------------------------------ parking meter

_PARK_SOL = '''import math


class ParkingMeter:
    def __init__(self, rate, daily_cap):
        if rate <= 0 or daily_cap <= 0:
            raise ValueError("rate and cap must be positive")
        self.rate = rate
        self.cap = daily_cap
        self._in = {}
        self._paid = {}

    def enter(self, plate, minute):
        if plate in self._in:
            raise ValueError("already parked")
        self._in[plate] = minute

    def leave(self, plate, minute):
        if plate not in self._in:
            raise KeyError(plate)
        start = self._in[plate]
        if minute < start:
            raise ValueError("leaves before arriving")
        hours = math.ceil((minute - start) / 60)
        key = (plate, minute // 1440)
        left = self.cap - self._paid.get(key, 0)
        charge = min(hours * self.rate, left)
        self._paid[key] = self._paid.get(key, 0) + charge
        del self._in[plate]
        return charge

    def is_parked(self, plate):
        return plate in self._in

    def paid_on(self, plate, day):
        return self._paid.get((plate, day), 0)
'''

_PARK_TESTS = '''from parking import ParkingMeter as SUT

ARGS = (@RATE@, @CAP@)


class Ref:
    def __init__(self, rate, cap):
        if rate <= 0 or cap <= 0:
            raise ValueError
        self.rate, self.cap = rate, cap
        self.cars = {}
        self.ledger = []

    def enter(self, plate, minute):
        if plate in self.cars:
            raise ValueError
        self.cars[plate] = minute

    def leave(self, plate, minute):
        if plate not in self.cars:
            raise KeyError(plate)
        if minute < self.cars[plate]:
            raise ValueError
        hours = -(-(minute - self.cars[plate]) // 60)
        day = minute // 1440
        amount = min(hours * self.rate, self.cap - self.paid_on(plate, day))
        self.ledger.append((plate, day, amount))
        del self.cars[plate]
        return amount

    def is_parked(self, plate):
        return plate in self.cars

    def paid_on(self, plate, day):
        return sum(a for p, d, a in self.ledger if p == plate and d == day)


HAND = [
    [o("enter", "A", 0), o("leave", "A", 61), o("leave", "A", 70), o("is_parked", "A")],
    [o("enter", "A", 10), o("leave", "A", 10), o("paid_on", "A", 0)],
    [o("enter", "A", 100), o("enter", "A", 200), o("leave", "A", 50), o("is_parked", "A"), o("leave", "A", 160)],
    [o("enter", "A", 1440), o("leave", "A", 2040), o("enter", "A", 2100), o("leave", "A", 2200),
     o("paid_on", "A", 1), o("paid_on", "A", 0)],
    [o("enter", "A", 0), o("enter", "B", 0), o("leave", "A", 1000), o("leave", "B", 1000),
     o("paid_on", "A", 0), o("paid_on", "B", 0)],
    [o("enter", "A", 1400), o("leave", "A", 1500), o("paid_on", "A", 0), o("paid_on", "A", 1)],
    [o("enter", "A", 0), o("leave", "A", 60), o("enter", "A", 61), o("leave", "A", 121), o("leave", "A", 130)],
    [o("enter", "A", 0), o("leave", "A", 600), o("enter", "A", 700), o("leave", "A", 760), o("paid_on", "A", 0)],
]


def gen(rng):
    ops, t = [], 0
    for _ in range(rng.randint(8, 30)):
        t += rng.choice([0, 5, 30, 59, 60, 61, 200, 700, 1500])
        p = rng.choice("ABC")
        k = rng.random()
        if k < 0.4:
            ops.append(o("enter", p, t))
        elif k < 0.8:
            ops.append(o("leave", p, t - rng.choice([0, 0, 0, 1000])))
        elif k < 0.9:
            ops.append(o("is_parked", p))
        else:
            ops.append(o("paid_on", p, rng.randint(0, 5)))
    return ops
'''

_PARK_PROMPT = """Write `parking.py` with a class `ParkingMeter` for a car park that charges by every started hour, with a daily cap per car. Times are whole minutes counted from the start of day 0, so the day number of a minute is `minute // 1440`.

- `ParkingMeter(rate, daily_cap)`: `rate` is the price per started hour (whole currency units) and `daily_cap` the most one car can be charged in one day. Both must be positive, otherwise `ValueError`.
- `enter(plate, minute)`: the car arrives. `ValueError` if that plate is already parked.
- `leave(plate, minute)`: the car leaves, and the method returns the charge for this stay. Hours are rounded up (61 minutes is 2 hours, 0 minutes is 0 hours) and the stay price is hours times rate. The cap is per plate and per day, where the day is the day the car leaves: all charges for one plate for stays ending in the same day add up to at most `daily_cap`, so the charge returned is the stay price cut down to what is left of that day's cap (never below 0). `KeyError` if the plate is not parked; `ValueError` if `minute` is earlier than its arrival minute (the car stays parked).
- `is_parked(plate)`: `True` or `False`.
- `paid_on(plate, day)`: the total charged to that plate for stays that ended on that day (0 if none).

A call that raises must change nothing."""


def _parking(rng):
    v = dict(RATE=pick(rng, [2, 3, 4]), CAP=pick(rng, [15, 18, 20]))
    return _build(v, module="parking", solution=_PARK_SOL, prompt=_PARK_PROMPT, tests=_PARK_TESTS, wrongs=[
        ("charge = min(hours * self.rate, left)", "charge = min(hours * self.rate, self.cap)"),
        ("hours = math.ceil((minute - start) / 60)", "hours = (minute - start) // 60"),
        ("key = (plate, minute // 1440)", "key = (plate, start // 1440)"),
        ("key = (plate, minute // 1440)", "key = (None, minute // 1440)"),
    ])


# ------------------------------------------------------------------ room calendar

_CAL_SOL = '''class RoomCalendar:
    def __init__(self):
        self._next = 1
        self._items = {}

    def is_free(self, room, start, end):
        if not start < end:
            raise ValueError("empty or reversed range")
        for r, s, e, _ in self._items.values():
            if r == room and s < end and start < e:
                return False
        return True

    def book(self, room, start, end, who):
        if not self.is_free(room, start, end):
            raise ValueError("slot taken")
        bid = self._next
        self._next += 1
        self._items[bid] = (room, start, end, who)
        return bid

    def cancel(self, booking_id):
        if booking_id not in self._items:
            raise KeyError(booking_id)
        return self._items.pop(booking_id)[3]

    def bookings(self, room):
        rows = [[bid, s, e, w] for bid, (r, s, e, w) in self._items.items() if r == room]
        return sorted(rows, key=lambda row: (row[1], row[0]))
'''

_CAL_TESTS = '''from calendar_book import RoomCalendar as SUT

ARGS = ()
ROOMS = ["@R1@", "@R2@"]


class Ref:
    def __init__(self):
        self.n = 0
        self.live = []

    def is_free(self, room, start, end):
        if end <= start:
            raise ValueError
        return all(not (r == room and s < end and start < e) for _, r, s, e, _w in self.live)

    def book(self, room, start, end, who):
        if not self.is_free(room, start, end):
            raise ValueError
        self.n += 1
        self.live.append((self.n, room, start, end, who))
        return self.n

    def cancel(self, bid):
        for i, row in enumerate(self.live):
            if row[0] == bid:
                del self.live[i]
                return row[4]
        raise KeyError(bid)

    def bookings(self, room):
        return [[b, s, e, w] for b, r, s, e, w in sorted(self.live, key=lambda x: (x[2], x[0])) if r == room]


R1, R2 = ROOMS
HAND = [
    [o("book", R1, 9, 10, "ann"), o("book", R1, 10, 11, "bo"), o("book", R1, 9, 11, "cy"), o("bookings", R1)],
    [o("book", R1, 9, 12, "ann"), o("book", R2, 9, 12, "bo"), o("book", R1, 11, 13, "cy"),
     o("cancel", 1), o("book", R1, 11, 13, "cy"), o("bookings", R1), o("bookings", R2)],
    [o("book", R1, 5, 6, "a"), o("cancel", 1), o("cancel", 1), o("book", R1, 5, 6, "b"), o("cancel", 2),
     o("book", R1, 7, 8, "c")],
    [o("book", R1, 5, 5, "a"), o("book", R1, 6, 5, "a"), o("is_free", R1, 3, 3), o("is_free", R1, 1, 9),
     o("book", R1, 1, 9, "a"), o("is_free", R1, 9, 10), o("is_free", R1, 0, 1), o("is_free", R2, 0, 20)],
    [o("book", R1, 10, 12, "late"), o("book", R1, 1, 3, "early"), o("book", R1, 5, 6, "mid"), o("bookings", R1),
     o("cancel", 3), o("bookings", R1), o("cancel", 99)],
    [o("book", R1, 0, 10, "wide"), o("book", R1, 3, 4, "inner"), o("book", R1, -2, 1, "left"),
     o("book", R1, 9, 12, "right"), o("book", R1, 10, 12, "touch"), o("bookings", R1)],
]


def gen(rng):
    ops = []
    for _ in range(rng.randint(8, 30)):
        room = rng.choice(ROOMS)
        s = rng.randint(0, 12)
        e = s + rng.randint(-1, 4)
        k = rng.random()
        if k < 0.5:
            ops.append(o("book", room, s, e, rng.choice(["ann", "bo", "cy"])))
        elif k < 0.7:
            ops.append(o("cancel", rng.randint(1, 8)))
        elif k < 0.85:
            ops.append(o("is_free", room, s, e))
        else:
            ops.append(o("bookings", room))
    return ops
'''

_CAL_PROMPT = """Write `calendar_book.py` with a class `RoomCalendar` that keeps bookings for meeting rooms. A booking covers the half-open range `[start, end)` of whole hours, so one booking may end at the hour the next one starts.

- `RoomCalendar()`: no arguments, starts empty.
- `is_free(room, start, end)`: `True` if no current booking in that room overlaps the range. `ValueError` if `start >= end`.
- `book(room, start, end, who)`: reserve the range for `who` and return the new booking id. Ids are integers counting 1, 2, 3 in the order of successful bookings; an id is never reused, even after a cancellation. `ValueError` for an invalid range or when it overlaps a current booking in the same room (different rooms never conflict).
- `cancel(booking_id)`: remove that booking so its hours are free again, and return the `who` it was booked for. `KeyError` if the id was never issued or was already cancelled.
- `bookings(room)`: the room's current bookings as a list of `[id, start, end, who]` lists, sorted by start hour (ties by id).

A call that raises must change nothing."""


def _calendar(rng):
    v = dict(R1=pick(rng, ["oak", "elm", "ash"]), R2=pick(rng, ["pine", "fir", "yew"]))
    return _build(v, module="calendar_book", solution=_CAL_SOL, prompt=_CAL_PROMPT, tests=_CAL_TESTS, wrongs=[
        ("s < end and start < e", "s <= end and start <= e"),
        ("bid = self._next\n        self._next += 1", "bid = len(self._items) + 1"),
        ("return self._items.pop(booking_id)[3]", "return self._items[booking_id][3]"),
        ("if r == room and s < end and start < e", "if s < end and start < e"),
    ])


# ------------------------------------------------------------------ sliding-window rate limiter

_RATE_SOL = '''class SlidingLimiter:
    def __init__(self, limit, window):
        if limit < 1 or window <= 0:
            raise ValueError("bad limit or window")
        self.limit = limit
        self.window = window
        self._hits = {}
        self._last = {}

    def _live(self, key, t):
        return [h for h in self._hits.get(key, []) if h > t - self.window]

    def allow(self, key, t):
        if t < self._last.get(key, t):
            raise ValueError("time went backwards")
        self._last[key] = t
        live = self._live(key, t)
        self._hits[key] = live
        if len(live) >= self.limit:
            return False
        live.append(t)
        return True

    def remaining(self, key, t):
        if t < self._last.get(key, t):
            raise ValueError("time went backwards")
        return self.limit - len(self._live(key, t))

    def reset(self, key):
        self._hits.pop(key, None)
        self._last.pop(key, None)
'''

_RATE_TESTS = '''from limiter import SlidingLimiter as SUT

ARGS = (@LIMIT@, @WINDOW@)
L, W = @LIMIT@, @WINDOW@


class Ref:
    def __init__(self, limit, window):
        if limit < 1 or window <= 0:
            raise ValueError
        self.limit, self.window = limit, window
        self.accepted = {}
        self.seen = {}

    def _count(self, key, t):
        return sum(1 for h in self.accepted.get(key, []) if t - h < self.window)

    def allow(self, key, t):
        if key in self.seen and t < self.seen[key]:
            raise ValueError
        self.seen[key] = t
        if self._count(key, t) >= self.limit:
            return False
        self.accepted.setdefault(key, []).append(t)
        return True

    def remaining(self, key, t):
        if key in self.seen and t < self.seen[key]:
            raise ValueError
        return self.limit - self._count(key, t)

    def reset(self, key):
        self.accepted.pop(key, None)
        self.seen.pop(key, None)


HAND = [
    [o("allow", "k", i) for i in range(L + 2)] + [o("remaining", "k", L + 1)],
    [o("allow", "k", 0)] * L + [o("allow", "k", W - 1), o("allow", "k", W), o("allow", "k", W + 1)],
    [o("allow", "k", 0)] * L + [o("allow", "k", 1)] * 3 + [o("allow", "k", W), o("allow", "k", W + 1),
                                                          o("remaining", "k", W + 1)],
    [o("allow", "a", 5), o("allow", "b", 3), o("allow", "a", 4), o("remaining", "a", 4), o("remaining", "b", 3)],
    [o("allow", "k", 10), o("remaining", "k", 9), o("remaining", "k", 10), o("remaining", "x", 1)],
    [o("allow", "k", 0)] * (L + 1) + [o("reset", "k"), o("remaining", "k", 0), o("allow", "k", 0)] * 2
    + [o("allow", "k", 1)],
    [o("allow", "k", 20), o("reset", "k"), o("allow", "k", 3), o("allow", "k", 2)],
    [o("allow", "k", 0)] * L + [o("allow", "k", 0), o("allow", "k", W), o("allow", "k", W), o("allow", "k", W)],
    [o("remaining", "k", 3), o("allow", "k", 2), o("remaining", "k", 1)],
]


def gen(rng):
    ops, t = [], 5
    for _ in range(rng.randint(10, 40)):
        t += rng.choice([-3, 0, 0, 1, 1, 2, 3, 5, W // 2, W])
        k = rng.choice("ab")
        r = rng.random()
        if r < 0.65:
            ops.append(o("allow", k, t))
        elif r < 0.88:
            ops.append(o("remaining", k, t))
        else:
            ops.append(o("reset", k))
    return ops
'''

_RATE_PROMPT = """Write `limiter.py` with a class `SlidingLimiter` that limits how often a key (for example a user name) may do something, counting over a sliding window. Times are numbers passed in by the caller (there is no real clock).

- `SlidingLimiter(limit, window)`: at most `limit` requests are allowed per key in any window of `window` time units. `ValueError` if `limit < 1` or `window <= 0`.
- `allow(key, t)`: asks to let a request through at time `t`. A request at time `t` counts against the requests that were *allowed* at times `h` with `h > t - window` (so a request allowed exactly `window` earlier no longer counts). It returns `True` and records the request if fewer than `limit` of those exist, otherwise it returns `False` and records nothing, so refused requests do not use up capacity. Times for one key must not go backwards: if `t` is smaller than the `t` of the latest `allow` call for that key (allowed or refused), raise `ValueError` and change nothing.
- `remaining(key, t)`: how many more requests would be allowed for that key at time `t`, without recording anything. It raises `ValueError` under the same backwards-time rule, comparing with the latest `allow` call for that key (`remaining` itself does not move the key's time).
- `reset(key)`: forgets everything about that key, including its latest time, as if it had never been seen. It does nothing for an unknown key.

Keys are independent of each other."""


def _limiter(rng):
    v = dict(LIMIT=pick(rng, [2, 3, 4]), WINDOW=pick(rng, [8, 10, 15]))
    return _build(v, module="limiter", solution=_RATE_SOL, prompt=_RATE_PROMPT, tests=_RATE_TESTS, wrongs=[
        ("if h > t - self.window]", "if h >= t - self.window]"),
        ("        if len(live) >= self.limit:\n            return False\n        live.append(t)\n        return True",
         "        live.append(t)\n        return len(live) <= self.limit"),
        ("        self._last.pop(key, None)", "        pass"),
        ("    def remaining(self, key, t):\n        if t < self._last.get(key, t):\n            raise ValueError(\"time went backwards\")\n",
         "    def remaining(self, key, t):\n        if t < self._last.get(key, t):\n            raise ValueError(\"time went backwards\")\n        self._last[key] = t\n"),
    ])


# ------------------------------------------------------------------ stock ledger with expiring reservations

_LEDGER_SOL = '''class Ledger:
    def __init__(self):
        self._stock = {}
        self._res = {}
        self._next = 1

    def receive(self, sku, qty):
        if qty <= 0:
            raise ValueError("quantity must be positive")
        self._stock[sku] = self._stock.get(sku, 0) + qty

    def available(self, sku, t):
        held = sum(q for s, q, exp in self._res.values() if s == sku and t < exp)
        return self._stock.get(sku, 0) - held

    def reserve(self, sku, qty, t, ttl):
        if qty <= 0 or ttl <= 0:
            raise ValueError("quantity and ttl must be positive")
        if qty > self.available(sku, t):
            raise ValueError("not enough available")
        rid = self._next
        self._next += 1
        self._res[rid] = (sku, qty, t + ttl)
        return rid

    def commit(self, rid, t):
        if rid not in self._res:
            raise KeyError(rid)
        sku, qty, exp = self._res[rid]
        if t >= exp:
            raise ValueError("reservation expired")
        del self._res[rid]
        self._stock[sku] -= qty
        return qty

    def release(self, rid):
        if rid not in self._res:
            raise KeyError(rid)
        return self._res.pop(rid)[1]
'''

_LEDGER_TESTS = '''from ledger import Ledger as SUT

ARGS = ()
A, B = "@SKU1@", "@SKU2@"


class Ref:
    def __init__(self):
        self.on_hand = {}
        self.holds = {}
        self.count = 0

    def receive(self, sku, qty):
        if qty <= 0:
            raise ValueError
        self.on_hand[sku] = self.on_hand.get(sku, 0) + qty

    def available(self, sku, t):
        active = [h for h in self.holds.values() if h["sku"] == sku and h["until"] > t]
        return self.on_hand.get(sku, 0) - sum(h["qty"] for h in active)

    def reserve(self, sku, qty, t, ttl):
        if qty <= 0 or ttl <= 0:
            raise ValueError
        if self.available(sku, t) < qty:
            raise ValueError
        self.count += 1
        self.holds[self.count] = {"sku": sku, "qty": qty, "until": t + ttl}
        return self.count

    def commit(self, rid, t):
        if rid not in self.holds:
            raise KeyError(rid)
        h = self.holds[rid]
        if h["until"] <= t:
            raise ValueError
        self.on_hand[h["sku"]] -= h["qty"]
        del self.holds[rid]
        return h["qty"]

    def release(self, rid):
        if rid not in self.holds:
            raise KeyError(rid)
        return self.holds.pop(rid)["qty"]


HAND = [
    [o("receive", A, 5), o("reserve", A, 3, 0, 10), o("available", A, 5), o("reserve", A, 3, 5, 10),
     o("reserve", A, 2, 5, 10), o("available", A, 5)],
    [o("receive", A, 5), o("reserve", A, 5, 0, 4), o("available", A, 3), o("available", A, 4),
     o("reserve", A, 5, 4, 4), o("commit", 1, 4), o("commit", 2, 5), o("available", A, 5)],
    [o("receive", A, 4), o("reserve", A, 4, 0, 10), o("commit", 1, 9), o("available", A, 9), o("available", A, 11),
     o("commit", 1, 9), o("release", 1), o("receive", A, 1), o("reserve", A, 2, 9, 1)],
    [o("receive", A, 4), o("reserve", A, 3, 0, 5), o("release", 1), o("release", 1), o("available", A, 1),
     o("reserve", A, 4, 1, 5), o("commit", 2, 2), o("available", A, 2)],
    [o("receive", A, 3), o("receive", B, 3), o("reserve", A, 3, 0, 9), o("reserve", B, 3, 0, 9),
     o("available", A, 1), o("available", B, 1), o("commit", 2, 1), o("available", B, 1), o("available", A, 1)],
    [o("receive", A, 0), o("receive", A, -1), o("reserve", A, 0, 0, 5), o("reserve", A, 1, 0, 0),
     o("reserve", A, 1, 0, 5), o("commit", 7, 0), o("release", 7), o("available", "zzz", 0)],
    [o("receive", A, 2), o("reserve", A, 1, 0, 3), o("commit", 1, 3), o("release", 1), o("available", A, 3),
     o("reserve", A, 2, 3, 3), o("commit", 2, 5), o("available", A, 5)],
]


def gen(rng):
    ops, t = [], 0
    for _ in range(rng.randint(10, 35)):
        t += rng.choice([0, 0, 1, 1, 2, 3])
        sku = rng.choice([A, B])
        k = rng.random()
        if k < 0.2:
            ops.append(o("receive", sku, rng.randint(0, 6)))
        elif k < 0.5:
            ops.append(o("reserve", sku, rng.randint(0, 5), t, rng.randint(0, 6)))
        elif k < 0.65:
            ops.append(o("available", sku, t))
        elif k < 0.85:
            ops.append(o("commit", rng.randint(1, 6), t))
        else:
            ops.append(o("release", rng.randint(1, 6)))
    return ops
'''

_LEDGER_PROMPT = """Write `ledger.py` with a class `Ledger` that tracks stock per SKU and temporary reservations on it. Times are numbers passed in by the caller (there is no real clock).

- `Ledger()`: starts with no stock and no reservations.
- `receive(sku, qty)`: adds `qty` to the stock on hand. `ValueError` if `qty <= 0`.
- `available(sku, t)`: stock on hand minus the quantities of reservations for that SKU that are *active* at time `t` (0 for an unknown SKU). A reservation made at time `t0` with a `ttl` is active while `t < t0 + ttl`; once `t` reaches `t0 + ttl` it no longer holds anything back, but it still exists until committed or released.
- `reserve(sku, qty, t, ttl)`: holds `qty` units from time `t` for `ttl` time units and returns a reservation id. Ids are integers counting 1, 2, 3 in order of successful reservations. `ValueError` if `qty <= 0` or `ttl <= 0`, or if `qty` is more than `available(sku, t)`.
- `commit(rid, t)`: turns an active reservation into a sale: removes its quantity from the stock on hand, deletes the reservation, and returns the quantity. `KeyError` if no such reservation exists (never made, already committed or released); `ValueError` if it exists but is no longer active at `t` (it is left as it was).
- `release(rid)`: deletes a reservation without selling (whether active or expired) and returns its quantity. `KeyError` if it does not exist.

A call that raises must change nothing."""


def _ledger(rng):
    v = dict(SKU1=pick(rng, ["bolt", "nut", "pin"]), SKU2=pick(rng, ["gear", "belt", "cog"]))
    return _build(v, module="ledger", solution=_LEDGER_SOL, prompt=_LEDGER_PROMPT, tests=_LEDGER_TESTS, wrongs=[
        ("if s == sku and t < exp)", "if s == sku)"),
        ("        self._stock[sku] -= qty\n", ""),
        ("if t >= exp:\n            raise ValueError(\"reservation expired\")", "if t > exp:\n            raise ValueError(\"reservation expired\")"),
        ("if qty > self.available(sku, t):", "if qty > self._stock.get(sku, 0):"),
    ])


# ------------------------------------------------------------------ points wallet

_WALLET_SOL = '''class PointsWallet:
    def __init__(self):
        self._lots = []
        self._seq = 0

    def earn(self, points, t, ttl):
        if points <= 0 or ttl <= 0:
            raise ValueError("points and ttl must be positive")
        self._seq += 1
        self._lots.append([t + ttl, self._seq, points])

    def balance(self, t):
        return sum(lot[2] for lot in self._lots if lot[0] > t)

    def spend(self, points, t):
        if points <= 0:
            raise ValueError("points must be positive")
        if points > self.balance(t):
            raise ValueError("not enough points")
        need = points
        used = []
        for lot in sorted(l for l in self._lots if l[0] > t):
            take = min(need, lot[2])
            lot[2] -= take
            need -= take
            used.append([lot[0], take])
            if need == 0:
                break
        self._lots = [l for l in self._lots if l[2] > 0]
        return used

    def expiring(self, t, within):
        return sum(lot[2] for lot in self._lots if t < lot[0] <= t + within)
'''

_WALLET_TESTS = '''from wallet import PointsWallet as SUT

ARGS = ()


class Ref:
    def __init__(self):
        self.lots = []
        self.n = 0

    def earn(self, points, t, ttl):
        if points <= 0 or ttl <= 0:
            raise ValueError
        self.n += 1
        self.lots.append({"exp": t + ttl, "n": self.n, "left": points})

    def _alive(self, t):
        return sorted((l for l in self.lots if l["exp"] > t), key=lambda l: (l["exp"], l["n"]))

    def balance(self, t):
        return sum(l["left"] for l in self._alive(t))

    def spend(self, points, t):
        if points <= 0:
            raise ValueError
        if self.balance(t) < points:
            raise ValueError
        out, need = [], points
        for l in self._alive(t):
            if need <= 0:
                break
            take = min(need, l["left"])
            l["left"] -= take
            need -= take
            out.append([l["exp"], take])
        self.lots = [l for l in self.lots if l["left"] > 0]
        return out

    def expiring(self, t, within):
        return sum(l["left"] for l in self._alive(t) if l["exp"] <= t + within)


HAND = [
    [o("earn", 10, 0, 100), o("earn", 5, 10, 20), o("spend", 12, 11), o("balance", 11)],
    [o("earn", 10, 0, 100), o("earn", 5, 10, 20), o("spend", 4, 12), o("spend", 3, 12), o("balance", 12)],
    [o("earn", 5, 0, 10), o("earn", 5, 0, 10), o("earn", 3, 1, 9), o("spend", 6, 2), o("spend", 6, 2),
     o("balance", 2)],
    [o("earn", 8, 0, 5), o("earn", 8, 1, 50), o("balance", 4), o("balance", 5), o("spend", 9, 5), o("spend", 8, 5),
     o("balance", 5)],
    [o("earn", 8, 0, 5), o("earn", 2, 0, 9), o("expiring", 0, 5), o("expiring", 0, 4), o("expiring", 5, 4),
     o("expiring", 0, 100), o("expiring", 9, 5)],
    [o("earn", 4, 0, 6), o("spend", 5, 1), o("balance", 1), o("spend", 4, 1), o("spend", 1, 1), o("balance", 1)],
    [o("earn", 0, 0, 5), o("earn", 3, 0, 0), o("spend", 0, 0), o("spend", -1, 0), o("balance", 0)],
    [o("earn", 6, 0, 3), o("earn", 7, 1, 1), o("earn", 7, 1, 2), o("spend", 13, 2), o("balance", 2),
     o("spend", 7, 2), o("balance", 3)],
]


def gen(rng):
    ops, t = [], 0
    for _ in range(rng.randint(10, 35)):
        t += rng.choice([0, 0, 1, 2, 3])
        k = rng.random()
        if k < 0.35:
            ops.append(o("earn", rng.randint(0, 6), t, rng.randint(0, 8)))
        elif k < 0.65:
            ops.append(o("spend", rng.randint(0, 9), t))
        elif k < 0.85:
            ops.append(o("balance", t))
        else:
            ops.append(o("expiring", t, rng.randint(0, 6)))
    return ops
'''

_WALLET_PROMPT = """Write `wallet.py` with a class `PointsWallet` for loyalty points that expire. Times are numbers passed in by the caller (there is no real clock).

- `PointsWallet()`: starts empty.
- `earn(points, t, ttl)`: adds a batch of `points` earned at time `t` that expires at time `t + ttl`. A batch is usable while `now < t + ttl`; at `now == t + ttl` it is already gone. `ValueError` if `points <= 0` or `ttl <= 0`.
- `balance(t)`: the total of the points that are not expired at time `t`.
- `spend(points, t)`: spends points from the batches that are not expired at `t`, taking from the batch that expires soonest first (when two batches expire at the same time, the one earned first goes first), using up one batch before touching the next. It returns a list of `[expiry_time, points_taken]` pairs, one per batch touched, in the order they were used. If `points` is more than `balance(t)`, raise `ValueError` and leave every batch untouched. `ValueError` also if `points <= 0`. Spent points are gone for good.
- `expiring(t, within)`: the total of the unexpired points (at `t`) that will expire at some time `x` with `t < x <= t + within`.

A call that raises must change nothing."""


def _wallet(rng):
    v = {}
    return _build(v, module="wallet", solution=_WALLET_SOL, prompt=_WALLET_PROMPT, tests=_WALLET_TESTS, wrongs=[
        ("for lot in sorted(l for l in self._lots if l[0] > t):", "for lot in sorted((l for l in self._lots if l[0] > t), key=lambda l: l[1]):"),
        ("return sum(lot[2] for lot in self._lots if lot[0] > t)", "return sum(lot[2] for lot in self._lots)"),
        ("t < lot[0] <= t + within", "t < lot[0] < t + within"),
        ("for lot in sorted(l for l in self._lots if l[0] > t):", "for lot in sorted(self._lots):"),
    ])


# ------------------------------------------------------------------ seat map

_SEAT_SOL = '''class SeatMap:
    def __init__(self, rows, per_row):
        if rows < 1 or per_row < 1:
            raise ValueError("bad size")
        self.rows = rows
        self.per_row = per_row
        self._owner = {}

    def _label(self, r, c):
        return chr(65 + r) + str(c + 1)

    def allocate(self, n, name):
        if n < 1 or n > self.per_row:
            raise ValueError("bad group size")
        for r in range(self.rows):
            run = 0
            for c in range(self.per_row):
                run = run + 1 if (r, c) not in self._owner else 0
                if run == n:
                    cells = [(r, k) for k in range(c - n + 1, c + 1)]
                    for cell in cells:
                        self._owner[cell] = name
                    return [self._label(*cell) for cell in cells]
        raise LookupError("no block of that size")

    def release(self, name):
        mine = [cell for cell, who in self._owner.items() if who == name]
        if not mine:
            raise KeyError(name)
        for cell in mine:
            del self._owner[cell]
        return len(mine)

    def free_count(self):
        return self.rows * self.per_row - len(self._owner)

    def seats_of(self, name):
        return [self._label(*cell) for cell in sorted(c for c, who in self._owner.items() if who == name)]
'''

_SEAT_TESTS = '''from seatmap import SeatMap as SUT

ARGS = (@ROWS@, @PER@)
PER = @PER@


class Ref:
    def __init__(self, rows, per_row):
        if rows < 1 or per_row < 1:
            raise ValueError
        self.rows, self.per = rows, per_row
        self.grid = [[None] * per_row for _ in range(rows)]

    def allocate(self, n, name):
        if not 1 <= n <= self.per:
            raise ValueError
        for r, row in enumerate(self.grid):
            for c in range(self.per - n + 1):
                if all(row[c + k] is None for k in range(n)):
                    for k in range(n):
                        row[c + k] = name
                    return ["%s%d" % (chr(65 + r), c + k + 1) for k in range(n)]
        raise LookupError

    def release(self, name):
        count = 0
        for row in self.grid:
            for c, who in enumerate(row):
                if who == name:
                    row[c] = None
                    count += 1
        if not count:
            raise KeyError(name)
        return count

    def free_count(self):
        return sum(1 for row in self.grid for x in row if x is None)

    def seats_of(self, name):
        return ["%s%d" % (chr(65 + r), c + 1) for r, row in enumerate(self.grid) for c, x in enumerate(row) if x == name]


HAND = [
    [o("allocate", 2, "ann"), o("allocate", 3, "bo"), o("seats_of", "ann"), o("seats_of", "bo"), o("free_count")],
    [o("allocate", 1, "a"), o("allocate", 1, "b"), o("allocate", 1, "a"), o("release", "a"), o("allocate", 2, "c"),
     o("seats_of", "c"), o("allocate", 1, "d"), o("seats_of", "d")],
    [o("allocate", PER, "full"), o("allocate", PER, "full2"), o("allocate", PER, "full3"), o("allocate", PER, "x"),
     o("allocate", 1, "y"), o("release", "full2"), o("allocate", PER, "z"), o("seats_of", "z")],
    [o("allocate", 0, "a"), o("allocate", PER + 1, "a"), o("release", "nobody"), o("seats_of", "nobody"),
     o("free_count")],
    [o("allocate", 2, "a"), o("allocate", 1, "b"), o("allocate", 2, "c"), o("release", "b"), o("allocate", 3, "d"),
     o("seats_of", "d"), o("allocate", 1, "e"), o("seats_of", "e"), o("seats_of", "c")],
    [o("allocate", PER - 1, "a"), o("allocate", 2, "b"), o("seats_of", "b"), o("allocate", 1, "c"),
     o("seats_of", "c"), o("release", "a"), o("allocate", 2, "d"), o("seats_of", "d")],
    [o("allocate", 1, "a"), o("allocate", 2, "a"), o("seats_of", "a"), o("release", "a"), o("release", "a"),
     o("free_count"), o("seats_of", "a")],
]


def gen(rng):
    ops = []
    for _ in range(rng.randint(10, 40)):
        k = rng.random()
        name = rng.choice("abcd")
        if k < 0.5:
            n = rng.choice([1, 1, 2, 2, 3, 4, PER - 1, PER, 0, PER + 1])
            ops.append(o("allocate", n, name))
        elif k < 0.7:
            ops.append(o("release", name))
        elif k < 0.85:
            ops.append(o("seats_of", name))
        else:
            ops.append(o("free_count"))
    return ops
'''

_SEAT_PROMPT = """Write `seatmap.py` with a class `SeatMap` that hands out seats in a small theatre. Rows are lettered from `A`, seats are numbered from 1 in each row, so the labels are `A1`, `A2`, ... `B1`, ...

- `SeatMap(rows, per_row)`: a hall with `rows` rows of `per_row` seats, all free. `ValueError` if either is below 1.
- `allocate(n, name)`: seats a group of `n` people under `name` in `n` seats that are free and next to each other in one row. It uses the first row (A before B) that has such a block, and in that row the block that starts at the lowest seat number. It returns the labels as a list in seat order, for example `["B3", "B4"]`. A name may be allocated several times and then owns all of those seats. `ValueError` if `n < 1` or `n` is more than `per_row`; `LookupError` if no row has a free block of `n` (nothing changes).
- `release(name)`: frees every seat owned by that name, and returns how many seats were freed. `KeyError` if the name owns no seats.
- `free_count()`: how many seats are free.
- `seats_of(name)`: the labels of the seats the name owns, in row-then-seat order (an empty list if none)."""


def _seatmap(rng):
    v = dict(ROWS=pick(rng, [3, 4]), PER=pick(rng, [5, 6, 8]))
    return _build(v, module="seatmap", solution=_SEAT_SOL, prompt=_SEAT_PROMPT, tests=_SEAT_TESTS, wrongs=[
        ("run = run + 1 if (r, c) not in self._owner else 0", "run = run + 1 if (r, c) not in self._owner else run"),
        ("        for cell in mine:\n            del self._owner[cell]\n        return len(mine)",
         "        del self._owner[mine[0]]\n        return len(mine)"),
        ("if n < 1 or n > self.per_row:", "if n < 1 or n >= self.per_row:"),
        ("for r in range(self.rows):\n            run = 0", "for r in reversed(range(self.rows)):\n            run = 0"),
    ])


# ------------------------------------------------------------------ document history

_DOC_SOL = '''class History:
    def __init__(self, text="", limit=@LIMIT@):
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._text = text
        self._limit = limit
        self._undo = []
        self._redo = []

    def edit(self, text):
        if text == self._text:
            return False
        self._undo.append(self._text)
        if len(self._undo) > self._limit:
            self._undo.pop(0)
        self._text = text
        self._redo.clear()
        return True

    def undo(self):
        if not self._undo:
            raise IndexError("nothing to undo")
        self._redo.append(self._text)
        self._text = self._undo.pop()
        return self._text

    def redo(self):
        if not self._redo:
            raise IndexError("nothing to redo")
        self._undo.append(self._text)
        self._text = self._redo.pop()
        return self._text

    def state(self):
        return [self._text, len(self._undo), len(self._redo)]
'''

_DOC_TESTS = '''from history import History as SUT

ARGS = ("start", @LIMIT@)
LIM = @LIMIT@


class Ref:
    def __init__(self, text="", limit=@LIMIT@):
        if limit < 1:
            raise ValueError
        self.limit = limit
        self.cur = text
        self.past = []
        self.future = []

    def edit(self, text):
        if text == self.cur:
            return False
        self.past = (self.past + [self.cur])[-self.limit:]
        self.cur = text
        self.future = []
        return True

    def undo(self):
        if not self.past:
            raise IndexError
        self.future.append(self.cur)
        self.cur = self.past.pop()
        return self.cur

    def redo(self):
        if not self.future:
            raise IndexError
        self.past.append(self.cur)
        self.cur = self.future.pop()
        return self.cur

    def state(self):
        return [self.cur, len(self.past), len(self.future)]


HAND = [
    [o("edit", "a"), o("edit", "b"), o("undo"), o("undo"), o("redo"), o("redo"), o("redo"), o("state")],
    [o("edit", "a"), o("edit", "b"), o("undo"), o("edit", "c"), o("redo"), o("undo"), o("undo"), o("state")],
    [o("edit", "start"), o("state"), o("undo"), o("edit", "a"), o("edit", "a"), o("undo"), o("undo")],
    [o("edit", str(i)) for i in range(LIM + 3)] + [o("undo")] * (LIM + 2) + [o("state")],
    [o("edit", str(i)) for i in range(LIM + 2)] + [o("undo")] * LIM + [o("redo")] * (LIM + 1) + [o("state")],
    [o("undo"), o("redo"), o("state"), o("edit", "x"), o("undo"), o("edit", "x"), o("redo"), o("state")],
    [o("edit", "a"), o("undo"), o("edit", "start"), o("redo"), o("state")],
    [o("edit", "a"), o("edit", "b"), o("undo"), o("undo"), o("edit", "b"), o("redo"), o("undo"), o("state")],
]


def test_constructor():
    for bad in (0, -1):
        try:
            SUT("x", bad)
        except ValueError:
            continue
        raise AssertionError("limit %r accepted" % bad)
    assert _run(lambda: SUT(), [o("state")]) == _run(lambda: Ref(), [o("state")])


def gen(rng):
    return [_doc_op(rng) for _ in range(rng.randint(8, 30))]


def _doc_op(rng):
    k = rng.random()
    if k < 0.45:
        return o("edit", rng.choice(["a", "b", "c", "start"]))
    if k < 0.65:
        return o("undo")
    if k < 0.8:
        return o("redo")
    return o("state")
'''

_DOC_PROMPT = """Write `history.py` with a class `History` that tracks the text of a document with undo and redo.

- `History(text="", limit=@LIMIT@)`: the document starts as `text`. At most `limit` undo steps are kept: when an edit would make more, the oldest step is forgotten for good. `ValueError` if `limit < 1`.
- `edit(new_text)`: replaces the text. If `new_text` equals the current text nothing happens and it returns `False`. Otherwise the old text becomes an undo step, anything that could have been redone is discarded, and it returns `True`.
- `undo()`: goes back to the text before the latest step, makes the text it left redoable, and returns the text now current. `IndexError` if there is nothing to undo.
- `redo()`: reapplies the most recently undone step and returns the text now current. `IndexError` if there is nothing to redo.
- `state()`: returns `[current_text, number_of_undo_steps, number_of_redo_steps]`.

A call that raises must change nothing."""


def _history(rng):
    v = dict(LIMIT=pick(rng, [3, 4]))
    # test_constructor, by hand: each bad limit must raise ValueError (assertion 0), and the defaults give
    # the solution's state (assertion 1).
    ctor = [{"init": ["x", bad], "steps": [], "raises": "ValueError", "test": "test_constructor", "covers": [0]}
            for bad in (0, -1)]
    ctor.append({"init": [], "steps": [["state", []]], "test": "test_constructor", "covers": [1]})
    return _build(v, module="history", solution=_DOC_SOL, prompt=_DOC_PROMPT, tests=_DOC_TESTS, extra=ctor, wrongs=[
        ("        self._redo.clear()\n        return True", "        return True"),
        ("        if text == self._text:\n            return False\n", ""),
        ("        if len(self._undo) > self._limit:\n            self._undo.pop(0)\n", ""),
        ("self._redo.append(self._text)\n        self._text = self._undo.pop()", "self._text = self._undo.pop()"),
    ])


# ------------------------------------------------------------------ league standings

_TABLE_SOL = '''class Standings:
    def __init__(self):
        self._matches = []

    def record(self, home, away, home_goals, away_goals):
        if home == away:
            raise ValueError("a team cannot play itself")
        if home_goals < 0 or away_goals < 0:
            raise ValueError("negative goals")
        self._matches.append([home, away, home_goals, away_goals])

    def undo_last(self):
        if not self._matches:
            raise IndexError("no matches")
        return self._matches.pop()

    def history(self):
        return [list(m) for m in self._matches]

    def table(self):
        rows = {}
        for home, away, hg, ag in self._matches:
            for team, gf, ga in ((home, hg, ag), (away, ag, hg)):
                row = rows.setdefault(team, [team, 0, 0, 0, 0])
                row[1] += 1
                row[2] += 3 if gf > ga else 1 if gf == ga else 0
                row[3] += gf - ga
                row[4] += gf
        return sorted(rows.values(), key=lambda r: (-r[2], -r[3], -r[4], r[0]))
'''

_TABLE_TESTS = '''from standings import Standings as SUT

ARGS = ()
T = @TEAMS@


class Ref:
    def __init__(self):
        self.log = []

    def record(self, home, away, hg, ag):
        if home == away or hg < 0 or ag < 0:
            raise ValueError
        self.log.append([home, away, hg, ag])

    def undo_last(self):
        if not self.log:
            raise IndexError
        return self.log.pop()

    def history(self):
        return [m[:] for m in self.log]

    def table(self):
        names = sorted({m[0] for m in self.log} | {m[1] for m in self.log})
        out = []
        for n in names:
            played = pts = gf = ga = 0
            for h, a, x, y in self.log:
                if n not in (h, a):
                    continue
                mine, theirs = (x, y) if n == h else (y, x)
                played += 1
                gf += mine
                ga += theirs
                pts += 3 if mine > theirs else (1 if mine == theirs else 0)
            out.append([n, played, pts, gf - ga, gf])
        return sorted(out, key=lambda r: (-r[2], -r[3], -r[4], r[0]))


A, B, C, D = T
HAND = [
    [o("record", A, B, 2, 1), o("record", C, D, 0, 0), o("table")],
    [o("record", A, B, 1, 0), o("record", C, D, 3, 2), o("table")],
    [o("record", A, B, 3, 0), o("record", C, D, 5, 2), o("table")],
    [o("record", A, B, 4, 1), o("record", C, D, 3, 0), o("table")],
    [o("record", A, B, 2, 2), o("record", C, D, 2, 2), o("table")],
    [o("record", B, A, 1, 1), o("record", D, C, 1, 1), o("record", A, C, 1, 0), o("record", B, D, 0, 1),
     o("table")],
    [o("record", A, B, 1, 0), o("record", C, D, 1, 0), o("undo_last"), o("table"), o("undo_last"), o("table"),
     o("undo_last"), o("history")],
    [o("record", A, B, 1, 0), o("record", B, C, 2, 0), o("record", C, A, 0, 0), o("undo_last"), o("history"),
     o("table"), o("record", C, A, 5, 0), o("table")],
    [o("record", A, A, 1, 1), o("record", A, B, -1, 0), o("record", A, B, 0, -2), o("table"), o("history")],
    [o("table"), o("history"), o("undo_last")],
]


def gen(rng):
    ops = []
    for _ in range(rng.randint(6, 25)):
        k = rng.random()
        if k < 0.6:
            h, a = rng.choice(T), rng.choice(T)
            ops.append(o("record", h, a, rng.choice([0, 0, 1, 1, 2, 3]), rng.choice([0, 0, 1, 1, 2, 3])))
        elif k < 0.75:
            ops.append(o("undo_last"))
        elif k < 0.95:
            ops.append(o("table"))
        else:
            ops.append(o("history"))
    return ops
'''

_TABLE_PROMPT = """Write `standings.py` with a class `Standings` for a small football league table.

- `Standings()`: no matches yet.
- `record(home, away, home_goals, away_goals)`: records a finished match between two team names. `ValueError` if the teams are the same or either goal count is negative.
- `undo_last()`: takes back the most recently recorded match (as if it had never been played, so a team that has no other matches disappears from the table) and returns it as `[home, away, home_goals, away_goals]`. `IndexError` if there are no matches.
- `history()`: all recorded matches, oldest first, each as `[home, away, home_goals, away_goals]`.
- `table()`: one row `[team, played, points, goal_difference, goals_for]` for every team that has at least one match. A win is worth 3 points, a draw 1 and a loss 0. Goal difference is goals scored minus goals conceded over all of the team's matches. Rows are ordered by points (highest first); teams with equal points by goal difference (highest first); then by goals scored (highest first); then by team name in alphabetical order."""


def _standings(rng):
    names = pick(rng, [["Reds", "Blues", "Greens", "Golds"], ["Hawks", "Otters", "Lynx", "Moles"],
                       ["Pines", "Cedars", "Elms", "Firs"]])
    v = dict(TEAMS=repr(names))
    return _build(v, module="standings", solution=_TABLE_SOL, prompt=_TABLE_PROMPT, tests=_TABLE_TESTS, wrongs=[
        ("key=lambda r: (-r[2], -r[3], -r[4], r[0])", "key=lambda r: (-r[2], -r[4], -r[3], r[0])"),
        ("key=lambda r: (-r[2], -r[3], -r[4], r[0])", "key=lambda r: (-r[2], -r[3], -r[4])"),
        ("return self._matches.pop()", "return self._matches.pop(0)"),
        ("row[2] += 3 if gf > ga else 1 if gf == ga else 0", "row[2] += 3 if gf > ga else 1 if gf == ga and home == team else 0"),
    ])


# ------------------------------------------------------------------ library hold desk

_HOLD_SOL = '''class HoldDesk:
    def __init__(self, titles, pickup_days):
        if pickup_days < 1:
            raise ValueError("pickup window must be at least 1")
        self._pickup = pickup_days
        self._shelf = {t: True for t in titles}
        self._queue = {t: [] for t in titles}
        self._free_since = {t: 0 for t in titles}

    def _settle(self, title, now):
        q = self._queue[title]
        while q and self._shelf[title]:
            if q[0][1] is None:
                q[0][1] = self._free_since[title]
            if now >= q[0][1] + self._pickup:
                self._free_since[title] = q[0][1] + self._pickup
                q.pop(0)
                continue
            break

    def place_hold(self, title, member, now):
        if title not in self._queue:
            raise KeyError(title)
        self._settle(title, now)
        q = self._queue[title]
        if any(m == member for m, _ in q):
            raise ValueError("already holding")
        if not q:
            self._free_since[title] = now
        q.append([member, None])
        self._settle(title, now)
        return len(q)

    def pickup(self, title, member, now):
        if title not in self._queue:
            raise KeyError(title)
        self._settle(title, now)
        q = self._queue[title]
        if q and self._shelf[title] and q[0][0] == member:
            q.pop(0)
            self._shelf[title] = False
            return True
        return False

    def return_copy(self, title, now):
        if title not in self._queue:
            raise KeyError(title)
        if self._shelf[title]:
            raise ValueError("copy is not out")
        self._shelf[title] = True
        self._free_since[title] = now
        self._settle(title, now)

    def queue(self, title, now):
        if title not in self._queue:
            raise KeyError(title)
        self._settle(title, now)
        return [m for m, _ in self._queue[title]]
'''

_HOLD_TESTS = '''from holddesk import HoldDesk as SUT

ARGS = (@TITLES@, @DAYS@)
TITLES = @TITLES@
P = @DAYS@


class Ref:
    def __init__(self, titles, pickup_days):
        if pickup_days < 1:
            raise ValueError
        self.p = pickup_days
        self.copy_in = {t: True for t in titles}
        self.line = {t: [] for t in titles}       # members in order
        self.offered = {t: None for t in titles}  # time the front member was offered the copy
        self.idle_from = {t: 0 for t in titles}

    def _advance(self, title, now):
        while self.line[title] and self.copy_in[title]:
            if self.offered[title] is None:
                self.offered[title] = self.idle_from[title]
            deadline = self.offered[title] + self.p
            if now < deadline:
                return
            self.line[title].pop(0)
            self.idle_from[title] = deadline
            self.offered[title] = None

    def place_hold(self, title, member, now):
        if title not in self.line:
            raise KeyError(title)
        self._advance(title, now)
        if member in self.line[title]:
            raise ValueError
        if not self.line[title]:
            self.idle_from[title] = now
            self.offered[title] = None
        self.line[title].append(member)
        self._advance(title, now)
        return len(self.line[title])

    def pickup(self, title, member, now):
        if title not in self.line:
            raise KeyError(title)
        self._advance(title, now)
        if self.copy_in[title] and self.line[title] and self.line[title][0] == member:
            self.line[title].pop(0)
            self.offered[title] = None
            self.copy_in[title] = False
            return True
        return False

    def return_copy(self, title, now):
        if title not in self.line:
            raise KeyError(title)
        if self.copy_in[title]:
            raise ValueError
        self.copy_in[title] = True
        self.idle_from[title] = now
        self.offered[title] = None
        self._advance(title, now)

    def queue(self, title, now):
        if title not in self.line:
            raise KeyError(title)
        self._advance(title, now)
        return list(self.line[title])


T0 = TITLES[0]
HAND = [
    [o("place_hold", T0, "ann", 0), o("place_hold", T0, "bo", 1), o("queue", T0, 2), o("pickup", T0, "bo", 2),
     o("pickup", T0, "ann", 2), o("queue", T0, 2), o("pickup", T0, "bo", 3)],
    [o("place_hold", T0, "ann", 0), o("place_hold", T0, "bo", 0), o("queue", T0, P - 1), o("queue", T0, P),
     o("pickup", T0, "ann", P), o("pickup", T0, "bo", P), o("queue", T0, P)],
    [o("place_hold", T0, "ann", 0), o("place_hold", T0, "bo", 0), o("place_hold", T0, "cy", 0),
     o("queue", T0, 3 * P - 1), o("pickup", T0, "cy", 3 * P - 1), o("pickup", T0, "bo", 2 * P),
     o("queue", T0, 3 * P)],
    [o("place_hold", T0, "ann", 0), o("place_hold", T0, "bo", 0), o("queue", T0, 5 * P),
     o("place_hold", T0, "ann", 5 * P), o("queue", T0, 5 * P)],
    [o("place_hold", T0, "ann", 0), o("place_hold", T0, "ann", 1), o("place_hold", T0, "bo", 1),
     o("pickup", T0, "ann", 1), o("place_hold", T0, "cy", 2), o("return_copy", T0, 3), o("return_copy", T0, 3),
     o("queue", T0, 3), o("pickup", T0, "bo", 3), o("pickup", T0, "bo", 3)],
    [o("place_hold", T0, "ann", 0), o("pickup", T0, "ann", 1), o("place_hold", T0, "bo", 2),
     o("place_hold", T0, "cy", 2), o("return_copy", T0, 10), o("queue", T0, 10), o("queue", T0, 10 + P),
     o("pickup", T0, "cy", 10 + P), o("return_copy", T0, 20), o("queue", T0, 20)],
    [o("place_hold", "nope", "ann", 0), o("pickup", "nope", "ann", 0), o("return_copy", T0, 0),
     o("queue", "nope", 0), o("pickup", T0, "ann", 0)],
    [o("place_hold", T0, "ann", 4), o("queue", T0, 4 + P), o("place_hold", T0, "bo", 4 + P),
     o("pickup", T0, "bo", 4 + P), o("return_copy", T0, 20), o("place_hold", T0, "ann", 21), o("queue", T0, 21)],
]


def gen(rng):
    ops, t = [], 0
    for _ in range(rng.randint(10, 40)):
        t += rng.choice([0, 0, 1, 1, 2, 3, 5])
        title = rng.choice(TITLES + ["zzz"] * (1 if rng.random() < 0.1 else 0))
        m = rng.choice("abcd")
        k = rng.random()
        if k < 0.35:
            ops.append(o("place_hold", title, m, t))
        elif k < 0.6:
            ops.append(o("pickup", title, m, t))
        elif k < 0.8:
            ops.append(o("return_copy", title, t))
        else:
            ops.append(o("queue", title, t))
    return ops
'''

_HOLD_PROMPT = """Write `holddesk.py` with a class `HoldDesk` for a library that has exactly one copy of each title and lets members queue for it. Times are whole days passed in by the caller (there is no real clock) and never go backwards from one call to the next.

- `HoldDesk(titles, pickup_days)`: `titles` is a list of title names; every copy starts on the shelf and nobody is waiting. `ValueError` if `pickup_days < 1`.
- `place_hold(title, member, now)`: puts the member at the back of the title's queue and returns their position (1 is the front). `KeyError` for an unknown title; `ValueError` if that member is already in the queue.
- `pickup(title, member, now)`: the member takes the copy home. It works (returns `True`) only if the copy is on the shelf and the member is at the front of the queue; then they leave the queue and the copy is out. Otherwise it returns `False` and changes nothing. `KeyError` for an unknown title.
- `return_copy(title, now)`: a copy that is out comes back to the shelf. `ValueError` if the copy is not out; `KeyError` for an unknown title.
- `queue(title, now)`: the list of members waiting, front first. `KeyError` for an unknown title.

The queue's front member has `pickup_days` days to collect a copy that is on the shelf. The clock starts when the copy is first available to them: when they join an empty queue with the copy on the shelf, when the copy is returned, or when the previous front member's window ran out. A member who has not picked up by the time `now >= clock_start + pickup_days` loses their place and drops out of the queue, and the next member's window starts at that moment (not when anyone next calls a method), so several expiries can happen at once when a long time has passed. Every method must first apply all expiries up to `now`, before it does its own work; a member who dropped out may join again."""


def _holds(rng):
    titles = pick(rng, [["atlas", "birds", "chess"], ["maps", "poems", "tides"], ["ants", "boats", "coins"]])
    v = dict(TITLES=repr(titles), DAYS=pick(rng, [2, 3, 4]))
    return _build(v, module="holddesk", solution=_HOLD_SOL, prompt=_HOLD_PROMPT, tests=_HOLD_TESTS, wrongs=[
        ("self._free_since[title] = q[0][1] + self._pickup", "self._free_since[title] = now"),
        ("if now >= q[0][1] + self._pickup:", "if now > q[0][1] + self._pickup:"),
        ("        if any(m == member for m, _ in q):\n            raise ValueError(\"already holding\")\n", ""),
        ("        return len(q)", "        return len(q) - 1"),
    ])


# ------------------------------------------------------------------ shift roster

_ROSTER_SOL = '''class Roster:
    def __init__(self, max_hours, min_rest):
        if max_hours < 1 or min_rest < 0:
            raise ValueError("bad limits")
        self.max_hours = max_hours
        self.min_rest = min_rest
        self._shifts = {}

    def assign(self, name, day, start, end):
        if not 0 <= start < end <= 24:
            raise ValueError("bad shift hours")
        a, b = day * 24 + start, day * 24 + end
        mine = self._shifts.get(name, [])
        for x, y in mine:
            if a < y + self.min_rest and x < b + self.min_rest:
                raise ValueError("not enough rest")
        total = sum(y - x for x, y in mine) + (b - a)
        if total > self.max_hours:
            raise ValueError("too many hours")
        self._shifts.setdefault(name, []).append((a, b))
        return total

    def unassign(self, name, day, start):
        a = day * 24 + start
        for i, (x, y) in enumerate(self._shifts.get(name, [])):
            if x == a:
                del self._shifts[name][i]
                return y - x
        raise KeyError((name, day, start))

    def hours(self, name):
        return sum(y - x for x, y in self._shifts.get(name, []))

    def schedule(self, day):
        rows = []
        for name, shifts in self._shifts.items():
            for x, y in shifts:
                if x // 24 == day:
                    rows.append([name, x - day * 24, y - day * 24])
        return sorted(rows, key=lambda r: (r[1], r[0]))
'''

_ROSTER_TESTS = '''from roster import Roster as SUT

ARGS = (@MAX@, @REST@)
MAXH, REST = @MAX@, @REST@


class Ref:
    def __init__(self, max_hours, min_rest):
        if max_hours < 1 or min_rest < 0:
            raise ValueError
        self.mx, self.rest = max_hours, min_rest
        self.items = []   # (name, absolute start, absolute end)

    def assign(self, name, day, start, end):
        if start < 0 or end > 24 or start >= end:
            raise ValueError
        a, b = day * 24 + start, day * 24 + end
        total = b - a
        for n, x, y in self.items:
            if n != name:
                continue
            total += y - x
            gap = a - y if a >= y else (x - b if x >= b else -1)
            if gap < self.rest:
                raise ValueError
        if total > self.mx:
            raise ValueError
        self.items.append((name, a, b))
        return total

    def unassign(self, name, day, start):
        for i, (n, x, y) in enumerate(self.items):
            if n == name and x == day * 24 + start:
                del self.items[i]
                return y - x
        raise KeyError

    def hours(self, name):
        return sum(y - x for n, x, y in self.items if n == name)

    def schedule(self, day):
        rows = [[n, x - day * 24, y - day * 24] for n, x, y in self.items if x // 24 == day]
        rows.sort(key=lambda r: (r[1], r[0]))
        return rows


HAND = [
    [o("assign", "ann", 0, 8, 16), o("assign", "ann", 1, 8, 16), o("assign", "ann", 0, 18, 22),
     o("hours", "ann"), o("schedule", 0), o("schedule", 1)],
    [o("assign", "ann", 0, 14, 22), o("assign", "ann", 1, 6, 14), o("assign", "ann", 1, 8, 14),
     o("assign", "ann", 1, 5, 6), o("assign", "ann", 1, 10, 11), o("hours", "ann")],
    [o("assign", "ann", 0, 0, 8), o("assign", "ann", 1, 0, 8), o("assign", "ann", 2, 0, 8), o("assign", "ann", 3, 0, 8),
     o("hours", "ann"), o("assign", "bo", 0, 0, 8), o("hours", "bo")],
    [o("assign", "ann", 0, 0, MAXH if MAXH <= 24 else 24), o("assign", "ann", 5, 0, 1), o("hours", "ann"),
     o("unassign", "ann", 0, 0), o("assign", "ann", 5, 0, 1), o("hours", "ann")],
    [o("assign", "ann", 0, 9, 17), o("assign", "ann", 0, 9, 17), o("assign", "ann", 0, 10, 12),
     o("assign", "ann", 0, 5, 10), o("assign", "bo", 0, 9, 17), o("schedule", 0)],
    [o("assign", "ann", 0, 5, 5), o("assign", "ann", 0, 6, 5), o("assign", "ann", 0, -1, 5), o("assign", "ann", 0, 20, 25),
     o("assign", "ann", 0, 0, 24), o("hours", "ann")],
    [o("assign", "bo", 2, 10, 14), o("assign", "ann", 2, 6, 10), o("assign", "cy", 2, 6, 10), o("assign", "bo", 2, 6, 9),
     o("schedule", 2), o("unassign", "bo", 2, 11), o("unassign", "bo", 2, 10), o("unassign", "bo", 2, 10),
     o("schedule", 2), o("hours", "bo")],
    [o("assign", "ann", 0, 6, 10), o("assign", "ann", 0, 10 + REST - 1, 11 + REST - 1),
     o("assign", "ann", 0, 10 + REST, 11 + REST), o("assign", "ann", 0, 9 + REST, 10 + REST), o("hours", "ann"),
     o("schedule", 0)],
    [o("assign", "ann", 0, 14, 22), o("assign", "ann", 1, 5, 6), o("assign", "ann", 1, 10, 11), o("hours", "ann"),
     o("unassign", "ann", 0, 14), o("assign", "ann", 1, 5, 6), o("schedule", 1)],
]


def gen(rng):
    ops = []
    for _ in range(rng.randint(10, 40)):
        name = rng.choice("abc")
        k = rng.random()
        if k < 0.6:
            s = rng.choice([0, 6, 8, 14, 18, 22])
            ops.append(o("assign", name, rng.randint(0, 3), s, s + rng.choice([2, 4, 6, 8, 9])))
        elif k < 0.75:
            ops.append(o("unassign", name, rng.randint(0, 3), rng.choice([0, 6, 8, 14, 18, 22])))
        elif k < 0.9:
            ops.append(o("hours", name))
        else:
            ops.append(o("schedule", rng.randint(0, 3)))
    return ops
'''

_ROSTER_PROMPT = """Write `roster.py` with a class `Roster` that builds a staff rota. A shift is on a whole-number day (0, 1, 2, ...) and runs from a start hour to an end hour, `0 <= start < end <= 24`, so a shift never crosses midnight.

- `Roster(max_hours, min_rest)`: no shifts yet. Nobody may be rostered for more than `max_hours` hours in total, and between any two shifts of the same person there must be at least `min_rest` hours of rest, counted from the end of the earlier shift to the start of the later one, across day boundaries too (day 0 ending at 22 and day 1 starting at 6 is 8 hours of rest). `ValueError` if `max_hours < 1` or `min_rest < 0`.
- `assign(name, day, start, end)`: adds a shift for that person and returns their new total hours. `ValueError` for invalid hours, for a shift that overlaps or is closer than `min_rest` hours to another shift of the same person, or for one that would take their total above `max_hours` (a total exactly equal to `max_hours` is fine). Different people never affect each other.
- `unassign(name, day, start)`: removes that person's shift on that day that starts at that hour and returns its length in hours, so that its hours and rest gap are free again. `KeyError` if there is no such shift.
- `hours(name)`: the person's total rostered hours (0 for an unknown name).
- `schedule(day)`: the shifts on that day as a list of `[name, start, end]` lists, sorted by start hour, then by name.

A call that raises must change nothing."""


def _roster(rng):
    v = dict(MAX=pick(rng, [16, 20, 24]), REST=pick(rng, [8, 10, 12]))
    return _build(v, module="roster", solution=_ROSTER_SOL, prompt=_ROSTER_PROMPT, tests=_ROSTER_TESTS, wrongs=[
        ("if a < y + self.min_rest and x < b + self.min_rest:", "if a < y and x < b:"),
        ("if total > self.max_hours:", "if total >= self.max_hours:"),
        ("key=lambda r: (r[1], r[0])", "key=lambda r: (r[0], r[1])"),
        ("if a < y + self.min_rest and x < b + self.min_rest:",
         "if x // 24 == day and a < y + self.min_rest and x < b + self.min_rest:"),
    ])


# ------------------------------------------------------------------ retry tracker

_RETRY_SOL = '''class RetryTracker:
    def __init__(self, max_failures, base_delay, max_delay):
        if max_failures < 1 or base_delay < 1 or max_delay < base_delay:
            raise ValueError("bad settings")
        self.max_failures = max_failures
        self.base = base_delay
        self.cap = max_delay
        self._jobs = {}

    def status(self, job, t):
        rec = self._jobs.get(job)
        if rec is None:
            return "ready"
        if rec["dead"]:
            return "dead"
        return "waiting" if t < rec["retry_at"] else "ready"

    def fail(self, job, t):
        state = self.status(job, t)
        if state == "dead":
            raise RuntimeError("job is dead")
        if state == "waiting":
            raise ValueError("job is not due yet")
        rec = self._jobs.setdefault(job, {"failures": 0, "retry_at": 0, "dead": False})
        rec["failures"] += 1
        if rec["failures"] >= self.max_failures:
            rec["dead"] = True
            return None
        rec["retry_at"] = t + min(self.cap, self.base * 2 ** (rec["failures"] - 1))
        return rec["retry_at"]

    def succeed(self, job):
        rec = self._jobs.get(job)
        if rec is not None and rec["dead"]:
            raise RuntimeError("job is dead")
        self._jobs.pop(job, None)

    def revive(self, job):
        rec = self._jobs.get(job)
        if rec is None or not rec["dead"]:
            raise ValueError("job is not dead")
        del self._jobs[job]
'''

_RETRY_TESTS = '''from retry import RetryTracker as SUT

ARGS = (@MAXF@, @BASE@, @CAP@)
MAXF, BASE, CAP = @MAXF@, @BASE@, @CAP@


class Ref:
    def __init__(self, max_failures, base_delay, max_delay):
        if max_failures < 1 or base_delay < 1 or max_delay < base_delay:
            raise ValueError
        self.m, self.b, self.c = max_failures, base_delay, max_delay
        self.fails = {}
        self.until = {}
        self.dead = set()

    def status(self, job, t):
        if job in self.dead:
            return "dead"
        if job in self.until and t < self.until[job]:
            return "waiting"
        return "ready"

    def fail(self, job, t):
        s = self.status(job, t)
        if s == "dead":
            raise RuntimeError
        if s == "waiting":
            raise ValueError
        n = self.fails.get(job, 0) + 1
        self.fails[job] = n
        if n >= self.m:
            self.dead.add(job)
            self.until.pop(job, None)
            return None
        self.until[job] = t + min(self.c, self.b * (2 ** (n - 1)))
        return self.until[job]

    def succeed(self, job):
        if job in self.dead:
            raise RuntimeError
        self.fails.pop(job, None)
        self.until.pop(job, None)

    def revive(self, job):
        if job not in self.dead:
            raise ValueError
        self.dead.discard(job)
        self.fails.pop(job, None)


def _die(job, t0):
    ops, t = [], t0
    for _ in range(MAXF):
        ops.append(o("fail", job, t))
        t += 1000
    return ops


HAND = [
    [o("status", "a", 0), o("fail", "a", 0), o("status", "a", BASE - 1), o("status", "a", BASE), o("fail", "a", BASE - 1),
     o("fail", "a", BASE), o("status", "a", 3 * BASE)],
    [o("fail", "a", 0), o("fail", "a", 100), o("fail", "a", 200), o("fail", "a", 300), o("fail", "a", 400),
     o("fail", "a", 500), o("fail", "a", 600), o("status", "a", 700)],
    [o("fail", "a", 0), o("fail", "a", 50), o("succeed", "a"), o("fail", "a", 51), o("status", "a", 51 + BASE - 1),
     o("status", "a", 51 + BASE)],
    [o("fail", "a", 0), o("succeed", "a"), o("fail", "a", 1), o("fail", "a", 1 + BASE), o("succeed", "a"),
     o("status", "a", 1)],
    _die("a", 0) + [o("status", "a", 99999), o("fail", "a", 99999), o("succeed", "a"), o("revive", "a"),
                    o("status", "a", 0), o("fail", "a", 0), o("revive", "a")],
    _die("a", 0) + [o("revive", "a")] + _die("a", 5000)[:-1] + [o("status", "a", 99999), o("fail", "a", 99999)],
    [o("succeed", "never"), o("revive", "never"), o("status", "never", 0), o("fail", "b", 0), o("fail", "c", 0),
     o("status", "b", 1), o("status", "c", BASE)],
    [o("fail", "a", 0), o("succeed", "a"), o("fail", "a", 0), o("fail", "a", BASE), o("fail", "a", BASE * 4),
     o("fail", "a", 9999)],
]


def test_constructor():
    for bad in ((0, 1, 5), (3, 0, 5), (3, 5, 4)):
        try:
            SUT(*bad)
        except ValueError:
            continue
        raise AssertionError("accepted %r" % (bad,))


def gen(rng):
    ops, t = [], 0
    for _ in range(rng.randint(10, 40)):
        t += rng.choice([0, 1, 2, 5, 10, 20])
        job = rng.choice("ab")
        k = rng.random()
        if k < 0.5:
            ops.append(o("fail", job, t))
        elif k < 0.65:
            ops.append(o("succeed", job))
        elif k < 0.85:
            ops.append(o("status", job, t))
        else:
            ops.append(o("revive", job))
    return ops
'''

_RETRY_PROMPT = """Write `retry.py` with a class `RetryTracker` that decides when failed jobs may be retried, with exponential back-off. Times are numbers passed in by the caller (there is no real clock). A job name is any string; jobs not seen before are fine to use.

- `RetryTracker(max_failures, base_delay, max_delay)`: a job that has failed `max_failures` times is dead. After its n-th failure (n less than `max_failures`) a job must wait `min(max_delay, base_delay * 2 ** (n - 1))` time units before its next attempt. `ValueError` if `max_failures < 1`, `base_delay < 1` or `max_delay < base_delay`.
- `status(job, t)`: `"dead"` if the job is dead; `"waiting"` if it failed and `t` is before its retry time; otherwise `"ready"` (this includes jobs never seen before).
- `fail(job, t)`: records a failed attempt at time `t`. Returns the time of the next allowed attempt (`t` plus the delay above), or `None` if this failure killed the job. `RuntimeError` if the job is already dead; `ValueError` if the job is waiting at `t` (an attempt must not be made before its retry time, so it cannot fail either).
- `succeed(job)`: the job worked, so forget its failures and any waiting time, as if it were new. Fine for a job never seen. `RuntimeError` if the job is dead.
- `revive(job)`: gives a dead job a fresh start (no failures, ready). `ValueError` if the job is not dead.

A call that raises must change nothing."""


def _retry(rng):
    v = dict(MAXF=pick(rng, [4, 5]), BASE=pick(rng, [2, 3]), CAP=pick(rng, [5, 6, 7]))
    # test_constructor, by hand: each bad setting must raise ValueError.
    ctor = [{"init": list(bad), "steps": [], "raises": "ValueError", "test": "test_constructor", "covers": [0]}
            for bad in ((0, 1, 5), (3, 0, 5), (3, 5, 4))]
    return _build(v, module="retry", solution=_RETRY_SOL, prompt=_RETRY_PROMPT, tests=_RETRY_TESTS, extra=ctor, wrongs=[
        ("        self._jobs.pop(job, None)\n\n    def revive", "        if rec is not None:\n            rec[\"retry_at\"] = 0\n\n    def revive"),
        ("t + min(self.cap, self.base * 2 ** (rec[\"failures\"] - 1))", "t + self.base * 2 ** (rec[\"failures\"] - 1)"),
        ("        if state == \"waiting\":\n            raise ValueError(\"job is not due yet\")\n", ""),
        ("        del self._jobs[job]", "        self._jobs[job][\"dead\"] = False"),
    ])


# ------------------------------------------------------------------ shopping cart with coupon

_CART_SOL = '''class Cart:
    def __init__(self, prices, coupons):
        self._prices = dict(prices)
        self._coupons = dict(coupons)
        self._lines = {}
        self._coupon = None

    def add(self, sku, qty=1):
        if sku not in self._prices:
            raise KeyError(sku)
        if qty < 1:
            raise ValueError("quantity must be at least 1")
        self._lines[sku] = self._lines.get(sku, 0) + qty

    def remove(self, sku, qty=1):
        if sku not in self._lines:
            raise KeyError(sku)
        if qty < 1 or qty > self._lines[sku]:
            raise ValueError("bad quantity")
        self._lines[sku] -= qty
        if self._lines[sku] == 0:
            del self._lines[sku]

    def apply_coupon(self, code):
        if code not in self._coupons:
            raise KeyError(code)
        self._coupon = code

    def items(self):
        return [[sku, self._lines[sku]] for sku in sorted(self._lines)]

    def total(self):
        subtotal = sum(self._prices[s] * q for s, q in self._lines.items())
        if self._coupon is None:
            return subtotal
        kind, value, minimum = self._coupons[self._coupon]
        if subtotal < minimum:
            return subtotal
        off = subtotal * value // 100 if kind == "pct" else value
        return max(0, subtotal - off)
'''

_CART_TESTS = '''from cart import Cart as SUT

PRICES = {"pen": @PEN@, "ink": 705, "pad": 1255}
COUPONS = {"TEN": ("pct", @PCT@, @MIN@), "FIVER": ("off", 500, 2000), "HUGE": ("off", 99999, 0)}
ARGS = (PRICES, COUPONS)


class Ref:
    def __init__(self, prices, coupons):
        self.prices = dict(prices)
        self.coupons = dict(coupons)
        self.qty = {}
        self.code = None

    def add(self, sku, qty=1):
        if sku not in self.prices:
            raise KeyError(sku)
        if qty < 1:
            raise ValueError
        self.qty[sku] = self.qty.get(sku, 0) + qty

    def remove(self, sku, qty=1):
        if sku not in self.qty:
            raise KeyError(sku)
        if qty < 1 or qty > self.qty[sku]:
            raise ValueError
        self.qty[sku] -= qty
        if not self.qty[sku]:
            del self.qty[sku]

    def apply_coupon(self, code):
        if code not in self.coupons:
            raise KeyError(code)
        self.code = code

    def items(self):
        return [[k, self.qty[k]] for k in sorted(self.qty)]

    def total(self):
        gross = sum(self.prices[k] * n for k, n in self.qty.items())
        if not self.code:
            return gross
        kind, value, floor = self.coupons[self.code]
        if gross < floor:
            return gross
        cut = (gross * value) // 100 if kind == "pct" else value
        return gross - cut if gross > cut else 0


MIN = @MIN@
PEN = @PEN@
HAND = [
    [o("add", "pen", 2), o("add", "ink"), o("total"), o("items")],
    [o("add", "pad", 2), o("apply_coupon", "TEN"), o("total"), o("add", "ink"), o("total"), o("add", "ink", 7),
     o("total")],
    [o("add", "pad", 2), o("apply_coupon", "FIVER"), o("total"), o("remove", "pad"), o("total"), o("add", "pad"),
     o("total"), o("items")],
    [o("add", "pad", 3), o("apply_coupon", "TEN"), o("apply_coupon", "FIVER"), o("total"), o("apply_coupon", "NOPE"),
     o("total")],
    [o("add", "pad"), o("apply_coupon", "HUGE"), o("total"), o("remove", "pad"), o("total"), o("add", "ink"),
     o("total")],
    [o("add", "pen", 3), o("apply_coupon", "TEN"), o("total"), o("add", "pen", 1), o("total"), o("add", "ink", 4),
     o("total")],
    [o("add", "zzz"), o("add", "pen", 0), o("add", "pen", -1), o("remove", "pen"), o("add", "pen", 2),
     o("remove", "pen", 3), o("remove", "pen", 0), o("remove", "pen", 2), o("items"), o("remove", "pen"),
     o("total")],
    [o("apply_coupon", "TEN"), o("total"), o("add", "pad", 9), o("total"), o("remove", "pad", 9), o("total")],
    [o("add", "ink", 3), o("add", "pad"), o("add", "pen"), o("items"), o("remove", "ink", 3), o("items")],
]


def gen(rng):
    ops = []
    for _ in range(rng.randint(8, 35)):
        sku = rng.choice(["pen", "ink", "pad", "pad", "zzz"] if rng.random() < 0.1 else ["pen", "ink", "pad"])
        k = rng.random()
        if k < 0.3:
            ops.append(o("add", sku, rng.choice([1, 1, 2, 3, 0])))
        elif k < 0.45:
            ops.append(o("remove", sku, rng.choice([1, 1, 2, 3, 0])))
        elif k < 0.6:
            ops.append(o("apply_coupon", rng.choice(["TEN", "FIVER", "HUGE", "NOPE", "TEN"])))
        elif k < 0.9:
            ops.append(o("total"))
        else:
            ops.append(o("items"))
    return ops
'''

_CART_PROMPT = """Write `cart.py` with a class `Cart` for a shop's basket, with all money in whole cents.

- `Cart(prices, coupons)`: `prices` maps an SKU to its unit price in cents. `coupons` maps a coupon code to a tuple `(kind, value, minimum)`: `kind` is `"pct"` (take `value` percent off) or `"off"` (take `value` cents off), and the coupon only counts while the basket subtotal is at least `minimum` cents. The basket starts empty with no coupon.
- `add(sku, qty=1)`: puts `qty` more of the item in the basket. `KeyError` for an SKU that is not in `prices`; `ValueError` if `qty < 1`.
- `remove(sku, qty=1)`: takes `qty` of the item out; a line that reaches 0 disappears. `KeyError` if the SKU is not in the basket; `ValueError` if `qty < 1` or more than the basket holds.
- `apply_coupon(code)`: attaches the coupon to the basket, replacing any coupon attached before (coupons never stack). `KeyError` for an unknown code. It may be attached to an empty basket, and it stays attached even when the basket is below its minimum, so it counts again as soon as the subtotal is high enough.
- `items()`: the basket lines as `[sku, qty]` lists sorted by SKU.
- `total()`: the subtotal (unit prices times quantities) minus the attached coupon's discount if the subtotal is at least the coupon's `minimum`. A `"pct"` discount is `subtotal * value // 100` (rounded down); the total is never below 0.

A call that raises must change nothing."""


def _cart(rng):
    v = dict(PEN=pick(rng, [125, 155, 185]), PCT=pick(rng, [10, 15, 25]), MIN=pick(rng, [1000, 1500, 2000]))
    return _build(v, module="cart", solution=_CART_SOL, prompt=_CART_PROMPT, tests=_CART_TESTS, wrongs=[
        ("if subtotal < minimum:\n            return subtotal", "if subtotal < minimum:\n            self._coupon = None\n            return subtotal"),
        ("subtotal * value // 100", "-(-subtotal * value // 100)"),
        ("        self._coupon = code", "        self._coupon = code if self._coupon is None else self._coupon"),
        ("        if self._lines[sku] == 0:\n            del self._lines[sku]\n", ""),
    ])


TEMPLATES = [
    probe_template("state", "state_parking_meter", _parking),
    probe_template("state", "state_room_calendar", _calendar),
    probe_template("state", "state_sliding_limiter", _limiter),
    probe_template("state", "state_reservation_ledger", _ledger),
    probe_template("state", "state_points_wallet", _wallet),
    probe_template("state", "state_seat_map", _seatmap),
    probe_template("state", "state_undo_history", _history),
    probe_template("state", "state_league_table", _standings),
    probe_template("state", "state_hold_desk", _holds),
    probe_template("state", "state_shift_roster", _roster),
    probe_template("state", "state_retry_tracker", _retry),
    probe_template("state", "state_cart_coupon", _cart),
]
