"""Deterministic Writing checks WD1-WD11 and the case score (spec 03 section 9.3, WRITING.md).

`score_writing(draft, case, *, outline=None, grammar=None)` is the entry point. Everything is
stdlib and offline. The rules follow WRITING.md; the places where it left room are decided
here and listed below.

Decisions where WRITING.md was ambiguous
----------------------------------------
* WD1 and WD3 read the whole draft except the W4 "Questions:" block (so an invitation title or
  a subject line can carry a point). WD3 passes when no anchor of any `must_not` entry appears
  outside a line that starts with ">" (any one anchor of an entry is a violation).
* WD2 allowed atoms: every request turn, source text, thread texts and senders, samples,
  `allowed_text`, sender and recipient names, greeting and sign-off, `edit.gold`, the point,
  fact and anchor texts, the case year and today's date. The cache key is (case_id, hash of
  that text). Sentence-initial capitals, weekdays and months are handled by
  `normalize.find_names` and `normalize.atom_keys`, as in the summarising scorers.
* WD4: names are the sender (full and preferred), the recipient, and capitalised text anchors
  of points and facts. Each name word of three or more letters is compared on a case- and
  diacritic-folded basis; a folded-equal token that is not byte-identical fails. A lowercase
  token that is also a common word ("will", "mark") is not flagged. P6, P7, P8 are run when
  they are in `active_prefs` (Meta from `pref_meta`, plus case_year and the source text).
* WD5: sentence count is taken on the body with the greeting and sign-off removed. The
  sign-off is `constraints.signoff` ("profile" means `writing.PROFILE_SIGNOFF[profile]`);
  it is checked with P9 on every surface (P9 itself is email-scoped). Any placeholder-looking
  token fails unless it also appears in the source text. Other active preferences (P4, P5,
  P1-P3 when listed) are run through `checkers.check`. `pref_meta["one_off"]` supports
  no_exclamation, all_lowercase, start_with_date, no_emoji, no_contractions, no_question_marks,
  end_with_question, single_line, one_paragraph; unknown names are reported as n/a inside the
  WD5 result and do not fail it. For W7 title cases the body is empty, so word limits on the
  body are not applied; title and subject limits are.
* WD6 (W3): "no new atom" reuses the WD2 computation (same allowed set: request, source text,
  facts), and the form share excludes WD2 for W3 so it is not charged twice. For shorten, the
  length limit is WD5's. W3 has no WD1, so `gate_pass` uses `fact_keep_ok` in its place.
* WD7: D7 and D9 run on the body without greeting; a preamble is a first line such as
  "Here's a draft:" and a closing offer is a last paragraph offering revisions, only under
  `output_only`. D7's own filler closers ("Let me know if ...") still apply, as the spec says.
* WD8: `fix_rate` counts an error only when the edited text is still recognisably the source
  (edit distance to gold at most half of the gold length), so an unrelated text cannot earn
  fixes by accident. A planted error is fixed when its `wrong` form is gone (word-bounded) and
  its `right` form or an alternative is present. The ambiguous check needs the span unchanged
  and the key word in the Questions block; `flag_rate` is the share satisfying both.
* WD9: contractions allowed = max(1, floor(words / 50)). "Sincerely" and "Dear" are matched
  case-sensitively (a mid-sentence "sincerely" is fine). Slang list is `SLANG`.
* WD10 is always `n/a`; `voice_delta` is None without samples or `pref_meta["neutral_reference"]`.
* WD11: a draft token is a spelling variant of an entity word when it is folded-equal but not
  byte-equal, or (entity word of five or more letters, token capitalised like the entity)
  one edit away. Tokens that appear byte-for-byte in the request are never variants. Figures
  (three or more digits) fail on the same digits written differently, or one digit changed.
  Outline cases without an `outline` argument are ordered by the brief's point order.
* Score: content x form. Content is `point_recall` (W1, W2, W5, W6, W7), `fact_keep_rate` (W3)
  or the mean of `fix_rate` and `flag_rate` where defined (W4). Form is the share of applicable
  checks that pass among WD2-WD9 and WD11 (WD1 is content, WD10 never counts; WD2 is left
  out for W3). WD8 stays in W4's form so overedit and protected spans carry weight.
"""
from __future__ import annotations

import math
import re
import unicodedata
from decimal import Decimal

from . import checkers as C
from . import hygiene as H
from . import normalize as N
from . import textutil as T
from . import writing as W

WRITING_CHECKS_VERSION = "edat-writing-checks/1"

PASS, FAIL, NA = "pass", "fail", "n/a"

SLANG = frozenset({"gonna", "wanna", "kinda", "sorta", "gotta", "yeah", "yep", "nope", "hey", "lol", "btw",
                   "omg", "u", "ya", "cool", "awesome", "super", "dude", "stuff", "guys"})

_ONE_OFF_RULES = ("no_exclamation", "all_lowercase", "start_with_date", "no_emoji", "no_contractions",
                  "no_question_marks", "end_with_question", "single_line", "one_paragraph")

_META_FIELDS = set(C.Meta.__dataclass_fields__)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _res(status: str, reason: str = "", **extra) -> dict:
    d = {"status": status, "reason": reason}
    d.update(extra)
    return d


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _fold_name(s: str) -> str:
    return _strip_accents(T.fold(s))


def lev(a: str, b: str) -> int:
    """Plain Levenshtein distance on characters, two rows, common prefix and suffix trimmed first."""
    if a == b:
        return 0
    i = 0
    n = min(len(a), len(b))
    while i < n and a[i] == b[i]:
        i += 1
    a, b = a[i:], b[i:]
    j = 0
    n = min(len(a), len(b))
    while j < n and a[-1 - j] == b[-1 - j]:
        j += 1
    if j:
        a, b = a[:len(a) - j], b[:len(b) - j]
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _bounded(text: str, s: str) -> bool:
    return bool(s) and re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", text) is not None


