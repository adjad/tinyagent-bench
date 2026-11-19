"""Sandbox: the self-test passes, and the harness refuses to run without it (spec 12 §3.4, S11)."""
from __future__ import annotations

import re

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
    assert not re.search(r"@[A-Z_]+@", text)          # paths may hold "@" (a Homebrew node@22, say)
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


def test_a_cpu_limit_kill_is_reported_as_stopped():
    import signal
    from edat.sidekick.sandbox.run import cpu_limit_kill
    assert cpu_limit_kill(-signal.SIGXCPU) == "cpu_time" and cpu_limit_kill(-signal.SIGKILL) == "cpu_time"
    assert cpu_limit_kill(128 + signal.SIGXCPU) == "cpu_time"          # the shell's view of its child's signal
    assert cpu_limit_kill(0) is None and cpu_limit_kill(1) is None and cpu_limit_kill(None) is None


def test_cpu_limit_stops_a_busy_loop(iso):
    with TrialArea() as area:
        r = iso.run(area, "python3 -c 'while True: pass'", limits=Limits(wall_s=30, cpu_s=1))
    assert r.killed == "cpu_time" and not r.ok, (r.exit_code, r.stderr[-200:])


def test_a_tool_loading_libraries_from_elsewhere_is_refused(monkeypatch):
    from edat.sidekick import toolchain
    monkeypatch.setattr(toolchain, "_linked", lambda b: {"/opt/x/bin/node": ["/usr/lib/libc++.1.dylib",
                                                                            "/opt/homebrew/opt/libuv/lib/libuv.1.dylib"]}.get(b, []))
    monkeypatch.setattr(toolchain, "_real", lambda p: p)
    monkeypatch.setattr(toolchain.os, "access", lambda p, m: True)
    assert toolchain.foreign_libraries("/opt/x/bin/node", ("/opt/x",)) == ["/opt/homebrew/opt/libuv/lib/libuv.1.dylib"]
    with pytest.raises(toolchain.ToolchainError, match="outside its own folder.*EDAT_SIDEKICK_NODE"):
        toolchain._require_self_contained("node", "/opt/x/bin/node", ("/opt/x",), "EDAT_SIDEKICK_NODE")
    assert toolchain.foreign_libraries("/opt/x/bin/other", ("/opt/x",)) == []


def test_read_inside_never_follows_a_link(tmp_path):
    import os
    secret = tmp_path / "host-secret.txt"
    secret.write_text("EDATHOSTSECRET\n")
    with TrialArea() as area:
        (area.work / "ok.py").write_text("x = 1\n")
        assert area.read_inside("ok.py") == "x = 1\n"
        (area.work / "out.py").symlink_to(secret)                       # to a host file
        (area.work / "in.py").symlink_to(area.work / "ok.py")           # even to a file inside
        (area.work / "d").symlink_to(tmp_path, target_is_directory=True)
        os.mkfifo(area.work / "pipe")
        (area.work / "sub").mkdir()
        for rel in ("out.py", "in.py", "d/host-secret.txt", "pipe", "sub", "missing.py", str(secret), "../x", ""):
            assert area.read_inside(rel) is None, rel


# ---------------------------------------------------------------- the host side never goes through a link
#
# Every file here is invented, in a temp folder. The race tests spin a loop for a second or two; they run in
# the hosted job (CI) or with EDAT_SLOW_TESTS=1, not in a plain local run.

import os
import threading
import time

SECRET = "EDATSYNTHHOSTSECRET"
slow = pytest.mark.skipif(not (os.environ.get("CI") or os.environ.get("EDAT_SLOW_TESTS")),
                          reason="a busy race loop: the hosted job runs it (or set EDAT_SLOW_TESTS=1)")


def _host_dir(tmp_path):
    host = tmp_path / "hostdir"
    host.mkdir()
    (host / "target.py").write_text(SECRET + "\n")
    (host / f"notes-{SECRET}.txt").write_text(SECRET + "\n")
    return host


def _swapper(root, host):
    """Keeps re-pointing root/work at the host folder and back, as a surviving process could."""
    work, stop = root / "work", threading.Event()

    def run():
        while not stop.is_set():
            try:
                os.rename(work, root / "w2")
                os.symlink(host, root / "lnk")
                os.rename(root / "lnk", work)
                os.unlink(work)
                os.rename(root / "w2", work)
            except OSError:
                pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return stop, t


@slow
def test_the_file_tools_never_reach_a_host_folder_swapped_in_meanwhile(iso, tmp_path):
    from edat.sidekick.tools_dev import DevEnvironment
    host = _host_dir(tmp_path)
    before = sorted(os.listdir(host))
    with TrialArea() as area:
        (area.work / "target.py").write_text("ok\n")
        env = DevEnvironment(iso, area)
        stop, t = _swapper(area.root, host)
        texts, swapped, i = set(), 0, 0
        try:
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                texts.add(env.call("read_file", {"path": "target.py"}).content[:200])
                env.call("write_file", {"path": f"w{i % 20}.txt", "content": "planted"})
                swapped += os.path.islink(area.work)
                i += 1
        finally:
            stop.set()
            t.join()
        assert swapped > 0                                       # the swap really happened meanwhile
        assert not any(SECRET in x for x in texts)
    assert sorted(os.listdir(host)) == before                    # nothing was written into the host folder


