"""The Writing family: the model writing on the user's behalf (spec 03 section 9).

This module owns the case contract, the plan (sub-families, kinds, counts and
splits), profile access, the request renderer and the split builder. The case
generators live in `writing_gen_draft` (W1, W2, W6), `writing_gen_edit` (W3, W4)
and `writing_gen_style` (W5, W7); the checks WD1-WD11 in `writing_checks`.

Case contract (every generator returns exactly this shape; `brief_errors` checks it)
-------------------------------------------------------------------------------
{
  "family": "writing", "case_id": "W1-EMAIL-<8 hex>", "sub": "W1", "kind": "email",
  "surface": "email" | "message" | "note" | "invite" | "document" | "bio",
  "profile": "student" | "tech" | "mixed" | None,     # None = neutral invented sender
  "seed": int, "generator": "<module>/<version>", "today": "YYYY-MM-DD", "case_year": int,
  "sender": {"name": str, "preferred_name": str},
  "recipient": {"name": str, "relation": str} | None,
  "audience": "manager" | "friend" | "customer" | "professor" | None,   # W6 (and WD9)
  "group_id": str | None,                       # W6: the four audiences of one brief share it
  "purpose": str,                               # one line, shown to the model
  "points": [{"id": "p1", "text": str, "anchors": [anchor, ...], "kind": "anchored" | "semantic",
              "lead": bool}],                   # lead: the ask/decision (WD9 manager rule)
  "must_not": [{"id": "f1", "text": str, "anchors": [anchor, ...], "stated": bool}],
                                                # stated: the request tells the model to leave it out
  "constraints": {"max_words": int|None, "min_words": int|None, "exact_words": int|None,
                  "max_sentences": int|None, "exact_sentences": int|None, "max_chars": int|None,
                  "subject": bool, "greeting": str|None, "signoff": str|None,
                  "title_max_words": int|None, "description_max_words": int|None,
                  "no_markdown": bool, "output_only": bool, "questions_list": bool},
  "context": {"thread": [{"from": str, "time": str, "text": str}] | None, "thread_kind": "email"|"message"|None,
              "source_text": str | None, "samples": [str] | None},
  "facts": [{"id": str, "text": str, "anchors": [anchor], "core": bool}],   # W3: facts in source_text
  "target_shape": "bullets" | "paragraphs" | None,                            # W3 prose<->bullets
  "edit": None | {"gold": str,                                             # W4
                  "errors": [{"id", "type", "wrong": str, "right": str, "alternatives": [str]}],
                  "protected": [str], "ambiguous": [{"span": str, "key": str}]},
  "active_prefs": [P-id, ...], "pref_meta": {...},  # W5 standing preference (checkers.Meta fields)
  "entities": [str],                                # W7 names/places/figures to spell consistently
  "turns": [str, ...],                              # the rendered user request(s); W7 outline has 2
  "reference_draft": str,                           # a draft that passes every deterministic check
  "reference_turns": [str, ...] | None,             # W7 outline: [outline, piece]
  "allowed_text": str,                              # extra text whose atoms are not "invented" (WD2)
}

An anchor is anything `normalize.parse_anchor` accepts: a plain string (a name,
a code, a distinctive term), or {"type": "date", "value": "YYYY-MM-DD"},
{"type": "time", "value": "HH:MM"}, {"type": "amount", "value": "1940.00"},
{"type": "number", "value": "12480"}.

Everything is invented. Profile-bound cases read the three synthetic profiles in
`test_fixtures/bench_profiles/`; the ledger never reaches the model.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import random
from pathlib import Path
from typing import Callable

WRITING_VERSION = "edat-writing/1"
WRITING_SYSTEM_VERSION = "edat-writing-system/1"
REPO = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO / "test_fixtures" / "bench_profiles"
PROFILE_IDS = ("student", "tech", "mixed")
SURFACES = ("email", "message", "note", "invite", "document", "bio")
AUDIENCES = ("manager", "friend", "customer", "professor")
NO_MARKDOWN_SURFACES = frozenset({"email", "message", "invite"})

# (sub, kind, total cases, surface). Totals follow spec 9.1: 342 cases.
PLAN: list[tuple[str, str, int, str]] = [
    ("W1", "email", 10, "email"), ("W1", "reply", 10, "email"), ("W1", "text", 10, "message"),
    ("W1", "note", 10, "note"), ("W1", "invite", 10, "invite"), ("W1", "section", 10, "document"),
    ("W1", "bio", 10, "bio"),
    ("W2", "decline", 8, "email"), ("W2", "bad_news", 8, "email"), ("W2", "ask", 8, "email"),
    ("W2", "follow_up", 8, "email"), ("W2", "apologize", 8, "message"), ("W2", "feedback", 8, "email"),
    ("W3", "shorten", 10, "document"), ("W3", "expand", 6, "document"),
    ("W3", "tone_formal", 4, "email"), ("W3", "tone_casual", 4, "message"), ("W3", "tone_warm", 4, "email"),
    ("W3", "tone_firm", 4, "email"), ("W3", "simplify", 8, "document"), ("W3", "to_bullets", 6, "document"),
    ("W3", "to_prose", 6, "document"), ("W3", "restructure", 8, "document"),
    ("W4", "minimal", 20, "document"), ("W4", "protected", 10, "document"), ("W4", "ambiguous", 10, "document"),
    ("W5", "standing_pref", 10, "email"), ("W5", "one_off", 10, "message"), ("W5", "voice", 12, "message"),
    ("W5", "conventions", 10, "email"), ("W5", "exact_count", 12, "note"),
    ("W6", "register", 40, "email"),
    ("W7", "consistency", 10, "document"), ("W7", "outline", 8, "document"), ("W7", "title", 12, "document"),
]
TOTAL_CASES = sum(n for _, _, n, _ in PLAN)
assert TOTAL_CASES == 342, TOTAL_CASES
PROFILE_BOUND_SUBS = frozenset({"W1", "W2", "W5", "W6"})   # about half of these are written as a profile


@functools.lru_cache(maxsize=1)
def _dev_alloc() -> dict[tuple[str, str], int]:
    """A quarter of each sub-family goes to development, spread over its kinds by largest
    remainder (ties to the earlier kind). W6 keeps whole groups of four: 3 of its 10 briefs."""
    out: dict[tuple[str, str], int] = {}
    subs: dict[str, list[tuple[str, int]]] = {}
    for sub, kind, total, _ in PLAN:
        subs.setdefault(sub, []).append((kind, total))
    for sub, kinds in subs.items():
        if sub == "W6":
            out[(sub, "register")] = 12
            continue
        want = int(sum(t for _, t in kinds) / 4 + 0.5)
        floors = {k: max(1, int(t / 4)) for k, t in kinds}
        rest = sorted(kinds, key=lambda kt: -(kt[1] / 4 - int(kt[1] / 4)))
        i = 0
        while sum(floors.values()) < want and i < len(rest):
            floors[rest[i][0]] += 1
            i += 1
        out.update({(sub, k): v for k, v in floors.items()})
    return out


def dev_count(sub: str, kind: str, total: int) -> int:
    return _dev_alloc()[(sub, kind)]


def split_counts() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for sub, kind, total, _ in PLAN:
        d = dev_count(sub, kind, total)
        out[f"{sub}/{kind}"] = {"dev": d, "heldout": total - d, "total": total}
    return out


def profile_for(sub: str, index: int) -> str | None:
    """About half of W1, W2, W5 and W6 are written as one of the three people; the rest are neutral."""
    if sub not in PROFILE_BOUND_SUBS:
        return None
    return (PROFILE_IDS[(index // 2) % 3] if index % 2 == 0 else None)


# --------------------------------------------------------------------------
# Profiles (read only; rendered through the profile package's own loader)
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=8)
def load_profile(pid: str) -> dict:
    try:
        from scripts.bench_profiles.render import load_profile as _load
        return _load(pid, root=PROFILES_DIR)
    except Exception:                             # noqa: BLE001 - fall back to the raw file
        return json.loads((PROFILES_DIR / pid / "profile.json").read_text())


def profile_today(pid: str) -> dt.date:
    return dt.date.fromisoformat(load_profile(pid)["anchor"]["date"])


# The sign-off each person uses on drafted email (file 14 item 9). Marcus's is in his own
# notes; Talia's and Camila's are taken from how they sign their sent mail to staff.
PROFILE_SIGNOFF = {"tech": "Thanks,\nMarcus", "student": "Best,\nTalia", "mixed": "Kind regards,\nCamila"}


def profile_sender(pid: str) -> dict:
    p = load_profile(pid)["person"]
    return {"name": p["name"], "preferred_name": p.get("preferred_name") or p["name"].split()[0]}


def contacts(pid: str, pred: Callable[[dict], bool] | None = None) -> list[dict]:
    cs = [c for c in load_profile(pid)["contacts"] if c.get("relation")]
    return [c for c in cs if pred is None or pred(c)]


def contact_by_id(pid: str, cid: str) -> dict | None:
    return next((c for c in load_profile(pid)["contacts"] if c["id"] == cid), None)


def own_messages(pid: str, *, min_words: int = 3) -> list[str]:
    """The person's own sent text messages (voice samples)."""
    out = []
    for t in load_profile(pid)["threads"]:
        for m in t["messages"]:
            if m["from"] == "me" and len(m["text"].split()) >= min_words and "{{" not in m["text"]:
                out.append(m["text"])
    return out


