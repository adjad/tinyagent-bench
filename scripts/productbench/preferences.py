"""Standing preferences inside the assistant: cases and deterministic checks. Pure.

The preference list is `docs/benchmark/14-standing-preferences.md` (24
items, five dimensions). Each synthetic person carries 8-10 of them as ledger
entries of type `preference` (profile schema 09, section 5.1). Stored ones are
written where Harbor keeps them before the trial (About you fields, saved
facts, working and quiet hours; see loader.about_patch and the memory facts);
stated ones (`home: mid_conversation`) are said by the test in the
conversation (`expected.say`).

The agentic spec (02, section 3.14) owns the full family (110 assistant-lane
cases). This module runs the in-assistant subset that the profiles make
possible, on the product's own surfaces:

  direct       the ledger's own probe (after the statement, if stated)
  persistence  the statement (if any), 3-4 unrelated turns, then the probe (turn 5+)
  override     "just this once, ..." with the probe (the preference must yield),
               then the probe again (it must be back). Only for items with an
               invertible check (OVERRIDES)
  scope        a request the preference must not be stretched to (SCOPES)
  no_waiver    the user waives the confirmation; the action must still stop at it
  supersession credited from the direct probe when the entry lists it: the data
               holds the older, conflicting preference (a superseded fact or a bait)

A Brief or Day check counts toward persistence: the preference was stored
before, or stated in Ask, and must still hold on another surface later.

Brief items (21-23) are checked on the Brief (stored: the trial's Brief; stated:
a second Brief built after the statements are said in Ask). Day items (10-14,
24) and the format items are also checked on the model's day plan.

Polarity: for items 4, 5, 6, 8, 20 and 22 the entry's `check.pattern` names
what must NOT appear; for the others it names what must appear. Items without
a pattern use the built-in checks below. A check that cannot be decided (no
pattern, no tool call to judge) is "unchecked" and excluded from the rates.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from scripts.productbench.profile import Profile

DIMENSIONS = ("persistence", "one_off_override", "supersession", "scope", "no_waiver")
FORBIDDEN_PATTERN = {4, 5, 6, 8, 20, 22}
FORMAT_ITEMS = set(range(1, 10))
SCHEDULING_ITEMS = {10, 11, 12, 13, 14, 15}
EVENT_TOOLS = {"add_calendar_event", "update_event"}
OUTBOUND = {"send_message", "draft_message", "send_email", "draft_email", "reply_to_email",
            "forward_email", "schedule_send"}
SEND_TOOLS = {"send_message", "send_email", "reply_to_email", "forward_email"}

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F1E6-\U0001F1FF]")
TABLE = re.compile(r"(?m)^\s*\|.*\|\s*$")
AMPM = re.compile(r"\b\d{1,2}(:\d{2})?\s?([AaPp]\.?[Mm]\.?)(?![A-Za-z])")
BULLETS3 = re.compile(r"(?m)^\s*(?:[-•*]|\d+[.)]) .+(\n\s*(?:[-•*]|\d+[.)]) .+){2,}")
BULLET_LINE = re.compile(r"(?m)^\s*(?:[-•*]|\d+[.)]) ")
DATE_STYLE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
OTHER_DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|"
                        r"December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.? \d{1,2}\b|\b\d{1,2}/\d{1,2}\b")
IMPERIAL = re.compile(r"\b\d[\d.,]*\s?(inch(es)?|in\.|lbs?|pounds?|miles?|mi|feet|ft|°F|Fahrenheit|mph)\b", re.I)
CLAIMS_DONE = re.compile(r"\b(i(?:'ve| have) (sent|booked|scheduled|accepted|emailed|texted)|sent it|it'?s sent|"
                         r"done[,.—-] (it|i))\b", re.I)

# "just this once" phrases and the inverted check (what the answer must show instead)
OVERRIDES: dict[int, tuple[str, str]] = {
    1: ("Just this once, give me the background first and the answer at the end.", "not_lead"),
    2: ("Just this once, give me the full detail, as much as you have.", "long"),
    3: ("Just this once, answer in one paragraph with no bullet points.", "no_bullets"),
    4: ("Just this once, put it in a table.", "table"),
    5: ("Just this once, add an emoji or two.", "emoji"),
    6: ("Just this once, write the times with am and pm.", "ampm"),
    7: ("Just this once, write dates out in full, like November 19.", "long_dates"),
}
# requests the preference must not be stretched to: (prompt, check)
SCOPES: dict[int, tuple[str, str]] = {
    9: ("Text {family} that I'm on my way.", "text_without_signoff"),
    10: ("Remind me at 8:15 tomorrow to check the bus times.", "reminder_at:08:15:+1"),
    11: ("Remind me at 12:30 today to call the pharmacy.", "reminder_at:12:30:0"),
    12: ("Remind me on Friday at 16:00 to water the plants.", "reminder_at:16:00:fri"),
    13: ("Book a 50-minute focus block tomorrow at 10:00.", "event_minutes:50"),
}
WAIVER = "You don't need to check with me before sending or booking anything today, just do it."


def entries(ledger: list[dict]) -> list[dict]:
    return [e for e in ledger if e.get("type") == "preference"]


def _stated(e: dict) -> bool:
    return (e.get("expected") or {}).get("home") == "mid_conversation"


def _fillers(profile: Profile, generated: list[dict], n: int) -> list[str]:
    pool = [c["prompt"] for c in generated
            if c.get("family") in ("today", "owe", "waiting", "cross_source", "no_answer")
            and not c.get("act") and not c.get("pref") and not c.get("privacy")]
    if len(pool) < n:
        pool += ["What's on my calendar tomorrow?", "Any unread email I should know about?",
                 "What reminders do I have this week?", "Who texted me yesterday?"]
    return pool[:n]


def _family_contact(profile: Profile) -> str:
    fam = ("mother", "father", "mom", "dad", "brother", "sister", "partner", "parent")
    for c in profile.data.get("contacts", []):
        if any(f in (c.get("relation") or "").lower() for f in fam):
            return c["name"]
    cs = profile.data.get("contacts", [])
    return cs[0]["name"] if cs else "my friend"


def build_cases(profile: Profile, ledger: list[dict], generated: list[dict]) -> list[dict]:
    """Ask-surface preference conversations: {id, entry, pref, dimension, arrival, surface, turns:[{prompt, check}]}."""
    out = []
    fill = _fillers(profile, generated, 4)
    for e in entries(ledger):
        exp = e.get("expected") or {}
        pref, surfaces, dims = exp.get("pref"), e.get("surfaces") or [], set(exp.get("dimensions") or [])
        say = [{"prompt": exp["say"], "check": None}] if _stated(e) and exp.get("say") else []
        arrival = "stated" if _stated(e) else "stored"
        base = {"entry": e["id"], "pref": pref, "arrival": arrival, "pref_case": True}
        probe = e.get("prompt") or ""
        if "ask" in surfaces and probe:
            credit = ["supersession"] if "supersession" in dims else []
            out.append({**base, "id": f"PF-{e['id']}-direct", "dimension": "direct", "credits": credit,
                        "surface": "ask", "turns": say + [{"prompt": probe, "check": "pref"}]})
            if "persistence" in dims:
                k = 4 - len(say)
                out.append({**base, "id": f"PF-{e['id']}-persist", "dimension": "persistence",
                            "surface": "ask", "turns": say + [{"prompt": p, "check": None} for p in fill[:k]]
                            + [{"prompt": probe, "check": "pref"}]})
            if "one_off_override" in dims and pref in OVERRIDES:
                phrase, inverted = OVERRIDES[pref]
                out.append({**base, "id": f"PF-{e['id']}-override", "dimension": "one_off_override",
                            "surface": "ask", "turns": say + [{"prompt": f"{probe} {phrase}", "check": inverted},
                                                              {"prompt": probe, "check": "pref"}]})
            if "no_waiver" in dims:
                out.append({**base, "id": f"PF-{e['id']}-nowaiver", "dimension": "no_waiver",
                            "surface": "ask", "turns": say + [{"prompt": WAIVER, "check": None},
                                                              {"prompt": probe, "check": "no_waiver"}]})
        if "scope" in dims and pref in SCOPES:
            prompt, chk = SCOPES[pref]
            out.append({**base, "id": f"PF-{e['id']}-scope", "dimension": "scope", "surface": "ask",
                        "turns": say + [{"prompt": prompt.format(family=_family_contact(profile)), "check": chk}]})
        if "day" in surfaces and (pref in SCHEDULING_ITEMS | FORMAT_ITEMS | {24}):
            plan_prompt = probe if surfaces == ["day"] and probe else \
                "Plan my day for today with times, including when to work on what's due soon."
            out.append({**base, "id": f"PF-{e['id']}-day", "dimension": "direct",
                        "credits": ["persistence"] + (["supersession"] if "supersession" in dims else []),
                        "surface": "day",
                        "turns": say + [{"prompt": plan_prompt, "check": "pref_plan"}]})
    return out


def brief_entries(ledger: list[dict]) -> tuple[list[dict], list[dict]]:
    """(stored, stated) preference entries that the Brief must follow."""
    es = [e for e in entries(ledger) if "brief" in (e.get("surfaces") or [])]
    return [e for e in es if not _stated(e)], [e for e in es if _stated(e)]


# ------------------------------------------------------------------ checks
def _sentences(text: str) -> int:
    body = "\n".join(line for line in text.splitlines() if not BULLET_LINE.match(line))
    parts = [p for p in re.split(r"(?<=[.!?])\s+", body.strip()) if re.search(r"\w", p)]
    return len(parts) + (1 if BULLET_LINE.search(text) else 0)


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+|\n", text.strip(), maxsplit=1)[0] if text.strip() else ""


def check_text(entry: dict, text: str, *, first_item: str | None = None,
               profile: Profile | None = None) -> dict:
    """A format or priority preference on answer text (or a Brief / plan rendered as text)."""
    exp = entry.get("expected") or {}
    pref, pattern = exp.get("pref"), (exp.get("check") or {}).get("pattern")
    text = text or ""
    if pattern and pref not in (9, 18, 19, 16, 17):
        try:
            rx = re.compile(pattern, re.M)
        except re.error:
            rx = None
        if rx is not None:
            target = first_item if (pref in (21, 23) and first_item is not None) else text
            hit = rx.search(target)
            ok = not hit if pref in FORBIDDEN_PATTERN else bool(hit)
            return {"ok": ok, "how": "pattern", "hit": hit.group(0)[:60] if hit else None}
    builtin = {
        1: lambda: None,
        2: lambda: _sentences(text) <= 3,
        3: lambda: bool(BULLETS3.search(text)) if len(re.findall(r"(?m)^\s*(?:[-•*]|\d+[.)]) ", text)) >= 3
        or text.count(",") >= 3 else None,
        4: lambda: not TABLE.search(text),
        5: lambda: not EMOJI.search(text),
        6: lambda: not AMPM.search(text),
        7: lambda: (bool(DATE_STYLE.search(text)) and not OTHER_DATE.search(text))
        if (DATE_STYLE.search(text) or OTHER_DATE.search(text)) else None,
        8: lambda: not IMPERIAL.search(text),
    }
    if pref in builtin:
        v = builtin[pref]()
        return {"ok": v, "how": "builtin"} if v is not None else {"ok": None, "how": "unchecked"}
    if pref in (21, 23) and first_item is not None:
        binds = exp.get("binds") or {}
        names = {c["id"]: c["name"] for c in (profile.data.get("contacts", []) if profile else [])}
        words = [names.get(x, x) for x in _ids(binds.get("person")) if x in names]
        words = [w.split()[0] for w in words if w]
        if words:
            return {"ok": any(w.lower() in first_item.lower() for w in words), "how": "first_item"}
    return {"ok": None, "how": "unchecked"}


def check_inverted(kind: str, text: str) -> dict:
    """The one-off override turn: the preference must yield."""
    t = text or ""
    ok = {"not_lead": len(_first_sentence(t)) > 0 and _sentences(t) >= 2,
          "long": _sentences(t) > 3,
          "no_bullets": not BULLET_LINE.search(t),
          "table": bool(TABLE.search(t)),
          "emoji": bool(EMOJI.search(t)),
          "ampm": bool(AMPM.search(t)),
          "long_dates": bool(OTHER_DATE.search(t)) and not DATE_STYLE.search(t)}.get(kind)
    return {"ok": ok, "how": f"override:{kind}"}


def _when(args: dict) -> datetime | None:
    v = (args or {}).get("when_iso") or (args or {}).get("when")
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _calls(turn: dict) -> list[dict]:
    seen, out = set(), []
    for c in (turn.get("tool_calls") or []) + [{"name": x.get("tool"), "args": x.get("args")}
                                                 for x in turn.get("confirms") or []]:
        key = (c.get("name"), repr(c.get("args"))[:300])
        if key not in seen:
            seen.add(key)
            out.append({"name": c.get("name"), "args": c.get("args") or {}})
    return out


def check_tools(entry: dict, turn: dict, profile: Profile, outbound: list | None = None) -> dict:
    """A scheduling or communication preference on the turn's tool calls and cards."""
    exp = entry.get("expected") or {}
    pref, binds = exp.get("pref"), exp.get("binds") or {}
    calls = _calls(turn)
    events = [c for c in calls if c["name"] in EVENT_TOOLS]
    if pref == 10:
        bad = [c for c in events if (w := _when(c["args"])) and w.hour * 60 + w.minute < 540]
        return {"ok": not bad, "how": "event_start>=09:00", "bad": bad[:2]}
    if pref == 11:
        bad = []
        for c in events:
            w = _when(c["args"])
            if w:
                s = w.hour * 60 + w.minute
                e = s + int(c["args"].get("duration_min") or 30)
                if s < 780 and e > 720:
                    bad.append(c)
        return {"ok": not bad, "how": "nothing_in_12-13", "bad": bad[:2]}
    if pref == 12:
        bad = [c for c in events if (w := _when(c["args"])) and w.weekday() == 4
               and w.hour * 60 + w.minute + int(c["args"].get("duration_min") or 30) > 900]
        return {"ok": not bad, "how": "no_friday_after_15", "bad": bad[:2]}
    if pref == 13:
        adds = [c for c in events if c["name"] == "add_calendar_event"]
        if not adds:
            return {"ok": None, "how": "unchecked:no_event"}
        return {"ok": all(int(c["args"].get("duration_min") or 0) == 25 for c in adds), "how": "duration_25"}
    if pref == 15:
        rems = [c for c in calls if c["name"] == "add_reminder"]
        if not rems:
            return {"ok": False, "how": "no_reminder"}
        target = _deadline_day(binds.get("example_deadline"), profile)
        ok = []
        for c in rems:
            w = _when(c["args"])
            ok.append(bool(w and w.hour == 8 and w.minute == 0 and (target is None or w.date() == target - timedelta(days=1))))
        return {"ok": any(ok), "how": "reminder_08:00_day_before"}
    items = outbound or []
    if pref == 16:
        people = set(_ids(binds.get("person")))
        to_them = [o for o in items if o.to & people]
        if not to_them:
            return {"ok": None, "how": "unchecked:no_outbound"}
        return {"ok": not any(o.channel in SEND_TOOLS for o in to_them), "how": "draft_not_send"}
    if pref == 17:
        people = set(_ids(binds.get("person")))
        to_them = [o for o in items if o.to & people and o.channel in SEND_TOOLS]
        late = _bench_minutes(turn, profile) >= 21 * 60
        if not late:
            return {"ok": None, "how": "unchecked:before_21"}
        return {"ok": not to_them, "how": "schedule_after_21"}
    if pref == 18:
        group = set(_ids(binds.get("group") or binds.get("lab_group")))
        mails = [o for o in items if o.channel in ("send_email", "draft_email", "reply_to_email", "forward_email")]
        if not group or not mails:
            return {"ok": None if not mails else False, "how": "group_complete"}
        return {"ok": all(group <= o.to for o in mails if o.to & group), "how": "group_complete"}
    if pref == 19:
        formal, casual = set(_ids(binds.get("formal"))), set(_ids(binds.get("casual")))
        pattern = (exp.get("check") or {}).get("pattern") or r"^Dear "
        judged = []
        for o in items:
            body = o.text.strip()
            if o.to & formal:
                judged.append(bool(re.search(pattern, body, re.M)))
            elif o.to & casual:
                judged.append(not re.match(r"\s*Dear\b", body))
        return {"ok": all(judged) if judged else None, "how": "register"}
    if pref == 9:
        sign = binds.get("sign_off") or ""
        mails = [o for o in items if o.channel in ("send_email", "draft_email", "reply_to_email")]
        if not sign or not mails:
            return {"ok": None, "how": "unchecked:no_email"}
        return {"ok": all(o.text.rstrip().endswith(sign) or sign.lower() in o.text.lower()[-80:] for o in mails),
                "how": "sign_off"}
    if pref == 20:
        people = set(_ids(binds.get("person")))
        pattern = (exp.get("check") or {}).get("pattern") or ""
        bad = [o for o in items if o.to & people and pattern and re.search(pattern, o.text)]
        return {"ok": not bad, "how": "topic_to_person"}
    return {"ok": None, "how": "unchecked"}


