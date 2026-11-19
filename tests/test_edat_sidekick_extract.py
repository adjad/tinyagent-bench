"""Answer extraction and placeholder detection (spec 12 §3.2, S4)."""
from __future__ import annotations

from edat.sidekick.extract import extract, find_placeholders, regex_from_snippet, visible_answer


def test_first_matching_fence():
    a = "Try this:\n```python\nprint(1)\n```\nor\n```python\nprint(2)\n```"
    e = extract(a, "python")
    assert e.ok and e.snippet == "print(1)" and e.how == "fence"


def test_tag_must_match_or_be_untagged():
    assert extract("```js\nx()\n```", "python").ok is False
    e = extract("```\nls -la\n```", "shell")
    assert e.ok and e.how == "fence_untagged"
    assert extract("```bash\nls\n```", "shell").snippet == "ls"


def test_single_line_and_inline_span():
    assert extract("git switch -c fix", "git").snippet == "git switch -c fix"
    assert extract("`wc -l *.csv`", "shell").snippet == "wc -l *.csv"
    e = extract("Run `git stash` and then\nswitch branches.", "git")
    assert e.ok and e.snippet == "git stash"
    assert not extract("line one\nline two", "python").ok


def test_reasoning_removed():
    assert visible_answer("<think>long thoughts</think>\nThe answer") == "The answer"
    assert visible_answer("<think>never closed") == ""
    assert not extract("<think>```python\nx=1\n```</think>", "python").ok


def test_empty_fails():
    assert not extract("", "python").ok
    assert not extract("   ", "shell").ok


def test_placeholders():
    assert find_placeholders("git switch -c <branch-name>") == ["<branch-name>"]
    assert find_placeholders("cp your-file.txt backup/")
    assert find_placeholders("cd path/to/project")
    assert find_placeholders("sort < data.txt") == []
    assert find_placeholders("diff <(sort a) <(sort b)") == []
    assert find_placeholders("git switch -c fix-login", ["fix-login"]) == []
    assert find_placeholders("find . -exec rm {} +") == []


def test_regex_forms():
    assert regex_from_snippet(r"[A-Z]{2}-\d{4}") == r"[A-Z]{2}-\d{4}"
    assert regex_from_snippet(r"r'^\d{5}$'") == r"^\d{5}$"
    assert regex_from_snippet(r"/^\d+$/g") == r"^\d+$"
    assert regex_from_snippet('import re\nCODE = re.compile(r"[a-z]+")') == "[a-z]+"
    assert regex_from_snippet("#[0-9a-fA-F]{6}") == "#[0-9a-fA-F]{6}"
    assert regex_from_snippet("# five digits\n[0-9]{5}") == "[0-9]{5}"
