"""`write_fn` templates: a description and a signature; graded by 8-15 hidden
tests plus 200 seeded differential inputs against a hidden reference.

Pattern for a template:
* pick names and constants from `rng`;
* write the reference source (hidden) in the case language;
* list hidden tests as data (`{"args", "expect"}` / `{"args", "raises"}`), computing
  expectations with a Python mirror of the reference;
* make 200 differential inputs from `rng`;
* references: the reference wrapped in a fence, as a model would answer;
* wrong: an obviously wrong but loadable function, plus an empty answer is checked
  automatically by the validator;
* tool-mode templates add `tool`: the file the model edits and the visible tests.
"""
from __future__ import annotations

import random

from edat.sidekick.gen.core import Instance, Template, fence, pick

FIRST = ["ada", "alan", "grace", "kathy", "edsger", "barbara", "donald", "frances", "ken", "radia", "émile", "zoë"]
LAST = ["lovelace", "turing", "hopper", "johnson", "dijkstra", "liskov", "knuth", "allen", "thompson", "perlman", "ørsted"]


def _initials_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["initials", "name_initials", "get_initials", "to_initials"])
    mod = pick(rng, ["names", "people", "text_utils"])
    ref = (f"def {fn}(full_name: str) -> str:\n"
           f"    return \"\".join(part[0].upper() for part in full_name.split())\n")

    def mirror(s: str) -> str:
        return "".join(p[0].upper() for p in s.split())

    def rand_name(r: random.Random) -> str:
        parts = [pick(r, FIRST)] + [pick(r, LAST) for _ in range(r.randint(0, 2))]
        sep = lambda: " " * r.randint(1, 3)
        return " " * r.randint(0, 2) + sep().join(parts) + " " * r.randint(0, 2)

    fixed = ["  ada  lovelace ", "Grace Hopper", "alan", "", "   ", "a b c", "zoë ørsted", "Edsger W Dijkstra",
             "x", "mary-jane watson"]
    tests = [{"args": [s], "expect": mirror(s)} for s in fixed]
    diff = [[rand_name(rng)] for _ in range(200)]
    prompt = (f"Write a Python function `{fn}(full_name: str) -> str` that returns the upper-case initials of a "
              f"name, ignoring extra spaces. For example `{fn}(\"  ada  lovelace \")` returns `\"AL\"` and "
              f"`{fn}(\"\")` returns `\"\"`.")
    visible = [{"args": ["  ada  lovelace "], "expect": "AL"}, {"args": ["Grace Hopper"], "expect": "GH"}]
    check = {"lang": "python", "entry": fn, "module": mod, "tests": tests, "visible_tests": visible,
             "reference": ref, "diff_inputs": diff}
    refs = [fence(ref, "python"),
            fence(f"def {fn}(full_name: str) -> str:\n    out = ''\n    for w in full_name.strip().split():\n"
                  f"        out += w[0].upper()\n    return out\n", "python")]
    wrong = [fence(f"def {fn}(full_name: str) -> str:\n    return full_name[:2].upper()\n", "python"),
             fence(f"def {fn}(full_name: str) -> str:\n    return ''.join(p[0] for p in full_name.split(' '))\n", "python")]
    tool = {"files": {f"{mod}.py": f"def {fn}(full_name: str) -> str:\n    raise NotImplementedError\n"},
            "target": f"{mod}.py"}
    return Instance(prompt, "python", "code", check, refs, wrong, given_names=[fn], tool=tool)


