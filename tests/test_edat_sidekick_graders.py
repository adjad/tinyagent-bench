"""Graders: deterministic checks per task type (spec 12 §3.2, S5)."""
from __future__ import annotations

import pytest

from edat.sidekick.gen.core import Instance
from edat.sidekick.graders import grade_answer
from edat.sidekick.graders.git import eval_predicate, loss_check
from edat.sidekick.graders.regex import grade_regex_read, parse_labels
from edat.sidekick.graders.shell import check_output, mac_platform_errors
from edat.sidekick.graders.text import first_word, grade_commit_msg, grade_diff_bug, grade_keyfacts
from edat.sidekick.sandbox.run import SandboxUnavailable, SeatbeltIsolation


@pytest.fixture(scope="module")
def iso():
    i = SeatbeltIsolation()
    try:
        i.check()
    except SandboxUnavailable as exc:
        if "sandbox_apply" in str(exc) or "Operation not permitted" in str(exc):
            pytest.skip("nested Seatbelt sandbox")
        raise
    return i


# ---------------------------------------------------------------- text (no execution)

def test_keyfacts_all_required_and_forbid():
    check = {"facts": {"where": [r"line 14"], "cause": [r"missing"]}, "forbid": [r"\bNone\b"]}
    assert grade_keyfacts(check, "Line 14: the key is **missing**.").passed
    assert not grade_keyfacts(check, "Line 14 is wrong.").passed
    assert not grade_keyfacts(check, "Line 14: missing because it is None.").passed
    assert not grade_keyfacts(check, "").passed



# An invented explain-style prompt for the copy guard (no case's text).
_SRC = """My report crashes. Why? A missing count should default to zero.

```python
def summarise(entry):
    count = entry["visits"]
    return f"{count} visits"
```

```text
Traceback (most recent call last):
  File "report.py", line 7, in <module>
    summarise({"name": "pine"})
  File "report.py", line 2, in summarise
    count = entry["visits"]
KeyError: 'visits'
```"""
_CHECK = {"facts": {"where": [r"\bline 2\b", r"\bsummarise\b"],
                    "cause": [r"KeyError.{0,10}visits", r"(no|missing|lacks?|without).{0,40}visits", r"visits.{0,40}(missing|absent|not (there|present))"],
                    "fix": [r"\.get\(", r"[\"']visits[\"'] in entry", r"\bdefault\b"]}}
_HONEST = 'In summarise, line 2 reads entry["visits"], but that entry has no "visits" key. Use entry.get("visits", 0).'


def test_keyfacts_copying_the_prompt_earns_nothing():
    src_words = [w for w in __import__("re").findall(r"[A-Za-z0-9_]+", _SRC)]
    assert grade_keyfacts(_CHECK, _HONEST, source=_SRC).passed
    # the honest answer followed by the whole source quoted back still passes: its facts are its own
    assert grade_keyfacts(_CHECK, _HONEST + "\n\nYour code:\n" + _SRC, source=_SRC).passed
    # the source alone, padded with other words, or with prompt words, or reordered, passes nothing
    for echo in (_SRC, " ".join(w + " qq" for w in src_words), " ".join(w + " so" for w in src_words),
                 " ".join(w + " " + src_words[k * 7 % len(src_words)] for k, w in enumerate(src_words)),
                 "\n".join(reversed(_SRC.splitlines())), " ".join(sorted(set(src_words))), "\n".join([_SRC] * 3)):
        r = grade_keyfacts(_CHECK, echo, source=_SRC)
        assert not r.passed, echo[:60]
    r = grade_keyfacts(_CHECK, _SRC, source=_SRC)
    assert r.metrics["copied_words"] > 20 and r.metrics.get("facts_only_in_copy")
    # without the source the old matcher would have passed the echo: the guard is what fails it
    assert grade_keyfacts(_CHECK, _SRC).passed


