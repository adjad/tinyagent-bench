"""`explain_error` templates: a stack trace plus up to 30 lines of code; graded by key facts.

Each build returns 3+ correct reference answers in different wording and 2+
wrong ones; the key-fact patterns must accept every correct one and reject
every wrong one (checked by tests/test_edat_sidekick_cases.py).
"""
from __future__ import annotations

import random

from edat.sidekick.gen.core import FIELDS, NOUNS, VERBS, Instance, Template, pick


def _py_keyerror(rng: random.Random) -> Instance:
    noun = pick(rng, NOUNS)
    fn = f"load_{noun}"
    keys = rng.sample(FIELDS, 3)
    missing = keys[1]
    start = rng.randint(8, 40)
    script = f"import_{noun}s.py"
    lines = [f"def {fn}(row):"] + [f"    {k} = row[\"{k}\"]" for k in keys] + \
            ["    return {" + ", ".join(f"\"{k}\": {k}" for k in keys) + "}"]
    numbered = "\n".join(f"{start + i:>3}  {l}" for i, l in enumerate(lines))
    bad_line = start + 2
    call_line = start + len(lines) + rng.randint(5, 20)
    trace = (f"Traceback (most recent call last):\n"
             f"  File \"{script}\", line {call_line}, in <module>\n"
             f"    {noun}s = [{fn}(r) for r in rows]\n"
             f"  File \"{script}\", line {bad_line}, in {fn}\n"
             f"    {missing} = row[\"{missing}\"]\n"
             f"KeyError: '{missing}'")
    prompt = (f"I get this when I run my script. What's wrong?\n\n```python\n{numbered}\n```\n\n```\n{trace}\n```")
    m = missing
    facts = {
        "where": [rf"\bline {bad_line}\b", rf"row\[\s*[\"']?{m}[\"']?\s*\]", rf"\b{fn}\b"],
        "cause": [rf"(no|missing|without|lacks?|doesn.?t (have|contain)|absent|not (have|contain)).{{0,60}}{m}",
                  rf"{m}.{{0,60}}(missing|not (there|present|found|in)|absent|doesn.?t exist)"],
        "fix": [r"\.get\(", rf"[\"']{m}[\"'] in row", r"\bif\b.{0,40}\bin\b.{0,10}row", r"try\s*:.{0,120}except\s+KeyError",
                r"setdefault", r"default value"],
    }
    forbid = [r"\bNone\b.{0,40}(cause|because|problem|is the issue)", r"\btypo\b.{0,30}(function|variable) name"]
    refs = [
        f"Line {bad_line} in `{fn}` reads `row[\"{m}\"]`, but the row you passed has no `\"{m}\"` key, so Python raises "
        f"`KeyError`. Use `row.get(\"{m}\")` (or give a default) if the field can be missing.",
        f"The dictionary `row` doesn't contain the key '{m}'. The crash is in {fn}, at `{m} = row[\"{m}\"]`. "
        f"Check first with `if \"{m}\" in row:` or skip rows without it.",
        f"KeyError: '{m}' means that key is missing from the dict. One of your rows lacks {m}. Wrap the lookup:\n\n"
        f"```python\ntry:\n    {m} = row[\"{m}\"]\nexcept KeyError:\n    {m} = None\n```\n\nThat is line {bad_line}.",
    ]
    wrong = [
        f"The value of `{m}` is None, which is the cause of the problem. Convert it to a string first.",
        f"You have a typo in the function name `{fn}`; rename it and run again.",
    ]
    return Instance(prompt=prompt, answer_kind="text", grader="keyfacts",
                    check={"facts": facts, "forbid": forbid}, references=refs, wrong=wrong,
                    given_names=[fn, m])