def own_emails(pid: str) -> list[dict]:
    return [e for e in load_profile(pid)["emails"] if e.get("mailbox") == "sent"]


def message_threads(pid: str, *, min_messages: int = 4) -> list[dict]:
    return [t for t in load_profile(pid)["threads"] if len(t["messages"]) >= min_messages]


def email_threads(pid: str, *, min_messages: int = 2) -> list[list[dict]]:
    by: dict[str, list[dict]] = {}
    for e in load_profile(pid)["emails"]:
        if not e.get("bulk"):
            by.setdefault(e.get("thread_id") or e["id"], []).append(e)
    return [sorted(v, key=lambda e: (e["day"], e["time"])) for v in by.values() if len(v) >= min_messages]


def profile_allowed_text(pid: str) -> str:
    """The person's own facts a draft may use without inventing them (WD2)."""
    p = load_profile(pid)
    person = p["person"]
    parts = [person["name"], person.get("preferred_name") or "", json.dumps(person.get("about_you", {}),
                                                                             ensure_ascii=False)]
    parts += [c.get("full_name") or c["name"] for c in p["contacts"]] + [c["name"] for c in p["contacts"]]
    parts += [a["address"] for a in p.get("accounts", [])]
    parts.append(PROFILE_SIGNOFF.get(pid, ""))
    return "\n".join(x for x in parts if x)