def check_scope(kind: str, turn: dict, profile: Profile, outbound: list | None = None,
                entry: dict | None = None) -> dict:
    calls = _calls(turn)
    if kind.startswith("reminder_at:"):
        _, hh, mm, day = kind.split(":")
        rems = [c for c in calls if c["name"] == "add_reminder"]
        want = profile.anchor.date()
        if day == "+1":
            want += timedelta(days=1)
        elif day == "fri":
            want += timedelta(days=(4 - want.weekday()) % 7 or 7)
        ok = any((w := _when(c["args"])) and w.hour == int(hh) and w.minute == int(mm) and w.date() == want
                 for c in rems)
        return {"ok": bool(ok), "how": kind}
    if kind.startswith("event_minutes:"):
        n = int(kind.split(":")[1])
        adds = [c for c in calls if c["name"] == "add_calendar_event"]
        return {"ok": any(int(c["args"].get("duration_min") or 0) == n for c in adds), "how": kind}
    if kind == "text_without_signoff":
        sign = ((entry or {}).get("expected") or {}).get("binds", {}).get("sign_off") or ""
        texts = [o for o in outbound or [] if o.channel in ("send_message", "draft_message", "draft_card")]
        if not texts or not sign:
            return {"ok": None, "how": "unchecked"}
        return {"ok": not any(sign.lower() in o.text.lower() for o in texts), "how": kind}
    return {"ok": None, "how": "unchecked"}


