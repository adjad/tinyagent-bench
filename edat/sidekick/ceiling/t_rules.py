"""Rung 4: a function with several rules that interact.

Each case states 4-6 rules in plain words; some rules override or modify
others, so a solution has to get their order and exceptions right. Hidden
tests hold a trusted reference copy and check hand-picked edge cases plus a
few hundred random inputs against it.
"""
from __future__ import annotations

import math
import random

from edat.sidekick.ceiling.rungs import answer_files, fill, probe_instance, probe_template
from edat.sidekick.gen.core import pick

# ------------------------------------------------------------------ shipping fee

_SHIP_SOLUTION = '''import math


def shipping_fee(subtotal, weight_kg, remote=False, member=False):
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    kg = math.ceil(weight_kg)
    base = @BASE@
    weight_part = @PER_KG@ * kg
    if subtotal >= @FREE_OVER@ and not remote:
        fee = weight_part if kg > @HEAVY@ else 0.0
    else:
        fee = base + weight_part
    if remote:
        fee *= 2
    if member:
        fee *= 0.9
    return round(fee, 2)
'''

_SHIP_TESTS = '''import math
import random

import pytest

from shipping import shipping_fee


def _ref(subtotal, weight_kg, remote=False, member=False):
    if weight_kg <= 0:
        raise ValueError
    kg = math.ceil(weight_kg)
    weight_part = @PER_KG@ * kg
    if subtotal >= @FREE_OVER@ and not remote:
        fee = weight_part if kg > @HEAVY@ else 0.0
    else:
        fee = @BASE@ + weight_part
    if remote:
        fee *= 2
    if member:
        fee *= 0.9
    return round(fee, 2)


CASES = [
    (@FREE_OVER@ - 0.01, 1.0, False, False),
    (@FREE_OVER@, 1.0, False, False),
    (@FREE_OVER@, @HEAVY@ + 0.5, False, False),
    (@FREE_OVER@, @HEAVY@, False, False),
    (@FREE_OVER@ + 10, 2.2, True, False),
    (5, 0.3, True, True),
    (@FREE_OVER@ * 3, @HEAVY@ + 3, False, True),
    (0, 7.01, False, True),
]


@pytest.mark.parametrize("args", CASES)
def test_edges(args):
    assert shipping_fee(*args) == pytest.approx(_ref(*args), abs=0.006)


def test_keyword_defaults():
    assert shipping_fee(1, 1) == pytest.approx(_ref(1, 1), abs=0.006)


def test_random():
    rng = random.Random(4711)
    for _ in range(300):
        args = (round(rng.uniform(0, @FREE_OVER@ * 2), 2), round(rng.uniform(0.05, @HEAVY@ * 2), 2),
                rng.random() < 0.3, rng.random() < 0.5)
        assert shipping_fee(*args) == pytest.approx(_ref(*args), abs=0.006), args


@pytest.mark.parametrize("w", [0, -2])
def test_bad_weight(w):
    with pytest.raises(ValueError):
        shipping_fee(10, w)
'''


def _shipping(rng: random.Random):
    v = dict(BASE=pick(rng, [4, 5, 6]), PER_KG=pick(rng, [1.5, 2.0, 2.5]), FREE_OVER=pick(rng, [40, 50, 60]),
             HEAVY=pick(rng, [10, 12, 15]))
    sol = fill(_SHIP_SOLUTION, **v)
    prompt = fill("""Write a Python function `shipping_fee(subtotal, weight_kg, remote=False, member=False)` in `shipping.py` that returns an order's shipping fee in dollars, rounded to cents. The rules:

1. The weight is rounded up to whole kilograms first (2.1 kg counts as 3 kg).
2. The normal fee is a base of $@BASE@ plus $@PER_KG@ per kilogram.
3. Orders with a subtotal of $@FREE_OVER@ or more ship free, except that a parcel over @HEAVY@ kg (after rounding) still pays the per-kilogram part, though not the base.
4. Free shipping never applies to remote addresses (`remote=True`), and a remote address pays double the fee.
5. Members (`member=True`) get 10% off the fee, after any doubling.
6. A weight of zero or less raises `ValueError`.""", **v)
    wrong_remote_free = sol.replace("if subtotal >= @FREE_OVER@ and not remote:".replace("@FREE_OVER@", str(v["FREE_OVER"])),
                                    f"if subtotal >= {v['FREE_OVER']}:")
    wrong_no_ceil = sol.replace("kg = math.ceil(weight_kg)", "kg = weight_kg")
    wrong_heavy_free = sol.replace(f"fee = weight_part if kg > {v['HEAVY']} else 0.0", "fee = 0.0")
    assert len({sol, wrong_remote_free, wrong_no_ceil, wrong_heavy_free}) == 4
    t = fill(_SHIP_TESTS, **v)
    edges = t.split("CASES = ", 1)[1].split("\n]\n", 1)[0] + "\n]"
    gen = ("(round(r.uniform(0, @FREE_OVER@ * 2), 2), round(r.uniform(0.05, @HEAVY@ * 2), 2), "
           "r.random() < 0.3, r.random() < 0.5)")
    probes = rules_probes("shipping", "shipping_fee", edges + " + [(1, 1)]", fill(gen, **v), "[(10, 0), (10, -2)]",
                          seed=4711, tol_abs=0.006)
    return probe_instance(
        prompt=prompt, targets=["shipping.py"], hidden_tests=None,
        references=[answer_files({"shipping.py": sol}), answer_files({"shipping.py": sol}, style="label")],
        wrong=[answer_files({"shipping.py": w}) for w in (wrong_remote_free, wrong_no_ceil, wrong_heavy_free)],
        probes=probes, solution={"shipping.py": sol})



# ------------------------------------------------------------------ shared builder

_BODY = """EDGES = @EDGES@


@pytest.mark.parametrize("args", EDGES)
def test_edges(args):
    assert FUNC(*args) == _ref(*args), args


def test_random():
    r = random.Random(1234)
    for _ in range(300):
        args = @GEN@
        assert FUNC(*args) == _ref(*args), args
"""