def _anchor_hit(text: str, anchor, year: int | None) -> bool:
    t, v, al = N.parse_anchor(anchor)
    return N.anchor_present(text, t, v, aliases=al, case_year=year)


def _anchor_first_pos(text: str, anchor, year: int | None) -> int | None:
    t, v, al = N.parse_anchor(anchor)
    sp = N.anchor_spans(text, t, v, aliases=al, case_year=year)
    return min(s for s, _ in sp) if sp else None


def _signoff(case: dict) -> str | None:
    so = case["constraints"].get("signoff") or (case.get("pref_meta") or {}).get("signoff")
    if so == "profile":
        so = W.PROFILE_SIGNOFF.get(case.get("profile") or "")
    return so or None


def _pref_surface(case: dict) -> str:
    return {"email": "draft_email", "message": "draft_message"}.get(case.get("surface"), "answer")


def _meta(case: dict) -> C.Meta:
    pm = {k: v for k, v in (case.get("pref_meta") or {}).items() if k in _META_FIELDS}
    pm.setdefault("case_year", case.get("case_year"))
    if "source_text" not in pm:
        pm["source_text"] = (case.get("context") or {}).get("source_text")
    pm["signoff"] = _signoff(case)
    return C.Meta(**pm)


_LABEL = re.compile(r"^\s*(subject|title|description)\s*:\s*(.*)$", re.I)
_QUESTIONS = re.compile(r"^\s*(?:\*\*)?questions(?:\*\*)?\s*:\s*(.*)$", re.I)
_GREETING_LINE = re.compile(r"^\s*(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b[^\n]{0,40}[,!]\s*$", re.I)
_CLOSER = re.compile(r"^\s*(?:best(?: regards| wishes)?|kind regards|warm regards|regards|sincerely(?: yours)?|"
                     r"yours(?: sincerely| truly| faithfully)?|thanks|thank you|many thanks|cheers|take care|"
                     r"with thanks|all the best)\s*[,!.]?\s*$", re.I)
_CLOSER_NAME = re.compile(r"^\s*(?:best|kind regards|warm regards|regards|sincerely|thanks|thank you|many thanks|"
                          r"cheers|take care|all the best)\s*,\s*[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,2}\s*$")


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", T.nfkc(s)).strip()


def _strip_greeting(body: str, case: dict) -> str:
    g = case["constraints"].get("greeting")
    b = body.lstrip()
    if g and b.startswith(g):
        return b[len(g):].lstrip(" \t")
    first, _, rest = b.partition("\n")
    if _GREETING_LINE.match(first) and len(first.split()) <= 6:
        return rest.lstrip("\n")
    return b


def _strip_signoff(body: str, case: dict) -> str:
    lines = body.rstrip().split("\n")
    so = _signoff(case)
    if so:
        want = [_norm_ws(x) for x in so.split("\n") if x.strip()]
        have = [_norm_ws(x) for x in lines if x.strip()]
        if want and len(have) >= len(want) and have[-len(want):] == want:
            kept = [x for x in lines if x.strip()]
            return "\n".join(kept[:len(kept) - len(want)]).rstrip()
        return body.rstrip()
    ne = [x for x in lines if x.strip()]
    if len(ne) >= 2 and _CLOSER.match(ne[-2]) and len(ne[-1].split()) <= 3:
        return "\n".join(ne[:-2]).rstrip()
    if ne and (_CLOSER.match(ne[-1]) or _CLOSER_NAME.match(ne[-1])):
        return "\n".join(ne[:-1]).rstrip()
    return body.rstrip()


def parse_draft(draft: str, case: dict) -> dict:
    """Split a draft into subject, title, description, body and questions (WRITING.md layout)."""
    raw = (draft or "").replace("\r\n", "\n").replace("\r", "\n")
    text = raw.strip()
    lines = text.split("\n") if text else []
    questions: list[str] = []
    has_q = case["constraints"].get("questions_list") or case.get("sub") == "W4"
    main = lines
    if has_q:
        for i, ln in enumerate(lines):
            m = _QUESTIONS.match(ln)
            if m:
                main = lines[:i]
                first = m.group(1).strip()
                if first:
                    questions.append(first)
                for q in lines[i + 1:]:
                    q = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", q).strip()
                    if q:
                        questions.append(q)
                break
    subject = title = description = None
    header: list[str] = []
    i = 0
    n = len(main)
    while i < n:
        if not main[i].strip():
            i += 1
            continue
        m = _LABEL.match(main[i])
        if not m:
            break
        lab = m.group(1).lower()
        if lab in header:
            break
        header.append(lab)
        val = m.group(2).strip()
        if lab == "subject":
            subject = val
        elif lab == "title":
            title = val
        else:
            description = "\n".join([val] + main[i + 1:]).strip()
            i = n
            break
        i += 1
    rest = "\n".join(main[i:]).strip()
    if case.get("surface") == "invite" and description is not None:
        body = description
    else:
        body = rest
    content = "\n".join(main).strip()
    core = _strip_signoff(_strip_greeting(body, case), case) if body else ""
    return {"raw": raw, "subject": subject, "title": title, "description": description, "body": body,
            "questions": questions, "header": header, "content": content, "core": core,
            "words": len(body.split())}


# --------------------------------------------------------------------------
# WD1 points made
# --------------------------------------------------------------------------

def _anchored_points(case: dict) -> list[dict]:
    return [p for p in case["points"] if p.get("kind") == "anchored" and p.get("anchors")]


def wd1(parsed: dict, case: dict) -> dict:
    pts = _anchored_points(case)
    if not pts:
        return _res(NA, "no anchored points", point_recall=None, points_anchored=0, points_made=0)
    yr = case.get("case_year")
    text = parsed["content"]
    missing = [p["id"] for p in pts if not all(_anchor_hit(text, a, yr) for a in p["anchors"])]
    made = len(pts) - len(missing)
    return _res(PASS if not missing else FAIL, "all points made" if not missing else "missing " + ", ".join(missing),
                point_recall=made / len(pts), points_anchored=len(pts), points_made=made, missing=missing)


