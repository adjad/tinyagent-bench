"""The pinned board clock and relative-time strings used in cases.

Cases never write absolute dates. They write times relative to the board
clock, so one case works under any pinned clock and per-case clock override:

    "+1d 15:00"   tomorrow at 15:00 (local)          -> "2026-11-19T15:00"
    "-2d 08:15"   two days ago at 08:15               -> "2026-11-16T08:15"
    "+0d"         today, date only                    -> "2026-11-18"
    "+3h"         three hours from now                -> "2026-11-18T11:30"
    "-45m"        45 minutes ago                      -> "2026-11-18T07:45"

Resolved values are naive local ISO strings ("YYYY-MM-DDTHH:MM" or
"YYYY-MM-DD"); the store records the zone once. A relative string may also sit
inside a longer string ("due {rel:+1d 08:00}") through `resolve_text`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Board version 1's pinned clock. A board version fixes this value; a case may
# override it with world.clock / world.tz (spec 02 §3.17).
DEFAULT_CLOCK = "2026-11-18T08:30"
DEFAULT_TZ = "America/Los_Angeles"

_REL_DAY = re.compile(r"^\s*([+-])(\d+)d(?:\s+(\d{1,2}):(\d{2}))?\s*$")
_REL_SPAN = re.compile(r"^\s*([+-])(\d+)(h|m)\s*$")
_INLINE = re.compile(r"\{rel:([^}]+)\}")


def is_relative(value) -> bool:
    return isinstance(value, str) and bool(_REL_DAY.match(value) or _REL_SPAN.match(value))


@dataclass(frozen=True)
class Clock:
    """A pinned local wall-clock time in a named zone."""

    now: datetime          # naive local time
    tz: str = DEFAULT_TZ

    @classmethod
    def parse(cls, iso: str | None = None, tz: str | None = None) -> "Clock":
        raw = iso or DEFAULT_CLOCK
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            zone = ZoneInfo(tz or DEFAULT_TZ)
            dt = dt.astimezone(zone).replace(tzinfo=None)
        ZoneInfo(tz or DEFAULT_TZ)   # fail early on a bad zone name
        return cls(now=dt.replace(second=0, microsecond=0), tz=tz or DEFAULT_TZ)

    @property
    def today(self) -> date:
        return self.now.date()

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def aware(self) -> datetime:
        return self.now.replace(tzinfo=self.zone)

    def iso(self) -> str:
        return self.now.strftime("%Y-%m-%dT%H:%M")

    def resolve(self, value: str) -> str:
        """Resolve one relative string; raise ValueError if it is not one."""
        m = _REL_DAY.match(value)
        if m:
            sign, n, hh, mm = m.groups()
            day = self.today + timedelta(days=int(n) * (1 if sign == "+" else -1))
            if hh is None:
                return day.isoformat()
            return f"{day.isoformat()}T{int(hh):02d}:{mm}"
        m = _REL_SPAN.match(value)
        if m:
            sign, n, unit = m.groups()
            delta = timedelta(hours=int(n)) if unit == "h" else timedelta(minutes=int(n))
            at = self.now + delta if sign == "+" else self.now - delta
            return at.strftime("%Y-%m-%dT%H:%M")
        raise ValueError(f"not a relative time: {value!r}")

    def resolve_text(self, text: str) -> str:
        """Replace every {rel:...} inside `text`."""
        return _INLINE.sub(lambda m: self.resolve(m.group(1)), text)

    def resolve_value(self, value):
        """Resolve a value if it is a relative string (whole) or holds {rel:...}."""
        if isinstance(value, str):
            if is_relative(value):
                return self.resolve(value)
            if "{rel:" in value:
                return self.resolve_text(value)
        return value

    def resolve_tree(self, obj):
        """Resolve relative strings anywhere in a nested structure (new object)."""
        if isinstance(obj, dict):
            return {k: self.resolve_tree(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_tree(v) for v in obj]
        return self.resolve_value(obj)


def parse_local(value: str) -> datetime | None:
    """Parse an ISO-ish local date or datetime string; None when it is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", value.strip()) else value.strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:T(\d{1,2}):(\d{2}))?", text)
        if not m:
            return None
        d = datetime.fromisoformat(m.group(1))
        if m.group(2):
            d = d.replace(hour=int(m.group(2)), minute=int(m.group(3)))
        return d
    return dt
