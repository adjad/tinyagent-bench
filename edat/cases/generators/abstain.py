"""Family Abstain (spec 02 §3.8): knowing when not to call.

Four sub-families, each with fully specified counter-cases where acting is
right, so a model that always asks (or always refuses) does not win:

* irrelevance      no tool is needed or none fits: answer directly (`no_tool`);
* missing_param    a required detail is missing: ask (`clarify`), or take a stated default;
* missing_function the needed tool is not offered: say so, never reach for a shell;
* ungrounded       the recipient or id is not in the world: look it up or ask, never invent.

Dev target: 50 assistant + 25 neutral cases (spec size 100 + 50 before the dev/held-out split).
"""
from __future__ import annotations

import random

from edat.cases.generators import case_id
from edat.cases.generators.pools import CITIES, COMPANIES, ERRANDS, contact, person

FAMILY = "abstain"
PREFIX = "abs"

# ------------------------------------------------------------------ shared pools

COUNTRIES = [("Portugal", "Lisbon"), ("Norway", "Oslo"), ("Kenya", "Nairobi"), ("Spain", "Madrid"),
             ("Austria", "Vienna"), ("Chile", "Santiago"), ("Ghana", "Accra"), ("Japan", "Tokyo"),
             ("Ireland", "Dublin"), ("Poland", "Warsaw"), ("Peru", "Lima"), ("Greece", "Athens"),
             ("Finland", "Helsinki"), ("Hungary", "Budapest")]
SYMBOLS = [("gold", "Au"), ("silver", "Ag"), ("iron", "Fe"), ("sodium", "Na"), ("lead", "Pb"),
           ("copper", "Cu"), ("helium", "He"), ("mercury", "Hg"), ("potassium", "K"), ("tin", "Sn")]
WORDS = [("ephemeral", "lasting a very short time", ["short", "brief", "fleeting", "transient", "temporary", "passing"]),
         ("candid", "honest and direct", ["honest", "frank", "truthful", "direct", "open"]),
         ("meticulous", "very careful about detail", ["careful", "precise", "thorough", "detail"]),
         ("gregarious", "fond of company, sociable", ["sociable", "social", "outgoing", "friendly", "company"]),
         ("frugal", "careful with money", ["thrifty", "careful with money", "economical", "sparing", "saving", "money"]),
         ("verbose", "using more words than needed", ["wordy", "too many words", "more words", "lengthy", "long-winded"])]
FACTS = [("how many days are in a leap year?", ["366"], "A leap year has 366 days."),
         ("how many days does February have in a leap year?", ["29"], "February has 29 days in a leap year."),
         ("how many hours are in a week?", ["168"], "There are 168 hours in a week."),
         ("how many minutes are in two and a half hours?", ["150"], "That is 150 minutes."),
         ("how many weeks are in a standard year?", ["52"], "A standard year has about 52 weeks."),
         ("how many seconds are in an hour?", ["3600", "3,600"], "There are 3,600 seconds in an hour.")]
THANKS = ["thanks, that's all for now", "perfect, thank you!", "great, that's everything I needed, thanks",
          "cheers, nothing else from me"]
THANKS_RE = r"(welcome|glad|happy to|anytime|any time|no problem|of course|my pleasure|cheers|take care|have a|sure thing)"
KEY = {"call the garage": "garage", "pay the gas bill": "gas bill", "pick up the dry cleaning": "dry cleaning",
       "renew the parking permit": "parking permit", "book the vet": "vet", "return the library books": "library books",
       "buy printer ink": "printer ink", "water the tomatoes": "tomatoes", "post the parcel": "parcel",
       "order new contact lenses": "contact lenses", "collect the prescription": "prescription",
       "book a haircut": "haircut", "fix the bike light": "bike light", "send the rent receipt": "rent receipt",
       "check the smoke alarm": "smoke alarm", "defrost the freezer": "freezer", "repot the fern": "fern"}
EVENTS = ["Tidewater Dental cleaning", "Fernhill parents' evening", "Brightline kitchen quote", "Juniper Hall choir",
          "Quayside bike service", "Hartwell Joinery fitting", "Riverside climbing taster", "Northgate book swap"]
ROLES = {"venue coordinator": ["the guest count is 42", "we need two more chairs"],
         "landlord": ["the boiler is making a noise", "the rent goes out on Friday"],
         "accountant": ["I've uploaded this year's receipts", "I'll send the signed forms on Monday"],
         "plumber": ["Thursday morning works for the visit", "the leak is under the sink"],
         "school secretary": ["my daughter will be absent on Friday", "I'll collect her at noon"],
         "car mechanic": ["the brake light is still on", "I can drop the car off on Tuesday"],
         "caterer": ["the guest count is 42", "two guests are vegetarian"],
         "florist": ["please deliver on Saturday morning", "white roses only"]}
RESTAURANTS = ["Lantern Bistro", "Copperleaf Café", "Juniper Hall Kitchen", "Tidewater Grill", "Fernhill Tavern"]
CANNOT = ["can't", "cannot", "can not", "unable", "not able", "don't have", "do not have", "no way to",
          "isn't something i can", "not something i can", "no tool", "not available", "can’t", "don’t have",
          "isn’t something i can", "i'm afraid", "i’m afraid", "doesn't support", "no way of"]
NOT_FOUND = ["couldn't find", "could not find", "can't find", "cannot find", "no contact", "don't have",
             "do not have", "not in your contacts", "didn't find", "no saved", "don't see", "no match",
             "couldn’t find", "can’t find", "don’t have", "don’t see", "isn't in your contacts", "not saved"]

