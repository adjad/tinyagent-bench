"""Noisy-input variants (spec 02 §3.18, build item B23): the same request written badly.

    typo, voice, para = make_variants(case, rng)

Every variant is a copy of the seed case with a new id (`<id>__typo`, `__voice`,
`__para`), `variant_of: <id>`, `variant: typo | transcript | paraphrase`, the tag
`noisy`, and ONLY the user prompt(s) changed. Checks that read the model's own
words are widened so the intended answer still passes (see `_widen`); the oracle
and the seeded world are never touched.

* typo: seeded character edits (swap, drop, double, neighbouring key) in about 4% of
  words (at least one edit per prompt, so a variant never equals its seed). Never inside
  numbers, ids, e-mail addresses, URLs, quoted text or `{placeholders}`. A name may be
  misspelled: any matcher that names it (`icontains`, `contains_any`, `eq`, ...) gets the
  misspelling added as an alternative, and, for a seeded contact's first name, also the
  surname and the phone tail, because a model that cannot read the misspelled name may
  fall back on either to find the intended contact.
* transcript: lowercase, no punctuation, fillers ("um", "like", "so"), small numbers and
  clock times spelled out ("three thirty", "twenty one") unless a matcher needs the digits
  (then the digits stay; if the matcher is the number alone, the spelled form is accepted
  as well), and two harmless homophones (there/their, here/hear).
* paraphrase: rule-based rewording with the same intent (a table of safe phrase swaps,
  polite prefixes reordered, a casual lead-in as a last resort). It never rewords a word
  a matcher names. Marked `review: pending` in `notes`: the spec wants paraphrases
  reviewed by hand.

`{placeholder}` facts stay as written in every variant (their value is only known when a
trial binds), so a number that comes from a fact is never spelled out.

A variant must still pass the oracle check and fail the null model; `build_variants`
checks that and returns the ones it dropped. `select_seeds` draws the 150 seed cases
(a `core` subset of 30) and `consistency` turns verdict rows into the two noise metrics.
Variants drop the seed's `core` and `pair_id`: they are not family cases and must not join
a clean/attacked pair.

Known shared-code limit: `bind` draws facts from the case id, so a variant's facts differ
from its seed's for the same world seed; the clean/variant comparison is by trial number,
not by identical world. (Suggested fix in the hand-off: key the draw on
`case.get("variant_of") or case["id"]`.)
"""
from __future__ import annotations

import copy
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import NamedTuple

from edat.cases.schema import FAMILIES

SEED_FAMILIES = ("select", "sequential", "parallel", "abstain", "restraint")
VARIANT_SUFFIX = {"typo": "typo", "transcript": "voice", "paraphrase": "para"}
VARIANT_KINDS = tuple(VARIANT_SUFFIX)
TYPO_RATE = 0.04
CHECK_KEYS = ("expect", "final_state", "answer", "claims", "clarify", "alt_pass", "verify",
              "grounded_args")
MATCHERS = {"eq", "icontains", "contains_any", "contains_all", "not_contains", "regex", "in",
            "present", "absent", "date_offset", "weekday", "gt", "gte", "lt", "lte", "datetime_eq",
            "rrule", "all_of", "any_of", "not"}

# ---------------------------------------------------------------------------- user-text slots


def _turn_dicts(case: dict) -> list[dict]:
    """Every dict that can hold a user prompt or per-turn checks (not the case itself)."""
    out = []
    if isinstance(case.get("followup_turn"), dict):
        out.append(case["followup_turn"])
    out.extend(t for t in case.get("turns") or [])
    for s in case.get("sessions") or []:
        out.extend(s.get("turns") or [])
    return out


def _slots(case: dict) -> list[tuple[dict, str]]:
    slots = []
    if isinstance(case.get("prompt"), str):
        slots.append((case, "prompt"))
    slots.extend((t, "user") for t in _turn_dicts(case) if isinstance(t.get("user"), str))
    return slots


def _check_holders(case: dict) -> list[dict]:
    return [case] + _turn_dicts(case)


# ---------------------------------------------------------------------------- literals


