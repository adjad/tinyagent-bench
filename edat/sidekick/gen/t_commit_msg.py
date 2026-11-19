"""`commit_msg` templates: a diff of up to 40 lines; graded on format rules, key
facts, and a no-invented-names check (every file name or identifier-like token
in the message must appear in the diff: list them all in `diff_names`).
"""
from __future__ import annotations

import random

import re

from edat.sidekick.gen.core import FILE_STEMS, NOUNS, Instance, Template, fence, pick
from edat.sidekick.graders.text import NAME_TOKEN_RE


def _rename_function(rng: random.Random) -> Instance:
    noun = pick(rng, NOUNS)
    old, new = f"get_{noun[:3]}", f"get_{noun}"
    if old == new:
        old = f"get_{noun}_"
    mod = f"{pick(rng, FILE_STEMS)}.py"
    caller = f"{pick(rng, ['views', 'api', 'cli'])}.py"
    diff = (f"diff --git a/{mod} b/{mod}\n--- a/{mod}\n+++ b/{mod}\n@@ -10,7 +10,7 @@\n"
            f"-def {old}(id):\n+def {new}(id):\n     return DB[id]\n"
            f"diff --git a/{caller} b/{caller}\n--- a/{caller}\n+++ b/{caller}\n@@ -3,9 +3,9 @@\n"
            f"-from {mod[:-3]} import {old}\n+from {mod[:-3]} import {new}\n"
            f"@@ -20,4 +20,4 @@\n-    item = {old}(request.id)\n+    item = {new}(request.id)\n"
            f"@@ -41,4 +41,4 @@\n-    return {old}(key)\n+    return {new}(key)\n")
    prompt = f"Write a commit message for this diff.\n\n```diff\n{diff}```"
    facts = {"rename": [r"\brenam", r"\b(old|previous) name\b"],
             "old": [rf"\b{old}\b"], "new": [rf"\b{new}\b"]}
    names = [mod, caller, old, new, mod[:-3], caller[:-3], "request.id", "DB"]
    refs = [f"Rename {old} to {new}\n\nUpdate the call sites in {caller}.",
            fence(f"Rename {old}() to {new}()", "text"),
            f"refactor: rename {old} to {new}\n\nThe old name was an abbreviation; {caller} now imports {new}."]
    wrong = ["Fixed stuff.", f"Renamed {old} to {new} in {mod}, utils.py and models.py."]
    return Instance(prompt, "text", "commit_msg",
                    {"facts": facts, "forbid": [], "diff_names": names, "subject_max": 72},
                    refs, wrong, given_names=[old, new])



def _names(diff: str, *extra: str) -> list[str]:
    """Every file name and identifier-like token in the diff, plus extras (so only invented names are flagged)."""
    found = set(NAME_TOKEN_RE.findall(diff)) | set(extra)
    found |= {t.rsplit("/", 1)[-1] for t in found}
    return sorted(found)


def _inst(diff: str, facts: dict, refs: list[str], wrong: list[str], given: list[str], *extra: str) -> Instance:
    prompt = f"Write a commit message for this diff.\n\n```diff\n{diff}```"
    return Instance(prompt, "text", "commit_msg",
                    {"facts": facts, "forbid": [], "diff_names": _names(diff, *extra), "subject_max": 72},
                    refs, wrong, given_names=given)