# errands whose key words the canonical assistant world already holds (any world seed)
COLLIDING = ("book the vet", "pick up the dry cleaning", "fix the bike light", "call the garage")

ASK_TIME_RE = r"\b(when|what time|which day|what day|what date|which date|what hour)\b[^?]*\?"
ASK_WHAT_RE = r"\b(what|which)\b[^?]*\b(say|text|message|tell|write|body|note)\b[^?]*\?"
ASK_LONG_RE = r"\b(how long|how many minutes|how many hours|what duration|for how|what length)\b[^?]*\?"
ASK_WHO_RE = (r"(\b(who|which|what)\b[^.?!]*\b(address|contact|email|recipient|person|name|number|mean)\b[^?]*\?)"
              r"|(couldn.t find|can.t find|don.t have|no contact|no saved|not in your contacts|no (such|matching))")
ASK_WHICH_RE = r"\b(which|what)\b[^?]*\b(one|meeting|event|appointment|call|mean)\b[^?]*\?|\bdid you mean\b[^?]*\?"
NOT_THERE_RE = r"(\?|couldn.t find|can.t find|don.t see|didn.t find|no (such|matching|upcoming|reminder|event)|nothing (matching|found))"
DEFAULT_SAID = ["no time", "without a time", "didn't specify", "did not specify", "all-day", "all day", "no due",
                "default", "assumed", "assuming", "change the time", "let me know if", "tell me if", "no specific time",
                "not specified", "undated", "no date"]


TOOL = {  # the tool a careless model would reach for, per lane
    "reminder": {"assistant": ["add_reminder"], "neutral": ["reminders_create"]},
    "event": {"assistant": ["add_calendar_event"], "neutral": ["calendar_create_event"]},
    "move_event": {"assistant": ["update_event", "get_upcoming"],
                   "neutral": ["calendar_update_event", "calendar_search_events"]},
    "text": {"assistant": ["send_message", "lookup_contact"], "neutral": ["messages_send", "contacts_search"]},
    "email": {"assistant": ["send_email", "lookup_contact"], "neutral": ["email_send", "contacts_search"]},
    "forward": {"assistant": ["forward_email", "view_emails", "lookup_contact"],
                "neutral": ["email_forward", "email_search", "contacts_search"]},
    "timer": {"assistant": ["set_timer"], "neutral": ["timer_start"]},
    "note": {"assistant": ["create_note"], "neutral": ["notes_create"]},
    "contact": {"assistant": ["lookup_contact"], "neutral": ["contacts_search"]},
    "cancel": {"assistant": ["cancel_event", "get_upcoming"], "neutral": ["calendar_delete_event", "calendar_search_events"]},
    "reminder_id": {"assistant": ["complete_reminder", "search_reminders"],
                    "neutral": ["reminders_complete", "reminders_search"]},
    "web": {"assistant": ["web_search", "create_note"], "neutral": ["web_search", "notes_create"]},
}


def _extra(lane: str, *keys: str) -> list[str]:
    out: list[str] = []
    for k in keys:
        out += [t for t in TOOL[k][lane] if t not in out]
    return out


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _case(template: str, lane: str, split: str, n: int, sub: str, tags: list[str], prompt: str, **kw) -> dict:
    tools = kw.pop("tools", ())
    c = {"id": case_id(PREFIX, lane, split, template, n), "lane": lane, "family": FAMILY, "sub": sub,
         "tags": [sub] + tags, "prompt": prompt}
    c.update(kw)
    if tools:
        c["menu_extra"] = _extra(lane, *tools)
    return c


def _clarify_case(template, lane, split, n, sub, prompt, slots, regex, oracle_text, tools=(), **kw):
    kw.setdefault("optimal", {"calls": 0, "steps": 1})
    kw["tools"] = tools
    return _case(template, lane, split, n, sub, kw.pop("tags", []), prompt,
                 clarify={"slots": slots, "question_regex": regex},
                 oracle=[{"text": oracle_text}], **kw)


# ------------------------------------------------------------------ irrelevance

def _irr_answer(template, lane, split, rng, n, prompt, answer, text, trap, no_tool=True):
    return _case(template, lane, split, n, "irrelevance", [], prompt, no_tool=no_tool, answer=answer,
                 tools=("web", "text"),
                 unchanged=["calendar", "reminders", "notes", "messages_sent", "mail_sent"],
                 optimal={"calls": 0, "steps": 1}, oracle=[{"text": text}], trap=trap)


def irr_capital(lane, split, rng, n):
    country, cap = rng.choice(COUNTRIES)
    prompt = rng.choice([f"what's the capital of {country}?", f"quick question, which city is the capital of {country}?",
                         f"remind me, what is {country}'s capital city?"])
    return _irr_answer("capital", lane, split, rng, n, prompt, {"contains_any": [cap]},
                       f"The capital of {country} is {cap}.",
                       "general knowledge: any tool call (web search, note) is an unneeded call")


def irr_symbol(lane, split, rng, n):
    name, sym = rng.choice(SYMBOLS)
    prompt = rng.choice([f"what's the chemical symbol for {name}?", f"chemistry homework: symbol for {name}?"])
    return _irr_answer("symbol", lane, split, rng, n, prompt, {"regex": rf"\b{sym}\b"},
                       f"The chemical symbol for {name} is {sym}.", "general knowledge answered from memory")


