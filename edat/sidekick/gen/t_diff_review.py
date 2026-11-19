"""`diff_review` templates: "what does this change do?" (key facts, variant
`what`), "is there a bug in this diff?" with a planted bug (variant `bug`) or
a clean diff (variant `clean`, which measures false alarms, SK-11).
"""
from __future__ import annotations

import random

from edat.sidekick.gen.core import FIELDS, NOUNS, VERBS, Instance, Template, pick


def _what_timeout(rng: random.Random) -> Instance:
    t = pick(rng, [3, 5, 10, 15, 30])
    noun = pick(rng, NOUNS)
    fn = f"fetch_{noun}"
    diff = (f"--- a/client.py\n+++ b/client.py\n@@ -1,8 +1,13 @@\n import requests\n+import logging\n+\n+log = logging.getLogger(__name__)\n \n \n"
            f" def {fn}(url):\n-    resp = requests.get(url)\n-    return resp.json()\n"
            f"+    try:\n+        resp = requests.get(url, timeout={t})\n+    except requests.RequestException as exc:\n"
            f"+        log.warning(\"{fn} failed: %s\", exc)\n+        return None\n+    return resp.json()\n")
    prompt = f"What does this change do? Two or three sentences.\n\n```diff\n{diff}```"
    facts = {"timeout": [rf"time.?out.{{0,40}}\b{t}\b", rf"\b{t}[\s-]*(s|sec|seconds?)\b"],
             "logged": [r"\blog(s|ged|ging)?\b.{0,60}(fail|error|exception)", r"(fail|error|exception)\w*.{0,60}\blog(s|ged|ging)?\b", r"warning"],
             "returns_none": [r"\bNone\b", r"returns? nothing", r"instead of (raising|crashing)"]}
    refs = [f"It adds a {t}-second timeout to the request in `{fn}` and catches request errors: a failure is logged "
            f"as a warning and the function returns None instead of raising.",
            f"`{fn}` now passes `timeout={t}` to `requests.get`. If the call fails, the exception is logged and None is returned.",
            f"Requests now time out after {t} seconds; failures get logged with log.warning and the function returns None."]
    wrong = [f"It renames {fn} and changes the URL.", "It switches the HTTP library from requests to urllib."]
    return Instance(prompt, "text", "keyfacts", {"facts": facts, "forbid": []}, refs, wrong)


def _bug_skips_first(rng: random.Random) -> Instance:
    arr = pick(rng, ["items", "rows", "orders", "users"])
    fn = f"check_{arr}"
    start = rng.randint(10, 60)
    diff = (f"--- a/validate.py\n+++ b/validate.py\n@@ -{start},6 +{start},6 @@\n def {fn}({arr}):\n"
            f"     # check every item\n-    for i in range(len({arr})):\n+    for i in range(1, len({arr})):\n"
            f"         if not {arr}[i].valid:\n             return False\n     return True\n")
    line = start + 2
    prompt = (f"Is there a bug in this diff? Start your answer with Yes or No.\n\n```diff\n{diff}```")
    facts = {"location": [r"range\(1,", rf"\bline {line}\b", r"\bloop\b", r"start(s|ing)? at (index )?1"],
             "cause": [r"(skip|miss|ignor|never check)\w*.{0,40}(first|index 0|\[0\]|element 0)",
                       r"(first|index 0|\[0\]).{0,40}(skip|miss|not (checked|validated)|never)"]}
    refs = [f"Yes. `range(1, len({arr}))` starts at 1, so the first item is skipped and never checked.",
            f"Yes, line {line}: the loop now misses index 0, so an invalid first element is not detected.",
            f"**Yes** — changing to `range(1, ...)` skips the first element even though the comment says every item."]
    wrong = ["No, the change looks fine.", f"Yes. `{fn}` should return None instead of False."]
    return Instance(prompt, "text", "diff_bug", {"has_bug": True, "facts": facts, "forbid": []}, refs, wrong)


def _clean_rename_var(rng: random.Random) -> Instance:
    a, b = rng.sample(["total", "acc", "running", "subtotal", "s"], 2)
    diff = (f"--- a/sum.py\n+++ b/sum.py\n@@ -1,5 +1,5 @@\n def add_all(xs):\n-    {a} = 0\n+    {b} = 0\n"
            f"     for x in xs:\n-        {a} += x\n-    return {a}\n+        {b} += x\n+    return {b}\n")
    prompt = f"Is there a bug in this diff? Start your answer with Yes or No.\n\n```diff\n{diff}```"
    refs = ["No. It only renames a local variable; behaviour is unchanged.", f"No — `{a}` is renamed to `{b}` everywhere it is used.",
            "No bug: a pure rename."]
    wrong = [f"Yes. `{b}` is never initialised.", "Yes, the loop is off by one."]
    return Instance(prompt, "text", "diff_bug", {"has_bug": False, "facts": {}, "forbid": []}, refs, wrong)