_RAISES = """

RAISES = @RAISES@


@pytest.mark.parametrize("args", RAISES)
def test_invalid(args):
    with pytest.raises(ValueError):
        FUNC(*args)
"""


def _make(v, module, func, sol, prompt, edges, gen, wrongs, raises=None):
    """Build one rules instance: the reference copy inside the tests is the solution itself, renamed `_ref`."""
    sol = fill(sol, **v)
    ref = sol.replace(f"def {func}(", "def _ref(")
    assert ref != sol
    head = f"import math\nimport random\n\nimport pytest\n\nfrom {module[:-3]} import {func}\n\n\n"
    body = fill(_BODY, EDGES=fill(edges, **v), GEN=fill(gen, **v)).replace("FUNC", func)
    if raises:
        body += fill(_RAISES, RAISES=fill(raises, **v)).replace("FUNC", func)
    tests = head + ref.rstrip("\n") + "\n\n\n" + body
    bad = []
    for old, new in wrongs:
        o, n = fill(old, **v), fill(new, **v)
        assert o in sol, o
        w = sol.replace(o, n, 1)
        assert w != sol
        bad.append(answer_files({module: w}))
    assert len(set(bad)) == len(bad)
    return probe_instance(
        prompt=fill(prompt, **v), targets=[module], hidden_tests=None,
        references=[answer_files({module: sol}), answer_files({module: sol}, style="label")], wrong=bad,
        probes=rules_probes(module[:-3], func, fill(edges, **v), fill(gen, **v), fill(raises, **v) if raises else None),
        solution={module: sol})


def rules_probes(mod: str, func: str, edges: str, gen: str, raises: str | None, *, seed: int = 1234,
                 n_random: int = 300, tol_abs: float | None = None) -> list[dict]:
    """The hidden tests as host-driven calls: the edge cases, `n_random` seeded random inputs (drawn here, on
    the host, exactly as the old test drew them) and the inputs that must raise ValueError."""
    ns = {"math": math, "random": random}
    calls = [list(a) for a in eval(edges, dict(ns))]                    # noqa: S307 - the template's own text
    r = random.Random(seed)
    calls += [list(eval(gen, dict(ns, r=r))) for _ in range(n_random)]  # noqa: S307
    out = [{"kind": "call", "mod": mod, "fn": func, "args": a} for a in calls]
    if tol_abs is not None:
        for p in out:
            p["tol_abs"] = tol_abs
    if raises:
        out += [{"kind": "call", "mod": mod, "fn": func, "args": list(a), "raises": "ValueError"}
                for a in eval(raises, dict(ns))]                          # noqa: S307
    return out


# ------------------------------------------------------------------ weekly pay

_PAY_SOL = """def weekly_pay(hours, rate_cents, holidays=()):
    if len(hours) != 7 or any(h < 0 for h in hours):
        raise ValueError("bad hours")
    total = 0
    for day, h in enumerate(hours):
        if day in holidays or day == 6:
            total += h * rate_cents * 20 // 10
        elif day == 5:
            total += h * rate_cents * 15 // 10
        else:
            regular = min(h, @DAILY@)
            mid = min(max(h - @DAILY@, 0), @DBL@ - @DAILY@)
            top = max(h - @DBL@, 0)
            total += (regular * 10 + mid * 15 + top * 20) * rate_cents // 10
    return total
"""

_PAY_PROMPT = """Write a Python function `weekly_pay(hours, rate_cents, holidays=())` in `payroll.py` that returns one week's pay in whole cents. `hours` is a list of 7 whole numbers of hours worked, Monday first (index 0) to Sunday (index 6). `rate_cents` is the normal hourly rate in cents (a whole number). `holidays` is a collection of day indexes that were public holidays. The rules:

1. Monday to Friday: the first @DAILY@ hours of a day are paid at the normal rate, hours beyond @DAILY@ up to @DBL@ at 1.5 times the rate, and hours beyond @DBL@ at 2 times the rate.
2. Saturday: every hour is paid at 1.5 times the rate.
3. Sunday: every hour is paid at 2 times the rate.
4. On a holiday every hour of that day is paid at 2 times the rate. This replaces whatever rule 1 or 2 would give; it does not stack with anything (a holiday on a Sunday is still 2 times).
5. Each day's pay is worked out in whole cents, rounding down for that day; the week is the sum of the days.
6. A list that does not have exactly 7 entries, or has a negative entry, raises `ValueError`."""


def _pay(rng):
    v = dict(DAILY=pick(rng, [8, 9]), DBL=pick(rng, [11, 12, 13]))
    rate = pick(rng, [1731, 1999, 2255, 2813])
    edges = ("[([8, 8, 8, 8, 8, 0, 0], %d, ()), ([@DAILY@ + 1, 0, 0, 0, 0, 0, 0], %d, ()), "
             "([0, @DBL@, 0, 0, 0, 0, 0], %d, ()), ([0, 0, @DBL@ + 1, 0, 0, 0, 0], %d, ()), "
             "([0, 0, 0, 0, 0, 5, 0], %d, ()), ([0, 0, 0, 0, 0, 0, 5], %d, ()), "
             "([0, 0, 0, 0, 0, 3, 0], %d, (5,)), ([@DBL@ + 2, 0, 0, 0, 0, 0, 0], %d, (0,)), "
             "([0, 0, 0, 0, 0, 0, 3], %d, (6,)), ([1, 1, 1, 1, 1, 1, 1], %d, ()), ([3, 0, 0, 0, 0, 1, 0], %d, [0, 5])]"
             % ((rate,) * 11))
    gen = ("([r.randint(0, 14) for _ in range(7)], r.randint(900, 4000), "
           "tuple(sorted(r.sample(range(7), r.randint(0, 2)))))")
    raises = "[([8] * 6, 1000), ([8] * 8, 1000), ([8, 8, 8, 8, -1, 0, 0], 1000)]"
    wrongs = [("if day in holidays or day == 6:", "if (day in holidays and day < 5) or day == 6:"),
              ("            regular = min(h, @DAILY@)", "            regular = min(h, @DAILY@ - 1)"),
              ("top = max(h - @DBL@, 0)", "top = max(h - @DBL@ - 1, 0)"),
              ("            total += (regular * 10 + mid * 15 + top * 20) * rate_cents // 10",
               "            total += (regular * 10 + mid * 15 + top * 20) * rate_cents / 10")]
    return _make(v, "payroll.py", "weekly_pay", _PAY_SOL, _PAY_PROMPT, edges, gen, wrongs, raises)


