"""Map a benchmark profile onto Harbor's two synthetic worlds (09-profile-schema.md §6).

    to_sandbox_state(profile)   -> a sandbox/world.py state dict (schema v1), clock frozen at anchor.now
    to_toolbench_world(profile) -> a scripts.toolbench.world.World, today = anchor date

Both take a RAW profile (as in profile.json) and render it first, so ``today`` and
``tz`` work the same as in render.load_profile.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from scripts.bench_profiles.render import render

SANDBOX_SCHEMA_VERSION = 1


def _rendered(profile: dict, today: date | None, tz: str | None) -> dict:
    return render(profile, today, tz)


def _display(p: dict, cid: str) -> str:
    c = next((c for c in p["contacts"] if c["id"] == cid), None)
    return c["name"] if c else cid


def _dm_label(p: dict, th: dict) -> str:
    """A DM's label: the contact's display name, or the raw handle of an unsaved sender."""
    if th["participants"]:
        return _display(p, th["participants"][0])
    return next((m["from"] for m in th["messages"] if m["from"] != "me"), "Unknown")


def to_sandbox_state(profile: dict, today: date | None = None, tz: str | None = None) -> dict[str, Any]:
    p = _rendered(profile, today, tz)
    booted = p["anchor"]["now_ts"]
    off = lambda ts: ts - booted  # noqa: E731
    acct_name = {a["id"]: a["name"] for a in p["accounts"]}

    contacts = {c["id"]: {"id": c["id"], "name": c["name"], "handles": c["phones"] + c["emails"],
                          "email": c["emails"][0] if c["emails"] else None}
                for c in p["contacts"]}
    threads = {}
    for th in p["threads"]:
        dm = th["kind"] == "dm"
        threads[th["id"]] = {
            "id": th["id"], "kind": th["kind"], "title": th["title"],
            "context_label": _dm_label(p, th) if dm else None,
            "participants": th["participants"], "draft": None,
            "messages": [{"id": m["id"], "ts_off": off(m["ts"]),
                          "from": "me" if m["from"] == "me" else _display(p, m["from"]),
                          "text": m["text"], "via": "seed"} for m in th["messages"]],
        }
    emails = {}
    for e in p["emails"]:
        emails[e["id"]] = {
            "id": e["id"], "message_id": e["message_id"], "account": acct_name[e["account"]],
            "account_id": "bench:" + e["account"], "ts_off": off(e["ts"]),
            "from_name": e["from"]["name"], "from_addr": e["from"]["addr"], "to": e["to"],
            "cc": e["cc"], "subject": e["subject"], "body": e["body"], "unread": e["unread"],
            "mailbox": e["mailbox"], "deep": e["day"] < -30,
            "in_reply_to": e["in_reply_to"],
        }
    calendar = {}
    for ev in p["events"]:
        sid = ev.get("series_id") or ev["id"]
        ts_off = off(ev["ts"])
        calendar[f"{sid}@{ts_off}"] = {
            "id": ev["id"], "source_id": sid, "kind": ev["kind"], "title": ev["title"],
            "context": ev["context"], "organizer": ev["organizer"],
            "account": acct_name.get(ev["account"]) if ev["account"] else ev["calendar"],
            "ts_off": ts_off, "all_day": ev["all_day"], "location": ev["location"],
            "end_off": off(ev["end_ts"]) if ev.get("end_ts") else None,
            "invitee_count": ev["invitee_count"], "organizer_is_user": ev["organizer_is_user"],
            "calendar_writable": ev["calendar_writable"],
        }
    reminders, undated = {}, []
    for r in p["reminders"]:
        if r["completed"]:
            continue
        if r["due_ts"] is None:
            undated.append({"source_id": r["id"], "title": r["title"], "context": r["list"]})
            continue
        reminders[r["id"]] = {"id": r["id"], "source_id": r["id"], "kind": "reminder",
                              "title": r["title"], "context": r["list"], "organizer": None,
                              "account": None, "ts_off": off(r["due_ts"]),
                              "all_day": r["all_day"], "location": None}
    notes = {n["id"]: {"id": n["id"], "ts_off": off(n["ts"]), "title": n["title"],
                       "folder": n["folder"], "body": n["body"]} for n in p["notes"]}
    browser: dict[str, list] = {"safari": [], "chrome": []}
    for b in p["browser"]:
        browser.setdefault(b["browser"], []).append(
            {"ts_off": off(b["ts"]), "host": b["host"], "path": b["path"], "title": b["title"]})
    return {
        "version": SANDBOX_SCHEMA_VERSION, "rev": 0, "booted_at": booted,
        "clock": {"mode": "frozen", "frozen_at": booted, "sync_scale": 0.05},
        "persona": {"identity_emails": [a["address"] for a in p["accounts"] if a["is_identity"]],
                    "bench_profile": p["profile_id"]},
        "contacts": contacts, "threads": threads, "emails": emails, "notes": notes,
        "calendar": calendar, "reminders": reminders, "undated_reminders": undated,
        "browser": browser, "notifications": [], "action_log": [],
        "injection": {"fail": {}, "stall_ms": {}, "drop_result": [], "unavailable": [],
                      "sync_paused": False, "latency_ms": 0},
        "sync_status": {},
    }


def _clock(hhmm: str | None) -> str:
    if not hhmm:
        return ""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return datetime(2000, 1, 1, h, m).strftime("%-I:%M %p")


def to_toolbench_world(profile: dict, today: date | None = None, tz: str | None = None):
    from scripts.toolbench import world as tw

    p = _rendered(profile, today, tz)
    t = date.fromisoformat(p["anchor"]["date"])
    hh, mm = int(p["anchor"]["now"][:2]), int(p["anchor"]["now"][3:])
    w = tw.World(today=t, facts={}, now=datetime(t.year, t.month, t.day, hh, mm))
    w.effects = tw._Effects(w)
    acct_name = {a["id"]: a["name"] for a in p["accounts"]}
    for ev in p["events"]:
        d = t + timedelta(days=ev["day"])
        dur = 60
        if ev.get("time") and ev.get("end"):
            dur = max(0, int((ev["end_ts"] - ev["ts"]) // 60))
        row = tw._event(ev["title"], d, _clock(ev["time"]) or "9:00 AM", ev["location"] or "",
                        account=acct_name.get(ev["account"], "") if ev["account"] else "",
                        calendar=ev["calendar"], writable=ev["calendar_writable"],
                        invitees=ev["invitee_count"], organizer_is_user=ev["organizer_is_user"],
                        organizer=ev["organizer"] or "", recurring=bool(ev.get("series_id")),
                        duration=dur, all_day=ev["all_day"], series=ev.get("series_id") or "",
                        days=ev.get("days", 1))
        row["bench_id"] = ev["id"]
        (w.events if ev["day"] >= 0 else w.past_events).append(row)
    for e in p["emails"]:
        if e["mailbox"] in ("spam", "trash"):
            continue
        row = tw._mail(e["id"], f"{e['from']['name']} <{e['from']['addr']}>",
                       acct_name[e["account"]], e["subject"], e["day"], _clock(e["time"]),
                       e["unread"], e["body"], e["category"], bulk=e["bulk"])
        row["day"] = t + timedelta(days=e["day"])
        row["mailbox"] = e["mailbox"]
        w.emails.append(row)
    for th in p["threads"]:
        group = th["kind"] == "group"
        ctx = th["title"] or (", ".join(_display(p, c) for c in th["participants"]) if group
                              else _dm_label(p, th))
        for m in th["messages"]:
            sender = "Me" if m["from"] == "me" else _display(p, m["from"])
            row = tw._text(ctx, sender, m["text"], m["day"], _clock(m["time"]),
                           bool(m.get("unread")), group=group)
            row["day"] = t + timedelta(days=m["day"])
            row["bench_id"] = m["id"]
            w.messages.append(row)
    for i, r in enumerate(x for x in p["reminders"] if not x["completed"]):
        due = t + timedelta(days=r["day"]) if r["day"] is not None else None
        row = tw._reminder(r["title"], due, _clock(r["time"]),
                           created=t + timedelta(days=r["created_day"]))
        row["seq"] = row["useq"] = i
        w.reminders.append(row)
    w.next_id["rseq"] = len(w.reminders)
    for c in p["contacts"]:
        w.contacts.append({"name": c["name"], "full": c["full_name"],
                           "email": c["emails"][0] if c["emails"] else "",
                           "phone": c["phones"][0] if c["phones"] else "",
                           **({"birthday": c["birthday"]} if c.get("birthday") else {})})
    w.notes = [{"title": n["title"], "body": n["body"]} for n in p["notes"]]
    for d in p["documents"]:
        pp = PurePosixPath(d["path"])
        w.files.setdefault(str(pp.parent), []).append(pp.name)
        w.documents[pp.name] = d["content"]
    for i, m in enumerate(p["memory"], start=1):
        w.memory_facts.append(tw._memory(i, m["text"], m["category"], m["origin"], t, m["day"]))
    w.next_id["mem"] = len(w.memory_facts)
    w.profile = {"name": p["person"]["preferred_name"],
                 "goals": list((p["person"].get("about_you") or {}).get("goals") or [])}
    w.apps = ["Calendar", "Finder", "Mail", "Messages", "Notes", "Safari", "Harbor"]
    w.mac = {"volume": 40, "muted": False, "locked": False,
             "media": {"music": "paused"}, "front": "Mail"}
    w.snapshot()
    return w