def _diff(path: str, start: int, rows: list[tuple[str, str]]) -> tuple[str, list[int]]:
    """Unified diff of one hunk. Returns the text and, per row, its line number in the new file."""
    old_n = sum(1 for t, _ in rows if t in " -")
    new_n = sum(1 for t, _ in rows if t in " +")
    out = f"--- a/{path}\n+++ b/{path}\n@@ -{start},{old_n} +{start},{new_n} @@\n"
    nums, cur = [], start
    for tag, text in rows:
        out += f"{tag}{text}\n"
        nums.append(cur)
        if tag in " +":
            cur += 1
    return out, nums


def _bug_prompt(diff: str) -> str:
    return f"Is there a bug in this diff? Start your answer with Yes or No.\n\n```diff\n{diff}```"


def _bug_inst(diff: str, facts: dict, refs: list[str], wrong: list[str]) -> Instance:
    return Instance(_bug_prompt(diff), "text", "diff_bug", {"has_bug": True, "facts": facts, "forbid": []}, refs, wrong)


def _what_dedupe_tags(rng: random.Random) -> Instance:
    fn = f"{pick(rng, VERBS)}{pick(rng, NOUNS).capitalize()}Tags"
    start = rng.randint(3, 40)
    diff, _ = _diff("tags.js", start, [
        (" ", f"function {fn}(list) {{"),
        ("-", "  return list.map(t => t.trim());"),
        ("+", "  return [...new Set(list.map(t => t.trim().toLowerCase()))];"),
        (" ", "}")])
    prompt = f"What does this change do? Two or three sentences.\n\n```diff\n{diff}```"
    facts = {"lowercase": [r"lower.?case", r"case.?insensitiv", r"to lower\b", r"\bcase\b"],
             "dedupe": [r"dup", r"uniq", r"dedup", r"distinct", r"\bSet\b", r"repeated", r"only once"]}
    refs = [f"Tags are now lowercased as well as trimmed, and duplicates are removed through a Set, so `{fn}` returns each distinct tag once.",
            f"`{fn}` still trims each tag, but now also converts it to lower case and drops repeated tags.",
            "After this change 'Foo' and 'foo ' collapse into a single entry: the function lowercases every tag and de-duplicates the list."]
    wrong = ["It trims whitespace from every tag in the list.", "It sorts the tags alphabetically and removes the empty ones."]
    return Instance(prompt, "text", "keyfacts", {"facts": facts, "forbid": []}, refs, wrong)


def _what_retry_backoff(rng: random.Random) -> Instance:
    n = rng.choice([3, 4, 5])
    word = {3: "three", 4: "four", 5: "five"}[n]
    fn = f"send_{pick(rng, NOUNS)}"
    start = rng.randint(1, 20)
    diff, _ = _diff("sender.py", start, [
        ("+", "import time"), ("+", ""), (" ", f"def {fn}(msg):"),
        ("-", "    return post(msg)"),
        ("+", f"    for attempt in range({n}):"), ("+", "        try:"), ("+", "            return post(msg)"),
        ("+", "        except ConnectionError:"), ("+", "            time.sleep(2 ** attempt)"),
        ("+", "    raise ConnectionError(\"gave up\")")])
    prompt = f"What does this change do? Two or three sentences.\n\n```diff\n{diff}```"
    facts = {"retry": [r"retr(y|ies|ied)", r"again", r"repeat", r"attempt"],
             "count": [rf"\b{n}\b", rf"\b{word}\b"],
             "wait": [r"sleep", r"wait", r"pause", r"delay", r"back.?off", r"doubl", r"exponential"]}
    refs = [f"`{fn}` now retries `post(msg)` up to {n} times when a ConnectionError happens, sleeping 1, 2, 4... seconds between attempts, "
            f"and raises ConnectionError if every attempt fails.",
            f"A failed send is tried again: up to {word} attempts with an exponential backoff between them. After the last failure it raises "
            f"a ConnectionError saying it gave up.",
            f"Instead of a single call, it loops {n} times, waits longer after each connection error (2 to the power of the attempt number seconds) and raises once it runs out of attempts."]
    wrong = [f"It adds logging for every failed `{fn}` call.", f"It runs `{fn}` in a background thread so the caller does not block."]
    return Instance(prompt, "text", "keyfacts", {"facts": facts, "forbid": []}, refs, wrong)