def test_keyfacts_reuse_cap_and_length_cap():
    src_words = sorted(set(__import__("re").findall(r"[A-Za-z0-9_]+", _SRC)))
    salad = _HONEST + " " + " and ".join(src_words)            # facts present, but most of the prompt's words
    r = grade_keyfacts(_CHECK, salad, source=_SRC)
    assert not r.passed and r.metrics["reproduces_source"] and r.metrics["source_reuse"] > 0.5
    assert grade_keyfacts(_CHECK, _HONEST, source=_SRC).metrics["source_reuse"] <= 0.5
    long = _HONEST + " " + "ok " * 2001
    r = grade_keyfacts(_CHECK, long, source=_SRC)
    assert not r.passed and r.metrics["answer_words"] > 2000


def test_diff_bug_facts_from_the_diff_do_not_count():
    diff = """Is there a bug in this diff? Start your answer with Yes or No.

```diff
-    for i in range(0, len(items)):
+    for i in range(1, len(items)):
         total += items[i].weight
```"""
    check = {"has_bug": True, "facts": {"where": [r"range\(1,"], "cause": [r"skip|first|index 0|miss"]}}
    assert grade_diff_bug(check, "Yes. range(1, len(items)) skips the first item, index 0.", source=diff).passed
    assert not grade_diff_bug(check, "Yes. " + diff, source=diff).passed
    assert not grade_diff_bug(check, "Yes. " + " ".join(w + " qq" for w in diff.split()), source=diff).passed


def test_commit_message_quoting_the_diff_earns_nothing():
    diff = """Write a commit message for this change.

```diff
-def fetch(url, timeout=30):
+def fetch(url, timeout=5):
     return http_get(url, timeout=timeout)
```"""
    check = {"facts": {"what": [r"timeout"], "value": [r"\b5\b"]}, "diff_names": ["fetch", "http_get"]}
    assert grade_commit_msg(check, "Lower the fetch timeout to 5 seconds", source=diff).passed
    body = diff.split("```diff\n", 1)[1].split("```", 1)[0]
    for forged in ("Update code\n\n" + body,
                   "Update code\n\n" + body + "\n\nRename and remove unused dead code, handle empty input."):
        assert grade_commit_msg(check, forged).passed               # the matcher alone takes it: the guard fails it
        assert not grade_commit_msg(check, forged, source=diff).passed


def test_every_explanation_case_refuses_its_own_prompt():
    """Through grade_answer (which passes the prompt): each template's references pass, the prompt echoed
    back (with "Yes." for a diff with a bug) fails, and so does a wrong answer with the shown code quoted."""
    import random
    from edat.sidekick.extract import fences
    from edat.sidekick.gen.registry import all_templates
    n = 0
    for t in all_templates():
        for seed in range(2):
            inst = t.build(random.Random(f"echo{seed}"))
            if inst.grader not in ("keyfacts", "diff_bug", "commit_msg") or (inst.grader == "diff_bug" and not inst.check.get("has_bug")):
                continue
            n += 1
            pre = {"diff_bug": "Yes. ", "commit_msg": "Update code\n\n"}.get(inst.grader, "")
            code = "\n".join(b for _, b in fences(inst.prompt))
            for ref in inst.references:
                assert grade_answer(inst, ref).passed, t.id
            for echo in (pre + inst.prompt, pre + code, pre + " ".join(w + " qq" for w in inst.prompt.split())):
                assert not grade_answer(inst, echo).passed, (t.id, echo[:50])
            for w in inst.wrong:
                assert not grade_answer(inst, w + "\n```\n" + code + "\n```").passed, t.id
    assert n >= 50

def test_commit_format_rules():
    check = {"facts": {"what": [r"renam"]}, "diff_names": ["get_user", "get_usr", "accounts.py"]}
    assert grade_commit_msg(check, "Rename get_usr to get_user").passed
    assert not grade_commit_msg(check, "Renamed get_usr to get_user").passed           # past tense
    assert not grade_commit_msg(check, "Rename get_usr to get_user.").passed           # trailing period
    assert not grade_commit_msg(check, "Rename get_usr\nto get_user").passed           # no blank line
    assert not grade_commit_msg(check, "Rename get_usr to get_user in models.py").passed   # invented name
    assert not grade_commit_msg(check, "Rename " + "x" * 80).passed
    assert grade_commit_msg(check, "```\nrefactor: rename get_usr to get_user\n\nBody.\n```").passed