def _chunk_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["chunk", "chunkArray", "splitIntoChunks", "batches"])
    mod = pick(rng, ["arrays", "listUtils", "batching"])
    ref = (f"function {fn}(arr, size) {{\n"
           f"  if (size <= 0) throw new RangeError('size must be positive');\n"
           f"  const out = [];\n"
           f"  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));\n"
           f"  return out;\n}}\n")

    def mirror(a, n):
        return [a[i:i + n] for i in range(0, len(a), n)]

    cases = [([1, 2, 3, 4, 5], 2), ([], 3), ([1], 1), ([1, 2, 3], 5), ([1, 2, 3, 4], 2), (["a", "b", "c"], 1),
             ([0, 0, 0, 0, 0, 0], 3), ([1, 2, 3, 4, 5, 6, 7], 3)]
    tests = [{"args": [a, n], "expect": mirror(a, n)} for a, n in cases]
    tests += [{"args": [[1, 2], 0], "raises": "RangeError"}, {"args": [[1, 2], -1], "raises": "RangeError"}]
    diff = []
    for _ in range(200):
        n = rng.randint(1, 6)
        diff.append([[rng.randint(-50, 50) for _ in range(rng.randint(0, 15))], n])
    prompt = (f"Write a JavaScript function `{fn}(arr, size)` that splits an array into chunks of `size` "
              f"elements; the last chunk may be shorter. If `size <= 0` it throws a `RangeError`. "
              f"For example `{fn}([1, 2, 3, 4, 5], 2)` returns `[[1, 2], [3, 4], [5]]`.")
    visible = [{"args": [[1, 2, 3, 4, 5], 2], "expect": [[1, 2], [3, 4], [5]]}]
    check = {"lang": "javascript", "entry": fn, "module": mod, "tests": tests, "visible_tests": visible,
             "reference": ref, "diff_inputs": diff}
    refs = [fence(ref, "javascript"),
            fence(f"const {fn} = (arr, size) => {{\n  if (!(size > 0)) throw new RangeError('bad size');\n"
                  f"  return Array.from({{ length: Math.ceil(arr.length / size) }}, (_, i) => arr.slice(i * size, (i + 1) * size));\n}};\n", "js")]
    wrong = [fence(f"function {fn}(arr, size) {{\n  return [arr];\n}}\n", "js"),
             fence(f"function {fn}(arr, size) {{\n  if (size <= 0) return [];\n  const out = [];\n  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));\n  return out;\n}}\n", "js")]
    tool = {"files": {f"{mod}.js": f"function {fn}(arr, size) {{\n  throw new Error('not implemented');\n}}\n\nmodule.exports = {{ {fn} }};\n"},
            "target": f"{mod}.js"}
    return Instance(prompt, "javascript", "code", check, refs, wrong, given_names=[fn], tool=tool)



# ---------------------------------------------------------------- shared builder for the compact templates

def _mirror_of(ref: str, fn: str):
    ns: dict = {}
    exec(ref, ns)
    return ns[fn]


def _make(lang: str, fn: str, mod: str, ref: str, mirror, cases: list, diff: list, prompt: str, visible: list,
          alt_refs: list[str], wrong: list[str], raises: list | None = None, tool_mode: bool = False,
          stub: str = "") -> Instance:
    """Assemble an Instance. `mirror` computes expectations (deep-copied args); `visible` are args lists."""
    import copy
    tests = [{"args": a, "expect": mirror(*copy.deepcopy(a))} for a in cases]
    tests += [{"args": a, "raises": name} for a, name in (raises or [])]
    assert 8 <= len(tests) <= 15, (fn, len(tests))
    vis = [{"args": a, "expect": mirror(*copy.deepcopy(a))} for a in visible]
    check = {"lang": lang, "entry": fn, "module": mod, "tests": tests, "visible_tests": vis,
             "reference": ref, "diff_inputs": diff}
    tag = "python" if lang == "python" else "javascript"
    refs = [fence(ref, tag)] + [fence(r, tag if lang == "python" else "js") for r in alt_refs]
    wr = [fence(w, tag if lang == "python" else "js") for w in wrong]
    tool = None
    if tool_mode:
        ext = "py" if lang == "python" else "js"
        tool = {"files": {f"{mod}.{ext}": stub}, "target": f"{mod}.{ext}"}
    return Instance(prompt, lang, "code", check, refs, wr, given_names=[fn], tool=tool)


WORDS = ["apple", "Banana", "cherry", "date", "Elder", "fig", "grape", "honey", "Iris", "jam", "kiwi", "lime",
         "mango", "nut", "olive", "pear", "quince", "plum", "café", "Éclair", "naïve", "über"]
ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _rand_text(r: random.Random, alphabet: str, lo: int = 0, hi: int = 20) -> str:
    return "".join(pick(r, alphabet) for _ in range(r.randint(lo, hi)))


def _stub_py(fn: str, params: str) -> str:
    return f"def {fn}({params}):\n    raise NotImplementedError\n"


# ---------------------------------------------------------------- Python templates

