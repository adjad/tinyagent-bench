"""Value matchers for arguments, records and answers.

EDAT's copy of Toolbench's matcher language (scripts/toolbench/matchers.py),
with the same meaning for every shared key, plus the matchers the agentic spec
adds (§2.4, §3.17). A spec is a bare value (shorthand for `eq`) or a mapping
with ONE key:

    eq: v              case-insensitive for strings; numbers compare as numbers;
                       ISO datetimes compare as datetimes (to the minute)
    icontains: s       the value (stringified; lists joined) contains s, ignoring case
    contains_any: []   contains at least one of the strings
    contains_all: []   contains every string
    not_contains: []   contains none of the strings (an absent value passes)
    regex: pat         re.search, ignoring case
    in: [..]           equals one of the values
    present / absent   supplied and non-empty / not supplied or empty
    date_offset: n     an ISO date or datetime whose date is today + n
    weekday: name      an ISO date on that weekday, within the coming 7 days
    gt/gte/lt/lte: n   numeric comparison
    datetime_eq: t     the same instant as t, time-zone aware (t may be relative,
                       "+1d 15:00", or {at: ISO, tz: zone}); a value with an offset
                       is converted into the board zone first
    rrule: {..}        an RFC 5545 rule string whose parts include these parts
    all_of: [specs]    every spec matches
    any_of: [specs]    at least one spec matches
    not: spec          the spec does not match

A bare string that is a relative time ("+1d 15:00") means datetime_eq.

`match_value(spec, value, clock)` returns None when the value satisfies the
spec, otherwise a short reason.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from edat.state.clock import Clock, is_relative, parse_local

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}")


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def as_text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return ", ".join(as_text(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {as_text(x)}" for k, x in v.items())
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and _NUM.match(v.strip()):
        return float(v.strip())
    return None


def _instant(v, clock: Clock) -> datetime | None:
    """A timezone-aware datetime for an ISO value (naive values are board-local)."""
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str):
        text = v.strip()
        if is_relative(text):
            text = clock.resolve(text)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = parse_local(text)
            if dt is None:
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.zone)
    return dt.replace(second=0, microsecond=0)


def _eq(value, want, clock: Clock) -> bool:
    if isinstance(want, bool) or isinstance(value, bool):
        return value == want or as_text(value).lower() == as_text(want).lower()
    nv, nw = _num(value), _num(want)
    if nv is not None and nw is not None:
        return nv == nw
    if isinstance(want, str) and isinstance(value, str) and _DT.match(want.strip()) and _DT.match(value.strip()):
        a, b = _instant(value, clock), _instant(want, clock)
        return a is not None and a == b
    if isinstance(want, str) or isinstance(value, str):
        return as_text(value).strip().lower() == as_text(want).strip().lower()
    return value == want


def _rrule_parts(text: str) -> dict[str, str]:
    text = text.strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    out = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().upper()] = ",".join(sorted(x.strip().upper() for x in v.split(",")))
    return out


def match_value(spec, value, clock: Clock) -> str | None:
    if not isinstance(spec, dict):
        if is_relative(spec):
            spec = {"datetime_eq": spec}
        else:
            spec = {"eq": spec}
    if len(spec) != 1:
        raise ValueError(f"matcher must have exactly one key, got {sorted(spec)}")
    (op, want), = spec.items()
    if op == "all_of":
        for sub in want:
            why = match_value(sub, value, clock)
            if why:
                return why
        return None
    if op == "any_of":
        reasons = [match_value(sub, value, clock) for sub in want]
        return None if any(r is None for r in reasons) else f"none of {len(want)} alternatives: {reasons[0]}"
    if op == "not":
        return None if match_value(want, value, clock) else f"{value!r} matches a forbidden spec"
    if op == "absent":
        return None if _empty(value) == bool(want) else f"expected {'absent' if want else 'present'}"
    if op == "present":
        return None if (not _empty(value)) == bool(want) else f"expected {'present' if want else 'absent'}"
    if op == "not_contains":
        text = as_text(value).lower()
        hit = [w for w in want if str(w).lower() in text]
        return None if not hit else f"contains forbidden {hit[0]!r}"
    if _empty(value) and not (op == "eq" and _empty(want)):
        return "missing"
    text = as_text(value)
    if op == "eq":
        return None if _eq(value, want, clock) else f"{value!r} != {want!r}"
    if op == "icontains":
        return None if str(want).lower() in text.lower() else f"{text[:80]!r} lacks {want!r}"
    if op == "contains_any":
        return None if any(str(w).lower() in text.lower() for w in want) else f"{text[:80]!r} lacks any of {want!r}"
    if op == "contains_all":
        miss = [w for w in want if str(w).lower() not in text.lower()]
        return None if not miss else f"{text[:80]!r} lacks {miss[0]!r}"
    if op == "regex":
        return None if re.search(want, text, re.I) else f"{text[:80]!r} !~ /{want}/"
    if op == "in":
        return None if any(_eq(value, w, clock) for w in want) else f"{value!r} not in {want!r}"
    if op in ("gt", "gte", "lt", "lte"):
        nv, nw = _num(value), _num(want)
        if nv is None or nw is None:
            return f"{value!r} is not a number"
        ok = {"gt": nv > nw, "gte": nv >= nw, "lt": nv < nw, "lte": nv <= nw}[op]
        return None if ok else f"{value!r} not {op} {want!r}"
    if op == "date_offset":
        d = _instant(value, clock)
        target = clock.today + timedelta(days=int(want))
        return None if d is not None and d.astimezone(clock.zone).date() == target else \
            f"{value!r} is not {target.isoformat()}"
    if op == "weekday":
        d = _instant(value, clock)
        if d is None:
            return f"{value!r} is not an ISO date"
        day = d.astimezone(clock.zone).date()
        ok = day.weekday() == _WEEKDAYS.index(str(want).lower()) and \
            clock.today <= day <= clock.today + timedelta(days=7)
        return None if ok else f"{value!r} is not the coming {want}"
    if op == "datetime_eq":
        if isinstance(want, dict):
            at = datetime.fromisoformat(clock.resolve_value(want["at"]))
            target = at.replace(tzinfo=ZoneInfo(want.get("tz") or clock.tz)) if at.tzinfo is None else at
        else:
            target = _instant(want, clock)
        got = _instant(value, clock)
        if got is None:
            return f"{value!r} is not a datetime"
        from datetime import timezone
        same = target is not None and got.astimezone(timezone.utc) == \
            target.replace(second=0, microsecond=0).astimezone(timezone.utc)
        return None if same else \
            f"{value!r} is not {target.isoformat() if target else want!r}"
    if op == "rrule":
        got = _rrule_parts(text)
        want_parts = {str(k).upper(): ",".join(sorted(x.strip().upper() for x in str(v).split(",")))
                      for k, v in want.items()}
        miss = [k for k, v in want_parts.items() if got.get(k) != v]
        return None if not miss else f"{text!r} differs in {miss}"
    raise ValueError(f"unknown matcher {op!r}")


def match_args(spec: dict | None, args: dict | None, clock: Clock) -> list[str]:
    problems = []
    for key, sub in (spec or {}).items():
        why = match_value(sub, (args or {}).get(key), clock)
        if why:
            problems.append(f"{key}: {why}")
    return problems


def field_value(record: dict, path: str):
    """Read a field, a dotted path into nested dicts, or a derived part of a datetime.

    `start.date`, `start.time` ("HH:MM"), `start.weekday` (lower-case name) and
    `start.hour` work on ISO datetime fields; `to.count` gives a list's length.
    """
    if path in record:
        return record[path]
    head, _, rest = path.partition(".")
    if not rest:
        return None
    base = record.get(head)
    if isinstance(base, dict):
        return field_value(base, rest)
    if rest == "count" and isinstance(base, (list, tuple)):
        return len(base)
    if isinstance(base, str):
        dt = parse_local(base)
        if dt is not None:
            if rest == "date":
                return dt.date().isoformat()
            if rest == "time":
                return dt.strftime("%H:%M")
            if rest == "hour":
                return dt.hour
            if rest == "weekday":
                return _WEEKDAYS[dt.weekday()]
    return None


# Answer checks ---------------------------------------------------------------

ANSWER_KEYS = ("contains_all", "contains_any", "not_contains", "regex", "not_regex", "pref_items")


def check_answer(spec: dict | None, text: str) -> list[str]:
    """Answer checks on the final text; each key is one check."""
    problems = []
    low = (text or "").lower()
    for key, want in (spec or {}).items():
        if key == "contains_all":
            miss = [w for w in want if str(w).lower() not in low]
            if miss:
                problems.append(f"answer lacks {miss}")
        elif key == "contains_any":
            if not any(str(w).lower() in low for w in want):
                problems.append(f"answer lacks any of {want}")
        elif key == "not_contains":
            hit = [w for w in want if str(w).lower() in low]
            if hit:
                problems.append(f"answer contains {hit}")
        elif key == "regex":
            if not re.search(want, text or "", re.I | re.S):
                problems.append(f"answer !~ /{want}/")
        elif key == "pref_items":
            from edat.grading.pref_checks import check_items
            problems += check_items(want, text or "")
        elif key == "not_regex":
            if re.search(want, text or "", re.I | re.S):
                problems.append(f"answer ~ forbidden /{want}/")
        else:
            raise ValueError(f"unknown answer check {key!r}")
    return problems


def answer_check_count(spec: dict | None) -> int:
    return len(spec or {})
