"""`write_test` templates: a function and its intended behaviour; the model
writes 2-6 tests. They must pass on the correct code and catch at least 3 of
5 hand-picked mutants (off-by-one, wrong comparison, a missing case...).
Each mutant must be caught by the reference tests (checked by the validator).
"""
from __future__ import annotations

import random

from edat.sidekick.gen.core import Instance, Template, fence, pick


def _is_leap_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["is_leap", "is_leap_year", "leap_year"])
    mod = pick(rng, ["calendar_utils", "dates", "leap"])
    impl = f"def {fn}(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n"
    mutants = [
        f"def {fn}(year):\n    return year % 4 == 0\n",
        f"def {fn}(year):\n    return year % 4 == 0 and year % 100 != 0\n",
        f"def {fn}(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 != 0)\n",
        f"def {fn}(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) or year == 1900\n",
        f"def {fn}(year):\n    return not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))\n",
    ]
    prompt = (f"Write pytest tests for `{fn}(year)` in `{mod}.py`: it returns True for leap years in the Gregorian "
              f"calendar (divisible by 4, except centuries, except centuries divisible by 400). "
              f"Import it with `from {mod} import {fn}`. Give 2 to 6 tests in one ```python block.\n\n"
              f"```python\n{impl}```")
    good = (f"from {mod} import {fn}\n\n"
            f"def test_regular_leap():\n    assert {fn}(2024)\n\n"
            f"def test_common_year():\n    assert not {fn}(2023)\n\n"
            f"def test_century_not_leap():\n    assert not {fn}(1900)\n\n"
            f"def test_400_is_leap():\n    assert {fn}(2000)\n")
    good2 = (f"import pytest\nfrom {mod} import {fn}\n\n"
             f"@pytest.mark.parametrize('y,want', [(1996, True), (1999, False), (2100, False), (1600, True)])\n"
             f"def test_{fn}(y, want):\n    assert {fn}(y) is want\n")
    wrong = [fence(f"from {mod} import {fn}\n\ndef test_it():\n    assert {fn}(2024)\n", "python"),
             fence(f"from {mod} import {fn}\n\ndef test_century():\n    assert {fn}(1900)\n", "python")]
    check = {"lang": "python", "module": mod, "entry": fn, "impl": impl, "mutants": mutants,
             "test_file": f"test_{mod}.py", "min_caught": 3}
    return Instance(prompt, "python", "write_test", check, [fence(good, "python"), fence(good2, "python")], wrong,
                    given_names=[fn, mod])


def _slugify_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["slugify", "toSlug", "makeSlug"])
    mod = pick(rng, ["slug", "urls", "textTools"])
    body = ("  return title.toLowerCase()\n"
            "    .replace(/[^a-z0-9\\s-]/g, '')\n"
            "    .trim()\n"
            "    .replace(/[\\s-]+/g, '-');\n")
    impl = f"function {fn}(title) {{\n{body}}}\nmodule.exports = {{ {fn} }};\n"
    m = [
        body.replace("/[\\s-]+/g", "/\\s/g"),                        # no collapse
        body.replace("    .trim()\n", ""),                           # keeps leading/trailing dashes
        body.replace("title.toLowerCase()", "title"),                # keeps upper case
        body.replace("[^a-z0-9\\s-]", "[^a-z\\s-]"),                 # drops digits
        body.replace("    .replace(/[^a-z0-9\\s-]/g, '')\n", ""),    # keeps punctuation
    ]
    mutants = [f"function {fn}(title) {{\n{b}}}\nmodule.exports = {{ {fn} }};\n" for b in m]
    prompt = (f"Write tests with Node's built-in `node:test` and `node:assert` for `{fn}(title)` from `./{mod}.js`: "
              f"it lower-cases, removes punctuation, turns spaces into dashes, collapses repeated dashes, and trims. "
              f"Import it with `const {{ {fn} }} = require('./{mod}.js');`. Give 2 to 6 tests in one ```js block.\n\n"
              f"```js\n{impl}```")
    good = (f"const test = require('node:test');\nconst assert = require('node:assert');\n"
            f"const {{ {fn} }} = require('./{mod}.js');\n\n"
            f"test('basic', () => assert.strictEqual({fn}('Hello World'), 'hello-world'));\n"
            f"test('collapse', () => assert.strictEqual({fn}('a  -  b'), 'a-b'));\n"
            f"test('trim', () => assert.strictEqual({fn}('  Hi  '), 'hi'));\n"
            f"test('digits and punctuation', () => assert.strictEqual({fn}('Top 10, ever!'), 'top-10-ever'));\n")
    wrong = [fence(f"const test = require('node:test');\nconst assert = require('node:assert');\n"
                   f"const {{ {fn} }} = require('./{mod}.js');\ntest('x', () => assert.ok(typeof {fn}('a') === 'string'));\n", "js"),
             fence("console.log('tests');\n", "js")]
    check = {"lang": "javascript", "module": mod, "entry": fn, "impl": impl, "mutants": mutants,
             "test_file": f"{mod}.test.js", "min_caught": 3}
    return Instance(prompt, "javascript", "write_test", check, [fence(good, "js")], wrong, given_names=[fn, mod])