_TOOLISH = re.compile(r"^[a-z]+(_[a-z]+)+$")
_SKIP_KEYS = {"tool", "any_of_tools"}


def _leaf_strings(node, out: list[str], key: str = "") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _SKIP_KEYS:
                continue
            if k == "in" and isinstance(v, str):      # a collection name, not a matcher
                continue
            _leaf_strings(v, out, k)
    elif isinstance(node, list):
        for v in node:
            _leaf_strings(v, out, key)
    elif isinstance(node, str) and not _TOOLISH.match(node):
        out.append(node)


class _Literals:
    """The strings the case's checks look for, so a variant can leave them alone or widen them."""

    def __init__(self, case: dict):
        leaves: list[str] = []
        for h in _check_holders(case):
            for k in CHECK_KEYS:
                if k in h:
                    _leaf_strings(h[k], leaves)
        self.leaves = [s.strip() for s in leaves if s.strip()]
        self.exact = {s.casefold() for s in self.leaves}
        self.words: set[str] = set()          # words inside a longer literal (never edited)
        for s in self.leaves:
            toks = re.findall(r"[A-Za-z0-9]+", s)
            if len(toks) > 1 or (toks and toks[0].casefold() != s.casefold()):
                self.words.update(t.casefold() for t in toks)
        contacts = ((case.get("world") or {}).get("seed_records") or {}).get("contacts") or []
        self.contacts = [c for c in contacts if isinstance(c, dict) and c.get("name")]

    def protected(self, word: str) -> bool:
        return word.casefold() in self.words

    def contains_token(self, token: str) -> bool:
        pat = re.compile(r"(?<![\w:.\-])" + re.escape(token) + r"(?![\w:])", re.I)
        return any(pat.search(s) for s in self.leaves)

    def contact_extras(self, word: str) -> list[str]:
        """Surname and phone tail of the seeded contact whose first name is `word`."""
        for c in self.contacts:
            parts = str(c["name"]).split()
            if parts and parts[0].casefold() == word.casefold():
                extra = [parts[-1]] if len(parts) > 1 else []
                digits = re.sub(r"\D", "", str(c.get("phone") or ""))
                if len(digits) >= 4:
                    extra.append(digits[-4:])
                return extra
        return []


# ---------------------------------------------------------------------------- widening matchers


def _widen(node, orig: str, alts: list[str]):
    """Return `node` with every matcher that names exactly `orig` also accepting `alts`."""
    o = orig.casefold()
    alts = [a for a in dict.fromkeys(alts) if a.casefold() != o]
    if not alts:
        return node
    if isinstance(node, list):
        return [_widen(v, orig, alts) for v in node]
    if not isinstance(node, dict):
        return node
    if len(node) == 1:
        (k, v), = node.items()
        if k == "icontains" and isinstance(v, str) and v.strip().casefold() == o:
            return {"contains_any": [v] + alts}
        if k == "contains_any" and isinstance(v, list) and any(isinstance(x, str) and x.casefold() == o for x in v):
            return {"contains_any": list(v) + [a for a in alts if a.casefold() not in {str(x).casefold() for x in v}]}
        if k == "contains_all" and isinstance(v, list) and any(isinstance(x, str) and x.casefold() == o for x in v):
            rest = [x for x in v if not (isinstance(x, str) and x.casefold() == o)]
            head = {"contains_any": [orig] + alts}
            return {"all_of": [head, {"contains_all": rest}]} if rest else head
        if k == "eq" and isinstance(v, str) and v.strip().casefold() == o:
            return {"in": [v] + alts}
        if k == "in" and isinstance(v, list) and any(isinstance(x, str) and x.casefold() == o for x in v):
            return {"in": list(v) + alts}
        if k == "regex" and isinstance(v, str) and re.search(re.escape(orig), v, re.I):
            return {"regex": re.sub(re.escape(orig), "(?:" + "|".join(re.escape(x) for x in [orig] + alts) + ")",
                                    v, flags=re.I)}
        if k in MATCHERS:
            return {k: _widen(v, orig, alts)} if k in ("all_of", "any_of", "not") else node
    return {k: _widen(v, orig, alts) for k, v in node.items()}


