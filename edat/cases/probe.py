"""The scaling probe (spec 02 §3.10, build item B7 subset): run conditions over existing cases.

    ps = probe_set(cases, random.Random("probe|20261118"))      # 80 Select/Parallel/Sequential per lane
    for cond in CONDITIONS:                                      # menu size M and history H
        out = run_probe(case, client, cond, trial)               # out.row feeds `robustness`

Conditions
----------
* Menu sizes M in {5, 10, 25, 50, 100} full schemas at no extra history. The needed tools are
  always offered (`select_menu(..., size=M)` keeps them and caps the menu at the lane's tool
  count), so a case that needs more than M tools offers its needed tools alone; probe cases are
  chosen with at most 5 needed tools so M=5 is a real 5-tool menu.
* History H in {0, 2k, 8k, 16k} tokens of earlier, unrelated turns (`history_padding`), run at
  M=10 (`HISTORY_MENU`). The H=0 baseline is therefore the M=10 condition, `CONDITIONS[1]`.
* One quarter of the probe cases put a needed fact (a contact's name or a city) in the earliest
  padded turn instead of the prompt (`history_fact_case`); that turn is present even at H=0.
  Only cases where that rewrite is clean are used; the rest of the quarter is reported in
  `ProbeSet.warnings` as a shortfall, never faked.
* A model whose window cannot hold menu + prompt + the output reserve is recorded as
  `not run (context)` at that condition (`fits`), never as a failure.
* Prompt tokens are estimated at every condition (4 characters per token, as the loop does) and
  reported in the row.

The Discovery condition (12 preloaded schemas, the rest as name-only stubs, plus a
`get_tool_schemas` tool) is NOT built here; see `run_discovery`.
"""
from __future__ import annotations

import copy
import json
import random
import re
from dataclasses import dataclass, field

from edat.cases.bind import bind
from edat.cases.generators.pools import CITIES
from edat.cases.menu import needed_tools, select_menu
from edat.cases.runner import WORLD_SEED_BASE, default_env_factory, run_trial
from edat.cases.schema import normalise
from edat.cases.variants import _quotas, _round_robin, single_turn
from edat.loop import CHARS_PER_TOKEN

PROBE_FAMILIES = ("select", "parallel", "sequential")
PROBE_SEED = "probe|20261118"
MENU_SIZES = (5, 10, 25, 50, 100)
HISTORY_TOKENS = (0, 2000, 8000, 16000)
HISTORY_MENU = 10
OUTPUT_RESERVE = 2048
MAX_NEEDED = 5
NOT_RUN_CONTEXT = "not run (context)"
FACT_SHARE = 0.25


@dataclass(frozen=True)
class Condition:
    name: str
    menu_size: int
    history_tokens: int


CONDITIONS: tuple[Condition, ...] = tuple(
    [Condition(f"M{m}", m, 0) for m in MENU_SIZES]
    + [Condition(f"H{h // 1000}k", HISTORY_MENU, h) for h in HISTORY_TOKENS if h])


# ---------------------------------------------------------------------------- the probe set


def estimate_tokens(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False)) // CHARS_PER_TOKEN


def needed_in_lane(case: dict) -> int:
    return len(needed_tools(normalise(case)))


# Facts that can be moved out of the prompt cleanly: a contact's first name, or a city.
_REF = {"contact": "the person I mentioned earlier", "city": "the city I mentioned earlier"}


