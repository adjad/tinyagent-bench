"""Rung 8: a small change that has to be made in two files.

Each case gives two small modules where one imports the other. The requested
change needs edits in both; changing only one of them leaves the hidden tests
failing. Hidden tests import both modules, check the new behaviour and check
that the old behaviour still works.
"""
from __future__ import annotations

import random

from edat.sidekick.ceiling.rungs import answer_files, fill, probe_instance, probe_template, show_file
from edat.sidekick.gen.core import pick


def _case(rng_vals: dict, prompt: str, given: dict, sol: dict, tests: str, extra=()):
    """Build the instance. `extra`: (file, old, new) edits of the solution, each a plausible near-miss."""
    v = rng_vals
    given = {k: fill(t, **v) for k, t in given.items()}
    sol = {k: fill(t, **v) for k, t in sol.items()}
    names = list(given)
    assert len(names) == 2
    w0 = {**given, names[0]: sol[names[0]]}
    w1 = {**given, names[1]: sol[names[1]]}
    wrong = [w0, w1]
    for fname, old, new in extra:
        old, new = fill(old, **v), fill(new, **v)
        assert old in sol[fname], (fname, old)
        wrong.append({**sol, fname: sol[fname].replace(old, new)})
    for w in wrong:
        assert w != sol
    shown = "\n\n".join(show_file(n, t) for n, t in given.items())
    return probe_instance(
        prompt=fill(prompt, **v) + "\n\nThe two files now:\n\n" + shown,
        targets=names, files=given, hidden_tests=fill(tests, **v),
        references=[answer_files(sol), answer_files(sol, style="label")],
        wrong=[answer_files(w) for w in wrong])


# ------------------------------------------------------------------ 1. ticket assignee

_TK_MODELS = r'''from dataclasses import dataclass


@dataclass
class Ticket:
    id: int
    title: str
    priority: int = @PRI@
'''
_TK_SERVICE = r'''from models import Ticket


def format_ticket(t):
    return "#%d %s (P%d)" % (t.id, t.title, t.priority)


def make_ticket(id, title, priority=@PRI@):
    return Ticket(id, title, priority)


def board(tickets):
    ordered = sorted(tickets, key=lambda t: (t.priority, t.id))
    return "\n".join(format_ticket(t) for t in ordered)
'''
_TK_MODELS_SOL = _TK_MODELS + '    @FIELD@: str = ""\n'
_TK_SERVICE_SOL = r'''from models import Ticket


def format_ticket(t):
    text = "#%d %s (P%d)" % (t.id, t.title, t.priority)
    if t.@FIELD@:
        text += " -> " + t.@FIELD@
    return text


def make_ticket(id, title, priority=@PRI@, @FIELD@=""):
    return Ticket(id, title, priority, @FIELD@)


def board(tickets):
    ordered = sorted(tickets, key=lambda t: (t.priority, t.id))
    return "\n".join(format_ticket(t) for t in ordered)
'''
_TK_TESTS = r'''import random

import pytest

from models import Ticket
from service import board, format_ticket, make_ticket


def _fmt(id, title, pri, who=""):
    text = "#%d %s (P%d)" % (id, title, pri)
    return text + " -> " + who if who else text


def test_default_is_empty():
    t = Ticket(1, "Login")
    assert t.@FIELD@ == ""
    assert format_ticket(t) == "#1 Login (P@PRI@)"


def test_positional_field():
    t = Ticket(4, "Fix", 2, "kim")
    assert t.@FIELD@ == "kim"
    assert format_ticket(t) == "#4 Fix (P2) -> kim"


def test_make_ticket():
    assert format_ticket(make_ticket(5, "X", 1, @FIELD@="sam")) == "#5 X (P1) -> sam"
    assert make_ticket(6, "Y").@FIELD@ == ""
    t = make_ticket(7, "Z", @FIELD@="lee")
    assert t.priority == @PRI@ and t.@FIELD@ == "lee"
    assert format_ticket(make_ticket(8, "Old", 3)) == "#8 Old (P3)"


def test_board_mixed():
    ts = [make_ticket(3, "c", 2), make_ticket(1, "a", 2, @FIELD@="ann"), make_ticket(2, "b", 1)]
    assert board(ts) == "#2 b (P1)\n#1 a (P2) -> ann\n#3 c (P2)"
    assert board([]) == ""


def test_random():
    rng = random.Random(1234)
    for _ in range(200):
        ts, want = [], []
        for i in range(rng.randint(0, 6)):
            who = rng.choice(["", "", "bo", "cy d"])
            ts.append(make_ticket(i, "t%d" % rng.randint(0, 9), rng.randint(1, 4), @FIELD@=who))
        want = [_fmt(t.id, t.title, t.priority, t.@FIELD@) for t in sorted(ts, key=lambda t: (t.priority, t.id))]
        assert board(ts) == "\n".join(want)
'''


def _ticket(rng):
    v = dict(PRI=pick(rng, [2, 3]), FIELD=pick(rng, ["assignee", "owner"]))
    prompt = ("The ticket board needs to show who a ticket is assigned to. Change `models.py` and `service.py`:\n\n"
              "1. `Ticket` gets a new last field `@FIELD@` (a string, default `\"\"`).\n"
              "2. `make_ticket` gets a new last keyword parameter `@FIELD@` (default `\"\"`) and passes it on.\n"
              "3. `format_ticket` adds ` -> <@FIELD@>` to the end of the text when the field is not empty, "
              "for example `#4 Fix (P2) -> kim`. With an empty field the text stays exactly as it is now.\n\n"
              "`board` needs no change in behaviour.")
    return _case(v, prompt, {"models.py": _TK_MODELS, "service.py": _TK_SERVICE},
                 {"models.py": _TK_MODELS_SOL, "service.py": _TK_SERVICE_SOL}, _TK_TESTS,
                 extra=[("service.py", 'text += " -> " + t.@FIELD@', 'text += " ->" + t.@FIELD@'),
                        ("service.py", "    if t.@FIELD@:\n        text += ", "    if True:\n        text += ")])


# ------------------------------------------------------------------ 2. dry run