# ---------------------------------------------------------------- shared builders

def _py_test_inst(fn: str, mod: str, impl: str, mutants: list[str], what: str, goods: list[str],
                  wrongs: list[str]) -> Instance:
    prompt = (f"Write pytest tests for `{fn}` in `{mod}.py`: {what} Import it with `from {mod} import {fn}`. "
              f"Give 2 to 6 tests in one ```python block.\n\n```python\n{impl}```")
    check = {"lang": "python", "module": mod, "entry": fn, "impl": impl, "mutants": mutants,
             "test_file": f"test_{mod}.py", "min_caught": 3}
    assert len(mutants) == 5
    return Instance(prompt, "python", "write_test", check, [fence(g, "python") for g in goods],
                    [fence(w, "python") for w in wrongs], given_names=[fn, mod])


def _js_test_inst(fn: str, mod: str, impl: str, mutants: list[str], what: str, goods: list[str],
                  wrongs: list[str]) -> Instance:
    prompt = (f"Write tests with Node's built-in `node:test` and `node:assert` for `{fn}` from `./{mod}.js`: "
              f"{what} Import it with `const {{ {fn} }} = require('./{mod}.js');`. Give 2 to 6 tests in one "
              f"```js block.\n\n```js\n{impl}```")
    check = {"lang": "javascript", "module": mod, "entry": fn, "impl": impl, "mutants": mutants,
             "test_file": f"{mod}.test.js", "min_caught": 3}
    assert len(mutants) == 5
    return Instance(prompt, "javascript", "write_test", check, [fence(g, "js") for g in goods],
                    [fence(w, "js") for w in wrongs], given_names=[fn, mod])


JS_HEAD = "const test = require('node:test');\nconst assert = require('node:assert');\n"


def _clamp_test_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["clamp", "clamp_value", "limit"])
    mod = pick(rng, ["ranges", "numrange", "bounds"])
    def src(body):
        return f"def {fn}(value, low, high):\n{body}"
    impl = src("    return max(low, min(value, high))\n")
    mutants = [
        src("    return max(low, value)\n"),
        src("    return min(value, high)\n"),
        src("    return max(low, min(value, high - 1))\n"),
        src("    return min(low, max(value, high))\n"),
        src("    return value if low < value < high else low\n"),
    ]
    good = (f"from {mod} import {fn}\n\n"
            f"def test_inside():\n    assert {fn}(5, 0, 10) == 5\n\n"
            f"def test_below():\n    assert {fn}(-3, 0, 10) == 0\n\n"
            f"def test_above():\n    assert {fn}(15, 0, 10) == 10\n\n"
            f"def test_edges():\n    assert {fn}(10, 0, 10) == 10\n    assert {fn}(0, 0, 10) == 0\n\n"
            f"def test_negative_range():\n    assert {fn}(-20, -10, -1) == -10\n    assert {fn}(-7, -10, -1) == -7\n")
    good2 = (f"import pytest\nfrom {mod} import {fn}\n\n"
             f"@pytest.mark.parametrize('v,want', [(5, 5), (-1, 0), (11, 10), (0, 0), (10, 10), (100, 10), (-100, 0)])\n"
             f"def test_{fn}(v, want):\n    assert {fn}(v, 0, 10) == want\n")
    wrong = [f"from {mod} import {fn}\n\ndef test_inside():\n    assert {fn}(5, 0, 10) == 5\n",
             f"from {mod} import {fn}\n\ndef test_above():\n    assert {fn}(15, 0, 10) == 15\n"]
    return _py_test_inst(fn, mod, impl, mutants,
                         f"`{fn}(value, low, high)` returns `value` limited to the range `low` to `high`, "
                         f"both ends included.", [good, good2], wrong)


