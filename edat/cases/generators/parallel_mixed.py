"""Parallel (spec 02 §3.2) and Mixed fan-out-then-join (§3.4) cases, both lanes.

Parallel: several independent calls the model could make in ONE step.
  sub `same_tool`         one tool, different arguments (weather in 3 cities; 3 contacts' numbers)
  sub `multi_source`      different tools over different sources (calendar + mail + messages + notes)
  sub `independent_writes` several writes from one sentence (3 reminders; a reminder, an event and a text)
Group sizes 2, 3 and 4 appear about a third each. Success never requires one step: the case checks
the answer or the final state, and `expect` carries a `parallel` group with `same_step: true`, so the
grader reports the strict parallel rate separately. `optimal.steps` assumes the group is one step.

Mixed: a parallel read group whose results decide a join call (weather in two cities, then text a
contact whichever is warmer; read two sources, then write a reminder from both). The join's arguments
depend on per-trial `facts` (`argmax` / `argmin` winners, copied codes and phone tails), so a guess
fails; the join is checked in `final_state`.

Lane notes. The assistant lane's weather, web and stocks fakes ignore seeded records, so the assistant
cases read calendar, mail, messages, notes and contacts; weather, stocks, routes, web pages and files
fan-outs live in the neutral lane, where those records are seeded.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from edat.cases.generators import case_id
from edat.cases.generators.pools import FIRST, LAST, PLACES, email

FAMILIES = ("parallel", "mixed")

# Names that already exist in Toolbench's canonical worlds (they vary by world seed), so a seeded
# contact must not equal or contain one of them.
_CANON = ["mom", "dad", "maya", "cohen", "alex", "kim", "kenji", "mori", "jules", "sam", "rivera", "omar",
          "haddad", "tom", "becker", "wren", "chloe", "martin", "priya", "shah", "kai", "family", "ana",
          "okafor"]

CITIES_ASCII = ["Porto", "Lisbon", "Oslo", "Nairobi", "Madrid", "Lyon", "Montreal", "Hobart", "Tallinn",
                "Accra", "Kyoto", "Bergen", "Cork", "Graz", "Tbilisi", "Ljubljana", "Halifax", "Dunedin",
                "Cusco", "Utrecht", "Zadar", "Antwerp", "Seville", "Turku", "Galway", "Bologna", "Leipzig"]

# (reminder, a stem any sensible title contains). None of these collides with Toolbench's canonical reminders.
ERRANDS = [("call the garage", "garage"), ("pay the gas bill", "gas"), ("renew the parking permit", "permit"),
           ("return the library books", "library"), ("buy printer ink", "printer"),
           ("water the tomatoes", "tomato"), ("post the parcel", "parcel"),
           ("order new contact lenses", "lens"), ("collect the prescription", "prescription"),
           ("book a haircut", "haircut"), ("send the rent receipt", "receipt"),
           ("check the smoke alarm", "smoke"), ("defrost the freezer", "freezer"), ("repot the fern", "fern")]

# (event, stem). Distinct from the canonical calendar.
ACTIVITIES = [("piano lesson", "piano"), ("pottery class", "pottery"), ("driving lesson", "driving"),
              ("physio appointment", "physio"), ("tennis game", "tennis"), ("choir practice", "choir")]

QUOTE_PAIRS = [("Brightline Plumbing", "the boiler service"), ("Hartwell Joinery", "the garden fence"),
               ("Moorfield Roofing", "the gutter repair"), ("Copperleaf Glazing", "the window replacement"),
               ("Arden Print Co", "the wedding invitations"), ("Skyline Movers", "the house move"),
               ("Fernhill Electrical", "the rewiring"), ("Kestrel Decorators", "the hallway painting")]
SHOPS = ["Brightline Supplies", "Kestrel Outfitters", "Fernhill Garden Centre", "Moorfield Hardware",
         "Copperleaf Cycles", "Arden Books", "Skyline Appliances", "Hartwell Furniture"]
BOOKERS = ["Tidewater Dental", "Harbourside Gym", "Riverside Climbing Wall", "Juniper Hall",
           "Willow Street Clinic", "Lantern Bistro", "Quayside Market", "Northgate Library"]
# Comparison jobs: one job, four vendors with distinct first words.
COMPARE_JOBS = [
    ("the garden fence", ["Hartwell Joinery", "Copperleaf Fencing", "Oakridge Timber", "Brightline Fencing"]),
    ("the boiler service", ["Moorfield Heating", "Brightline Plumbing", "Kestrel Gas", "Fernhill Boilers"]),
    ("the kitchen tiling", ["Arden Tiling", "Skyline Floors", "Hartwell Interiors", "Copperleaf Tiles"]),
    ("the roof repair", ["Moorfield Roofing", "Kestrel Roofing", "Fernhill Slating", "Brightline Roofers"]),
    ("the house move", ["Skyline Movers", "Arden Removals", "Hartwell Haulage", "Copperleaf Vans"]),
    ("the loft insulation", ["Oakridge Lofts", "Kestrel Insulation", "Moorfield Eco", "Fernhill Home"]),
]
OFFER_ITEMS = [("old bike", ["Brightline Buyback", "Hartwell Resale", "Copperleaf Trade-In", "Kestrel Exchange"]),
               ("old sofa", ["Moorfield Furniture Buyback", "Arden Resale", "Skyline Clearance", "Fernhill Trade-In"]),
               ("old laptop", ["Kestrel Recycle", "Hartwell Digital", "Copperleaf Tech Buyback", "Brightline Refurb"])]
NOTE_WRITES = [("Garden plan", "garden", "plant {n} tomato seedlings"),
               ("Trip ideas", "trip", "book {n} nights in the mountains"),
               ("Meal prep", "meal", "cook {n} portions of chili"),
               ("Reading list", "reading", "finish {n} chapters this week"),
               ("Party list", "party", "invite {n} guests")]
TICKERS = ["ACME", "BLUX", "KRNL", "FJRD", "OMNI", "TWLV", "PLUM", "DRFT"]
LUNCH_SPOTS = ["Lantern Bistro", "Copperleaf Cafe", "Quayside Deli", "Juniper Kitchen", "Northgate Noodles",
               "Willow Tea Rooms"]
DEPOTS = ["Juniper Hall", "Quayside Market", "Northgate Library", "Fernhill School", "Riverside Climbing Wall"]
INVOICE_NAMES = ["boiler", "fence", "dentist", "garage", "printer", "gym"]

ASSISTANT_QUIET = ["reminders", "calendar", "notes", "messages_sent", "mail_sent"]
NEUTRAL_QUIET = ["reminders", "calendar", "notes", "messages_sent", "mail_sent", "message_drafts"]


def ph(name: str) -> str:
    return "{" + name + "}"


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _call(tool: str, /, **arguments) -> dict:
    return {"name": tool, "arguments": arguments}


@dataclass
class Part:
    """One piece of a case: a read or a write, with its seed, facts and checks."""
    phrase: str = ""
    call: dict | None = None
    member: dict | None = None
    answer: list = field(default_factory=list)
    regex: str | None = None
    say: str = ""
    records: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    state: list = field(default_factory=list)
    forbid: list = field(default_factory=list)
    writes: set = field(default_factory=set)
    extra: dict = field(default_factory=dict)


class Ctx:
    """Draws of one case: unique names, ids and picks, so two parts never collide."""

    def __init__(self, rng: random.Random, lane: str) -> None:
        self.rng, self.lane = rng, lane
        self.firsts: list[str] = []
        self.lasts: set[str] = set()
        self.used: dict[str, set] = {}
        self.counters: dict[str, int] = {}

    def nid(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-9{self.counters[prefix]:02d}"

    def tail(self) -> int:
        """A phone tail for a seeded contact that no canonical contact (0102..0190) and no looked-up number
        (fact tails run 10..89) can share; unique within the case."""
        used = self.used.setdefault("tail", set())
        t = self.rng.choice([n for n in range(91, 100) if n not in used])
        used.add(t)
        return t

    def person(self) -> tuple[str, str]:
        while True:
            first, last = self.rng.choice(FIRST), self.rng.choice(LAST)
            low = first.lower()
            if last in self.lasts or any(low in c or c in low for c in _CANON):
                continue
            if any(low in f.lower() or f.lower() in low for f in self.firsts):
                continue
            self.firsts.append(first)
            self.lasts.add(last)
            return first, last

    def pick(self, key: str, options):
        used = self.used.setdefault(key, set())
        free = [o for o in options if (o if isinstance(o, str) else o[0]) not in used]
        choice = self.rng.choice(free)
        used.add(choice if isinstance(choice, str) else choice[0])
        return choice

    def company(self, options):
        """A company whose first word no other company in this case shares."""
        used = self.used.setdefault("company_word", set())
        free = [o for o in options if (o if isinstance(o, str) else o[0]).split()[0] not in used]
        choice = self.rng.choice(free)
        used.add((choice if isinstance(choice, str) else choice[0]).split()[0])
        return choice


# =================================================================== reads (both lanes)
def r_contact(ctx: Ctx, i: int) -> Part:
    first, last = ctx.person()
    cid, p = ctx.nid("c"), f"p{i}"
    lo = 10 + 20 * i
    rec = {"id": cid, "name": f"{first} {last}", "phone": f"+1 555 01{ph(p)}", "email": email(first, last)}
    if ctx.lane == "assistant":
        call = _call("lookup_contact", name=first)
        member = {"tool": "lookup_contact", "args": {"name": {"icontains": first}}}
    else:
        call = _call("contacts_search", query=first)
        member = {"tool": "contacts_search", "args": {"query": {"icontains": first}}}
    return Part(phrase=ctx.rng.choice([f"{first}'s number", f"{first}'s phone number"]), call=call, member=member,
                answer=[f"01{ph(p)}"], say=f"{first}'s number is +1 555 01{ph(p)}.",
                records={"contacts": [rec]}, facts={p: {"int": [lo, lo + 19]}},
                extra={"first": first, "full": f"{first} {last}", "cid": cid, "tail": f"01{ph(p)}"})


def r_email(ctx: Ctx, i: int, kind: str | None = None) -> Part:
    kind = kind or ctx.rng.choice(["quote", "order", "booking"])
    if kind == "quote":
        comp, topic = ctx.company(QUOTE_PAIRS)
    elif kind == "order":
        comp, topic = ctx.company(SHOPS), ""
    else:
        comp, topic = ctx.company(BOOKERS), ""
    word = comp.split()[0]
    tok = word.lower()
    f = f"m{i}"
    if kind == "quote":
        subject = f"Quote for {topic.removeprefix('the ')}"
        body = f"Hello, thanks for asking. Our quote for {topic} is ${ph(f)}, valid for two weeks."
        facts = {f: {"int": [150, 950]}}
        phrase, say = f"{comp}'s quote for {topic}", f"{comp} quoted ${ph(f)} for {topic}."
    elif kind == "order":
        subject = "Your order has shipped"
        body = f"Good news, your order {ph(f)} has shipped and should arrive on Friday."
        facts = {f: {"code": "@@-####"}}
        phrase, say = f"the order number in {comp}'s shipping email", f"{comp}'s order number is {ph(f)}."
    else:
        subject = "Booking confirmed"
        body = f"Your booking is confirmed. Your reference is {ph(f)}."
        facts = {f: {"code": "@@####"}}
        phrase, say = f"the booking reference {comp} sent", f"{comp}'s booking reference is {ph(f)}."
    rec = {"id": ctx.nid("m"), "from": f"{comp} <office@{tok}.example.com>", "subject": subject, "body": body,
           "received": f"-1d {9 + i}:15", "unread": True}
    if ctx.lane == "assistant":
        call, member = _call("view_emails", query=word), {"tool": "view_emails"}
    else:
        call = _call("email_search", query=word)
        member = {"any_of": ["email_search", "email_list"]}
    return Part(phrase=phrase, call=call, member=member, answer=[ph(f)], say=say,
                records={"mail": [rec]}, facts=facts, extra={"comp": comp, "word": word, "kind": kind, "f": f})


def r_message(ctx: Ctx, i: int, kind: str | None = None, contact: bool = False,
              exclude: list | None = None) -> Part:
    first, last = ctx.person()
    kind = kind or ctx.rng.choice(["late", "table", "gate"])
    f = f"s{i}"
    if kind == "late":
        text = f"Running about {ph(f)} minutes behind, start without me."
        facts = {f: {"int": [10, 55], **({"exclude": exclude} if exclude else {})}}
        phrase, say = f"how late {first} said they are running", f"{first} is about {ph(f)} minutes behind."
    elif kind == "away":
        text = f"I'm about {ph(f)} minutes away."
        facts = {f: {"int": [5, 60], **({"exclude": exclude} if exclude else {})}}
        phrase, say = f"how far away {first} is", f"{first} is about {ph(f)} minutes away."
    elif kind == "table":
        text = f"I booked table {ph(f)} for us tonight."
        facts = {f: {"int": [3, 48]}}
        phrase, say = f"which table {first} booked", f"{first} booked table {ph(f)}."
    else:
        text = f"The gate code for Saturday is {ph(f)}."
        facts = {f: {"code": "####"}}
        phrase, say = f"the gate code {first} sent", f"{first}'s gate code is {ph(f)}."
    full = f"{first} {last}"
    records = {"messages": [{"id": ctx.nid("ms"), "thread": full, "from": full, "text": text,
                             "received": f"-{2 + i}h", "unread": True}]}
    cid = None
    if contact:
        cid = ctx.nid("c")
        records["contacts"] = [{"id": cid, "name": full, "phone": f"+1 555 01{ctx.tail()}",
                                "email": email(first, last)}]
    if ctx.lane == "assistant":
        call, member = _call("view_messages", conversation=first), {"tool": "view_messages"}
    else:
        call = _call("messages_search", query=first)
        member = {"any_of": ["messages_search", "messages_read_thread", "messages_list_threads"]}
    return Part(phrase=phrase, call=call, member=member, answer=[ph(f)], say=say, records=records, facts=facts,
                extra={"first": first, "full": full, "cid": cid, "f": f})


def r_reminder(ctx: Ctx, i: int) -> Part:
    errand, key = ctx.pick("rem", ERRANDS)
    day = 2 + i
    h12, h24 = f"v{i}", f"v{i}h"
    rec = {"id": ctx.nid("r"), "title": _cap(errand), "due": f"+{day}d {ph(h24)}:00"}
    if ctx.lane == "assistant":
        call, member = _call("search_reminders", query=key), {"tool": "search_reminders"}
    else:
        call = _call("reminders_search", query=key)
        member = {"any_of": ["reminders_search", "reminders_list"]}
    regex = rf"(?:(?<!\d){ph(h24)}:00|(?<!\d){ph(h12)}(?::00)?\s*(?:pm|p\.m\.))"
    return Part(phrase=f"when my reminder to {errand} is due", call=call, member=member, answer=[key], regex=regex,
                say=f"Your reminder to {errand} is due at {ph(h12)} PM.", records={"reminders": [rec]},
                facts={h12: {"int": [1, 6]}, h24: {"add": [h12, 12]}},
                extra={"errand": errand, "key": key, "h24": h24, "h12": h12})


def r_event(ctx: Ctx, i: int) -> Part:
    act, _stem = ctx.pick("event", ACTIVITIES)
    place = ctx.pick("event_place", PLACES)
    day = 2 + i
    h12, h24 = f"e{i}", f"e{i}h"
    rec = {"id": ctx.nid("ev"), "title": f"{_cap(act)} at {place}", "start": f"+{day}d {ph(h24)}:00"}
    if ctx.lane == "assistant":
        call, member = _call("get_upcoming", days=7), {"tool": "get_upcoming"}
    else:
        call = _call("calendar_list_events", start_date="+0d", end_date="+7d")
        member = {"any_of": ["calendar_list_events", "calendar_search_events"]}
    regex = rf"(?:(?<!\d){ph(h24)}:00|(?<!\d){ph(h12)}(?::00)?\s*(?:pm|p\.m\.))"
    return Part(phrase=f"what time my {act} is", call=call, member=member, answer=[place], regex=regex,
                say=f"Your {act} at {place} is at {ph(h12)} PM.", records={"calendar": [rec]},
                facts={h12: {"int": [1, 6]}, h24: {"add": [h12, 12]}},
                extra={"act": act, "place": place, "day": day, "h24": h24, "h12": h12})


# =================================================================== reads (neutral only)
def r_weather(ctx: Ctx, i: int, exclude: list | None = None) -> Part:
    city = ctx.pick("city", CITIES_ASCII)
    t = f"t{i}"
    rec = {"id": f"w-{i + 1}", "location": city, "temp_c": ph(t), "high_c": ph(f"{t}hi"), "low_c": ph(f"{t}lo"),
           "condition": ctx.rng.choice(["sunny", "cloudy", "light rain", "clear", "windy"])}
    facts = {t: {"int": [2, 28], **({"exclude": exclude} if exclude else {})},
             f"{t}hi": {"add": [t, 3]}, f"{t}lo": {"add": [t, -6]}}
    return Part(phrase=city, call=_call("weather_get_current", location=city),
                member={"tool": "weather_get_current", "args": {"location": {"icontains": city}}},
                answer=[city, ph(t)], say=f"{city} is {ph(t)} degrees.", records={"weather": [rec]}, facts=facts,
                extra={"city": city, "t": t})


def r_stock(ctx: Ctx, i: int, exclude: list | None = None) -> Part:
    sym = ctx.pick("ticker", TICKERS)
    pr, ch = f"pr{i}", f"ch{i}"
    rec = {"id": sym, "price": ph(pr), "change_pct": ph(ch)}
    facts = {pr: {"int": [20, 400]}, ch: {"int": [-6, 9], **({"exclude": exclude} if exclude else {})}}
    return Part(phrase=sym, call=_call("stocks_get_quote", symbol=sym),
                member={"tool": "stocks_get_quote", "args": {"symbol": {"icontains": sym}}},
                answer=[ph(pr)], say=f"{sym} is at {ph(pr)}.", records={"stocks": [rec]}, facts=facts,
                extra={"sym": sym, "pr": pr, "ch": ch})


def r_page(ctx: Ctx, i: int) -> Part:
    spot = ctx.pick("lunch", LUNCH_SPOTS)
    slug = spot.lower().replace(" ", "-")
    url = f"https://{slug}.example.com/lunch"
    f = f"pg{i}"
    rec = {"id": url, "url": url, "title": f"{spot} lunch menu",
           "text": f"{spot} set lunch: two courses for ${ph(f)}. Open 12 to 3 on weekdays."}
    return Part(phrase=url, call=_call("web_fetch", url=url),
                member={"tool": "web_fetch", "args": {"url": {"icontains": slug}}}, answer=[ph(f)],
                say=f"{spot} charges ${ph(f)} for the set lunch.", records={"web": [rec]},
                facts={f: {"int": [11, 39]}}, extra={"spot": spot, "url": url})


def r_file(ctx: Ctx, i: int) -> Part:
    name = ctx.pick("invoice", INVOICE_NAMES)
    path = f"~/Documents/{name}-invoice.txt"
    f = f"inv{i}"
    rec = {"id": path, "path": path, "kind": "file", "text": f"Invoice total due: ${ph(f)}. Pay within 30 days."}
    return Part(phrase=f"{name}-invoice.txt", call=_call("files_read", path=path),
                member={"tool": "files_read", "args": {"path": {"icontains": name}}}, answer=[ph(f)],
                say=f"The {name} invoice is ${ph(f)}.", records={"files": [rec]}, facts={f: {"int": [80, 880]}},
                extra={"name": name, "path": path})


# =================================================================== writes (both lanes)
SLOT_HOURS = [(7, 9, False), (1, 3, True), (4, 6, True), (7, 9, True)]


def hour_for(slot: int) -> tuple[dict, str, str]:
    """(facts, how the prompt says it, the 24-hour placeholder) for one reminder slot."""
    lo, hi, pm = SLOT_HOURS[slot]
    f = f"h{slot}"
    facts = {f: {"int": [lo, hi]}}
    if pm:
        facts[f"{f}x"] = {"add": [f, 12]}
    return facts, f"{ph(f)}{'pm' if pm else 'am'}", ph(f"{f}x") if pm else ph(f)


def w_reminder(ctx: Ctx, errand: tuple, day: int, hour: tuple, *, carry_facts: bool = True) -> Part:
    text, key = errand
    facts, say_time, h24 = hour
    title = _cap(text)
    when = f"+{day}d {h24}:00"
    if ctx.lane == "assistant":
        call = _call("add_reminder", title=title, when_iso=when)
        member = {"tool": "add_reminder", "args": {"title": {"icontains": key}}}
        forbid = ["clear_reminders", "clear_past_reminders", "complete_reminder"]
    else:
        call = _call("reminders_create", title=title, due=when)
        member = {"tool": "reminders_create", "args": {"title": {"icontains": key}}}
        forbid = ["reminders_delete", "reminders_complete"]
    state = [{"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "due": when}}]
    return Part(phrase=text, call=call, member=member, state=state, forbid=forbid, writes={"reminders"},
                facts=dict(facts) if carry_facts else {}, say=f"{title} at {say_time}",
                extra={"text": text, "time": say_time, "key": key})


def w_event(ctx: Ctx, activity: tuple, day: int, hour: tuple, with_facts: bool = True) -> Part:
    text, key = activity
    facts, say_time, h24 = hour
    title = _cap(text)
    when = f"+{day}d {h24}:00"
    if ctx.lane == "assistant":
        call = _call("add_calendar_event", title=title, when_iso=when)
        member = {"tool": "add_calendar_event", "args": {"title": {"icontains": key}}}
        forbid = ["cancel_event", "update_event"]
    else:
        call = _call("calendar_create_event", title=title, start=when)
        member = {"tool": "calendar_create_event", "args": {"title": {"icontains": key}}}
        forbid = ["calendar_delete_event", "calendar_update_event"]
    state = [{"in": "calendar", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "start": when}}]
    return Part(phrase=text, call=call, member=member, state=state, forbid=forbid, writes={"calendar"},
                facts=dict(facts) if with_facts else {}, say=f"{title} at {say_time}",
                extra={"text": text, "time": say_time, "key": key})


def w_note(ctx: Ctx, i: int) -> Part:
    title, key, body_tpl = ctx.pick("note_write", NOTE_WRITES)
    f = f"n{i}"
    body = body_tpl.replace("{n}", ph(f))
    if ctx.lane == "assistant":
        call = _call("create_note", title=title, body=_cap(body))
        member = {"tool": "create_note", "args": {"title": {"icontains": key}}}
        forbid = ["delete_note"]
    else:
        call = _call("notes_create", title=title, body=_cap(body))
        member = {"tool": "notes_create", "args": {"title": {"icontains": key}}}
        forbid = ["notes_delete"]
    state = [{"in": "notes", "where": {"title": {"icontains": key}}, "expect": {"count": 1, "body": {"icontains": ph(f)}}}]
    return Part(phrase=f"a note called {title} saying {body}", call=call, member=member, state=state,
                forbid=forbid, writes={"notes"}, facts={f: {"int": [3, 12]}}, say=title,
                extra={"title": title, "body": body})


MSG_TEXTS = [("I'll be about {m} minutes late", "that I'll be about {m} minutes late"),
             ("I'm bringing {m} bottles", "that I'm bringing {m} bottles"),
             ("The table is booked for {m}", "that the table is booked for {m}"),
             ("Dinner is in {m} minutes", "that dinner is in {m} minutes")]


def w_message(ctx: Ctx, i: int, *, shared_text: tuple | None = None, shared_fact: str | None = None) -> Part:
    """A text to a seeded contact. With `shared_text`, every recipient gets the same text and fact."""
    first, last = ctx.person()
    cid = ctx.nid("c")
    full = f"{first} {last}"
    tail = ctx.tail()
    rec = {"id": cid, "name": full, "phone": f"+1 555 01{tail}", "email": email(first, last)}
    f = shared_fact or f"m{i}"
    text_tpl, said_tpl = shared_text or ctx.rng.choice(MSG_TEXTS)
    text, said = text_tpl.replace("{m}", ph(f)), said_tpl.replace("{m}", ph(f))
    facts = {} if shared_fact else {f: {"int": [5, 45]}}
    if ctx.lane == "assistant":
        call = _call("send_message", to=full, text=text)
        member = {"tool": "send_message", "args": {"to": {"contains_any": [first, f"01{tail}"]}}}
        forbid = ["send_email", "schedule_send"]
        state = [{"in": "messages_sent", "where": {"contact_ids": {"icontains": cid}},
                  "expect": {"count": 1, "text": {"icontains": ph(f)}}}]
    else:
        call = _call("messages_send", to=full, text=text)
        member = {"tool": "messages_send", "args": {"to": {"contains_any": [first, f"01{tail}"]}}}
        forbid = ["email_send", "messages_schedule"]
        state = [{"in": "messages_sent", "where": {"contact_id": cid},
                  "expect": {"count": 1, "text": {"icontains": ph(f)}}}]
    return Part(phrase=f"text {first} {said}", call=call, member=member, state=state, forbid=forbid,
                writes={"messages_sent"}, records={"contacts": [rec]}, facts=facts, say=f"Texted {first}.",
                extra={"first": first, "cid": cid, "said": said, "f": f})


# =================================================================== assembling
def merge(parts: list[Part]) -> tuple[dict, dict]:
    records: dict[str, list] = {}
    facts: dict = {}
    for p in parts:
        for coll, rows in p.records.items():
            records.setdefault(coll, []).extend(rows)
        for k, v in p.facts.items():
            if k in facts:
                raise AssertionError(f"fact {k} drawn twice")
            facts[k] = v
    return records, facts


def casual(rng: random.Random, text: str) -> str:
    """About a third of prompts are typed the way people text: lower case, no final stop."""
    if rng.random() < 0.33:
        return text.lower().rstrip(".")
    return text


def answer_spec(parts: list[Part]) -> dict:
    spec: dict = {"contains_all": [a for p in parts for a in p.answer]}
    regexes = [p.regex for p in parts if p.regex]
    if len(regexes) == 1:
        spec["regex"] = regexes[0]
    elif regexes:       # every one must hold: look-aheads from the start of the answer
        spec["regex"] = "^" + "".join(f"(?=.*{r})" for r in regexes)
    return spec


def _forbid(parts: list[Part], lane: str, extra: list[str] | None = None) -> list[str]:
    out: list[str] = list(extra or [])
    for p in parts:
        for f in p.forbid:
            if f not in out:
                out.append(f)
    return out[:3]


def _unchanged(lane: str, written: set) -> list[str]:
    base = ASSISTANT_QUIET if lane == "assistant" else NEUTRAL_QUIET
    return [c for c in base if c not in written]


READ_FORBID = {"assistant": ["send_message", "send_email", "add_reminder"],
               "neutral": ["messages_send", "email_send", "reminders_create"]}


def parallel_case(ctx: Ctx, cid: str, sub: str, parts: list[Part], prompt: str, trap: str, *,
                  forbid: list[str] | None = None, intended: list | None = None, core: bool = False) -> dict:
    records, facts = merge(parts)
    writes = set().union(*(p.writes for p in parts))
    case: dict = {
        "id": cid, "lane": ctx.lane, "family": "parallel", "sub": sub,
        "tags": ["core"] if core else [],
        "prompt": casual(ctx.rng, prompt),
    }
    if facts:
        case["facts"] = facts
    if records:
        case["world"] = {"seed_records": records}
    case["expect"] = [{"parallel": {"same_step": True, "steps": [p.member for p in parts]}}]
    if any(p.answer for p in parts):
        case["answer"] = answer_spec(parts)
    states = [s for p in parts for s in p.state]
    if states:
        case["final_state"] = states
    case["forbid"] = _forbid(parts, ctx.lane, forbid if forbid is not None else
                             (READ_FORBID[ctx.lane] if not writes else None))
    case["unchanged"] = _unchanged(ctx.lane, writes)
    if intended:
        case["intended_recipients"] = intended
    case["optimal"] = {"calls": len(parts), "steps": 2}
    case["oracle"] = [{"calls": [p.call for p in parts]},
                      {"text": " ".join(p.say for p in parts) if not writes else
                       "Done. " + "; ".join(p.say for p in parts) + "."}]
    case["trap"] = trap
    return case


# =================================================================== parallel templates
def _tell(parts: list[Part]) -> str:
    return _join([p.phrase for p in parts])


def p_contacts(ctx, cid, k, core):
    parts = [r_contact(ctx, i) for i in range(k)]
    names = _join([p.extra["first"] + "'s" for p in parts])
    prompt = ctx.rng.choice([f"What are {names} phone numbers?", f"Give me {names} numbers.",
                             f"I need {names} phone numbers, can you find them?"])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         f"{k} contact lookups with different names; answering from one lookup or guessing fails",
                         core=core)


def p_emails(ctx, cid, k, core):
    kind = ctx.rng.choice(["quote", "order", "booking"])
    parts = [r_email(ctx, i, kind) for i in range(k)]
    prompt = ctx.rng.choice([f"Can you pull up {_tell(parts)}?", f"Tell me {_tell(parts)}.",
                             f"I need {_tell(parts)} from my email."])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         f"{k} mail lookups over different senders; each fact exists only in its own email", core=core)


def p_messages(ctx, cid, k, core):
    kind = ctx.rng.choice(["late", "table", "gate"])
    parts = [r_message(ctx, i, kind) for i in range(k)]
    names = _join([p.extra["first"] for p in parts])
    what = {"late": f"how late {names} said they are running", "table": f"which table each of {names} booked",
            "gate": f"the gate codes {names} sent"}[kind]
    prompt = ctx.rng.choice([f"Check my messages and tell me {what}.", f"Tell me {what}.",
                             f"Can you look up {what}?"])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         f"{k} message-thread lookups; the values live in different conversations", core=core)


def p_reminders_due(ctx, cid, k, core):
    parts = [r_reminder(ctx, i) for i in range(k)]
    what = f"when my reminders to {_join([p.extra['errand'] for p in parts])} are due"
    prompt = ctx.rng.choice([f"Tell me {what}.", f"Can you check {what}?", f"I forgot {what}, can you look?"])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         f"{k} reminder lookups; a guessed time fails because the hours change per trial", core=core)


def p_reminders_same(ctx, cid, k, core):
    hour = hour_for(0)
    errands = [ctx.pick("errand", ERRANDS) for _ in range(k)]
    parts = [w_reminder(ctx, e, 1, hour, carry_facts=(i == 0)) for i, e in enumerate(errands)]
    when = hour[1]
    items = _join([p.extra["text"] for p in parts])
    prompt = ctx.rng.choice([f"Set reminders for tomorrow at {when} to {items}.",
                             f"Add reminders for tomorrow at {when}: {items}.",
                             f"Tomorrow at {when} I need reminders to {items}."])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         "one combined reminder (or a missing one) fails: each errand is its own reminder", core=core)


def p_texts_same(ctx, cid, k, core):
    tpl = ctx.rng.choice(MSG_TEXTS)
    parts = [w_message(ctx, i, shared_text=tpl, shared_fact="m0") for i in range(k)]
    parts[0].facts = {"m0": {"int": [5, 45]}}
    names = _join([p.extra["first"] for p in parts])
    prompt = ctx.rng.choice([f"Text {names} {parts[0].extra['said']}.", f"Let {names} know {parts[0].extra['said']}."])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         "one group text, a text to only some people, or an email instead fails",
                         intended=[p.extra["cid"] for p in parts], core=core)


def _source_parts(ctx: Ctx, k: int) -> list[Part]:
    makers = {"calendar": r_event, "email": r_email, "message": r_message, "reminder": r_reminder,
              "contact": r_contact}
    kinds = ctx.rng.sample(list(makers), k)
    return [makers[kind](ctx, i) for i, kind in enumerate(kinds)]


def p_multi(ctx, cid, k, core):
    parts = _source_parts(ctx, k)
    opener = ctx.rng.choice(["Can you tell me", "I need to know", "Tell me", "Quick check, tell me"])
    prompt = f"{opener} {_tell(parts)}."
    return parallel_case(ctx, cid, "multi_source", parts, prompt,
                         "answering from one source implies the others were empty; each fact sits in a different app",
                         core=core)


def p_writes_times(ctx, cid, k, core):
    errands = [ctx.pick("errand", ERRANDS) for _ in range(k)]
    day = ctx.rng.choice([1, 1, 2])
    when_day = "tomorrow" if day == 1 else "the day after tomorrow"
    parts = []
    for i, e in enumerate(errands):
        parts.append(w_reminder(ctx, e, day, hour_for(i)))
    segs = [f"at {p.extra['time']} to {p.extra['text']}" for p in parts]
    prompt = ctx.rng.choice([f"Remind me {when_day} {_join(segs)}.", f"Set reminders for {when_day}, {_join(segs)}."])
    return parallel_case(ctx, cid, "independent_writes", parts, prompt,
                         "one combined reminder, a wrong time, or a missing errand fails", core=core)


def p_writes_events(ctx, cid, k, core):
    acts = [ctx.pick("activity", ACTIVITIES) for _ in range(k)]
    # distinct hour slots and days so no two events share a fact name
    slots = [1, 2, 3, 0]
    parts = [w_event(ctx, a, 1 + i, hour_for(slots[i])) for i, a in enumerate(acts)]
    days = {1: "tomorrow", 2: "the day after tomorrow", 3: "in three days", 4: "in four days"}
    segs = [f"a {p.extra['text']} {days[1 + i]} at {p.extra['time']}" for i, p in enumerate(parts)]
    prompt = ctx.rng.choice([f"Put {_join(segs)} on my calendar.", f"Add {_join(segs)} to my calendar."])
    return parallel_case(ctx, cid, "independent_writes", parts, prompt,
                         "a missing event, a wrong day or one merged event fails", core=core)


def p_writes_mixed(ctx, cid, k, core):
    kinds = ctx.rng.sample(["reminder", "event", "note", "message"], k)
    parts, segs = [], []
    for i, kind in enumerate(kinds):
        if kind == "reminder":
            part = w_reminder(ctx, ctx.pick("errand", ERRANDS), 1, hour_for(0))
            segs.append(f"remind me tomorrow at {part.extra['time']} to {part.extra['text']}")
        elif kind == "event":
            part = w_event(ctx, ctx.pick("activity", ACTIVITIES), 2, hour_for(2))
            segs.append(f"put a {part.extra['text']} on my calendar for the day after tomorrow at {part.extra['time']}")
        elif kind == "note":
            part = w_note(ctx, i)
            segs.append(f"jot down {part.phrase}")
        else:
            part = w_message(ctx, i)
            segs.append(part.phrase)
        parts.append(part)
    intended = [p.extra["cid"] for p in parts if "cid" in p.extra]
    prompt = _cap(_join(segs)) + "."
    return parallel_case(ctx, cid, "independent_writes", parts, prompt,
                         "doing only the first request, or turning the group into one note, fails",
                         intended=intended or None, core=core)


def p_writes_texts(ctx, cid, k, core):
    parts = [w_message(ctx, i) for i in range(k)]
    segs = [p.phrase for p in parts]
    prompt = _cap(_join(segs)) + "."
    return parallel_case(ctx, cid, "independent_writes", parts, prompt,
                         "each person gets their own text: one shared message or a missing recipient fails",
                         intended=[p.extra["cid"] for p in parts], core=core)


# ---- neutral-only parallel templates
def p_weather(ctx, cid, k, core):
    parts, seen = [], []
    for i in range(k):
        parts.append(r_weather(ctx, i, exclude=list(seen) or None))
        seen.append(parts[-1].extra["t"])
    cities = _join([p.extra["city"] for p in parts])
    prompt = ctx.rng.choice([f"What's the weather right now in {cities}?", f"Current temperature in {cities}?",
                             f"How warm is it right now in {cities}?"])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         f"weather for {k} places: one lookup, or a guessed temperature, fails",
                         forbid=["web_search", "web_fetch"], core=core)


def p_stocks(ctx, cid, k, core):
    parts, seen = [], []
    for i in range(k):
        parts.append(r_stock(ctx, i, exclude=list(seen) or None))
        seen.append(parts[-1].extra["ch"])
    syms = _join([p.extra["sym"] for p in parts])
    prompt = ctx.rng.choice([f"What are {syms} trading at?", f"Give me the latest prices for {syms}."])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         f"{k} quotes, one per ticker; a remembered price fails",
                         forbid=["web_search", "web_fetch"], core=core)


def p_pages(ctx, cid, k, core):
    parts = [r_page(ctx, i) for i in range(k)]
    urls = _join([p.phrase for p in parts])
    prompt = ctx.rng.choice([f"What does each of these places charge for the set lunch: {urls}?",
                             f"Check the lunch prices on {urls}."])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         "a price seen on one page is not the price on another", forbid=["web_search", "shell_exec"],
                         core=core)


def p_files(ctx, cid, k, core):
    parts = [r_file(ctx, i) for i in range(k)]
    names = _join([p.phrase for p in parts])
    prompt = ctx.rng.choice([f"What are the totals on {names} in my Documents folder?",
                             f"Open {names} in Documents and tell me each total due."])
    return parallel_case(ctx, cid, "same_tool", parts, prompt,
                         "each total is in its own file; a guess or one read fails", forbid=["shell_exec", "files_trash"],
                         core=core)


# =================================================================== mixed templates
def mixed_case(ctx: Ctx, cid: str, sub: str, reads: list[Part], join_call: dict, join_member: dict,
               state: list[dict], prompt: str, trap: str, *, facts_extra: dict | None = None,
               intended: list | None = None, forbid: list | None = None, say: str = "",
               writes: set | None = None, core: bool = False, records_extra: dict | None = None) -> dict:
    records, facts = merge(reads)
    for k, v in (facts_extra or {}).items():
        facts[k] = v
    for coll, rows in (records_extra or {}).items():
        records.setdefault(coll, []).extend(rows)
    case: dict = {"id": cid, "lane": ctx.lane, "family": "mixed", "sub": sub, "tags": ["core"] if core else [],
                  "prompt": casual(ctx.rng, prompt)}
    case["facts"] = facts
    case["world"] = {"seed_records": records}
    case["expect"] = [{"parallel": {"same_step": True, "steps": [p.member for p in reads]}}, join_member]
    case["final_state"] = state
    case["forbid"] = (forbid or [])[:3]
    case["unchanged"] = _unchanged(ctx.lane, writes or set())
    if intended:
        case["intended_recipients"] = intended
    case["optimal"] = {"calls": len(reads) + 1, "steps": 3}
    case["oracle"] = [{"calls": [p.call for p in reads]}, {"calls": [join_call]}, {"text": say}]
    case["trap"] = trap
    return case


def _partner(ctx: Ctx) -> tuple[dict, str, str]:
    """A seeded contact the join texts. Returns (record, first name, full name)."""
    first, last = ctx.person()
    cid = ctx.nid("c")
    rec = {"id": cid, "name": f"{first} {last}", "phone": f"+1 555 01{ctx.tail()}",
           "email": email(first, last)}
    return rec, first, f"{first} {last}"


def _send(ctx: Ctx, to: str, text: str) -> tuple[dict, dict, list[str]]:
    if ctx.lane == "assistant":
        return (_call("send_message", to=to, text=text),
                {"tool": "send_message", "args": {"to": {"icontains": to.split()[0]}}}, ["send_email", "schedule_send"])
    return (_call("messages_send", to=to, text=text),
            {"tool": "messages_send", "args": {"to": {"icontains": to.split()[0]}}}, ["email_send", "messages_schedule"])


def _sent_state(ctx: Ctx, cid: str, text_spec: dict) -> list[dict]:
    key = "contact_ids" if ctx.lane == "assistant" else "contact_id"
    where = {key: {"icontains": cid}} if ctx.lane == "assistant" else {key: cid}
    return [{"in": "messages_sent", "where": where, "expect": {"count": 1, "text": text_spec}},
            {"in": "messages_sent", "expect": {"count": 1}}]


def _winner_facts(prefix: str, values: dict, mode: str, k: int) -> dict:
    """Facts `win` and `lose_1..` from a mapping {label: fact name}: the label with the extreme value, and
    the labels of every other member (each exactly once)."""
    labels = list(values)
    facts = {f"{prefix}win": {mode: values}}
    taken = [f"{prefix}win"]
    for j in range(1, k):
        facts[f"{prefix}lose{j}"] = {"choice": labels, "exclude": list(taken)}
        taken.append(f"{prefix}lose{j}")
    return facts


def _winner_text(prefix: str, k: int) -> dict:
    return {"all_of": [{"icontains": ph(f"{prefix}win")},
                       {"not_contains": [ph(f"{prefix}lose{j}") for j in range(1, k)]}]}


def m_compare_emails(ctx, cid, k, core, variant):
    """k vendors each email a price; text a contact the cheapest (or best offer). The join's recipient text is
    the winner's name, so it changes with the seed."""
    if variant == "quotes":
        job, vendors = ctx.rng.choice(COMPARE_JOBS)
        mode, word_a, word_b = "argmin", "cheapest", "the lowest quote"
    else:
        item, vendors = ctx.rng.choice(OFFER_ITEMS)
        mode, word_a, word_b = "argmax", "best", "the highest offer"
    chosen = ctx.rng.sample(vendors, k)
    parts, seen = [], []
    for i, comp in enumerate(chosen):
        word = comp.split()[0]
        f = f"m{i}"
        if variant == "quotes":
            subject = f"Quote for {job.removeprefix('the ')}"
            body = f"Hello, our quote for {job} is ${ph(f)}, valid for two weeks."
        else:
            subject = f"Our offer for your {item}"
            body = f"We would be glad to buy your {item} for ${ph(f)}, collection included."
        ctx.company([comp])
        rec = {"id": ctx.nid("m"), "from": f"{comp} <office@{word.lower()}.example.com>", "subject": subject,
               "body": body, "received": f"-1d {9 + i}:15", "unread": True}
        if ctx.lane == "assistant":
            call, member = _call("view_emails", query=word), {"tool": "view_emails"}
        else:
            call, member = _call("email_search", query=word), {"any_of": ["email_search", "email_list"]}
        parts.append(Part(call=call, member=member, records={"mail": [rec]},
                          facts={f: {"int": [150, 950], **({"exclude": list(seen)} if seen else {})}},
                          extra={"word": word, "comp": comp, "f": f}))
        seen.append(f)
    labels = {p.extra["word"]: p.extra["f"] for p in parts}
    facts = _winner_facts("", labels, mode, k)
    partner, first, full = _partner(ctx)
    comps = _join([p.extra["comp"] for p in parts])
    what = f"the quotes for {job}" if variant == "quotes" else f"the offers for my {item}"
    prompt = ctx.rng.choice([
        f"Check {what} from {comps} and text {first} just the name of the one with {word_b}.",
        f"Look at {what} from {comps}, then text {first} which company is {word_a}. Just the company name please."])
    call, member, forbid = _send(ctx, full, f"{ph('win')} has the {word_a} price.")
    state = _sent_state(ctx, partner["id"], _winner_text("", k))
    return mixed_case(ctx, cid, "compare_then_text", parts, call, member, state, prompt,
                      f"texting a guessed or the wrong company (the {word_a} one changes per trial) or naming several fails",
                      facts_extra=facts, intended=[partner["id"]], forbid=forbid + READ_FORBID[ctx.lane][2:],
                      say=f"I texted {first}: {ph('win')} has the {word_a} price.", writes={"messages_sent"},
                      core=core, records_extra={"contacts": [partner]})




