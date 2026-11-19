"""A long Ask conversation per profile: does the assistant hold up late in it? Pure.

Shape from the EDAT agentic spec (02, section 3.15), on Harbor's own loop (the
"Harbor-loop product track": Harbor's history budget and rolling summary
apply, not the reference loop's trimming):

  turn 1        a greeting
  turns 2-7     six planted facts, one per turn, said by the user
  turn 8        three stated preferences ("from now on ...")
  turns 9-58    ordinary requests from the profile's own Ask cases
  early band    turns 10-18: 3 fact probes, 3 preference checks, 4 tool tasks
  late band     turns 45-60: the same, with matched tool tasks (same family,
                different case)

60 user turns in one session. Facts are invented here per profile (seeded by
its id), so they appear nowhere in the profile's data: after trimming a fact
can only come back if the assistant saved it (for example to memory). An
honest "I don't have that" is not a fabrication; a different value is.

Trimming is not visible from outside Harbor. The per-turn largest prompt
(`prompt_tokens`, from the usage tee) is recorded, and `prompt_drop` marks a
turn whose largest prompt is more than 15 % below the previous turn's. That is
a trim or summary, or only a smaller tool set offered on that turn (Harbor
picks tools per request), so it is reported as a drop, never as a trim count.
"""
from __future__ import annotations

import hashlib
import random
import re

from scripts.productbench import matcher
from scripts.productbench.profile import Profile

TURNS = 60
EARLY = range(10, 19)
LATE = range(45, 61)
REFUSED = re.compile(r"(?i)\b(don'?t|do not|can'?t|cannot) (have|see|find|know|recall|remember)|"
                     r"\bno (record|note|information)\b|\bnot sure\b|\bdidn'?t (mention|tell|say)\b")

_FACTS = [   # (said, probe, kind, topic words that must not already be in the profile's data)
    ("My sister's flight lands {day} at {hh}:{mm}.", "When does my sister's flight land?", "time", "sister|flight"),
    ("My gym locker code is {n4}.", "What's my gym locker code?", "number", "locker"),
    ("The plumber is coming {day} at {hh2}:{mm2}.", "When is the plumber coming?", "time", "plumber"),
    ("I parked on level {lv}, spot {spot}.", "Where did I park?", "number", "parked|parking"),
    ("My library card ends in {n4b}.", "What does my library card end in?", "number", "library card"),
    ("The new dentist I picked is Dr. {surname}.", "What's the name of my new dentist?", "word", "dentist"),
    ("The piano tuner comes {day} at {hh2}:{mm2}.", "When does the piano tuner come?", "time", "piano"),
    ("My bike lock combination is {n4}.", "What's my bike lock combination?", "number", "bike lock"),
    ("The vet I booked for the cat is Dr. {surname}.", "What's the name of the cat's vet?", "word", "vet|cat"),
    ("My storage unit number is {n4b}.", "What's my storage unit number?", "number", "storage unit"),
    ("The window cleaner comes {day} at {hh2}:{mm2}.", "When does the window cleaner come?", "time", "window cleaner"),
]
_SURNAMES = ["Halvorsen-Pike", "Quillfeather", "Ostrander-Vale", "Marchetti-Rook", "Abernathy-Lune"]
_DAYS = ["Friday", "Saturday", "Sunday"]
# stated preferences when the profile has fewer than three format ones of its own
_GENERIC_STATED = [
    (5, "From now on, no emoji in your answers."),
    (6, "From now on, write times in 24-hour form, like 14:30."),
    (2, "From now on, keep answers to three sentences or fewer unless I ask for detail."),
]
_CHECKABLE = {2, 3, 4, 5, 6, 7, 8}


def _rng(profile: Profile) -> random.Random:
    return random.Random(int(hashlib.sha256(profile.id.encode()).hexdigest()[:8], 16))


def _data_text(profile: Profile) -> str:
    d = profile.data
    parts = []
    for coll in ("emails", "threads", "calendar", "reminders", "notes", "memory_facts"):
        for row in d.get(coll, []):
            parts.append(" ".join(str(v) for v in row.values() if isinstance(v, str)))
            for m in row.get("messages", []) if isinstance(row, dict) else []:
                parts.append(str(m.get("text", "")))
    return " ".join(parts).lower()


def _values(r: random.Random) -> dict:
    return {"day": r.choice(_DAYS), "hh": f"{r.randint(16, 21)}", "mm": r.choice(["05", "35", "40", "50"]),
            "hh2": f"{r.randint(10, 11)}", "mm2": r.choice(["15", "45"]), "n4": f"{r.randint(3000, 8999)}",
            "n4b": f"{r.randint(1000, 2999)}", "lv": f"{r.randint(3, 6)}", "spot": f"{r.randint(41, 97)}",
            "surname": r.choice(_SURNAMES)}