_PG_CLEAN = r'''def @FN@(cache, keys):
    removed = []
    for key in keys:
        if key in cache:
            del cache[key]
            removed.append(key)
    return removed
'''
_PG_MAINT = r'''from cleanup import @FN@


def nightly(cache, prefix):
    stale = sorted(k for k in cache if k.startswith(prefix))
    removed = @FN@(cache, stale)
    return {"removed": removed, "count": len(removed)}
'''
_PG_CLEAN_SOL = r'''def @FN@(cache, keys, dry_run=False):
    removed = []
    for key in keys:
        if key in cache and key not in removed:
            if not dry_run:
                del cache[key]
            removed.append(key)
    return removed
'''
_PG_MAINT_SOL = r'''from cleanup import @FN@


def nightly(cache, prefix, dry_run=False):
    stale = sorted(k for k in cache if k.startswith(prefix))
    removed = @FN@(cache, stale, dry_run=dry_run)
    return {"removed": removed, "count": len(removed), "dry_run": dry_run}
'''
_PG_TESTS = r'''import random

import cleanup
import maint


def _ref(cache, keys, dry_run=False):
    removed = []
    for k in keys:
        if k in cache and k not in removed:
            removed.append(k)
    if not dry_run:
        for k in removed:
            del cache[k]
    return removed


def _fn(*a, **kw):
    return getattr(cleanup, "@FN@")(*a, **kw)


def test_real_run_unchanged():
    c = {"a": 1, "b": 2, "c": 3}
    assert _fn(c, ["b", "zz", "a"]) == ["b", "a"]
    assert c == {"c": 3}


def test_duplicates_listed_once():
    c = {"a": 1, "b": 2}
    assert _fn(c, ["a", "a", "b", "a"]) == ["a", "b"]
    assert c == {}


def test_dry_run_keeps_cache():
    c = {"a": 1, "b": 2, "c": 3}
    assert _fn(c, ["c", "x", "a", "c"], dry_run=True) == ["c", "a"]
    assert c == {"a": 1, "b": 2, "c": 3}
    assert _fn(c, ["b"], True) == ["b"]
    assert "b" in c


def test_nightly_default():
    c = {"@PFX@1": 1, "@PFX@2": 2, "keep": 3}
    assert maint.nightly(c, "@PFX@") == {"removed": ["@PFX@1", "@PFX@2"], "count": 2, "dry_run": False}
    assert c == {"keep": 3}


def test_nightly_dry_run():
    c = {"@PFX@1": 1, "keep": 3}
    r = maint.nightly(c, "@PFX@", dry_run=True)
    assert r == {"removed": ["@PFX@1"], "count": 1, "dry_run": True}
    assert c == {"@PFX@1": 1, "keep": 3}
    assert maint.nightly(c, "nothing", dry_run=True) == {"removed": [], "count": 0, "dry_run": True}


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        base = {rng.choice("abcdef"): 1 for _ in range(rng.randint(0, 6))}
        keys = [rng.choice("abcdefg") for _ in range(rng.randint(0, 8))]
        dry = rng.random() < 0.5
        c1, c2 = dict(base), dict(base)
        assert _fn(c1, keys, dry_run=dry) == _ref(c2, keys, dry)
        assert c1 == c2
'''


def _dryrun(rng):
    v = dict(FN=pick(rng, ["purge", "evict"]), PFX=pick(rng, ["tmp:", "sess:", "job:"]))
    prompt = ("Add a dry-run option to the nightly cache clean-up. Change `cleanup.py` and `maint.py`:\n\n"
              "1. `@FN@(cache, keys, dry_run=False)`: with `dry_run=True` it returns the same list it would return "
              "for a real run but does not change `cache`. A key listed twice in `keys` appears once in the result "
              "(in both modes).\n"
              "2. `nightly(cache, prefix, dry_run=False)` passes the option on and adds the key `\"dry_run\"` "
              "(the value it was given) to the dict it returns. The existing keys stay.\n\n"
              "A real run behaves as before.")
    return _case(v, prompt, {"cleanup.py": _PG_CLEAN, "maint.py": _PG_MAINT},
                 {"cleanup.py": _PG_CLEAN_SOL, "maint.py": _PG_MAINT_SOL}, _PG_TESTS,
                 extra=[("cleanup.py", " and key not in removed", ""),
                        ("cleanup.py", "            if not dry_run:\n                del cache[key]\n",
                         "            del cache[key]\n")])


# ------------------------------------------------------------------ 3. line discount

_DS_PRICING = r'''def line_total(price, qty):
    if qty < 0:
        raise ValueError("qty must not be negative")
    return round(price * qty, 2)
'''
_DS_CHECKOUT = r'''from pricing import line_total


def order_total(items):
    return round(sum(line_total(price, qty) for price, qty in items), 2)
'''
_DS_PRICING_SOL = r'''def line_total(price, qty, discount_pct=0):
    if qty < 0:
        raise ValueError("qty must not be negative")
    if not 0 <= discount_pct <= @MAXD@:
        raise ValueError("discount out of range")
    return round(price * qty * (1 - discount_pct / 100), 2)
'''
_DS_CHECKOUT_SOL = r'''from pricing import line_total


def order_total(items, discount_pct=0):
    return round(sum(line_total(price, qty, discount_pct) for price, qty in items), 2)
'''
_DS_TESTS = r'''import random

import pytest

from checkout import order_total
from pricing import line_total


def _ref_line(price, qty, d=0):
    if qty < 0 or not 0 <= d <= @MAXD@:
        raise ValueError
    return round(price * qty * (1 - d / 100), 2)


def _ref_order(items, d=0):
    return round(sum(_ref_line(p, q, d) for p, q in items), 2)


def test_old_behaviour():
    assert line_total(4, 3) == 12
    assert order_total([(4, 3), (2.5, 2)]) == 17
    assert order_total([]) == 0
    with pytest.raises(ValueError):
        line_total(1, -1)


@pytest.mark.parametrize("p,q,d", [(10, 2, 10), (19.99, 3, 25), (5, 0, 20), (8, 1, @MAXD@), (3, 7, 0), (12, 2, 5)])
def test_line_discount(p, q, d):
    assert line_total(p, q, d) == pytest.approx(_ref_line(p, q, d), abs=0.011)
    assert line_total(p, q, discount_pct=d) == pytest.approx(_ref_line(p, q, d), abs=0.011)


@pytest.mark.parametrize("d", [-1, @MAXD@ + 1, 150])
def test_bad_discount(d):
    with pytest.raises(ValueError):
        line_total(10, 1, d)


def test_order_discount():
    items = [(10, 2), (4, 5), (1.5, 3)]
    assert order_total(items, 20) == pytest.approx(_ref_order(items, 20), abs=0.011)
    assert order_total(items, discount_pct=0) == pytest.approx(_ref_order(items), abs=0.011)
    with pytest.raises(ValueError):
        order_total(items, @MAXD@ + 5)


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        items = [(rng.randint(1, 40) / 2, rng.randint(0, 6)) for _ in range(rng.randint(1, 5))]
        d = rng.choice([0, 5, 10, 15, 25, @MAXD@])
        assert order_total(items, d) == pytest.approx(_ref_order(items, d), abs=0.02 * len(items))
'''