def _count_vowels_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["count_vowels", "vowel_count", "num_vowels"])
    mod = pick(rng, ["textstats", "strings", "wordtools"])
    ref = f"def {fn}(text: str) -> int:\n    return sum(1 for ch in text.lower() if ch in \"aeiou\")\n"
    m = _mirror_of(ref, fn)
    cases = [[s] for s in ["", "hello", "AEIOU", "rhythm", "Queue", "café", "y", "a e i o u", "ÀÉÎ",
                           "Programming Language", "bcdfg", "xyz" * 3]]
    diff = [[_rand_text(rng, ALPHA + "   éøü.,", 0, 25)] for _ in range(200)]
    prompt = (f"Write a Python function `{fn}(text: str) -> int` that counts the vowels `a`, `e`, `i`, `o`, `u` "
              f"in a string, upper or lower case. Other letters (including accented ones) do not count. "
              f"For example `{fn}(\"Hello\")` returns `2` and `{fn}(\"rhythm\")` returns `0`.")
    alt = (f"def {fn}(text: str) -> int:\n    n = 0\n    for ch in text:\n        if ch in \"aeiouAEIOU\":\n"
           f"            n += 1\n    return n\n")
    wrong = [f"def {fn}(text: str) -> int:\n    return sum(1 for ch in text if ch in \"aeiou\")\n",
             f"def {fn}(text: str) -> int:\n    return len(text) // 3\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [["Hello"], ["rhythm"]], [alt], wrong)


def _clamp_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["clamp", "clamp_value", "limit_to_range"])
    mod = pick(rng, ["numrange", "mathutil", "ranges"])
    ref = (f"def {fn}(value, low, high):\n    if low > high:\n        raise ValueError(\"low must not exceed high\")\n"
           f"    return max(low, min(value, high))\n")
    m = _mirror_of(ref, fn)
    cases = [[5, 0, 10], [-3, 0, 10], [15, 0, 10], [0, 0, 0], [7, 7, 7], [-5, -10, -1], [-20, -10, -1], [3, 5, 5],
             [10, 0, 10], [0, 0, 10], [2.5, 0, 2]]
    raises = [([1, 5, 2], "ValueError")]
    diff = []
    for _ in range(200):
        lo = rng.randint(-30, 30)
        hi = lo + rng.randint(0, 40) if rng.random() < 0.92 else lo - rng.randint(1, 5)
        diff.append([rng.randint(-60, 60), lo, hi])
    prompt = (f"Write a Python function `{fn}(value, low, high)` that returns `value` limited to the range from "
              f"`low` to `high` inclusive. If `low > high` it raises `ValueError`. For example "
              f"`{fn}(15, 0, 10)` returns `10` and `{fn}(-3, 0, 10)` returns `0`.")
    alt = (f"def {fn}(value, low, high):\n    if low > high:\n        raise ValueError('bad range')\n"
           f"    if value < low:\n        return low\n    if value > high:\n        return high\n    return value\n")
    wrong = [f"def {fn}(value, low, high):\n    return min(value, high)\n",
             f"def {fn}(value, low, high):\n    return max(low, min(value, high - 1))\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [[15, 0, 10], [-3, 0, 10]], [alt], wrong,
                 raises=raises)


def _dedupe_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["dedupe", "unique_in_order", "remove_duplicates"])
    mod = pick(rng, ["listtools", "sequences", "cleanup"])
    ref = (f"def {fn}(items: list) -> list:\n    seen = set()\n    out = []\n    for x in items:\n"
           f"        if x not in seen:\n            seen.add(x)\n            out.append(x)\n    return out\n")
    m = _mirror_of(ref, fn)
    cases = [[[]], [[1]], [[1, 1, 1]], [[3, 1, 3, 2, 1]], [["a", "A", "a"]], [[1, "1", 1]], [[-1, -1, 0, 0]],
             [["é", "e", "é"]], [[5, 4, 3, 2, 1]], [["x", "y", "x", "z", "y"]]]
    pool = [rng.randint(-5, 9) for _ in range(6)] + [pick(rng, WORDS) for _ in range(4)]
    diff = [[[pick(rng, pool) for _ in range(rng.randint(0, 20))]] for _ in range(200)]
    prompt = (f"Write a Python function `{fn}(items: list) -> list` that removes duplicates from a list and keeps "
              f"the first occurrence of each item in its original order. Items are ints or strings. For example "
              f"`{fn}([3, 1, 3, 2, 1])` returns `[3, 1, 2]`.")
    alt = f"def {fn}(items: list) -> list:\n    return list(dict.fromkeys(items))\n"
    wrong = [f"def {fn}(items: list) -> list:\n    return sorted(set(items))\n",
             f"def {fn}(items: list) -> list:\n    return list(set(items))\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [[[3, 1, 3, 2, 1]], [[]]], [alt], wrong,
                 tool_mode=True, stub=_stub_py(fn, "items: list"))