def _bug_is_string(rng: random.Random) -> Instance:
    mode = pick(rng, ["fast", "quiet", "strict", "dry", "full"])
    fn = f"pick_{pick(rng, ['speed', 'level', 'style', 'plan'])}"
    start = rng.randint(2, 50)
    diff, nums = _diff("modes.py", start, [
        (" ", f"def {fn}(mode):"),
        ("-", f"    if mode == \"{mode}\":"),
        ("+", f"    if mode is \"{mode}\":"),
        (" ", "        return 2"), (" ", "    return 1")])
    line = nums[2]
    facts = {"location": [rf"mode is [\"']{mode}", rf"\bline {line}\b", r"`?\bis\b`?.{0,30}(operator|comparison|compar|string)", rf"\bis\b.{{0,12}}{mode}", rf"\b{fn}\b", r"== ?(to|with)? ?`?is\b|`is`"],
             "cause": [r"identity", r"same object", r"(compar\w+|check\w*|test\w*) (the )?(object|identity|id|memory)",
                       r"\bis\b.{0,40}(not|isn.?t|doesn.?t).{0,30}(equal|compar|value|content)",
                       r"(use|should be|instead of|replace\w* with|change\w* (it )?(back )?to|need|want).{0,20}==",
                       r"(interning|interned)"]}
    refs = [f"Yes. Line {line} compares strings with `is`, which tests object identity rather than equality. `mode is \"{mode}\"` can be False even when the text matches; use `==`.",
            f"Yes, `mode is \"{mode}\"` is a bug. `is` checks whether both names point to the same object, and equal strings are not guaranteed to be the same object. Go back to ==.",
            f"**Yes**: switching `==` to `is` makes the {fn} check unreliable for strings (it depends on interning), so the fast path may never trigger. Equality should use `==`."]
    wrong = ["No, the change looks fine.", f"Yes. `{fn}` should return a string instead of an int."]
    return _bug_inst(diff, facts, refs, wrong)


def _bug_missing_await(rng: random.Random) -> Instance:
    noun = pick(rng, NOUNS)
    fn = f"count{noun.capitalize()}Items"
    loader = f"load{noun.capitalize()}"
    start = rng.randint(3, 60)
    diff, nums = _diff(f"{noun}s.js", start, [
        (" ", f"async function {fn}(id) {{"),
        ("-", f"  const {noun} = await {loader}(id);"),
        ("+", f"  const {noun} = {loader}(id, {{ fresh: true }});"),
        (" ", f"  return {noun}.items.length;"), (" ", "}")])
    line = nums[2]
    facts = {"location": [rf"\b{loader}\b", rf"\bline {line}\b", r"\bawait\b", rf"{noun}\.items"],
             "cause": [r"(missing|removed|dropped|forgot|no longer|without|lost|lacks?|never|not|isn.?t|doesn.?t).{0,30}\bawait", r"promise",
                       r"(async|asynchronous).{0,60}(not|never)", r"pending"]}
    refs = [f"Yes. The `await` on `{loader}` was dropped on line {line}, so `{noun}` is a Promise and `{noun}.items.length` is undefined. Keep `await {loader}(id, {{ fresh: true }})`.",
            f"Yes, the new line forgets to await the async call. `{loader}` returns a promise, and reading `.items` on a pending promise gives undefined, not the count.",
            f"**Yes**. Removing `await` is the problem: {fn} is async, but `{noun}` now holds an unresolved Promise rather than the loaded data."]
    wrong = ["No, adding the fresh option is a harmless improvement.", f"Yes. `{fn}` should not be declared async."]
    return _bug_inst(diff, facts, refs, wrong)


def _bug_strict_equality(rng: random.Random) -> Instance:
    key = pick(rng, FIELDS)
    fn = f"filterBy{key.capitalize()}"
    start = rng.randint(3, 50)
    diff, nums = _diff("filters.js", start, [
        (" ", f"// {key} is a number (1, 2, 3); input.value comes from a <select>, so it is a string"),
        (" ", f"function {fn}(rows, input) {{"),
        ("-", f"  return rows.filter(r => r.{key} == input.value);"),
        ("+", f"  return rows.filter(r => r.{key} === input.value);"),
        (" ", "}")])
    line = nums[3]
    facts = {"location": [r"={3}", rf"\bline {line}\b", rf"r\.{key}\s*===", r"strict(er)? (equality|comparison)", rf"\b{fn}\b"],
             "cause": [r"(string|str)\b.{0,60}(number|numeric|int)", r"(number|numeric|int)\b.{0,60}(string|str)\b",
                       r"(different|mismatched|differing) types?", r"no longer (match|coerc|convert)", r"never (equal|match)",
                       r"(strict|===).{0,60}(coerc|convert|type)", r"nothing (will )?match"]}
    refs = [f"Yes. Line {line} switches to `===`, but `r.{key}` is a number while `input.value` is a string, so strict equality is never true and the filter returns nothing. Convert with `Number(input.value)` or keep `==`.",
            f"Yes, `===` does not coerce types. Comparing the number in `r.{key}` with a string from the select always fails, so every row gets filtered out.",
            f"**Yes**: the stricter comparison breaks {fn}. A string like \"2\" is not strictly equal to the number 2, so nothing matches any more."]
    wrong = ["No, `===` is always safer than `==`, so this is an improvement.", f"Yes. `{fn}` should use `map` instead of `filter`."]
    return _bug_inst(diff, facts, refs, wrong)