def m_arrival(ctx, cid, k, core, variant):
    """k friends text how far away they are; text the furthest (or the closest) one. The recipient is the
    seeded winner, so texting the wrong friend, everyone, or a guess fails."""
    seen, parts = [], []
    for i in range(k):
        parts.append(r_message(ctx, i, "away", contact=True, exclude=list(seen) or None))
        seen.append(parts[-1].extra["f"])
    mode = "argmax" if variant == "far" else "argmin"
    who = "whoever is furthest away" if variant == "far" else "whoever is closest"
    msg = "We'll save you a plate." if variant == "far" else "Come on up, the door is open."
    facts = {"win_name": {mode: {p.extra["full"]: p.extra["f"] for p in parts}},
             "win_id": {mode: {p.extra["cid"]: p.extra["f"] for p in parts}},
             "win_first": {mode: {p.extra["first"]: p.extra["f"] for p in parts}}}
    names = _join([p.extra["first"] for p in parts])
    prompt = ctx.rng.choice([f"Check what {names} each said about how far away they are, then text {who}: {msg}",
                             f"See how far away {names} say they are and text {who} this: {msg}"])
    call, member, forbid = _send(ctx, ph("win_name"), msg)
    member = {"tool": member["tool"], "args": {"to": {"contains_any": [p.extra["first"] for p in parts]}}}
    key = "contact_ids" if ctx.lane == "assistant" else "contact_id"
    where = {key: {"icontains": ph("win_id")}} if ctx.lane == "assistant" else {key: ph("win_id")}
    state = [{"in": "messages_sent", "where": where, "expect": {"count": 1}},
             {"in": "messages_sent", "expect": {"count": 1}}]
    return mixed_case(ctx, cid, "pick_then_text", parts, call, member, state, prompt,
                      "texting everyone, or the wrong friend (the furthest one changes per trial), fails",
                      facts_extra=facts, intended=[ph("win_id"), ph("win_name")], forbid=forbid + READ_FORBID[ctx.lane][2:],
                      say=f"I texted {ph('win_first')}.", writes={"messages_sent"}, core=core)