# ------------------------------------------------------------------ ticket price

_TICKET_SOL = """def _pct(cents, pct):
    return (cents * pct + 50) // 100


def ticket_price(age, group_size, member):
    if age < 0 or group_size < 1:
        raise ValueError("bad input")
    if age < 3:
        return 0
    if age <= 12:
        pct = 50
    elif age >= @SENIOR@:
        pct = 70
    else:
        pct = 100
    price = _pct(@BASE@, pct)
    if group_size >= 20:
        price = _pct(price, 75)
    elif group_size >= 10:
        price = _pct(price, 85)
    if member:
        price = _pct(price, 90)
    return max(price, @FLOOR@)
"""

_TICKET_PROMPT = """Write a Python function `ticket_price(age, group_size, member)` in `tickets.py` that returns the price of ONE museum ticket in cents (a whole number). The full price is @BASE@ cents. The rules:

1. Children under 3 get in free: the price is 0 and none of the other rules apply.
2. Ages 3 to 12 pay 50% of the full price, ages @SENIOR@ and over pay 70%, everyone else pays 100%.
3. Groups of 20 or more people pay 75% of the price from rule 2 per ticket; groups of 10 to 19 pay 85%. The two group discounts never add up, and `group_size` counts everyone in the group (a single visitor has `group_size=1`).
4. Members (`member=True`) then pay 90% of the price so far.
5. Each percentage step rounds to the nearest cent, with exact halves rounding up (use whole-number arithmetic, for example `(cents * pct + 50) // 100`).
6. A paid ticket never costs less than @FLOOR@ cents (checked last, after all discounts).
7. A negative age or a `group_size` below 1 raises `ValueError`."""