# --------------------------------------------------------------------------
# Case helpers shared by the generators
# --------------------------------------------------------------------------

def case_seed(base: int, sub: str, kind: str, index: int) -> int:
    h = hashlib.sha256(f"{base}:{sub}:{kind}:{index}".encode()).hexdigest()
    return int(h[:12], 16)


def case_id(sub: str, kind: str, seed: int) -> str:
    return f"{sub}-{kind.upper().replace('_', '-')}-{hashlib.sha256(str(seed).encode()).hexdigest()[:8]}"


def point(pid: str, text: str, anchors=(), *, kind: str | None = None, lead: bool = False) -> dict:
    anchors = list(anchors)
    return {"id": pid, "text": text, "anchors": anchors, "kind": kind or ("anchored" if anchors else "semantic"),
            "lead": lead}


def forbid(fid: str, text: str, anchors, *, stated: bool = True) -> dict:
    return {"id": fid, "text": text, "anchors": list(anchors), "stated": stated}


def date_anchor(d: dt.date) -> dict:
    return {"type": "date", "value": d.isoformat()}


def time_anchor(t: str) -> dict:
    return {"type": "time", "value": t}


def amount_anchor(a: str) -> dict:
    return {"type": "amount", "value": a}


def number_anchor(n: str) -> dict:
    return {"type": "number", "value": n}