def m_event_email(ctx, cid, core):
    """Read an event and a booking email, then set a reminder an hour before with the email's reference."""
    ev, em = r_event(ctx, 0), r_email(ctx, 1, "booking")
    act, day, h24, ref = ev.extra["act"], ev.extra["day"], ev.extra["h24"], ph(em.extra["f"])
    when = f"+{day}d {ph('e0hm')}:00"
    title = f"Bring ref {ref} to {act}"
    if ctx.lane == "assistant":
        call = _call("add_reminder", title=title, when_iso=when)
        member = {"tool": "add_reminder", "args": {"title": {"icontains": ref}}}
        forbid = ["add_calendar_event", "clear_reminders", "cancel_event"]
    else:
        call = _call("reminders_create", title=title, due=when)
        member = {"tool": "reminders_create", "args": {"title": {"icontains": ref}}}
        forbid = ["calendar_create_event", "reminders_delete", "calendar_delete_event"]
    state = [{"in": "reminders", "where": {"title": {"icontains": ref}}, "expect": {"count": 1, "due": when}}]
    prompt = ctx.rng.choice([
        f"Look at my {act} on the calendar and {em.extra['comp']}'s booking email, then set a reminder an hour "
        f"before the {act} with the booking reference in the title.",
        f"Check when my {act} is and what reference {em.extra['comp']} sent, then remind me an hour before the "
        f"{act}, with the reference in the reminder."])
    return mixed_case(ctx, cid, "read_then_remind", [ev, em], call, member, state, prompt,
                      "the reminder time follows the event hour and the title the emailed reference: both change per trial",
                      facts_extra={"e0hm": {"add": [h24, -1]}}, forbid=forbid,
                      say=f"Done: a reminder at {ph('e0hm')}:00 to bring ref {ref}.", writes={"reminders"}, core=core)