# --------------------------------------------------------------------------
# WD2 nothing invented
# --------------------------------------------------------------------------

_ALLOWED_CACHE: dict[tuple, frozenset] = {}


def _anchor_texts(anchors) -> list[str]:
    out = []
    for a in anchors:
        out.append(str(a["value"]) if isinstance(a, dict) else str(a))
    return out


def _allowed_text(case: dict) -> str:
    ctx = case.get("context") or {}
    parts: list[str] = list(case.get("turns") or [])
    parts.append(ctx.get("source_text") or "")
    for m in ctx.get("thread") or []:
        parts += [m.get("text", ""), m.get("from", "")]
    parts += ctx.get("samples") or []
    parts.append(case.get("allowed_text") or "")
    s = case.get("sender") or {}
    parts += [s.get("name", ""), s.get("preferred_name", "")]
    r = case.get("recipient") or {}
    parts.append(r.get("name", ""))
    c = case["constraints"]
    parts += [c.get("greeting") or "", _signoff(case) or ""]
    if case.get("edit"):
        parts.append(case["edit"].get("gold", ""))
    for p in case["points"]:
        parts.append(p.get("text", ""))
        parts += _anchor_texts(p.get("anchors", []))
    for f in case.get("facts") or []:
        parts.append(f.get("text", ""))
        parts += _anchor_texts(f.get("anchors", []))
    for f in case["must_not"]:
        if f.get("stated"):
            parts.append(f.get("text", ""))
    return "\n".join(x for x in parts if x)


def allowed_keys(case: dict) -> frozenset:
    text = _allowed_text(case)
    ck = (case.get("case_id"), hash(text))
    hit = _ALLOWED_CACHE.get(ck)
    if hit is not None:
        return hit
    keys = set(N.atom_keys(text))
    yr = case.get("case_year")
    if yr:
        keys.add(("num", Decimal(int(yr))))
    try:
        import datetime as dt
        today = dt.date.fromisoformat(case["today"])
        keys |= {("date", today.month, today.day), ("num", Decimal(today.day))}
    except (KeyError, ValueError):
        pass
    for k in list(keys):
        if k[0] == "date":
            keys.add(("dayonly", k[2]))
        elif k[0] == "num":
            try:
                keys.add(("num", Decimal(k[1]).normalize()))
            except Exception:                                # noqa: BLE001
                pass
    out = frozenset(keys)
    if len(_ALLOWED_CACHE) > 4096:
        _ALLOWED_CACHE.clear()
    _ALLOWED_CACHE[ck] = out
    return out


def invented_atoms(text: str, case: dict) -> tuple[list, list]:
    allowed = allowed_keys(case)
    atoms = N.find_atoms(text)
    inv = []
    for a in atoms:
        k = N.atom_key(a)
        if k[0] == "num":
            k = ("num", k[1].normalize())
        if k in allowed:
            continue
        if a.kind == "name" and ("name", a.value) in allowed:
            continue
        if a.kind == "time" and a.value is None:
            continue
        inv.append(a)
    return atoms, inv


def wd2(parsed: dict, case: dict) -> dict:
    atoms, inv = invented_atoms(parsed["raw"].strip(), case)
    rate = (len(inv) / len(atoms)) if atoms else 0.0
    return _res(PASS if not inv else FAIL,
                "no invented atoms" if not inv else "invented: " + ", ".join(a.surface for a in inv[:5]),
                atoms=len(atoms), invented_atoms=len(inv), invented_rate=rate,
                invented=[a.surface for a in inv[:10]])


# --------------------------------------------------------------------------
# WD3 forbidden content
# --------------------------------------------------------------------------

def wd3(parsed: dict, case: dict) -> dict:
    if not case["must_not"]:
        return _res(NA, "no forbidden content", forbidden_rate=None)
    text = "\n".join(ln for ln in parsed["raw"].split("\n") if not ln.lstrip().startswith(">"))
    yr = case.get("case_year")
    hit = [f["id"] for f in case["must_not"] if any(_anchor_hit(text, a, yr) for a in f["anchors"])]
    return _res(PASS if not hit else FAIL, "none present" if not hit else "present: " + ", ".join(hit),
                forbidden_rate=len(hit) / len(case["must_not"]), hits=hit)


# --------------------------------------------------------------------------
# WD4 exact details
# --------------------------------------------------------------------------

_COMMON_LOWER = frozenset({"will", "mark", "rose", "grace", "may", "bill", "dawn", "hope", "art", "joy", "faith",
                           "summer", "sue", "ray", "jack", "pat", "frank", "earl", "bob", "don", "gene", "rob",
                           "guy", "case", "page", "sage", "reed", "wade", "cliff", "brook", "lane", "ward",
                           "hunter", "miles", "chase", "drew", "rich", "sandy", "wen", "ling", "may"})
_NAME_RX = re.compile(r"[A-Z][\w'’-]+(?: [A-Z][\w'’-]+){0,2}")
_TOKEN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")


def brief_names(case: dict) -> list[str]:
    names: list[str] = []
    s = case.get("sender") or {}
    names += [s.get("name") or "", s.get("preferred_name") or ""]
    r = case.get("recipient") or {}
    names.append(r.get("name") or "")
    for grp in (case["points"], case.get("facts") or []):
        for p in grp:
            for a in _anchor_texts(p.get("anchors", [])):
                if _NAME_RX.fullmatch(a) and not re.search(r"\d", a):
                    names.append(a)
    seen, out = set(), []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _name_words(names: list[str]) -> dict[str, set[str]]:
    """folded form -> exact spellings"""
    out: dict[str, set[str]] = {}
    for n in names:
        for w in _TOKEN.findall(n):
            if len(w) >= 3:
                out.setdefault(_fold_name(w), set()).add(w)
    return out


