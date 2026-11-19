"""Calendar, reminders and notes (collections `calendar`, `reminders`, `notes`)."""
from __future__ import annotations

import calendar as _cal
import re
from datetime import date, datetime, time, timedelta

from edat.state.store import StateStore
from edat.tools_generic.registry import (
    PAGING, TEXT_WINDOW, A, B, I, S, ToolError, arg_bool, arg_int, arg_list, arg_str, clean,
    fmt, matches, need_str, not_found, page, parse_hhmm, parse_when, record_dt, tool, window,
)

DEFAULT_CALENDAR = "Calendar"
DEFAULT_LIST = "Reminders"
_DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_SPAN_LIMIT_DAYS = 3 * 366


# ================================================================== calendar helpers
def _bounds(ev: dict) -> tuple[datetime, datetime, bool]:
    raw = str(ev.get("start", ""))
    start = record_dt(raw)
    if start is None:
        raise ToolError(f"event {ev.get('id')!r} has an unreadable start time")
    all_day = bool(ev.get("all_day")) or len(raw.strip()) == 10
    end = record_dt(ev.get("end")) if ev.get("end") else None
    if all_day:
        start = datetime.combine(start.date(), time())
        last = end.date() if end else start.date()
        end = datetime.combine(last, time()) + timedelta(days=1)
    elif end is None or end <= start:
        end = start + timedelta(minutes=60)
    return start, end, all_day


def _view(ev: dict) -> dict:
    out = clean(ev)
    start, end, all_day = _bounds(ev)
    if not all_day:
        out.setdefault("end", fmt(end))
    out.setdefault("calendar", DEFAULT_CALENDAR)
    out.setdefault("writable", True)
    return out


def _rrule(text: str) -> dict[str, str]:
    t = text.strip()
    if t.upper().startswith("RRULE:"):
        t = t[6:]
    parts = {}
    for p in t.split(";"):
        if "=" in p:
            k, v = p.split("=", 1)
            parts[k.strip().upper()] = v.strip().upper()
    return parts


def _check_rrule(text: str) -> str:
    rule = _rrule(text)
    if rule.get("FREQ") not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        raise ToolError("invalid recurrence: use an RFC 5545 rule such as FREQ=WEEKLY;BYDAY=TU")
    return text.strip()[6:] if text.strip().upper().startswith("RRULE:") else text.strip()


