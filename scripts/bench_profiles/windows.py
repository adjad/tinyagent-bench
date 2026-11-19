"""What is truly on the person's schedule, and what falls in a time window.

The calendar in a profile is deliberately wrong in places: an event moved or cancelled by
email or text was never updated, and some plans exist only in messages. The ledger's
``schedule_ops`` record each difference (09-profile-schema.md §5.4). This module applies
them, so a scorer can ask "what is really on in the next 3 hours at 11:30?" for any pinned
time, not just the ones the ledger spells out.

    true_schedule(profile, ledger)                -> every real event, calendar order
    in_window(profile, ledger, day, "11:30", 3)   -> {"items", "reminders", "stale", "all_day"}
    window_entry(profile, ledger, day, at, hours) -> a ``time_window`` ledger entry

Pure functions over the raw (unrendered) profile: day offsets and HH:MM, no clock.
"""
from __future__ import annotations

from typing import Any

DAY_MIN = 24 * 60


def _m(t: str | None) -> int:
    return int(t[:2]) * 60 + int(t[3:5]) if t else 0


def _abs(day: int, t: str | None) -> int:
    return day * DAY_MIN + _m(t)


def _hhmm(abs_min: int) -> tuple[int, str]:
    day, m = divmod(abs_min, DAY_MIN)
    return day, f"{m // 60:02d}:{m % 60:02d}"


def true_schedule(profile: dict, ledger: dict) -> list[dict[str, Any]]:
    """The calendar with every schedule op applied. Each item: ref (event id, or op id for
    an event that exists only in messages), title, day, time, end, all_day, days, location,
    kind, changed (bool), because (proof ids), calendar."""
    ops = ledger.get("schedule_ops") or []
    by_ref: dict[str, list[dict]] = {}
    for op in ops:
        if op.get("ref"):
            by_ref.setdefault(op["ref"], []).append(op)
    out = []
    for ev in profile["events"]:
        item = {"ref": ev["id"], "title": ev["title"], "day": ev["day"], "time": ev["time"],
                "end": ev["end"], "end_day": ev.get("end_day", ev["day"]), "all_day": ev["all_day"],
                "days": ev.get("days", 1), "location": ev["location"], "kind": ev["kind"],
                "calendar": ev["calendar"], "changed": False, "because": []}
        dropped = False
        for op in by_ref.get(ev["id"], []):
            item["because"] += op["because"]
            item["changed"] = True
            if op["op"] in ("cancel", "exclude"):
                dropped = True
            elif op["op"] == "move":
                dur = (_abs(item["end_day"], item["end"]) - _abs(item["day"], item["time"])
                       if item["time"] and item["end"] else 60)
                item["day"] = op["day"] if op.get("day") is not None else item["day"]
                item["time"] = op.get("time") or item["time"]
                if op.get("end"):
                    item["end"], item["end_day"] = op["end"], item["day"]
                elif item["time"]:
                    item["end_day"], item["end"] = _hhmm(_abs(item["day"], item["time"]) + dur)
                if op.get("location"):
                    item["location"] = op["location"]
                if op.get("title"):
                    item["title"] = op["title"]
            elif op["op"] == "relocate":
                item["location"] = op.get("location") or item["location"]
        if not dropped:
            out.append(item)
    for op in ops:
        if op["op"] == "add":
            out.append({"ref": op["id"], "title": op["title"], "day": op["day"], "time": op["time"],
                        "end": op.get("end"), "end_day": op["day"], "all_day": not op.get("time"),
                        "days": 1, "location": op.get("location"), "kind": op.get("kind") or "event",
                        "calendar": None, "changed": True, "because": list(op["because"])})
    out.sort(key=lambda i: (i["day"], 0 if i["all_day"] else 1, _m(i["time"]), i["ref"]))
    return out


def stale_calendar(profile: dict, ledger: dict) -> list[dict[str, Any]]:
    """Calendar rows that are wrong as shown: cancelled, moved away, or not the person's."""
    ops = ledger.get("schedule_ops") or []
    hit = {op["ref"]: op for op in ops if op.get("ref") and op["op"] in ("cancel", "move", "exclude")}
    return [{"ref": ev["id"], "title": ev["title"], "day": ev["day"], "time": ev["time"],
             "end": ev["end"], "all_day": ev["all_day"], "op": hit[ev["id"]]["op"],
             "because": hit[ev["id"]]["because"]}
            for ev in profile["events"] if ev["id"] in hit]