def _word_freq_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["word_freq", "count_words", "word_counts"])
    mod = pick(rng, ["wordstats", "textcount", "corpus"])
    ref = (f"def {fn}(text: str) -> dict:\n    counts = {{}}\n    for raw in text.lower().split():\n"
           f"        word = raw.strip(\".,!?;:\\\"'()\")\n        if word:\n            counts[word] = counts.get(word, 0) + 1\n"
           f"    return counts\n")
    m = _mirror_of(ref, fn)
    cases = [[s] for s in ["", "a a b", "The the THE", "Hello, world! Hello.", "...", "  spaced   out  ", "one",
                           "(yes) \"yes\" yes;", "Café café CAFÉ", "it's it's", "end. End? END!", "a-b a-b a"]]
    toks = [pick(rng, WORDS) for _ in range(6)]
    punct = ["", "", ",", ".", "!", "?", ";", ":", "\"", "'", ")"]
    diff = []
    for _ in range(200):
        parts = []
        for _ in range(rng.randint(0, 14)):
            w = pick(rng, toks)
            if rng.random() < 0.3:
                w = w.upper()
            parts.append(pick(rng, ["", "(", "\""]) + w + pick(rng, punct))
        diff.append([(" " * rng.randint(1, 2)).join(parts)])
    prompt = (f"Write a Python function `{fn}(text: str) -> dict` that counts how often each word appears. Split "
              f"on whitespace, lower-case every word, and strip these punctuation characters from both ends of "
              f"a word: `. , ! ? ; : \" ' ( )`. Words that end up empty are skipped. For example "
              f"`{fn}(\"Hello, world! Hello.\")` returns `{{\"hello\": 2, \"world\": 1}}`.")
    alt = (f"from collections import Counter\n\ndef {fn}(text: str) -> dict:\n    words = (w.strip(\".,!?;:\\\"'()\") "
           f"for w in text.lower().split())\n    return dict(Counter(w for w in words if w))\n")
    wrong = [f"def {fn}(text: str) -> dict:\n    out = {{}}\n    for w in text.split():\n        out[w] = out.get(w, 0) + 1\n    return out\n",
             f"def {fn}(text: str) -> dict:\n    return {{w: 1 for w in text.lower().split()}}\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [["Hello, world! Hello."], ["a a b"]], [alt], wrong)


def _second_largest_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["second_largest", "runner_up", "second_highest"])
    mod = pick(rng, ["rankings", "scores", "numstats"])
    ref = (f"def {fn}(nums: list):\n    distinct = sorted(set(nums), reverse=True)\n"
           f"    return distinct[1] if len(distinct) >= 2 else None\n")
    m = _mirror_of(ref, fn)
    cases = [[[]], [[1]], [[2, 2]], [[1, 2]], [[3, 1, 2]], [[5, 5, 4]], [[-1, -5, -3]], [[10, 10, 10, 9]],
             [[1, 2, 3, 4, 5]], [[0, 0, -1]], [[7, 7, 7, 7]]]
    diff = [[[rng.randint(-8, 8) for _ in range(rng.randint(0, 12))]] for _ in range(200)]
    prompt = (f"Write a Python function `{fn}(nums: list)` that returns the second largest distinct value in a "
              f"list of ints, or `None` if there are fewer than two distinct values. For example "
              f"`{fn}([5, 5, 4])` returns `4` and `{fn}([2, 2])` returns `None`.")
    alt = (f"def {fn}(nums: list):\n    first = second = None\n    for n in nums:\n"
           f"        if first is None or n > first:\n            if first is not None and n != first:\n"
           f"                second = first\n            first = n\n"
           f"        elif n != first and (second is None or n > second):\n            second = n\n    return second\n")
    wrong = [f"def {fn}(nums: list):\n    return sorted(nums)[-2]\n",
             f"def {fn}(nums: list):\n    return sorted(set(nums))[1] if len(set(nums)) > 1 else None\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [[[5, 5, 4]], [[2, 2]]], [alt], wrong)


def _merge_sorted_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["merge_sorted", "merge_lists", "combine_sorted"])
    mod = pick(rng, ["merging", "sortedlists", "seqtools"])
    ref = (f"def {fn}(a: list, b: list) -> list:\n    out = []\n    i = j = 0\n"
           f"    while i < len(a) and j < len(b):\n        if a[i] <= b[j]:\n            out.append(a[i])\n"
           f"            i += 1\n        else:\n            out.append(b[j])\n            j += 1\n"
           f"    out.extend(a[i:])\n    out.extend(b[j:])\n    return out\n")
    m = _mirror_of(ref, fn)
    cases = [[[], []], [[1], []], [[], [2]], [[1, 3, 5], [2, 4, 6]], [[1, 2, 3], [4, 5]], [[4, 5], [1, 2, 3]],
             [[1, 1, 2], [1, 2, 2]], [[-5, -1, 0], [-3, 2]], [[2], [2]], [[1, 2, 3], [1, 2, 3]]]
    diff = [[sorted(rng.randint(-20, 20) for _ in range(rng.randint(0, 10))),
             sorted(rng.randint(-20, 20) for _ in range(rng.randint(0, 10)))] for _ in range(200)]
    prompt = (f"Write a Python function `{fn}(a: list, b: list) -> list` that merges two lists that are each "
              f"already sorted in ascending order into one new sorted list, keeping duplicates. Do not change "
              f"the inputs. For example `{fn}([1, 3, 5], [2, 4])` returns `[1, 2, 3, 4, 5]`.")
    alt = f"def {fn}(a: list, b: list) -> list:\n    return sorted(a + b)\n"
    wrong = [f"def {fn}(a: list, b: list) -> list:\n    return a + b\n",
             f"def {fn}(a: list, b: list) -> list:\n    return sorted(set(a + b))\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [[[1, 3, 5], [2, 4]], [[], [2]]], [alt], wrong)


def _parse_kv_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["parse_kv", "parse_pairs", "read_settings"])
    mod = pick(rng, ["settings", "kvparse", "configtext"])
    ref = (f"def {fn}(text: str) -> dict:\n    out = {{}}\n    for part in text.split(\";\"):\n"
           f"        if \"=\" not in part:\n            continue\n        key, value = part.split(\"=\", 1)\n"
           f"        key = key.strip()\n        if key:\n            out[key] = value.strip()\n    return out\n")
    m = _mirror_of(ref, fn)
    cases = [[s] for s in ["", "a=1", "a=1;b=2", " a = 1 ; b=2 ", "a=1;;b=2;", "a=b=c", "novalue;a=1", "a=1;a=2",
                           "=x;b=2", "k=", "ключ=значение;naïve=ok", "  ;  ; "]]
    keys = [pick(rng, ["host", "port", "mode", "name", "level", "tag"]) for _ in range(5)]
    vals = ["1", "x", "a=b", "", "hello world", "é", " 7 "]
    diff = []
    for _ in range(200):
        parts = []
        for _ in range(rng.randint(0, 6)):
            r = rng.random()
            sp = lambda: " " * rng.randint(0, 2)
            if r < 0.75:
                parts.append(sp() + pick(rng, keys) + sp() + "=" + sp() + pick(rng, vals) + sp())
            elif r < 0.85:
                parts.append(pick(rng, keys))
            elif r < 0.93:
                parts.append("")
            else:
                parts.append("=" + pick(rng, vals))
        diff.append([";".join(parts)])
    prompt = (f"Write a Python function `{fn}(text: str) -> dict` that parses settings written as "
              f"`\"key=value;key2=value2\"` into a dict of strings. Trim spaces around every key and value. "
              f"Split each pair on the first `=` only, so a value may contain `=`. Skip empty pieces, pieces with "
              f"no `=`, and pairs whose key is empty. If a key repeats, the last value wins. For example "
              f"`{fn}(\" a = 1 ; b=x=y \")` returns `{{\"a\": \"1\", \"b\": \"x=y\"}}`.")
    alt = (f"def {fn}(text: str) -> dict:\n    result = {{}}\n    for chunk in text.split(';'):\n"
           f"        k, sep, v = chunk.partition('=')\n        if sep and k.strip():\n"
           f"            result[k.strip()] = v.strip()\n    return result\n")
    wrong = [f"def {fn}(text: str) -> dict:\n    return dict(p.split('=') for p in text.split(';'))\n",
             f"def {fn}(text: str) -> dict:\n    return {{k: v for k, v in (p.split('=', 1) for p in text.split(';') if '=' in p)}}\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [[" a = 1 ; b=x=y "], ["a=1;;b=2;"]], [alt], wrong)


def _format_bytes_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["format_bytes", "human_size", "size_label"])
    mod = pick(rng, ["filesize", "sizes", "diskinfo"])
    ref = (f"def {fn}(n: int) -> str:\n    if n < 0:\n        raise ValueError(\"n must not be negative\")\n"
           f"    if n < 1024:\n        return f\"{{n}} B\"\n    if n < 1024 * 1024:\n        return f\"{{n // 1024}} KB\"\n"
           f"    return f\"{{n // (1024 * 1024)}} MB\"\n")
    m = _mirror_of(ref, fn)
    cases = [[0], [1], [1023], [1024], [1536], [2047], [1048575], [1048576], [5 * 1024 ** 2 + 7], [10 ** 9], [3 * 1024]]
    raises = [([-1], "ValueError")]
    marks = [0, 1, 1023, 1024, 1025, 1048575, 1048576, 1048577, 10 ** 7, 5 * 10 ** 9]
    diff = []
    for _ in range(200):
        r = rng.random()
        diff.append([pick(rng, marks) if r < 0.4 else rng.randint(0, 3000) if r < 0.6 else rng.randint(0, 10 ** 10)])
    prompt = (f"Write a Python function `{fn}(n: int) -> str` that formats a byte count as text. Below 1024 give "
              f"`\"<n> B\"`; from 1024 up to (but not including) 1024*1024 give whole kilobytes `\"<n> KB\"` "
              f"(1 KB = 1024 bytes, rounded down); from 1024*1024 up give whole megabytes `\"<n> MB\"` (rounded "
              f"down, no larger unit). A negative `n` raises `ValueError`. For example `{fn}(1536)` returns "
              f"`\"1 KB\"` and `{fn}(512)` returns `\"512 B\"`.")
    alt = (f"def {fn}(n: int) -> str:\n    if n < 0:\n        raise ValueError('negative')\n"
           f"    if n >= 1 << 20:\n        return str(n >> 20) + ' MB'\n    if n >= 1 << 10:\n        return str(n >> 10) + ' KB'\n"
           f"    return str(n) + ' B'\n")
    wrong = [f"def {fn}(n: int) -> str:\n    return f\"{{round(n / 1024)}} KB\"\n",
             f"def {fn}(n: int) -> str:\n    if n <= 1024:\n        return f\"{{n}} B\"\n    return f\"{{n // 1024}} KB\"\n"]
    return _make("python", fn, mod, ref, m, cases, diff, prompt, [[1536], [512]], [alt], wrong, raises=raises)