def _widen_case(case: dict, widenings: list[tuple[str, list[str]]]) -> None:
    for h in _check_holders(case):
        for k in CHECK_KEYS:
            if k in h:
                for orig, alts in widenings:
                    h[k] = _widen(h[k], orig, alts)


# ---------------------------------------------------------------------------- text helpers

_QUOTES = [re.compile(r'"[^"]*"'), re.compile("“[^”]*”"),
           re.compile(r"(?<![A-Za-z])'[^']*'(?![A-Za-z])"), re.compile("(?<![A-Za-z])‘[^’]*’(?![A-Za-z])")]
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_URLISH = re.compile(r"\S*(?:[@/\\_#]|\w\.\w|://)\S*")
_DIGITCHUNK = re.compile(r"\S*\d\S*")


def _spans(text: str, *patterns) -> list[tuple[int, int]]:
    return [m.span() for p in patterns for m in p.finditer(text)]


def _overlaps(s: int, e: int, spans) -> bool:
    return any(s < b and a < e for a, b in spans)


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}
_ORD_ONES = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
             "nine": "ninth", "twelve": "twelfth"}


def num_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + (f" {_ONES[o]}" if o else "")
    h, r = divmod(n, 100)
    return f"{_ONES[h]} hundred" + (f" {num_words(r)}" if r else "")