DEFAULT_CONSTRAINTS = {"max_words": None, "min_words": None, "exact_words": None, "max_sentences": None,
                       "exact_sentences": None, "max_chars": None, "subject": False, "greeting": None,
                       "signoff": None, "title_max_words": None, "description_max_words": None,
                       "no_markdown": False, "output_only": True, "questions_list": False}


def new_case(*, sub: str, kind: str, seed: int, generator: str, today: dt.date, profile: str | None,
             sender: dict, purpose: str, recipient: dict | None = None, surface: str | None = None,
             **fields) -> dict:
    """A case with every contract field present; generators fill in the rest and call `finish`."""
    surf = surface or next(s for sb, k, _, s in PLAN if sb == sub and k == kind)
    case = {
        "family": "writing", "case_id": case_id(sub, kind, seed), "sub": sub, "kind": kind, "surface": surf,
        "profile": profile, "seed": seed, "generator": generator, "today": today.isoformat(),
        "case_year": today.year, "sender": sender, "recipient": recipient, "audience": None, "group_id": None,
        "purpose": purpose, "points": [], "must_not": [], "constraints": dict(DEFAULT_CONSTRAINTS),
        "context": {"thread": None, "thread_kind": None, "source_text": None, "samples": None},
        "facts": [], "target_shape": None, "edit": None, "active_prefs": [], "pref_meta": {}, "entities": [],
        "turns": [], "reference_draft": "", "reference_turns": None, "allowed_text": "",
    }
    case["constraints"]["no_markdown"] = surf in NO_MARKDOWN_SURFACES
    for k, v in fields.items():
        if k == "constraints":
            case["constraints"].update(v)
        elif k == "context":
            case["context"].update(v)
        else:
            case[k] = v
    return case


def finish(case: dict) -> dict:
    """Render the request(s) when the generator did not, and add the profile's own facts."""
    if not case["turns"]:
        case["turns"] = [render_request(case)]
    if case["profile"] and case["profile"] not in case["allowed_text"]:
        case["allowed_text"] = (case["allowed_text"] + "\n" + profile_allowed_text(case["profile"])).strip()
    errs = brief_errors(case)
    if errs:
        raise ValueError(f"{case['case_id']}: " + "; ".join(errs))
    return case


# --------------------------------------------------------------------------
# Rendering: the model sees only the plain request (spec 9.2)
# --------------------------------------------------------------------------

_SURFACE_WORD = {"email": "an email", "message": "a text message", "note": "a note", "invite": "a calendar invitation",
                 "document": "a short piece", "bio": "a short bio"}


def render_thread(case: dict) -> str:
    th = case["context"]["thread"] or []
    head = "The email thread so far:" if case["context"]["thread_kind"] == "email" else "The conversation so far:"
    lines = [head]
    for m in th:
        lines.append(f"[{m['time']}] {m['from']}: {m['text']}")
    return "\n".join(lines)