def wd4(parsed: dict, case: dict) -> dict:
    text = parsed["raw"].strip()
    bad: list[str] = []
    words = _name_words(brief_names(case))
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        base = re.sub(r"['’]s$", "", tok)
        f = _fold_name(base)
        exact = words.get(f)
        if not exact or base in exact or tok in exact:
            continue
        if f in _COMMON_LOWER and base == base.lower() and _strip_accents(base) == base:
            continue
        bad.append(f"{tok!r} for {sorted(exact)[0]!r}")
    pref_fail: list[str] = []
    ap = [p.upper() for p in case.get("active_prefs") or []]
    meta = _meta(case)
    surf = _pref_surface(case)
    for pid in ("P6", "P7", "P8"):
        if pid in ap:
            r = C.check(pid, text, surf, meta)
            if r.status == FAIL:
                pref_fail.append(f"{pid}: {r.reason}")
    if bad or pref_fail:
        return _res(FAIL, "; ".join(bad[:3] + pref_fail[:3]), name_errors=bad, pref_failures=pref_fail)
    return _res(PASS, "names and formats exact", name_errors=[], pref_failures=[])


# --------------------------------------------------------------------------
# WD5 limits and conventions
# --------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]|<\s*[A-Za-z][A-Za-z _-]{0,30}\s*>|\{\{?[^}\n]{1,30}\}?\}|_{4,}|"
                          r"\bX{2,}\b|\bTBD\b|\bTBC\b", re.I)
_CONTRACTION = re.compile(r"\b[A-Za-z]+(?:n't|'(?:re|ve|ll|d|m))\b|"
                          r"\b(?:it|that|there|here|what|who|he|she|let|how|where|when|one)'s\b", re.I)


def _count_contractions(text: str) -> int:
    return len(_CONTRACTION.findall(text.replace("’", "'")))


def _one_off(name: str, parsed: dict, case: dict) -> tuple[str, str]:
    core = parsed["core"] or parsed["body"]
    if name == "no_exclamation":
        return (FAIL, "has '!'") if "!" in parsed["raw"] else (PASS, "")
    if name == "all_lowercase":
        txt = core
        return (PASS, "") if txt == txt.lower() else (FAIL, "has capitals")
    if name == "start_with_date":
        ds = N.find_dates(core)
        ok = bool(ds) and core[:ds[0].start].strip() == "" and ds[0].value[1] is not None
        return (PASS, "") if ok else (FAIL, "does not start with a date")
    if name == "no_emoji":
        return (FAIL, "has emoji") if T.find_emoji(parsed["raw"]) else (PASS, "")
    if name == "no_contractions":
        return (FAIL, "has contractions") if _count_contractions(core) else (PASS, "")
    if name == "no_question_marks":
        return (FAIL, "has '?'") if "?" in parsed["raw"] else (PASS, "")
    if name == "end_with_question":
        return (PASS, "") if core.rstrip().endswith("?") else (FAIL, "does not end with '?'")
    if name == "single_line":
        return (PASS, "") if "\n" not in parsed["body"].strip() else (FAIL, "more than one line")
    if name == "one_paragraph":
        return (PASS, "") if not re.search(r"\n\s*\n", parsed["body"].strip()) else (FAIL, "more than one paragraph")
    return NA, f"unknown one-off rule {name!r}"


def wd5(parsed: dict, case: dict) -> dict:
    c = case["constraints"]
    fails: list[str] = []
    body, core = parsed["body"], parsed["core"]
    surface = case.get("surface")
    wc = parsed["words"]
    if body or c.get("exact_words"):
        if c.get("max_words") and wc > c["max_words"]:
            fails.append(f"{wc} words > {c['max_words']}")
        if c.get("min_words") and wc < c["min_words"]:
            fails.append(f"{wc} words < {c['min_words']}")
        if c.get("exact_words") and wc != c["exact_words"]:
            fails.append(f"{wc} words != {c['exact_words']}")
    if body or c.get("exact_sentences"):
        ns = len(T.split_sentences(core)) if core else 0
        if c.get("max_sentences") and ns > c["max_sentences"]:
            fails.append(f"{ns} sentences > {c['max_sentences']}")
        if c.get("exact_sentences") and ns != c["exact_sentences"]:
            fails.append(f"{ns} sentences != {c['exact_sentences']}")
    if c.get("max_chars") and len(body) > c["max_chars"]:
        fails.append(f"{len(body)} chars > {c['max_chars']}")
    hdr = parsed["header"]
    if c.get("title_max_words"):
        if parsed["title"] is None:
            fails.append("no Title: line")
        elif not parsed["title"].strip() or len(parsed["title"].split()) > c["title_max_words"]:
            fails.append("title length")
        elif hdr and hdr[0] != "title":
            fails.append("Title: is not the first line")
    if surface == "invite" or c.get("description_max_words"):
        if parsed["description"] is None:
            fails.append("no Description: line")
        elif c.get("description_max_words") and wc > c["description_max_words"]:
            fails.append(f"description {wc} words > {c['description_max_words']}")
    if c.get("subject"):
        if parsed["subject"] is None:
            fails.append("no Subject: line")
        elif not parsed["subject"].strip() or len(parsed["subject"].split()) > 10:
            fails.append("subject empty or over 10 words")
        elif not c.get("title_max_words") and hdr and hdr[0] != "subject":
            fails.append("Subject: is not the first line")
    g = c.get("greeting")
    if g and not body.lstrip().startswith(g):
        fails.append(f"greeting {g!r} missing")
    so = _signoff(case)
    if so:
        r = C.check("P9", body, "draft_email", C.Meta(signoff=so))
        if r.status == FAIL:
            fails.append("sign-off: " + r.reason)
    if (c.get("no_markdown") or surface in W.NO_MARKDOWN_SURFACES) and parsed["content"]:
        if H.has_markdown(parsed["content"]):
            fails.append("Markdown present")
    src = (case.get("context") or {}).get("source_text") or ""
    for m in _PLACEHOLDER.finditer(parsed["content"]):
        if m.group(0) not in src:
            fails.append(f"placeholder {m.group(0)!r}")
            break
    ap = [p.upper() for p in case.get("active_prefs") or []]
    meta = _meta(case)
    surf = _pref_surface(case)
    for pid in ap:
        if pid in ("P6", "P7", "P8", "P9"):
            continue
        r = C.check(pid, parsed["raw"].strip(), surf, meta)
        if r.status == FAIL:
            fails.append(f"{pid}: {r.reason}")
    one_off = (case.get("pref_meta") or {}).get("one_off")
    one_off_res = None
    if one_off:
        names = [one_off] if isinstance(one_off, str) else list(one_off)
        one_off_res = {}
        for nm in names:
            st, why = _one_off(nm, parsed, case)
            one_off_res[nm] = {"status": st, "reason": why}
            if st == FAIL:
                fails.append(f"one-off {nm}: {why}")
    return _res(PASS if not fails else FAIL, "; ".join(fails[:4]) if fails else "limits and conventions hold",
                failures=fails, one_off=one_off_res)