def _discount(rng):
    v = dict(MAXD=pick(rng, [50, 60, 80]))
    prompt = ("Add a percentage discount to the order maths. Change `pricing.py` and `checkout.py`:\n\n"
              "1. `line_total(price, qty, discount_pct=0)` takes a percentage off the line: the result is "
              "`price * qty * (1 - discount_pct / 100)` rounded to 2 decimals. A `discount_pct` below 0 or above "
              "@MAXD@ raises `ValueError`. The negative-quantity check stays.\n"
              "2. `order_total(items, discount_pct=0)` applies the same discount to every line (each line rounded "
              "by `line_total`) and still rounds the sum to 2 decimals.\n\n"
              "Calls without a discount give the same results as now.")
    return _case(v, prompt, {"pricing.py": _DS_PRICING, "checkout.py": _DS_CHECKOUT},
                 {"pricing.py": _DS_PRICING_SOL, "checkout.py": _DS_CHECKOUT_SOL}, _DS_TESTS,
                 extra=[("pricing.py", "    if not 0 <= discount_pct <= @MAXD@:\n        raise ValueError(\"discount out of range\")\n", ""),
                        ("checkout.py", "line_total(price, qty, discount_pct)", "line_total(price, qty)")])


# ------------------------------------------------------------------ 4. rename with alias

_RN_STORAGE = r'''def save_record(store, key, record):
    """Store a copy of record under key; return True when the key is new."""
    is_new = key not in store
    store[key] = dict(record)
    return is_new


def load_record(store, key):
    return dict(store[key])
'''
_RN_API = r'''import storage


def create_user(store, name, email):
    return storage.save_record(store, "@PFX@" + name, {"name": name, "email": email})


def update_user(store, name, **fields):
    rec = storage.load_record(store, "@PFX@" + name)
    rec.update(fields)
    storage.save_record(store, "@PFX@" + name, rec)
    return rec
'''
_RN_STORAGE_SOL = r'''def @NEW@(store, key, record):
    """Store a copy of record under key; return True when the key is new."""
    is_new = key not in store
    store[key] = dict(record)
    return is_new


def save_record(store, key, record):
    """Old name, kept so existing callers keep working."""
    return @NEW@(store, key, record)


def load_record(store, key):
    return dict(store[key])
'''
_RN_API_SOL = _RN_API.replace("storage.save_record", "storage.@NEW@")
_RN_TESTS = r'''import pytest

import api
import storage


def test_new_name_works():
    s = {}
    assert storage.@NEW@(s, "k", {"a": 1}) is True
    assert storage.@NEW@(s, "k", {"a": 2}) is False
    assert s == {"k": {"a": 2}}
    rec = {"x": 1}
    storage.@NEW@(s, "j", rec)
    rec["x"] = 9
    assert storage.load_record(s, "j") == {"x": 1}


def test_old_name_still_works():
    s = {}
    assert storage.save_record(s, "k", {"a": 1}) is True
    assert storage.save_record(s, "k", {"a": 1}) is False
    assert s == {"k": {"a": 1}}


def test_old_name_calls_new(monkeypatch):
    seen = []
    monkeypatch.setattr(storage, "@NEW@", lambda store, key, record: seen.append((key, record)) or "sentinel")
    assert storage.save_record({}, "kk", {"v": 1}) == "sentinel"
    assert seen == [("kk", {"v": 1})]


def test_api_uses_new_name(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("api still calls the old name")

    monkeypatch.setattr(storage, "save_record", boom)
    s = {}
    assert api.create_user(s, "ann", "a@example.test") is True
    assert api.create_user(s, "ann", "a@example.test") is False
    assert api.update_user(s, "ann", email="b@example.test") == {"name": "ann", "email": "b@example.test"}
    assert s["@PFX@ann"]["email"] == "b@example.test"


def test_api_behaviour():
    s = {}
    api.create_user(s, "bo", "bo@example.test")
    api.update_user(s, "bo", email="x@example.test", city="Oslo")
    assert storage.load_record(s, "@PFX@bo") == {"name": "bo", "email": "x@example.test", "city": "Oslo"}
    with pytest.raises(KeyError):
        api.update_user(s, "nobody", email="z")
'''


def _rename(rng):
    v = dict(NEW=pick(rng, ["write_record", "put_record"]), PFX=pick(rng, ["user:", "u/", "acct-"]))
    prompt = ("Rename `save_record` in `storage.py` to `@NEW@`. The old name must keep working: `save_record` stays "
              "as a thin alias that just calls `@NEW@` and returns its result. In `api.py`, every call must now use "
              "the new name `@NEW@` (nothing in `api.py` should call `save_record` any more). Behaviour is otherwise "
              "unchanged.")
    return _case(v, prompt, {"storage.py": _RN_STORAGE, "api.py": _RN_API},
                 {"storage.py": _RN_STORAGE_SOL, "api.py": _RN_API_SOL}, _RN_TESTS,
                 extra=[("storage.py", "    return @NEW@(store, key, record)\n",
                         "    is_new = key not in store\n    store[key] = dict(record)\n    return is_new\n")])


# ------------------------------------------------------------------ 5. out of stock