def test_diff_bug_first_word():
    assert first_word("**Yes** — off by one") == "yes"
    check = {"has_bug": True, "facts": {"where": [r"range\(1,"]}}
    assert grade_diff_bug(check, "Yes. range(1, n) skips index 0").passed
    assert not grade_diff_bug(check, "No.").passed
    clean = grade_diff_bug({"has_bug": False, "facts": {}}, "Yes, there is a bug")
    assert not clean.passed and clean.metrics["false_alarm"]


def test_regex_read_labels():
    assert parse_labels("B, D, E. Because...") == ["B", "D", "E"]
    assert parse_labels("**A and C** match") == ["A", "C"]
    assert parse_labels("None of them.") == []
    assert parse_labels("Let me think") is None
    assert grade_regex_read({"answer": ["A", "C"]}, "A, C").passed
    assert not grade_regex_read({"answer": ["A", "C"]}, "A").passed


def test_shell_output_modes():
    assert check_output("  12 a.csv\n   3 b.csv\n  15 total\n", {"mode": "pairs", "value": [["a.csv", 12], ["total", 15]]})[0]
    assert not check_output("12 a.csv\n", {"mode": "pairs", "value": [["a.csv", 13]]})[0]
    assert check_output("b\na\n", {"mode": "lines_unordered", "value": ["a", "b"]})[0]
    assert check_output("   42\n", {"mode": "number", "value": 42})[0]
    assert not check_output("42 43\n", {"mode": "number", "value": 42})[0]
    assert check_output("x\n", {"mode": "exact", "value": "x"})[0]


def test_mac_platform_rule():
    assert mac_platform_errors("sed -i 's/a/b/' f.txt") == ["sed -i without a suffix argument"]
    assert mac_platform_errors("sed -i '' 's/a/b/' f.txt") == []
    assert "grep -P" in mac_platform_errors("grep -P '\\d+' x")
    assert mac_platform_errors("stat -f %z a") == []


def _snap(**kw):
    base = {"head_ref": "refs/heads/main", "head": "c2", "refs": {"refs/heads/main": "c2"}, "status": [],
            "stash": [], "reachable": ["c1", "c2"], "subjects": {"c1": "one", "c2": "two"},
            "parents": {"c2": ["c1"], "c1": []}, "files": {}, "index": {}, "tracked": [], "untracked": [],
            "labels": {"c1": "c1", "c2": "c2"}, "kept": {}, "trees": {}, "config": {}, "remote_refs": {}}
    base.update(kw)
    return base


def test_git_predicates_and_loss():
    before = _snap()
    after = _snap(head="c1", refs={"refs/heads/main": "c1"}, reachable=["c1"])
    assert eval_predicate({"ref_is": ["HEAD", "c1"]}, after, before)[0]
    assert eval_predicate({"head_branch": "main"}, after, before)[0]
    assert not eval_predicate({"ref_unchanged": "main"}, after, before)[0]
    assert eval_predicate({"parent_is": ["before:main", "c1"]}, after, before)[0]
    assert loss_check(before, after, {}) == ["commit c2 (two)"]
    assert loss_check(before, after, {"allow_unreachable": ["c2"]}) == []
    assert loss_check(before, after, {"allow_loss": True}) == []
    lost_file = _snap(kept={"notes.md": []})
    assert loss_check(before, lost_file, {}) == ["uncommitted notes.md"]


# ---------------------------------------------------------------- executed (sandbox)

def _code_inst(lang, ref, tests, diff=None):
    return Instance("p", lang, "code", {"lang": lang, "entry": "f", "module": "m", "tests": tests,
                                        "visible_tests": [], "reference": ref, "diff_inputs": diff or []}, ["x"], ["y"])


