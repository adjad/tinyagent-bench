"""`fix_bug` templates: a function of at most 30 lines and a failing test's
output; graded by the failing test plus hidden regression tests that catch a
"fix" which deletes the behaviour or special-cases the test's input.

`visible_tests` holds the failing test (it must pass after the fix); `tests`
holds the hidden regression tests. `keep_names` (Python) lists other
top-level definitions that the fix may not change.
"""
from __future__ import annotations

import json
import random

from edat.sidekick.gen.core import Instance, Template, fence, pick


def _average_precedence(rng: random.Random) -> Instance:
    fn = pick(rng, ["average", "mean", "avg", "mean_of"])
    mod = pick(rng, ["stats", "mathutils", "numbers_lib"])
    a, b = sorted(rng.sample(range(2, 30, 2), 2))
    exp = (a + b) / 2
    got = (a + b) / 2 - 1
    buggy = (f"def {fn}(nums):\n"
             f"    \"\"\"Return the arithmetic mean of a non-empty list.\"\"\"\n"
             f"    return sum(nums) / len(nums) - 1\n")
    fixed = (f"def {fn}(nums):\n"
             f"    \"\"\"Return the arithmetic mean of a non-empty list.\"\"\"\n"
             f"    return sum(nums) / len(nums)\n")
    output = (f"============================= FAILURES =============================\n"
              f"___________________________ test_{fn} ____________________________\n\n"
              f"    def test_{fn}():\n>       assert {fn}([{a}, {b}]) == {exp:g}\n"
              f"E       assert {got} == {exp:g}\nE        +  where {got} = {fn}([{a}, {b}])\n\n"
              f"test_{mod}.py:4: AssertionError\n=========== 1 failed in 0.01s ===========")
    prompt = (f"This test fails. Fix the function.\n\n```python\n{buggy}```\n\n```\n{output}\n```")
    lists = [[1, 2, 3], [10], [-4, 4], [2.5, 3.5], [7, 7, 7, 7], [1, 2], [100, 0, 50], [-3, -6, -9]]
    tests = [{"args": [l], "expect": sum(l) / len(l)} for l in lists]
    visible = [{"args": [[a, b]], "expect": exp}]
    check = {"lang": "python", "entry": fn, "module": mod, "tests": tests, "visible_tests": visible,
             "reference": fixed, "diff_inputs": []}
    refs = [fence(fixed, "python"),
            "The `- 1` is applied after the division. Remove it:\n\n" + fence(f"def {fn}(nums):\n    return sum(nums) / len(nums)\n", "python")]
    wrong = [fence(f"def {fn}(nums):\n    if nums == [{a}, {b}]:\n        return {exp!r}\n    return sum(nums) / len(nums) - 1\n", "python"),
             fence(f"def {fn}(nums):\n    return (sum(nums) - 1) / len(nums)\n", "python")]
    tool = {"files": {f"{mod}.py": buggy}, "target": f"{mod}.py"}
    return Instance(prompt, "python", "code", check, refs, wrong, given_names=[fn], tool=tool)


