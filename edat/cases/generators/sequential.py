"""Family Sequential (spec 02 §3.3): real dependency chains.

Call n needs a value that only call n-1's result contains. Every value that
passes down the chain (a time, an amount, a code, a file path, an address, an
id) is a seeded `{fact}` that changes per trial, so a guessed or remembered
value fails, and the next call's arguments are checked against it in the
`final_state` (and, for ids, paths and addresses, in `grounded_args`).

Assistant lane: Harbor's send tools resolve names themselves, so "look up a
contact, then send" is NOT a chain. Chains run on values from results: a time
in an email -> a calendar event -> a text with that time; a note's pointer ->
a document -> an email; an event's time -> a moved event -> a text. Calendar
writes take `title` and `when_iso`, never ids. Neutral lane: chains may carry
ids (`calendar_search_events` -> `calendar_update_event(event_id)`), paths and
URLs, to depth 5.

Sub-families: depth2, depth3, depth4 (both lanes), depth5 (neutral only).

Text in this module marks a seeded fact as `[[name]]` (turned into `{name}`
for the case by `_fx`), so ordinary f-strings can sit next to facts.
"""
from __future__ import annotations

import random
import re

from edat.cases.generators import case_id
from edat.cases.generators.pools import COMPANIES, ERRANDS, PLACES, contact, email, person, phone

FAMILY = "sequential"
PREFIX = "seq"

SERVICES = ["boiler service", "chimney sweep", "window cleaning", "gutter clearing", "carpet cleaning",
            "pest inspection", "solar panel check", "fence repair", "drain survey", "oven deep clean"]
ACTIVITIES = ["pottery class", "piano lesson", "swim coaching", "life drawing", "salsa night", "origami hour",
              "woodwork workshop"]
JOBS = ["kitchen refit", "roof repair", "loft insulation", "patio build", "bathroom retile", "garage door",
        "garden shed"]
AIRLINES = ["Skyline Air", "Kestrel Air", "Tidewater Airways", "Juniper Air", "Bluebarrow Air"]
RESTAURANTS = ["Lantern Bistro", "Copperleaf Café", "Quayside Market", "Juniper Hall", "Harbourside Grill",
               "Fernhill Kitchen", "Maple Row Tavern"]
STREETS = ["Quarry Lane", "Ashgrove Close", "Millrace Way", "Tanner Court", "Juniper Row", "Willow Mews"]
DOCS = [("lease summary", "security deposit", "monthly rent"), ("insurance policy", "excess", "annual premium"),
        ("tuition letter", "registration fee", "term fee"), ("service contract", "setup fee", "monthly fee")]
ITEMS = ["oat milk", "lightbulbs", "batteries", "stamps", "dish soap", "coffee filters", "bin bags"]
THINGS = ["parking permit", "library card", "boat licence", "allotment lease", "bike registration", "club membership"]
EVENT_NAMES = ["Harvest Fair", "Lantern Night", "Riverside Film Night", "Winter Market", "Open Studio Evening",
               "Jazz in the Hall"]
TITLES = ["Team lunch", "Budget review", "Planning session", "Design critique", "Supplier call", "Book club"]
VOUCHERS = [10, 15, 20, 25]

# Last four digits of the canonical assistant-lane contacts' phones (all three world seeds).
RESERVED_TAILS = {"0166", "0123", "0102", "0144", "0177", "0190", "0133"}

# First names of the canonical contacts across the three world seeds (a seeded contact must not share one).
RESERVED_NAMES = {"Mom", "Dad", "Maya", "Alex", "Kenji", "Jules", "Sam", "Omar", "Tom", "Wren", "Chloe", "Priya", "Kai",
                  "Priyanka"}

UNCH_AST = ["calendar", "reminders", "notes", "messages_sent", "mail_sent", "message_drafts", "mail_drafts",
            "scheduled_sends"]
UNCH_NEU = ["calendar", "reminders", "notes", "messages_sent", "mail_sent", "message_drafts", "mail_drafts",
            "scheduled_sends", "files", "contacts", "mail"]

# Padding that pushes the useful part of a message past a 160-character search snippet (200 for web).
FILLER = ("Thank you for choosing us. We value your custom and want everything to run smoothly, so please read the "
          "notes below carefully and keep this message until everything is complete. If anything looks wrong, "
          "just reply and a member of the team will be glad to help you out. ")
assert len(FILLER) > 240

DATE_FACTS = {1: "tomorrow", 2: "in_2_days", 3: "in_3_days", 7: "in_7_days"}


# ------------------------------------------------------------------ helpers
def _fx(obj, *, skip=("facts", "id")):
    """Turn `[[name]]` into `{name}` everywhere except in the facts block."""
    if isinstance(obj, dict):
        return {k: (v if k in skip else _fx(v, skip=())) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fx(v, skip=()) for v in obj]
    if isinstance(obj, str):
        return obj.replace("[[", "{").replace("]]", "}")
    return obj


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower().replace("é", "e"))


def _dom(name: str) -> str:
    return f"{_slug(name)}.example.com"


def _tf(p: str, lo: int = 13, hi: int = 20, mins=("15", "30", "45")) -> dict:
    """Facts for one afternoon/evening time: hour, minute, and the 12-hour hour."""
    return {f"{p}_h": {"int": [lo, hi]}, f"{p}_m": {"choice": list(mins)}, f"{p}_h12": {"add": [f"{p}_h", -12]}}


def _tre(p: str) -> str:
    """Regex for the time of prefix `p` in either 12-hour or 24-hour form."""
    return rf"(?<![\d:])(?:[[{p}_h12]]:[[{p}_m]]|[[{p}_h]]:[[{p}_m]])(?!\d)"


def _ts(p: str, style: str) -> str:
    """The time as prose text: '2:30 PM' or '14:30'."""
    return f"[[{p}_h12]]:[[{p}_m]] PM" if style == "12" else f"[[{p}_h]]:[[{p}_m]]"


def _money(p: str, lo: int, hi: int, exclude=()) -> dict:
    spec = {"int": [lo, hi]}
    if exclude:
        spec["exclude"] = list(exclude)
    return {p: spec, f"{p}_s": {"template": "{" + p + ":,}"}}


def _mre(p: str) -> str:
    """Regex for an amount written 4250, 4,250 or $4,250.00."""
    return rf"(?<![\d,.])(?:[[{p}]]|[[{p}_s]])(?!\d)"


def _contacts(rng: random.Random, cids: list[str]) -> list[dict]:
    names, phones = set(RESERVED_NAMES), set(RESERVED_TAILS)
    out = []
    for cid in cids:
        c = contact(rng, cid, names, phones)
        # keep the first word of every name unique and clear of the canonical world
        out.append(c)
    return out


def _ids(rng: random.Random, prefix: str, k: int) -> list[str]:
    """Distinct record ids, drawn per case. (bind.py used to skip facts under a nested `id` key, so an id cannot
    be a per-trial fact; it varies per case instead.)"""
    out: list[str] = []
    while len(out) < k:
        v = f"{prefix}-{rng.randint(1000, 9999)}"
        if v not in out:
            out.append(v)
    return out


def _first(c: dict) -> str:
    return c["name"].split()[0]


def _pick(rng: random.Random, options: list[str]) -> str:
    return rng.choice(options)


def _ast(split, tpl, n, sub, prompt, facts, seed, expect, final_state, oracle, trap, *, grounded=None,
         forbid=None, touch=(), tags=None, recipients=None, distractor=False):
    calls = sum(len(s.get("calls") or []) for s in oracle)
    case = {"id": case_id(PREFIX, "assistant", split, tpl, n), "lane": "assistant", "family": FAMILY, "sub": sub,
            "tags": list(tags or []), "prompt": prompt, "facts": facts, "world": {"seed_records": seed},
            "expect": expect}
    if grounded:
        case["grounded_args"] = grounded
    case["forbid"] = list(forbid or [])
    case["final_state"] = final_state
    case["unchanged"] = [c for c in UNCH_AST if c not in touch]
    if recipients:
        case["intended_recipients"] = recipients
    case["optimal"] = {"calls": calls, "steps": calls + 1}
    case["oracle"] = oracle
    case["trap"] = trap
    return _fx(case)


def _neu(split, tpl, n, sub, prompt, facts, seed, expect, final_state, oracle, trap, *, grounded=None,
         forbid=None, touch=(), tags=None, recipients=None):
    calls = sum(len(s.get("calls") or []) for s in oracle)
    case = {"id": case_id(PREFIX, "neutral", split, tpl, n), "lane": "neutral", "family": FAMILY, "sub": sub,
            "tags": list(tags or []), "prompt": prompt, "facts": facts, "world": {"seed_records": seed},
            "expect": expect}
    if grounded:
        case["grounded_args"] = grounded
    case["forbid"] = list(forbid or [])
    case["final_state"] = final_state
    case["unchanged"] = [c for c in UNCH_NEU if c not in touch]
    if recipients:
        case["intended_recipients"] = recipients
    case["optimal"] = {"calls": calls, "steps": calls + 1}
    case["oracle"] = oracle
    case["trap"] = trap
    return _fx(case)