def _until(v: str | None) -> date | None:
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", v or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _day_matches(rule: dict, d0: date, d: date) -> bool:
    freq = rule["FREQ"]
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    byday = [x for x in rule.get("BYDAY", "").split(",") if x]
    if freq == "DAILY":
        return (d - d0).days % interval == 0
    if freq == "WEEKLY":
        days = {_DAYS[x[-2:]] for x in byday if x[-2:] in _DAYS} or {d0.weekday()}
        week0 = d0 - timedelta(days=d0.weekday())
        return d.weekday() in days and ((d - week0).days // 7) % interval == 0
    if freq == "MONTHLY":
        months = (d.year - d0.year) * 12 + d.month - d0.month
        if months % interval:
            return False
        if byday:
            for item in byday:
                m = re.match(r"^([+-]?\d*)(MO|TU|WE|TH|FR|SA|SU)$", item)
                if not m or _DAYS[m.group(2)] != d.weekday():
                    continue
                n = int(m.group(1)) if m.group(1) not in ("", "+", "-") else 0
                last = _cal.monthrange(d.year, d.month)[1]
                if n == 0 or (n > 0 and (d.day - 1) // 7 + 1 == n) or (n < 0 and (last - d.day) // 7 + 1 == -n):
                    return True
            return False
        if rule.get("BYMONTHDAY"):
            return d.day in {int(x) for x in rule["BYMONTHDAY"].split(",") if x.lstrip("-").isdigit()}
        return d.day == d0.day
    if freq == "YEARLY":
        return (d.year - d0.year) % interval == 0 and (d.month, d.day) == (d0.month, d0.day)
    return False


def _occurrences(ev: dict, lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    """Occurrences of an event overlapping [lo, hi)."""
    start, end, _ = _bounds(ev)
    if not ev.get("rrule"):
        return [(start, end)] if start < hi and end > lo else []
    rule = _rrule(str(ev["rrule"]))
    if rule.get("FREQ") not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return [(start, end)] if start < hi and end > lo else []
    dur = end - start
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = _until(rule.get("UNTIL"))
    d, last = start.date(), min(hi.date(), start.date() + timedelta(days=_SPAN_LIMIT_DAYS))
    out, seen = [], 0
    while d <= last:
        if until and d > until:
            break
        if _day_matches(rule, start.date(), d):
            seen += 1
            if count is not None and seen > count:
                break
            s = datetime.combine(d, start.time())
            if s < hi and s + dur > lo:
                out.append((s, s + dur))
        d += timedelta(days=1)
    return out


def _range(store: StateStore, args: dict, key_lo: str, key_hi: str, required: bool) -> tuple[datetime, datetime] | None:
    if args.get(key_lo) is None and not required:
        if args.get(key_hi) is None:
            return None
        lo = datetime(1900, 1, 1)
    else:
        lo_dt, _ = parse_when(store, args.get(key_lo), key_lo)
        lo = datetime.combine(lo_dt.date(), time())
    if args.get(key_hi) is None:
        # A list covers one day by default; a search runs open-ended forward.
        hi = lo + timedelta(days=1) if required else datetime(2999, 1, 1)
    else:
        hi_dt, _ = parse_when(store, args.get(key_hi), key_hi)
        hi = datetime.combine(hi_dt.date(), time()) + timedelta(days=1)
    if hi <= lo:
        raise ToolError(f"invalid {key_hi}: must not be before {key_lo}")
    return lo, hi


def _event(store: StateStore, event_id: str) -> dict:
    ev = store.find("calendar", event_id)
    if ev is None:
        raise not_found("event", event_id)
    return ev


def _writable(ev: dict) -> None:
    if ev.get("writable") is False:
        raise ToolError(f"event {ev['id']!r} is on a read-only calendar "
                        f"({ev.get('calendar') or DEFAULT_CALENDAR})", outcome="denied")


def _calendar_writable(store: StateStore, name: str) -> None:
    for ev in store.get("calendar"):
        if (ev.get("calendar") or DEFAULT_CALENDAR).lower() == name.lower() and ev.get("writable") is False:
            raise ToolError(f"calendar {name!r} is read-only", outcome="denied")


def _occ_view(ev: dict, s: datetime, e: datetime) -> dict:
    out = _view(ev)
    all_day = _bounds(ev)[2]
    if ev.get("rrule"):
        out["series_start"] = ev["start"]
        out["start"] = s.date().isoformat() if all_day else fmt(s)
        if not all_day:
            out["end"] = fmt(e)
    return out


# ================================================================== calendar tools
_EV_ID = S("The event id, as returned by a calendar search or list.")


@tool("calendar_create_event", "calendar",
      "Create a NEW calendar event. To change an existing event use calendar_update_event instead.",
      {"title": S("Event title."),
       "start": S("Start as local ISO time, e.g. 2026-11-19T15:00, or a date (YYYY-MM-DD) for an all-day event."),
       "end": S("End as local ISO time. Defaults to start plus duration_minutes, or 60 minutes."),
       "duration_minutes": I("Length in minutes, used when end is not given.", 1),
       "location": S("Where the event takes place."),
       "attendees": A("Names or email addresses of people to invite."),
       "notes": S("Free-text notes."),
       "calendar": S(f"Calendar to put the event on (default {DEFAULT_CALENDAR})."),
       "recurrence": S("RFC 5545 recurrence rule for a repeating event, e.g. FREQ=WEEKLY;BYDAY=MO.")},
      ("title", "start"), effect=True)
def calendar_create_event(store: StateStore, args: dict) -> dict:
    title = need_str(args, "title")
    start, date_only = parse_when(store, args["start"], "start")
    cal_name = arg_str(args, "calendar") or DEFAULT_CALENDAR
    _calendar_writable(store, cal_name)
    row: dict = {"title": title, "calendar": cal_name, "writable": True}
    if date_only:
        row.update(start=start.date().isoformat(), all_day=True)
        if args.get("end") is not None:
            end, _ = parse_when(store, args["end"], "end")
            if end.date() < start.date():
                raise ToolError("invalid end: must not be before start")
            row["end"] = end.date().isoformat()
    else:
        if args.get("end") is not None:
            end = parse_when(store, args["end"], "end")[0]
        else:
            end = start + timedelta(minutes=arg_int(args, "duration_minutes", 60, lo=1))
        if end <= start:
            raise ToolError("invalid end: must be after start")
        row.update(start=fmt(start), end=fmt(end))
    for key in ("location", "notes"):
        if arg_str(args, key):
            row[key] = arg_str(args, key)
    if arg_list(args, "attendees"):
        row["attendees"] = arg_list(args, "attendees")
    if arg_str(args, "recurrence"):
        row["rrule"] = _check_rrule(args["recurrence"])
    ev = store.add("calendar", row, prefix="evt")
    return {"ok": True, "event": _view(ev)}


@tool("calendar_update_event", "calendar",
      "Change fields of an EXISTING event (title, time, place, notes). Only the fields you pass change. "
      "Moving the start keeps the event's length unless you also pass end.",
      {"event_id": _EV_ID, "title": S("New title."), "start": S("New start, local ISO time."),
       "end": S("New end, local ISO time."), "location": S("New location."), "notes": S("New notes (replaces the old notes).")},
      ("event_id",), effect=True)
def calendar_update_event(store: StateStore, args: dict) -> dict:
    ev = _event(store, args["event_id"])
    _writable(ev)
    changes: dict = {}
    for key in ("title", "location", "notes"):
        if args.get(key) is not None:
            changes[key] = arg_str(args, key)
    old_start, old_end, all_day = _bounds(ev)
    new_start = new_end = None
    if args.get("start") is not None:
        new_start, d_only = parse_when(store, args["start"], "start")
        if d_only and not all_day:
            raise ToolError("invalid start: give a time, e.g. 2026-11-19T15:00")
    if args.get("end") is not None:
        new_end = parse_when(store, args["end"], "end")[0]
    if new_start is not None or new_end is not None:
        s = new_start or old_start
        e = new_end or (s + (old_end - old_start))
        if all_day:
            if e.date() < s.date():
                raise ToolError("invalid end: must not be before start")
            changes["start"] = s.date().isoformat()
            if new_end is not None or ev.get("end"):
                changes["end"] = (e.date() if new_end else (e - timedelta(days=1)).date()).isoformat()
        else:
            if e <= s:
                raise ToolError("invalid end: must be after start")
            changes.update(start=fmt(s), end=fmt(e))
    if not changes:
        raise ToolError("nothing to update: pass at least one field to change")
    store.update("calendar", ev["id"], **changes)
    return {"ok": True, "event": _view(ev)}


@tool("calendar_delete_event", "calendar", "Delete an event from the calendar (the whole series for a repeating event).",
      {"event_id": _EV_ID}, ("event_id",), effect=True)
def calendar_delete_event(store: StateStore, args: dict) -> dict:
    ev = _event(store, args["event_id"])
    _writable(ev)
    store.remove("calendar", ev["id"])
    return {"ok": True, "deleted": ev["id"], "title": ev.get("title")}


@tool("calendar_get_event", "calendar", "Get one event's full details by id.", {"event_id": _EV_ID}, ("event_id",))
def calendar_get_event(store: StateStore, args: dict) -> dict:
    return {"event": _view(_event(store, args["event_id"]))}


@tool("calendar_search_events", "calendar",
      "Search events by words in the title, location, notes or attendees. Every word must match. "
      "Optionally limit to events between two dates.",
      {"query": S("Words to look for."), "start_date": S("Only events on or after this date (YYYY-MM-DD)."),
       "end_date": S("Only events on or before this date (YYYY-MM-DD)."), **PAGING},
      ("query",))
def calendar_search_events(store: StateStore, args: dict) -> dict:
    rng = _range(store, args, "start_date", "end_date", required=False)
    hits = []
    for ev in store.get("calendar"):
        if not matches(args["query"], ev, ("title", "location", "notes", "attendees")):
            continue
        if rng is None:
            hits.append((_bounds(ev)[0], _view(ev)))
            continue
        occ = _occurrences(ev, *rng)
        if occ:
            hits.append((occ[0][0], _occ_view(ev, *occ[0])))
    hits.sort(key=lambda h: (h[0], str(h[1]["id"])))
    return page([h[1] for h in hits], args)


@tool("calendar_list_events", "calendar",
      "List all events (including repeats) from start_date through end_date, in time order.",
      {"start_date": S("First day to list (YYYY-MM-DD)."), "end_date": S("Last day to list, inclusive (default: start_date)."),
       "calendar": S("Only events on this calendar."), **PAGING},
      ("start_date",))
def calendar_list_events(store: StateStore, args: dict) -> dict:
    lo, hi = _range(store, args, "start_date", "end_date", required=True)
    cal_name = arg_str(args, "calendar")
    rows = []
    for ev in store.get("calendar"):
        if cal_name and (ev.get("calendar") or DEFAULT_CALENDAR).lower() != cal_name.lower():
            continue
        for s, e in _occurrences(ev, lo, hi):
            rows.append((s, str(ev["id"]), _occ_view(ev, s, e)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return page([r[2] for r in rows], args)


@tool("calendar_list_calendars", "calendar", "List the calendars, whether each can be written to, and how many events each holds.", {})
def calendar_list_calendars(store: StateStore, args: dict) -> dict:
    cals: dict[str, dict] = {}
    for ev in store.get("calendar"):
        name = ev.get("calendar") or DEFAULT_CALENDAR
        c = cals.setdefault(name, {"name": name, "writable": True, "events": 0})
        c["events"] += 1
        if ev.get("writable") is False:
            c["writable"] = False
    if not cals:
        cals[DEFAULT_CALENDAR] = {"name": DEFAULT_CALENDAR, "writable": True, "events": 0}
    return {"calendars": sorted(cals.values(), key=lambda c: c["name"].lower())}


@tool("calendar_find_free_time", "calendar",
      "Find free slots of at least duration_minutes on one day, between day_start and day_end.",
      {"date": S("The day to check (YYYY-MM-DD)."), "duration_minutes": I("Minimum slot length in minutes.", 5),
       "day_start": S("Earliest time to consider, HH:MM (default 09:00)."),
       "day_end": S("Latest time to consider, HH:MM (default 17:00).")},
      ("date", "duration_minutes"))
def calendar_find_free_time(store: StateStore, args: dict) -> dict:
    day = parse_when(store, args["date"], "date")[0].date()
    need = timedelta(minutes=arg_int(args, "duration_minutes", lo=5))
    h0, m0 = parse_hhmm(arg_str(args, "day_start") or "09:00", "day_start")
    h1, m1 = parse_hhmm(arg_str(args, "day_end") or "17:00", "day_end")
    lo, hi = datetime.combine(day, time(h0, m0)), datetime.combine(day, time(h1, m1))
    if hi <= lo:
        raise ToolError("invalid day_end: must be after day_start")
    busy = sorted((max(s, lo), min(e, hi)) for ev in store.get("calendar") if not _bounds(ev)[2]
                  for s, e in _occurrences(ev, lo, hi))
    slots, cur = [], lo
    for s, e in busy:
        if s - cur >= need:
            slots.append({"start": fmt(cur), "end": fmt(s)})
        cur = max(cur, e)
    if hi - cur >= need:
        slots.append({"start": fmt(cur), "end": fmt(hi)})
    return {"date": day.isoformat(), "slots": slots}


@tool("calendar_add_attendee", "calendar", "Add one person (name or email address) to an existing event's attendees.",
      {"event_id": _EV_ID, "attendee": S("Name or email address to add.")}, ("event_id", "attendee"), effect=True)
def calendar_add_attendee(store: StateStore, args: dict) -> dict:
    ev = _event(store, args["event_id"])
    _writable(ev)
    who = need_str(args, "attendee").strip()
    current = list(ev.get("attendees") or [])
    if any(str(a).lower() == who.lower() for a in current):
        raise ToolError(f"{who} is already an attendee")
    store.update("calendar", ev["id"], attendees=current + [who])
    return {"ok": True, "event_id": ev["id"], "attendees": ev["attendees"]}


@tool("calendar_remove_attendee", "calendar", "Remove one person from an existing event's attendees.",
      {"event_id": _EV_ID, "attendee": S("Name or email address to remove, as listed on the event.")},
      ("event_id", "attendee"), effect=True)
def calendar_remove_attendee(store: StateStore, args: dict) -> dict:
    ev = _event(store, args["event_id"])
    _writable(ev)
    who = need_str(args, "attendee").strip().lower()
    current = list(ev.get("attendees") or [])
    keep = [a for a in current if str(a).lower() != who]
    if len(keep) == len(current):
        raise ToolError(f"{args['attendee']} is not an attendee of {ev['id']}", outcome="no_match")
    store.update("calendar", ev["id"], attendees=keep)
    return {"ok": True, "event_id": ev["id"], "attendees": keep}


# ================================================================== reminders
_REM_ID = S("The reminder id, as returned by reminders_list or reminders_search.")


def _reminder(store: StateStore, rid: str) -> dict:
    r = store.find("reminders", rid)
    if r is None:
        raise not_found("reminder", rid)
    return r


def _rem_view(r: dict) -> dict:
    out = clean(r)
    out.setdefault("done", False)
    out.setdefault("due", None)
    out.setdefault("list", DEFAULT_LIST)
    return out


def _rem_key(r: dict):
    d = record_dt(r.get("due"))
    return (d is None, d or datetime.min, str(r.get("id")))


def _due(store: StateStore, value) -> str | None:
    if value is None:
        return None
    dt, date_only = parse_when(store, value, "due")
    return dt.date().isoformat() if date_only else fmt(dt)


@tool("reminders_create", "reminders", "Create a new reminder, optionally with a due date or time.",
      {"title": S("What to be reminded of."), "due": S("Due date (YYYY-MM-DD) or local ISO time; omit for no due date."),
       "notes": S("Extra notes."), "list": S(f"Reminder list name (default {DEFAULT_LIST})."),
       "priority": S("Priority (default none).", ["none", "low", "medium", "high"])},
      ("title",), effect=True)
def reminders_create(store: StateStore, args: dict) -> dict:
    row = {"title": need_str(args, "title"), "due": _due(store, args.get("due")), "done": False,
           "list": arg_str(args, "list") or DEFAULT_LIST}
    for key in ("notes", "priority"):
        if arg_str(args, key):
            row[key] = arg_str(args, key)
    return {"ok": True, "reminder": _rem_view(store.add("reminders", row, prefix="rem"))}


@tool("reminders_update", "reminders", "Change an existing reminder's title, due time, notes or list.",
      {"reminder_id": _REM_ID, "title": S("New title."), "due": S("New due date or local ISO time."),
       "notes": S("New notes."), "list": S("Move to this list."),
       "priority": S("New priority.", ["none", "low", "medium", "high"])},
      ("reminder_id",), effect=True)
def reminders_update(store: StateStore, args: dict) -> dict:
    r = _reminder(store, args["reminder_id"])
    changes = {k: arg_str(args, k) for k in ("title", "notes", "list", "priority") if args.get(k) is not None}
    if args.get("due") is not None:
        changes["due"] = _due(store, args["due"])
    if not changes:
        raise ToolError("nothing to update: pass at least one field to change")
    store.update("reminders", r["id"], **changes)
    return {"ok": True, "reminder": _rem_view(r)}


@tool("reminders_complete", "reminders", "Mark a reminder as done. It stays in the list as completed; nothing is deleted.",
      {"reminder_id": _REM_ID}, ("reminder_id",), effect=True)
def reminders_complete(store: StateStore, args: dict) -> dict:
    r = _reminder(store, args["reminder_id"])
    if r.get("done"):
        raise ToolError(f"reminder {r['id']!r} is already done")
    store.update("reminders", r["id"], done=True, completed_at=store.clock.iso())
    return {"ok": True, "reminder": _rem_view(r)}


@tool("reminders_delete", "reminders", "Delete a reminder permanently, without marking it done.",
      {"reminder_id": _REM_ID}, ("reminder_id",), effect=True)
def reminders_delete(store: StateStore, args: dict) -> dict:
    r = _reminder(store, args["reminder_id"])
    store.remove("reminders", r["id"])
    return {"ok": True, "deleted": r["id"], "title": r.get("title")}


@tool("reminders_list", "reminders", "List reminders, soonest due first. Completed ones are left out unless include_completed is true.",
      {"list": S("Only this reminder list."), "include_completed": B("Include completed reminders (default false)."),
       "due_before": S("Only reminders due on or before this date or time."), **PAGING})
def reminders_list(store: StateStore, args: dict) -> dict:
    lst = arg_str(args, "list")
    done_too = arg_bool(args, "include_completed", False)
    before = None
    if args.get("due_before") is not None:
        dt, d_only = parse_when(store, args["due_before"], "due_before")
        before = datetime.combine(dt.date(), time(23, 59)) if d_only else dt
    rows = []
    for r in store.get("reminders"):
        if lst and (r.get("list") or DEFAULT_LIST).lower() != lst.lower():
            continue
        if r.get("done") and not done_too:
            continue
        if before is not None:
            d = record_dt(r.get("due"))
            if d is None or d > before:
                continue
        rows.append(r)
    rows.sort(key=_rem_key)
    return page([_rem_view(r) for r in rows], args)


@tool("reminders_search", "reminders", "Search reminders (open and completed) by words in the title or notes.",
      {"query": S("Words to look for."), **PAGING}, ("query",))
def reminders_search(store: StateStore, args: dict) -> dict:
    rows = sorted((r for r in store.get("reminders") if matches(args["query"], r, ("title", "notes", "list"))), key=_rem_key)
    return page([_rem_view(r) for r in rows], args)


@tool("reminders_list_lists", "reminders", "List the reminder lists with their open and completed counts.", {})
def reminders_list_lists(store: StateStore, args: dict) -> dict:
    lists: dict[str, dict] = {}
    for r in store.get("reminders"):
        name = r.get("list") or DEFAULT_LIST
        e = lists.setdefault(name, {"name": name, "open": 0, "completed": 0})
        e["completed" if r.get("done") else "open"] += 1
    return {"lists": sorted(lists.values(), key=lambda x: x["name"].lower())}


# ================================================================== notes
_NOTE_ID = S("The note id, as returned by notes_search or notes_list.")


def _note(store: StateStore, nid: str) -> dict:
    n = store.find("notes", nid)
    if n is None:
        raise not_found("note", nid)
    return n


@tool("notes_create", "notes", "Create a new note.",
      {"body": S("The note's text."), "title": S("Title (default: the start of the body)."), "folder": S("Folder name.")},
      ("body",), effect=True)
def notes_create(store: StateStore, args: dict) -> dict:
    body = need_str(args, "body")
    title = arg_str(args, "title") or (body.strip().splitlines()[0][:60] if body.strip() else "Note")
    row = {"title": title, "body": body}
    if arg_str(args, "folder"):
        row["folder"] = arg_str(args, "folder")
    return {"ok": True, "note": store.add("notes", row, prefix="note")}


@tool("notes_append", "notes", "Add text to the end of an existing note, keeping what is already there.",
      {"note_id": _NOTE_ID, "text": S("Text to add on a new line.")}, ("note_id", "text"), effect=True)
def notes_append(store: StateStore, args: dict) -> dict:
    n = _note(store, args["note_id"])
    text = need_str(args, "text")
    body = n.get("body") or ""
    store.update("notes", n["id"], body=f"{body}\n{text}" if body else text)
    return {"ok": True, "note": clean(n)}


@tool("notes_update", "notes", "Replace a note's title or whole body. To add to a note without losing text, use notes_append.",
      {"note_id": _NOTE_ID, "title": S("New title."), "body": S("New body; replaces the old body entirely.")},
      ("note_id",), effect=True)
def notes_update(store: StateStore, args: dict) -> dict:
    n = _note(store, args["note_id"])
    changes = {k: arg_str(args, k) for k in ("title", "body") if args.get(k) is not None}
    if not changes:
        raise ToolError("nothing to update: pass title or body")
    store.update("notes", n["id"], **changes)
    return {"ok": True, "note": clean(n)}


@tool("notes_delete", "notes", "Delete a note.", {"note_id": _NOTE_ID}, ("note_id",), effect=True)
def notes_delete(store: StateStore, args: dict) -> dict:
    n = _note(store, args["note_id"])
    store.remove("notes", n["id"])
    return {"ok": True, "deleted": n["id"], "title": n.get("title")}


@tool("notes_search", "notes", "Search notes by words in the title or body. Every word must match.",
      {"query": S("Words to look for."), "folder": S("Only notes in this folder."), **PAGING}, ("query",))
def notes_search(store: StateStore, args: dict) -> dict:
    folder = arg_str(args, "folder")
    rows = [clean(n) for n in store.get("notes")
            if matches(args["query"], n, ("title", "body"))
            and (not folder or (n.get("folder") or "").lower() == folder.lower())]
    return page(rows, args)


@tool("notes_list", "notes", "List notes (id, title, folder) without their bodies.",
      {"folder": S("Only notes in this folder."), **PAGING})
def notes_list(store: StateStore, args: dict) -> dict:
    folder = arg_str(args, "folder")
    rows = [{"id": n["id"], "title": n.get("title"), "folder": n.get("folder")} for n in store.get("notes")
            if not folder or (n.get("folder") or "").lower() == folder.lower()]
    return page(rows, args)


@tool("notes_read", "notes", "Read a note's full text. Long notes can be read in parts with offset and limit.",
      {"note_id": _NOTE_ID, **TEXT_WINDOW}, ("note_id",))
def notes_read(store: StateStore, args: dict) -> dict:
    n = _note(store, args["note_id"])
    return {"id": n["id"], "title": n.get("title"), "folder": n.get("folder"), **window(n.get("body") or "", args)}