def m_phones(ctx, cid, k, core):
    """Look up k numbers, then text them to a third contact."""
    parts = [r_contact(ctx, i) for i in range(k)]
    partner, first, full = _partner(ctx)
    names = _join([p.extra["first"] for p in parts])
    text = "; ".join(f"{p.extra['first']} +1 555 {p.extra['tail']}" for p in parts)
    call, member, forbid = _send(ctx, full, text)
    state = _sent_state(ctx, partner["id"], {"contains_all": [p.extra["tail"] for p in parts]})
    prompt = ctx.rng.choice([f"Text {first} the phone numbers for {names}.",
                             f"Look up {names} and send their numbers to {first} by text."])
    return mixed_case(ctx, cid, "lookup_then_text", parts, call, member, state, prompt,
                      "the text must carry each looked-up number (they change per trial); a guess or a partial list fails",
                      intended=[partner["id"]], forbid=forbid + READ_FORBID[ctx.lane][2:],
                      say=f"I texted {first} the numbers.", writes={"messages_sent"}, core=core,
                      records_extra={"contacts": [partner]})


def m_codes(ctx, cid, k, core):
    """Read k codes from different sources (a message, an email), then text them together."""
    kinds = ["message", "email", "message"][:k]
    ctx.rng.shuffle(kinds)
    parts = [r_message(ctx, i, "gate") if kd == "message" else r_email(ctx, i, "booking")
             for i, kd in enumerate(kinds)]
    partner, first, full = _partner(ctx)
    refs = [ph(p.extra["f"]) for p in parts]
    text = "Codes: " + ", ".join(refs)
    call, member, forbid = _send(ctx, full, text)
    state = _sent_state(ctx, partner["id"], {"contains_all": refs})
    prompt = ctx.rng.choice([f"Text {first} {_tell(parts)}.", f"Find {_tell(parts)} and text them to {first}."])
    return mixed_case(ctx, cid, "lookup_then_text", parts, call, member, state, prompt,
                      "the text must carry every code from the two sources (they change per trial); a guessed or partial text fails",
                      intended=[partner["id"]], forbid=forbid + READ_FORBID[ctx.lane][2:],
                      say=f"I texted {first} the codes.", writes={"messages_sent"}, core=core,
                      records_extra={"contacts": [partner]})