# ---------------------------------------------------------------- JavaScript templates

def _sum_digits_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["sumDigits", "digitSum", "addDigits"])
    mod = pick(rng, ["numtools", "digits", "mathbits"])
    ref = (f"function {fn}(n) {{\n  let total = 0;\n  for (const ch of String(Math.abs(n))) total += Number(ch);\n"
           f"  return total;\n}}\n")

    def m(n):
        return sum(int(c) for c in str(abs(n)))

    cases = [[v] for v in [0, 5, 123, -123, 1000, 99999, -7, 2147483647, 10 ** 12, 909, -1000000]]
    diff = [[rng.randint(-10 ** rng.randint(1, 12), 10 ** rng.randint(1, 12))] for _ in range(200)]
    prompt = (f"Write a JavaScript function `{fn}(n)` that returns the sum of the decimal digits of an integer, "
              f"ignoring its sign. For example `{fn}(123)` returns `6` and `{fn}(-45)` returns `9`.")
    alt = (f"const {fn} = (n) => {{\n  let x = Math.abs(n), s = 0;\n  while (x > 0) {{\n    s += x % 10;\n"
           f"    x = Math.floor(x / 10);\n  }}\n  return s;\n}};\n")
    wrong = [f"function {fn}(n) {{\n  return String(n).split('').reduce((a, c) => a + Number(c), 0);\n}}\n",
             f"function {fn}(n) {{\n  return n % 9;\n}}\n"]
    return _make("javascript", fn, mod, ref, m, cases, diff, prompt, [[123], [-45]], [alt], wrong)


