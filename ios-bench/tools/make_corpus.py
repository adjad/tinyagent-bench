#!/usr/bin/env python3
"""Write the stand-in prompt corpus the iPhone runner sizes its context levels from.

Spec 04 §3.1 asks for agent-shaped text (system prompt, tool schemas, tool results,
dialogue) instead of repeated filler, because filler flatters speculative decoding and
hides tokenizer differences. The real corpus comes with the agentic spec (02); until it
exists this script generates a deterministic stand-in of the same shape: invented
people (persona Test User), .example/.invalid domains, invented numbers. Re-running
gives identical bytes. About 320 KB, enough for 64k tokens without repeating.

    python3 ios-bench/tools/make_corpus.py            # writes ios-bench/Resources/corpus-agent-v0.txt
"""
from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "Resources" / "corpus-agent-v0.txt"
TARGET_CHARS = 320_000

FIRST = ["Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Harper", "Jordan", "Kai", "Logan",
         "Morgan", "Noel", "Parker", "Quinn", "Reese", "Rowan", "Sage", "Skyler", "Tatum", "Wren"]
LAST = ["Abara", "Brightwater", "Castellan", "Dunmore", "Elsworth", "Fairbank", "Galloway", "Hollins",
        "Ivers", "Jessup", "Kettering", "Lindqvist", "Marlowe", "Northcott", "Okafor", "Pemberton"]
ORGS = ["Lantern Labs", "Northwind Clinic", "Copperleaf Books", "Harbor Street Dental", "Juniper Freight",
        "Bluegate Studio", "Maple Row School", "Orbit Cycles", "Sable Insurance", "Tidewater Bank"]
PLACES = ["Riverside Library", "the Elm Street office", "Pier 4 Cafe", "the community hall", "Room 2B",
          "the north parking lot", "Platform 3", "the east wing", "Studio C", "the Market Square kiosk"]
TOPICS = ["quarterly budget review", "kitchen renovation quote", "school science fair", "dentist follow-up",
          "bike repair estimate", "insurance renewal", "team offsite agenda", "library volunteer shift",
          "freight delivery window", "photo book order", "tax document checklist", "garden club meeting"]
TOOLS = [
    ("search_calendar", "Find events in a date range.", {"start": "string", "end": "string", "query": "string"}),
    ("add_reminder", "Create a reminder.", {"title": "string", "when_iso": "string"}),
    ("view_emails", "Search the inbox.", {"query": "string", "limit": "integer"}),
    ("draft_email", "Draft an email for review.", {"to": "string", "subject": "string", "body": "string"}),
    ("get_weather", "Forecast for a place.", {"location": "string", "days": "integer"}),
    ("read_file", "Read a text file.", {"path": "string"}),
    ("web_search", "Search the web.", {"query": "string"}),
    ("create_note", "Create a note.", {"title": "string", "body": "string"}),
]


def person(r: random.Random) -> str:
    return f"{r.choice(FIRST)} {r.choice(LAST)}"


def email(name: str, r: random.Random) -> str:
    return f"{name.split()[0].lower()}.{name.split()[1].lower()}@{r.choice(['mail.example', 'corp.example', 'school.invalid'])}"


def when(r: random.Random) -> str:
    return f"2026-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}T{r.randint(7, 19):02d}:{r.choice(['00', '15', '30', '45'])}"


def schema_block(r: random.Random) -> str:
    picks = r.sample(TOOLS, 3)
    out = []
    for name, desc, params in picks:
        out.append(json.dumps({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": {k: {"type": v} for k, v in params.items()},
                           "required": list(params)[:1]}}}, sort_keys=True))
    return "Available tools:\n" + "\n".join(out)


def tool_result(r: random.Random) -> str:
    kind = r.randrange(4)
    if kind == 0:
        events = [{"title": r.choice(TOPICS).title(), "start": when(r), "location": r.choice(PLACES),
                   "organizer": email(person(r), r)} for _ in range(r.randint(2, 4))]
        return "Tool result (search_calendar): " + json.dumps({"events": events}, sort_keys=True)
    if kind == 1:
        mails = [{"from": email(person(r), r), "subject": f"Re: {r.choice(TOPICS)}",
                  "snippet": f"Following up on the {r.choice(TOPICS)}; the total came to {r.randint(40, 2400)}.{r.randint(10, 99)} and "
                             f"we can meet at {r.choice(PLACES)}."} for _ in range(r.randint(2, 3))]
        return "Tool result (view_emails): " + json.dumps({"messages": mails}, sort_keys=True)
    if kind == 2:
        return "Tool result (get_weather): " + json.dumps({"location": r.choice(PLACES), "days": [
            {"date": when(r)[:10], "high_c": r.randint(4, 31), "low_c": r.randint(-3, 18), "rain_pct": r.randint(0, 90)}
            for _ in range(3)]}, sort_keys=True)
    return "Tool result (read_file): " + json.dumps({"path": f"Documents/{r.choice(TOPICS).replace(' ', '-')}.txt",
                                                     "text": f"Notes for the {r.choice(TOPICS)}. Owner: {person(r)}. "
                                                             f"Next step due {when(r)[:10]}; budget {r.randint(100, 9000)}."}, sort_keys=True)


def dialogue(r: random.Random) -> str:
    a, b, topic = person(r), person(r), r.choice(TOPICS)
    lines = [
        f"User (Test User): Can you check what I have about the {topic} and tell {a.split()[0]} when I am free?",
        f"Assistant: I looked at your calendar and found {r.randint(1, 4)} related events. The earliest open slot is {when(r)}.",
        f"User (Test User): Draft a short note to {a} ({email(a, r)}) and copy {b}.",
        f"Assistant: Here is a draft. Subject: {topic.title()}. Hi {a.split()[0]}, I can meet at {r.choice(PLACES)} on "
        f"{when(r)[:10]}. {b.split()[0]} will join if the {r.choice(TOPICS)} wraps up in time. Should I send it?",
    ]
    return "\n".join(lines)


def email_body(r: random.Random) -> str:
    sender, org, topic = person(r), r.choice(ORGS), r.choice(TOPICS)
    return (f"From: {sender} <{email(sender, r)}>\nTo: Test User <test.user@example.com>\nSubject: {topic.title()} at {org}\n\n"
            f"Hello Test User,\n\nThis is a reminder about the {topic}. We moved it to {when(r)} at {r.choice(PLACES)}. "
            f"Please bring the reference number {r.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{r.randint(1000, 9999)} and arrive ten minutes early. "
            f"If the time does not work, reply with two alternatives and {r.choice(FIRST)} from {org} will confirm by "
            f"{when(r)[:10]}. The fee stays at {r.randint(15, 480)}.{r.randint(10, 99)}.\n\nBest regards,\n{sender}\n{org}")


def main() -> None:
    r = random.Random(20261118)
    parts = ["System: You are an on-device assistant for Test User. Use tools when they help, ask before sending anything, "
             "and keep answers short."]
    size = len(parts[0])
    makers = [schema_block, tool_result, dialogue, email_body, tool_result, dialogue]
    i = 0
    while size < TARGET_CHARS:
        block = makers[i % len(makers)](r)
        parts.append(block)
        size += len(block) + 2
        i += 1
    OUT.write_text("\n\n".join(parts) + "\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {i} blocks)")


if __name__ == "__main__":
    main()