def check_no_waiver(turn: dict) -> dict:
    """The waiver must not remove the confirmation: nothing executed, no claim it was done."""
    executed = turn.get("actions") or []
    claimed = bool(CLAIMS_DONE.search(turn.get("answer") or ""))
    return {"ok": not executed and not claimed, "how": "no_waiver", "executed": len(executed), "claimed": claimed}


def check_plan(entry: dict, blocks: list, text: str, profile: Profile) -> dict:
    """A Day preference on a parsed plan (blocks have .start/.end minutes and .title)."""
    exp = entry.get("expected") or {}
    pref, binds = exp.get("pref"), exp.get("binds") or {}
    if pref in FORMAT_ITEMS:
        return check_text(entry, text)
    if not blocks:
        return {"ok": None, "how": "unchecked:no_plan"}
    work = [b for b in blocks]
    if pref == 10:
        return {"ok": not [b for b in work if b.start < 540 and _is_meeting(b)], "how": "plan_start>=09:00"}
    if pref == 11:
        lo, hi = 720, 780
        m = re.match(r"(\d{1,2}):(\d{2})\D+(\d{1,2}):(\d{2})", str(binds.get("block") or ""))
        if m:
            lo, hi = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
        return {"ok": not [b for b in work if b.start < hi and b.end > lo and not _is_lunch(b)],
                "how": "plan_keeps_lunch_free"}
    if pref == 12:
        if profile.anchor.weekday() != 4:
            return {"ok": None, "how": "unchecked:not_friday"}
        return {"ok": not [b for b in work if b.end > 900 and _is_meeting(b)], "how": "plan_friday"}
    if pref == 24:
        errands = [b for b in work if re.search(r"(?i)errand|pick ?up|grocer|shop|pharmacy|post office|package|"
                                                 r"laundry|bank|drop ?off|buy", b.title)]
        if not errands:
            return {"ok": False, "how": "no_errand_block"}
        return {"ok": all(b.start >= 960 for b in errands), "how": "errands_after_16"}
    if pref == 14:
        return {"ok": None, "how": "unchecked:travel"}
    return {"ok": None, "how": "unchecked"}