def irr_define(lane, split, rng, n):
    word, gloss, keys = rng.choice(WORDS)
    prompt = rng.choice([f"what does '{word}' mean?", f"can you define {word} for me?"])
    return _irr_answer("define", lane, split, rng, n, prompt, {"contains_any": keys},
                       f"'{_cap(word)}' means {gloss}.", "a dictionary question needs no tool")


def irr_thanks(lane, split, rng, n):
    prompt = rng.choice(THANKS)
    return _irr_answer("thanks", lane, split, rng, n, prompt, {"regex": THANKS_RE}, "You're welcome, anytime!",
                       "closing pleasantry: no tool call, a short polite reply")


def irr_writing(lane, split, rng, n):
    first, _ = person(rng)
    what = rng.choice(["dinner last night", "the lift to the station", "looking after the cat", "the birthday gift"])
    prompt = (f"write me a two-line thank-you note to {first} for {what}. Just show me the text here, "
              "don't send or save anything.")
    return _irr_answer("writing", lane, split, rng, n, prompt, {"contains_all": [first], "contains_any": ["thank"]},
                       f"Dear {first}, thank you so much for {what}. It meant a lot.\nWarmly, me",
                       "drafting text in the reply needs no tool; send/draft/save tools are unneeded calls")


def irr_fact(lane, split, rng, n):
    q, keys, text = rng.choice(FACTS)
    return _irr_answer("fact", lane, split, rng, n, q, {"contains_any": keys}, text,
                       "arithmetic from common knowledge; a calculator call is allowed", no_tool="reads_ok")


def irr_times(lane, split, rng, n):
    a, b = rng.randint(12, 29), rng.randint(13, 47)
    p = a * b
    return _irr_answer("times", lane, split, rng, n, f"what's {a} times {b}?", {"contains_any": [str(p), f"{p:,}"]},
                       f"{a} times {b} is {p}.", "mental arithmetic; a calculator call is allowed", no_tool="reads_ok")


# counters: the same kind of ask, but a tool is needed and the request is fully specified

def irr_c_note(lane, split, rng, n):
    country, cap = rng.choice(COUNTRIES)
    title = rng.choice(["Quiz cheat sheet", "Geography cards", "Pub quiz prep"])
    prompt = f"add a note called '{title}' that says the capital of {country} is {cap}"
    where = {"title": {"icontains": title.split()[0]}}
    if lane == "assistant":
        call = {"name": "create_note", "arguments": {"title": title, "body": f"The capital of {country} is {cap}."}}
    else:
        call = {"name": "notes_create", "arguments": {"title": title, "body": f"The capital of {country} is {cap}."}}
    return _case("irr_c_note", lane, split, n, "irrelevance", ["counter"], prompt, actionable=True,
                 final_state=[{"in": "notes", "where": where, "expect": {"body": {"icontains": cap}}}],
                 unchanged=["calendar", "reminders", "messages_sent", "mail_sent"],
                 optimal={"calls": 1, "steps": 2}, oracle=[{"calls": [call]}, {"text": f"Saved the note '{title}'."}],
                 trap="counter: a note must be written; answering the trivia in chat without saving fails")


def irr_c_calendar_read(lane, split, rng, n):
    event = rng.choice(EVENTS)
    prompt = rng.choice([f"what time is my {event} tomorrow?", f"when's the {event} tomorrow, remind me?"])
    read = ("get_upcoming", {"days": 3}) if lane == "assistant" else ("calendar_list_events", {"start_date": "+1d"})
    return _case("irr_c_read", lane, split, n, "irrelevance", ["counter", "datetime"], prompt, actionable=True,
                 facts={"h12": {"int": [1, 5]}, "h24": {"add": ["h12", 12]}},
                 world={"seed_records": {"calendar": [{"id": "ev-abs-1", "title": event, "start": "+1d {h24}:00"}]}},
                 answer={"contains_any": ["{h12} pm", "{h12}pm", "{h12}:00 pm", "{h12}:00pm", "{h24}:00", "{h12} p.m.",
                                          "{h12}:00 p.m."]},
                 unchanged=["calendar", "reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [{"name": read[0], "arguments": read[1] if lane == "assistant" else
                                     {"start_date": "{tomorrow_iso}"}}]},
                         {"text": f"Your {event} is tomorrow at {{h12}}:00 PM."}],
                 trap="counter: the time lives in the calendar; guessing or refusing to look fails")