def _safe_divide_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["safeDivide", "divideOr", "ratioOr"])
    mod = pick(rng, ["calc", "mathsafe", "arith"])
    ref = f"function {fn}(a, b, fallback = 0) {{\n  if (b === 0) return fallback;\n  return a / b;\n}}\n"

    def m(a, b, fallback=0):
        return fallback if b == 0 else a / b

    cases = [[10, 2], [7, 2], [1, 0], [5, 5], [-9, 3], [5, -2], [0, 0], [1, 0, -1], [-8, -4], [100, 8, 99],
             [3, 0, 42]]
    diff = []
    for _ in range(200):
        args = [rng.randint(-50, 50), rng.randint(-6, 6)]
        if rng.random() < 0.5:
            args.append(rng.randint(-9, 9))
        diff.append(args)
    prompt = (f"Write a JavaScript function `{fn}(a, b, fallback = 0)` that returns `a / b`, or `fallback` when "
              f"`b` is `0`. For example `{fn}(7, 2)` returns `3.5`, `{fn}(1, 0)` returns `0` and "
              f"`{fn}(1, 0, -1)` returns `-1`.")
    alt = f"const {fn} = (a, b, fallback = 0) => (b === 0 ? fallback : a / b);\n"
    wrong = [f"function {fn}(a, b, fallback = 0) {{\n  return a / b;\n}}\n",
             f"function {fn}(a, b, fallback = 0) {{\n  return b ? Math.floor(a / b) : fallback;\n}}\n"]
    return _make("javascript", fn, mod, ref, m, cases, diff, prompt, [[7, 2], [1, 0, -1]], [alt], wrong)


