"""Known capture faults: inputs the harness or the sandbox world got wrong, not the model.

A record carrying one of these is not evidence about the model on that point. The runner stamps
`known_faults` on each record as it is written; the findings page reads them back (and finds the
same faults in older records from their captured tool results, so earlier runs are marked too).

  mail_texts_unanalysed     a tool told the model mail and texts were not analysed yet: a home
                            that asked before any Brief cycle had read them (fixed: each such
                            home now runs one unscored Brief first)
  reminder_receipt_untrusted  a reminder write returned no receipt Harbor trusts: the sandbox's
                            reminders writer, not the model (open)
  availability_not_ready    find_free_time said Calendar and Reminders were not ready in the
                            sandbox (open)
  sandbox_allowed_refused_write  the sandbox let a move or cancel succeed that a real Mac
                            refuses: an all-day event, one organised by someone else, one with
                            invitees and an unknown organiser, one on a read-only calendar, or a
                            move onto another occurrence of the same series. Decided from the
                            profile's event fields and the action's target (`refused_writes`),
                            so it needs the profile: the runner, the re-score and the findings
                            page stamp it (`stamp_writes`)

Run-level gaps (a surface not run, a fault that touches every record of one surface) go in the
run's meta.json under `known_gaps`; `run_gaps(meta)` adds the ones a rule decides:

  native_writes_unanswered  before the sandbox answered Calendar and Reminders write events, an
                            unanswered event replayed on every event-stream reconnect, so a
                            record's logged `actions` can repeat an earlier native write many
                            times. Every run is assumed to be from before that fix unless its
                            meta.json says `"sandbox_answers_native_writes": true` (the runner
                            that carries the fix writes it). For such a run, `drop_native_replays`
                            keeps a native write only when its action id appears once in the
                            run's whole record set.
"""
from __future__ import annotations

import json

SIGNS = {
    "mail_texts_unanalysed": "have not been analyzed yet",
    "reminder_receipt_untrusted": "did not return a verified completion receipt",
    "availability_not_ready": "could not check current Calendar and Reminders availability",
}

ABOUT = {
    "sandbox_allowed_refused_write": "sandbox allowed a write a Mac would refuse (a move or cancel of an "
                                     "all-day event, someone else's event, one with invitees and an unknown "
                                     "organiser, one on a read-only calendar, or a move onto another "
                                     "occurrence); no result should lean on it",
    "mail_texts_unanalysed": "the assistant had not yet analysed mail and texts in this trial home "
                             "(no Brief cycle ran before the question); the model could not see changes "
                             "announced only there",
    "reminder_receipt_untrusted": "a reminder write got no receipt Harbor trusts from the sandbox, so the "
                                  "model was told it failed",
    "availability_not_ready": "Harbor reported Calendar and Reminders as not ready in the sandbox, so free "
                              "time could not be checked",
}

GAPS = {
    "native_writes_unanswered": "native writes unanswered in the sandbox; logged actions may repeat",
    "act_not_run": "act for me was not run: in the sandbox, saved contacts are not recognised in texts "
                   "(the Messages feed carries no chat id or service), so it would measure the sandbox",
}


def _turns(captured: dict) -> list[dict]:
    if not isinstance(captured, dict):
        return []
    if isinstance(captured.get("turns"), list):
        return [t for t in captured["turns"] if isinstance(t, dict)]
    return [captured]


def of_record(rec: dict) -> list[str]:
    """The known faults this record's own captured tool results show."""
    found = set()
    for turn in _turns(rec.get("captured") or {}):
        for call in turn.get("tool_calls") or []:
            text = json.dumps(call.get("result"), ensure_ascii=False) if isinstance(call, dict) else ""
            for fault, sign in SIGNS.items():
                if sign in text:
                    found.add(fault)
    return sorted(found)


def stamp(rec: dict) -> dict:
    faults = sorted(set(rec.get("known_faults") or []) | set(of_record(rec)))
    if faults:
        rec["known_faults"] = faults
    return rec


# ------------------------------------------------------------------ native write replays
NATIVE_FIXED_MARK = "sandbox_answers_native_writes"
_NATIVE_VERBS = ("create_", "update_", "delete_", "complete_", "cancel_", "move_", "add_")


def is_native_write(action: dict) -> bool:
    """A Calendar or Reminders write the sandbox logs (create_reminder, create_calendar_event, ...)."""
    t = str(action.get("type") or "")
    return t.startswith(_NATIVE_VERBS) and ("reminder" in t or "calendar" in t or t.endswith("_event"))


def run_gaps(meta: dict) -> dict:
    """The run's known gaps: those its meta.json lists, plus the ones decided by rule."""
    gaps = dict(meta.get("known_gaps") or {})
    if not meta.get(NATIVE_FIXED_MARK):
        gaps.setdefault("native_writes_unanswered", GAPS["native_writes_unanswered"])
    return gaps