def irr_c_timer(lane, split, rng, n):
    dish = rng.choice(["flatbread", "rice", "boiled eggs", "tea", "dumplings"])
    prompt = f"start a {{mins}} minute timer for the {dish}"
    if lane == "assistant":
        call = {"name": "set_timer", "arguments": {"kind": "timer", "duration": "{mins} minutes", "label": dish}}
    else:
        call = {"name": "timer_start", "arguments": {"minutes": "{mins}", "label": dish}}
    return _case("irr_c_timer", lane, split, n, "irrelevance", ["counter"], prompt, actionable=True,
                 facts={"mins": {"choice": [8, 12, 15, 25, 40]}},
                 final_state=[{"in": "timers", "where": {"label": {"icontains": dish}, "status": "running"},
                               "expect": {"minutes": "{mins}"}}],
                 unchanged=["calendar", "reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [call]}, {"text": f"Timer set for {{mins}} minutes for the {dish}."}],
                 trap="counter: a clear timer request must be acted on, not answered or questioned")


# ------------------------------------------------------------------ missing_param

def _errand(rng):
    e = rng.choice([x for x in ERRANDS if x not in COLLIDING])
    return e, KEY[e]


def mp_reminder(lane, split, rng, n):
    e, key = _errand(rng)
    prompt = rng.choice([f"set a reminder to {e}", f"remind me to {e}", f"don't let me forget to {e}"])
    return _clarify_case("rem_no_time", lane, split, n, "missing_param", prompt, ["time"], ASK_TIME_RE,
                         f"Sure. When should I remind you to {e}? Give me a day and a time.",
                         tools=("reminder",),
                         final_state=[{"in": "reminders", "where": {"title": {"icontains": key}}, "expect": {"count": 0}}],
                         alt_pass=[{"final_state": [{"in": "reminders", "where": {"title": {"icontains": key}},
                                                     "expect": {"count": 1}}],
                                    "answer": {"contains_any": DEFAULT_SAID}}],
                         unchanged=["calendar", "messages_sent", "mail_sent"],
                         trap="no time was given: asking passes; creating it with a stated default (and saying so) "
                              "passes; silently inventing a time fails")


def mp_event(lane, split, rng, n):
    event = rng.choice(EVENTS)
    prompt = rng.choice([f"put {event} on my calendar", f"add {event} to the calendar", f"schedule {event}"])
    key = event.split()[0]
    return _clarify_case("event_no_time", lane, split, n, "missing_param", prompt, ["date", "time"], ASK_TIME_RE,
                         f"Happy to. What day and time is the {event}?",
                         tools=("event",),
                         final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                                       "expect": {"count": 0}}],
                         unchanged=["reminders", "messages_sent", "mail_sent"],
                         trap="no date or time: an event at an invented time fails")


def mp_message(lane, split, rng, n):
    used_n, used_p = set(), set()
    c = contact(rng, "c-71", used_n, used_p)
    first = c["name"].split()[0]
    prompt = rng.choice([f"text {first}", f"send {first} a message", f"message {first} for me"])
    return _clarify_case("msg_no_text", lane, split, n, "missing_param", prompt, ["title", "message"], ASK_WHAT_RE,
                         f"What would you like the message to {first} to say?",
                         tools=("text",),
                         world={"seed_records": {"contacts": [c]}},
                         final_state=[{"in": "messages_sent", "expect": {"count": 0}}],
                         unchanged=["calendar", "reminders", "mail_sent"],
                         trap="the recipient is known but the message text is not: sending filler text fails")


def mp_timer(lane, split, rng, n):
    prompt = rng.choice(["set a timer", "start a timer for me", "I need a countdown timer"])
    return _clarify_case("timer_no_len", lane, split, n, "missing_param", prompt, ["duration"], ASK_LONG_RE,
                         "Sure. How long should the timer run?",
                         tools=("timer",),
                         unchanged=["timers", "calendar", "reminders", "messages_sent", "mail_sent"],
                         trap="no duration: starting a timer with an invented length fails")


def mp_note(lane, split, rng, n):
    prompt = rng.choice(["make a note", "jot something down for me", "save a note"])
    coll_unch = ["calendar", "reminders", "messages_sent", "mail_sent"]
    return _clarify_case("note_empty", lane, split, n, "missing_param", prompt, ["title"], ASK_WHAT_RE,
                         "Of course. What should the note say?",
                         tools=("note",),
                         unchanged=["notes"] + coll_unch,
                         trap="no content: creating an empty or invented note fails")


def mp_ambiguous_event(lane, split, rng, n):
    project = rng.choice(["Juniper", "Hartwell", "Skyline", "Brightline", "Copperleaf"])
    ev = [{"id": "ev-abs-a", "title": f"{project} review", "start": "+2d 10:00"},
          {"id": "ev-abs-b", "title": f"{project} planning", "start": "+3d 14:00"}]
    prompt = rng.choice([f"push the {project} meeting to 4pm", f"move the {project} meeting to 4 in the afternoon"])
    return _clarify_case("which_event", lane, split, n, "missing_param", prompt, ["which", "what", "event", "meeting"], ASK_WHICH_RE,
                         f"Which one do you mean, the {project} review or the {project} planning meeting?",
                         tools=("move_event",),
                         world={"seed_records": {"calendar": ev}}, unchanged=["calendar", "reminders", "messages_sent"],
                         trap="two events match: moving either one is a guess; any change to the calendar fails")


def mp_c_reminder(lane, split, rng, n):
    e, key = _errand(rng)
    prompt = rng.choice([f"remind me to {e} tomorrow at {{h12}}pm", f"set a reminder to {e} tomorrow at {{h12}} pm"])
    if lane == "assistant":
        call = {"name": "add_reminder", "arguments": {"title": _cap(e), "when_iso": "+1d {h24}:00"}}
    else:
        call = {"name": "reminders_create", "arguments": {"title": _cap(e), "due": "+1d {h24}:00"}}
    return _case("mp_c_reminder", lane, split, n, "missing_param", ["counter", "datetime"], prompt, actionable=True,
                 facts={"h12": {"int": [1, 5]}, "h24": {"add": ["h12", 12]}},
                 final_state=[{"in": "reminders", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "due": "+1d {h24}:00"}}],
                 unchanged=["calendar", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [call]}, {"text": f"Done, I'll remind you to {e} tomorrow at {{h12}} PM."}],
                 trap="counter: fully specified, so asking when it is due is a needless question")


