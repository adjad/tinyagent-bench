"""Authoring helpers for the benchmark user profiles (schema: edge-bench/09-profile-schema.md).

Everything here is synthetic. A profile module (scripts/bench_profiles/profiles/<id>.py)
defines ``build() -> ProfileBuilder``: it creates a builder, adds people, mail, messages,
events and the rest, records ledger entries, and returns it. ``build.py`` turns the
builder into ``profile.json`` and ``ledger.json``.

No I/O, no wall clock: every time is a day offset from the pinned anchor plus "HH:MM".
Dates inside free text are written as tokens (see ``tok``) so a profile can be rebased.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

SCHEMA_VERSION = 1

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class tok:
    """Date tokens for free text. Offsets are relative to the ANCHOR date, never to the
    item's own day. ``tok.dow(2)`` -> "{{dow:+2}}"; ``tok.date(-1, "short")`` -> "{{date:-1:short}}"."""

    @staticmethod
    def _o(n: int) -> str:
        return f"+{n}" if n >= 0 else str(n)

    @staticmethod
    def dow(n: int, lang: str | None = None) -> str:
        return "{{dow:%s%s}}" % (tok._o(n), f":{lang}" if lang else "")

    @staticmethod
    def date(n: int, fmt: str = "long", lang: str | None = None) -> str:
        return "{{date:%s:%s%s}}" % (tok._o(n), fmt, f":{lang}" if lang else "")


