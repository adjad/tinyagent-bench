"""Writing generators for W5 (style control) and W7 (longer pieces), spec 03 section 9.

Every generator is `gen(seed, *, index, profile) -> case` and is deterministic in `seed`.
`index` picks the scenario (and the preference combination, voice or variant), the seed
fills the slots (people, dates, figures). Cases follow the contract in `writing.py`;
the reference drafts are built from the same slots, so they pass the checks by construction.

W5 (kind -> notes)
  standing_pref  an email under 1-2 of P5-P9. The brief gives times as "3:30 pm", dates as
                 "Tuesday 19 November" and quantities in miles, pounds or feet, so the
                 draft has to convert. `pref_meta` carries the `checkers.Meta` fields and
                 `pref_texts` (the sentences the system prompt shows).
  one_off        a text with one style request in the brief. What the deterministic checks
                 cannot see is named in `pref_meta["one_off"]` (key) and
                 `pref_meta["one_off_text"]` (the rule in words); see ONE_OFF_KEYS.
  voice          4-6 samples of the person's own writing; `pref_meta["neutral_reference"]`
                 is the plain version of the same text (the WD10 comparison point).
  conventions    exact subject-line prefix, greeting and sign-off; the prefix is in
                 `pref_meta["subject_prefix"]`.
  exact_count    exactly N words or N sentences (`exact_words` / `exact_sentences`).
W7 (neutral senders only)
  consistency    a project update that names 3 people, 2 places and 4 figures (`entities`).
  outline        two turns: an outline, then the piece in the outline's order.
  title          a title (<= 8 words) and an email subject line (<= 10 words) for a given piece.
"""
from __future__ import annotations

import random
import re
from typing import Callable

from edat.quality import normalize as N
from edat.quality import writing as W
from edat.quality.world import World, long_date, spoken_time

GENERATOR = "writing_gen_style/1"

