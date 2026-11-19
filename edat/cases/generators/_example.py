"""A worked example generator: the pattern every family generator follows.

It is skipped by `edat.cases.build` (the leading underscore); tests run it to
prove the pipeline end to end on both real lanes.
"""
from __future__ import annotations

import random

from edat.cases.generators import case_id
from edat.cases.generators.pools import ERRANDS, contact

FAMILY = "select"


def _assistant_complete(split: str, rng: random.Random, n: int) -> dict:
    # Near-miss: complete_reminder vs clear_reminders. The seeded reminder title is drawn per case;
    # its due hour changes per trial through `facts`.
    errand, other = rng.sample(ERRANDS, 2)
    return {
        "id": case_id("sel", "assistant", split, "complete", n), "lane": "assistant", "family": "select",
        "sub": "near_miss", "prompt": f"done with the {errand} one, tick it off",
        "facts": {"due_h": {"int": [9, 17]}},
        "world": {"seed_records": {"reminders": [
            {"id": "r-901", "title": errand.capitalize(), "due": "+1d {due_h}:00", "done": False},
            {"id": "r-902", "title": other.capitalize(), "due": "+2d 18:00", "done": False}]}},
        "expect": [{"tool": "complete_reminder", "args": {"title": {"icontains": errand.split()[-1]}}}],
        "forbid": ["clear_reminders", "clear_past_reminders"],
        "final_state": [{"in": "reminders", "where": {"id": "r-901"}, "expect": {"done": True}},
                        {"in": "reminders", "where": {"id": "r-902"}, "expect": {"done": False}}],
        "unchanged": ["calendar", "messages_sent", "mail_sent"],
        "optimal": {"calls": 1, "steps": 2},
        "oracle": [{"calls": [{"name": "complete_reminder", "arguments": {"title": errand.capitalize()}}]},
                   {"text": f"Done, I ticked off '{errand}'."}],
        "trap": "near-miss: clear_reminders removes reminders instead of completing one",
    }


def _neutral_message(split: str, rng: random.Random, n: int) -> dict:
    used_n, used_p = set(), set()
    c = contact(rng, "c-11", used_n, used_p)
    first = c["name"].split()[0]
    return {
        "id": case_id("sel", "neutral", split, "msg", n), "lane": "neutral", "family": "select",
        "sub": "near_miss", "prompt": f"Text {first} that I'm {{mins}} minutes late.",
        "facts": {"mins": {"choice": [5, 10, 15, 20]}},
        "world": {"seed_records": {"contacts": [c]}},
        "expect": [{"tool": "messages_send", "args": {"to": {"contains_any": [first, c["phone"][-4:]]},
                                                      "text": {"icontains": "{mins}"}}}],
        "forbid": ["shell_exec"],
        "final_state": [{"in": "messages_sent", "where": {"contact_id": "c-11"},
                         "expect": {"count": 1, "text": {"icontains": "{mins}"}}}],
        "unchanged": ["message_drafts", "mail_sent"],
        "intended_recipients": ["c-11"],
        "optimal": {"calls": 1, "steps": 2},
        "oracle": [{"calls": [{"name": "messages_send", "arguments": {"to": c["name"], "text": "Running {mins} minutes late"}}]},
                   {"text": f"Sent to {first}."}],
    }


def generate(split: str, rng: random.Random) -> list[dict]:
    return [_assistant_complete(split, rng, 1), _neutral_message(split, rng, 1)]