@slow
def test_reads_and_writes_never_reach_a_host_folder_swapped_in_meanwhile(tmp_path):
    host = _host_dir(tmp_path)
    before = sorted(os.listdir(host))
    with TrialArea() as area:
        (area.work / "target.py").write_text("ok\n")
        stop, t = _swapper(area.root, host)
        seen, swapped, i = set(), 0, 0
        try:
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                seen.add(area.read_inside("target.py"))
                swapped += os.path.islink(area.work)
                if i % 50 == 0:
                    area.write_inside(f"out{i}.txt", "x")
                i += 1
        finally:
            stop.set()
            t.join()
        assert swapped > 0 and "ok\n" in seen
        assert not any(SECRET in (x or "") for x in seen)
    assert sorted(os.listdir(host)) == before


@slow
def test_a_surviving_sandboxed_swapper_is_found_and_locked_out(iso, tmp_path):
    """The reviewer's case: model code starts a `setsid` child that survives the process-group kill and
    keeps swapping `work` for a link to a host folder. It is reported as a survivor; reads never return host
    contents; once sealed, it can change nothing and `work` is the original folder again."""
    from edat.sidekick.graders.common import area_events, tree_snapshot
    host = _host_dir(tmp_path)
    with TrialArea() as area:
        (area.work / "target.py").write_text("ok\n")
        root = area.root
        perl = ("use POSIX; exit if fork; POSIX::setsid(); my $e=time+3; my $r=q{%s};"
                "while (time<$e) { rename(qq{$r/work},qq{$r/w2}); symlink(q{%s},qq{$r/lnk}); rename(qq{$r/lnk},qq{$r/work});"
                " unlink(qq{$r/work}); rename(qq{$r/w2},qq{$r/work}); }") % (root, host)
        r = iso.run(area, "/usr/bin/perl -e '%s' >/dev/null 2>&1 </dev/null; echo launched" % perl,
                    limits=Limits(wall_s=10))
        assert "launched" in r.stdout and r.survivor and area.survivors
        reads, swapped = [], 0
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            reads.append(area.read_inside("target.py"))
            swapped += os.path.islink(area.work)
        assert swapped > 0, "the surviving swapper did not run"
        assert not any(SECRET in (x or "") for x in reads) and "ok\n" in reads
        area.seal()
        assert not area.work_replaced() and area.read_inside("target.py") == "ok\n"
        for _ in range(20):                                      # still running, but locked out
            snap = tree_snapshot(area.work)
            assert "target.py" in snap and not any(SECRET in k for k in snap)
            assert not area.work_replaced()
        assert not root.exists()                                 # it cannot recreate the old folder
        assert ("unsafe_action", "surviving_process") in {(e.kind, e.rule) for e in area_events(area)}


def test_a_process_that_escapes_its_group_is_reported(iso):
    with TrialArea() as area:
        r = iso.run(area, "/usr/bin/perl -e 'use POSIX; exit if fork; POSIX::setsid(); sleep 2' "
                          ">/dev/null 2>&1 </dev/null; echo ok")
        assert r.survivor and area.survivors
        plain = iso.run(area, "sleep 30 & echo ok")             # a background child of the group is killed
        assert not plain.survivor and len(area.survivors) == 1