def _action_lists(rec: dict) -> list[list]:
    cap = rec.get("captured") or {}
    lists = [t["actions"] for t in _turns(cap) if isinstance(t.get("actions"), list)]
    if isinstance(cap.get("background_writes"), list):
        lists.append(cap["background_writes"])
    return lists


def native_counts(records: list[dict]) -> dict[str, int]:
    """How often each native write's action id appears across a run's records."""
    counts: dict[str, int] = {}
    for rec in records:
        for actions in _action_lists(rec):
            for a in actions:
                if isinstance(a, dict) and is_native_write(a) and a.get("action_id"):
                    counts[str(a["action_id"])] = counts.get(str(a["action_id"]), 0) + 1
    return counts


def drop_native_replays(records: list[dict], counts: dict[str, int] | None = None) -> tuple[list[dict], int]:
    """Copies of the records with every native write whose action id appears more than once in
    the run's record set taken out of their logged actions (a replay, not a new write; the
    first copy goes too, since nothing tells it apart). Returns (records, writes dropped)."""
    counts = native_counts(records) if counts is None else counts
    out, dropped = [], 0
    for rec in records:
        rec = json.loads(json.dumps(rec))
        n = 0
        for actions in _action_lists(rec):
            keep = [a for a in actions if not (isinstance(a, dict) and is_native_write(a)
                                                and counts.get(str(a.get("action_id")), 0) > 1)]
            n += len(actions) - len(keep)
            actions[:] = keep
        if n:
            rec["native_replays_dropped"] = n
        dropped += n
        out.append(rec)
    return out, dropped


# ------------------------------------------------------------------ writes a Mac would refuse
CALENDAR_CHANGES = {"move_calendar_event", "delete_calendar_event", "update_calendar_event",
                    "cancel_calendar_event"}


def refused_reason(action: dict, events: dict[str, dict]) -> str | None:
    """Why a real Mac would have refused this successful move or cancel (None: it would not).
    `events`: the profile's event fields by id (shared.adapt `event_flags`, or calendar rows)."""
    if not isinstance(action, dict) or action.get("type") not in CALENDAR_CHANGES or action.get("ok") is False:
        return None
    p = action.get("payload") or action.get("args") or {}
    ev = events.get(str(p.get("source_id") or p.get("event_id") or ""))
    if not ev:
        return None
    if ev.get("all_day"):
        return "all-day event"
    if ev.get("calendar_writable") is False:
        return "read-only calendar"
    mine = ev.get("organizer_is_user")
    if mine is False:
        return "organised by someone else"
    if ev.get("invitee_count") and mine is None:
        return "invitees and an unknown organiser"
    if action.get("type") in ("move_calendar_event", "update_calendar_event") and p.get("when_ts") and ev.get("start"):
        from datetime import datetime
        try:
            new = float(p["when_ts"])
            length = ((datetime.fromisoformat(ev["end"]) - datetime.fromisoformat(ev["start"])).total_seconds()
                      if ev.get("end") else 3600.0)
        except (TypeError, ValueError):
            return None
        tz = action.get("_tz")
        for oid, other in events.items():
            if oid == str(p.get("source_id")) or not ev.get("series_id") or other.get("series_id") != ev["series_id"]:
                continue
            if not other.get("start") or not other.get("end"):
                continue
            o_start = datetime.fromisoformat(other["start"])
            o_end = datetime.fromisoformat(other["end"])
            if tz is not None:
                o_start, o_end = o_start.replace(tzinfo=tz), o_end.replace(tzinfo=tz)
            if new < o_end.timestamp() and new + length > o_start.timestamp():
                return f"move clashes with another occurrence ({oid})"
    return None


def refused_writes(rec: dict, events: dict[str, dict], tz=None) -> list[dict]:
    out = []
    for actions in _action_lists(rec):
        for a in actions:
            why = refused_reason({**a, "_tz": tz} if isinstance(a, dict) else a, events)
            if why:
                p = a.get("payload") or {}
                out.append({"type": a.get("type"), "event": p.get("source_id") or p.get("event_id"), "why": why})
    return out


def stamp_writes(rec: dict, profile) -> dict:
    """Stamp `sandbox_allowed_refused_write` when the record's logged actions show such a write."""
    data = getattr(profile, "data", None) or {}
    events = data.get("event_flags") or {e["id"]: e for e in data.get("calendar") or []}
    hits = refused_writes(rec, events, getattr(profile, "tz", None))
    if hits:
        rec["known_faults"] = sorted(set(rec.get("known_faults") or []) | {"sandbox_allowed_refused_write"})
        rec["refused_writes"] = hits
    return rec