def history_fact_case(case: dict) -> tuple[dict, dict] | None:
    """Rewrite `case` so a needed fact is stated in the earliest padded turn, not in the prompt.

    Returns (new_case, first_turn) where first_turn is {"user", "assistant"} text that may hold
    `{fact}` placeholders (resolved per trial by `run_probe`), or None when the rewrite is not
    clean: more than one place to rewrite, a possessive, a name that is also another contact's
    first name, or a prompt that does not name a contact or a city.
    """
    if not single_turn(case):
        return None
    prompt = case["prompt"]
    contacts = [c for c in ((case.get("world") or {}).get("seed_records") or {}).get("contacts") or []
                if isinstance(c, dict) and c.get("name")]
    hits = []
    for c in contacts:
        first = str(c["name"]).split()[0]
        ms = list(re.finditer(rf"(?<![\w'’]){re.escape(first)}(?![\w'’])", prompt))
        if len(ms) == 1 and sum(str(o["name"]).split()[0] == first for o in contacts) == 1 \
                and not re.search(rf"{re.escape(first)}['’]s", prompt):
            hits.append(("contact", ms[0], str(c["name"])))
    facts = case.get("facts") or {}
    for name, spec in facts.items():
        opts = spec.get("choice") if isinstance(spec, dict) else None
        if opts and all(o in CITIES for o in opts) and len(re.findall(rf"\{{{name}\}}", prompt)) == 1:
            m = re.search(rf"\{{{name}\}}", prompt)
            hits.append(("city", m, "{" + name + "}"))
    for city in CITIES:
        ms = list(re.finditer(rf"(?<![\w]){re.escape(city)}(?![\w])", prompt))
        if len(ms) == 1:
            hits.append(("city", ms[0], city))
    if len(hits) != 1:
        return None
    kind, m, value = hits[0]
    new_prompt = prompt[:m.start()] + _REF[kind] + prompt[m.end():]
    if kind == "contact":
        turn = {"user": f"Quick heads-up for later today: the person I will need to get in touch with is {value}.",
                "assistant": f"Got it, I will remember that it is {value}."}
    else:
        turn = {"user": f"For later: the city I am planning around is {value}.",
                "assistant": f"Noted, the city is {value}."}
    new = copy.deepcopy(case)
    new["id"] = f"{case['id']}__hist"
    new["prompt"] = new_prompt
    new["variant_of"] = case["id"]
    new["variant"] = "history_fact"
    new["notes"] = "; ".join(x for x in (case.get("notes"), f"history-fact probe: the {kind} is stated "
                                         "in the first padded turn") if x)
    new.pop("pair_id", None)
    new["tags"] = [t for t in (case.get("tags") or []) if t != "core"]
    return new, turn


@dataclass
class ProbeSet:
    by_lane: dict[str, list[dict]] = field(default_factory=dict)
    fact_turns: dict[str, dict] = field(default_factory=dict)    # case id -> first padded turn
    seed: str = PROBE_SEED
    warnings: list[str] = field(default_factory=list)

    def cases(self, lane: str | None = None) -> list[dict]:
        if lane:
            return list(self.by_lane.get(lane, []))
        return [c for lane_cases in self.by_lane.values() for c in lane_cases]


def probe_set(cases: list[dict], rng: random.Random | None = None, n: int = 80, *,
              seed: str | None = None, fact_share: float = FACT_SHARE) -> ProbeSet:
    """n cases per lane from Select, Parallel and Sequential (single-turn), stratified by family.

    A quarter put a needed fact in the earliest padded turn (rewritten via `history_fact_case`,
    new id `<id>__hist`). `rng` defaults to `random.Random(seed or PROBE_SEED)`; the seed is
    recorded on the result.
    """
    seed = seed if seed is not None else PROBE_SEED
    rng = rng or random.Random(seed)
    out = ProbeSet(seed=seed)
    for lane in ("assistant", "neutral"):
        pool = [c for c in sorted(cases, key=lambda c: c["id"])
                if c.get("lane") == lane and c.get("family") in PROBE_FAMILIES and single_turn(c)
                and not c.get("menu_pin") and not c.get("variant_of")]
        need_of = {c["id"]: needed_in_lane(c) for c in pool}
        small = lambda c: need_of[c["id"]] <= MAX_NEEDED  # noqa: E731
        want_fact = round(n * fact_share)

        eligible = [(c, r) for c in pool if (r := history_fact_case(c))]
        fam_avail = {f: sum(1 for c, _ in eligible if c["family"] == f) for f in PROBE_FAMILIES}
        fq = _quotas(want_fact, fam_avail)
        chosen_fact: list[tuple[dict, tuple]] = []
        for f in PROBE_FAMILIES:
            sub = [(c, r) for c, r in eligible if c["family"] == f]
            by_id = {c["id"]: (c, r) for c, r in sub}
            picked = _round_robin([c for c, _ in sub], fq[f], rng, prefer=small)
            chosen_fact.extend(by_id[c["id"]] for c in picked)
        if len(chosen_fact) < want_fact:
            out.warnings.append(f"{lane}: only {len(chosen_fact)} of {want_fact} history-fact cases are "
                                "clean to build; the rest are plain probe cases")
        used = {c["id"] for c, _ in chosen_fact}
        rest = [c for c in pool if c["id"] not in used]
        rq = _quotas(n - len(chosen_fact), {f: sum(1 for c in rest if c["family"] == f) for f in PROBE_FAMILIES})
        plain: list[dict] = []
        for f in PROBE_FAMILIES:
            plain.extend(_round_robin([c for c in rest if c["family"] == f], rq[f], rng, prefer=small))
        if len(plain) + len(chosen_fact) < n:
            out.warnings.append(f"{lane}: only {len(plain) + len(chosen_fact)} probe cases available (wanted {n})")
        if any(not small(c) for c in plain):
            out.warnings.append(f"{lane}: {sum(not small(c) for c in plain)} probe cases need more than "
                                f"{MAX_NEEDED} tools, so their M=5 menu is larger than 5")
        lane_cases = list(plain)
        for c, (new, turn) in chosen_fact:
            lane_cases.append(new)
            out.fact_turns[new["id"]] = turn
        lane_cases.sort(key=lambda c: c["id"])
        out.by_lane[lane] = lane_cases
    return out