def _parse_bool_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["parse_bool", "to_bool", "read_flag"])
    mod = pick(rng, ["flags", "convert", "options"])
    def src(body):
        return f"def {fn}(text):\n{body}"
    base = ("    t = text.strip().lower()\n"
            "    if t in (\"yes\", \"true\", \"1\", \"on\"):\n        return True\n"
            "    if t in (\"no\", \"false\", \"0\", \"off\"):\n        return False\n"
            "    raise ValueError(f\"not a boolean: {text!r}\")\n")
    impl = src(base)
    mutants = [
        src(base.replace(".strip()", "")),
        src(base.replace(".lower()", "")),
        src(base.replace(", \"on\"", "")),
        src(base.replace("    raise ValueError(f\"not a boolean: {text!r}\")\n", "    return False\n")),
        src("    t = text.strip().lower()\n    if t.startswith((\"y\", \"t\", \"1\", \"o\")):\n        return True\n"
            "    if t in (\"no\", \"false\", \"0\", \"off\"):\n        return False\n"
            "    raise ValueError(f\"not a boolean: {text!r}\")\n"),
    ]
    good = (f"import pytest\nfrom {mod} import {fn}\n\n"
            f"def test_true_words():\n    assert {fn}('yes') is True\n    assert {fn}('TRUE') is True\n    assert {fn}('1') is True\n\n"
            f"def test_false_words():\n    assert {fn}('no') is False\n    assert {fn}('False') is False\n    assert {fn}('0') is False\n    assert {fn}('off') is False\n\n"
            f"def test_spaces_and_on():\n    assert {fn}('  on ') is True\n\n"
            f"def test_unknown_raises():\n    with pytest.raises(ValueError):\n        {fn}('maybe')\n    with pytest.raises(ValueError):\n        {fn}('')\n")
    wrong = [f"from {mod} import {fn}\n\ndef test_yes():\n    assert {fn}('yes') is True\n\ndef test_no():\n    assert {fn}('no') is False\n",
             f"from {mod} import {fn}\n\ndef test_maybe():\n    assert {fn}('maybe') is False\n"]
    return _py_test_inst(fn, mod, impl, mutants,
                         f"`{fn}(text)` turns `yes`, `true`, `1`, `on` into `True` and `no`, `false`, `0`, `off` "
                         f"into `False`; it ignores case and surrounding spaces, and raises `ValueError` for "
                         f"anything else.", [good], wrong)