def _empty_query(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    noun = pick(rng, NOUNS)
    fn = f"search_{noun}s"
    a = rng.randint(8, 30)
    diff = (f"diff --git a/{stem}.py b/{stem}.py\n--- a/{stem}.py\n+++ b/{stem}.py\n@@ -{a},5 +{a},7 @@\n"
            f" def {fn}(query, rows):\n+    if not query.strip():\n+        return []\n"
            f"     needle = query.lower()\n     return [r for r in rows if needle in r[\"name\"].lower()]\n"
            f"diff --git a/test_{stem}.py b/test_{stem}.py\n--- a/test_{stem}.py\n+++ b/test_{stem}.py\n@@ -14,3 +14,7 @@\n"
            f"     assert {fn}(\"ap\", ROWS) == [ROWS[0]]\n+\n+\n+def test_{fn}_empty_query():\n+    assert {fn}(\"  \", ROWS) == []\n")
    facts = {"empty": [r"\bempty\b", r"\bblank\b", r"\bwhitespace\b"],
             "early": [r"return(s|ing)? (early|an empty|\[\]|no )", r"\bearly\b", r"short.?circuit", r"\bskip", r"\bguard", r"no (results|matches)",
                       r"instead of (matching|returning) (every|all)"],
             "test": [r"\btests?\b", r"\btest_", r"\.test\.", r"coverage"]}
    refs = [f"Return no results for an empty query\n\nAdd an early return in {fn} when the query is blank, plus a test for it.",
            f"Short-circuit {fn} on empty query\n\nAn empty search used to match every row. Add test_{stem}.py coverage.",
            f"fix: skip search when the query is empty\n\n{fn} now returns [] for blank input, and test_{fn}_empty_query covers it."]
    wrong = ["Update search", f"Fixed the query bug and added tests to {stem}.py"]
    return _inst(diff, facts, refs, wrong, [fn])


def _pagination(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    noun = pick(rng, NOUNS)
    fn = f"get{noun.capitalize()}Page"
    size = rng.choice([2, 3, 5])
    diff = (f"diff --git a/{stem}.js b/{stem}.js\n--- a/{stem}.js\n+++ b/{stem}.js\n@@ -6,5 +6,5 @@\n"
            f" function {fn}(rows, page, pageSize) {{\n-  const start = page * pageSize;\n+  const start = (page - 1) * pageSize;\n"
            f"   return rows.slice(start, start + pageSize);\n }}\n"
            f"diff --git a/{stem}.test.js b/{stem}.test.js\n--- a/{stem}.test.js\n+++ b/{stem}.test.js\n@@ -9,3 +9,6 @@\n"
            f"+test(\"first page starts at the first row\", () => {{\n+  expect({fn}(rows, 1, {size})).toEqual(rows.slice(0, {size}));\n+}});\n")
    facts = {"page": [r"\bpages?\b"],
             "offbyone": [r"off.by.one", r"first page", r"skip(s|ped|ping)", r"1.based", r"one.based", r"page (1|one)\b", r"start(s|ing)? (at|from) (0|zero)"]}
    refs = [f"Fix off-by-one in page offset\n\nPages are 1-based, so {fn} skipped the first rows of data.",
            f"Use 1-based page numbers in {fn}\n\nThe offset is now (page - 1) * pageSize, with a test for page 1.",
            f"Correct page offset so page 1 returns the first rows\n\n{fn} computed the start index from page directly."]
    wrong = ["Update pagination", "Fix off-by-one error in sortRows"]
    return _inst(diff, facts, refs, wrong, [fn])


def _config_option(rng: random.Random) -> Instance:
    opt, val = pick(rng, [("max_retries", "3"), ("page_size", "25"), ("log_level", "\"info\""), ("cache_ttl", "300"), ("poll_interval", "10")])
    plain = val.strip('"')
    diff = (f"diff --git a/settings.py b/settings.py\n--- a/settings.py\n+++ b/settings.py\n@@ -3,6 +3,11 @@\n"
            f" DEFAULTS = {{\n     \"theme\": \"light\",\n+    \"{opt}\": {val},\n }}\n \n \n"
            f" def get_setting(cfg, key):\n     return cfg.get(key, DEFAULTS[key])\n+\n+\n+def get_{opt}(cfg):\n+    return get_setting(cfg, \"{opt}\")\n")
    facts = {"option": [rf"\b{opt}\b", rf"{opt.replace('_', ' ')}"],
             "default": [r"\bdefault"], "value": [rf"\b{re.escape(plain)}(?![\w])"]}
    refs = [f"Add {opt} option with default {plain}\n\nRead it through the new get_{opt} helper.",
            f"Make {opt} configurable, defaulting to {plain}",
            f"Introduce {opt} setting\n\nIt defaults to {plain} when the config does not set it."]
    wrong = ["Add a new option", f"Add a setting with default {plain} to config.py"]
    return _inst(diff, facts, refs, wrong, [opt])


def _remove_dead(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    fn = f"format{pick(rng, ['Legacy', 'Old', 'Draft'])}{pick(rng, ['Date', 'Label', 'Price'])}"
    diff = (f"diff --git a/{stem}.js b/{stem}.js\n--- a/{stem}.js\n+++ b/{stem}.js\n@@ -1,14 +1,6 @@\n"
            f" const {{ parseDate }} = require(\"./dates\");\n-\n-function {fn}(d) {{\n-  const day = d.getDate();\n"
            f"-  return `${{pad(day)}}/${{pad(d.getMonth() + 1)}}/${{d.getFullYear()}}`;\n-}}\n \n"
            f" function pad(n) {{ return String(n).padStart(2, \"0\"); }}\n \n"
            f"-module.exports = {{ parseDate, {fn} }};\n+module.exports = {{ parseDate }};\n")
    facts = {"removed": [r"\b(remov|delet|drop|get rid)\w*"], "name": [rf"\b{fn}\b"],
             "why": [r"unused|dead|obsolete|legacy|no longer|not (used|called)|never (used|called)|nothing (calls|uses)"]}
    refs = [f"Remove unused {fn}",
            f"Drop dead {fn} helper\n\nAlso remove it from the module exports.",
            f"Delete {fn}\n\nNothing calls it any more, so it goes out of {stem}.js along with its export."]
    wrong = [f"Clean up {stem}.js", f"Removed unused code from helpers.js"]
    return _inst(diff, facts, refs, wrong, [fn])


def _bump_timeout(rng: random.Random) -> Instance:
    stem = pick(rng, FILE_STEMS)
    old = rng.choice([3, 5, 10])
    new = rng.choice([20, 30, 45, 60])
    fn = f"fetch_{pick(rng, NOUNS)}"
    diff = (f"diff --git a/{stem}.py b/{stem}.py\n--- a/{stem}.py\n+++ b/{stem}.py\n@@ -4,7 +4,8 @@\n import requests\n \n"
            f"-DEFAULT_TIMEOUT = {old}\n+# the report endpoint can take more than {old} seconds to answer\n+DEFAULT_TIMEOUT = {new}\n \n \n"
            f" def {fn}(url):\n     return requests.get(url, timeout=DEFAULT_TIMEOUT)\n")
    facts = {"timeout": [r"time.?out"], "value": [rf"\b{new}(?!\d)"]}
    refs = [f"Increase request timeout from {old} to {new} seconds",
            f"Raise DEFAULT_TIMEOUT to {new}\n\nThe report endpoint is slow, so {fn} needs more time before it gives up.",
            f"Lengthen the HTTP timeout to {new}s for slow endpoints"]
    wrong = ["Update client", f"Increase timeout to {new + 7} seconds"]
    return _inst(diff, facts, refs, wrong, [fn], "DEFAULT_TIMEOUT")


TEMPLATES = [
    Template("commit_rename_function", "commit_msg", "L1", "text", _rename_function, keyfact_refs_min=3, human_graded=True),
    Template("commit_empty_query", "commit_msg", "L1", "python", _empty_query, keyfact_refs_min=3, human_graded=True),
    Template("commit_bump_timeout", "commit_msg", "L1", "python", _bump_timeout, keyfact_refs_min=3, human_graded=True),
    Template("commit_fix_pagination", "commit_msg", "L2", "javascript", _pagination, keyfact_refs_min=3, human_graded=True),
    Template("commit_config_option", "commit_msg", "L2", "python", _config_option, keyfact_refs_min=3, human_graded=True),
    Template("commit_remove_dead_fn", "commit_msg", "L2", "javascript", _remove_dead, keyfact_refs_min=3, human_graded=True),
]