# --------------------------------------------------------------------------
# WD6 facts kept (W3)
# --------------------------------------------------------------------------

def wd6(parsed: dict, case: dict) -> dict:
    if case.get("sub") != "W3":
        return _res(NA, "not a rewrite", fact_keep_rate=None)
    yr = case.get("case_year")
    facts = [f for f in case.get("facts") or [] if f.get("anchors")]
    if case["kind"] == "shorten":
        facts = [f for f in facts if f.get("core")]
    text = parsed["content"]
    lost = [f["id"] for f in facts if not all(_anchor_hit(text, a, yr) for a in f["anchors"])]
    kept = len(facts) - len(lost)
    rate = (kept / len(facts)) if facts else None
    fails: list[str] = []
    if lost:
        fails.append("facts lost: " + ", ".join(lost))
    _, inv = invented_atoms(parsed["raw"].strip(), case)
    if inv:
        fails.append("new atoms: " + ", ".join(a.surface for a in inv[:4]))
    shape = None
    want = case.get("target_shape")
    if case["kind"] == "to_bullets" or (want == "bullets" and case["kind"] != "to_prose"):
        want = "bullets"
    elif case["kind"] == "to_prose" or want == "paragraphs":
        want = "paragraphs"
    else:
        want = None
    if want:
        shape = H.classify_shape(parsed["body"])
        ok = shape in (("bullets", "numbered", "label-list") if want == "bullets" else ("paragraphs", "sentence"))
        if not ok:
            fails.append(f"shape {shape} != {want}")
    return _res(PASS if not fails else FAIL, "; ".join(fails) if fails else "facts kept",
                fact_keep_rate=rate, fact_keep_ok=not lost, facts_lost=lost, new_atoms=[a.surface for a in inv[:10]],
                shape=shape)


# --------------------------------------------------------------------------
# WD7 hygiene
# --------------------------------------------------------------------------

_PREAMBLE = re.compile(
    r"^\W*(?:here(?:'s| is| are)\b|sure\b|of course\b|certainly\b|absolutely\b|below (?:is|are)\b|okay\b|ok\b|"
    r"great\b|i(?:'ve| have) (?:written|drafted|prepared|rewritten|put together|edited|corrected)\b|"
    r"(?:the )?(?:draft|rewrite|rewritten|revised|edited|corrected) (?:text|version|email|message)?\s*(?:is|follows)\b)", re.I)
_CLOSING_OFFER = re.compile(
    r"(?:let me know if you(?:'d| would)? (?:like|want|need)|would you like me to|do you want me to|want me to\b|"
    r"i hope (?:this|that) (?:helps|works)|hope (?:this|that) helps|feel free to (?:ask|tell|let)|"
    r"happy to (?:adjust|revise|make|tweak|rewrite|change|help)|i can (?:also )?(?:adjust|revise|shorten|rewrite|make))", re.I)


def wd7(parsed: dict, case: dict) -> dict:
    fails: list[str] = []
    st, why = H.d6_no_leaks(parsed["raw"], {}, {})
    if st == FAIL:
        fails.append("D6 " + why)
    core = parsed["core"] or parsed["body"]
    st, why = H.d7_no_filler(core, {}, {})
    if st == FAIL:
        fails.append("D7 " + why)
    st, why = H.d9_no_repetition(core, {}, {})
    if st == FAIL:
        fails.append("D9 " + why)
    if case["constraints"].get("output_only"):
        text = parsed["raw"].strip()
        lines = [ln for ln in text.split("\n") if ln.strip()]
        if lines and _PREAMBLE.match(lines[0]):
            fails.append(f"preamble {lines[0][:30]!r}")
        main = parsed["content"]
        paras = [p for p in re.split(r"\n\s*\n", main) if p.strip()]
        if len(paras) >= 2 and _CLOSING_OFFER.search(paras[-1]) and len(paras[-1].split()) <= 30:
            fails.append("closing offer")
        elif paras and len(paras) == 1:
            last = [ln for ln in main.split("\n") if ln.strip()][-1:]
            if last and len(main.split("\n")) > 1 and _CLOSING_OFFER.match(last[0]):
                fails.append("closing offer")
    return _res(PASS if not fails else FAIL, "; ".join(fails) if fails else "hygiene holds", failures=fails)


# --------------------------------------------------------------------------
# WD8 proofreading
# --------------------------------------------------------------------------

