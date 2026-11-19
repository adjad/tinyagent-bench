"""Bind a case to one trial: seeded facts, placeholders and relative times.

Each trial uses a different world seed (spec 02 §2.3). A case's `facts:` block
declares how each `{fact}` value is drawn, so a value a model needs from a
tool result changes with the seed and cannot be guessed or memorised:

    facts:
      dentist_hour: {int: [9, 15]}
      dentist_hour_plus_1: {add: [dentist_hour, 1]}
      dentist_event_id: {id: ev}                 # "ev-4821"
      flight_no: {code: "####"}                  # '#' digit, '@' capital letter
      city: {choice: [Porto, Madrid, Lyon]}
      other_city: {choice: [Porto, Madrid, Lyon], exclude: [city]}
      landing_time: {time: [13, 22], step_min: 5}  # "17:35"
      label: {template: "{city} at {landing_time}"}
      warmer_city: {argmax: {Porto: temp_porto, Madrid: temp_madrid}}
      greeting: {same_as: label}

Facts are drawn in the order written, from a random generator seeded by
(case id, world seed). Built-in date facts come from the case's clock
(`today_iso`, `today_weekday`, `tomorrow_weekday`, `tomorrow_iso`, ...).

A string that is exactly "{fact}" takes the fact's value and type; a fact
inside a longer string is formatted in. An unknown `{name}` is an error.
Relative times ("+1d 15:00") are resolved in the structured parts of a case
(records, predicates, argument specs); "{rel:+1d 15:00}" works inside text.
"""
from __future__ import annotations

import copy
import hashlib
import random
import re
from datetime import timedelta

from edat.cases.schema import CaseError, normalise
from edat.state.clock import Clock

PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# Keys whose subtrees are structured (relative times resolved anywhere in them).
STRUCTURED = ("world", "final_state", "claims", "alt_pass", "expect", "inject", "faults",
              "stored_preferences", "grounded_args", "verify", "clarify")
NOT_BOUND = ("trap",)  # skipped at any depth
TOP_NOT_BOUND = ("notes", "id")  # the case's own id and notes; nested `id`/`notes` are bound


def rng_for(case_id: str, world_seed: int) -> random.Random:
    digest = hashlib.sha256(f"{case_id}|{world_seed}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def date_facts(clock: Clock) -> dict:
    t = clock.today
    out = {"today_iso": t.isoformat(), "today_weekday": t.strftime("%A"),
           "now_hhmm": clock.now.strftime("%H:%M")}
    for n, name in ((1, "tomorrow"), (2, "in_2_days"), (3, "in_3_days"), (7, "in_7_days"),
                    (-1, "yesterday")):
        d = t + timedelta(days=n)
        out[f"{name}_iso"] = d.isoformat()
        out[f"{name}_weekday"] = d.strftime("%A")
    return out


def _gen(spec, facts: dict, rng: random.Random, name: str):
    if not isinstance(spec, dict) or len([k for k in spec if k not in ("fmt", "exclude", "step_min")]) != 1:
        raise CaseError(f"fact {name}: generator must be a mapping with one kind, got {spec!r}")
    kind = next(k for k in spec if k not in ("fmt", "exclude", "step_min"))
    arg = spec[kind]
    if kind == "choice":
        excluded = {facts[e] for e in spec.get("exclude", []) if e in facts}
        options = [o for o in arg if o not in excluded]
        if not options:
            raise CaseError(f"fact {name}: no choice left after exclusions")
        value = rng.choice(options)
    elif kind == "int":
        lo, hi = arg
        excluded = {facts[e] for e in spec.get("exclude", []) if e in facts}
        options = [v for v in range(int(lo), int(hi) + 1) if v not in excluded]
        value = rng.choice(options)
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
        excluded = {facts[e] for e in spec.get("exclude", []) if e in facts}
        slots = [s for s in slots if f"{s // 60:02d}:{s % 60:02d}" not in excluded]
        s = rng.choice(slots)
        value = f"{s // 60:02d}:{s % 60:02d}"
    else:
        raise CaseError(f"fact {name}: unknown generator {kind!r}")
    if "fmt" in spec:
        value = format(value, spec["fmt"])
    return value


def draw_facts(case: dict, world_seed: int, clock: Clock) -> dict:
    facts = date_facts(clock)
    rng = rng_for(case.get("variant_of") or case["id"], world_seed)  # a variant draws its seed's facts
    for name, spec in (case.get("facts") or {}).items():
        try:
            facts[name] = _gen(spec, facts, rng, name)
        except KeyError as exc:
            raise CaseError(f"{case['id']}: fact {name} refers to unknown fact {exc}") from None
    return facts


def substitute(obj, facts: dict, *, where: str = ""):
    if isinstance(obj, dict):
        return {k: (v if k in NOT_BOUND or (not where and k in TOP_NOT_BOUND)
                    else substitute(v, facts, where=f"{where}.{k}")) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, facts, where=where) for v in obj]
    if isinstance(obj, str):
        whole = PLACEHOLDER.fullmatch(obj)
        if whole:
            name = whole.group(1)
            if name not in facts:
                raise CaseError(f"unknown placeholder {{{name}}} at {where or 'case'}")
            return facts[name]

        def rep(m):
            if m.group(1) not in facts:
                raise CaseError(f"unknown placeholder {{{m.group(1)}}} at {where or 'case'}")
            return str(facts[m.group(1)])
        return PLACEHOLDER.sub(rep, obj)
    return obj