def _js_undefined_length(rng: random.Random) -> Instance:
    noun = pick(rng, NOUNS)
    fn = f"total{noun.capitalize()}"
    arr = pick(rng, ["items", "lines", "entries", "parts"])
    start = rng.randint(3, 30)
    lines = [f"function {fn}({noun}) {{",
             f"  let sum = 0;",
             f"  for (let i = 0; i < {noun}.{arr}.length; i++) {{",
             f"    sum += {noun}.{arr}[i].price;",
             f"  }}",
             f"  return sum;",
             f"}}",
             f"",
             f"console.log({fn}());"]
    numbered = "\n".join(f"{start + i:>3}  {l}" for i, l in enumerate(lines))
    bad_line = start + 2
    file = f"{noun}s.js"
    trace = (f"{file}:{bad_line}\n  for (let i = 0; i < {noun}.{arr}.length; i++) {{\n"
             f"                          ^\n\nTypeError: Cannot read properties of undefined (reading '{arr}')\n"
             f"    at {fn} ({file}:{bad_line}:28)\n    at Object.<anonymous> ({file}:{start + 8}:13)")
    prompt = f"Why does this crash?\n\n```js\n{numbered}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad_line}\b", rf"{noun}\.{arr}", rf"\b{fn}\(\)"],
        "cause": [rf"{noun}\b.{{0,40}}(is |was |are )?undefined", rf"(no|without (an?|any)?|didn.?t pass|not pass|never pass|nothing).{{0,30}}(argument|parameter|{noun})",
                  rf"called (with no|without).{{0,20}}argument"],
        "fix": [rf"{fn}\(\s*[a-z{{\[]", r"default (value|parameter)", rf"{noun}\s*=\s*\{{", r"pass (an?|the)", r"\?\.", r"if\s*\(\s*!"],
    }
    forbid = [rf"{arr}\b.{{0,30}}(is )?(an? )?empty array", r"\bnull\b.{0,20}price"]
    refs = [
        f"On line {bad_line}, `{noun}` is `undefined` because you call `{fn}()` with no argument, so reading "
        f"`{noun}.{arr}` throws. Pass an object, e.g. `{fn}({{ {arr}: [] }})`, or give the parameter a default.",
        f"You never pass the {noun} into {fn}(), so the parameter is undefined and `{noun}.{arr}.length` fails. "
        f"Fix: call it with the data, or write `function {fn}({noun} = {{ {arr}: [] }})`.",
        f"`{fn}()` is called without an argument. Inside it `{noun}` is undefined, and `{noun}.{arr}` "
        f"is where it breaks. Guard with `if (!{noun}) return 0;` or pass the {noun}.",
    ]
    wrong = [f"`{arr}` is an empty array, so `.length` is 0 and the loop crashes.",
             f"The `price` field is null; check each element's price before adding it."]
    return Instance(prompt=prompt, answer_kind="text", grader="keyfacts",
                    check={"facts": facts, "forbid": forbid}, references=refs, wrong=wrong,
                    given_names=[fn, noun, arr])



def _num(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i:>3}  {l}" for i, l in enumerate(lines))


def _mk(prompt: str, facts: dict, forbid: list, refs: list, wrong: list, given: list) -> Instance:
    return Instance(prompt=prompt, answer_kind="text", grader="keyfacts",
                    check={"facts": facts, "forbid": forbid}, references=refs, wrong=wrong, given_names=given)


def _py_index_end(rng: random.Random) -> Instance:
    v = pick(rng, ["scores", "readings", "prices", "temps", "weights"])
    fn = f"gaps_in_{v}"
    start = rng.randint(2, 30)
    nums = ", ".join(str(rng.randint(1, 90)) for _ in range(rng.randint(3, 5)))
    lines = [f"def {fn}({v}):", "    gaps = []", f"    for i in range(len({v})):",
             f"        gaps.append({v}[i + 1] - {v}[i])", "    return gaps", "", "",
             f"print({fn}([{nums}]))"]
    bad = start + 3
    call = start + 7
    f = f"{v}_gaps.py"
    trace = (f"Traceback (most recent call last):\n  File \"{f}\", line {call}, in <module>\n"
             f"    print({fn}([{nums}]))\n  File \"{f}\", line {bad}, in {fn}\n"
             f"    gaps.append({v}[i + 1] - {v}[i])\nIndexError: list index out of range")
    prompt = f"My script crashes. Why?\n\n```python\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", rf"{v}\[\s*i\s*\+\s*1\s*\]", rf"\b{fn}\b"],
        "cause": [r"(last|final).{0,60}(iteration|index|element|item|pass|loop)",
                  r"(past|beyond|off|outside|out of).{0,30}(end|bounds|range|the list)",
                  r"i\s*\+\s*1.{0,60}(len|length|end|exist|too (big|large|far))"],
        "fix": [r"range\(\s*len\(\s*\w+\s*\)\s*-\s*1", r"\bzip\(", r"len\(\s*\w+\s*\)\s*-\s*1",
                r"(stop|end|loop).{0,30}(one|1) (earlier|sooner|short|less|fewer)"],
    }
    forbid = [rf"\b{v}\b (is|was) empty", r"\btypo\b"]
    refs = [f"On line {bad}, `{v}[i + 1]` goes one past the end. When `i` is the last index, `i + 1` equals `len({v})`, "
            f"so there is no such item. Loop with `range(len({v}) - 1)` instead.",
            f"The loop runs `i` up to the last index, and on that last iteration `{v}[i + 1]` is outside the list, hence IndexError "
            f"in {fn}. Stop one element early, or use `zip({v}, {v}[1:])`.",
            f"IndexError means you read an index that doesn't exist. `{v}[i + 1]` fails on the final pass because there is no next element. "
            f"Change the range to `len({v}) - 1`."]
    wrong = [f"`{v}` is empty, so there is no first element to read. Check `if not {v}` before the loop.",
             f"You have a typo in `{fn}`; the name should be spelled differently where you call it."]
    return _mk(prompt, facts, forbid, refs, wrong, [fn, v])


