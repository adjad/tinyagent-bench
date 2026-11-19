"""The A3 spoken-command families and their render plan (spec 05 §5 A3).

Every family is generated from a seed and a count, never by hand:

=================  =====================================================  ==========
family             what                                                   clips
=================  =====================================================  ==========
single             speakable agentic cases (:func:`speakable_cases`),      ~1 per case
                   speakable Toolbench cases (:func:`toolbench_cases`),
                   500 entity-dense commands (commands.entity_commands)
voice_variance     200 single commands x 12 voices                         2,400
follow_ups         400 dialogues of 2-4 spoken turns (dialogue.py)         ~1,200
corrections        500 mid-utterance corrections, with abandoned values    500
disfluency         500 fillers / restarts / repetitions / long pauses /    500
                   trailing off / run-on, seeded from Toolbench voice.yaml
noisy              the single set at SNR 20, 10 and 5 dB, six noise types  ~4,500
=================  =====================================================  ==========

:func:`speak` verbalises a typed command (numbers, times, dates, money,
percentages, emails, phone numbers, codes): spoken forms carry no digits.
:func:`a3_plan` is the deterministic render plan (one :class:`ClipSpec` per
clip, voices rotated so the 12 get equal shares) and :func:`materialise` turns
a spec into audio with any :class:`~edat.media.audio.tts.SpeechEngine`.

``python -m edat.media.audio.spoken_cases counts`` prints the family summary.
The agentic cases live with the agentic tests (``test_fixtures/agentic/dev``);
pass ``--agentic-dir`` (or set ``EDAT_AGENTIC_DIR``) when they are not on this
branch.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np

from edat.media.audio import commands as cmd
from edat.media.audio.commands import (CommandCase, Entity, address_entity, correction_ok,
                                       entity_case, entity_commands, say_digits, say_email,
                                       say_int, say_money, say_ordinal, say_phone, say_street,
                                       say_time, strip_markers)
from edat.media.audio.dialogue import SpokenDialogue, SpokenTurn
from edat.media.audio import mix as _mix
from edat.media.audio.mix import GENERATED_NOISES, RECORDED_NOISES
from edat.media.common import (GIVEN_NAMES, MONTHS, WEEKDAYS, Inventor, canon_phone, canon_text,
                               date_long, rng_for, time_12h)

__all__ = ["A3_SEED", "FAMILIES", "ClipSpec", "A3Suite", "speak", "speakable_cases",
           "convert_agentic", "toolbench_cases", "entity_dense", "voice_variance_ids",
           "follow_ups", "corrections", "disfluency", "noisy_plan", "build_suite", "a3_plan",
           "materialise", "correction_ok", "counts"]

A3_SEED = cmd.COMMANDS_SEED
FAMILIES = ("single", "voice_variance", "follow_ups", "corrections", "disfluency", "noisy")
N_VOICES = 12
VOICE_VARIANCE_N = 200
FOLLOW_UP_N = 400
CORRECTION_N = 500
DISFLUENCY_N = 500
NOISY_SNR_DB = (20, 10, 5)
# §3A noise types, rotated evenly. Recorded types (cafe, street, tv_speech, music) have no
# approved set yet, so each is a generated stand-in: mix.generate_a4_noise when mix.py has
# it, else the nearest v1 generator (NOISE_STAND_INS). The plan records which.
SPEC_NOISES = ("cafe", "street", "keyboard", "fan", "tv_speech", "music")
NOISE_STAND_INS = {"cafe": "babble", "street": "brown", "keyboard": "keyboard", "fan": "fan",
                   "tv_speech": "babble", "music": "pink"}
HAS_A4_NOISE = hasattr(_mix, "generate_a4_noise")
RATES = (0.9, 1.0, 1.0, 1.1)
REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLBENCH_CASES_DIR = REPO_ROOT / "scripts" / "toolbench" / "cases"
AGENTIC_DEV_DIR = REPO_ROOT / "test_fixtures" / "agentic" / "dev"
AGENTIC_CLOCK = "2026-11-18T08:30"            # edat/state/clock.py DEFAULT_CLOCK
TOOLBENCH_TODAY = date(2026, 10, 29)          # scripts/toolbench/world.py CANONICAL_TODAY
STUB_VOICES = tuple(f"stub-{i:02d}" for i in range(N_VOICES))


# ------------------------------------------------------------------ verbalising

_ABBR = {"tmrw": "tomorrow", "tmrow": "tomorrow", "tmr": "tomorrow", "pls": "please",
         "plz": "please", "u": "you", "ur": "your", "wat": "what", "calender": "calendar",
         "scedule": "schedule", "mins": "minutes", "min": "minutes", "hrs": "hours", "hr": "hour",
         "km": "kilometers", "kg": "kilograms", "lbs": "pounds", "lb": "pounds",
         "mg": "milligrams", "ml": "milliliters", "btw": "by the way", "asap": "as soon as possible",
         "usd": "dollars", "eur": "euros", "gbp": "pounds", "approx": "about", "vs": "versus",
         "w/": "with", "thx": "thanks"}
_UNITS = {"kg": "kilograms", "km": "kilometers", "mg": "milligrams", "ml": "milliliters",
          "lb": "pounds", "lbs": "pounds", "min": "minutes", "mins": "minutes", "h": "hours",
          "hr": "hours", "hrs": "hours", "g": "grams", "G": "gigabytes", "GB": "gigabytes",
          "gb": "gigabytes", "TB": "terabytes", "k": "thousand", "K": "thousand", "x": "times",
          "pm": "p m", "am": "a m"}
_CODE_WORDS = {"code", "pin", "number", "ref", "reference", "card", "ending", "extension",
               "unit", "room", "flight", "order", "id", "is"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+\d{1,2}[\s.-]?)?(?:\(\d{3}\)\s?|\b\d{3}[\s.-])?\b\d{3}[\s.-]\d{4}\b")
RANGE_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:-|–|to)\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?",
                      re.I)
AMPM_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.I)
CLOCK_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
MONEY_RE = re.compile(r"\$\s?(\d[\d,]*)(?:\.(\d{1,2}))?")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
DEGREE_RE = re.compile(r"(\d+(?:\.\d+)?)\s?°\s?([CF])\b")
ORDINAL_RE = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def say_year(n: int) -> str:
    if 2000 <= n <= 2009:
        return "two thousand" + ("" if n == 2000 else " " + say_int(n - 2000))
    hi, lo = divmod(n, 100)
    return f"{say_int(hi)} {say_int(lo) if lo >= 10 else ('hundred' if lo == 0 else 'oh ' + say_int(lo))}"


def _say_number(tok: str, prev: str) -> str:
    """One numeric token: decimals, comma groups, years, codes, plain integers."""
    if "." in tok:
        whole, frac = tok.split(".", 1)
        return f"{_say_number(whole, '')} point {say_digits(frac)}"
    plain = tok.replace(",", "")
    n = int(plain)
    if "," in tok:
        return say_int(n)
    if len(plain) >= 4 or plain.startswith("0") and len(plain) > 1:
        if 1900 <= n <= 2099 and prev not in _CODE_WORDS and not plain.startswith("0"):
            return say_year(n)
        return say_digits(plain)
    return say_int(n)


def _say_bare_clock(h: int, m: int) -> str:
    if m == 0:
        return f"{say_int(h)} o'clock"
    return f"{say_int(h)} " + (f"oh {say_int(m)}" if m < 10 else say_int(m))


def _say_token(tok: str, prev: str) -> str:
    """A whitespace token that still holds digits after the pattern passes."""
    m = re.fullmatch(r"([(\"']*)(.*?)([)\"'.,;:!?]*)", tok)
    lead, core, trail = m.groups()
    if not re.search(r"\d", core):
        return tok
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", core):
        return lead + _say_number(core, prev) + trail
    u = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]+)", core)
    if u and (u.group(2) in _UNITS or u.group(2).lower() in _UNITS):
        unit = _UNITS.get(u.group(2)) or _UNITS[u.group(2).lower()]
        return f"{lead}{_say_number(u.group(1), prev)} {unit}{trail}"
    r = re.fullmatch(r"(\d+)-(\d+)", core)
    if r:
        return f"{lead}{say_int(int(r.group(1)))} to {say_int(int(r.group(2)))}{trail}"
    # A code: letters spelled, digits read one by one.
    out = []
    for piece in re.findall(r"[A-Za-z]+|\d+", core):
        if piece.isdigit():
            out.append(say_digits(piece))
        elif piece.isupper() or len(piece) <= 2:
            out.append(" ".join(piece.upper()))
        else:
            out.append(piece)
    return lead + " ".join(out) + trail


def speak(text: str, *, rng: random.Random | None = None) -> str:
    """Verbalise a typed command: what a person says aloud. The result holds no digits."""
    r = rng or random.Random(0)
    s = text.replace("’", "'").replace("“", "").replace("”", "").replace('"', "")
    s = EMAIL_RE.sub(lambda m: say_email(m.group(0)), s)

    def phone(m):
        groups = re.findall(r"\d+", m.group(0))
        lead = "plus " if m.group(0).lstrip().startswith("+") else ""
        return lead + ", ".join(say_digits(g) for g in groups)
    s = PHONE_RE.sub(phone, s)

    def rng_range(m):
        h1, m1, h2, m2, ap = m.groups()
        h2i, m2i = int(h2), int(m2 or 0)
        hh = (h2i % 12) + (12 if ap.lower() == "p" else 0)
        first = say_int(int(h1)) if not m1 or int(m1) == 0 else _say_bare_clock(int(h1), int(m1))
        return f"{first} to {say_time(f'{hh:02d}:{m2i:02d}', 0)}"
    s = RANGE_RE.sub(rng_range, s)

    def ampm(m):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        if not 1 <= h <= 12:
            return m.group(0)
        hh = (h % 12) + (12 if ap == "p" else 0)
        return say_time(f"{hh:02d}:{mi:02d}", r.choice((0, 0, 1)))
    s = AMPM_RE.sub(ampm, s)

    def clock(m):
        h, mi = int(m.group(1)), int(m.group(2))
        if h >= 24 or mi >= 60:
            return m.group(0)
        if h >= 13 or h == 0:
            return say_time(f"{h:02d}:{mi:02d}", r.choice((0, 2)))
        return _say_bare_clock(h, mi)
    s = CLOCK_RE.sub(clock, s)

    def money(m):
        dollars = int(m.group(1).replace(",", ""))
        cents = int((m.group(2) or "0").ljust(2, "0"))
        return say_money(dollars + cents / 100)
    s = MONEY_RE.sub(money, s)
    s = DEGREE_RE.sub(lambda m: f"{_say_number(m.group(1), '')} degrees "
                      f"{'celsius' if m.group(2) == 'C' else 'fahrenheit'}", s)
    s = s.replace("°", " degrees")
    s = PERCENT_RE.sub(lambda m: f"{_say_number(m.group(1), '')} percent", s)
    s = ORDINAL_RE.sub(lambda m: say_ordinal(int(m.group(1))), s)
    s = ISO_RE.sub(lambda m: f"{MONTHS[int(m.group(2)) - 1]} {say_ordinal(int(m.group(3)))}, "
                   f"{say_year(int(m.group(1)))}", s)
    toks = s.split()
    out = []
    for k, tok in enumerate(toks):
        prev = canon_text(toks[k - 1]) if k else ""
        out.append(_say_token(tok, prev))
    s = " ".join(out)
    s = re.sub(r"\d", lambda m: " " + say_digits(m.group(0)) + " ", s)     # nothing left behind
    for sym, word in (("&", " and "), ("+", " plus "), ("=", " equals "), ("#", " number "),
                      ("×", " times "), ("/", " slash "), ("@", " at ")):
        s = s.replace(sym, word)
    s = re.sub(r"(?<![\w'])([a-z]+)(?![\w'])", lambda m: _ABBR.get(m.group(1), m.group(1)), s)
    return re.sub(r"\s+", " ", s).strip()


def has_digits(text: str) -> bool:
    return bool(re.search(r"\d", strip_markers(text)))


# ------------------------------------------------------------------ speakability

URL_RE = re.compile(r"https?://|\bwww\.|\b[\w-]+\.(?:com|org|net|io|test|dev)/\S*", re.I)
PATH_RE = re.compile(r"(?:^|\s)(?:~|\.{1,2})?/[\w./-]+|\b[\w-]+\.(?:txt|pdf|zip|json|csv|png|jpe?g|"
                     r"docx?|xlsx?|md|py|sh|ics|pptx?|heic|mov|mp3|wav)\b", re.I)
CODE_RE = re.compile(r"`|[{}\[\]<>|\\]|=|\b[A-Za-z0-9]+_[A-Za-z0-9_]+\b")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
MEDIA_KEYS = ("attachments", "attachment", "media", "image", "images", "audio", "files_in")
MULTI_TURN_KEYS = ("history", "followup_turn", "turns", "sessions")


def unspeakable_reason(prompt: str, raw: dict | None = None) -> str | None:
    """Why a bound prompt cannot be spoken as one command, or None."""
    raw = raw or {}
    if any(raw.get(k) for k in MEDIA_KEYS):
        return "attachment"
    if any(raw.get(k) for k in MULTI_TURN_KEYS):
        return "multi_turn"
    if PLACEHOLDER.search(prompt):
        return "unbound_placeholder"
    if URL_RE.search(prompt):
        return "url"
    if PATH_RE.search(prompt):
        return "file_path"
    if CODE_RE.search(prompt):
        return "code"
    if not prompt.isascii() and re.search(r"[^\x00-\x7f°’“”–—éèáàóíúñçäöüåøłš]", prompt):
        return "non_english_script"
    return None


@dataclass
class Conversion:
    source: str
    total: int
    cases: list[CommandCase]
    dropped: dict[str, list[str]]           # reason -> case ids

    @property
    def speakable(self) -> int:
        return len(self.cases)

    def report(self) -> dict:
        return {"source": self.source, "total": self.total, "speakable": self.speakable,
                "dropped": {k: len(v) for k, v in sorted(self.dropped.items())}}


# ------------------------------------------------------------------ agentic binding (local)

_REL_DAY = re.compile(r"^\s*([+-])(\d+)d(?:\s+(\d{1,2}):(\d{2}))?\s*$")
_REL_SPAN = re.compile(r"^\s*([+-])(\d+)(h|m)\s*$")
_INLINE_REL = re.compile(r"\{rel:([^}]+)\}")


class Unbound(ValueError):
    pass


def _rel(value: str, now: datetime) -> str:
    m = _REL_DAY.match(value)
    if m:
        sign, n, hh, mm = m.groups()
        day = now.date() + timedelta(days=int(n) * (1 if sign == "+" else -1))
        return day.isoformat() if hh is None else f"{day.isoformat()}T{int(hh):02d}:{mm}"
    m = _REL_SPAN.match(value)
    if m:
        sign, n, unit = m.groups()
        d = timedelta(hours=int(n)) if unit == "h" else timedelta(minutes=int(n))
        return (now + d if sign == "+" else now - d).strftime("%Y-%m-%dT%H:%M")
    raise ValueError(value)


def _resolve(obj, now: datetime):
    if isinstance(obj, dict):
        return {k: _resolve(v, now) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(v, now) for v in obj]
    if isinstance(obj, str):
        if _REL_DAY.match(obj) or _REL_SPAN.match(obj):
            return _rel(obj, now)
        return _INLINE_REL.sub(lambda m: _rel(m.group(1), now), obj)
    return obj


def agentic_clock(case: dict) -> datetime:
    world = case.get("world") or {}
    return datetime.fromisoformat(world.get("clock") or AGENTIC_CLOCK).replace(tzinfo=None)


def agentic_facts(case: dict, world_seed: int) -> dict:
    """The case's facts for one world seed, drawn exactly as ``edat/cases/bind.py`` draws them."""
    now = agentic_clock(case)
    t = now.date()
    facts = {"today_iso": t.isoformat(), "today_weekday": t.strftime("%A"),
             "now_hhmm": now.strftime("%H:%M")}
    for n, name in ((1, "tomorrow"), (2, "in_2_days"), (3, "in_3_days"), (7, "in_7_days"),
                    (-1, "yesterday")):
        d = t + timedelta(days=n)
        facts[f"{name}_iso"] = d.isoformat()
        facts[f"{name}_weekday"] = d.strftime("%A")
    key = case.get("variant_of") or case["id"]
    rng = random.Random(int(hashlib.sha256(f"{key}|{world_seed}".encode()).hexdigest()[:16], 16))
    for name, spec in (case.get("facts") or {}).items():
        facts[name] = _gen_fact(spec, facts, rng, name)
    return facts


