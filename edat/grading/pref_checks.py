"""The seam to the quality spec's format checkers for preference items 1-9.

Spec 02 §3.14: items 1-9 of 14-standing-preferences.md (lead with the answer,
at most three sentences, bullets for three or more items, no tables, no
emoji, 24-hour times, dates like "Tue 19 Nov", metric units, a fixed sign-off)
are checked by the quality chat's shared checkers, `edat/quality/checkers.py`.

Until that module lands, this file supplies **stub** checkers with the same
call shape, so Preferences cases can be written and validated now. Every
result made by a stub is marked, and `using_stubs()` says whether any stub is
active, so no published number can come from a stub unnoticed.

The real module's API (build/quality, edat/quality/checkers.py):

    check(pref_id: "P1".."P9", text, surface, meta) -> CheckResult{status pass|fail|n/a, reason, ...}

This seam keeps its own call shape, `check(item, text, **params) -> str | None`
(None when `text` follows the preference, else a short reason), and maps onto
the real API when it is importable: item n -> "Pn"; `surface` (default
"answer"); `items` -> meta.expected_items; `sign_off` -> meta.signoff;
`require` (item 7) -> meta.needs_date; any other key goes to `Meta` by name
(unknown keys are dropped). A real "n/a" counts as following the preference.

An answer check `pref_items: [6, {item: 9, sign_off: "Best, Ines"}]` runs
these checks on the turn's final text.
"""
from __future__ import annotations

import re

try:  # the quality chat's module, when it lands
    from edat.quality.checkers import check as _real_check  # type: ignore
except Exception:  # pragma: no cover - expected until the quality branch merges
    _real_check = None

_META_RENAMES = {"items": "expected_items", "sign_off": "signoff", "require": "needs_date"}


def _real(item: int, text: str, **params) -> str | None:
    surface = params.pop("surface", "answer")
    meta = {_META_RENAMES.get(k, k): v for k, v in params.items()}
    r = _real_check(f"P{int(item)}", text or "", surface, meta)
    return (r.reason or f"fails P{item}") if r.status == "fail" else None

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
_TWELVE_H = re.compile(r"\b\d{1,2}(:\d{2})?\s?(am|pm|a\.m\.|p\.m\.)\b", re.I)
_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_SENT = re.compile(r"[^.!?]+[.!?]")
_IMPERIAL = re.compile(r"\b\d+(\.\d+)?\s?(°f|degrees fahrenheit|fahrenheit|miles?|mph|lbs?|pounds|feet|ft|inches|oz)\b", re.I)
_DATE_STYLE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
_OTHER_DATE = re.compile(r"\b(January|February|March|April|June|July|August|September|October|November|December) \d{1,2}\b|"
                         r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b")
_FILLER_OPEN = re.compile(r"^\W*(sure|of course|great question|certainly|okay|ok|absolutely|happy to help|let me)\b", re.I)


def _stub(item: int, text: str, **p) -> str | None:
    t = (text or "").strip()
    if item == 1:
        return "opens with filler instead of the answer" if _FILLER_OPEN.search(t) else None
    if item == 2:
        n = len(_SENT.findall(t)) or (1 if t else 0)
        return f"{n} sentences, more than 3" if n > int(p.get("max_sentences", 3)) else None
    if item == 3:
        lines = [l for l in t.splitlines() if re.match(r"^\s*([-*•]|\d+[.)])\s", l)]
        return f"needs bullets for {p.get('items', 3)} items" if len(lines) < int(p.get("items", 3)) else None
    if item == 4:
        return "contains a table" if _TABLE.search(t) else None
    if item == 5:
        return "contains emoji" if _EMOJI.search(t) else None
    if item == 6:
        return "uses a 12-hour time" if _TWELVE_H.search(t) else None
    if item == 7:
        if _OTHER_DATE.search(t):
            return "a date not written like 'Tue 19 Nov'"
        if p.get("require") and not _DATE_STYLE.search(t):
            return "no date written like 'Tue 19 Nov'"
        return None
    if item == 8:
        return "uses imperial units" if _IMPERIAL.search(t) else None
    if item == 9:
        sign = p.get("sign_off", "")
        return None if sign and t.rstrip().endswith(sign.strip()) else f"does not end with {sign!r}"
    raise ValueError(f"preference item {item} is not a format item (1-9)")


def using_stubs() -> bool:
    return _real_check is None


def check(item: int, text: str, **params) -> str | None:
    if _real_check is not None:
        return _real(item, text, **params)
    params.pop("surface", None)
    why = _stub(item, text, **params)
    return f"{why} [stub checker]" if why else None


def check_items(items, text: str) -> list[str]:
    out = []
    for it in items:
        if isinstance(it, dict):
            params = {k: v for k, v in it.items() if k != "item"}
            why = check(int(it["item"]), text, **params)
            n = it["item"]
        else:
            why = check(int(it), text)
            n = it
        if why:
            out.append(f"preference {n}: {why}")
    return out
