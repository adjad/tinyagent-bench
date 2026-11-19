"""The git grader: git's own view of a sanitised copy is graded, with the view mechanisms (replace refs,
grafts, ignore rules, index flags, attributes, the model's config) closed; a strict raw reader of the same
copy is a tripwire whose disagreements are flagged for a person and never change a grade. Git tasks are
labelled not hardened. Also: the sandbox reaches no terminal or other process; survivors and oversized
folders fail closed. Every repository and file is invented.
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


def _flagged(gr) -> bool:
    """The tripwire fired: a flag in the metrics, never an event, never a grade change."""
    return gr.metrics.get("git_view_mismatch", 0) > 0 and bool(gr.metrics.get("git_view_flags")) \
        and not any(e.rule == "git_view_mismatch" for e in gr.events)


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
    assert _flagged(gr)
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
    assert _flagged(gr)


def test_a_shallow_list_does_not_squash_history(iso):
    gr, _ = _grade(iso, SQUASH, "git rev-parse HEAD > .git/shallow")
    assert _flagged(gr)
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


# ------------------------------------------------------------------ strict parsing: read nothing git reads differently

def _case(tid):
    import random
    from edat.sidekick.gen.registry import get
    return get(tid).build(random.Random("git-raw-tests"))


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _answer(iso, inst, cmd):
    from edat.sidekick.graders import grade_answer
    return grade_answer(inst, "```zsh\n" + cmd + "\n```", iso)


def _mismatch(iso, inst, cmd) -> bool:
    """The raw reader refuses what git reads, or reads it differently: flagged, and graded on git's view."""
    return _flagged(_answer(iso, inst, cmd))


ID = "Test Person <test@example.test> 1767614400 +0000"


def _amend_forgery(write_ref: str) -> tuple:
    inst = _case("git_amend_message")
    mk = (f"c=$(git commit-tree 'HEAD^{{tree}}' -p HEAD~1 -m {_q(inst.given_names[0])}); o=$(git rev-parse HEAD); "
          "print -r -- \"$c refs/heads/main\" > .git/packed-refs; ")
    return inst, mk + write_ref


@pytest.mark.parametrize("write_ref", [
    'print -r -- "$o x" > .git/refs/heads/main',                                   # git reads "<sha> junk"
    'print -r -- ${(U)o} > .git/refs/heads/main',                                  # and upper-case hex
    'git branch -q keep $o; print -r -- "ref:refs/heads/keep" > .git/refs/heads/main',   # and "ref:" with no space
])
def test_a_loose_ref_git_reads_differently_never_falls_back_to_packed_refs(iso, write_ref):
    inst, cmd = _amend_forgery(write_ref)
    assert _mismatch(iso, inst, cmd)


def test_a_second_tree_header_does_not_squash_a_commit(iso):
    inst = _case("git_squash_last_two")
    cmd = (f"t2=$(git rev-parse 'HEAD~1^{{tree}}'); t3=$(git rev-parse 'HEAD^{{tree}}'); p=$(git rev-parse HEAD~2); "
           f"c=$(printf 'tree %s\\nparent %s\\nauthor {ID}\\ncommitter {ID}\\ntree %s\\n\\n%s\\n' $t2 $p $t3 "
           f"{_q(inst.given_names[0])} | git hash-object -t commit -w --stdin); git update-ref refs/heads/main $c")
    assert _mismatch(iso, inst, cmd)


def test_a_stash_log_without_refs_stash_is_no_stash(iso):
    inst = _case("git_stash_then_switch")
    cmd = (f"git stash -u -q; git switch -q {inst.given_names[0]}; mv .git/logs/refs/stash .git/st; "
           "git update-ref -d refs/stash; mv .git/st .git/logs/refs/stash")
    assert _mismatch(iso, inst, cmd)


def test_a_config_include_fails_the_config_read(iso):
    inst = _case("git_push_set_upstream")
    b = inst.given_names[0]
    cmd = (f"git push -q -u origin {b}; printf '[include]\\n\\tpath = up.cfg\\n' >> .git/config; "
           f"printf '[branch \"%s\"]\\n\\tremote = nowhere\\n' {b} > .git/up.cfg")
    assert _mismatch(iso, inst, cmd)