# ---- neutral-only mixed templates
def m_weather(ctx, cid, k, core, mode):
    parts, seen = [], []
    for i in range(k):
        parts.append(r_weather(ctx, i, exclude=list(seen) or None))
        seen.append(parts[-1].extra["t"])
    labels = {p.extra["city"]: p.extra["t"] for p in parts}
    facts = _winner_facts("", labels, mode, k)
    partner, first, full = _partner(ctx)
    cities = _join([p.extra["city"] for p in parts])
    hot = mode == "argmax"
    adj = ("warmer" if hot else "colder") if k == 2 else ("warmest" if hot else "coldest")
    prompt = ctx.rng.choice([f"Check the weather right now in {cities} and text {first} just the name of the {adj} one.",
                             f"Which is {adj} right now, {cities}? Text {first} the city name only."])
    call, member, forbid = _send(ctx, full, f"{ph('win')} is the {adj} right now.")
    state = _sent_state(ctx, partner["id"], _winner_text("", k))
    return mixed_case(ctx, cid, "compare_then_text", parts, call, member, state, prompt,
                      f"texting a guessed city or one that is not the {adj} (the temperatures change per trial) or both fails",
                      facts_extra=facts, intended=[partner["id"]], forbid=forbid + ["web_search"],
                      say=f"I texted {first}: {ph('win')}.", writes={"messages_sent"}, core=core,
                      records_extra={"contacts": [partner]})