def _gen_fact(spec, facts: dict, rng: random.Random, name: str):
    kinds = [k for k in spec if k not in ("fmt", "exclude", "step_min")]
    if len(kinds) != 1:
        raise Unbound(f"fact {name}: {spec!r}")
    kind, arg = kinds[0], spec[kinds[0]]
    excluded = {facts[e] for e in spec.get("exclude", []) if e in facts}
    if kind == "choice":
        value = rng.choice([o for o in arg if o not in excluded])
    elif kind == "int":
        lo, hi = arg
        value = rng.choice([v for v in range(int(lo), int(hi) + 1) if v not in excluded])
    elif kind == "id":
        value = f"{arg}-{rng.randint(1000, 9999)}"
    elif kind == "code":
        value = "".join(str(rng.randint(0, 9)) if ch == "#" else
                        chr(rng.randint(65, 90)) if ch == "@" else ch for ch in str(arg))
    elif kind == "add":
        base, n = arg
        value = facts[base] + n
    elif kind == "template":
        value = str(arg).format(**facts)
    elif kind in ("argmax", "argmin"):
        pairs = [(facts[f], k) for k, f in arg.items()]
        value = (max if kind == "argmax" else min)(pairs)[1]
    elif kind == "same_as":
        value = facts[arg]
    elif kind == "time":
        lo, hi = arg
        step = int(spec.get("step_min", 15))
        slots = [h * 60 + m for h in range(int(lo), int(hi) + 1) for m in range(0, 60, step)]
        slots = [s for s in slots if f"{s // 60:02d}:{s % 60:02d}" not in excluded]
        s = rng.choice(slots)
        value = f"{s // 60:02d}:{s % 60:02d}"
    else:
        raise Unbound(f"fact {name}: unknown generator {kind!r}")
    if "fmt" in spec:
        value = format(value, spec["fmt"])
    return value