def test_a_tree_naming_a_file_twice_is_not_read(iso):
    """Git takes the first of two entries with one name; a reader taking the last would see "new"."""
    check = {"setup": ONE, "predicates": [{"tree_has": ["HEAD", "f.txt", "new\n"]}]}
    cmd = ("o=$(git rev-parse HEAD:f.txt); n=$(print -r -- new | git hash-object -w --stdin); "
           "t=$(python3 -c 'import sys; o, n = (bytes.fromhex(x) for x in sys.argv[1:]); "
           "sys.stdout.buffer.write(b\"100644 f.txt\\0\" + o + b\"100644 f.txt\\0\" + n)' $o $n "
           "| git hash-object -t tree -w --literally --stdin); "
           "c=$(git commit-tree $t -p HEAD -m dup); git update-ref refs/heads/main $c")
    gr, _ = _grade(iso, check, cmd)
    assert _flagged(gr)


def test_packed_refs_out_of_order_are_not_read(iso):
    """A false "sorted" header lets git miss a ref by bisection that a line-by-line reader finds."""
    check = {"setup": ONE, "predicates": [{"ref_exists": "feature"}]}
    cmd = ("h=$(git rev-parse HEAD); git update-ref -d refs/heads/main 2>/dev/null; "
           "printf '# pack-refs with: peeled fully-peeled sorted \\n%s refs/heads/zz\\n%s refs/heads/feature\\n"
           "%s refs/heads/main\\n' $h $h $h > .git/packed-refs")
    gr, _ = _grade(iso, check, cmd)
    assert _flagged(gr)
    ok, _ = _grade(iso, check, "git branch feature")
    assert ok.passed


def test_an_honest_repository_shows_no_mismatch(iso):
    """Branches, a merge, a stash with untracked files, a staged and an unstaged change, a tag, a remote with
    an upstream: git and the raw reader agree on every fact."""
    check = {"setup": TWO + "git tag -a v1 -m v1\n", "origin": True, "predicates": [{"head_branch": "main"}]}
    cmd = ("git switch -q -c side; print -r -- s > s.txt; git add s.txt; git commit -q -m side; git switch -q main; "
           "git merge -q --no-edit side; git push -q -u origin main; print -r -- tmp > u.txt; git stash -u -q; "
           "print -r -- c >> f.txt; git add f.txt; print -r -- d >> f.txt; print -r -- n > new.txt")
    gr, _ = _grade(iso, check, cmd)
    assert gr.passed and not gr.events and gr.metrics["git_view_mismatch"] == 0, (gr.reasons, gr.metrics)


# ------------------------------------------------------------------ round 3: nothing left out, nothing cut short

PAD = "python3 -c \"import sys; sys.stdout.write('# ' + 'x' * (1 << 20) + '\\n')\" >> .git/config; "


@pytest.mark.parametrize("tail", [
    "printf '[branch \"%s\"]\\n\\tremote = nowhere\\n' {b} >> .git/config",                  # a later override
    "printf '[include]\\n\\tpath = up.cfg\\n' >> .git/config; "                              # or an include
    "printf '[branch \"%s\"]\\n\\tremote = nowhere\\n' {b} > .git/up.cfg",
])
def test_a_config_past_the_read_limit_is_not_read_at_all(iso, tail):
    """Git reads all of a config; a reader that stops at its limit would miss what comes after it. Past the
    cap nothing is read, so no config predicate holds, and the trial is flagged."""
    inst = _case("git_push_set_upstream")
    b = inst.given_names[0]
    g = _answer(iso, inst, f"git push -q -u origin {b}; " + PAD + tail.format(b=b))
    assert not g.passed and _flagged(g), (g.reasons, g.metrics)


def test_config_worktree_is_read_where_git_reads_it(iso):
    inst = _case("git_push_set_upstream")
    b = inst.given_names[0]
    from edat.sidekick.graders import grade_answer
    cmd = (f"git push -q -u origin {b}; git config extensions.worktreeConfig true; "
           f"git config --worktree branch.{b}.remote nowhere")
    assert not grade_answer(inst, "```zsh\n" + cmd + "\n```", iso).passed
    ok = grade_answer(inst, f"```zsh\ngit push -q -u origin {b}; git config extensions.worktreeConfig true\n```", iso)
    assert ok.passed and not ok.events and ok.metrics["git_view_mismatch"] == 0, (ok.reasons, ok.metrics)


AMEND_TO = "c=$(git commit-tree 'HEAD^{{tree}}' -p HEAD~1 -m {m}); git update-ref refs/heads/main $c; "


