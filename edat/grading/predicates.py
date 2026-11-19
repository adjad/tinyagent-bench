"""World-state predicates (`final_state`, `claims[].state`, `unchanged`).

A predicate:

    - in: <collection>
      where: {<field>: <matcher>, ...}      # optional filter
      expect: {count: n | <matcher>}        # how many rows match `where`
              {absent: true}                # no row matches (count 0)
              {<field>: <matcher>, ...}     # every matching row satisfies these,
                                            # and at least one row matches
              (count and fields may be combined)

Fields may be dotted paths or derived datetime parts (`due.time`,
`start.weekday`; see matchers.field_value).
"""
from __future__ import annotations

from dataclasses import dataclass

from edat.grading.matchers import field_value, match_value
from edat.state.clock import Clock
from edat.state.store import StateStore, diff


@dataclass
class PredicateResult:
    ok: bool
    reason: str = ""
    predicate: dict | None = None


def rows_where(store: StateStore, collection: str, where: dict | None) -> list[dict]:
    out = []
    for row in store.get(collection):
        if all(match_value(spec, field_value(row, f), store.clock) is None for f, spec in (where or {}).items()):
            out.append(row)
    return out


def check_predicate(pred: dict, store: StateStore) -> PredicateResult:
    coll = pred["in"]
    rows = rows_where(store, coll, pred.get("where"))
    expect = dict(pred.get("expect") or {})
    clock: Clock = store.clock
    if expect.pop("absent", False):
        expect.setdefault("count", 0)
    if "count" in expect:
        want = expect.pop("count")
        why = match_value(want, len(rows), clock) if isinstance(want, dict) else \
            (None if len(rows) == int(want) else f"{len(rows)} rows, want {want}")
        if why:
            return PredicateResult(False, f"{coll}{_w(pred)}: {why}", pred)
        if not expect:
            return PredicateResult(True, "", pred)
        if not rows:
            return PredicateResult(True, "", pred)   # count 0 asked and met; field checks are vacuous
    if expect and not rows:
        return PredicateResult(False, f"{coll}{_w(pred)}: no matching row", pred)
    for row in rows:
        for f, spec in expect.items():
            why = match_value(spec, field_value(row, f), clock)
            if why:
                return PredicateResult(False, f"{coll}{_w(pred)} row {row.get('id')!r}: {f}: {why}", pred)
    return PredicateResult(True, "", pred)


def _w(pred: dict) -> str:
    return f" where {pred['where']}" if pred.get("where") else ""


def check_all(preds: list[dict] | None, store: StateStore) -> list[PredicateResult]:
    return [check_predicate(p, store) for p in (preds or [])]


def collateral(unchanged: list[str] | None, before: dict, after: dict) -> list[str]:
    """Collections listed `unchanged` that did change, with what changed."""
    changes = diff(before, after)
    return [f"{name}: {changes[name]}" for name in (unchanged or []) if name in changes]