def _mutable_default(rng: random.Random) -> Instance:
    fn = pick(rng, ["add_tag", "append_label", "add_item", "push_name"])
    p = pick(rng, ["tags", "labels", "items", "names"])
    mod = pick(rng, ["tagging", "labels_lib", "collect"])
    x, y = rng.sample(["red", "blue", "urgent", "home", "draft", "beta", "ops"], 2)
    buggy = (f"def {fn}(value, {p}=[]):\n"
             f"    \"\"\"Append value to {p} and return the list.\"\"\"\n"
             f"    {p}.append(value)\n"
             f"    return {p}\n")
    fixed = (f"def {fn}(value, {p}=None):\n"
             f"    \"\"\"Append value to {p} and return the list.\"\"\"\n"
             f"    if {p} is None:\n        {p} = []\n"
             f"    {p}.append(value)\n"
             f"    return {p}\n")
    output = (f"    def test_{fn}_fresh_list():\n        {fn}(\"{x}\")\n>       assert {fn}(\"{y}\") == [\"{y}\"]\n"
              f"E       AssertionError: assert ['{x}', '{y}'] == ['{y}']\nE         At index 0 diff: '{x}' != '{y}'\n"
              f"E         Left contains one more item: '{y}'\n\ntest_{mod}.py:7: AssertionError\n"
              f"========= 1 failed, 2 passed in 0.02s =========")
    prompt = f"Calling this twice gives the wrong list. Fix it so the test passes.\n\n```python\n{buggy}```\n\n```\n{output}\n```"
    visible = [{"calls": [[x], [y]], "expect": [y]}]
    tests = [
        {"args": ["a"], "expect": ["a"]},
        {"calls": [["a"], ["b"], ["c"]], "expect": ["c"]},
        {"args": ["z", ["q"]], "expect": ["q", "z"]},
        {"script": f"l = ['k']\n{fn}('m', l)\nresult = l", "expect": ["k", "m"]},
        {"script": f"l = []\nr = {fn}('m', l)\nresult = r is l", "expect": True},
        {"calls": [["a", ["x"]], ["b"]], "expect": ["b"]},
    ]
    check = {"lang": "python", "entry": fn, "module": mod, "tests": tests, "visible_tests": visible,
             "reference": fixed, "diff_inputs": []}
    refs = [fence(fixed, "python"),
            "The default list is created once and shared between calls. Use `None`:\n\n" +
            fence(f"def {fn}(value, {p}=None):\n    {p} = [] if {p} is None else {p}\n    {p}.append(value)\n    return {p}\n", "python")]
    wrong = [fence(f"def {fn}(value, {p}=[]):\n    return [value]\n", "python"),
             fence(f"def {fn}(value, {p}=None):\n    {p} = list({p} or [])\n    {p}.append(value)\n    return {p}\n", "python")]
    tool = {"files": {f"{mod}.py": buggy}, "target": f"{mod}.py"}
    return Instance(prompt, "python", "code", check, refs, wrong, given_names=[fn], tool=tool)



# ------------------------------------------------------------------ helpers for the added templates

def _pytest_out(test_fn, mod, test_src, e_lines, lineno, summary="1 failed in 0.02s"):
    src = "\n".join("    " + l if not l.startswith(">") else l for l in test_src.splitlines())
    e = "\n".join("E       " + l for l in e_lines)
    return (f"============================= FAILURES =============================\n"
            f"{('_' * 20 + ' ' + test_fn + ' ' + '_' * 20)}\n\n{src}\n{e}\n\n"
            f"test_{mod}.py:{lineno}: AssertionError\n=========== {summary} ===========")


def _node_out(name, test_src, actual, expected, mod, line):
    return (f"✖ {name} (1.8ms)\n  AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal:\n"
            f"  + actual - expected\n\n  + {actual}\n  - {expected}\n\n"
            f"      at TestContext.<anonymous> ({mod}.test.js:{line}:10)\n\nℹ tests 1\nℹ pass 0\nℹ fail 1")


def _py_prompt(intro, buggy, output):
    return f"{intro}\n\n```python\n{buggy}```\n\n```\n{output}\n```"


def _js_prompt(intro, buggy, test, output):
    return f"{intro}\n\n```javascript\n{buggy}```\n\nThe failing test:\n\n```javascript\n{test}\n```\n\n```\n{output}\n```"


def _py_check(fn, mod, tests, visible, fixed):
    return {"lang": "python", "entry": fn, "module": mod, "tests": tests, "visible_tests": visible,
            "reference": fixed, "diff_inputs": []}


def _js_check(fn, mod, tests, visible, fixed):
    return {"lang": "javascript", "entry": fn, "module": mod, "tests": tests, "visible_tests": visible,
            "reference": fixed, "diff_inputs": []}


def _py_tool(mod, buggy):
    return {"files": {f"{mod}.py": buggy}, "target": f"{mod}.py"}


def _js_tool_for(fn, mod, buggy):
    return {"files": {f"{mod}.js": buggy + f"\nmodule.exports = {{ {fn} }};\n"}, "target": f"{mod}.js"}


# ------------------------------------------------------------------ Python