def _ordinal_words(n: int) -> str:
    words = num_words(n).split(" ")
    last = words[-1]
    if last in _ORD_ONES:
        last = _ORD_ONES[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return " ".join(words[:-1] + [last])


def _say_time(h: int, m: int, ampm: str | None) -> str:
    hw = num_words(h)
    if m == 0:
        if ampm:
            return f"{hw} {ampm}"
        return f"{hw} hundred" if h >= 13 or h == 0 else hw
    mw = f"oh {num_words(m)}" if m < 10 else num_words(m)
    return f"{hw} {mw}" + (f" {ampm}" if ampm else "")


_RE_INT = re.compile(r"^\d{1,3}$")
_RE_TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*(am|pm)?$", re.I)
_RE_AMPM = re.compile(r"^(\d{1,2})(am|pm)$", re.I)
_RE_ORD = re.compile(r"^(\d{1,2})(st|nd|rd|th)$", re.I)


def spoken_number(token: str) -> str | None:
    """'21' -> 'twenty one', '3:30' -> 'three thirty', '3pm' -> 'three pm'; None when not safe."""
    if _RE_INT.match(token):
        return num_words(int(token))
    m = _RE_TIME.match(token)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return _say_time(h, mi, (m.group(3) or "").lower() or None) if h < 24 and mi < 60 else None
    m = _RE_AMPM.match(token)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{num_words(int(m.group(1)))} {m.group(2).lower()}"
    m = _RE_ORD.match(token)
    if m and 1 <= int(m.group(1)) <= 31:
        return _ordinal_words(int(m.group(1)))
    return None


# ---------------------------------------------------------------------------- typos

_KEYS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
_NEIGHBOURS: dict[str, str] = {}
for _r, _row in enumerate(_KEYS):
    for _i, _ch in enumerate(_row):
        near = []
        for _dr in (-1, 0, 1):
            rr = _r + _dr
            if 0 <= rr < len(_KEYS):
                for _di in (-1, 0, 1):
                    ii = _i + _di
                    if 0 <= ii < len(_KEYS[rr]) and (rr, ii) != (_r, _i):
                        near.append(_KEYS[rr][ii])
        _NEIGHBOURS[_ch] = "".join(near)

_WORD = re.compile(r"(?<![\w'’])[A-Za-z]{4,}(?![\w'’])")


def typo_word(word: str, rng: random.Random) -> str:
    """One character edit that never touches the first letter and always changes the word."""
    for _ in range(20):
        kind = rng.choice(("swap", "drop", "double", "neighbour"))
        i = rng.randrange(1, len(word))
        if kind == "swap" and i < len(word) - 1 and word[i].lower() != word[i + 1].lower():
            out = word[:i] + word[i + 1] + word[i] + word[i + 2:]
        elif kind == "drop" and len(word) >= 5:
            out = word[:i] + word[i + 1:]
        elif kind == "double":
            out = word[:i] + word[i] + word[i:]
        elif kind == "neighbour":
            near = _NEIGHBOURS.get(word[i].lower())
            if not near:
                continue
            c = rng.choice(near)
            out = word[:i] + (c.upper() if word[i].isupper() else c) + word[i + 1:]
        else:
            continue
        if out.casefold() != word.casefold():
            return out
    return word


def _typo_text(text: str, lits: _Literals, rng: random.Random) -> tuple[str, list[tuple[str, str]]]:
    skip = _spans(text, _PLACEHOLDER, *_QUOTES, _URLISH, _DIGITCHUNK)
    cands = [m for m in _WORD.finditer(text)
             if not _overlaps(m.start(), m.end(), skip) and not lits.protected(m.group())]
    if not cands:
        return text, []
    chosen = [m for m in cands if rng.random() < TYPO_RATE] or [rng.choice(cands)]
    out, edits, last = [], [], 0
    for m in chosen:
        new = typo_word(m.group(), rng)
        if new == m.group():
            continue
        out.append(text[last:m.start()])
        out.append(new)
        last = m.end()
        edits.append((m.group(), new))
    out.append(text[last:])
    return "".join(out), edits


# ---------------------------------------------------------------------------- transcript

FILLERS = ["um", "uh", "like", "so", "okay so", "um like"]
HOMOPHONES = {"there": "their", "their": "there", "here": "hear", "hear": "here", "ok": "okay"}
_TRAIL = ".,!?;:)”\"'’"
_LEAD = "(“\"'‘"


def _transcript_text(text: str, lits: _Literals, rng: random.Random) -> tuple[str, list[tuple[str, list[str]]]]:
    holders: list[str] = []

    def mask(m):
        holders.append(m.group())
        return f"zzph{len(holders) - 1}zz"

    masked = _PLACEHOLDER.sub(mask, text)
    widen: list[tuple[str, list[str]]] = []
    words: list[str] = []
    for chunk in masked.split():
        core = chunk.strip(_TRAIL + _LEAD)
        if not core:
            continue
        spoken = spoken_number(core)
        if spoken is not None:
            if lits.contains_token(core) and core.casefold() not in lits.exact:
                words.append(core)                      # a matcher needs these digits
            else:
                if core.casefold() in lits.exact:        # the matcher is the number alone: accept both
                    widen.append((core, list(dict.fromkeys([spoken, spoken.replace(" ", "-")]))))
                words.append(spoken)
            continue
        if re.search(r"[@/\\_#$%+=]|\w\.\w|\d", core):   # ids, URLs, e-mail, money: leave alone
            words.append(core)
            continue
        w = re.sub(r"['’]", "", core).replace("-", " ")
        w = re.sub(r"[^A-Za-z0-9 ]", "", w).lower()
        if not w:
            continue
        if w in HOMOPHONES and not lits.protected(w) and w not in lits.exact and rng.random() < 0.6:
            w = HOMOPHONES[w]
        words.append(w)
    if rng.random() < 0.75:
        words.insert(0, rng.choice(FILLERS))
    if len(words) > 4 and rng.random() < 0.5:
        words.insert(rng.randrange(2, len(words)), rng.choice(["um", "like", "so"]))
    out = " ".join(words)
    for i, h in enumerate(holders):
        out = out.replace(f"zzph{i}zz", h)
    return out, widen


# ---------------------------------------------------------------------------- paraphrase

STARTERS = {"text", "call", "remind", "add", "set", "what", "whats", "how", "when", "where", "who", "is", "are",
            "do", "does", "can", "could", "would", "will", "send", "tell", "show", "find", "check", "create",
            "make", "book", "move", "cancel", "read", "open", "turn", "put", "start", "list", "search", "look",
            "get", "give", "write", "draft", "reply", "forward", "mark", "save", "schedule", "convert",
            "calculate", "if", "should", "any", "did", "was", "pull", "mute", "lock", "play", "pause", "empty",
            "delete", "remove", "rename", "copy", "email", "message", "ask", "let", "go"}

# (pattern, alternatives). Each is a rewording that keeps the request and the tool it points to.
SWAPS: list[tuple[str, list[str]]] = [
    (r"\bcan you\b", ["could you"]),
    (r"\bcould you\b", ["can you"]),
    (r"\bi need you to\b", ["please"]),
    (r"\bi'd like you to\b", ["please"]),
    (r"\bi want you to\b", ["please"]),
    (r"\bwhat's\b", ["what is"]),
    (r"\bwhat is\b", ["what's"]),
    (r"\bhow's\b", ["how is"]),
    (r"\bis it going to\b", ["will it"]),
    (r"\btell me\b", ["let me know"]),
    (r"\blet me know\b", ["tell me"]),
    (r"\bshow me\b", ["let me see"]),
    (r"\bright away\b", ["immediately"]),
    (r"\bright now\b", ["at the moment"]),
    (r"\basap\b", ["as soon as possible"]),
    (r"\bgo ahead and\b", [""]),
    (r"\bset a timer\b", ["start a timer"]),
    (r"\badd a reminder\b", ["create a reminder"]),
    (r"\badd an event\b", ["create an event"]),
    (r"\badd a calendar event\b", ["create a calendar event"]),
    (r"\badd a note\b", ["create a note"]),
    (r"\bin a while\b", ["a bit later"]),
    (r"\ba couple of\b", ["a few"]),
    (r"\bkeep me posted\b", ["update me"]),
    (r"\bfigure out\b", ["work out"]),
    (r"\bfind out\b", ["check"]),
    (r"\bdone with\b", ["finished with"]),
    (r"\bthanks\b", ["thank you"]),
]
LEAD_INS = ["Hey, ", "Quick one: ", "When you get a moment, ", "Hi there, "]


def _match_case(src: str, repl: str) -> str:
    return repl[:1].upper() + repl[1:] if src[:1].isupper() and repl else repl


def _starter_lower(s: str) -> str:
    first = re.match(r"[A-Za-z']+", s)
    if first and first.group().lower().replace("'", "") in STARTERS:
        return s[:1].lower() + s[1:]
    return s


def _paraphrase_text(text: str, lits: _Literals, rng: random.Random) -> str:
    done = 0
    cur = text
    for pat, alts in rng.sample(SWAPS, len(SWAPS)):
        if done >= 3:
            break
        rx = re.compile(pat, re.I)
        masked = _spans(cur, _PLACEHOLDER, *_QUOTES)
        for m in rx.finditer(cur):
            if _overlaps(m.start(), m.end(), masked):
                continue
            if any(lits.protected(w) or w.casefold() in lits.exact for w in re.findall(r"[A-Za-z]+", m.group())):
                continue
            repl = _match_case(m.group(), rng.choice(alts))
            cur = (cur[:m.start()] + repl + cur[m.end():]).replace("  ", " ")
            cur = cur.lstrip()
            cur = cur[:1].upper() + cur[1:] if text[:1].isupper() else cur
            done += 1
            break
    # polite prefix reordering
    m = re.match(r"^(please[,]?\s+)(.+?)([.!?]?)$", cur, re.I | re.S)
    if m and "?" not in cur and "thank" not in cur.lower() and done < 4:
        rest = m.group(2)
        cur = rest[:1].upper() + rest[1:] + ", please" + (m.group(3) or ".")
        done += 1
    else:
        m = re.match(r"^(.+?)[,]?\s+please([.!?]?)$", cur, re.I | re.S)
        if m and "?" not in cur and "thank" not in cur.lower() and done < 4:
            cur = "Please " + _starter_lower(m.group(1)) + (m.group(2) or ".")
            done += 1
    if cur == text:
        lead = rng.choice(LEAD_INS)
        cur = lead + _starter_lower(text) if text[:1].isupper() else lead.lower() + text
    return cur


# ---------------------------------------------------------------------------- variants


def _clone(case: dict, kind: str) -> dict:
    v = copy.deepcopy(case)
    v["id"] = f"{case['id']}__{VARIANT_SUFFIX[kind]}"
    v["variant_of"] = case["id"]
    v["variant"] = kind
    v.pop("pair_id", None)
    v.pop("core", None)
    tags = [t for t in (v.get("tags") or []) if t != "core"]
    if "noisy" not in tags:
        tags.append("noisy")
    v["tags"] = tags
    return v


def _apply(variant: dict, fn) -> None:
    for holder, key in _slots(variant):
        holder[key] = fn(holder[key])


def make_variants(case: dict, rng: random.Random) -> list[dict]:
    """[typo, transcript, paraphrase] copies of `case`; only the user prompt(s) differ."""
    lits = _Literals(case)

    typo = _clone(case, "typo")
    widenings: list[tuple[str, list[str]]] = []

    def do_typo(text):
        new, edits = _typo_text(text, lits, rng)
        for orig, bad in edits:
            widenings.append((orig, [bad] + lits.contact_extras(orig)))
        return new

    _apply(typo, do_typo)
    # A misspelled name (or any word a matcher names) must still be accepted: the model will
    # copy what it read, so the matcher also takes the misspelling (and, for a contact, the
    # surname or phone tail it may fall back on).
    _widen_case(typo, widenings)

    transcript = _clone(case, "transcript")
    spoken: list[tuple[str, list[str]]] = []

    def do_voice(text):
        new, w = _transcript_text(text, lits, rng)
        spoken.extend(w)
        return new

    _apply(transcript, do_voice)
    _widen_case(transcript, spoken)       # a matcher that is just the number also accepts "twenty one"

    para = _clone(case, "paraphrase")
    _apply(para, lambda t: _paraphrase_text(t, lits, rng))
    para["notes"] = "; ".join(x for x in (case.get("notes"), "review: pending") if x)
    return [typo, transcript, para]


def user_texts(case: dict) -> list[str]:
    return [h[k] for h, k in _slots(case)]


# ---------------------------------------------------------------------------- seeds


def single_turn(case: dict) -> bool:
    return "prompt" in case and "turns" not in case and "sessions" not in case and "followup_turn" not in case


class SeedSet(NamedTuple):
    seeds: list[dict]       # up to n seed cases (unchanged)
    core: list[dict]        # the core subset (30), drawn from `seeds`


def _round_robin(pool: list[dict], k: int, rng: random.Random, prefer=None) -> list[dict]:
    """k cases spread over (lane, sub) buckets; `prefer(case)` cases come first within a bucket."""
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for c in pool:
        buckets[(c.get("lane"), c.get("sub"))].append(c)
    keys = sorted(buckets, key=str)
    rng.shuffle(keys)
    for key in keys:
        rng.shuffle(buckets[key])
        if prefer:
            buckets[key].sort(key=lambda c: 0 if prefer(c) else 1)
    out: list[dict] = []
    while len(out) < k and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(out) < k:
                out.append(buckets[key].pop(0))
    return out


def _quotas(total: int, avail: dict[str, int]) -> dict[str, int]:
    """Equal shares over families, the shortfall of a small family passed to the others."""
    q = {f: 0 for f in avail}
    left = total
    live = [f for f in avail if avail[f] > 0]
    while left > 0 and live:
        share = max(1, left // len(live))
        nxt = []
        for f in live:
            take = min(share, avail[f] - q[f], left)
            q[f] += take
            left -= take
            if q[f] < avail[f]:
                nxt.append(f)
        if left > 0 and not any(q[f] < avail[f] for f in nxt):
            break
        live = nxt
    return q


def select_seeds(cases: list[dict], rng: random.Random, n: int = 150, *, n_core: int = 30) -> SeedSet:
    """Seed cases for the noisy runs: Select, Sequential, Parallel, Abstain and Restraint,
    single-turn only, stratified by family (then by lane and sub-family), plus a core subset."""
    pool = [c for c in sorted(cases, key=lambda c: c["id"])
            if c.get("family") in SEED_FAMILIES and single_turn(c) and not c.get("variant_of")
            and "noisy" not in (c.get("tags") or [])]
    by_fam: dict[str, list[dict]] = {f: [c for c in pool if c["family"] == f] for f in SEED_FAMILIES}
    quota = _quotas(n, {f: len(v) for f, v in by_fam.items()})
    seeds: list[dict] = []
    for f in SEED_FAMILIES:
        seeds.extend(_round_robin(by_fam[f], quota[f], rng))
    core_quota = _quotas(n_core, {f: sum(1 for c in seeds if c["family"] == f) for f in SEED_FAMILIES})
    core: list[dict] = []
    for f in SEED_FAMILIES:
        core.extend(_round_robin([c for c in seeds if c["family"] == f], core_quota[f], rng,
                                 prefer=lambda c: "core" in (c.get("tags") or [])))
    return SeedSet(seeds, core)


@dataclass
class VariantSet:
    variants: list[dict] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)   # (variant id, why)
    core_ids: set[str] = field(default_factory=set)                # variants of the core seeds


def build_variants(seedset: SeedSet | list[dict], rng: random.Random, *, check: bool = True,
                   seeds=(1, 2, 3)) -> VariantSet:
    """All three variants of every seed. A variant is dropped (and reported) when its prompt equals
    its seed's, or when the oracle fails it, or when the null model passes it."""
    from edat.cases.oracle import check_case
    seeds_list, core = (seedset.seeds, seedset.core) if isinstance(seedset, SeedSet) else (list(seedset), [])
    core_ids = {c["id"] for c in core}
    out = VariantSet()
    for seed in seeds_list:
        for v in make_variants(seed, rng):
            if user_texts(v) == user_texts(seed):
                out.dropped.append((v["id"], "prompt unchanged"))
                continue
            if check:
                res = check_case(v, seeds=seeds)
                if not res.ok:
                    out.dropped.append((v["id"], "; ".join(res.problems)[:300]))
                    continue
            out.variants.append(v)
            if seed["id"] in core_ids:
                out.core_ids.add(v["id"])
    return out


# ---------------------------------------------------------------------------- metrics


def _rate(rows: list[dict]) -> float | None:
    return sum(bool(r["success"]) for r in rows) / len(rows) if rows else None


def consistency(rows: list[dict]) -> dict:
    """Noise robustness and noise consistency from verdict rows.

    A row has `case_id`, `variant_of` (None/absent on a clean row), `variant`
    (typo|transcript|paraphrase), `trial`, `success` (excluded rows, `excluded: true`, are ignored).

    robustness[kind] = success(variant) / success(clean), the clean rate taken over the seeds
    that have that variant (so a dropped variant does not bias it). consistency = the share of
    (seed, trial) pairs whose verdict is the same for the clean case and all three variants,
    over the pairs that have all four rows.
    """
    rows = [r for r in rows if not r.get("excluded")]
    clean = {(r["case_id"], r["trial"]): bool(r["success"]) for r in rows if not r.get("variant_of")}
    var: dict[str, dict[tuple, bool]] = {k: {} for k in VARIANT_KINDS}
    for r in rows:
        if r.get("variant_of") and r.get("variant") in var:
            var[r["variant"]][(r["variant_of"], r["trial"])] = bool(r["success"])
    robustness = {}
    for k in VARIANT_KINDS:
        pairs = [key for key in var[k] if key in clean]
        v = sum(var[k][key] for key in pairs)
        c = sum(clean[key] for key in pairs)
        n = len(pairs)
        robustness[k] = {"n": n, "variant_rate": v / n if n else None, "clean_rate": c / n if n else None,
                         "robustness": (v / c) if c else None}
    full = [key for key in clean if all(key in var[k] for k in VARIANT_KINDS)]
    same = sum(1 for key in full if len({clean[key]} | {var[k][key] for k in VARIANT_KINDS}) == 1)
    return {"robustness": robustness,
            "consistency": {"pairs": len(full), "same": same, "value": same / len(full) if full else None}}


def rows_for(results) -> list[dict]:
    """Verdict rows (`aggregate.rows_from`) plus `variant_of` and `variant`, ready for `consistency`."""
    from edat.grading.aggregate import rows_from
    rows = rows_from(results)
    for row, r in zip(rows, results):
        row["variant_of"] = r.case.get("variant_of")
        row["variant"] = r.case.get("variant")
    return rows