def _py_str_plus_int(rng: random.Random) -> Instance:
    k = pick(rng, ["qty", "age", "size", "level", "rank", "total"])
    q = {"qty": "How many? ", "age": "Age: ", "size": "Size: ", "level": "Level: ", "rank": "Rank: ", "total": "Total so far: "}[k]
    step = rng.randint(1, 9)
    start = rng.randint(1, 25)
    lines = ["name = input(\"Name: \")", f"{k} = input(\"{q}\")", "print(\"Hello\", name)",
             f"after = {k} + {step}", "print(\"Next:\", after)"]
    bad = start + 3
    f = f"{k}_check.py"
    trace = (f"Traceback (most recent call last):\n  File \"{f}\", line {bad}, in <module>\n"
             f"    after = {k} + {step}\nTypeError: can only concatenate str (not \"int\") to str")
    prompt = f"I type a number but this fails:\n\n```python\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", rf"{k}\s*\+\s*{step}", r"\bafter\s*=", r"\binput\("],
        "cause": [r"input\(\).{0,60}(returns?|gives?|is|always).{0,20}(a )?(string|str)",
                  rf"{k}.{{0,40}}(is|are|a) (a )?(string|str)\b", r"(add|adding|mix\w*|combin\w*|concatenat\w*).{0,30}(string|str).{0,30}(int|number)",
                  r"(string|str).{0,30}(and|with|to).{0,10}(an? )?(int|integer|number)"],
        "fix": [r"\bint\(", r"\bfloat\(", r"convert.{0,30}(int|integer|number)"],
    }
    forbid = [rf"\b{k}\b.{{0,20}}(is|was) (None|undefined|empty)", r"\bimport\b.{0,30}(missing|needed|forgot)"]
    refs = [f"`input()` always returns a string, so `{k}` is a str and `{k} + {step}` tries to add a string and an int (line {bad}). "
            f"Convert first: `{k} = int(input(\"{q}\"))`.",
            f"Python won't concatenate a str with an int. `{k}` holds the text you typed, not a number. Wrap it: `int({k}) + {step}` on line {bad}.",
            f"TypeError here means mixing types: {k} is a string from input(), {step} is an integer. Use `int()` to convert it before adding."]
    wrong = [f"`{k}` is None because the user typed nothing. Add a default value.",
             "You forgot to import the math module, which provides the + operator for numbers."]
    return _mk(prompt, facts, forbid, refs, wrong, [k])