def _camel_to_snake_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["camelToSnake", "toSnakeCase", "snakeify"])
    mod = pick(rng, ["casing", "naming", "strcase"])
    ref = (f"function {fn}(s) {{\n  return s.replace(/[A-Z]/g, (c, i) => (i > 0 ? '_' : '') + c.toLowerCase());\n}}\n")

    def m(s):
        return "".join((("_" if i > 0 else "") + c.lower()) if "A" <= c <= "Z" else c for i, c in enumerate(s))

    cases = [[s] for s in ["", "name", "myVarName", "Name", "userID", "a", "A", "x1Y2", "naïveCase", "cafÉ",
                           "HTTPServer", "already_snake"]]
    diff = [[_rand_text(rng, "abcdefghij" * 2 + "ABCDE" + "0123é_", 0, 18)] for _ in range(200)]
    prompt = (f"Write a JavaScript function `{fn}(s)` that converts a camelCase identifier to snake_case: every "
              f"ASCII capital letter that is not the first character gets an underscore before it, and all "
              f"capitals become lower case (so `userID` becomes `user_i_d`). Other characters are left alone. "
              f"For example `{fn}('myVarName')` returns `'my_var_name'` and `{fn}('Name')` returns `'name'`.")
    alt = (f"function {fn}(s) {{\n  let out = '';\n  for (let i = 0; i < s.length; i++) {{\n    const c = s[i];\n"
           f"    if (c >= 'A' && c <= 'Z') out += (i > 0 ? '_' : '') + c.toLowerCase();\n    else out += c;\n  }}\n  return out;\n}}\n")
    wrong = [f"function {fn}(s) {{\n  return s.replace(/[A-Z]/g, (c) => '_' + c.toLowerCase());\n}}\n",
             f"function {fn}(s) {{\n  return s.toLowerCase();\n}}\n"]
    return _make("javascript", fn, mod, ref, m, cases, diff, prompt, [["myVarName"], ["Name"]], [alt], wrong)


def _title_case_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["titleCase", "capitalizeWords", "toTitle"])
    mod = pick(rng, ["headings", "textcase", "labels"])
    ref = (f"function {fn}(s) {{\n  return s.split(' ')\n    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())\n"
           f"    .join(' ');\n}}\n")

    def m(s):
        return " ".join(w[:1].upper() + w[1:].lower() for w in s.split(" "))

    cases = [[s] for s in ["", "hello", "hello world", "HELLO WORLD", "mIxEd cAsE", "  two  spaces ", "a b c",
                           "élan vital", "über cool", "x", "one-two three", "3rd place"]]
    diff = [[_rand_text(rng, ALPHA + "éü" + "  ", 0, 24)] for _ in range(200)]
    prompt = (f"Write a JavaScript function `{fn}(s)` that capitalizes each word: the first letter of every "
              f"space-separated word becomes upper case and the rest lower case. Words are separated by single "
              f"space characters, and any extra spaces stay as they are. For example `{fn}('hello wORLD')` "
              f"returns `'Hello World'`.")
    alt = (f"const {fn} = (s) => s.split(' ').map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w)).join(' ');\n")
    wrong = [f"function {fn}(s) {{\n  return s.charAt(0).toUpperCase() + s.slice(1);\n}}\n",
             f"function {fn}(s) {{\n  return s.split(' ').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');\n}}\n"]
    return _make("javascript", fn, mod, ref, m, cases, diff, prompt, [["hello wORLD"], [""]], [alt], wrong)


