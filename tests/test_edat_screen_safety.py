"""Screen World touches nothing real (spec 11 §3.4, S10): no network, no real clock, no held-out cases
or local paths in the repository."""
from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from edat.screen.adapter import PolicyClient
from edat.screen.cases.bind import bind
from edat.screen.cases.oracle import Oracle
from edat.screen.loop import ScreenLoop, ScreenSettings

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "edat" / "screen"
FIXTURES = ROOT / "test_fixtures" / "screen"
SIM_DIRS = ("world", "apps", "scenarios", "author", "cases", "grade", "render")


def _py(dirs=SIM_DIRS):
    for d in dirs:
        yield from sorted((CODE / d).rglob("*.py"))


def test_simulator_code_imports_no_network_or_os_automation():
    banned = re.compile(r"^\s*(import|from)\s+(socket|urllib(?!\.parse)|http|requests|httpx|subprocess|ctypes|Quartz|AppKit|"
                        r"pyautogui|selenium|playwright)\b", re.M)
    hits = [f"{p.relative_to(ROOT)}" for p in _py() if banned.search(p.read_text())]
    assert not hits, hits


def test_nothing_in_the_screen_tier_can_see_or_drive_the_real_screen():
    """Screenshots are drawn from the simulated layout (render/raster.py), never captured; the real-run
    harness (live.py) only talks to the engine and runs read-only host checks."""
    banned = re.compile(r"ImageGrab|screencapture|CGWindowListCreateImage|CGDisplayCreateImage|\b(Quartz|AppKit|"
                        r"pyautogui|pynput|atomacos|ApplicationServices)\b|osascript|System Events")
    hits = [f"{p.relative_to(ROOT)}" for p in sorted(CODE.rglob("*.py")) if banned.search(p.read_text())]
    assert not hits, hits


def test_simulator_never_reads_the_real_clock():
    rx = re.compile(r"\b(datetime\.now|datetime\.utcnow|date\.today|time\.time\(|time\.localtime)")
    hits = [f"{p.relative_to(ROOT)}" for p in _py() if rx.search(p.read_text())]
    assert not hits, hits


def test_a_trial_runs_with_sockets_disabled(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("Screen World opened a socket")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    case = {"id": "scr_ground_lp_001", "spec": "screen", "family": "ground", "level": 1,
            "start": {"app": "settings", "screen": "battery"}, "world": {"apps": ["settings"]},
            "task": "Turn on Low Power Mode.", "final_state": [{"in": "settings.battery", "expect": {"low_power": True}}],
            "oracle": [{"click": "settings.battery.low_power"}, {"finish": True}]}
    b = bind(case, 1)
    ep = ScreenLoop().run(PolicyClient(Oracle(b)), b, ScreenSettings())
    assert ep.finished


@pytest.mark.parametrize("path", sorted(p for p in FIXTURES.rglob("*") if p.is_file()) or [None])
def test_fixtures_hold_no_heldout_cases_local_paths_or_real_numbers(path):
    if path is None:
        pytest.skip("no fixtures yet")
    text = path.read_text(errors="replace")
    assert not re.search(r"\bscr_[a-z]+h_[a-z0-9_]+_\d{3}\b", text), "a held-out case id is in the repository"
    assert "/Users/" not in text and "\\Users\\" not in text
    for m in re.finditer(r"\b\d{3}-\d{4}\b", text):
        assert m.group(0).startswith("555"), m.group(0)
