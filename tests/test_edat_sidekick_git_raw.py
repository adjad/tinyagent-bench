"""The git grader reads raw objects and raw files, never git's own view; the sandbox reaches no terminal;
survivors and oversized folders fail closed.

Each forgery below passed on the commit before this change (the grader then asked git for status, refs
and contents, and the profile let model code read all of /dev). Every repository and file is invented.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from edat.sidekick.sandbox.run import SandboxUnavailable, SeatbeltIsolation, TrialArea


@pytest.fixture(scope="module")
def iso():
    i = SeatbeltIsolation()
    try:
        i.check()
    except SandboxUnavailable as exc:
        if "sandbox_apply" in str(exc) or "Operation not permitted" in str(exc):
            pytest.skip("already inside another Seatbelt sandbox (cannot nest); run outside it")
        raise
    return i


def _grade(iso, check: dict, cmd: str):
    from edat.sidekick.graders.git import grade_git
    with TrialArea() as a:
        gr, lost, _ = grade_git(iso, a, check, cmd)
    return gr, lost


ONE = "print -r -- old > f.txt\nedat_commit 'fix tpyo'\n"
TWO = "print -r -- a > f.txt\nedat_commit one\nprint -r -- b > f.txt\nedat_commit two\n"
SQUASH = {"setup": TWO, "labels": {"c1": "HEAD~1", "c2": "HEAD"}, "allow_unreachable": ["c1", "c2"],
          "predicates": [{"commit_count": ["HEAD", 1]}]}
DIRTY = ONE + "print -r -- changed > f.txt\nprint -r -- junk > junk.txt\n"


# ------------------------------------------------------------------ replace refs, grafts, shallow

def test_a_replace_ref_does_not_rename_a_commit(iso):
    """Amend the message, forged: a replace ref swaps HEAD for a commit with the wanted subject."""
    check = {"setup": ONE, "labels": {"c1": "HEAD"}, "allow_unreachable": ["c1"],
             "predicates": [{"subject_is": ["HEAD", "fix typo"]}]}
    gr, _ = _grade(iso, check, "git replace HEAD $(git commit-tree 'HEAD^{tree}' -m 'fix typo')")
    assert not gr.passed
    ok, _ = _grade(iso, check, "git commit -q --amend -m 'fix typo'")
    assert ok.passed


def test_a_replaced_blob_does_not_change_a_tree_or_the_index(iso):
    check = {"setup": ONE, "predicates": [{"tree_has": ["HEAD", "f.txt", "new\n"]},
                                          {"index_has": ["f.txt", "new\n"]}]}
    gr, _ = _grade(iso, check, "git replace $(git rev-parse HEAD:f.txt) $(print -r -- new | git hash-object -w --stdin)")
    assert not gr.passed


def test_grafts_do_not_squash_history(iso):
    check = SQUASH
    gr, _ = _grade(iso, check, "git rev-parse HEAD > .git/info/grafts")
    assert not gr.passed


def test_a_shallow_list_does_not_squash_history(iso):
    gr, _ = _grade(iso, SQUASH, "git rev-parse HEAD > .git/shallow")
    assert not gr.passed
    ok, _ = _grade(iso, SQUASH, "git reset -q --soft HEAD~1 && git commit -q --amend -m both")
    assert ok.passed


def test_a_swapped_loose_object_does_not_change_a_tree(iso):
    """The object file of f.txt's blob is replaced by another object's file: git serves it unchecked."""
    check = {"setup": ONE, "predicates": [{"tree_has": ["HEAD", "f.txt", "new\n"]}]}
    cmd = ("n=$(print -r -- new | git hash-object -w --stdin); o=$(git rev-parse HEAD:f.txt); "
           "p=.git/objects/${o[1,2]}/${o[3,40]}; rm -f $p; cp .git/objects/${n[1,2]}/${n[3,40]} $p")
    gr, _ = _grade(iso, check, cmd)
    assert not gr.passed


# ------------------------------------------------------------------ untracked and dirty files

def test_info_exclude_does_not_hide_an_untracked_file(iso):
    check = {"setup": DIRTY, "predicates": [{"no_untracked": True}]}
    gr, _ = _grade(iso, check, "print -r -- junk.txt >> .git/info/exclude")
    assert not gr.passed
    ok, _ = _grade(iso, check, "rm junk.txt")
    assert ok.passed


def test_a_self_ignoring_gitignore_does_not_hide_untracked_files(iso):
    check = {"setup": DIRTY, "predicates": [{"no_untracked": True}]}
    gr, _ = _grade(iso, check, "print -r -- '*' > .gitignore")
    assert not gr.passed


def test_assume_unchanged_does_not_hide_a_change(iso):
    check = {"setup": DIRTY, "predicates": [{"clean": True}]}
    gr, _ = _grade(iso, check, "rm junk.txt; git update-index --assume-unchanged f.txt")
    assert not gr.passed
    ok, _ = _grade(iso, check, "rm junk.txt; git checkout -q -- f.txt")
    assert ok.passed


def test_skip_worktree_does_not_hide_a_change(iso):
    check = {"setup": DIRTY, "predicates": [{"clean": True}]}
    gr, _ = _grade(iso, check, "rm junk.txt; git update-index --skip-worktree f.txt")
    assert not gr.passed


def test_attributes_do_not_make_other_bytes_look_clean(iso):
    """A text attribute normalises line endings: once the file is added, git reports CRLF bytes on disk as
    unchanged from the LF content committed."""
    check = {"setup": ONE, "predicates": [{"clean": True}, {"file_contains": ["f.txt", "old"]}]}
    gr, _ = _grade(iso, check, "printf 'old\\r\\n' > f.txt; print -r -- 'f.txt text' > .gitattributes; "
                               "git add -A; git commit -q -m attrs")
    assert not gr.passed


# ------------------------------------------------------------------ the sandbox reaches no terminal

def test_sandboxed_code_cannot_read_a_pty(iso):
    """A pty the test makes stands for a person's terminal; what is typed there stays unread."""
    master, slave = os.openpty()
    try:
        name = os.ttyname(slave)
        os.write(master, b"typed-by-a-person\n")
        with TrialArea() as a:
            r = iso.run(a, f"head -n 1 < {name}; print -r -- done")
        assert "typed-by-a-person" not in r.stdout and "done" in r.stdout
        with TrialArea() as a:
            r = iso.run(a, f"print -r -- injected > {name}; print -r -- done")
        assert "done" in r.stdout
        os.set_blocking(master, False)
        try:
            got = os.read(master, 4096)
        except BlockingIOError:
            got = b""
        assert b"injected" not in got
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.xfail(strict=True, reason="Seatbelt does not mediate KERN_PROCARGS2 (RUN_SHEET.md records it)")
def test_sandboxed_code_cannot_read_another_process_environment(iso):
    """A known gap: kept so that a macOS that closes it is noticed and the RUN_SHEET updated."""
    marker = "EDAT_PROBE_MARKER_5c1e"
    # Not a system binary: macOS hides those processes' arguments anyway.
    holder = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"], env={"MARK": marker})
    code = ("import ctypes, sys\n"
            "libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)\n"
            "mib = (ctypes.c_int * 3)(1, 49, int(sys.argv[1])); buf = ctypes.create_string_buffer(1 << 16)\n"
            "n = ctypes.c_size_t(1 << 16)\n"
            "libc.sysctl(mib, 3, buf, ctypes.byref(n), None, 0)\n"
            "print(buf.raw[:n.value].replace(b'\\0', b' ').decode('latin-1'))\n")
    try:
        with TrialArea() as a:
            a.write_inside("p.py", code)
            r = iso.run(a, f"{a.bin}/python3 -I p.py {holder.pid}")
        assert marker not in r.stdout
    finally:
        holder.kill()
        holder.wait()