def _group_by_first_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["groupByFirst", "indexByInitial", "bucketByLetter"])
    mod = pick(rng, ["grouping", "buckets", "wordindex"])
    ref = (f"function {fn}(words) {{\n  const out = {{}};\n  for (const w of words) {{\n    if (!w) continue;\n"
           f"    const key = w[0].toLowerCase();\n    if (!out[key]) out[key] = [];\n    out[key].push(w);\n  }}\n"
           f"  return out;\n}}\n")

    def m(words):
        out: dict = {}
        for w in words:
            if not w:
                continue
            out.setdefault(w[0].lower(), []).append(w)
        return out

    cases = [[w] for w in [[], [""], ["apple"], ["apple", "avocado", "banana"], ["Apple", "avocado"],
                           ["b", "B", "b"], ["", "x", ""], ["1st", "10th", "2nd"], ["Élan", "école", "eagle"],
                           ["zeta", "alpha", "zulu", "Alpha"]]]
    pool = [pick(rng, WORDS) for _ in range(8)] + ["", "ant", "Ant", "bee", "3d"]
    diff = [[[pick(rng, pool) for _ in range(rng.randint(0, 12))]] for _ in range(200)]
    prompt = (f"Write a JavaScript function `{fn}(words)` that groups an array of strings by their first letter. "
              f"It returns an object whose keys are the lower-cased first characters and whose values are arrays "
              f"of the original words (with their original case) in input order. Empty strings are skipped. "
              f"For example `{fn}(['Apple', 'avocado', 'banana'])` returns "
              f"`{{ a: ['Apple', 'avocado'], b: ['banana'] }}`.")
    alt = (f"function {fn}(words) {{\n  return words.filter(Boolean).reduce((acc, w) => {{\n"
           f"    const k = w.charAt(0).toLowerCase();\n    (acc[k] = acc[k] || []).push(w);\n    return acc;\n  }}, {{}});\n}}\n")
    wrong = [f"function {fn}(words) {{\n  const out = {{}};\n  for (const w of words) {{\n    (out[w[0]] = out[w[0]] || []).push(w);\n  }}\n  return out;\n}}\n",
             f"function {fn}(words) {{\n  const out = {{}};\n  for (const w of words) {{\n    out[w[0].toLowerCase()] = w;\n  }}\n  return out;\n}}\n"]
    stub = ""
    return _make("javascript", fn, mod, ref, m, cases, diff, prompt, [[["Apple", "avocado", "banana"]], [[]]], [alt], wrong)


TEMPLATES = [
    Template("write_py_initials", "write_fn", "L1", "python", _initials_py, tool_mode=True),
    Template("write_js_chunk", "write_fn", "L2", "javascript", _chunk_js, tool_mode=True),
    Template("write_py_dedupe", "write_fn", "L1", "python", _dedupe_py, tool_mode=True),
    Template("write_py_count_vowels", "write_fn", "L1", "python", _count_vowels_py),
    Template("write_py_clamp", "write_fn", "L1", "python", _clamp_py),
    Template("write_js_sum_digits", "write_fn", "L1", "javascript", _sum_digits_js),
    Template("write_js_safe_divide", "write_fn", "L1", "javascript", _safe_divide_js),
    Template("write_py_word_freq", "write_fn", "L2", "python", _word_freq_py),
    Template("write_py_second_largest", "write_fn", "L2", "python", _second_largest_py),
    Template("write_py_merge_sorted", "write_fn", "L2", "python", _merge_sorted_py),
    Template("write_js_camel_to_snake", "write_fn", "L2", "javascript", _camel_to_snake_js),
    Template("write_js_title_case", "write_fn", "L2", "javascript", _title_case_js),
    Template("write_py_parse_kv", "write_fn", "L3", "python", _parse_kv_py),
    Template("write_py_format_bytes", "write_fn", "L3", "python", _format_bytes_py),
    Template("write_js_group_by_first", "write_fn", "L3", "javascript", _group_by_first_js),
]