def _fizz_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["fizzbuzz", "fizz_word", "say_number"])
    mod = pick(rng, ["games", "wordplay", "counting"])
    def src(body):
        return f"def {fn}(n):\n{body}"
    impl = src("    if n % 15 == 0:\n        return \"FizzBuzz\"\n    if n % 3 == 0:\n        return \"Fizz\"\n"
               "    if n % 5 == 0:\n        return \"Buzz\"\n    return str(n)\n")
    mutants = [
        src("    if n % 3 == 0:\n        return \"Fizz\"\n    if n % 5 == 0:\n        return \"Buzz\"\n    if n % 15 == 0:\n        return \"FizzBuzz\"\n    return str(n)\n"),
        src("    if n % 15 == 0:\n        return \"FizzBuzz\"\n    if n % 3 == 1:\n        return \"Fizz\"\n    if n % 5 == 0:\n        return \"Buzz\"\n    return str(n)\n"),
        src("    if n % 15 == 0:\n        return \"FizzBuzz\"\n    if n % 3 == 0:\n        return \"Fizz\"\n    if n % 5 == 0:\n        return \"Buzz\"\n    return n\n"),
        src("    if n % 15 == 0:\n        return \"FizzBuzz\"\n    if n % 3 == 0:\n        return \"Fizz\"\n    if n % 5 == 0 and n > 5:\n        return \"Buzz\"\n    return str(n)\n"),
        src("    if n > 0 and n % 15 == 0:\n        return \"FizzBuzz\"\n    if n > 0 and n % 3 == 0:\n        return \"Fizz\"\n    if n > 0 and n % 5 == 0:\n        return \"Buzz\"\n    return str(n)\n"),
    ]
    good = (f"from {mod} import {fn}\n\n"
            f"def test_plain_numbers():\n    assert {fn}(1) == '1'\n    assert {fn}(7) == '7'\n\n"
            f"def test_fizz():\n    assert {fn}(3) == 'Fizz'\n    assert {fn}(9) == 'Fizz'\n\n"
            f"def test_buzz():\n    assert {fn}(5) == 'Buzz'\n    assert {fn}(10) == 'Buzz'\n\n"
            f"def test_fizzbuzz():\n    assert {fn}(15) == 'FizzBuzz'\n    assert {fn}(30) == 'FizzBuzz'\n\n"
            f"def test_zero_and_negative():\n    assert {fn}(0) == 'FizzBuzz'\n    assert {fn}(-3) == 'Fizz'\n")
    wrong = [f"from {mod} import {fn}\n\ndef test_plain():\n    assert {fn}(2) == '2'\n",
             f"from {mod} import {fn}\n\ndef test_fizz():\n    assert {fn}(3) == 'Fizz'\n    assert {fn}(15) == 'Fizz'\n"]
    return _py_test_inst(fn, mod, impl, mutants,
                         f"`{fn}(n)` returns `\"FizzBuzz\"` if `n` is divisible by 3 and 5, `\"Fizz\"` if only by 3, "
                         f"`\"Buzz\"` if only by 5, otherwise `n` as a string. It works for 0 and negative numbers "
                         f"too.", [good], wrong)


def _median_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["median", "middle_value", "median_of"])
    mod = pick(rng, ["stats", "numstats", "summary"])
    def src(body):
        return f"def {fn}(nums):\n{body}"
    head = "    if not nums:\n        raise ValueError(\"empty list\")\n"
    impl = src(head + "    s = sorted(nums)\n    n = len(s)\n    mid = n // 2\n    if n % 2:\n        return s[mid]\n"
               "    return (s[mid - 1] + s[mid]) / 2\n")
    mutants = [
        src(head + "    s = list(nums)\n    n = len(s)\n    mid = n // 2\n    if n % 2:\n        return s[mid]\n    return (s[mid - 1] + s[mid]) / 2\n"),
        src(head + "    s = sorted(nums)\n    n = len(s)\n    mid = n // 2\n    if n % 2:\n        return s[mid]\n    return s[mid]\n"),
        src(head + "    s = sorted(nums)\n    n = len(s)\n    mid = n // 2\n    if n % 2 == 0:\n        return s[mid]\n    return (s[mid - 1] + s[mid]) / 2\n"),
        src("    if not nums:\n        return 0\n    s = sorted(nums)\n    n = len(s)\n    mid = n // 2\n    if n % 2:\n        return s[mid]\n    return (s[mid - 1] + s[mid]) / 2\n"),
        src(head + "    s = sorted(nums)\n    n = len(s)\n    mid = n // 2\n    if n % 2:\n        return s[mid]\n    return (s[mid - 1] + s[mid]) // 2\n"),
    ]
    good = (f"import pytest\nfrom {mod} import {fn}\n\n"
            f"def test_odd_unsorted():\n    assert {fn}([3, 1, 2]) == 2\n    assert {fn}([7, 1, 3, 5, 9]) == 5\n\n"
            f"def test_even_averages_middle_pair():\n    assert {fn}([4, 1, 3, 2]) == 2.5\n    assert {fn}([1, 2]) == 1.5\n\n"
            f"def test_single():\n    assert {fn}([5]) == 5\n\n"
            f"def test_empty_raises():\n    with pytest.raises(ValueError):\n        {fn}([])\n")
    wrong = [f"from {mod} import {fn}\n\ndef test_sorted_odd():\n    assert {fn}([1, 2, 3]) == 2\n    assert {fn}([1, 2, 3, 4, 5]) == 3\n",
             f"from {mod} import {fn}\n\ndef test_even():\n    assert {fn}([1, 2, 3, 4]) == 2\n"]
    return _py_test_inst(fn, mod, impl, mutants,
                         f"`{fn}(nums)` returns the median of a list of numbers in any order: the middle value "
                         f"for an odd count, the average of the two middle values for an even count, and it "
                         f"raises `ValueError` for an empty list.", [good], wrong)