def _call(name: str, **arguments) -> dict:
    return {"name": name, "arguments": arguments}


def _step(*calls: dict) -> dict:
    return {"calls": list(calls)}


# ================================================================== assistant: depth 2
def a2_emailevent(split, rng, n):
    """A visit time in an email -> a calendar event at that time."""
    company, other = rng.sample(COMPANIES, 2)
    service = _pick(rng, SERVICES)
    title = service.capitalize() + " visit"
    when = _ts("t", rng.choice(["12", "24"]))
    prompt = rng.choice([
        f"{company} emailed me about when they're coming for the {service}. Put the visit in my calendar as '{title}'.",
        f"Add the {service} visit from {company}'s email to my calendar, and call it '{title}'.",
        f"Check the email from {company} for when the {service} is happening and put it in my calendar as '{title}'."])
    seed = {"mail": [
        {"id": f"m-9{n}01", "from": f"{company} <service@{_dom(company)}>", "subject": f"Your {service} visit ([[ref]])",
         "body": f"Hello, this confirms your {service} visit. Our engineer will arrive tomorrow at {when}. "
                 f"Please make sure someone is home. Job ref [[ref]].", "received": "-20m"},
        {"id": f"m-9{n}02", "from": f"{other} <news@{_dom(other)}>", "subject": f"{other} autumn offers",
         "body": "Our shop opens at [[o_h]]:00 AM on Saturday mornings. See you soon.", "received": "-40m"}]}
    facts = {**_tf("t"), "ref": {"code": "@@####"}, "o_h": {"int": [9, 11]}}
    return _ast(split, "emailevent", n, "depth2", prompt, facts, seed,
                [{"any_of": ["view_emails", "summarize_emails"]},
                 {"tool": "add_calendar_event", "args": {"title": {"icontains": service}, "when_iso": "+1d [[t_h]]:[[t_m]]"}}],
                [{"in": "calendar", "where": {"title": {"icontains": service}},
                  "expect": {"count": 1, "start": "+1d [[t_h]]:[[t_m]]"}}],
                [_step(_call("view_emails", query=company)),
                 _step(_call("add_calendar_event", title=title, when_iso="[[tomorrow_iso]]T[[t_h]]:[[t_m]]", duration_min=60)),
                 {"text": f"Added '{title}' for tomorrow at {when}."}],
                "the visit time exists only in the email; a guessed time puts the event at the wrong hour",
                touch=("calendar",), forbid=["cancel_event"])


def a2_timetext(split, rng, n):
    """A time read from a reminder or an event -> a text that states it."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    if n % 2:
        errand = _pick(rng, ERRANDS)
        seed = {"contacts": [c], "reminders": [{"id": f"r-9{n}1", "title": errand.capitalize(), "due": "+1d [[t_h]]:[[t_m]]"}]}
        prompt = rng.choice([f"Text {first} what time my reminder to {errand} is set for.",
                             f"{first} asked when I'm doing the '{errand}' thing. Look at my reminder and text them the time."])
        steps = [_step(_call("search_reminders", query=errand)),
                 _step(_call("send_message", to=first, text=f"My reminder to {errand} is set for [[t_h12]]:[[t_m]] PM tomorrow.")),
                 {"text": f"Texted {first} the time."}]
        expect = [{"any_of": ["search_reminders", "get_upcoming"]},
                  {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": _tre("t")}}}]
        trap = "the reminder's due time is only in the reminder list; a guessed time is wrong"
    else:
        activity = _pick(rng, ACTIVITIES)
        seed = {"contacts": [c], "calendar": [{"id": f"ev-9{n}1", "title": activity.capitalize(), "start": "+1d [[t_h]]:[[t_m]]",
                                                "end": "+1d [[e_h]]:[[t_m]]"}]}
        prompt = rng.choice([f"Text {first} what time my {activity} is tomorrow.",
                             f"{first} wants to know when my {activity} starts tomorrow. Check my calendar and text them."])
        steps = [_step(_call("get_upcoming", days=2, query=activity)),
                 _step(_call("send_message", to=first, text=f"My {activity} starts at [[t_h12]]:[[t_m]] PM tomorrow.")),
                 {"text": f"Texted {first} the start time."}]
        expect = [{"any_of": ["get_upcoming", "get_my_day_plan"]},
                  {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": _tre("t")}}}]
        trap = "the event's start time is only in the calendar; a guessed time is wrong"
    facts = {**_tf("t", 13, 18), "e_h": {"add": ["t_h", 1]}}
    return _ast(split, "timetext", n, "depth2", prompt, facts, seed, expect,
                [{"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}},
                  "expect": {"count": 1, "text": {"regex": _tre("t")}}}],
                steps, trap, touch=("messages_sent",), recipients=[c["id"]], forbid=["draft_message"])


def a2_msgfigure(split, rng, n):
    """A figure in a vendor's text -> an email that states it."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    job, other_job = rng.sample(JOBS, 2)
    vendor, other_vendor = rng.sample(COMPANIES, 2)
    seed = {"contacts": [c], "messages": [
        {"id": f"t-9{n}1", "thread": vendor, "from": vendor, "received": "-30m", "unread": True,
         "text": f"Your {job} quote came to [[amt]] dollars, valid until Friday."},
        {"id": f"t-9{n}2", "thread": other_vendor, "from": other_vendor, "received": "-35m", "unread": True,
         "text": f"Your {other_job} quote came to [[amt2]] dollars, valid until Monday."}]}
    prompt = rng.choice([f"{vendor} texted me the {job} quote. Email {first} the amount.",
                         f"{first} asked what the {job} is going to cost. {vendor} sent me the figure by text; email {first} the quote."])
    facts = {**_money("amt", 1200, 9800), **_money("amt2", 1200, 9800, exclude=["amt"])}
    return _ast(split, "msgfigure", n, "depth2", prompt, facts, seed,
                [{"any_of": ["view_messages", "summarize_messages"]},
                 {"tool": "send_email", "args": {"to": {"icontains": first}, "body": {"regex": _mre("amt")}}}],
                [{"in": "mail_sent", "where": {"contact_ids": {"icontains": c["id"]}},
                  "expect": {"count": 1, "body": {"all_of": [{"regex": _mre("amt")}, {"not_contains": ["[[amt2]]", "[[amt2_s]]"]}]}}}],
                [_step(_call("view_messages", query=vendor)),
                 _step(_call("send_email", to=first, subject=f"{job.capitalize()} quote", body=f"Hi {first}, the quote for the {job} is [[amt_s]] dollars.")),
                 {"text": f"Emailed {first} the {job} quote."}],
                "the figure lives only in the vendor's text (seeded per trial); a second text holds a decoy figure",
                touch=("mail_sent",), recipients=[c["id"]], forbid=["draft_email"])


def a2_msgreminder(split, rng, n):
    """A courier text gives a reference and a pickup time -> a reminder with both."""
    courier = _pick(rng, COMPANIES)
    place = _pick(rng, PLACES)
    prompt = rng.choice([f"{courier} texted about my parcel. Set a reminder to collect it at the time they said, with the reference in the title.",
                         f"Make a reminder for collecting my {courier} parcel at the time in their text, and put the parcel reference in the title."])
    seed = {"messages": [{"id": f"t-9{n}1", "thread": courier, "from": courier, "received": "-25m", "unread": True,
                          "text": f"Parcel [[ref]] is ready for collection from {place} after {_ts('t', rng.choice(['12', '24']))} tomorrow."}]}
    facts = {**_tf("t"), "ref": {"code": "@@####"}}
    return _ast(split, "msgreminder", n, "depth2", prompt, facts, seed,
                [{"any_of": ["view_messages", "summarize_messages"]},
                 {"tool": "add_reminder", "args": {"title": {"icontains": "[[ref]]"}, "when_iso": "+1d [[t_h]]:[[t_m]]"}}],
                [{"in": "reminders", "where": {"title": {"icontains": "[[ref]]"}},
                  "expect": {"count": 1, "due": "+1d [[t_h]]:[[t_m]]"}}],
                [_step(_call("view_messages", query=courier)),
                 _step(_call("add_reminder", title=f"Collect parcel [[ref]] from {place}", when_iso="[[tomorrow_iso]]T[[t_h]]:[[t_m]]")),
                 {"text": "Reminder set for the parcel pickup."}],
                "the reference and pickup time exist only in the text; a guess cannot match the seeded reference",
                touch=("reminders",))