def _py_zero_div(rng: random.Random) -> Instance:
    v = pick(rng, ["marks", "times", "prices", "ratings", "heights"])
    thr = rng.choice([50, 80, 100, 120])
    start = rng.randint(2, 28)
    lines = ["def average(xs):", "    return sum(xs) / len(xs)", "", "",
             f"{v} = [4, 9, 12, 7, 3]", f"big = [x for x in {v} if x > {thr}]", "print(average(big))"]
    bad = start + 1
    call = start + 6
    f = f"{v}_stats.py"
    trace = (f"Traceback (most recent call last):\n  File \"{f}\", line {call}, in <module>\n    print(average(big))\n"
             f"  File \"{f}\", line {bad}, in average\n    return sum(xs) / len(xs)\nZeroDivisionError: division by zero")
    prompt = f"This worked on other data but now crashes:\n\n```python\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", r"sum\(xs\)\s*/\s*len\(xs\)", r"\baverage\b"],
        "cause": [r"(empty|no (items|elements|values|numbers))", r"len\(xs\).{0,30}(is |equals? |was )?(0|zero)",
                  r"(nothing|none of.{0,20}|no value).{0,40}(passes?|matches?|filter|greater)"],
        "fix": [r"if not xs", r"if len\(xs\)\s*(==|>|!=)", r"\bif\b.{0,30}\bxs\b", r"(guard|check|handle).{0,40}(empty|zero|len)",
                r"(return|default).{0,30}(0|none)", r"try\s*:.{0,100}except\s+ZeroDivisionError", r"max\(\s*len"],
    }
    forbid = [r"(float|decimal)s?\b.{0,30}(fix|needed|required|instead)", r"sum\(xs\).{0,30}(is|was) (zero|0)\b.{0,30}(cause|problem)"]
    refs = [f"`big` ends up empty (nothing in `{v}` is above {thr}), so `len(xs)` is 0 and line {bad} divides by zero. "
            f"Guard it: `if not xs: return 0`.",
            "average() is called with an empty list, which makes len(xs) zero. Dividing by zero raises ZeroDivisionError. "
            "Check for an empty list first and return None or 0.",
            f"The filter removes every element, so xs has no items and `sum(xs) / len(xs)` becomes 0 / 0. "
            f"Handle that case before dividing, e.g. `return sum(xs) / len(xs) if xs else 0`."]
    wrong = ["Dividing integers always needs float(): write `float(sum(xs)) / len(xs)`.",
             f"`sum(xs)` is zero because the values in `{v}` are all small."]
    return _mk(prompt, facts, forbid, refs, wrong, ["average", v])


def _py_none_no_return(rng: random.Random) -> Instance:
    noun = pick(rng, NOUNS)
    fn = f"clean_{noun}_path"
    start = rng.randint(5, 40)
    lines = [f"def {fn}(path):", "    parts = path.strip().split(\"/\")", "    kept = [p for p in parts if p]",
             "    joined = \"/\".join(kept)", "", "",
             f"label = {fn}(\" /a//b/ \").upper()", "print(label)"]
    bad = start + 6
    f = f"{noun}_paths.py"
    trace = (f"Traceback (most recent call last):\n  File \"{f}\", line {bad}, in <module>\n"
             f"    label = {fn}(\" /a//b/ \").upper()\nAttributeError: 'NoneType' object has no attribute 'upper'")
    prompt = f"I call .upper() on the result and get an AttributeError. The function looks fine to me.\n\n```python\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", rf"\b{fn}\b", r"\.upper\(\)"],
        "cause": [r"(forgot|missing|no|without|never|doesn.?t|does not|didn.?t|lacks?).{0,40}\breturn\b",
                  r"returns? (nothing|none)", r"(implicitly|automatically).{0,20}none"],
        "fix": [r"(add|put|include|write|use|insert|end with).{0,30}\breturn\b", r"\breturn\s+(joined|\"/\"\.join)"],
    }
    forbid = [r"\bpath\b (is|was) (None|empty)", r"\.upper\(\)\s*(isn.?t|is not|doesn.?t|does not)\b"]
    refs = [f"`{fn}` never returns anything. It builds `joined` but has no `return`, so Python hands back None and "
            f"`None.upper()` fails on line {bad}. Add `return joined`.",
            f"The function ends without a return statement, which means the call evaluates to None. That's why 'NoneType' has no attribute 'upper'. "
            f"Put `return joined` at the end of {fn}.",
            f"{fn}(...) gives back None because you forgot to return the joined string. Fix it by adding `return \"/\".join(kept)`."]
    wrong = [f"The `path` argument is None, so check it at the top of {fn}.",
             "`.upper()` is not a string method in Python; use `.capitalize()` instead."]
    return _mk(prompt, facts, forbid, refs, wrong, [fn])