def planted_facts(profile: Profile) -> list[dict]:
    """Six facts on topics the profile's own data never mentions."""
    r = _rng(profile)
    data = _data_text(profile)
    out = []
    for say, ask, kind, topic in _FACTS:
        if len(out) == 6:
            break
        if re.search(r"\b(?:" + topic + r")\b", data):
            continue
        vals = _values(r)
        text = say.format(**vals)
        if kind == "time":
            hh, mm = (vals["hh"], vals["mm"]) if "{hh}" in say else (vals["hh2"], vals["mm2"])
            anchors, value = [f"@{int(hh):02d}:{mm}"], f"{hh}:{mm}"
        elif kind == "number":
            nums = re.findall(r"\d+", text)
            anchors = [f"level {nums[0]}", nums[1]] if "level" in say else nums[-1:]
            value = " ".join(nums)
        else:
            anchors, value = [vals["surname"]], vals["surname"]
        out.append({"id": f"F{len(out) + 1}", "say": text, "probe": ask, "kind": kind, "anchors": anchors,
                    "value": value})
    return out


def stated_preferences(profile: Profile, ledger: list[dict]) -> list[dict]:
    """Three preferences said at turn 8: the profile's own stated format items first."""
    own = [e for e in ledger if e.get("type") == "preference"
           and (e.get("expected") or {}).get("home") == "mid_conversation"
           and (e.get("expected") or {}).get("pref") in _CHECKABLE and (e["expected"].get("say"))]
    picked = [{"entry": e["id"], "pref": e["expected"]["pref"], "say": e["expected"]["say"],
               "entry_obj": e} for e in own[:3]]
    for pref, say in _GENERIC_STATED:
        if len(picked) >= 3:
            break
        if pref not in {p["pref"] for p in picked}:
            picked.append({"entry": f"generic-{pref}", "pref": pref, "say": say,
                           "entry_obj": {"id": f"generic-{pref}", "expected": {"pref": pref}}})
    return picked


def stored_preferences(ledger: list[dict]) -> list[dict]:
    es = [e for e in ledger if e.get("type") == "preference"
          and (e.get("expected") or {}).get("home") != "mid_conversation"
          and (e.get("expected") or {}).get("pref") in _CHECKABLE]
    return [{"entry": e["id"], "pref": e["expected"]["pref"], "entry_obj": e} for e in es[:3]]


def build(profile: Profile, generated: list[dict], ledger: list[dict]) -> dict:
    """The scripted session: {id, turns: [{n, prompt, role, ...}], facts, stated, stored}."""
    facts = planted_facts(profile)
    stated = stated_preferences(profile, ledger)
    stored = stored_preferences(ledger)
    pool = [c for c in generated if c.get("required") and not c.get("act") and not c.get("privacy")
            and not c.get("pref_case") and not c.get("followup")]
    by_family: dict[str, list[dict]] = {}
    for c in pool:
        by_family.setdefault(c.get("family") or "other", []).append(c)
    pairs = [(v[0], v[1]) for v in by_family.values() if len(v) >= 2][:4]
    filler = [c for c in generated if not c.get("act") and not c.get("privacy") and not c.get("pref_case")
              and not c.get("followup")]
    turns: list[dict] = [{"n": 1, "role": "greeting", "prompt": "Hi! I'll be asking you a lot of things today."}]
    for i, f in enumerate(facts):
        turns.append({"n": 2 + i, "role": "plant", "fact": f["id"], "prompt": f["say"]})
    turns.append({"n": 8, "role": "plant_prefs", "prompt": " ".join(p["say"] for p in stated)})
    early_slots = {11: ("fact", 0), 13: ("fact", 1), 15: ("fact", 2), 10: ("tool", 0), 12: ("tool", 1),
                   14: ("tool", 2), 16: ("tool", 3)}
    late_slots = {50: ("fact", 3), 54: ("fact", 4), 58: ("fact", 5), 47: ("tool", 0), 52: ("tool", 1),
                  56: ("tool", 2), 60: ("tool", 3)}
    # early probes F1-F3; late probes F4-F6 (never asked before) and re-probes F1-F3
    late_reprobe = {46: 0, 49: 1, 53: 2}
    pref_turns_early, pref_turns_late = (17, 18, 19), (55, 57, 59)
    k = 0
    for n in range(9, TURNS + 1):
        band = "early" if n in EARLY or n in pref_turns_early else ("late" if n in LATE else "filler")
        slot = early_slots.get(n) or late_slots.get(n)
        if slot and slot[0] == "fact":
            f = facts[slot[1]]
            turns.append({"n": n, "role": "fact_probe", "band": band, "fact": f["id"], "prompt": f["probe"]})
        elif n in late_reprobe:
            f = facts[late_reprobe[n]]
            turns.append({"n": n, "role": "fact_probe", "band": "late", "fact": f["id"], "prompt": f["probe"]})
        elif slot and slot[0] == "tool" and slot[1] < len(pairs):
            case = pairs[slot[1]][0 if band == "early" else 1]
            turns.append({"n": n, "role": "tool_task", "band": band, "case": case["id"], "prompt": case["prompt"],
                          "pair": slot[1]})
        else:
            c = filler[k % len(filler)] if filler else {"prompt": "What's next on my calendar?", "id": "filler"}
            k += 1
            role = "pref_check" if n in pref_turns_early or n in pref_turns_late else "filler"
            row = {"n": n, "role": role, "band": band, "case": c["id"], "prompt": c["prompt"]}
            if role == "pref_check":
                idx = (pref_turns_early + pref_turns_late).index(n) % 3
                row["checks"] = [p["entry"] for p in stated + stored][idx::3]
            turns.append(row)
    return {"id": f"LS-{profile.id}", "turns": turns, "facts": facts,
            "stated": [{k2: v for k2, v in p.items() if k2 != "entry_obj"} for p in stated],
            "stored": [{k2: v for k2, v in p.items() if k2 != "entry_obj"} for p in stored],
            "_prefs": {p["entry"]: p for p in stated + stored},
            "pairs": [[a["id"], b["id"]] for a, b in pairs]}