def a2_emailaddr(split, rng, n):
    """An address in an email -> an email sent to exactly that address."""
    company = _pick(rng, COMPANIES)
    doc = _pick(rng, ["signed form", "meter reading", "photo ID copy", "signed quote"])
    prompt = rng.choice([f"{company} emailed asking me to send them my {doc}. Email it to the address they gave, quoting their reference in the subject.",
                         f"Reply to {company}'s request for my {doc}, but send it to the address in their email and put their reference in the subject."])
    seed = {"mail": [{"id": f"m-9{n}01", "from": f"{company} <noreply@{_dom(company)}>", "subject": "Action needed on your account",
                      "body": f"Please email your {doc} to [[box]]@{_dom(company)} by Friday, quoting reference [[ref]].",
                      "received": "-20m"}]}
    facts = {"box": {"code": "intake-####"}, "ref": {"code": "RF@@####"}}
    return _ast(split, "emailaddr", n, "depth2", prompt, facts, seed,
                [{"any_of": ["view_emails", "summarize_emails"]},
                 {"tool": "send_email", "args": {"to": {"icontains": "[[box]]@"}, "subject": {"icontains": "[[ref]]"}}}],
                [{"in": "mail_sent", "where": {"to": {"icontains": "[[box]]@"}},
                  "expect": {"count": 1, "subject": {"icontains": "[[ref]]"}}}],
                [_step(_call("view_emails", query=company)),
                 _step(_call("send_email", to=f"[[box]]@{_dom(company)}", subject="Re: [[ref]]", body=f"Hello, my {doc} is attached. Reference [[ref]].")),
                 {"text": f"Sent my {doc} to {company}."}],
                "the destination address and reference exist only in the email; a guessed address goes to the wrong place",
                grounded=[{"tool": "send_email", "arg": "to"}], touch=("mail_sent",), forbid=["draft_email"])


# ================================================================== assistant: depth 3
def a3_flight(split, rng, n):
    """Booking email -> calendar event at the landing time -> a text with that time."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    airline = _pick(rng, AIRLINES)
    style = rng.choice(["12", "24"])
    prompt = rng.choice([f"Put my {airline} flight from the booking email in my calendar and text {first} the landing time.",
                         f"Add my {airline} flight to my calendar for the time it lands, then tell {first} by text when I land."])
    seed = {"contacts": [c], "mail": [{"id": f"m-9{n}01", "from": f"{airline} <noreply@{_dom(airline)}>",
                                       "subject": "Booking confirmed [[fno]]", "received": "-20m",
                                       "body": f"Departs 08:05, arrives {_ts('t', style)} local tomorrow. Seat 14C. Flight [[fno]]."}]}
    facts = {**_tf("t"), "fno": {"code": "SK####"}}
    return _ast(split, "flight", n, "depth3", prompt, facts, seed,
                [{"any_of": ["view_emails", "summarize_emails"]},
                 {"tool": "add_calendar_event", "args": {"title": {"icontains": "[[fno]]"}, "when_iso": "+1d [[t_h]]:[[t_m]]"}},
                 {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": _tre("t")}}}],
                [{"in": "calendar", "where": {"title": {"icontains": "[[fno]]"}}, "expect": {"count": 1, "start": "+1d [[t_h]]:[[t_m]]"}},
                 {"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}},
                  "expect": {"count": 1, "text": {"regex": _tre("t")}}}],
                [_step(_call("view_emails", query=airline)),
                 _step(_call("add_calendar_event", title=f"{airline} flight [[fno]] lands", when_iso="[[tomorrow_iso]]T[[t_h]]:[[t_m]]", duration_min=30)),
                 _step(_call("send_message", to=first, text=f"My flight [[fno]] lands at {_ts('t', style)} tomorrow.")),
                 {"text": f"Added the flight to your calendar and texted {first}."}],
                "the landing time is only in the booking email; the event and the text both depend on it",
                touch=("calendar", "messages_sent"), recipients=[c["id"]], forbid=["cancel_event", "draft_message"])


def a3_doc(split, rng, n):
    """A text says where a document is saved -> read it -> email a figure from it."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    sender = _pick(rng, COMPANIES)
    kind, wanted, other = _pick(rng, DOCS)
    prompt = rng.choice([f"Email {first} the {wanted} from my {kind}; {sender} texted me where the file is saved.",
                         f"{first} needs the {wanted} from my {kind}. {sender} sent me a text saying where the document is; email {first} the figure."])
    facts = {"code": {"code": "####"}, **_money("amt", 1000, 3600), **_money("oth", 900, 2400, exclude=["amt"])}
    path = f"~/Documents/{_slug(kind)}-[[code]].txt"
    seed = {"contacts": [c],
            "messages": [{"id": f"t-9{n}1", "thread": sender, "from": sender, "received": "-30m", "unread": True,
                          "text": f"Your {kind} is saved at {path}"}],
            "files": [{"path": path,
                       "text": f"{kind.capitalize()}. {other.capitalize()}: [[oth]] dollars. {wanted.capitalize()}: [[amt]] dollars. Valid from next month."}]}
    return _ast(split, "doc", n, "depth3", prompt, facts, seed,
                [{"any_of": ["view_messages", "summarize_messages"]}, {"tool": "read_file", "args": {"path": {"icontains": f"{_slug(kind)}-[[code]]"}}},
                 {"tool": "send_email", "args": {"to": {"icontains": first}, "body": {"regex": _mre("amt")}}}],
                [{"in": "mail_sent", "where": {"contact_ids": {"icontains": c["id"]}},
                  "expect": {"count": 1, "body": {"all_of": [{"regex": _mre("amt")}, {"not_contains": ["[[oth]]", "[[oth_s]]"]}]}}}],
                [_step(_call("view_messages", query=sender)),
                 _step(_call("read_file", path=path)),
                 _step(_call("send_email", to=first, subject=f"The {wanted}", body=f"Hi {first}, the {wanted} is [[amt_s]] dollars.")),
                 {"text": f"Emailed {first} the {wanted}."}],
                "the file name exists only in the text and the figure only in the file; the file also holds a decoy figure",
                grounded=[{"tool": "read_file", "arg": "path"}], touch=("mail_sent",), recipients=[c["id"]], forbid=["draft_email"])