def _py_unbound_local(rng: random.Random) -> Instance:
    v = pick(rng, ["count", "total", "hits", "seen", "calls"])
    fn = f"tally_{pick(rng, NOUNS)}s"
    start = rng.randint(2, 30)
    lines = [f"{v} = 0", "", "", f"def {fn}(xs):", "    for x in xs:", "        if x > 0:",
             f"            {v} += 1", f"    return {v}", "", "", f"print({fn}([3, -1, 4]))"]
    bad = start + 6
    call = start + 10
    f = f"{fn}.py"
    trace = (f"Traceback (most recent call last):\n  File \"{f}\", line {call}, in <module>\n    print({fn}([3, -1, 4]))\n"
             f"  File \"{f}\", line {bad}, in {fn}\n    {v} += 1\n"
             f"UnboundLocalError: cannot access local variable '{v}' where it is not associated with a value")
    prompt = f"`{v}` is defined at the top, so why does Python say it has no value?\n\n```python\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", rf"{v}\s*\+=\s*1", rf"\b{fn}\b"],
        "cause": [r"(assign|\+=|augmented|increment).{0,80}(local|makes|treat|scope)",
                  r"local.{0,60}(variable|scope|name).{0,80}(assign|\+=|shadow)",
                  r"(global|module.level|outer).{0,60}(not|can.?t|isn.?t|doesn.?t).{0,40}(see|visible|access|used|reach|read)",
                  r"shadow"],
        "fix": [r"\bglobal\b", r"(initiali[sz]e|set|define|start|create).{0,30}(inside|within|in).{0,20}(the )?(function|local)",
                r"\bnonlocal\b", r"(pass|take|return).{0,40}(parameter|argument|value|instead)", rf"\b{v}\s*=\s*0\b.{{0,30}}(inside|within|in the function)"],
    }
    forbid = [rf"\b{v}\b.{{0,30}}never (defined|created|set) (anywhere|at all)", r"missing an? import"]
    refs = [f"Because `{v} += 1` on line {bad} assigns to `{v}` inside `{fn}`, Python treats `{v}` as a local variable for the whole function. "
            f"That local has no value yet when `+=` reads it. Either declare `global {v}` or start a local `{v} = 0` inside the function.",
            f"The module-level `{v}` is shadowed: any assignment (including `+=`) in a function makes the name local to that function. "
            f"So the read before the first assignment fails. Use `global {v}`, or better, initialise `{v}` inside {fn} and return it.",
            f"This is a scope rule. `{v}` is assigned in the function, so Python considers it local, and the global one is not used. "
            f"Put `{v} = 0` at the start of {fn} (the failing line is {bad}) instead of at module level."]
    wrong = [f"`{v}` is never defined anywhere in the file; define it at module level.",
             "`x > 0` raises when x is negative; compare with abs(x) instead."]
    return _mk(prompt, facts, forbid, refs, wrong, [fn, v])


def _py_recursion(rng: random.Random) -> Instance:
    fn = pick(rng, ["sum_to", "countdown_total", "add_up", "triangle"])
    start = rng.randint(2, 30)
    n = rng.randint(4, 12)
    lines = [f"def {fn}(n):", f"    return n + {fn}(n - 1)", "", "", f"print({fn}({n}))"]
    bad = start + 1
    call = start + 4
    f = f"{fn}.py"
    trace = (f"Traceback (most recent call last):\n  File \"{f}\", line {call}, in <module>\n    print({fn}({n}))\n"
             f"  File \"{f}\", line {bad}, in {fn}\n    return n + {fn}(n - 1)\n"
             f"  File \"{f}\", line {bad}, in {fn}\n    return n + {fn}(n - 1)\n"
             f"  File \"{f}\", line {bad}, in {fn}\n    return n + {fn}(n - 1)\n  [Previous line repeated 996 more times]\n"
             f"RecursionError: maximum recursion depth exceeded")
    prompt = f"I just want the sum of 1..n. What does this error mean?\n\n```python\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", rf"{fn}\(n - 1\)", rf"\b{fn}\b"],
        "cause": [r"(no|missing|without|lacks?|forgot).{0,30}(base case|stop|stopping|exit|terminat)",
                  r"(never|doesn.?t|does not|can.?t|won.?t) (stops?|ends?|terminates?|returns?|reach)",
                  r"(infinite|endless|unbounded).{0,20}recurs"],
        "fix": [r"base case", rf"\bif n\s*(<=|<|==|<=)\s*[01]\b", r"\bif\b.{0,25}return\b", r"(stop|end|exit).{0,40}(when|at|once).{0,20}(0|1|zero|one)"],
    }
    forbid = [r"(set|call|use|raise|increase)\s+(sys\.)?setrecursionlimit", r"(raise|increase) the (recursion )?limit (to|with|by)"]
    refs = [f"`{fn}` calls itself forever: there is no base case, so `n` goes 5, 4, ... 0, -1, ... and the call stack overflows (line {bad}). "
            f"Add `if n <= 0: return 0` before the recursive call.",
            f"RecursionError means the recursion never stops. Your function has nothing that ends it, so it keeps calling {fn}(n - 1). "
            f"Give it a stopping condition, e.g. `if n == 0: return 0`.",
            f"Infinite recursion: the function has no exit condition. Without a base case the depth limit is hit. "
            f"The recursive call `{fn}(n - 1)` on line {bad} needs a way out: return early when n reaches 0."]
    wrong = ["Python's recursion limit is too low here. Call `sys.setrecursionlimit(100000)` and it works.",
             "Integers can't be used with recursion; pass a list and use a for loop."]
    return _mk(prompt, facts, forbid, refs, wrong, [fn])