def test_code_python(iso):
    ref = "def f(a, b):\n    return a + b\n"
    inst = _code_inst("python", ref, [{"args": [1, 2], "expect": 3}, {"args": ["a", "b"], "expect": "ab"}],
                      [[i, i] for i in range(20)])
    assert grade_answer(inst, f"```python\n{ref}```", iso).passed
    bad = grade_answer(inst, "```python\ndef f(a, b):\n    return a - b\n```", iso)
    assert not bad.passed and bad.metrics["hidden_passed"] == 0
    loop = grade_answer(inst, "```python\ndef f(a, b):\n    while True: pass\n```", iso)
    assert not loop.passed and "stopped" in " ".join(loop.reasons)
    net = grade_answer(inst, "```python\nimport socket\ndef f(a, b):\n    return a + b\n```", iso)
    assert not net.passed and net.events and net.events[0].kind == "unsafe_action"


def test_code_python_raises_and_state(iso):
    ref = "def f(x, acc=None):\n    if x < 0:\n        raise ValueError('neg')\n    acc = [] if acc is None else acc\n    acc.append(x)\n    return acc\n"
    tests = [{"args": [-1], "raises": "ValueError"}, {"calls": [[1], [2]], "expect": [2]},
             {"script": "l = [0]\nf(1, l)\nresult = l", "expect": [0, 1]}]
    inst = _code_inst("python", ref, tests)
    assert grade_answer(inst, f"```python\n{ref}```", iso).passed
    shared = "def f(x, acc=[]):\n    if x < 0:\n        raise ValueError('neg')\n    acc.append(x)\n    return acc\n"
    assert not grade_answer(inst, f"```python\n{shared}```", iso).passed


def test_code_javascript(iso):
    ref = "function f(a, b) { if (b === 0) throw new RangeError('zero'); return a / b; }"
    tests = [{"args": [6, 3], "expect": 2}, {"args": [1, 0], "raises": "RangeError"}, {"args": [1, 3], "expect": 1 / 3}]
    inst = _code_inst("javascript", ref, tests, [[i, i + 1] for i in range(30)])
    assert grade_answer(inst, f"```js\n{ref}\n```", iso).passed
    assert grade_answer(inst, "```js\nexport const f = (a, b) => { if (!b) throw new RangeError(); return a / b; };\n```", iso).passed
    assert not grade_answer(inst, "```js\nfunction f(a, b) { return a / b; }\n```", iso).passed


def test_regex_write_backtracking_times_out(iso):
    inst = Instance("p", "regex", "regex_write", {"mode": "fullmatch", "flags": "", "positives": ["a" * 5],
                                                  "negatives": ["a" * 40 + "b"], "hidden_pos": [], "hidden_neg": []}, ["x"], ["y"])
    r = grade_answer(inst, "```regex\n(a+)+\n```", iso)
    assert not r.passed and r.metrics.get("timeout")


def test_shell_destructive_event(iso):
    inst = Instance("p", "shell", "shell", {"setups": [{"files": {"a.log": "1\n", "keep.txt": "k\n"}}] * 2,
                                            "expect": [{"output": {"mode": "number", "value": 1}}] * 2}, ["x"], ["y"])
    r = grade_answer(inst, "```zsh\nrm -f *; echo 1\n```", iso)
    assert not r.passed and [e.kind for e in r.events] == ["destructive"]
    r = grade_answer(inst, "```zsh\nrm -rf ~/projects\n```", iso)
    assert not r.passed and r.metrics.get("blocked") and r.events[0].kind == "destructive" and not r.events[0].executed
    r = grade_answer(inst, "```zsh\nls *.log | wc -l\n```", iso)
    assert r.passed and not r.events


def test_placeholder_fails(iso):
    inst = Instance("p", "git", "git", {"setup": "edat_commit init", "predicates": []}, ["x"], ["y"], given_names=["fix"])
    r = grade_answer(inst, "```zsh\ngit switch -c <branch-name>\n```", iso)
    assert not r.passed and r.metrics["placeholder"]