@dataclass
class ProfileBuilder:
    profile_id: str
    anchor: dict
    label: str
    person: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    accounts: list = field(default_factory=list)
    locations: list = field(default_factory=list)
    travel: list = field(default_factory=list)
    contacts: list = field(default_factory=list)
    threads: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    events: list = field(default_factory=list)
    reminders: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    browser: list = field(default_factory=list)
    memory: list = field(default_factory=list)
    ledger: list = field(default_factory=list)
    schedule_ops: list = field(default_factory=list)
    window_points: list = field(default_factory=lambda: [(0, "07:45", 3), (0, "11:30", 3), (0, "15:00", 3)])
    _seq: dict = field(default_factory=dict, repr=False)
    _ids: set = field(default_factory=set, repr=False)

    # ------------------------------------------------------------------ ids
    def _id(self, kind: str, given: str | None = None) -> str:
        if given:
            iid = given if given.startswith(kind + "-") else f"{kind}-{given}"
        else:
            n = self._seq[kind] = self._seq.get(kind, 0) + 1
            iid = f"{kind}-{n:04d}"
            while iid in self._ids:
                n = self._seq[kind] = n + 1
                iid = f"{kind}-{n:04d}"
        if iid in self._ids:
            raise ValueError(f"duplicate id {iid}")
        self._ids.add(iid)
        return iid

    # ------------------------------------------------------------ calendar maths
    @property
    def anchor_date(self) -> date:
        return date.fromisoformat(self.anchor["date"])

    def day_of(self, offset: int) -> date:
        return self.anchor_date + timedelta(days=offset)

    def weekday_offsets(self, weekdays: Iterable[str], lo: int = -28, hi: int = 12,
                        skip: Iterable[int] = ()) -> list[int]:
        """Day offsets in [lo, hi] whose weekday is in ``weekdays`` ("mon", "tue", ...)."""
        want = {_WD_ABBR[w[:3].lower()] for w in weekdays}
        skip = set(skip)
        return [d for d in range(lo, hi + 1)
                if self.day_of(d).weekday() in want and d not in skip]

    def next_weekday(self, name: str, after: int = 0) -> int:
        """Offset of the first ``name`` weekday strictly after day ``after``."""
        target = _WD_ABBR[name[:3].lower()]
        d = after + 1
        while self.day_of(d).weekday() != target:
            d += 1
        return d

    # ------------------------------------------------------------------ people
    def account(self, id: str, name: str, address: str, kind: str, is_identity: bool = True) -> str:
        iid = self._id("acct", id)
        self.accounts.append({"id": iid, "name": name, "address": address, "kind": kind,
                              "is_identity": is_identity})
        return iid

    def location(self, id: str, name: str, address: str) -> str:
        iid = self._id("loc", id)
        self.locations.append({"id": iid, "name": name, "address": address})
        return iid

    def route(self, frm: str, to: str, mode: str, minutes: int) -> None:
        self.travel.append({"from": frm, "to": to, "mode": mode, "minutes": minutes})

    def contact(self, id: str, name: str, *, full_name: str | None = None, relation: str | None = None,
                company: str | None = None, phones: list[str] | None = None,
                emails: list[str] | None = None, birthday: str | None = None,
                address: str | None = None, note: str | None = None) -> str:
        iid = self._id("ct", id)
        self.contacts.append({"id": iid, "name": name, "full_name": full_name or name,
                              "relation": relation, "company": company, "phones": phones or [],
                              "emails": emails or [], "birthday": birthday, "address": address,
                              "note": note})
        return iid

    # ---------------------------------------------------------------- messages
    def thread(self, id: str, participants: list[str], *, kind: str | None = None,
               title: str | None = None, service: str = "imessage") -> "Thread":
        iid = self._id("th", id)
        th = {"id": iid, "kind": kind or ("group" if len(participants) > 1 else "dm"),
              "title": title, "participants": participants, "service": service, "messages": []}
        self.threads.append(th)
        return Thread(self, th)

    # ------------------------------------------------------------------- email
    def email(self, *, account: str, day: int, time: str, frm: tuple[str, str] | str,
              subject: str, body: str, to: list[str] | None = None, cc: list[str] | None = None,
              id: str | None = None, thread_id: str | None = None, in_reply_to: str | None = None,
              mailbox: str = "inbox", category: str = "people", bulk: bool = False,
              unread: bool = False, flagged: bool = False, attachments: list[dict] | None = None,
              lang: str | None = None) -> str:
        iid = self._id("em", id)
        if isinstance(frm, str):
            name, addr = self._me_from(account) if frm == "me" else (frm, frm)
        else:
            name, addr = frm
        acct = next(a for a in self.accounts if a["id"] == account)
        row = {"id": iid, "thread_id": thread_id or f"emt-{iid[3:]}",
               "message_id": f"<{iid}@{self.profile_id}.bench.example>",
               "in_reply_to": in_reply_to, "account": account, "mailbox": mailbox,
               "category": category, "bulk": bulk, "day": day, "time": time,
               "from": {"name": name, "addr": addr},
               "to": to if to is not None else [acct["address"]], "cc": cc or [],
               "subject": subject, "body": body, "unread": unread, "flagged": flagged,
               "attachments": attachments or []}
        if lang:
            row["lang"] = lang
        self.emails.append(row)
        return iid

    def _me_from(self, account: str) -> tuple[str, str]:
        acct = next(a for a in self.accounts if a["id"] == account)
        return self.person.get("name", "Me"), acct["address"]

    # ---------------------------------------------------------------- calendar
    def event(self, *, title: str, day: int, time: str | None = None, end: str | None = None,
              calendar: str, account: str | None = None, kind: str = "event",
              all_day: bool = False, days: int = 1, location: str | None = None,
              organizer: str | None = None, organizer_is_user: bool | None = True,
              invitee_count: int | None = 0, calendar_writable: bool = True,
              context: str | None = None, notes: str | None = None, status: str = "confirmed",
              id: str | None = None, series_id: str | None = None, end_day: int | None = None) -> str:
        iid = self._id("ev", id)
        row = {"id": iid, "series_id": series_id, "calendar": calendar, "account": account,
               "kind": kind, "title": title, "day": day,
               "time": None if all_day else time, "end": None if all_day else end,
               "all_day": all_day, "days": days, "location": location, "organizer": organizer,
               "organizer_is_user": organizer_is_user, "invitee_count": invitee_count,
               "calendar_writable": calendar_writable, "context": context, "notes": notes,
               "status": status}
        if end_day is not None:
            row["end_day"] = end_day
        self.events.append(row)
        return iid

    def series(self, sid: str, offsets: Iterable[int], **kw) -> list[str]:
        """Expand a recurring series. Each occurrence id is ``ev-<sid>-d<offset>``
        (``m`` for negative: ``ev-stats-lec-dm7``). Returns the ids in order."""
        ids = []
        for d in offsets:
            tag = f"d{d}" if d >= 0 else f"dm{-d}"
            ids.append(self.event(day=d, id=f"{sid}-{tag}", series_id=f"ser-{sid}", **kw))
        return ids

    # --------------------------------------------------------------- the rest
    def reminder(self, title: str, *, day: int | None, time: str | None = None,
                 created_day: int, list: str = "Reminders", completed: bool = False,
                 completed_day: int | None = None, completed_time: str | None = None,
                 all_day: bool = False, notes: str | None = None, id: str | None = None) -> str:
        iid = self._id("rem", id)
        self.reminders.append({"id": iid, "title": title, "list": list, "day": day,
                               "time": time if day is not None and not all_day else None,
                               "all_day": all_day, "completed": completed,
                               "completed_day": completed_day, "completed_time": completed_time,
                               "created_day": created_day, "notes": notes})
        return iid

    def note(self, title: str, body: str, *, day: int, time: str, folder: str = "Notes",
             created_day: int | None = None, id: str | None = None) -> str:
        iid = self._id("note", id)
        self.notes.append({"id": iid, "title": title, "folder": folder, "body": body,
                           "day": day, "time": time,
                           "created_day": created_day if created_day is not None else day})
        return iid

    def document(self, path: str, title: str, *, kind: str, day: int, time: str,
                 content: str = "", rows: list[list] | None = None, id: str | None = None) -> str:
        iid = self._id("doc", id)
        if rows is not None and not content:
            content = "\n".join(",".join(_csv_cell(c) for c in r) for r in rows)
        self.documents.append({"id": iid, "path": path, "title": title, "kind": kind,
                               "day": day, "time": time, "content": content, "rows": rows})
        return iid

    def web(self, host: str, path: str, title: str, *, day: int, time: str,
            browser: str = "safari", id: str | None = None) -> str:
        iid = self._id("web", id)
        self.browser.append({"id": iid, "browser": browser, "day": day, "time": time,
                             "host": host, "path": path, "title": title})
        return iid

    def remember(self, text: str, *, category: str, day: int, origin: str = "explicit",
                 superseded_by: str | None = None, id: str | None = None) -> str:
        iid = self._id("mem", id)
        self.memory.append({"id": iid, "text": text, "category": category, "origin": origin,
                            "day": day, "superseded_by": superseded_by})
        return iid

    def fact(self, type: str, truth: str, *, evidence: list[str], surfaces: list[str],
             why: str, prompt: str | None = None, expected: dict | None = None,
             distractors: list[str] | None = None, difficulty: str = "medium") -> str:
        n = len(self.ledger) + 1
        lid = f"L-{self.profile_id}-{n:02d}"
        self.ledger.append({"id": lid, "type": type, "surfaces": surfaces, "prompt": prompt,
                            "truth": truth, "expected": expected or {}, "evidence": evidence,
                            "distractors": distractors or [], "why": why,
                            "difficulty": difficulty})
        return lid

    def schedule(self, op: str, *, because: list[str], ref: str | None = None,
                 title: str | None = None, day: int | None = None, time: str | None = None,
                 end: str | None = None, location: str | None = None, kind: str = "event",
                 reason: str | None = None, id: str | None = None) -> str:
        """Record how the TRUE schedule differs from the calendar (see windows.py).

        op = "move" | "relocate" | "cancel" (ref = the calendar event), "add" (an event that
        exists only in mail or messages: title/day/time/end/location), or "exclude" (ref is on
        the calendar but is not the person's own plan, e.g. a partner's shift calendar).
        ``because`` = the items that prove it. Returns the op id (``op-<slug>``)."""
        if op not in ("move", "relocate", "cancel", "add", "exclude"):
            raise ValueError(op)
        n = len(self.schedule_ops) + 1
        oid = id if id and id.startswith("op-") else f"op-{id or n:0>2}"
        self.schedule_ops.append({"id": oid, "op": op, "ref": ref, "title": title, "day": day,
                                  "time": time, "end": end, "location": location, "kind": kind,
                                  "because": because, "reason": reason})
        return oid

    # ----------------------------------------------------------------- output
    def profile_dict(self) -> dict[str, Any]:
        def _sorted(rows, key=lambda r: (r["day"] if r.get("day") is not None else 10**6,
                                         r.get("time") or "")):
            return sorted(rows, key=key)
        threads = []
        for th in self.threads:
            threads.append({**th, "messages": sorted(th["messages"],
                                                     key=lambda m: (m["day"], m["time"]))})
        return {
            "schema_version": SCHEMA_VERSION, "profile_id": self.profile_id, "synthetic": True,
            "label": self.label, "anchor": self.anchor, "person": self.person,
            "settings": self.settings, "accounts": self.accounts, "locations": self.locations,
            "travel": self.travel, "contacts": self.contacts, "threads": threads,
            "emails": _sorted(self.emails), "events": _sorted(self.events),
            "reminders": _sorted(self.reminders), "notes": _sorted(self.notes),
            "documents": _sorted(self.documents), "browser": _sorted(self.browser),
            "memory": _sorted(self.memory),
        }

    def ledger_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "profile_id": self.profile_id,
                "synthetic": True, "schedule_ops": self.schedule_ops, "entries": self.ledger}