def _js_not_a_function(rng: random.Random) -> Instance:
    noun = pick(rng, NOUNS)
    arr = f"{noun}s"
    start = rng.randint(2, 30)
    names = ["Ada", "Bo", "Cy", "Dee", "Eli", "Flo"]
    items = ", ".join("{ name: \"%s\" }" % n for n in names[:rng.randint(2, 4)])
    lines = [f"const {arr} = [{items}];", f"const names = {arr}.map(o => o.name);",
             "const total = names.length();", "console.log(`Found ${total} names`);"]
    bad = start + 2
    f = f"{arr}.js"
    trace = (f"{f}:{bad}\nconst total = names.length();\n                    ^\n\n"
             f"TypeError: names.length is not a function\n    at Object.<anonymous> ({f}:{bad}:21)")
    prompt = f"Node says something is not a function but `names` is an array.\n\n```js\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", r"names\.length\(\)", r"\.length\(", r"parenthes"],
        "cause": [r"length.{0,40}(is |a )?(a )?(property|number|not a method|value)", r"length isn.?t a function", r"(property|number).{0,40}(not|isn.?t|rather than).{0,20}(method|function)",
                  r"(can.?t|cannot|don.?t|shouldn.?t|not).{0,20}(call|invoke).{0,30}length"],
        "fix": [r"names\.length\b(?!\()", r"(remove|drop|delete|without|omit).{0,30}(parenthes|parens|brackets|\(\))", r"(use|write|just).{0,20}\.length\b"],
    }
    forbid = [r"\bnames\b.{0,20}(is|was) undefined", r"\.map\b.{0,30}(doesn.?t|does not|isn.?t|not) (exist|available|supported)"]
    refs = [f"`length` is a property that holds a number, not a method, so `names.length()` on line {bad} tries to call a number. Write `names.length`.",
            "Arrays have `.length` as a plain value. You added parentheses, which makes JavaScript try to call it as a function and throw TypeError. Remove the `()`.",
            f"On `names.length()`: length isn't a function. Just use `names.length` without parentheses and `total` will be the count."]
    wrong = [f"`names` is undefined because `.map` returned nothing; check `{arr}` is not empty.",
             "`.map` doesn't exist in Node. Use a for loop to collect the names."]
    return _mk(prompt, facts, forbid, refs, wrong, ["names", arr])


