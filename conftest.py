"""Repository-wide pytest safety bootstrap.

This file is imported before test modules, which is early enough to keep
service modules with import-time stores away from the user's real Harbor state.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

import pytest


_REAL_HARBOR_HOME = (Path.home() / ".moe").resolve()
_PYTEST_STATE = tempfile.TemporaryDirectory(prefix="harbor-pytest-state-")
_ISOLATED_ROOT = Path(_PYTEST_STATE.name).resolve()

# Never inherit an opt-in that could activate live tests, host credentials, or
# installed templates. Tests that need one set it explicitly with monkeypatch.
for _key in tuple(os.environ):
    if _key.startswith("HARBOR_"):
        os.environ.pop(_key, None)

os.environ.update({
    "HARBOR_HOME": str(_ISOLATED_ROOT / "harbor"),
    "HARBORAIR_HOME": str(_ISOLATED_ROOT / "air"),
    "HARBOR_TEST_PYTHON": sys.executable,
    "PYTHONDONTWRITEBYTECODE": "1",
})

# service/local_http.py refuses any request whose Host is not this Mac's
# loopback address. In-process test clients use made-up names instead, so the
# names in use are allowed here, for the test run only. A running Harbor never
# loads this file and keeps the set empty. tests/test_local_http_guard.py
# empties it again to check the real rules.
_IN_PROCESS_TEST_HOSTS = frozenset({
    "testserver",                # Starlette's TestClient default
    "test", "fixture",
    "test.invalid", "fixture.invalid", "synthetic.invalid",
})
try:
    from service import local_http as _local_http
except ImportError:              # no web stack installed, so no guarded app either
    pass
else:
    _local_http._TEST_HOSTS.update(_IN_PROCESS_TEST_HOSTS)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail closed if Harbor storage escaped the disposable test directory."""
    from service.paths import MOE_DIR

    resolved = MOE_DIR.resolve()
    if resolved == _REAL_HARBOR_HOME or not resolved.is_relative_to(_ISOLATED_ROOT):
        raise pytest.UsageError(
            f"refusing to run tests with Harbor state at {resolved}; "
            f"expected a path under {_ISOLATED_ROOT}"
        )


def pytest_unconfigure(config: pytest.Config) -> None:
    _PYTEST_STATE.cleanup()