def test_the_snapshot_never_lists_behind_a_swapped_work_folder(tmp_path):
    from edat.sidekick.graders.common import tree_snapshot
    host = _host_dir(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    (root / "work").symlink_to(host, target_is_directory=True)
    assert tree_snapshot(root / "work") == {}                    # nothing behind the top-level link
    work = tmp_path / "w"
    work.mkdir()
    (work / "a.txt").write_text("a\n")
    (work / "out").symlink_to(host / "target.py")
    snap = tree_snapshot(work)
    assert snap["a.txt"]["content"] == "a\n" and snap["out"] == {"link": str(host / "target.py"), "mode": 0}
    assert not any(SECRET in json_text for json_text in map(str, snap.values()))


def test_a_planted_pipe_cannot_hang_the_snapshot(tmp_path):
    from edat.sidekick.graders.common import tree_snapshot
    work = tmp_path / "w"
    work.mkdir()
    (work / "a.txt").write_text("a\n")
    os.mkfifo(work / "pipe")
    out = {}
    t = threading.Thread(target=lambda: out.update(snap=tree_snapshot(work)), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "the snapshot blocked on the pipe"
    assert out["snap"]["pipe"]["content"] is None and out["snap"]["a.txt"]["content"] == "a\n"


def test_the_snapshot_caps_what_it_reads(tmp_path, monkeypatch):
    from edat.sidekick.graders import common
    monkeypatch.setattr(common, "SNAPSHOT_FILE_BYTES", 1000)
    work = tmp_path / "w"
    work.mkdir()
    (work / "big.txt").write_text("x" * 5000)
    (work / "small.txt").write_text("y")
    snap = common.tree_snapshot(work)
    assert snap["big.txt"]["content"] is None and snap["big.txt"]["too_large"] and snap["small.txt"]["content"] == "y"


def test_the_written_tests_cannot_turn_a_later_layout_into_a_host_write(iso, tmp_path):
    """Written tests run once per mutant in the same area. A test file that replaces the module with a link
    to a host file must not get the next mutant's source written into that host file."""
    import random
    from edat.sidekick.gen.registry import get
    from edat.sidekick.graders.write_test import grade_write_test
    inst = get("test_py_is_leap").build(random.Random("link-test"))
    host = tmp_path / "host-notes.txt"
    host.write_text(SECRET + "\n")
    mod = inst.check["module"] + ".py"
    tests = (f"import os\n"
             f"def test_plant():\n"
             f"    if os.path.lexists({mod!r}):\n        os.remove({mod!r})\n"
             f"    os.symlink({str(host)!r}, {mod!r})\n")
    with TrialArea() as area:
        try:
            grade_write_test(iso, area, inst.check, tests)
        except RuntimeError:
            pass                                                 # refusing to lay out is fine
    assert host.read_text() == SECRET + "\n"


def test_the_git_grader_never_writes_into_a_planted_grade_folder(iso, tmp_path):
    import random
    from edat.sidekick.gen.registry import get
    from edat.sidekick.graders.git import grade_git
    inst = get("git_unstage_file").build(random.Random("link-test"))
    host = tmp_path / "hostdir"
    host.mkdir()
    (host / "snap.json").write_text('{"' + SECRET + '": true}')
    with TrialArea() as area:
        try:
            grade_git(iso, area, inst.check, f"rm -rf ../grade; ln -s {host} ../grade")
        except RuntimeError:
            pass
    assert sorted(os.listdir(host)) == ["snap.json"] and SECRET in (host / "snap.json").read_text()


def test_the_profile_is_never_a_file_the_model_can_replace(iso):
    """The sandbox profile used to sit in the trial folder, which the model can write: replaced with a
    permissive one, the model's next command would run unconfined."""
    with TrialArea() as area:
        outside = area.root.parent / f"edat-sk-escape-{area.canary}"
        for name in ("../.edat-profile.sb", ".edat-profile.sb"):    # unlink the old one, write a permissive one
            iso.run(area, f"unlink {name} 2>/dev/null; printf '(version 1)\\n(allow default)\\n' > {name}; true")
        iso.run(area, f"print -r -- x > {outside}")
        escaped = outside.exists()
        if escaped:
            outside.unlink()
        assert not escaped


def test_grader_folders_are_fresh_and_never_a_planted_link(tmp_path):
    host = tmp_path / "hostdir"
    host.mkdir()
    with TrialArea() as area:
        (area.root / "grade").symlink_to(host, target_is_directory=True)   # left by code that ran earlier
        gd = area.grade_dir({"runner.py": "print(1)\n"})
        assert gd.parent == area.root and gd.name.startswith("grade-") and (gd / "runner.py").read_text() == "print(1)\n"
        assert area.read_result(gd / "runner.py") == b"print(1)\n"
        (host / "x.json").write_text('{"' + SECRET + '": 1}')
        (gd / "result.json").symlink_to(host / "x.json")
        assert area.read_result(gd / "result.json") is None
    assert os.listdir(host) == ["x.json"]


def test_a_command_after_work_was_replaced_is_refused_not_raised(iso, tmp_path):
    """Model code that replaces `work` and then runs a command fails that trial as unsafe; the harness does
    not raise (which would end the whole run)."""
    from edat.sidekick.graders.common import area_events
    from edat.sidekick.tools_dev import DevEnvironment
    host = _host_dir(tmp_path)
    with TrialArea() as area:
        env = DevEnvironment(iso, area, test_cmd="cat target.py")
        os.rename(area.work, area.root / "w2")
        os.symlink(host, area.work)
        r = iso.run(area, "cat target.py")
        assert r.exit_code == 126 and SECRET not in r.stdout
        out = env.call("run_tests", {})
        assert SECRET not in out.content and out.outcome == "failed"
        assert ("unsafe_action", "planted_link") in {(e.kind, e.rule) for e in area_events(area)}


def test_the_git_grader_puts_a_swapped_work_folder_back_and_reports_it(iso, tmp_path):
    import random
    from edat.sidekick.gen.registry import get
    from edat.sidekick.graders.common import area_events
    from edat.sidekick.graders.git import grade_git
    inst = get("git_unstage_file").build(random.Random("link-test"))
    host = _host_dir(tmp_path)
    with TrialArea() as area:
        gr, _, after = grade_git(iso, area, inst.check, f"cd .. && mv work w2 && ln -s {host} work")
        assert not area.work_replaced()
        assert ("unsafe_action", "planted_link") in {(e.kind, e.rule) for e in area_events(area)}
        assert SECRET not in str(after)