_OS_INV = r'''def reserve(stock, sku, qty):
    """Take qty units of sku out of stock (in place)."""
    stock[sku] = stock.get(sku, 0) - qty
'''
_OS_ORD = r'''from inventory import reserve


def place_order(stock, lines):
    for sku, qty in lines:
        reserve(stock, sku, qty)
    return {"ok": True, "stock": stock}
'''
_OS_INV_SOL = r'''class OutOfStock(Exception):
    def __init__(self, sku, short):
        super().__init__("%s short by %d" % (sku, short))
        self.sku = sku
        self.short = short


def reserve(stock, sku, qty):
    """Take qty units of sku out of stock (in place)."""
    have = stock.get(sku, 0)
    if have < qty:
        raise OutOfStock(sku, qty - have)
    stock[sku] = have - qty
'''
_OS_ORD_SOL = r'''from inventory import OutOfStock, reserve


def place_order(stock, lines):
    work = dict(stock)
    try:
        for sku, qty in lines:
            reserve(work, sku, qty)
    except OutOfStock as e:
        return {"ok": False, "error": "@MSG@: %s (short %d)" % (e.sku, e.short)}
    stock.update(work)
    return {"ok": True, "stock": stock}
'''
_OS_TESTS = r'''import random

import pytest

from inventory import OutOfStock, reserve
from orders import place_order


def _ref(stock, lines):
    work = dict(stock)
    for sku, qty in lines:
        have = work.get(sku, 0)
        if have < qty:
            return {"ok": False, "error": "@MSG@: %s (short %d)" % (sku, qty - have)}, stock
        work[sku] = have - qty
    return {"ok": True}, work


def test_reserve_ok_and_error():
    s = {"a": 5}
    reserve(s, "a", 5)
    assert s == {"a": 0}
    with pytest.raises(OutOfStock) as e:
        reserve(s, "a", 2)
    assert e.value.sku == "a" and e.value.short == 2
    assert s == {"a": 0}
    with pytest.raises(OutOfStock) as e:
        reserve(s, "zz", 3)
    assert e.value.short == 3 and "zz" not in s


def test_order_ok_is_in_place():
    s = {"a": 5, "b": 2}
    r = place_order(s, [("a", 2), ("b", 2), ("a", 3)])
    assert r["ok"] is True and r["stock"] is s
    assert s == {"a": 0, "b": 0}


def test_order_failure_changes_nothing():
    s = {"a": 5, "b": 1}
    r = place_order(s, [("a", 3), ("b", 4), ("a", 1)])
    assert r == {"ok": False, "error": "@MSG@: b (short 3)"}
    assert s == {"a": 5, "b": 1}


def test_same_sku_twice_counts_total():
    s = {"a": 4}
    r = place_order(s, [("a", 3), ("a", 3)])
    assert r == {"ok": False, "error": "@MSG@: a (short 2)"}
    assert s == {"a": 4}


def test_unknown_sku_and_empty():
    s = {"a": 1}
    assert place_order(s, [("nope", 2)]) == {"ok": False, "error": "@MSG@: nope (short 2)"}
    assert place_order(s, []) == {"ok": True, "stock": {"a": 1}}


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        stock = {k: rng.randint(0, 5) for k in "abc" if rng.random() < 0.8}
        lines = [(rng.choice("abcd"), rng.randint(1, 4)) for _ in range(rng.randint(0, 4))]
        want, after = _ref(stock, lines)
        s = dict(stock)
        got = place_order(s, lines)
        if want["ok"]:
            assert got["ok"] is True and s == after
        else:
            assert got == want and s == stock
'''


def _stock(rng):
    v = dict(MSG=pick(rng, ["out of stock", "insufficient stock"]))
    prompt = ("Stop the stock level going negative. Change `inventory.py` and `orders.py`:\n\n"
              "1. `inventory.py` gets `class OutOfStock(Exception)` with attributes `sku` and `short` (how many units "
              "are missing), created as `OutOfStock(sku, short)`. `reserve(stock, sku, qty)` raises it when "
              "`stock.get(sku, 0) < qty` and then leaves `stock` untouched. Otherwise it works as now. (An unknown "
              "sku has 0 units.)\n"
              "2. `place_order(stock, lines)` catches `OutOfStock` for the first line that fails and returns "
              "`{\"ok\": False, \"error\": \"@MSG@: <sku> (short <n>)\"}`. When an order fails, `stock` must be "
              "left completely unchanged, including the lines before the failing one. Lines may repeat a sku, and "
              "their quantities add up. A successful order changes `stock` in place and returns "
              "`{\"ok\": True, \"stock\": stock}` as now.")
    return _case(v, prompt, {"inventory.py": _OS_INV, "orders.py": _OS_ORD},
                 {"inventory.py": _OS_INV_SOL, "orders.py": _OS_ORD_SOL}, _OS_TESTS,
                 extra=[("orders.py", "    work = dict(stock)\n", "    work = stock\n"),
                        ("orders.py", "(short %d)\" % (e.sku, e.short)", "(short %d)\" % (e.sku, e.short + 1)")])


# ------------------------------------------------------------------ 6. table alignment

_TB_TABLE = r'''def render_row(cells, widths):
    return "@SEP@".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip()
'''
_TB_REPORT = r'''from table import render_row


def render_report(header, rows, widths):
    lines = [render_row(header, widths), "@RULE@".join("-" * w for w in widths)]
    lines += [render_row(r, widths) for r in rows]
    return "\n".join(lines)
'''
_TB_TABLE_SOL = r'''def render_row(cells, widths, align="left"):
    if align not in ("left", "right"):
        raise ValueError("bad align")
    pad = str.ljust if align == "left" else str.rjust
    return "@SEP@".join(pad(str(c), w) for c, w in zip(cells, widths)).rstrip()
'''
_TB_REPORT_SOL = r'''from table import render_row


def render_report(header, rows, widths, align="left"):
    lines = [render_row(header, widths, align), "@RULE@".join("-" * w for w in widths)]
    lines += [render_row(r, widths, align) for r in rows]
    return "\n".join(lines)
'''
_TB_TESTS = r'''import random

import pytest

from report import render_report
from table import render_row

SEP = "@SEP@"
RULE = "@RULE@"


def _row(cells, widths, align="left"):
    pad = str.ljust if align == "left" else str.rjust
    return SEP.join(pad(str(c), w) for c, w in zip(cells, widths)).rstrip()


def _report(header, rows, widths, align="left"):
    out = [_row(header, widths, align), RULE.join("-" * w for w in widths)]
    out += [_row(r, widths, align) for r in rows]
    return "\n".join(out)


def test_left_unchanged():
    assert render_row(["a", 12], [4, 5]) == "a   " + SEP + "12"
    assert render_row(["a", 12], [4, 5], "left") == "a   " + SEP + "12"
    assert render_report(["n", "qty"], [["x", 3]], [3, 4]) == _report(["n", "qty"], [["x", 3]], [3, 4])


def test_right_row():
    assert render_row(["a", 12], [4, 5], "right") == "   a" + SEP + "   12"
    assert render_row(["a", 12], [4, 5], align="right") == "   a" + SEP + "   12"


def test_bad_align():
    for a in ("center", "RIGHT", "", None):
        with pytest.raises(ValueError):
            render_row(["a"], [3], a)
    with pytest.raises(ValueError):
        render_report(["a"], [["b"]], [3], align="middle")


def test_report_right():
    got = render_report(["item", "qty"], [["pen", 5], ["notebook", 12]], [8, 4], align="right")
    assert got == _report(["item", "qty"], [["pen", 5], ["notebook", 12]], [8, 4], "right")
    lines = got.split("\n")
    assert lines[0].startswith("    item")
    assert lines[1] == "-" * 8 + RULE + "-" * 4


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        n = rng.randint(1, 4)
        widths = [rng.randint(3, 8) for _ in range(n)]
        mk = lambda: [rng.choice(["a", "bb", 7, 1234, "xyz"]) for _ in range(n)]
        header, rows = mk(), [mk() for _ in range(rng.randint(0, 3))]
        al = rng.choice(["left", "right"])
        assert render_report(header, rows, widths, al) == _report(header, rows, widths, al)
'''