def _py_range_end(rng: random.Random) -> Instance:
    fn = pick(rng, ["powers_upto", "squares_upto", "series_to", "table_upto"])
    mod = pick(rng, ["series", "tables", "calc_lib"])
    k = pick(rng, [2, 3])
    n = rng.randint(3, 7)
    expr = "i * i" if k == 2 else "i ** 3"
    buggy = (f"def {fn}(n):\n"
             f"    \"\"\"Return [1^{k}, 2^{k}, ..., n^{k}]; an empty list when n < 1.\"\"\"\n"
             f"    out = []\n    for i in range(1, n):\n        out.append({expr})\n    return out\n")
    fixed = buggy.replace("range(1, n)", "range(1, n + 1)")
    exp = [i ** k for i in range(1, n + 1)]
    got = exp[:-1]
    out = _pytest_out(f"test_{fn}", mod, f"def test_{fn}():\n>       assert {fn}({n}) == {exp}",
                      [f"assert {got} == {exp}", f"  Right contains one more item: {exp[-1]}",
                       f"  + where {got} = {fn}({n})"], 4)
    prompt = _py_prompt("This test fails. Fix the function.", buggy, out)
    tests = [{"args": [m], "expect": [i ** k for i in range(1, m + 1)]} for m in (1, 2, 5, 8, 10)] + \
            [{"args": [0], "expect": []}, {"args": [-3], "expect": []}]
    visible = [{"args": [n], "expect": exp}]
    refs = [fence(fixed, "python"),
            f"`range(1, n)` stops before `n`. Use `n + 1`:\n\n" + fence(f"def {fn}(n):\n    return [i ** {k} for i in range(1, n + 1)]\n", "python")]
    wrong = [fence(f"def {fn}(n):\n    if n == {n}:\n        return {exp!r}\n    return [{expr} for i in range(1, n)]\n", "python"),
             fence(f"def {fn}(n):\n    return [i ** {k} for i in range(1, n + 2)]\n", "python")]
    return Instance(prompt, "python", "code", _py_check(fn, mod, tests, visible, fixed), refs, wrong,
                    given_names=[fn], tool=_py_tool(mod, buggy))