# ---------------------------------------------------------------------------- menu checks


def menu_for(case: dict, size: int, world_seed: int = 1, *, env_factory=default_env_factory) -> list[str]:
    bound = bind(case, world_seed)
    env = env_factory(bound["lane"], bound["_clock"], world_seed)
    return select_menu(bound, env, world_seed, size=size)


def check_menu(case: dict, size: int, world_seed: int = 1, *, env_factory=default_env_factory) -> list[str]:
    """Problems with the menu `select_menu` gives at `size` (empty when fine): every needed tool
    present, no duplicates, and length min(max(size, needed), the lane's tool count)."""
    bound = bind(case, world_seed)
    env = env_factory(bound["lane"], bound["_clock"], world_seed)
    menu = select_menu(bound, env, world_seed, size=size)
    lane_tools = env.tool_names()
    need = [n for n in needed_tools(bound) if n in lane_tools]
    errs = []
    if len(set(menu)) != len(menu):
        errs.append("duplicate tools in the menu")
    errs += [f"needed tool {n} missing at M={size}" for n in need if n not in menu]
    want = min(max(size, len(need)), len(lane_tools))
    if not bound.get("menu_pin") and len(menu) != want:
        errs.append(f"menu has {len(menu)} tools, expected {want}")
    return errs


# ---------------------------------------------------------------------------- history padding

_TOPICS = ["sourdough starter", "tide tables", "a balcony herb box", "board game night", "bicycle tyres",
           "the night train timetable", "tax receipts", "houseplant watering", "a jigsaw puzzle", "winter boots",
           "a kite festival", "a coffee grinder", "the allotment waiting list", "choir rehearsal", "a pottery class",
           "fridge freezer manuals", "a hiking route", "a second-hand piano", "lantern repair", "a seed swap"]
_PLACES = ["Tallinn", "Cork", "Graz", "Bergen", "Dunedin", "Turku", "Seville", "Antwerp", "Zadar", "Halifax"]
_ITEMS = ["tin of paint", "spare key", "birthday card", "bag of compost", "power bank", "yoga mat", "phone case",
          "glue stick", "garden twine", "lamp bulb", "picture hooks", "tea towels", "batteries", "plant pots"]
_VERBS = ["Water", "Order", "Return", "Sort", "Label", "Check", "Wipe", "Fold", "Pack", "Update", "Print", "Sharpen"]
_FILES = ["lantern-notes", "seed-list", "route-draft", "puzzle-scores", "piano-quotes", "tide-chart", "boots-compare",
          "choir-setlist", "pottery-glaze", "manual-scan"]
_SENDERS = ["Fernhill Allotments", "Quayside Market", "Juniper Hall", "Harbourside Gym", "Northgate Library",
            "Maple Row Garage", "Riverside Climbing Wall", "Tidewater Dental"]