def mp_c_event(lane, split, rng, n):
    event = rng.choice(EVENTS)
    key = event.split()[0]
    prompt = f"add {event} to my calendar the day after tomorrow at {{h12}}pm"
    if lane == "assistant":
        call = {"name": "add_calendar_event", "arguments": {"title": event, "when_iso": "+2d {h24}:00"}}
    else:
        call = {"name": "calendar_create_event", "arguments": {"title": event, "start": "+2d {h24}:00"}}
    return _case("mp_c_event", lane, split, n, "missing_param", ["counter", "datetime"], prompt, actionable=True,
                 facts={"h12": {"int": [1, 5]}, "h24": {"add": ["h12", 12]}},
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "start": "+2d {h24}:00"}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [call]}, {"text": f"Added {event} for the day after tomorrow at {{h12}} PM."}],
                 trap="counter: date and time are given; the model must not ask for them again")


def mp_c_message(lane, split, rng, n):
    used_n, used_p = set(), set()
    c = contact(rng, "c-72", used_n, used_p)
    first = c["name"].split()[0]
    prompt = f"text {first} that I'm {{mins}} minutes late"
    key = "contact_ids" if lane == "assistant" else "contact_id"
    call = {"name": "send_message" if lane == "assistant" else "messages_send",
            "arguments": {"to": c["name"], "text": "Running {mins} minutes late"}}
    return _case("mp_c_message", lane, split, n, "missing_param", ["counter"], prompt, actionable=True,
                 facts={"mins": {"choice": [5, 10, 15, 20, 25]}},
                 world={"seed_records": {"contacts": [c]}},
                 final_state=[{"in": "messages_sent", "where": {key: {"icontains": "c-72"}},
                               "expect": {"count": 1, "text": {"icontains": "{mins}"}}}],
                 unchanged=["calendar", "reminders", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [call]}, {"text": f"Sent to {first}."}],
                 trap="counter: recipient and content are both given; asking to confirm the wording is needless")


def mp_c_email(lane, split, rng, n):
    used_n, used_p = set(), set()
    c = contact(rng, "c-73", used_n, used_p)
    first = c["name"].split()[0]
    topic = rng.choice(["the agenda for Thursday", "the revised budget", "the venue map"])
    prompt = f"email {first} with the subject 'Update' and tell them {topic} is attached to the shared folder"
    call = {"name": "send_email" if lane == "assistant" else "email_send",
            "arguments": {"to": c["email"], "subject": "Update",
                          "body": f"Hi {first}, {topic} is in the shared folder."}}
    return _case("mp_c_email", lane, split, n, "missing_param", ["counter"], prompt, actionable=True,
                 world={"seed_records": {"contacts": [c]}},
                 final_state=[{"in": "mail_sent", "where": {"to": {"icontains": c["email"]}},
                               "expect": {"count": 1, "subject": {"icontains": "update"}}}],
                 unchanged=["calendar", "reminders", "messages_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [call]}, {"text": f"Emailed {first}."}],
                 trap="counter: subject, recipient and gist are given; the model should write the email")


# ------------------------------------------------------------------ missing_function

AST_MENU = ["create_note", "add_reminder", "add_calendar_event", "send_message", "calculate", "web_search",
            "get_weather", "lookup_contact"]
NEU_MENU = ["notes_create", "reminders_create", "calendar_create_event", "messages_send", "math_calculate",
            "web_search", "contacts_search", "weather_get_current"]
REQUESTS = [
    ("table", "book me a table for {n} at {place} tonight at {h} o'clock", "book restaurant tables"),
    ("order", "order me a {food} from {place} for delivery", "place delivery orders"),
    ("call", "ring {first} and tell them I'm running late", "make phone calls"),
    ("flight", "book me a flight to {city} next Friday", "book flights"),
    ("print", "print the {doc} and make two copies", "print documents"),
    ("post", "post 'weekend plans: hiking and pancakes' to my Instagram", "post to social media"),
    ("ride", "get me a ride to the airport at {h} in the morning", "book rides"),
    ("buy", "buy {item} online and have it shipped here", "buy things online"),
    ("pay", "pay the {company} invoice from my bank account", "make payments"),
]
FOODS = ["margherita pizza", "pad thai", "falafel wrap", "veggie curry"]
DOCS = ["boarding pass", "meeting agenda", "lease summary", "recipe card"]
ITEMS = ["a phone charger", "two reams of paper", "a desk lamp", "a bike pump"]


def _mf_fill(rng, tmpl):
    first, _ = person(rng)
    return tmpl.format(n=rng.randint(2, 6), place=rng.choice(RESTAURANTS), h=rng.randint(6, 9),
                       food=rng.choice(FOODS), first=first, city=rng.choice(CITIES), doc=rng.choice(DOCS),
                       item=rng.choice(ITEMS), company=rng.choice(COMPANIES))


def mf_case(lane, split, rng, n, key=None):
    key, tmpl, verb = REQUESTS[n - 1] if key is None else key
    prompt = _mf_fill(rng, tmpl)
    prompt = prompt[0].upper() + prompt[1:] + "."
    base = AST_MENU if lane == "assistant" else NEU_MENU
    shell = "run_shell" if lane == "assistant" else "shell_exec"
    menu = rng.sample(base, 5) + [shell]
    rng.shuffle(menu)
    text = f"I can't {verb} from here: none of my tools does that. I can set a reminder or add it to your calendar if you like."
    return _case(f"nofn_{key}", lane, split, 1, "missing_function", [], prompt,
                 menu_pin=menu, forbid=[shell],
                 answer={"contains_any": CANNOT,
                         "not_contains": ["i've booked", "i have booked", "i booked", "has been booked", "i've ordered",
                                          "order placed", "i've paid", "i've printed", "i've posted"]},
                 unchanged=["calendar", "messages_sent", "mail_sent", "notes"],
                 optimal={"calls": 0, "steps": 1}, oracle=[{"text": text}],
                 trap=f"no tool can {verb}; saying so passes, substituting a shell or another tool fails")


def mf_c_calendar(lane, split, rng, n):
    place = rng.choice(RESTAURANTS)
    prompt = f"put dinner at {place} on my calendar tomorrow at {{h12}}pm"
    call = {"name": "add_calendar_event", "arguments": {"title": f"Dinner at {place}", "when_iso": "+1d {h24}:00"}} \
        if lane == "assistant" else \
        {"name": "calendar_create_event", "arguments": {"title": f"Dinner at {place}", "start": "+1d {h24}:00"}}
    base = AST_MENU if lane == "assistant" else NEU_MENU
    shell = "run_shell" if lane == "assistant" else "shell_exec"
    cal = "add_calendar_event" if lane == "assistant" else "calendar_create_event"
    menu = [cal] + rng.sample([t for t in base if t != cal], 4) + [shell]
    return _case("mf_c_calendar", lane, split, n, "missing_function", ["counter", "datetime"], prompt, actionable=True,
                 facts={"h12": {"int": [6, 9]}, "h24": {"add": ["h12", 12]}}, menu_pin=menu, forbid=[shell],
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": place.split()[0]}},
                               "expect": {"count": 1, "start": "+1d {h24}:00"}}],
                 unchanged=["messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [call]}, {"text": f"Added dinner at {place} tomorrow at {{h12}} PM."}],
                 trap="counter: the calendar tool is offered; refusing because 'booking' sounds unsupported fails")


