"""Family Select (spec 02 §3.1): calling the right tool with the right arguments.

Two lanes, hand-written templates, stratified by domain (mail, messages, calendar, reminders, notes,
files, device, web, contacts, memory; the domain is in each case's `notes` and in its template name)
and by distractor difficulty (`sub`):

* `easy`       the right tool is the only sensible one in its domain;
* `near_miss`  a similar tool is also offered (send vs draft, view vs summarize, complete vs clear,
               trash vs permanent delete, create vs update). A destructive or wrong-effect sibling is in
               `forbid`; any other sibling is in `menu_extra` so it is always offered;
* `paraphrase` the request shares no keyword with the tool name.

Writes are graded on `final_state`, reads on `answer` facts drawn per trial with `facts`. Everything
here was checked against the tool code (neutral: edat/tools_generic/tools_*.py; assistant:
scripts/toolbench/world.py fakes). Assistant-lane limits that shaped the templates: weather, stocks and
web are fixed (not seedable), `append_note` and `move_path` leave no trace in the world, and
`complete_reminder` removes the reminder (the lane reports it with done: true).

In templates `<<name>>` marks a value filled in at generation time; `{name}` marks a per-trial fact that
the case binds (edat/cases/bind.py).
"""
from __future__ import annotations

import random

from edat.cases.generators import case_id
from edat.cases.generators.pools import contact, email as make_email

FAMILY = "select"

A_COLLECTIONS = ["calendar", "reminders", "notes", "contacts", "mail", "files", "memory", "timers",
                 "scheduled_sends", "device", "messages_sent", "mail_sent", "message_drafts", "mail_drafts"]
N_COLLECTIONS = ["calendar", "reminders", "notes", "contacts", "mail", "mail_sent", "mail_drafts",
                 "messages_sent", "message_drafts", "scheduled_sends", "files", "trash", "device", "timers",
                 "memory"]


def T(s: str, **kw) -> str:
    """Fill generation-time `<<name>>` markers, leaving `{fact}` markers for the case binder."""
    for k, v in kw.items():
        s = s.replace(f"<<{k}>>", str(v))
    return s


def unch(lane: str, *touched: str) -> list[str]:
    base = A_COLLECTIONS if lane == "assistant" else N_COLLECTIONS
    return [c for c in base if c not in touched]


def last_word(s: str) -> str:
    return s.split()[-1]


# ----------------------------------------------------------------------------- builders
_RNG: list = [random.Random(0)]


def mk(lane: str, split: str, tmpl: str, n: int, *, sub: str, domain: str, prompt: str, expect: list,
       oracle: list, trap: str, facts: dict | None = None, seed: dict | None = None,
       final_state: list | None = None, answer: dict | None = None, forbid: list | None = None,
       unchanged: list | None = None, menu_extra: list | None = None, tags: list | None = None,
       extra: dict | None = None) -> dict:
    calls = sum(len(s.get("calls") or []) for s in oracle)
    case = {
        "id": case_id("sel", lane, split, tmpl, n), "lane": lane, "family": "select", "sub": sub,
        "tags": list(tags or []), "notes": f"domain: {domain}", "prompt": prompt,
    }
    if facts:
        case["facts"] = facts
    if seed:
        case["world"] = {"seed_records": seed}
    case["expect"] = expect
    if forbid:
        case["forbid"] = forbid
    if menu_extra:
        case["menu_extra"] = menu_extra
    if final_state:
        case["final_state"] = final_state
    if answer:
        case["answer"] = answer
    if unchanged is not None:
        case["unchanged"] = unchanged
    case["optimal"] = {"calls": calls, "steps": len(oracle)}
    case["oracle"] = oracle
    case["trap"] = trap
    if extra:
        case.update(extra)
    return _literal_ids(case, _RNG[0])


def call(tool_name: str, /, **args) -> dict:
    return {"calls": [{"name": tool_name, "arguments": args}]}


def say(text: str) -> dict:
    return {"text": text}


def person(rng: random.Random, used=None) -> tuple[str, str]:
    used = used or set()
    return rng.choice([f for f in FIRSTS if f not in used]), rng.choice(LASTS)


def pick(rng: random.Random, options: list, k: int) -> list:
    """k distinct options, in a draw order that depends on the rng."""
    return rng.sample(options, min(k, len(options)))


# Names that never collide with the assistant lane's canonical world (Wren, Kai, Remy, Tom, Maya, Omar, ... exist there).
FIRSTS = ["Ines", "Kofi", "Mei", "Rafael", "Leila", "Oskar", "Nadia", "Jonah", "Aiko", "Bruno", "Selin", "Mateo",
          "Hana", "Idris", "Clara", "Dmitri", "Farah", "Ingrid", "Joaquin", "Marisol"]
LASTS = ["Duarte", "Okafor", "Lindqvist", "Mensah", "Tanaka", "Ferreira", "Novak", "Achebe", "Moreau", "Castillo",
         "Byrne", "Kowalski"]
# (errand, a keyword no canonical reminder shares); the canonical world has bike light, vet, dry cleaning, bills ...
A_ERRANDS = [("renew the parking permit", "permit"), ("return the library books", "library"),
             ("buy printer ink", "printer"), ("water the tomatoes", "tomatoes"), ("post the parcel", "parcel"),
             ("order new contact lenses", "lenses"), ("collect the prescription", "prescription"),
             ("book a haircut", "haircut"), ("defrost the freezer", "freezer"), ("repot the fern", "fern"),
             ("check the smoke alarm", "smoke"), ("call the garage", "garage")]

THINGS = [("engineer visit", "the boiler service"), ("coach departure", "the school trip"),
          ("signing appointment", "the lease renewal"), ("bike pickup", "the bike repair"),
          ("badge collection", "the conference badge"), ("permit appointment", "the parking permit"),
          ("delivery window", "the garden fence"), ("fitting", "the wardrobe")]
PLACES_ = ["Lantern Bistro", "Copperleaf Café", "Harbourside Gym", "Northgate Library", "Juniper Hall",
           "Quayside Market"]
TASKS = ["pottery class", "piano lesson", "school meeting", "physio session", "chess evening", "tax appointment",
         "language exchange", "framing workshop"]
DRINKS = ["ginger tea", "jasmine tea", "oat flat white", "lemon barley water", "mint cocoa"]
LOCKERS = ["locker code", "garage keypad code", "storage unit code", "bike lock code", "gym door code"]
WEEKDAY_FACTS = ("in_2_days_weekday", "in_3_days_weekday")


def _ids(**names) -> dict:
    """Ids drawn when the case is generated (bind.py does not bind a key named `id`, so a per-trial id
    cannot be seeded); `{name}` in the case is replaced by the literal id."""
    return {k: {"id": v} for k, v in names.items()}


_DRAWN: set[str] = set()


def _literal_ids(case: dict, rng: random.Random) -> dict:
    facts = case.get("facts") or {}
    lit = {}
    for name, spec in list(facts.items()):
        if isinstance(spec, dict) and list(spec) == ["id"]:
            while True:
                val = f"{spec['id']}-{rng.randint(2000, 9999)}"
                if val not in _DRAWN:
                    _DRAWN.add(val)
                    break
            lit[name] = val
            del facts[name]
    if not facts:
        case.pop("facts", None)

    def sub(o):
        if isinstance(o, dict):
            return {k: sub(v) for k, v in o.items()}
        if isinstance(o, list):
            return [sub(v) for v in o]
        if isinstance(o, str):
            for k, v in lit.items():
                o = o.replace("{" + k + "}", v)
        return o
    return sub(case)


def names(rng: random.Random, k: int) -> list[tuple[str, str]]:
    firsts = rng.sample(FIRSTS, k)
    return [(f, rng.choice(LASTS)) for f in firsts]


# ============================================================================= ASSISTANT LANE
def a_mail_view(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        first, last = person(rng)
        other = rng.choice([t for t in THINGS if t[0] != thing])[0]
        addr = make_email(first, last)
        phr = [f"what time did {first} say the {thing} is?", f"{first} emailed about the {thing}. what's the time again?",
               f"i need the exact time of the {thing} from {first}'s email"][i % 3]
        out.append(mk(
            "assistant", split, "mail_view", i + 1, sub=["paraphrase", "near_miss", "near_miss"][i % 3], domain="mail", prompt=phr,
            facts={"h": {"int": [9, 11]}, "h2": {"int": [1, 4]}, **_ids(mid="m", mid2="m")},
            seed={"mail": [
                {"id": "{mid}", "from": f"{first} {last} <{addr}>", "subject": f"The {thing}",
                 "body": T("Hi, just confirming the <<thing>> is at {h}:30 in the morning. See you then. <<first>>",
                           thing=thing, first=first), "received": "-20m", "unread": True},
                {"id": "{mid2}", "from": f"{first} {last} <{addr}>", "subject": f"The {other}",
                 "body": T("Hello again, the <<other>> is at {h2}:15 in the afternoon. <<first>>",
                           other=other, first=first), "received": "-3d 10:00", "unread": False}]},
            expect=[{"any_of": ["view_emails", "summarize_emails"]}],
            answer={"contains_all": ["{h}:30"], "not_contains": ["{h2}:15"]},
            forbid=["reply_to_email", "send_email", "forward_email", "draft_email"],
            unchanged=unch("assistant"),
            oracle=[call("view_emails", query=first), say(f"{first} says the {thing} is at {{h}}:30 in the morning.")],
            trap="near-miss: summarize_emails gives a digest without the body, so the time is never read; "
                 "the older email from the same sender carries a different time"))
    return out


def a_mail_overview(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        thing = rng.choice(THINGS)[0]
        phr = ["give me a quick rundown of what has come into my inbox", "who has emailed me recently?"][i % 2]
        out.append(mk(
            "assistant", split, "mail_overview", i + 1, sub="near_miss", domain="mail", prompt=phr,
            facts=_ids(mid="m"),
            seed={"mail": [{"id": "{mid}", "from": f"{first} {last} <{make_email(first, last)}>",
                            "subject": f"About the {thing}", "body": f"Could you look at the {thing} soon?",
                            "received": "-10m", "unread": True}]},
            expect=[{"any_of": ["summarize_emails", "triage_inbox", "view_emails"]}],
            answer={"contains_all": [first]},
            forbid=["reply_to_email", "send_email", "forward_email", "email_triage"], unchanged=unch("assistant"),
            oracle=[call("summarize_emails"), say(f"Recent mail includes one from {first} {last} about the {thing}.")],
            trap="a broad overview wants the digest (or the recent raw list), and must not act on any message"))
    return out


def a_mail_archive(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        first, last = person(rng)
        f2, l2 = person(rng, {first})
        phr = [f"archive {first}'s email about the {thing}, i'm done with it but don't delete it",
               f"get {first}'s {thing} email out of my inbox and file it away"][i % 2]
        out.append(mk(
            "assistant", split, "mail_archive", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="mail", prompt=phr,
            facts=_ids(mid="m", mid2="m"),
            seed={"mail": [
                {"id": "{mid}", "from": f"{first} {last} <{make_email(first, last)}>", "subject": f"The {thing}",
                 "body": f"Thanks again about the {thing}.", "received": "-30m", "unread": True},
                {"id": "{mid2}", "from": f"{f2} {l2} <{make_email(f2, l2)}>", "subject": f"Lunch plans",
                 "body": "Are you free on Friday?", "received": "-40m", "unread": True}]},
            expect=[{"tool": "email_triage", "args": {"action": "archive"}}],
            final_state=[{"in": "mail", "where": {"id": "{mid}"}, "expect": {"folder": "archive"}},
                         {"in": "mail", "where": {"id": "{mid2}"}, "expect": {"folder": "inbox"}}],
            unchanged=unch("assistant", "mail"),
            oracle=[call("view_emails", query=first), call("email_triage", message_id="{mid}", action="archive"),
                    say("Archived it; it is out of your inbox but not deleted.")],
            trap="near-miss: mark_read leaves the email in the inbox; the other sender's email must stay",
            menu_extra=["email_triage"]))
    return out


def a_mail_markread(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        first, last = person(rng)
        phr = [f"mark {first}'s email about the {thing} as read", f"i've dealt with {first}'s {thing} email, clear its unread dot"][i % 2]
        out.append(mk(
            "assistant", split, "mail_markread", i + 1, sub="paraphrase" if i % 2 else "near_miss", domain="mail",
            prompt=phr, facts=_ids(mid="m"),
            seed={"mail": [{"id": "{mid}", "from": f"{first} {last} <{make_email(first, last)}>",
                            "subject": f"The {thing}", "body": "Please have a look when you can.",
                            "received": "-30m", "unread": True}]},
            expect=[{"tool": "email_triage", "args": {"action": "mark_read"}}],
            final_state=[{"in": "mail", "where": {"id": "{mid}"}, "expect": {"unread": False, "folder": "inbox"}}],
            unchanged=unch("assistant", "mail"),
            oracle=[call("view_emails", query=first), call("email_triage", message_id="{mid}", action="mark_read"),
                    say("Marked as read.")],
            trap="near-miss: archive also clears the unread flag but moves the email out of the inbox"))
    return out


def a_mail_reply(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        first, last = person(rng)
        wd = ["in_2_days_weekday", "in_3_days_weekday"][i % 2]
        phr = [f"reply to {first}'s email about the {thing} and say {{{wd}}} works for me",
               f"answer {first} about the {thing}: tell them {{{wd}}} is fine"][i % 2]
        out.append(mk(
            "assistant", split, "mail_reply", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="mail", prompt=phr,
            facts=_ids(mid="m"),
            seed={"mail": [{"id": "{mid}", "from": f"{first} {last} <{make_email(first, last)}>",
                            "subject": f"Can we fix the {thing}?", "body": "Which day suits you best this week?",
                            "received": "-25m", "unread": True}]},
            expect=[{"tool": "reply_to_email", "args": {"message_id": "{mid}"}}],
            forbid=["forward_email"],
            final_state=[{"in": "mail_sent", "where": {"kind": "reply", "in_reply_to": "{mid}"},
                          "expect": {"count": 1, "body": {"icontains": f"{{{wd}}}"}}}],
            unchanged=unch("assistant", "mail_sent"), menu_extra=["send_email", "draft_email"],
            oracle=[call("view_emails", query=first),
                    call("reply_to_email", message_id="{mid}", body=f"Hi {first}, {{{wd}}} works for me. Thanks!"),
                    say("Replied.")],
            trap="near-miss: a reply must answer the thread (reply_to_email), not start a new email or forward it"))
    return out


def a_mail_send(split, rng, k):
    out = []
    for i, ((first, last), item) in enumerate(zip(names(rng, k), pick(rng, ["chairs", "tents", "ladders", "lamps"], k))):
        addr = make_email(first, last)
        phr = [f"email {first} to say i'm bringing {{n}} {item} on saturday",
               f"send {first} a quick email: i'll bring {{n}} {item} saturday"][i % 2]
        out.append(mk(
            "assistant", split, "mail_send", i + 1, sub=["paraphrase", "near_miss"][i % 2], domain="mail", prompt=phr,
            facts={"cid": {"id": "c"}, "n": {"int": [3, 9]}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "phone": "+1 555 0171", "email": addr}]},
            expect=[{"tool": "send_email", "args": {"body": {"icontains": "{n}"}}}],
            final_state=[{"in": "mail_sent", "where": {"kind": "send"},
                          "expect": {"count": 1, "to": {"icontains": addr}, "body": {"icontains": "{n}"}}}],
            unchanged=unch("assistant", "mail_sent"), menu_extra=["draft_email", "schedule_send"],
            oracle=[call("send_email", to=addr, subject="Saturday", body=f"Hi {first}, I'm bringing {{n}} {item} on Saturday."),
                    say(f"Sent the email to {first}.")],
            trap="near-miss: draft_email only saves a draft, so nothing reaches the recipient",
            extra={"intended_recipients": ["{cid}"]}))
    return out


def a_mail_draft(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        addr = make_email(first, last)
        phr = [f"draft an email to {first} asking if {{n}} people can come on friday, i want to read it before it goes",
               f"write up an email to {first} about {{n}} guests friday, but don't send it, i'll check it first"][i % 2]
        out.append(mk(
            "assistant", split, "mail_draft", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="mail", prompt=phr,
            facts={"cid": {"id": "c"}, "n": {"int": [3, 9]}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "phone": "+1 555 0172", "email": addr}]},
            expect=[{"tool": "draft_email"}],
            forbid=["send_email", "schedule_send", "reply_to_email", "forward_email"],
            final_state=[{"in": "mail_drafts", "expect": {"count": 1}},
                         {"in": "mail_drafts", "where": {"to": {"icontains": addr}}, "expect": {"body": {"icontains": "{n}"}}}],
            unchanged=unch("assistant", "mail_drafts"),
            oracle=[call("draft_email", to=addr, subject="Friday", body=f"Hi {first}, can {{n}} people come on Friday?"),
                    say("The draft is ready for you to review; nothing was sent.")],
            trap="near-miss: send_email delivers it now, against 'let me read it first'",
            extra={"intended_recipients": ["{cid}"]}))
    return out


def a_mail_forward(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        first, last = person(rng)
        f2, l2 = person(rng, {first})
        a2 = make_email(f2, l2)
        phr = [f"forward {first}'s email about the {thing} to {f2}", f"pass {first}'s {thing} email on to {f2}"][i % 2]
        out.append(mk(
            "assistant", split, "mail_forward", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="mail", prompt=phr,
            facts={**_ids(mid="m"), "cid": {"id": "c"}},
            seed={"mail": [{"id": "{mid}", "from": f"{first} {last} <{make_email(first, last)}>",
                            "subject": f"The {thing}", "body": f"Here are the details for the {thing}.",
                            "received": "-30m", "unread": True}],
                  "contacts": [{"id": "{cid}", "name": f"{f2} {l2}", "phone": "+1 555 0173", "email": a2}]},
            expect=[{"tool": "forward_email", "args": {"message_id": "{mid}"}}],
            forbid=["reply_to_email"],
            final_state=[{"in": "mail_sent", "where": {"kind": "forward", "in_reply_to": "{mid}"},
                          "expect": {"count": 1, "to": {"icontains": a2}}}],
            unchanged=unch("assistant", "mail_sent"), menu_extra=["send_email"],
            oracle=[call("view_emails", query=first), call("forward_email", message_id="{mid}", to=a2),
                    say(f"Forwarded it to {f2}.")],
            trap="near-miss: reply_to_email would answer the original sender instead of passing the email on",
            extra={"intended_recipients": ["{cid}"]}))
    return out


def a_mail_schedule(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        addr = make_email(first, last)
        out.append(mk(
            "assistant", split, "mail_schedule", i + 1, sub="near_miss", domain="mail",
            prompt=f"email {first} tomorrow at 9am to remind them about the {{n}}pm meeting",
            facts={"cid": {"id": "c"}, "n": {"int": [2, 5]}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "phone": "+1 555 0174", "email": addr}]},
            expect=[{"tool": "schedule_send", "args": {"channel": "email"}}],
            forbid=["send_email"],
            final_state=[{"in": "scheduled_sends", "where": {"channel": "email", "to": {"icontains": addr}},
                          "expect": {"count": 1, "when": "+1d 09:00", "status": "queued", "text": {"icontains": "{n}"}}}],
            unchanged=unch("assistant", "scheduled_sends"), tags=["datetime"], menu_extra=["draft_email"],
            oracle=[call("schedule_send", channel="email", to=addr, when="tomorrow at 9am", subject="Meeting reminder",
                         body=f"Hi {first}, a reminder about the {{n}}pm meeting."),
                    say(f"Scheduled for tomorrow at 9:00 AM to {first}.")],
            trap="near-miss: send_email would deliver it now instead of tomorrow morning",
            extra={"intended_recipients": ["{cid}"]}))
    return out


def a_msg_send(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        phr = [f"text {first} that i'm {{n}} minutes late", f"let {first} know i'll be {{n}} minutes behind"][i % 2]
        out.append(mk(
            "assistant", split, "msg_send", i + 1, sub="paraphrase" if i % 2 else "near_miss", domain="messages",
            prompt=phr, facts={"cid": {"id": "c"}, "n": {"int": [5, 25]}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "phone": "+1 555 0175",
                                "email": make_email(first, last)}]},
            expect=[{"tool": "send_message", "args": {"text": {"icontains": "{n}"}}}],
            final_state=[{"in": "messages_sent", "expect": {"count": 1}},
                         {"in": "messages_sent", "where": {"contact_ids": {"icontains": "{cid}"}},
                          "expect": {"count": 1, "text": {"icontains": "{n}"}}}],
            unchanged=unch("assistant", "messages_sent"), menu_extra=["draft_message", "schedule_send"],
            oracle=[call("send_message", to=f"{first} {last}", text=f"Running {{n}} minutes late, sorry!"),
                    say(f"Texted {first}.")],
            trap="near-miss: draft_message only prepares a draft, so the text is never sent",
            extra={"intended_recipients": ["{cid}"]}))
    return out


def a_msg_draft(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        phr = [f"write {first} a text asking if {{n}}:30 works, but let me look at it before it goes out",
               f"draft a message to {first} proposing {{n}}:30, i'll send it myself"][i % 2]
        out.append(mk(
            "assistant", split, "msg_draft", i + 1, sub=["paraphrase", "near_miss"][i % 2], domain="messages", prompt=phr,
            facts={"cid": {"id": "c"}, "n": {"int": [3, 6]}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "phone": "+1 555 0176",
                                "email": make_email(first, last)}]},
            expect=[{"tool": "draft_message"}], forbid=["send_message", "schedule_send"],
            final_state=[{"in": "message_drafts", "expect": {"count": 1}},
                         {"in": "message_drafts", "where": {"contact_ids": {"icontains": "{cid}"}},
                          "expect": {"count": 1}}],
            unchanged=unch("assistant", "message_drafts"),
            oracle=[call("draft_message", to=f"{first} {last}", text=f"Does {{n}}:30 work for you?"),
                    say("The draft is ready for you to review; nothing was sent.")],
            trap="near-miss: send_message would send it without the review the user asked for",
            extra={"intended_recipients": ["{cid}"]}))
    return out