def _js_tdz(rng: random.Random) -> Instance:
    v = pick(rng, ["total", "average", "subtotal", "grand"])
    arr = pick(rng, ["prices", "scores", "weights", "fees"])
    start = rng.randint(2, 30)
    nums = ", ".join(str(rng.randint(2, 40)) for _ in range(4))
    lines = [f"const {arr} = [{nums}];", f"console.log(`Result: ${{{v}}}`);",
             f"const {v} = {arr}.reduce((a, b) => a + b, 0);", f"console.log(\"done\");"]
    bad = start + 1
    f = f"{arr}.js"
    trace = (f"{f}:{bad}\nconsole.log(`Result: ${{{v}}}`);\n                      ^\n\n"
             f"ReferenceError: Cannot access '{v}' before initialization\n    at Object.<anonymous> ({f}:{bad}:23)")
    prompt = f"I declared `{v}` in this file, so why is it an error?\n\n```js\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", rf"console\.log", rf"\blog\b", rf"\${{\s*{v}\s*}}"],
        "cause": [r"(before|prior to).{0,40}(declar|defin|initiali|assign)", r"temporal dead zone|\bTDZ\b",
                  r"(used|use|access\w*|read\w*|referenc\w*|log\w*).{0,40}(before|above|earlier than)"],
        "fix": [r"(move|put|place|declare|define|initiali[sz]e|swap|reorder).{0,50}(above|before|earlier|top|first|prior)",
                r"(after|below).{0,30}(declar|defin|reduce|const)"],
    }
    forbid = [rf"\b{v}\b.{{0,20}}(never|isn.?t|not) (declared|defined) (anywhere|at all)", r"\bconst\b.{0,30}(can.?t|cannot) be (changed|reassigned)"]
    refs = [f"`const {v}` is only initialised on line {bad + 1}, but line {bad} reads it first. Variables declared with const/let can't be used before their declaration runs "
            f"(the temporal dead zone). Move the `console.log` below the declaration.",
            f"You use `{v}` before it is declared. The declaration is later in the file, so JavaScript throws ReferenceError at the log. "
            f"Put the `const {v} = ...` line above the log.",
            f"Hoisting doesn't help with const: the name exists but is not initialised until its line executes, so the log on line {bad} reads `{v}` before it is declared. Reorder so `{v}` is computed above where you print it."]
    wrong = [f"`{v}` is never declared anywhere in the file; add `let {v};` to fix it.",
             f"`const` variables can't be changed after assignment, so switch `{v}` to `let`."]
    return _mk(prompt, facts, forbid, refs, wrong, [v, arr])


def _js_promise_pending(rng: random.Random) -> Instance:
    noun = pick(rng, NOUNS)
    fn = f"load{noun.capitalize()}"
    start = rng.randint(2, 25)
    ident = rng.randint(2, 99)
    lines = [f"async function {fn}(id) {{", f"  const res = await fetch(`https://api.example.invalid/{noun}s/${{id}}`);",
             "  return res.json();", "}", "", f"const data = {fn}({ident});", "console.log(data);", "console.log(data.name);"]
    call = start + 5
    prompt = (f"I expected an object but I get this output and no error:\n\n```js\n{_num(lines, start)}\n```\n\n"
              f"```\nPromise {{ <pending> }}\nundefined\n```")
    facts = {
        "where": [rf"\bline {call}\b", rf"{fn}\(\s*{ident}\s*\)", rf"\bconst data\b", r"\bdata\b"],
        "cause": [r"(async|asynchronous).{0,60}(returns?|gives?|resolves?).{0,30}promise", r"returns? (a )?promise",
                  r"(not|never|forgot|without|didn.?t|hasn.?t).{0,30}(await|wait|resolv)", r"still pending|promise.{0,30}pending"],
        "fix": [rf"\bawait\s+{fn}", r"\bawait the call\b", r"\.then\(", r"top-level await", r"\basync\b.{0,40}\bawait\b.{0,40}\bcall"],
    }
    forbid = [r"(api|server|url|endpoint).{0,30}(is down|unreachable|returns? (nothing|empty))", r"res\.json\(\)\s*(returns|gives) undefined"]
    refs = [f"`{fn}` is async, so calling it returns a Promise right away, and `data` is that promise rather than the object. "
            f"Use `const data = await {fn}({ident});` (inside an async function or a module) or `.then(...)`.",
            "You forgot to await the call. The function runs asynchronously, and `console.log(data)` prints the pending Promise before the "
            "fetch finishes, so `data.name` is undefined. Await the call first.",
            f"Async functions always give back a promise. Until it resolves, `data.name` doesn't exist. Do `{fn}({ident}).then(d => console.log(d.name))`."]
    wrong = ["The API at example.invalid is down, so nothing comes back; try again later.",
             "`res.json()` returns undefined; use `res.text()` and parse it yourself."]
    return _mk(prompt, facts, forbid, refs, wrong, [fn, "data"])