class Thread:
    """Handle returned by ``ProfileBuilder.thread``; ``t.msg(day, time, frm, text)``."""

    def __init__(self, b: ProfileBuilder, row: dict) -> None:
        self.b, self.row = b, row
        self.slug = row["id"][3:]
        self.id = row["id"]

    def msg(self, day: int, time: str, frm: str, text: str, *, unread: bool = False,
            lang: str | None = None, id: str | None = None) -> str:
        if id:
            mid = self.b._id("msg", id)
        else:
            n = len(self.row["messages"]) + 1
            mid = self.b._id("msg", f"{self.slug}-{n:04d}")
        m = {"id": mid, "day": day, "time": time, "from": frm, "text": text, "unread": unread}
        if lang:
            m["lang"] = lang
        self.row["messages"].append(m)
        return mid

    def script(self, day: int, lines: list[tuple], lang: str | None = None) -> list[str]:
        """Many messages on one day: lines are (time, frm, text) or (time, frm, text, opts)."""
        out = []
        for ln in lines:
            opts = ln[3] if len(ln) > 3 else {}
            out.append(self.msg(day, ln[0], ln[1], ln[2], lang=opts.get("lang", lang),
                                unread=opts.get("unread", False), id=opts.get("id")))
        return out


def _csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return f'"{s}"' if any(c in s for c in ',"\n') else s


def hhmm(h: int, m: int = 0) -> str:
    return f"{h:02d}:{m:02d}"


def jitter_time(rng: random.Random, lo: str, hi: str) -> str:
    """A random HH:MM between lo and hi (inclusive), for noise."""
    a = int(lo[:2]) * 60 + int(lo[3:])
    b = int(hi[:2]) * 60 + int(hi[3:])
    m = rng.randint(a, b)
    return f"{m // 60:02d}:{m % 60:02d}"


TOKEN_RE = re.compile(r"\{\{(dow|date):([+-]\d+)(?::([a-z]+))?(?::([a-z]+))?\}\}")
