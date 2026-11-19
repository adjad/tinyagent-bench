"""The plain JSON state store both lanes are graded on.

A store is a set of named collections, each a list of records (dicts). Every
record has an `id`. The neutral lane keeps its world in a store directly; the
assistant lane builds a store view of Toolbench's world after the turn. One
predicate grader (edat/grading/predicates.py) reads both.

Collection names and record fields are shared by both lanes; see
edat/state/collections.py.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Iterable

from edat.state.clock import Clock


class StateError(KeyError):
    pass


class StateStore:
    def __init__(self, collections: dict[str, list[dict]] | None = None, *,
                 clock: Clock | None = None) -> None:
        self.clock = clock or Clock.parse()
        self.collections: dict[str, list[dict]] = {}
        self._next: dict[str, int] = {}
        self.log: list[dict] = []
        for name, rows in (collections or {}).items():
            for row in rows:
                self.add(name, row, log=False)

    # ---------------------------------------------------------------- reading
    def get(self, collection: str) -> list[dict]:
        """The live list for a collection (created empty on first use)."""
        return self.collections.setdefault(collection, [])

    def names(self) -> list[str]:
        return sorted(self.collections)

    def find(self, collection: str, record_id: Any) -> dict | None:
        for row in self.get(collection):
            if row.get("id") == record_id:
                return row
        return None

    def where(self, collection: str, **eq) -> list[dict]:
        return [r for r in self.get(collection) if all(r.get(k) == v for k, v in eq.items())]

    # ---------------------------------------------------------------- writing
    def new_id(self, prefix: str) -> str:
        n = self._next[prefix] = self._next.get(prefix, 900) + 1
        return f"{prefix}-{n}"

    def add(self, collection: str, record: dict, *, prefix: str | None = None, log: bool = True) -> dict:
        row = copy.deepcopy(record)
        if row.get("id") in (None, ""):
            row["id"] = row.get("path") or self.new_id(prefix or collection[:3])
        if self.find(collection, row["id"]) is not None:
            raise StateError(f"{collection}: duplicate id {row['id']!r}")
        self.get(collection).append(row)
        if log:
            self.log.append({"op": "add", "collection": collection, "id": row["id"]})
        return row

    def update(self, collection: str, record_id: Any, **fields) -> dict:
        row = self.find(collection, record_id)
        if row is None:
            raise StateError(f"{collection}: no record {record_id!r}")
        row.update(copy.deepcopy(fields))
        self.log.append({"op": "update", "collection": collection, "id": record_id,
                         "fields": sorted(fields)})
        return row

    def remove(self, collection: str, record_id: Any) -> dict:
        rows = self.get(collection)
        for i, row in enumerate(rows):
            if row.get("id") == record_id:
                self.log.append({"op": "remove", "collection": collection, "id": record_id})
                return rows.pop(i)
        raise StateError(f"{collection}: no record {record_id!r}")

    def move(self, src: str, dst: str, record_id: Any, **fields) -> dict:
        row = self.remove(src, record_id)
        row.update(fields)
        self.get(dst).append(row)
        self.log.append({"op": "add", "collection": dst, "id": record_id})
        return row

    # ---------------------------------------------------------------- snapshots
    def snapshot(self) -> dict[str, list[dict]]:
        return copy.deepcopy({k: v for k, v in self.collections.items()})

    def to_json(self) -> str:
        return json.dumps({"clock": self.clock.iso(), "tz": self.clock.tz,
                           "collections": self.collections}, sort_keys=True, default=str)

    @classmethod
    def from_snapshot(cls, snap: dict[str, list[dict]], *, clock: Clock | None = None) -> "StateStore":
        st = cls(clock=clock)
        st.collections = copy.deepcopy(snap)
        return st


def _index(rows: Iterable[dict]) -> dict:
    return {json.dumps(r.get("id"), default=str): r for r in rows}


def diff(before: dict[str, list[dict]], after: dict[str, list[dict]]) -> dict[str, dict]:
    """Per collection: ids added, removed and changed between two snapshots.

    Only collections with a change appear. Ids are compared by their JSON
    form; a record without an id is compared by content.
    """
    out: dict[str, dict] = {}
    for name in sorted(set(before) | set(after)):
        b, a = _index(before.get(name, [])), _index(after.get(name, []))
        added = [a[k].get("id") for k in a if k not in b]
        removed = [b[k].get("id") for k in b if k not in a]
        changed = [a[k].get("id") for k in a if k in b and a[k] != b[k]]
        if added or removed or changed:
            out[name] = {"added": added, "removed": removed, "changed": changed}
    return out