# What `pref_meta["one_off"]` can hold: the key and the rule the checks agent may add.
ONE_OFF_KEYS = {
    "no_exclamation": "the draft contains no '!'",
    "starts_with_date": "the draft starts with the date in the short form given in the request (e.g. 'Tue 19 Nov')",
    "all_lowercase": "the draft has no capital letters",
    "ends_with_question": "the last sentence of the draft ends with '?'",
    "no_contractions": "the draft contains no contraction (no apostrophe-t/-s/-m/-ll/-re/-ve/-d forms)",
    "no_greeting_or_signoff": "the draft has no greeting line and no sign-off",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _first(name: str) -> str:
    return name.split()[0]


def _last(name: str) -> str:
    p = name.split()
    return p[-1] if len(p) > 1 else ""


def _ctx(seed: int, profile: str | None, rels=("friend",)):
    """rng, world, today, sender, recipient for one case."""
    rng = random.Random(seed)
    if profile:
        today = W.profile_today(profile)
        w = World.make(seed, year=today.year)
        w.today = today
        sender = W.profile_sender(profile)
        cs = W.contacts(profile, lambda c: "(" not in c["name"] and "(" not in (c.get("relation") or "")
                        and len(c["name"].split()) <= 2)
        c = rng.choice(cs)
        rcp = {"name": c["name"], "relation": c["relation"]}
    else:
        w = World.make(seed, year=2027)
        today = w.today
        s = w.contacts[0]
        sender = {"name": s["name"], "preferred_name": s["first"]}
        r = w.contacts[1]
        rcp = {"name": r["name"], "relation": rng.choice(list(rels))}
    return rng, w, today, sender, rcp


def _time(rng: random.Random) -> str:
    return f"{rng.choice([9, 10, 11, 13, 14, 15, 16, 17, 18]):02d}:{rng.choice(['00', '15', '30', '45'])}"


def _slots(w: World, rng: random.Random, cy: int, places=None) -> dict:
    d = w.date_after(2, 18)
    d2 = w.date_after(19, 33)
    t = _time(rng)
    t2 = f"{rng.choice([16, 17, 18, 19, 20]):02d}:{rng.choice(['00', '30'])}"
    return {
        "d": d, "d2": d2, "d_long": long_date(d), "d2_long": long_date(d2),
        "d_p7": N.format_p7(d, cy), "d2_p7": N.format_p7(d2, cy),
        "t": t, "t12": spoken_time(t), "t2": t2, "t2_12": spoken_time(t2),
        "amt": w.amount(20, 300), "code": w.code(),
        "place": rng.choice(places) if places else w.place(),
        "n": rng.randrange(12, 48),
    }


def _anchor(key: str, s: dict):
    if key == "d":
        return W.date_anchor(s["d"])
    if key == "d2":
        return W.date_anchor(s["d2"])
    if key == "t":
        return W.time_anchor(s["t"])
    if key == "t2":
        return W.time_anchor(s["t2"])
    if key == "amt":
        return W.amount_anchor(s["amt"])
    if key == "n":
        return W.number_anchor(str(s["n"]))
    return s[key]          # code, place: plain strings


def _req_forms(s: dict) -> dict:
    """Slot text as the brief writes it (long dates, 12-hour times)."""
    return {"d": s["d_long"], "d2": s["d2_long"], "d_long": s["d_long"], "d2_long": s["d2_long"],
            "t": s["t12"], "t12": s["t12"], "t2": s["t2_12"], "t2_12": s["t2_12"], "amt": s["amt"],
            "code": s["code"], "place": s["place"], "n": s["n"]}


def _ref_forms(s: dict) -> dict:
    """Slot text for neutral message drafts (short dates, 24-hour times)."""
    return {"d": s["d_p7"], "d2": s["d2_p7"], "t": s["t"], "t2": s["t2"], "amt": s["amt"], "code": s["code"],
            "place": s["place"], "n": s["n"]}


def _lower_first(s: str) -> str:
    if s[:2] in ("I ", "I'") or not s[:1].isupper() or s[1:2].isupper():
        return s
    return s[0].lower() + s[1:]


def _with_extra(case: dict, extra: list[str]) -> str:
    """`writing.render_request` with extra style lines placed before the constraint lines."""
    base = W.render_request(case)
    cons = "\n".join(W.render_constraints(case["constraints"], case["surface"]))
    if cons and base.endswith(cons):
        base = base[:-len(cons)]
        return base + "\n".join(extra + [cons]) if extra else base + cons
    return base + ("\n" + "\n".join(extra) if extra else "")


def _case(seed, sub, kind, today, profile, sender, purpose, rcp=None, **fields):
    return W.new_case(sub=sub, kind=kind, seed=seed, generator=GENERATOR, today=today, profile=profile,
                      sender=sender, purpose=purpose, recipient=rcp, **fields)


def _pts(specs, s, req) -> list[dict]:
    """Points from (request template, [anchor keys]) specs."""
    out = []
    for i, (tmpl, keys) in enumerate(specs, 1):
        out.append(W.point(f"p{i}", tmpl.format(**req), [_anchor(k, s) for k in keys]))
    return out


# ---------------------------------------------------------------------------
# W5 standing_pref
# ---------------------------------------------------------------------------

COMBOS = [("P6", "P7"), ("P9",), ("P5", "P9"), ("P8",), ("P6",), ("P7", "P9"), ("P8", "P9"), ("P5",),
          ("P6", "P8"), ("P7",)]

EMAIL_SC = [
    dict(purpose="arrange collecting the camping trailer I am borrowing", rels=["friend", "neighbour", "colleague"],
         places=["Fernhill Depot", "Oakbarrow Yard", "Saltwick Garage"], unit="mi", vals=[10, 15, 20, 25, 30],
         base_req="you will pick the trailer up from {place}", base_ref="I will pick the trailer up from {place}.",
         when_req="pickup is on {d_long} at {t12}", when_ref="Pickup is on {d} at {t}.",
         qty_req="the drive there is {q} each way", qty_ref="The drive there is {q} each way.",
         tone_req="thank them warmly, it is a big favour", tone_ref="Thank you so much, this is a big favour.",
         close_ref="I will bring it back clean.", subj="Collecting the camping trailer"),
    dict(purpose="invite them on a Sunday hike", rels=["friend", "cousin", "colleague"],
         places=["Larkspur Ridge", "Hollin Fell", "Marrow Heath"], unit="mi", vals=[6, 8, 12, 14, 18],
         base_req="you are meeting at the car park for {place}", base_ref="We are meeting at the car park for {place}.",
         when_req="meet on {d_long} at {t12}", when_ref="Let us meet on {d} at {t}.",
         qty_req="the loop is {q} long", qty_ref="The loop is {q} long, with a long stop for lunch.",
         tone_req="sound keen but relaxed", tone_ref="It should be a lovely walk, and I would enjoy the company.",
         close_ref="Bring good boots.", subj="Hike plans"),
    dict(purpose="confirm the sofa delivery with the neighbour who is helping", rels=["neighbour", "friend", "brother"],
         places=["Quarry Lane", "Pellam Court", "Greywell Row"], unit="lb", vals=[90, 110, 130, 150, 180],
         base_req="the sofa goes to the flat on {place}", base_ref="The sofa is going to the flat on {place}.",
         when_req="the van arrives on {d_long} at {t12}", when_ref="The van arrives on {d} at {t}.",
         qty_req="the sofa weighs {q}", qty_ref="The sofa weighs {q}, so two people will need to carry it.",
         tone_req="say you are grateful for the help", tone_ref="I am really grateful for the help.",
         close_ref="I will have the stairs cleared.", subj="Sofa delivery"),
    dict(purpose="suggest dinner after the concert", rels=["friend", "sister", "colleague"],
         places=["Marigold Bistro", "Tern Kitchen", "Olive Row Cafe"], unit="mi", vals=[2, 3, 4, 5, 6],
         base_req="you booked a table at {place}", base_ref="I booked a table at {place}.",
         when_req="the table is on {d_long} at {t12}", when_ref="The table is on {d} at {t}.",
         qty_req="the walk from the hall is {q}", qty_ref="The walk from the hall is {q}, which is easy after the show.",
         tone_req="sound excited about it", tone_ref="I am looking forward to the evening.",
         close_ref="The menu has plenty of vegetarian choices.", subj="Dinner after the concert"),
    dict(purpose="plan the layout of the hall for the street party", rels=["neighbour", "colleague", "friend"],
         places=["Tidewater Hall", "Beacon Hall", "Pinegate Hall"], unit="ft", vals=[30, 40, 50, 60, 80],
         base_req="the party is in {place}", base_ref="The party is in {place}.",
         when_req="setup starts on {d_long} at {t12}", when_ref="Setup starts on {d} at {t}.",
         qty_req="the room is {q} long", qty_ref="The room is {q} long, which is enough for six tables.",
         tone_req="thank them for taking charge of the tables", tone_ref="Thank you for taking charge of the tables.",
         close_ref="I will bring extra chairs.", subj="Street party layout"),
    dict(purpose="agree a swap of seedlings and compost", rels=["neighbour", "friend", "aunt"],
         places=["Thistle Allotments", "Bramble Gate", "Wren Meadow"], unit="lb", vals=[20, 25, 30, 40, 50],
         base_req="the swap happens at {place}", base_ref="The swap happens at {place}.",
         when_req="come on {d_long} at {t12}", when_ref="Please come on {d} at {t}.",
         qty_req="you will bring {q} of compost", qty_ref="I will bring {q} of compost for the beds.",
         tone_req="say you love their tomato plants", tone_ref="I love your tomato plants.",
         close_ref="I will bring a spare crate for the seedlings.", subj="Seedling and compost swap"),
    dict(purpose="coordinate a shared drive to the lake", rels=["colleague", "friend", "cousin"],
         places=["Cedar Lake", "Harrow Water", "Linton Mere"], unit="mi", vals=[40, 45, 60, 75, 90],
         base_req="you are driving to {place}", base_ref="We are heading to {place}.",
         when_req="leave on {d_long} at {t12}", when_ref="We leave on {d} at {t}.",
         qty_req="the drive is {q}", qty_ref="The drive is {q}, so I will fill the tank beforehand.",
         tone_req="sound cheerful", tone_ref="It will be a great day out.",
         close_ref="I can collect you from your door.", subj="Drive to the lake"),
    dict(purpose="confirm what you are carrying to the charity run", rels=["friend", "colleague", "brother"],
         places=["Ashgrove Park", "Mill Common", "Eastbury Green"], unit="lb", vals=[15, 20, 25, 35, 45],
         base_req="the run starts at {place}", base_ref="The run starts at {place}.",
         when_req="the start is on {d_long} at {t12}", when_ref="The start is on {d} at {t}.",
         qty_req="you will carry {q} of water and supplies", qty_ref="I will carry {q} of water and supplies.",
         tone_req="say you are proud of the team", tone_ref="I am proud of everyone on the team.",
         close_ref="I will wear the green shirt so you can spot me.", subj="Charity run"),
]

_NEUTRAL_SIGN = ["Kind regards,", "Best wishes,", "Warm regards,", "With thanks,"]
_METRIC = {"mi": (1.609344, "km"), "lb": (0.45359237, "kg"), "ft": (0.3048, "m")}
_IMP_WORD = {"mi": "miles", "lb": "lb", "ft": "ft"}


def _metric(v: int, unit: str) -> tuple[str, str]:
    f, mu = _METRIC[unit]
    x = v * f
    d = 0 if x >= 10 else 1
    return f"{round(x, d):.{d}f}", mu


def gen_standing_pref(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = EMAIL_SC[index % len(EMAIL_SC)]
    prefs = COMBOS[index % len(COMBOS)]
    rng, w, today, sender, rcp = _ctx(seed, profile, sc["rels"])
    cy = today.year
    s = _slots(w, rng, cy, sc["places"])
    first = _first(rcp["name"])
    sfirst = sender["preferred_name"]
    v = rng.choice(sc["vals"])
    unit = sc["unit"]
    imp = f"{v} {_IMP_WORD[unit]}"
    mval, munit = _metric(v, unit)
    metric = f"{mval} {munit}"
    req = {**_req_forms(s), "q": imp}
    ref = {"d": s["d_p7"] if "P7" in prefs else s["d_long"], "t": s["t"] if "P6" in prefs else s["t12"],
           "q": metric if "P8" in prefs else imp, "place": s["place"]}

    if "P9" in prefs:
        signoff = W.PROFILE_SIGNOFF[profile] if profile else f"{rng.choice(_NEUTRAL_SIGN)}\n{sfirst}"
    else:
        signoff = None
    ref_sign = signoff or f"Best,\n{sfirst}"

    q_anchor = W.number_anchor(str(v))
    if "P8" in prefs:
        q_anchor = {"type": "number", "value": mval}
        if mval.endswith(tuple("0123456789")) and "." not in mval:
            f, _ = _METRIC[unit]
            q_anchor["aliases"] = [f"{v * f:.1f}"]
    points = [
        W.point("p1", sc["base_req"].format(**req), [s["place"]]),
        W.point("p2", sc["when_req"].format(**req), [W.date_anchor(s["d"]), W.time_anchor(s["t"])]),
        W.point("p3", sc["qty_req"].format(**req), [q_anchor]),
        W.point("p4", sc["tone_req"], []),
    ]
    texts = {"P5": "never use emoji", "P6": "write times in 24-hour form, like 17:45",
             "P7": 'write dates like "Sat 8 Mar", with the weekday and no year',
             "P8": "use metric units (kilometres, kilograms, metres) instead of miles, pounds or feet",
             "P9": ("end every drafted email with the sign-off \"" + (signoff or "").replace("\n", " / ") + "\"")}
    meta: dict = {"pref_texts": [texts[p] for p in prefs], "case_year": cy}
    if "P6" in prefs:
        meta.update(source_times=[s["t"]], needs_time=True)
    if "P7" in prefs:
        meta.update(source_dates=[s["d"].isoformat()], needs_date=True)
    if "P8" in prefs:
        meta["source_quantities"] = [{"value": v, "unit": unit}]
    if "P9" in prefs:
        meta["signoff"] = signoff

    body = " ".join([sc["base_ref"].format(**ref), sc["when_ref"].format(**ref), sc["qty_ref"].format(**ref),
                     sc["tone_ref"], sc["close_ref"]])
    draft = f"Subject: {sc['subj']}\nHi {first},\n\n{body}\n\n{ref_sign}"
    case = _case(seed, "W5", "standing_pref", today, profile, sender, sc["purpose"], rcp,
                 points=points, active_prefs=list(prefs), pref_meta=meta,
                 constraints={"max_words": 110, "subject": True},
                 allowed_text=f"{mval} {munit}", reference_draft=draft)
    return W.finish(case)


# ---------------------------------------------------------------------------
# W5 voice (text message in the person's voice)
# ---------------------------------------------------------------------------

# Neutral message scenarios: three plain sentences; the anchors are always in sentences 1-2.
MSG_SC = [
    dict(purpose="split this month's electricity bill", rels=["flatmate", "friend", "sister"], places=None,
         pts=[("the electricity bill is {amt} euros this month", ["amt"]),
              ("ask them to send their half by {d_long}", ["d"])],
         s=["The electricity bill came to {amt} euros this month.", "Could you send me your half by {d}?",
            "I will pay the provider the same day."]),
    dict(purpose="say you are running late for dinner", rels=["friend", "partner", "sister"],
         places=["Marigold Bistro", "Tern Kitchen", "Olive Row Cafe"],
         pts=[("you will arrive at {place} around {t12}", ["place", "t"])],
         s=["I am running late for dinner.", "I will reach {place} around {t}.", "Please start without me."]),
    dict(purpose="tell them a parcel is waiting for them", rels=["neighbour", "flatmate", "friend"],
         places=["Ferngate Station", "Bramble Market", "Old Mill Bakery"],
         pts=[("a parcel for them is at {place}", ["place"]), ("the collection code is {code}", ["code"])],
         s=["A parcel for you arrived at {place}.", "The collection code is {code}.",
            "You can pick it up any time this week."]),
    dict(purpose="ask to borrow their folding chairs", rels=["neighbour", "friend", "colleague"], places=None,
         pts=[("ask to borrow their folding chairs on {d_long}", ["d"]),
              ("you will bring them back by {t12} the next day", ["t"])],
         s=["Could I borrow your folding chairs on {d}?", "I will bring them back by {t} the next day.",
            "I can collect them the evening before."]),
    dict(purpose="tell them the planning call has moved", rels=["colleague", "friend", "brother"], places=None,
         pts=[("the planning call moves from {d_long} to {d2_long}", ["d", "d2"]),
              ("the time stays {t12}", ["t"])],
         s=["The planning call has moved from {d} to {d2}.", "It is still at {t}.",
            "Please tell me if that clashes with anything."]),
    dict(purpose="sort out concert tickets", rels=["friend", "sister", "cousin"],
         places=["Quayside Theatre", "Riverside Hall", "Larkspur Studio"],
         pts=[("the tickets cost {amt} euros each", ["amt"]), ("the show starts at {t12} at {place}", ["t", "place"])],
         s=["The tickets cost {amt} euros each.", "The show starts at {t} at {place}.",
            "I can pay for both and you can pay me back later."]),
    dict(purpose="say you found their scarf", rels=["friend", "colleague", "flatmate"],
         places=["Cobalt Cafe", "Kestrel Gym", "Hollow Oak Pub"],
         pts=[("they left a scarf at {place}", ["place"]), ("you can bring it on {d_long}", ["d"])],
         s=["You left your scarf at {place}.", "I can bring it on {d}.", "It is safe in my bag for now."]),
    dict(purpose="confirm when the plumber is coming", rels=["flatmate", "partner", "neighbour"], places=None,
         pts=[("the plumber comes on {d_long} at {t12}", ["d", "t"]), ("the job reference is {code}", ["code"])],
         s=["The plumber is coming on {d} at {t}.", "The job reference is {code}.",
            "Someone needs to be home to let him in."]),
]

# Neutral voices: invented samples plus the rules the reference draft applies.
VOICES = [
    dict(id="lower_terse", lower=True, contract=True, end="", open="hey {first},", close="", drop3=True,
         samples=["running late, be there in 10", "can you grab milk on your way", "nah i'm good thanks",
                  "ok see you at the usual spot", "lol that was close", "sounds good, bring the charger"]),
    dict(id="enthusiastic", lower=False, contract=True, end="!", open="Hi {first}!", close="Thanks so much!",
         drop3=False,
         samples=["Yay, that works for me!", "Can't wait to see everyone!", "Just landed and starving!!",
                  "Thank you so much, you're the best!", "Count me in!", "That sounds amazing!"]),
    dict(id="formal", lower=False, contract=False, end=".", open="Hello {first},", close="Thank you.", drop3=False,
         samples=["I will arrive at the station at noon.", "Please let me know if the plan changes.",
                  "Thank you for sending the details.", "I am afraid I cannot attend on Friday.",
                  "Kindly confirm the address.", "I appreciate your help with this."]),
    dict(id="dry_british", lower=False, contract=True, end=".", open="Alright {first},", close="Cheers.",
         drop3=False,
         samples=["Cheers, that works.", "Running a bit late, sorry mate.", "Not bad at all, ta.",
                  "Fine by me, see you there.", "Right, I'll bring the folding table.", "Cheers for sorting that, mate."]),
    dict(id="ellipsis", lower=True, contract=True, end="", open="hi {first}...", close="", drop3=True, join="... ",
         samples=["ok so... maybe thursday?", "hmm let me check... i think yes", "sure... i'll bring something",
                  "wait, is it the 7th or the 8th?", "ok ok, got it", "lol fine, you win"]),
    dict(id="warm_x", lower=False, contract=True, end=".", open="Hi {first},", close="Lots of love x", drop3=False,
         samples=["Hi love, that sounds lovely x", "Can't wait to see you both x", "Thank you darling, safe trip x",
                  "Yes please, see you soon x", "Wonderful news, so happy for you x", "Lots of love x"]),
]
PROFILE_VOICES = {
    "student": dict(id="p_student", lower=True, contract=True, end="", open="hey {first},", close="", drop3=True),
    "tech": dict(id="p_tech", lower=False, contract=True, end="", open="Hey {first},", close="", drop3=True),
    "mixed": dict(id="p_mixed", lower=False, contract=True, end="!", open="Hi {first}!", close="", drop3=False),
}
_CONTRACT = [("I am ", "I'm "), ("I will ", "I'll "), ("It is ", "It's "), ("it is ", "it's "),
             ("do not ", "don't "), ("cannot ", "can't "), ("Please tell me if that clashes with anything.",
                                                             "Tell me if that clashes with anything.")]


def _apply_voice(v: dict, sentences: list[str], first: str) -> str:
    sents = sentences[:2] if v.get("drop3") else list(sentences)
    out = []
    for i, s in enumerate(sents):
        if v["contract"]:
            for x, y in _CONTRACT:
                s = s.replace(x, y)
        if v["lower"]:
            s = _lower_first(s)
            s = re.sub(r"\bI\b", "i", s)
        q = s.endswith("?")
        s = s.rstrip(".?!")
        last = i == len(sents) - 1
        if q:
            s += "?"
        elif v["end"] == "!":
            s += "!"
        elif v["end"] == "." or (v["end"] == "" and not last and not v["lower"]):
            s += "."
        out.append(s)
    if v.get("join"):
        text = "... ".join(x.rstrip(".?!") for x in out[:-1] + [""]).rstrip(". ") + "... " + out[-1] if len(out) > 1 else out[0]
    elif v["end"] == "" and v["lower"]:
        text = ""
        for i, x in enumerate(out):
            text += x + ("" if i == len(out) - 1 else (" " if x.endswith("?") else ", "))
    else:
        text = " ".join(out)
    if v["open"].endswith(","):
        text = _lower_first(text)
    text = v["open"].format(first=first) + " " + text
    if v.get("close"):
        text += " " + v["close"]
    return text


def _msg_parts(sc: dict, rng: random.Random, w: World, cy: int):
    s = _slots(w, rng, cy, sc["places"])
    req = _req_forms(s)
    points = _pts(sc["pts"], s, req)
    ref = _ref_forms(s)
    sents = [x.format(**ref) for x in sc["s"]]
    return s, points, sents


def gen_voice(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = MSG_SC[index % len(MSG_SC)]
    rng, w, today, sender, rcp = _ctx(seed, profile, sc["rels"])
    s, points, sents = _msg_parts(sc, rng, w, today.year)
    first = _first(rcp["name"])
    if profile:
        v = PROFILE_VOICES[profile]
        pool = sorted({m for m in W.own_messages(profile) if 3 <= len(m.split()) <= 28 and len(m) <= 150})
        k = rng.choice([4, 5, 6])
        samples = rng.sample(pool, k)
    else:
        v = VOICES[(index // 2) % len(VOICES)]
        samples = rng.sample(v["samples"], rng.choice([4, 5, 6]))
    draft = _apply_voice(v, sents, first)
    neutral = f"Hello {first}. " + " ".join(sents)
    case = _case(seed, "W5", "voice", today, profile, sender, sc["purpose"], rcp,
                 points=points, constraints={"max_words": 45}, context={"samples": samples},
                 pref_meta={"neutral_reference": neutral, "voice": v["id"]}, reference_draft=draft)
    return W.finish(case)


# ---------------------------------------------------------------------------
# W5 one_off
# ---------------------------------------------------------------------------

# s: three plain sentences, no contractions, no '!'. {on_d}/{by_d} drop out when the date leads.
ONEOFF_SC = [
    dict(purpose="remind them about next month's rent", rels=["flatmate", "friend", "brother"],
         pts=[("the rent for next month is {amt} euros", ["amt"]), ("you will transfer it on {d_long}", ["d"])],
         s=["The rent for next month is {amt} euros.", "I will transfer it{on_d}.",
            "Please confirm that the amount is right."],
         short="The rent is {amt} euros and I will transfer it{on_d}.", q="Is that amount right?"),
    dict(purpose="remind them about their dentist appointment", rels=["partner", "sister", "friend"],
         pts=[("the appointment is on {d_long} at {t12}", ["d", "t"])],
         s=["Your dentist appointment is{on_d} at {t}.", "It is a routine check.", "Please bring the insurance card."],
         short="Your dentist appointment is{on_d} at {t}.", q="Can you make it?"),
    dict(purpose="confirm a dinner booking", rels=["friend", "cousin", "colleague"],
         pts=[("dinner is booked for {d_long} at {t12}", ["d", "t"]), ("the deposit was {amt} euros", ["amt"])],
         s=["Dinner is booked{on_d} at {t}.", "I paid a deposit of {amt} euros.", "We can settle up on the night."],
         short="Dinner is booked{on_d} at {t}, deposit {amt} euros.", q="Does that time suit you?"),
    dict(purpose="ask them to pay back a taxi fare", rels=["friend", "colleague", "flatmate"],
         pts=[("the taxi cost {amt} euros", ["amt"]), ("ask them to pay you back by {d_long}", ["d"])],
         s=["The taxi last night cost {amt} euros.", "Could you pay me back{by_d}?", "A bank transfer is fine."],
         short="The taxi cost {amt} euros, so could you pay me back{by_d}?", q="Could you pay me back soon?"),
    dict(purpose="ask them to return a borrowed drill", rels=["neighbour", "friend", "brother"],
         pts=[("ask them to return the drill on {d_long}", ["d"]), ("before {t12}", ["t"])],
         s=["I need the drill back{on_d}.", "Could you drop it round before {t}?", "I need it for the shelves."],
         short="Could you return the drill{on_d}, before {t}?", q="Could you bring it round?"),
    dict(purpose="set up a quick call about the budget", rels=["colleague", "friend", "sister"],
         pts=[("a call on {d_long} at {t12}", ["d", "t"]), ("the budget is {amt} euros", ["amt"])],
         s=["Can we have a quick call{on_d} at {t}?", "I want to go through the budget of {amt} euros.",
            "It should take ten minutes."],
         short="Quick call{on_d} at {t} about the {amt} euro budget?", q="Does that work for you?"),
]
ONEOFF_VARIANTS = ["no_exclamation", "exact_two", "start_with_date", "all_lowercase", "max_words", "max_sentences",
                   "ends_with_question", "no_contractions", "no_greeting_signoff", "max_chars"]


def gen_one_off(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = ONEOFF_SC[index % len(ONEOFF_SC)]
    variant = ONEOFF_VARIANTS[index % len(ONEOFF_VARIANTS)]
    rng, w, today, sender, rcp = _ctx(seed, profile, sc["rels"])
    cy = today.year
    s = _slots(w, rng, cy)
    req = _req_forms(s)
    points = _pts(sc["pts"], s, req)
    first = _first(rcp["name"])
    lead = variant == "start_with_date"
    f = {**_ref_forms(s), "on_d": "" if lead else f" on {s['d_p7']}", "by_d": "" if lead else f" by {s['d_p7']}"}
    sents = [x.format(**f) for x in sc["s"]]
    short = sc["short"].format(**f)
    q = sc["q"]
    cons: dict = {"max_words": 60}
    extra: list[str] = []
    meta: dict = {}
    if variant == "no_exclamation":
        extra = ["Please do not use any exclamation marks."]
        draft = f"Hi {first}, " + " ".join([_lower_first(sents[0])] + sents[1:])
    elif variant == "exact_two":
        cons["exact_sentences"] = 2
        draft = sents[0] + " " + sents[1].rstrip(".") + ", and " + _lower_first(sents[2])
    elif variant == "start_with_date":
        extra = [f'Start the message with the date, in the short form "{s["d_p7"]}".']
        draft = f"{s['d_p7']}: " + " ".join(sents)
    elif variant == "all_lowercase":
        extra = ["Write it all in lowercase, with no capital letters at all."]
        draft = " ".join(sents).lower()
    elif variant == "max_words":
        cons["max_words"] = 20
        draft = short
    elif variant == "max_sentences":
        cons["max_sentences"] = 3
        draft = " ".join(sents)
    elif variant == "ends_with_question":
        extra = ["End the message with a question."]
        draft = f"Hi {first}, " + " ".join([_lower_first(sents[0]), sents[1], q])
    elif variant == "no_contractions":
        extra = ["Do not use contractions."]
        draft = f"Hi {first}, " + " ".join([_lower_first(sents[0])] + sents[1:])
    elif variant == "no_greeting_signoff":
        extra = ["No greeting and no sign-off, just the message."]
        draft = " ".join(sents)
    else:                                   # max_chars
        cons["max_chars"] = 120
        draft = short
    rule = {"no_exclamation": "no_exclamation", "start_with_date": "starts_with_date",
            "all_lowercase": "all_lowercase", "ends_with_question": "ends_with_question",
            "no_contractions": "no_contractions", "no_greeting_signoff": "no_greeting_or_signoff"}.get(variant)
    if rule:
        meta = {"one_off": rule, "one_off_text": ONE_OFF_KEYS[rule]}
        if rule == "starts_with_date":
            meta["lead_date"] = s["d_p7"]
    meta["variant"] = variant
    case = _case(seed, "W5", "one_off", today, profile, sender, sc["purpose"], rcp,
                 points=points, constraints=cons, pref_meta=meta, reference_draft=draft)
    case["turns"] = [_with_extra(case, extra)]
    return W.finish(case)


# ---------------------------------------------------------------------------
# W5 conventions
# ---------------------------------------------------------------------------

CONV_SC = [
    dict(purpose="update a colleague on the project status", rels=["colleague", "manager"], places=None,
         pts=[("the draft report is saved under reference {code}", ["code"]),
              ("ask for review comments by {d_long}", ["d"])],
         s=["The draft report is ready for review.", "It is saved under reference {code}.",
            "Please send your comments by {d}."], subj="draft report ready for review",
         prefixes=["Update:", "Reminder:"]),
    dict(purpose="ask the landlord for a signed form", rels=["landlord", "agent"], places=None,
         pts=[("you need the signed lease form, reference {code}", ["code"]), ("ask for it before {d_long}", ["d"])],
         s=["I need the signed lease form, reference {code}.", "Could you send it before {d}?",
            "A scan or a clear photo is fine."], subj="signed lease form needed", prefixes=["Request:", "Action:"]),
    dict(purpose="move a meeting to a later date", rels=["colleague", "manager"],
         places=["Room Cobalt", "Room Larch", "Room Finch"],
         pts=[("the meeting moves from {d_long} to {d2_long}", ["d", "d2"]), ("it stays in {place}", ["place"])],
         s=["I need to move our meeting from {d} to {d2}.", "It will stay in {place}.", "The start time is unchanged."],
         subj="meeting moved to a later date", prefixes=["Update:", "Heads up:"]),
    dict(purpose="ask a contractor for a repair quote", rels=["contractor", "neighbour"], places=None,
         pts=[("your budget is up to {amt} euros", ["amt"]), ("the work has to be done before {d_long}", ["d"])],
         s=["Could you send me a quote for the repair?", "My budget is up to {amt} euros.",
            "The work needs to be done before {d}."], subj="quote for the repair", prefixes=["Request:", "Question:"]),
    dict(purpose="confirm a venue booking", rels=["event manager", "colleague"],
         places=["Larkspur Studio", "Riverside Hall", "Copperleaf Hotel"],
         pts=[("the booking is at {place} on {d_long} at {t12}", ["place", "d", "t"]),
              ("the booking reference is {code}", ["code"])],
         s=["I am writing to confirm our booking at {place}.", "It is for {d} at {t}.",
            "The booking reference is {code}."], subj="venue booking confirmed", prefixes=["Update:", "Reminder:"]),
    dict(purpose="say a parcel arrived and thank the sender", rels=["friend", "colleague", "aunt"], places=None,
         pts=[("the parcel arrived on {d_long}", ["d"]), ("everything on order {code} was inside", ["code"])],
         s=["The parcel arrived on {d}.", "Everything on order {code} was inside.", "Thank you for the quick delivery."],
         subj="parcel received", prefixes=["Update:", "FYI:"]),
]
_GREETINGS = ["Hi {first},", "Dear {first},", "Hello {first},", "Good morning {first},", "Dear {title} {last},"]
_NEUTRAL_CLOSE = ["Best regards,", "Kind regards,", "Thanks,", "Warm wishes,", "Many thanks,"]


def gen_conventions(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = CONV_SC[index % len(CONV_SC)]
    rng, w, today, sender, rcp = _ctx(seed, profile, sc["rels"])
    s = _slots(w, rng, today.year, sc["places"])
    req = _req_forms(s)
    points = _pts(sc["pts"], s, req)
    first = _first(rcp["name"])
    gpool = _GREETINGS[:4] if profile or not _last(rcp["name"]) else _GREETINGS
    gtmpl = gpool[(index // 2 + index) % len(gpool)]
    greeting = gtmpl.format(first=first, last=_last(rcp["name"]), title=rng.choice(["Ms", "Mr", "Dr"]))
    sfirst = sender["preferred_name"]
    signoff = W.PROFILE_SIGNOFF[profile] if profile else f"{rng.choice(_NEUTRAL_CLOSE)}\n{sfirst}"
    prefix = sc["prefixes"][(index // 3) % len(sc["prefixes"])]
    ref = _ref_forms(s)
    body = " ".join(x.format(**ref) for x in sc["s"])
    draft = f"Subject: {prefix} {sc['subj']}\n{greeting}\n\n{body}\n\n{signoff}"
    case = _case(seed, "W5", "conventions", today, profile, sender, sc["purpose"], rcp,
                 points=points, pref_meta={"subject_prefix": prefix},
                 constraints={"max_words": 90, "subject": True, "greeting": greeting, "signoff": signoff},
                 reference_draft=draft)
    case["turns"] = [_with_extra(case, [f'Start the subject line with "{prefix}".'])]
    return W.finish(case)


# ---------------------------------------------------------------------------
# W5 exact_count
# ---------------------------------------------------------------------------

# seq: (sentence, required). Required sentences carry every anchor.
COUNT_SC = [
    dict(purpose="announce the library's new opening hours", rels=[], places=None,
         pts=[("the new hours start on {d_long}", ["d"]), ("the library opens at {t12} and closes at {t2_12} on weekdays", ["t", "t2"])],
         seq=[("From {d} the library has new opening hours.", True),
              ("It will open at {t} and close at {t2} on weekdays.", True),
              ("Weekend hours stay the same.", False), ("The reading room follows the same times.", False),
              ("Study spaces are free to use on a first come basis.", False),
              ("Staff at the front desk can answer any questions.", False)]),
    dict(purpose="warn the office that the shared fridge will be cleared", rels=[], places=None,
         pts=[("the fridge is cleared out on {d_long} at {t12}", ["d", "t"])],
         seq=[("The shared fridge will be cleared out on {d} at {t}.", True),
              ("Please take any food you want to keep before then.", False),
              ("Anything left behind will go in the compost bin.", False),
              ("Clean containers can be collected from the sink.", False),
              ("Thank you for keeping the kitchen tidy.", False)]),
    dict(purpose="tell residents a car park is closing", rels=[], places=["Millpond Yard", "Harrow Lane Car Park", "Beacon Court"],
         pts=[("the car park at {place} closes on {d_long}", ["place", "d"])],
         seq=[("Parking at {place} will close on {d}.", True),
              ("Please use the street spaces nearby instead.", False),
              ("The barrier will be removed that morning.", False),
              ("Disabled bays are not affected.", False),
              ("Updates will be posted at the front door.", False)]),
    dict(purpose="invite the team to a potluck lunch", rels=[], places=["Thistle Room", "Kestrel Room", "Fennel Room"],
         pts=[("the potluck is on {d_long} at {t12}", ["d", "t"]), ("it is in the {place}", ["place"])],
         seq=[("The team potluck is on {d} at {t} in the {place}.", True),
              ("Please bring one dish to share.", False), ("Plates and cups will be provided.", False),
              ("Tell us about any allergies in advance.", False),
              ("Vegetarian options are especially welcome.", False)]),
    dict(purpose="set out the garden watering rota", rels=[], places=None,
         pts=[("the rota starts on {d_long}", ["d"]), ("each shift is {n} minutes", ["n"])],
         seq=[("The garden watering rota starts on {d}.", True), ("Each shift is {n} minutes.", True),
              ("The hose is kept in the shed.", False), ("Please wind it back up when you finish.", False),
              ("The tap sticks, so turn it firmly.", False)]),
    dict(purpose="announce the next book club meeting", rels=[], places=["Larkspur Studio", "Riverside Hall", "Hollow Oak Pub"],
         pts=[("the meeting is on {d_long} at {t12}", ["d", "t"]), ("it is at {place}", ["place"])],
         seq=[("The book club meets next on {d} at {t} at {place}.", True),
              ("We will discuss the first half of the book.", False), ("Newcomers are very welcome.", False),
              ("Tea and biscuits will be on the table.", False),
              ("Please bring your own copy if you can.", False)]),
]


def gen_exact_count(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = COUNT_SC[index % len(COUNT_SC)]
    by_sentences = (index // len(COUNT_SC)) % 2 == 1
    rng, w, today, sender, rcp = _ctx(seed, profile, ["colleague"])
    s = _slots(w, rng, today.year, sc["places"])
    req = _req_forms(s)
    points = _pts(sc["pts"], s, req)
    # long dates: the shared splitter reads "Oct." as an abbreviation, not a sentence end
    ref = {**_ref_forms(s), "d": s["d_long"], "d2": s["d2_long"]}
    seq = [(t.format(**ref), r) for t, r in sc["seq"]]
    optional = [i for i, (_, r) in enumerate(seq) if not r]
    k = rng.randrange(1, len(optional) + 1) if len(optional) > 1 else 1
    if by_sentences:
        k = min(k, 3)
    chosen = set(rng.sample(optional, k))
    sents = [t for i, (t, r) in enumerate(seq) if r or i in chosen]
    draft = " ".join(sents)
    cons = {"exact_sentences": len(sents)} if by_sentences else {"exact_words": len(draft.split())}
    case = _case(seed, "W5", "exact_count", today, profile, sender, sc["purpose"], None,
                 points=points, constraints=cons, reference_draft=draft)
    return W.finish(case)


# ---------------------------------------------------------------------------
# W7 helpers
# ---------------------------------------------------------------------------

def _neutral_ctx(seed: int, n_people: int = 4):
    rng = random.Random(seed)
    w = World.make(seed, year=2027)
    sender = {"name": w.contacts[0]["name"], "preferred_name": w.contacts[0]["first"]}
    return rng, w, w.today, sender


def _words(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# W7 consistency
# ---------------------------------------------------------------------------

CONS_PARAS = [
    "{Proj} is close to done, and this update sets out where things stand. For the past month the work has "
    "centred on {action}, and most of it is now finished. {A} has coordinated the effort from {S1}, and {B} has "
    "looked after the work at {S2}. {C} has handled the paperwork and the contact with suppliers.",
    "At {S1}, the team has dealt with {F1} {thing} so far. {A} reports that the pace held steady after the first "
    "week and credits early planning for much of that. The share of work finished at {S1} has reached {F3} "
    "percent, which is ahead of what we expected a month ago.",
    "{S2} has been harder. {B} found that {obstacle}, and that cost several days. Even so, {B} has kept the "
    "schedule within reach, and the team at {S2} has logged {F4} hours of work since the start of the month.",
    "On cost, the approved budget is {F2} euros, and spending to date sits comfortably below it. {C} reviews the "
    "invoices every week and reports any change to {A}. No item has gone over its limit, and {C} does not "
    "expect any surprises before the end.",
    "The main risk is {risk}. {A} has asked {B} to prepare a fallback at {S2}, and {C} will check the figures "
    "against the budget of {F2} euros before anything is committed. If the fallback is needed, it will use the "
    "same team and the same {thing}.",
    "Looking ahead, the next steps are {nexts}. {A} will lead that work at {S1}, {B} will repeat it at {S2}, and "
    "{C} will confirm the final {F1} {thing} against the records.",
    "Taken together, the {F3} percent complete at {S1}, the {F4} hours logged and the {F1} {thing} handled show "
    "steady progress. {benefit}.",
    "The next update will come once {A}, {B} and {C} have signed off the results at both {S1} and {S2}. Until "
    "then, the figures above, including the budget of {F2} euros, remain the working numbers.",
]
CONS_SC = [
    dict(Proj="The depot relocation", proj="the depot relocation", action="moving stock into the new racking",
         thing="pallets", obstacle="the racking supplier had delivered the wrong brackets",
         risk="a late delivery of the replacement brackets", nexts="a full stock count and the handover of the old building",
         benefit="Faster picking is the payoff the whole team is working toward",
         places=["Eastfield Yard", "Harbour Wharf", "Copperhill Store", "Ashby Sheds"]),
    dict(Proj="The website relaunch", proj="the website relaunch", action="rebuilding the product pages",
         thing="pages", obstacle="the image archive had been saved in an old format",
         risk="a delay in the payment provider's sign-off", nexts="a final content review and the switch-over",
         benefit="A faster, clearer site is the payoff the whole team is working toward",
         places=["Linden Studio", "Northwick Office", "Cedar Hub", "Marlow House"]),
    dict(Proj="The library move", proj="the library move", action="packing and shelving the collection",
         thing="crates", obstacle="the lift in the old building had broken down twice",
         risk="bad weather on the day of the main move", nexts="a final shelving check and the reopening plan",
         benefit="A bigger reading room is the payoff the whole team is working toward",
         places=["Wrenfield Rooms", "Quayside Annexe", "Larch Court", "Bramwell Hall"]),
    dict(Proj="The garden build", proj="the garden build", action="building and filling the raised beds",
         thing="planters", obstacle="the first load of soil had arrived too wet to use",
         risk="a shortage of volunteers over the holiday weeks", nexts="the watering rota and the opening day",
         benefit="Fresh vegetables for the neighbourhood are the payoff the whole team is working toward",
         places=["Fenwick Allotments", "Rowan Terrace", "Millpond Green", "Tansy Court"]),
    dict(Proj="The records scan", proj="the records scan", action="scanning and indexing the paper records",
         thing="files", obstacle="the scanner had jammed on the oldest folders",
         risk="a gap in the backup of the scanned files", nexts="a spot check of the index and the shredding schedule",
         benefit="Quicker access to records is the payoff the whole team is working toward",
         places=["Juniper Clinic", "Oakmere Surgery", "Alder House", "Stanton Centre"]),
    dict(Proj="The cafe refit", proj="the cafe refit", action="installing the new counter and seating",
         thing="fittings", obstacle="the counter had arrived with a cracked panel",
         risk="a late visit from the fire inspector", nexts="the final inspection and the reopening date",
         benefit="A calmer, brighter room is the payoff the whole team is working toward",
         places=["Sorrel Parlour", "Bluebell Lane", "Tamarind Corner", "Pewter Street"]),
]


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def gen_consistency(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = CONS_SC[index % len(CONS_SC)]
    rng, w, today, sender = _neutral_ctx(seed)
    people = [c["name"] for c in w.contacts[1:4]]
    places = rng.sample(sc["places"], 2)
    f1 = rng.randrange(1200, 9800)
    f4 = rng.randrange(1200, 9800)
    while abs(f4 - f1) < 500:
        f4 = rng.randrange(1200, 9800)
    f3 = f"{rng.randrange(52, 94)}.{rng.choice([2, 4, 6, 8])}"
    amt = w.amount(40000, 190000)
    whole, cents = amt.split(".")
    f2 = f"{int(whole):,}.{cents}"
    F1, F4 = _fmt_int(f1), _fmt_int(f4)
    slots = {**{k: v for k, v in sc.items() if k != "places"}, "A": people[0], "B": people[1], "C": people[2],
             "S1": places[0], "S2": places[1], "F1": F1, "F2": f2, "F3": f3, "F4": F4}
    draft = "\n\n".join(p.format(**slots) for p in CONS_PARAS)
    entities = people + places + [F1, f2, f3, F4]
    thing = sc["thing"]
    points = [
        W.point("p1", f"{people[0]} coordinated the effort from {places[0]}", [people[0], places[0]]),
        W.point("p2", f"{people[1]} looked after the work at {places[1]}", [people[1], places[1]]),
        W.point("p3", f"{people[2]} handled the paperwork and the invoices", [people[2]]),
        W.point("p4", f"{F1} {thing} handled in total", [W.number_anchor(str(f1))]),
        W.point("p5", f"the approved budget is {f2} euros", [W.amount_anchor(amt)]),
        W.point("p6", f"{f3} percent of the work finished", [W.number_anchor(f3)]),
        W.point("p7", f"{F4} hours of work logged", [W.number_anchor(str(f4))]),
    ]
    case = _case(seed, "W7", "consistency", today, None, sender, f"a project update on {sc['proj']}",
                 points=points, entities=entities, constraints={"min_words": 300, "max_words": 520},
                 reference_draft=draft)
    lines = [f"Write a project update on {sc['proj']} for me ({sender['name']}), in plain paragraphs. "
             f"Aim for about 400 words.",
             f"It covers {sc['action']}. Use these names, places and figures exactly as written here, and spell "
             f"each one the same way every time you use it:"]
    lines += [f"- {p['text']}" for p in points]
    lines += [f"- the work took place at {places[0]} and {places[1]}"]
    lines += W.render_constraints(case["constraints"], "document")
    case["turns"] = ["\n".join(lines)]
    return W.finish(case)


# ---------------------------------------------------------------------------
# W7 outline
# ---------------------------------------------------------------------------

RATIONALE = [
    "This keeps the arrangement predictable for everyone involved.",
    "It also saves time for the people who have to check the details later.",
    "The aim is to make the routine simple enough to remember without a reminder.",
    "Most people will find that it changes very little about how they already work.",
    "Where something is unclear, the sensible step is to ask early rather than guess.",
    "Having one clear rule here avoids the confusion that built up in the past.",
    "It is a small change, but it removes a lot of back and forth.",
    "People who follow it should notice fewer delays over the coming months.",
    "The rule applies equally to everyone, whatever their role or seniority.",
    "It is worth building into your routine from the first day.",
    "Exceptions are possible, but they need to be agreed in advance.",
    "The reasoning is simple: fewer surprises mean less work for everybody.",
    "A short habit now will save a longer conversation later.",
    "None of this is meant to add paperwork for its own sake.",
    "If the arrangement turns out to be a poor fit, it will be reviewed.",
    "Taken together, these points are meant to be easy to follow.",
    "Questions are welcome at any point, and nobody will mind being asked twice.",
    "It is easier to get this right at the start than to fix it afterwards.",
    "People who have already tried it say that it soon becomes second nature.",
    "The details may be refined later, but the main idea will stay the same.",
]
_ORD = ["First,", "Second,", "Third,", "Fourth,", "Fifth,", "Sixth,"]

# point: (kind, head, brief text, outline tag, paragraph). kind: date | amount | number | term:<text>
OUT_SC = [
    dict(topic="the new expense policy", purpose="explain the new expense policy to the team",
         intro="This note explains the new expense policy and what it means for your claims. The rules below replace every earlier version.",
         closing="Thank you for reading this through. The rules are short on purpose, and following them will keep every claim moving quickly.",
         points=[
             ("date", "Start date", "the policy starts on {v}", "starts {v}",
              "The new policy starts on {v}. Any claim for spending after that day follows these rules, while older spending is handled under the previous process."),
             ("amount", "Approval limit", "claims above {v} euros need a manager's approval", "above {v} euros",
              "Any single claim above {v} euros needs a manager's approval before it is submitted. Smaller claims go straight to the finance team, so most everyday purchases are unaffected."),
             ("number", "Receipt deadline", "receipts must be uploaded within {v} days", "within {v} days",
              "Receipts must be uploaded within {v} days of the purchase. A photo taken on a phone is enough, as long as the total and the date can be read."),
             ("term:Skyline portal", "Booking portal", "travel is booked through the Skyline portal", "Skyline portal",
              "All travel is booked through the Skyline portal, which applies the agreed fares automatically. Bookings made anywhere else will not be reimbursed unless finance agrees in advance."),
             ("term:Ledger Desk", "Questions", "questions go to the Ledger Desk", "Ledger Desk",
              "Questions about any of this go to the Ledger Desk, which answers within one working day. The team there also keeps a list of examples that people have asked about."),
         ]),
    dict(topic="the office move", purpose="prepare the team for the office move",
         intro="This note covers the office move and what each person needs to do before it happens. Please read it before you start packing.",
         closing="That is the full plan for now. Further details will follow as the building work is signed off.",
         points=[
             ("date", "Move day", "the move happens on {v}", "on {v}",
              "The move happens on {v}. The old office will be closed to everyone from the evening before, so please finish any urgent work by then."),
             ("number", "Packing boxes", "each person gets {v} boxes", "{v} boxes",
              "Each person will receive {v} boxes, with labels and tape included. Please pack your own desk and mark every box with your name and floor."),
             ("term:Birchgate House", "New address", "the new office is Birchgate House", "Birchgate House",
              "The new office is Birchgate House, a short walk from the station. Reception staff there will hand out access cards on the first morning."),
             ("amount", "Taxi allowance", "a taxi allowance of {v} euros covers move day", "{v} euros",
              "Staff who cannot use public transport on move day can claim a taxi allowance of {v} euros. The receipt should show the date and the route."),
             ("term:Mina Okafor", "Contact", "questions go to the move coordinator Mina Okafor", "Mina Okafor",
              "Questions about the move go to Mina Okafor, who is coordinating the whole project. She will hold a short drop-in session in the week before."),
         ]),
    dict(topic="the volunteer rota", purpose="explain how the new volunteer rota works",
         intro="This note sets out how the volunteer rota will work from now on. It is meant to share the load more fairly across the group.",
         closing="Thank you for helping out. A rota only works when people take it seriously, and this one has been kept deliberately simple.",
         points=[
             ("date", "First shift", "the first shifts begin on {v}", "begin {v}",
              "The first shifts begin on {v}. Each volunteer will find their name on the sheet by the front door, with the slots marked in colour."),
             ("number", "Shift length", "each shift lasts {v} minutes", "{v} minutes",
              "Each shift lasts {v} minutes, which is long enough to get a task done and short enough to fit around a busy day."),
             ("term:Harvest Table", "Main duty", "the main duty is the Harvest Table", "Harvest Table",
              "The main duty is running the Harvest Table, where donated food is sorted and shared out. Gloves and aprons are kept in the cupboard behind it."),
             ("amount", "Travel refunds", "volunteers can claim up to {v} euros in travel costs", "up to {v} euros",
              "Volunteers can claim up to {v} euros in travel costs each month. Claims need a ticket or a receipt and go to the treasurer."),
             ("term:Rowan Hall", "Meeting place", "the monthly meeting is at Rowan Hall", "Rowan Hall",
              "The monthly meeting takes place at Rowan Hall. It is the place to swap shifts, raise problems and welcome new volunteers."),
         ]),
    dict(topic="the safety audit", purpose="tell staff about the coming safety audit",
         intro="This note explains what to expect from the safety audit and what we need from you. The audit is routine, and nobody should be worried by it.",
         closing="Thanks for your help with this. A calm, well prepared audit makes it easier for everyone to see what is working.",
         points=[
             ("date", "Audit day", "the auditors visit on {v}", "visit {v}",
              "The auditors visit on {v}. They will walk through each floor, and a manager from every team will go with them."),
             ("term:Halvorsen Group", "Auditor", "the auditors come from the Halvorsen Group", "Halvorsen Group",
              "The auditors work for the Halvorsen Group, an independent firm. They will carry identity cards and will ask to see the records for their checklist."),
             ("number", "Fire exits", "every one of the {v} fire exits must be clear", "{v} exits",
              "All {v} fire exits across the site must be clear and unlocked during working hours. Please check the ones near your desk each morning."),
             ("amount", "Equipment budget", "a budget of {v} euros covers any repairs", "{v} euros",
              "A budget of {v} euros has been set aside for any repairs that the audit identifies. Teams should report faults early so that the money is used well."),
             ("term:Logbook Seven", "Records", "incidents are recorded in Logbook Seven", "Logbook Seven",
              "Every incident, however small, is recorded in Logbook Seven at reception. The auditors will read it, so entries should be dated and legible."),
         ]),
    dict(topic="the new starter week", purpose="describe the first week for new starters",
         intro="This note describes the first week for new starters, so that nobody arrives not knowing what to expect. It is written for the new team and their managers.",
         closing="That is the shape of the week. We hope it makes the start feel welcoming and gives everyone a clear first goal.",
         points=[
             ("date", "Start date", "the next group starts on {v}", "starts {v}",
              "The next group starts on {v}. Everyone is asked to arrive at the main entrance, where a host will meet them."),
             ("term:Compass Course", "Training", "all starters take the Compass Course", "Compass Course",
              "Every starter takes the Compass Course in the first two days. It covers the tools, the main processes and the people to ask for help."),
             ("number", "Buddy meetings", "each starter has {v} minutes with a buddy each day", "{v} minutes",
              "Each starter spends {v} minutes with a buddy every day. The buddy answers questions and points out the unwritten habits of the team."),
             ("amount", "Equipment", "a laptop and kit worth {v} euros are provided", "{v} euros",
              "Each starter receives a laptop and kit worth {v} euros. It is set up in advance, so the first morning is spent on people rather than passwords."),
             ("term:Lena Marsh", "People contact", "the people contact is Lena Marsh", "Lena Marsh",
              "The first person to contact is Lena Marsh, who runs the starter programme. She will check in at the end of the week."),
         ]),
    dict(topic="the recycling scheme", purpose="introduce the new recycling scheme to residents",
         intro="This note introduces the new recycling scheme for the building and explains what changes for residents. The scheme is simple, and it replaces the old bins.",
         closing="Thank you for taking part. Small, steady habits make this kind of scheme work, and we will report the results openly.",
         points=[
             ("date", "Launch", "the scheme launches on {v}", "launches {v}",
              "The scheme launches on {v}. The old bins will be taken away that morning, and the new ones will be in place by the afternoon."),
             ("number", "Bin count", "there are {v} new bins around the building", "{v} bins",
              "There will be {v} new bins around the building, each with a clear colour code. Glass, paper and food waste all have their own place."),
             ("term:Greenlink", "Collector", "the collector is Greenlink", "Greenlink",
              "The collection is run by Greenlink, which comes twice a week. Their crew will leave a tag on any bin that has been filled wrongly."),
             ("amount", "Savings", "the building saves {v} euros a year in waste fees", "{v} euros",
              "The scheme is expected to save {v} euros a year in waste fees. That money will go back into the shared garden and the entrance lights."),
             ("term:Elm Court", "Collection point", "bulky items go to the store at Elm Court", "Elm Court",
              "Bulky items such as boxes and old furniture go to the store at Elm Court. It is open on Saturday mornings for anyone who needs it."),
         ]),
]


def _point_value(kind: str, rng: random.Random, w: World, cy: int):
    """(display value, anchor, anchor text)"""
    if kind == "date":
        d = w.date_after(3, 33)
        return N.format_p7(d, cy), W.date_anchor(d)
    if kind == "amount":
        a = w.amount(60, 900)
        return a, W.amount_anchor(a)
    if kind == "number":
        n = rng.randrange(12, 90)
        return str(n), W.number_anchor(str(n))
    term = kind.split(":", 1)[1]
    return term, term


def gen_outline(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = OUT_SC[index % len(OUT_SC)]
    rng, w, today, sender = _neutral_ctx(seed)
    cy = today.year
    pts = []
    for i, (kind, head, req, tag, para) in enumerate(sc["points"]):
        v, anchor = _point_value(kind, rng, w, cy)
        pts.append({"head": head, "req": req.format(v=v), "tag": tag.format(v=v), "para": para.format(v=v),
                    "anchor": anchor, "id": f"p{i + 1}"})
    order = list(range(len(pts)))
    while True:
        rng.shuffle(order)
        if order != sorted(order):
            break
    ordered = [pts[i] for i in order]
    outline = "\n".join(f"- {p['head']}: {p['tag']}" for p in ordered)
    rationale = rng.sample(RATIONALE, len(RATIONALE))
    paras = []
    r = 0
    for i, p in enumerate(ordered):
        paras.append(f"{_ORD[i]} {p['para'][0].lower() + p['para'][1:]} {rationale[r]} {rationale[r + 1]} {rationale[r + 2]}")
        r += 3
    extra_r = 15
    piece = sc["intro"] + "\n\n" + "\n\n".join(paras) + "\n\n" + sc["closing"]
    i = 0
    while _words(piece) < 415 and extra_r < len(rationale):
        paras[i % len(paras)] += " " + rationale[extra_r]
        extra_r += 1
        i += 1
        piece = sc["intro"] + "\n\n" + "\n\n".join(paras) + "\n\n" + sc["closing"]
    points = [W.point(p["id"], p["req"], [p["anchor"]]) for p in pts]
    case = _case(seed, "W7", "outline", today, None, sender, sc["purpose"],
                 points=points, constraints={"min_words": 400, "max_words": 600}, reference_draft=piece,
                 reference_turns=[outline, piece])
    t1 = [f"I need a note of about 500 words on {sc['topic']}, written for me ({sender['name']}). "
          f"Before I see it, give me an outline. It needs to cover:"]
    t1 += [f"- {p['text']}" for p in points]
    t1 += ["Give only the outline, as a short bulleted list with one line per point, in the order you think works best."]
    t2 = ["Thanks. Now write the note itself, following your outline point by point in the same order. "
          "Write plain paragraphs with no headings or bullets."]
    t2 += W.render_constraints(case["constraints"], "document")
    case["turns"] = ["\n".join(t1), "\n".join(t2)]
    return W.finish(case)


# ---------------------------------------------------------------------------
# W7 title
# ---------------------------------------------------------------------------

TITLE_PARAS = [
    "Residents of {area} will soon see a change. The {Name} launches {when}, and it brings {what} to {place}. "
    "The idea has been in planning for more than a year, and its backers say the wait was worth it because the "
    "plan is now far better prepared than the first version was.",
    "The {Name} grew out of a survey in which {n} residents described the same problem: {problem}. A working group "
    "led by {person} used those answers to shape a plan that is simple to explain and inexpensive to run. The "
    "group met every fortnight through the winter and tested each idea against what people had actually said.",
    "Here is how it works. {how}. {freebie}, and after that the cost is low enough that the organisers expect "
    "most people to keep using the {Name}. Payment can be made on the day, and no account is needed.",
    "Not everyone is convinced. Some local traders worry about {worry}, and {person2}, who runs a shop close to "
    "{place}, said she would wait and see before deciding what she thinks. The organisers have promised a review "
    "after three months and say the results will be published in full.",
    "{person} said the aim is modest. \"{quote},\" {person} said. The team does not claim that every problem will "
    "be solved at once, but it hopes to learn quickly from what happens on the ground and to change course if "
    "something is not working.",
    "Next comes {next}. Residents who want to take part are asked to share the details with their neighbours and "
    "to send feedback about {place} and the streets around it. The working group will read every message and "
    "report back in its first monthly note.",
]
TITLE_SC = [
    dict(names=["Lantern Scheme", "Beacon Scheme"], area="the harbour district",
         what="a free evening shuttle bus", when="in March", problem="getting home after dark was hard without a car",
         how="Two small buses will run every twenty minutes between the pier and the town centre",
         freebie="The first {n2} rides for each person are free",
         worry="noise and parking near the pier", quote="We would rather start small and get it right",
         next="a sign-up desk at the pier on two Saturdays", places=["Tidewater Pier", "Quayside Steps"],
         title_tail="brings evening shuttle to {place}", subj="Evening shuttle to {place} starts in March"),
    dict(names=["Marigold Plan", "Foxglove Plan"], area="the Larkfield estate",
         what="a community compost and swap point", when="in April",
         problem="there was nowhere to take garden waste or spare tools",
         how="Residents drop off garden waste on Saturday mornings and can take away compost or borrow tools from a shared shed",
         freebie="The first {n2} visits for each household are free",
         worry="smells and extra traffic", quote="We want the place to feel like it belongs to the street",
         next="a tidy-up day at the site", places=["Larkfield Green", "Bramble Gate"],
         title_tail="brings compost point to {place}", subj="New compost and swap point at {place}"),
    dict(names=["Kestrel Project", "Swift Project"], area="the old rail district",
         what="a free bike repair workshop", when="in May",
         problem="cheap bike repairs were hard to find and many bikes sat unused",
         how="A volunteer mechanic and a set of shared tools will be in a converted goods shed every Thursday evening",
         freebie="The first {n2} repairs for each person are free",
         worry="the cost of keeping the workshop warm", quote="A bike that works is worth a lot to a family",
         next="a training evening for volunteer mechanics", places=["Ferngate Shed", "Goods Yard"],
         title_tail="opens bike repair at {place}", subj="Free bike repair opens at {place} in May"),
    dict(names=["Harbour Light Trial", "Quayside Light Trial"], area="the market quarter",
         what="brighter low-energy street lighting", when="in October",
         problem="several streets were dim and people felt unsafe walking home",
         how="New lamps are being fitted on every second post, and a light sensor adjusts the brightness through the night",
         freebie="For the first {n2} weeks the trial costs residents nothing",
         worry="glare in bedroom windows", quote="People should feel at ease on their own street",
         next="a walk-round with residents after dark", places=["Cobbler Square", "Saltmarsh Row"],
         title_tail="lights up {place}", subj="Brighter street lights for {place} from October"),
    dict(names=["Reading Hour", "Bedtime Hour"], area="the east side",
         what="a weekly children's story hour", when="in September",
         problem="there was no regular activity for young children near their homes",
         how="Volunteers read aloud for forty minutes each Tuesday in the library annexe, with books to borrow at the end",
         freebie="The first {n2} sessions are free for every family",
         worry="the noise for other library users", quote="A story shared early can change how a child sees books",
         next="a volunteer training session in the annexe", places=["Riverbank Annexe", "Weir Lane Library"],
         title_tail="begins at {place}", subj="Weekly children's stories start at {place}"),
    dict(names=["Cinder Market", "Saffron Market"], area="the north end of town",
         what="a fortnightly street market", when="in June",
         problem="the shops had closed and there was nowhere local to buy fresh food",
         how="Around twenty stalls will set up on the closed street every second Saturday, with a hot food corner at one end",
         freebie="Stalls are free for the first {n2} days",
         worry="litter and early deliveries", quote="A market is just neighbours who bring things to share",
         next="a stallholder meeting at the community room", places=["Cinder Lane", "Almond Row"],
         title_tail="returns to {place}", subj="Fortnightly stalls open on {place}"),
]


def gen_title(seed: int, *, index: int, profile: str | None = None) -> dict:
    sc = TITLE_SC[index % len(TITLE_SC)]
    rng, w, today, sender = _neutral_ctx(seed)
    name = rng.choice(sc["names"])
    place = rng.choice(sc["places"])
    people = rng.sample([c["name"] for c in w.contacts[1:]], 2)
    n = rng.randrange(210, 880)
    n2 = rng.choice([10, 12, 15, 20, 25])
    slots = {**{k: v for k, v in sc.items() if k not in ("names", "places", "title_tail", "subj")},
             "Name": name, "place": place, "n": n, "person": people[0], "person2": people[1], "n2": n2}
    slots["freebie"] = sc["freebie"].format(n2=n2)
    source = "\n\n".join(p.format(**slots) for p in TITLE_PARAS)
    title = f"{name} {sc['title_tail'].format(place=place)}"
    subject = sc["subj"].format(place=place)
    draft = f"Title: {title}\nSubject: {subject}"
    points = [W.point("p1", f"the title names the {name}", [name]), W.point("p2", f"the subject line names {place}", [place])]
    case = _case(seed, "W7", "title", today, None, sender, "a title and an email subject line for the piece",
                 points=points, constraints={"title_max_words": 8}, context={"source_text": source},
                 reference_draft=draft)
    lines = [f"I ({sender['name']}) wrote the piece below and I am about to email it to our mailing list. "
             f"It needs a title and an email subject line. Between them they must mention:"]
    lines += [f"- {p['text'].replace('the title names the ', '').replace('the subject line names ', '')}" for p in points]
    lines += ["", "The piece:", '"""', source, '"""']
    lines += W.render_constraints({**case["constraints"], "output_only": False}, "document")
    lines += ['Then an email subject line of at most 10 words, written as "Subject: ...".', "Reply with the text only."]
    case["turns"] = ["\n".join(lines)]
    return W.finish(case)


GENERATORS: dict[tuple[str, str], Callable[..., dict]] = {
    ("W5", "standing_pref"): gen_standing_pref,
    ("W5", "one_off"): gen_one_off,
    ("W5", "voice"): gen_voice,
    ("W5", "conventions"): gen_conventions,
    ("W5", "exact_count"): gen_exact_count,
    ("W7", "consistency"): gen_consistency,
    ("W7", "outline"): gen_outline,
    ("W7", "title"): gen_title,
}