@pytest.mark.parametrize("elsewhere", [
    # git would read refs (and objects) from a shadow repository
    "mkdir -p ../shadow/refs/heads && cp -R .git/objects ../shadow/ && git rev-parse HEAD@{1} > ../shadow/refs/heads/main "
    "&& cp .git/config ../shadow/config && echo ../../shadow > .git/commondir",
    "git config core.worktree ../../wt2",                                       # a work tree elsewhere
    "git refs migrate --ref-format=reftable",                                   # refs git keeps in a format not read
    # the same by hand, on any git (`refs migrate` needs 2.46): a reftable folder and the extension
    "mkdir -p .git/reftable && print -r -- x > .git/reftable/tables.list && git config extensions.refStorage reftable "
    "&& git config core.repositoryformatversion 1",
    "git config core.bare true",
    "git rev-parse HEAD > .git/info/grafts",
    "git replace HEAD HEAD~1",
])
def test_a_repository_git_reads_from_elsewhere_is_flagged(iso, elsewhere):
    """The copy leaves the pointer out (git then reads what is stored here); the trial says so."""
    from edat.sidekick.toolchain import find_toolchain, git_version
    if elsewhere.startswith("git refs migrate") and git_version(find_toolchain().git.version) < (2, 46):
        pytest.skip("`git refs migrate` needs git 2.46; this git cannot make a reftable repository that way "
                    "(the hand-made variant runs on every git)")
    inst = _case("git_amend_message")
    assert _mismatch(iso, inst, AMEND_TO.format(m=_q(inst.given_names[0])) + elsewhere)


def test_a_flag_never_changes_the_grade(iso):
    """An include the raw reader refuses and git (includes off) reads past: flagged, and still passed."""
    check = {"setup": ONE, "predicates": [{"head_branch": "main"}, {"clean": True}]}
    gr, _ = _grade(iso, check, "printf '[include]\\n\\tpath = none.cfg\\n' >> .git/config")
    assert gr.passed and not gr.events and _flagged(gr), (gr.reasons, gr.metrics)
    assert gr.metrics["grading"] == "git_on_copy" and gr.metrics["grade_hardened"] is False


def test_a_file_at_the_size_limit_never_stops_the_snapshot(iso):
    """The sandbox's file-size limit stops the write (below what either reader reads whole); the file that is
    left is kept by id, its text dropped, and the trial is graded."""
    check = {"setup": ONE, "predicates": [{"untracked": "big.bin"}, {"file": ["f.txt", "old\n"]}]}
    gr, _ = _grade(iso, check, "head -c 40000000 /dev/zero > big.bin")
    assert gr.passed and not gr.events and gr.metrics["git_view_mismatch"] == 0, (gr.reasons, gr.metrics)


BASE = "printf 'A = 1\\n' > a.py && printf '# demo\\n' > README.md && edat_commit 'Initial commit'\n"
DIRS = BASE + "mkdir d1 d2 && printf '1\\n' > d1/x && printf '2\\n' > d2/y && edat_commit dirs\n"
NFD = "python3 -c \"import unicodedata as u; open(u.normalize('NFD', 'caf\\u00e9.txt'), 'w').write('x')\""


def test_a_link_to_a_folder_and_a_nested_repository_are_untracked_entries(iso):
    check = {"setup": BASE + "mkdir src && printf 'x\\n' > src/m.py && edat_commit src\n",
             "predicates": [{"no_untracked": True}]}
    for cmd, entry in (("ln -s src lib", "lib"), ("git init -q sub && printf 's\\n' > sub/s.txt", "sub/")):
        gr, _ = _grade(iso, check, cmd)
        assert not gr.passed and not _flagged(gr), (cmd, gr.reasons)
        ok, _ = _grade(iso, {**check, "predicates": [{"untracked": entry}, {"not_tracked": "sub/s.txt"}]}, cmd)
        assert ok.passed and not ok.events and ok.metrics["git_view_mismatch"] == 0, (cmd, ok.reasons, ok.metrics)