def _clean_early_return(rng: random.Random) -> Instance:
    noun = pick(rng, ["user", "member", "account", "guest"])
    fn = f"display_name_of_{noun}"
    start = rng.randint(3, 50)
    diff, _ = _diff("people.py", start, [
        (" ", f"def {fn}({noun}):"),
        ("-", f"    if {noun} is not None:"), ("-", f"        if {noun}.active:"), ("-", f"            return {noun}.name"),
        ("-", "    return None"),
        ("+", f"    if {noun} is None or not {noun}.active:"), ("+", "        return None"), ("+", f"    return {noun}.name")])
    prompt = _bug_prompt(diff)
    refs = [f"No. It flattens the nested ifs into one guard clause. Both versions return None when `{noun}` is missing or inactive and the name otherwise.",
            "No bug: the condition is just rewritten with De Morgan's law, so the behaviour is identical.",
            f"No, this refactor is equivalent. `{noun}` None or not active returns None, every other case returns the name."]
    wrong = [f"Yes. It now returns None when the {noun} is active.", f"Yes, `{noun}.name` fails when `{noun}` is None."]
    return Instance(prompt, "text", "diff_bug", {"has_bug": False, "facts": {}, "forbid": []}, refs, wrong)


def _bug_stale_rename(rng: random.Random) -> Instance:
    old, new = rng.choice([("total", "subtotal"), ("acc", "running"), ("result", "amount"), ("s", "partial")])
    fn = f"{pick(rng, VERBS)}_{pick(rng, NOUNS)}_cost"
    start = rng.randint(2, 40)
    diff, nums = _diff("pricing.py", start, [
        (" ", f"def {fn}(prices, tax):"),
        ("-", f"    {old} = 0"), ("+", f"    {new} = 0"),
        (" ", "    for p in prices:"),
        ("-", f"        {old} += p"), ("+", f"        {new} += p"),
        (" ", f"    return {old} * (1 + tax)")])
    line = nums[6]
    facts = {"location": [rf"return {old}\b", rf"\bline {line}\b", rf"\b{old}\b"],
             "cause": [rf"(still|but|yet|again) (uses?|refers?|returns?|references?|reads?).{{0,40}}{old}",
                       r"(not|wasn.?t|never|missed|forgot|didn.?t|without).{0,30}(renam|updat|chang)",
                       r"(undefined|NameError|not defined|doesn.?t exist|no longer exist)",
                       r"(old|previous|original) (variable )?name"]}
    refs = [f"Yes. The rename was only half done: `{old}` became `{new}` on the init and `+=` lines, but the unchanged `return {old} * (1 + tax)` on line {line} still uses the old name, so it raises NameError.",
            f"Yes, `return {old}` was not updated. After the change there is no variable called `{old}` any more, only `{new}`, so the function crashes when it reaches the return.",
            f"**Yes**: the return statement still refers to `{old}`, which is undefined now that the variable is `{new}`. Update that line too."]
    wrong = ["No, it only renames a local variable.", f"Yes. The loop should iterate over `tax` instead of `prices`."]
    return _bug_inst(diff, facts, refs, wrong)


TEMPLATES = [
    Template("diff_what_timeout_logging", "diff_review", "L1", "text", _what_timeout, variant="what", keyfact_refs_min=3),
    Template("diff_what_dedupe_tags", "diff_review", "L1", "javascript", _what_dedupe_tags, variant="what", keyfact_refs_min=3),
    Template("diff_what_retry_backoff", "diff_review", "L2", "python", _what_retry_backoff, variant="what", keyfact_refs_min=3),
    Template("diff_bug_range_skips_first", "diff_review", "L2", "text", _bug_skips_first, variant="bug", keyfact_refs_min=3),
    Template("diff_bug_is_string", "diff_review", "L1", "python", _bug_is_string, variant="bug", keyfact_refs_min=3),
    Template("diff_bug_missing_await", "diff_review", "L2", "javascript", _bug_missing_await, variant="bug", keyfact_refs_min=3),
    Template("diff_bug_strict_equality", "diff_review", "L2", "javascript", _bug_strict_equality, variant="bug", keyfact_refs_min=3),
    Template("diff_clean_rename_local", "diff_review", "L1", "text", _clean_rename_var, variant="clean", keyfact_refs_min=3),
    Template("diff_clean_early_return", "diff_review", "L3", "python", _clean_early_return, variant="clean", keyfact_refs_min=3),
    Template("diff_bug_stale_rename", "diff_review", "L3", "python", _bug_stale_rename, variant="bug", keyfact_refs_min=3),
]