def _py_case_tag(rng: random.Random) -> Instance:
    fn = pick(rng, ["has_tag", "is_tagged", "contains_tag", "has_label"])
    mod = pick(rng, ["tagcheck", "labels", "taglib"])
    pool = ["Red", "Blue", "Urgent", "Home", "Draft", "Beta", "Ops", "Legal"]
    tags = rng.sample(pool, 3)
    q = rng.choice(tags[:2])
    qs = q.lower()
    buggy = (f"def {fn}(tags, name):\n"
             f"    \"\"\"True if name is one of the tags, ignoring upper/lower case.\"\"\"\n"
             f"    return name in tags\n")
    fixed = (f"def {fn}(tags, name):\n"
             f"    \"\"\"True if name is one of the tags, ignoring upper/lower case.\"\"\"\n"
             f"    return name.lower() in [t.lower() for t in tags]\n")
    out = _pytest_out(f"test_{fn}", mod, f"def test_{fn}():\n>       assert {fn}({tags!r}, \"{qs}\")",
                      [f"assert False", f" +  where False = {fn}({tags!r}, '{qs}')"], 4)
    prompt = _py_prompt("The check is meant to ignore case, but this test fails. Fix it.", buggy, out)
    other = [t for t in pool if t not in tags][0]
    tests = [{"args": [tags, tags[2].upper()], "expect": True}, {"args": [tags, tags[0]], "expect": True},
             {"args": [tags, other.lower()], "expect": False}, {"args": [tags, other], "expect": False},
             {"args": [[], "red"], "expect": False}, {"args": [["red"], "RED"], "expect": True},
             {"args": [tags, tags[1][:-1].lower()], "expect": False}, {"args": [["Ab", "cD"], "CD"], "expect": True}]
    visible = [{"args": [tags, qs], "expect": True}]
    refs = [fence(fixed, "python"),
            "`in` compares exactly. Lower-case both sides:\n\n" + fence(f"def {fn}(tags, name):\n    return any(t.lower() == name.lower() for t in tags)\n", "python")]
    wrong = [fence(f"def {fn}(tags, name):\n    if name == \"{qs}\":\n        return True\n    return name in tags\n", "python"),
             fence(f"def {fn}(tags, name):\n    return name in tags or name.lower() in tags\n", "python")]
    return Instance(prompt, "python", "code", _py_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


def _py_mutates_input(rng: random.Random) -> Instance:
    fn = pick(rng, ["without_lowest", "drop_lowest", "remove_min", "skip_lowest"])
    mod = pick(rng, ["scoring", "gradebook", "listutil"])
    p = pick(rng, ["scores", "marks", "values"])
    vals = rng.sample(range(10, 99), 4)
    low = min(vals)
    res = [v for v in vals if v != low]
    buggy = (f"def {fn}({p}):\n"
             f"    \"\"\"Return the {p} without the lowest one (only one copy if it repeats).\n"
             f"    The list that was passed in must not change.\"\"\"\n"
             f"    lowest = min({p})\n    {p}.remove(lowest)\n    return {p}\n")
    fixed = (f"def {fn}({p}):\n"
             f"    \"\"\"Return the {p} without the lowest one (only one copy if it repeats).\n"
             f"    The list that was passed in must not change.\"\"\"\n"
             f"    result = list({p})\n    result.remove(min(result))\n    return result\n")
    out = _pytest_out(f"test_{fn}_keeps_input", mod,
                      f"def test_{fn}_keeps_input():\n    data = {vals}\n    assert {fn}(data) == {res}\n>       assert data == {vals}",
                      [f"assert {res} == {vals}", f"  At index {vals.index(low)} diff: {res[vals.index(low)] if vals.index(low) < len(res) else None} != {low}"
                       if vals.index(low) < len(res) else f"  Right contains one more item: {low}"], 6)
    prompt = _py_prompt("This test fails: the caller's list is changed. Fix the function.", buggy, out)
    visible = [{"script": f"data = {vals!r}\nr = {fn}(data)\nresult = [r, data]", "expect": [res, vals]}]
    tests = [{"args": [[5, 3, 9]], "expect": [5, 9]}, {"args": [[4, 4, 7]], "expect": [4, 7]},
             {"args": [[-2, 0, 6]], "expect": [0, 6]}, {"args": [[8]], "expect": []},
             {"script": "d = [3, 1, 2]\nfn_r = %s(d)\nresult = d" % fn, "expect": [3, 1, 2]},
             {"script": "d = [3, 1, 2]\nresult = %s(d) is d" % fn, "expect": False},
             {"script": "d = [2, 2, 2]\nr = %s(d)\nresult = [r, d]" % fn, "expect": [[2, 2], [2, 2, 2]]}]
    refs = [fence(fixed, "python"),
            "`remove` edits the list you were given. Work on a copy:\n\n" +
            fence(f"def {fn}({p}):\n    copy = {p}[:]\n    copy.remove(min(copy))\n    return copy\n", "python")]
    wrong = [fence(f"def {fn}({p}):\n    if {p} == {vals!r}:\n        return {res!r}\n    {p}.remove(min({p}))\n    return {p}\n", "python"),
             fence(f"def {fn}({p}):\n    lowest = min({p})\n    {p}.remove(lowest)\n    return list({p})\n", "python")]
    return Instance(prompt, "python", "code", _py_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


def _py_open_hours(rng: random.Random) -> Instance:
    fn = pick(rng, ["is_open", "in_service", "is_staffed", "within_hours"])
    mod = pick(rng, ["hours", "shop_clock", "schedule"])
    o, c = rng.randint(6, 10), rng.randint(16, 22)
    buggy = (f"def {fn}(hour, opens, closes):\n"
             f"    \"\"\"True from the opening hour up to, but not including, the closing hour.\"\"\"\n"
             f"    return opens < hour < closes\n")
    fixed = buggy.replace("opens < hour < closes", "opens <= hour < closes")
    out = _pytest_out(f"test_{fn}_at_opening", mod, f"def test_{fn}_at_opening():\n>       assert {fn}({o}, {o}, {c})",
                      ["assert False", f" +  where False = {fn}({o}, {o}, {c})"], 5)
    prompt = _py_prompt("The shop is not shown as open at the moment it opens. Fix the function.", buggy, out)
    o2, c2 = o + 1, c + 1
    tests = [{"args": [h, o2, c2], "expect": o2 <= h < c2} for h in (o2 - 1, o2, o2 + 1, (o2 + c2) // 2, c2 - 1, c2, c2 + 1, 0, 23)]
    tests += [{"args": [9, 9, 17], "expect": True}, {"args": [17, 9, 17], "expect": False}]
    visible = [{"args": [o, o, c], "expect": True}]
    refs = [fence(fixed, "python"),
            "The opening hour should count, so use `<=` on the left only:\n\n" +
            fence(f"def {fn}(hour, opens, closes):\n    return hour >= opens and hour < closes\n", "python")]
    wrong = [fence(f"def {fn}(hour, opens, closes):\n    if (hour, opens, closes) == ({o}, {o}, {c}):\n        return True\n    return opens < hour < closes\n", "python"),
             fence(f"def {fn}(hour, opens, closes):\n    return opens < hour <= closes\n", "python")]
    return Instance(prompt, "python", "code", _py_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


def _py_shadowed_loop(rng: random.Random) -> Instance:
    fn = pick(rng, ["count_word", "word_total", "occurrences", "times_said"])
    mod = pick(rng, ["wordstats", "textlib", "counter"])
    pool = ["cat", "hat", "sun", "map", "tea", "fox", "pen", "lamp", "door", "tree"]
    target = pick(rng, pool)
    filler = rng.sample([w for w in pool if w != target], 4)
    k = rng.randint(2, 3)
    words = [target] * k + [rng.choice(filler) for _ in range(rng.randint(3, 4))]
    rng.shuffle(words)
    shown = [w.capitalize() if i == words.index(target) and w == target else w for i, w in enumerate(words)]
    text = " ".join(shown)
    buggy = (f"def {fn}(text, word):\n"
             f"    \"\"\"How many times word appears in text, ignoring case.\"\"\"\n"
             f"    word = word.lower()\n    count = 0\n"
             f"    for word in text.lower().split():\n"
             f"        if word == word:\n            count += 1\n    return count\n")
    fixed = (f"def {fn}(text, word):\n"
             f"    \"\"\"How many times word appears in text, ignoring case.\"\"\"\n"
             f"    word = word.lower()\n    count = 0\n"
             f"    for w in text.lower().split():\n"
             f"        if w == word:\n            count += 1\n    return count\n")
    got = len(words)
    out = _pytest_out(f"test_{fn}", mod, f"def test_{fn}():\n>       assert {fn}(\"{text}\", \"{target}\") == {k}",
                      [f"assert {got} == {k}", f" +  where {got} = {fn}('{text}', '{target}')"], 4)
    prompt = _py_prompt("This test fails: the count is far too high. Fix the function.", buggy, out)
    tests = [{"args": ["the cat and the hat", "the"], "expect": 2}, {"args": ["The THE the", "the"], "expect": 3},
             {"args": ["one two three", "four"], "expect": 0}, {"args": ["", "a"], "expect": 0},
             {"args": ["go go go go", "GO"], "expect": 4}, {"args": ["tea tea cup", "cup"], "expect": 1},
             {"args": ["theme the", "the"], "expect": 1}]
    visible = [{"args": [text, target], "expect": k}]
    refs = [fence(fixed, "python"),
            "The loop variable `word` overwrites the parameter, so `word == word` is always true. Give the loop its own name:\n\n" +
            fence(f"def {fn}(text, word):\n    return [w.lower() for w in text.split()].count(word.lower())\n", "python")]
    wrong = [fence(f"def {fn}(text, word):\n    if text == \"{text}\":\n        return {k}\n    return len(text.split())\n", "python"),
             fence(f"def {fn}(text, word):\n    return text.split().count(word)\n", "python")]
    return Instance(prompt, "python", "code", _py_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


# ------------------------------------------------------------------ JavaScript

def _js_sort_numbers(rng: random.Random) -> Instance:
    fn = pick(rng, ["sortedScores", "orderedTotals", "sortedAges", "ascendingTimes"])
    mod = pick(rng, ["ranking", "sorting", "listtools"])
    while True:
        nums = rng.sample(range(1, 120), 5)
        if sorted(nums) != sorted(nums, key=str):
            break
    exp, got = sorted(nums), sorted(nums, key=str)
    buggy = (f"function {fn}(values) {{\n  // Return a new array, smallest number first.\n  return [...values].sort();\n}}\n")
    fixed = buggy.replace(".sort()", ".sort((a, b) => a - b)")
    test = (f"test('{fn}', () => {{\n  assert.deepStrictEqual({fn}({json.dumps(nums)}), {json.dumps(exp)});\n}});")
    out = _node_out(fn, test, json.dumps(got).replace(",", ", "), json.dumps(exp).replace(",", ", "), mod, 5)
    prompt = _js_prompt("This test fails. Fix the function.", buggy, test, out)
    tests = [{"args": [a], "expect": sorted(a)} for a in ([3, 20, 100, 1], [5, 4, 3, 2, 1], [-5, 10, -20, 3], [7], [], [11, 2, 2, 100], [1000, 99, 8])]
    tests.append({"script": f"const a = [3, 1, 2];\n{fn}(a);\nresult = a;", "expect": [3, 1, 2]})
    visible = [{"args": [nums], "expect": exp}]
    refs = [fence(fixed, "javascript"),
            "`sort()` without a comparator compares as strings. Pass a numeric comparator:\n\n" +
            fence(f"function {fn}(values) {{\n  return values.slice().sort((a, b) => a - b);\n}}", "javascript")]
    wrong = [fence(f"function {fn}(values) {{\n  if (values.length === 5 && values[0] === {nums[0]}) return {json.dumps(exp)};\n  return [...values].sort();\n}}", "javascript"),
             fence(f"function {fn}(values) {{\n  return [...values].sort().reverse();\n}}", "javascript")]
    return Instance(prompt, "javascript", "code", _js_check(fn, mod, tests, visible, fixed), refs, wrong,
                    given_names=[fn], tool=_js_tool_for(fn, mod, buggy))


def _js_last_n(rng: random.Random) -> Instance:
    fn = pick(rng, ["lastN", "tailItems", "recentOnes", "takeLast"])
    mod = pick(rng, ["slicing", "recent", "listtools"])
    arr = rng.sample(["a", "b", "c", "d", "e", "f", "g", "h"], rng.randint(5, 6))
    n = rng.randint(2, 3)
    buggy = f"function {fn}(items, n) {{\n  // The last n items, in their original order (1 <= n <= items.length).\n  return items.slice(items.length - n + 1);\n}}\n"
    fixed = buggy.replace("items.length - n + 1", "items.length - n")
    exp, got = arr[-n:], arr[-n + 1:]
    test = f"test('{fn}', () => {{\n  assert.deepStrictEqual({fn}({json.dumps(arr)}, {n}), {json.dumps(exp)});\n}});"
    out = _node_out(fn, test, json.dumps(got).replace(",", ", "), json.dumps(exp).replace(",", ", "), mod, 5)
    prompt = _js_prompt("This test fails. Fix the function.", buggy, test, out)
    tests = [{"args": [["x", "y", "z"], 1], "expect": ["z"]}, {"args": [[1, 2, 3, 4], 2], "expect": [3, 4]},
             {"args": [[1, 2, 3, 4], 4], "expect": [1, 2, 3, 4]}, {"args": [["p"], 1], "expect": ["p"]},
             {"args": [[5, 6, 7, 8, 9], 3], "expect": [7, 8, 9]}, {"args": [[True, False, True], 2], "expect": [False, True]}]
    visible = [{"args": [arr, n], "expect": exp}]
    refs = [fence(fixed, "javascript"),
            "`slice` takes the start index, so the `+ 1` drops one item too many. Use `slice(-n)`:\n\n" +
            fence(f"function {fn}(items, n) {{\n  return items.slice(-n);\n}}", "javascript")]
    wrong = [fence(f"function {fn}(items, n) {{\n  if (items.length === {len(arr)}) return {json.dumps(exp)};\n  return items.slice(items.length - n + 1);\n}}", "javascript"),
             fence(f"function {fn}(items, n) {{\n  return items.slice(0, n);\n}}", "javascript")]
    return Instance(prompt, "javascript", "code", _js_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


def _js_loose_equals(rng: random.Random) -> Instance:
    fn = pick(rng, ["countMatches", "tallyExact", "howMany", "countEqual"])
    mod = pick(rng, ["tally", "matching", "countlib"])
    t = rng.randint(2, 9)
    k, m = rng.randint(1, 2), rng.randint(1, 2)
    vals = [t] * k + [str(t)] * m + [t + 1, str(t + 2)]
    rng.shuffle(vals)
    buggy = (f"function {fn}(values, target) {{\n  // Count the values that are exactly the target (same type and value).\n"
             f"  return values.filter((v) => v == target).length;\n}}\n")
    fixed = buggy.replace("v == target", "v === target")
    test = f"test('{fn}', () => {{\n  assert.strictEqual({fn}({json.dumps(vals)}, {t}), {k});\n}});"
    out = (f"✖ {fn} (1.2ms)\n  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:\n\n  {k + m} !== {k}\n\n"
           f"      at TestContext.<anonymous> ({mod}.test.js:5:10)\n\nℹ tests 1\nℹ pass 0\nℹ fail 1")
    prompt = _js_prompt("This test fails. Fix the function.", buggy, test, out)
    tests = [{"args": [[1, "1", 1, True], 1], "expect": 2}, {"args": [["0", 0, ""], 0], "expect": 1},
             {"args": [["a", "b", "a"], "a"], "expect": 2}, {"args": [[], 3], "expect": 0},
             {"args": [[5, "5", "5"], "5"], "expect": 2}, {"args": [[None, 0, False], 0], "expect": 1},
             {"args": [[7, 8, 9], 4], "expect": 0}]
    visible = [{"args": [vals, t], "expect": k}]
    refs = [fence(fixed, "javascript"),
            "`==` converts types (`\"5\" == 5` is true). Compare with `===`:\n\n" +
            fence(f"function {fn}(values, target) {{\n  let n = 0;\n  for (const v of values) if (v === target) n++;\n  return n;\n}}", "javascript")]
    wrong = [fence(f"function {fn}(values, target) {{\n  if (target === {t}) return {k};\n  return values.filter((v) => v == target).length;\n}}", "javascript"),
             fence(f"function {fn}(values, target) {{\n  return values.filter((v) => String(v) === String(target)).length;\n}}", "javascript")]
    return Instance(prompt, "javascript", "code", _js_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


def _js_missing_await(rng: random.Random) -> Instance:
    fn = pick(rng, ["totalOwed", "sumBalances", "addUpFees", "grandTotal"])
    mod = pick(rng, ["ledger", "accounts", "billing"])
    ids = rng.sample(["a1", "b2", "c3", "d4", "e5"], 3)
    amts = [rng.randint(5, 60) for _ in ids]
    table = dict(zip(ids, amts))
    buggy = (f"async function {fn}(ids, getAmount) {{\n  // getAmount(id) returns a Promise of a number.\n  let total = 0;\n"
             f"  for (const id of ids) {{\n    total += getAmount(id);\n  }}\n  return total;\n}}\n")
    fixed = buggy.replace("total += getAmount(id)", "total += await getAmount(id)")
    table_js = json.dumps(table)
    test = (f"test('{fn}', async () => {{\n  const amounts = {table_js};\n"
            f"  const total = await {fn}({json.dumps(ids)}, async (id) => amounts[id]);\n  assert.strictEqual(total, {sum(amts)});\n}});")
    out = (f"✖ {fn} (2.1ms)\n  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:\n\n"
           f"  '0[object Promise][object Promise][object Promise]' !== {sum(amts)}\n\n"
           f"      at TestContext.<anonymous> ({mod}.test.js:6:10)\n\nℹ tests 1\nℹ pass 0\nℹ fail 1")
    prompt = _js_prompt("This test fails. Fix the function.", buggy, test, out)
    vis_script = f"const amounts = {table_js};\nresult = await {fn}({json.dumps(ids)}, async (id) => amounts[id]);"
    tests = [{"script": "result = await %s([], async () => 5);" % fn, "expect": 0},
             {"script": "result = await %s(['x', 'y'], async (id) => ({x: 3, y: 4})[id]);" % fn, "expect": 7},
             {"script": "result = await %s(['x'], (id) => 9);" % fn, "expect": 9},
             {"script": "result = await %s(['q', 'q', 'q'], async () => 2);" % fn, "expect": 6},
             {"script": "const calls = [];\nawait %s(['a', 'b'], async (id) => { calls.push(id); return 1; });\nresult = calls;" % fn, "expect": ["a", "b"]},
             {"script": "result = await %s(['m', 'n'], async (id) => (await Promise.resolve(id === 'm' ? 10 : 20)));" % fn, "expect": 30}]
    visible = [{"script": vis_script, "expect": sum(amts)}]
    refs = [fence(fixed, "javascript"),
            "`getAmount` returns a Promise, so each call has to be awaited before it is added:\n\n" +
            fence(f"async function {fn}(ids, getAmount) {{\n  const amounts = await Promise.all(ids.map((id) => getAmount(id)));\n  return amounts.reduce((a, b) => a + b, 0);\n}}", "javascript")]
    wrong = [fence(f"async function {fn}(ids, getAmount) {{\n  if (ids.length === 3 && ids[0] === \"{ids[0]}\") return {sum(amts)};\n  let total = 0;\n  for (const id of ids) total += getAmount(id);\n  return total;\n}}", "javascript"),
             fence(f"async function {fn}(ids, getAmount) {{\n  let total = 0;\n  ids.forEach(async (id) => {{\n    total += await getAmount(id);\n  }});\n  return total;\n}}", "javascript")]
    return Instance(prompt, "javascript", "code", _js_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


def _js_lost_this(rng: random.Random) -> Instance:
    fn = pick(rng, ["withTax", "addVat", "priceWithFee", "applyMarkup"])
    mod = pick(rng, ["pricing", "checkout", "shoplib"])
    rate = pick(rng, [0.25, 0.5, 0.75])
    prices = [4 * rng.randint(5, 40) for _ in range(3)]
    exp = [p * (1 + rate) for p in prices]
    buggy = (f"function {fn}(prices, rate) {{\n  const calc = {{\n    rate,\n    apply(price) {{\n      return price * (1 + this.rate);\n    }},\n  }};\n"
             f"  return prices.map(calc.apply);\n}}\n")
    fixed = buggy.replace("prices.map(calc.apply)", "prices.map((p) => calc.apply(p))")
    test = f"test('{fn}', () => {{\n  assert.deepStrictEqual({fn}({json.dumps(prices)}, {rate}), {json.dumps(exp)});\n}});"
    out = _node_out(fn, test, "[ NaN, NaN, NaN ]", "[ " + ", ".join(f"{e:g}" for e in exp) + " ]", mod, 5)
    prompt = _js_prompt("This test fails: every price comes back as `NaN`. Fix the function.", buggy, test, out)
    tests = [{"args": [[100, 40], 0.5], "expect": [150, 60]}, {"args": [[8], 0.25], "expect": [10]},
             {"args": [[], 0.5], "expect": []}, {"args": [[20, 20, 20, 20], 0], "expect": [20, 20, 20, 20]},
             {"args": [[12, 4], 0.75], "expect": [21, 7]}, {"args": [[40], 1], "expect": [80]}]
    visible = [{"args": [prices, rate], "expect": exp}]
    refs = [fence(fixed, "javascript"),
            "Passing `calc.apply` to `map` detaches it from `calc`, so `this` is no longer the object. Bind it or call it through the object:\n\n" +
            fence(f"function {fn}(prices, rate) {{\n  const calc = {{\n    rate,\n    apply(price) {{\n      return price * (1 + this.rate);\n    }},\n  }};\n  return prices.map(calc.apply, calc);\n}}", "javascript")]
    wrong = [fence(f"function {fn}(prices, rate) {{\n  if (rate === {rate}) return {json.dumps(exp)};\n  return prices.map((p) => p * (1 + this.rate));\n}}", "javascript"),
             fence(f"function {fn}(prices, rate) {{\n  const calc = {{\n    rate,\n    apply(price) {{\n      return price * (1 + this.rate);\n    }},\n  }};\n  return prices.map(calc.apply());\n}}", "javascript")]
    return Instance(prompt, "javascript", "code", _js_check(fn, mod, tests, visible, fixed), refs, wrong, given_names=[fn])


TEMPLATES = [
    Template("fix_py_precedence", "fix_bug", "L1", "python", _average_precedence, tool_mode=True),
    Template("fix_py_mutable_default", "fix_bug", "L3", "python", _mutable_default, tool_mode=True),
    Template("fix_py_range_end", "fix_bug", "L1", "python", _py_range_end, tool_mode=True),
    Template("fix_js_sort_numbers", "fix_bug", "L1", "javascript", _js_sort_numbers, tool_mode=True),
    Template("fix_py_case_tag", "fix_bug", "L1", "python", _py_case_tag),
    Template("fix_js_last_n", "fix_bug", "L1", "javascript", _js_last_n),
    Template("fix_py_mutates_input", "fix_bug", "L2", "python", _py_mutates_input),
    Template("fix_py_open_hours", "fix_bug", "L2", "python", _py_open_hours),
    Template("fix_py_shadowed_loop", "fix_bug", "L2", "python", _py_shadowed_loop),
    Template("fix_js_loose_equals", "fix_bug", "L2", "javascript", _js_loose_equals),
    Template("fix_js_missing_await", "fix_bug", "L2", "javascript", _js_missing_await),
    Template("fix_js_lost_this", "fix_bug", "L3", "javascript", _js_lost_this),
]