def _truncate_py(rng: random.Random) -> Instance:
    fn = pick(rng, ["truncate", "shorten", "ellipsize"])
    mod = pick(rng, ["textfit", "preview", "captions"])
    def src(body):
        return f"def {fn}(text, width):\n{body}"
    guard = "    if width < 3:\n        raise ValueError(\"width must be at least 3\")\n"
    impl = src(guard + "    if len(text) <= width:\n        return text\n    return text[:width - 3] + \"...\"\n")
    mutants = [
        src(guard + "    if len(text) < width:\n        return text\n    return text[:width - 3] + \"...\"\n"),
        src(guard + "    if len(text) <= width:\n        return text\n    return text[:width - 2] + \"...\"\n"),
        src(guard.replace("< 3", "<= 3") + "    if len(text) <= width:\n        return text\n    return text[:width - 3] + \"...\"\n"),
        src(guard + "    if len(text) <= width:\n        return text\n    return text[:width - 3]\n"),
        src("    if len(text) <= width:\n        return text\n    return text[:width - 3] + \"...\"\n"),
    ]
    good = (f"import pytest\nfrom {mod} import {fn}\n\n"
            f"def test_short_text_unchanged():\n    assert {fn}('hi', 10) == 'hi'\n    assert {fn}('', 5) == ''\n\n"
            f"def test_exact_fit_unchanged():\n    assert {fn}('hello', 5) == 'hello'\n\n"
            f"def test_long_text_cut_with_dots():\n    assert {fn}('hello world', 8) == 'hello...'\n    assert len({fn}('abcdefghij', 6)) == 6\n\n"
            f"def test_minimum_width():\n    assert {fn}('abcdef', 3) == '...'\n\n"
            f"def test_too_narrow():\n    with pytest.raises(ValueError):\n        {fn}('abcdef', 2)\n")
    wrong = [f"from {mod} import {fn}\n\ndef test_short():\n    assert {fn}('hi', 10) == 'hi'\n",
             f"from {mod} import {fn}\n\ndef test_cut():\n    assert {fn}('hello world', 8) == 'hello wo...'\n"]
    return _py_test_inst(fn, mod, impl, mutants,
                         f"`{fn}(text, width)` shortens text to at most `width` characters: text that fits is "
                         f"returned unchanged, longer text is cut and ends with `...` so the result is exactly "
                         f"`width` long. A `width` below 3 raises `ValueError`.", [good], wrong)


def _hex_color_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["isValidHexColor", "isHexColor", "validHex"])
    mod = pick(rng, ["colors", "palette", "cssutil"])
    core = "/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/"
    def src(rx):
        return f"function {fn}(s) {{\n  return typeof s === 'string' && {rx}.test(s);\n}}\nmodule.exports = {{ {fn} }};\n"
    impl = src(core)
    mutants = [
        src("/^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/"),
        src("/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/"),
        src("/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})/"),
        src("/^#[0-9a-fA-F]{6}$/"),
        src("/^#[0-9a-fA-F]{3,6}$/"),
    ]
    good = (JS_HEAD + f"const {{ {fn} }} = require('./{mod}.js');\n\n"
            f"test('accepts 3 and 6 digits', () => {{\n  assert.strictEqual({fn}('#fff'), true);\n  assert.strictEqual({fn}('#1a2B3c'), true);\n  assert.strictEqual({fn}('#ABC'), true);\n}});\n"
            f"test('needs the hash', () => assert.strictEqual({fn}('fff'), false));\n"
            f"test('rejects other lengths', () => {{\n  assert.strictEqual({fn}('#ffff'), false);\n  assert.strictEqual({fn}('#12345'), false);\n  assert.strictEqual({fn}(''), false);\n}});\n"
            f"test('rejects bad characters', () => {{\n  assert.strictEqual({fn}('#ggg'), false);\n  assert.strictEqual({fn}('#12345g'), false);\n}});\n")
    wrong = [JS_HEAD + f"const {{ {fn} }} = require('./{mod}.js');\ntest('x', () => assert.strictEqual({fn}('#fff'), true));\n",
             JS_HEAD + f"const {{ {fn} }} = require('./{mod}.js');\ntest('x', () => assert.strictEqual({fn}('#ffff'), true));\n"]
    return _js_test_inst(fn, mod, impl, mutants,
                         f"`{fn}(s)` returns `true` for CSS hex colors: a `#` followed by exactly 3 or exactly 6 "
                         f"hex digits (upper or lower case), and `false` for anything else.", [good], wrong)


