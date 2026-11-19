"""Date and time reasoning cases (spec 02 §3.17), tagged `datetime`.

Cases sit in the host families select, sequential, abstain and pev:

* relative dates ("in three weeks", "the day after tomorrow"), time arithmetic and durations;
* this / next weekday: resolved when unambiguous, a clarifying question when truly ambiguous
  (with fully unambiguous counter-cases so a model that always asks fails them);
* time zones: a call with someone in another zone (`datetime_eq` with `{at, tz}`);
* recurring events (neutral lane only: Toolbench's calendar models no recurrence);
* edges: the daylight-saving change, the last day of a month, 29 February. The case's own
  clock (`world.clock`) sits on the edge; the expected instants are computed here from that clock.

Lane notes found while building this:

* the assistant lane's fakes always run in America/Los_Angeles whatever `world.tz` says, and its
  calendar rejects offset-bearing times that differ from the host Mac's zone, so assistant cases
  keep the default zone and expect a naive local wall time; time-zone arithmetic is in the request;
* assistant calendar and reminder writes take `when_iso` and store a naive local `start` / `due`;
  the neutral lane stores the same naive local form plus an `rrule`.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from edat.cases.generators import case_id
from edat.cases.generators.pools import ERRANDS, person

PREFIX = "dt"
WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
LA = "America/Los_Angeles"
# errands whose key words the canonical assistant world already holds (any world seed)
COLLIDING = ("book the vet", "pick up the dry cleaning", "fix the bike light", "call the garage")
ERRAND_KEY = {"pay the gas bill": "gas bill", "renew the parking permit": "parking permit",
              "return the library books": "library books", "buy printer ink": "printer ink",
              "water the tomatoes": "tomatoes", "post the parcel": "parcel", "order new contact lenses": "contact lenses",
              "collect the prescription": "prescription", "book a haircut": "haircut",
              "send the rent receipt": "rent receipt", "check the smoke alarm": "smoke alarm",
              "defrost the freezer": "freezer", "repot the fern": "fern"}
EVENTS = [("Tidewater Dental cleaning", "Tidewater"), ("Fernhill parents' evening", "Fernhill"),
          ("Brightline kitchen quote", "Brightline"), ("Juniper Hall choir", "Juniper"),
          ("Quayside bike service", "Quayside"), ("Hartwell Joinery fitting", "Hartwell"),
          ("Riverside climbing taster", "Riverside"), ("Northgate book swap", "Northgate")]
PROJECTS = ["Juniper", "Hartwell", "Skyline", "Brightline", "Copperleaf", "Fernhill"]
# (label, zone) of the other person; the user is on Pacific time in the assistant lane
PARTNERS = [("London", "Europe/London"), ("Berlin", "Europe/Berlin"), ("Tokyo", "Asia/Tokyo"),
            ("New York", "America/New_York"), ("Sydney", "Australia/Sydney"), ("Madrid", "Europe/Madrid")]


# ------------------------------------------------------------------ date helpers

def _case(template: str, lane: str, split: str, n: int, family: str, sub: str, tags: list[str], prompt: str,
          clock: str, tz: str | None = None, **kw) -> dict:
    world = kw.pop("world", {})
    world = {**world, "clock": clock}
    if tz:
        world["tz"] = tz
    c = {"id": case_id(PREFIX, lane, split, template, n), "lane": lane, "family": family, "sub": sub,
         "tags": ["datetime"] + tags, "prompt": prompt, "world": world}
    c.update(kw)
    return c


def nth_weekday(year: int, month: int, wd: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(wd - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_weekday(year: int, month: int, wd: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def safe_base(rng: random.Random, weekday: int | None = None) -> date:
    """A date in the stable summer window of 2027 (no DST change in any partner zone within a month)."""
    d = date(2027, 4, 12) + timedelta(days=rng.randint(0, 150))
    if weekday is not None:
        d += timedelta(days=(weekday - d.weekday()) % 7)
    return d


def clock_str(d: date, hh: int = 8, mm: int = 30) -> str:
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}"


def rel(d: date, base: date, hh: int | None = None, mm: int = 0) -> str:
    off = (d - base).days
    return f"{off:+d}d" if hh is None else f"{off:+d}d {hh:02d}:{mm:02d}"


def h12(h: int) -> int:
    return h % 12 or 12


def say_time(h: int, m: int = 0) -> str:
    return f"{h12(h)}{'' if m == 0 else f':{m:02d}'}{'am' if h < 12 else 'pm'}"


def time_forms(h: int, m: int) -> list[str]:
    ap = "am" if h < 12 else "pm"
    out = [f"{h12(h)}:{m:02d} {ap}", f"{h12(h)}:{m:02d}{ap}", f"{h:02d}:{m:02d}", f"{h}:{m:02d}",
           f"{h12(h)}:{m:02d} {ap[0]}.m."]
    if m == 0:
        out += [f"{h12(h)} {ap}", f"{h12(h)}{ap}"]
    return out


def tool_event(lane: str, title: str, start: str, duration: int | None = None, recurrence: str | None = None) -> dict:
    if lane == "assistant":
        args = {"title": title, "when_iso": start}
        if duration:
            args["duration_min"] = duration
        return {"name": "add_calendar_event", "arguments": args}
    args = {"title": title, "start": start}
    if duration:
        args["duration_minutes"] = duration
    if recurrence:
        args["recurrence"] = recurrence
    return {"name": "calendar_create_event", "arguments": args}


def tool_reminder(lane: str, title: str, when: str) -> dict:
    if lane == "assistant":
        return {"name": "add_reminder", "arguments": {"title": title, "when_iso": when}}
    return {"name": "reminders_create", "arguments": {"title": title, "due": when}}


def tool_read(lane: str, query: str, day: str | None = None, days: int = 14) -> dict:
    if lane == "assistant":
        return {"name": "get_upcoming", "arguments": {"days": days, "query": query}}
    return {"name": "calendar_search_events", "arguments": {"query": query}}


def _errand(rng: random.Random) -> tuple[str, str]:
    e = rng.choice([x for x in ERRANDS if x in ERRAND_KEY])
    return e, ERRAND_KEY[e]


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ------------------------------------------------------------------ select: relative dates

def rel_reminder(lane, split, rng, n):
    base = safe_base(rng)
    e, key = _errand(rng)
    k = rng.randint(2, 6)
    phrases = [(f"in {k} weeks", 7 * k), ("the day after tomorrow", 2), ("a fortnight from today", 14),
               (f"{rng.choice(['nine', 'ten', 'eleven', 'twelve'])} days from now", None), (f"in {k + 2} days", k + 2)]
    phrase, off = rng.choice(phrases)
    if off is None:
        off = {"nine": 9, "ten": 10, "eleven": 11, "twelve": 12}[phrase.split()[0]]
    hh = rng.choice([8, 9, 10, 14, 15, 17])
    due = f"+{off}d {hh:02d}:00"
    prompt = f"remind me to {e} {phrase} at {say_time(hh)}"
    return _case("rel_reminder", lane, split, n, "select", "relative_date", [], prompt, clock_str(base),
                 final_state=[{"in": "reminders", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "due": due}}],
                 unchanged=["calendar", "messages_sent", "mail_sent"], forbid=[], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_reminder(lane, _cap(e), due)]}, {"text": f"Done, I'll remind you to {e} {phrase} at {say_time(hh)}."}],
                 trap="relative-date arithmetic: the due date is the case clock plus the stated offset")


def rel_event(lane, split, rng, n):
    base = safe_base(rng)
    title, key = rng.choice(EVENTS)
    phrase, off = rng.choice([("the day after tomorrow", 2), ("three days from now", 3), ("a week from tomorrow", 8),
                              ("in two weeks", 14), ("in three weeks", 21)])
    hh = rng.choice([9, 11, 13, 16])
    start = f"+{off}d {hh:02d}:00"
    return _case("rel_event", lane, split, n, "select", "relative_date", [], f"put {title} on my calendar {phrase} at {say_time(hh)}",
                 clock_str(base),
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "start": start}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_event(lane, title, start)]}, {"text": f"Added {title} {phrase} at {say_time(hh)}."}],
                 trap="relative-date arithmetic for a calendar event")


def duration_event(lane, split, rng, n):
    base = safe_base(rng)
    title, key = rng.choice(EVENTS)
    hh, mm = rng.choice([(14, 15), (16, 45), (9, 30), (13, 20)])
    dur, spoken = rng.choice([(90, "an hour and a half"), (135, "two hours and fifteen minutes"),
                              (105, "an hour and 45 minutes"), (150, "two and a half hours")])
    end = datetime(2000, 1, 1, hh, mm) + timedelta(minutes=dur)
    start = f"+1d {hh:02d}:{mm:02d}"
    end_rel = f"+1d {end.hour:02d}:{end.minute:02d}"
    prompt = f"add {title} tomorrow at {say_time(hh, mm)}, it runs {spoken}. what time does it finish?"
    return _case("duration", lane, split, n, "select", "duration", [], prompt, clock_str(base),
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "start": start, "end": end_rel}}],
                 answer={"contains_any": time_forms(end.hour, end.minute)},
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_event(lane, title, start, dur)]},
                         {"text": f"Added. It finishes at {h12(end.hour)}:{end.minute:02d} {'AM' if end.hour < 12 else 'PM'}."}],
                 trap="duration arithmetic: the end time is start plus the length, in the event and in the answer")


def days_until(lane, split, rng, n):
    base = safe_base(rng)
    title, key = rng.choice(EVENTS)
    prompt = rng.choice([f"how many days until my {title}?", f"how long until the {title}, in days?"])
    return _case("days_until", lane, split, n, "select", "duration", [], prompt, clock_str(base),
                 facts={"d": {"int": [3, 13]}},
                 world={"seed_records": {"calendar": [{"id": "ev-dt-1", "title": title, "start": "+{d}d 10:00"}]}},
                 answer={"regex": r"\b{d}\s*(more\s+)?days?\b|\b{d}-day|in {d}\b"},
                 unchanged=["calendar", "reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_read(lane, key, days=21) if lane == "assistant" else
                                    {"name": "calendar_list_events", "arguments": {"start_date": "{today_iso}", "end_date": "+30d"}}]},
                         {"text": f"Your {title} is in {{d}} days."}],
                 trap="the count of days lives in the calendar; guessing from the prompt fails")


# ------------------------------------------------------------------ select: this / next weekday (unambiguous)

def weekday_resolved(lane, split, rng, n):
    kind = rng.choice(["this", "next_monday", "next_week"])
    if kind == "this":      # Monday or Tuesday, "this Thursday/Friday": the coming one in the same week
        base = safe_base(rng, rng.choice([0, 1]))
        wd = rng.choice([3, 4])
        target = base + timedelta(days=wd - base.weekday())
        phrase = f"this {WD[wd]}"
    elif kind == "next_monday":  # Wednesday to Friday: "next Monday" is the same date either way
        base = safe_base(rng, rng.choice([2, 3, 4]))
        target = base + timedelta(days=7 - base.weekday())
        phrase = "next Monday"
    else:                   # explicit: "<weekday> of next week"
        base = safe_base(rng, rng.choice([0, 1, 2]))
        wd = rng.choice([1, 3, 4])
        target = base + timedelta(days=7 - base.weekday() + wd)
        phrase = f"{WD[wd]} of next week"
    title, key = rng.choice(EVENTS)
    hh = rng.choice([10, 14, 16])
    start = rel(target, base, hh)
    return _case("weekday", lane, split, n, "select", "weekday", [], f"put {title} on my calendar {phrase} at {say_time(hh)}",
                 clock_str(base),
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "start": start}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_event(lane, title, start)]},
                         {"text": f"Added {title} for {WD[target.weekday()]} {target.isoformat()} at {say_time(hh)}."}],
                 trap=f"'{phrase}' said on a {WD[base.weekday()]} has one reading; the date is {target.isoformat()}")


# ------------------------------------------------------------------ select: time zones and the DST edge

def _convert(partner_tz: str, local_tz: str, d: date, hh: int, mm: int = 0):
    there = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ZoneInfo(partner_tz))
    here = there.astimezone(ZoneInfo(local_tz)).replace(tzinfo=None)
    return there.replace(tzinfo=None), here


def _tz_case(template, lane, split, n, rng, base, d, partner, hh, mm, sub, trap, clock_hh=8, local_tz=LA,
             phrase_day=None, tz_world=None, tags=()):
    label, ptz = partner
    first, _ = person(rng)
    there, here = _convert(ptz, local_tz, d, hh, mm)
    zone_note = "I'm on Pacific time" if lane == "assistant" else ""
    phrase_day = phrase_day or f"on {WD[d.weekday()]}"
    prompt = f"set up a call with {first} in {label} {phrase_day} at {say_time(hh, mm)} their time"
    prompt += f" ({zone_note})" if zone_note else ""
    at = there.strftime("%Y-%m-%dT%H:%M")
    start_local = here.strftime("%Y-%m-%dT%H:%M")
    title = f"Call with {first}"
    return _case(template, lane, split, n, "select", sub, list(tags), prompt, clock_str(base, clock_hh, 0), tz=tz_world,
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": first}},
                               "expect": {"count": 1, "start": {"datetime_eq": {"at": at, "tz": ptz}}}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_event(lane, title, start_local)]},
                         {"text": f"Booked {title} for {here.strftime('%A %H:%M')} your time, which is "
                                  f"{say_time(hh, mm)} in {label}."}],
                 trap=trap)


def tz_call(lane, split, rng, n):
    base = safe_base(rng, rng.choice([0, 1, 2]))
    for _ in range(40):
        partner = rng.choice(PARTNERS)
        d = base + timedelta(days=rng.randint(2, 6))
        hh, mm = rng.choice([(9, 0), (10, 30), (14, 0), (15, 30), (8, 0), (17, 0), (11, 0)])
        here = _convert(partner[1], LA, d, hh, mm)[1]
        if 6 <= here.hour <= 21:
            break
    return _tz_case("tz_call", lane, split, n, rng, base, d, partner, hh, mm, "timezone",
                    f"{partner[0]} to Pacific conversion: a wrong offset gives the wrong hour")


def tz_call_neutral(lane, split, rng, n):
    base = safe_base(rng, rng.choice([0, 1, 2]))
    local = "Europe/London"
    partner = rng.choice([("New York", "America/New_York"), ("Tokyo", "Asia/Tokyo"), ("Sydney", "Australia/Sydney")])
    d = base + timedelta(days=rng.randint(2, 6))
    hh, mm = rng.choice([(9, 0), (10, 30), (14, 0), (15, 0), (8, 0)])
    # keep the London wall time between 07:00 and 21:00
    for _ in range(40):
        here = _convert(partner[1], local, d, hh, mm)[1]
        if 7 <= here.hour <= 21:
            break
        hh, mm = rng.choice([(9, 0), (10, 30), (14, 0), (15, 0), (8, 0), (17, 30)])
    c = _tz_case("tz_call", lane, split, n, rng, base, d, partner, hh, mm, "timezone",
                 f"{partner[0]} to London conversion: a wrong offset gives the wrong hour", local_tz=local,
                 tz_world=local)
    c["prompt"] = c["prompt"] + ". I'm on London time."
    return c


def dst_edge(lane, split, rng, n):
    """The clock sits the evening before a US DST change; the call is the Monday after it."""
    year = rng.choice([2027, 2028, 2029])
    spring = rng.random() < 0.5
    if spring:
        sunday = nth_weekday(year, 3, 6, 2)          # second Sunday of March: clocks go forward
        partner = rng.choice([("Tokyo", "Asia/Tokyo"), ("Sydney", "Australia/Sydney")])
        hhs = [(9, 0), (8, 0), (8, 30)]
    else:
        sunday = nth_weekday(year, 11, 6, 1)         # first Sunday of November: clocks go back
        partner = ("London", "Europe/London")
        hhs = [(17, 0), (16, 0), (18, 30)]
    base = sunday - timedelta(days=1)
    d = sunday + timedelta(days=1)
    hh, mm = rng.choice(hhs)
    c = _tz_case("dst_edge", lane, split, n, rng, base, d, partner, hh, mm, "dst_edge",
                 "the US clock changes between now and the call: today's offset gives the wrong hour",
                 clock_hh=20, tz_world=None)
    # make sure the local wall time avoids the 01:00-03:00 window of a change day
    return c


def dst_edge_neutral(lane, split, rng, n):
    year = rng.choice([2027, 2028, 2029])
    sunday = nth_weekday(year, 3, 6, 2)            # US clocks change; the UK has not yet
    base = sunday - timedelta(days=1)
    d = sunday + timedelta(days=1)
    partner = ("New York", "America/New_York")
    hh, mm = rng.choice([(9, 0), (10, 0), (14, 30)])
    c = _tz_case("dst_edge", lane, split, n, rng, base, d, partner, hh, mm, "dst_edge",
                 "New York changes clocks this weekend and London does not: the usual 5-hour gap is 4 hours",
                 clock_hh=20, local_tz="Europe/London", tz_world="Europe/London")
    c["prompt"] += ". I'm on London time."
    return c


# ------------------------------------------------------------------ select: month end and 29 February

def leap_day(lane, split, rng, n):
    year = rng.choice([2027, 2031, 2035])           # the next year is a leap year
    base = date(year, 12, rng.randint(6, 20))
    e, key = _errand(rng)
    target = f"{year + 1}-02-29T09:00"
    prompt = f"remind me to {e} on the last day of February at 9am"
    return _case("leap_day", lane, split, n, "select", "month_end", [], prompt, clock_str(base),
                 final_state=[{"in": "reminders", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "due": target}}],
                 unchanged=["calendar", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_reminder(lane, _cap(e), target)]}, {"text": f"Set for Feb 29, {year + 1} at 9am."}],
                 trap=f"{year + 1} is a leap year, so the last day of February is the 29th")


def last_day_next_month(lane, split, rng, n):
    month = rng.choice([1, 8, 10, 12, 4])
    year = rng.choice([2027, 2028, 2029])
    base = date(year, month, rng.randint(5, 20))
    ny, nm = (year + (month == 12), month % 12 + 1)
    last = (date(ny + (nm == 12), nm % 12 + 1, 1) - timedelta(days=1))
    title, key = rng.choice(EVENTS)
    start = rel(last, base, 17)
    return _case("last_day", lane, split, n, "select", "month_end", [],
                 f"book {title} for the last day of next month at 5pm", clock_str(base),
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "start": start}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_event(lane, title, start)]}, {"text": f"Booked {title} for {last.isoformat()} at 5pm."}],
                 trap=f"next month ends on day {last.day} ({last.isoformat()}); month lengths and leap years matter")


# ------------------------------------------------------------------ sequential and PEV

def shift_event(lane, split, rng, n):
    base = safe_base(rng)
    title, key = rng.choice(EVENTS)
    start_off = rng.randint(2, 5)
    shift, phrase = rng.choice([(7, "to the same time next week"), (14, "two weeks later, same time"),
                                (3, "three days later, keep the time"), (10, "ten days later at the same time")])
    new_off = start_off + shift
    prompt = f"move my {title} {phrase}"
    start_old = f"+{start_off}d {{h24}}:00"
    start_new = f"+{new_off}d {{h24}}:00"
    if lane == "assistant":
        steps = [{"calls": [tool_read(lane, key, days=14)]},
                 {"calls": [{"name": "update_event", "arguments": {"title": title, "when_iso": start_new,
                                                                   "current_when_iso": start_old}}]},
                 {"text": f"Moved {title}."}]
    else:
        steps = [{"calls": [tool_read(lane, key)]},
                 {"calls": [{"name": "calendar_update_event", "arguments": {"event_id": "ev-dt-m1", "start": start_new}}]},
                 {"text": f"Moved {title}."}]
    return _case("shift", lane, split, n, "sequential", "shift", [], prompt, clock_str(base),
                 facts={"h24": {"int": [9, 16]}},
                 world={"seed_records": {"calendar": [{"id": "ev-dt-m1", "title": title, "start": start_old}]}},
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "start": start_new}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 2, "steps": 3},
                 oracle=steps, trap="the time of day is only in the calendar: read it, then shift the date by the offset")


def follow_up(lane, split, rng, n):
    base = safe_base(rng)
    project = rng.choice(PROJECTS)
    k = rng.choice([7, 10, 14, 21])
    kick = f"{project} kickoff"
    prompt = f"schedule the {project} follow-up exactly {k} days after the kickoff, same time, and check it landed"
    start = "+{dk}d {h24}:00"
    read_call = tool_read(lane, project, days=21)
    if lane == "neutral":
        read_after = {"name": "calendar_list_events", "arguments": {"start_date": "{today_iso}", "end_date": "+40d"}}
        verify = {"after": "calendar_create_event",
                  "read_any": ["calendar_get_event", "calendar_search_events", "calendar_list_events"]}
    else:
        read_after = tool_read(lane, project, days=40)
        verify = {"after": "add_calendar_event", "read_any": ["get_upcoming", "get_past_events", "get_my_day_plan"]}
    return _case("follow_up", lane, split, n, "pev", "follow_up", [], prompt, clock_str(base),
                 facts={"d": {"int": [2, 6]}, "dk": {"add": ["d", k]}, "h24": {"int": [9, 15]}},
                 world={"seed_records": {"calendar": [{"id": "ev-dt-k1", "title": kick, "start": "+{d}d {h24}:00"}]}},
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": "follow"}},
                               "expect": {"count": 1, "start": start}}],
                 verify=verify,
                 claims=[{"success_claim": r"(scheduled|booked|created|added|set up)",
                          "state": {"in": "calendar", "where": {"title": {"icontains": "follow"}}, "expect": {"count": 1}}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 3, "steps": 4},
                 oracle=[{"calls": [read_call]},
                         {"calls": [tool_event(lane, f"{project} follow-up", start)]},
                         {"calls": [read_after]},
                         {"text": f"Scheduled the {project} follow-up {k} days after the kickoff and confirmed it is on the calendar."}],
                 trap="read the kickoff date, add the offset, then read the calendar back before claiming it is done")


# ------------------------------------------------------------------ abstain: this / next weekday

AMBIGUOUS = [(0, "next Friday", 4), (1, "next Wednesday", 2), (2, "next Friday", 4), (3, "next Friday", 4),
             (4, "Friday", 4)]


def weekday_ambiguous(lane, split, rng, n):
    base_wd, phrase, wd = rng.choice(AMBIGUOUS)
    base = safe_base(rng, base_wd)
    near = base + timedelta(days=(wd - base.weekday()) % 7) if phrase != "Friday" else base
    far = near + timedelta(days=7)
    title, key = rng.choice(EVENTS)
    prompt = f"put {title} on my calendar {'on ' if phrase == 'Friday' else ''}{phrase} at {say_time(15)}"
    near_name = "today" if near == base else f"this coming {WD[wd]} ({near.strftime('%b %-d')})"
    return _case("ambiguous", lane, split, n, "abstain", "ambiguous_weekday", ["missing_param"], prompt, clock_str(base),
                 menu_extra=[tool_event(lane, "x", "y")["name"]],
                 clarify={"slots": ["date"], "question_regex": r"\b(which|what|do you mean|this|next|coming|today)\b[^?]*\?"},
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}}, "expect": {"count": 0}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 0, "steps": 1},
                 oracle=[{"text": f"Do you mean {near_name} or {WD[wd]} {far.strftime('%b %-d')}?"}],
                 trap=f"'{phrase}' said on a {WD[base.weekday()]} can mean {near.isoformat()} or {far.isoformat()}: "
                      "picking one silently fails")


def weekday_unambiguous(lane, split, rng, n):
    kind = rng.choice([(0, "this Friday", 4, 0), (1, "this Thursday", 3, 0), (2, "next Monday", 0, 1), (3, "next Monday", 0, 1)])
    base_wd, phrase, wd, nxt = kind
    base = safe_base(rng, base_wd)
    target = base + timedelta(days=(wd - base.weekday()) % 7 or 7)
    title, key = rng.choice(EVENTS)
    hh = rng.choice([10, 14, 16])
    start = rel(target, base, hh)
    return _case("unambiguous", lane, split, n, "abstain", "ambiguous_weekday", ["counter"],
                 f"put {title} on my calendar {phrase} at {say_time(hh)}", clock_str(base), actionable=True,
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "start": start}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_event(lane, title, start)]},
                         {"text": f"Added {title} on {WD[target.weekday()]} {target.isoformat()} at {say_time(hh)}."}],
                 trap=f"counter: '{phrase}' said on a {WD[base.weekday()]} has one reading; asking is a needless question")


# ------------------------------------------------------------------ neutral: recurring events

def _recur_case(template, split, rng, n, prompt, base, title, key, start, rrule_spec, oracle_rule, trap):
    return _case(template, "neutral", split, n, "select", "recurring", [], prompt, clock_str(base),
                 final_state=[{"in": "calendar", "where": {"title": {"icontains": key}},
                               "expect": {"count": 1, "rrule": rrule_spec, "start": start}}],
                 unchanged=["reminders", "messages_sent", "mail_sent"], optimal={"calls": 1, "steps": 2},
                 oracle=[{"calls": [tool_event("neutral", title, start, recurrence=oracle_rule)]},
                         {"text": f"Created the repeating event '{title}'."}], trap=trap)


def recur_weekly(lane, split, rng, n):
    base = safe_base(rng, rng.choice([0, 1, 2]))
    wd = rng.choice([3, 4])
    first = base + timedelta(days=wd - base.weekday())
    title, key = rng.choice(EVENTS)
    hh = rng.choice([9, 12, 18])
    start = rel(first, base, hh)
    return _recur_case("recur_weekly", split, rng, n,
                       f"every {WD[wd]} at {say_time(hh)} I have {title}; put it on my calendar, starting this coming {WD[wd]}",
                       base, title, key, start, {"rrule": {"FREQ": "WEEKLY", "BYDAY": BYDAY[wd]}},
                       f"FREQ=WEEKLY;BYDAY={BYDAY[wd]}", "weekly recurrence rule on the right weekday, first occurrence this week")


def recur_biweekly(lane, split, rng, n):
    base = safe_base(rng, rng.choice([0, 1]))
    wd = rng.choice([2, 3])
    first = base + timedelta(days=wd - base.weekday())
    title, key = rng.choice(EVENTS)
    hh = rng.choice([10, 15, 17])
    start = rel(first, base, hh)
    return _recur_case("recur_biweekly", split, rng, n,
                       f"add {title} to my calendar every second {WD[wd]} at {say_time(hh)}, first one this coming {WD[wd]}",
                       base, title, key, start,
                       {"rrule": {"FREQ": "WEEKLY", "INTERVAL": "2", "BYDAY": BYDAY[wd]}},
                       f"FREQ=WEEKLY;INTERVAL=2;BYDAY={BYDAY[wd]}", "'every second Tuesday' is INTERVAL=2, not a weekly rule")


def recur_last_friday(lane, split, rng, n):
    base = safe_base(rng)
    first = last_weekday(base.year, base.month, 4)
    if first <= base:
        m = base.month % 12 + 1
        first = last_weekday(base.year + (base.month == 12), m, 4)
    title, key = rng.choice(EVENTS)
    start = rel(first, base, 16)
    spec = {"any_of": [{"rrule": {"FREQ": "MONTHLY", "BYDAY": "-1FR"}},
                       {"rrule": {"FREQ": "MONTHLY", "BYDAY": "FR", "BYSETPOS": "-1"}}]}
    return _recur_case("recur_last_friday", split, rng, n,
                       f"{title} happens on the last Friday of every month at 4pm, add it starting with the next one",
                       base, title, key, start, spec, "FREQ=MONTHLY;BYDAY=-1FR",
                       "'last Friday of the month' is a monthly rule with -1FR (or BYSETPOS=-1), first occurrence computed from the clock")


# ------------------------------------------------------------------ neutral: leap day with the London clock, and ambiguity

# ------------------------------------------------------------------ assembly

ASSISTANT = [(rel_reminder, 3), (rel_event, 1), (duration_event, 2), (days_until, 1), (weekday_resolved, 2),
             (tz_call, 2), (dst_edge, 2), (leap_day, 1), (last_day_next_month, 2),
             (shift_event, 2), (follow_up, 2), (weekday_ambiguous, 3), (weekday_unambiguous, 2)]
NEUTRAL = [(recur_weekly, 1), (recur_biweekly, 1), (recur_last_friday, 1), (tz_call_neutral, 1),
           (dst_edge_neutral, 1), (leap_day, 1), (weekday_ambiguous, 1), (follow_up, 1)]


def _run(plan, lane, split, rng):
    cases, counts = [], {}
    for fn, times in plan:
        for _ in range(times):
            counts[fn.__name__] = counts.get(fn.__name__, 0) + 1
            cases.append(fn(lane, split, rng, counts[fn.__name__]))
    for i, c in enumerate(cases):
        if i % 5 in (0, 3):
            c["tags"].append("core")
    return cases


def generate(split: str, rng: random.Random) -> list[dict]:
    return _run(ASSISTANT, "assistant", split, rng) + _run(NEUTRAL, "neutral", split, rng)
