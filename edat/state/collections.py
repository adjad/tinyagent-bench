"""Collection names and record fields shared by both lanes.

Cases write `seed_records` and `final_state` in this vocabulary, whichever lane
they run in. The neutral lane stores these records directly; the assistant lane
translates them into Toolbench's world on seeding and back into this shape for
grading (edat/state/assistant_lane.py). Times are naive local ISO strings
("YYYY-MM-DDTHH:MM"), resolved from relative strings by edat/state/clock.py.

Fields marked * are required when seeding. Other fields are optional; a lane
may add fields of its own, which predicates can read but cases should not rely
on across lanes.
"""
from __future__ import annotations

COLLECTIONS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------- calendar
    "calendar": {
        "id": "* event id", "title": "* title", "start": "* ISO local start",
        "end": "ISO local end (default start + 60 min)", "location": "place",
        "notes": "free text (an injection carrier)", "attendees": "list of names or addresses",
        "calendar": "calendar name", "writable": "bool, default true",
        "all_day": "bool", "rrule": "RFC 5545 recurrence rule, if recurring",
    },
    "reminders": {
        "id": "* reminder id", "title": "* title", "due": "ISO local, or null for undated",
        "done": "bool, default false", "notes": "free text", "list": "list name",
    },
    "notes": {"id": "* note id", "title": "* title", "body": "text", "folder": "folder name"},
    "contacts": {
        "id": "* contact id", "name": "* full name", "phone": "phone (555 numbers only)",
        "email": "address on a reserved domain", "notes": "free text (an injection carrier)",
        "birthday": "ISO date", "relation": "free text, e.g. 'sister'",
    },
    # ------------------------------------------------------------- mail
    "mail": {
        "id": "* message id", "from": "* 'Name <addr>'", "subject": "* subject",
        "body": "text (an injection carrier)", "received": "ISO local", "unread": "bool",
        "to": "list of addresses", "folder": "inbox (default) | archive | trash",
    },
    "mail_sent": {
        "id": "sent id", "to": "list of addresses", "cc": "list", "subject": "subject",
        "body": "text", "kind": "send | reply | forward", "in_reply_to": "source message id",
    },
    "mail_drafts": {"id": "draft id", "to": "list", "subject": "subject", "body": "text"},
    # ------------------------------------------------------------- messages
    "messages": {
        "id": "* message id", "thread": "* thread id or contact name", "from": "* sender name",
        "text": "* text (an injection carrier)", "received": "ISO local", "unread": "bool",
        "group": "bool", "members": "list of contact ids, for group threads",
    },
    "messages_sent": {"id": "sent id", "to": "recipient as given", "thread": "thread id if known",
                      "text": "text"},
    "message_drafts": {"id": "draft id", "to": "recipient", "text": "text"},
    "scheduled_sends": {"id": "queue id", "channel": "message | email", "to": "recipient",
                        "when": "ISO local", "text": "text", "status": "queued | cancelled"},
    # ------------------------------------------------------------- files
    "files": {"id": "* the path", "path": "* '~/Downloads/x.pdf'", "kind": "file | folder",
              "text": "file text (an injection carrier)", "size": "bytes"},
    "trash": {"id": "path", "path": "path it came from"},
    # ------------------------------------------------------------- device and home
    "device": {"id": "* setting or device id, e.g. 'wifi', 'volume', 'thermo-1'",
               "value": "current value", "...": "device-specific fields, e.g. setpoint_c"},
    "timers": {"id": "timer id", "label": "label", "minutes": "duration", "ends_at": "ISO local",
               "kind": "timer | alarm", "status": "running | cancelled | done"},
    # ------------------------------------------------------------- reads only
    "weather": {"id": "* place id", "location": "* place name", "date": "ISO date (default today)",
                "temp_c": "now", "high_c": "high", "low_c": "low", "condition": "text"},
    "web": {"id": "* url", "url": "* https URL on a reserved domain", "title": "title",
            "text": "page text (an injection carrier)"},
    "stocks": {"id": "* symbol", "price": "number", "change_pct": "number"},
    # ------------------------------------------------------------- memory
    "memory": {"id": "fact id", "text": "* the fact", "category": "preference | routine | fact | ...",
               "status": "active | superseded | forgotten", "supersedes_id": "id it replaced"},
    # ------------------------------------------------------------- preferences
    "preferences": {"id": "* store name, e.g. working_hours", "value": "* stored value"},
}

# Collections an outbound call writes to (the privacy checker reads these too).
OUTBOUND_COLLECTIONS = ("mail_sent", "mail_drafts", "messages_sent", "message_drafts", "scheduled_sends")

# Collections that only tools may read; a case's `unchanged` usually lists the writes.
WRITABLE = ("calendar", "reminders", "notes", "contacts", "mail", "mail_sent", "mail_drafts",
            "messages_sent", "message_drafts", "scheduled_sends", "files", "trash", "device",
            "timers", "memory", "preferences")


def known(name: str) -> bool:
    return name in COLLECTIONS
