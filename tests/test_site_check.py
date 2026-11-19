"""The website's own checks (site/tests/check.mjs) run in the offline gate.

Part 1 reads the sample export the way the pages do: schema, registry, store
values, empty-cell reasons, the name and path scan. Part 2 renders every route
in headless Chrome when Chrome is installed (the macOS runner has it) and checks
the layout the pages promise, such as the cost chart above the leaderboard table.
No model, no engine and no network: the pages are served from a local port the
script picks itself.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "site" / "tests" / "check.mjs"


def _node() -> str | None:
    return os.environ.get("EDAT_SIDEKICK_NODE") or shutil.which("node")


def test_site_checks_pass():
    node = _node()
    if not node:
        pytest.skip("no Node on this machine")
    env = dict(os.environ, CHECK_JOBS=os.environ.get("CHECK_JOBS", "3"))
    proc = subprocess.run([node, str(CHECK)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    tail = "\n".join(proc.stdout.splitlines()[-40:])
    assert proc.returncode == 0, f"site checks failed:\n{tail}\n{proc.stderr[-2000:]}"
    assert "PASSED" in proc.stdout, tail
