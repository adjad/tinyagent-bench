"""Tests for export_portable_cases.py (run: python -m pytest ios-bench/tools/test_export_portable_cases.py -q).

They need PyYAML and the repository's `service` package (the tool registry); without them they skip.
"""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_portable_cases as ex  # noqa: E402

RES = HERE.parent / "Resources"
COMMITTED = json.loads((RES / "portable-cases.json").read_text(encoding="utf-8"))
TODAY = date.fromisoformat(COMMITTED["source"]["bound_today"])


@pytest.fixture(scope="module", autouse=True)
def empty_home():
    """Never read a real home directory while importing the service package."""
    old = os.environ.get("HOME")
    os.environ["HOME"] = tempfile.mkdtemp(prefix="portable-test-home-")
    try:
        pytest.importorskip("yaml")
        yield
    finally:
        if old is not None:
            os.environ["HOME"] = old


def test_committed_bundle_matches_a_fresh_export():
    try:
        fresh = ex.build_bundle(COMMITTED["source"]["exported_at"], TODAY)
    except ImportError as e:  # pragma: no cover - environment without the service package
        pytest.skip(f"tool registry not importable: {e}")
    assert ex.render(fresh) == (RES / "portable-cases.json").read_text(encoding="utf-8")


def test_held_out_cases_are_refused(monkeypatch):
    monkeypatch.setattr(ex, "ABSTAIN_IDS", ex.ABSTAIN_IDS + ["chat_define"])   # a gate (held-out) case
    with pytest.raises(SystemExit, match="only development-split"):
        ex._toolbench_cases(TODAY)


def test_every_bundled_case_binds_completely():
    cases, stamp = ex._toolbench_cases(TODAY)
    assert stamp["version"] == 2
    for cid in ex.SELECT_IDS:
        ex._check_portable(cases[cid], False)
    for cid in ex.ABSTAIN_IDS:
        ex._check_portable(cases[cid], True)