def a3_moveevent(split, rng, n):
    """Read an event's time -> move it an hour later -> text the new time."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    activity = _pick(rng, ACTIVITIES)
    prompt = rng.choice([f"Push my {activity} back an hour and let {first} know the new time.",
                         f"Move my {activity} an hour later, then text {first} what time it is now."])
    facts = {"s_h": {"int": [13, 18]}, "s_m": {"choice": ["15", "30", "45"]}, "n_h": {"add": ["s_h", 1]},
             "n_h12": {"add": ["s_h", -11]}, "e_h": {"add": ["s_h", 2]}}
    seed = {"contacts": [c], "calendar": [{"id": f"ev-9{n}1", "title": activity.capitalize(), "start": "+2d [[s_h]]:[[s_m]]", "end": "+2d [[n_h]]:[[s_m]]"}]}
    tre = r"(?<![\d:])(?:[[n_h12]]:[[s_m]]|[[n_h]]:[[s_m]])(?!\d)"
    return _ast(split, "moveevent", n, "depth3", prompt, facts, seed,
                [{"any_of": ["get_upcoming", "get_my_day_plan"]},
                 {"tool": "update_event", "args": {"title": {"icontains": activity}, "when_iso": "+2d [[n_h]]:[[s_m]]"}},
                 {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": tre}}}],
                [{"in": "calendar", "where": {"id": f"ev-9{n}1"}, "expect": {"count": 1, "start": "+2d [[n_h]]:[[s_m]]", "end": "+2d [[e_h]]:[[s_m]]"}},
                 {"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}}, "expect": {"count": 1, "text": {"regex": tre}}}],
                [_step(_call("get_upcoming", days=3, query=activity)),
                 _step(_call("update_event", title=activity.capitalize(), when_iso="[[in_2_days_iso]]T[[n_h]]:[[s_m]]")),
                 _step(_call("send_message", to=first, text=f"My {activity} is now at [[n_h12]]:[[s_m]] PM.")),
                 {"text": f"Moved the {activity} an hour later and told {first}."}],
                "the old start time is only in the calendar; the move and the text both derive from it",
                touch=("calendar", "messages_sent"), tags=["datetime"], recipients=[c["id"]],
                forbid=["cancel_event", "add_calendar_event", "draft_message"])


def a3_proposal(split, rng, n):
    """A friend's text proposes a time -> calendar event -> a text confirming that time."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    activity = _pick(rng, ["climbing", "a bike ride", "board games", "a swim", "coffee at the market", "a gallery visit"])
    prompt = rng.choice([f"{first} texted me a time for {activity}. Put it in my calendar and confirm to them.",
                         f"Check {first}'s text about {activity}, add it to my calendar for that time, and text them back that I'll be there."])
    style = rng.choice(["12", "24"])
    seed = {"contacts": [c], "messages": [{"id": f"t-9{n}1", "thread": c["name"], "from": c["name"], "received": "-30m", "unread": True,
                                           "text": f"Fancy {activity} tomorrow? I can do {_ts('t', style)}."}]}
    return _ast(split, "proposal", n, "depth3", prompt, _tf("t"), seed,
                [{"any_of": ["view_messages", "summarize_messages"]},
                 {"tool": "add_calendar_event", "args": {"when_iso": "+1d [[t_h]]:[[t_m]]"}},
                 {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": _tre("t")}}}],
                [{"in": "calendar", "where": {"title": {"icontains": activity.split()[-1]}}, "expect": {"count": 1, "start": "+1d [[t_h]]:[[t_m]]"}},
                 {"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}},
                  "expect": {"count": 1, "text": {"regex": _tre("t")}}}],
                [_step(_call("view_messages", conversation=c["name"])),
                 _step(_call("add_calendar_event", title=f"{activity.capitalize()} with {first}", when_iso="[[tomorrow_iso]]T[[t_h]]:[[t_m]]", duration_min=60)),
                 _step(_call("send_message", to=first, text=f"See you tomorrow at {_ts('t', style)}!")),
                 {"text": f"Added it to your calendar and confirmed with {first}."}],
                "the proposed time is only in the friend's text; the event and the reply both repeat it",
                touch=("calendar", "messages_sent"), recipients=[c["id"]], forbid=["cancel_event", "draft_message"])


def a3_bill(split, rng, n):
    """A total in an email -> arithmetic -> a text with the result."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    company = _pick(rng, RESTAURANTS)
    voucher = _pick(rng, [str(v) for v in VOUCHERS])
    prompt = rng.choice([f"{company} emailed my receipt. Take my ${voucher} voucher off the total and text {first} what's left to pay.",
                         f"Work out what I owe for the {company} bill after my ${voucher} voucher and text {first} the amount."])
    seed = {"contacts": [c], "mail": [{"id": f"m-9{n}01", "from": f"{company} <receipts@{_dom(company)}>", "subject": f"Your receipt from {company}",
                                       "body": "Thanks for dining with us. Items: 4. Total: [[total]] dollars including service.", "received": "-20m"}]}
    facts = {"total": {"int": [80, 400]}, "due": {"add": ["total", -int(voucher)]}}
    pat = r"(?<![\d.])\$?[[due]](?:\.00)?(?!\d)"
    return _ast(split, "bill", n, "depth3", prompt, facts, seed,
                [{"any_of": ["view_emails", "summarize_emails"]},
                 {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": pat}}}],
                [{"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}}, "expect": {"count": 1, "text": {"regex": pat}}}],
                [_step(_call("view_emails", query=company)),
                 _step(_call("calculate", expression=f"[[total]] - {voucher}")),
                 _step(_call("send_message", to=first, text=f"After my voucher the bill leaves [[due]] dollars to pay.")),
                 {"text": f"Texted {first} that [[due]] dollars is left."}],
                "the total is only in the email and the amount to send is derived from it; a guess or the raw total fails",
                touch=("messages_sent",), recipients=[c["id"]], forbid=["draft_message"])


# ================================================================== assistant: depth 4
def a4_dinner(split, rng, n):
    """Text points to an email -> read it -> calendar event -> text back the time."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    place = _pick(rng, RESTAURANTS)
    style = rng.choice(["12", "24"])
    prompt = rng.choice([f"{first} texted about the dinner booking. Find the details, add it to my calendar and text {first} back the time.",
                         f"Look at {first}'s message about dinner, get the booking details from the email it mentions, put it in my calendar and confirm the time to {first}."])
    seed = {"contacts": [c],
            "messages": [{"id": f"t-9{n}1", "thread": c["name"], "from": c["name"], "received": "-30m", "unread": True,
                          "text": f"The {place} booking confirmation is in your email. Can you put it in the diary and tell me the time?"}],
            "mail": [{"id": f"m-9{n}01", "from": f"{place} <bookings@{_dom(place)}>", "subject": "Your table is confirmed", "received": "-20m",
                      "body": f"Table for [[k]] confirmed for tomorrow at {_ts('t', style)}. Booking ref [[ref]]."}]}
    facts = {**_tf("t"), "k": {"int": [2, 6]}, "ref": {"code": "@@####"}}
    return _ast(split, "dinner", n, "depth4", prompt, facts, seed,
                [{"any_of": ["view_messages", "summarize_messages"]}, {"any_of": ["view_emails", "summarize_emails"]},
                 {"tool": "add_calendar_event", "args": {"when_iso": "+1d [[t_h]]:[[t_m]]"}},
                 {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": _tre("t")}}}],
                [{"in": "calendar", "where": {"title": {"icontains": place.split()[0]}}, "expect": {"count": 1, "start": "+1d [[t_h]]:[[t_m]]"}},
                 {"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}},
                  "expect": {"count": 1, "text": {"regex": _tre("t")}}}],
                [_step(_call("view_messages", conversation=c["name"])),
                 _step(_call("view_emails", query=place)),
                 _step(_call("add_calendar_event", title=f"Dinner at {place}", when_iso="[[tomorrow_iso]]T[[t_h]]:[[t_m]]", duration_min=120)),
                 _step(_call("send_message", to=first, text=f"Booked, dinner at {place} tomorrow at {_ts('t', style)}.")),
                 {"text": f"Added dinner to your calendar and told {first} the time."}],
                "the restaurant is named only in the text and the time only in the email it points to",
                touch=("calendar", "messages_sent"), recipients=[c["id"]], forbid=["cancel_event", "draft_message"])


def a4_invoice(split, rng, n):
    """Email points to an invoice file -> read it -> reminder at its due time -> text the amount and due time."""
    c, = _contacts(rng, [f"c-9{n}1"])
    first = _first(c)
    company = _pick(rng, COMPANIES)
    prompt = rng.choice([f"{company} sent me an invoice by email. Set a reminder to pay it at the due time with the invoice reference in the title, and text {first} how much it is.",
                         f"Take the {company} invoice from my email, remind me to pay it at its due time (put the invoice reference in the title), and text {first} the amount."])
    facts = {**_tf("t"), "code": {"code": "####"}, "ref": {"code": "INV-####"}, **_money("amt", 300, 4800)}
    seed = {"contacts": [c],
            "mail": [{"id": f"m-9{n}01", "from": f"{company} <accounts@{_dom(company)}>", "subject": "Invoice [[ref]] attached", "received": "-20m",
                      "body": "Your invoice [[ref]] is ready. The full invoice is saved at ~/Documents/inv-[[code]].txt on your account."}],
            "files": [{"path": "~/Documents/inv-[[code]].txt",
                       "text": f"{company}. Invoice [[ref]]. Amount due: [[amt]] dollars. Due: [[in_3_days_iso]] at [[t_h]]:[[t_m]]. Pay by bank transfer."}]}
    msg = {"all_of": [{"regex": _mre("amt")}]}
    return _ast(split, "invoice", n, "depth4", prompt, facts, seed,
                [{"any_of": ["view_emails", "summarize_emails"]}, {"tool": "read_file", "args": {"path": {"icontains": "inv-[[code]]"}}},
                 {"tool": "add_reminder", "args": {"title": {"icontains": "[[ref]]"}, "when_iso": "+3d [[t_h]]:[[t_m]]"}},
                 {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": _mre("amt")}}}],
                [{"in": "reminders", "where": {"title": {"icontains": "[[ref]]"}}, "expect": {"count": 1, "due": "+3d [[t_h]]:[[t_m]]"}},
                 {"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}}, "expect": {"count": 1, "text": msg}}],
                [_step(_call("view_emails", query=company)),
                 _step(_call("read_file", path="~/Documents/inv-[[code]].txt")),
                 _step(_call("add_reminder", title=f"Pay {company} invoice [[ref]]", when_iso="[[in_3_days_iso]]T[[t_h]]:[[t_m]]")),
                 _step(_call("send_message", to=first, text=f"The {company} invoice is [[amt_s]] dollars, due in three days.")),
                 {"text": f"Set the reminder and told {first} the amount."}],
                "the file path is only in the email, then the amount and due time only in the file",
                grounded=[{"tool": "read_file", "arg": "path"}], touch=("reminders", "messages_sent"),
                recipients=[c["id"]], forbid=["draft_message"])


def a4_viewing(split, rng, n):
    """Event names the owner -> owner's email gives the address -> reminder 30 min before -> text the time."""
    first, last = person(rng, set(RESERVED_NAMES))
    full = f"{first} {last}"
    c = {"id": f"c-9{n}1", "name": full, "phone": phone(rng, set(RESERVED_TAILS)), "email": email(first, last)}
    street = _pick(rng, STREETS)
    prompt = rng.choice(["I have a flat viewing tomorrow. Get the address from the owner's email, set a reminder to leave 30 minutes before it starts with the address in the title, and text the owner that I'll be there at the start time.",
                         "For my flat viewing tomorrow: set a reminder half an hour before the start, put the address from the owner's email in its title, and text the owner the start time."])
    facts = {"h": {"int": [13, 18]}, "h12": {"add": ["h", -12]}, "num": {"int": [11, 98]}}
    seed = {"contacts": [c],
            "calendar": [{"id": f"ev-9{n}1", "title": f"Flat viewing with {full}", "start": "+1d [[h]]:30", "end": "+1d [[h]]:30"}],
            "mail": [{"id": f"m-9{n}01", "from": f"{full} <{c['email']}>", "subject": "The flat", "received": "-20m",
                      "body": f"Hello, the flat is at [[num]] {street}. Ring the top bell when you arrive."}]}
    seed["calendar"][0]["end"] = "+1d [[e_h]]:30"
    facts["e_h"] = {"add": ["h", 1]}
    tre = r"(?<![\d:])(?:[[h12]]:30|[[h]]:30)(?!\d)"
    return _ast(split, "viewing", n, "depth4", prompt, facts, seed,
                [{"any_of": ["get_upcoming", "get_my_day_plan"]}, {"any_of": ["view_emails", "summarize_emails"]},
                 {"tool": "add_reminder", "args": {"title": {"icontains": f"[[num]] {street}"}, "when_iso": "+1d [[h]]:00"}},
                 {"tool": "send_message", "args": {"to": {"icontains": first}, "text": {"regex": tre}}}],
                [{"in": "reminders", "where": {"title": {"icontains": f"[[num]] {street}"}}, "expect": {"count": 1, "due": "+1d [[h]]:00"}},
                 {"in": "messages_sent", "where": {"contact_ids": {"icontains": c["id"]}}, "expect": {"count": 1, "text": {"regex": tre}}}],
                [_step(_call("get_upcoming", days=2, query="viewing")),
                 _step(_call("view_emails", query=last)),
                 _step(_call("add_reminder", title=f"Leave for [[num]] {street}", when_iso="[[tomorrow_iso]]T[[h]]:00")),
                 _step(_call("send_message", to=first, text="See you tomorrow at [[h12]]:30 PM for the viewing.")),
                 {"text": f"Set the reminder for [[h]]:00 and texted {first}."}],
                "the owner is named only in the event, the address only in their email, the start time only in the event",
                touch=("reminders", "messages_sent"), tags=["datetime"], recipients=[c["id"]], forbid=["draft_message"])


def a4_quote(split, rng, n):
    """A reminder names a quote file -> read it -> email the address it names -> follow-up reminder."""
    company = _pick(rng, COMPANIES)
    job = _pick(rng, JOBS)
    prompt = rng.choice([f"Accept my {company} quote for the {job}: my reminder about it says where the details are. Then remind me tomorrow at 9am to chase it, with the quote reference in the reminder title.",
                         f"My reminder to accept the {company} quote says which file explains how. Do what the file says, then set a 9am reminder for tomorrow to chase it (quote reference in the title)."])
    facts = {"code": {"code": "####"}, "ref": {"code": "QT-####"}, "box": {"code": "orders-####"}, **_money("amt", 1200, 9800)}
    seed = {"reminders": [{"id": f"r-9{n}1", "title": f"Accept {company} quote for the {job}, details in ~/Documents/quote-[[code]].txt", "due": "+2d 17:00"}],
            "files": [{"path": "~/Documents/quote-[[code]].txt",
                       "text": f"To accept this quote, email [[box]]@{_dom(company)} quoting reference [[ref]] and the total of [[amt]] dollars."}]}
    return _ast(split, "quote", n, "depth4", prompt, facts, seed,
                [{"any_of": ["search_reminders", "get_upcoming"]}, {"tool": "read_file", "args": {"path": {"icontains": "quote-[[code]]"}}},
                 {"tool": "send_email", "args": {"to": {"icontains": "[[box]]@"}, "body": {"icontains": "[[ref]]"}}},
                 {"tool": "add_reminder", "args": {"title": {"icontains": "[[ref]]"}, "when_iso": "+1d 09:00"}}],
                [{"in": "mail_sent", "where": {"to": {"icontains": "[[box]]@"}},
                  "expect": {"count": 1, "body": {"all_of": [{"icontains": "[[ref]]"}, {"regex": _mre("amt")}]}}},
                 {"in": "reminders", "where": {"title": {"icontains": "[[ref]]"}}, "expect": {"count": 1, "due": "+1d 09:00"}}],
                [_step(_call("search_reminders", query=f"{company} quote")),
                 _step(_call("read_file", path="~/Documents/quote-[[code]].txt")),
                 _step(_call("send_email", to=f"[[box]]@{_dom(company)}", subject="Quote [[ref]] accepted", body="I accept quote [[ref]] for a total of [[amt_s]] dollars.")),
                 _step(_call("add_reminder", title="Chase quote [[ref]]", when_iso="[[tomorrow_iso]]T09:00")),
                 {"text": "Accepted the quote and set a 9am reminder to chase it."}],
                "the path is only in the reminder, the address, reference and total only in the file",
                grounded=[{"tool": "read_file", "arg": "path"}, {"tool": "send_email", "arg": "to"}],
                touch=("mail_sent", "reminders"), forbid=["draft_email", "clear_reminders"])


# ================================================================== neutral: depth 2
def n2_calmove(split, rng, n):
    """Two events share a title; the right id is found by search and then updated."""
    title = _pick(rng, TITLES)
    eva, evb = _ids(rng, "ev", 2)
    prompt = rng.choice([f"Move my {title} on [[in_3_days_weekday]] to [[n_h]]:[[n_m]].",
                         f"Change the {title} that's on [[in_3_days_weekday]] so it starts at [[n_h]]:[[n_m]]."])
    facts = {"o_h": {"int": [9, 12]}, "o_m": {"choice": ["00", "30"]}, "o_e": {"add": ["o_h", 1]},
             "t_h": {"int": [13, 16]}, "t_m": {"choice": ["00", "30"]}, "t_e": {"add": ["t_h", 1]},
             "n_h": {"int": [17, 19]}, "n_m": {"choice": ["00", "15", "30", "45"]}}
    seed = {"calendar": [{"id": eva, "title": title, "start": "+1d [[o_h]]:[[o_m]]", "end": "+1d [[o_e]]:[[o_m]]"},
                         {"id": evb, "title": title, "start": "+3d [[t_h]]:[[t_m]]", "end": "+3d [[t_e]]:[[t_m]]"}]}
    return _neu(split, "calmove", n, "depth2", prompt, facts, seed,
                [{"tool": "calendar_search_events"}, {"tool": "calendar_update_event", "args": {"event_id": {"eq": evb}, "start": "+3d [[n_h]]:[[n_m]]"}}],
                [{"in": "calendar", "where": {"id": evb}, "expect": {"count": 1, "start": "+3d [[n_h]]:[[n_m]]"}},
                 {"in": "calendar", "where": {"id": eva}, "expect": {"count": 1, "start": "+1d [[o_h]]:[[o_m]]"}}],
                [_step(_call("calendar_search_events", query=title)),
                 _step(_call("calendar_update_event", event_id=evb, start="[[in_3_days_iso]]T[[n_h]]:[[n_m]]")),
                 {"text": f"Moved the {title} on [[in_3_days_weekday]]."}],
                "two events share a title; the id of the right one exists only in the search result",
                grounded=[{"tool": "calendar_update_event", "arg": "event_id"}], touch=("calendar",),
                forbid=["calendar_delete_event"])


def n2_forward(split, rng, n):
    """Two emails from one company; find the right message id, then forward it."""
    c, = _contacts(rng, [f"c-8{n}1"])
    first = _first(c)
    company = _pick(rng, COMPANIES)
    topic, decoy = rng.sample(["invoice", "delivery notice", "renewal offer", "price change", "account statement"], 2)
    ma, mb = _ids(rng, "mt", 2)
    prompt = rng.choice([f"Forward {company}'s {topic} email to {first}.", f"Send {first} the {topic} email I got from {company}."])
    seed = {"contacts": [c], "mail": [
        {"id": ma, "from": f"{company} <hello@{_dom(company)}>", "subject": f"Your {topic}", "body": f"Please find your {topic} below. Thank you.", "received": "-1d 10:00"},
        {"id": mb, "from": f"{company} <hello@{_dom(company)}>", "subject": f"Your {decoy}", "body": f"Please find your {decoy} below. Thank you.", "received": "-1d 11:00"}]}
    return _neu(split, "forward", n, "depth2", prompt, {}, seed,
                [{"tool": "email_search"}, {"tool": "email_forward", "args": {"message_id": {"eq": ma}, "to": {"icontains": first}}}],
                [{"in": "mail_sent", "where": {"in_reply_to": ma}, "expect": {"count": 1, "kind": "forward", "to": {"icontains": c["email"]}}},
                 {"in": "mail_sent", "where": {"in_reply_to": mb}, "expect": {"absent": True}}],
                [_step(_call("email_search", query=topic)),
                 _step(_call("email_forward", message_id=ma, to=[first])),
                 {"text": f"Forwarded the {topic} email to {first}."}],
                "the message id exists only in the search result; the other email from the same sender is a decoy",
                grounded=[{"tool": "email_forward", "arg": "message_id"}], touch=("mail_sent",),
                forbid=["email_send", "email_trash"], recipients=[c["id"]])


def n2_noteappend(split, rng, n):
    """Two list notes; find the right note id, then append to it."""
    a, b = rng.sample(["Shopping list", "Packing list", "Garden jobs", "Gift ideas", "Reading list"], 2)
    item = _pick(rng, ITEMS)
    na, nb = _ids(rng, "nt", 2)
    prompt = rng.choice([f"Add {item} to my {a.lower()} note.", f"Put {item} on the {a.lower()} note."])
    seed = {"notes": [{"id": na, "title": a, "body": "milk\nbread"}, {"id": nb, "title": b, "body": "passport\ncharger"}]}
    return _neu(split, "noteappend", n, "depth2", prompt, {}, seed,
                [{"tool": "notes_search"}, {"tool": "notes_append", "args": {"note_id": {"eq": na}, "text": {"icontains": item}}}],
                [{"in": "notes", "where": {"id": na}, "expect": {"count": 1, "body": {"icontains": item}}},
                 {"in": "notes", "where": {"id": nb}, "expect": {"body": {"not_contains": [item]}}}],
                [_step(_call("notes_search", query=a.split()[0])),
                 _step(_call("notes_append", note_id=na, text=item)),
                 {"text": f"Added {item} to your {a.lower()} note."}],
                "the note id exists only in the search result; a guessed id fails or edits the wrong note",
                grounded=[{"tool": "notes_append", "arg": "note_id"}], touch=("notes",), forbid=["notes_update", "notes_delete"])


# ================================================================== neutral: depth 3
def n3_emailevent(split, rng, n):
    """Search an email, read it in full (the details sit past the snippet), create the event."""
    company = _pick(rng, COMPANIES)
    service = _pick(rng, SERVICES)
    place = _pick(rng, PLACES)
    title = service.capitalize()
    mid, = _ids(rng, "mt", 1)
    prompt = rng.choice([f"{company}'s email has my appointment details. Put it in my calendar as '{title}'.",
                         f"Find the {company} email about my appointment and add it to my calendar as '{title}'."])
    facts = _tf("t")
    seed = {"mail": [{"id": mid, "from": f"{company} <bookings@{_dom(company)}>", "subject": f"Your appointment with {company}", "received": "-1d 15:00",
                      "body": FILLER + f"Your {service} appointment is on [[tomorrow_weekday]] at [[t_h]]:[[t_m]], at {place}. Please arrive five minutes early."}]}
    return _neu(split, "emailevent", n, "depth3", prompt, facts, seed,
                [{"tool": "email_search"}, {"tool": "email_read", "args": {"message_id": {"eq": mid}}},
                 {"tool": "calendar_create_event", "args": {"title": {"icontains": service}, "start": "+1d [[t_h]]:[[t_m]]"}}],
                [{"in": "calendar", "where": {"title": {"icontains": service}}, "expect": {"count": 1, "start": "+1d [[t_h]]:[[t_m]]"}}],
                [_step(_call("email_search", query=company)),
                 _step(_call("email_read", message_id=mid)),
                 _step(_call("calendar_create_event", title=title, start="[[tomorrow_iso]]T[[t_h]]:[[t_m]]", location=place)),
                 {"text": f"Added '{title}' for tomorrow."}],
                "the appointment time is past the search snippet; only email_read shows it",
                grounded=[{"tool": "email_read", "arg": "message_id"}], touch=("calendar",), forbid=["calendar_delete_event"])


def n3_msgfile(split, rng, n):
    """A text names a file -> read it -> text a figure from it."""
    c, = _contacts(rng, [f"c-8{n}1"])
    first = _first(c)
    sender = _pick(rng, COMPANIES)
    kind, wanted, other = _pick(rng, DOCS)
    prompt = rng.choice([f"Text {first} the {wanted} from my {kind}; {sender} texted me where it's saved.",
                         f"{first} asked for the {wanted}. {sender} told me by text where my {kind} is saved; text {first} the figure."])
    facts = {"code": {"code": "####"}, **_money("amt", 1000, 3600), **_money("oth", 900, 2400, exclude=["amt"])}
    path = f"~/Documents/{_slug(kind)}-[[code]].txt"
    old = f"~/Documents/{_slug(kind)}-old.txt"
    seed = {"contacts": [c],
            "messages": [{"id": "msg-801", "thread": sender, "from": sender, "received": "-30m", "unread": True,
                          "text": f"Your {kind} is saved as {path}"}],
            "files": [{"path": path, "kind": "file", "text": f"{kind.capitalize()}. {other.capitalize()}: [[oth]] dollars. {wanted.capitalize()}: [[amt]] dollars."},
                      {"path": old, "kind": "file", "text": f"Superseded. {wanted.capitalize()}: [[oth]] dollars."}]}
    return _neu(split, "msgfile", n, "depth3", prompt, facts, seed,
                [{"tool": "messages_search"}, {"tool": "files_read", "args": {"path": {"icontains": f"{_slug(kind)}-[[code]]"}}},
                 {"tool": "messages_send", "args": {"to": {"icontains": first}, "text": {"regex": _mre("amt")}}}],
                [{"in": "messages_sent", "where": {"contact_id": c["id"]}, "expect": {"count": 1, "text": {"all_of": [{"regex": _mre("amt")}, {"not_contains": ["[[oth]]"]}]}}}],
                [_step(_call("messages_search", query=kind)),
                 _step(_call("files_read", path=path)),
                 _step(_call("messages_send", to=first, text=f"The {wanted} is [[amt_s]] dollars.")),
                 {"text": f"Texted {first} the {wanted}."}],
                "the path is only in the text and the figure only in the file; an old file holds a decoy figure",
                grounded=[{"tool": "files_read", "arg": "path"}], touch=("messages_sent",), forbid=["files_write", "files_delete_permanent"],
                recipients=[c["id"]])


def n3_calshift(split, rng, n):
    """Find an event and its start -> move it an hour later by id -> text the new time."""
    c, = _contacts(rng, [f"c-8{n}1"])
    first = _first(c)
    title = _pick(rng, TITLES)
    ev, = _ids(rng, "ev", 1)
    prompt = rng.choice([f"Push my {title} tomorrow back an hour and text {first} the new start time.",
                         f"Move tomorrow's {title} an hour later, then let {first} know by text what time it starts now."])
    facts = {"s_h": {"int": [13, 18]}, "s_m": {"choice": ["00", "15", "30", "45"]}, "n_h": {"add": ["s_h", 1]},
             "e_h": {"add": ["s_h", 2]}}
    seed = {"contacts": [c], "calendar": [{"id": ev, "title": title, "start": "+1d [[s_h]]:[[s_m]]", "end": "+1d [[n_h]]:[[s_m]]"}]}
    tre = r"(?<![\d:])[[n_h]]:[[s_m]](?!\d)"
    return _neu(split, "calshift", n, "depth3", prompt, facts, seed,
                [{"tool": "calendar_search_events"}, {"tool": "calendar_update_event", "args": {"event_id": {"eq": ev}, "start": "+1d [[n_h]]:[[s_m]]"}},
                 {"tool": "messages_send", "args": {"to": {"icontains": first}, "text": {"regex": tre}}}],
                [{"in": "calendar", "where": {"id": ev}, "expect": {"count": 1, "start": "+1d [[n_h]]:[[s_m]]", "end": "+1d [[e_h]]:[[s_m]]"}},
                 {"in": "messages_sent", "where": {"contact_id": c["id"]}, "expect": {"count": 1, "text": {"regex": tre}}}],
                [_step(_call("calendar_search_events", query=title)),
                 _step(_call("calendar_update_event", event_id=ev, start="[[tomorrow_iso]]T[[n_h]]:[[s_m]]")),
                 _step(_call("messages_send", to=first, text=f"My {title} now starts at [[n_h]]:[[s_m]].")),
                 {"text": f"Moved the {title} an hour later and texted {first}."}],
                "the event id and old start exist only in the search result; the update and the text derive from it",
                grounded=[{"tool": "calendar_update_event", "arg": "event_id"}], touch=("calendar", "messages_sent"),
                tags=["datetime"], forbid=["calendar_delete_event", "calendar_create_event"], recipients=[c["id"]])


# ================================================================== neutral: depth 4
def n4_dinner(split, rng, n):
    """Text names a venue -> search the email -> read it -> create the event."""
    c, = _contacts(rng, [f"c-8{n}1"])
    first = _first(c)
    place = _pick(rng, RESTAURANTS)
    mid, = _ids(rng, "bk", 1)
    prompt = rng.choice([f"{first} texted about our dinner booking. Get the details from the email they mention and add it to my calendar.",
                         f"Add the dinner {first} mentioned in their text to my calendar, using the booking email for the time."])
    facts = {**_tf("t"), "ref": {"code": "@@####"}}
    seed = {"contacts": [c],
            "messages": [{"id": "msg-801", "thread": c["name"], "from": c["name"], "text": f"The {place} confirmation is in your email, can you add it to the diary?", "received": "-30m", "unread": True}],
            "mail": [{"id": mid, "from": f"{place} <bookings@{_dom(place)}>", "subject": "Your table is confirmed", "received": "-20m",
                      "body": FILLER + "Your table is confirmed for tomorrow at [[t_h]]:[[t_m]]. Booking ref [[ref]]."}]}
    return _neu(split, "dinner", n, "depth4", prompt, facts, seed,
                [{"tool": "messages_search"}, {"tool": "email_search"}, {"tool": "email_read", "args": {"message_id": {"eq": mid}}},
                 {"tool": "calendar_create_event", "args": {"start": "+1d [[t_h]]:[[t_m]]"}}],
                [{"in": "calendar", "where": {"title": {"icontains": place.split()[0]}}, "expect": {"count": 1, "start": "+1d [[t_h]]:[[t_m]]"}}],
                [_step(_call("messages_search", query="confirmation", thread=c["name"])),
                 _step(_call("email_search", query=place)),
                 _step(_call("email_read", message_id=mid)),
                 _step(_call("calendar_create_event", title=f"Dinner at {place}", start="[[tomorrow_iso]]T[[t_h]]:[[t_m]]", duration_minutes=120)),
                 {"text": f"Added dinner at {place} for tomorrow."}],
                "the venue is only in the text, the message id only in the search, the time only in the full email",
                grounded=[{"tool": "email_read", "arg": "message_id"}], touch=("calendar",), forbid=["calendar_delete_event", "messages_send"])


def n4_invoice(split, rng, n):
    """Email -> read it (file path) -> read the file (amount, due) -> create the reminder."""
    company = _pick(rng, COMPANIES)
    mid, = _ids(rng, "iv", 1)
    prompt = rng.choice([f"{company} sent an invoice by email. Set a reminder to pay it at the due time, with the invoice reference in the title.",
                         f"Make a reminder to pay the {company} invoice from my email: use the due time on the invoice and put its reference in the title."])
    facts = {**_tf("t"), "code": {"code": "####"}, "ref": {"code": "INV-####"}, **_money("amt", 300, 4800)}
    path = "~/Documents/inv-[[code]].txt"
    seed = {"mail": [{"id": mid, "from": f"{company} <accounts@{_dom(company)}>", "subject": "Invoice [[ref]]", "received": "-20m",
                      "body": FILLER + f"The full invoice is saved at {path}."}],
            "files": [{"path": path, "kind": "file",
                       "text": f"{company}. Invoice [[ref]]. Amount due: [[amt]] dollars. Due: [[in_3_days_iso]] at [[t_h]]:[[t_m]]."}]}
    return _neu(split, "invoice", n, "depth4", prompt, facts, seed,
                [{"tool": "email_search"}, {"tool": "email_read", "args": {"message_id": {"eq": mid}}},
                 {"tool": "files_read", "args": {"path": {"icontains": "inv-[[code]]"}}},
                 {"tool": "reminders_create", "args": {"title": {"icontains": "[[ref]]"}, "due": "+3d [[t_h]]:[[t_m]]"}}],
                [{"in": "reminders", "where": {"title": {"icontains": "[[ref]]"}}, "expect": {"count": 1, "due": "+3d [[t_h]]:[[t_m]]"}}],
                [_step(_call("email_search", query=company)),
                 _step(_call("email_read", message_id=mid)),
                 _step(_call("files_read", path=path)),
                 _step(_call("reminders_create", title=f"Pay {company} invoice [[ref]]", due="[[in_3_days_iso]]T[[t_h]]:[[t_m]]")),
                 {"text": "Reminder set for the due time."}],
                "the path is in the full email, the due time and reference only in the file",
                grounded=[{"tool": "files_read", "arg": "path"}, {"tool": "email_read", "arg": "message_id"}],
                touch=("reminders",), forbid=["reminders_delete"])


def n4_renewal(split, rng, n):
    """Reminder carries a ref -> the email with that ref names a file -> file gives the date -> move that same reminder."""
    thing, other = rng.sample(THINGS, 2)
    k = rng.choice([3, 7])
    day = DATE_FACTS[k]
    rid, oid = _ids(rng, "rm", 2)
    prompt = rng.choice([f"My '{thing}' reminder carries a reference for the renewal date. Move the reminder to 9am on that date.",
                         f"Find the renewal date for my {thing} (the reminder has the reference to look up) and set the reminder to that date at 9am."])
    facts = {"ref": {"code": "RN-####"}, "code": {"code": "####"}, **_money("fee", 20, 480)}
    path = "~/Documents/renew-[[code]].txt"
    seed = {"reminders": [{"id": rid, "title": f"Renew {thing} (ref [[ref]])", "due": "+10d 09:00"},
                          {"id": oid, "title": f"Renew {other}", "due": "+12d 09:00"}],
            "mail": [{"id": "mt-8101", "from": "Renewals Desk <renewals@example.org>", "subject": "Renewal reference [[ref]]",
                      "body": f"The renewal dates are in {path}.", "received": "-1d 09:00"}],
            "files": [{"path": path, "kind": "file",
                       "text": f"Renewals for the {thing} open on [[{day}_iso]]. Fee: [[fee]] dollars. Bring photo ID."}]}
    return _neu(split, "renewal", n, "depth4", prompt, facts, seed,
                [{"tool": "reminders_search"}, {"tool": "email_search"},
                 {"tool": "files_read", "args": {"path": {"icontains": "renew-[[code]]"}}},
                 {"tool": "reminders_update", "args": {"reminder_id": {"eq": rid}, "due": f"+{k}d 09:00"}}],
                [{"in": "reminders", "where": {"id": rid}, "expect": {"count": 1, "due": f"+{k}d 09:00", "title": {"icontains": thing}}},
                 {"in": "reminders", "where": {"id": oid}, "expect": {"count": 1, "due": "+12d 09:00"}}],
                [_step(_call("reminders_search", query=thing)),
                 _step(_call("email_search", query="[[ref]]")),
                 _step(_call("files_read", path=path)),
                 _step(_call("reminders_update", reminder_id=rid, due=f"[[{day}_iso]]T09:00")),
                 {"text": "Moved the reminder to the renewal date at 9am."}],
                "the ref is only in the reminder, the file path in the email, the date in the file; the reminder id comes from step one",
                grounded=[{"tool": "files_read", "arg": "path"}, {"tool": "reminders_update", "arg": "reminder_id"}],
                touch=("reminders",), forbid=["reminders_delete", "reminders_create"])


# ================================================================== neutral: depth 5
def n5_receipt(split, rng, n):
    """Email -> read it -> read the receipt file -> create the event -> text the total."""
    c, = _contacts(rng, [f"c-8{n}1"])
    first = _first(c)
    company = _pick(rng, COMPANIES)
    venue = _pick(rng, STREETS)
    mid, = _ids(rng, "bk", 1)
    prompt = rng.choice([f"{company} emailed my booking. Put it in my calendar with the booking reference in the title and the venue address as the location, then text {first} the total I paid.",
                         f"Add the {company} booking from my email to my calendar (booking reference in the title, venue address as the location) and text {first} what it cost."])
    facts = {**_tf("t"), "code": {"code": "####"}, "ref": {"code": "BK@@###"}, "num": {"int": [11, 98]}, **_money("amt", 60, 480)}
    path = "~/Downloads/receipt-[[code]].txt"
    seed = {"contacts": [c],
            "mail": [{"id": mid, "from": f"{company} <bookings@{_dom(company)}>", "subject": "Booking [[ref]]", "received": "-20m",
                      "body": FILLER + f"Booking reference [[ref]]. Receipt: {path}"}],
            "files": [{"path": path, "kind": "file",
                       "text": f"{company} receipt. Ref [[ref]]. Venue: [[num]] {venue}. Date: [[in_2_days_iso]]. Start: [[t_h]]:[[t_m]]. Total paid: [[amt]] dollars."}]}
    return _neu(split, "receipt", n, "depth5", prompt, facts, seed,
                [{"tool": "email_search"}, {"tool": "email_read", "args": {"message_id": {"eq": mid}}},
                 {"tool": "files_read", "args": {"path": {"icontains": "receipt-[[code]]"}}},
                 {"tool": "calendar_create_event", "args": {"title": {"icontains": "[[ref]]"}, "start": "+2d [[t_h]]:[[t_m]]"}},
                 {"tool": "messages_send", "args": {"to": {"icontains": first}, "text": {"regex": _mre("amt")}}}],
                [{"in": "calendar", "where": {"title": {"icontains": "[[ref]]"}},
                  "expect": {"count": 1, "start": "+2d [[t_h]]:[[t_m]]", "location": {"icontains": f"[[num]] {venue}"}}},
                 {"in": "messages_sent", "where": {"contact_id": c["id"]}, "expect": {"count": 1, "text": {"regex": _mre("amt")}}}],
                [_step(_call("email_search", query=company)),
                 _step(_call("email_read", message_id=mid)),
                 _step(_call("files_read", path=path)),
                 _step(_call("calendar_create_event", title=f"{company} booking [[ref]]", start="[[in_2_days_iso]]T[[t_h]]:[[t_m]]", location=f"[[num]] {venue}")),
                 _step(_call("messages_send", to=first, text="The booking cost [[amt_s]] dollars.")),
                 {"text": f"Added the booking to your calendar and told {first} the total."}],
                "the message id, the file path, then the date, venue and total each appear only in the previous result",
                grounded=[{"tool": "files_read", "arg": "path"}, {"tool": "email_read", "arg": "message_id"}],
                touch=("calendar", "messages_sent"), forbid=["calendar_delete_event", "email_send"], recipients=[c["id"]])


def n5_agenda(split, rng, n):
    """Text names a booking code and a file -> find the event -> read the agenda -> set the room -> email the host."""
    c, = _contacts(rng, [f"c-8{n}1"])
    first = _first(c)
    title = _pick(rng, ["Workshop", "Studio session", "Planning day", "Training morning"])
    ev, = _ids(rng, "ev", 1)
    prompt = rng.choice([f"{first} texted me about my {title.lower()} booking. Find the event, read the agenda file {first} mentions, set the event's location to the room it names, and email the host address in that file which room it is.",
                         f"Sort out the room for the {title.lower()} {first} texted me about: the agenda file they mention has the new room and a host address. Put the room on the event as its location and email the host the room."])
    facts = {**_tf("t"), "code": {"code": "####"}, "wc": {"code": "WS-####"}, "room": {"code": "@##"},
             "host": {"code": "host-####"}, "e_h": {"add": ["t_h", 2]}, "n_h": {"add": ["t_h", 1]}}
    path = "~/Documents/agenda-[[code]].txt"
    seed = {"contacts": [c],
            "messages": [{"id": "msg-801", "thread": c["name"], "from": c["name"], "received": "-30m", "unread": True,
                          "text": f"Booking [[wc]] needs a room sorted. The agenda is saved at {path}."}],
            "calendar": [{"id": ev, "title": f"{title} [[wc]]", "start": "+1d [[t_h]]:[[t_m]]", "end": "+1d [[n_h]]:[[t_m]]", "location": "TBC"}],
            "files": [{"path": path, "kind": "file",
                       "text": "Agenda. Room change: we have moved to Room [[room]]. Host contact: [[host]]@example.org. Bring the printed figures."}]}
    return _neu(split, "agenda", n, "depth5", prompt, facts, seed,
                [{"tool": "messages_search"}, {"tool": "calendar_search_events"},
                 {"tool": "files_read", "args": {"path": {"icontains": "agenda-[[code]]"}}},
                 {"tool": "calendar_update_event", "args": {"event_id": {"eq": ev}, "location": {"icontains": "[[room]]"}}},
                 {"tool": "email_send", "args": {"to": {"icontains": "[[host]]@example.org"}, "body": {"icontains": "[[room]]"}}}],
                [{"in": "calendar", "where": {"id": ev}, "expect": {"count": 1, "location": {"icontains": "[[room]]"}, "start": "+1d [[t_h]]:[[t_m]]"}},
                 {"in": "mail_sent", "where": {"to": {"icontains": "[[host]]@example.org"}}, "expect": {"count": 1, "body": {"icontains": "[[room]]"}}}],
                [_step(_call("messages_search", query="booking", thread=c["name"])),
                 _step(_call("calendar_search_events", query="[[wc]]")),
                 _step(_call("files_read", path=path)),
                 _step(_call("calendar_update_event", event_id=ev, location="Room [[room]]")),
                 _step(_call("email_send", to=["[[host]]@example.org"], subject=f"Room for {title.lower()} [[wc]]", body="The room is Room [[room]].")),
                 {"text": "Set the location to Room [[room]] and emailed the host."}],
                "the booking code and file path are only in the text, the event id in the event, the room and host address in the file",
                grounded=[{"tool": "files_read", "arg": "path"}, {"tool": "calendar_update_event", "arg": "event_id"},
                          {"tool": "email_send", "arg": "to"}],
                touch=("calendar", "mail_sent"), forbid=["calendar_delete_event", "calendar_create_event", "email_draft"])


def n5_web(split, rng, n):
    """Search -> event page -> its booking page -> calendar event -> text the ticket code."""
    c, = _contacts(rng, [f"c-8{n}1"])
    first = _first(c)
    name = _pick(rng, EVENT_NAMES)
    slug = _slug(name)
    prompt = rng.choice([f"Find the {name} event page, follow its booking link, add the event to my calendar and text {first} the ticket code.",
                         f"Look up the {name}, check the booking page it links to, put the event on my calendar and text {first} the ticket code."])
    facts = {**_tf("t"), "e1": {"code": "####"}, "e2": {"code": "####"}, "tc": {"code": "TK@@####"}}
    seed = {"contacts": [c], "web": [
        {"url": f"https://{slug}.example.org/events/[[e1]]", "title": f"{name} - event page",
         "text": FILLER + "Book your place here: https://tickets.example.org/book/[[e2]]"},
        {"url": "https://tickets.example.org/book/[[e2]]", "title": f"{name} tickets",
         "text": f"{name}. Doors [[t_h]]:[[t_m]] on [[in_7_days_iso]]. Your ticket code is [[tc]]."}]}
    return _neu(split, "web", n, "depth5", prompt, facts, seed,
                [{"tool": "web_search"}, {"tool": "web_fetch"}, {"tool": "web_fetch", "args": {"url": {"icontains": "tickets.example.org/book/[[e2]]"}}},
                 {"tool": "calendar_create_event", "args": {"title": {"icontains": name}, "start": "+7d [[t_h]]:[[t_m]]"}},
                 {"tool": "messages_send", "args": {"to": {"icontains": first}, "text": {"icontains": "[[tc]]"}}}],
                [{"in": "calendar", "where": {"title": {"icontains": name}}, "expect": {"count": 1, "start": "+7d [[t_h]]:[[t_m]]"}},
                 {"in": "messages_sent", "where": {"contact_id": c["id"]}, "expect": {"count": 1, "text": {"icontains": "[[tc]]"}}}],
                [_step(_call("web_search", query=name)),
                 _step(_call("web_fetch", url=f"https://{slug}.example.org/events/[[e1]]")),
                 _step(_call("web_fetch", url="https://tickets.example.org/book/[[e2]]")),
                 _step(_call("calendar_create_event", title=name, start="[[in_7_days_iso]]T[[t_h]]:[[t_m]]")),
                 _step(_call("messages_send", to=first, text="Your ticket code for the " + name + " is [[tc]].")),
                 {"text": f"Added the {name} to your calendar and texted {first} the ticket code."}],
                "the booking link is past the search snippet; the time and ticket code are only on the booking page",
                grounded=[{"tool": "web_fetch", "arg": "url"}], touch=("calendar", "messages_sent"),
                forbid=["calendar_delete_event", "shell_exec"], recipients=[c["id"]])


# ================================================================== generate
TEMPLATES = [
    # (function, draws per template) in assistant lane
    (a2_emailevent, 4), (a2_timetext, 4), (a2_msgfigure, 4), (a2_msgreminder, 4), (a2_emailaddr, 4),
    (a3_flight, 4), (a3_doc, 4), (a3_moveevent, 4), (a3_proposal, 4), (a3_bill, 4),
    (a4_dinner, 5), (a4_invoice, 5), (a4_viewing, 5), (a4_quote, 5),
    (n2_calmove, 2), (n2_forward, 2), (n2_noteappend, 2),
    (n3_emailevent, 3), (n3_msgfile, 3), (n3_calshift, 3),
    (n4_dinner, 3), (n4_invoice, 3), (n4_renewal, 3),
    (n5_receipt, 2), (n5_agenda, 2), (n5_web, 2),
]


def generate(split: str, rng: random.Random) -> list[dict]:
    cases: list[dict] = []
    for fn, draws in TEMPLATES:
        for n in range(1, draws + 1):
            cases.append(fn(split, rng, n))
    # About 40% of each (lane, sub) group is `core`.
    seen: dict[tuple[str, str], int] = {}
    for c in cases:
        key = (c["lane"], c["sub"])
        i = seen.get(key, 0)
        seen[key] = i + 1
        if i % 5 in (0, 2):
            c["tags"] = sorted(set(c["tags"]) | {"core"})
    return cases