def resolve_times(case: dict, clock: Clock) -> dict:
    out = dict(case)
    for key in STRUCTURED:
        if key in out:
            out[key] = clock.resolve_tree(out[key])
    sessions = []
    for s in out.get("sessions", []):
        turns = []
        for t in s.get("turns", []):
            t = dict(t)
            for key in STRUCTURED:
                if key in t:
                    t[key] = clock.resolve_tree(t[key])
            if isinstance(t.get("user"), str):
                t["user"] = clock.resolve_text(t["user"])
            if "oracle" in t:
                t["oracle"] = _resolve_oracle(t["oracle"], clock)
            turns.append(t)
        sessions.append({**s, "turns": turns})
    out["sessions"] = sessions
    for key in ("system_policy",):
        if isinstance(out.get(key), str):
            out[key] = clock.resolve_text(out[key])
    return out


def _resolve_oracle(steps, clock: Clock):
    out = []
    for st in steps or []:
        st = dict(st)
        if isinstance(st.get("text"), str):
            st["text"] = clock.resolve_text(st["text"])
        if st.get("calls"):
            st["calls"] = [{**c, "arguments": _resolve_args(c.get("arguments"), clock)}
                           for c in st["calls"]]
        out.append(st)
    return out


def _resolve_args(args, clock: Clock):
    if isinstance(args, dict):
        return {k: _resolve_args(v, clock) for k, v in args.items()}
    if isinstance(args, list):
        return [_resolve_args(v, clock) for v in args]
    return clock.resolve_value(args)


def case_clock(case: dict, default: Clock | None = None) -> Clock:
    world = case.get("world") or {}
    if world.get("clock") or world.get("tz"):
        base = default or Clock.parse()
        return Clock.parse(world.get("clock") or base.iso(), world.get("tz") or base.tz)
    return default or Clock.parse()


def bind(case: dict, world_seed: int, *, clock: Clock | None = None) -> dict:
    """A normalised, fully bound copy of `case` for one trial."""
    c = normalise(copy.deepcopy(case))
    ck = case_clock(c, clock)
    facts = draw_facts(c, world_seed, ck)
    body = {k: v for k, v in c.items() if k != "facts"}
    bound = substitute(body, facts)
    bound = resolve_times(bound, ck)
    bound["_facts"] = facts
    bound["_clock"] = ck
    bound["_world_seed"] = world_seed
    return bound
