"""A benchmark user: their data, their settings and the ground-truth ledger.

This is the product benchmark's own profile shape ("productbench/1"). The
shared profile schema written by the profiles session
(`docs/benchmark/09-profile-schema.md`) is adapted into it by
`adapt_shared()` once that schema exists; until then the stand-in profile in
test_fixtures/productbench/ is written directly in this shape.

Times are local ISO strings ("2026-03-10T09:30") in the profile's IANA
timezone. The anchor is the moment the run calls "now".

Ledger items carry what the scorers need:

  id, type        event | deadline | task | owed | waiting_on_me | change | fact | noise
  title           what the item is, in plain words
  anchors         strings whose presence shows the item was surfaced; any one counts.
                  "@15:30" is a clock time, matched against every time in the text
                  (3:30 pm, 15:30, 3.30pm); "#2026-03-12" is a date
  stale_anchors   strings that show a superseded version was presented as current
  current_anchors the current value's anchors, checked before a stale anchor counts
                  (default: anchors); "moved from Friday to Thursday" is not stale
  sources         ids of the emails / threads / notes / events the item comes from
  hidden          true when it exists only inside mail or message text (no reminder,
                  no event): the "hidden task" Brief must find
  attention       true when the Brief should surface it
  start, end      for timed items (events, changed events)
  due             for deadlines and tasks
  duration_min    how long the work takes, for Day coverage
  fixed           the time cannot move (Day must keep it)
  personal        a personal obligation (pick up a child, a doctor's visit)
  travel_min      minutes needed to get there, which Day must keep free before start
  priority        1 (highest) to 3, for Brief ordering
  supersedes      the ledger id this item replaces (a change)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA = "productbench/1"
LEDGER_TYPES = {"event", "deadline", "task", "owed", "waiting_on_me", "change", "fact", "noise"}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    data: dict[str, Any]
    path: Path | None = None
    tz: ZoneInfo = field(init=False)
    generated_cases: dict | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        validate(self.data)
        self.tz = ZoneInfo(self.data["timezone"])

    # --- identity -----------------------------------------------------
    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def user(self) -> dict:
        return self.data["user"]

    @property
    def ledger(self) -> list[dict]:
        return self.data.get("ledger", [])

    def item(self, ledger_id: str) -> dict:
        for it in self.ledger:
            if it["id"] == ledger_id:
                return it
        raise KeyError(ledger_id)

    # --- time -----------------------------------------------------------
    def parse(self, value: str) -> datetime:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=self.tz)

    def epoch(self, value: str) -> float:
        return self.parse(value).timestamp()

    @property
    def anchor(self) -> datetime:
        return self.parse(self.data["anchor"])

    @property
    def anchor_epoch(self) -> float:
        return self.anchor.timestamp()

    @property
    def today(self) -> str:
        return self.anchor.date().isoformat()

    def on_day(self, value: str | None, day: str | None = None) -> bool:
        return bool(value) and self.parse(value).date().isoformat() == (day or self.today)

    def minutes(self, value: str) -> int:
        dt = self.parse(value)
        return dt.hour * 60 + dt.minute

    def working_window(self) -> tuple[int, int]:
        hours = self.data.get("hours", {}).get("working", {})
        return (_hm(hours.get("start", "09:00")), _hm(hours.get("end", "17:00")))

    def relative_day(self, offset_days: int) -> str:
        return (self.anchor + timedelta(days=offset_days)).date().isoformat()


def _hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _raw_handle(value: str) -> bool:
    """An unsaved sender: a phone number or an address, shown as itself."""
    return bool(value) and (value.startswith("+") or "@" in value or value[:1].isdigit())


def validate(data: dict) -> None:
    def need(cond: bool, msg: str) -> None:
        if not cond:
            raise ProfileError(msg)

    need(isinstance(data, dict), "profile must be an object")
    need(data.get("schema") == SCHEMA, f"profile schema must be {SCHEMA!r}")
    for key in ("id", "timezone", "anchor", "user"):
        need(key in data, f"profile needs {key!r}")
    ZoneInfo(data["timezone"])
    datetime.fromisoformat(data["anchor"])
    need(bool(data["user"].get("name")), "user.name is required")
    ids: set[str] = set()
    for coll in ("contacts", "calendar", "reminders", "emails", "threads", "notes"):
        for row in data.get(coll, []):
            need("id" in row, f"{coll} row without id")
            need(row["id"] not in ids, f"duplicate id {row['id']!r}")
            ids.add(row["id"])
    contact_ids = {c["id"] for c in data.get("contacts", [])}
    for t in data.get("threads", []):
        for cid in t.get("with", []):
            need(cid in contact_ids, f"thread {t['id']} names unknown contact {cid}")
        for m in t.get("messages", []):
            sender = str(m.get("from") or "")
            need(sender == "me" or sender in contact_ids or _raw_handle(sender),
                 f"thread {t['id']} message from unknown sender {sender!r}")
    for ev in data.get("calendar", []):
        need("start" in ev and "title" in ev, f"event {ev['id']} needs start and title")
    lids: set[str] = set()
    for it in data.get("ledger", []):
        need(it.get("type") in LEDGER_TYPES, f"ledger {it.get('id')} has bad type {it.get('type')!r}")
        need(it["id"] not in lids, f"duplicate ledger id {it['id']}")
        lids.add(it["id"])
        for src in it.get("sources", []):
            need(src in ids, f"ledger {it['id']} names unknown source {src}")
    for it in data.get("ledger", []):
        if it.get("supersedes"):
            need(it["supersedes"] in lids, f"ledger {it['id']} supersedes unknown {it['supersedes']}")


def load(path: str | Path) -> Profile:
    """A productbench/1 profile, or a shared tinyagent-bench profile (with its ledger.json beside it)."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") == SCHEMA:
        return Profile(data, path)
    if data.get("schema_version") == 1 and "profile_id" in data:
        from scripts.productbench.shared import load_pair
        adapted, cases = load_pair(path)
        prof = Profile(adapted, path)
        prof.generated_cases = cases
        return prof
    raise ProfileError(f"{path}: neither productbench/1 nor the shared tinyagent-bench schema v1")