def _table(rng):
    sep, rule = pick(rng, [(" | ", "-+-"), (" : ", "-:-"), (" # ", "-#-")])
    v = dict(SEP=sep, RULE=rule)
    prompt = ("Add right alignment to the text tables. Change `table.py` and `report.py`:\n\n"
              "1. `render_row(cells, widths, align=\"left\")`: `align=\"right\"` pads each cell on the left "
              "instead of the right. Any value other than `\"left\"` or `\"right\"` raises `ValueError`.\n"
              "2. `render_report(header, rows, widths, align=\"left\")` passes `align` on for the header and for "
              "every data row. The dashed rule line does not change.\n\n"
              "Left alignment gives exactly the output it gives now.")
    return _case(v, prompt, {"table.py": _TB_TABLE, "report.py": _TB_REPORT},
                 {"table.py": _TB_TABLE_SOL, "report.py": _TB_REPORT_SOL}, _TB_TESTS,
                 extra=[("table.py", '    if align not in ("left", "right"):\n        raise ValueError("bad align")\n', ""),
                        ("report.py", "render_row(header, widths, align)", "render_row(header, widths)")])


# ------------------------------------------------------------------ 7. item tags

_IT_MODELS = r'''from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    name: str
    price: float
'''
_IT_REPO = r'''from models import Item


def to_line(item):
    return "%s@SEP@%s@SEP@%.2f" % (item.sku, item.name, item.price)


def from_line(line):
    sku, name, price = line.strip().split("@SEP@")
    return Item(sku, name, float(price))


def dump_items(items):
    return "".join(to_line(i) + "\n" for i in items)


def load_items(text):
    return [from_line(ln) for ln in text.splitlines() if ln.strip()]
'''
_IT_MODELS_SOL = _IT_MODELS + "    tags: tuple = ()\n"
_IT_REPO_SOL = r'''from models import Item


def to_line(item):
    line = "%s@SEP@%s@SEP@%.2f" % (item.sku, item.name, item.price)
    if item.tags:
        line += "@SEP@" + "@TSEP@".join(item.tags)
    return line


def from_line(line):
    parts = line.strip().split("@SEP@")
    tags = tuple(t for t in parts[3].split("@TSEP@") if t) if len(parts) > 3 else ()
    return Item(parts[0], parts[1], float(parts[2]), tags)


def dump_items(items):
    return "".join(to_line(i) + "\n" for i in items)


def load_items(text):
    return [from_line(ln) for ln in text.splitlines() if ln.strip()]
'''
_IT_TESTS = r'''import random

from models import Item
from repo import dump_items, from_line, load_items, to_line


def test_default_tags():
    assert Item("a1", "Mug", 3.5).tags == ()
    assert Item("a1", "Mug", 3.5, ("x",)).tags == ("x",)


def test_old_lines_unchanged():
    assert to_line(Item("a1", "Mug", 3.5)) == "a1@SEP@Mug@SEP@3.50"
    assert from_line("a1@SEP@Mug@SEP@3.50") == Item("a1", "Mug", 3.5, ())
    assert from_line("a1@SEP@Mug@SEP@3.50\n").tags == ()


def test_tagged():
    it = Item("b2", "Pan", 12.0, ("steel", "big"))
    assert to_line(it) == "b2@SEP@Pan@SEP@12.00@SEP@steel@TSEP@big"
    assert from_line("b2@SEP@Pan@SEP@12.00@SEP@steel@TSEP@big") == it
    assert from_line("b2@SEP@Pan@SEP@12.00@SEP@one").tags == ("one",)


def test_empty_fourth_field_is_no_tags():
    assert from_line("c3@SEP@Cup@SEP@1.00@SEP@").tags == ()


def test_dump_load():
    items = [Item("a", "A", 1.0), Item("b", "B", 2.25, ("t1",)), Item("c", "C", 3.0, ("p", "q", "r"))]
    text = dump_items(items)
    assert text.count("\n") == 3 and text.endswith("\n")
    assert load_items(text) == items
    assert load_items("") == [] and dump_items([]) == ""


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        tags = tuple(rng.choice(["red", "xl", "new", "sale"]) for _ in range(rng.randint(0, 3)))
        it = Item("s%d" % rng.randint(0, 99), rng.choice(["Mug", "Big Pan"]), rng.randint(0, 5000) / 100, tags)
        line = to_line(it)
        assert line.count("@SEP@") == (3 if tags else 2)
        assert from_line(line) == it
'''


def _tags(rng):
    sep, tsep = pick(rng, [("|", ","), (";", "+"), ("|", ";")])
    v = dict(SEP=sep, TSEP=tsep)
    prompt = ("Items get tags. Change `models.py` and `repo.py`:\n\n"
              "1. `Item` gets a new last field `tags`, a tuple of strings, default `()`.\n"
              "2. `to_line` writes the tags as an extra 4th field, joined with `@TSEP@`, e.g. "
              "`b2@SEP@Pan@SEP@12.00@SEP@steel@TSEP@big`. An item without tags is written as the 3-field line it is "
              "written as now (no trailing separator).\n"
              "3. `from_line` reads lines with 3 or 4 fields; a missing or empty 4th field means no tags, and "
              "`tags` is always a tuple. Tags never contain `@SEP@` or `@TSEP@`.\n\n"
              "`dump_items` and `load_items` keep working through these two functions.")
    return _case(v, prompt, {"models.py": _IT_MODELS, "repo.py": _IT_REPO},
                 {"models.py": _IT_MODELS_SOL, "repo.py": _IT_REPO_SOL}, _IT_TESTS,
                 extra=[("repo.py", "    if item.tags:\n        line += ", "    if True:\n        line += ")])


# ------------------------------------------------------------------ 8. cli limit

