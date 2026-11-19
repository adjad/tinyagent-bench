"""Runs the invented unit examples in test_fixtures/quality/checker_examples.jsonl
through edat.quality.checkers.check (rules: edat/quality/CHECKERS.md).

An example with an "xfail" key records a known disagreement between the code
and the rule; the expectation stays the rule's, and strict xfail turns an
accidental fix into a failure so the key gets removed.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from edat.quality.checkers import PREF_IDS, check

FIXTURE = Path(__file__).resolve().parents[1] / "test_fixtures" / "quality" / "checker_examples.jsonl"


def _load() -> list[dict]:
    rows = []
    for n, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as e:  # pragma: no cover
                raise AssertionError(f"{FIXTURE.name}:{n}: {e}") from e
    return rows


ROWS = _load()


def _params():
    out = []
    for r in ROWS:
        marks = []
        if r.get("xfail"):
            marks.append(pytest.mark.xfail(reason=r["xfail"], strict=True))
        out.append(pytest.param(r, id=r["id"], marks=marks))
    return out


@pytest.mark.parametrize("row", _params())
def test_example(row):
    res = check(row["pref_id"], row["text"], row["surface"], row.get("meta") or {})
    assert res.status == row["expect"], f"{row['note']}: got {res.status} ({res.reason})"


def test_ids_unique():
    ids = [r["id"] for r in ROWS]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    assert not dupes, dupes


def test_row_shape():
    for r in ROWS:
        assert r["pref_id"] in PREF_IDS
        assert r["expect"] in ("pass", "fail", "n/a")
        assert r["surface"] in ("answer", "draft_email", "draft_message", "json", "tool_args")
        assert isinstance(r["text"], str) and isinstance(r["meta"], dict) and r["note"]


@pytest.mark.parametrize("pid", PREF_IDS)
def test_minimum_examples(pid):
    c = Counter(r["expect"] for r in ROWS if r["pref_id"] == pid)
    assert c["pass"] >= 20, (pid, c)
    assert c["fail"] >= 20, (pid, c)
    assert c["n/a"] >= 5, (pid, c)