def render_constraints(c: dict, surface: str) -> list[str]:
    out = []
    if c.get("exact_words"):
        out.append(f"Use exactly {c['exact_words']} words.")
    elif c.get("max_words") and c.get("min_words"):
        out.append(f"Use between {c['min_words']} and {c['max_words']} words.")
    elif c.get("max_words"):
        out.append(f"Keep it to {c['max_words']} words or fewer.")
    if c.get("exact_sentences"):
        out.append(f"Write exactly {c['exact_sentences']} sentences.")
    elif c.get("max_sentences"):
        out.append(f"Use at most {c['max_sentences']} sentences.")
    if c.get("max_chars"):
        out.append(f"Keep it under {c['max_chars']} characters.")
    if c.get("subject"):
        out.append('Put a subject line first, written as "Subject: ...".')
    if c.get("title_max_words"):
        out.append(f"Give a title of at most {c['title_max_words']} words on the first line, written as \"Title: ...\".")
    if c.get("description_max_words"):
        out.append(f"Then a description of at most {c['description_max_words']} words, written as \"Description: ...\".")
    if c.get("greeting"):
        out.append(f'Start with "{c["greeting"]}".')
    if c.get("signoff"):
        so = c["signoff"].replace("\n", " / ")
        out.append(f'End with the sign-off "{so}"' + (" (on two lines)." if "\n" in c["signoff"] else "."))
    if c.get("no_markdown"):
        out.append("Plain text only, no formatting.")
    if c.get("questions_list"):
        out.append('After the text, add a line "Questions:" listing anything that is ambiguous and that you left '
                   'unchanged, one per line.')
    if c.get("output_only"):
        out.append("Reply with the text only.")
    return out


def render_request(case: dict) -> str:
    """The default request for a brief. Generators for rewrites, edits and two-turn cases
    write their own `turns` and may call the pieces below."""
    sender = case["sender"]["name"]
    rcp = case.get("recipient")
    who = f" to {rcp['name']} ({rcp['relation']})" if rcp else ""
    lines = [f"Write {_SURFACE_WORD[case['surface']]}{who} for me ({sender}). Purpose: {case['purpose']}."]
    pts = [p for p in case["points"]]
    if pts:
        lines.append("It needs to say:")
        lines += [f"- {p['text']}" for p in pts]
    stated = [f for f in case["must_not"] if f.get("stated")]
    if stated:
        lines.append("Leave out: " + "; ".join(f["text"] for f in stated) + ".")
    if case["context"].get("thread"):
        lines.append("")
        lines.append(render_thread(case))
        lines.append("")
    if case["context"].get("samples"):
        lines.append("Some things I've written before, so you can match how I write:")
        lines += [f'- "{s}"' for s in case["context"]["samples"]]
    if case["context"].get("source_text"):
        lines.append("")
        lines.append("The text:")
        lines.append('"""')
        lines.append(case["context"]["source_text"])
        lines.append('"""')
    lines += render_constraints(case["constraints"], case["surface"])
    return "\n".join(lines)


def system_prompt(case: dict) -> str:
    today = dt.date.fromisoformat(case["today"])
    who = case["sender"]["name"]
    lines = [f"You are a personal assistant running on the user's own device. You write and edit text for "
             f"{who}, in English, to be sent or kept under their name.",
             f"Today is {today.strftime('%A')} {today.isoformat()}."]
    if case.get("active_prefs"):
        lines.append(f"{who}'s standing preferences: " + "; ".join(case["pref_meta"].get("pref_texts", [])) + ".")
    return "\n".join(lines)


def build_writing_messages(case: dict, turn: int = 0, prior: list[str] | None = None) -> list[dict]:
    """Messages for one turn. `prior` holds the model's own earlier answers (W7 outline)."""
    msgs = [{"role": "system", "content": system_prompt(case)}]
    for i in range(turn):
        msgs.append({"role": "user", "content": case["turns"][i]})
        msgs.append({"role": "assistant", "content": (prior or [])[i]})
    msgs.append({"role": "user", "content": case["turns"][turn]})
    return msgs


# --------------------------------------------------------------------------
# Contract check
# --------------------------------------------------------------------------

_REQUIRED = ("family", "case_id", "sub", "kind", "surface", "profile", "seed", "generator", "today", "case_year",
             "sender", "recipient", "audience", "group_id", "purpose", "points", "must_not", "constraints",
             "context", "facts", "target_shape", "edit", "active_prefs", "pref_meta", "entities", "turns",
             "reference_draft", "reference_turns", "allowed_text")


def _anchor_ok(a) -> bool:
    if isinstance(a, str):
        return bool(a.strip())
    if isinstance(a, dict):
        return a.get("type") in ("date", "time", "amount", "number", "code", "text") and bool(str(a.get("value", "")))
    return False