_SUBJECTS = ["Your order has shipped", "Rehearsal moved to Thursday", "Receipt for your purchase", "Newsletter: autumn events",
             "Booking confirmed", "Reminder: class tomorrow", "Opening hours this week", "Your parcel is ready"]
_CONDITIONS = ["sunny", "light rain", "cloudy", "fog", "windy", "clear", "showers", "overcast"]


def _pick(rng, pool, k):
    return [rng.choice(pool) for _ in range(k)]


def _day(rng, i):
    return f"2026-11-{19 + i:02d}"


def _mk(user, calls, reply):
    return {"user": user, "calls": calls, "reply": reply}


# --- assistant lane: Harbor's tools return plain text
def _a_weather(rng, rows):
    place = rng.choice(_PLACES)
    lines = [f"- {_day(rng, i)}: {rng.choice(_CONDITIONS)}, high {rng.randint(2, 14)}C, low {rng.randint(-5, 3)}C, "
             f"wind {rng.randint(5, 30)} km/h" for i in range(max(1, min(rows, 7)))]
    return _mk(f"what's the weather like in {place} this week", [("get_weather", {"location": place}, f"Forecast for {place}:\n" + "\n".join(lines))],
               f"It looks {rng.choice(_CONDITIONS)} in {place} for most of the week, with highs around {rng.randint(5, 12)}C.")


def _a_notes(rng, rows):
    t = rng.choice(_TOPICS)
    lines = [f"- {rng.choice(_VERBS)} {rng.choice(_ITEMS)}; ask about {rng.choice(_TOPICS)} ({rng.randint(1, 28)} Oct)" for _ in range(rows)]
    return _mk(f"search my notes for {t}", [("search_notes", {"query": t.split()[-1]}, "\n".join(lines))],
               f"I found {rows} notes that mention it; the newest is about {rng.choice(_TOPICS)}.")