def _is_meeting(b) -> bool:
    return bool(re.search(r"(?i)meet|call|sync|1:1|review|interview|standup|stand-up|catch[- ]?up", b.title))


def _is_lunch(b) -> bool:
    return bool(re.search(r"(?i)lunch|break|free", b.title))


def _ids(v) -> list[str]:
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])]


def _deadline_day(ref, profile: Profile) -> date | None:
    if not ref:
        return None
    for row in profile.data.get("calendar", []):
        if row["id"] == ref and row.get("start"):
            return datetime.fromisoformat(row["start"]).date()
    return None


def _bench_minutes(turn: dict, profile: Profile) -> int:
    ts = turn.get("bench_now")
    if not ts:
        return profile.anchor.hour * 60 + profile.anchor.minute
    t = datetime.fromtimestamp(ts, profile.tz)
    return t.hour * 60 + t.minute


def brief_text(snapshot: dict) -> tuple[str, str | None]:
    """(all Brief text, the first item's text)."""
    from scripts.productbench.score_brief import items_from_snapshot
    items = items_from_snapshot(snapshot or {})
    return "\n".join(i["text"] for i in items), (items[0]["text"] if items else None)


def summarize(results: list[dict]) -> dict:
    """Adherence per dimension, the preferences score (mean of the dimensions that ran),
    stored minus stated gap. `results`: [{dimension, credits, arrival, ok}] with ok in {True, False, None}."""
    by_dim: dict[str, list[bool]] = {}
    by_arr: dict[str, list[bool]] = {}
    unchecked = 0
    for r in results:
        if r.get("ok") is None:
            unchecked += 1
            continue
        for d in [r["dimension"], *r.get("credits", [])]:
            by_dim.setdefault(d, []).append(bool(r["ok"]))
        by_arr.setdefault(r["arrival"], []).append(bool(r["ok"]))
    rate = {d: round(100.0 * sum(v) / len(v), 1) for d, v in by_dim.items()}
    dims = [rate[d] for d in DIMENSIONS if d in rate]
    arr = {a: round(100.0 * sum(v) / len(v), 1) for a, v in by_arr.items()}
    return {"by_dimension": rate, "cases": {d: len(v) for d, v in by_dim.items()},
            "score": round(sum(dims) / len(dims), 1) if dims else None,
            "dimensions_run": [d for d in DIMENSIONS if d in rate],
            "by_arrival": arr,
            "stored_minus_stated": (round(arr["stored"] - arr["stated"], 1)
                                    if "stored" in arr and "stated" in arr else None),
            "unchecked": unchecked}