def _gram_signal(text: str, case: dict, grammar):
    if grammar is None:
        return None
    from . import grammar as G
    if isinstance(grammar, dict):
        sig = grammar
    else:
        checkers = list(grammar) if isinstance(grammar, (list, tuple)) else [grammar]
        sig = G.grammar_signal(text, case, checkers)
    return sig if sig and sig.get("available") else None


def wd8(parsed: dict, case: dict, grammar=None) -> dict:
    edit = case.get("edit")
    if not edit:
        return _res(NA, "not a proofreading case", fix_rate=None, overedit=None, protected_ok=None, flag_rate=None,
                    introduced_errors=None)
    text = parsed["content"].strip()
    gold = edit["gold"].strip()
    dist = lev(text, gold)
    overedit = dist / max(1, len(gold))
    recognisable = overedit <= 0.5
    errs = edit.get("errors") or []
    fixed = []
    for e in errs:
        right = [e["right"], *(e.get("alternatives") or [])]
        gone = (not _bounded(text, e["wrong"])) or (e["wrong"] in e["right"] and _bounded(text, e["right"]))
        if recognisable and gone and any(_bounded(text, r) for r in right):
            fixed.append(e["id"])
    fix_rate = (len(fixed) / len(errs)) if errs else None
    prot = edit.get("protected") or []
    prot_bad = [s for s in prot if s not in text]
    protected_ok = (not prot_bad) if prot else None
    amb = edit.get("ambiguous") or []
    qs = [T.fold(q) for q in parsed["questions"]]
    flagged = 0
    amb_bad = []
    for a in amb:
        key = T.fold(a["key"])
        ok = a["span"] in text and any(_bounded(q, key) for q in qs)
        flagged += ok
        if not ok:
            amb_bad.append(a["span"])
    flag_rate = (flagged / len(amb)) if amb else None
    fails: list[str] = []
    if fix_rate is not None and fix_rate < 1:
        fails.append(f"fix_rate {fix_rate:.2f}")
    if overedit > 0.02:
        fails.append(f"overedit {overedit:.3f} > 0.02")
    if prot_bad:
        fails.append("protected changed: " + ", ".join(repr(s) for s in prot_bad[:3]))
    if amb_bad:
        fails.append("ambiguous not kept and flagged: " + ", ".join(repr(s) for s in amb_bad[:3]))
    introduced = None
    sig_out = _gram_signal(text, case, grammar)
    if sig_out is not None:
        sig_gold = _gram_signal(gold, case, grammar)
        if sig_gold is not None:
            from collections import Counter
            have = Counter((i["rule_id"], T.fold(i.get("text", ""))) for i in sig_gold["issues"])
            extra = 0
            for i in sig_out["issues"]:
                k = (i["rule_id"], T.fold(i.get("text", "")))
                if have[k] > 0:
                    have[k] -= 1
                else:
                    extra += 1
            introduced = extra
    return _res(PASS if not fails else FAIL, "; ".join(fails) if fails else "edits exact",
                fix_rate=fix_rate, overedit=overedit, protected_ok=protected_ok, flag_rate=flag_rate,
                introduced_errors=introduced, edit_distance=dist)


# --------------------------------------------------------------------------
# WD9 register
# --------------------------------------------------------------------------