def _a_upcoming(rng, rows):
    days = []
    for i in range(max(1, min(rows // 2 + 1, 6))):
        ev = "\n".join(f"- **{rng.randint(8, 18)}:{rng.choice(['00', '15', '30', '45'])}** — {rng.choice(_TOPICS).capitalize()} · {rng.choice(_SENDERS)}"
                       for _ in range(rng.randint(1, 3)))
        days.append(f"**{['Thu', 'Fri', 'Sat', 'Sun', 'Mon', 'Tue'][i]} · Nov {19 + i}**\n{ev}")
    return _mk("what's on over the next few days", [("get_upcoming", {"days": 7}, f"Next 7 days: {rows} events\n\n" + "\n\n".join(days))],
               "You have a fairly light week; the busiest day is Friday.")


def _a_web(rng, rows):
    q = rng.choice(_TOPICS)
    lines = [f"{i + 1}. {rng.choice(_TOPICS).capitalize()} — a short guide for beginners. https://example.org/{rng.choice(_FILES)}-{i}" for i in range(rows)]
    return _mk(f"look up {q}", [("web_search", {"query": q}, "\n".join(lines))],
               f"The top result is a beginner's guide to {q}; the rest are shorter how-tos.")


def _a_files(rng, rows):
    q = rng.choice(_FILES).split("-")[0]
    lines = [f"~/Documents/{rng.choice(_FILES)}-{rng.randint(1, 40)}.{rng.choice(['pdf', 'txt', 'md'])}" for _ in range(rows)]
    return _mk(f"find files about {q}", [("find_files", {"query": q}, "\n".join(lines))],
               f"I found {rows} files in Documents that match.")


def _a_emails(rng, rows):
    blocks = [f"From: {rng.choice(_SENDERS)} <news@example.com>\nSubject: {rng.choice(_SUBJECTS)}\nDate: Nov {rng.randint(1, 17)}, 2026\nMessage-ID: m-{rng.randint(1000, 9999)}"
              for _ in range(max(1, min(rows, 8)))]
    return _mk("anything new in my inbox", [("view_emails", {"count": len(blocks)}, "\n\n".join(blocks))],
               "Mostly shop and club mail; nothing looks urgent.")


def _a_small(rng, rows):
    kind = rng.randint(0, 2)
    if kind == 0:
        a, b = rng.randint(12, 90), rng.randint(3, 19)
        return _mk(f"what is {a} times {b}", [("calculate", {"expression": f"{a}*{b}"}, str(a * b))], f"That's {a * b}.")
    if kind == 1:
        v = rng.randint(2, 40)
        return _mk(f"convert {v} km to miles", [("convert_units", {"value": v, "from_unit": "km", "to_unit": "mi"}, f"{v} km = {v * 0.621371:.4f} mi")],
                   f"{v} km is about {v * 0.621371:.1f} miles.")
    place = rng.choice(["Tokyo", "Lisbon", "Sydney", "Cairo"])
    return _mk(f"what time is it in {place}", [("world_time", {"place": place}, f"In {place} it's {rng.randint(1, 12)}:{rng.choice(['05', '30', '45'])} {rng.choice(['AM', 'PM'])} today.")],
               "That's the local time there right now.")


ASSISTANT_PADDING = [_a_weather, _a_notes, _a_upcoming, _a_web, _a_files, _a_emails, _a_small]


# --- neutral lane: JSON results
def _j(d) -> str:
    return json.dumps(d, sort_keys=True)


def _n_weather(rng, rows):
    place = rng.choice(_PLACES)
    days = [{"condition": rng.choice(_CONDITIONS), "date": _day(rng, i), "high_c": rng.randint(2, 14), "humidity_pct": rng.randint(40, 90),
             "location": place, "low_c": rng.randint(-5, 3), "precip_chance_pct": rng.choice([0, 5, 20, 60]), "wind_kph": rng.randint(5, 30)}
            for i in range(max(1, min(rows, 7)))]
    return _mk(f"What's the forecast for {place}?", [("weather_get_forecast", {"location": place, "days": len(days)}, _j({"days": days}))],
               f"{place} looks {days[0]['condition']} to start, then {days[-1]['condition']}.")


def _n_events(rng, rows):
    items = [{"id": f"ev-{rng.randint(1000, 9999)}", "title": rng.choice(_TOPICS).capitalize(), "start": f"{_day(rng, i % 6)}T{rng.randint(8, 18):02d}:{rng.choice(['00', '30'])}",
              "location": rng.choice(_SENDERS)} for i in range(rows)]
    return _mk("What's on my calendar this week?", [("calendar_list_events", {"start_date": "2026-11-19"}, _j({"items": items, "next_offset": None, "total": rows}))],
               f"You have {rows} things on, starting with {items[0]['title'].lower()}.")


def _n_notes(rng, rows):
    t = rng.choice(_TOPICS)
    items = [{"id": f"n-{rng.randint(100, 999)}", "title": rng.choice(_TOPICS).capitalize(), "snippet": f"{rng.choice(_VERBS)} {rng.choice(_ITEMS)} before the weekend"} for _ in range(rows)]
    return _mk(f"Search my notes for {t}.", [("notes_search", {"query": t.split()[-1]}, _j({"items": items, "next_offset": None, "total": rows}))],
               f"There are {rows} matching notes; the first is {items[0]['title'].lower()}.")


def _n_web(rng, rows):
    q = rng.choice(_TOPICS)
    items = [{"title": f"{rng.choice(_TOPICS).capitalize()}: a short guide", "url": f"https://example.org/{rng.choice(_FILES)}-{i}",
              "snippet": f"Everything to know about {rng.choice(_TOPICS)} in five minutes."} for i in range(rows)]
    return _mk(f"Search the web for {q}.", [("web_search", {"query": q}, _j({"items": items, "next_offset": None, "total": rows}))],
               f"The first hit is a short guide to {q}.")


def _n_places(rng, rows):
    q = rng.choice(["bakery", "pharmacy", "hardware shop", "cafe"])
    items = [{"address": f"{rng.randint(10, 900)} {rng.choice(['Lake', 'Maple', 'Harbour', 'Mill'])} Street", "distance_km": round(rng.uniform(0.2, 9), 1),
              "id": f"pl-{rng.randint(10000, 99999)}", "name": f"{rng.choice(['Green', 'Blue', 'Old', 'Little'])} {rng.choice(['Pine', 'Oak', 'Kettle', 'Anchor'])} {q.split()[0].capitalize()}",
              "open_now": rng.random() < 0.6, "rating": round(rng.uniform(3.2, 4.9), 1)} for _ in range(rows)]
    return _mk(f"Find a {q} near me.", [("maps_search_places", {"query": q}, _j({"items": items, "next_offset": None, "total": rows}))],
               f"The closest is {items[0]['name']}, {items[0]['distance_km']} km away.")


def _n_files(rng, rows):
    items = [{"name": f"{rng.choice(_FILES)}-{rng.randint(1, 40)}.{rng.choice(['pdf', 'txt', 'md'])}", "size_kb": rng.randint(2, 900), "modified": f"2026-10-{rng.randint(1, 28):02d}"}
             for _ in range(rows)]
    return _mk("List the files in my Documents folder.", [("files_list", {"folder": "Documents"}, _j({"items": items, "next_offset": None, "total": rows}))],
               f"Documents holds {rows} files; the newest changed in late October.")


def _n_email(rng, rows):
    items = [{"id": f"m-{rng.randint(1000, 9999)}", "from": f"news@{rng.choice(['fernhill', 'quayside', 'juniper'])}.example.com",
              "subject": rng.choice(_SUBJECTS), "date": f"2026-11-{rng.randint(1, 17):02d}"} for _ in range(rows)]
    return _mk("Check my recent email.", [("email_list", {"folder": "inbox", "limit": rows}, _j({"items": items, "next_offset": None, "total": rows}))],
               "Mostly club and shop mail; nothing urgent.")


def _n_small(rng, rows):
    kind = rng.randint(0, 2)
    if kind == 0:
        a, b = rng.randint(12, 90), rng.randint(3, 19)
        return _mk(f"What is {a} times {b}?", [("math_calculate", {"expression": f"{a}*{b}"}, _j({"expression": f"{a}*{b}", "result": a * b}))], f"It comes to {a * b}.")
    if kind == 1:
        v = rng.randint(2, 40)
        return _mk(f"Convert {v} km to miles.", [("units_convert", {"value": v, "from_unit": "km", "to_unit": "mi"},
                                                  _j({"from_unit": "km", "result": round(v * 0.621371, 6), "to_unit": "mi", "value": v}))],
                   f"{v} km is about {v * 0.621371:.1f} miles.")
    tz = rng.choice(["Asia/Tokyo", "Europe/Lisbon", "Australia/Sydney", "Africa/Cairo"])
    return _mk(f"What time is it in {tz.split('/')[1]}?", [("time_get_current", {"timezone": tz}, _j({"now": f"2026-11-19T{rng.randint(0, 23):02d}:{rng.choice(['05', '30', '45'])}", "timezone": tz, "weekday": "Thursday"}))],
               "That's the local time there.")


NEUTRAL_PADDING = [_n_weather, _n_events, _n_notes, _n_web, _n_places, _n_files, _n_email, _n_small]
PADDING = {"assistant": ASSISTANT_PADDING, "neutral": NEUTRAL_PADDING}


def padding_tool_names(lane: str) -> set[str]:
    """Every tool the padding for `lane` can call (tests check they exist in the lane)."""
    names = set()
    for fn in PADDING[lane]:
        for seed in range(60):
            names.update(c[0] for c in fn(random.Random(seed), 3)["calls"])
    return names


def _messages(turn: dict, n: int) -> list[dict]:
    msgs = [{"role": "user", "content": turn["user"]}]
    calls = [{"id": f"call_pad_{n}_{i}", "type": "function",
              "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args, _) in enumerate(turn["calls"])]
    msgs.append({"role": "assistant", "content": "", "tool_calls": calls})
    for c, (name, _, result) in zip(calls, turn["calls"]):
        msgs.append({"role": "tool", "tool_call_id": c["id"], "name": name, "content": result})
    msgs.append({"role": "assistant", "content": turn["reply"]})
    return msgs


def history_padding(tokens: int, rng: random.Random, lane: str, *, first_turn: dict | None = None,
                    avoid: tuple[str, ...] | set[str] = ()) -> list[dict]:
    """Earlier, unrelated turns worth about `tokens` tokens: user asks, the assistant calls a tool of
    the lane, the tool answers with invented results, the assistant replies. `first_turn`
    ({"user", "assistant"}) is placed first (a needed fact); it is kept even at tokens=0.
    Nothing in the padding contains a word in `avoid`."""
    out: list[dict] = []
    if first_turn:
        out += [{"role": "user", "content": first_turn["user"]}, {"role": "assistant", "content": first_turn["assistant"]}]
    if tokens <= 0:
        return out
    avoid_l = [a.casefold() for a in avoid if a]
    fns = PADDING[lane]
    total = estimate_tokens(out)
    n = 0
    while total < tokens - 60:
        remaining = tokens - total
        rows = 1 if remaining < 220 else min(14, max(2, remaining // 70))
        rows = rng.randint(max(1, rows // 2), rows) if remaining > 1500 else rows
        for _ in range(25):
            turn = rng.choice(fns)(rng, rows)
            blob = json.dumps(turn).casefold()
            if not any(a in blob for a in avoid_l):
                break
        else:
            raise ValueError("could not build padding that avoids the given words")
        msgs = _messages(turn, n)
        size = estimate_tokens(msgs)
        if total + size > tokens + 150 and rows > 1:
            continue                      # too big for the room left: draw a smaller one
        out += msgs
        total += size
        n += 1
    return out


# ---------------------------------------------------------------------------- context fit


def fits(context_window: int | None, prompt_tokens: int, output_reserve: int = OUTPUT_RESERVE) -> bool:
    """True when the prompt (menu + system + history + user) and the output reserve fit the window.
    A window of None (unknown or unbounded) always fits. False means `not run (context)`."""
    return context_window is None or prompt_tokens + output_reserve <= context_window


def _avoid_words(bound: dict, case: dict) -> set[str]:
    words: set[str] = set()
    for c in ((bound.get("world") or {}).get("seed_records") or {}).get("contacts") or []:
        words.update(re.findall(r"[A-Za-z]{3,}", str(c.get("name", ""))))
    for v in (bound.get("_facts") or {}).values():
        words.update(re.findall(r"[A-Za-z]{3,}", str(v)))
    for s in bound["sessions"]:
        for t in s["turns"]:
            words.update(re.findall(r"\b[A-Z][a-z]{2,}", t["user"]))
    return {w for w in words if w.lower() not in {"the", "and", "for", "please", "text", "call"}}


def _resolve_turn(turn: dict | None, facts: dict) -> dict | None:
    if not turn:
        return None
    return {k: re.sub(r"\{([a-z_][a-z0-9_]*)\}", lambda m: str(facts.get(m.group(1), m.group(0))), v) for k, v in turn.items()}


def prompt_token_estimate(case: dict, condition: Condition, trial: int, *, fact_turn: dict | None = None,
                          env_factory=default_env_factory) -> tuple[int, list[dict], list[str]]:
    """(estimated prompt tokens, the history messages, the menu) a trial at this condition would send."""
    ws = WORLD_SEED_BASE + trial - 1
    bound = bind(case, ws)
    env = env_factory(bound["lane"], bound["_clock"], ws)
    menu = select_menu(bound, env, ws, size=condition.menu_size)
    first = _resolve_turn(fact_turn, bound["_facts"])
    pad_rng = random.Random(f"{case['id']}|{trial}|{condition.history_tokens}|pad")
    history = history_padding(condition.history_tokens, pad_rng, bound["lane"], first_turn=first,
                              avoid=_avoid_words(bound, case))
    system = env.system_prompt(policy=bound.get("system_policy"))
    user = bound["sessions"][0]["turns"][0]["user"]
    return estimate_tokens(env.schemas(menu)) + estimate_tokens(system) + estimate_tokens(history) \
        + estimate_tokens(user), history, menu


@dataclass
class ProbeRun:
    row: dict
    result: object | None = None      # the TrialResult; None when not run


def run_probe(case: dict, client, condition: Condition, trial: int = 1, *, fact_turn: dict | None = None,
              context_window: int | None = None, loop=None, settings=None,
              env_factory=default_env_factory, output_reserve: int = OUTPUT_RESERVE) -> ProbeRun:
    """One trial of `case` at `condition` through `run_trial(menu_size=M, extra_history=...)`.

    `fact_turn` is `ProbeSet.fact_turns[case["id"]]` for a history-fact case. The window comes
    from `context_window` or `client.describe()["context_window"]`; a prompt that does not fit
    gives a row with status `not run (context)` and success None, and the model is never called.
    """
    window = context_window if context_window is not None else client.describe().get("context_window")
    tokens, history, menu = prompt_token_estimate(case, condition, trial, fact_turn=fact_turn, env_factory=env_factory)
    row = {"case_id": case["id"], "variant_of": case.get("variant_of"), "lane": case["lane"], "family": case["family"],
           "trial": trial, "condition": condition.name, "menu_size": condition.menu_size,
           "history_tokens": condition.history_tokens, "menu_actual": len(menu), "prompt_tokens_est": tokens,
           "fact_in_history": fact_turn is not None, "context_window": window}
    if not fits(window, tokens, output_reserve):
        row.update(status=NOT_RUN_CONTEXT, success=None, excluded=False)
        return ProbeRun(row)
    kw = {k: v for k, v in (("loop", loop), ("settings", settings)) if v is not None}
    res = run_trial(case, client, trial=trial, menu_size=condition.menu_size, extra_history=history,
                    env_factory=env_factory, **kw)
    v = res.verdict
    row.update(status="excluded" if v.excluded else "ok", success=bool(v.success), excluded=bool(v.excluded),
               violation=bool(v.violation))
    return ProbeRun(row, res)


def run_discovery(*args, **kwargs):
    """The Discovery condition (spec 02 §3.10): 12 preloaded full schemas, every other tool as a
    name-only stub, and a `get_tool_schemas` tool the model calls to ask for what it needs.
    Discovery gap = success(full menu, M=25) - success(Discovery).

    NOT IMPLEMENTED. It needs a loop that serves stubs and answers `get_tool_schemas` (a new tool
    that must be added to the offered menu and executed by the environment), and the reference loop
    offers a fixed menu of full schemas. That loop change is outside this module's files.
    """
    raise NotImplementedError("Discovery condition (12 preloaded schemas + name-only stubs + "
                              "get_tool_schemas) is not built; see run_discovery's docstring")


# ---------------------------------------------------------------------------- metrics


def _cell(rows: list[dict], menu: int, hist: int) -> dict[tuple, bool]:
    return {(r["case_id"], r["trial"]): bool(r["success"]) for r in rows
            if r.get("status", "ok") == "ok" and not r.get("excluded") and r.get("success") is not None
            and r["menu_size"] == menu and r["history_tokens"] == hist}


def _ratio(num: dict, den: dict) -> dict:
    keys = sorted(set(num) & set(den))
    a = sum(num[k] for k in keys)
    b = sum(den[k] for k in keys)
    n = len(keys)
    return {"n": n, "numerator": a / n if n else None, "denominator": b / n if n else None,
            "ratio": (a / b) if b else None}


def robustness(rows: list[dict]) -> dict:
    """Menu robustness = success(M=50)/success(M=10) at H=0; history robustness =
    success(H=8k)/success(H=0) at M=10. Only (case, trial) pairs that ran at BOTH conditions count
    (a `not run (context)` cell is left out, never a failure). `history_fact` repeats the history
    figure for the cases that keep a needed fact in the first padded turn."""
    menu = _ratio(_cell(rows, 50, 0), _cell(rows, 10, 0))
    hist = _ratio(_cell(rows, HISTORY_MENU, 8000), _cell(rows, HISTORY_MENU, 0))
    fact_ids = {r["case_id"] for r in rows if r.get("fact_in_history")}
    sub = [r for r in rows if r["case_id"] in fact_ids]
    return {"menu": menu, "history": hist,
            "history_fact": _ratio(_cell(sub, HISTORY_MENU, 8000), _cell(sub, HISTORY_MENU, 0)),
            "not_run": sorted({(r["menu_size"], r["history_tokens"]) for r in rows if r.get("status") == NOT_RUN_CONTEXT})}