def brief_errors(case: dict) -> list[str]:
    errs = [f"missing {k}" for k in _REQUIRED if k not in case]
    if errs:
        return errs
    if (case["sub"], case["kind"]) not in {(s, k) for s, k, _, _ in PLAN}:
        errs.append(f"unknown kind {case['sub']}/{case['kind']}")
    if case["surface"] not in SURFACES:
        errs.append(f"surface {case['surface']}")
    if case["profile"] not in (None, *PROFILE_IDS):
        errs.append(f"profile {case['profile']}")
    if case["audience"] not in (None, *AUDIENCES):
        errs.append(f"audience {case['audience']}")
    for p in case["points"]:
        if p["kind"] == "anchored" and not p["anchors"]:
            errs.append(f"point {p['id']} has no anchors")
        if not all(_anchor_ok(a) for a in p["anchors"]):
            errs.append(f"point {p['id']} has a bad anchor")
    for f in case["must_not"]:
        if not f["anchors"] or not all(_anchor_ok(a) for a in f["anchors"]):
            errs.append(f"must_not {f['id']} needs anchors")
    if set(case["constraints"]) != set(DEFAULT_CONSTRAINTS):
        errs.append("constraints keys differ from the contract")
    if not case["turns"] or not all(isinstance(t, str) and t.strip() for t in case["turns"]):
        errs.append("no rendered request")
    if not case["reference_draft"].strip():
        errs.append("no reference draft")
    if case["kind"] == "outline" and (not case["reference_turns"] or len(case["turns"]) != 2):
        errs.append("outline cases have two turns and two reference turns")
    if case["sub"] == "W4" and not case["edit"]:
        errs.append("W4 needs edit")
    if case["sub"] == "W3" and not (case["context"]["source_text"] and case["facts"]):
        errs.append("W3 needs source_text and facts")
    if case["sub"] == "W6" and not (case["audience"] and case["group_id"]):
        errs.append("W6 needs audience and group_id")
    # nothing structured may leak into the request: forbidden content the request does not
    # state must not appear in it, outside the quoted thread or source text
    return errs


# --------------------------------------------------------------------------
# Building the splits
# --------------------------------------------------------------------------

def generator_for(sub: str, kind: str) -> Callable[..., dict]:
    if sub in ("W1", "W2", "W6"):
        from edat.quality import writing_gen_draft as G
    elif sub in ("W3", "W4"):
        from edat.quality import writing_gen_edit as G
    else:
        from edat.quality import writing_gen_style as G
    return G.GENERATORS[(sub, kind)]


def build_writing_split(split: str, base_seed: int, *, avoid: set | None = None) -> list[dict]:
    """All cases of one split. Index ranges are disjoint between splits (dev takes the first
    `dev_count` indices of every kind), so profile binding and W6 grouping stay balanced.
    `avoid` holds anchor keys from the other split; a case that collides is regenerated."""
    from edat.quality.writing_checks import case_anchor_keys
    out = []
    for sub, kind, total, _ in PLAN:
        d = dev_count(sub, kind, total)
        idx = range(0, d) if split == "dev" else range(d, total)
        gen = generator_for(sub, kind)
        if sub == "W6":
            groups = sorted({i // 4 for i in idx})
            for g in groups:
                for attempt in range(20):
                    seed = case_seed(base_seed + attempt * 7919, sub, kind, g)
                    cases = gen(seed, index=g, profile=profile_for(sub, g))
                    keys = set().union(*(case_anchor_keys(c) for c in cases))
                    if not avoid or not (keys & avoid):
                        break
                out += cases
            continue
        for i in idx:
            for attempt in range(20):
                seed = case_seed(base_seed + attempt * 7919, sub, kind, i)
                c = gen(seed, index=i, profile=profile_for(sub, i))
                if not avoid or not (case_anchor_keys(c) & avoid):
                    break
            out.append(c)
    return out


def rng_for(seed: int) -> random.Random:
    return random.Random(seed)