def fill(obj, facts: dict, *, strict: bool = True):
    """Fill ``{fact}`` placeholders; a whole-string placeholder keeps the fact's type."""
    if isinstance(obj, dict):
        return {k: (v if k == "trap" else fill(v, facts, strict=strict)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill(v, facts, strict=strict) for v in obj]
    if isinstance(obj, str):
        whole = PLACEHOLDER.fullmatch(obj)
        if whole and whole.group(1) in facts:
            return facts[whole.group(1)]

        def rep(m):
            if m.group(1) in facts:
                return str(facts[m.group(1)])
            if strict:
                raise Unbound(m.group(1))
            return m.group(0)
        return PLACEHOLDER.sub(rep, obj)
    return obj


def _rng(*labels) -> random.Random:
    return rng_for(A3_SEED, "a3-spoken", *labels)


# ------------------------------------------------------------------ single: agentic

def convert_agentic(case_dicts: Iterable[dict], *, world_seed: int = 1) -> Conversion:
    """Every speakable single-turn agentic case as a spoken command (bound for ``world_seed``)."""
    cases, dropped, total = [], {}, 0
    for raw in case_dicts:
        total += 1
        try:
            facts = agentic_facts(raw, world_seed)
            now = agentic_clock(raw)
            prompt = _resolve(fill(str(raw["prompt"]), facts), now)
        except (Unbound, KeyError, ValueError, IndexError) as exc:
            dropped.setdefault("unbound_placeholder", []).append(raw.get("id", "?"))
            continue
        why = unspeakable_reason(prompt, raw)
        if why:
            dropped.setdefault(why, []).append(raw["id"])
            continue
        try:
            expect = _resolve(fill(copy.deepcopy(raw.get("expect") or []), facts), now)
            forbid = list(fill(raw.get("forbid") or [], facts))
        except Unbound:
            dropped.setdefault("unbound_placeholder", []).append(raw["id"])
            continue
        spoken = speak(prompt, rng=_rng("agentic", raw["id"]))
        lane = raw.get("lane", "assistant")
        cases.append(CommandCase(
            f"a3-ag-{raw['id']}", prompt, spoken, expect, [], today=now.date().isoformat(),
            source="agentic", tags=list(raw.get("tags") or []) + [f"family:{raw.get('family')}",
                                                                  f"lane:{lane}"],
            forbid=forbid, no_tool=bool(raw.get("no_tool")), tool_menu=f"agentic:{lane}",
            system_policy=str(fill(raw.get("system_policy") or "", facts, strict=False)),
            origin={"source": "agentic", "case_id": raw["id"], "world_seed": world_seed,
                    "facts": {k: facts[k] for k in PLACEHOLDER.findall(str(raw["prompt"]))
                              if k in facts},
                    "expect": raw.get("expect"), "case": raw}))
    return Conversion("agentic", total, cases, dropped)


def speakable_cases(case_dicts: Iterable[dict], *, world_seed: int = 1) -> list[CommandCase]:
    """The speakable single-turn agentic cases as spoken commands (spec 05 §5 A3 single (a)).

    Drops attachments, multi-turn cases and prompts with URLs, file paths or
    code; binds ``{fact}`` placeholders for ``world_seed`` the way the agentic
    harness does; keeps the original ``expect`` (``origin["expect"]``, bound in
    ``expect``) for the agentic matchers.
    """
    return convert_agentic(case_dicts, world_seed=world_seed).cases


def load_agentic(directory: str | Path | None = None) -> list[dict] | None:
    """The agentic dev cases from ``directory`` (default: EDAT_AGENTIC_DIR or the repo), or None."""
    import yaml
    d = Path(directory or os.environ.get("EDAT_AGENTIC_DIR") or AGENTIC_DEV_DIR)
    files = sorted(d.glob("*/*.yaml")) or sorted(d.glob("*.yaml"))
    if not files:
        return None
    out = []
    for f in files:
        out.extend(yaml.safe_load(f.read_text()) or [])
    return out


# ------------------------------------------------------------------ single: Toolbench

def _toolbench_raw(directory: str | Path | None = None) -> list[dict]:
    import yaml
    out = []
    for path in sorted(Path(directory or TOOLBENCH_CASES_DIR).glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text()) or []:
            out.append({**raw, "_category": path.stem})
    return out


def toolbench_facts() -> dict:
    """The canonical gate world's facts (``scripts/toolbench/world.make_facts``)."""
    from scripts.toolbench.world import make_facts
    return make_facts(None, TOOLBENCH_TODAY)


def toolbench_cases(directory: str | Path | None = None) -> Conversion:
    """Toolbench's cases bound in the canonical world, the speakable single-turn ones kept."""
    facts = toolbench_facts()
    cases, dropped, raws = [], {}, _toolbench_raw(directory)
    for raw in raws:
        if "{city_typo}" in raw["prompt"]:
            dropped.setdefault("typed_typo", []).append(raw["id"])
            continue
        prompt = fill(raw["prompt"], facts, strict=False)
        why = unspeakable_reason(prompt, raw)
        if why:
            dropped.setdefault(why, []).append(raw["id"])
            continue
        cases.append(CommandCase(
            f"a3-tb-{raw['id']}", prompt, speak(prompt, rng=_rng("toolbench", raw["id"])),
            fill(copy.deepcopy(raw.get("expect") or []), facts, strict=False), [],
            today=TOOLBENCH_TODAY.isoformat(), source="toolbench",
            tags=[f"category:{raw['_category']}", f"split:{raw.get('split', 'gate')}"],
            forbid=list(raw.get("forbid") or []), no_tool=bool(raw.get("no_tool")),
            tool_menu="toolbench",
            origin={"source": "toolbench", "case_id": raw["id"], "world": "canonical",
                    "expect": raw.get("expect"), "answer": raw.get("answer"),
                    "confirm": raw.get("confirm"), "world_access": raw.get("world")}))
    return Conversion("toolbench", len(raws), cases, dropped)


# ------------------------------------------------------------------ single: entity-dense

def entity_dense(n: int = cmd.ENTITY_SET_SIZE, *, seed: str = A3_SEED) -> list[CommandCase]:
    return entity_commands(n, seed=seed)


# ------------------------------------------------------------------ voice variance

def voice_variance_ids(singles: list[CommandCase], *, n: int = VOICE_VARIANCE_N,
                       seed: str = A3_SEED) -> list[str]:
    """``n`` single commands for the 12-voice subset: a seeded sample, half from the
    entity-dense set and half from the converted cases when both exist."""
    r = rng_for(seed, "a3-voice-variance")
    ent = sorted(c.case_id for c in singles if c.source == "entity_dense")
    conv = sorted(c.case_id for c in singles if c.source != "entity_dense")
    k_conv = min(len(conv), n // 2)
    picked = r.sample(conv, k_conv) + r.sample(ent, min(len(ent), n - k_conv))
    return sorted(picked)


# ------------------------------------------------------------------ follow-ups

EVENT_TITLES = ("dentist", "piano lesson", "team lunch", "yoga class", "book club",
                "pottery class", "haircut", "budget review", "parent meeting", "climbing session")
REMINDER_TASKS = (("water the plants", "plants"), ("renew the parking permit", "permit"),
                  ("call the plumber", "plumber"), ("return the library books", "library"),
                  ("pick up the dry cleaning", "cleaning"), ("book the vet", "vet"),
                  ("pay the electricity bill", "electricity"), ("order more coffee", "coffee"))
BEFORE = ((60, "an hour before"), (30, "half an hour before"), (15, "fifteen minutes before"),
          (120, "two hours before"))


def _day(wd: int) -> date:
    return cmd._weekday_after(cmd.TODAY, wd)


def _minus(hhmm: str, minutes: int) -> str:
    h, m = map(int, hhmm.split(":"))
    t = h * 60 + m - minutes
    return f"{t // 60:02d}:{t % 60:02d}"


def _turn(text: str, expect, entities=(), *, r: random.Random, oracle=(), results=None,
          reply="Done.", state=None, tags=(), **kw) -> SpokenTurn:
    return SpokenTurn(text, speak(text, rng=r), expect, list(entities), oracle=list(oracle),
                      results=dict(results or {}), reply=reply, state=dict(state or {}),
                      tags=list(tags), **kw)


def _ev_id(r) -> str:
    return f"ev-{r.randint(1000, 9999)}"


def _fu_event_chain(r, inv) -> tuple[list[SpokenTurn], dict]:
    title = r.choice(EVENT_TITLES)
    key = title.split()[0]
    wd1, wd2 = r.sample(range(7), 2)
    d1, d2 = _day(wd1), _day(wd2)
    hhmm = inv.clock(9, 18, 30)
    ev = {"id": _ev_id(r), "title": title.capitalize(), "date": d1.isoformat(), "start_time": hhmm}
    moved = {**ev, "date": d2.isoformat()}
    mins, phrase = r.choice(BEFORE)
    name = inv.given()
    turns = [
        _turn(f"Add {title} on {WEEKDAYS[wd1]} at {time_12h(hhmm)}",
              [{"tool": "add_calendar_event", "args": {"title": {"icontains": key},
                                                       "date": d1.isoformat()}}],
              [Entity("date", d1.isoformat(), "date"), Entity("time", hhmm, "start_time")], r=r,
              oracle=[{"name": "add_calendar_event", "arguments": {
                  "title": ev["title"], "date": ev["date"], "start_time": hhmm}}],
              results={"add_calendar_event": f"Added {ev['title']} on {date_long(d1)} at "
                       f"{time_12h(hhmm)} (id {ev['id']})."},
              reply=f"Added {title} on {WEEKDAYS[wd1]} at {time_12h(hhmm)}.",
              state={"calendar": [ev]}),
        _turn(r.choice(("Move it to {d}", "Actually, push it to {d}", "Can you move that to {d}"))
              .format(d=WEEKDAYS[wd2]),
              [{"tool": "update_calendar_event", "args": {"title": {"icontains": key},
                                                          "date": d2.isoformat()}}],
              [Entity("date", d2.isoformat(), "date")], r=r,
              oracle=[{"name": "update_calendar_event", "arguments": {
                  "title": ev["title"], "date": d2.isoformat()}}],
              results={"update_calendar_event": f"Moved {ev['title']} to {date_long(d2)} at "
                       f"{time_12h(hhmm)}."},
              reply=f"Moved it to {WEEKDAYS[wd2]}.", state={"calendar": [moved]},
              tags=["refers_back"]),
        _turn(f"And remind me {phrase}",
              [{"tool": "add_reminder", "args": {"date": d2.isoformat()}}],
              [Entity("date", d2.isoformat(), "date"), Entity("time", _minus(hhmm, mins), "time")],
              r=r, oracle=[{"name": "add_reminder", "arguments": {
                  "text": ev["title"], "date": d2.isoformat(), "time": _minus(hhmm, mins)}}],
              results={"add_reminder": f"Reminder set for {date_long(d2)} at "
                       f"{time_12h(_minus(hhmm, mins))}."},
              state={"calendar": [moved], "reminders": [{"text": ev["title"], "date": d2.isoformat(),
                                                         "time": _minus(hhmm, mins)}]},
              tags=["refers_back", "relative_time"]),
        _turn(f"And text {name} the new day",
              [{"tool": "send_message", "args": {"to": {"icontains": name}}}],
              [Entity("name", canon_text(name), "to")], r=r,
              oracle=[{"name": "send_message", "arguments": {
                  "to": name, "body": f"{ev['title']} is now on {WEEKDAYS[wd2]}."}}],
              results={"send_message": f"Sent to {name}."}, tags=["refers_back"]),
    ]
    return turns, {}


def _fu_other_one(r, inv) -> tuple[list[SpokenTurn], dict]:
    title = r.choice(("Team lunch", "Piano lesson", "Coffee with Bram", "Physio session"))
    key = title.split()[0].lower() if title.split()[0] != "Coffee" else "coffee"
    wd1, wd2 = sorted(r.sample(range(7), 2), key=lambda w: _day(w))
    d1, d2 = _day(wd1), _day(wd2)
    orig1, orig2 = inv.clock(10, 13, 30), inv.clock(10, 13, 30)
    new = inv.clock(14, 17, 30)
    world = {"calendar": [{"id": _ev_id(r), "title": title, "date": d1.isoformat(), "start_time": orig1},
                          {"id": _ev_id(r), "title": title, "date": d2.isoformat(), "start_time": orig2}]}
    person = inv.given()
    turns = [
        _turn(f"Move my {title.lower()} to {time_12h(new)}",
              [{"tool": "update_calendar_event", "args": {"title": {"icontains": key},
                                                          "start_time": new}}],
              [Entity("time", new, "start_time")], r=r,
              oracle=[{"name": "update_calendar_event", "arguments": {
                  "title": title, "current_date": d1.isoformat(), "start_time": new}}],
              results={"update_calendar_event": f"Two events are called {title}. Moved the one on "
                       f"{date_long(d1)} to {time_12h(new)}."},
              reply=f"Moved {WEEKDAYS[wd1]}'s {title.lower()} to {time_12h(new)}.",
              state={"calendar": [{**world["calendar"][0], "start_time": new}, world["calendar"][1]]},
              tags=["ambiguous_title"]),
        _turn(r.choice(("No, the other one", "No, not that one, the other one",
                        "Sorry, I meant the other one")),
              [{"tool": "update_calendar_event", "args": {"title": {"icontains": key},
                                                          "current_date": d2.isoformat(),
                                                          "start_time": new}}],
              [Entity("date", d2.isoformat(), "current_date"), Entity("time", new, "start_time")],
              r=r, oracle=[{"name": "update_calendar_event", "arguments": {
                  "title": title, "current_date": d2.isoformat(), "start_time": new}}],
              results={"update_calendar_event": f"Moved {title} on {date_long(d2)} to "
                       f"{time_12h(new)}."},
              reply=f"Moved {WEEKDAYS[wd2]}'s one to {time_12h(new)} instead.",
              state={"calendar": [{**world["calendar"][0], "start_time": new},
                                  {**world["calendar"][1], "start_time": new}]},
              tags=["refers_back", "the_other_one"]),
        _turn(f"And put the {WEEKDAYS[wd1]} one back at {time_12h(orig1)}",
              [{"tool": "update_calendar_event", "args": {"title": {"icontains": key},
                                                          "current_date": d1.isoformat(),
                                                          "start_time": orig1}}],
              [Entity("date", d1.isoformat(), "current_date"), Entity("time", orig1, "start_time")],
              r=r, oracle=[{"name": "update_calendar_event", "arguments": {
                  "title": title, "current_date": d1.isoformat(), "start_time": orig1}}],
              results={"update_calendar_event": f"Moved {title} on {date_long(d1)} back to "
                       f"{time_12h(orig1)}."},
              state={"calendar": [world["calendar"][0], {**world["calendar"][1], "start_time": new}]},
              tags=["refers_back"]),
        _turn(f"Then text {person} that we're on at {time_12h(new)} on {WEEKDAYS[wd2]}",
              [{"tool": "send_message", "args": {"to": {"icontains": person}}}],
              [Entity("name", canon_text(person), "to"), Entity("time", new, "body")], r=r,
              oracle=[{"name": "send_message", "arguments": {
                  "to": person, "body": f"We're on at {time_12h(new)} on {WEEKDAYS[wd2]}."}}],
              results={"send_message": f"Sent to {person}."}),
    ]
    return turns, world


def _fu_reminder_chain(r, inv) -> tuple[list[SpokenTurn], dict]:
    what, key = r.choice(REMINDER_TASKS)
    wd1, wd2 = r.sample(range(7), 2)
    d1, d2 = _day(wd1), _day(wd2)
    t1 = t2 = inv.clock(8, 20, 30)
    while t2 == t1:
        t2 = inv.clock(8, 20, 30)
    rem = {"text": what.capitalize(), "date": d1.isoformat(), "time": t1}
    turns = [
        _turn(f"Remind me to {what} on {WEEKDAYS[wd1]} at {time_12h(t1)}",
              [{"tool": "add_reminder", "args": {"text": {"icontains": key}, "date": d1.isoformat()}}],
              [Entity("date", d1.isoformat(), "date"), Entity("time", t1, "time")], r=r,
              oracle=[{"name": "add_reminder", "arguments": dict(rem)}],
              results={"add_reminder": f"Reminder '{rem['text']}' set for {date_long(d1)} at "
                       f"{time_12h(t1)}."}, state={"reminders": [rem]}),
        _turn(r.choice(("Make that {t} instead", "Actually make it {t}", "Change it to {t}"))
              .format(t=time_12h(t2)),
              [{"tool": "update_reminder", "args": {"text": {"icontains": key}}}],
              [Entity("time", t2, "time")], r=r,
              oracle=[{"name": "update_reminder", "arguments": {"text": rem["text"], "time": t2}}],
              results={"update_reminder": f"Moved '{rem['text']}' to {time_12h(t2)}."},
              state={"reminders": [{**rem, "time": t2}]}, tags=["refers_back"]),
        _turn(f"Actually, move it to {WEEKDAYS[wd2]}",
              [{"tool": "update_reminder", "args": {"text": {"icontains": key},
                                                    "date": d2.isoformat()}}],
              [Entity("date", d2.isoformat(), "date")], r=r,
              oracle=[{"name": "update_reminder", "arguments": {"text": rem["text"],
                                                                "date": d2.isoformat()}}],
              results={"update_reminder": f"Moved '{rem['text']}' to {date_long(d2)}."},
              state={"reminders": [{**rem, "time": t2, "date": d2.isoformat()}]},
              tags=["refers_back"]),
        _turn(r.choice(("Never mind, delete it", "Forget it, just delete that reminder")),
              [{"tool": "delete_reminder", "args": {"text": {"icontains": key}}}], r=r,
              oracle=[{"name": "delete_reminder", "arguments": {"text": rem["text"]}}],
              results={"delete_reminder": f"Deleted '{rem['text']}'."}, state={"reminders": []},
              forbid=["delete_calendar_event"], tags=["refers_back"]),
    ]
    return turns, {}


def _fu_contact(r, inv) -> tuple[list[SpokenTurn], dict]:
    name = f"{inv.given()} {inv.surname()}"
    first = name.split()[0]
    phone = inv.phone()
    d = canon_phone(phone)
    email = inv.email(name)
    t = inv.clock(12, 19, 15)
    turns = [
        _turn(f"Call {name} at {phone}",
              [{"tool": "call_number", "args": {"phone": {"regex": d[-7:-4] + r"\D*" + d[-4:]}}}],
              [Entity("phone", d, "phone")], r=r,
              oracle=[{"name": "call_number", "arguments": {"phone": phone}}],
              results={"call_number": f"No answer from {phone}."},
              reply=f"{first} didn't pick up.", state={"last_contact": {"name": name, "phone": phone}}),
        _turn(f"Text them that I'll call back at {time_12h(t)}",
              [{"tool": "send_message", "args": {"to": {"regex": f"{first}|{d[-4:]}"}}}],
              [Entity("time", t, "body")], r=r,
              oracle=[{"name": "send_message", "arguments": {
                  "to": phone, "body": f"I'll call you back at {time_12h(t)}."}}],
              results={"send_message": f"Sent to {name}."}, tags=["refers_back", "pronoun"]),
        _turn(f"And email them the same at {email}",
              [{"tool": "send_email", "args": {"to": {"eq": email}}}],
              [Entity("email", email.lower(), "to"), Entity("time", t, "body")], r=r,
              oracle=[{"name": "send_email", "arguments": {
                  "to": email, "subject": "Call back", "body": f"I'll call you back at {time_12h(t)}."}}],
              results={"send_email": f"Email sent to {email}."}, tags=["refers_back", "pronoun"]),
        _turn(f"Remind me to call {first} back at {time_12h(t)} today",
              [{"tool": "add_reminder", "args": {"text": {"icontains": first},
                                                 "date": cmd.TODAY.isoformat()}}],
              [Entity("time", t, "time"), Entity("name", canon_text(first), "text")], r=r,
              oracle=[{"name": "add_reminder", "arguments": {
                  "text": f"Call {first} back", "date": cmd.TODAY.isoformat(), "time": t}}],
              results={"add_reminder": f"Reminder set for {time_12h(t)} today."}),
    ]
    return turns, {}


def _fu_timer(r, inv) -> tuple[list[SpokenTurn], dict]:
    n = r.choice((5, 8, 10, 12, 15, 20, 25))
    k = r.choice((2, 3, 5, 10))
    m = r.choice((4, 6, 7, 9, 11, 30))
    turns = [
        _turn(f"Set a timer for {n} minutes",
              [{"tool": "set_timer", "args": {"minutes": {"regex": rf"^{n}(\.0+)?$"}}}],
              [Entity("duration", str(n), "minutes")], r=r,
              oracle=[{"name": "set_timer", "arguments": {"minutes": n}}],
              results={"set_timer": f"Timer set for {n} minutes."}, state={"timer": n}),
        _turn(f"Add {k} more minutes",
              [{"tool": "set_timer", "args": {"minutes": {"regex": rf"^{n + k}(\.0+)?$"}}}],
              [Entity("duration", str(n + k), "minutes")], r=r,
              oracle=[{"name": "set_timer", "arguments": {"minutes": n + k}}],
              results={"set_timer": f"Timer set for {n + k} minutes."}, state={"timer": n + k},
              tags=["refers_back", "arithmetic"]),
        _turn(r.choice(("Actually, cancel it", "Cancel the timer")),
              [{"tool": "cancel_timer"}], r=r, oracle=[{"name": "cancel_timer", "arguments": {}}],
              results={"cancel_timer": "Timer cancelled."}, state={"timer": None},
              tags=["refers_back"]),
        _turn(f"Set a new one for {m} minutes",
              [{"tool": "set_timer", "args": {"minutes": {"regex": rf"^{m}(\.0+)?$"}}}],
              [Entity("duration", str(m), "minutes")], r=r,
              oracle=[{"name": "set_timer", "arguments": {"minutes": m}}],
              results={"set_timer": f"Timer set for {m} minutes."}, state={"timer": m}),
    ]
    return turns, {}


def _fu_directions(r, inv) -> tuple[list[SpokenTurn], dict]:
    street, town = inv.street_address(), inv.town()
    full = f"{street}, {town}"
    surname = street.split()[1]
    name = inv.given()
    amount = inv.money(8, 60)
    wd = r.randrange(7)
    d = _day(wd)
    t = inv.clock(9, 18, 30)
    title = r.choice(("Viewing", "Pickup", "Workshop", "Fitting"))
    turns = [
        _turn(f"Directions to {full}",
              [{"tool": "get_directions", "args": {"destination": {"icontains": surname}}}],
              [address_entity(street, town, "destination")], r=r,
              oracle=[{"name": "get_directions", "arguments": {"destination": full}}],
              results={"get_directions": f"{full} is 14 minutes away by car."},
              reply=f"It's about fourteen minutes by car.", state={"last_place": full}),
        _turn(f"Text {name} that address",
              [{"tool": "send_message", "args": {"to": {"icontains": name},
                                                 "body": {"icontains": surname}}}],
              [Entity("name", canon_text(name), "to"), address_entity(street, town, "body")], r=r,
              oracle=[{"name": "send_message", "arguments": {"to": name, "body": full}}],
              results={"send_message": f"Sent to {name}."}, tags=["refers_back"]),
        _turn(f"And log ${amount:.2f} for the taxi there",
              [{"tool": "log_expense", "args": {"amount": {"regex": cmd._num_regex(amount)}}}],
              [Entity("money", f"{amount:.2f} USD", "amount")], r=r,
              oracle=[{"name": "log_expense", "arguments": {"amount": amount, "currency": "USD",
                                                            "note": f"Taxi to {full}"}}],
              results={"log_expense": f"Logged ${amount:.2f}."}, tags=["refers_back"]),
        _turn(f"Put a {title.lower()} there on {WEEKDAYS[wd]} at {time_12h(t)}",
              [{"tool": "add_calendar_event", "args": {"date": d.isoformat(),
                                                       "location": {"icontains": surname}}}],
              [Entity("date", d.isoformat(), "date"), Entity("time", t, "start_time"),
               address_entity(street, town, "location")], r=r,
              oracle=[{"name": "add_calendar_event", "arguments": {
                  "title": title, "date": d.isoformat(), "start_time": t, "location": full}}],
              results={"add_calendar_event": f"Added {title} on {date_long(d)} at {time_12h(t)}."},
              tags=["refers_back"]),
    ]
    return turns, {}


def _fu_expense(r, inv) -> tuple[list[SpokenTurn], dict]:
    amount = inv.money(12, 300)
    note = r.choice(("the conference hotel", "client lunch", "train tickets", "printer ink"))
    wd = r.randrange(7)
    d = _day(wd)
    email = inv.email()
    turns = [
        _turn(f"Log ${amount:.2f} for {note}",
              [{"tool": "log_expense", "args": {"amount": {"regex": cmd._num_regex(amount)}}}],
              [Entity("money", f"{amount:.2f} USD", "amount")], r=r,
              oracle=[{"name": "log_expense", "arguments": {"amount": amount, "currency": "USD",
                                                            "note": note}}],
              results={"log_expense": f"Logged ${amount:.2f} for {note}."},
              state={"expenses": [{"amount": amount, "note": note}]}),
        _turn(f"And remind me on {WEEKDAYS[wd]} to get reimbursed for it",
              [{"tool": "add_reminder", "args": {"text": {"icontains": "reimburs"},
                                                 "date": d.isoformat()}}],
              [Entity("date", d.isoformat(), "date")], r=r,
              oracle=[{"name": "add_reminder", "arguments": {
                  "text": f"Get reimbursed for {note}", "date": d.isoformat()}}],
              results={"add_reminder": f"Reminder set for {date_long(d)}."}, tags=["refers_back"]),
        _turn(f"Email {email} how much it was",
              [{"tool": "send_email", "args": {"to": {"eq": email}}}],
              [Entity("email", email.lower(), "to"), Entity("money", f"{amount:.2f} USD", "body")],
              r=r, oracle=[{"name": "send_email", "arguments": {
                  "to": email, "subject": note.capitalize(), "body": f"It was ${amount:.2f}."}}],
              results={"send_email": f"Email sent to {email}."}, tags=["refers_back"]),
        _turn("Actually, delete that reminder",
              [{"tool": "delete_reminder", "args": {"text": {"icontains": "reimburs"}}}], r=r,
              oracle=[{"name": "delete_reminder", "arguments": {"text": f"Get reimbursed for {note}"}}],
              results={"delete_reminder": "Deleted the reminder."}, tags=["refers_back"]),
    ]
    return turns, {}


FOLLOW_UP_TEMPLATES = (_fu_event_chain, _fu_other_one, _fu_reminder_chain, _fu_contact,
                       _fu_timer, _fu_directions, _fu_expense)


def _agentic_dialogues(case_dicts: Iterable[dict], *, world_seed: int = 1) -> tuple[list[SpokenDialogue], dict]:
    """Agentic multi-turn cases (``followup_turn`` or ``turns``) that are speakable."""
    out, dropped = [], {}
    for raw in case_dicts:
        follow = raw.get("followup_turn")
        turns_raw = raw.get("turns")
        if not follow and not turns_raw:
            continue
        if raw.get("sessions"):
            dropped.setdefault("sessions", []).append(raw["id"])
            continue
        try:
            facts = agentic_facts(raw, world_seed)
            now = agentic_clock(raw)
            users = [raw["prompt"]] + ([follow["user"]] if follow else [t["user"] for t in turns_raw])
            users = [_resolve(fill(u, facts), now) for u in users]
        except (Unbound, KeyError, ValueError):
            dropped.setdefault("unbound_placeholder", []).append(raw["id"])
            continue
        why = next((w for w in (unspeakable_reason(u) for u in users) if w), None)
        if why:
            dropped.setdefault(why, []).append(raw["id"])
            continue
        later = [follow] if follow else list(turns_raw)
        confirm = list(raw.get("confirm") or [])
        turns = [SpokenTurn(users[0], speak(users[0], rng=_rng("agentic-dlg", raw["id"], 0)),
                            fill(copy.deepcopy(raw.get("expect") or []), facts, strict=False),
                            forbid=confirm + list(raw.get("forbid") or []),
                            oracle=_oracle_calls(raw.get("oracle"), facts),
                            reply=_oracle_text(raw.get("oracle"), facts) or "Done.")]
        for k, t in enumerate(later, start=1):
            calls = _oracle_calls(t.get("oracle"), facts)
            turns.append(SpokenTurn(users[k], speak(users[k], rng=_rng("agentic-dlg", raw["id"], k)),
                                    [{"tool": c["name"]} for c in calls],
                                    oracle=calls, reply=_oracle_text(t.get("oracle"), facts) or "Done."))
        lane = raw.get("lane", "assistant")
        out.append(SpokenDialogue(f"a3-fu-ag-{raw['id']}", turns, today=now.date().isoformat(),
                                  world=fill(copy.deepcopy(raw.get("world") or {}), facts, strict=False),
                                  source="agentic", tags=list(raw.get("tags") or []),
                                  tool_menu=f"agentic:{lane}",
                                  system_policy=str(fill(raw.get("system_policy") or "", facts,
                                                         strict=False)),
                                  origin={"source": "agentic", "case_id": raw["id"],
                                          "world_seed": world_seed, "case": raw}))
    return out, dropped


def _oracle_calls(steps, facts) -> list[dict]:
    calls = []
    for st in steps or []:
        for c in st.get("calls") or []:
            calls.append({"name": c["name"], "arguments": fill(copy.deepcopy(c.get("arguments") or {}),
                                                               facts, strict=False)})
    return calls


def _oracle_text(steps, facts) -> str:
    texts = [st["text"] for st in steps or [] if isinstance(st.get("text"), str)]
    return str(fill(texts[-1], facts, strict=False)) if texts else ""


def _toolbench_dialogues(directory=None) -> tuple[list[SpokenDialogue], dict]:
    """Toolbench cases with ``history``: earlier turns replayed as spoken turns."""
    facts = toolbench_facts()
    out, dropped = [], {}
    for raw in _toolbench_raw(directory):
        hist = raw.get("history") or []
        if not hist:
            continue
        users = [fill(h.get("user", ""), facts, strict=False) for h in hist] + \
            [fill(raw["prompt"], facts, strict=False)]
        why = next((w for w in (unspeakable_reason(u) for u in users) if w), None)
        if why or "{city_typo}" in raw["prompt"]:
            dropped.setdefault(why or "typed_typo", []).append(raw["id"])
            continue
        turns = []
        for k, h in enumerate(hist):
            tools = [t.strip() for t in str(h.get("tools") or "").split(",") if t.strip()]
            calls = [{"name": c["name"], "arguments": fill(c.get("args") or {}, facts, strict=False)}
                     for c in h.get("calls") or []]
            results = {c["name"]: fill(str(c.get("result", "Done.")), facts, strict=False)
                       for c in h.get("calls") or []}
            turns.append(SpokenTurn(users[k], speak(users[k], rng=_rng("tb-dlg", raw["id"], k)),
                                    [{"any_of": tools}] if tools else None,
                                    oracle=calls or [{"name": t, "arguments": {}} for t in tools[:1]],
                                    results=results,
                                    reply=fill(str(h.get("assistant") or "Done."), facts, strict=False)))
        turns.append(SpokenTurn(users[-1], speak(users[-1], rng=_rng("tb-dlg", raw["id"], len(hist))),
                                fill(copy.deepcopy(raw.get("expect") or []), facts, strict=False),
                                no_tool=bool(raw.get("no_tool")), forbid=list(raw.get("forbid") or [])))
        out.append(SpokenDialogue(f"a3-fu-tb-{raw['id']}", turns, today=TOOLBENCH_TODAY.isoformat(),
                                  source="toolbench", tags=[f"category:{raw['_category']}"],
                                  tool_menu="toolbench",
                                  origin={"source": "toolbench", "case_id": raw["id"],
                                          "answer": raw.get("answer")}))
    return out, dropped


def follow_ups(n: int = FOLLOW_UP_N, *, seed: str = A3_SEED, agentic_cases=None,
               toolbench_dir=None, world_seed: int = 1, report: dict | None = None
               ) -> list[SpokenDialogue]:
    """``n`` spoken dialogues of 2-4 turns: agentic and Toolbench multi-turn cases first,
    the rest from the templates over :data:`~edat.media.audio.commands.TOOLS`."""
    imported: list[SpokenDialogue] = []
    if agentic_cases:
        ag, ag_drop = _agentic_dialogues(agentic_cases, world_seed=world_seed)
        imported += ag
        if report is not None:
            report["agentic_multi_turn"] = {"speakable": len(ag),
                                            "dropped": {k: len(v) for k, v in ag_drop.items()}}
    tb, tb_drop = _toolbench_dialogues(toolbench_dir)
    imported += tb
    if report is not None:
        report["toolbench_history"] = {"speakable": len(tb),
                                       "dropped": {k: len(v) for k, v in tb_drop.items()}}
    imported = [d for d in imported if 2 <= len(d.turns) <= 4][:n]
    out = list(imported)
    for i in range(n - len(out)):
        r = rng_for(seed, "a3-followup", i)
        inv = Inventor(r)
        tpl = FOLLOW_UP_TEMPLATES[i % len(FOLLOW_UP_TEMPLATES)]
        turns, world = tpl(r, inv)
        k = r.choice((2, 3, 3, 4, 4))
        out.append(SpokenDialogue(f"a3-fu-{i:04d}", turns[:k], world=world, source="template",
                                  tags=[f"template:{tpl.__name__[4:]}", f"turns:{k}"]))
    return out


# ------------------------------------------------------------------ corrections

CORRECTION_CUES = ("no", "sorry", "I mean", "actually", "wait, make that", "no wait")
LATE = ("running late", "on my way", "outside", "five minutes away", "stuck in traffic")


def _distinct_names(r, k=2) -> list[str]:
    while True:
        names = r.sample(GIVEN_NAMES, k)
        if not any(a.lower() in b.lower() for a in names for b in names if a != b):
            return names


def correction_case(i: int, *, seed: str = A3_SEED) -> CommandCase:
    r = rng_for(seed, "a3-correction", i)
    inv = Inventor(r)
    kind = i % 11
    cue = r.choice(CORRECTION_CUES)
    cid = f"a3-cor-{i:04d}"

    def mk(text, expect, entities, abandoned, tags, abandoned_tools=(), forbid=()):
        return CommandCase(cid, text, speak(text, rng=r), expect, entities, family="corrections",
                           source="generated", tags=["correction"] + tags, abandoned=abandoned,
                           abandoned_tools=list(abandoned_tools), forbid=list(forbid))
    if kind == 0:
        a, b = _distinct_names(r)
        late = r.choice(LATE)
        return mk(f"Text {a}, {cue}, {b}, that I'm {late}",
                  [{"tool": "send_message", "args": {"to": {"icontains": b}}}],
                  [Entity("name", canon_text(b), "to")], [Entity("name", canon_text(a), "to")],
                  ["correct_name"])
    if kind == 1:
        h1, h2 = r.sample(range(1, 9), 2)
        what, key = r.choice(REMINDER_TASKS)
        day = cmd.TODAY + timedelta(days=1)
        return mk(f"Remind me to {what} tomorrow at {h1}, {cue}, {h2} pm",
                  [{"tool": "add_reminder", "args": {"text": {"icontains": key},
                                                     "date": day.isoformat()}}],
                  [Entity("time", f"{h2 + 12:02d}:00", "time"), Entity("date", day.isoformat(), "date")],
                  [Entity("time", f"{h1 + 12:02d}:00", "time")], ["correct_time"])
    if kind == 2:
        w1, w2 = r.sample(range(7), 2)
        title = r.choice(EVENT_TITLES)
        hhmm = inv.clock(9, 18, 30)
        return mk(f"Add {title} on {WEEKDAYS[w1]}, {cue}, {WEEKDAYS[w2]}, at {time_12h(hhmm)}",
                  [{"tool": "add_calendar_event", "args": {"title": {"icontains": title.split()[0]},
                                                           "date": _day(w2).isoformat()}}],
                  [Entity("date", _day(w2).isoformat(), "date"), Entity("time", hhmm, "start_time")],
                  [Entity("date", _day(w1).isoformat(), "date")], ["correct_date"])
    if kind == 3:
        n1, n2 = r.sample((5, 8, 10, 12, 15, 20, 25, 30, 40, 45), 2)
        return mk(f"Set a timer for {n1}, {cue}, {n2} minutes",
                  [{"tool": "set_timer", "args": {"minutes": {"regex": rf"^{n2}(\.0+)?$"}}}],
                  [Entity("duration", str(n2), "minutes")], [Entity("duration", str(n1), "minutes")],
                  ["correct_number"])
    if kind == 4:
        a1, a2 = r.sample(range(4, 90), 2)
        note = r.choice(("lunch", "taxi", "groceries", "parking", "books", "the plumber"))
        return mk(f"Log ${a1}, {cue}, ${a2} for {note}",
                  [{"tool": "log_expense", "args": {"amount": {"regex": rf"^{a2}(\.0+)?$"}}}],
                  [Entity("money", f"{a2:.2f} USD", "amount")],
                  [Entity("money", f"{a1:.2f} USD", "amount")], ["correct_money"])
    if kind == 5:
        person = inv.person()
        local = person.lower().replace(" ", ".")
        d1, d2 = r.sample(("example.com", "example.org", "mail.test"), 2)
        subject = r.choice(("the rota", "Saturday", "the invoice", "our trip", "the draft"))
        good = f"{local}@{d2}"
        return mk(f"Email {local}@{d1}, {cue}, {good}, about {subject}",
                  [{"tool": "send_email", "args": {"to": {"eq": good}}}],
                  [Entity("email", good, "to")], [Entity("email", f"{local}@{d1}", "to")],
                  ["correct_email"])
    if kind == 6:
        name = inv.given()
        p1 = inv.phone()
        p2 = p1
        while canon_phone(p2) == canon_phone(p1):
            p2 = p1[:-2] + f"{r.randint(0, 99):02d}"
        d2 = canon_phone(p2)
        return mk(f"Call {name} on {p1}, {cue}, {p2}",
                  [{"tool": "call_number", "args": {"phone": {"regex": d2[-7:-4] + r"\D*" + d2[-4:]}}}],
                  [Entity("phone", d2, "phone")], [Entity("phone", canon_phone(p1), "phone")],
                  ["correct_phone"])
    if kind == 7:
        sender = inv.merchant() if r.random() < 0.5 else inv.person()
        lead = r.choice(("Delete the email from {s}... actually just archive it",
                         "Delete the {s} email, no, archive it instead",
                         "Bin the email from {s}, actually, just archive it"))
        return mk(lead.format(s=sender),
                  [{"tool": "archive_email", "args": {"query": {"icontains": sender.split()[0]}}}],
                  [Entity("name", canon_text(sender.split()[0]), "query")], [], ["correct_action"],
                  abandoned_tools=["delete_email"], forbid=["delete_email"])
    if kind == 8:
        street, town = inv.street_address(), inv.town()
        num, rest = street.split(" ", 1)
        wrong = num
        while wrong == num:
            wrong = str(r.randint(2, 980))
        return mk(f"Directions to {wrong} {rest}, {cue}, {num} {rest}, {town}",
                  [{"tool": "get_directions", "args": {"destination": {"icontains": rest.split()[0]}}}],
                  [address_entity(street, town, "destination")],
                  [Entity("number", wrong, "destination")], ["correct_address"])
    if kind == 9:
        a = inv.brand()
        b = a
        while b == a or a.lower() in b.lower() or b.lower() in a.lower():
            b = inv.brand()
        return mk(f"Open {a}, {cue}, {b}",
                  [{"tool": "open_app", "args": {"name": {"icontains": b}}}],
                  [Entity("term", canon_text(b), "name", sub="app")],
                  [Entity("term", canon_text(a), "name", sub="app")], ["correct_term"])
    q1, q2 = r.sample(range(2, 13), 2)
    product = f"{inv.brand()} {r.choice(cmd.PRODUCT_KINDS)}"
    return mk(f"Add {q1}, {cue}, {q2} {product} to my shopping list",
              [{"tool": "add_to_list", "args": {"item": {"icontains": product.split()[0]}}}],
              [Entity("number", str(q2), "quantity"),
               Entity("term", canon_text(product), "item", sub="product")],
              [Entity("number", str(q1), "quantity")], ["correct_quantity"])


def corrections(n: int = CORRECTION_N, *, seed: str = A3_SEED) -> list[CommandCase]:
    """``n`` mid-utterance corrections; each records the ``abandoned`` values."""
    return [correction_case(i, seed=seed) for i in range(n)]


# ------------------------------------------------------------------ disfluency

FILLERS = ("um", "uh", "er", "erm", "like", "you know", "so")
TRAIL_OFF = ("and um", "and uh, yeah", "if you can, um", "or something", "so yeah")
RUN_ON_LEADS = ("okay so", "right so", "hey so", "yeah so")
PAUSES_S = (1.0, 1.5, 2.0, 2.5)
DISFLUENCY_KINDS = ("filler", "restart", "repetition", "long_pause", "trailing_off", "run_on")
_BRAND_SEEDS = {"voice_web_stock"}     # names a real company; kept as is, not expanded


def disfluent(spoken: str, r: random.Random, kinds: list[str]) -> str:
    """Apply disfluency ``kinds`` to a spoken command; returns a script (may hold pauses)."""
    words = spoken.split()
    for kind in kinds:
        if kind == "filler":
            for _ in range(r.randint(1, 2)):
                words.insert(r.randint(0, max(0, len(words) - 1)), r.choice(FILLERS) + ",")
        elif kind == "restart":
            k = min(len(words), r.randint(1, 3))
            head = " ".join(words[:k]).rstrip(",")
            lead = r.choice(("{h}, {h}", "{h}, um, {h}", "can you, {h}", "I need to, {h}"))
            words = lead.format(h=head).split() + words[k:]
        elif kind == "repetition":
            j = r.randint(1, max(1, len(words) - 1)) if len(words) > 1 else 0
            words.insert(j, words[j - 1] if j else words[0])
        elif kind == "long_pause":
            if len(words) > 1:
                words.insert(r.randint(1, len(words) - 1), f"<pause {r.choice(PAUSES_S)}>")
        elif kind == "trailing_off":
            words += r.choice(TRAIL_OFF).split()
        elif kind == "run_on":
            words = r.choice(RUN_ON_LEADS).split() + [w.strip(",.;:!?").lower() if not
                                                       w.startswith("<") else w for w in words]
            words += r.choice(("and thanks", "and that's it", "thanks")).split()
    return " ".join(words)


def _voice_seeds(directory=None) -> list[tuple[dict, str, str]]:
    """Toolbench voice.yaml cases, bound: (raw, spoken prompt, typed twin prompt)."""
    facts = toolbench_facts()
    raws = _toolbench_raw(directory)
    by_id = {r["id"]: r for r in raws}
    out = []
    for raw in raws:
        if raw["_category"] != "voice":
            continue
        m = re.search(r"voice twin of (\w+)", raw.get("trap", ""))
        twin = by_id.get(m.group(1)) if m else None
        typed = fill((twin or raw)["prompt"], facts, strict=False)
        out.append((raw, fill(raw["prompt"], facts, strict=False), typed))
    return out


def disfluency(n: int = DISFLUENCY_N, *, seed: str = A3_SEED, toolbench_dir=None,
               variants_per_seed: int = 4) -> list[CommandCase]:
    """``n`` disfluent commands: the 16 voice.yaml cases, disfluent variants of them, and
    entity-dense commands made disfluent. The typed twin is the fluent command."""
    facts = toolbench_facts()
    out: list[CommandCase] = []
    for raw, prompt, typed in _voice_seeds(toolbench_dir):
        expect = fill(copy.deepcopy(raw.get("expect") or []), facts, strict=False)
        common = dict(today=TOOLBENCH_TODAY.isoformat(), source="toolbench", family="disfluency",
                      forbid=list(raw.get("forbid") or []), no_tool=bool(raw.get("no_tool")),
                      tool_menu="toolbench", origin={"source": "toolbench", "case_id": raw["id"],
                                                     "answer": raw.get("answer")})
        spoken = speak(prompt, rng=_rng("voice", raw["id"]))
        out.append(CommandCase(f"a3-dis-{raw['id']}", typed, spoken, expect, [],
                               tags=["disfluency", "voice_seed"], **copy.deepcopy(common)))
        if raw["id"] in _BRAND_SEEDS:
            continue
        for v in range(variants_per_seed):
            r = rng_for(seed, "a3-disfluency-seed", raw["id"], v)
            kinds = [DISFLUENCY_KINDS[v % len(DISFLUENCY_KINDS)]]
            kinds += [k for k in r.sample(DISFLUENCY_KINDS, 1) if k not in kinds]
            script = disfluent(spoken, r, kinds)
            out.append(_dis_case(f"a3-dis-{raw['id']}-v{v}", typed, script, expect, [], kinds,
                                 **copy.deepcopy(common)))
    i = 0
    while len(out) < n:
        r = rng_for(seed, "a3-disfluency", i)
        base = entity_case(i, seed=f"{seed}/disfluency", prefix="a3-dis-ent")
        kinds = [DISFLUENCY_KINDS[i % len(DISFLUENCY_KINDS)]]
        kinds += [k for k in r.sample(DISFLUENCY_KINDS, r.randint(0, 2)) if k not in kinds]
        script = disfluent(base.spoken, r, kinds)
        out.append(_dis_case(base.case_id, base.text, script, base.expect, base.entities, kinds,
                             source="generated", family="disfluency"))
        i += 1
    return out[:n]


def _dis_case(cid, typed, script, expect, entities, kinds, **kw) -> CommandCase:
    spoken = strip_markers(script)
    tags = ["disfluency"] + [f"dis_{k}" for k in kinds]
    pauses = [float(p) for p in re.findall(r"<pause\s+([\d.]+)>", script)]
    if any(p >= 1.3 for p in pauses):
        tags.append("pause_over_endpoint")      # Harbor's endpointer ends the turn at 1.3 s
    return CommandCase(cid, typed, spoken, copy.deepcopy(expect), list(entities), tags=tags,
                       script=script if script != spoken else None, **kw)


# ------------------------------------------------------------------ noisy plan

def noisy_plan(singles: list[CommandCase]) -> list[dict]:
    """Each single case at SNR 20, 10 and 5 dB; the six §3A noise types rotated evenly."""
    out = []
    for i, c in enumerate(sorted(singles, key=lambda c: c.case_id)):
        for k, snr in enumerate(NOISY_SNR_DB):
            spec = SPEC_NOISES[(i + k) % len(SPEC_NOISES)]
            recorded = spec in RECORDED_NOISES
            if HAS_A4_NOISE:
                gen, generator = spec, "mix.generate_a4_noise"
            else:
                gen, generator = NOISE_STAND_INS[spec], "mix.generate_noise"
            source = (f"generated stand-in ({generator}('{gen}')) until a recorded {spec} set "
                      "is approved") if recorded else f"generated ({generator}('{gen}'))"
            out.append({"case_id": c.case_id, "condition": {
                "type": "noise", "noise": gen, "snr_db": snr, "spec_noise": spec,
                "stand_in": recorded, "generator": generator, "noise_source": source}})
    return out


# ------------------------------------------------------------------ the suite and its plan

@dataclass(frozen=True)
class ClipSpec:
    clip_id: str
    case_id: str                 # the case, or the dialogue for a follow-up turn
    family: str
    voice: str
    condition: dict
    rate: float = 1.0
    turn_index: int | None = None
    pitch_semitones: float = 0.0
    seed: int = 0                # noise / room seed

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class A3Suite:
    seed: str
    singles: list[CommandCase]
    voice_variance: list[str]
    dialogues: list[SpokenDialogue]
    corrections: list[CommandCase]
    disfluency: list[CommandCase]
    noisy: list[dict]
    report: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._index = {c.case_id: c for c in self.singles + self.corrections + self.disfluency}
        self._dialogues = {d.dialogue_id: d for d in self.dialogues}

    def case(self, case_id: str) -> CommandCase:
        return self._index[case_id]

    def dialogue(self, dialogue_id: str) -> SpokenDialogue:
        return self._dialogues[dialogue_id]

    def render_text(self, spec: ClipSpec) -> str:
        if spec.turn_index is not None:
            return self._dialogues[spec.case_id].turns[spec.turn_index].render_text
        return self._index[spec.case_id].render_text


def build_suite(seed: str = A3_SEED, *, agentic_cases: list[dict] | None = None,
                agentic_dir=None, toolbench_dir=None, world_seed: int = 1) -> A3Suite:
    report: dict = {}
    if agentic_cases is None:
        agentic_cases = load_agentic(agentic_dir)
    singles: list[CommandCase] = []
    if agentic_cases:
        conv = convert_agentic(agentic_cases, world_seed=world_seed)
        singles += conv.cases
        report["agentic"] = conv.report()
    else:
        report["agentic"] = {"source": "agentic", "total": 0, "speakable": 0,
                             "note": "agentic fixtures not found (pass --agentic-dir)"}
    tb = toolbench_cases(toolbench_dir)
    singles += tb.cases
    report["toolbench"] = tb.report()
    ent = entity_dense(seed=seed)
    singles += ent
    report["entity_dense"] = {"cases": len(ent)}
    dialogues = follow_ups(seed=seed, agentic_cases=agentic_cases, toolbench_dir=toolbench_dir,
                           world_seed=world_seed, report=report)
    return A3Suite(seed, singles, voice_variance_ids(singles, seed=seed), dialogues,
                   corrections(seed=seed), disfluency(seed=seed, toolbench_dir=toolbench_dir),
                   noisy_plan(singles), report)


def _rate(seed: str, case_id: str) -> float:
    return rng_for(seed, "a3-rate", case_id).choice(RATES)


def _noise_seed(seed: str, clip_id: str) -> int:
    return rng_for(seed, "a3-noise", clip_id).randrange(2 ** 31)


def plan_for(suite: A3Suite, *, voices: list[str] | None = None) -> list[ClipSpec]:
    """The deterministic render plan for a built suite."""
    voices = list(voices or STUB_VOICES)
    nv = len(voices)
    seed = suite.seed
    off = rng_for(seed, "a3-voice-offset").randrange(nv)
    plan: list[ClipSpec] = []
    clean = {"type": "clean"}
    single_voice = {}
    for j, c in enumerate(sorted(suite.singles, key=lambda c: c.case_id)):
        v = voices[(off + j) % nv]
        single_voice[c.case_id] = v
        plan.append(ClipSpec(f"single/{c.case_id}", c.case_id, "single", v, clean,
                             _rate(seed, c.case_id)))
    for cid in suite.voice_variance:
        for v in voices:
            plan.append(ClipSpec(f"voice_variance/{cid}/{v}", cid, "voice_variance", v, clean, 1.0))
    load = Counter({v: 0 for v in voices})
    for d in suite.dialogues:       # one speaker per dialogue; the least-used voice next
        v = min(voices, key=lambda x: (load[x], (voices.index(x) - off) % nv))
        load[v] += len(d.turns)
        rate = _rate(seed, d.dialogue_id)
        for t in range(len(d.turns)):
            plan.append(ClipSpec(f"follow_ups/{d.dialogue_id}/t{t}", d.dialogue_id, "follow_ups",
                                 v, clean, rate, turn_index=t))
    for fam, cases in (("corrections", suite.corrections), ("disfluency", suite.disfluency)):
        for j, c in enumerate(cases):
            plan.append(ClipSpec(f"{fam}/{c.case_id}", c.case_id, fam, voices[(off + j) % nv],
                                 clean, _rate(seed, c.case_id)))
    for item in suite.noisy:
        cid = item["case_id"]
        clip_id = f"noisy/{cid}/{item['condition']['spec_noise']}-{item['condition']['snr_db']}dB"
        plan.append(ClipSpec(clip_id, cid, "noisy", single_voice[cid], dict(item["condition"]),
                             _rate(seed, cid), seed=_noise_seed(seed, clip_id)))
    return plan


def a3_plan(seed: str = A3_SEED, *, voices: list[str] | None = None, **kw) -> list[ClipSpec]:
    """Every A3 clip as a :class:`ClipSpec` (case, family, voice, condition, rate, turn)."""
    return plan_for(build_suite(seed, **kw), voices=voices)


def _babble_source(engine, voice: str):
    others = [v.id for v in engine.voices() if v.id != voice] or [voice]
    line = "the quarterly figures are in the blue folder on the second shelf by the window"

    def source(seconds, s):
        clip = engine.synthesize(line, others[s % len(others)])
        x = clip.samples
        reps = int(seconds * clip.sample_rate // max(1, len(x))) + 1
        return np.tile(x, reps)
    return source


def materialise(spec: ClipSpec, engine, suite: A3Suite):
    """Render one planned clip with ``engine`` (``<pause s>`` markers become silence)."""
    text = suite.render_text(spec)
    clip = cmd.synthesize_script(engine, text, spec.voice, rate=spec.rate,
                                 pitch_semitones=spec.pitch_semitones)
    cond = {k: v for k, v in spec.condition.items()}
    babble = _babble_source(engine, spec.voice) if cond.get("noise") == "babble" else None
    clip = cmd.finish_clip(clip, spec.case_id, condition=cond, seed=spec.seed, babble_source=babble)
    if cond.get("type") == "noise":
        clip.meta["condition"] = {**clip.meta["condition"], "spec_noise": cond["spec_noise"],
                                  "stand_in": cond["stand_in"], "generator": cond["generator"],
                                  "noise_source": cond["noise_source"]}
    clip.meta.update({"clip_id": spec.clip_id, "family": spec.family, "voice": spec.voice,
                      "turn_index": spec.turn_index, "rate": spec.rate})
    return clip


# ------------------------------------------------------------------ counts

def counts(suite: A3Suite, plan: list[ClipSpec]) -> dict:
    fam_clips = Counter(s.family for s in plan)
    turns = sum(len(d.turns) for d in suite.dialogues)
    voices = {f: dict(sorted(Counter(s.voice for s in plan if s.family == f).items()))
              for f in FAMILIES}
    noise = Counter(s.condition.get("spec_noise") for s in plan if s.family == "noisy")
    singles_by_source = Counter(c.source for c in suite.singles)
    return {
        "seed": suite.seed,
        "cases": {"single": len(suite.singles), "voice_variance": len(suite.voice_variance),
                  "follow_ups": len(suite.dialogues), "follow_up_turns": turns,
                  "corrections": len(suite.corrections), "disfluency": len(suite.disfluency),
                  "noisy": len(suite.noisy)},
        "single_by_source": dict(singles_by_source),
        "clips": {f: fam_clips.get(f, 0) for f in FAMILIES} | {"total": len(plan)},
        "conversion": suite.report,
        "follow_ups_by_source": dict(Counter(d.source for d in suite.dialogues)),
        "turns_per_dialogue": dict(sorted(Counter(len(d.turns) for d in suite.dialogues).items())),
        "noisy_by_spec_noise": dict(noise),
        "noise_generator": "mix.generate_a4_noise" if HAS_A4_NOISE else
        {"mix.generate_noise": NOISE_STAND_INS},
        "voice_share_range": {f: (min(v.values()), max(v.values())) if v else None
                              for f, v in voices.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.media.audio.spoken_cases")
    ap.add_argument("command", choices=["counts", "plan"])
    ap.add_argument("--seed", default=A3_SEED)
    ap.add_argument("--agentic-dir", default=None)
    ap.add_argument("--toolbench-dir", default=None)
    ap.add_argument("--world-seed", type=int, default=1)
    a = ap.parse_args(argv)
    suite = build_suite(a.seed, agentic_dir=a.agentic_dir, toolbench_dir=a.toolbench_dir,
                        world_seed=a.world_seed)
    plan = plan_for(suite)
    if a.command == "counts":
        print(json.dumps(counts(suite, plan), indent=2, default=str))
    else:
        for s in plan:
            print(json.dumps(s.to_json()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
