"""`git` templates: a described situation in a repository of known state; one
to three commands; graded on the repository's resulting state with plumbing
predicates and a loss check (edat/sidekick/graders/git.py).

`setup` is a zsh script run in the (empty) work folder after `git init -b
main`; `edat_commit "subject"` commits everything with a fixed timestamp.
Describe the state in the prompt with `git status`/`git log` style output the
generator writes itself (never by running git).
"""
from __future__ import annotations

import random

from edat.sidekick.gen.core import BRANCH_WORDS, FILE_STEMS, Instance, Template, fence, pick


def _sq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _write(path: str, content: str) -> str:
    return f"mkdir -p \"$(dirname {_sq(path)})\" && printf %s {_sq(content)} > {_sq(path)}\n"


def _new_branch_keep_changes(rng: random.Random) -> Instance:
    br = f"fix-{pick(rng, BRANCH_WORDS)}"
    f1, f2 = [f"{s}.py" for s in rng.sample(FILE_STEMS, 2)]
    v1 = f"def run():\n    return {rng.randint(1, 9)}\n"
    v2 = v1.replace("return", "return 10 +")
    new = f"# draft {rng.randint(100, 999)}\n"
    setup = (_write(f1, v1) + _write("README.md", "# demo\n") + "edat_commit 'Initial commit'\n"
             + _write(f1, v2) + _write(f2, new))
    status = f"On branch main\nChanges not staged for commit:\n\tmodified:   {f1}\n\nUntracked files:\n\t{f2}"
    prompt = (f"I'm on `main` and want a new branch called `{br}` with my uncommitted changes on it. "
              f"Commands only, no editor.\n\n```\n$ git status\n{status}\n```")
    check = {"setup": setup, "labels": {"c1": "HEAD"},
             "predicates": [{"head_branch": br}, {"ref_unchanged": "refs/heads/main"}, {"file": [f1, v2]},
                            {"file": [f2, new]}, {"ref_is": [br, "c1"]}],
             "keep": {"files": {f1: v2, f2: new}}}
    refs = [fence(f"git switch -c {br}", "zsh"), fence(f"git checkout -b {br}", "sh")]
    wrong = [fence(f"git branch {br}", "zsh"), fence(f"git stash\ngit switch -c {br}", "zsh")]
    tool = {"kind": "git"}
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[br], tool=tool)