def _phone_js(rng: random.Random) -> Instance:
    fn = pick(rng, ["normalizePhone", "cleanPhone", "phoneDigits"])
    mod = pick(rng, ["phones", "contacts", "dialing"])
    def src(body):
        return f"function {fn}(raw) {{\n{body}}}\nmodule.exports = {{ {fn} }};\n"
    d = "  const digits = String(raw).replace(/\\D/g, '');\n"
    tail = "  return local.length === 10 ? local : null;\n"
    cond = "digits.length === 11 && digits[0] === '1'"
    impl = src(d + f"  const local = {cond} ? digits.slice(1) : digits;\n" + tail)
    mutants = [
        src(d + "  const local = digits;\n" + tail),
        src("  const digits = String(raw).replace(/[\\s-]/g, '');\n" + f"  const local = {cond} ? digits.slice(1) : digits;\n" + tail),
        src(d + f"  const local = {cond} ? digits.slice(1) : digits;\n  return local;\n"),
        src(d + "  const local = digits[0] === '1' ? digits.slice(1) : digits;\n" + tail),
        src(d + f"  const local = {cond} ? digits.slice(1) : digits;\n  return local.length >= 10 ? local : null;\n"),
    ]
    good = (JS_HEAD + f"const {{ {fn} }} = require('./{mod}.js');\n\n"
            f"test('strips punctuation', () => {{\n  assert.strictEqual({fn}('(555) 123-4567'), '5551234567');\n  assert.strictEqual({fn}('555.123.4567'), '5551234567');\n}});\n"
            f"test('drops a leading country 1', () => {{\n  assert.strictEqual({fn}('+1 555 123 4567'), '5551234567');\n  assert.strictEqual({fn}('1-555-123-4567'), '5551234567');\n}});\n"
            f"test('keeps a 10 digit number that starts with 1', () => assert.strictEqual({fn}('1234567890'), '1234567890'));\n"
            f"test('wrong length gives null', () => {{\n  assert.strictEqual({fn}('12345'), null);\n  assert.strictEqual({fn}(''), null);\n  assert.strictEqual({fn}('22345678901'), null);\n}});\n")
    wrong = [JS_HEAD + f"const {{ {fn} }} = require('./{mod}.js');\ntest('x', () => assert.strictEqual({fn}('5551234567'), '5551234567'));\n",
             JS_HEAD + f"const {{ {fn} }} = require('./{mod}.js');\ntest('x', () => assert.strictEqual({fn}('(555) 123-4567'), '(555) 123-4567'));\n"]
    return _js_test_inst(fn, mod, impl, mutants,
                         f"`{fn}(raw)` keeps only the digits of a phone number string. If that gives 11 digits "
                         f"starting with `1`, the leading `1` is dropped. It returns the 10 remaining digits as a "
                         f"string, or `null` if there are not exactly 10.", [good], wrong)


TEMPLATES = [
    Template("test_py_is_leap", "write_test", "L1", "python", _is_leap_py, variant="pytest"),
    Template("test_js_slugify", "write_test", "L2", "javascript", _slugify_js, variant="node"),
    Template("test_py_clamp", "write_test", "L1", "python", _clamp_test_py, variant="pytest"),
    Template("test_py_parse_bool", "write_test", "L1", "python", _parse_bool_py, variant="pytest"),
    Template("test_py_fizzbuzz", "write_test", "L1", "python", _fizz_py, variant="pytest"),
    Template("test_py_median", "write_test", "L2", "python", _median_py, variant="pytest"),
    Template("test_js_hex_color", "write_test", "L2", "javascript", _hex_color_js, variant="node"),
    Template("test_js_phone", "write_test", "L2", "javascript", _phone_js, variant="node"),
    Template("test_py_truncate", "write_test", "L3", "python", _truncate_py, variant="pytest"),
]