def a_msg_schedule(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        out.append(mk(
            "assistant", split, "msg_schedule", i + 1, sub="near_miss", domain="messages",
            prompt=f"text {first} at 8 tomorrow morning saying happy birthday",
            facts={"cid": {"id": "c"}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "phone": "+1 555 0177",
                                "email": make_email(first, last)}]},
            expect=[{"tool": "schedule_send", "args": {"channel": "message"}}], forbid=["send_message"],
            final_state=[{"in": "scheduled_sends", "where": {"channel": "message", "to": {"icontains": "0177"}},
                          "expect": {"count": 1, "when": "+1d 08:00", "status": "queued",
                                     "text": {"icontains": "birthday"}}}],
            unchanged=unch("assistant", "scheduled_sends"), tags=["datetime"], menu_extra=["draft_message"],
            oracle=[call("schedule_send", channel="message", to=f"{first} {last}", when="tomorrow at 8am",
                         body="Happy birthday!"), say(f"Scheduled the text to {first} for tomorrow at 8:00 AM.")],
            trap="near-miss: send_message would text them right now, a day early",
            extra={"intended_recipients": ["{cid}"]}))
    return out


def a_msg_view(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        first, last = person(rng)
        full = f"{first} {last}"
        phr = [f"what did {first} text me about the {thing}?", f"{first} messaged me the time for the {thing}, what was it?"][i % 2]
        out.append(mk(
            "assistant", split, "msg_view", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="messages", prompt=phr,
            facts={"h": {"int": [9, 11]}, "h2": {"int": [1, 4]}, **_ids(mid="x", mid2="x")},
            seed={"messages": [
                {"id": "{mid2}", "thread": full, "from": full, "text": "Earlier I said {h2}:45, but that was wrong",
                 "received": "-2d 09:00", "unread": False},
                {"id": "{mid}", "thread": full, "from": full,
                 "text": T("Update on the <<thing>>: it is at {h}:15 sharp", thing=thing), "received": "-20m",
                 "unread": True}]},
            expect=[{"any_of": ["view_messages", "summarize_messages"]}],
            answer={"contains_all": ["{h}:15"]},
            forbid=["send_message", "draft_message", "schedule_send"], unchanged=unch("assistant"),
            oracle=[call("view_messages", conversation=first), say(f"{first} says the {thing} is at {{h}}:15.")],
            trap="reading a specific message needs the raw text, not a mail search or a send"))
    return out


def a_msg_vs_mail(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        first, last = person(rng)
        full = f"{first} {last}"
        phr = [f"{first} sent me the time for the {thing} in a text, what was it?",
               f"what time did {first} message me for the {thing}? not the email"][i % 2]
        out.append(mk(
            "assistant", split, "msg_vs_mail", i + 1, sub="paraphrase", domain="messages", prompt=phr,
            facts={"h": {"int": [9, 11]}, "h2": {"int": [1, 4]}, **_ids(mid="x", mid2="m")},
            seed={"messages": [{"id": "{mid}", "thread": full, "from": full,
                                "text": f"Quick one, the {thing} is at {{h}}:20", "received": "-15m", "unread": True}],
                  "mail": [{"id": "{mid2}", "from": f"{full} <{make_email(first, last)}>",
                            "subject": f"The {thing}", "body": f"Per our chat, the {thing} is at {{h2}}:40 pm.",
                            "received": "-12m", "unread": True}]},
            expect=[{"any_of": ["view_messages", "summarize_messages"]}],
            answer={"contains_all": ["{h}:20"], "not_contains": ["{h2}:40"]},
            forbid=["send_message", "draft_message", "reply_to_email", "send_email"], unchanged=unch("assistant"),
            oracle=[call("view_messages", conversation=first), say(f"By text, {first} said the {thing} is at {{h}}:20.")],
            trap="domain confusion: the same person also emailed a different time"))
    return out


# ---- calendar
def a_cal_add(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        what = ["lunch", "coffee", "a walk", "a catch-up"][i % 4]
        phr = [f"put {what} with {first} on my calendar for {{in_3_days_weekday}} at {{h12}} pm",
               f"add {what} with {first} to the calendar, {{in_3_days_weekday}} {{h12}}pm"][i % 2]
        out.append(mk(
            "assistant", split, "cal_add", i + 1, sub="near_miss", domain="calendar", prompt=phr,
            facts={"h12": {"int": [2, 5]}, "h24": {"add": ["h12", 12]}},
            expect=[{"tool": "add_calendar_event", "args": {"title": {"icontains": first}}}],
            final_state=[{"in": "calendar", "where": {"title": {"icontains": first}},
                          "expect": {"count": 1, "start": "+3d {h24}:00"}}],
            unchanged=unch("assistant", "calendar"), tags=["datetime"], menu_extra=["add_reminder"],
            oracle=[call("add_calendar_event", title=f"{what.capitalize()} with {first}", when_iso="{in_3_days_iso}T{h24}:00"),
                    say(f"Added {what} with {first}.")],
            trap="near-miss: add_reminder makes a private reminder, not a calendar event"))
    return out


def a_cal_update(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        what = TASKS[(i * 3 + rng.randint(0, 2)) % len(TASKS)]
        out.append(mk(
            "assistant", split, "cal_update", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="calendar",
            prompt=[f"move my {what} with {first} to tomorrow at {{h12}} pm", f"push the {what} with {first} to {{h12}}pm tomorrow"][i % 2],
            facts={"h12": {"int": [1, 5]}, "h24": {"add": ["h12", 12]}, "eid": {"id": "ev"}},
            seed={"calendar": [{"id": "{eid}", "title": f"{what.capitalize()} with {first}", "start": "+2d 10:00"}]},
            expect=[{"tool": "update_event", "args": {"title": {"icontains": first}}}],
            forbid=["cancel_event"],
            final_state=[{"in": "calendar", "where": {"id": "{eid}"}, "expect": {"start": "+1d {h24}:00"}},
                         {"in": "calendar", "where": {"title": {"icontains": first}}, "expect": {"count": 1}}],
            unchanged=unch("assistant", "calendar"), tags=["datetime"], menu_extra=["add_calendar_event"],
            oracle=[call("update_event", title=f"{what.capitalize()} with {first}", when_iso="{tomorrow_iso}T{h24}:00"),
                    say("Moved it.")],
            trap="near-miss: add_calendar_event would leave the old event and duplicate it; cancel_event would delete it"))
    return out


def a_cal_cancel(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        what = TASKS[(i * 4 + rng.randint(0, 3)) % len(TASKS)]
        out.append(mk(
            "assistant", split, "cal_cancel", i + 1, sub="near_miss", domain="calendar",
            prompt=[f"cancel my {what} with {first}", f"i can't make the {what} with {first} any more, take it off my calendar"][i % 2],
            facts={"eid": {"id": "ev"}, "eid2": {"id": "ev"}},
            seed={"calendar": [{"id": "{eid}", "title": f"{what.capitalize()} with {first}", "start": "+2d 10:00"},
                               {"id": "{eid2}", "title": f"Dinner with {first}", "start": "+3d 19:00"}]},
            expect=[{"tool": "cancel_event", "args": {"title": {"icontains": what.split()[0]}}}],
            final_state=[{"in": "calendar", "where": {"id": "{eid}"}, "expect": {"absent": True}},
                         {"in": "calendar", "where": {"id": "{eid2}"}, "expect": {"count": 1}}],
            unchanged=unch("assistant", "calendar"), menu_extra=["update_event"],
            oracle=[call("cancel_event", title=f"{what.capitalize()} with {first}"), say("Cancelled it.")],
            trap="near-miss: update_event moves the event instead of removing it; a second event with the same person must stay"))
    return out


def a_cal_upcoming(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        what = TASKS[(i * 3 + rng.randint(0, 2)) % len(TASKS)]
        out.append(mk(
            "assistant", split, "cal_upcoming", i + 1, sub="paraphrase" if i else "easy", domain="calendar",
            prompt=["what do i have on the calendar the day after tomorrow?", "am i busy in two days? what's on?",
                    "what's my schedule looking like on {in_2_days_weekday}?"][i % 3],
            facts={"h": {"int": [9, 12]}, "eid": {"id": "ev"}},
            seed={"calendar": [{"id": "{eid}", "title": f"{what.capitalize()} with {first}", "start": "+2d {h}:00"}]},
            expect=[{"any_of": ["get_upcoming", "get_my_day_plan", "daily_brief"]}],
            answer={"contains_all": [first]}, forbid=["cancel_event", "update_event", "add_calendar_event"],
            unchanged=unch("assistant"),
            oracle=[call("get_upcoming", period="in two days"),
                    say(f"On that day you have {what} with {first} at {{h}}:00.")],
            trap="a calendar read must not change the calendar", extra={}))
    return out


def a_cal_past(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        what = TASKS[(i * 5 + rng.randint(0, 4)) % len(TASKS)]
        out.append(mk(
            "assistant", split, "cal_past", i + 1, sub="near_miss", domain="calendar",
            prompt=["who did i have a meeting with yesterday afternoon?", "what was on my calendar yesterday?"][i % 2],
            facts={"eid": {"id": "ev"}},
            seed={"calendar": [{"id": "{eid}", "title": f"{what.capitalize()} with {first}", "start": "-1d 14:00"}]},
            expect=[{"tool": "get_past_events"}], answer={"contains_all": [first]},
            forbid=["add_calendar_event", "cancel_event"], unchanged=unch("assistant"), menu_extra=["get_upcoming"],
            oracle=[call("get_past_events", days=2), say(f"Yesterday at 2 PM you had {what} with {first}.")],
            trap="near-miss: get_upcoming looks forward and never shows yesterday"))
    return out


# ---- reminders
def a_rem_complete(split, rng, k):
    out = []
    for i, ((errand, kw), (other, _)) in enumerate(zip(pick(rng, A_ERRANDS, k), pick(rng, A_ERRANDS[::-1], k))):
        if errand == other:
            other = A_ERRANDS[(A_ERRANDS.index((errand, kw)) + 1) % len(A_ERRANDS)][0]
        phr = [f"done with the {kw} one, tick it off", f"{errand} - that's finished, check it off",
               f"mark the {kw} reminder as complete"][i % 3]
        out.append(mk(
            "assistant", split, "rem_complete", i + 1, sub=["near_miss", "paraphrase", "near_miss"][i % 3], domain="reminders", prompt=phr,
            facts={"due_h": {"int": [9, 17]}, **_ids(rid="rem", rid2="rem")},
            seed={"reminders": [{"id": "{rid}", "title": errand.capitalize(), "due": "+1d {due_h}:00", "done": False},
                                {"id": "{rid2}", "title": other.capitalize(), "due": "+2d 18:00", "done": False}]},
            expect=[{"tool": "complete_reminder", "args": {"title": {"icontains": kw}}}],
            forbid=["clear_reminders", "clear_past_reminders"],
            final_state=[{"in": "reminders", "where": {"id": "{rid}"}, "expect": {"done": True}},
                         {"in": "reminders", "where": {"id": "{rid2}"}, "expect": {"done": False}}],
            unchanged=unch("assistant", "reminders"),
            oracle=[call("complete_reminder", title=errand.capitalize()), say(f"Done, I ticked off '{errand}'.")],
            trap="near-miss: clear_reminders deletes reminders instead of completing the one"))
    return out


def a_rem_add(split, rng, k):
    out = []
    for i, (errand, kw) in enumerate(pick(rng, A_ERRANDS, k)):
        out.append(mk(
            "assistant", split, "rem_add", i + 1, sub="paraphrase" if i % 2 else "near_miss", domain="reminders",
            prompt=[f"remind me to {errand} tomorrow at {{h}} am", f"don't let me forget to {errand} tomorrow morning at {{h}}"][i % 2],
            facts={"h": {"int": [10, 11]}},
            expect=[{"tool": "add_reminder", "args": {"title": {"icontains": kw}}}],
            final_state=[{"in": "reminders", "where": {"title": {"icontains": kw}}, "expect": {"count": 1, "due": "+1d {h}:00"}}],
            unchanged=unch("assistant", "reminders"), tags=["datetime"], menu_extra=["add_calendar_event", "set_timer"],
            oracle=[call("add_reminder", title=errand.capitalize(), when_iso="{tomorrow_iso}T{h}:00"), say("Reminder set.")],
            trap="near-miss: a calendar event or a countdown timer is not a reminder"))
    return out


def a_rem_update(split, rng, k):
    out = []
    for i, (errand, kw) in enumerate(pick(rng, A_ERRANDS, k)):
        out.append(mk(
            "assistant", split, "rem_update", i + 1, sub="near_miss", domain="reminders",
            prompt=[f"push the {kw} reminder to {{in_3_days_weekday}} at {{h}}", f"move my {kw} reminder to {{h}} on {{in_3_days_weekday}}"][i % 2],
            facts={"h": {"int": [10, 16]}, "rid": {"id": "rem"}},
            seed={"reminders": [{"id": "{rid}", "title": errand.capitalize(), "due": "+1d 09:00", "done": False}]},
            expect=[{"tool": "update_reminder", "args": {"title": {"icontains": kw}}}],
            forbid=["clear_reminders", "clear_past_reminders", "complete_reminder"],
            final_state=[{"in": "reminders", "where": {"id": "{rid}"}, "expect": {"done": False, "due": "+3d {h}:00"}},
                         {"in": "reminders", "where": {"title": {"icontains": kw}}, "expect": {"count": 1}}],
            unchanged=unch("assistant", "reminders"), tags=["datetime"], menu_extra=["add_reminder"],
            oracle=[call("update_reminder", title=errand.capitalize(), when_iso="{in_3_days_iso}T{h}:00"), say("Moved it.")],
            trap="near-miss: add_reminder would leave the old reminder and duplicate it"))
    return out


def a_rem_clear(split, rng, k):
    out = []
    for i, (e1, e2) in enumerate([tuple(e for e, _ in pick(rng, A_ERRANDS, 2))] if k else []):
        out.append(mk(
            "assistant", split, "rem_clear", i + 1, sub="near_miss", domain="reminders",
            prompt="i'm taking the day off, wipe out everything on my reminder list for today",
            facts=_ids(r1="rem", r2="rem", r3="rem"),
            seed={"reminders": [{"id": "{r1}", "title": e1.capitalize(), "due": "+2h", "done": False},
                                {"id": "{r2}", "title": e2.capitalize(), "due": "+4h", "done": False},
                                {"id": "{r3}", "title": "Plan the tomorrow standup", "due": "+1d 09:00", "done": False}]},
            expect=[{"tool": "clear_reminders", "args": {"scope": "today"}}],
            final_state=[{"in": "reminders", "where": {"id": "{r1}"}, "expect": {"absent": True}},
                         {"in": "reminders", "where": {"id": "{r2}"}, "expect": {"absent": True}},
                         {"in": "reminders", "where": {"id": "{r3}"}, "expect": {"done": False}}],
            unchanged=unch("assistant", "reminders"), menu_extra=["complete_reminder", "clear_past_reminders"],
            oracle=[call("clear_reminders", scope="today"), say("Cleared today's reminders; tomorrow's is untouched.")],
            trap="near-miss: complete_reminder per item keeps the rows (done), and a wider scope would clear tomorrow's too"))
    return out


def a_rem_search(split, rng, k):
    out = []
    for i, (errand, kw) in enumerate(pick(rng, A_ERRANDS, k)):
        out.append(mk(
            "assistant", split, "rem_search", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="reminders",
            prompt=[f"when is my reminder to {errand} due?", f"what time did i set for the {kw} reminder?"][i % 2],
            facts={"h": {"int": [10, 11]}, "rid": {"id": "rem"}},
            seed={"reminders": [{"id": "{rid}", "title": errand.capitalize(), "due": "+2d {h}:00", "done": False}]},
            expect=[{"any_of": ["search_reminders", "get_upcoming"]}],
            answer={"contains_any": ["{h}:00", "{h} am", "{h}am", "{h} AM", "{h}:00 AM"]},
            forbid=["complete_reminder", "clear_reminders", "update_reminder"], unchanged=unch("assistant"),
            oracle=[call("search_reminders", query=kw), say(f"It is due on Friday at {{h}}:00 AM.")],
            trap="a lookup must not complete or change the reminder"))
    return out


# ---- notes
def a_note_create(split, rng, k):
    out = []
    for i, city in enumerate(pick(rng, ["Porto", "Lisbon", "Oslo", "Kyoto"], k)):
        out.append(mk(
            "assistant", split, "note_create", i + 1, sub="near_miss", domain="notes",
            prompt=[f"make a note called Packing for {city} saying to bring {{n}} adapters",
                    f"jot down a note titled Packing for {city}: {{n}} adapters"][i % 2],
            facts={"n": {"int": [2, 6]}},
            expect=[{"tool": "create_note", "args": {"title": {"icontains": city}}}],
            final_state=[{"in": "notes", "where": {"title": {"icontains": city}},
                          "expect": {"count": 1, "body": {"icontains": "{n}"}}}],
            unchanged=unch("assistant", "notes"), menu_extra=["remember", "append_note", "add_reminder"],
            oracle=[call("create_note", title=f"Packing for {city}", body="Bring {n} adapters."), say("Saved the note.")],
            trap="near-miss: remember files a personal fact in memory and add_reminder makes a reminder, not a note"))
    return out


def a_note_search(split, rng, k):
    out = []
    for i, place in enumerate(pick(rng, ["cabin", "boathouse", "studio", "allotment"], k)):
        out.append(mk(
            "assistant", split, "note_search", i + 1, sub="paraphrase" if i else "easy", domain="notes",
            prompt=[f"what was the wifi password i wrote down for the {place}?",
                    f"look up the {place} wifi code in my notes"][i % 2],
            facts={"code": {"code": "@@##@@"}, "nid": {"id": "note"}},
            seed={"notes": [{"id": "{nid}", "title": f"{place.capitalize()} wifi", "body": f"Network password for the {place}: {{code}}"}]},
            expect=[{"tool": "search_notes"}], answer={"contains_all": ["{code}"]},
            forbid=["delete_note", "append_note"], unchanged=unch("assistant"), menu_extra=["recall"],
            oracle=[call("search_notes", query=place), say(f"The {place} wifi password is {{code}}.")],
            trap="the answer lives in a note, not in saved memory"))
    return out


def a_note_delete(split, rng, k):
    out = []
    for i, title in enumerate(pick(rng, ["Old gift list", "Spare keys", "Garden plan", "Draft toast"], k)):
        out.append(mk(
            "assistant", split, "note_delete", i + 1, sub="easy", domain="notes",
            prompt=f"delete my note called {title}", facts=_ids(nid="note", nid2="note"),
            seed={"notes": [{"id": "{nid}", "title": title, "body": "stuff nobody needs"},
                            {"id": "{nid2}", "title": f"{title} (new)", "body": "keep this"}]},
            expect=[{"tool": "delete_note", "args": {"title": {"icontains": title.split()[0]}}}],
            final_state=[{"in": "notes", "where": {"id": "{nid}"}, "expect": {"absent": True}},
                         {"in": "notes", "where": {"id": "{nid2}"}, "expect": {"count": 1}}],
            unchanged=unch("assistant", "notes"), menu_extra=["append_note", "forget"],
            oracle=[call("delete_note", title=title), say("Deleted it.")],
            trap="the note with a similar title must survive"))
    return out


# ---- files
def a_file_read(split, rng, k):
    out = []
    for i, (name, what) in enumerate(pick(rng, [("gate_notes", "gate code"), ("shed_info", "padlock code"),
                                                ("garage_notes", "keypad code"), ("storage_info", "unit number")], k)):
        out.append(mk(
            "assistant", split, "file_read", i + 1, sub="paraphrase" if i else "near_miss", domain="files",
            prompt=[f"what {what} is written in my {name} file in Documents?", f"check {name}.txt in Documents and tell me the {what}"][i % 2],
            facts={"code": {"code": "####"}},
            seed={"files": [{"id": f"~/Documents/{name}.txt", "path": f"~/Documents/{name}.txt", "kind": "file",
                             "text": f"Reminder: the {what} is {{code}}. Do not share it."}]},
            expect=[{"tool": "read_file"}], answer={"contains_all": ["{code}"]},
            forbid=["write_file", "move_path", "run_shell"], unchanged=unch("assistant"), menu_extra=["find_files", "list_dir"],
            oracle=[call("read_file", path=f"~/Documents/{name}.txt"), say(f"The {what} is {{code}}.")],
            trap="near-miss: listing or finding the file does not read what is inside"))
    return out


def a_file_find(split, rng, k):
    out = []
    for i, name in enumerate(pick(rng, ["boarding_pass", "bike_receipt", "visa_letter", "class_syllabus"], k)):
        code = rng.randint(1000, 9999)
        path = f"~/Downloads/{name}_{code}.pdf"
        out.append(mk(
            "assistant", split, "file_find", i + 1, sub="easy" if i % 2 else "paraphrase", domain="files",
            prompt=[f"where did i save the {name.replace('_', ' ')} pdf?", f"find my {name.replace('_', ' ')} file"][i % 2],
            seed={"files": [{"id": path, "path": path, "kind": "file"}]},
            expect=[{"tool": "find_files"}], answer={"contains_all": [f"{name}_{code}"]},
            forbid=["write_file", "move_path", "run_shell"], unchanged=unch("assistant"), menu_extra=["list_dir", "read_file"],
            oracle=[call("find_files", query=name.split("_")[0]), say(f"It is at {path}.")],
            trap="a find request needs a search; the file name carries a number only the file system knows"))
    return out


# ---- device and apps
SETTINGS = [("dark_mode", True, ["switch the screen to dark mode", "my eyes hurt, go dark"]),
            ("bluetooth", False, ["turn off bluetooth, i'm done with the headphones", "stop bluetooth from running"]),
            ("do_not_disturb", True, ["i'm presenting, silence the notifications", "turn on do not disturb"]),
            ("wifi", False, ["switch the wifi off", "disconnect me from the wireless network"])]


def a_setting(split, rng, k):
    out = []
    for i, (name, on, phrases) in enumerate(pick(rng, SETTINGS, k)):
        out.append(mk(
            "assistant", split, "setting", i + 1, sub="paraphrase" if i % 2 else "easy", domain="device",
            prompt=phrases[i % 2],
            expect=[{"tool": "toggle_setting", "args": {"setting": name, "on": on}}],
            final_state=[{"in": "device", "where": {"id": name}, "expect": {"value": on}}],
            unchanged=unch("assistant", "device"), menu_extra=["mac_mute", "mac_set_volume"],
            forbid=["run_shell", "run_applescript"],
            oracle=[call("toggle_setting", setting=name, on=on), say("Done.")],
            trap="a named system setting goes through toggle_setting, not a shell or the volume tools"))
    return out


def a_volume(split, rng, k):
    return [mk(
        "assistant", split, "volume", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="device",
        prompt=["turn the volume to {n} percent", "set the sound level to {n}%"][i % 2],
        facts={"n": {"choice": [10, 20, 30, 55, 60, 70, 80, 90]}},
        expect=[{"tool": "mac_set_volume", "args": {"level": "{n}"}}], forbid=["mac_mute"],
        final_state=[{"in": "device", "where": {"id": "volume"}, "expect": {"value": "{n}"}},
                     {"in": "device", "where": {"id": "muted"}, "expect": {"value": False}}],
        unchanged=unch("assistant", "device"), menu_extra=["toggle_setting"],
        oracle=[call("mac_set_volume", level="{n}"), say("Volume set.")],
        trap="near-miss: mac_mute silences instead of setting a level") for i in range(min(k, 2))]


def a_mute(split, rng, k):
    return [mk(
        "assistant", split, "mute", i + 1, sub="paraphrase", domain="device",
        prompt=["shh, silence the speakers", "kill the sound on the mac"][i % 2],
        expect=[{"tool": "mac_mute", "args": {"state": "on"}}],
        final_state=[{"in": "device", "where": {"id": "muted"}, "expect": {"value": True}}],
        unchanged=unch("assistant", "device"), menu_extra=["mac_set_volume", "toggle_setting"],
        oracle=[call("mac_mute", state="on"), say("Muted.")],
        trap="near-miss: lowering the volume with mac_set_volume does not mute") for i in range(min(k, 1))]


def a_app(split, rng, k):
    out = []
    plan = [("quit_app", "Safari", ["quit Safari completely", "close Safari down, the whole app"]),
            ("open_app", "Maps", ["open the Maps app", "launch Maps for me"]),
            ("quit_app", "Notes", ["quit the Notes app", "shut down Notes entirely"])]
    for i, (tool, app, phrases) in enumerate(pick(rng, plan, k)):
        if tool == "quit_app":
            fs = [{"in": "device", "where": {"id": "apps"}, "expect": {"value": {"not_contains": [app]}}}]
        else:
            fs = [{"in": "device", "where": {"id": "apps"}, "expect": {"value": {"icontains": app}}}]
        out.append(mk(
            "assistant", split, "app", i + 1, sub="near_miss", domain="device", prompt=phrases[i % 2],
            expect=[{"tool": tool, "args": {"name": app}}], forbid=["run_shell", "run_applescript"],
            final_state=fs, unchanged=unch("assistant", "device"), menu_extra=["window_control", "open_app", "quit_app"],
            oracle=[call(tool, name=app), say("Done.")],
            trap="near-miss: window_control closes only a window; the app must really quit or open"))
    return out


def a_media(split, rng, k):
    out = []
    plan = [("spotify", "spotify", ["play something on Spotify", "start my Spotify"]),
            ("music", None, ["play some music", "put on music from the Music app"])]
    for i, (store, app, phrases) in enumerate(pick(rng, plan, k)):
        args = {"action": "play"} | ({"app": app} if app else {})
        out.append(mk(
            "assistant", split, "media", i + 1, sub="near_miss", domain="device", prompt=phrases[i % 2],
            expect=[{"tool": "mac_media", "args": {"action": "play"}}],
            final_state=[{"in": "device", "where": {"id": store}, "expect": {"value": "playing"}}],
            unchanged=unch("assistant", "device"), menu_extra=["open_app", "set_timer"],
            oracle=[call("mac_media", **args), say("Playing.")],
            trap="near-miss: open_app launches the app but does not start playback"))
    return out


def a_timer(split, rng, k):
    out = []
    for i, thing in enumerate(pick(rng, ["tea", "laundry", "bread", "eggs"], k)):
        out.append(mk(
            "assistant", split, "timer", i + 1, sub="near_miss", domain="device",
            prompt=[f"set a timer for {{n}} minutes for the {thing}", f"countdown {{n}} minutes, the {thing} needs checking"][i % 2],
            facts={"n": {"choice": [7, 15, 20, 25, 35, 40, 45]}},
            expect=[{"tool": "set_timer", "args": {"kind": "timer"}}],
            final_state=[{"in": "timers", "where": {"kind": "timer", "minutes": "{n}"}, "expect": {"count": 1, "status": "running"}}],
            unchanged=unch("assistant", "timers"), menu_extra=["add_reminder", "manage_timers"],
            oracle=[call("set_timer", kind="timer", duration="{n} minutes", label=thing), say(f"Timer set for {{n}} minutes.")],
            trap="near-miss: add_reminder is for dated commitments, not a countdown"))
    return out


def a_alarm(split, rng, k):
    return [mk(
        "assistant", split, "alarm", i + 1, sub="paraphrase", domain="device",
        prompt=["wake me up at {t} tomorrow", "i need to be up at {t} tomorrow morning, make sure i am"][i % 2],
        facts={"t": {"time": [5, 7], "step_min": 15}},
        expect=[{"tool": "set_timer", "args": {"kind": "alarm"}}],
        final_state=[{"in": "timers", "where": {"kind": "alarm"}, "expect": {"count": 1, "ends_at": "+1d {t}"}}],
        unchanged=unch("assistant", "timers"), tags=["datetime"], menu_extra=["add_reminder", "add_calendar_event"],
        oracle=[call("set_timer", kind="alarm", time="{t}"), say("Alarm set for {t} tomorrow.")],
        trap="near-miss: a reminder or a calendar event does not ring at that time") for i in range(min(k, 2))]


def a_timer_cancel(split, rng, k):
    return [mk(
        "assistant", split, "timer_cancel", 1, sub="near_miss", domain="device",
        prompt="never mind my kitchen timer, stop it",
        expect=[{"tool": "manage_timers", "args": {"action": {"any_of": ["cancel", "cancel_all"]}}}], forbid=["set_timer"],
        final_state=[{"in": "timers", "where": {"status": "running"}, "expect": {"count": 0}}],
        unchanged=unch("assistant", "timers"),
        oracle=[call("manage_timers", action="cancel_all"), say("Cancelled the timer.")],
        trap="near-miss: set_timer would start another timer instead of stopping the running one")] if k else []


# ---- contacts
def a_contact_lookup(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        out.append(mk(
            "assistant", split, "contact_lookup", i + 1, sub="paraphrase" if i % 2 else "easy", domain="contacts",
            prompt=[f"what's {first}'s number?", f"how can i call {first}?"][i % 2],
            facts={"p": {"code": "01##"}, "cid": {"id": "c"}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "phone": "+1 555 {p}", "email": make_email(first, last)}]},
            expect=[{"any_of": ["lookup_contact", "list_contacts"]}], answer={"contains_all": ["{p}"]},
            forbid=["send_message", "manage_contacts"], unchanged=unch("assistant"),
            oracle=[call("lookup_contact", name=first), say(f"{first}'s number is +1 555 {{p}}.")],
            trap="a number read must not text or edit the contact"))
    return out


def a_contact_phone(split, rng, k):
    out = []
    for i, (first, last) in enumerate(names(rng, k)):
        out.append(mk(
            "assistant", split, "contact_phone", i + 1, sub="near_miss", domain="contacts",
            prompt=[f"{first} {last} has a new number, +1 555 {{p}}, add it to their contact", f"save +1 555 {{p}} on {first}'s card"][i % 2],
            facts={"p": {"code": "01##"}, "cid": {"id": "c"}},
            seed={"contacts": [{"id": "{cid}", "name": f"{first} {last}", "email": make_email(first, last)}]},
            expect=[{"tool": "manage_contacts", "args": {"action": "add_phone"}}],
            final_state=[{"in": "contacts", "where": {"id": "{cid}"}, "expect": {"phone": {"icontains": "{p}"}}},
                         {"in": "contacts", "where": {"name": {"icontains": first}}, "expect": {"count": 1}}],
            unchanged=unch("assistant", "contacts"),
            oracle=[call("manage_contacts", action="add_phone", name=f"{first} {last}", value="+1 555 {p}"), say("Added the number.")],
            trap="near-miss: action=create would make a duplicate contact instead of editing the existing one"))
    return out


# ---- memory
def a_mem_remember(split, rng, k):
    out = []
    for i, what in enumerate(pick(rng, LOCKERS, k)):
        out.append(mk(
            "assistant", split, "mem_remember", i + 1, sub="paraphrase" if i % 2 else "near_miss", domain="memory",
            prompt=[f"remember that my {what} is {{code}}", f"keep in mind my {what} is {{code}}"][i % 2],
            facts={"code": {"code": "####"}},
            expect=[{"tool": "remember"}],
            final_state=[{"in": "memory", "where": {"text": {"icontains": "{code}"}}, "expect": {"count": 1, "status": "active"}}],
            unchanged=unch("assistant", "memory"), menu_extra=["create_note", "add_reminder"],
            oracle=[call("remember", fact=f"My {what} is {{code}}", category="fact"), say("I'll remember that.")],
            trap="near-miss: a note or a reminder is not long-term memory"))
    return out


def a_mem_recall(split, rng, k):
    out = []
    for i, what in enumerate(pick(rng, LOCKERS, k)):
        out.append(mk(
            "assistant", split, "mem_recall", i + 1, sub="near_miss", domain="memory",
            prompt=[f"what's my {what} again?", f"remind me what my {what} is"][i % 2],
            facts={"code": {"code": "####"}, "nid": {"id": "note"}},
            seed={"memory": [{"text": f"My {what} is {{code}}", "category": "fact"}],
                  "notes": [{"id": "{nid}", "title": "Gym locker", "body": "Locker rental is due in March"}]},
            expect=[{"tool": "recall"}], answer={"contains_all": ["{code}"]},
            forbid=["forget", "remember"], unchanged=unch("assistant"), menu_extra=["search_notes"],
            oracle=[call("recall", query=what), say(f"Your {what} is {{code}}.")],
            trap="near-miss: searching notes finds only an unrelated note, the fact is in memory"))
    return out


def a_mem_forget(split, rng, k):
    out = []
    for i, drink in enumerate(pick(rng, DRINKS, k)):
        out.append(mk(
            "assistant", split, "mem_forget", i + 1, sub=["easy", "paraphrase"][i % 2], domain="memory",
            prompt=[f"forget that i take my {drink} without sugar", f"delete what you know about me and {drink}"][i % 2],
            seed={"memory": [{"text": f"Takes {drink} without sugar", "category": "preference"}]},
            expect=[{"tool": "forget"}],
            final_state=[{"in": "memory", "where": {"text": {"icontains": drink}}, "expect": {"status": "forgotten"}}],
            unchanged=unch("assistant", "memory"), menu_extra=["remember", "delete_note"],
            oracle=[call("forget", query=f"{drink} without sugar"), say("I've forgotten that.")],
            trap="near-miss: remember would save a new fact; delete_note touches notes, not memory"))
    return out


# ---- web, weather, utilities (fixed in this lane)
def a_weather(split, rng, k):
    return [mk(
        "assistant", split, "weather", i + 1, sub="easy" if i == 0 else "paraphrase", domain="web",
        prompt=["what's the high in Lisbon tomorrow?", "how warm does it get in Lisbon tomorrow afternoon?"][i % 2],
        expect=[{"tool": "get_weather", "args": {"location": {"icontains": "lisbon"}}}],
        answer={"contains_any": ["78", "26"]}, forbid=["send_message", "send_email"], unchanged=unch("assistant"),
        menu_extra=["web_search", "wikipedia_summary"],
        oracle=[call("get_weather", location="Lisbon", period="tomorrow"), say("Tomorrow's high in Lisbon is 78°F (about 26°C).")],
        trap="a forecast question needs the weather tool; the figure cannot be guessed") for i in range(min(k, 2))]


def a_stock(split, rng, k):
    out = []
    for i, (name, price, alts) in enumerate(pick(rng, [("Microsoft", "448.10", ["448.1"]), ("Apple", "231.45", []),
                                                       ("Nvidia", "142.88", [])], k)):
        out.append(mk(
            "assistant", split, "stock", i + 1, sub="paraphrase", domain="web",
            prompt=[f"how's {name} trading right now?", f"what's one {name} share going for?"][i % 2],
            expect=[{"tool": "get_stock_price"}], answer={"contains_any": [price] + alts},
            unchanged=unch("assistant"), menu_extra=["web_search"],
            oracle=[call("get_stock_price", symbols=[name]), say(f"{name} is at ${price}.")],
            trap="a price question needs the quote tool, not a web search"))
    return out


def a_calc(split, rng, k):
    out = []
    for i in range(k):
        amt, pct = rng.choice([40, 60, 80, 120, 160, 240]), rng.choice([12, 15, 18, 20])
        v = round(amt * pct / 100, 2)
        shown = f"{v:.2f}"
        out.append(mk(
            "assistant", split, "calc", i + 1, sub="easy", domain="utility",
            prompt=[f"what's {pct}% of {amt}?", f"how much is a {pct} percent tip on a {amt} dollar bill?"][i % 2],
            expect=[{"tool": "calculate"}], answer={"contains_any": [shown, f"{v:g}"]}, unchanged=unch("assistant"),
            oracle=[call("calculate", expression=f"{pct}% of {amt}"), say(f"That comes to {shown}.")],
            trap="arithmetic goes through the calculator, and nothing is sent or saved"))
    return out


def a_convert(split, rng, k):
    out = []
    for i in range(k):
        miles = rng.choice([5, 8, 12, 15, 25])
        v = miles * 1.609344
        out.append(mk(
            "assistant", split, "convert", i + 1, sub="easy", domain="utility",
            prompt=[f"how many km is {miles} miles?", f"convert {miles} miles into kilometres"][i % 2],
            expect=[{"any_of": ["convert_units", "calculate"]}],
            answer={"contains_any": [f"{v:.2f}", f"{v:.1f}"]}, unchanged=unch("assistant"),
            oracle=[call("convert_units", value=miles, from_unit="miles", to_unit="km"), say(f"{miles} miles is about {v:.2f} km.")],
            trap="a conversion is a read; the digits come from the tool"))
    return out


# ============================================================================= NEUTRAL LANE
N_ERRANDS = [("call the garage", "garage"), ("pay the gas bill", "gas"), ("pick up the dry cleaning", "cleaning"),
             ("renew the parking permit", "permit"), ("book the vet", "vet"), ("return the library books", "library"),
             ("buy printer ink", "printer"), ("water the tomatoes", "tomatoes"), ("post the parcel", "parcel"),
             ("collect the prescription", "prescription"), ("defrost the freezer", "freezer"),
             ("check the smoke alarm", "smoke")]
_PHONES = iter(())


def _ph(rng: random.Random, used: set) -> str:
    while True:
        tail = f"01{rng.randint(0, 99):02d}"
        if tail not in used:
            used.add(tail)
            return f"+1 555 {tail}"


def ncontacts(rng: random.Random, k: int) -> list[dict]:
    used: set = set()
    return [{"id": "{c%d}" % i, "name": f"{f} {l}", "phone": _ph(rng, used), "email": make_email(f, l)}
            for i, (f, l) in enumerate(names(rng, k))]


def _cids(k: int) -> dict:
    return {f"c{i}": {"id": "c"} for i in range(k)}


def n_rem_complete(split, rng, k):
    out = []
    for i, ((errand, kw), (other, _)) in enumerate(zip(pick(rng, N_ERRANDS, k), pick(rng, N_ERRANDS[::-1], k))):
        if other == errand:
            other = N_ERRANDS[(N_ERRANDS.index((errand, kw)) + 1) % len(N_ERRANDS)][0]
        out.append(mk(
            "neutral", split, "rem_complete", i + 1, sub="near_miss", domain="reminders",
            prompt=[f"i finished the {kw} errand, mark it done", f"tick off '{errand}', it's finished"][i % 2],
            facts={"h": {"int": [9, 17]}, **_ids(r1="rem", r2="rem")},
            seed={"reminders": [{"id": "{r1}", "title": errand.capitalize(), "due": "+1d {h}:00", "done": False},
                                {"id": "{r2}", "title": other.capitalize(), "due": "+2d 18:00", "done": False}]},
            expect=[{"tool": "reminders_complete", "args": {"reminder_id": "{r1}"}}], forbid=["reminders_delete"],
            final_state=[{"in": "reminders", "where": {"id": "{r1}"}, "expect": {"done": True}},
                         {"in": "reminders", "where": {"id": "{r2}"}, "expect": {"done": False}}],
            unchanged=unch("neutral", "reminders"), menu_extra=["reminders_update"],
            oracle=[call("reminders_search", query=kw), call("reminders_complete", reminder_id="{r1}"),
                    say(f"Marked '{errand}' as done.")],
            trap="near-miss: reminders_delete removes the reminder instead of completing it"))
    return out


def n_rem_delete(split, rng, k):
    out = []
    for i, ((errand, kw), (other, _)) in enumerate(zip(pick(rng, N_ERRANDS, k), pick(rng, N_ERRANDS[::-1], k))):
        if other == errand:
            other = N_ERRANDS[(N_ERRANDS.index((errand, kw)) + 1) % len(N_ERRANDS)][0]
        out.append(mk(
            "neutral", split, "rem_delete", i + 1, sub="near_miss", domain="reminders",
            prompt=f"delete the {kw} reminder, i don't need it any more", facts=_ids(r1="rem", r2="rem"),
            seed={"reminders": [{"id": "{r1}", "title": errand.capitalize(), "due": "+1d 10:00", "done": False},
                                {"id": "{r2}", "title": other.capitalize(), "due": "+2d 18:00", "done": False}]},
            expect=[{"tool": "reminders_delete", "args": {"reminder_id": "{r1}"}}],
            final_state=[{"in": "reminders", "where": {"id": "{r1}"}, "expect": {"absent": True}},
                         {"in": "reminders", "where": {"id": "{r2}"}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "reminders"), menu_extra=["reminders_complete"],
            oracle=[call("reminders_search", query=kw), call("reminders_delete", reminder_id="{r1}"), say("Deleted it.")],
            trap="near-miss: reminders_complete keeps the reminder as a done item instead of deleting it"))
    return out


def n_rem_create(split, rng, k):
    out = []
    for i, (errand, kw) in enumerate(pick(rng, N_ERRANDS, k)):
        out.append(mk(
            "neutral", split, "rem_create", i + 1, sub="paraphrase", domain="reminders",
            prompt=f"don't let me forget to {errand} tomorrow at {{h}}", facts={"h": {"int": [10, 17]}},
            expect=[{"tool": "reminders_create", "args": {"title": {"icontains": kw}}}],
            final_state=[{"in": "reminders", "where": {"title": {"icontains": kw}}, "expect": {"count": 1, "due": "+1d {h}:00"}}],
            unchanged=unch("neutral", "reminders"), tags=["datetime"], menu_extra=["calendar_create_event", "timer_start"],
            oracle=[call("reminders_create", title=errand.capitalize(), due="{tomorrow_iso}T{h}:00"), say("Reminder set.")],
            trap="near-miss: a calendar event or a timer is not a reminder"))
    return out


def n_rem_update(split, rng, k):
    out = []
    for i, (errand, kw) in enumerate(pick(rng, N_ERRANDS, k)):
        out.append(mk(
            "neutral", split, "rem_update", i + 1, sub="near_miss", domain="reminders",
            prompt=f"move the {kw} reminder to {{in_3_days_weekday}} at {{h}}", facts={"h": {"int": [10, 16]}, **_ids(r1="rem")},
            seed={"reminders": [{"id": "{r1}", "title": errand.capitalize(), "due": "+1d 09:00", "done": False}]},
            expect=[{"tool": "reminders_update", "args": {"reminder_id": "{r1}"}}],
            forbid=["reminders_delete", "reminders_complete"],
            final_state=[{"in": "reminders", "where": {"id": "{r1}"}, "expect": {"done": False, "due": "+3d {h}:00"}},
                         {"in": "reminders", "where": {"title": {"icontains": kw}}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "reminders"), tags=["datetime"], menu_extra=["reminders_create"],
            oracle=[call("reminders_search", query=kw), call("reminders_update", reminder_id="{r1}", due="{in_3_days_iso}T{h}:00"),
                    say("Moved it.")],
            trap="near-miss: reminders_create would duplicate the reminder"))
    return out


def n_cal_create(split, rng, k):
    out = []
    for i, ((first, last), what) in enumerate(zip(names(rng, k), pick(rng, ["lunch", "coffee", "walk", "catch-up"], k))):
        hh, dur = rng.choice([13, 14, 15, 16]), rng.choice([30, 45, 60])
        end_h, end_m = divmod(hh * 60 + dur, 60)
        out.append(mk(
            "neutral", split, "cal_create", i + 1, sub="near_miss", domain="calendar",
            prompt=f"schedule a {dur} minute {what} with {first} on {{in_3_days_weekday}} at {hh}:00 in my calendar",
            expect=[{"tool": "calendar_create_event", "args": {"title": {"icontains": first}}}],
            final_state=[{"in": "calendar", "where": {"title": {"icontains": first}},
                          "expect": {"count": 1, "start": f"+3d {hh}:00", "end": f"+3d {end_h:02d}:{end_m:02d}"}}],
            unchanged=unch("neutral", "calendar"), tags=["datetime"], menu_extra=["reminders_create", "calendar_update_event"],
            oracle=[call("calendar_create_event", title=f"{what.capitalize()} with {first}", start=f"{{in_3_days_iso}}T{hh}:00",
                         duration_minutes=dur), say("Added it to your calendar.")],
            trap="near-miss: a reminder is not a calendar event with a length"))
    return out


def n_cal_update(split, rng, k):
    out = []
    for i, ((first, last), what) in enumerate(zip(names(rng, k), pick(rng, TASKS, k))):
        h = rng.choice([13, 14, 15, 16])
        out.append(mk(
            "neutral", split, "cal_update", i + 1, sub="near_miss", domain="calendar",
            prompt=f"move my {what} with {first} to tomorrow at {h}:00", facts=_ids(e1="evt"),
            seed={"calendar": [{"id": "{e1}", "title": f"{what.capitalize()} with {first}", "start": "+2d 10:00", "end": "+2d 11:00"}]},
            expect=[{"tool": "calendar_update_event", "args": {"event_id": "{e1}"}}], forbid=["calendar_delete_event"],
            final_state=[{"in": "calendar", "where": {"id": "{e1}"}, "expect": {"start": f"+1d {h}:00", "end": f"+1d {h + 1}:00"}},
                         {"in": "calendar", "where": {"title": {"icontains": first}}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "calendar"), tags=["datetime"], menu_extra=["calendar_create_event"],
            oracle=[call("calendar_search_events", query=first),
                    call("calendar_update_event", event_id="{e1}", start=f"{{tomorrow_iso}}T{h}:00"), say("Moved it.")],
            trap="near-miss: creating a new event duplicates it; deleting loses it"))
    return out


def n_cal_delete(split, rng, k):
    out = []
    for i, ((first, last), what) in enumerate(zip(names(rng, k), pick(rng, TASKS, k))):
        out.append(mk(
            "neutral", split, "cal_delete", i + 1, sub="near_miss", domain="calendar",
            prompt=f"cancel the {what} with {first}", facts=_ids(e1="evt", e2="evt"),
            seed={"calendar": [{"id": "{e1}", "title": f"{what.capitalize()} with {first}", "start": "+2d 10:00"},
                               {"id": "{e2}", "title": f"Dinner with {first}", "start": "+3d 19:00"}]},
            expect=[{"tool": "calendar_delete_event", "args": {"event_id": "{e1}"}}],
            final_state=[{"in": "calendar", "where": {"id": "{e1}"}, "expect": {"absent": True}},
                         {"in": "calendar", "where": {"id": "{e2}"}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "calendar"), menu_extra=["calendar_update_event"],
            oracle=[call("calendar_search_events", query=what), call("calendar_delete_event", event_id="{e1}"), say("Cancelled it.")],
            trap="near-miss: the other event with the same person must stay"))
    return out


def n_cal_list(split, rng, k):
    out = []
    for i in range(k):
        (f1, _), (f2, _), (f3, _) = names(rng, 3)
        t1, t2, t3 = rng.sample(TASKS, 3)
        out.append(mk(
            "neutral", split, "cal_list", i + 1, sub="near_miss", domain="calendar",
            prompt=["what's on my calendar tomorrow?", "what do i have planned for tomorrow?"][i % 2],
            facts={"h1": {"int": [9, 11]}, "h2": {"int": [13, 16]}, **_ids(e1="evt", e2="evt", e3="evt")},
            seed={"calendar": [{"id": "{e1}", "title": f"{t1.capitalize()} with {f1}", "start": "+1d {h1}:00"},
                               {"id": "{e2}", "title": f"{t2.capitalize()} with {f2}", "start": "+1d {h2}:00"},
                               {"id": "{e3}", "title": f"{t3.capitalize()} with {f3}", "start": "+2d 10:00"}]},
            expect=[{"any_of": ["calendar_list_events", "calendar_search_events"]}],
            answer={"contains_all": [f1, f2], "not_contains": [f3]},
            forbid=["calendar_delete_event", "calendar_update_event", "calendar_create_event"], unchanged=unch("neutral"),
            oracle=[call("calendar_list_events", start_date="{tomorrow_iso}"),
                    say(f"Tomorrow you have {t1} with {f1} at {{h1}}:00 and {t2} with {f2} at {{h2}}:00.")],
            trap="near-miss: listing a day is not a keyword search; the day-after event must not appear"))
    return out


def n_cal_free(split, rng, k):
    return [mk(
        "neutral", split, "cal_free", i + 1, sub="paraphrase", domain="calendar",
        prompt="when is the earliest i could start a one hour call tomorrow? i work 9 to 5",
        facts={"a": {"int": [10, 11]}, **_ids(e1="evt", e2="evt")},
        seed={"calendar": [{"id": "{e1}", "title": "Planning block", "start": "+1d 09:00", "end": "+1d {a}:00"},
                           {"id": "{e2}", "title": "Site visit", "start": "+1d 13:00", "end": "+1d 15:00"}]},
        expect=[{"any_of": ["calendar_find_free_time", "calendar_list_events"]}],
        answer={"contains_any": ["{a}:00", "{a} am", "{a}am", "{a} a.m."]},
        forbid=["calendar_create_event"], unchanged=unch("neutral"), tags=["datetime"],
        oracle=[call("calendar_find_free_time", date="{tomorrow_iso}", duration_minutes=60),
                say("The earliest you could start is {a}:00, after your planning block.")],
        trap="paraphrase: no keyword overlap with calendar_find_free_time; the answer depends on the seeded busy block")
        for i in range(min(k, 1))]


def n_msg_send(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "msg_send", i + 1, sub=["near_miss", "paraphrase"][i % 2], domain="messages",
            prompt=[f"let {first} know i'll be {{n}} minutes late", f"text {first} that i'm running {{n}} minutes behind"][i % 2],
            facts={"n": {"int": [5, 25]}, **_cids(2)}, seed={"contacts": c},
            expect=[{"tool": "messages_send", "args": {"text": {"icontains": "{n}"}}}],
            final_state=[{"in": "messages_sent", "expect": {"count": 1}},
                         {"in": "messages_sent", "where": {"contact_id": "{c0}"}, "expect": {"count": 1, "text": {"icontains": "{n}"}}}],
            unchanged=unch("neutral", "messages_sent"), menu_extra=["messages_draft", "messages_schedule"],
            oracle=[call("messages_send", to=c[0]["name"], text="Running {n} minutes late, sorry!"), say(f"Texted {first}.")],
            trap="near-miss: messages_draft saves a draft that nobody sends",
            extra={"intended_recipients": ["{c0}"]}))
    return out


def n_msg_draft(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "msg_draft", i + 1, sub="near_miss", domain="messages",
            prompt=f"write a text to {first} suggesting {{n}}:30 for dinner, but don't send it, i want to read it first",
            facts={"n": {"int": [6, 8]}, **_cids(2)}, seed={"contacts": c},
            expect=[{"tool": "messages_draft"}], forbid=["messages_send", "messages_schedule"],
            final_state=[{"in": "message_drafts", "where": {"contact_id": "{c0}"}, "expect": {"count": 1, "text": {"icontains": "{n}"}}}],
            unchanged=unch("neutral", "message_drafts"),
            oracle=[call("messages_draft", to=c[0]["name"], text="Dinner at {n}:30?"), say("Draft saved; nothing was sent.")],
            trap="near-miss: messages_send would send it despite 'don't send it'",
            extra={"intended_recipients": ["{c0}"]}))
    return out


def n_msg_schedule(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "msg_schedule", i + 1, sub="near_miss", domain="messages",
            prompt=f"text {first} tomorrow at {{h}}:00 in the morning to wish them luck for the interview",
            facts={"h": {"int": [10, 11]}, **_cids(2)}, seed={"contacts": c},
            expect=[{"tool": "messages_schedule"}], forbid=["messages_send"],
            final_state=[{"in": "scheduled_sends", "where": {"channel": "message", "contact_id": "{c0}"},
                          "expect": {"count": 1, "when": "+1d {h}:00", "status": "queued", "text": {"icontains": "luck"}}}],
            unchanged=unch("neutral", "scheduled_sends"), tags=["datetime"], menu_extra=["messages_draft"],
            oracle=[call("messages_schedule", to=c[0]["name"], text="Good luck for the interview!", send_at="{tomorrow_iso}T{h}:00"),
                    say("Scheduled.")],
            trap="near-miss: messages_send would text them immediately",
            extra={"intended_recipients": ["{c0}"]}))
    return out


def n_msg_cancel(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "msg_cancel", i + 1, sub="near_miss", domain="messages",
            prompt=f"cancel the text that's queued up for {first}", facts={**_cids(2), **_ids(s1="sched", s2="sched")},
            seed={"contacts": c,
                  "scheduled_sends": [{"id": "{s1}", "channel": "message", "to": c[0]["phone"], "contact_id": "{c0}",
                                       "to_name": c[0]["name"], "when": "+1d 09:00", "text": "Happy birthday!", "status": "queued"},
                                      {"id": "{s2}", "channel": "message", "to": c[1]["phone"], "contact_id": "{c1}",
                                       "to_name": c[1]["name"], "when": "+2d 09:00", "text": "See you soon", "status": "queued"}]},
            expect=[{"tool": "messages_cancel_scheduled", "args": {"schedule_id": "{s1}"}}], forbid=["messages_send"],
            final_state=[{"in": "scheduled_sends", "where": {"id": "{s1}"}, "expect": {"status": "cancelled"}},
                         {"in": "scheduled_sends", "where": {"id": "{s2}"}, "expect": {"status": "queued"}}],
            unchanged=unch("neutral", "scheduled_sends"), menu_extra=["messages_delete_draft"],
            oracle=[call("messages_list_scheduled"), call("messages_cancel_scheduled", schedule_id="{s1}"), say("Cancelled it.")],
            trap="near-miss: sending now is the opposite of cancelling; the other queued text must stay"))
    return out


def n_msg_vs_mail(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        c = ncontacts(rng, 1)
        first = c[0]["name"].split()[0]
        ask_text = i % 2 == 0
        out.append(mk(
            "neutral", split, "msg_vs_mail", i + 1, sub="paraphrase", domain="messages",
            prompt=(f"what time did {first} text me for the {thing}?" if ask_text
                    else f"what time did {first} put in their email about the {thing}?"),
            facts={"h": {"int": [9, 11]}, "h2": {"int": [1, 4]}, **_cids(1), **_ids(m1="msg", e1="mail")},
            seed={"contacts": c,
                  "messages": [{"id": "{m1}", "thread": c[0]["name"], "from": c[0]["name"],
                                "text": f"The {thing} is at {{h}}:20", "received": "-15m", "unread": True}],
                  "mail": [{"id": "{e1}", "from": f"{c[0]['name']} <{c[0]['email']}>", "subject": f"The {thing}",
                            "body": f"Per our call, the {thing} is at {{h2}}:40 pm.", "received": "-12m", "unread": True}]},
            expect=[{"any_of": (["messages_search", "messages_read_thread", "messages_list_threads"] if ask_text
                                else ["email_search", "email_read", "email_list"])}],
            answer=({"contains_all": ["{h}:20"], "not_contains": ["{h2}:40"]} if ask_text
                    else {"contains_all": ["{h2}:40"], "not_contains": ["{h}:20"]}),
            forbid=["messages_send", "email_send", "email_reply"], unchanged=unch("neutral"),
            menu_extra=(["email_search"] if ask_text else ["messages_search"]),
            oracle=([call("messages_search", query=first), say(f"By text, {first} said {{h}}:20.")] if ask_text
                    else [call("email_search", query=first), say(f"In the email, {first} said {{h2}}:40 pm.")]),
            trap="domain confusion: the same person sent a different time on the other channel"))
    return out


def n_email_send(split, rng, k):
    out = []
    for i, item in enumerate(pick(rng, ["chairs", "tents", "ladders", "lamps"], k)):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "email_send", i + 1, sub="near_miss", domain="mail",
            prompt=f"email {first} to say i'm bringing {{n}} {item} on saturday",
            facts={"n": {"int": [3, 9]}, **_cids(2)}, seed={"contacts": c},
            expect=[{"tool": "email_send", "args": {"body": {"icontains": "{n}"}}}],
            final_state=[{"in": "mail_sent", "expect": {"count": 1}},
                         {"in": "mail_sent", "where": {"kind": "send"}, "expect": {"to": {"icontains": c[0]["email"]},
                                                                                     "body": {"icontains": "{n}"}}}],
            unchanged=unch("neutral", "mail_sent"), menu_extra=["email_draft", "email_schedule"],
            oracle=[call("email_send", to=[c[0]["name"]], subject="Saturday", body=f"Hi {first}, I'm bringing {{n}} {item} on Saturday."),
                    say(f"Sent the email to {first}.")],
            trap="near-miss: email_draft only saves a draft, so nothing is delivered",
            extra={"intended_recipients": ["{c0}"]}))
    return out


def n_email_draft(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "email_draft", i + 1, sub="near_miss", domain="mail",
            prompt=f"draft an email to {first} asking if {{n}} guests are ok on friday; i'll read it before it goes",
            facts={"n": {"int": [3, 9]}, **_cids(2)}, seed={"contacts": c},
            expect=[{"tool": "email_draft"}], forbid=["email_send", "email_schedule", "email_send_draft"],
            final_state=[{"in": "mail_drafts", "expect": {"count": 1}},
                         {"in": "mail_drafts", "where": {"to": {"icontains": c[0]["email"]}}, "expect": {"body": {"icontains": "{n}"}}}],
            unchanged=unch("neutral", "mail_drafts"),
            oracle=[call("email_draft", to=[c[0]["name"]], subject="Friday", body=f"Hi {first}, are {{n}} guests ok on Friday?"),
                    say("The draft is saved for you to review; nothing was sent.")],
            trap="near-miss: email_send delivers it now",
            extra={"intended_recipients": ["{c0}"]}))
    return out


def n_email_reply(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "email_reply", i + 1, sub="near_miss", domain="mail",
            prompt=f"reply to {first}'s email about the {thing} and tell them {{in_2_days_weekday}} works",
            facts={**_cids(2), **_ids(e1="mail", e2="mail")},
            seed={"contacts": c,
                  "mail": [{"id": "{e1}", "from": f"{c[0]['name']} <{c[0]['email']}>", "subject": f"Can we fix the {thing}?",
                            "body": "Which day suits you this week?", "received": "-25m", "unread": True},
                           {"id": "{e2}", "from": f"{c[1]['name']} <{c[1]['email']}>", "subject": "Lunch plans",
                            "body": "Free on Friday?", "received": "-35m", "unread": True}]},
            expect=[{"tool": "email_reply", "args": {"message_id": "{e1}"}}], forbid=["email_forward"],
            final_state=[{"in": "mail_sent", "where": {"kind": "reply", "in_reply_to": "{e1}"},
                          "expect": {"count": 1, "body": {"icontains": "{in_2_days_weekday}"}}},
                         {"in": "mail_sent", "expect": {"count": 1}}],
            unchanged=unch("neutral", "mail_sent"), menu_extra=["email_send"],
            oracle=[call("email_search", query=first),
                    call("email_reply", message_id="{e1}", body=f"Hi {first}, {{in_2_days_weekday}} works for me."),
                    say("Replied.")],
            trap="near-miss: email_send starts a new thread and email_forward passes it on; only a reply answers the thread",
            extra={"intended_recipients": ["{c0}"]}))
    return out


def n_email_forward(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        c = ncontacts(rng, 2)
        f0, f1 = (x["name"].split()[0] for x in c)
        out.append(mk(
            "neutral", split, "email_forward", i + 1, sub="near_miss", domain="mail",
            prompt=f"forward {f0}'s email about the {thing} to {f1}", facts={**_cids(2), **_ids(e1="mail")},
            seed={"contacts": c,
                  "mail": [{"id": "{e1}", "from": f"{c[0]['name']} <{c[0]['email']}>", "subject": f"The {thing}",
                            "body": f"Details for the {thing} are below.", "received": "-30m", "unread": True}]},
            expect=[{"tool": "email_forward", "args": {"message_id": "{e1}"}}], forbid=["email_reply"],
            final_state=[{"in": "mail_sent", "where": {"kind": "forward", "in_reply_to": "{e1}"},
                          "expect": {"count": 1, "to": {"icontains": c[1]["email"]}}},
                         {"in": "mail_sent", "expect": {"count": 1}}],
            unchanged=unch("neutral", "mail_sent"), menu_extra=["email_send"],
            oracle=[call("email_search", query=f0), call("email_forward", message_id="{e1}", to=[c[1]["name"]]),
                    say(f"Forwarded it to {f1}.")],
            trap="near-miss: email_reply would answer the original sender instead",
            extra={"intended_recipients": ["{c1}"]}))
    return out


def n_email_archive(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        c = ncontacts(rng, 2)
        f0 = c[0]["name"].split()[0]
        archive = i % 2 == 0
        out.append(mk(
            "neutral", split, "email_archive" if archive else "email_trash", i + 1, sub="near_miss" if archive else "paraphrase",
            domain="mail",
            prompt=(f"i'm done with {f0}'s email about the {thing}; file it away but keep it" if archive
                    else f"bin {f0}'s email about the {thing}, it's junk"),
            facts={**_cids(2), **_ids(e1="mail", e2="mail")},
            seed={"contacts": c,
                  "mail": [{"id": "{e1}", "from": f"{c[0]['name']} <{c[0]['email']}>", "subject": f"The {thing}",
                            "body": "Just the usual.", "received": "-30m", "unread": False, "folder": "inbox"},
                           {"id": "{e2}", "from": f"{c[1]['name']} <{c[1]['email']}>", "subject": "Lunch plans",
                            "body": "Free on Friday?", "received": "-35m", "unread": False, "folder": "inbox"}]},
            expect=[{"tool": "email_archive" if archive else "email_trash", "args": {"message_id": "{e1}"}}],
            forbid=["email_trash"] if archive else [],
            final_state=[{"in": "mail", "where": {"id": "{e1}"}, "expect": {"folder": "archive" if archive else "trash"}},
                         {"in": "mail", "where": {"id": "{e2}"}, "expect": {"folder": "inbox"}}],
            unchanged=unch("neutral", "mail"), menu_extra=["email_mark_read"] + ([] if archive else ["email_archive"]),
            oracle=[call("email_search", query=f0), call("email_archive" if archive else "email_trash", message_id="{e1}"), say("Done.")],
            trap=("near-miss: email_trash discards what should be kept" if archive else
                  "near-miss: email_archive keeps junk the user wants binned")))
    return out


def n_email_buried(split, rng, k):
    out = []
    for i, (thing, topic) in enumerate(pick(rng, THINGS, k)):
        c = ncontacts(rng, 1)
        first = c[0]["name"].split()[0]
        filler = ("Thanks for booking with us. This message confirms your request and lists the terms that apply to it, "
                  "including our cancellation policy, our arrival guidance and the contact details of the desk. ")
        out.append(mk(
            "neutral", split, "email_buried", i + 1, sub="near_miss", domain="mail",
            prompt=f"what's the confirmation code in {first}'s email about the {thing}?",
            facts={"code": {"code": "@@####"}, **_cids(1), **_ids(e1="mail")},
            seed={"contacts": c,
                  "mail": [{"id": "{e1}", "from": f"{c[0]['name']} <{c[0]['email']}>", "subject": f"The {thing}",
                            "body": filler * 2 + "Your confirmation code is {code}. Please keep it safe.",
                            "received": "-30m", "unread": True}]},
            expect=[{"tool": "email_read", "args": {"message_id": "{e1}"}}],
            answer={"contains_all": ["{code}"]}, forbid=["email_reply", "email_forward", "email_send", "email_trash"],
            unchanged=unch("neutral"), tags=["buried"], menu_extra=["email_search", "email_list"],
            oracle=[call("email_search", query=first), call("email_read", message_id="{e1}"),
                    say("The confirmation code is {code}.")],
            trap="near-miss: the search snippet cuts the body off before the code, so the email must be opened"))
    return out


def n_note_append(split, rng, k):
    out = []
    for i, item in enumerate(pick(rng, ["oat milk", "candles", "lemons", "batteries"], k)):
        out.append(mk(
            "neutral", split, "note_append", i + 1, sub="near_miss", domain="notes",
            prompt=f"add {item} to my shopping list note", facts=_ids(n1="note", n2="note"),
            seed={"notes": [{"id": "{n1}", "title": "Shopping list", "body": "bread\neggs"},
                            {"id": "{n2}", "title": "Gift ideas", "body": "scarf"}]},
            expect=[{"tool": "notes_append", "args": {"note_id": "{n1}"}}], forbid=["notes_update", "notes_delete"],
            final_state=[{"in": "notes", "where": {"id": "{n1}"}, "expect": {"body": {"contains_all": ["bread", "eggs", item]}}},
                         {"in": "notes", "where": {"id": "{n2}"}, "expect": {"body": "scarf"}}],
            unchanged=unch("neutral", "notes"), menu_extra=["notes_create"],
            oracle=[call("notes_search", query="shopping"), call("notes_append", note_id="{n1}", text=item), say("Added.")],
            trap="near-miss: notes_update replaces the whole body and would wipe the existing items"))
    return out


def n_note_create(split, rng, k):
    out = []
    for i, city in enumerate(pick(rng, ["Porto", "Lisbon", "Oslo", "Kyoto"], k)):
        out.append(mk(
            "neutral", split, "note_create", i + 1, sub="near_miss", domain="notes",
            prompt=f"make a note called Packing for {city} saying to bring {{n}} adapters", facts={"n": {"int": [2, 6]}},
            expect=[{"tool": "notes_create", "args": {"title": {"icontains": city}}}],
            final_state=[{"in": "notes", "where": {"title": {"icontains": city}}, "expect": {"count": 1, "body": {"icontains": "{n}"}}}],
            unchanged=unch("neutral", "notes"), menu_extra=["memory_save", "reminders_create", "notes_append"],
            oracle=[call("notes_create", title=f"Packing for {city}", body="Bring {n} adapters."), say("Saved the note.")],
            trap="near-miss: memory_save and reminders_create store the text somewhere else"))
    return out


def n_note_read(split, rng, k):
    out = []
    for i, place in enumerate(pick(rng, ["cabin", "boathouse", "studio", "allotment"], k)):
        out.append(mk(
            "neutral", split, "note_read", i + 1, sub="easy", domain="notes",
            prompt=f"what's the wifi password for the {place} that i wrote down?",
            facts={"code": {"code": "@@##@@"}, **_ids(n1="note")},
            seed={"notes": [{"id": "{n1}", "title": f"{place.capitalize()} wifi", "body": f"Network password for the {place}: {{code}}"}]},
            expect=[{"any_of": ["notes_search", "notes_read", "notes_list"]}], answer={"contains_all": ["{code}"]},
            forbid=["notes_delete", "notes_update"], unchanged=unch("neutral"), menu_extra=["memory_search"],
            oracle=[call("notes_search", query=place), say(f"The {place} wifi password is {{code}}.")],
            trap="the answer lives in a note, not in saved memory"))
    return out


def n_note_delete(split, rng, k):
    out = []
    for i, title in enumerate(pick(rng, ["Old gift list", "Spare keys", "Garden plan", "Draft toast"], k)):
        out.append(mk(
            "neutral", split, "note_delete", i + 1, sub="near_miss", domain="notes",
            prompt=f"delete my note called {title}", facts=_ids(n1="note", n2="note"),
            seed={"notes": [{"id": "{n1}", "title": title, "body": "stuff nobody needs"},
                            {"id": "{n2}", "title": f"{title} (new)", "body": "keep this"}]},
            expect=[{"tool": "notes_delete", "args": {"note_id": "{n1}"}}],
            final_state=[{"in": "notes", "where": {"id": "{n1}"}, "expect": {"absent": True}},
                         {"in": "notes", "where": {"id": "{n2}"}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "notes"), menu_extra=["notes_update"],
            oracle=[call("notes_list"), call("notes_delete", note_id="{n1}"), say("Deleted it.")],
            trap="near-miss: the note with the similar title must survive"))
    return out


def n_file_trash(split, rng, k):
    out = []
    for i, name in enumerate(pick(rng, ["old_draft.docx", "scan_0412.pdf", "copy_of_cv.docx", "export_2019.csv"], k)):
        path, keep = f"~/Downloads/{name}", f"~/Downloads/keep_{name}"
        out.append(mk(
            "neutral", split, "file_trash", i + 1, sub="near_miss", domain="files",
            prompt=[f"get rid of {name} in my Downloads", f"delete the old file {name} from Downloads"][i % 2],
            seed={"files": [{"id": path, "path": path, "kind": "file", "text": "old"},
                            {"id": keep, "path": keep, "kind": "file", "text": "keep"}]},
            expect=[{"tool": "files_trash", "args": {"paths": {"icontains": name}}}],
            forbid=["files_delete_permanent", "files_empty_trash", "shell_exec"],
            final_state=[{"in": "files", "where": {"id": path}, "expect": {"absent": True}},
                         {"in": "trash", "where": {"path": path}, "expect": {"count": 1}},
                         {"in": "files", "where": {"id": keep}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "files", "trash"),
            oracle=[call("files_trash", paths=[path]), say("Moved it to the trash; you can restore it later.")],
            trap="near-miss: permanent deletion cannot be undone, and 'get rid of' does not ask for that"))
    return out


def n_file_delete_perm(split, rng, k):
    out = []
    for i, name in enumerate(pick(rng, ["secrets_scan.pdf", "passport_copy.jpg"], k)):
        path = f"~/Downloads/{name}"
        out.append(mk(
            "neutral", split, "file_delete_perm", i + 1, sub="near_miss", domain="files",
            prompt=f"permanently delete {name} from Downloads, skip the trash, it must be gone for good",
            seed={"files": [{"id": path, "path": path, "kind": "file", "text": "x"}],
                  "trash": [{"id": "~/Downloads/older.txt", "path": "~/Downloads/older.txt", "kind": "file"}]},
            expect=[{"tool": "files_delete_permanent", "args": {"paths": {"icontains": name}}}],
            forbid=["files_empty_trash", "shell_exec"],
            final_state=[{"in": "files", "where": {"id": path}, "expect": {"absent": True}},
                         {"in": "trash", "where": {"path": path}, "expect": {"absent": True}},
                         {"in": "trash", "where": {"path": "~/Downloads/older.txt"}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "files"),
            oracle=[call("files_delete_permanent", paths=[path]), say("Deleted it permanently.")],
            trap="near-miss: files_trash leaves a recoverable copy; files_empty_trash would also wipe the other trashed item"))
    return out


def n_file_empty_trash(split, rng, k):
    return [mk(
        "neutral", split, "file_empty_trash", i + 1, sub="paraphrase", domain="files",
        prompt="i'm low on disk space, clear out the bin for good",
        seed={"trash": [{"id": "~/Downloads/a.zip", "path": "~/Downloads/a.zip", "kind": "file"},
                        {"id": "~/Downloads/b.mov", "path": "~/Downloads/b.mov", "kind": "file"}],
              "files": [{"id": "~/Documents/keep.txt", "path": "~/Documents/keep.txt", "kind": "file", "text": "keep"}]},
        expect=[{"tool": "files_empty_trash"}], forbid=["shell_exec"],
        final_state=[{"in": "trash", "expect": {"count": 0}}, {"in": "files", "where": {"id": "~/Documents/keep.txt"}, "expect": {"count": 1}}],
        unchanged=unch("neutral", "trash"), menu_extra=["files_delete_permanent", "files_trash"],
        oracle=[call("files_empty_trash"), say("The trash is empty.")],
        trap="paraphrase: no keyword overlap with files_empty_trash; files_trash would add to the pile") for i in range(min(k, 1))]


def n_file_move(split, rng, k):
    out = []
    for i, name in enumerate(pick(rng, ["lease_scan.pdf", "receipt_july.pdf"], k)):
        src, dst = f"~/Downloads/{name}", f"~/Documents/{name}"
        out.append(mk(
            "neutral", split, "file_move", i + 1, sub="near_miss", domain="files",
            prompt=f"move {name} from Downloads into Documents",
            seed={"files": [{"id": src, "path": src, "kind": "file", "text": "scan"},
                            {"id": "~/Documents", "path": "~/Documents", "kind": "folder"}]},
            expect=[{"tool": "files_move"}], forbid=["files_delete_permanent", "shell_exec"],
            final_state=[{"in": "files", "where": {"id": src}, "expect": {"absent": True}},
                         {"in": "files", "where": {"id": dst}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "files"), menu_extra=["files_copy"],
            oracle=[call("files_move", source=src, destination="~/Documents"), say("Moved it.")],
            trap="near-miss: files_copy leaves the original behind"))
    return out


def n_file_copy(split, rng, k):
    return [mk(
        "neutral", split, "file_copy", i + 1, sub="near_miss", domain="files",
        prompt="make a backup copy of budget_2026.xlsx in Documents, keep the original where it is",
        seed={"files": [{"id": "~/Downloads/budget_2026.xlsx", "path": "~/Downloads/budget_2026.xlsx", "kind": "file", "text": "numbers"},
                        {"id": "~/Documents", "path": "~/Documents", "kind": "folder"}]},
        expect=[{"tool": "files_copy"}], forbid=["files_trash", "shell_exec"],
        final_state=[{"in": "files", "where": {"id": "~/Downloads/budget_2026.xlsx"}, "expect": {"count": 1}},
                     {"in": "files", "where": {"id": "~/Documents/budget_2026.xlsx"}, "expect": {"count": 1}}],
        unchanged=unch("neutral", "files"), menu_extra=["files_move"],
        oracle=[call("files_copy", source="~/Downloads/budget_2026.xlsx", destination="~/Documents"), say("Copied.")],
        trap="near-miss: files_move would take the original away") for i in range(min(k, 1))]


def n_file_restore(split, rng, k):
    return [mk(
        "neutral", split, "file_restore", i + 1, sub="paraphrase", domain="files",
        prompt="i threw away tax_return.pdf by mistake, get it back",
        seed={"trash": [{"id": "~/Documents/tax_return.pdf", "path": "~/Documents/tax_return.pdf", "kind": "file"},
                        {"id": "~/Documents/old.txt", "path": "~/Documents/old.txt", "kind": "file"}]},
        expect=[{"tool": "files_restore"}], forbid=["files_empty_trash", "files_delete_permanent"],
        final_state=[{"in": "files", "where": {"id": "~/Documents/tax_return.pdf"}, "expect": {"count": 1}},
                     {"in": "trash", "where": {"path": "~/Documents/old.txt"}, "expect": {"count": 1}},
                     {"in": "trash", "where": {"path": "~/Documents/tax_return.pdf"}, "expect": {"absent": True}}],
        unchanged=unch("neutral", "files", "trash"), menu_extra=["files_list_trash", "files_move"],
        oracle=[call("files_list_trash"), call("files_restore", path="~/Documents/tax_return.pdf"), say("Restored it.")],
        trap="paraphrase: 'get it back' means restore from the trash") for i in range(min(k, 1))]


def n_file_search(split, rng, k):
    out = []
    for i, name in enumerate(pick(rng, ["boarding_pass", "bike_receipt", "visa_letter", "class_syllabus"], k)):
        code = rng.randint(1000, 9999)
        path = f"~/Downloads/{name}_{code}.pdf"
        out.append(mk(
            "neutral", split, "file_search", i + 1, sub="easy" if i % 2 else "paraphrase", domain="files",
            prompt=[f"where did i save the {name.replace('_', ' ')} pdf?", f"find my {name.replace('_', ' ')} file"][i % 2],
            seed={"files": [{"id": path, "path": path, "kind": "file", "text": "x"},
                            {"id": "~/Downloads/other.pdf", "path": "~/Downloads/other.pdf", "kind": "file", "text": "y"}]},
            expect=[{"any_of": ["files_search", "files_list"]}], answer={"contains_all": [f"{name}_{code}"]},
            forbid=["shell_exec", "files_trash"], unchanged=unch("neutral"), menu_extra=["files_read"],
            oracle=[call("files_search", query=name.split("_")[0]), say(f"It is at {path}.")],
            trap="a find request needs a search; the file name carries a number only the file system knows"))
    return out


def n_file_read(split, rng, k):
    out = []
    for i, (name, what) in enumerate(pick(rng, [("gate_notes", "gate code"), ("shed_info", "padlock code")], k)):
        path = f"~/Documents/{name}.txt"
        out.append(mk(
            "neutral", split, "file_read", i + 1, sub="near_miss", domain="files",
            prompt=f"what's the {what} written in ~/Documents/{name}.txt?", facts={"code": {"code": "####"}},
            seed={"files": [{"id": path, "path": path, "kind": "file", "text": f"Reminder: the {what} is {{code}}. Do not share it."}]},
            expect=[{"tool": "files_read", "args": {"path": {"icontains": name}}}], answer={"contains_all": ["{code}"]},
            forbid=["files_write", "files_trash", "shell_exec"], unchanged=unch("neutral"), menu_extra=["files_list", "files_search"],
            oracle=[call("files_read", path=path), say(f"The {what} is {{code}}.")],
            trap="near-miss: listing or searching does not open the file"))
    return out


def n_volume(split, rng, k):
    out = []
    plan = [("device_set_volume", "volume", ["it's far too loud, drop it to {n}%", "set the sound level to {n}"], "paraphrase"),
            ("device_set_brightness", "brightness", ["dim the screen to {n} percent", "set the display brightness to {n}%"], "near_miss")]
    for i, (tool, setting, phrases, sub) in enumerate(plan[:k]):
        out.append(mk(
            "neutral", split, setting, 1, sub=sub, domain="device", prompt=phrases[0],
            facts={"n": {"choice": [15, 25, 35, 45, 85, 95]}},
            expect=[{"tool": tool, "args": {"level": "{n}"}}],
            final_state=[{"in": "device", "where": {"id": setting}, "expect": {"value": "{n}"}}],
            unchanged=unch("neutral", "device"), menu_extra=["device_set_brightness" if setting == "volume" else "device_set_volume"],
            oracle=[call(tool, level="{n}"), say("Done.")],
            trap="near-miss: volume and brightness are separate levels"))
    return out


def n_dnd(split, rng, k):
    out = []
    plan = [("device_set_do_not_disturb", "do_not_disturb", "i'm walking into a meeting, mute the notifications", ["device_set_airplane_mode", "device_set_wifi"], "paraphrase"),
            ("device_set_airplane_mode", "airplane_mode", "we're about to take off, turn off all the radios", ["device_set_wifi", "device_set_bluetooth"], "near_miss")]
    for i, (tool, setting, phrase, sibs, sub) in enumerate(plan[:k]):
        out.append(mk(
            "neutral", split, setting, 1, sub=sub, domain="device", prompt=phrase,
            expect=[{"tool": tool, "args": {"enabled": True}}],
            forbid=sibs if setting == "do_not_disturb" else [],
            final_state=[{"in": "device", "where": {"id": setting}, "expect": {"value": True}}],
            unchanged=unch("neutral", "device"), menu_extra=sibs,
            oracle=[call(tool, enabled=True), say("Done.")],
            trap="near-miss: wifi or bluetooth alone is not the mode the user means"))
    return out


def n_battery(split, rng, k):
    return [mk(
        "neutral", split, "battery", i + 1, sub="easy", domain="device", prompt="how much battery have i got left?",
        facts={"pct": {"int": [12, 79]}}, seed={"device": [{"id": "battery", "value": "{pct}"}]},
        expect=[{"any_of": ["device_get_setting", "device_get_status"]}], answer={"contains_all": ["{pct}"]},
        forbid=["device_set_airplane_mode", "device_set_wifi"], unchanged=unch("neutral"),
        oracle=[call("device_get_setting", setting="battery"), say("You have {pct}% battery left.")],
        trap="the level is per-trial, so it cannot be guessed") for i in range(min(k, 1))]


def n_thermostat(split, rng, k):
    return [mk(
        "neutral", split, "thermostat", i + 1, sub="paraphrase", domain="home", prompt="it's chilly in here, make it {n} degrees",
        facts={"n": {"int": [20, 24]}}, seed={"device": [{"id": "thermo-1", "setpoint_c": 18, "mode": "heat"},
                                                         {"id": "light-living", "room": "living", "on": True}]},
        expect=[{"tool": "home_set_thermostat", "args": {"setpoint_c": "{n}"}}],
        final_state=[{"in": "device", "where": {"id": "thermo-1"}, "expect": {"setpoint_c": "{n}"}}],
        unchanged=unch("neutral", "device"), menu_extra=["home_set_lights", "device_set_brightness"],
        oracle=[call("home_set_thermostat", setpoint_c="{n}"), say("Set the heating to {n} degrees.")],
        trap="paraphrase: no keyword overlap with home_set_thermostat") for i in range(min(k, 1))]


def n_lights(split, rng, k):
    return [mk(
        "neutral", split, "lights", i + 1, sub="near_miss", domain="home", prompt="switch off the living room lights",
        seed={"device": [{"id": "light-living", "room": "living", "on": True}, {"id": "light-bed", "room": "bedroom", "on": True}]},
        expect=[{"tool": "home_set_lights", "args": {"on": False}}],
        final_state=[{"in": "device", "where": {"id": "light-living"}, "expect": {"on": False}},
                     {"in": "device", "where": {"id": "light-bed"}, "expect": {"on": True}}],
        unchanged=unch("neutral", "device"), menu_extra=["home_set_plug", "home_set_blinds"],
        oracle=[call("home_set_lights", on=False, room="living"), say("The living room lights are off.")],
        trap="near-miss: with no room, all the lights go off, including the bedroom") for i in range(min(k, 1))]


def n_lock(split, rng, k):
    return [mk(
        "neutral", split, "lock", i + 1, sub="near_miss", domain="home", prompt="i'm heading out, lock up the front door",
        seed={"device": [{"id": "lock-front", "locked": False}]},
        expect=[{"tool": "home_lock_door"}], forbid=["home_unlock_door"],
        final_state=[{"in": "device", "where": {"id": "lock-front"}, "expect": {"locked": True}}],
        unchanged=unch("neutral", "device"), menu_extra=["home_set_lights"],
        oracle=[call("home_lock_door", device_id="lock-front"), say("The front door is locked.")],
        trap="near-miss: unlock is the opposite action") for i in range(min(k, 1))]


def n_timer(split, rng, k):
    out = []
    for i, thing in enumerate(pick(rng, ["tea", "laundry", "bread", "eggs"], k)):
        out.append(mk(
            "neutral", split, "timer", i + 1, sub="near_miss", domain="device",
            prompt=f"start a {{n}} minute timer for the {thing}", facts={"n": {"choice": [7, 15, 20, 25, 35, 40]}},
            expect=[{"tool": "timer_start", "args": {"minutes": "{n}"}}],
            final_state=[{"in": "timers", "where": {"kind": "timer", "minutes": "{n}"}, "expect": {"count": 1, "status": "running"}}],
            unchanged=unch("neutral", "timers"), menu_extra=["alarm_set", "reminders_create"],
            oracle=[call("timer_start", minutes="{n}", label=thing), say("Timer started.")],
            trap="near-miss: alarm_set rings at a clock time; a reminder is not a countdown"))
    return out


def n_alarm(split, rng, k):
    return [mk(
        "neutral", split, "alarm", i + 1, sub="paraphrase", domain="device",
        prompt="i need to be up at {t} tomorrow morning, make sure i am", facts={"t": {"time": [5, 7], "step_min": 15}},
        expect=[{"tool": "alarm_set"}],
        final_state=[{"in": "timers", "where": {"kind": "alarm"}, "expect": {"count": 1, "ends_at": "+1d {t}"}}],
        unchanged=unch("neutral", "timers"), tags=["datetime"], menu_extra=["timer_start", "reminders_create"],
        oracle=[call("alarm_set", time="{t}", label="wake up"), say("Alarm set for {t}.")],
        trap="paraphrase: a countdown timer or a reminder would not ring at that clock time") for i in range(min(k, 1))]


def n_timer_cancel(split, rng, k):
    return [mk(
        "neutral", split, "timer_cancel", i + 1, sub="near_miss", domain="device", prompt="cancel my pasta timer",
        seed={"timers": [{"id": "{t1}", "label": "pasta", "kind": "timer", "minutes": 12, "ends_at": "+12m", "status": "running"},
                         {"id": "{t2}", "label": "laundry", "kind": "timer", "minutes": 40, "ends_at": "+40m", "status": "running"}]},
        facts=_ids(t1="timer", t2="timer"),
        expect=[{"tool": "timer_cancel"}], forbid=["timer_start"],
        final_state=[{"in": "timers", "where": {"id": "{t1}"}, "expect": {"status": "cancelled"}},
                     {"in": "timers", "where": {"id": "{t2}"}, "expect": {"status": "running"}}],
        unchanged=unch("neutral", "timers"), menu_extra=["alarm_cancel"],
        oracle=[call("timer_cancel", label="pasta"), say("Cancelled the pasta timer.")],
        trap="near-miss: the other timer must keep running") for i in range(min(k, 1))]


def n_weather_now(split, rng, k):
    out = []
    for i, city in enumerate(pick(rng, ["Porto", "Lisbon", "Oslo", "Kyoto", "Bergen"], k)):
        out.append(mk(
            "neutral", split, "weather_now", i + 1, sub="near_miss", domain="web",
            prompt=f"what's it like outside in {city} right now?",
            facts={"t": {"int": [2, 20]}, "h": {"int": [26, 33]}},
            seed={"weather": [{"id": f"w-{city}-0", "location": city, "temp_c": "{t}", "high_c": "{h}", "low_c": 1, "condition": "overcast"},
                              {"id": f"w-{city}-1", "location": city, "date": "+1d", "high_c": 7, "low_c": 0, "condition": "snow"}]},
            expect=[{"tool": "weather_get_current", "args": {"location": {"icontains": city}}}],
            answer={"contains_all": ["{t}"]}, unchanged=unch("neutral"), menu_extra=["weather_get_forecast", "weather_get_hourly"],
            oracle=[call("weather_get_current", location=city), say(f"It's {{t}}°C and overcast in {city}.")],
            trap="near-miss: the forecast tools would give the high, not the current temperature"))
    return out


def n_weather_forecast(split, rng, k):
    out = []
    for i, city in enumerate(pick(rng, ["Porto", "Lisbon", "Oslo", "Kyoto", "Bergen"], k)):
        out.append(mk(
            "neutral", split, "weather_forecast", i + 1, sub="near_miss", domain="web",
            prompt=f"how warm will it get in {city} tomorrow?", facts={"t": {"int": [2, 9]}, "h": {"int": [14, 25]}},
            seed={"weather": [{"id": f"w-{city}-0", "location": city, "temp_c": "{t}", "high_c": 8, "low_c": 1, "condition": "overcast"},
                              {"id": f"w-{city}-1", "location": city, "date": "+1d", "high_c": "{h}", "low_c": 6, "condition": "sunny"}]},
            expect=[{"any_of": ["weather_get_forecast", "weather_get_hourly"]}], answer={"contains_all": ["{h}"]},
            unchanged=unch("neutral"), menu_extra=["weather_get_current"],
            oracle=[call("weather_get_forecast", location=city, days=2), say(f"Tomorrow's high in {city} is {{h}}°C.")],
            trap="near-miss: weather_get_current answers for today only"))
    return out


def n_web_search(split, rng, k):
    out = []
    for i, place in enumerate(pick(rng, ["Lantern Bistro", "Copperleaf Cafe", "Harbourside Gym", "Northgate Library"], k)):
        out.append(mk(
            "neutral", split, "web_search", i + 1, sub="paraphrase", domain="web",
            prompt=f"when does {place} open on sundays?", facts={"h": {"int": [8, 11]}},
            seed={"web": [{"id": f"https://www.example.org/{place.split()[0].lower()}", "url": f"https://www.example.org/{place.split()[0].lower()}",
                           "title": f"{place} - opening hours", "text": f"{place} opening hours. Sunday opening: {{h}}:30. Closed on bank holidays."},
                          {"id": "https://www.example.com/other", "url": "https://www.example.com/other", "title": "Unrelated page",
                           "text": "Nothing about opening hours here."}]},
            expect=[{"tool": "web_search"}], answer={"contains_all": ["{h}:30"]}, forbid=["shell_exec", "email_send", "messages_send"],
            unchanged=unch("neutral"), menu_extra=["web_fetch", "maps_search_places"],
            oracle=[call("web_search", query=f"{place} opening hours"), say(f"{place} opens at {{h}}:30 on Sundays.")],
            trap="paraphrase: no keyword overlap with web_search; the hour only exists in the seeded page"))
    return out


def n_web_fetch(split, rng, k):
    return [mk(
        "neutral", split, "web_fetch", i + 1, sub="near_miss", domain="web",
        prompt="open https://www.example.org/permits/renewal and tell me how much the renewal fee is",
        facts={"fee": {"int": [40, 95]}},
        seed={"web": [{"id": "https://www.example.org/permits/renewal", "url": "https://www.example.org/permits/renewal",
                       "title": "Permit renewal", "text": "Renewals are handled online. " * 4 + "The renewal fee is ${fee}. Payment by card only."}]},
        expect=[{"tool": "web_fetch", "args": {"url": {"icontains": "permits/renewal"}}}], answer={"contains_all": ["{fee}"]},
        forbid=["shell_exec"], unchanged=unch("neutral"), menu_extra=["web_search"],
        oracle=[call("web_fetch", url="https://www.example.org/permits/renewal"), say("The renewal fee is ${fee}.")],
        trap="near-miss: with a URL in hand the page is fetched, not searched for") for i in range(min(k, 1))]


def n_contact_search(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        c[0]["phone"] = "+1 555 {p}"
        out.append(mk(
            "neutral", split, "contact_search", i + 1, sub="paraphrase" if i % 2 else "easy", domain="contacts",
            prompt=[f"what's {first}'s number?", f"how can i call {first}?"][i % 2],
            facts={"p": {"code": "01##"}, **_cids(2)}, seed={"contacts": c},
            expect=[{"any_of": ["contacts_search", "contacts_list", "contacts_get"]}], answer={"contains_all": ["{p}"]},
            forbid=["messages_send", "contacts_update", "contacts_delete"], unchanged=unch("neutral"),
            oracle=[call("contacts_search", query=first), say(f"{first}'s number is +1 555 {{p}}.")],
            trap="a number read must not text or edit the contact"))
    return out


def n_contact_update(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "contact_update", i + 1, sub="near_miss", domain="contacts",
            prompt=f"{first} has a new number, +1 555 {{p}}; update their contact", facts={"p": {"code": "02##"}, **_cids(2)},
            seed={"contacts": c}, expect=[{"tool": "contacts_update", "args": {"contact_id": "{c0}"}}],
            forbid=["contacts_delete"],
            final_state=[{"in": "contacts", "where": {"id": "{c0}"}, "expect": {"phone": {"icontains": "{p}"}}},
                         {"in": "contacts", "where": {"name": {"icontains": first}}, "expect": {"count": 1}},
                         {"in": "contacts", "where": {"id": "{c1}"}, "expect": {"phone": c[1]["phone"]}}],
            unchanged=unch("neutral", "contacts"), menu_extra=["contacts_create"],
            oracle=[call("contacts_search", query=first), call("contacts_update", contact_id="{c0}", phone="+1 555 {p}"),
                    say("Updated the number.")],
            trap="near-miss: contacts_create would add a duplicate card; contacts_delete would lose the old one"))
    return out


def n_contact_create(split, rng, k):
    out = []
    for i in range(k):
        (first, last), = names(rng, 1)
        addr = make_email(first, last)
        out.append(mk(
            "neutral", split, "contact_create", i + 1, sub="easy", domain="contacts",
            prompt=f"add {first} {last} to my contacts, number +1 555 {{p}}, email {addr}", facts={"p": {"code": "03##"}},
            expect=[{"tool": "contacts_create", "args": {"name": {"icontains": last}}}],
            final_state=[{"in": "contacts", "where": {"name": {"icontains": last}},
                          "expect": {"count": 1, "phone": {"icontains": "{p}"}, "email": addr}}],
            unchanged=unch("neutral", "contacts"), menu_extra=["contacts_update"],
            oracle=[call("contacts_create", name=f"{first} {last}", phone="+1 555 {p}", email=addr), say("Added the contact.")],
            trap="a new person is a new card"))
    return out


def n_contact_delete(split, rng, k):
    out = []
    for i in range(k):
        c = ncontacts(rng, 2)
        first = c[0]["name"].split()[0]
        out.append(mk(
            "neutral", split, "contact_delete", i + 1, sub="near_miss", domain="contacts",
            prompt=f"remove {first} from my contacts", facts=_cids(2), seed={"contacts": c},
            expect=[{"tool": "contacts_delete", "args": {"contact_id": "{c0}"}}],
            final_state=[{"in": "contacts", "where": {"id": "{c0}"}, "expect": {"absent": True}},
                         {"in": "contacts", "where": {"id": "{c1}"}, "expect": {"count": 1}}],
            unchanged=unch("neutral", "contacts"), menu_extra=["contacts_update"],
            oracle=[call("contacts_search", query=first), call("contacts_delete", contact_id="{c0}"), say("Removed the contact.")],
            trap="near-miss: contacts_update would only edit the card"))
    return out


def n_mem_save(split, rng, k):
    out = []
    for i, what in enumerate(pick(rng, LOCKERS, k)):
        out.append(mk(
            "neutral", split, "mem_save", i + 1, sub=["easy", "paraphrase"][i % 2], domain="memory",
            prompt=[f"remember that my {what} is {{code}}", f"keep in mind my {what} is {{code}}"][i % 2],
            facts={"code": {"code": "####"}}, expect=[{"tool": "memory_save"}],
            final_state=[{"in": "memory", "where": {"text": {"icontains": "{code}"}}, "expect": {"count": 1, "status": "active"}}],
            unchanged=unch("neutral", "memory"), menu_extra=["notes_create", "reminders_create"],
            oracle=[call("memory_save", text=f"My {what} is {{code}}", category="fact"), say("I'll remember that.")],
            trap="near-miss: a note or a reminder is not long-term memory"))
    return out


def n_mem_update(split, rng, k):
    out = []
    for i, what in enumerate(pick(rng, LOCKERS, k)):
        out.append(mk(
            "neutral", split, "mem_update", i + 1, sub="near_miss", domain="memory",
            prompt=f"my {what} changed, it's now {{new}}", facts={"old": {"code": "####"}, "new": {"code": "####"}, **_ids(m1="mem")},
            seed={"memory": [{"id": "{m1}", "text": f"My {what} is {{old}}", "category": "fact", "status": "active"}]},
            expect=[{"any_of": ["memory_update", "memory_save"]}], forbid=["memory_delete"],
            final_state=[{"in": "memory", "where": {"status": "active", "text": {"icontains": what}}, "expect": {"count": 1, "text": {"icontains": "{new}"}}},
                         {"in": "memory", "where": {"id": "{m1}"}, "expect": {"status": "superseded"}}],
            unchanged=unch("neutral", "memory"), menu_extra=["memory_save", "memory_search"],
            oracle=[call("memory_search", query=what), call("memory_update", memory_id="{m1}", text=f"My {what} is {{new}}"),
                    say("Updated.")],
            trap="near-miss: saving a second fact leaves two conflicting facts; deleting loses the history"))
    return out


def n_mem_delete(split, rng, k):
    out = []
    for i, drink in enumerate(pick(rng, DRINKS, k)):
        out.append(mk(
            "neutral", split, "mem_delete", i + 1, sub="near_miss", domain="memory",
            prompt=f"forget that i take my {drink} without sugar", facts=_ids(m1="mem", m2="mem"),
            seed={"memory": [{"id": "{m1}", "text": f"Takes {drink} without sugar", "category": "preference", "status": "active"},
                             {"id": "{m2}", "text": "Prefers window seats", "category": "preference", "status": "active"}]},
            expect=[{"tool": "memory_delete", "args": {"memory_id": "{m1}"}}],
            final_state=[{"in": "memory", "where": {"id": "{m1}"}, "expect": {"status": "forgotten"}},
                         {"in": "memory", "where": {"id": "{m2}"}, "expect": {"status": "active"}}],
            unchanged=unch("neutral", "memory"), menu_extra=["memory_update", "notes_delete"],
            oracle=[call("memory_search", query=drink), call("memory_delete", memory_id="{m1}"), say("Forgotten.")],
            trap="near-miss: memory_update would rewrite the fact instead of removing it"))
    return out


def n_mem_search(split, rng, k):
    out = []
    for i, what in enumerate(pick(rng, LOCKERS, k)):
        out.append(mk(
            "neutral", split, "mem_search", i + 1, sub="near_miss", domain="memory",
            prompt=f"what's my {what} again?", facts={"code": {"code": "####"}, **_ids(m1="mem", n1="note")},
            seed={"memory": [{"id": "{m1}", "text": f"My {what} is {{code}}", "category": "fact", "status": "active"}],
                  "notes": [{"id": "{n1}", "title": "Gym locker", "body": "Locker rental is due in March"}]},
            expect=[{"any_of": ["memory_search", "memory_list"]}], answer={"contains_all": ["{code}"]},
            forbid=["memory_delete", "memory_save", "memory_update"], unchanged=unch("neutral"), menu_extra=["notes_search"],
            oracle=[call("memory_search", query=what), say(f"Your {what} is {{code}}.")],
            trap="near-miss: a notes search finds only an unrelated note, the fact is in memory"))
    return out


def n_travel(split, rng, k):
    out = []
    for i in range(k):
        a, b = rng.sample(["Porto", "Lisbon", "Braga", "Coimbra", "Faro", "Evora"], 2)
        out.append(mk(
            "neutral", split, "travel", i + 1, sub="near_miss", domain="web",
            prompt=f"how long does it take to drive from {a} to {b}?", facts={"m": {"int": [25, 58]}},
            seed={"routes": [{"origin": a, "destination": b, "mode": "driving", "distance_km": 120, "duration_min": "{m}"}]},
            expect=[{"any_of": ["maps_get_travel_time", "maps_get_directions"]}], answer={"contains_any": ["{m} min", "{m}-min", "{m} minutes"]},
            unchanged=unch("neutral"), menu_extra=["maps_search_places"],
            oracle=[call("maps_get_travel_time", origin=a, destination=b, mode="driving"),
                    say(f"It takes about {{m}} minutes to drive from {a} to {b}.")],
            trap="a travel time is read from the route tool, not from a search"))
    return out


def n_calc(split, rng, k):
    out = []
    for i in range(k):
        amt, pct = rng.choice([40, 60, 80, 120, 160, 240]), rng.choice([12, 15, 18, 20])
        shown = f"{amt * pct / 100:.2f}"
        out.append(mk(
            "neutral", split, "calc", i + 1, sub="easy", domain="utility",
            prompt=f"what's {pct}% of {amt}?", expect=[{"tool": "math_calculate"}],
            answer={"contains_any": [shown, f"{amt * pct / 100:g}"]}, unchanged=unch("neutral"), menu_extra=["units_convert"],
            oracle=[call("math_calculate", expression=f"{pct}% of {amt}"), say(f"That comes to {shown}.")],
            trap="arithmetic goes through the calculator"))
    return out


def n_units(split, rng, k):
    out = []
    for i in range(k):
        miles = rng.choice([5, 8, 12, 15, 25])
        v = miles * 1.609344
        out.append(mk(
            "neutral", split, "units", i + 1, sub="easy", domain="utility",
            prompt=f"how many km is {miles} miles?", expect=[{"any_of": ["units_convert", "math_calculate"]}],
            answer={"contains_any": [f"{v:.2f}", f"{v:.1f}"]}, unchanged=unch("neutral"), menu_extra=["math_calculate"],
            oracle=[call("units_convert", value=miles, from_unit="miles", to_unit="km"), say(f"{miles} miles is about {v:.2f} km.")],
            trap="a conversion is a read; the digits come from the tool"))
    return out


def n_stock(split, rng, k):
    out = []
    for i, sym in enumerate(pick(rng, ["ACME", "GLOBEX", "INITECH"], k)):
        out.append(mk(
            "neutral", split, "stock", i + 1, sub="paraphrase", domain="web",
            prompt=f"how is {sym} trading right now?", facts={"p": {"int": [31, 420]}},
            seed={"stocks": [{"id": sym, "price": "{p}", "change_pct": 1.2}]},
            expect=[{"tool": "stocks_get_quote", "args": {"symbol": sym}}], answer={"contains_all": ["{p}"]},
            unchanged=unch("neutral"), menu_extra=["web_search"],
            oracle=[call("stocks_get_quote", symbol=sym), say(f"{sym} is at ${{p}}.")],
            trap="a quote needs the quote tool; the price is per-trial"))
    return out


N_PLAN = [
    (n_rem_complete, 1), (n_rem_delete, 1), (n_rem_create, 1),
    (n_cal_create, 1), (n_cal_update, 1), (n_cal_list, 1), (n_cal_free, 1),
    (n_msg_send, 1), (n_msg_draft, 1), (n_msg_cancel, 1), (n_msg_vs_mail, 1),
    (n_email_send, 1), (n_email_draft, 1), (n_email_reply, 1), (n_email_archive, 2), (n_email_buried, 1),
    (n_note_append, 1), (n_note_read, 1), (n_note_delete, 1),
    (n_file_trash, 1), (n_file_delete_perm, 1), (n_file_empty_trash, 1), (n_file_move, 1), (n_file_search, 1),
    (n_volume, 1), (n_dnd, 2), (n_battery, 1),
    (n_thermostat, 1), (n_lock, 1), (n_timer, 1), (n_alarm, 1),
    (n_weather_now, 1), (n_weather_forecast, 1), (n_web_search, 1), (n_web_fetch, 1),
    (n_contact_search, 1), (n_contact_update, 1), (n_mem_save, 1), (n_mem_update, 1), (n_mem_search, 1),
    (n_travel, 1), (n_units, 1),
]


# plan: (template function, instances)
A_PLAN = [
    (a_mail_view, 3), (a_mail_overview, 1), (a_mail_archive, 2), (a_mail_markread, 1), (a_mail_reply, 2),
    (a_mail_send, 2), (a_mail_draft, 2), (a_mail_forward, 2), (a_mail_schedule, 1),
    (a_msg_send, 2), (a_msg_draft, 2), (a_msg_schedule, 1), (a_msg_view, 2), (a_msg_vs_mail, 1),
    (a_cal_add, 3), (a_cal_update, 2), (a_cal_cancel, 1), (a_cal_upcoming, 2), (a_cal_past, 1),
    (a_rem_complete, 3), (a_rem_add, 2), (a_rem_update, 2), (a_rem_clear, 1), (a_rem_search, 2),
    (a_note_create, 2), (a_note_search, 2), (a_note_delete, 1),
    (a_file_read, 2), (a_file_find, 2),
    (a_setting, 2), (a_volume, 2), (a_mute, 1), (a_app, 1), (a_media, 1), (a_timer, 1), (a_alarm, 1), (a_timer_cancel, 1),
    (a_contact_lookup, 2), (a_contact_phone, 1),
    (a_mem_remember, 2), (a_mem_recall, 2), (a_mem_forget, 1),
    (a_weather, 1), (a_stock, 1), (a_calc, 1), (a_convert, 1),
]



def _mark_core(cases: list[dict]) -> None:
    """About 40% of each (lane, sub) group's templates carry the `core` tag."""
    seen: dict[tuple[str, str], list[str]] = {}
    for c in cases:
        tmpl = c["id"].split("_", 2)[2].rsplit("_", 1)[0]
        group = seen.setdefault((c["lane"], c["sub"]), [])
        if tmpl not in group:
            group.append(tmpl)
    core: set[tuple[str, str, str]] = set()
    for (lane, sub), tmpls in seen.items():
        for i, t in enumerate(tmpls):
            if int(0.4 * (i + 1)) > int(0.4 * i):
                core.add((lane, sub, t))
    for c in cases:
        tmpl = c["id"].split("_", 2)[2].rsplit("_", 1)[0]
        if (c["lane"], c["sub"], tmpl) in core and "core" not in c["tags"]:
            c["tags"].append("core")


def generate(split: str, rng: random.Random) -> list[dict]:
    _RNG[0] = rng
    _DRAWN.clear()
    cases: list[dict] = []
    for fn, k in A_PLAN:
        cases += fn(split, rng, k)
    for fn, k in N_PLAN:
        cases += fn(split, rng, k)
    _mark_core(cases)
    return cases