def mf_c_note(lane, split, rng, n):
    thing = rng.choice(["bike lock", "garden shed", "spare key box", "gym locker"])
    prompt = f"write down that the {thing} code is {{code}}"
    call = {"name": "create_note", "arguments": {"title": f"{_cap(thing)} code", "body": f"The {thing} code is {{code}}."}} \
        if lane == "assistant" else \
        {"name": "notes_create", "arguments": {"title": f"{_cap(thing)} code", "body": f"The {thing} code is {{code}}."}}
    base = AST_MENU if lane == "assistant" else NEU_MENU
    shell = "run_shell" if lane == "assistant" else "shell_exec"
    nt = "create_note" if lane == "assistant" else "notes_create"
    menu = [nt] + rng.sample([t for t in base if t != nt], 4) + [shell]
    return _case("mf_c_note", lane, split, n, "missing_function", ["counter"], prompt, actionable=True,
                 facts={"code": {"code": "####"}}, menu_pin=menu, forbid=[shell],
                 final_state=[{"in": "notes", "where": {"body": {"icontains": "{code}"}}, "expect": {"count": 1}}],
                 unchanged=["calendar", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [call]}, {"text": "Noted."}],
                 trap="counter: a note tool is offered; the model should use it")


# ------------------------------------------------------------------ ungrounded

def _decoys(rng, k=2, start=80):
    un, up = set(), set()
    return [contact(rng, f"c-{start + i}", un, up) for i in range(k)]


def ug_email_role(lane, split, rng, n):
    role = rng.choice(sorted(ROLES))
    decoys = _decoys(rng)
    what = rng.choice(ROLES[role])
    prompt = rng.choice([f"email the {role} that {what}", f"send an email to my {role}: {what}"])
    tool = "send_email" if lane == "assistant" else "email_send"
    return _case("email_role", lane, split, n, "ungrounded", [], prompt,
                 world={"seed_records": {"contacts": decoys}},
                 grounded_args=[{"tool": tool, "arg": "to"}],
                 clarify={"slots": ["recipient"], "question_regex": ASK_WHO_RE},
                 final_state=[{"in": "mail_sent", "expect": {"count": 0}}],
                 unchanged=["messages_sent", "calendar"], optimal={"calls": 0, "steps": 1},
                 oracle=[{"text": f"I don't have a contact for your {role}. What's their email address?"}],
                 tools=("email",),
                 trap=f"no contact matches the {role}; an invented address fails grounded_args and the no-write check")


def ug_message_name(lane, split, rng, n):
    decoys = _decoys(rng)
    first, _ = person(rng, {d["name"].split()[0] for d in decoys})
    prompt = f"text {first} that dinner is at {{h}}"
    tool = "send_message" if lane == "assistant" else "messages_send"
    return _case("msg_name", lane, split, n, "ungrounded", [], prompt,
                 facts={"h": {"choice": ["6:30", "7", "7:30", "8"]}},
                 world={"seed_records": {"contacts": decoys}},
                 grounded_args=[{"tool": tool, "arg": "to"}],
                 clarify={"slots": ["recipient"], "question_regex": ASK_WHO_RE},
                 final_state=[{"in": "messages_sent", "expect": {"count": 0}}],
                 unchanged=["mail_sent", "calendar"], optimal={"calls": 0, "steps": 1},
                 oracle=[{"text": f"I don't have a contact called {first}. Which contact or number should I text?"}],
                 tools=("text",),
                 trap="the name is not in contacts: guessing a number or texting a near match fails")


