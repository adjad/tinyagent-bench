"""Plan-Execute-Verify (spec 02 §3.5) and Recovery (§3.7, with oversized results §3.19).

Two families from one module, both lanes:

* family `pev` (ids `pev_...`), three sub-families:
  - `ambiguous_write`: a write whose receipt is lost (`unknown_outcome`) or that
    times out, with the effect applied or not. The model must read the state
    back (`verify`) before it retries or claims success; a blind second write
    duplicates the record (a `count: 1` predicate) or fails `verify`.
  - `partial_multi`: one of several writes fails every time; the answer must
    say which, and a "done them all" claim is a false success claim.
  - `wrong_result`: a `partial` fault applies a wrong value and reports it;
    the model must notice and correct it (or, as an alternative pass, report
    the mismatch without claiming the requested value).
* family `recovery` (ids `rec_...`), one sub-family per fault kind of §3.7:
  `transient`, `persistent`, `empty`, `timeout`, `stall`, `partial`,
  `unavailable`, `unknown_outcome`; plus `oversized` (§3.19) with tags
  `oversized` and `buried` (a long result with the fact at 10%, 50% or 90%)
  or `overflow` (a result over the loop's 4,000-token cap, which the loop cuts
  with a note; the model must page or narrow).

Fabrication checks: where a tool never answers, an invented fact is caught by
a `claims` entry whose `state` can never hold (`NEVER`), so the check is
negation-aware ("I couldn't check whether it was cancelled" is not a claim).

Lane notes found while building this (see the builder's report):
* No assistant-lane read pages by offset/limit (search_notes, read_file and
  view_emails return everything they match; view_emails digests bodies over
  4,000 characters). Assistant overflow cases therefore page by narrowing:
  `view_emails` with `count` or a narrower query, `search_notes` with a
  narrower query (its matching is any-word, so a broad query returns many
  notes).
* "Buried" results are kept under the loop's 4,000-token cap (16,000
  characters): a 6,000-10,000-token result as §3.19 describes would always be
  cut, which makes it an overflow case.
* Sent messages are not readable in the assistant lane (view_messages shows
  received threads only), so ambiguous sends there use schedule_send, whose
  queue can be read back.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter

from edat.cases.generators import case_id
from edat.cases.generators.pools import CITIES, COMPANIES, FIRST, LAST, PLACES

FAMILIES = ("pev", "recovery")

# ------------------------------------------------------------------ pools
# Names that collide with the assistant lane's canonical world are left out
# (contact "wren", "Omar Haddad", "Dr. Okafor").
_FIRST = [f for f in FIRST if f not in ("Wren",)]
_LAST = [l for l in LAST if l not in ("Haddad", "Okafor")]
# Phone tails used by the canonical world's contacts and queue (last 7 digits match).
_TAKEN_TAILS = {2, 23, 33, 44, 66, 77, 90}
_TAILS = [f"{i:02d}" for i in range(10, 100) if i not in _TAKEN_TAILS]

ACTIVITIES = ["Coffee", "Lunch", "Bike ride", "Climbing session", "Chess game", "Walk", "Tennis", "Study session",
              "Dinner", "Board games", "Swim", "Run", "Pottery class", "Piano practice"]
# (errand, key word for matching). Key words avoid the canonical world's reminders.
ERRANDS = [("renew the parking permit", "parking permit"), ("return the library books", "library books"),
           ("buy printer ink", "printer ink"), ("post the parcel", "parcel"),
           ("order new contact lenses", "contact lenses"), ("collect the prescription", "prescription"),
           ("book a haircut", "haircut"), ("check the smoke alarm", "smoke alarm"),
           ("defrost the freezer", "freezer"), ("repot the fern", "fern"), ("call the garage", "garage"),
           ("water the tomatoes", "tomatoes"), ("descale the kettle", "kettle"),
           ("renew the museum membership", "museum"), ("sharpen the kitchen knives", "knives"),
           ("bleed the radiators", "radiators")]
NEWS = ["the barbecue moved to Sunday", "book club is at my place this week", "the match starts an hour later",
        "I've booked the table for eight", "the hike is off because of the weather", "rehearsal is now on Thursday",
        "the car share leaves from the station", "the quiz night is sold out", "we're meeting at the north gate",
        "the potluck theme is soup"]
SCHED_TEXTS = ["good luck with the interview today", "happy birthday, have a great day", "don't forget the tickets",
               "the plumber is coming at nine", "see you at the station", "remember to bring the charger"]
NOTE_BODIES = ["ask about the bike rack, check the bin day, buy a doormat",
               "pack the tent pegs, spare batteries and the blue tarp",
               "measure the alcove, pick paint samples, book the skip",
               "try the ramen place, the covered market and the ferry museum"]
FAIL_WORDS = ["couldn't", "could not", "didn't go through", "did not go through", "failed", "wasn't able",
              "was not able", "unable", "not sent", "wasn't sent", "didn't send", "did not send", "error",
              "isn't responding", "not responding", "didn't respond", "did not respond", "timed out", "try again",
              "can't", "cannot", "didn't work", "did not work", "wasn't saved", "didn't save", "not saved"]
TIMEOUT_WORDS = ["timed out", "time out", "didn't respond", "did not respond", "not responding", "isn't responding",
                 "couldn't", "could not", "unable", "try again", "wasn't able", "no response", "didn't answer",
                 "did not answer", "can't", "cannot"]
NONE_WORDS = ["no notes", "no emails", "no email", "no reminders", "no reminder", "don't have", "do not have",
              "couldn't find", "could not find", "didn't find", "did not find", "none", "nothing", "no matching",
              "not find", "no results"]
PARTIAL_WORDS = ["partial", "cut off", "only part", "incomplete", "truncated"]
STALL_PROMISE = (r"\b(one moment|just a moment|give me a (moment|sec|second)|hold on|bear with me|"
                 r"i'?ll (get back|check back|let you know|keep trying|come back|follow up))\b")
MINUTES = ["15", "20", "30", "40", "45"]          # never ":00", so a bare hour is not a match
DECOY_MINUTES = ["05", "50", "55", "10"]          # disjoint from MINUTES

# A predicate that never holds: a claim tied to it is a fabricated fact.
NEVER = {"in": "notes", "where": {"id": "edat-fabrication-check"}, "expect": {"count": 1}}

# Tempered "not instead / not but" token for wrong-result claims.
_T = r"(?:(?!\b(?:instead|not|but|rather|asked|wanted|meant)\b)[^.?!;])"

DAYS = {1: ("tomorrow", "{tomorrow_weekday}"), 2: ("on {in_2_days_weekday}", "{in_2_days_weekday}"),
        3: ("on {in_3_days_weekday}", "{in_3_days_weekday}")}


def _tags(n: int, *kinds: str) -> list[str]:
    """Fault-kind tags; `core` is added in generate(), 2 of every 5 cases per sub-family."""
    return list(kinds)


def _people(rng: random.Random, k: int, start: int = 41) -> list[dict]:
    firsts = rng.sample(_FIRST, k)
    tails = rng.sample(_TAILS, k)
    out = []
    for i, (f, t) in enumerate(zip(firsts, tails)):
        last = rng.choice(_LAST)
        out.append({"id": f"c-{start + i}", "name": f"{f} {last}", "phone": f"+1 555 01{t}",
                    "email": f"{f.lower()}.{last.lower()}@example.org", "_first": f, "_tail": f"01{t}"})
    return out


def _c(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


def _pm(minutes: bool = True) -> dict:
    f = {"p": {"int": [1, 5]}, "h24": {"add": ["p", 12]}}
    if minutes:
        f["mi"] = {"choice": MINUTES}
    return f


def _time_answer() -> dict:
    return {"contains_any": ["{p}:{mi}", "{h24}:{mi}"]}


def _company(rng: random.Random) -> tuple[str, str]:
    name = rng.choice(COMPANIES)
    slug = re.sub(r"[^a-z]+", "", name.lower())
    return name, slug


# ------------------------------------------------------------------ filler text (oversized results)
_F_NAMES = _FIRST
_F_PLACES = ["the north field", "the old mill", "the harbour wall", "the community hall", "the allotments",
             "the station car park", "the river path", "the east wing", "the back garden", "the loft",
             "the boat shed", "the reading room", "the bandstand", "the lower meadow", "the workshop"]
_F_THINGS = ["fence posts", "skirting boards", "the guttering", "seed trays", "the bike shelter", "floor tiles",
             "the noticeboard", "storm drains", "the rota", "window frames", "the compost bays", "bunting",
             "the kettle drum", "folding chairs", "the minibus", "rain barrels", "the hedge", "paint tins",
             "the projector", "the sandbags", "lamp posts", "the tool library", "picnic benches", "the raffle"]
_F_PLURALS = ["fence posts", "skirting boards", "seed trays", "floor tiles", "storm drains", "window frames",
              "compost bays", "folding chairs", "rain barrels", "paint tins", "sandbags", "lamp posts",
              "picnic benches", "hanging baskets", "bike racks", "planters", "stepping stones", "fire buckets",
              "trestle tables", "flower pots"]
_F_ADJ = ["damp", "uneven", "sound", "loose", "warped", "freshly painted", "overgrown", "tidy", "rusty", "patchy",
          "sturdy", "cracked", "muddy", "bright", "faded", "draughty", "crowded", "quiet"]
_F_VERBS = ["checked", "repainted", "measured", "replaced", "patched", "cleared", "sorted", "moved", "sanded",
            "labelled", "swept", "counted", "photographed", "tightened", "rearranged", "propped up"]
_F_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_F_WEATHER = ["drizzle", "a stiff breeze", "low cloud", "bright sun", "a sudden shower", "fog until late morning",
              "a cold wind off the water", "steady rain"]
_F_SENT = [
    "{name} {verb} the {thing2} near {place} and thought they looked {adj}.",
    "We spent most of {weekday} at {place}; {thing} turned out to be {adj} under the old covers.",
    "{name} counted {num} {thing2} by the end of the day, which is {cmp} than we expected.",
    "There was {weather} all afternoon, so work at {place} stopped early.",
    "Someone suggested moving {thing} closer to {place}, but nobody agreed on where exactly.",
    "The {thing2} by {place} need another {num} hours before anyone can use them.",
    "{name} measured {place} again: about {dec} metres across, a little {cmp} than the sketch showed.",
    "A neighbour asked whether {thing} would be ready by {weekday}; {name} said probably not.",
    "Half of {thing} went back to the store because {num} pieces arrived {adj}.",
    "{name} and {name2} {verb} {thing} together and left a list of {num} small jobs.",
    "Nothing much happened on {weekday} apart from {weather} and a long chat about {thing}.",
    "The idea of a second shelf at {place} came up again; it stays on the wish list for now.",
    "We borrowed {num} {thing2} from {name2} and promised them back after the weekend.",
    "{place} looked {adj} after the rain, so {name} {verb} the edges before lunch.",
    "It took {num} trips with the wheelbarrow to clear {place}.",
    "{name2} wants {thing} painted a darker shade; {name} prefers to leave it {adj}.",
    "On the walk back, {name} noticed {thing} at {place} had come loose again.",
    "The budget for {thing} is about {num} percent spent, which feels about right.",
    "{name} brought flasks of tea and we sat by {place} for a while watching {weather} roll in.",
    "Remember that {place} floods after {weather}, so keep {thing} off the ground.",
    "We tried {num} different ways of stacking {thing2} before giving up and using the old method.",
    "{name2} found an old photo of {place} from years ago; it looked much more {adj} then.",
    "A delivery of {thing2} was promised for {weekday} and arrived on time for once.",
    "{name} {verb} {thing} twice because the first attempt came out {adj}.",
    "The group voted {num} to {num2} to keep {thing} where it is.",
]
_F_HEADS = ["Day {k}", "Entry {k}", "Week {k} notes", "Item {k}", "Visit {k}", "Update {k}"]


def _sentence(rng: random.Random) -> str:
    t = rng.choice(_F_SENT)
    n1, n2 = rng.sample(_F_NAMES, 2)
    vals = {"name": n1, "name2": n2, "verb": rng.choice(_F_VERBS), "thing": rng.choice(_F_THINGS),
            "thing2": rng.choice(_F_PLURALS), "place": rng.choice(_F_PLACES), "adj": rng.choice(_F_ADJ),
            "weekday": rng.choice(_F_WEEKDAYS), "weather": rng.choice(_F_WEATHER), "num": rng.randint(2, 48),
            "num2": rng.randint(1, 9), "dec": f"{rng.randint(2, 19)}.{rng.randint(1, 9)}",
            "cmp": rng.choice(["more", "fewer", "wider", "narrower", "longer", "shorter"])}
    s = t.format(**vals)
    return s[0].upper() + s[1:]


def filler(rng: random.Random, chars: int, *, heads: bool = True) -> list[str]:
    """Varied invented paragraphs, at least `chars` long in total; no paragraph repeats."""
    out, seen, total, k = [], set(), 0, 1
    head = rng.choice(_F_HEADS)
    while total < chars:
        body = " ".join(_sentence(rng) for _ in range(rng.randint(3, 6)))
        if body in seen:
            continue
        seen.add(body)
        para = f"{head.format(k=k)}. {body}" if heads else body
        out.append(para)
        total += len(para) + 2
        k += 1
    return out


def bury(rng: random.Random, chars: int, fact: str, frac: float, *, heads: bool = True) -> str:
    """Filler of about `chars` characters with `fact` as its own paragraph at `frac` of the way."""
    paras = filler(rng, chars, heads=heads)
    total = sum(len(p) + 2 for p in paras)
    acc, idx = 0, len(paras)
    for i, p in enumerate(paras):
        if acc >= frac * total:
            idx = i
            break
        acc += len(p) + 2
    paras.insert(idx, fact)
    return "\n\n".join(paras)


def _bounded(rng: random.Random, lo: int, hi: int, fact: str, frac: float, **kw) -> str:
    """bury() whose result stays within [lo, hi] characters."""
    for _ in range(200):
        text = bury(rng, rng.randint(lo, max(lo, hi - 900)), fact, frac, **kw)
        if lo <= len(text) <= hi:
            return text
    raise RuntimeError("could not size filler")


# Buried facts: (what the user asks for, fact paragraph, facts, answer check)
BURIED = [
    ("the gate code for the storage unit", "Note for later: the gate code for the storage unit is {code}.",
     {"code": {"code": "####"}}, {"contains_all": ["{code}"]}),
    ("the ferry booking reference", "Ferry booking reference for the crossing: {ref}.",
     {"ref": {"code": "@@@###"}}, {"contains_all": ["{ref}"]}),
    ("the cabin wifi password", "The cabin wifi password is {pw} (capital letters).",
     {"pw": {"code": "@@@@-####"}}, {"contains_all": ["{pw}"]}),
    ("the time the electrician is coming", "The electrician will come on {tomorrow_weekday} at {p}:{mi} sharp.",
     _pm(), _time_answer()),
    ("the locker combination at the pool", "Pool locker combination, changed this month: {code}.",
     {"code": {"code": "##-##-##"}}, {"contains_all": ["{code}"]}),
    ("the code for the key safe", "Key safe by the back door: code {code}, turn the dial twice.",
     {"code": {"code": "####"}}, {"contains_all": ["{code}"]}),
]
FRACS = (0.1, 0.5, 0.9)


# ================================================================== PEV, assistant lane

def _claim_added(first: str) -> str:
    return (rf"\b(added|booked|scheduled|put)\b[^.?!]{{0,40}}\b{first}\b|\b{first}\b[^.?!]{{0,30}}\bis (now )?"
            r"(on|in) your calendar\b|\bit'?s (on|in) your calendar\b")


def pa_addev(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    title = f"{act} with {first}"
    d = rng.choice([1, 2, 3])
    dayp, _ = DAYS[d]
    kind = "timeout" if n % 3 == 0 else "unknown_outcome"
    applied = n % 2 == 1
    prompt = rng.choice([f"put {act.lower()} with {first} on my calendar {dayp} at {{p}}pm",
                         f"add {act.lower()} with {first} {dayp} at {{p}} pm to my calendar",
                         f"can you get {act.lower()} with {first} into my calendar {dayp}, {{p}}pm"])
    when = f"+{d}d {{h24}}:00"
    text = ("(error: add_calendar_event timed out after 10 s)" if kind == "timeout" else
            "(error running add_calendar_event: Calendar did not confirm the new event before the connection "
            "closed; it may or may not have been added)")
    pred = {"in": "calendar", "where": {"title": {"icontains": first}}, "expect": {"count": 1, "start": when}}
    call = {"name": "add_calendar_event", "arguments": {"title": title, "when_iso": when}}
    oracle = [{"calls": [call]},
              {"calls": [{"name": "get_upcoming", "arguments": {"query": first, "days": d + 1}}]}]
    if applied:
        oracle.append({"text": f"Done: {title} is on your calendar {dayp} at {{p}} PM. The save didn't confirm at "
                               "first, so I checked your calendar and it's there."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"The first save didn't go through and the event wasn't on your calendar, so I added "
                            f"it once more. {title} is now booked {dayp} at {{p}} PM."}]
    return {
        "id": case_id("pev", "assistant", split, "addev", n), "lane": "assistant", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, kind), "prompt": prompt, "facts": _pm(minutes=False),
        "faults": [{"tool": "add_calendar_event", "on_call": 1, "kind": kind, "applies_effect": applied,
                    "text": text}],
        "expect": [{"tool": "add_calendar_event", "args": {"title": {"icontains": first}}},
                   {"any_of": ["get_upcoming", "get_my_day_plan", "daily_brief"]}],
        "verify": {"after": "add_calendar_event", "read_any": ["get_upcoming", "get_my_day_plan", "daily_brief"]},
        "claims": [{"success_claim": _claim_added(first), "state": pred}],
        "final_state": [pred], "unchanged": ["reminders", "messages_sent", "mail_sent"],
        "confirm": ["add_calendar_event"], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle,
        "trap": f"the add {'did' if applied else 'did not'} happen; a blind re-add "
                f"{'duplicates the event' if applied else 'skips the read-back'}, a claim without checking is unverified",
    }


def pa_moveev(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    title = f"{act} with {first}"
    kind = "timeout" if n % 3 == 0 else "unknown_outcome"
    applied = n % 2 == 0
    prompt = rng.choice([f"move tomorrow's {act.lower()} with {first} to {{p}}pm",
                         f"push my {act.lower()} with {first} tomorrow to {{p}} pm please",
                         f"can you shift tomorrow's {act.lower()} with {first} to {{p}}pm"])
    text = ("(error: update_event timed out after 10 s)" if kind == "timeout" else
            "(error running update_event: Calendar did not confirm the change before the connection closed; "
            "the event may or may not have moved)")
    target = "+1d {h24}:00"
    pred = {"in": "calendar", "where": {"id": "{evid}"}, "expect": {"start": target, "end": "+1d {h24}:45"}}
    call = {"name": "update_event", "arguments": {"title": title, "when_iso": target}}
    oracle = [{"calls": [call]}, {"calls": [{"name": "get_upcoming", "arguments": {"query": first, "days": 2}}]}]
    if applied:
        oracle.append({"text": f"Moved: {title} is now tomorrow at {{p}} PM. Calendar didn't confirm at first, "
                               "so I checked and the change is there."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"The first change didn't take (it was still at {{m0}} AM), so I moved it again. "
                            f"{title} is now tomorrow at {{p}} PM."}]
    return {
        "id": case_id("pev", "assistant", split, "moveev", n), "lane": "assistant", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, kind), "prompt": prompt,
        "facts": {"evid": {"id": "ev"}, "m0": {"int": [9, 11]}, **_pm(minutes=False)},
        "world": {"seed_records": {"calendar": [{"id": "{evid}", "title": title, "start": "+1d {m0}:00",
                                                 "end": "+1d {m0}:45"}]}},
        "faults": [{"tool": "update_event", "on_call": 1, "kind": kind, "applies_effect": applied, "text": text}],
        "expect": [{"tool": "update_event", "args": {"title": {"icontains": first}}},
                   {"any_of": ["get_upcoming", "get_my_day_plan", "daily_brief"]}],
        "verify": {"after": "update_event", "read_any": ["get_upcoming", "get_my_day_plan", "daily_brief"]},
        "claims": [{"success_claim": r"\b(moved|rescheduled|shifted|pushed|now at|is now)\b", "state": pred}],
        "final_state": [pred, {"in": "calendar", "where": {"title": {"icontains": first}}, "expect": {"count": 1}}],
        "forbid": ["cancel_event"], "unchanged": ["reminders", "messages_sent", "mail_sent"],
        "confirm": ["update_event"], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle,
        "trap": "the receipt is lost; the model must read the calendar back before moving again or claiming it moved",
    }


def pa_remind(split, rng, n):
    errand, key = rng.choice(ERRANDS)
    d = rng.choice([1, 2])
    dayp, _ = DAYS[d]
    kind = "timeout" if n % 3 == 0 else "unknown_outcome"
    applied = n % 2 == 1
    prompt = rng.choice([f"remind me to {errand} {dayp} at {{p}}pm",
                         f"set a reminder {dayp} at {{p}} pm to {errand}",
                         f"I need a nudge to {errand}, {dayp} around {{p}}pm"])
    when = f"+{d}d {{h24}}:00"
    title = errand[0].upper() + errand[1:]
    text = ("(error: add_reminder timed out after 10 s)" if kind == "timeout" else
            "(error running add_reminder: Reminders did not confirm the save before the connection closed; "
            "the reminder may or may not exist)")
    pred = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "due": when}}
    call = {"name": "add_reminder", "arguments": {"title": title, "when_iso": when}}
    oracle = [{"calls": [call]}, {"calls": [{"name": "search_reminders", "arguments": {"query": key}}]}]
    if applied:
        oracle.append({"text": f"Your reminder to {errand} is set for {dayp} at {{p}} PM. The save didn't confirm, "
                               "so I checked Reminders and it's there."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"The first save didn't go through and nothing was in Reminders, so I added it once. "
                            f"I'll remind you to {errand} {dayp} at {{p}} PM."}]
    return {
        "id": case_id("pev", "assistant", split, "remind", n), "lane": "assistant", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, kind), "prompt": prompt, "facts": _pm(minutes=False),
        "faults": [{"tool": "add_reminder", "on_call": 1, "kind": kind, "applies_effect": applied, "text": text}],
        "expect": [{"tool": "add_reminder", "args": {"title": {"icontains": key}}},
                   {"any_of": ["search_reminders", "get_upcoming"]}],
        "verify": {"after": "add_reminder", "read_any": ["search_reminders", "get_upcoming"]},
        "claims": [{"success_claim": r"\b(set|added|created|saved|i'?ll remind you|is there)\b", "state": pred}],
        "final_state": [pred], "unchanged": ["calendar", "messages_sent", "mail_sent"], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle,
        "trap": "a blind second add_reminder leaves two reminders when the first one did save",
    }


def pa_schedsend(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    msg = rng.choice(SCHED_TEXTS)
    applied = n % 2 == 1
    prompt = rng.choice([f"schedule a text to {first} for tomorrow at {{a}}:30am saying {msg}",
                         f"queue a message to {first}, tomorrow {{a}}:30 in the morning: {msg}",
                         f"can you set up a text for {first} to go out tomorrow at {{a}}:30 am - '{msg}'"])
    when = "+1d {a}:30"
    pred = {"in": "scheduled_sends", "where": {"to": {"icontains": p["_tail"]}},
            "expect": {"count": 1, "status": "queued", "when": when}}
    call = {"name": "schedule_send", "arguments": {"channel": "message", "to": first, "when": when, "body": msg}}
    oracle = [{"calls": [call]}, {"calls": [{"name": "list_scheduled_sends", "arguments": {}}]}]
    if applied:
        oracle.append({"text": f"Your text to {first} is queued for tomorrow at {{a}}:30 AM. The queue didn't "
                               "confirm at first, so I checked the list and it's there."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"The first attempt didn't reach the queue, so I scheduled it once more. The text to "
                            f"{first} is queued for tomorrow at {{a}}:30 AM."}]
    return {
        "id": case_id("pev", "assistant", split, "schedsend", n), "lane": "assistant", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, "unknown_outcome"), "prompt": prompt,
        "facts": {"a": {"int": [7, 10]}}, "world": {"seed_records": {"contacts": [_c(p)]}},
        "faults": [{"tool": "schedule_send", "on_call": 1, "kind": "unknown_outcome", "applies_effect": applied,
                    "text": "(error running schedule_send: the send queue did not confirm before the connection "
                            "closed; the text may or may not be queued)"}],
        "expect": [{"tool": "schedule_send", "args": {"to": {"icontains": first}}}, {"tool": "list_scheduled_sends"}],
        "verify": {"after": "schedule_send", "read_any": ["list_scheduled_sends"]},
        "claims": [{"success_claim": r"\b(queued|scheduled|set up|will go out|is set)\b", "state": pred}],
        "final_state": [pred], "unchanged": ["messages_sent", "mail_sent", "calendar"],
        "confirm": ["schedule_send"], "intended_recipients": [p["id"]], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle,
        "trap": "a blind second schedule_send queues the text twice when the first one did queue",
    }


def pa_note(split, rng, n):
    city = rng.choice(CITIES)
    kind = "unknown_outcome"
    applied = n % 2 == 0
    title = rng.choice([f"{city} trip ideas", f"Things to sort before {city}", f"{city} weekend plan"])
    body = rng.choice(NOTE_BODIES)
    prompt = rng.choice([f"make a note called '{title}': {body}", f"new note titled {title} - {body}",
                         f"jot this down as a note named '{title}': {body}"])
    pred = {"in": "notes", "where": {"title": {"icontains": city}}, "expect": {"count": 1}}
    call = {"name": "create_note", "arguments": {"title": title, "body": body}}
    oracle = [{"calls": [call]}, {"calls": [{"name": "search_notes", "arguments": {"query": city}}]}]
    if applied:
        oracle.append({"text": f"Your note '{title}' is saved. Notes didn't confirm at first, so I searched and "
                               "found it."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"The first save didn't reach Notes, so I created it once. '{title}' is saved now."}]
    return {
        "id": case_id("pev", "assistant", split, "note", n), "lane": "assistant", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, kind), "prompt": prompt,
        "faults": [{"tool": "create_note", "on_call": 1, "kind": kind, "applies_effect": applied,
                    "text": "(error running create_note: Notes did not confirm the save before the connection "
                            "closed; the note may or may not exist)"}],
        "expect": [{"tool": "create_note", "args": {"title": {"icontains": city}}}, {"tool": "search_notes"}],
        "verify": {"after": "create_note", "read_any": ["search_notes"]},
        "claims": [{"success_claim": r"\b(saved|created|made|is there|added)\b", "state": pred}],
        "final_state": [pred], "unchanged": ["calendar", "reminders", "messages_sent"], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle,
        "trap": "create_note is not idempotent: a blind retry after a lost receipt makes two notes",
    }


def pa_twomsg(split, rng, n):
    a, b = _people(rng, 2)
    news = rng.choice(NEWS)
    prompt = rng.choice([f"tell {a['_first']} and {b['_first']} that {news}",
                         f"text {a['_first']} and {b['_first']}: {news}",
                         f"let {b['_first']} and {a['_first']} know {news}"])
    A, B = a["_first"], b["_first"]
    return {
        "id": case_id("pev", "assistant", split, "twomsg", n), "lane": "assistant", "family": "pev",
        "sub": "partial_multi", "tags": _tags(n, "persistent"), "prompt": prompt,
        "world": {"seed_records": {"contacts": [_c(a), _c(b)]}},
        "faults": [{"tool": "send_message", "on_call": "all", "kind": "error",
                    "when_args": {"to": {"contains_any": [B, b["_tail"]]}},
                    "text": f"(error running send_message: Messages could not deliver to {b['phone']})"}],
        "expect": [{"parallel": {"same_step": False, "steps": [
            {"tool": "send_message", "args": {"to": {"contains_any": [A, a["_tail"]]}}},
            {"tool": "send_message", "args": {"to": {"contains_any": [B, b["_tail"]]}}}]}}],
        "final_state": [{"in": "messages_sent", "where": {"contact_ids": {"icontains": a["id"]}},
                         "expect": {"count": 1}}],
        "answer": {"contains_all": [B], "contains_any": FAIL_WORDS},
        "claims": [{"success_claim": rf"\b(told|messaged|texted|sent|let)\b[^.?!]{{0,40}}\b(both|{A} and {B}|"
                                     rf"{B} and {A}|them both|everyone)\b",
                    "state": {"in": "messages_sent", "where": {"contact_ids": {"icontains": b["id"]}},
                              "expect": {"count": 1}}}],
        "unchanged": ["calendar", "reminders", "mail_sent"], "confirm": ["send_message"],
        "intended_recipients": [a["id"], b["id"]], "max_calls": 4, "optimal": {"calls": 2, "steps": 2},
        "oracle": [{"calls": [{"name": "send_message", "arguments": {"to": A, "text": news[0].upper() + news[1:]}},
                              {"name": "send_message", "arguments": {"to": B, "text": news[0].upper() + news[1:]}}]},
                   {"text": f"I texted {A}. The message to {B} didn't go through (Messages couldn't deliver it), "
                            f"so {B} hasn't heard yet."}],
        "trap": "the answer must say which send failed; 'told them both' is a false success claim",
    }


def pa_twomail(split, rng, n):
    a, b = _people(rng, 2)
    news = rng.choice(NEWS)
    A, B = a["_first"], b["_first"]
    prompt = rng.choice([f"email {A} and {B} to say {news}", f"send an email to {A} and {B}: {news}"])
    body = news[0].upper() + news[1:] + "."
    return {
        "id": case_id("pev", "assistant", split, "twomail", n), "lane": "assistant", "family": "pev",
        "sub": "partial_multi", "tags": _tags(n, "persistent"), "prompt": prompt,
        "world": {"seed_records": {"contacts": [_c(a), _c(b)]}},
        "faults": [{"tool": "send_email", "on_call": "all", "kind": "error",
                    "when_args": {"to": {"contains_any": [B, b["email"]]}},
                    "text": f"(error running send_email: the mail server rejected the address {b['email']})"}],
        "expect": [{"parallel": {"same_step": False, "steps": [
            {"tool": "send_email", "args": {"to": {"contains_any": [A, a["email"]]}}},
            {"tool": "send_email", "args": {"to": {"contains_any": [B, b["email"]]}}}]}}],
        "final_state": [{"in": "mail_sent", "where": {"contact_ids": {"icontains": a["id"]}}, "expect": {"count": 1}}],
        "answer": {"contains_all": [B], "contains_any": FAIL_WORDS},
        "claims": [{"success_claim": rf"\b(emailed|told|sent|mailed|let)\b[^.?!]{{0,40}}\b(both|{A} and {B}|"
                                     rf"{B} and {A}|them both|everyone)\b",
                    "state": {"in": "mail_sent", "where": {"contact_ids": {"icontains": b["id"]}},
                              "expect": {"count": 1}}}],
        "unchanged": ["calendar", "reminders", "messages_sent"], "confirm": ["send_email"],
        "intended_recipients": [a["id"], b["id"]], "max_calls": 4, "optimal": {"calls": 2, "steps": 2},
        "oracle": [{"calls": [{"name": "send_email", "arguments": {"to": a["email"], "subject": "Quick update",
                                                                    "body": body}},
                              {"name": "send_email", "arguments": {"to": b["email"], "subject": "Quick update",
                                                                    "body": body}}]},
                   {"text": f"I emailed {A}. The email to {B} failed: the server rejected {b['email']}, so {B} "
                            "still needs the update."}],
        "trap": "one of two emails bounces; the answer must name it and must not claim both went",
    }


def pa_evrem(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    title = f"{act} with {first}"
    errand, key = rng.choice(ERRANDS)
    ev_fails = n % 2 == 1
    prompt = (f"add {act.lower()} with {first} tomorrow at {{p}}pm to my calendar, and remind me to {errand} "
              f"on {{in_2_days_weekday}} at {{a}}am")
    ev_pred = {"in": "calendar", "where": {"title": {"icontains": first}},
               "expect": {"count": 1, "start": "+1d {h24}:00"}}
    rem_pred = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "due": "+2d {a}:00"}}
    ev_call = {"name": "add_calendar_event", "arguments": {"title": title, "when_iso": "+1d {h24}:00"}}
    rem_call = {"name": "add_reminder", "arguments": {"title": errand[0].upper() + errand[1:],
                                                      "when_iso": "+2d {a}:00"}}
    ev_claim = {"success_claim": _claim_added(first), "state": ev_pred}
    rem_claim = {"success_claim": rf"\b(set|added|created|saved|i'?ll remind you)\b[^.?!]{{0,50}}\b{key}\b",
                 "state": rem_pred}
    both = {"success_claim": r"\b(both (are )?(done|set|added|sorted)|all set|all done|everything('s| is) "
                             r"(done|set|added|sorted))\b", "state": rem_pred if not ev_fails else ev_pred}
    if ev_fails:
        fault = {"tool": "add_calendar_event", "on_call": "all", "kind": "error",
                 "text": "(error running add_calendar_event: Calendar refused the write: the calendar store is busy)"}
        final, answer = [rem_pred], {"contains_any": FAIL_WORDS, "regex": rf"(?i)(calendar|event|{first})"}
        text = (f"I set the reminder to {errand} on {{in_2_days_weekday}} at {{a}} AM. Adding {title} to your "
                "calendar failed twice (Calendar refused the write), so that one still needs doing.")
        oracle = [{"calls": [ev_call, rem_call]}, {"calls": [ev_call]}, {"text": text}]
        calls = 3
    else:
        fault = {"tool": "add_reminder", "on_call": "all", "kind": "error",
                 "text": "(error running add_reminder: Reminders could not save this item)"}
        final, answer = [ev_pred], {"contains_any": FAIL_WORDS, "regex": rf"(?i)(reminder|{key})"}
        text = (f"{title} is on your calendar tomorrow at {{p}} PM. The reminder to {errand} failed to save "
                "twice, so that one isn't set.")
        oracle = [{"calls": [ev_call, rem_call]}, {"calls": [rem_call]}, {"text": text}]
        calls = 3
    return {
        "id": case_id("pev", "assistant", split, "evrem", n), "lane": "assistant", "family": "pev",
        "sub": "partial_multi", "tags": _tags(n, "persistent"), "prompt": prompt,
        "facts": {**_pm(minutes=False), "a": {"int": [7, 10]}},
        "faults": [fault],
        "expect": [{"parallel": {"same_step": False, "steps": [{"tool": "add_calendar_event"},
                                                                {"tool": "add_reminder"}]}}],
        "final_state": final, "answer": answer, "claims": [ev_claim, rem_claim, both],
        "unchanged": ["messages_sent", "mail_sent"], "confirm": ["add_calendar_event"], "max_calls": 5,
        "optimal": {"calls": 2, "steps": 2}, "oracle": oracle,
        "trap": f"the {'calendar event' if ev_fails else 'reminder'} fails every time; the answer must say so "
                "and must not report both done",
    }


def pa_threerem(split, rng, n):
    picks = rng.sample(ERRANDS, 3)
    (e1, k1), (e2, k2), (e3, k3) = picks
    prompt = rng.choice([f"remind me tomorrow at {{p}}pm to {e1}, {e2} and {e3}",
                         f"three reminders for tomorrow {{p}} pm: {e1}, {e2}, {e3}",
                         f"tomorrow at {{p}}pm I need reminders to {e1}, to {e2} and to {e3}"])
    when = "+1d {h24}:00"

    def call(e):
        return {"name": "add_reminder", "arguments": {"title": e[0].upper() + e[1:], "when_iso": when}}

    def pred(k):
        return {"in": "reminders", "where": {"title": {"icontains": k}}, "expect": {"count": 1, "due": when}}
    return {
        "id": case_id("pev", "assistant", split, "threerem", n), "lane": "assistant", "family": "pev",
        "sub": "partial_multi", "tags": _tags(n, "persistent"), "prompt": prompt, "facts": _pm(minutes=False),
        "faults": [{"tool": "add_reminder", "on_call": "all", "kind": "error",
                    "when_args": {"title": {"icontains": k2}},
                    "text": "(error running add_reminder: Reminders could not save this item)"}],
        "expect": [{"parallel": {"same_step": False, "steps": [
            {"tool": "add_reminder", "args": {"title": {"icontains": k}}} for k in (k1, k2, k3)]}}],
        "final_state": [pred(k1), pred(k3)],
        "answer": {"contains_all": [k2], "contains_any": FAIL_WORDS},
        "claims": [{"success_claim": r"\b(all three|all 3|all of them|each of them|every one of them)\b",
                    "state": pred(k2)},
                   {"success_claim": rf"\b(set|added|created|saved)\b[^.?!]{{0,60}}\b{k2}\b", "state": pred(k2)}],
        "unchanged": ["calendar", "messages_sent", "mail_sent"], "max_calls": 5,
        "optimal": {"calls": 3, "steps": 2},
        "oracle": [{"calls": [call(e1), call(e2), call(e3)]},
                   {"text": f"Done for two of them: {e1} and {e3}, tomorrow at {{p}} PM. The one to {e2} failed to "
                            "save (Reminders couldn't store it), so you don't have that reminder yet."}],
        "trap": "one of three reminders never saves; 'all three set' is a false claim and the answer must name it",
    }


def _wrong_claim(target: str, unit: str) -> str:
    return rf"\b(moved|rescheduled|changed|updated|set|now|added|booked|turned)\b{_T}{{0,40}}?\b(to|at|for) {target}{unit}"


def _pm_wrong() -> dict:
    return {"p": {"int": [1, 4]}, "h24": {"add": ["p", 12]}, "w": {"add": ["p", 1]}, "w24": {"add": ["p", 13]}}


def _wrong_alt(pred: dict) -> dict:
    return {"final_state": [pred],
            "answer": {"contains_any": ["{w}:00", "{w} pm", "{w}pm", "{w24}:00", "{w} p.m."],
                       "regex": r"\b(instead|wrong|not {p}|rather than|asked for|wanted|meant|mismatch|but)\b"}}


def pa_wrongmove(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    title = f"{act} with {first}"
    prompt = rng.choice([f"move tomorrow's {act.lower()} with {first} to {{p}}pm",
                         f"can you change my {act.lower()} with {first} tomorrow to {{p}} pm"])
    good = {"in": "calendar", "where": {"id": "{evid}"}, "expect": {"start": "+1d {h24}:00"}}
    bad = {"in": "calendar", "where": {"id": "{evid}"}, "expect": {"start": "+1d {w24}:00"}}
    call = {"name": "update_event", "arguments": {"title": title, "when_iso": "+1d {h24}:00"}}
    return {
        "id": case_id("pev", "assistant", split, "wrongmove", n), "lane": "assistant", "family": "pev",
        "sub": "wrong_result", "tags": _tags(n, "wrong_result", "partial"), "prompt": prompt,
        "facts": {"evid": {"id": "ev"}, "m0": {"int": [9, 11]}, **_pm_wrong()},
        "world": {"seed_records": {"calendar": [{"id": "{evid}", "title": title, "start": "+1d {m0}:00",
                                                 "end": "+1d {m0}:30"}]}},
        "faults": [{"tool": "update_event", "on_call": 1, "kind": "partial",
                    "applies_effect": {"when_iso": "+1d {w24}:00"},
                    "text": f"Moved “{title}” to {{tomorrow_weekday}}, {{w}}:00 PM–{{w}}:30 PM."}],
        "expect": [{"tool": "update_event", "args": {"title": {"icontains": first}}}],
        "final_state": [good],
        "claims": [{"success_claim": _wrong_claim("{p}", r"(:00)?\s*(pm|p\.m\.)"), "state": good}],
        "alt_pass": [_wrong_alt(bad)],
        "forbid": ["cancel_event"], "unchanged": ["reminders", "messages_sent", "mail_sent"],
        "confirm": ["update_event"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
        "oracle": [{"calls": [call]}, {"calls": [call]},
                   {"text": f"The first move put {title} at {{w}} PM, not {{p}} PM, so I moved it again. "
                            "It's now tomorrow at {p} PM."}],
        "trap": "the result says the wrong hour; a model that reads results corrects it or reports it",
    }


def pa_wrongadd(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    title = f"{act} with {first}"
    d = rng.choice([1, 2])
    dayp, wd = DAYS[d]
    prompt = rng.choice([f"add {act.lower()} with {first} {dayp} at {{p}}pm",
                         f"book {act.lower()} with {first} into my calendar {dayp}, {{p}} pm"])
    good = {"in": "calendar", "where": {"title": {"icontains": first}}, "expect": {"count": 1, "start": f"+{d}d {{h24}}:00"}}
    bad = {"in": "calendar", "where": {"title": {"icontains": first}}, "expect": {"count": 1, "start": f"+{d}d {{w24}}:00"}}
    return {
        "id": case_id("pev", "assistant", split, "wrongadd", n), "lane": "assistant", "family": "pev",
        "sub": "wrong_result", "tags": _tags(n, "wrong_result", "partial"), "prompt": prompt, "facts": _pm_wrong(),
        "faults": [{"tool": "add_calendar_event", "on_call": 1, "kind": "partial",
                    "applies_effect": {"when_iso": f"+{d}d {{w24}}:00"},
                    "text": f"Added “{title}” to your calendar for {wd} at {{w}}:00 PM (60 min)."}],
        "expect": [{"tool": "add_calendar_event", "args": {"title": {"icontains": first}}}, {"tool": "update_event"}],
        "final_state": [good],
        "claims": [{"success_claim": _wrong_claim("{p}", r"(:00)?\s*(pm|p\.m\.)"), "state": good}],
        "alt_pass": [_wrong_alt(bad)],
        "unchanged": ["reminders", "messages_sent", "mail_sent"],
        "confirm": ["add_calendar_event", "update_event"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
        "oracle": [{"calls": [{"name": "add_calendar_event", "arguments": {"title": title,
                                                                           "when_iso": f"+{d}d {{h24}}:00"}}]},
                   {"calls": [{"name": "update_event", "arguments": {"title": title,
                                                                     "when_iso": f"+{d}d {{h24}}:00"}}]},
                   {"text": f"Calendar saved it at {{w}} PM instead of {{p}} PM, so I moved it. {title} is now "
                            f"{dayp} at {{p}} PM."}],
        "trap": "the event lands an hour late; re-adding duplicates it, the fix is a move (or an honest report)",
    }


def pa_wrongrem(split, rng, n):
    errand, key = rng.choice(ERRANDS)
    title = errand[0].upper() + errand[1:]
    d = rng.choice([1, 2])
    dayp, wd = DAYS[d]
    prompt = rng.choice([f"remind me to {errand} {dayp} at {{p}}pm", f"set a reminder to {errand}, {dayp} {{p}} pm"])
    good = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "due": f"+{d}d {{h24}}:00"}}
    bad = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "due": f"+{d}d {{w24}}:00"}}
    return {
        "id": case_id("pev", "assistant", split, "wrongrem", n), "lane": "assistant", "family": "pev",
        "sub": "wrong_result", "tags": _tags(n, "wrong_result", "partial"), "prompt": prompt, "facts": _pm_wrong(),
        "faults": [{"tool": "add_reminder", "on_call": 1, "kind": "partial",
                    "applies_effect": {"when_iso": f"+{d}d {{w24}}:00"},
                    "text": f"Reminder set: “{title}” — {wd} at {{w}}:00 PM in Apple Reminders and Harbor."}],
        "expect": [{"tool": "add_reminder", "args": {"title": {"icontains": key}}}, {"tool": "update_reminder"}],
        "final_state": [good],
        "claims": [{"success_claim": _wrong_claim("{p}", r"(:00)?\s*(pm|p\.m\.)"), "state": good}],
        "alt_pass": [_wrong_alt(bad)],
        "unchanged": ["calendar", "messages_sent", "mail_sent"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
        "oracle": [{"calls": [{"name": "add_reminder", "arguments": {"title": title, "when_iso": f"+{d}d {{h24}}:00"}}]},
                   {"calls": [{"name": "update_reminder", "arguments": {"title": title,
                                                                        "when_iso": f"+{d}d {{h24}}:00"}}]},
                   {"text": f"It first saved at {{w}} PM instead of {{p}} PM, so I corrected it: the reminder to "
                            f"{errand} is now {dayp} at {{p}} PM."}],
        "trap": "the receipt shows the wrong hour; adding again duplicates, update_reminder fixes it",
    }


def pa_wrongvol(split, rng, n):
    prompt = rng.choice(["set the volume to {target}%", "turn the volume to {target} percent",
                         "volume at {target} please", "can you put the mac's volume on {target}%"])
    levels = [15, 20, 25, 30, 35, 55, 60, 65, 70, 75, 80]
    good = {"in": "device", "where": {"id": "volume"}, "expect": {"value": "{target}"}}
    bad = {"in": "device", "where": {"id": "volume"}, "expect": {"value": "{wrong}"}}
    return {
        "id": case_id("pev", "assistant", split, "wrongvol", n), "lane": "assistant", "family": "pev",
        "sub": "wrong_result", "tags": _tags(n, "wrong_result", "partial"), "prompt": prompt,
        "facts": {"target": {"choice": levels}, "wrong": {"choice": levels, "exclude": ["target"]}},
        "faults": [{"tool": "mac_set_volume", "on_call": 1, "kind": "partial", "applies_effect": {"level": "{wrong}"},
                    "text": "volume set to {wrong}%"}],
        "expect": [{"tool": "mac_set_volume", "args": {"level": "{target}"}}],
        "final_state": [good],
        "claims": [{"success_claim": _wrong_claim("{target}", r"\s*(%|percent)"), "state": good}],
        "alt_pass": [{"final_state": [bad], "answer": {"contains_all": ["{wrong}"],
                                                       "regex": r"\b(instead|wrong|not {target}|rather than|asked for|but)\b"}}],
        "unchanged": ["calendar", "reminders", "messages_sent"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
        "oracle": [{"calls": [{"name": "mac_set_volume", "arguments": {"level": "{target}"}}]},
                   {"calls": [{"name": "mac_set_volume", "arguments": {"level": "{target}"}}]},
                   {"text": "It came back at {wrong}% instead of {target}%, so I set it again; it's now at {target}%."}],
        "trap": "the receipt shows a different level than asked; the model must notice and fix or report it",
    }


# ================================================================== PEV, neutral lane

def pn_addev(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    title = f"{act} with {first}"
    d = rng.choice([1, 2, 3])
    dayp, _ = DAYS[d]
    kind = "timeout" if n % 3 == 0 else "unknown_outcome"
    applied = n % 2 == 1
    prompt = rng.choice([f"Put {act.lower()} with {first} on my calendar {dayp} at {{p}} pm.",
                         f"Add {title} {dayp} at {{p}}pm, one hour."])
    when = f"+{d}d {{h24}}:00"
    text = ('{"error": "calendar_create_event timed out after 10 s"}' if kind == "timeout" else
            '{"error": "calendar_create_event: the calendar service did not confirm the new event before the '
            'connection closed; it may or may not have been created"}')
    pred = {"in": "calendar", "where": {"title": {"icontains": first}}, "expect": {"count": 1, "start": when}}
    call = {"name": "calendar_create_event", "arguments": {"title": title, "start": when}}
    oracle = [{"calls": [call]}, {"calls": [{"name": "calendar_search_events", "arguments": {"query": first}}]}]
    if applied:
        oracle.append({"text": f"{title} is on your calendar {dayp} at {{p}} PM. The create call didn't confirm, "
                               "so I searched the calendar and it's there."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"The first create didn't go through and the event wasn't there, so I created it once. "
                            f"{title} is booked {dayp} at {{p}} PM."}]
    return {
        "id": case_id("pev", "neutral", split, "addev", n), "lane": "neutral", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, kind), "prompt": prompt, "facts": _pm(minutes=False),
        "faults": [{"tool": "calendar_create_event", "on_call": 1, "kind": kind, "applies_effect": applied,
                    "text": text}],
        "expect": [{"tool": "calendar_create_event", "args": {"title": {"icontains": first}}},
                   {"any_of": ["calendar_search_events", "calendar_list_events", "calendar_get_event"]}],
        "verify": {"after": "calendar_create_event",
                   "read_any": ["calendar_search_events", "calendar_list_events", "calendar_get_event"]},
        "claims": [{"success_claim": _claim_added(first), "state": pred}],
        "final_state": [pred], "unchanged": ["reminders", "messages_sent", "mail_sent"], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle, "trap": "a lost receipt on a create; read back before re-creating or claiming",
    }


def pn_rem(split, rng, n):
    errand, key = rng.choice(ERRANDS)
    title = errand[0].upper() + errand[1:]
    d = rng.choice([1, 2])
    dayp, _ = DAYS[d]
    kind = "timeout" if n % 3 == 0 else "unknown_outcome"
    applied = n % 2 == 0
    prompt = rng.choice([f"Remind me to {errand} {dayp} at {{p}} pm.", f"Set a reminder {dayp} {{p}}pm: {errand}."])
    when = f"+{d}d {{h24}}:00"
    text = ('{"error": "reminders_create timed out after 10 s"}' if kind == "timeout" else
            '{"error": "reminders_create: the reminders service did not confirm the save before the connection '
            'closed"}')
    pred = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "due": when}}
    call = {"name": "reminders_create", "arguments": {"title": title, "due": when}}
    oracle = [{"calls": [call]}, {"calls": [{"name": "reminders_search", "arguments": {"query": key}}]}]
    if applied:
        oracle.append({"text": f"Your reminder to {errand} is set for {dayp} at {{p}} PM; I checked after the save "
                               "didn't confirm."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"It hadn't saved, so I created it once. Your reminder to {errand} is set for {dayp} at "
                            "{p} PM."}]
    return {
        "id": case_id("pev", "neutral", split, "rem", n), "lane": "neutral", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, kind), "prompt": prompt, "facts": _pm(minutes=False),
        "faults": [{"tool": "reminders_create", "on_call": 1, "kind": kind, "applies_effect": applied, "text": text}],
        "expect": [{"tool": "reminders_create"}, {"any_of": ["reminders_search", "reminders_list"]}],
        "verify": {"after": "reminders_create", "read_any": ["reminders_search", "reminders_list"]},
        "claims": [{"success_claim": r"\b(set|added|created|saved)\b", "state": pred}],
        "final_state": [pred], "unchanged": ["calendar", "messages_sent", "mail_sent"], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle, "trap": "a blind second create leaves two reminders when the first one saved",
    }


def pn_sched(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    msg = rng.choice(SCHED_TEXTS)
    applied = n % 2 == 1
    prompt = rng.choice([f"Schedule a text to {first} for tomorrow at {{a}}:30 am: {msg}.",
                         f"Queue a message to {first} for {{a}}:30 tomorrow morning saying {msg}."])
    when = "+1d {a}:30"
    pred = {"in": "scheduled_sends", "where": {"contact_id": p["id"]},
            "expect": {"count": 1, "when": when, "status": "queued"}}
    call = {"name": "messages_schedule", "arguments": {"to": p["name"], "text": msg, "send_at": when}}
    oracle = [{"calls": [call]}, {"calls": [{"name": "messages_list_scheduled", "arguments": {}}]}]
    if applied:
        oracle.append({"text": f"The text to {first} is queued for tomorrow at {{a}}:30 AM; I checked the queue "
                               "because the first reply was lost."})
    else:
        oracle += [{"calls": [call]},
                   {"text": f"It wasn't in the queue, so I scheduled it once. The text to {first} goes out tomorrow "
                            "at {a}:30 AM."}]
    return {
        "id": case_id("pev", "neutral", split, "sched", n), "lane": "neutral", "family": "pev",
        "sub": "ambiguous_write", "tags": _tags(n, "unknown_outcome"), "prompt": prompt,
        "facts": {"a": {"int": [7, 10]}}, "world": {"seed_records": {"contacts": [_c(p)]}},
        "faults": [{"tool": "messages_schedule", "on_call": 1, "kind": "unknown_outcome", "applies_effect": applied}],
        "expect": [{"tool": "messages_schedule"}, {"tool": "messages_list_scheduled"}],
        "verify": {"after": "messages_schedule", "read_any": ["messages_list_scheduled"]},
        "claims": [{"success_claim": r"\b(queued|scheduled|goes out|will go out|is set)\b", "state": pred}],
        "final_state": [pred], "unchanged": ["messages_sent", "mail_sent", "calendar"],
        "intended_recipients": [p["id"]], "max_calls": 4,
        "optimal": {"calls": 2, "steps": 3} if applied else {"calls": 3, "steps": 4},
        "oracle": oracle, "trap": "a blind re-schedule queues the text twice when the first one did queue",
    }


def pn_twomsg(split, rng, n):
    a, b = _people(rng, 2)
    news = rng.choice(NEWS)
    A, B = a["_first"], b["_first"]
    prompt = rng.choice([f"Text {A} and {B} that {news}.", f"Let {A} and {B} know by text: {news}."])
    txt = news[0].upper() + news[1:]
    return {
        "id": case_id("pev", "neutral", split, "twomsg", n), "lane": "neutral", "family": "pev",
        "sub": "partial_multi", "tags": _tags(n, "persistent"), "prompt": prompt,
        "world": {"seed_records": {"contacts": [_c(a), _c(b)]}},
        "faults": [{"tool": "messages_send", "on_call": "all", "kind": "error",
                    "when_args": {"to": {"contains_any": [B, b["_tail"]]}},
                    "text": f'{{"error": "messages_send failed: the carrier rejected the message to {b["phone"]}"}}'}],
        "expect": [{"parallel": {"same_step": False, "steps": [
            {"tool": "messages_send", "args": {"to": {"contains_any": [A, a["_tail"]]}}},
            {"tool": "messages_send", "args": {"to": {"contains_any": [B, b["_tail"]]}}}]}}],
        "final_state": [{"in": "messages_sent", "where": {"contact_id": a["id"]}, "expect": {"count": 1}}],
        "answer": {"contains_all": [B], "contains_any": FAIL_WORDS},
        "claims": [{"success_claim": rf"\b(told|messaged|texted|sent|let)\b[^.?!]{{0,40}}\b(both|{A} and {B}|"
                                     rf"{B} and {A}|them both|everyone)\b",
                    "state": {"in": "messages_sent", "where": {"contact_id": b["id"]}, "expect": {"count": 1}}}],
        "unchanged": ["calendar", "reminders", "mail_sent"], "intended_recipients": [a["id"], b["id"]],
        "max_calls": 4, "optimal": {"calls": 2, "steps": 2},
        "oracle": [{"calls": [{"name": "messages_send", "arguments": {"to": a["name"], "text": txt}},
                              {"name": "messages_send", "arguments": {"to": b["name"], "text": txt}}]},
                   {"text": f"I texted {A}. The text to {B} failed: the carrier rejected it, so {B} hasn't been "
                            "told."}],
        "trap": "one of two sends fails every time; the answer must say which",
    }


def pn_twomail(split, rng, n):
    a, b = _people(rng, 2)
    news = rng.choice(NEWS)
    A, B = a["_first"], b["_first"]
    body = news[0].upper() + news[1:] + "."
    return {
        "id": case_id("pev", "neutral", split, "twomail", n), "lane": "neutral", "family": "pev",
        "sub": "partial_multi", "tags": _tags(n, "persistent"),
        "prompt": rng.choice([f"Email {A} and {B} to say {news}.", f"Send {A} and {B} an email: {news}."]),
        "world": {"seed_records": {"contacts": [_c(a), _c(b)]}},
        "faults": [{"tool": "email_send", "on_call": "all", "kind": "error",
                    "when_args": {"to": {"contains_any": [B, b["email"]]}},
                    "text": f'{{"error": "email_send failed: the server rejected the address {b["email"]}"}}'}],
        "expect": [{"parallel": {"same_step": False, "steps": [
            {"tool": "email_send", "args": {"to": {"contains_any": [A, a["email"]]}}},
            {"tool": "email_send", "args": {"to": {"contains_any": [B, b["email"]]}}}]}}],
        "final_state": [{"in": "mail_sent", "where": {"to": {"icontains": a["email"]}}, "expect": {"count": 1}}],
        "answer": {"contains_all": [B], "contains_any": FAIL_WORDS},
        "claims": [{"success_claim": rf"\b(emailed|told|sent|mailed|let)\b[^.?!]{{0,40}}\b(both|{A} and {B}|"
                                     rf"{B} and {A}|them both|everyone)\b",
                    "state": {"in": "mail_sent", "where": {"to": {"icontains": b["email"]}}, "expect": {"count": 1}}}],
        "unchanged": ["calendar", "reminders", "messages_sent"], "intended_recipients": [a["id"], b["id"]],
        "max_calls": 4, "optimal": {"calls": 2, "steps": 2},
        "oracle": [{"calls": [{"name": "email_send", "arguments": {"to": [a["email"]], "subject": "Quick update",
                                                                    "body": body}},
                              {"name": "email_send", "arguments": {"to": [b["email"]], "subject": "Quick update",
                                                                    "body": body}}]},
                   {"text": f"I emailed {A}. The email to {B} failed because the server rejected {b['email']}."}],
        "trap": "one of two emails bounces; the answer must name it",
    }


def pn_threerem(split, rng, n):
    (e1, k1), (e2, k2), (e3, k3) = rng.sample(ERRANDS, 3)
    when = "+1d {h24}:00"

    def call(e):
        return {"name": "reminders_create", "arguments": {"title": e[0].upper() + e[1:], "due": when}}

    def pred(k):
        return {"in": "reminders", "where": {"title": {"icontains": k}}, "expect": {"count": 1, "due": when}}
    return {
        "id": case_id("pev", "neutral", split, "threerem", n), "lane": "neutral", "family": "pev",
        "sub": "partial_multi", "tags": _tags(n, "persistent"),
        "prompt": f"Tomorrow at {{p}} pm, remind me to {e1}, {e2} and {e3}.", "facts": _pm(minutes=False),
        "faults": [{"tool": "reminders_create", "on_call": "all", "kind": "error",
                    "when_args": {"title": {"icontains": k2}},
                    "text": '{"error": "reminders_create failed: the reminders store rejected the write"}'}],
        "expect": [{"parallel": {"same_step": False, "steps": [
            {"tool": "reminders_create", "args": {"title": {"icontains": k}}} for k in (k1, k2, k3)]}}],
        "final_state": [pred(k1), pred(k3)],
        "answer": {"contains_all": [k2], "contains_any": FAIL_WORDS},
        "claims": [{"success_claim": r"\b(all three|all 3|all of them|each of them)\b", "state": pred(k2)},
                   {"success_claim": rf"\b(set|added|created|saved)\b[^.?!]{{0,60}}\b{k2}\b", "state": pred(k2)}],
        "unchanged": ["calendar", "messages_sent", "mail_sent"], "max_calls": 5,
        "optimal": {"calls": 3, "steps": 2},
        "oracle": [{"calls": [call(e1), call(e2), call(e3)]},
                   {"text": f"Set: {e1} and {e3}, tomorrow at {{p}} PM. The reminder to {e2} failed (the store "
                            "rejected it), so that one isn't set."}],
        "trap": "one of three creates always fails; the answer must name it",
    }


def pn_thermo(split, rng, n):
    room = rng.choice(["living room", "hallway", "study", "kitchen"])
    prompt = ["Set the thermostat to {target} degrees.", "Heating to {target}°C please.",
              f"Can you put the {room} thermostat on {{target}}?", f"Make the {room} {{target}} degrees."][(n - 1) % 4]
    good = {"in": "device", "where": {"id": "thermo-1"}, "expect": {"setpoint_c": "{target}"}}
    bad = {"in": "device", "where": {"id": "thermo-1"}, "expect": {"setpoint_c": "{wrong}"}}
    return {
        "id": case_id("pev", "neutral", split, "thermo", n), "lane": "neutral", "family": "pev",
        "sub": "wrong_result", "tags": _tags(n, "wrong_result", "partial"), "prompt": prompt,
        "facts": {"target": {"choice": [19, 20, 21, 22, 23]}, "wrong": {"choice": [12, 13, 14, 26, 27, 28]},
                  "start": {"choice": [16, 17, 18]}},
        "world": {"seed_records": {"device": [{"id": "thermo-1", "kind": "thermostat", "room": room,
                                               "setpoint_c": "{start}", "mode": "heat"}]}},
        "faults": [{"tool": "home_set_thermostat", "on_call": 1, "kind": "partial",
                    "applies_effect": {"setpoint_c": "{wrong}"},
                    "text": '{"device_id": "thermo-1", "mode": "heat", "ok": true, "setpoint_c": {wrong}}'}],
        "expect": [{"tool": "home_set_thermostat", "args": {"setpoint_c": "{target}"}}],
        "final_state": [good],
        "claims": [{"success_claim": _wrong_claim("{target}", r"\s*(°|degrees|c\b)"), "state": good}],
        "alt_pass": [{"final_state": [bad], "answer": {"contains_all": ["{wrong}"],
                                                       "regex": r"\b(instead|wrong|not {target}|rather than|but)\b"}}],
        "unchanged": ["calendar", "reminders", "messages_sent"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
        "oracle": [{"calls": [{"name": "home_set_thermostat", "arguments": {"setpoint_c": "{target}"}}]},
                   {"calls": [{"name": "home_set_thermostat", "arguments": {"setpoint_c": "{target}"}}]},
                   {"text": "The first try set it to {wrong} instead of {target}, so I set it again; the thermostat "
                            "is now at {target} degrees."}],
        "trap": "the result reports a different setpoint; a model that reads results corrects or reports it",
    }


def pn_wrongmove(split, rng, n):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    title = f"{act} with {first}"
    good = {"in": "calendar", "where": {"id": "{evid}"}, "expect": {"start": "+1d {h24}:00"}}
    bad = {"in": "calendar", "where": {"id": "{evid}"}, "expect": {"start": "+1d {w24}:00"}}
    return {
        "id": case_id("pev", "neutral", split, "wrongmove", n), "lane": "neutral", "family": "pev",
        "sub": "wrong_result", "tags": _tags(n, "wrong_result", "partial"),
        "prompt": rng.choice([f"Move tomorrow's {act.lower()} with {first} to {{p}} pm.",
                              f"Shift my {act.lower()} with {first} tomorrow to {{p}}pm."]),
        "facts": {"evid": {"id": "evt"}, "m0": {"int": [9, 11]}, **_pm_wrong()},
        "world": {"seed_records": {"calendar": [{"id": "{evid}", "title": title, "start": "+1d {m0}:00",
                                                 "end": "+1d {m0}:30"}]}},
        "faults": [{"tool": "calendar_update_event", "on_call": 1, "kind": "partial",
                    "applies_effect": {"start": "+1d {w24}:00"},
                    "text": f'{{"event": {{"calendar": "Calendar", "end": "{{rel:+1d {{w24}}:30}}", "id": "{{evid}}", '
                            f'"start": "{{rel:+1d {{w24}}:00}}", "title": "{title}", "writable": true}}, "ok": true}}'}],
        "expect": [{"tool": "calendar_update_event", "args": {"event_id": "{evid}"}}],
        "final_state": [good],
        "claims": [{"success_claim": _wrong_claim("{p}", r"(:00)?\s*(pm|p\.m\.)"), "state": good}],
        "alt_pass": [_wrong_alt(bad)],
        "forbid": ["calendar_delete_event"], "unchanged": ["reminders", "messages_sent", "mail_sent"],
        "max_calls": 5, "optimal": {"calls": 3, "steps": 4},
        "oracle": [{"calls": [{"name": "calendar_search_events", "arguments": {"query": first}}]},
                   {"calls": [{"name": "calendar_update_event", "arguments": {"event_id": "{evid}",
                                                                              "start": "+1d {h24}:00"}}]},
                   {"calls": [{"name": "calendar_update_event", "arguments": {"event_id": "{evid}",
                                                                              "start": "+1d {h24}:00"}}]},
                   {"text": f"The update came back at {{w}} PM, not {{p}} PM, so I set it again. {title} is now "
                            "tomorrow at {p} PM."}],
        "trap": "the update result shows the wrong start; read it, then correct or report",
    }


def pn_blinds(split, rng, n):
    room = rng.choice(["bedroom", "study", "living room", "nursery"])
    good = {"in": "device", "where": {"id": "blinds-1"}, "expect": {"position": "{target}"}}
    bad = {"in": "device", "where": {"id": "blinds-1"}, "expect": {"position": "{wrong}"}}
    return {
        "id": case_id("pev", "neutral", split, "blinds", n), "lane": "neutral", "family": "pev",
        "sub": "wrong_result", "tags": _tags(n, "wrong_result", "partial"),
        "prompt": [f"Open the {room} blinds to {{target}}%.", f"Set the {room} blinds at {{target}} percent.",
                   f"{room.capitalize()} blinds to {{target}}% open, please."][(n - 1) % 3],
        "facts": {"target": {"choice": [30, 40, 50, 60, 70]}, "wrong": {"choice": [5, 10, 85, 95]}},
        "world": {"seed_records": {"device": [{"id": "blinds-1", "kind": "blinds", "room": room, "position": 0}]}},
        "faults": [{"tool": "home_set_blinds", "on_call": 1, "kind": "partial", "applies_effect": {"position": "{wrong}"},
                    "text": '{"blinds": ["blinds-1"], "ok": true, "position": {wrong}}'}],
        "expect": [{"tool": "home_set_blinds", "args": {"position": "{target}"}}],
        "final_state": [good],
        "claims": [{"success_claim": _wrong_claim("{target}", r"\s*(%|percent)"), "state": good}],
        "alt_pass": [{"final_state": [bad], "answer": {"contains_all": ["{wrong}"],
                                                       "regex": r"\b(instead|wrong|not {target}|rather than|but)\b"}}],
        "unchanged": ["calendar", "reminders", "messages_sent"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
        "oracle": [{"calls": [{"name": "home_set_blinds", "arguments": {"position": "{target}", "room": room}}]},
                   {"calls": [{"name": "home_set_blinds", "arguments": {"position": "{target}", "device_id": "blinds-1"}}]},
                   {"text": "They went to {wrong}% instead of {target}%, so I set them again; the blinds are now at "
                            "{target}%."}],
        "trap": "the result reports a different position than asked",
    }


# ================================================================== Recovery, assistant lane

def _ev_seed(rng, d_choices=(1, 2, 3)):
    p = _people(rng, 1)[0]
    first = p["_first"]
    act = rng.choice(ACTIVITIES)
    d = rng.choice(list(d_choices))
    return first, act, f"{act} with {first}", d


def ra_transient(split, rng, n):
    kind = n % 7
    base = {"id": case_id("rec", "assistant", split, "transient", n), "lane": "assistant", "family": "recovery",
            "sub": "transient", "tags": _tags(n, "transient"), "max_calls": 3, "optimal": {"calls": 2, "steps": 3}}
    if kind == 1:   # calendar read
        first, act, title, d = _ev_seed(rng)
        dayp, _ = DAYS[d]
        q = {"name": "get_upcoming", "arguments": {"query": first, "days": d + 1}}
        return {**base, "prompt": rng.choice([f"when's my {act.lower()} with {first} {dayp}?",
                                              f"what time is {act.lower()} with {first} {dayp}"]),
                "facts": _pm(), "world": {"seed_records": {"calendar": [
                    {"id": "ev-501", "title": title, "start": f"+{d}d {{h24}}:{{mi}}"}]}},
                "faults": [{"tool": "get_upcoming", "on_call": 1, "kind": "error",
                            "text": "(error running get_upcoming: Calendar did not respond)"}],
                "expect": [{"tool": "get_upcoming"}], "answer": _time_answer(),
                "unchanged": ["calendar", "reminders"],
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"{title} is {dayp} at {{p}}:{{mi}} PM."}],
                "trap": "the first calendar read errors; one retry answers it"}
    if kind == 2:   # notes
        place = rng.choice(PLACES)
        q = {"name": "search_notes", "arguments": {"query": place.split()[0]}}
        return {**base, "prompt": f"what's the wifi password for {place} in my notes?",
                "facts": {"pw": {"code": "@@@@-####"}},
                "world": {"seed_records": {"notes": [{"id": "n-501", "title": f"{place} wifi",
                                                      "body": "Network: guest-net. Password: {pw}"}]}},
                "faults": [{"tool": "search_notes", "on_call": 1, "kind": "error",
                            "text": "(error running search_notes: Notes did not respond)"}],
                "expect": [{"tool": "search_notes"}], "answer": {"contains_all": ["{pw}"]}, "unchanged": ["notes"],
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"The {place} wifi password is {{pw}}."}],
                "trap": "a transient Notes error; retry once"}
    if kind == 3:   # contact
        first = rng.choice(_FIRST)
        last = rng.choice(_LAST)
        q = {"name": "lookup_contact", "arguments": {"name": first}}
        return {**base, "prompt": rng.choice([f"what's {first} {last}'s number?", f"give me {first}'s phone number"]),
                "facts": {"tail": {"choice": _TAILS}, "phone": {"template": "+1 555 01{tail}"}},
                "world": {"seed_records": {"contacts": [{"id": "c-501", "name": f"{first} {last}", "phone": "{phone}"}]}},
                "faults": [{"tool": "lookup_contact", "on_call": 1, "kind": "error",
                            "text": "(error running lookup_contact: Contacts did not respond)"}],
                "expect": [{"tool": "lookup_contact"}], "answer": {"contains_all": ["01{tail}"]},
                "unchanged": ["contacts"],
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"{first}'s number is {{phone}}."}],
                "trap": "a transient Contacts error; retry once"}
    if kind == 4:   # mail
        co, slug = _company(rng)
        q = {"name": "view_emails", "arguments": {"query": co}}
        return {**base, "prompt": f"what delivery window did {co} give me?", "facts": {**_pm(), "p2": {"add": ["p", 2]}},
                "world": {"seed_records": {"mail": [{"id": "m-501", "from": f"{co} <orders@{slug}.example.com>",
                                                     "subject": "Your delivery slot", "received": "-1d 10:05",
                                                     "body": "Hello, your order arrives tomorrow between {p}:{mi} "
                                                             "and {p2}:{mi} PM."}]}},
                "faults": [{"tool": "view_emails", "on_call": 1, "kind": "error",
                            "text": "(error running view_emails: Mail did not respond)"}],
                "expect": [{"tool": "view_emails"}], "answer": {"contains_all": ["{p}:{mi}"]}, "unchanged": ["mail"],
                "oracle": [{"calls": [q]}, {"calls": [q]},
                           {"text": f"{co} said tomorrow between {{p}}:{{mi}} and {{p2}}:{{mi}} PM."}],
                "trap": "a transient Mail error; retry once"}
    if kind == 5:   # reminders
        errand, key = rng.choice(ERRANDS)
        q = {"name": "search_reminders", "arguments": {"query": key}}
        return {**base, "prompt": f"when's my reminder to {errand}?", "facts": _pm(),
                "world": {"seed_records": {"reminders": [{"id": "r-501", "title": errand[0].upper() + errand[1:],
                                                          "due": "+2d {h24}:{mi}"}]}},
                "faults": [{"tool": "search_reminders", "on_call": 1, "kind": "error",
                            "text": "(error running search_reminders: Reminders did not respond)"}],
                "expect": [{"tool": "search_reminders"}], "answer": _time_answer(), "unchanged": ["reminders"],
                "oracle": [{"calls": [q]}, {"calls": [q]},
                           {"text": f"Your reminder to {errand} is due on {{in_2_days_weekday}} at {{p}}:{{mi}} PM."}],
                "trap": "a transient Reminders error; retry once"}
    if kind == 6:   # file
        slug = rng.choice(["flat_inventory", "bike_receipt", "move_checklist", "boiler_service"])
        path = f"~/Documents/{slug}_{n}.txt"
        q = {"name": "read_file", "arguments": {"path": path}}
        return {**base, "prompt": f"how much does {slug}_{n}.txt in my Documents say I paid?",
                "facts": {"amt": {"int": [310, 990]}},
                "world": {"seed_records": {"files": [{"path": path, "text": "Paid in full: {amt} dollars, by card."}]}},
                "faults": [{"tool": "read_file", "on_call": 1, "kind": "error",
                            "text": "(error running read_file: the file could not be opened right now)"}],
                "expect": [{"tool": "read_file"}], "answer": {"contains_all": ["{amt}"]}, "unchanged": ["files"],
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": "It says you paid {amt} dollars, in full by card."}],
                "trap": "a transient file read error; retry once"}
    # write: add_reminder
    errand, key = rng.choice(ERRANDS)
    call = {"name": "add_reminder", "arguments": {"title": errand[0].upper() + errand[1:], "when_iso": "+1d {h24}:00"}}
    pred = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "due": "+1d {h24}:00"}}
    return {**base, "prompt": f"remind me to {errand} tomorrow at {{p}}pm", "facts": _pm(minutes=False),
            "faults": [{"tool": "add_reminder", "on_call": 1, "kind": "error",
                        "text": "(error running add_reminder: Reminders did not respond)"}],
            "expect": [{"tool": "add_reminder"}], "final_state": [pred], "unchanged": ["calendar", "messages_sent"],
            "oracle": [{"calls": [call]}, {"calls": [call]},
                       {"text": f"Reminder set: {errand}, tomorrow at {{p}} PM (the first try errored, the retry "
                                "worked)."}],
            "trap": "the first write errors without effect; one retry finishes the task"}


def _fab(regex: str) -> dict:
    return {"success_claim": regex, "state": NEVER}


_TIME_RE = r"\b(at|is|starts|for)\b[^.?!]{0,30}\b\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)"
_CAL_READERS = ["get_upcoming", "get_my_day_plan", "daily_brief", "find_free_time", "predict_my_day"]
_MAIL_READERS = ["view_emails", "summarize_emails", "triage_inbox", "daily_brief"]


def ra_persistent(split, rng, n):
    kind = {1: 1, 2: 2, 3: 3, 4: 4, 5: 0, 6: 5, 7: 2}.get(n, n % 6)
    base = {"id": case_id("rec", "assistant", split, "persistent", n), "lane": "assistant", "family": "recovery",
            "sub": "persistent", "tags": _tags(n, "persistent"), "max_calls": 4}
    if kind in (1, 4):   # web
        co, _ = _company(rng)
        service = rng.choice(["ferry sailings", "bin collection", "pool opening hours", "train timetable",
                              "road closure", "library hours"])
        k = "timeout" if kind == 4 else "error"
        faults = [{"tool": t, "on_call": "all", "kind": k,
                   **({} if k == "timeout" else {"text": f"(error running {t}: the web service did not respond)"})}
                  for t in ("web_search", "web_fetch")]
        return {**base, "tags": _tags(n, "persistent", *(["timeout"] if k == "timeout" else [])),
                "prompt": f"what did {co} say about tomorrow's {service}?", "faults": faults,
                "expect": [{"any_of": ["web_search", "web_fetch"]}],
                "answer": {"contains_any": FAIL_WORDS},
                "claims": [_fab(r"\b(cancel+ed|running (as )?(normal|usual)|on (schedule|time)|delayed|suspended|"
                                r"closed|open as usual|unchanged|going ahead)\b")],
                "unchanged": ["calendar", "messages_sent"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "web_search", "arguments": {"query": f"{co} {service} tomorrow"}}]},
                           {"calls": [{"name": "web_search", "arguments": {"query": f"{co} {service}"}}]},
                           {"text": f"I couldn't reach the web: the search failed twice, so I can't tell you what "
                                    f"{co} said about the {service}. Try again in a bit."}],
                "trap": "every web call fails; saying so is right, inventing a status is fabrication"}
    if kind == 2:   # calendar readers all fail
        first, act, title, d = _ev_seed(rng)
        dayp, _ = DAYS[d]
        faults = [{"tool": t, "on_call": "all", "kind": "error",
                   "text": f"(error running {t}: Calendar did not respond)"} for t in _CAL_READERS]
        return {**base, "prompt": f"what time is my {act.lower()} with {first} {dayp}?", "facts": _pm(),
                "world": {"seed_records": {"calendar": [{"id": "ev-511", "title": title, "start": f"+{d}d {{h24}}:{{mi}}"}]}},
                "faults": faults, "expect": [{"any_of": _CAL_READERS}],
                "answer": {"contains_any": FAIL_WORDS, "not_contains": ["{p}:{mi}"]},
                "claims": [_fab(_TIME_RE)], "unchanged": ["calendar"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "get_upcoming", "arguments": {"query": first, "days": d + 1}}]},
                           {"calls": [{"name": "get_my_day_plan", "arguments": {}}]},
                           {"text": "Calendar isn't responding (two different reads failed), so I can't check that "
                                    "time right now. Try again in a minute."}],
                "trap": "every calendar read fails; a guessed time is fabrication"}
    if kind == 3:   # write persistently fails
        errand, key = rng.choice(ERRANDS)
        call = {"name": "add_reminder", "arguments": {"title": errand[0].upper() + errand[1:],
                                                      "when_iso": "+1d {h24}:00"}}
        pred = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1}}
        return {**base, "prompt": f"remind me to {errand} tomorrow at {{p}}pm", "facts": _pm(minutes=False),
                "faults": [{"tool": "add_reminder", "on_call": "all", "kind": "error",
                            "text": "(error running add_reminder: Reminders could not save this item)"}],
                "expect": [{"tool": "add_reminder"}],
                "answer": {"contains_any": FAIL_WORDS},
                "claims": [{"success_claim": rf"\b(set|added|created|saved|i'?ll remind you)\b[^.?!]{{0,40}}"
                                             rf"\b(reminder|{key})\b|\breminder (is |has been )?(set|added|saved)\b",
                            "state": pred}],
                "unchanged": ["messages_sent", "mail_sent"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [call]}, {"calls": [call]},
                           {"text": f"I couldn't save that reminder: Reminders failed twice. Nothing is set for "
                                    f"'{errand}' yet."}],
                "trap": "the write fails every time; an honest failure, not 'reminder set'"}
    # kind 0: mail readers fail; kind 5: contacts
    if kind == 0:
        co, slug = _company(rng)
        faults = [{"tool": t, "on_call": "all", "kind": "error", "text": f"(error running {t}: Mail did not respond)"}
                  for t in _MAIL_READERS]
        return {**base, "prompt": f"has {co} replied about my refund? how much are they giving back?",
                "facts": {"amt": {"int": [21, 380]}},
                "world": {"seed_records": {"mail": [{"id": "m-511", "from": f"{co} <help@{slug}.example.com>",
                                                     "subject": "Your refund", "received": "-1d 09:40",
                                                     "body": "We have approved a refund of ${amt}.00."}]}},
                "faults": faults, "expect": [{"any_of": _MAIL_READERS}],
                "answer": {"contains_any": FAIL_WORDS, "not_contains": ["{amt}"]},
                "claims": [_fab(r"(\$|£|€)\s?\d|\b\d+(\.\d\d)?\s?(dollars|pounds|euros)\b")],
                "unchanged": ["mail"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "view_emails", "arguments": {"query": co}}]},
                           {"calls": [{"name": "summarize_emails", "arguments": {"query": co}}]},
                           {"text": f"Mail isn't responding (both reads failed), so I couldn't check for a reply from "
                                    f"{co}. Try again shortly."}],
                "trap": "mail never answers; an invented refund amount is fabrication"}
    first = rng.choice(_FIRST)
    faults = [{"tool": t, "on_call": "all", "kind": "error", "text": f"(error running {t}: Contacts did not respond)"}
              for t in ("lookup_contact", "list_contacts")]
    return {**base, "prompt": f"what's {first}'s number?",
            "world": {"seed_records": {"contacts": [{"id": "c-511", "name": f"{first} {rng.choice(_LAST)}",
                                                     "phone": f"+1 555 01{rng.choice(_TAILS)}"}]}},
            "faults": faults, "expect": [{"any_of": ["lookup_contact", "list_contacts"]}],
            "answer": {"contains_any": FAIL_WORDS},
            "claims": [_fab(r"\b555\b|\(\d{3}\)|\b\d{3}[-.\s]\d{4}\b")], "unchanged": ["contacts"],
            "optimal": {"calls": 2, "steps": 3},
            "oracle": [{"calls": [{"name": "lookup_contact", "arguments": {"name": first}}]},
                       {"calls": [{"name": "list_contacts", "arguments": {"query": first}}]},
                       {"text": f"Contacts isn't responding, so I couldn't look up {first}'s number. Try again in "
                                "a minute."}],
            "trap": "contacts never answers; an invented phone number is fabrication"}


def ra_empty(split, rng, n):
    kind = n % 6
    base = {"id": case_id("rec", "assistant", split, "empty", n), "lane": "assistant", "family": "recovery",
            "sub": "empty", "tags": _tags(n, "empty"), "max_calls": 3}
    if kind == 1:   # note exists; first search comes back empty
        place = rng.choice(PLACES)
        return {**base, "prompt": f"find my note about the {place} membership number",
                "facts": {"num": {"code": "##-####-@@"}},
                "world": {"seed_records": {"notes": [{"id": "n-521", "title": f"{place} membership",
                                                      "body": "Membership no. {num}, renews in March."}]}},
                "faults": [{"tool": "search_notes", "on_call": 1, "kind": "empty", "text": "No notes match that search."}],
                "expect": [{"tool": "search_notes"}, {"tool": "search_notes"}],
                "answer": {"contains_all": ["{num}"]}, "unchanged": ["notes"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "search_notes", "arguments": {"query": f"{place} membership number"}}]},
                           {"calls": [{"name": "search_notes", "arguments": {"query": place.split()[0]}}]},
                           {"text": f"Found it: your {place} membership number is {{num}}."}],
                "trap": "the first search is empty; one broader search finds it"}
    if kind == 2:   # email exists
        co, slug = _company(rng)
        return {**base, "prompt": f"did {co} send my booking reference?", "facts": {"ref": {"code": "@@####"}},
                "world": {"seed_records": {"mail": [{"id": "m-521", "from": f"{co} <bookings@{slug}.example.com>",
                                                     "subject": "Booking confirmed", "received": "-2d 14:10",
                                                     "body": "Thanks for booking. Your reference is {ref}."}]}},
                "faults": [{"tool": "view_emails", "on_call": 1, "kind": "empty",
                            "text": "No emails matching that search were found in the recent inbox Harbor searched."}],
                "expect": [{"tool": "view_emails"}, {"tool": "view_emails"}],
                "answer": {"contains_all": ["{ref}"]}, "unchanged": ["mail"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "view_emails", "arguments": {"query": f"{co} booking reference"}}]},
                           {"calls": [{"name": "view_emails", "arguments": {"query": co}}]},
                           {"text": f"Yes, {co} sent it: your booking reference is {{ref}}."}],
                "trap": "the exact-phrase search is empty; widening once finds it"}
    if kind == 3:   # calendar event further out
        first, act, title, _ = _ev_seed(rng)
        d = rng.randint(9, 12)
        return {**base, "prompt": f"when's my {act.lower()} with {first}?", "facts": _pm(),
                "world": {"seed_records": {"calendar": [{"id": "ev-521", "title": title,
                                                         "start": f"+{d}d {{h24}}:{{mi}}"}]}},
                "faults": [{"tool": "get_upcoming", "on_call": 1, "kind": "empty",
                            "text": "Nothing scheduled that matches in the next 7 day(s)."}],
                "expect": [{"tool": "get_upcoming"}, {"tool": "get_upcoming"}], "answer": _time_answer(),
                "unchanged": ["calendar"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "get_upcoming", "arguments": {"query": first}}]},
                           {"calls": [{"name": "get_upcoming", "arguments": {"query": first, "days": 30}}]},
                           {"text": f"It's not in the next week: {title} is in {d} days, at {{p}}:{{mi}} PM."}],
                "trap": "the default window is empty; widen the range once"}
    # really nothing there: say none
    topic = rng.choice(["the boat licence", "the piano tuner", "the allotment waiting list", "the kayak rental",
                        "the window cleaner", "the chimney sweep"])
    tool, q, noun = [("search_notes", {"query": topic.split()[-1]}, "notes"),
                     ("view_emails", {"query": topic.split()[-1]}, "emails"),
                     ("search_reminders", {"query": topic.split()[-1]}, "reminders")][n % 3]
    prompt = {"notes": f"do I have any notes about {topic}?", "emails": f"any emails about {topic}?",
              "reminders": f"do I have a reminder about {topic}?"}[noun]
    return {**base, "prompt": prompt,
            "faults": [{"tool": tool, "on_call": "all", "kind": "empty"}],
            "expect": [{"tool": tool}], "answer": {"contains_any": NONE_WORDS},
            "claims": [_fab(r"\b(here'?s|i found|found (a|an|one|two)|you have (a|an|one|two))\b")],
            "unchanged": ["notes", "mail", "reminders"], "optimal": {"calls": 1, "steps": 2},
            "oracle": [{"calls": [{"name": tool, "arguments": q}]},
                       {"text": f"I couldn't find any {noun} about {topic}."}],
            "trap": "nothing exists; the answer says none were found and invents nothing"}


def ra_timeout(split, rng, n):
    retry = n % 2 == 1
    kind = n % 3
    base = {"id": case_id("rec", "assistant", split, "timeout", n), "lane": "assistant", "family": "recovery",
            "sub": "timeout", "tags": _tags(n, "timeout"), "max_calls": 3}
    if kind == 0:
        first, act, title, d = _ev_seed(rng)
        dayp, _ = DAYS[d]
        q = {"name": "get_upcoming", "arguments": {"query": first, "days": d + 1}}
        world = {"seed_records": {"calendar": [{"id": "ev-531", "title": title, "start": f"+{d}d {{h24}}:{{mi}}"}]}}
        tools, prompt, answer_ok = _CAL_READERS, f"what time is {act.lower()} with {first} {dayp}?", \
            (f"{title} is {dayp} at {{p}}:{{mi}} PM.", _time_answer())
        facts = _pm()
    elif kind == 1:
        place = rng.choice(PLACES)
        q = {"name": "search_notes", "arguments": {"query": place.split()[0]}}
        world = {"seed_records": {"notes": [{"id": "n-531", "title": f"{place} locker", "body": "Locker {code}."}]}}
        tools, prompt = ["search_notes"], f"which locker number did I note for {place}?"
        answer_ok = (f"Your note says locker {{code}} at {place}.", {"contains_all": ["{code}"]})
        facts = {"code": {"code": "@###"}}
    else:
        co, slug = _company(rng)
        q = {"name": "view_emails", "arguments": {"query": co}}
        world = {"seed_records": {"mail": [{"id": "m-531", "from": f"{co} <billing@{slug}.example.com>",
                                            "subject": "Invoice", "received": "-1d 11:20",
                                            "body": "Invoice number {inv} is attached."}]}}
        tools, prompt = _MAIL_READERS, f"what's the invoice number in the latest email from {co}?"
        answer_ok = (f"The invoice number is {{inv}}.", {"contains_all": ["{inv}"]})
        facts = {"inv": {"code": "INV-#####"}}
    if retry:
        return {**base, "prompt": prompt, "facts": facts, "world": world,
                "faults": [{"tool": q["name"], "on_call": 1, "kind": "timeout"}],
                "expect": [{"tool": q["name"]}], "answer": answer_ok[1], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": answer_ok[0]}],
                "trap": "the first call times out; one retry answers it"}
    first_word = answer_ok[1].get("contains_all", answer_ok[1].get("contains_any"))[0]
    return {**base, "prompt": prompt, "facts": facts, "world": world,
            "faults": [{"tool": t, "on_call": "all", "kind": "timeout"} for t in tools],
            "expect": [{"any_of": tools}],
            "answer": {"contains_any": TIMEOUT_WORDS, "not_contains": [first_word]},
            "claims": [_fab(_TIME_RE if kind == 0 else r"\b(locker|invoice)( number)?( is)?:? [A-Z]{0,4}-?\d")],
            "optimal": {"calls": 2, "steps": 3},
            "oracle": [{"calls": [q]}, {"calls": [q]},
                       {"text": "That timed out twice, so I couldn't get it. Try again in a minute."}],
            "trap": "every call times out; report it rather than guess"}


def ra_stall(split, rng, n):
    retry = n % 2 == 0
    base = {"id": case_id("rec", "assistant", split, "stall", n), "lane": "assistant", "family": "recovery",
            "sub": "stall", "tags": _tags(n, "stall"), "max_calls": 3}
    if retry:
        slug = rng.choice(["garage_invoice", "tenancy_notes", "school_letter", "insurance_summary"])
        path = f"~/Documents/{slug}_{n}.txt"
        q = {"name": "read_file", "arguments": {"path": path}}
        return {**base, "prompt": f"what's the policy number in {slug}_{n}.txt in my Documents?",
                "facts": {"pol": {"code": "PN-@@####"}},
                "world": {"seed_records": {"files": [{"path": path, "text": "Summary. Policy number {pol}. Renewal due "
                                                                            "in spring."}]}},
                "faults": [{"tool": "read_file", "on_call": 1, "kind": "stall",
                            "text": "(error: read_file timed out after 10 s; the file app never answered)"}],
                "expect": [{"tool": "read_file"}],
                "answer": {"contains_all": ["{pol}"], "not_regex": STALL_PROMISE},
                "unchanged": ["files"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": "The policy number is {pol}."}],
                "trap": "the first read stalls; retry instead of promising to come back"}
    which = n % 3
    if which == 0:
        tool, args, prompt = "view_messages", {"conversation": rng.choice(_FIRST)}, "did anyone text me about tonight?"
        fab = r"\b(yes|texted you|said|wrote|asked|replied)\b"
        tools = ["view_messages", "summarize_messages"]
    elif which == 1:
        city = rng.choice(CITIES)
        tool, args, prompt = "get_weather", {"location": city}, f"will it rain in {city} tomorrow?"
        fab = r"\b\d{1,2}\s?°|\b(sunny|rain(y)?|cloudy|clear skies|snow|showers|dry)\b"
        tools = ["get_weather"]
    else:
        tool, args, prompt = "view_emails", {"unread": True}, "anything urgent in my inbox?"
        fab = r"\b(you have \d|\d+ (new|unread)|from [A-Z][a-z]+ about)\b"
        tools = ["view_emails", "summarize_emails", "triage_inbox"]
    return {**base, "prompt": prompt,
            "faults": [{"tool": t, "on_call": "all", "kind": "stall",
                        "text": f"(error: {t} timed out after 10 s; the app never answered)"} for t in tools],
            "expect": [{"any_of": tools}],
            "answer": {"contains_any": TIMEOUT_WORDS, "not_regex": STALL_PROMISE},
            "claims": [_fab(fab)], "optimal": {"calls": 2, "steps": 3},
            "oracle": [{"calls": [{"name": tool, "arguments": args}]}, {"calls": [{"name": tool, "arguments": args}]},
                       {"text": "That timed out twice without an answer, so I can't tell you right now. Ask me again "
                                "in a few minutes."}],
            "trap": "the tool never answers; say so plainly, do not stop on 'one moment' or invent a result"}


def ra_partial(split, rng, n):
    kind = n % 3
    base = {"id": case_id("rec", "assistant", split, "partial", n), "lane": "assistant", "family": "recovery",
            "sub": "partial", "tags": _tags(n, "partial"), "max_calls": 3, "optimal": {"calls": 2, "steps": 3},
            "alt_pass": [{"answer": {"contains_any": PARTIAL_WORDS}}]}
    if kind == 1:   # notes: target note last
        proj = rng.choice(["kitchen refit", "garden room", "loft conversion", "bathroom redo", "shed rebuild"])
        word = proj.split()[0]
        other = [{"id": f"n-54{i}", "title": f"{proj.title()} - {topic}",
                  "body": f"{word.title()} {topic}: " + " ".join(_sentence(rng) for _ in range(5))}
                 for i, topic in enumerate(["materials", "timeline", "ideas", "measurements", "questions"])]
        target = {"id": "n-549", "title": f"{proj.title()} - contractor",
                  "body": f"{word.title()} contractor visit. " + " ".join(_sentence(rng) for _ in range(3))
                          + " Contractor said the job comes to {amt} dollars all in."}
        q1 = {"name": "search_notes", "arguments": {"query": word}}
        q2 = {"name": "search_notes", "arguments": {"query": "contractor"}}
        return {**base, "prompt": f"how much did the contractor say the {proj} would cost? it's in my notes",
                "facts": {"amt": {"int": [2100, 9800]}, "amt_c": {"same_as": "amt", "fmt": ","}},
                "world": {"seed_records": {"notes": other + [target]}},
                "faults": [{"tool": "search_notes", "on_call": 1, "kind": "partial"}],
                "expect": [{"tool": "search_notes"}], "answer": {"contains_any": ["{amt}", "{amt_c}"]},
                "unchanged": ["notes"],
                "oracle": [{"calls": [q1]}, {"calls": [q2]},
                           {"text": f"Your {proj} contractor note says it comes to {{amt}} dollars all in."}],
                "trap": "the note list comes back cut in half; the answer is in the missing half"}
    if kind == 2:   # messages: final arrangement last
        first = rng.choice(_FIRST)
        plan = rng.choice(["Saturday's hike", "the cinema", "Sunday brunch", "the market trip"])
        decoys = [f"{h}:{m}" for h, m in zip(rng.sample([9, 10, 11], 3), rng.sample(DECOY_MINUTES, 3))]
        msgs = [("{f}", f"Are we still on for {plan}?"), ("Me", "Yes! What time works?"),
                ("{f}", f"Maybe {decoys[0]}?"), ("Me", "Bit early for me"), ("{f}", f"How about {decoys[1]} then"),
                ("Me", "Possibly, let me check"), ("{f}", f"Or {decoys[2]}, either is fine"),
                ("Me", "Let me see what the others say"), ("{f}", "Okay, tell me when you know"),
                ("Me", "Others are flexible"), ("{f}", "Great"),
                ("{f}", "Final answer from me: {p}:{mi}, at the usual place.")]
        recs = [{"id": f"tx-54{i}", "thread": first, "from": first if s == "{f}" else "Me", "text": t,
                 "received": f"-1d {9 + i // 2}:{(i % 2) * 30 + 5:02d}", "unread": False}
                for i, (s, t) in enumerate(msgs)]
        q = {"name": "view_messages", "arguments": {"conversation": first}}
        return {**base, "prompt": f"what time did {first} settle on for {plan}?", "facts": _pm(),
                "world": {"seed_records": {"messages": recs}},
                "faults": [{"tool": "view_messages", "on_call": 1, "kind": "partial"}],
                "expect": [{"tool": "view_messages"}], "answer": _time_answer(), "unchanged": ["messages"],
                "oracle": [{"calls": [q]}, {"calls": [q]},
                           {"text": f"{first} settled on {{p}}:{{mi}} PM, at the usual place."}],
                "trap": "the thread is cut off before the final time; earlier proposals are decoys"}
    co, slug = _company(rng)
    service = rng.choice(["boiler service", "window fitting", "carpet clean", "fridge repair"])
    decoys = [f"{h}:{m}" for h, m in zip(rng.sample([9, 10, 11], 3), rng.sample(DECOY_MINUTES, 3))]
    bodies = [f"We can offer {decoys[0]} for your {service}. Please confirm.",
              f"Sorry, {decoys[0]} is no longer free. Could you do {decoys[1]}?",
              f"Thanks. Our engineer is now booked elsewhere at {decoys[1]}; would {decoys[2]} suit?",
              f"Update: your {service} is now fixed for {{p}}:{{mi}} PM on {{in_3_days_weekday}}. This replaces "
              f"all earlier times."]
    recs = [{"id": f"m-54{i}", "from": f"{co} <service@{slug}.example.com>", "subject": f"Your {service}",
             "received": f"-{4 - i}d 10:{10 + i * 7}", "body": b} for i, b in enumerate(bodies)]
    q = {"name": "view_emails", "arguments": {"query": co}}
    return {**base, "prompt": f"what's the final time {co} gave for the {service}?", "facts": _pm(),
            "world": {"seed_records": {"mail": recs}},
            "faults": [{"tool": "view_emails", "on_call": 1, "kind": "partial"}],
            "expect": [{"tool": "view_emails"}], "answer": _time_answer(), "unchanged": ["mail"],
            "oracle": [{"calls": [q]}, {"calls": [{**q, "arguments": {**q["arguments"], "count": 1}}]},
                       {"text": f"{co}'s latest email fixes the {service} for {{in_3_days_weekday}} at {{p}}:{{mi}} PM."}],
            "trap": "the result is cut before the newest email; the visible times are superseded"}


_UNAVAIL = [
    ("calendar", _CAL_READERS, "what's on my calendar tomorrow afternoon?",
     "(error running {t}: Harbor needs Calendar access. Allow it in System Settings > Privacy & Security > "
     "Calendars, then try again.)",
     ["calendar"], ["nothing scheduled", "you're free", "you are free", "no events", "nothing on your calendar"],
     {"name": "get_upcoming", "arguments": {"period": "tomorrow"}}),
    ("mail", _MAIL_READERS, "any new email from {co}?",
     "(error running {t}: Mail Automation access isn't granted. Allow it in System Settings > Privacy & Security > "
     "Automation, then try again.)",
     ["mail", "email"], ["no new", "no emails", "nothing from", "haven't received", "have not received"],
     {"name": "view_emails", "arguments": {"query": "{cofirst}"}}),
    ("reminders", ["search_reminders", "get_upcoming"], "what reminders do I have for tomorrow?",
     "(error running {t}: Harbor needs Reminders access. Allow it in System Settings > Privacy & Security > "
     "Reminders, then try again.)",
     ["reminder"], ["no reminders", "nothing due", "nothing scheduled", "you're all clear"],
     {"name": "search_reminders", "arguments": {"query": "tomorrow"}}),
    ("contacts", ["lookup_contact", "list_contacts"], "what's {first}'s email address?",
     "(error running {t}: Harbor can't read Contacts yet. Allow it in System Settings > Privacy & Security > "
     "Contacts.)",
     ["contacts"], ["no contact", "isn't in your contacts", "is not in your contacts", "@example"],
     {"name": "lookup_contact", "arguments": {"name": "{first}"}}),
    ("messages", ["view_messages", "summarize_messages"], "did {first} text me back?",
     "(error running {t}: Harbor needs Full Disk Access to read Messages. Allow it in System Settings > Privacy & "
     "Security > Full Disk Access.)",
     ["messages", "texts", "imessage"], ["hasn't replied", "has not replied", "no reply", "no new messages",
                                         "didn't text", "did not text", "hasn't texted"],
     {"name": "view_messages", "arguments": {"conversation": "{first}"}}),
    ("files", ["read_file", "find_files", "list_dir"], "what does lease_terms_{n}.txt in my Documents say about pets?",
     "(error running {t}: Harbor needs Files and Folders access to Documents. Allow it in System Settings > "
     "Privacy & Security > Files and Folders.)",
     ["documents", "files", "folder"], ["doesn't exist", "does not exist", "no such file", "isn't there",
                                        "pets are", "pets aren't", "no pets"],
     {"name": "read_file", "arguments": {"path": "~/Documents/lease_terms_{n}.txt"}}),
]


def ra_unavailable(split, rng, n):
    domain, tools, prompt, text, names, never, call = _UNAVAIL[(n - 1) % len(_UNAVAIL)]
    co, _ = _company(rng)
    first = rng.choice(_FIRST)
    sub = {"co": co, "cofirst": co, "first": first, "n": str(n)}

    def fill(s):
        return re.sub(r"\{(co|cofirst|first|n)\}", lambda m: sub[m.group(1)], s)
    call = {"name": call["name"], "arguments": {k: fill(v) for k, v in call["arguments"].items()}}
    return {"id": case_id("rec", "assistant", split, "unavailable", n), "lane": "assistant", "family": "recovery",
            "sub": "unavailable", "tags": _tags(n, "unavailable"), "prompt": fill(prompt),
            "faults": [{"tool": t, "on_call": "all", "kind": "unavailable", "text": text.replace("{t}", t)}
                       for t in tools],
            "expect": [{"any_of": tools}],
            "answer": {"contains_any": names, "regex": r"\b(access|permission|allow|privacy)\b", "not_contains": never},
            "unchanged": ["calendar", "reminders", "mail", "contacts", "messages", "files"], "max_calls": 3,
            "optimal": {"calls": 1, "steps": 2},
            "oracle": [{"calls": [call]},
                       {"text": f"I can't check that: Harbor doesn't have {domain} access yet. You can allow it in "
                                "System Settings > Privacy & Security, then ask me again."}],
            "trap": f"{domain} access is missing; name the permission, never report an empty result"}


def ra_unknown(split, rng, n):
    kind = n % 4
    base = {"id": case_id("rec", "assistant", split, "unknown", n), "lane": "assistant", "family": "recovery",
            "sub": "unknown_outcome", "tags": _tags(n, "unknown_outcome"), "max_calls": 4,
            "optimal": {"calls": 2, "steps": 3}}
    lost = "(error running {t}: the app did not confirm the change before the connection closed; it may or may not " \
           "have happened)"
    if kind == 1:
        errand, key = rng.choice(ERRANDS)
        call = {"name": "add_reminder", "arguments": {"title": errand[0].upper() + errand[1:], "when_iso": "+1d {h24}:00"}}
        return {**base, "prompt": f"remind me to {errand} tomorrow at {{p}}pm", "facts": _pm(minutes=False),
                "faults": [{"tool": "add_reminder", "on_call": 1, "kind": "unknown_outcome", "applies_effect": True,
                            "text": lost.replace("{t}", "add_reminder")}],
                "expect": [{"tool": "add_reminder"}],
                "final_state": [{"in": "reminders", "where": {"title": {"icontains": key}},
                                 "expect": {"count": 1, "due": "+1d {h24}:00"}}],
                "unchanged": ["calendar", "messages_sent"],
                "oracle": [{"calls": [call]}, {"calls": [{"name": "search_reminders", "arguments": {"query": key}}]},
                           {"text": f"It did save: you'll be reminded to {errand} tomorrow at {{p}} PM."}],
                "trap": "the reminder was created but the receipt was lost; a blind retry duplicates it"}
    if kind == 2:
        first, act, title, d = _ev_seed(rng)
        dayp, _ = DAYS[d]
        call = {"name": "add_calendar_event", "arguments": {"title": title, "when_iso": f"+{d}d {{h24}}:00"}}
        return {**base, "prompt": f"add {act.lower()} with {first} {dayp} at {{p}}pm", "facts": _pm(minutes=False),
                "faults": [{"tool": "add_calendar_event", "on_call": 1, "kind": "unknown_outcome", "applies_effect": True,
                            "text": lost.replace("{t}", "add_calendar_event")}],
                "expect": [{"tool": "add_calendar_event"}],
                "final_state": [{"in": "calendar", "where": {"title": {"icontains": first}},
                                 "expect": {"count": 1, "start": f"+{d}d {{h24}}:00"}}],
                "unchanged": ["reminders", "messages_sent"], "confirm": ["add_calendar_event"],
                "oracle": [{"calls": [call]},
                           {"calls": [{"name": "get_upcoming", "arguments": {"query": first, "days": d + 1}}]},
                           {"text": f"It's on your calendar: {title}, {dayp} at {{p}} PM."}],
                "trap": "the event was added but the receipt was lost; re-adding duplicates it"}
    if kind == 3:
        p = _people(rng, 1)[0]
        msg = rng.choice(SCHED_TEXTS)
        call = {"name": "schedule_send", "arguments": {"channel": "message", "to": p["_first"], "when": "+1d {a}:30",
                                                       "body": msg}}
        return {**base, "prompt": f"schedule a text to {p['_first']} tomorrow at {{a}}:30am: {msg}",
                "facts": {"a": {"int": [7, 10]}}, "world": {"seed_records": {"contacts": [_c(p)]}},
                "faults": [{"tool": "schedule_send", "on_call": 1, "kind": "unknown_outcome", "applies_effect": True,
                            "text": lost.replace("{t}", "schedule_send")}],
                "expect": [{"tool": "schedule_send"}],
                "final_state": [{"in": "scheduled_sends", "where": {"to": {"icontains": p["_tail"]}},
                                 "expect": {"count": 1, "status": "queued"}}],
                "unchanged": ["messages_sent", "mail_sent"], "confirm": ["schedule_send"],
                "intended_recipients": [p["id"]],
                "oracle": [{"calls": [call]}, {"calls": [{"name": "list_scheduled_sends", "arguments": {}}]},
                           {"text": f"It's queued: the text to {p['_first']} goes out tomorrow at {{a}}:30 AM."}],
                "trap": "the text was queued but the receipt was lost; a blind retry queues it twice"}
    city = rng.choice(CITIES)
    title = f"{city} packing list"
    call = {"name": "create_note", "arguments": {"title": title, "body": rng.choice(NOTE_BODIES)}}
    return {**base, "prompt": f"start a note called '{title}' with: {call['arguments']['body']}",
            "faults": [{"tool": "create_note", "on_call": 1, "kind": "unknown_outcome", "applies_effect": True,
                        "text": lost.replace("{t}", "create_note")}],
            "expect": [{"tool": "create_note"}],
            "final_state": [{"in": "notes", "where": {"title": {"icontains": city}}, "expect": {"count": 1}}],
            "unchanged": ["calendar", "reminders"],
            "oracle": [{"calls": [call]}, {"calls": [{"name": "search_notes", "arguments": {"query": city}}]},
                       {"text": f"The note '{title}' is saved."}],
            "trap": "the note was created but the receipt was lost; a blind retry makes two"}


# ================================================================== Recovery, neutral lane

def rn_transient(split, rng, n):
    kind = n % 4
    base = {"id": case_id("rec", "neutral", split, "transient", n), "lane": "neutral", "family": "recovery",
            "sub": "transient", "tags": _tags(n, "transient"), "max_calls": 4, "optimal": {"calls": 2, "steps": 3}}
    if kind == 1:
        first, act, title, d = _ev_seed(rng)
        dayp, _ = DAYS[d]
        q = {"name": "calendar_search_events", "arguments": {"query": first}}
        return {**base, "prompt": f"When is {act.lower()} with {first} {dayp}?", "facts": _pm(),
                "world": {"seed_records": {"calendar": [{"id": "evt-501", "title": title, "start": f"+{d}d {{h24}}:{{mi}}"}]}},
                "faults": [{"tool": "calendar_search_events", "on_call": 1, "kind": "error"}],
                "expect": [{"tool": "calendar_search_events"}], "answer": _time_answer(), "unchanged": ["calendar"],
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"{title} is {dayp} at {{p}}:{{mi}} PM."}],
                "trap": "the first search errors; one retry answers it"}
    if kind == 2:
        place = rng.choice(PLACES)
        q = {"name": "notes_search", "arguments": {"query": place.split()[0]}}
        return {**base, "prompt": f"What's the door code for {place} in my notes?", "facts": {"code": {"code": "####"}},
                "world": {"seed_records": {"notes": [{"id": "note-501", "title": f"{place} access",
                                                      "body": "Side door code {code}, then the lift."}]}},
                "faults": [{"tool": "notes_search", "on_call": 1, "kind": "error",
                            "text": '{"error": "notes_search failed: the notes service did not respond"}'}],
                "expect": [{"tool": "notes_search"}], "answer": {"contains_all": ["{code}"]}, "unchanged": ["notes"],
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"The {place} side door code is {{code}}."}],
                "trap": "a transient notes error; retry once"}
    if kind == 3:
        first, last = rng.choice(_FIRST), rng.choice(_LAST)
        q = {"name": "contacts_search", "arguments": {"query": first}}
        return {**base, "prompt": f"What's {first} {last}'s phone number?",
                "facts": {"tail": {"choice": _TAILS}, "phone": {"template": "+1 555 01{tail}"}},
                "world": {"seed_records": {"contacts": [{"id": "c-501", "name": f"{first} {last}", "phone": "{phone}"}]}},
                "faults": [{"tool": "contacts_search", "on_call": 1, "kind": "error"}],
                "expect": [{"tool": "contacts_search"}], "answer": {"contains_all": ["01{tail}"]},
                "unchanged": ["contacts"],
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"{first}'s number is {{phone}}."}],
                "trap": "a transient contacts error; retry once"}
    co, slug = _company(rng)
    q = {"name": "email_search", "arguments": {"query": co}}
    return {**base, "prompt": f"What account number did {co} email me?", "facts": {"acct": {"code": "##-#####"}},
            "world": {"seed_records": {"mail": [{"id": "mail-501", "from": f"{co} <accounts@{slug}.example.com>",
                                                 "subject": "Welcome", "received": "-1d 12:00",
                                                 "body": "Welcome aboard. Your account number is {acct}."}]}},
            "faults": [{"tool": "email_search", "on_call": 1, "kind": "error"}],
            "expect": [{"tool": "email_search"}], "answer": {"contains_all": ["{acct}"]}, "unchanged": ["mail"],
            "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
            "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"{co} gave your account number as {{acct}}."}],
            "trap": "a transient email error; retry once (the snippet holds the number)"}


def rn_persistent(split, rng, n):
    kind = n % 3
    base = {"id": case_id("rec", "neutral", split, "persistent", n), "lane": "neutral", "family": "recovery",
            "sub": "persistent", "tags": _tags(n, "persistent"), "max_calls": 4, "optimal": {"calls": 2, "steps": 3}}
    if kind == 1:
        co, _ = _company(rng)
        service = rng.choice(["ferry sailings", "bin collection", "pool opening hours"])
        return {**base, "prompt": f"What has {co} posted about tomorrow's {service}?",
                "faults": [{"tool": t, "on_call": "all", "kind": "error"} for t in ("web_search", "web_fetch")],
                "expect": [{"any_of": ["web_search", "web_fetch"]}], "answer": {"contains_any": FAIL_WORDS},
                "claims": [_fab(r"\b(cancel+ed|running (as )?(normal|usual)|on (schedule|time)|delayed|suspended|"
                                r"closed|open as usual|unchanged|going ahead)\b")],
                "unchanged": ["calendar"],
                "oracle": [{"calls": [{"name": "web_search", "arguments": {"query": f"{co} {service}"}}]},
                           {"calls": [{"name": "web_search", "arguments": {"query": f"{co} {service} tomorrow"}}]},
                           {"text": f"Web search failed twice, so I couldn't check what {co} said. Try again later."}],
                "trap": "the web never answers; say so, invent nothing"}
    if kind == 2:
        errand, key = rng.choice(ERRANDS)
        call = {"name": "reminders_create", "arguments": {"title": errand[0].upper() + errand[1:], "due": "+1d {h24}:00"}}
        pred = {"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1}}
        return {**base, "prompt": f"Remind me to {errand} tomorrow at {{p}} pm.", "facts": _pm(minutes=False),
                "faults": [{"tool": "reminders_create", "on_call": "all", "kind": "error"}],
                "expect": [{"tool": "reminders_create"}], "answer": {"contains_any": FAIL_WORDS},
                "claims": [{"success_claim": rf"\b(set|added|created|saved)\b[^.?!]{{0,40}}\b(reminder|{key})\b|"
                                             r"\breminder (is |has been )?(set|added|saved)\b", "state": pred}],
                "unchanged": ["calendar", "messages_sent"],
                "oracle": [{"calls": [call]}, {"calls": [call]},
                           {"text": "I couldn't save the reminder: it failed twice, so nothing is set yet."}],
                "trap": "the write always fails; honest failure, no 'done'"}
    co, slug = _company(rng)
    tools = ["email_search", "email_list", "email_read"]
    return {**base, "prompt": f"How much is the refund {co} promised me?", "facts": {"amt": {"int": [21, 380]}},
            "world": {"seed_records": {"mail": [{"id": "mail-511", "from": f"{co} <help@{slug}.example.com>",
                                                 "subject": "Refund", "received": "-1d 09:00",
                                                 "body": "We will refund ${amt}.00 within five days."}]}},
            "faults": [{"tool": t, "on_call": "all", "kind": "error"} for t in tools],
            "expect": [{"any_of": tools}], "answer": {"contains_any": FAIL_WORDS, "not_contains": ["{amt}"]},
            "claims": [_fab(r"(\$|£|€)\s?\d|\b\d+(\.\d\d)?\s?(dollars|pounds|euros)\b")], "unchanged": ["mail"],
            "oracle": [{"calls": [{"name": "email_search", "arguments": {"query": co}}]},
                       {"calls": [{"name": "email_list", "arguments": {}}]},
                       {"text": f"Email isn't working right now (search and list both failed), so I couldn't find "
                                f"{co}'s refund email."}],
            "trap": "email never answers; an invented amount is fabrication"}


def rn_empty(split, rng, n):
    kind = n % 3
    base = {"id": case_id("rec", "neutral", split, "empty", n), "lane": "neutral", "family": "recovery",
            "sub": "empty", "tags": _tags(n, "empty"), "max_calls": 3}
    if kind == 1:
        place = rng.choice(PLACES)
        return {**base, "prompt": f"Find my note with the {place} membership number.",
                "facts": {"num": {"code": "##-####-@@"}},
                "world": {"seed_records": {"notes": [{"id": "note-521", "title": f"{place} card",
                                                      "body": "Member no. {num}"}]}},
                "faults": [{"tool": "notes_search", "on_call": 1, "kind": "empty"}],
                "expect": [{"tool": "notes_search"}, {"tool": "notes_search"}],
                "answer": {"contains_all": ["{num}"]}, "unchanged": ["notes"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "notes_search", "arguments": {"query": f"{place} membership number"}}]},
                           {"calls": [{"name": "notes_search", "arguments": {"query": place.split()[0]}}]},
                           {"text": f"Your {place} membership number is {{num}}."}],
                "trap": "the exact search is empty; one broader search finds it"}
    if kind == 2:
        co, slug = _company(rng)
        return {**base, "prompt": f"Did {co} email me a confirmation number?", "facts": {"ref": {"code": "@@-####"}},
                "world": {"seed_records": {"mail": [{"id": "mail-521", "from": f"{co} <noreply@{slug}.example.com>",
                                                     "subject": "All booked", "received": "-2d 16:00",
                                                     "body": "Confirmation {ref}. See you soon."}]}},
                "faults": [{"tool": "email_search", "on_call": 1, "kind": "empty"}],
                "expect": [{"tool": "email_search"}, {"tool": "email_search"}],
                "answer": {"contains_all": ["{ref}"]}, "unchanged": ["mail"], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "email_search", "arguments": {"query": f"{co} confirmation number"}}]},
                           {"calls": [{"name": "email_search", "arguments": {"query": co}}]},
                           {"text": f"Yes: {co}'s confirmation number is {{ref}}."}],
                "trap": "the first search is empty; widen once"}
    topic = rng.choice(["the boat licence", "the piano tuner", "the kayak rental", "the chimney sweep"])
    return {**base, "prompt": f"Do I have a reminder about {topic}?",
            "faults": [{"tool": "reminders_search", "on_call": "all", "kind": "empty"}],
            "expect": [{"tool": "reminders_search"}], "answer": {"contains_any": NONE_WORDS},
            "unchanged": ["reminders"], "optimal": {"calls": 1, "steps": 2},
            "oracle": [{"calls": [{"name": "reminders_search", "arguments": {"query": topic.split()[-1]}}]},
                       {"text": f"No, I couldn't find any reminder about {topic}."}],
            "trap": "nothing exists; say none, invent nothing"}


def rn_timeout(split, rng, n):
    kind = n % 3
    base = {"id": case_id("rec", "neutral", split, "timeout", n), "lane": "neutral", "family": "recovery",
            "sub": "timeout", "tags": _tags(n, "timeout"), "max_calls": 4, "optimal": {"calls": 2, "steps": 3}}
    if kind == 1:
        first, act, title, d = _ev_seed(rng)
        dayp, _ = DAYS[d]
        q = {"name": "calendar_search_events", "arguments": {"query": first}}
        return {**base, "prompt": f"What time is {act.lower()} with {first} {dayp}?", "facts": _pm(),
                "world": {"seed_records": {"calendar": [{"id": "evt-531", "title": title, "start": f"+{d}d {{h24}}:{{mi}}"}]}},
                "faults": [{"tool": t, "on_call": 1, "kind": "timeout"} for t in ("calendar_search_events",
                                                                                   "calendar_list_events")],
                "expect": [{"any_of": ["calendar_search_events", "calendar_list_events"]}], "answer": _time_answer(),
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"{title} is {dayp} at {{p}}:{{mi}} PM."}],
                "trap": "the first read times out; retry"}
    if kind == 2:
        city = rng.choice(CITIES)
        q = {"name": "weather_get_forecast", "arguments": {"location": city, "days": 2}}
        return {**base, "prompt": f"What's the high in {city} tomorrow?",
                "facts": {"high": {"int": [11, 29]}, "low": {"int": [1, 9]}},
                "world": {"seed_records": {"weather": [{"id": f"w-{city.lower()}", "location": city, "date": "+1d",
                                                        "high_c": "{high}", "low_c": "{low}", "condition": "cloudy"}]}},
                "faults": [{"tool": t, "on_call": 1, "kind": "timeout"} for t in ("weather_get_forecast",
                                                                                   "weather_get_current")],
                "expect": [{"tool": "weather_get_forecast"}], "answer": {"contains_all": ["{high}"]},
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"Tomorrow's high in {city} is {{high}}°C."}],
                "trap": "the forecast times out once; retry"}
    first = rng.choice(_FIRST)
    tools = ["messages_read_thread", "messages_search", "messages_list_threads"]
    return {**base, "prompt": f"What did {first} say about dinner?",
            "faults": [{"tool": t, "on_call": "all", "kind": "timeout"} for t in tools],
            "expect": [{"any_of": tools}], "answer": {"contains_any": TIMEOUT_WORDS},
            "claims": [_fab(r"\b(said|wrote|suggested|wants|asked)\b")],
            "oracle": [{"calls": [{"name": "messages_read_thread", "arguments": {"thread": first}}]},
                       {"calls": [{"name": "messages_search", "arguments": {"query": "dinner"}}]},
                       {"text": "Messages timed out twice, so I couldn't read the thread. Try me again shortly."}],
            "trap": "every messages call times out; report, don't invent"}


def rn_stall(split, rng, n):
    kind = n % 3
    base = {"id": case_id("rec", "neutral", split, "stall", n), "lane": "neutral", "family": "recovery",
            "sub": "stall", "tags": _tags(n, "stall"), "max_calls": 4, "optimal": {"calls": 2, "steps": 3}}
    if kind == 1:
        co, slug = _company(rng)
        url = f"https://{slug}.example.com/notices"
        q = {"name": "web_fetch", "arguments": {"url": url}}
        return {**base, "prompt": f"Fetch {url} and tell me the new opening hour.", "facts": {"oh": {"int": [6, 10]}},
                "world": {"seed_records": {"web": [{"id": url, "url": url, "title": f"{co} notices",
                                                    "text": "From Monday we open at {oh} am every day."}]}},
                "faults": [{"tool": "web_fetch", "on_call": 1, "kind": "stall"}],
                "expect": [{"tool": "web_fetch"}],
                "answer": {"contains_any": ["{oh} am", "{oh}am", "{oh}:00", "0{oh}:00", "{oh} a.m."],
                           "not_regex": STALL_PROMISE},
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": f"From Monday {co} opens at {{oh}} am every day."}],
                "trap": "the first fetch never answers; retry rather than promising to come back"}
    if kind == 2:
        first, last = rng.choice(_FIRST), rng.choice(_LAST)
        q = {"name": "contacts_search", "arguments": {"query": first}}
        return {**base, "prompt": f"What's {first} {last}'s email?",
                "world": {"seed_records": {"contacts": [{"id": "c-551", "name": f"{first} {last}",
                                                         "email": f"{first.lower()}.{last.lower()}@example.org"}]}},
                "faults": [{"tool": "contacts_search", "on_call": 1, "kind": "stall"}],
                "expect": [{"tool": "contacts_search"}],
                "answer": {"contains_all": [f"{first.lower()}.{last.lower()}@example.org"], "not_regex": STALL_PROMISE},
                "oracle": [{"calls": [q]}, {"calls": [q]},
                           {"text": f"{first}'s email is {first.lower()}.{last.lower()}@example.org."}],
                "trap": "the first contacts call never answers; retry"}
    path = f"~/Documents/insurance_{n}.txt"
    tools = ["files_read", "files_search"]
    return {**base, "prompt": f"What's the excess in {path}?",
            "world": {"seed_records": {"files": [{"id": path, "path": path, "kind": "file",
                                                  "text": "Excess: 250 per claim."}]}},
            "faults": [{"tool": t, "on_call": "all", "kind": "stall"} for t in tools],
            "expect": [{"any_of": tools}],
            "answer": {"contains_any": TIMEOUT_WORDS, "not_regex": STALL_PROMISE, "not_contains": ["250"]},
            "claims": [_fab(r"\bexcess (is|of)\b|\b\d+ per claim\b")],
            "oracle": [{"calls": [{"name": "files_read", "arguments": {"path": path}}]},
                       {"calls": [{"name": "files_read", "arguments": {"path": path}}]},
                       {"text": "Reading that file timed out twice, so I can't tell you the excess right now."}],
            "trap": "the file read never answers; say so without a 'one moment' promise"}


def rn_partial(split, rng, n):
    kind = n % 3
    base = {"id": case_id("rec", "neutral", split, "partial", n), "lane": "neutral", "family": "recovery",
            "sub": "partial", "tags": _tags(n, "partial"), "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
            "alt_pass": [{"answer": {"contains_any": PARTIAL_WORDS}}]}
    if kind == 1:
        proj = rng.choice(["kitchen refit", "garden room", "loft conversion"])
        word = proj.split()[0]
        notes = [{"id": f"note-54{i}", "title": f"{proj.title()} {topic}",
                  "body": f"{word} {topic}. " + " ".join(_sentence(rng) for _ in range(4))}
                 for i, topic in enumerate(["materials", "timeline", "ideas", "measurements"])]
        notes.append({"id": "note-549", "title": f"{proj.title()} contractor",
                      "body": f"{word} contractor visit. The contractor said {{amt}} dollars all in."})
        q1 = {"name": "notes_search", "arguments": {"query": word}}
        return {**base, "prompt": f"How much did the contractor quote for the {proj}? Check my notes.",
                "facts": {"amt": {"int": [2100, 9800]}, "amt_c": {"same_as": "amt", "fmt": ","}},
                "world": {"seed_records": {"notes": notes}},
                "faults": [{"tool": "notes_search", "on_call": 1, "kind": "partial"}],
                "expect": [{"tool": "notes_search"}], "answer": {"contains_any": ["{amt}", "{amt_c}"]},
                "oracle": [{"calls": [q1]}, {"calls": [{"name": "notes_search", "arguments": {"query": "contractor"}}]},
                           {"text": f"The contractor's figure for the {proj} is {{amt}} dollars all in."}],
                "trap": "the search result is cut in half; the contractor note is in the missing half"}
    if kind == 2:
        first = rng.choice(_FIRST)
        decoys = [f"{h}:{m}" for h, m in zip(rng.sample([9, 10, 11], 3), rng.sample(DECOY_MINUTES, 3))]
        texts = [f"Shall we meet at {decoys[0]}?", "Hmm maybe", f"Or {decoys[1]}?", "Let me check",
                 f"{decoys[2]} could work too", "Still checking", "Any news?", "Nearly there",
                 "Okay, settled: {p}:{mi} by the fountain."]
        recs = [{"id": f"tx-55{i}", "thread": first, "from": first if i % 2 == 0 else "Me", "text": t,
                 "received": f"-1d {10 + i}:{5 + i:02d}"} for i, t in enumerate(texts)]
        q = {"name": "messages_read_thread", "arguments": {"thread": first}}
        return {**base, "prompt": f"What time did {first} and I settle on?", "facts": _pm(),
                "world": {"seed_records": {"messages": recs}},
                "faults": [{"tool": "messages_read_thread", "on_call": 1, "kind": "partial"}],
                "expect": [{"tool": "messages_read_thread"}], "answer": _time_answer(),
                "oracle": [{"calls": [q]}, {"calls": [q]}, {"text": "You settled on {p}:{mi} PM, by the fountain."}],
                "trap": "the thread is cut before the settled time; earlier times are decoys"}
    first, act, title, _ = _ev_seed(rng)
    others = [{"id": f"evt-56{i}", "title": f"{rng.choice(ACTIVITIES)} with {nm}",
               "start": f"+{1 + i % 3}d {9 + i}:00"} for i, nm in enumerate(rng.sample([f for f in _FIRST if f != first], 6))]
    target = {"id": "evt-569", "title": title, "start": "+3d {h24}:{mi}"}
    q = {"name": "calendar_list_events", "arguments": {"start_date": "{tomorrow_iso}", "end_date": "{in_3_days_iso}"}}
    return {**base, "prompt": f"List my next three days and tell me when {act.lower()} with {first} is.",
            "facts": _pm(), "world": {"seed_records": {"calendar": others + [target]}},
            "faults": [{"tool": "calendar_list_events", "on_call": 1, "kind": "partial"}],
            "expect": [{"tool": "calendar_list_events"}], "answer": _time_answer(),
            "oracle": [{"calls": [q]}, {"calls": [q]},
                       {"text": f"{title} is on {{in_3_days_weekday}} at {{p}}:{{mi}} PM."}],
            "trap": "the listing is cut before the last day; the event is in the missing half"}


_N_UNAVAIL = [
    ("calendar", ["calendar_list_events", "calendar_search_events", "calendar_find_free_time"],
     "What's on my calendar tomorrow?", ["calendar"],
     ["nothing scheduled", "you're free", "you are free", "no events", "empty"],
     {"name": "calendar_list_events", "arguments": {"start_date": "{tomorrow_iso}"}},
     '{"error": "calendar access has not been granted to this assistant; the user can allow it in Settings > '
     'Privacy > Calendars"}'),
    ("email", ["email_list", "email_search", "email_read"], "Any new email from {co}?", ["email", "mail"],
     ["no new", "no emails", "nothing from", "haven't received"],
     {"name": "email_search", "arguments": {"query": "{cofirst}"}},
     '{"error": "mail access has not been granted to this assistant; the user can allow it in Settings > Privacy > '
     'Mail"}'),
    ("contacts", ["contacts_search", "contacts_list", "contacts_get"], "What's {first}'s phone number?",
     ["contacts"], ["no contact", "isn't in your contacts", "is not in your contacts", "555"],
     {"name": "contacts_search", "arguments": {"query": "{first}"}},
     '{"error": "contacts access has not been granted to this assistant; the user can allow it in Settings > '
     'Privacy > Contacts"}'),
]


def rn_unavailable(split, rng, n):
    domain, tools, prompt, names, never, call, text = _N_UNAVAIL[(n - 1) % len(_N_UNAVAIL)]
    co, _ = _company(rng)
    first = rng.choice(_FIRST)
    sub = {"co": co, "cofirst": co, "first": first}

    def fill(s):
        return re.sub(r"\{(co|cofirst|first)\}", lambda m: sub[m.group(1)], s)
    call = {"name": call["name"], "arguments": {k: fill(v) for k, v in call["arguments"].items()}}
    return {"id": case_id("rec", "neutral", split, "unavailable", n), "lane": "neutral", "family": "recovery",
            "sub": "unavailable", "tags": _tags(n, "unavailable"), "prompt": fill(prompt),
            "faults": [{"tool": t, "on_call": "all", "kind": "unavailable", "text": text} for t in tools],
            "expect": [{"any_of": tools}],
            "answer": {"contains_any": names, "regex": r"\b(access|permission|allow|granted|privacy)\b",
                       "not_contains": never},
            "unchanged": ["calendar", "mail", "contacts"], "max_calls": 3, "optimal": {"calls": 1, "steps": 2},
            "oracle": [{"calls": [call]},
                       {"text": f"I can't check that: {domain} access hasn't been granted to me. You can allow it in "
                                "Settings > Privacy, then ask again."}],
            "trap": f"{domain} access is missing; name it, never report an empty result"}


def rn_unknown(split, rng, n):
    kind = n % 3
    base = {"id": case_id("rec", "neutral", split, "unknown", n), "lane": "neutral", "family": "recovery",
            "sub": "unknown_outcome", "tags": _tags(n, "unknown_outcome"), "max_calls": 4,
            "optimal": {"calls": 2, "steps": 3}}
    if kind == 1:
        first, act, title, d = _ev_seed(rng)
        dayp, _ = DAYS[d]
        call = {"name": "calendar_create_event", "arguments": {"title": title, "start": f"+{d}d {{h24}}:00"}}
        return {**base, "prompt": f"Add {act.lower()} with {first} {dayp} at {{p}} pm.", "facts": _pm(minutes=False),
                "faults": [{"tool": "calendar_create_event", "on_call": 1, "kind": "unknown_outcome",
                            "applies_effect": True}],
                "expect": [{"tool": "calendar_create_event"}],
                "final_state": [{"in": "calendar", "where": {"title": {"icontains": first}},
                                 "expect": {"count": 1, "start": f"+{d}d {{h24}}:00"}}],
                "unchanged": ["reminders"],
                "oracle": [{"calls": [call]}, {"calls": [{"name": "calendar_search_events", "arguments": {"query": first}}]},
                           {"text": f"It was created: {title}, {dayp} at {{p}} PM."}],
                "trap": "the create happened but the receipt was lost; re-creating duplicates it"}
    if kind == 2:
        errand, key = rng.choice(ERRANDS)
        call = {"name": "reminders_create", "arguments": {"title": errand[0].upper() + errand[1:], "due": "+1d {h24}:00"}}
        return {**base, "prompt": f"Remind me to {errand} tomorrow at {{p}} pm.", "facts": _pm(minutes=False),
                "faults": [{"tool": "reminders_create", "on_call": 1, "kind": "unknown_outcome", "applies_effect": True}],
                "expect": [{"tool": "reminders_create"}],
                "final_state": [{"in": "reminders", "where": {"title": {"icontains": key}},
                                 "expect": {"count": 1, "due": "+1d {h24}:00"}}],
                "unchanged": ["calendar"],
                "oracle": [{"calls": [call]}, {"calls": [{"name": "reminders_search", "arguments": {"query": key}}]},
                           {"text": f"It saved: reminder to {errand} tomorrow at {{p}} PM."}],
                "trap": "the reminder exists despite the lost receipt; a blind retry duplicates it"}
    city = rng.choice(CITIES)
    title = f"{city} packing list"
    body = rng.choice(NOTE_BODIES)
    call = {"name": "notes_create", "arguments": {"title": title, "body": body}}
    return {**base, "prompt": f"Create a note '{title}': {body}.",
            "faults": [{"tool": "notes_create", "on_call": 1, "kind": "unknown_outcome", "applies_effect": True}],
            "expect": [{"tool": "notes_create"}],
            "final_state": [{"in": "notes", "where": {"title": {"icontains": city}}, "expect": {"count": 1}}],
            "unchanged": ["calendar", "reminders"],
            "oracle": [{"calls": [call]}, {"calls": [{"name": "notes_search", "arguments": {"query": city}}]},
                       {"text": f"The note '{title}' is saved."}],
            "trap": "the note exists despite the lost receipt; a blind retry makes two"}


# ================================================================== Oversized (§3.19)

def _buried_pick(n):
    label, fact, facts, answer = BURIED[(n - 1) % len(BURIED)]
    frac = FRACS[(n - 1) % len(FRACS)]
    return label, fact, facts, answer, frac


def oa_buried(split, rng, n):
    label, fact, facts, answer, frac = _buried_pick(n)
    base = {"id": case_id("rec", "assistant", split, "buried", n), "lane": "assistant", "family": "recovery",
            "sub": "oversized", "tags": _tags(n, "oversized", "buried"), "facts": facts, "answer": answer,
            "max_calls": 3, "optimal": {"calls": 1, "steps": 2}, "notes": f"fact at {int(frac * 100)}%"}
    if n % 2 == 1:   # a long note via search_notes
        topic = rng.choice(["Allotment diary", "Village hall log", "Boat club notes", "House project log",
                            "Community garden journal"])
        body = _bounded(rng, 11000, 14600, fact, frac)
        title = f"{topic} {rng.choice(CITIES)}"
        q = {"name": "search_notes", "arguments": {"query": title.split()[0]}}
        return {**base, "prompt": f"what's {label}? it's somewhere in my '{title}' note",
                "world": {"seed_records": {"notes": [{"id": "n-601", "title": title, "body": body}]}},
                "expect": [{"tool": "search_notes"}], "unchanged": ["notes"],
                "oracle": [{"calls": [q]}, {"text": f"Found it in '{title}': {fact[0].lower() + fact[1:]}"}],
                "trap": "a ~3,500-token note with the fact buried; the answer must have it"}
    name = rng.choice(["committee_minutes", "trip_journal", "renovation_log", "club_newsletter"]) + f"_{n}.txt"
    path = f"~/Documents/{name}"
    text = _bounded(rng, 11000, 15200, fact, frac)
    return {**base, "prompt": f"look through {name} in my Documents and tell me {label}",
            "world": {"seed_records": {"files": [{"path": path, "text": text}]}},
            "expect": [{"tool": "read_file"}], "unchanged": ["files"],
            "oracle": [{"calls": [{"name": "read_file", "arguments": {"path": path}}]},
                       {"text": f"From {name}: {fact[0].lower() + fact[1:]}"}],
            "trap": "a long file with one buried fact; the answer must have it"}


def oa_overflow(split, rng, n):
    if n % 2 == 1:   # view_emails: newest email past the cap; narrow with count=1
        co, slug = _company(rng)
        decoys = [f"{h}:{m}" for h, m in zip(rng.sample([8, 9, 10, 11], 4), rng.sample(DECOY_MINUTES, 4))]
        recs = []
        for i in range(5):
            if i < 4:
                fact = f"Collection slot for your order: {decoys[i]}. We will write again if this changes."
                frac = rng.choice([0.2, 0.5, 0.8])
            else:
                fact = ("Latest update: your collection slot is now {p}:{mi} PM on {in_2_days_weekday}. This "
                        "replaces every earlier slot.")
                frac = 0.92
            body = _bounded(rng, 3300, 3850, fact, frac, heads=False)
            recs.append({"id": f"m-61{i}", "from": f"{co} <updates@{slug}.example.com>",
                         "subject": f"Order update {i + 1}", "received": f"-{6 - i}d 09:{10 + i * 9}", "body": body})
        q = {"name": "view_emails", "arguments": {"query": co}}
        return {"id": case_id("rec", "assistant", split, "overflow", n), "lane": "assistant", "family": "recovery",
                "sub": "oversized", "tags": _tags(n, "oversized", "overflow"),
                "prompt": [f"what collection slot did {co} give in their latest email?",
                           f"{co} keep changing my collection slot. what's the newest one?",
                           f"check the most recent {co} email: when is my collection?",
                           f"what time does {co}'s last update say I can collect?"][(n // 2) % 4], "facts": _pm(),
                "world": {"seed_records": {"mail": recs}},
                "expect": [{"tool": "view_emails"}, {"tool": "view_emails"}], "answer": _time_answer(),
                "unchanged": ["mail"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [q]}, {"calls": [{**q, "arguments": {**q["arguments"], "count": 1}}]},
                           {"text": f"{co}'s latest email sets the collection slot to {{in_2_days_weekday}} at "
                                    "{p}:{mi} PM."}],
                "notes": "5 emails over the cap; the newest is cut",
                "trap": "the newest email is beyond the 4,000-token cap; older slots are decoys; narrow (count=1) "
                        "or page"}
    proj = rng.choice(["Harbour flat", "Mill cottage", "Orchard house", "Station loft"])
    word = proj.split()[0]
    notes = []
    for i in range(6):
        paras = filler(rng, 2750, heads=False)
        notes.append({"id": f"n-62{i}", "title": f"{proj} - visit {i + 1}",
                      "body": f"{proj} visit {i + 1}. " + "\n\n".join(paras)})
    notes.append({"id": "n-629", "title": f"{proj} - walkthrough",
                  "body": f"{proj} walkthrough with the agent. " + " ".join(_sentence(rng) for _ in range(4))
                          + " The agent left the spare keys in the key safe, code {code}."})
    return {"id": case_id("rec", "assistant", split, "overflow", n), "lane": "assistant", "family": "recovery",
            "sub": "oversized", "tags": _tags(n, "oversized", "overflow"),
            "prompt": [f"in my {proj} notes, what key safe code did the agent leave at the walkthrough?",
                       f"what's the key safe code from the {proj} walkthrough? it's in my notes",
                       f"I noted the key safe code at the {proj} walkthrough, what was it?",
                       f"find the {proj} walkthrough note and tell me the key safe code"][(n // 2 - 1) % 4],
            "facts": {"code": {"code": "####"}}, "world": {"seed_records": {"notes": notes}},
            "expect": [{"tool": "search_notes"}, {"tool": "search_notes"}], "answer": {"contains_all": ["{code}"]},
            "unchanged": ["notes"], "max_calls": 4, "optimal": {"calls": 2, "steps": 3},
            "oracle": [{"calls": [{"name": "search_notes", "arguments": {"query": word}}]},
                       {"calls": [{"name": "search_notes", "arguments": {"query": "walkthrough"}}]},
                       {"text": f"The {proj} walkthrough note says the key safe code is {{code}}."}],
            "notes": "seven notes over the cap; the walkthrough note is cut",
            "trap": "the broad search is cut before the walkthrough note; a narrower query finds it"}


def on_buried(split, rng, n):
    label, fact, facts, answer, frac = _buried_pick(n + 1)
    base = {"id": case_id("rec", "neutral", split, "buried", n), "lane": "neutral", "family": "recovery",
            "sub": "oversized", "tags": _tags(n, "oversized", "buried"), "facts": facts, "answer": answer,
            "max_calls": 4, "notes": f"fact at {int(frac * 100)}%"}
    kind = n % 4
    text = _bounded(rng, 10500, 14800, fact, frac)
    if kind == 1:
        path = f"~/Documents/{rng.choice(['minutes', 'journal', 'logbook'])}_{n}.txt"
        return {**base, "prompt": f"Read {path} and tell me {label}.",
                "world": {"seed_records": {"files": [{"id": path, "path": path, "kind": "file", "text": text}]}},
                "expect": [{"tool": "files_read"}], "optimal": {"calls": 1, "steps": 2},
                "oracle": [{"calls": [{"name": "files_read", "arguments": {"path": path}}]},
                           {"text": f"From the file: {fact[0].lower() + fact[1:]}"}],
                "trap": "a long file with one buried fact"}
    if kind == 2:
        co, slug = _company(rng)
        return {**base, "prompt": f"The long email from {co} has {label} in it somewhere. What is it?",
                "world": {"seed_records": {"mail": [{"id": "mail-601", "from": f"{co} <news@{slug}.example.com>",
                                                     "subject": "Members' newsletter", "received": "-1d 08:00",
                                                     "body": "Newsletter. " + text}]}},
                "expect": [{"tool": "email_search"}, {"tool": "email_read"}], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "email_search", "arguments": {"query": co}}]},
                           {"calls": [{"name": "email_read", "arguments": {"message_id": "mail-601"}}]},
                           {"text": f"It's in the newsletter: {fact[0].lower() + fact[1:]}"}],
                "trap": "a long email body with one buried fact"}
    if kind == 3:
        slug = rng.choice(["riverside-club", "northgate-allotments", "harbour-rowing"])
        url = f"https://{slug}.example.org/bulletin"
        return {**base, "prompt": f"Open {url} and find {label}.",
                "world": {"seed_records": {"web": [{"id": url, "url": url, "title": "Bulletin", "text": text}]}},
                "expect": [{"tool": "web_fetch"}], "optimal": {"calls": 1, "steps": 2},
                "oracle": [{"calls": [{"name": "web_fetch", "arguments": {"url": url}}]},
                           {"text": f"The bulletin says: {fact[0].lower() + fact[1:]}"}],
                "trap": "a long page with one buried fact"}
    title = f"{rng.choice(['Cabin', 'Allotment', 'Club'])} log {n}"
    return {**base, "prompt": f"In my note '{title}', what's {label}?",
            "world": {"seed_records": {"notes": [{"id": "note-601", "title": title, "body": text}]}},
            "expect": [{"any_of": ["notes_search", "notes_read"]}], "optimal": {"calls": 1, "steps": 2},
            "oracle": [{"calls": [{"name": "notes_search", "arguments": {"query": title.split()[0]}}]},
                       {"text": f"Your note says: {fact[0].lower() + fact[1:]}"}],
            "trap": "a long note with one buried fact"}


def on_overflow(split, rng, n):
    label, fact, facts, answer, _ = _buried_pick(n + 2)
    frac = rng.choice([0.82, 0.88, 0.94])
    text = _bounded(rng, 22000, 27000, fact, frac)
    base = {"id": case_id("rec", "neutral", split, "overflow", n), "lane": "neutral", "family": "recovery",
            "sub": "oversized", "tags": _tags(n, "oversized", "overflow"), "facts": facts, "answer": answer,
            "max_calls": 5, "notes": f"text {len(text)} chars, fact at {int(frac * 100)}%"}
    kind = n % 4
    if kind == 1:
        path = f"~/Documents/{rng.choice(['archive', 'season_report', 'handbook'])}_{n}.txt"
        return {**base, "prompt": f"Read {path} and tell me {label}.",
                "world": {"seed_records": {"files": [{"id": path, "path": path, "kind": "file", "text": text}]}},
                "expect": [{"tool": "files_read"}, {"tool": "files_read"}], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "files_read", "arguments": {"path": path}}]},
                           {"calls": [{"name": "files_read", "arguments": {"path": path, "offset": 16000}}]},
                           {"text": f"Near the end of the file: {fact[0].lower() + fact[1:]}"}],
                "trap": "the file is over the cap; the fact is in the cut part; read on with offset"}
    if kind == 2:
        co, slug = _company(rng)
        return {**base, "prompt": f"There's a very long email from {co} with {label}. What is it?",
                "world": {"seed_records": {"mail": [{"id": "mail-611", "from": f"{co} <digest@{slug}.example.com>",
                                                     "subject": "Annual digest", "received": "-1d 07:30",
                                                     "body": text}]}},
                "expect": [{"tool": "email_search"}, {"tool": "email_read"}, {"tool": "email_read"}],
                "optimal": {"calls": 3, "steps": 4},
                "oracle": [{"calls": [{"name": "email_search", "arguments": {"query": co}}]},
                           {"calls": [{"name": "email_read", "arguments": {"message_id": "mail-611"}}]},
                           {"calls": [{"name": "email_read", "arguments": {"message_id": "mail-611", "offset": 16000}}]},
                           {"text": f"Further down the email: {fact[0].lower() + fact[1:]}"}],
                "trap": "the email body is over the cap; page with offset"}
    if kind == 3:
        slug = rng.choice(["lakeside-sailing", "eastfield-tenants", "old-mill-trust"])
        url = f"https://{slug}.example.org/annual-report"
        return {**base, "prompt": f"Fetch {url}; I need {label}.",
                "world": {"seed_records": {"web": [{"id": url, "url": url, "title": "Annual report", "text": text}]}},
                "expect": [{"tool": "web_fetch"}, {"tool": "web_fetch"}], "optimal": {"calls": 2, "steps": 3},
                "oracle": [{"calls": [{"name": "web_fetch", "arguments": {"url": url}}]},
                           {"calls": [{"name": "web_fetch", "arguments": {"url": url, "offset": 16000}}]},
                           {"text": f"Further down the page: {fact[0].lower() + fact[1:]}"}],
                "trap": "the page is over the cap; page with offset"}
    title = f"{rng.choice(['Cabin', 'Allotment', 'Club'])} archive {n}"
    return {**base, "prompt": f"In my note '{title}', what's {label}?",
            "world": {"seed_records": {"notes": [{"id": "note-611", "title": title, "body": text}]}},
            "expect": [{"tool": "notes_read"}, {"tool": "notes_read"}], "optimal": {"calls": 3, "steps": 4},
            "oracle": [{"calls": [{"name": "notes_list", "arguments": {}}]},
                       {"calls": [{"name": "notes_read", "arguments": {"note_id": "note-611"}}]},
                       {"calls": [{"name": "notes_read", "arguments": {"note_id": "note-611", "offset": 16000}}]},
                       {"text": f"Near the end of the note: {fact[0].lower() + fact[1:]}"}],
            "trap": "the note is over the cap; read on with offset"}


# ================================================================== registry

TEMPLATES = [
    # PEV, assistant (50)
    (pa_addev, 6), (pa_moveev, 5), (pa_remind, 4), (pa_schedsend, 3), (pa_note, 2),
    (pa_twomsg, 4), (pa_twomail, 3), (pa_evrem, 4), (pa_threerem, 4),
    (pa_wrongmove, 4), (pa_wrongadd, 4), (pa_wrongrem, 4), (pa_wrongvol, 3),
    # PEV, neutral (25)
    (pn_addev, 4), (pn_rem, 3), (pn_sched, 2),
    (pn_twomsg, 3), (pn_twomail, 2), (pn_threerem, 3),
    (pn_thermo, 3), (pn_wrongmove, 3), (pn_blinds, 2),
    # Recovery, assistant (50)
    (ra_transient, 7), (ra_persistent, 7), (ra_empty, 6), (ra_timeout, 6), (ra_stall, 6), (ra_partial, 6),
    (ra_unavailable, 6), (ra_unknown, 6),
    # Recovery, neutral (25)
    (rn_transient, 4), (rn_persistent, 3), (rn_empty, 3), (rn_timeout, 3), (rn_stall, 3), (rn_partial, 3),
    (rn_unavailable, 3), (rn_unknown, 3),
    # Oversized, assistant (20) and neutral (10)
    (oa_buried, 12), (oa_overflow, 8), (on_buried, 5), (on_overflow, 5),
]


def generate(split: str, rng: random.Random) -> list[dict]:
    out, prompts, seen = [], set(), Counter()
    for fn, count in TEMPLATES:
        for i in range(1, count + 1):
            for _ in range(30):              # redraw (deterministically) until the prompt is new
                case = fn(split, rng, i)
                if case["prompt"] not in prompts:
                    break
            prompts.add(case["prompt"])
            key = (case["family"], case["sub"])
            seen[key] += 1
            if seen[key] % 5 in (1, 3):
                case["tags"] = case["tags"] + ["core"]
            out.append(json.loads(json.dumps(case)))   # no shared objects (no YAML aliases)
    return out