def _ticket(rng):
    base = pick(rng, [1200, 1500, 1800])
    v = dict(BASE=base, SENIOR=pick(rng, [60, 65]), FLOOR=base * pick(rng, [38, 40, 42]) // 100)
    edges = ("[(2, 1, False), (3, 1, False), (12, 1, False), (13, 1, False), (@SENIOR@ - 1, 1, False), "
             "(@SENIOR@, 1, False), (30, 9, False), (30, 10, False), (30, 19, False), (30, 20, False), "
             "(5, 20, True), (@SENIOR@, 10, True), (40, 1, True), (2, 25, True), (0, 1, False), (9, 12, True)]")
    gen = "(r.randint(0, 90), r.choice([1, 1, 2, 5, 9, 10, 15, 19, 20, 30]), r.random() < 0.5)"
    raises = "[(-1, 1, False), (30, 0, False)]"
    wrongs = [("if age < 3:", "if age <= 3:"),
              ("elif age >= @SENIOR@:", "elif age > @SENIOR@:"),
              ("    elif group_size >= 10:", "    if group_size >= 10:"),
              ("    return max(price, @FLOOR@)", "    return price"),
              ("    return (cents * pct + 50) // 100", "    return cents * pct // 100")]
    return _make(v, "tickets.py", "ticket_price", _TICKET_SOL, _TICKET_PROMPT, edges, gen, wrongs, raises)


# ------------------------------------------------------------------ late return fee

_LATE_SOL = """RATES = {"book": @BOOK@, "dvd": 100, "game": 150}
GRACE = {"book": @BOOKG@, "dvd": 0, "game": 1}


def late_fee(kind, days_late, renewals, cap_cents):
    if kind not in RATES or days_late < 0:
        raise ValueError("bad input")
    chargeable = days_late - GRACE[kind] - min(renewals, 2)
    if chargeable <= 0:
        return 0
    fee = chargeable * RATES[kind]
    fee = max(fee, @MINC@)
    return min(fee, cap_cents)
"""

_LATE_PROMPT = """Write a Python function `late_fee(kind, days_late, renewals, cap_cents)` in `library.py` that returns the fee in cents (a whole number) for an item returned late. `kind` is "book", "dvd" or "game". The rules:

1. Each kind has a grace period of free late days: book @BOOKG@, dvd 0, game 1.
2. Every earlier renewal of the loan adds one more grace day, but at most 2 extra days in total (`renewals` of 2 or more all add 2).
3. Every late day after the grace days costs: book @BOOK@ cents, dvd 100 cents, game 150 cents.
4. If at least one day is chargeable, the fee is raised to at least @MINC@ cents. If no day is chargeable the fee is 0.
5. The final fee never exceeds `cap_cents`; the cap is applied last, so it wins over the minimum in rule 4 (a cap of 30 gives 30 even though the minimum is @MINC@).
6. An unknown `kind` or negative `days_late` raises `ValueError`."""


def _late(rng):
    v = dict(BOOK=pick(rng, [20, 25, 30]), BOOKG=pick(rng, [1, 2, 3]), MINC=pick(rng, [50, 75]))
    edges = ("[(\"book\", @BOOKG@, 0, 1000), (\"book\", @BOOKG@ + 1, 0, 1000), (\"book\", @BOOKG@ + 3, 2, 1000), "
             "(\"book\", @BOOKG@ + 3, 5, 1000), (\"book\", @BOOKG@ + 4, 1, 1000), (\"dvd\", 0, 0, 500), (\"dvd\", 1, 0, 500), "
             "(\"dvd\", 9, 0, 500), (\"dvd\", 3, 1, 150), (\"game\", 1, 0, 500), (\"game\", 2, 0, 500), (\"game\", 2, 0, 30), "
             "(\"book\", @BOOKG@ + 1, 0, 10), (\"game\", 40, 3, 600), (\"book\", 50, 0, 120)]")
    gen = "(r.choice([\"book\", \"dvd\", \"game\"]), r.randint(0, 20), r.randint(0, 5), r.choice([30, 120, 400, 900]))"
    raises = "[(\"cd\", 3, 0, 500), (\"book\", -1, 0, 500)]"
    wrongs = [("chargeable = days_late - GRACE[kind] - min(renewals, 2)", "chargeable = days_late - GRACE[kind] - renewals"),
              ("    fee = max(fee, @MINC@)\n    return min(fee, cap_cents)", "    fee = min(fee, cap_cents)\n    return max(fee, @MINC@)"),
              ("    if chargeable <= 0:", "    if chargeable < 0:"),
              ("    fee = max(fee, @MINC@)\n", "")]
    return _make(v, "library.py", "late_fee", _LATE_SOL, _LATE_PROMPT, edges, gen, wrongs, raises)


# ------------------------------------------------------------------ password messages

_PW_SOL = """def password_problems(password, username):
    if password.strip() == "":
        return ["blank"]
    problems = []
    if any(c.isspace() for c in password):
        problems.append("has spaces")
    short = len(password) < @MINLEN@
    if short:
        problems.append("too short")
    elif len(password) > @MAXLEN@:
        problems.append("too long")
    if username and username.lower() in password.lower():
        problems.append("contains username")
    if not short:
        if not any(c.isdigit() for c in password):
            problems.append("no digit")
        if not (any(c.isupper() for c in password) and any(c.islower() for c in password)):
            problems.append("no mixed case")
    return problems
"""

_PW_PROMPT = """Write a Python function `password_problems(password, username)` in `policy.py` that checks a new password and returns a list of problem messages (strings), in the fixed order below; an acceptable password gives an empty list. The rules:

1. If the password is empty or only whitespace, return exactly `["blank"]` and nothing else.
2. Otherwise check these in this order and add each message that applies: "has spaces" (any whitespace character), then "too short" (fewer than @MINLEN@ characters) or, if it is not short, "too long" (more than @MAXLEN@ characters), then "contains username" (the username appears in the password, ignoring upper/lower case; an empty username never matches), then "no digit", then "no mixed case" (it must contain at least one uppercase and at least one lowercase letter).
3. When "too short" applies, "no digit" and "no mixed case" are left out (the user fixes the length first); the other messages still apply.
4. Length is the plain `len` of the password, spaces included."""


def _password(rng):
    v = dict(MINLEN=pick(rng, [8, 10, 12]), MAXLEN=pick(rng, [20, 32]))
    edges = ("[(\"\", \"kim\"), (\"   \", \"\"), (\"Aa1\" + \"x\" * (@MINLEN@ - 3), \"\"), (\"Aa1\" + \"x\" * (@MINLEN@ - 4), \"\"), "
             "(\"Aa1\" + \"x\" * (@MAXLEN@ - 3), \"\"), (\"Aa1\" + \"x\" * (@MAXLEN@ - 2), \"\"), (\"has space 1A\" + \"z\" * @MINLEN@, \"\"), "
             "(\"abc\", \"kim\"), (\"xxKIMxx9Zz\" + \"q\" * @MINLEN@, \"kim\"), (\"xxkimxx\", \"KIM\"), (\"a\" * @MINLEN@, \"\"), "
             "(\"A\" * @MINLEN@ + \"1\", \"\"), (\"a b\", \"\"), (\"Kim\" * 12, \"kim\")]")
    gen = ("(lambda name, chars: (lambda pw, k: (pw if not (name and k) else pw[:k] + r.choice([name, name.upper(), name.capitalize()]) + pw[k:], name))"
           "(\"\".join(r.choice(chars) for _ in range(r.randint(0, 36))), r.randint(0, 3) if r.random() < 0.4 else 0))"
           "(r.choice([\"\", \"kim\", \"robin\"]), r.choice([\"abcXYZ019\", \"abc\", \"ABC123\", \"abcdef ghi\", \"abcABC\"]))")
    wrongs = [("if password.strip() == \"\":", "if password == \"\":"),
              ("if username and username.lower() in password.lower():", "if username and username in password:"),
              ("    if not short:\n        if not any", "    if True:\n        if not any"),
              ("    elif len(password) > @MAXLEN@:", "    elif len(password) >= @MAXLEN@:")]
    return _make(v, "policy.py", "password_problems", _PW_SOL, _PW_PROMPT, edges, gen, wrongs)


# ------------------------------------------------------------------ alert level

_ALERT_SOL = """LEVELS = ["ok", "warn", "alarm", "critical"]


def alert_level(temp_c, pressure_kpa, smoke, door_open):
    if smoke or temp_c >= @CRIT@:
        return "critical"
    hits = 0
    if temp_c >= @WARN@:
        hits += 1
    if pressure_kpa < @LO@ or pressure_kpa > @HI@:
        hits += 1
    level = hits
    if door_open and level >= 1:
        level += 1
    return LEVELS[min(level, 3)]
"""

_ALERT_PROMPT = """Write a Python function `alert_level(temp_c, pressure_kpa, smoke, door_open)` in `sensors.py` for a server-room monitor. It returns one of the strings "ok", "warn", "alarm", "critical". `temp_c` and `pressure_kpa` are whole numbers, `smoke` and `door_open` are booleans. The rules:

1. Smoke detected (`smoke=True`) is always "critical", whatever the other readings.
2. A temperature of @CRIT@ or more is also "critical".
3. Otherwise count the warning conditions: a temperature of @WARN@ or more is one, a pressure below @LO@ or above @HI@ is another (@LO@ and @HI@ themselves are fine). No conditions is "ok", one is "warn", two is "alarm".
4. An open door (`door_open=True`) raises the result one step ("warn" becomes "alarm", "alarm" becomes "critical") but only when there is at least one warning condition; an open door alone leaves "ok" as "ok".
5. The result never goes above "critical"."""


def _alert(rng):
    warn = pick(rng, [60, 70, 75])
    v = dict(WARN=warn, CRIT=warn + pick(rng, [15, 20]), LO=pick(rng, [90, 95]), HI=pick(rng, [105, 110]))
    edges = ("[(@WARN@ - 1, @LO@, False, False), (@WARN@, @LO@, False, False), (@CRIT@ - 1, @LO@, False, False), (@CRIT@, @LO@, False, False), "
             "(20, @LO@ - 1, False, False), (20, @LO@, False, False), (20, @HI@, False, False), (20, @HI@ + 1, False, False), "
             "(@WARN@, @HI@ + 1, False, False), (@WARN@, @HI@ + 1, False, True), (20, @LO@, False, True), (20, @LO@ - 5, False, True), "
             "(@WARN@, 100, False, True), (@WARN@ - 1, 100, True, False), (@CRIT@ - 1, @LO@ - 1, False, True), (0, 100, False, False)]")
    gen = "(r.randint(@WARN@ - 30, @CRIT@ + 10), r.randint(@LO@ - 8, @HI@ + 8), r.random() < 0.1, r.random() < 0.5)"
    wrongs = [("if pressure_kpa < @LO@ or pressure_kpa > @HI@:", "if pressure_kpa <= @LO@ or pressure_kpa >= @HI@:"),
              ("    if door_open and level >= 1:", "    if door_open:"),
              ("    if smoke or temp_c >= @CRIT@:", "    if temp_c >= @CRIT@:"),
              ("    return LEVELS[min(level, 3)]", "    return LEVELS[min(level, 2)]")]
    return _make(v, "sensors.py", "alert_level", _ALERT_SOL, _ALERT_PROMPT, edges, gen, wrongs)


# ------------------------------------------------------------------ income tax split

_TAX_SOL = """def income_tax(income, dependents):
    if income < 0 or dependents < 0:
        raise ValueError("bad input")
    taxable = max(income - @DED@, 0)
    if taxable == 0:
        return 0
    low = min(taxable, @B1@)
    mid = min(max(taxable - @B1@, 0), @B2@ - @B1@)
    high = max(taxable - @B2@, 0)
    tax100 = low * 10 + mid * 20 + high * 30
    tax100 = min(tax100, income * @CAP@)
    tax100 -= dependents * @CREDIT@ * 100
    if tax100 <= 0:
        return 0
    return (tax100 + 50) // 100
"""

_TAX_PROMPT = """Write a Python function `income_tax(income, dependents)` in `tax.py` that returns the yearly tax owed, as a whole number of dollars. `income` is a whole number of dollars. The rules:

1. First subtract a standard deduction of @DED@ from the income (not going below 0). If nothing taxable is left, the tax is 0.
2. The taxable amount is taxed in bands: the first @B1@ at 10%, the part from @B1@ up to @B2@ at 20%, and everything above @B2@ at 30%.
3. The tax from rule 2 is capped at @CAP@% of the gross `income` (before the deduction).
4. Then each dependent takes @CREDIT@ dollars off the tax, but the tax never goes below 0.
5. Work in exact fractions of a dollar until the end and round only once, to the nearest dollar with exact halves rounding up (for example in whole-number arithmetic: hundredths of dollars, then `(x + 50) // 100`).
6. A negative income or negative number of dependents raises `ValueError`."""


def _tax(rng):
    v = dict(DED=pick(rng, [1000, 1200, 1500]), B1=pick(rng, [10000, 12000]), B2=pick(rng, [40000, 45000]),
             CAP=pick(rng, [20, 22, 25]), CREDIT=pick(rng, [200, 250, 300]))
    edges = ("[(0, 0), (@DED@, 0), (@DED@ + 1, 0), (@DED@ + 7, 3), (@DED@ + @B1@, 0), (@DED@ + @B1@ + 1, 0), (@DED@ + @B2@, 1), "
             "(@DED@ + @B2@ + 1, 0), (@DED@ + @B2@ + 5, 2), (70000, 0), (120000, 0), (120000, 3), (200001, 2), (@DED@ + 3335, 1), (@DED@ + 15, 0)]")
    gen = "(r.choice([r.randint(0, 3000), r.randint(0, 60000), r.randint(0, 150000)]), r.randint(0, 6))"
    raises = "[(-1, 0), (1000, -1)]"
    wrongs = [("    tax100 = min(tax100, income * @CAP@)\n    tax100 -= dependents * @CREDIT@ * 100",
               "    tax100 -= dependents * @CREDIT@ * 100\n    tax100 = min(tax100, income * @CAP@)"),
              ("    return (tax100 + 50) // 100", "    return tax100 // 100"),
              ("    high = max(taxable - @B2@, 0)", "    high = max(taxable - @B2@ - 1, 0)"),
              ("    tax100 = min(tax100, income * @CAP@)\n", "    tax100 = min(tax100, taxable * @CAP@)\n")]
    return _make(v, "tax.py", "income_tax", _TAX_SOL, _TAX_PROMPT, edges, gen, wrongs, raises)


# ------------------------------------------------------------------ loyalty points

_LOYAL_SOL = """TIER = {"bronze": 100, "silver": 125, "gold": 150}


def points_earned(amount_cents, tier, items, double_day):
    if tier not in TIER:
        raise ValueError("unknown tier")
    if amount_cents <= 0:
        return 0
    pts = amount_cents // 100
    pts = pts * TIER[tier] // 100
    if double_day and amount_cents >= @DBLMIN@:
        pts *= 2
    if items >= @ITEMS@:
        pts += 20
    return min(pts, @CAP@)
"""

_LOYAL_PROMPT = """Write a Python function `points_earned(amount_cents, tier, items, double_day)` in `loyalty.py` that returns the loyalty points (a whole number) a shop gives for one purchase. The rules:

1. The base is 1 point for every full 100 cents spent (so 199 cents is 1 point).
2. The member tier then scales the base: "bronze" 100%, "silver" 125%, "gold" 150%, rounding down to a whole number of points.
3. On a double-points day (`double_day=True`) the points so far are doubled, but only if the purchase was at least @DBLMIN@ cents.
4. A purchase of @ITEMS@ or more items (`items`) adds a flat bonus of 20 points; this bonus is added after doubling and is never doubled.
5. A purchase never earns more than @CAP@ points in total, bonus included.
6. An amount of 0 or less earns 0 points. An unknown tier raises `ValueError`, checked before anything else (even for an amount of 0)."""


def _loyal(rng):
    v = dict(DBLMIN=pick(rng, [2000, 3000]), ITEMS=pick(rng, [8, 10]), CAP=pick(rng, [300, 400, 500]))
    edges = ("[(199, \"gold\", 0, False), (199, \"silver\", 0, False), (1000, \"silver\", 0, False), (333, \"silver\", 0, False), "
             "(@DBLMIN@ - 1, \"bronze\", 0, True), (@DBLMIN@, \"bronze\", 0, True), (@DBLMIN@ + 150, \"gold\", 3, True), "
             "(1500, \"gold\", @ITEMS@, True), (1500, \"bronze\", @ITEMS@ - 1, False), (1500, \"bronze\", @ITEMS@, False), "
             "(50000, \"gold\", 0, False), (50000, \"bronze\", @ITEMS@, False), (90000, \"gold\", 2, True), (0, \"gold\", 20, True), "
             "(-5, \"silver\", 1, False), (@DBLMIN@ * 9, \"silver\", @ITEMS@, True)]")
    gen = "(r.randint(-100, 90000), r.choice([\"bronze\", \"silver\", \"gold\"]), r.randint(0, 14), r.random() < 0.5)"
    raises = "[(1000, \"platinum\", 1, False), (0, \"vip\", 0, False)]"
    wrongs = [("    pts = pts * TIER[tier] // 100", "    pts = amount_cents * TIER[tier] // 10000"),
              ("    if items >= @ITEMS@:\n        pts += 20\n    return min(pts, @CAP@)",
               "    pts = min(pts, @CAP@)\n    if items >= @ITEMS@:\n        pts += 20\n    return pts"),
              ("    if double_day and amount_cents >= @DBLMIN@:\n        pts *= 2\n    if items >= @ITEMS@:\n        pts += 20",
               "    if items >= @ITEMS@:\n        pts += 20\n    if double_day and amount_cents >= @DBLMIN@:\n        pts *= 2"),
              ("amount_cents >= @DBLMIN@", "amount_cents > @DBLMIN@")]
    return _make(v, "loyalty.py", "points_earned", _LOYAL_SOL, _LOYAL_PROMPT, edges, gen, wrongs, raises)


# ------------------------------------------------------------------ meeting slot score

_SLOT_SOL = """def slot_score(start_min, duration_min, free_pct, friday):
    end = start_min + duration_min
    if start_min < @OPEN@ or end > @CLOSE@:
        return 0
    score = free_pct
    if start_min < 780 and end > 720:
        score -= 25
    if friday and start_min >= 900:
        score -= 15
    if duration_min > 60:
        score -= 10 * ((duration_min - 60) // 30)
    if free_pct < 50:
        score = min(score, 40)
    return max(score, 0)
"""

_SLOT_PROMPT = """Write a Python function `slot_score(start_min, duration_min, free_pct, friday)` in `slots.py` that scores a proposed meeting slot from 0 (bad) to 100 (best), as a whole number. Times are whole minutes since midnight (09:00 is 540). `free_pct` is the whole-number percentage (0 to 100) of invited people who are free. The rules:

1. The slot must lie inside working hours, from @OPEN@ (start allowed) to @CLOSE@ (the meeting may end exactly at @CLOSE@). A slot that starts earlier or ends later scores 0, whatever else is true.
2. Otherwise the score starts at `free_pct`.
3. A slot that overlaps lunch (12:00 to 13:00, minutes 720 to 780) loses 25 points. A slot that only touches lunch, for example one that ends exactly at 720 or starts exactly at 780, does not overlap it.
4. On a Friday (`friday=True`) a slot that starts at 15:00 (minute 900) or later loses 15 points.
5. Every full 30 minutes beyond the first 60 minutes of the duration loses 10 points (a 100 minute meeting loses 10, a 120 minute one loses 20).
6. If `free_pct` is below 50 the score is capped at 40 (after the deductions above).
7. The score never goes below 0."""


def _slot(rng):
    v = dict(OPEN=pick(rng, [480, 540]), CLOSE=pick(rng, [1020, 1080]))
    edges = ("[(@OPEN@ - 1, 30, 100, False), (@OPEN@, 30, 100, False), (@CLOSE@ - 30, 30, 100, False), (@CLOSE@ - 29, 30, 100, False), "
             "(660, 60, 80, False), (660, 61, 80, False), (780, 30, 80, False), (779, 30, 80, False), (900, 60, 90, True), "
             "(899, 60, 90, True), (900, 60, 90, False), (600, 100, 90, False), (600, 119, 90, False), (600, 120, 90, False), "
             "(600, 90, 49, False), (600, 90, 50, False), (600, 30, 49, False), (720, 240, 60, True), (840, 180, 10, True), (600, 30, 0, False)]")
    gen = "(r.randrange(420, 1100, 15), r.choice([15, 30, 45, 60, 75, 90, 105, 120, 150, 180]), r.randint(0, 100), r.random() < 0.4)"
    wrongs = [("end > @CLOSE@", "end >= @CLOSE@"),
              ("    if free_pct < 50:\n        score = min(score, 40)\n", ""),
              ("    if start_min < 780 and end > 720:", "    if start_min <= 780 and end >= 720:"),
              ("    if friday and start_min >= 900:", "    if friday and start_min > 900:"),
              ("        score -= 10 * ((duration_min - 60) // 30)", "        score -= 10 * ((duration_min - 45) // 30)")]
    return _make(v, "slots.py", "slot_score", _SLOT_SOL, _SLOT_PROMPT, edges, gen, wrongs)


# ------------------------------------------------------------------ delivery window

_WIN_SOL = """WINDOWS = ["today afternoon", "today evening", "tomorrow morning", "tomorrow afternoon"]


def delivery_window(placed_min, express, weight_kg):
    if placed_min >= @LATE@:
        return WINDOWS[3]
    if placed_min < @EARLY@:
        idx = 0
    elif placed_min < @CUT@:
        idx = 1
    else:
        idx = 2
    if express and placed_min < 1200:
        idx = max(idx - 1, 0)
    if weight_kg >= @HEAVY@:
        idx += 1
    return WINDOWS[min(idx, 3)]
"""

_WIN_PROMPT = """Write a Python function `delivery_window(placed_min, express, weight_kg)` in `courier.py` that picks the delivery window for an order. `placed_min` is the minute of the day the order was placed (0 to 1439; 09:00 is 540), `express` a boolean, `weight_kg` a whole number. The windows, from earliest to latest, are exactly the strings "today afternoon", "today evening", "tomorrow morning", "tomorrow afternoon". The rules:

1. An order placed at @LATE@ or later always gets "tomorrow afternoon"; nothing below changes that.
2. Otherwise a standard order gets "today afternoon" if placed before @EARLY@, "today evening" if placed from @EARLY@ up to (not including) @CUT@, and "tomorrow morning" from @CUT@ on.
3. An express order (`express=True`) placed before 1200 (20:00) moves one window earlier than the standard window, but never earlier than "today afternoon". After 1200 express makes no difference.
4. A heavy parcel (`weight_kg` of @HEAVY@ or more) then moves one window later, after rule 3 has been applied.
5. No result is later than "tomorrow afternoon"."""


def _window(rng):
    v = dict(EARLY=pick(rng, [540, 600]), CUT=pick(rng, [900, 960]), LATE=pick(rng, [1260, 1320]), HEAVY=pick(rng, [25, 30]))
    edges = ("[(0, False, 1), (@EARLY@ - 1, False, 1), (@EARLY@, False, 1), (@CUT@ - 1, False, 1), (@CUT@, False, 1), "
             "(@EARLY@ - 1, True, 1), (@EARLY@, True, 1), (@CUT@, True, 1), (@CUT@, True, @HEAVY@ - 1), (@CUT@, True, @HEAVY@), "
             "(@EARLY@ - 1, True, @HEAVY@), (@CUT@ - 1, False, @HEAVY@), (@CUT@, False, @HEAVY@), (1199, True, 1), (1200, True, 1), "
             "(1200, False, 1), (@LATE@ - 1, False, 1), (@LATE@ - 1, False, @HEAVY@), (@LATE@, False, 1), (1439, True, 1)]")
    gen = "(r.randint(0, 1439), r.random() < 0.5, r.randint(1, 60))"
    wrongs = [("if placed_min >= @LATE@:", "if placed_min > @LATE@:"),
              ("    if express and placed_min < 1200:", "    if express and placed_min <= 1200:"),
              ("    if weight_kg >= @HEAVY@:", "    if weight_kg > @HEAVY@:"),
              ("    elif placed_min < @CUT@:", "    elif placed_min <= @CUT@:"),
              ("    idx = max(idx - 1, 0)", "    idx = idx - 1\n        if idx < 0:\n            idx = 1")]
    # the last wrong answer is syntactically indented with 8 spaces inside the if block; fix its shape
    wrongs[4] = ("        idx = max(idx - 1, 0)", "        idx = max(idx - 1, 0) if idx else 1")
    return _make(v, "courier.py", "delivery_window", _WIN_SOL, _WIN_PROMPT, edges, gen, wrongs)


# ------------------------------------------------------------------ discount stacking

_DISC_SOL = """def final_price(unit_cents, qty, coupon, member):
    if qty < 1:
        raise ValueError("qty must be at least 1")
    code = coupon.upper() if coupon else None
    if code not in (None, "SAVE10", "HALF", "FIVEOFF"):
        raise ValueError("unknown coupon")
    total = unit_cents * qty
    qd = 80 if qty >= @Q2@ else 90 if qty >= @Q1@ else 100
    md = @MD@ if member else 100
    cd = 90 if code == "SAVE10" else 100
    if code == "HALF":
        qd, md, cd = 100, 100, 50
    price = total * qd * md * cd // 1000000
    if code == "FIVEOFF":
        price -= @FLAT@
    return max(price, total * 20 // 100)
"""

_DISC_PROMPT = """Write a Python function `final_price(unit_cents, qty, coupon, member)` in `checkout.py` that returns the total price of an order in whole cents. `coupon` is a string or `None`. The rules:

1. Quantity discount: @Q1@ or more units take 10% off, @Q2@ or more units take 20% off (the better one only).
2. Members (`member=True`) take a further @MEM@% off.
3. Coupons are matched ignoring upper/lower case: "SAVE10" takes a further 10% off; "HALF" takes 50% off the undiscounted total and replaces the quantity and member discounts (they are not applied at all); "FIVEOFF" takes a flat @FLAT@ cents off after all the percentage discounts. `None` (or an empty string) means no coupon.
4. All percentage discounts that apply are multiplied together (each one applies to the price left by the others) and the result is rounded down to a whole cent once. Use whole-number arithmetic, for example `total * qd * md * cd // 1000000` with each factor a remaining percentage.
5. The order never costs less than 20% of the undiscounted total (rounded down), checked last, after the flat amount is taken off.
6. A `qty` below 1 or an unknown coupon raises `ValueError`."""


def _disc(rng):
    mem = pick(rng, [5, 10])
    v = dict(Q1=pick(rng, [5, 6]), Q2=pick(rng, [10, 12]), MEM=mem, MD=100 - mem, FLAT=pick(rng, [300, 500]))
    edges = ("[(1000, 1, None, False), (1000, @Q1@ - 1, None, False), (1000, @Q1@, None, False), (1000, @Q2@ - 1, None, False), "
             "(1000, @Q2@, None, False), (1999, 3, None, True), (1999, @Q1@, \"save10\", True), (1999, @Q2@, \"SAVE10\", True), "
             "(1999, @Q2@, \"HALF\", True), (1999, 1, \"half\", False), (2500, 2, \"FIVEOFF\", False), (100, 1, \"FiveOff\", False), "
             "(300, 1, \"FIVEOFF\", True), (4999, @Q2@ + 3, \"FIVEOFF\", True), (777, 7, \"\", True), (1233, 3, \"SAVE10\", False)]")
    gen = ("(r.randint(100, 5000), r.randint(1, 15), r.choice([None, \"SAVE10\", \"half\", \"HALF\", \"FiveOff\", \"FIVEOFF\", \"\"]), "
           "r.random() < 0.5)")
    raises = "[(1000, 1, \"BOGUS\", False), (1000, 0, None, False)]"
    wrongs = [("        qd, md, cd = 100, 100, 50", "        cd = 50"),
              ("    return max(price, total * 20 // 100)",
               "    return price"),
              ("    qd = 80 if qty >= @Q2@ else 90 if qty >= @Q1@ else 100", "    qd = 80 if qty > @Q2@ else 90 if qty > @Q1@ else 100"),
              ("    price = total * qd * md * cd // 1000000\n    if code == \"FIVEOFF\":\n        price -= @FLAT@\n    return max(price, total * 20 // 100)",
               "    price = max(total * qd * md * cd // 1000000, total * 20 // 100)\n    if code == \"FIVEOFF\":\n        price -= @FLAT@\n    return max(price, 0)")]
    return _make(v, "checkout.py", "final_price", _DISC_SOL, _DISC_PROMPT, edges, gen, wrongs, raises)


# ------------------------------------------------------------------ course grade

_GRADE_SOL = """def final_grade(scores, late_count, extra_credit):
    if not scores:
        raise ValueError("no scores")
    kept = sorted(scores)[1:] if len(scores) >= @DROPAT@ else list(scores)
    avg = (2 * sum(kept) + len(kept)) // (2 * len(kept))
    avg = max(0, avg - @LATEPEN@ * late_count)
    avg = min(100, avg + min(extra_credit, @MAXEC@))
    if avg >= 90:
        letter = "A"
    elif avg >= 80:
        letter = "B"
    elif avg >= 70:
        letter = "C"
    elif avg >= 60:
        letter = "D"
    else:
        letter = "F"
    if 0 in kept and letter in ("A", "B"):
        letter = "C"
    return avg, letter
"""

_GRADE_PROMPT = """Write a Python function `final_grade(scores, late_count, extra_credit)` in `grading.py` that returns a tuple `(points, letter)` for a course. `scores` is a list of whole-number assignment scores from 0 to 100 (a missing assignment is a 0), `late_count` the number of late submissions, `extra_credit` a whole number of bonus points. The rules:

1. If there are @DROPAT@ or more scores, the single lowest score is dropped first (only one, even if several are equal).
2. `points` starts as the average of the remaining scores, rounded to the nearest whole number with exact halves rounding up (use whole-number arithmetic, for example `(2 * total + n) // (2 * n)`).
3. Each late submission then takes @LATEPEN@ points off, but `points` cannot go below 0 at this step.
4. Then the extra credit is added, but at most @MAXEC@ points of it count, and `points` is capped at 100.
5. The letter is "A" for 90 or more, "B" for 80 or more, "C" for 70 or more, "D" for 60 or more, otherwise "F".
6. If any of the remaining scores (after the drop) is 0, the letter can be no better than "C": an "A" or "B" becomes "C". The points do not change.
7. An empty list of scores raises `ValueError`."""


def _grade(rng):
    v = dict(DROPAT=pick(rng, [4, 5]), LATEPEN=pick(rng, [1, 2, 3]), MAXEC=pick(rng, [3, 5]))
    edges = ("[([90], 0, 0), ([89], 0, 1), ([79, 80], 0, 0), ([70, 71], 0, 0), ([60, 59], 0, 0), ([100, 100, 100, 100], 0, 0), "
             "([0, 100, 100, 100, 100], 0, 0), ([0, 0, 100, 100, 100], 0, 0), ([0, 0, 0, 0, 0], 0, 20), ([95, 95, 95, 0], 0, 0), "
             "([50, 90, 90, 90, 90], 0, 0), ([85, 85, 85, 85, 85], 2, 0), ([85, 85, 85, 85, 85], 0, @MAXEC@ + 4), ([40], 9, 5), "
             "([3, 3, 3], 5, 4), ([98, 99, 100], 0, 9), ([60, 60, 60, 60, 61], 1, 0), ([69, 70, 70, 70], 0, 0), ([100, 50], 0, 0), ([0, 100], 0, 0)]")
    gen = "([0 if r.random() < 0.15 else r.randint(50, 100) for _ in range(r.randint(1, 8))], r.randint(0, 6), r.randint(0, 9))"
    raises = "[([], 0, 0)]"
    wrongs = [("    if 0 in kept and letter", "    if 0 in scores and letter"),
              ("    avg = max(0, avg - @LATEPEN@ * late_count)\n    avg = min(100, avg + min(extra_credit, @MAXEC@))",
               "    avg = avg - @LATEPEN@ * late_count + min(extra_credit, @MAXEC@)\n    avg = max(0, min(100, avg))"),
              ("    avg = min(100, avg + min(extra_credit, @MAXEC@))", "    avg = min(100, avg + extra_credit)"),
              ("    avg = (2 * sum(kept) + len(kept)) // (2 * len(kept))", "    avg = sum(kept) // len(kept)"),
              ("if len(scores) >= @DROPAT@", "if len(scores) > @DROPAT@")]
    return _make(v, "grading.py", "final_grade", _GRADE_SOL, _GRADE_PROMPT, edges, gen, wrongs, raises)


TEMPLATES = [
    probe_template("rules", "rules_shipping_fee", _shipping),
    probe_template("rules", "rules_weekly_pay", _pay),
    probe_template("rules", "rules_ticket_price", _ticket),
    probe_template("rules", "rules_late_fee", _late),
    probe_template("rules", "rules_password_problems", _password),
    probe_template("rules", "rules_alert_level", _alert),
    probe_template("rules", "rules_income_tax", _tax),
    probe_template("rules", "rules_loyalty_points", _loyal),
    probe_template("rules", "rules_slot_score", _slot),
    probe_template("rules", "rules_delivery_window", _window),
    probe_template("rules", "rules_discount_stack", _disc),
    probe_template("rules", "rules_course_grade", _grade),
]
