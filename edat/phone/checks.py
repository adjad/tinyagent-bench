"""One-generation checks for phone parity items (spec 02 §6.3), shared by the Mac
runner and the re-grader so there is one scorer, not two.

A check is one of:

    {"kind": "calls", "same_step": true, "match": "all_any_order", "extra_calls": "allowed",
     "calls": [{"name": "update_event" | ["a", "b"], "args": {<arg>: <matcher>}}], "forbid": [...]}
    {"kind": "no_call", "text": {contains_all, contains_any, not_contains, regex}}
    {"kind": "either", "options": [<calls check>, <no_call check>], "forbid": [...]}

Matchers are Toolbench's set only (eq, icontains, contains_any, regex, in,
present, absent, date_offset, weekday), relative to the item's `today`, so the
Swift runner can implement the same ones. Regexes are limited to a subset
Python `re` and ICU read the same way (`portable_regex`).
"""
from __future__ import annotations

import json
import re
from datetime import date

PHONE_MATCHERS = ("eq", "icontains", "contains_any", "regex", "in", "present", "absent", "date_offset", "weekday")
_NONPORTABLE = [
    (re.compile(r"\(\?<[=!]"), "lookbehind"),
    (re.compile(r"\(\?>"), "atomic group"),
    (re.compile(r"\(\?P"), "Python named group"),
    (re.compile(r"(?<!\\)[*+?}]\+"), "possessive quantifier"),
    (re.compile(r"\(\?[a-hj-z]"), "inline flag other than i"),
    (re.compile(r"\\[AZzGQE]"), "anchor or quote escape"),
    (re.compile(r"\[\[:"), "POSIX class"),
    (re.compile(r"\\[pP]\{"), "Unicode property"),
]


def portable_regex(pattern: str) -> str | None:
    """None when the pattern is in the portable subset, else why not."""
    for rx, why in _NONPORTABLE:
        if rx.search(pattern):
            return why
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"does not compile: {exc}"
    return None


def _matcher_value(spec, value, today: date) -> str | None:
    from edat.grading.matchers import match_value
    from edat.state.clock import Clock
    if isinstance(spec, dict) and next(iter(spec)) not in PHONE_MATCHERS:
        raise ValueError(f"matcher {next(iter(spec))!r} is not available on the phone")
    clock = Clock.parse(f"{today.isoformat()}T12:00")
    return match_value(spec, value, clock)


def _call_ok(want: dict, call: dict, today: date) -> bool:
    names = want["name"] if isinstance(want["name"], list) else [want["name"]]
    if call.get("name") not in names or call.get("arguments") is None:
        return False
    args = call.get("arguments") or {}
    return all(_matcher_value(spec, args.get(k), today) is None for k, spec in (want.get("args") or {}).items())


def _text_ok(spec: dict | None, text: str) -> list[str]:
    from edat.grading.matchers import check_answer
    return check_answer(spec, text)


def evaluate(check: dict, calls: list[dict], text: str, today: date) -> tuple[bool, list[str]]:
    """(pass, labels) for one generation: its parsed calls and its visible text."""
    labels = []
    if any(c.get("arguments") is None for c in calls):
        labels.append("malformed")
    forbid = set(check.get("forbid") or [])
    if any(c.get("name") in forbid for c in calls):
        labels.append("unsafe_call")
    kind = check["kind"]
    if kind == "either":
        ok = any(evaluate({**opt, "forbid": check.get("forbid")}, calls, text, today)[0] for opt in check["options"])
        return ok and "unsafe_call" not in labels, labels
    if kind == "no_call":
        if calls:
            labels.append("unneeded_call")
        problems = _text_ok(check.get("text"), text)
        if problems:
            labels.append("text_check")
        return not calls and not problems and "unsafe_call" not in labels, labels
    if kind == "calls":
        used: set[int] = set()
        for want in check["calls"]:
            hit = next((i for i, c in enumerate(calls) if i not in used and _call_ok(want, c, today)), None)
            if hit is None:
                labels.append("missing_call")
                return False, labels
            used.add(hit)
        if check.get("extra_calls") == "forbidden" and len(calls) > len(used):
            labels.append("extra_call")
            return False, labels
        if check.get("text"):
            if _text_ok(check["text"], text):
                labels.append("text_check")
                return False, labels
        return "unsafe_call" not in labels, labels
    raise ValueError(f"unknown check kind {kind!r}")


# ------------------------------------------------------------------ Gemma 4 text-call parser

_GEMMA_CALL = re.compile(r"<\|tool_call>call:([A-Za-z_][\w.:-]*)\{(.*?)\}<tool_call\|>", re.S)
_Q = '<|"|>'


def _gemma_value(text: str):
    text = text.strip()
    if text.startswith(_Q) and text.endswith(_Q):
        return text[len(_Q):-len(_Q)]
    if text in ("true", "false"):
        return text == "true"
    try:
        return json.loads(text)
    except ValueError:
        return text


def parse_gemma4(raw_text: str) -> list[dict]:
    """Calls written in Gemma 4's `<|tool_call>call:name{key:<|"|>v<|"|>,...}<tool_call|>` form.

    tinyagent-bench's own reading of the format, used to compare call lists (parser
    agreement, §5.3 #44). It is not oMLX's parser; values that hold a comma
    outside quotes are split naively and reported as a parse error.
    """
    calls = []
    for m in _GEMMA_CALL.finditer(raw_text or ""):
        name, body = m.group(1), m.group(2)
        args, err = {}, None
        parts, buf, inq, i = [], "", False, 0
        while i < len(body):
            if body.startswith(_Q, i):
                inq = not inq
                buf += _Q
                i += len(_Q)
                continue
            ch = body[i]
            if ch == "," and not inq:
                parts.append(buf)
                buf = ""
            else:
                buf += ch
            i += 1
        if buf.strip():
            parts.append(buf)
        for p in parts:
            if ":" not in p:
                err = f"cannot read {p[:40]!r}"
                continue
            k, v = p.split(":", 1)
            args[k.strip()] = _gemma_value(v)
        calls.append({"name": name, "arguments": None if err else args, "parse_error": err})
    return calls


def same_calls(a: list[dict], b: list[dict]) -> bool:
    key = lambda c: (c.get("name"), json.dumps(c.get("arguments"), sort_keys=True))
    return sorted(map(key, a)) == sorted(map(key, b))