_CL_CONFIG = r'''def parse_args(argv):
    opts = {"upper": False, "names": []}
    for a in argv:
        if a == "--upper":
            opts["upper"] = True
        else:
            opts["names"].append(a)
    return opts
'''
_CL_CLI = r'''from config import parse_args


def main(argv):
    opts = parse_args(argv)
    lines = ["@HI@, " + n for n in opts["names"]]
    if opts["upper"]:
        lines = [ln.upper() for ln in lines]
    return lines
'''
_CL_CONFIG_SOL = r'''def parse_args(argv):
    opts = {"upper": False, "names": [], "limit": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--upper":
            opts["upper"] = True
        elif a == "@FLAG@":
            if i + 1 >= len(argv):
                raise ValueError("@FLAG@ needs a value")
            n = int(argv[i + 1])
            if n < 1:
                raise ValueError("bad @FLAG@")
            opts["limit"] = n
            i += 1
        else:
            opts["names"].append(a)
        i += 1
    return opts
'''
_CL_CLI_SOL = r'''from config import parse_args


def main(argv):
    opts = parse_args(argv)
    lines = ["@HI@, " + n for n in opts["names"]]
    if opts["upper"]:
        lines = [ln.upper() for ln in lines]
    if opts["limit"] is not None:
        lines = lines[:opts["limit"]]
    return lines
'''
_CL_TESTS = r'''import random

import pytest

from cli import main
from config import parse_args

FLAG = "@FLAG@"


def test_parse_old():
    assert parse_args([]) == {"upper": False, "names": [], "limit": None}
    assert parse_args(["a", "--upper", "b"]) == {"upper": True, "names": ["a", "b"], "limit": None}


def test_parse_limit():
    assert parse_args(["a", FLAG, "2", "b"]) == {"upper": False, "names": ["a", "b"], "limit": 2}
    assert parse_args([FLAG, "10", "--upper"])["limit"] == 10
    assert parse_args(["x", "--upper", FLAG, "1"]) == {"upper": True, "names": ["x"], "limit": 1}


@pytest.mark.parametrize("argv", [[FLAG], ["a", FLAG], [FLAG, "x"], [FLAG, "0"], [FLAG, "-3"],
                                  [FLAG, "--upper"], [FLAG, "2.5"]])
def test_parse_bad(argv):
    with pytest.raises(ValueError):
        parse_args(argv)


def test_main_old():
    assert main(["ann", "bob"]) == ["@HI@, ann", "@HI@, bob"]
    assert main(["--upper", "ann"]) == ["@HI@, ANN".upper()]
    assert main([]) == []


def test_main_limit():
    assert main(["a", "b", "c", FLAG, "2"]) == ["@HI@, a", "@HI@, b"]
    assert main([FLAG, "5", "a", "b"]) == ["@HI@, a", "@HI@, b"]
    assert main(["a", "b", FLAG, "1", "--upper"]) == ["@HI@, A".upper()]


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        names = [rng.choice(["ann", "bo", "cy"]) for _ in range(rng.randint(0, 5))]
        up = rng.random() < 0.5
        lim = rng.choice([None, 1, 2, 3, 9])
        argv = list(names)
        if up:
            argv.insert(rng.randint(0, len(argv)), "--upper")
        if lim is not None:
            argv.insert(rng.randint(0, len(argv)) if rng.random() < 0.5 else 0, FLAG)
            # keep the value right after the flag
            k = argv.index(FLAG)
            argv.insert(k + 1, str(lim))
        want = ["@HI@, " + n for n in names]
        if up:
            want = [w.upper() for w in want]
        if lim is not None:
            want = want[:lim]
        assert main(argv) == want
'''


def _cli(rng):
    v = dict(HI=pick(rng, ["hello", "welcome", "greetings"]), FLAG=pick(rng, ["--limit", "--max"]))
    prompt = ("Add a `@FLAG@ N` option to the little greeter. Change `config.py` and `cli.py`:\n\n"
              "1. `parse_args` recognises `@FLAG@` followed by a separate value `N`, anywhere in `argv`. Neither "
              "token ends up in `names`. The result dict gets a new key `\"limit\"`: the integer, or `None` when the "
              "flag is absent. A missing value, a value that is not an integer, or a value below 1 raises "
              "`ValueError`.\n"
              "2. `main` keeps only the first `limit` greeting lines when a limit is given (after the `--upper` "
              "conversion, which does not change the count).\n\n"
              "Everything without the flag works as now.")
    return _case(v, prompt, {"config.py": _CL_CONFIG, "cli.py": _CL_CLI},
                 {"config.py": _CL_CONFIG_SOL, "cli.py": _CL_CLI_SOL}, _CL_TESTS,
                 extra=[("config.py", "            if n < 1:\n                raise ValueError(\"bad @FLAG@\")\n", ""),
                        ("cli.py", "lines[:opts[\"limit\"]]", "lines[:opts[\"limit\"] + 1]")])


# ------------------------------------------------------------------ 9. median

_MD_STATS = r'''def summarize(values):
    if not values:
        raise ValueError("no values")
    return {"count": len(values), "mean": sum(values) / len(values), "min": min(values), "max": max(values)}
'''
_MD_REPORT = r'''from stats import summarize


def describe(values, label="@LABEL@"):
    s = summarize(values)
    return "%s: n=%d mean=%.@DEC@f min=%g max=%g" % (label, s["count"], s["mean"], s["min"], s["max"])
'''
_MD_STATS_SOL = r'''def summarize(values):
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2
    return {"count": len(values), "mean": sum(values) / len(values), "min": min(values), "max": max(values),
            "median": median}
'''
_MD_REPORT_SOL = r'''from stats import summarize


def describe(values, label="@LABEL@"):
    s = summarize(values)
    return "%s: n=%d mean=%.@DEC@f min=%g max=%g median=%g" % (
        label, s["count"], s["mean"], s["min"], s["max"], s["median"])
'''
_MD_TESTS = r'''import random

import pytest

from report import describe
from stats import summarize


def _median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _desc(vals, label="@LABEL@"):
    return "%s: n=%d mean=%.@DEC@f min=%g max=%g median=%g" % (
        label, len(vals), sum(vals) / len(vals), min(vals), max(vals), _median(vals))


def test_old_keys():
    s = summarize([3, 1, 2])
    assert (s["count"], s["mean"], s["min"], s["max"]) == (3, 2, 1, 3)
    with pytest.raises(ValueError):
        summarize([])


def test_median_odd_even():
    assert summarize([5, 1, 9])["median"] == 5
    assert summarize([4, 1, 3, 2])["median"] == 2.5
    assert summarize([7])["median"] == 7
    assert summarize([2, 2, 10, 10])["median"] == 6


def test_input_not_changed():
    vals = [9, 1, 5, 3]
    summarize(vals)
    assert vals == [9, 1, 5, 3]


def test_describe():
    assert describe([4, 1, 3, 2]) == _desc([4, 1, 3, 2])
    assert describe([10, 20, 60], label="ms").startswith("ms: n=3 ")
    assert describe([10, 20, 60], label="ms").endswith(" median=20")
    with pytest.raises(ValueError):
        describe([])


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        vals = [rng.randint(0, 40) / 2 for _ in range(rng.randint(1, 9))]
        assert summarize(vals)["median"] == _median(vals)
        assert describe(vals) == _desc(vals)
'''