@pytest.mark.parametrize("setup,cmd,preds", [
    (BASE + "mkdir src && printf 'x\\n' > src/m.py && ln -s src lib && edat_commit link\n", "true",
     [{"clean": True}, {"no_untracked": True}, {"tracked": "lib"}]),
    (DIRS, "git sparse-checkout set d1", [{"clean": True}, {"no_untracked": True}]),
    (DIRS, "git sparse-checkout init --cone --sparse-index && git sparse-checkout set d1",
     [{"clean": True}, {"tracked": "d2/y"}]),
    (BASE, "git update-index --skip-worktree a.py && printf 'A = 9\\n' > a.py", [{"clean": False}]),
    (BASE, "git update-index --assume-unchanged a.py && printf 'A = 9\\n' > a.py", [{"clean": False}]),
    # No attribute is applied by either reader: CRLF bytes on disk against the LF blob git stored are a change
    ("printf '* text=auto\\n' > .gitattributes && printf 'a\\r\\nb\\r\\n' > w.txt && edat_commit crlf\n", "true",
     [{"clean": False}, {"tree_has": ["HEAD", "w.txt", "a\nb\n"]}]),
    (BASE, "git switch -q --orphan o && git commit -q --allow-empty -m root", [{"head_branch": "o"}]),
    (BASE, "git commit -q --allow-empty -m '   Fix typo'", [{"subject_is": ["HEAD", "   Fix typo"]}]),
    (BASE, NFD + " && git add -A && git commit -q -m nfd", [{"clean": True}, {"tracked": "caf\u00e9.txt"}]),
    (BASE, NFD, [{"untracked": "caf\u00e9.txt"}]),
    (BASE, "mv README.md readme.md", [{"clean": True}]),
    (BASE, "git mv README.md readme.md && git commit -q -m rename", [{"clean": True}, {"tracked": "readme.md"}]),
    (BASE, "head -c 6000000 /dev/zero > big.bin", [{"untracked": "big.bin"}]),
    (BASE, "git tag -a tt -m tree 'HEAD^{tree}' && git tag blob HEAD:a.py", [{"ref_exists": "refs/tags/tt"}]),
    # neither reader looks inside a submodule
    (BASE, "git init -q sub && (cd sub && printf s > s && git add s && git -c user.name=T -c user.email=t@example.test "
     "commit -q -m s) && git add sub 2>/dev/null && git commit -q -m sub && printf x >> sub/s", [{"clean": True}]),
], ids=["link to a folder", "sparse cone", "sparse index", "skip-worktree edited", "assume-unchanged edited",
        "text=auto with CRLF", "orphan empty root", "leading spaces in a subject", "NFD name committed",
        "NFD name untracked", "case-only mv", "case-only git mv", "6 MB untracked", "tags of a tree and a blob",
        "submodule with dirty content"])
def test_honest_repository_states_read_the_same_both_ways(iso, setup, cmd, preds):
    """Each passes, with no event and no flag: the raw reader and git agree on every fact."""
    gr, _ = _grade(iso, {"setup": setup, "predicates": preds}, cmd)
    assert gr.passed and not gr.events and gr.metrics["git_view_mismatch"] == 0, (gr.reasons, gr.metrics)


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


def test_sandboxed_code_learns_nothing_about_another_process(iso):
    """Not its arguments or environment (KERN_PROCARGS2), its kinfo_proc (KERN_PROC_PID), its path, its
    details (proc_pidinfo) or the list of processes, by any of the routes the reviews tried."""
    marker = "EDAT_PROBE_MARKER_5c1e"
    # Not a system binary: macOS hides those processes' arguments anyway.
    holder = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)", marker],
                              env={"MARK": marker})
    code = ("import ctypes, sys\n"
            "libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)\n"
            "pid = int(sys.argv[1]); seen = []\n"
            "def sysctl(mib):\n"
            "    a = (ctypes.c_int * len(mib))(*mib); buf = ctypes.create_string_buffer(1 << 16)\n"
            "    n = ctypes.c_size_t(1 << 16)\n"
            "    r = libc.sysctl(a, len(mib), buf, ctypes.byref(n), None, 0)\n"
            "    return buf.raw[:n.value] if r == 0 else b''\n"
            "seen.append(sysctl([1, 49, pid]))                       # KERN_PROCARGS2\n"
            "seen.append(sysctl([1, 14, 1, pid]))                    # KERN_PROC_PID\n"
            "mib = (ctypes.c_int * 4)(); n = ctypes.c_size_t(4)\n"
            "if libc.sysctlnametomib(b'kern.procargs2', mib, ctypes.byref(n)) == 0:\n"
            "    seen.append(sysctl(list(mib[:n.value]) + [pid]))\n"
            "path = ctypes.create_string_buffer(4096)\n"
            "print('pidpath', libc.proc_pidpath(pid, path, 4096))\n"
            "info = ctypes.create_string_buffer(1024)\n"
            "print('pidinfo', libc.proc_pidinfo(pid, 3, 0, info, 1024))   # PROC_PIDTBSDINFO\n"
            "print('listpids', libc.proc_listpids(1, 0, None, 0))\n"
            "print(b' '.join(seen).replace(b'\\0', b' ').decode('latin-1'))\n")
    try:
        with TrialArea() as a:
            a.write_inside("p.py", code)
            r = iso.run(a, f"{a.bin}/python3 -I p.py {holder.pid}")
        assert "pidpath" in r.stdout, r.stderr[-300:]
        assert marker not in r.stdout
        assert "pidpath 0" in r.stdout and "pidinfo 0" in r.stdout
        assert "listpids 0" in r.stdout or "listpids -1" in r.stdout
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