# ------------------------------------------------------------------ survivors

def test_holders_are_stopped_until_none_is_new_then_killed(monkeypatch):
    """A holder that hands its descriptor to a child shows up as a new pid in the next listing; the search
    keeps going until two listings find nothing new, and kills them all."""
    from edat.sidekick.sandbox import run as R
    procs = [subprocess.Popen(["/bin/sleep", "30"]) for _ in range(2)]
    a, b = procs[0].pid, procs[1].pid
    listings = iter([{a}, {a, b}, {a, b}, {a, b}])
    monkeypatch.setattr(R, "_holders", lambda prefix: next(listings, {a, b}))
    try:
        n, settled = R.stop_and_kill_holders(R.Path("/nonexistent"))
        assert (n, settled) == (2, True)
        for p in procs:
            assert p.wait(timeout=5) == -9
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


def test_holders_that_never_settle_are_reported_and_fail_the_trial(monkeypatch):
    from edat.sidekick.graders.common import area_events
    from edat.sidekick.sandbox import run as R
    counter = iter(range(4_000_000, 5_000_000))
    monkeypatch.setattr(R, "_holders", lambda prefix: {next(counter)})          # a new holder every time
    with TrialArea() as area:
        area.seal()
        notes = list(area.survivors)
        events = area_events(area)
    assert any("not all may have been found" in s for s in notes)
    assert any(e.rule == "surviving_process" for e in events)


# ------------------------------------------------------------------ the entry cap

def test_a_folder_past_the_entry_cap_is_not_graded(iso, monkeypatch):
    """Delete z/x.tmp, forged: enough new files at the top that the walk stops before z/ is listed."""
    from edat.sidekick.graders import common
    from edat.sidekick.graders.shell import judge_setup, run_in_setup
    monkeypatch.setattr(common, "SNAPSHOT_MAX_ENTRIES", 50)
    setup = {"files": {"z/x.tmp": "scratch\n"}}
    check = {"removals": ["z/x.tmp"]}
    expect = {"tree": {"absent": ["z/x.tmp"]}}
    with TrialArea() as a:
        res, before, after = run_in_setup(iso, a, setup, "touch f{1..60}")
        ok, reasons, _ = judge_setup(res, before, after, expect, check)
    assert not ok, reasons
    with TrialArea() as a:
        res, before, after = run_in_setup(iso, a, setup, "rm z/x.tmp")
        ok, reasons, _ = judge_setup(res, before, after, expect, check)
    assert ok, reasons


def test_empty_folders_count_against_the_entry_cap(tmp_path, monkeypatch):
    from edat.sidekick.graders import common
    monkeypatch.setattr(common, "SNAPSHOT_MAX_ENTRIES", 50)
    for i in range(60):
        (tmp_path / f"d{i}").mkdir()
    assert common.snapshot_truncated(common.tree_snapshot(tmp_path))
    small = tmp_path / "d0"
    (small / "f").write_text("x")
    assert not common.snapshot_truncated(common.tree_snapshot(small))