def _undo_last_commit_keep(rng: random.Random) -> Instance:
    f = f"{pick(rng, FILE_STEMS)}.js"
    msg1 = "Add first draft"
    msg2 = pick(rng, ["Tweak totals", "WIP pricing", "Change rounding", "Try new layout"])
    v1 = f"module.exports = {rng.randint(1, 50)};\n"
    v2 = f"module.exports = {rng.randint(51, 99)};\n"
    setup = _write(f, v1) + f"edat_commit {_sq(msg1)}\n" + _write(f, v2) + f"edat_commit {_sq(msg2)}\n"
    log = f"* {msg2} (HEAD -> main)\n* {msg1}"
    prompt = ("Undo my last commit but keep its changes in my working folder; I haven't pushed. Commands only.\n\n"
              f"```\n$ git log --oneline --graph\n{log}\n```")
    check = {"setup": setup, "labels": {"c1": "HEAD~1", "c2": "HEAD"},
             "predicates": [{"head_branch": "main"}, {"ref_is": ["HEAD", "c1"]}, {"file": [f, v2]}],
             "keep": {"files": {f: v2}}, "allow_unreachable": ["c2"]}
    refs = [fence("git reset --soft HEAD~1", "zsh"), fence("git reset HEAD~1", "zsh"), fence("git reset --mixed HEAD^", "sh")]
    wrong = [fence("git reset --hard HEAD~1", "zsh"), fence("git revert --no-edit HEAD", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong)


def _unstage_file(rng: random.Random) -> Instance:
    a, b = [f"{s}.py" for s in rng.sample(FILE_STEMS, 2)]
    n, m = rng.randint(1, 40), rng.randint(41, 90)
    a1, a2 = f"LIMIT = {n}\n", f"LIMIT = {n + 5}\n"
    b1, b2 = f"RATE = {m}\n", f"RATE = {m}  # trying {m * 2}\n"
    setup = (_write(a, a1) + _write(b, b1) + "edat_commit 'Initial commit'\n"
             + _write(a, a2) + _write(b, b2) + f"git add {_sq(a)} {_sq(b)}\n")
    status = f"On branch main\nChanges to be committed:\n\tmodified:   {a}\n\tmodified:   {b}"
    prompt = (f"I staged both files, but `{b}` shouldn't go into my next commit. Unstage `{b}` only and keep my "
              f"edits to it in the working folder; `{a}` stays staged. Commands only, no editor.\n\n"
              f"```\n$ git status\n{status}\n```")
    check = {"setup": setup, "labels": {"c1": "HEAD"},
             "predicates": [{"head_branch": "main"}, {"ref_unchanged": "refs/heads/main"},
                            {"index_has": [a, a2]}, {"index_has": [b, b1]}, {"file": [a, a2]}, {"file": [b, b2]}],
             "keep": {"files": {a: a2, b: b2}}}
    refs = [fence(f"git restore --staged {b}", "zsh"), fence(f"git reset HEAD -- {b}", "sh"),
            "Use `git reset` with the path:\n\n" + fence(f"git reset {b}", "bash")]
    wrong = [fence("git reset", "zsh"), fence(f"git checkout HEAD -- {b}", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[a, b], tool={"kind": "git"})


_AMEND_PAIRS = [("Add {s} export", "Add {s} exprot"), ("Fix {s} rounding", "Fix {s} roudning"),
                ("Update {s} totals", "Updte {s} totals"), ("Remove unused {s} helper", "Remove unsued {s} helper"),
                ("Speed up {s} lookup", "Sped up {s} lookup"), ("Validate {s} input", "Valdiate {s} input")]


def _amend_message(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    good, typo = (x.format(s=stem) for x in pick(rng, _AMEND_PAIRS))
    f = f"{stem}.py"
    v1 = f"def run():\n    return {rng.randint(1, 9)}\n"
    v2 = v1 + f"\n\ndef extra():\n    return {rng.randint(10, 99)}\n"
    setup = _write(f, v1) + "edat_commit 'Initial commit'\n" + _write(f, v2) + f"edat_commit {_sq(typo)}\n"
    prompt = (f"The message of my last commit has a typo. Change it to `{good}`; the files in the commit stay the same. "
              f"I haven't pushed. Commands only, no editor.\n\n"
              f"```\n$ git log --format='%s%d'\n{typo} (HEAD -> main)\nInitial commit\n```")
    check = {"setup": setup, "labels": {"c1": "HEAD~1", "c2": "HEAD"},
             "predicates": [{"head_branch": "main"}, {"subject_is": ["HEAD", good]}, {"parent_is": ["HEAD", "c1"]},
                            {"tree_has": ["HEAD", f, v2]}, {"clean": True}],
             "allow_unreachable": ["c2"]}
    refs = [fence(f'git commit --amend -m "{good}"', "zsh"), fence(f"git commit --amend --only -m '{good}'", "sh"),
            fence(f'git reset --soft HEAD~1\ngit commit -m "{good}"', "zsh")]
    wrong = [fence(f'git commit -m "{good}"', "zsh"),
             fence(f'git reset --hard HEAD~1\ngit commit --allow-empty -m "{good}"', "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[good], tool={"kind": "git"})


def _discard_one_file(rng: random.Random) -> Instance:
    a, b = [f"{s}.js" for s in rng.sample(FILE_STEMS, 2)]
    a1, a2 = f"module.exports = {rng.randint(1, 50)};\n", f"module.exports = {rng.randint(51, 99)}; // experiment\n"
    b1, b2 = "const x = 1;\n", f"const x = {rng.randint(2, 99)};\n"
    setup = (_write(a, a1) + _write(b, b1) + "edat_commit 'Initial commit'\n" + _write(a, a2) + _write(b, b2))
    status = f"On branch main\nChanges not staged for commit:\n\tmodified:   {a}\n\tmodified:   {b}"
    prompt = (f"Throw away my uncommitted changes to `{a}` (back to the last commit), but keep my changes to `{b}`. "
              f"Commands only.\n\n```\n$ git status\n{status}\n```")
    check = {"setup": setup, "labels": {"c1": "HEAD"},
             "predicates": [{"head_branch": "main"}, {"ref_unchanged": "refs/heads/main"}, {"file": [a, a1]},
                            {"file": [b, b2]}],
             "keep": {"files": {b: b2}}}
    refs = [fence(f"git restore {a}", "zsh"), fence(f"git checkout -- {a}", "sh"),
            fence(f"git checkout HEAD -- {a}", "zsh")]
    wrong = [fence("git checkout -- .", "zsh"), fence(f"git reset -- {a}", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[a, b])


def _rename_branch(rng: random.Random) -> Instance:
    w = pick(rng, BRANCH_WORDS)
    old, new = pick(rng, [(f"feat-{w}", f"feature-{w}"), (f"{w}-wip", f"{w}-v2"), (f"tmp-{w}", f"add-{w}")])
    f = f"{pick(rng, FILE_STEMS)}.py"
    setup = (_write(f, "A = 1\n") + "edat_commit 'Initial commit'\n" + f"git switch -q -c {_sq(old)}\n"
             + _write(f, f"A = {rng.randint(2, 99)}\n") + f"edat_commit 'Start {w}'\n")
    prompt = (f"Rename my current branch `{old}` to `{new}`. It has never been pushed. Commands only.\n\n"
              f"```\n$ git branch\n  main\n* {old}\n```")
    check = {"setup": setup, "labels": {"tip": "HEAD"},
             "predicates": [{"head_branch": new}, {"ref_absent": old}, {"ref_is": [new, "tip"]},
                            {"ref_unchanged": "refs/heads/main"}]}
    refs = [fence(f"git branch -m {new}", "zsh"), fence(f"git branch -m {old} {new}", "sh"),
            fence(f"git branch --move {old} {new}", "zsh")]
    wrong = [fence(f"git switch -c {new}", "zsh"), fence(f"git branch {new}", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[old, new])


def _push_set_upstream(rng: random.Random) -> Instance:
    br = f"{pick(rng, ['add', 'fix', 'try'])}-{pick(rng, BRANCH_WORDS)}"
    f = f"{pick(rng, FILE_STEMS)}.py"
    setup = (_write(f, "A = 1\n") + "edat_commit 'Initial commit'\n" + "git push -q origin main\n"
             + f"git switch -q -c {_sq(br)}\n" + _write(f, f"A = {rng.randint(2, 99)}\n")
             + f"edat_commit 'Work on {br}'\n")
    prompt = (f"I'm on my new local branch `{br}`. Push it to `origin` and make it track `origin/{br}`, so that "
              f"next time a plain `git push` works. Commands only.\n\n"
              f"```\n$ git status\nOn branch {br}\nnothing to commit, working tree clean\n```")
    check = {"setup": setup, "origin": True, "labels": {"tip": "HEAD", "c1": "main"},
             "predicates": [{"head_branch": br}, {"remote_ref_is": [br, "tip"]}, {"remote_ref_is": ["main", "c1"]},
                            {"config": [f"branch.{br}.remote", "origin"]},
                            {"config": [f"branch.{br}.merge", f"refs/heads/{br}"]}]}
    refs = [fence(f"git push -u origin {br}", "zsh"), fence(f"git push --set-upstream origin {br}", "sh"),
            fence("git push -u origin HEAD", "zsh")]
    wrong = [fence(f"git push origin {br}", "zsh"), fence("git push -u origin main", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[br])


def _cherry_pick(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    f, wip = f"{stem}.py", pick(rng, ["layout.css", "draft.md", "sketch.js"])
    exp = pick(rng, ["exp", "spike", "playground", "scratch"])
    fix = pick(rng, [f"Fix {stem} rounding", f"Fix crash in {stem} loader", f"Fix {stem} date parsing"])
    n = rng.randint(1, 9)
    v1, v2 = f"def value():\n    return {n} / 3\n", f"def value():\n    return round({n} / 3, 2)\n"
    r1, r2 = "# demo\n", f"# demo\n\nVersion {rng.randint(2, 9)}.\n"
    setup = (_write(f, v1) + _write("README.md", r1) + "edat_commit 'Initial commit'\n"
             + f"git switch -q -c {exp}\n" + _write(wip, "/* wip */\n") + "edat_commit 'Try new layout'\n"
             + _write(f, v2) + f"edat_commit {_sq(fix)}\n" + "git switch -q main\n"
             + _write("README.md", r2) + "edat_commit 'Update README'\n")
    graph = (f"* {fix} ({exp})\n* Try new layout\n| * Update README (HEAD -> main)\n|/\n* Initial commit")
    prompt = (f"I'm on `main`. I want only the commit \"{fix}\" from branch `{exp}` on `main`, not the "
              f"layout experiment before it. Commands only, no editor.\n\n"
              f"```\n$ git log --graph --all --format='%s%d'\n{graph}\n```")
    check = {"setup": setup, "labels": {"cm": "main"},
             "predicates": [{"head_branch": "main"}, {"parent_is": ["HEAD", "cm"]}, {"subject_is": ["HEAD", fix]},
                            {"tree_has": ["HEAD", f, v2]}, {"tree_has": ["HEAD", "README.md", r2]},
                            {"tree_lacks": ["HEAD", wip]}, {"ref_unchanged": f"refs/heads/{exp}"}]}
    refs = [fence(f"git cherry-pick {exp}", "zsh"), fence(f"git cherry-pick -x {exp}", "sh"),
            fence(f"git switch main\ngit cherry-pick {exp}~0", "zsh")]
    wrong = [fence(f"git merge {exp}", "zsh"), fence(f"git cherry-pick {exp}~1", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[exp])


def _stash_then_switch(rng: random.Random) -> Instance:
    br = f"hotfix-{pick(rng, BRANCH_WORDS)}"
    f = f"{pick(rng, FILE_STEMS)}.py"
    new = pick(rng, ["scratch.py", "notes.md", "plan.txt"])
    v1, v2 = "A = 1\n", "A = 2\n"
    v3 = f"A = 2\nB = {rng.randint(3, 99)}\n"
    nv = f"draft {rng.randint(100, 999)}\n"
    setup = (_write(f, v1) + "edat_commit 'Initial commit'\n" + f"git branch {br}\n" + _write(f, v2)
             + "edat_commit 'Bump A'\n" + _write(f, v3) + _write(new, nv))
    status = f"On branch main\nChanges not staged for commit:\n\tmodified:   {f}\n\nUntracked files:\n\t{new}"
    prompt = (f"I'm in the middle of work on `main` and need to switch to `{br}` to check something. Set all my "
              f"uncommitted work aside without committing it, including the new file `{new}`, so the working "
              f"folder is clean on `{br}`. Commands only.\n\n```\n$ git status\n{status}\n```")
    check = {"setup": setup, "labels": {},
             "predicates": [{"head_branch": br}, {"clean": True}, {"no_untracked": True}, {"stash_count": 1},
                            {"ref_unchanged": "refs/heads/main"}, {"ref_unchanged": f"refs/heads/{br}"}],
             "keep": {"files": {f: v3, new: nv}}}
    refs = [fence(f"git stash -u\ngit switch {br}", "zsh"),
            fence(f'git stash push --include-untracked -m "wip"\ngit checkout {br}', "sh"),
            fence(f"git stash --all && git switch {br}", "zsh")]
    wrong = [fence(f"git stash\ngit switch {br}", "zsh"), fence(f"git checkout -f {br}", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[br, new])


def _squash_last_two(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    f, g = f"{stem}.py", f"test_{stem}.py"
    s2, s3 = pick(rng, [("WIP", "WIP 2"), (f"Start {stem} export", f"Finish {stem} export"), ("part 1", "part 2")])
    msg = pick(rng, [f"Add {stem} export", f"Export {stem} as CSV", f"Add {stem} report"])
    v1 = "def export():\n    pass\n"
    v2 = f"def export():\n    return '{stem}'\n"
    gv = f"def test_export():\n    assert True  # {rng.randint(1, 99)}\n"
    setup = (_write(f, v1) + "edat_commit 'Initial commit'\n" + _write(f, v2) + f"edat_commit {_sq(s2)}\n"
             + _write(g, gv) + f"edat_commit {_sq(s3)}\n")
    prompt = (f"Combine my last two commits into one commit with the message `{msg}`. They aren't pushed. "
              f"Commands only, no editor.\n\n"
              f"```\n$ git log --format='%s%d'\n{s3} (HEAD -> main)\n{s2}\nInitial commit\n```")
    check = {"setup": setup, "labels": {"c1": "HEAD~2", "c2": "HEAD~1", "c3": "HEAD"},
             "predicates": [{"head_branch": "main"}, {"subject_is": ["HEAD", msg]}, {"parent_is": ["HEAD", "c1"]},
                            {"tree_has": ["HEAD", f, v2]}, {"tree_has": ["HEAD", g, gv]}, {"clean": True}],
             "allow_unreachable": ["c2", "c3"]}
    refs = [fence(f'git reset --soft HEAD~2\ngit commit -m "{msg}"', "zsh"),
            fence(f"git reset --soft HEAD~2 && git commit -m '{msg}'", "sh"),
            fence(f'git reset HEAD~2\ngit add -A\ngit commit -m "{msg}"', "zsh")]
    wrong = [fence(f'git commit --amend -m "{msg}"', "zsh"),
             fence(f'git reset --hard HEAD~2\ngit commit --allow-empty -m "{msg}"', "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[msg])


def _recover_reset_reflog(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    f, g = f"{stem}.py", f"{pick(rng, ['helpers', 'utils', 'checks'])}.py"
    sa = pick(rng, [f"Add {stem} filters", f"Parse {stem} dates", f"Cache {stem} lookups"])
    sb = pick(rng, ["Add helpers", "Add input checks", "Split out utils"])
    v1, v2 = "A = 1\n", f"A = {rng.randint(2, 99)}\n"
    gv = f"def check(x):\n    return x > {rng.randint(0, 9)}\n"
    setup = (_write(f, v1) + "edat_commit 'Initial commit'\n" + _write(f, v2) + f"edat_commit {_sq(sa)}\n"
             + _write(g, gv) + f"edat_commit {_sq(sb)}\n" + "git reset -q --hard HEAD~2\n")
    reflog = (f"HEAD@{{0}}: reset: moving to HEAD~2\nHEAD@{{1}}: commit: {sb}\nHEAD@{{2}}: commit: {sa}\n"
              f"HEAD@{{3}}: commit (initial): Initial commit")
    prompt = (f"I ran `git reset --hard HEAD~2` on `main` by mistake and my last two commits (\"{sa}\" and "
              f"\"{sb}\") are gone. I had nothing uncommitted. Put `main` back where it was. Commands only.\n\n"
              f"```\n$ git reflog -4 --format='%gd: %gs'\n{reflog}\n```")
    check = {"setup": setup, "labels": {"lost": "HEAD@{1}"},
             "predicates": [{"head_branch": "main"}, {"ref_is": ["main", "lost"]}, {"clean": True},
                            {"file": [g, gv]}, {"file": [f, v2]}]}
    refs = [fence("git reset --hard HEAD@{1}", "zsh"), fence("git reset --hard 'main@{1}'", "sh"),
            fence("git reset --hard ORIG_HEAD", "zsh"), fence("git merge --ff-only 'HEAD@{1}'", "zsh")]
    wrong = [fence("git reset --hard HEAD@{2}", "zsh"), fence("git checkout HEAD@{1}", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong)


def _recover_dropped_stash(rng: random.Random) -> Instance:
    f = f"{pick(rng, FILE_STEMS)}.py"
    v1 = f"def total(xs):\n    return sum(xs)\n"
    v2 = f"def total(xs):\n    return round(sum(xs), {rng.randint(1, 4)})\n"
    setup = (_write(f, v1) + _write("README.md", "# demo\n") + "edat_commit 'Initial commit'\n"
             + _write(f, v2) + "edat_tick\ngit stash -q\ngit stash drop -q\n")
    prompt = (f"I ran `git stash drop` by mistake. That stash was the only copy of my changes to `{f}`, and I "
              f"didn't note the commit id it printed. Give me commands that find the dropped stash and put those "
              f"changes back in my working folder; I can't read output in between. Commands only.\n\n"
              f"```\n$ git stash list\n$ git status\nOn branch main\nnothing to commit, working tree clean\n```")
    check = {"setup": setup, "labels": {},
             "predicates": [{"head_branch": "main"}, {"ref_unchanged": "refs/heads/main"}, {"file": [f, v2]}]}
    refs = [fence("git stash apply $(git fsck --no-reflogs | grep 'dangling commit' | cut -d' ' -f3)", "zsh"),
            fence("git fsck --unreachable | grep commit | cut -d' ' -f3 | xargs git log --merges --no-walk "
                  "--grep=WIP --format=%H | head -n 1 | xargs git stash apply", "sh"),
            fence("sha=$(git fsck --lost-found | awk '$2 == \"commit\" {print $3}')\ngit stash apply \"$sha\"", "zsh")]
    wrong = [fence("git stash pop", "zsh"), fence(f"git checkout stash@{{0}} -- {f}", "zsh")]
    return Instance(prompt, "git", "git", check, refs, wrong, given_names=[f])


TEMPLATES = [
    Template("git_new_branch_keep_changes", "git", "L1", "git", _new_branch_keep_changes, tool_mode=True),
    Template("git_unstage_file", "git", "L1", "git", _unstage_file, tool_mode=True),
    Template("git_amend_message", "git", "L1", "git", _amend_message, tool_mode=True),
    Template("git_undo_last_commit_keep", "git", "L2", "git", _undo_last_commit_keep),
    Template("git_discard_one_file", "git", "L1", "git", _discard_one_file),
    Template("git_rename_branch", "git", "L1", "git", _rename_branch),
    Template("git_push_set_upstream", "git", "L2", "git", _push_set_upstream),
    Template("git_cherry_pick_one", "git", "L2", "git", _cherry_pick),
    Template("git_stash_then_switch", "git", "L2", "git", _stash_then_switch),
    Template("git_squash_last_two", "git", "L2", "git", _squash_last_two),
    Template("git_recover_reset_reflog", "git", "L3", "git", _recover_reset_reflog),
    Template("git_recover_dropped_stash", "git", "L3", "git", _recover_dropped_stash),
]