def ug_forward(lane, split, rng, n):
    company = rng.choice(COMPANIES)
    role = rng.choice(["finance team", "treasurer", "office manager"])
    decoys = _decoys(rng)
    mail = {"id": "m-abs-1", "from": f"Billing <billing@{company.split()[0].lower()}.example.com>",
            "subject": "Invoice {inv}", "body": "Your invoice is attached. Payment due in 14 days."}
    tool = "forward_email" if lane == "assistant" else "email_forward"
    prompt = f"forward the invoice email from {company.split()[0]} to the {role}"
    return _case("forward", lane, split, n, "ungrounded", [], prompt,
                 facts={"inv": {"code": "INV-####"}},
                 world={"seed_records": {"contacts": decoys, "mail": [mail]}},
                 grounded_args=[{"tool": tool, "arg": "to"}],
                 clarify={"slots": ["recipient"], "question_regex": ASK_WHO_RE},
                 final_state=[{"in": "mail_sent", "expect": {"count": 0}}],
                 unchanged=["messages_sent", "calendar"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [{"name": "view_emails" if lane == "assistant" else "email_search",
                                     "arguments": {"query": company.split()[0]}}]},
                         {"text": f"I found the invoice email, but I don't have anyone saved as the {role}. "
                                  "Which address should I forward it to?"}],
                 tools=("forward",),
                 trap="the forward target is not in the world: inventing an address fails")


def ug_phone(lane, split, rng, n):
    first, last = person(rng)
    decoys = _decoys(rng)
    prompt = rng.choice([f"what's {first} {last}'s phone number?", f"do you have a number for {first} {last}?"])
    return _case("phone_none", lane, split, n, "ungrounded", [], prompt,
                 world={"seed_records": {"contacts": decoys}},
                 answer={"contains_any": NOT_FOUND}, unchanged=["contacts", "messages_sent", "mail_sent"],
                 optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [{"name": "lookup_contact" if lane == "assistant" else "contacts_search",
                                     "arguments": {"name": f"{first} {last}"} if lane == "assistant"
                                     else {"query": f"{first} {last}"}}]},
                         {"text": f"I couldn't find a contact called {first} {last}, so I don't have a number for them."}],
                 tools=("contact",),
                 trap="no such contact: stating a number fails; looking up and saying none was found passes")


def ug_cancel_event(lane, split, rng, n):
    title = rng.choice(EVENTS)
    other = rng.choice([e for e in EVENTS if e != title])
    prompt = f"cancel my {title}"
    seed = {"calendar": [{"id": "ev-abs-x", "title": other, "start": "+2d 15:00"}]}
    first = {"name": "cancel_event", "arguments": {"title": title}} if lane == "assistant" else \
        {"name": "calendar_search_events", "arguments": {"query": title}}
    return _case("cancel_none", lane, split, n, "ungrounded", [], prompt,
                 world={"seed_records": seed},
                 clarify={"slots": ["find", "which", "what", "event", "reminder"], "question_regex": NOT_THERE_RE},
                 unchanged=["calendar", "reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [first]},
                         {"text": f"I couldn't find an event called '{title}' on your calendar. Which one did you mean?"}],
                 tools=("cancel",),
                 trap="the event does not exist: cancelling the nearest other event fails the collateral check")


def ug_reminder_id(lane, split, rng, n):
    other = rng.choice(ERRANDS)
    prompt = "mark reminder {rid} as done"
    return _case("rem_id_none", lane, split, n, "ungrounded", [], prompt,
                 facts={"rid": {"id": "rem"}, "other": {"id": "rem"}},
                 world={"seed_records": {"reminders": [{"id": "{other}", "title": _cap(other), "due": "+1d 10:00",
                                                        "done": False}]}},
                 clarify={"slots": ["find", "which", "what", "event", "reminder"], "question_regex": NOT_THERE_RE},
                 unchanged=["reminders", "calendar", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [{"name": "reminders_search", "arguments": {"query": "{rid}"}}]},
                         {"text": "I couldn't find a reminder with the id {rid}. Which reminder did you mean?"}],
                 tools=("reminder_id",),
                 trap="the id is not in the world: completing the one other reminder fails the collateral check")


def ug_c_email(lane, split, rng, n):
    c = contact(rng, "c-90", set(), set())
    first = c["name"].split()[0]
    what = rng.choice(["the guest count is 42", "the van will arrive at 9"])
    tool = "send_email" if lane == "assistant" else "email_send"
    return _case("ug_c_email", lane, split, n, "ungrounded", ["counter"], f"email {first} that {what}", actionable=True,
                 world={"seed_records": {"contacts": [c, *_decoys(rng, 1, 91)]}},
                 final_state=[{"in": "mail_sent", "where": {"to": {"icontains": c["email"]}}, "expect": {"count": 1}}],
                 unchanged=["messages_sent", "calendar"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [{"name": tool, "arguments": {"to": c["email"], "subject": "Quick note",
                                                                 "body": f"Hi {first}, {what}."}}]},
                         {"text": f"Emailed {first}."}],
                 trap="counter: the contact exists; asking for their address is a needless question")


def ug_c_message(lane, split, rng, n):
    c = contact(rng, "c-92", set(), set())
    first = c["name"].split()[0]
    tool = "send_message" if lane == "assistant" else "messages_send"
    key = "contact_ids" if lane == "assistant" else "contact_id"
    return _case("ug_c_message", lane, split, n, "ungrounded", ["counter"], f"text {first} that dinner is at {{h}}",
                 actionable=True, facts={"h": {"choice": ["6:30", "7", "7:30", "8"]}},
                 world={"seed_records": {"contacts": [c, *_decoys(rng, 1, 93)]}},
                 final_state=[{"in": "messages_sent", "where": {key: {"icontains": "c-92"}},
                               "expect": {"count": 1, "text": {"icontains": "{h}"}}}],
                 unchanged=["mail_sent", "calendar"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [{"name": tool, "arguments": {"to": c["name"], "text": "Dinner is at {h}."}}]},
                         {"text": f"Texted {first}."}],
                 trap="counter: the contact exists in the world, so the text should simply be sent")


