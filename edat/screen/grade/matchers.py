"""Value matchers and state predicates over Screen World state.

The matcher names are Toolbench's set (`scripts/toolbench/matchers.py:5-15`:
`eq`, `icontains`, `contains_any`, `regex`, `in`, `present`, `absent`,
`date_offset`, `weekday`), as spec 02's predicate engine (B3) uses them, plus
the comparison words a count needs (`gte`, `lte`, `gt`, `lt`, `ne`) and
`not_icontains`. When the shared engine (`edat/grading/`, agentic build) is on
`main`, `match_value` should defer to it; the semantics here are the same.

A predicate:

    {in: settings.battery, expect: {low_power: true}}               # a dict
    {in: shop.orders, where: {item: tb750}, expect: {count: 1, qty: 2}}   # a list

For a list, `count` is the number of items that match `where` (all items when
there is no `where`); every other `expect` field must hold for every matching
item, and at least one item must match.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

MISSING = object()
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _s(v: Any) -> str:
    return " ".join(str(v).split()).casefold()


def _num(v: Any):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def match_value(spec: Any, value: Any, today: date | None = None) -> bool:
    if isinstance(spec, dict) and len(spec) == 1 and next(iter(spec)) in OPS:
        (op, want), = spec.items()
        return OPS[op](want, value, today)
    if isinstance(spec, dict) and spec and all(k in OPS for k in spec):
        return all(OPS[op](want, value, today) for op, want in spec.items())
    return _eq(spec, value, today)


def _eq(want, value, today=None) -> bool:
    if value is MISSING:
        return False
    if isinstance(want, bool) or isinstance(value, bool):
        return bool(want) == bool(value) if isinstance(value, bool) and isinstance(want, bool) else _s(want) == _s(value)
    if isinstance(want, (int, float)) and _num(value) is not None:
        return abs(float(want) - float(_num(value))) < 1e-9
    if isinstance(want, list):
        return isinstance(value, list) and len(want) == len(value) and all(_eq(a, b) for a, b in zip(want, value))
    return _s(want) == _s(value)


def _icontains(want, value, today=None) -> bool:
    if value is MISSING or value is None:
        return False
    if isinstance(value, list):
        return any(_icontains(want, v) for v in value)
    return _s(want) in _s(value)


def _not_icontains(want, value, today=None) -> bool:
    return value is MISSING or value is None or not _icontains(want, value)


def _contains_any(want, value, today=None) -> bool:
    return any(_icontains(w, value) for w in want)


def _regex(want, value, today=None) -> bool:
    if value is MISSING or value is None:
        return False
    return re.search(want, str(value), re.IGNORECASE) is not None


def _in(want, value, today=None) -> bool:
    return value is not MISSING and any(_eq(w, value) for w in want)


def _present(want, value, today=None) -> bool:
    ok = value is not MISSING and value not in (None, "", [], {})
    return ok if want in (True, None) else not ok


def _absent(want, value, today=None) -> bool:
    return not _present(True, value)


def _cmp(fn):
    def op(want, value, today=None):
        a, b = _num(value), _num(want)
        return a is not None and b is not None and fn(a, b)
    return op


def _date_offset(want, value, today=None) -> bool:
    if value is MISSING or today is None:
        return False
    target = today + timedelta(days=int(want))
    return str(value)[:10] == target.isoformat()


def _weekday(want, value, today=None) -> bool:
    if value is MISSING:
        return False
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return False
    return WEEKDAYS[d.weekday()] == str(want).lower()


OPS = {"eq": _eq, "icontains": _icontains, "not_icontains": _not_icontains, "contains_any": _contains_any,
       "regex": _regex, "in": _in, "present": _present, "absent": _absent, "gte": _cmp(lambda a, b: a >= b),
       "lte": _cmp(lambda a, b: a <= b), "gt": _cmp(lambda a, b: a > b), "lt": _cmp(lambda a, b: a < b),
       "ne": lambda w, v, t=None: not _eq(w, v), "date_offset": _date_offset, "weekday": _weekday}


def get_path(state: dict, path: str) -> Any:
    cur: Any = state
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return MISSING
    return cur


def _field(item: Any, name: str) -> Any:
    return get_path(item, name) if isinstance(item, dict) else MISSING


def check_predicate(pred: dict, state: dict, today: date | None = None) -> tuple[bool, str]:
    """(holds, why-not) for one `final_state` predicate."""
    target = get_path(state, pred["in"])
    expect = dict(pred.get("expect") or {})
    where = pred.get("where") or {}
    if isinstance(target, list):
        items = [x for x in target if all(match_value(v, _field(x, k), today) for k, v in where.items())]
        if "count" in expect:
            want = expect.pop("count")
            if not match_value(want, len(items), today):
                return False, f"{pred['in']}: {len(items)} matching items, wanted {want}"
        if expect:
            if not items:
                return False, f"{pred['in']}: no item matches {where}"
            for x in items:
                for k, v in expect.items():
                    if not match_value(v, _field(x, k), today):
                        return False, f"{pred['in']}: {k}={_field(x, k)!r} does not match {v!r}"
        return True, ""
    if target is MISSING:
        if expect.get("absent") is True or (expect == {} and pred.get("absent")):
            return True, ""
        return False, f"{pred['in']}: missing"
    if where:
        return False, f"{pred['in']}: 'where' needs a list"
    if isinstance(target, dict):
        for k, v in expect.items():
            if not match_value(v, _field(target, k), today):
                return False, f"{pred['in']}.{k}={_field(target, k)!r} does not match {v!r}"
        return True, ""
    if "value" in expect and not match_value(expect["value"], target, today):
        return False, f"{pred['in']}={target!r} does not match {expect['value']!r}"
    return True, ""