def _median(rng):
    v = dict(LABEL=pick(rng, ["values", "samples", "latency"]), DEC=pick(rng, [1, 2]))
    prompt = ("The summary needs a median. Change `stats.py` and `report.py`:\n\n"
              "1. `summarize` adds the key `\"median\"` to its dict. For an even number of values it is the mean of "
              "the two middle values. The input list must not be changed or reordered. The other keys and the "
              "`ValueError` for an empty list stay.\n"
              "2. `describe` appends ` median=<value>` to the end of its text, formatted with `%g` like min and max.\n\n"
              "The rest of the text is unchanged.")
    return _case(v, prompt, {"stats.py": _MD_STATS, "report.py": _MD_REPORT},
                 {"stats.py": _MD_STATS_SOL, "report.py": _MD_REPORT_SOL}, _MD_TESTS,
                 extra=[("stats.py", "ordered[mid - 1] + ordered[mid]) / 2", "ordered[mid - 1] + ordered[mid]) // 2"),
                        ("stats.py", "ordered = sorted(values)", "ordered = values\n    ordered.sort()")])


# ------------------------------------------------------------------ 10. slug separator

_SL_SLUG = r'''import re


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
'''
_SL_URLS = r'''from slug import slugify


def article_path(title, year):
    return "@ROOT@/%d/%s" % (year, slugify(title))
'''
_SL_SLUG_SOL = r'''import re


def slugify(text, sep="-"):
    if sep not in ("@A1@", "@A2@", "@A3@"):
        raise ValueError("bad separator")
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)
'''
_SL_URLS_SOL = r'''from slug import slugify


def article_path(title, year, sep="-"):
    return "@ROOT@/%d/%s" % (year, slugify(title, sep))
'''
_SL_TESTS = r'''import random
import re

import pytest

from slug import slugify
from urls import article_path

ALLOWED = ["@A1@", "@A2@", "@A3@"]


def _ref(text, sep="-"):
    if sep not in ALLOWED:
        raise ValueError
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)


def test_default_unchanged():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  --Trim  me--  ") == "trim-me"
    assert slugify("") == ""
    assert article_path("Big News", 2024) == "@ROOT@/2024/big-news"


@pytest.mark.parametrize("sep", ALLOWED)
def test_separators(sep):
    assert slugify("Hello,  World -- v2!", sep) == _ref("Hello,  World -- v2!", sep)
    assert slugify("  !!Edge Case!!  ", sep=sep) == "edge" + sep + "case"
    assert article_path("Big News", 2024, sep) == "@ROOT@/2024/big" + sep + "news"
    assert article_path("Big News", 2024, sep=sep) == "@ROOT@/2024/big" + sep + "news"


@pytest.mark.parametrize("sep", ["", "--", "x", " ", "a-"])
def test_bad_sep(sep):
    with pytest.raises(ValueError):
        slugify("a b", sep)
    with pytest.raises(ValueError):
        article_path("a b", 2020, sep)


def test_random():
    rng = random.Random(1234)
    for _ in range(300):
        text = "".join(rng.choice("abZ09 -_.,!") for _ in range(rng.randint(0, 14)))
        sep = rng.choice(ALLOWED)
        assert slugify(text, sep) == _ref(text, sep)
        assert article_path(text, 1999, sep) == "@ROOT@/1999/" + _ref(text, sep)
'''


def _slug(rng):
    allowed = pick(rng, [("-", "_", "."), ("-", "_", "+"), ("-", ".", "+")])
    v = dict(A1=allowed[0], A2=allowed[1], A3=allowed[2], ROOT=pick(rng, ["", "/blog", "/posts"]))
    prompt = ("Let article paths use another word separator. Change `slug.py` and `urls.py`:\n\n"
              "1. `slugify(text, sep=\"-\")`: runs of characters other than lowercase letters and digits become one "
              "`sep`, and `sep` is stripped from both ends. `sep` must be one of `@A1@`, `@A2@`, `@A3@`; anything "
              "else raises `ValueError`.\n"
              "2. `article_path(title, year, sep=\"-\")` passes `sep` on.\n\n"
              "Calls without `sep` give the same results as now.")
    return _case(v, prompt, {"slug.py": _SL_SLUG, "urls.py": _SL_URLS},
                 {"slug.py": _SL_SLUG_SOL, "urls.py": _SL_URLS_SOL}, _SL_TESTS,
                 extra=[("slug.py", '    if sep not in ("@A1@", "@A2@", "@A3@"):\n        raise ValueError("bad separator")\n', ""),
                        ("urls.py", "slugify(title, sep)", "slugify(title)")])


# ------------------------------------------------------------------ 11. page size into config

_PC_CONFIG = r'''APP_NAME = "stockroom"
DEBUG = False
'''
_PC_PAGER = r'''PER_PAGE = @N@


def page(items, number):
    """Return page `number` (1-based) of items."""
    start = (number - 1) * PER_PAGE
    return items[start:start + PER_PAGE]


def page_count(items):
    return max(1, -(-len(items) // PER_PAGE))
'''
_PC_CONFIG_SOL = _PC_CONFIG + "PAGE_SIZE = @N@\n"
_PC_PAGER_SOL = r'''import config


def page(items, number):
    """Return page `number` (1-based) of items."""
    start = (number - 1) * config.PAGE_SIZE
    return items[start:start + config.PAGE_SIZE]


def page_count(items):
    return max(1, -(-len(items) // config.PAGE_SIZE))
'''
_PC_TESTS = r'''import random

import config
import pager


def _ref_page(items, number, size):
    return items[(number - 1) * size:number * size]


def _ref_count(items, size):
    return max(1, -(-len(items) // size))


def test_config_has_the_constant():
    assert config.PAGE_SIZE == @N@
    assert config.APP_NAME == "stockroom" and config.DEBUG is False


def test_constant_moved_out_of_pager():
    assert not hasattr(pager, "PER_PAGE")


def test_default_size():
    items = list(range(@N@ * 2 + 1))
    assert pager.page(items, 1) == items[:@N@]
    assert pager.page(items, 3) == [items[-1]]
    assert pager.page(items, 4) == []
    assert pager.page_count(items) == 3
    assert pager.page_count([]) == 1
    assert pager.page_count(list(range(@N@))) == 1


def test_follows_config_at_call_time(monkeypatch):
    monkeypatch.setattr(config, "PAGE_SIZE", 2)
    items = list(range(5))
    assert pager.page(items, 1) == [0, 1]
    assert pager.page(items, 3) == [4]
    assert pager.page_count(items) == 3
    monkeypatch.setattr(config, "PAGE_SIZE", 10)
    assert pager.page(items, 1) == items
    assert pager.page_count(items) == 1


def test_random(monkeypatch):
    rng = random.Random(1234)
    for _ in range(300):
        size = rng.randint(1, 7)
        monkeypatch.setattr(config, "PAGE_SIZE", size)
        items = list(range(rng.randint(0, 20)))
        n = rng.randint(1, 6)
        assert pager.page(items, n) == _ref_page(items, n, size)
        assert pager.page_count(items) == _ref_count(items, size)
'''