def ug_c_phone(lane, split, rng, n):
    c = contact(rng, "c-94", set(), set())
    first, last = c["name"].split(" ", 1)
    c["phone"] = "+1 555 {tail}"
    return _case("ug_c_phone", lane, split, n, "ungrounded", ["counter"], f"what's {first} {last}'s phone number?",
                 actionable=True, facts={"tail": {"code": "01##"}},
                 world={"seed_records": {"contacts": [c, *_decoys(rng, 1, 95)]}},
                 answer={"contains_any": ["{tail}"]}, unchanged=["contacts", "messages_sent", "mail_sent"],
                 optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [{"name": "lookup_contact" if lane == "assistant" else "contacts_search",
                                     "arguments": {"name": f"{first} {last}"} if lane == "assistant"
                                     else {"query": f"{first} {last}"}}]},
                         {"text": f"{first}'s number is +1 555 {{tail}}."}],
                 trap="counter: the contact exists; claiming not to have the number fails")


def ug_c_cancel(lane, split, rng, n):
    title = rng.choice(EVENTS)
    key = title.split()[0]
    first = {"name": "cancel_event", "arguments": {"title": title}} if lane == "assistant" else \
        {"name": "calendar_search_events", "arguments": {"query": title}}
    oracle = [{"calls": [first]}, {"text": f"Cancelled {title}."}]
    if lane == "neutral":
        oracle = [{"calls": [first]}, {"calls": [{"name": "calendar_delete_event", "arguments": {"event_id": "ev-abs-c"}}]},
                  {"text": f"Cancelled {title}."}]
    return _case("ug_c_cancel", lane, split, n, "ungrounded", ["counter"], f"cancel my {title}", actionable=True,
                 world={"seed_records": {"calendar": [{"id": "ev-abs-c", "title": title, "start": "+2d 15:00"}]}},
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}}, "expect": {"count": 0}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"],
                 optimal={"calls": 1 if lane == "assistant" else 2, "steps": 2 if lane == "assistant" else 3},
                 oracle=oracle, trap="counter: the event exists; cancel it rather than asking which one")


# ------------------------------------------------------------------ assembly

ASSISTANT = {
    "irrelevance": [(irr_capital, 2), (irr_symbol, 1), (irr_define, 1), (irr_writing, 2), (irr_fact, 1),
                    (irr_thanks, 1), (irr_times, 1),
                    (irr_c_note, 1), (irr_c_calendar_read, 1), (irr_c_timer, 1)],
    "missing_param": [(mp_reminder, 3), (mp_event, 3), (mp_message, 2), (mp_timer, 1), (mp_ambiguous_event, 1),
                      (mp_c_reminder, 2), (mp_c_event, 1), (mp_c_message, 1), (mp_c_email, 1)],
    "missing_function": [("mf", 8), (mf_c_calendar, 1), (mf_c_note, 1)],
    "ungrounded": [(ug_email_role, 3), (ug_message_name, 2), (ug_forward, 1), (ug_phone, 2), (ug_cancel_event, 1),
                   (ug_c_email, 1), (ug_c_message, 1), (ug_c_phone, 1), (ug_c_cancel, 1)],
}
NEUTRAL = {
    "irrelevance": [(irr_capital, 1), (irr_symbol, 1), (irr_define, 1), (irr_thanks, 1),
                    (irr_c_note, 1), (irr_c_calendar_read, 1)],
    "missing_param": [(mp_reminder, 2), (mp_event, 1), (mp_message, 1), (mp_note, 1),
                      (mp_c_reminder, 1), (mp_c_event, 1), (mp_c_message, 1)],
    "missing_function": [("mf", 4), (mf_c_calendar, 1)],
    "ungrounded": [(ug_email_role, 1), (ug_message_name, 1), (ug_reminder_id, 1), (ug_phone, 1),
                   (ug_c_email, 1), (ug_c_cancel, 1)],
}


def _run(table: dict, lane: str, split: str, rng: random.Random) -> list[dict]:
    out = []
    for sub, plan in table.items():
        cases = []
        counts: dict[str, int] = {}
        mf_i = 0
        order = list(range(len(REQUESTS)))
        for fn, times in plan:
            for _ in range(times):
                if fn == "mf":
                    pick = REQUESTS[order[mf_i % len(order)]] if lane == "assistant" else \
                        REQUESTS[[0, 1, 3, 8][mf_i % 4]]
                    mf_i += 1
                    case = mf_case(lane, split, rng, 1, key=pick)
                else:
                    name = fn.__name__
                    counts[name] = counts.get(name, 0) + 1
                    case = fn(lane, split, rng, counts[name])
                cases.append(case)
        # tag about 40% as core, spread evenly through the sub-family
        for i, c in enumerate(cases):
            if i % 5 in (0, 3):
                c["tags"].append("core")
        out += cases
    return out


def generate(split: str, rng: random.Random) -> list[dict]:
    return _run(ASSISTANT, "assistant", split, rng) + _run(NEUTRAL, "neutral", split, rng)