# ------------------------------------------------------------------ git before 2.40 (no --attr-source)

def test_git_versions_are_read_and_old_ones_refused(monkeypatch):
    from edat.sidekick import toolchain
    assert toolchain.git_version("git version 2.39.5 (Apple Git-154)") == (2, 39)
    assert toolchain.git_version("git version 2.54.0 (Apple Git-157)\n") == (2, 54)
    assert toolchain.git_version("not git") == (0, 0)
    monkeypatch.delenv("EDAT_SIDEKICK_GIT", raising=False)
    monkeypatch.setattr(toolchain, "_run", lambda cmd: "git version 2.37.1 (Apple Git-137.1)" if cmd[-1] == "--version"
                        else "/usr/bin/git")
    monkeypatch.setattr(toolchain, "_real", lambda p: p)
    with pytest.raises(toolchain.ToolchainError, match="older than git 2.39"):
        toolchain._git()


@pytest.fixture
def git_239(monkeypatch):
    """Grade as on a git without --attr-source (the CI runner's Apple git 2.39): the fixed info/attributes alone."""
    import edat.sidekick.graders.git as g
    monkeypatch.setattr(g, "attr_source_supported", lambda: False)


def test_without_attr_source_attributes_still_do_not_make_other_bytes_look_clean(iso, git_239):
    check = {"setup": ONE, "predicates": [{"clean": True}, {"file_contains": ["f.txt", "old"]}]}
    gr, _ = _grade(iso, check, "printf 'old\\r\\n' > f.txt; print -r -- 'f.txt text' > .gitattributes; "
                               "git add -A; git commit -q -m attrs")
    assert not gr.passed


@pytest.mark.parametrize("template", ["git_new_branch_keep_changes", "git_unstage_file", "git_stash_then_switch",
                                      "git_push_set_upstream"])
def test_without_attr_source_git_references_pass(template, iso, git_239):
    from edat.sidekick.gen.registry import get
    from edat.sidekick.validate import validate_templates
    rep = validate_templates([get(template)], trials=1, iso=iso)
    assert rep.ok, [p.what for p in rep.problems]


def test_without_attr_source_text_auto_crlf_is_a_change(iso, git_239):
    check = {"setup": "printf '* text=auto\\n' > .gitattributes && printf 'a\\r\\nb\\r\\n' > w.txt && edat_commit crlf\n",
             "predicates": [{"clean": False}, {"tree_has": ["HEAD", "w.txt", "a\nb\n"]}]}
    gr, _ = _grade(iso, check, "true")
    assert gr.passed and not gr.events and gr.metrics["git_view_mismatch"] == 0, (gr.reasons, gr.metrics)


def test_the_grader_git_is_stated_by_absolute_path(monkeypatch):
    from edat.sidekick import toolchain
    monkeypatch.setenv("EDAT_SIDEKICK_GIT", "git")
    with pytest.raises(toolchain.ToolchainError, match="absolute path"):
        toolchain._git()
    monkeypatch.setenv("EDAT_SIDEKICK_GIT", "/nonexistent/bin/git")
    with pytest.raises(toolchain.ToolchainError, match="not an executable file"):
        toolchain._git()


def test_every_git_row_records_the_git_version(iso):
    from edat.sidekick.toolchain import find_toolchain
    gr, _ = _grade(iso, {"setup": ONE, "predicates": [{"clean": True}]}, "true")
    assert gr.passed and gr.metrics["git_version"] == find_toolchain().git.version.strip().removeprefix("git version ")


def test_a_fixture_the_grader_git_cannot_read_stops_the_run_and_fails_no_answer(iso, monkeypatch):
    """The case's own repository, before the model acts, unreadable by the host's git: never a failed answer."""
    import edat.sidekick.graders.git as g
    from edat.sidekick.sandbox.run import SandboxUnavailable
    monkeypatch.setattr(g, "snapshot", lambda *a, **k: {"unreadable": "git could not read the repository (for-each-ref)"})
    with pytest.raises(g.GitGraderUnavailable, match="host's git, not the answer") as exc:
        _grade(iso, {"setup": ONE, "predicates": [{"clean": True}]}, "true")
    assert isinstance(exc.value, SandboxUnavailable)      # the runner and the ceiling stop on it
