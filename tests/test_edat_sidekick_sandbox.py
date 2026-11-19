"""Sandbox: the self-test passes, and the harness refuses to run without it (spec 12 §3.4, S11)."""
from __future__ import annotations

import pytest

from edat.sidekick.sandbox import run as sbrun
from edat.sidekick.sandbox.denials import parse_lines
from edat.sidekick.sandbox.run import Limits, SandboxUnavailable, SeatbeltIsolation, TrialArea, render_profile


def _nested(exc: Exception) -> bool:
    msg = str(exc)
    return "sandbox_apply" in msg or "Operation not permitted" in msg


@pytest.fixture(scope="module")
def iso():
    i = SeatbeltIsolation()
    try:
        i.check()
    except SandboxUnavailable as exc:
        if _nested(exc):
            pytest.skip("already inside another Seatbelt sandbox (cannot nest); run outside it")
        raise
    return i


def test_refuses_without_sandbox_exec():
    i = SeatbeltIsolation(sandbox_exec="/nonexistent/sandbox-exec")
    with pytest.raises(SandboxUnavailable):
        i.check()
    with TrialArea() as area, pytest.raises(SandboxUnavailable):
        i.run(area, "echo hi")          # run() checks first; there is no unsandboxed path


def test_refuses_when_profile_cannot_apply(monkeypatch, tmp_path):
    bad = tmp_path / "bad.sb"
    bad.write_text("(version 1)\n(this is not a valid profile\n")
    monkeypatch.setattr(sbrun, "PROFILE_TEMPLATE", bad)
    with pytest.raises(SandboxUnavailable):
        SeatbeltIsolation().check()


def test_refuses_when_profile_does_not_deny(monkeypatch, tmp_path):
    permissive = tmp_path / "allow.sb"
    permissive.write_text("(version 1)\n(allow default)\n")
    monkeypatch.setattr(sbrun, "PROFILE_TEMPLATE", permissive)
    try:
        with pytest.raises(SandboxUnavailable, match="did not deny"):
            SeatbeltIsolation().check()
    except SandboxUnavailable as exc:      # pragma: no cover - nested environment
        if _nested(exc):
            pytest.skip("nested sandbox")
        raise


def test_profile_renders_without_placeholders():
    from edat.sidekick.toolchain import find_toolchain
    with TrialArea() as area:
        text = render_profile(area.root, find_toolchain())
    assert "@" not in text.replace("(version 1)", "")
    assert "(deny default)" in text and "(deny network*)" in text


def test_selftest_passes(iso):
    from edat.sidekick.sandbox.selftest import run_selftest
    rep = run_selftest(iso)
    assert rep.passed, rep.text()
    names = {p.name for p in rep.probes}
    assert "loopback TCP is denied" in names and "shell write outside the trial is denied" in names


def test_trial_area_layout_and_cleanup():
    with TrialArea() as area:
        root = area.root
        assert (area.home / ".gitconfig").exists()
        assert (area.home / "edat-home-canary.txt").read_text().startswith("EDATCANARY")
        with pytest.raises(ValueError):
            area.path("../outside.txt")
        with pytest.raises(ValueError):
            area.write_files({"../x.txt": "no"})
    assert not root.exists()


def test_watchdog_and_memory_limit(iso):
    with TrialArea() as area:
        r = iso.run(area, "python3 -c \"x = bytearray(600*1024*1024); import time; time.sleep(5)\"",
                    limits=Limits(wall_s=8, rss_bytes=200 * 1024 * 1024))
        assert r.killed == "memory"


def test_cwd_must_be_inside_trial(iso, tmp_path):
    with TrialArea() as area, pytest.raises(ValueError):
        iso.run(area, "pwd", cwd=tmp_path)


def test_denial_line_parsing():
    lines = ["Sandbox: python3.13(4242) deny(1) network-outbound 192.0.2.1:9",
             "Sandbox: zsh(4243) deny(1) file-write-create /private/tmp/x/escape.txt",
             "Sandbox: git(9999) deny(1) file-write-create /elsewhere",
             "Sandbox: python3.13(4242) deny(1) sysctl-read kern.iossupportversion"]
    ds = parse_lines(lines, {4242, 4243})
    assert [(d.pid, d.op) for d in ds] == [(4242, "network-outbound"), (4243, "file-write-create")]
    assert all(d.is_escape for d in ds)