def _pager(rng):
    v = dict(N=pick(rng, [4, 5, 8, 10]))
    prompt = ("Move the page size into the settings module. Change `config.py` and `pager.py`:\n\n"
              "1. `config.py` gets `PAGE_SIZE = @N@` (the value `pager.py` uses today).\n"
              "2. `pager.py` no longer has its own `PER_PAGE`; both functions read `config.PAGE_SIZE` each time "
              "they are called, so that changing `config.PAGE_SIZE` while the program runs takes effect "
              "immediately.\n\n"
              "The page numbering and results are otherwise unchanged.")
    return _case(v, prompt, {"config.py": _PC_CONFIG, "pager.py": _PC_PAGER},
                 {"config.py": _PC_CONFIG_SOL, "pager.py": _PC_PAGER_SOL}, _PC_TESTS,
                 extra=[("pager.py", "import config\n", "from config import PAGE_SIZE\n")])


# ------------------------------------------------------------------ 12. amount errors

_AM_AMOUNTS = r'''def parse_amount(text):
    """'12.50' -> 1250 (cents)."""
    whole, _, frac = text.strip().partition(".")
    cents = int((frac + "00")[:2]) if frac else 0
    return int(whole) * 100 + cents
'''
_AM_LEDGER = r'''from amounts import parse_amount


def import_rows(rows):
    """rows: list of (label, amount_text). Returns the total in cents."""
    return sum(parse_amount(text) for _, text in rows)
'''
_AM_AMOUNTS_SOL = r'''import re


class AmountError(ValueError):
    pass


def parse_amount(text):
    """'12.50' -> 1250 (cents)."""
    t = text.strip()
    if not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", t):
        raise AmountError("bad amount: %r" % text)
    whole, _, frac = t.partition(".")
    cents = int((frac + "00")[:2]) if frac else 0
    return int(whole) * 100 + cents
'''
_AM_LEDGER_SOL = r'''from amounts import AmountError, parse_amount


def import_rows(rows, @FLAG@=False):
    """rows: list of (label, amount_text). Returns the total in cents."""
    total = 0
    for _, text in rows:
        try:
            total += parse_amount(text)
        except AmountError:
            if not @FLAG@:
                raise
    return total
'''
_AM_TESTS = r'''import random
import re

import pytest

from amounts import AmountError, parse_amount
from ledger import import_rows


def _ref(text):
    t = text.strip()
    if not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", t):
        return None
    whole, _, frac = t.partition(".")
    return int(whole) * 100 + (int((frac + "00")[:2]) if frac else 0)


def test_good_amounts_unchanged():
    assert parse_amount("12.50") == 1250
    assert parse_amount("7") == 700
    assert parse_amount(" 3.05 ") == 305
    assert parse_amount("0.5") == 50
    assert parse_amount("0") == 0


@pytest.mark.parametrize("bad", ["", "abc", "-5", "-5.25", "1.234", ".5", "5.", "1,50", "1.2.3", "+4", "1e3", "- 2"])
def test_bad_amounts(bad):
    with pytest.raises(AmountError):
        parse_amount(bad)
    with pytest.raises(ValueError):
        parse_amount(bad)


def test_error_is_a_value_error():
    assert issubclass(AmountError, ValueError)


def test_import_rows_default():
    rows = [("a", "1.00"), ("b", "2.50")]
    assert import_rows(rows) == 350
    assert import_rows([]) == 0
    with pytest.raises(AmountError):
        import_rows([("a", "1.00"), ("b", "oops")])


def test_import_rows_skip():
    rows = [("a", "1.00"), ("b", "oops"), ("c", "-2"), ("d", "2.50"), ("e", "1.999")]
    assert import_rows(rows, @FLAG@=True) == 350
    assert import_rows(rows, True) == 350
    assert import_rows([("x", "bad")], @FLAG@=True) == 0
    with pytest.raises(AmountError):
        import_rows(rows, @FLAG@=False)


def test_random():
    rng = random.Random(1234)
    pool = ["1", "2.5", "10.05", "x", "-1", "3.456", "", "0.99", ".5", "7."]
    for _ in range(300):
        rows = [("r", rng.choice(pool)) for _ in range(rng.randint(0, 6))]
        want = sum(_ref(t) or 0 for _, t in rows)
        assert import_rows(rows, @FLAG@=True) == want
        if any(_ref(t) is None for _, t in rows):
            with pytest.raises(AmountError):
                import_rows(rows)
        else:
            assert import_rows(rows) == want
'''


def _amount(rng):
    v = dict(FLAG=pick(rng, ["skip_bad", "ignore_bad"]))
    prompt = ("Reject bad money amounts instead of guessing. Change `amounts.py` and `ledger.py`:\n\n"
              "1. `amounts.py` gets `class AmountError(ValueError)`. `parse_amount` raises it unless the stripped "
              "text is one or more digits, optionally followed by a point and one or two digits (so `\"12\"`, "
              "`\"12.5\"`, `\"12.50\"` are fine; empty text, signs, `\".5\"`, `\"5.\"`, more than two decimals, "
              "commas and letters are errors). Valid text gives the same cents as now.\n"
              "2. `import_rows(rows, @FLAG@=False)` in `ledger.py`: by default an `AmountError` propagates as it "
              "comes. With `@FLAG@=True` rows with a bad amount are left out of the total and the rest are summed.")
    return _case(v, prompt, {"amounts.py": _AM_AMOUNTS, "ledger.py": _AM_LEDGER},
                 {"amounts.py": _AM_AMOUNTS_SOL, "ledger.py": _AM_LEDGER_SOL}, _AM_TESTS,
                 extra=[("amounts.py", "[0-9]+(", "-?[0-9]+("),
                        ("ledger.py", "            if not @FLAG@:\n                raise\n", "            pass\n")])


TEMPLATES = [
    probe_template("twofiles", "twofiles_ticket_assignee", _ticket),
    probe_template("twofiles", "twofiles_cache_dry_run", _dryrun),
    probe_template("twofiles", "twofiles_line_discount", _discount),
    probe_template("twofiles", "twofiles_rename_alias", _rename),
    probe_template("twofiles", "twofiles_out_of_stock", _stock),
    probe_template("twofiles", "twofiles_table_align", _table),
    probe_template("twofiles", "twofiles_item_tags", _tags),
    probe_template("twofiles", "twofiles_cli_limit", _cli),
    probe_template("twofiles", "twofiles_median", _median),
    probe_template("twofiles", "twofiles_slug_sep", _slug),
    probe_template("twofiles", "twofiles_page_size", _pager),
    probe_template("twofiles", "twofiles_amount_error", _amount),
]
