"""Deterministic readability hygiene layer (checks D1-D11, spec 03 s4.2).

Stdlib only, no model, no network. Reuses the shared building blocks in
``textutil``, ``normalize`` and ``checkers`` (D4 is ``checkers.answer_first``).

Every check has the signature ``(text, case, ctx) -> (status, reason)`` with
status one of ``"pass" | "fail" | "n/a"``.

``case`` keys read (all optional unless noted):
    qtype            fact | yesno | set | records | steps | explain | compare |
                     summary | draft | spoken | format | refuse/limit
    profile          "panel" (default) | "window"
    request          the user's request text (D4, D7)
    answer_key       list of anchors (D4); n/a when empty
    case_year        int, passed to the D4 anchor matcher
    reference_words  int, words in the reference answer (D5)
    format_constraints  list of dicts (D10)
    source_text      the source the answer may quote (D8, D11)
    subject_requested   bool, a draft may carry a subject line (D3)

``ctx`` keys: ``active_prefs`` (frozenset of "P1".."P9").
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Callable

from . import checkers as C
from . import textutil as T

HYGIENE_VERSION = "edat-hygiene/1"

HYGIENE_WEIGHTS = {"D1": 1, "D2": 2, "D3": 2, "D4": 2, "D5": 1, "D6": 3,
                   "D7": 1, "D8": 1, "D9": 2, "D10": 2, "D11": 1}

PASS, FAIL, NA = "pass", "fail", "n/a"

_CODE_FENCE = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", re.S)
_INLINE_CODE = re.compile(r"(`+)(?:.+?)\1", re.S)


def _no_code(text: str) -> str:
    return _INLINE_CODE.sub(" ", _CODE_FENCE.sub(" ", text))


# ==========================================================================
# Shape classifier and the qtype table (spec 4.1)
# ==========================================================================

SHAPES = ("sentence", "paragraphs", "bullets", "numbered", "label-list", "mixed")


def _top_items(block) -> list:
    return [it for it in block.items if it.depth == 0]


def classify_shape(text: str) -> str:
    """Label an answer's shape: sentence | paragraphs | bullets | numbered |
    label-list | mixed.

    * ``sentence``: no list and at most two sentences (an empty answer also
      returns this label; callers treat empty text separately).
    * ``paragraphs``: no list, three or more sentences.
    * ``bullets`` / ``numbered``: one kind of list, optionally with one lead-in
      paragraph before it and one short closing paragraph after it (each at
      most two sentences). Nested items do not change the label (D2 charges them).
    * ``label-list``: a bullet list where >= 70 % of top-level items start with
      a bold span followed by ":" or a dash.
    * ``mixed``: anything else (headings, tables, code, quotes, html, rules,
      bullets plus numbered, prose between lists, long prose around a list).
    """
    blocks = T.parse_blocks(text or "")
    if not blocks:
        return "sentence"
    if any(b.kind in ("heading", "table", "code", "quote", "hr", "html") for b in blocks):
        return "mixed"
    lists = [i for i, b in enumerate(blocks) if b.kind in ("bullets", "numbered")]
    if not lists:
        n = sum(len(T.split_sentences(T.strip_markdown(b.text))) for b in blocks)
        return "sentence" if n <= 2 else "paragraphs"
    first, last = lists[0], lists[-1]
    if lists != list(range(first, last + 1)):
        return "mixed"                         # prose between two lists
    kinds = {blocks[i].kind for i in lists}
    if len(kinds) > 1:
        return "mixed"
    if first > 1 or len(blocks) - 1 - last > 1:
        return "mixed"
    for b in blocks[:first] + blocks[last + 1:]:
        if len(T.split_sentences(T.strip_markdown(b.text))) > 2:
            return "mixed"
    kind = kinds.pop()
    if kind == "bullets":
        items = [it for i in lists for it in _top_items(blocks[i])]
        if items and sum(1 for it in items if T.is_bold_label(it.text)) / len(items) >= 0.7:
            return "label-list"
    return kind


# qtype -> (expected, acceptable) in the narrow `panel` profile (spec 4.1).
EXPECTED_SHAPES: dict[str, tuple[frozenset, frozenset]] = {
    "fact": (frozenset({"sentence"}), frozenset()),
    "yesno": (frozenset({"sentence"}), frozenset()),
    "set": (frozenset({"bullets"}), frozenset({"numbered", "label-list"})),
    "records": (frozenset({"label-list"}), frozenset({"bullets"})),
    "steps": (frozenset({"numbered"}), frozenset()),
    "explain": (frozenset({"paragraphs", "sentence"}), frozenset({"bullets", "label-list"})),
    "compare": (frozenset({"bullets", "label-list"}), frozenset({"paragraphs", "sentence"})),
    "summary": (frozenset({"bullets", "label-list"}), frozenset({"paragraphs", "sentence"})),
    "draft": (frozenset({"paragraphs", "sentence"}), frozenset()),
    "spoken": (frozenset({"paragraphs", "sentence"}), frozenset()),
    "refuse/limit": (frozenset({"sentence"}), frozenset()),
}
EXPECTED_SHAPES["refuse_limit"] = EXPECTED_SHAPES["refuse/limit"]

# `window` allows a table (pseudo-label "table", see _d3_label) for these only.
WINDOW_TABLE_QTYPES = frozenset({"compare", "records"})

EXPECTED_SHAPES_WINDOW: dict[str, tuple[frozenset, frozenset]] = {
    q: ((e, a | {"table"}) if q in WINDOW_TABLE_QTYPES else (e, a))
    for q, (e, a) in EXPECTED_SHAPES.items()
}


def expected_shapes(qtype: str, profile: str = "panel", active_prefs=()) -> tuple[frozenset, frozenset] | None:
    """(expected, acceptable) for a qtype, or None when D3 does not apply.

    P4 active: for compare/records the expected shape is the bold-label list
    and the table is never acceptable, in any profile (spec 4.5).
    """
    q = (qtype or "").lower()
    table = EXPECTED_SHAPES_WINDOW if profile == "window" else EXPECTED_SHAPES
    if q not in table:
        return None
    exp, acc = table[q]
    if "P4" in {p.upper() for p in active_prefs} and q in WINDOW_TABLE_QTYPES:
        rest = (exp | acc) - {"label-list", "table"}
        return frozenset({"label-list"}), frozenset(rest)
    return exp, acc


# ==========================================================================
# Markdown helpers
# ==========================================================================

_LINK = re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")


def _runs(text: str, ch: str) -> list[int]:
    """Lengths of delimiter runs of `ch` that can open or close emphasis.

    A run is ignored when both sides are whitespace/edge (a lone "*", "3 * 4"),
    when it is escaped, and for "_" when it sits inside a word (snake_case).
    """
    out = []
    t = text.replace("\\" + ch, "")
    for m in re.finditer(re.escape(ch) + "+", t):
        a, b = m.start(), m.end()
        left = t[a - 1] if a > 0 else " "
        right = t[b] if b < len(t) else " "
        if left.isspace() and right.isspace():
            continue
        if ch == "_" and (left.isalnum() and right.isalnum()):
            continue
        out.append(b - a)
    return out


def _delims_balanced(segment: str) -> str | None:
    """None when balanced, else a short reason. `segment` has no code in it."""
    for ch in ("*", "_"):
        r = _runs(segment, ch)
        if len(r) % 2 or sum(r) % 2:
            return f"unbalanced {ch * 2 if max(r or [0]) > 1 else ch} delimiter"
    return None


def _segments(blocks) -> list[str]:
    segs = []
    for b in blocks:
        if b.kind == "code":
            continue
        if b.kind in ("bullets", "numbered"):
            segs.extend(it.text for it in b.items)
        elif b.kind in ("table",):
            segs.extend(b.lines)
        elif b.kind == "heading":
            segs.extend(b.lines)
        else:
            segs.append(b.text)
    return segs


def d1_valid_markdown(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    if not text.strip():
        return NA, "empty answer"
    blocks = T.parse_blocks(text)
    for b in blocks:
        if b.kind == "code" and not b.closed:
            return FAIL, "unclosed code fence"
    for seg in _segments(blocks):
        seg2 = _INLINE_CODE.sub(" ", seg)
        if "`" in seg2:
            return FAIL, "unbalanced backtick"
        why = _delims_balanced(_LINK.sub(" ", seg2))
        if why:
            return FAIL, why
    for b in blocks:
        if b.kind == "numbered":
            nums = [it.number for it in _top_items(b)]
            if nums and any(nums[i + 1] != nums[i] + 1 for i in range(len(nums) - 1)):
                return FAIL, f"ordered list not consecutive: {nums}"
        if b.kind == "bullets":
            markers = {it.marker for it in _top_items(b)}
            if len(markers) > 1:
                return FAIL, f"mixed bullet markers: {sorted(markers)}"
    return PASS, "markdown is well formed"


def has_markdown(text: str) -> bool:
    """True when the text contains any Markdown at all (draft / spoken rule)."""
    blocks = T.parse_blocks(text)
    if any(b.kind != "paragraph" for b in blocks):
        return True
    t = text
    if "`" in t or _LINK.search(t) or T.has_image(t):
        return True
    if re.search(r"\*\*|__", t):
        return True
    return bool(_runs(t, "*") or _runs(t, "_"))


def d2_renders_in_profile(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    if not text.strip():
        return NA, "empty answer"
    qtype = (case.get("qtype") or "").lower()
    profile = case.get("profile", "panel")
    prefs = ctx["active_prefs"]
    if qtype in ("draft", "spoken"):
        if has_markdown(text):
            return FAIL, f"{qtype} text must carry no Markdown"
        return PASS, "plain text"
    if T.has_html(text):
        return FAIL, "raw HTML"
    blocks = T.parse_blocks(text)
    kinds = {b.kind for b in blocks}
    table_ok = "P4" in prefs                      # P4 charges it, not D2
    if "table" in kinds and not table_ok:
        if profile == "panel":
            return FAIL, "table in the panel profile"
        if qtype not in WINDOW_TABLE_QTYPES:
            return FAIL, f"table in window for qtype {qtype or 'unknown'}"
    if profile == "window":
        return PASS, "renders in window"
    if "heading" in kinds:
        return FAIL, "heading in the panel profile"
    if "hr" in kinds:
        return FAIL, "horizontal rule"
    if T.has_image(text):
        return FAIL, "image"
    for b in blocks:
        if b.kind in ("bullets", "numbered") and any(it.depth > 0 for it in b.items):
            return FAIL, "nested list"
    return PASS, "renders in panel"


# ==========================================================================
# D3 shape
# ==========================================================================

_CLOSING = re.compile(
    r"^\s*(?i:best(?: regards| wishes)?|kind regards|warm regards|regards|sincerely(?: yours)?|"
    r"yours(?: sincerely| truly| faithfully)?|thanks|thank you|many thanks|cheers|take care|"
    r"with thanks|all the best)\s*[,!.]?\s*(?:[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,2})?\s*$")
_PLACEHOLDER = re.compile(
    r"\[[^\]\n]{1,40}\]|<\s*(?:your\s+)?(?:name|recipient|date|sender)\s*>|\{\{?[^}\n]{1,30}\}?\}|_{4,}",
    re.I)
_SUBJECT = re.compile(r"^\s*(?:\*\*)?subject(?:\*\*)?\s*:", re.I | re.M)


def _has_signoff(text: str) -> bool:
    lines = [l for l in text.strip().split("\n") if l.strip()]
    tail = lines[-3:]
    return any(_CLOSING.match(l) and len(l.split()) <= 4 for l in tail)


def _d3_label(text: str, case: dict, ctx: dict) -> str:
    """classify_shape, plus the pseudo-label "table" for a window answer that is
    prose plus table(s) only."""
    label = classify_shape(text)
    if label == "mixed":
        blocks = T.parse_blocks(text)
        if (any(b.kind == "table" for b in blocks)
                and all(b.kind in ("table", "paragraph") for b in blocks)):
            return "table"
    return label


def d3_shape_fits_qtype(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    qtype = (case.get("qtype") or "").lower()
    if not text.strip():
        return NA, "empty answer"
    shapes = expected_shapes(qtype, case.get("profile", "panel"), ctx["active_prefs"])
    if shapes is None:
        return NA, f"no shape rule for qtype {qtype or 'unknown'}"
    exp, acc = shapes
    label = _d3_label(text, case, ctx)
    if qtype == "draft":
        if _PLACEHOLDER.search(text):
            return FAIL, "draft carries a placeholder"
        if "P9" not in ctx["active_prefs"] and _has_signoff(text):
            return FAIL, "draft ends with a sign-off name line"
        if _SUBJECT.search(text) and not case.get("subject_requested"):
            return FAIL, "draft has a subject line nobody asked for"
    if label in exp or label in acc:
        return PASS, f"shape {label} fits {qtype}"
    return FAIL, f"shape {label} not in expected {sorted(exp)} or acceptable {sorted(acc)} for {qtype}"


# ==========================================================================
# D4 answer first
# ==========================================================================

def d4_answer_first(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    key = case.get("answer_key") or []
    if not key:
        return NA, "case has no answer key"
    ok, reason = C.answer_first(text, key, request=case.get("request"), case_year=case.get("case_year"))
    return (PASS if ok else FAIL), reason


# ==========================================================================
# D5 length
# ==========================================================================

def d5_length(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    ref = case.get("reference_words")
    p2 = "P2" in ctx["active_prefs"]
    if not ref and not p2:
        return NA, "no reference answer length"
    wc = len(T.words(text))
    if ref:
        lo, hi = 0.5 * ref, 2.0 * ref
        if wc < lo:
            return FAIL, f"{wc} words < 0.5 x reference ({ref})"
        if wc > hi:
            return FAIL, f"{wc} words > 2.0 x reference ({ref})"
    if p2:
        u = C.sentence_units(text)
        if u > 3:
            return FAIL, f"{u} sentence units > 3 under P2"
    return PASS, f"{wc} words within band" if ref else f"{wc} words, P2 limit kept"


# ==========================================================================
# D6 leaks
# ==========================================================================

# family -> regex patterns searched in the raw answer (case-sensitive unless
# written with (?i)). The structural leaks (empty, stall, raw JSON) are
# checked in code: see STRUCTURAL_LEAKS.
LEAK_FAMILIES: dict[str, list[str]] = {
    "reasoning_tags": [r"(?i)</?think(?:ing)?>", r"(?i)</?reasoning>", r"(?i)</?thought>",
                       r"(?i)</?scratchpad>"],
    "gemma_channel": [r"<\|?channel\|?>", r"<\|channel\|?>", r"<channel\|>", r"<\|think\|>"],
    "tool_markup": [r"<\|tool_call\|?>", r"<tool_call\|?>", r"</tool_call>", r"<\|tool_response\|?>",
                    r"<tool_response>", r"<function_call>", r"<\|python_tag\|>",
                    r"<\|tool_call_start\|>", r"\[TOOL_CALLS\]", r"\bcall:[A-Za-z_][\w.]*\s*\{"],
    "tool_json": [r'\{\s*"(?:name|tool|tool_name|function)"\s*:\s*"[^"\n]+"\s*,\s*"(?:arguments|parameters|args|input)"\s*:',
                  r'\{\s*"(?:arguments|parameters)"\s*:\s*\{[^{}]*\}\s*,\s*"name"\s*:',
                  r'"tool_calls"\s*:\s*\['],
    "planning_prose": [
        r"(?is)\A\W*(?:(?:okay|ok|so|alright|hmm|right|well)\b[,.]?\s+)?(?:"
        r"the user (?:wants|is asking|asks|asked|needs|said|would like|has asked)|"
        r"we need to|we should|we have to|we must|"
        r"let me (?:check|look|see|think|search|find|pull|get|try|verify|call|start|figure)|"
        r"let's (?:check|see|look|call|start|figure)|"
        r"i will now|i'll now|i am going to (?:call|check|look|use)|i'm going to (?:call|check|look|use)|"
        r"i need to (?:call|check|use|look|find|search|figure)|i should (?:call|check|use|look)|"
        r"first,? (?:i|let me|we) )"],
    "template_gemma": [r"<end_of_turn>", r"<start_of_turn>", r"<bos>", r"<eos>", r"<\|turn\>", r"<turn\|>"],
    "template_chatml": [r"<\|im_start\|>", r"<\|im_end\|>", r"<\|endoftext\|>", r"<\|startoftext\|>",
                        r"<\|object_ref_start\|>"],
    "template_llama": [r"<\|eot_id\|>", r"<\|start_header_id\|>", r"<\|end_header_id\|>",
                       r"<\|begin_of_text\|>", r"<\|eom_id\|>", r"<<SYS>>", r"<</SYS>>"],
    "template_phi": [r"<\|end\|>", r"<\|assistant\|>", r"<\|user\|>", r"<\|system\|>"],
    "template_mistral": [r"(?i)\[/?INST\]", r"</s>", r"(?<![\w/])<s>", r"\[/?SYSTEM_PROMPT\]",
                         r"\[AVAILABLE_TOOLS\]"],
    "template_granite": [r"<\|start_of_role\|>", r"<\|end_of_role\|>", r"<\|end_of_text\|>"],
    "template_deepseek": [r"<[｜|](?:end|begin)[▁_ ]of[▁_ ]sentence[｜|]", r"<[｜|](?:User|Assistant)[｜|]",
                          r"<[｜|]fim[▁_ ](?:begin|hole|end)[｜|]"],
    "escaped_text": [r"(?<!\\)\\n", r"(?<!\\)\\t", r'\\"', r"\\u[0-9a-fA-F]{4}"],
}
LEAK_FAMILIES_COMPILED = {f: [re.compile(p) for p in ps] for f, ps in LEAK_FAMILIES.items()}
STRUCTURAL_LEAKS = ("empty", "stall", "raw_json")

_STALL = re.compile(
    r"(?i)^\W*(?:one moment|just a moment|a moment|hold on|give me a (?:moment|second|sec|minute)|"
    r"please wait|bear with me|let me (?:just )?(?:check|look|see|find|pull up|get|search|verify)|"
    r"checking (?:that|now|on that)|looking (?:that|this|it) up)")
_RAW_JSON_START = re.compile(r'^\s*[\[{]\s*"[^"\n]+"\s*:')


def _first_line_text(t: str) -> str:
    return t.lstrip()


def _json_value(s: str):
    s = s.strip()
    m = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", s, re.S | re.I)
    if m:
        s = m.group(1).strip()
    try:
        v = json.loads(s)
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, (dict, list)) else None


def _wants_json(case: dict) -> bool:
    return any(c.get("kind") == "json" for c in (case.get("format_constraints") or []))


def d6_no_leaks(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    raw = text or ""
    vis, _ = T.visible_answer(raw)
    found: list[str] = []
    if not vis.strip() or not re.sub(r"[\s​‌‍﻿]", "", vis):
        found.append("empty answer")
    scan = _no_code(raw)
    for fam, rxs in LEAK_FAMILIES_COMPILED.items():
        if fam == "escaped_text":
            target = scan
        elif fam == "planning_prose":
            target = re.sub(r"^[\s>*_#`\"']+", "", vis)
        else:
            target = raw
        if any(rx.search(target) for rx in rxs):
            found.append(fam)
    if vis.strip() and not _wants_json(case):
        if _json_value(vis) is not None or (_RAW_JSON_START.match(vis) and vis.rstrip()[-1:] in "}]"):
            found.append("raw_json")
    if vis.strip() and "empty answer" not in found:
        body = re.sub(r"\s+", " ", vis.strip())
        sents = T.split_sentences(body)
        if sents and _STALL.search(sents[-1]) and len(T.words(sents[-1])) <= 14:
            found.append("stall")
    if found:
        return FAIL, "leak: " + ", ".join(dict.fromkeys(found))
    return PASS, "no leaks"


# ==========================================================================
# D7 filler
# ==========================================================================

def d7_no_filler(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    if not text.strip():
        return NA, "empty answer"
    blocks = T.parse_blocks(text)
    sents = T.split_sentences(T.strip_markdown(text))
    if not sents:
        return NA, "no sentences"
    if blocks and blocks[0].kind == "paragraph":
        first = T.split_sentences(blocks[0].text)[0]
        if T.is_filler_opener(first):
            return FAIL, f"filler opener: {first[:40]!r}"
        if T.restates_request(first, case.get("request")):
            return FAIL, "first sentence restates the request"
    elif blocks and T.is_filler_opener(T.strip_markdown(blocks[0].text)):
        return FAIL, "filler opener"
    for s in sents[-2:]:
        if T.is_filler_closer(s):
            return FAIL, f"filler closer: {s[:40]!r}"
    return PASS, "no filler"


# ==========================================================================
# D8 decoration
# ==========================================================================

_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_CAPS = re.compile(r"\b[A-Z]{2,}\b")
_SHOUT = {"NOW", "NOT", "NO", "YES", "ALL", "DO", "OR", "AND", "THE", "YOU", "YOUR", "ONLY", "MUST",
          "NEVER", "ALWAYS", "STOP", "URGENT", "WARNING", "IMPORTANT", "NOTE", "FREE", "WOW", "OK"}
_ACRONYM_OK = {"HTML", "JSON", "HTTP", "HTTPS", "IBAN", "SMTP", "IMAP", "WIFI", "OLED", "ASAP",
               "NASA", "UNESCO", "SQL", "GPU", "USB", "PDF", "FAQ", "VPN", "DNS", "TODO", "NATO", "SWIFT"}


def d8_restrained_decoration(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    if not text.strip():
        return NA, "empty answer"
    qtype = (case.get("qtype") or "").lower()
    prefs = ctx["active_prefs"]
    wc = max(len(T.words(text)), 1)
    plain = _no_code(text)
    problems = []
    if "P5" not in prefs:
        n = len(T.find_emoji(plain))
        limit = 0.0 if qtype in ("draft", "spoken") else wc / 100.0
        if n > limit:
            problems.append(f"{n} emoji in {wc} words")
    shape = classify_shape(text)
    spans = len(_BOLD.findall(plain))
    if shape == "label-list":
        for b in T.parse_blocks(text):
            if b.kind in ("bullets", "numbered"):
                spans -= sum(1 for it in _top_items(b) if T.is_bold_label(it.text))
    if spans > wc / 15.0 and spans > 0:
        problems.append(f"{spans} bold spans in {wc} words")
    if qtype != "draft":
        bangs = len(re.findall(r"!(?!\[|=)", plain))
        if bangs > 1:
            problems.append(f"{bangs} exclamation marks")
    allowed_ctx = " ".join(str(case.get(k) or "") for k in ("request", "source_text"))
    allowed_ctx += " " + " ".join(str(a) for a in (case.get("answer_key") or []))
    allowed = set(_CAPS.findall(allowed_ctx))
    plain2 = re.sub(r"https?://\S+|\b[\w.+-]+@[\w.-]+\b", " ", plain)
    shouted = []
    for w in _CAPS.findall(plain2):
        if w in allowed or w in _ACRONYM_OK:
            continue
        if len(w) >= 4 or w in _SHOUT:
            shouted.append(w)
    if shouted:
        problems.append("all-caps: " + ", ".join(dict.fromkeys(shouted)))
    if problems:
        return FAIL, "; ".join(problems)
    return PASS, "restrained"


# ==========================================================================
# D9 repetition
# ==========================================================================

def _norm_sentence(s: str) -> str:
    return re.sub(r"[^\w\s]", "", T.fold(T.strip_markdown(s))).strip()


def d9_no_repetition(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    if not text.strip():
        return NA, "empty answer"
    body = _CODE_FENCE.sub(" ", text)
    seen: dict[str, int] = {}
    for s in T.split_sentences(T.strip_markdown(body)):
        k = _norm_sentence(s)
        if len(k.split()) >= 3:
            seen[k] = seen.get(k, 0) + 1
    dup = [k for k, v in seen.items() if v > 1]
    if dup:
        return FAIL, f"sentence repeated: {dup[0][:40]!r}"
    lines: dict[str, int] = {}
    for ln in body.split("\n"):
        k = _norm_sentence(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", ln))
        if k:
            lines[k] = lines.get(k, 0) + 1
    loop = [k for k, v in lines.items() if v >= 3]
    if loop:
        return FAIL, f"line repeated 3+ times: {loop[0][:40]!r}"
    toks = [T.fold(w) for w in T.words(body)]
    if len(toks) >= 30:
        grams = [tuple(toks[i:i + 4]) for i in range(len(toks) - 3)]
        rate = (len(grams) - len(set(grams))) / len(grams)
        if rate > 0.05:
            return FAIL, f"repeated 4-gram rate {rate:.1%} > 5%"
    return PASS, "no repetition"


# ==========================================================================
# D10 explicit format constraints (IFEval-style)
# ==========================================================================

def _items(text: str) -> list[str]:
    blocks = T.parse_blocks(text)
    items = [it.text for b in blocks if b.kind in ("bullets", "numbered") for it in _top_items(b)]
    if items:
        return items
    return [l.strip() for l in text.split("\n") if l.strip()]


def _count_ok(actual: int, n: int, mode: str) -> bool:
    return {"exact": actual == n, "at_most": actual <= n, "at_least": actual >= n}.get(mode, actual == n)


def _check_constraint(text: str, c: dict) -> str | None:
    """None when satisfied, else a reason."""
    kind = c.get("kind")
    n = c.get("n")
    mode = c.get("mode", "exact")
    blocks = T.parse_blocks(text)
    if kind == "item_count":
        k = len(_items(text))
        return None if _count_ok(k, n, mode) else f"{k} items, wanted {mode} {n}"
    if kind == "sentence_count":
        k = len(T.split_sentences(T.strip_markdown(text)))
        return None if _count_ok(k, n, mode) else f"{k} sentences, wanted {mode} {n}"
    if kind == "max_words_per_item":
        over = [i for i in _items(text) if len(T.words(i)) > n]
        return None if not over else f"item over {n} words: {over[0][:30]!r}"
    if kind == "max_words":
        k = len(T.words(text))
        return None if k <= n else f"{k} words > {n}"
    if kind == "no_intro":
        if not blocks or blocks[0].kind not in ("bullets", "numbered"):
            return "text before the first list item"
        return None
    if kind == "no_outro":
        if not blocks or blocks[-1].kind not in ("bullets", "numbered"):
            return "text after the last list item"
        return None
    if kind == "one_line":
        return None if "\n" not in text.strip() else "more than one line"
    if kind == "json":
        v = _json_value(text)
        if v is None:
            return "not valid JSON"
        keys = c.get("keys")
        if keys and not (isinstance(v, dict) and all(k in v for k in keys)):
            return f"JSON lacks keys {keys}"
        return None
    if kind == "numbered":
        lists = [b for b in blocks if b.kind in ("bullets", "numbered")]
        if not lists or any(b.kind != "numbered" for b in lists):
            return "not a numbered list"
        return None
    if kind == "no_list":
        return None if not any(b.kind in ("bullets", "numbered") for b in blocks) else "contains a list"
    if kind == "no_markdown":
        return None if not has_markdown(text) else "contains Markdown"
    return f"unknown constraint kind {kind!r}"


def d10_format_followed(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    cons = case.get("format_constraints") or []
    if not cons:
        return NA, "no countable constraints"
    if not text.strip():
        return FAIL, "empty answer"
    bad = []
    for c in cons:
        why = _check_constraint(text, c)
        if why:
            bad.append(f"{c.get('kind')}: {why}")
    if bad:
        return FAIL, "; ".join(bad)
    return PASS, f"{len(cons)} constraint(s) met"


# ==========================================================================
# D11 English answer
# ==========================================================================
# Heuristic (version 1): a sentence is foreign when
#  (a) more than half of its letters are outside the Latin script (Cyrillic,
#      CJK, Arabic, ...), at least 4 letters; or
#  (b) it has >= 5 words, >= 3 French/Spanish/German/Italian/Portuguese/Dutch
#      stopwords that are not English words, those make up >= 25 % of its words
#      and exceed the English stopword hits.
# Sentences (or quoted spans) that appear verbatim in `case["source_text"]`
# are exempt, as are code and URLs.

_EN_STOP = set("""the a an is are was were be been to of and or in on at for with by from as it its this that
these those i you he she we they my your his her our their not no do does did have has had will would can could
should may might what when where who how which there here if then than so but about into over after before just
also all any some more most""".split())
_FOREIGN_STOP = set("""le la les des une un est et du que qui pour dans pas sur avec vous nous je il elle ce sont
au aux ne ou mais sont très comme
el los las una es y del por para con se su pero muy como más está
der die das und ist nicht ein eine mit von zu den dem auf für sich auch wir sie ich es
il lo gli che di per con non sono della del una nel
não uma os as do da em com um são você
het een van de en op te voor niet zijn maar""".split()) - _EN_STOP - {"die", "es", "me", "se", "ne", "un", "una", "con", "per", "para", "sur", "del", "est"} | {
    "est", "para", "per", "des", "und", "nicht", "pas", "una", "con", "del", "sur", "der", "das", "ist"}
_SCRIPT_CACHE: dict[str, bool] = {}


def _non_latin(ch: str) -> bool:
    v = _SCRIPT_CACHE.get(ch)
    if v is None:
        try:
            v = ch.isalpha() and not unicodedata.name(ch).startswith("LATIN")
        except ValueError:
            v = False
        _SCRIPT_CACHE[ch] = v
    return v


_QUOTED = re.compile(r"\"([^\"\n]{2,})\"|“([^”\n]{2,})”|«([^»\n]{2,})»")


def d11_english(text: str, case: dict, ctx: dict) -> tuple[str, str]:
    if not text.strip():
        return NA, "empty answer"
    src = T.fold(case.get("source_text") or "")
    body = re.sub(r"https?://\S+|\b[\w.+-]+@[\w.-]+\b", " ", _no_code(text))

    def quoted(m):
        inner = next(g for g in m.groups() if g)
        return " " if src and T.fold(inner) in src else m.group(0)

    body = _QUOTED.sub(quoted, body)
    for s in T.split_sentences(body):
        letters = [c for c in s if c.isalpha()]
        if len(letters) < 4:
            continue
        if src and T.fold(T.strip_markdown(s)).strip(" .!?\"'") in src:
            continue
        nl = sum(1 for c in letters if _non_latin(c))
        if nl / len(letters) > 0.5:
            return FAIL, f"sentence in another script: {s[:30]!r}"
        toks = [w.lower() for w in re.findall(r"[^\W\d_]+", s)]
        if len(toks) >= 5:
            fr = sum(1 for w in toks if w in _FOREIGN_STOP)
            en = sum(1 for w in toks if w in _EN_STOP)
            if fr >= 3 and fr / len(toks) >= 0.25 and fr > en:
                return FAIL, f"sentence looks non-English: {s[:30]!r}"
    return PASS, "English"


# ==========================================================================
# Registry, scoring
# ==========================================================================

CHECKS: dict[str, Callable[[str, dict, dict], tuple[str, str]]] = {
    "D1": d1_valid_markdown, "D2": d2_renders_in_profile, "D3": d3_shape_fits_qtype,
    "D4": d4_answer_first, "D5": d5_length, "D6": d6_no_leaks, "D7": d7_no_filler,
    "D8": d8_restrained_decoration, "D9": d9_no_repetition, "D10": d10_format_followed,
    "D11": d11_english,
}
_RAW_CHECKS = {"D6"}           # D6 sees the raw answer; the others see the visible one
_EMPTY_NA = {"D1", "D2", "D3", "D7", "D8", "D9", "D11"}


def hygiene(text: str, case: dict | None = None, *, active_prefs=()) -> dict:
    """Run D1-D11 and combine them.

    Returns ``{"checks": {id: {"status", "reason"}}, "score": 0-100,
    "leak": bool, "dropped": [ids], "shape": label}``.

    Conflict rules (spec 4.5): P1 drops D4 (removed from ``checks``, listed in
    ``dropped``); P5 drops D8's emoji clause; P4 makes label-list the expected
    compare/records shape and D2 stops charging a table; P2 adds the
    three-unit limit to D5; P9 turns off the draft sign-off clause.
    The score is the weight-weighted pass rate over the checks that are
    neither dropped nor n/a. An empty answer scores 0.
    """
    case = dict(case or {})
    raw = text or ""
    prefs = frozenset(p.upper() for p in active_prefs)
    ctx = {"active_prefs": prefs}
    visible, _ = T.visible_answer(raw)
    dropped = ["D4"] if "P1" in prefs else []
    checks: dict[str, dict] = {}
    for cid, fn in CHECKS.items():
        if cid in dropped:
            continue
        subject = raw if cid in _RAW_CHECKS else visible
        status, reason = fn(subject, case, ctx)
        checks[cid] = {"status": status, "reason": reason}
    got = sum(HYGIENE_WEIGHTS[c] for c, r in checks.items() if r["status"] == PASS)
    tot = sum(HYGIENE_WEIGHTS[c] for c, r in checks.items() if r["status"] != NA)
    score = round(100.0 * got / tot, 1) if tot else 0.0
    if not visible.strip():
        score = 0.0
    return {"checks": checks, "score": score, "leak": checks["D6"]["status"] == FAIL,
            "dropped": dropped, "shape": classify_shape(visible), "version": HYGIENE_VERSION}


# ==========================================================================
# Prose proxies (spec 5): reported, never scored
# ==========================================================================

_VAGUE = ["various", "a number of", "some things", "several things", "many things", "a variety of",
          "a lot of", "stuff", "things like that", "and so on", "and more", "etc", "somehow", "in some way",
          "a few things", "certain things", "numerous", "a range of"]
_HEDGES = ["might", "may", "perhaps", "possibly", "probably", "maybe", "i think", "i believe", "it seems",
           "it appears", "appears to", "seems to", "could", "likely", "somewhat", "generally", "typically",
           "sort of", "kind of", "not sure", "as far as i know", "tends to"]


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(n, 1)


def _grams(s: str) -> set[str]:
    t = re.sub(r"\s+", " ", T.fold(s))
    return {t[i:i + 4] for i in range(len(t) - 3)}


def _count_phrases(text: str, phrases) -> int:
    t = T.fold(text)
    return sum(len(re.findall(r"(?<!\w)" + re.escape(p) + r"(?!\w)", t)) for p in phrases)


def prose_proxies(text: str) -> dict:
    """Diagnostics only: sentence-length stats, Flesch, vague phrases,
    redundancy (max pairwise char-4-gram Jaccard) and hedge density."""
    vis, _ = T.visible_answer(text or "")
    body = _CODE_FENCE.sub(" ", vis)
    sents = T.split_sentences(T.strip_markdown(body))
    lens = [len(T.words(s)) for s in sents if T.words(s)]
    ws = T.words(body)
    out = {"sentences": len(lens), "words": len(ws), "mean_sentence_words": 0.0, "p90_sentence_words": 0.0,
           "share_over_30_words": 0.0, "flesch_reading_ease": None, "vague_phrases": 0,
           "redundancy": 0.0, "hedges_per_100_words": 0.0}
    if not lens:
        return out
    srt = sorted(lens)
    out["mean_sentence_words"] = round(sum(lens) / len(lens), 2)
    out["p90_sentence_words"] = float(srt[min(len(srt) - 1, max(0, math.ceil(0.9 * len(srt)) - 1))])
    out["share_over_30_words"] = round(sum(1 for x in lens if x > 30) / len(lens), 3)
    syl = sum(_syllables(w) for w in ws)
    out["flesch_reading_ease"] = round(206.835 - 1.015 * (len(ws) / len(lens)) - 84.6 * (syl / max(len(ws), 1)), 1)
    out["vague_phrases"] = _count_phrases(body, _VAGUE)
    gs = [_grams(s) for s in sents]
    best = 0.0
    for i in range(len(gs)):
        for j in range(i + 1, len(gs)):
            if gs[i] and gs[j]:
                best = max(best, len(gs[i] & gs[j]) / len(gs[i] | gs[j]))
    out["redundancy"] = round(best, 3)
    out["hedges_per_100_words"] = round(100.0 * _count_phrases(body, _HEDGES) / max(len(ws), 1), 2)
    return out