def _overlaps(start: int, end: int, lo: int, hi: int) -> bool:
    return start < hi and end > lo if end > start else lo <= start < hi


def in_window(profile: dict, ledger: dict, day: int, at: str, hours: float) -> dict[str, list]:
    """Everything truly happening in [day at, + hours): timed events that overlap it (one
    already running counts), all-day events on a day it touches, open reminders due inside
    it, and the stale calendar rows a careless answer would wrongly include."""
    lo = _abs(day, at)
    hi = lo + int(round(hours * 60))
    first_day, last_day = lo // DAY_MIN, (hi - 1) // DAY_MIN
    items, all_day = [], []
    for it in true_schedule(profile, ledger):
        if it["all_day"]:
            if it["day"] <= last_day and it["day"] + it["days"] - 1 >= first_day:
                all_day.append(it)
            continue
        s = _abs(it["day"], it["time"])
        e = _abs(it["end_day"], it["end"]) if it["end"] else s
        if _overlaps(s, e, lo, hi):
            items.append({**it, "running": s < lo})
    reminders = [r for r in profile["reminders"]
                 if not r["completed"] and r["day"] is not None and r["time"]
                 and lo <= _abs(r["day"], r["time"]) < hi]
    stale = []
    for st in stale_calendar(profile, ledger):
        if st["all_day"]:
            continue
        s = _abs(st["day"], st["time"])
        e = _abs(st["day"], st["end"]) if st["end"] else s
        if _overlaps(s, e, lo, hi):
            stale.append(st)
    return {"items": items, "all_day": all_day, "reminders": reminders, "stale": stale}


def _fmt(t: str) -> str:
    return t


def window_entry(profile: dict, ledger: dict, day: int, at: str, hours: float) -> dict[str, Any]:
    """A ``time_window`` ledger entry (without an id) for one pinned window."""
    w = in_window(profile, ledger, day, at, hours)
    end_day, end_t = _hhmm(_abs(day, at) + int(round(hours * 60)))
    items = [{"ref": i["ref"], "title": i["title"], "day": i["day"], "time": i["time"],
              "end": i["end"], "location": i["location"], "running": i["running"],
              "changed": i["changed"]} for i in w["items"]]
    rems = [{"ref": r["id"], "title": r["title"], "day": r["day"], "time": r["time"]}
            for r in w["reminders"]]
    stale = [{"ref": s["ref"], "title": s["title"], "time": s["time"], "op": s["op"]} for s in w["stale"]]
    alld = [{"ref": a["ref"], "title": a["title"]} for a in w["all_day"]]
    evidence: list[str] = []
    stale_refs = {s["ref"] for s in w["stale"]}
    for i in w["items"]:
        # A moved event keeps its id; when its old row is also in this window it is the
        # distractor, so the proof is the message or email that moved it.
        if not i["ref"].startswith("op-") and i["ref"] not in stale_refs:
            evidence.append(i["ref"])
        evidence += [b for b in i["because"] if b not in stale_refs]
    evidence += [r["id"] for r in w["reminders"]]
    evidence = list(dict.fromkeys(evidence))
    names = "; ".join(f"{i['time']} {i['title']}" for i in items) or "nothing timed"
    if rems:
        names += "; reminders: " + "; ".join(f"{r['time']} {r['title']}" for r in rems)
    label = f"day {day:+d} {at}" if day else at
    return {
        "type": "time_window", "surfaces": ["ask"],
        "prompt": (f"Is there anything in the next {hours:g} hours?" if day == 0 else None),
        "truth": f"At {label}, the next {hours:g} h (to {end_t}{'' if end_day == day else ' next day'}) "
                 f"really hold: {names}.",
        "expected": {"window": {"day": day, "at": at, "hours": hours},
                     "items": items, "reminders": rems, "all_day": alld,
                     "must_not_include_refs": [s["ref"] for s in stale]},
        "evidence": evidence, "distractors": [s["ref"] for s in stale],
        "why": "Generated by windows.py from the calendar plus the schedule changes announced only "
               "in mail or messages; stale calendar rows in the window are the distractors.",
        "difficulty": "hard" if (stale or any(i["changed"] for i in items)) else "medium",
    }