def wd9(parsed: dict, case: dict) -> dict:
    aud = case.get("audience")
    if not aud:
        return _res(NA, "no audience", register_ok=None)
    body = parsed["body"]
    fails: list[str] = []
    if aud in ("professor", "customer"):
        first = body.lstrip().split("\n", 1)[0] if body.strip() else ""
        if not re.match(r"(?:Dear|Hello)\b", first.strip(), re.I):
            fails.append("no formal greeting")
        slang = sorted({w for w in re.findall(r"[A-Za-z]+", body.lower()) if w in SLANG})
        if slang:
            fails.append("slang: " + ", ".join(slang))
        if T.find_emoji(body) or T.find_emoticons(body):
            fails.append("emoji")
        n = _count_contractions(body)
        allowed = max(1, len(body.split()) // 50)
        if n > allowed:
            fails.append(f"{n} contractions > {allowed}")
    elif aud == "friend":
        if re.search(r"\bDear\b", body):
            fails.append("'Dear'")
        if re.search(r"\bSincerely\b", body) or re.search(r"^\s*yours sincerely", body, re.I | re.M):
            fails.append("'Sincerely'")
    elif aud == "manager":
        lead = [p for p in case["points"] if p.get("lead") and p.get("anchors")]
        if not lead:
            return _res(NA, "no anchored lead point", register_ok=None)
        sents = T.split_sentences(parsed["core"] or body)[:2]
        head = " ".join(sents)
        yr = case.get("case_year")
        for p in lead:
            if not all(_anchor_hit(head, a, yr) for a in p["anchors"]):
                fails.append(f"lead point {p['id']} not in the first two sentences")
    return _res(PASS if not fails else FAIL, "; ".join(fails) if fails else "register markers hold",
                register_ok=not fails)


# --------------------------------------------------------------------------
# WD10 voice proxies
# --------------------------------------------------------------------------

_FUNCTION_WORDS = ("the a an and or but if so of to in on at for with by from as is are was were be been i you we "
                   "they he she it my your our their this that these those not no do does did have has had will "
                   "would can could should just very really also then than there here what when which who how "
                   "about up out").split()
_FW_INDEX = {w: i for i, w in enumerate(_FUNCTION_WORDS)}
_GREET = re.compile(r"^\W*(?:hi|hello|hey|dear|good (?:morning|afternoon|evening)|morning)\b", re.I)
_SIGN = re.compile(r"(?:^|\n)\s*(?:best|kind regards|regards|thanks|thank you|cheers|sincerely|take care|xx?)\b", re.I)


def _features(text: str) -> dict:
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", text.replace("’", "'"))]
    n = max(1, len(words))
    vec = [0.0] * len(_FUNCTION_WORDS)
    for w in words:
        i = _FW_INDEX.get(w)
        if i is not None:
            vec[i] += 1
    vec = [v / n for v in vec]
    sents = T.split_sentences(text) or [text]
    msl = n / max(1, len(sents))
    contr = _count_contractions(text) / n
    excl = (text.count("!") + len(T.find_emoji(text))) / max(1, len(sents))
    low = sum(1 for s in sents if s[:1].isalpha() and s[:1].islower()) / max(1, len(sents))
    return {"vec": vec, "msl": msl, "contr": contr, "excl": excl, "greet": float(bool(_GREET.match(text))),
            "sign": float(bool(_SIGN.search(text))), "low": low}


def _cos_dist(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 1.0
    return 1.0 - sum(x * y for x, y in zip(a, b)) / (na * nb)


def _rel(a: float, b: float, floor: float) -> float:
    return abs(a - b) / max(a, b, floor)


def voice_distance(text: str, samples: list[str]) -> float:
    f = _features(text)
    sf = [_features(s) for s in samples]
    k = len(sf)
    mean = {key: sum(x[key] for x in sf) / k for key in ("msl", "contr", "excl", "greet", "sign", "low")}
    mvec = [sum(x["vec"][i] for x in sf) / k for i in range(len(_FUNCTION_WORDS))]
    parts = [_cos_dist(f["vec"], mvec), _rel(f["msl"], mean["msl"], 1.0), _rel(f["contr"], mean["contr"], 0.02),
             min(1.0, abs(f["excl"] - mean["excl"])), abs(f["greet"] - mean["greet"]),
             abs(f["sign"] - mean["sign"]), abs(f["low"] - mean["low"])]
    return sum(parts) / len(parts)


def wd10(parsed: dict, case: dict) -> dict:
    samples = (case.get("context") or {}).get("samples") or []
    neutral = (case.get("pref_meta") or {}).get("neutral_reference")
    if not samples or not parsed["body"].strip():
        return _res(NA, "diagnostic only; no samples", voice_delta=None, voice_distance=None)
    d = voice_distance(parsed["body"], samples)
    delta = None
    if neutral:
        delta = d - voice_distance(neutral, samples)
    return _res(NA, "diagnostic only" + (f"; voice_delta {delta:+.3f}" if delta is not None else ""),
                voice_delta=delta, voice_distance=d)


# --------------------------------------------------------------------------
# WD11 long-piece consistency
# --------------------------------------------------------------------------

_NUMTOK = re.compile(r"\d[\d.,]*\d|\d")


def _brief_tokens(case: dict) -> tuple[set[str], set[str]]:
    text = "\n".join(case.get("turns") or [])
    return set(_TOKEN.findall(text)), set(_NUMTOK.findall(text))


def _entity_variants(text: str, case: dict) -> list[str]:
    ents = list(case.get("entities") or [])
    if not ents:
        return []
    brief_words, brief_nums = _brief_tokens(case)
    bad: list[str] = []
    ent_words: dict[str, str] = {}
    ent_nums: list[str] = []
    for e in ents:
        for w in _TOKEN.findall(e):
            if len(w) >= 3:
                ent_words[w] = e
        for n in _NUMTOK.findall(e):
            if len(re.sub(r"\D", "", n)) >= 3:
                ent_nums.append(n)
    exact = set(ent_words)
    folded = {}
    for w in ent_words:
        folded.setdefault(_fold_name(w), []).append(w)
    for m in _TOKEN.finditer(text):
        tok = re.sub(r"['’]s$", "", m.group(0))
        if tok in exact or tok in brief_words or len(tok) < 3:
            continue
        f = _fold_name(tok)
        hit = folded.get(f)
        if hit:
            if f in _COMMON_LOWER and tok == tok.lower():
                continue
            bad.append(f"{tok!r} for {hit[0]!r}")
            continue
        if len(tok) >= 4:
            for w in exact:
                if len(w) >= 5 and abs(len(w) - len(tok)) <= 1 and w[:1].isupper() == tok[:1].isupper() \
                        and tok[:1].isupper() and lev(_fold_name(w), f) == 1:
                    bad.append(f"{tok!r} for {w!r}")
                    break
    for m in _NUMTOK.finditer(text):
        t = m.group(0).rstrip(".,")
        d = re.sub(r"\D", "", t)
        if len(d) < 3 or t in ent_nums or t in brief_nums:
            continue
        for e in ent_nums:
            de = re.sub(r"\D", "", e)
            diff = [i for i, (x, y) in enumerate(zip(d, de)) if x != y] if len(d) == len(de) else []
            swap = len(diff) == 2 and diff[1] == diff[0] + 1 and d[diff[0]] == de[diff[1]] and d[diff[1]] == de[diff[0]]
            if d == de or len(diff) == 1 or swap:
                bad.append(f"{t!r} for {e!r}")
                break
    return bad


def wd11(parsed: dict, case: dict, outline: str | None = None) -> dict:
    ents = case.get("entities") or []
    is_outline = case.get("kind") == "outline"
    if not ents and not is_outline:
        return _res(NA, "no entities", consistency_ok=None)
    text = parsed["content"]
    fails = _entity_variants(text, case)
    order_ok = None
    if is_outline:
        yr = case.get("case_year")
        pts = _anchored_points(case)
        pos_piece = {p["id"]: _anchor_first_pos(text, p["anchors"][0], yr) for p in pts}
        absent = [pid for pid, v in pos_piece.items() if v is None]
        if absent:
            fails.append("outline points missing: " + ", ".join(absent))
        if outline:
            pos_ref = {p["id"]: _anchor_first_pos(outline, p["anchors"][0], yr) for p in pts}
            ref_order = [pid for pid, v in sorted(((k, v) for k, v in pos_ref.items() if v is not None), key=lambda x: x[1])]
        else:
            ref_order = [p["id"] for p in pts]
        got = [pid for pid in ref_order if pos_piece.get(pid) is not None]
        want_sorted = sorted(got, key=lambda pid: pos_piece[pid])
        order_ok = got == want_sorted
        if not order_ok:
            fails.append("points out of outline order")
    return _res(PASS if not fails else FAIL, "; ".join(fails[:3]) if fails else "spelling and order consistent",
                consistency_ok=not fails, order_ok=order_ok, variants=fails[:10])


# --------------------------------------------------------------------------
# Score
# --------------------------------------------------------------------------

_FORM_CHECKS = ("WD2", "WD3", "WD4", "WD5", "WD6", "WD7", "WD8", "WD9", "WD11")


def score_writing(draft: str, case: dict, *, outline: str | None = None, grammar=None) -> dict:
    parsed = parse_draft(draft or "", case)
    checks = {
        "WD1": wd1(parsed, case), "WD2": wd2(parsed, case), "WD3": wd3(parsed, case), "WD4": wd4(parsed, case),
        "WD5": wd5(parsed, case), "WD6": wd6(parsed, case), "WD7": wd7(parsed, case),
        "WD8": wd8(parsed, case, grammar), "WD9": wd9(parsed, case), "WD10": wd10(parsed, case),
        "WD11": wd11(parsed, case, outline),
    }
    st = {k: v["status"] for k, v in checks.items()}
    w3 = case.get("sub") == "W3"
    gate = (st["WD1"] != FAIL and st["WD3"] != FAIL and st["WD5"] != FAIL and st["WD7"] != FAIL
            and (not w3 or checks["WD6"].get("fact_keep_ok", True)))
    passed = all(s != FAIL for s in st.values())
    # content
    sub = case.get("sub")
    if st["WD1"] != NA:
        content = checks["WD1"]["point_recall"]
    elif w3 and checks["WD6"].get("fact_keep_rate") is not None:
        content = checks["WD6"]["fact_keep_rate"]
    elif st["WD8"] != NA:
        vals = [v for v in (checks["WD8"]["fix_rate"], checks["WD8"]["flag_rate"]) if v is not None]
        content = sum(vals) / len(vals) if vals else 0.0
    else:
        content = 1.0 if (draft or "").strip() else 0.0
    if not (draft or "").strip():
        content = 0.0
    form_ids = [c for c in _FORM_CHECKS if not (w3 and c == "WD2") and st[c] != NA]
    form = (sum(1 for c in form_ids if st[c] == PASS) / len(form_ids)) if form_ids else 1.0
    score = round(max(0.0, min(1.0, content * form)), 6)

    sig = _gram_signal(parsed["content"], case, grammar)
    c1, c2, c3, c6, c8, c9, c10, c11 = (checks[k] for k in ("WD1", "WD2", "WD3", "WD6", "WD8", "WD9", "WD10", "WD11"))
    metrics = {
        "point_recall": c1["point_recall"], "points_anchored": c1["points_anchored"], "points_made": c1["points_made"],
        "atoms": c2["atoms"], "invented_atoms": c2["invented_atoms"], "invented_rate": c2["invented_rate"],
        "forbidden": st["WD3"] == FAIL, "forbidden_rate": c3["forbidden_rate"],
        "constraint_ok": all(st[k] != FAIL for k in ("WD4", "WD5", "WD7")),
        "fact_keep_ok": c6.get("fact_keep_ok"), "fact_keep_rate": c6["fact_keep_rate"],
        "fix_rate": c8["fix_rate"], "overedit": c8["overedit"], "flag_rate": c8["flag_rate"],
        "protected_ok": c8["protected_ok"], "introduced_errors": c8["introduced_errors"],
        "register_ok": c9["register_ok"], "voice_delta": c10["voice_delta"], "consistency_ok": c11["consistency_ok"],
        "word_count": parsed["words"],
        "grammar_errors_per_100w": sig["errors_per_100w"] if sig else None,
    }
    return {"checks": checks, "metrics": metrics, "gate_pass": bool(gate), "pass": bool(passed),
            "score": score, "version": WRITING_CHECKS_VERSION}


# --------------------------------------------------------------------------
# Case-level helpers for the split builder and validator
# --------------------------------------------------------------------------

def _anchor_key(anchor, allowed_fold: str, allowed_raw: str):
    t, v, _ = N.parse_anchor(anchor)
    if t == "text" or t == "code":
        f = T.fold(str(v))
        if not f or f in allowed_fold:
            return None
        return ("text", f)
    sv = str(v)
    if sv in allowed_raw:
        return None
    if t in ("amount", "number"):
        try:
            d = Decimal(sv.replace(",", ""))
            d = d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()
            return (t, format(d, "f"))
        except Exception:                                    # noqa: BLE001
            return (t, sv)
    return (t, sv)


def case_anchor_keys(case: dict) -> set[tuple]:
    """Normalized keys of every anchor in points, must_not and facts (and long W4 error spans),
    leaving out anchors that appear in `allowed_text` (profile names are shared across splits)."""
    raw = case.get("allowed_text") or ""
    fold = T.fold(raw)
    keys: set[tuple] = set()
    for grp in (case.get("points") or [], case.get("must_not") or [], case.get("facts") or []):
        for item in grp:
            for a in item.get("anchors") or []:
                k = _anchor_key(a, fold, raw)
                if k:
                    keys.add(k)
    edit = case.get("edit")
    if edit:
        for e in edit.get("errors") or []:
            f = T.fold(e.get("wrong", ""))
            if len(f) >= 6 and f not in fold:
                keys.add(("text", f))
    return keys


def empty_or_off_brief_scores_zero(case: dict, other_case: dict) -> bool:
    return (score_writing("", case)["score"] == 0
            and score_writing(other_case["reference_draft"], case)["score"] == 0)