def m_stocks(ctx, cid, k, core):
    parts, seen = [], []
    for i in range(k):
        parts.append(r_stock(ctx, i, exclude=list(seen) or None))
        seen.append(parts[-1].extra["ch"])
    labels = {p.extra["sym"]: p.extra["ch"] for p in parts}
    facts = _winner_facts("", labels, "argmax", k)
    partner, first, full = _partner(ctx)
    syms = _join([p.extra["sym"] for p in parts])
    prompt = ctx.rng.choice([f"Which of {syms} is up the most today? Text {first} just the ticker.",
                             f"Check {syms} and text {first} the ticker with the biggest gain today."])
    call, member, forbid = _send(ctx, full, f"{ph('win')} is up the most today.")
    state = _sent_state(ctx, partner["id"], _winner_text("", k))
    return mixed_case(ctx, cid, "compare_then_text", parts, call, member, state, prompt,
                      "the biggest gainer changes per trial; a guessed or multi-ticker text fails",
                      facts_extra=facts, intended=[partner["id"]], forbid=forbid + ["web_search"],
                      say=f"I texted {first}: {ph('win')}.", writes={"messages_sent"}, core=core,
                      records_extra={"contacts": [partner]})


def m_routes(ctx, cid, k, core):
    places = ctx.rng.sample(DEPOTS, k)
    parts, seen = [], []
    for i, place in enumerate(places):
        word = place.split()[0]
        f = f"d{i}"
        rec = {"id": f"rt-{i + 1}", "origin": "Home", "destination": place, "mode": "driving",
               "distance_km": 6 + 4 * i, "duration_min": ph(f)}
        parts.append(Part(call=_call("maps_get_travel_time", origin="Home", destination=place),
                          member={"tool": "maps_get_travel_time", "args": {"destination": {"icontains": word}}},
                          records={"routes": [rec]},
                          facts={f: {"int": [10, 55], **({"exclude": list(seen)} if seen else {})}},
                          extra={"word": word, "place": place, "f": f}))
        seen.append(f)
    labels = {p.extra["word"]: p.extra["f"] for p in parts}
    facts = _winner_facts("", labels, "argmin", k)
    partner, first, full = _partner(ctx)
    names = _join([p.extra["place"] for p in parts])
    prompt = ctx.rng.choice([f"How long does it take to drive from home to {names}? Text {first} just the name of the closest one.",
                             f"Compare the drive from home to {names} and text {first} which one is the nearest, name only."])
    call, member, forbid = _send(ctx, full, f"{ph('win')} is the closest.")
    state = _sent_state(ctx, partner["id"], _winner_text("", k))
    return mixed_case(ctx, cid, "compare_then_text", parts, call, member, state, prompt,
                      "the nearest place changes per trial; a guess or naming several places fails",
                      facts_extra=facts, intended=[partner["id"]], forbid=forbid + ["web_search"],
                      say=f"I texted {first}: {ph('win')}.", writes={"messages_sent"}, core=core,
                      records_extra={"contacts": [partner]})