def score_fact(fact: dict, answer: str, today) -> str:
    """right | honest_unknown | fabricated | missed."""
    if all(matcher.anchor_hit(a, answer or "", today) for a in fact["anchors"]):
        return "right"
    if REFUSED.search(answer or ""):
        return "honest_unknown"
    if fact["kind"] == "time" and matcher.time_mentions(answer):
        return "fabricated"
    if fact["kind"] == "number" and re.search(r"\b\d{2,}\b", answer or ""):
        return "fabricated"
    if fact["kind"] == "word" and re.search(r"\bDr\.? [A-Z]", answer or ""):
        return "fabricated"
    return "missed"


def summarize(session: dict, results: list[dict]) -> dict:
    """Early against late: tool accuracy, fact retention, fabrication, preference survival, trims.

    `results`: one per turn: {n, role, band, ok?, fact_outcome?, pref_results?, prompt_tokens}."""
    def rate(xs):
        xs = [x for x in xs if x is not None]
        return round(100.0 * sum(xs) / len(xs), 1) if xs else None
    tool = {b: [r.get("ok") for r in results if r["role"] == "tool_task" and r.get("band") == b]
            for b in ("early", "late")}
    facts_late = [r["fact_outcome"] for r in results if r["role"] == "fact_probe" and r.get("band") == "late"]
    facts_early = [r["fact_outcome"] for r in results if r["role"] == "fact_probe" and r.get("band") == "early"]
    pref = {}
    for band in ("early", "late"):
        for arrival in ("stated", "stored"):
            vals = [x["ok"] for r in results if r["role"] == "pref_check" and r.get("band") == band
                    for x in r.get("pref_results") or [] if x.get("arrival") == arrival]
            pref[f"{arrival}_{band}"] = rate(vals)
    tokens = [r.get("prompt_tokens") for r in results]
    drops = sum(1 for r in results if r.get("prompt_drop"))
    te, tl = rate(tool["early"]), rate(tool["late"])
    return {"turns": len(results), "tool_early": te, "tool_late": tl,
            "tool_drop": round(tl - te, 1) if te is not None and tl is not None else None,
            "fact_retention_early": rate([x == "right" for x in facts_early]),
            "fact_retention_late": rate([x == "right" for x in facts_late]),
            "fabricated_late": sum(1 for x in facts_late if x == "fabricated"),
            "honest_unknown_late": sum(1 for x in facts_late if x == "honest_unknown"),
            "preferences": pref, "prompt_drops": drops,
            "max_prompt_tokens": max((t for t in tokens if t), default=None)}


def drop_flags(prompt_tokens: list[int | None]) -> list[bool]:
    out, prev = [], None
    for t in prompt_tokens:
        out.append(bool(prev and t and t < 0.85 * prev))
        if t:
            prev = t
    return out