def _js_this_detached(rng: random.Random) -> Instance:
    cls = pick(rng, ["Counter", "Tally", "Clicker", "Meter"])
    v = pick(rng, ["count", "hits", "clicks", "ticks"])
    inst = cls[0].lower() + cls[1:]
    start = rng.randint(2, 25)
    lines = [f"class {cls} {{", "  constructor() {", f"    this.{v} = 0;", "  }", "", "  bump() {",
             f"    this.{v} += 1;", f"    return this.{v};", "  }", "}", "",
             f"const {inst} = new {cls}();", f"const handler = {inst}.bump;", "handler();"]
    bad = start + 6
    call = start + 13
    f = f"{inst}.js"
    trace = (f"{f}:{bad}\n    this.{v} += 1;\n         ^\n\nTypeError: Cannot read properties of undefined (reading '{v}')\n"
             f"    at bump ({f}:{bad}:10)\n    at Object.<anonymous> ({f}:{call}:1)")
    prompt = f"`{v}` is set in the constructor, yet it blows up. Why?\n\n```js\n{_num(lines, start)}\n```\n\n```\n{trace}\n```"
    facts = {
        "where": [rf"\bline {bad}\b", rf"this\.{v}", r"\bbump\b", r"\bhandler\(\)"],
        "cause": [r"\bthis\b.{0,80}(undefined|lost|not (the|your)|detach|unbound|no longer|isn.?t)",
                  r"(detach|extract|pull|copy|assign|stor)\w*.{0,60}(method|function).{0,60}(object|instance|receiver|context|this)",
                  r"(plain|standalone|regular|bare) function"],
        "fix": [r"\.bind\(", r"=>", rf"\b{inst}\.bump\(\)", r"(call|invoke).{0,30}(on|through|via) the (object|instance)"],
    }
    forbid = [rf"\b{v}\b.{{0,30}}(was|is|were) (never|not) (initiali[sz]ed|set|defined)", r"static (property|field)"]
    refs = [f"Copying `{inst}.bump` into `handler` detaches the method from the object. Calling `handler()` as a plain function means `this` is undefined "
            f"(class bodies are strict), so `this.{v}` fails on line {bad}. Use `const handler = {inst}.bump.bind({inst});`.",
            f"The problem is `this`. When you call `handler()` there's no object before the dot, so inside `bump` `this` is undefined, not your {cls}. "
            f"Call `{inst}.bump()` directly or wrap it: `const handler = () => {inst}.bump();`.",
            f"`this` is lost when a method is passed around on its own. `bump` expects to be called on the instance. "
            f"Bind it (`.bind({inst})`) or define it as an arrow function property."]
    wrong = [f"`{v}` was never initialised in the constructor, so it is undefined; set it to 0.",
             f"Class fields can't be modified by methods; make `{v}` a static property."]
    return _mk(prompt, facts, forbid, refs, wrong, [cls, v, "bump", inst, "handler"])


TEMPLATES = [
    Template("explain_py_keyerror", "explain_error", "L1", "python", _py_keyerror, keyfact_refs_min=3, human_graded=True),
    Template("explain_js_undefined_prop", "explain_error", "L2", "javascript", _js_undefined_length, keyfact_refs_min=3, human_graded=True),
    Template("explain_py_index_end", "explain_error", "L1", "python", _py_index_end, keyfact_refs_min=3),
    Template("explain_py_str_plus_int", "explain_error", "L1", "python", _py_str_plus_int, keyfact_refs_min=3),
    Template("explain_py_zero_div", "explain_error", "L1", "python", _py_zero_div, keyfact_refs_min=3),
    Template("explain_py_none_no_return", "explain_error", "L2", "python", _py_none_no_return, keyfact_refs_min=3, human_graded=True),
    Template("explain_py_unbound_local", "explain_error", "L3", "python", _py_unbound_local, keyfact_refs_min=3, human_graded=True),
    Template("explain_py_recursion", "explain_error", "L2", "python", _py_recursion, keyfact_refs_min=3, human_graded=True),
    Template("explain_js_not_a_function", "explain_error", "L1", "javascript", _js_not_a_function, keyfact_refs_min=3),
    Template("explain_js_tdz", "explain_error", "L2", "javascript", _js_tdz, keyfact_refs_min=3, human_graded=True),
    Template("explain_js_promise_pending", "explain_error", "L2", "javascript", _js_promise_pending, keyfact_refs_min=3, human_graded=True),
    Template("explain_js_this_detached", "explain_error", "L3", "javascript", _js_this_detached, keyfact_refs_min=3, human_graded=True),
]