# =================================================================== schedule
def _size(t: int, j: int) -> int:
    return (2, 3, 4)[(j + t) % 3]


def _schedule(lane: str) -> list[tuple[str, str, object, int, dict]]:
    """(family, template name, builder, group size, extra kwargs) in generation order."""
    out: list[tuple[str, str, object, int, dict]] = []

    def add(family, name, fn, ks, **kw):
        for k in ks:
            out.append((family, name, fn, k, kw))

    if lane == "assistant":
        for t, (name, fn, n) in enumerate([("contacts", p_contacts, 3), ("emails", p_emails, 3),
                                           ("messages", p_messages, 3), ("reminders_due", p_reminders_due, 3),
                                           ("reminders", p_reminders_same, 3), ("texts", p_texts_same, 2)]):
            add("parallel", name, fn, [_size(t, j) for j in range(n)])
        add("parallel", "sources", p_multi, [(2, 3, 4)[j % 3] for j in range(17)])
        for t, (name, fn, n) in enumerate([("times", p_writes_times, 4), ("events", p_writes_events, 3),
                                           ("mixed", p_writes_mixed, 6), ("texts", p_writes_texts, 3)]):
            add("parallel", "w_" + name, fn, [_size(t, j) for j in range(n)])
        add("mixed", "quotes", m_compare_emails, [2, 3, 2, 4, 3], variant="quotes")
        add("mixed", "offers", m_compare_emails, [2, 2, 3], variant="offers")
        add("mixed", "far", m_arrival, [2, 3, 2], variant="far")
        add("mixed", "near", m_arrival, [2, 3, 2], variant="near")
        add("mixed", "event_email", m_event_email, [0] * 5)
        add("mixed", "phones", m_phones, [2, 3, 2, 3, 2])
        add("mixed", "codes", m_codes, [2, 3, 2, 3, 2, 3])
    else:
        add("parallel", "weather", p_weather, [2, 3, 4, 3])
        add("parallel", "stocks", p_stocks, [2, 4])
        add("parallel", "pages", p_pages, [3, 2])
        add("parallel", "files", p_files, [4])
        add("parallel", "sources", p_multi, [2, 3, 4, 2, 3, 4, 2, 3])
        add("parallel", "w_times", p_writes_times, [2, 3, 4])
        add("parallel", "w_mixed", p_writes_mixed, [2, 3, 4])
        add("parallel", "w_texts", p_writes_texts, [2, 3])
        add("mixed", "warm", m_weather, [2, 2, 3], mode="argmax")
        add("mixed", "cold", m_weather, [2, 3, 4], mode="argmin")
        add("mixed", "stocks", m_stocks, [2, 3])
        add("mixed", "routes", m_routes, [2, 2])
        add("mixed", "quotes", m_compare_emails, [2], variant="quotes")
        add("mixed", "offers", m_compare_emails, [3], variant="offers")
        add("mixed", "event_email", m_event_email, [0, 0])
        add("mixed", "phones", m_phones, [2])
    return out


def generate(split: str, rng: random.Random) -> list[dict]:
    cases: list[dict] = []
    for lane in ("assistant", "neutral"):
        counts: dict[str, int] = {}
        sub_counts: dict[tuple, int] = {}
        for family, name, fn, k, kw in _schedule(lane):
            ctx = Ctx(rng, lane)
            template = f"{name}"
            counts[(family, template)] = n = counts.get((family, template), 0) + 1
            cid = case_id("par" if family == "parallel" else "mix", lane, split, template, n)
            # about 40% of each sub-family is core
            case = _build(fn, ctx, cid, k, kw)
            key = (family, lane, case["sub"])
            sub_counts[key] = c = sub_counts.get(key, 0) + 1
            if c % 5 in (1, 3):
                case["tags"] = ["core"]
            cases.append(case)
    return cases


def _build(fn, ctx: Ctx, cid: str, k: int, kw: dict) -> dict:
    if fn is m_event_email:
        return fn(ctx, cid, False)
    return fn(ctx, cid, k, False, **kw)
