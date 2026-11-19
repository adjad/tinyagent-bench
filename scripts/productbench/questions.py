"""The person's own questions about their day, scored item by item. Pure.

  todo      "What's on my to-do list?": every open promise, request and deadline must be
            there; done, superseded and other people's items must not be shown as open
  inbox     "What's in my email?": the mail that matters, without promotions and stale mail
  messages  "What's in my messages?": the same for texts
  window    "Is there anything in the next 3 hours?", asked with the clock pinned at a time
            of day: what is truly on, with changes announced only in mail or messages applied

The keys come from the profile's ledger (09-profile-schema sections 5.4 to 5.6: `todo_list`,
`inbox_highlights`, `messages_highlights`, `time_window`). Without them, `fallback_*` builds a
weaker key from the profile (marked `approximate`): to-dos from the commitment, request and
deadline entries; inbox and messages from the last day's unread, non-bulk items; windows from
the calendar as it stands (no corrections).

Each item gets an outcome: found, missed, wrongly_raised (shown though it is not the person's
or not a task), or stale (a done or superseded item shown as open, a moved or cancelled event
at its old time). Every outcome carries the line of the answer that decided it.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from scripts.productbench import matcher
from scripts.productbench.profile import Profile

_STOP = {"with", "from", "your", "this", "that", "they", "them", "about", "before", "after", "today",
         "tomorrow", "tonight", "still", "into", "have", "will", "should", "their", "there", "what",
         "when", "where", "which", "would", "could", "back", "just", "also", "need", "needs", "here",
         "every", "some", "more", "than", "then", "only", "over", "very", "being", "been"}
_NOTHING = re.compile(r"\b(nothing|no (?:events?|meetings?|plans?)|(?:you're|you are) (?:free|clear)|"
                      r"(?:don't|do not) have anything|clear (?:schedule|calendar)|free until)\b", re.I)
_CHANGE = re.compile(r"\b(moved|cancel+ed|instead|no longer|rescheduled|not (?:today|happening)|now (?:at|on|in))\b",
                     re.I)
_BULK = re.compile(r"(no-?reply|newsletter|news@|promo|deals|offers|marketing|notifications?@|digest)", re.I)
FAMILIES = ("todo", "inbox", "messages", "window")
PROMPTS = {"todo": "What's on my to-do list?", "inbox": "What's in my email?",
           "messages": "What's in my messages?", "window": "Is there anything in the next 3 hours?"}


# ------------------------------------------------------------------ matching
def words(text: str | None) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9'.:]*[a-z0-9%]|[a-z0-9]", matcher.fold(text or "")):
        w = w.strip("'.:")
        if any(c.isdigit() for c in w):
            if ":" in w or "." in w or len(w) >= 3:
                out.append(w)
        elif len(w) >= 4 and w not in _STOP:
            out.append(w)
    return list(dict.fromkeys(out))


def _has(word: str, text: str) -> bool:
    stem = word[:6] if len(word) > 6 and not word[0].isdigit() else word
    return re.search(rf"(?<![a-z0-9:.]){re.escape(stem)}", text) is not None


def word_hit(phrase: str, text: str, share: float = 0.5) -> bool:
    """Most of the phrase's distinctive words appear in the text, or one of its numbers (a time,
    an amount) together with one of its words."""
    ws = words(phrase)
    if not ws:
        return False
    t = matcher.fold(text)
    nums = [w for w in ws if any(c.isdigit() for c in w)]
    rest = [w for w in ws if w not in nums]
    hits = sum(1 for w in rest if _has(w, t))
    if any(_has(n, t) for n in nums) and (hits or not rest):
        return True
    if not rest:
        return False
    return hits >= max(1, math.ceil(share * len(rest)))


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|(?:^|\s)[-*•]\s+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def line_for(text: str, test) -> str | None:
    for s in sentences(text):
        if test(s):
            return s[:240]
    return None


def _anchors_of(profile: Profile, refs: list[str]) -> list[str]:
    out: list[str] = []
    for r in refs or []:
        try:
            out += profile.item(r).get("anchors") or []
        except KeyError:
            pass
    return out


def _item_test(profile: Profile, item: dict):
    anchors = list(item.get("anchors") or []) + _anchors_of(profile, item.get("refs") or [])
    today = profile.anchor.date()

    def test(s: str) -> bool:
        return word_hit(item.get("title") or item.get("point") or "", s) or bool(
            anchors and matcher.any_hit(anchors, s, today) and words(item.get("title") or item.get("point"))
            and any(_has(w, matcher.fold(s)) for w in words(item.get("title") or item.get("point"))))
    return test


# ------------------------------------------------------------------ keys
def _entry(profile: Profile, etype: str) -> dict | None:
    return next((e for e in profile.data.get("edat_entries") or [] if e.get("type") == etype), None)


def _at(profile: Profile, due: dict | None) -> str | None:
    if not due or due.get("day") is None:
        return None
    d = profile.anchor.date() + timedelta(days=int(due["day"]))
    return f"{d.isoformat()}" + (f"T{due['time']}" if due.get("time") else "")


def todo_key(profile: Profile) -> dict:
    e = _entry(profile, "todo_list")
    if e:
        x = e.get("expected") or {}
        return {"source": e["id"], "approximate": False, "prompt": e.get("prompt") or PROMPTS["todo"],
                "include": [{**i, "due": _at(profile, i.get("due"))} for i in x.get("include") or []],
                "exclude": [{**i, "due": _at(profile, i.get("due"))} for i in x.get("exclude") or []],
                "must_include": list(x.get("must_include") or []),
                "must_not_include": list(x.get("must_not_include") or [])}
    return fallback_todo(profile)


def fallback_todo(profile: Profile) -> dict:
    kinds = {"commitment_user": "promised", "request_to_user": "asked", "deadline": "deadline"}
    inc, exc = [], []
    for it in profile.ledger:
        k = kinds.get(it.get("edat_type") or "")
        if not k:
            continue
        row = {"title": it["title"], "kind": k, "state": it.get("status") or "open", "refs": [it["id"]],
               "anchors": it.get("anchors") or []}
        (inc if row["state"] == "open" else exc).append(row)
    return {"source": "ledger entries", "approximate": True, "prompt": PROMPTS["todo"], "include": inc,
            "exclude": exc, "must_include": [], "must_not_include": []}


def _ref_text(profile: Profile, ref: str) -> str:
    for e in profile.data.get("emails", []):
        if e["id"] == ref:
            return f"{e.get('from_name', '')} {e.get('subject', '')}"
    for t in profile.data.get("threads", []):
        for m in t.get("messages", []):
            if m.get("id") == ref:
                return m.get("text") or ""
    return ""


def highlights_key(profile: Profile, which: str) -> dict:
    e = _entry(profile, f"{'inbox' if which == 'inbox' else 'messages'}_highlights")
    if e:
        x = e.get("expected") or {}
        return {"source": e["id"], "approximate": False, "prompt": e.get("prompt") or PROMPTS[which],
                "mention": [{**m, "about": _ref_text(profile, m.get("ref", ""))} for m in x.get("mention") or []],
                "leave_out": [{**m, "about": _ref_text(profile, m.get("ref", ""))} for m in x.get("leave_out") or []],
                "must_include": list(x.get("must_include") or []),
                "must_not_include": list(x.get("must_not_include") or [])}
    return fallback_highlights(profile, which)


def fallback_highlights(profile: Profile, which: str, hours: int = 24) -> dict:
    now = profile.anchor.replace(tzinfo=None)
    lo = now - timedelta(hours=hours)
    mention, leave = [], []
    if which == "inbox":
        for e in profile.data.get("emails", []):
            t = datetime.fromisoformat(e["received"])
            if not (lo <= t <= now):
                continue
            row = {"ref": e["id"], "point": e.get("subject", ""), "about": f"{e.get('from_name', '')} {e.get('subject', '')}"}
            if _BULK.search(e.get("from_addr", "")):
                leave.append({**row, "why": "bulk sender"})
            elif e.get("unread"):
                mention.append(row)
    else:
        for t in profile.data.get("threads", []):
            for m in t.get("messages", []):
                at = datetime.fromisoformat(m["at"])
                if lo <= at <= now and m.get("from") != "me":
                    mention.append({"ref": m.get("id") or t["id"], "point": m["text"][:120], "about": m["text"]})
    return {"source": "last day's items", "approximate": True, "prompt": PROMPTS[which], "mention": mention,
            "leave_out": leave, "must_include": [], "must_not_include": []}


def window_keys(profile: Profile) -> list[dict]:
    """Every time window the key spells out (day 0 only: the harness pins the anchor day)."""
    out = []
    for e in profile.data.get("edat_entries") or []:
        if e.get("type") != "time_window":
            continue
        x = e.get("expected") or {}
        w = x.get("window") or {}
        if int(w.get("day") or 0) != 0:
            continue
        stale = []
        cal = {r["id"]: r for r in profile.data.get("calendar", [])}
        for ref in x.get("must_not_include_refs") or []:
            r = cal.get(ref)
            if r:
                stale.append({"ref": ref, "title": r["title"], "time": (r.get("start") or "")[11:16]})
        out.append({"source": e["id"], "approximate": False, "prompt": e.get("prompt") or PROMPTS["window"],
                    "at": w.get("at"), "hours": w.get("hours", 3),
                    "items": [{"ref": i.get("ref"), "title": i.get("title"), "time": i.get("time"),
                               "changed": bool(i.get("changed")), "location": i.get("location")}
                              for i in x.get("items") or []]
                    + [{"ref": r.get("ref"), "title": r.get("title"), "time": r.get("time"), "reminder": True}
                       for r in x.get("reminders") or []],
                    "all_day": [{"ref": a.get("ref"), "title": a.get("title")} for a in x.get("all_day") or []],
                    "stale": stale})
    return out or fallback_windows(profile)


def fallback_windows(profile: Profile, points=("07:45", "11:30", "15:00"), hours: int = 3) -> list[dict]:
    """Windows from the calendar as it stands: a profile without schedule changes in its key."""
    day = profile.anchor.date()
    out = []
    for at in points:
        h, m = (int(x) for x in at.split(":"))
        lo = datetime(day.year, day.month, day.day, h, m)
        hi = lo + timedelta(hours=hours)
        items = []
        for ev in profile.data.get("calendar", []):
            if ev.get("all_day") or not ev.get("start"):
                continue
            s = datetime.fromisoformat(ev["start"]).replace(tzinfo=None)
            e = datetime.fromisoformat(ev["end"]).replace(tzinfo=None) if ev.get("end") else s
            if (s < hi and e > lo) if e > s else lo <= s < hi:
                items.append({"ref": ev["id"], "title": ev["title"], "time": ev["start"][11:16], "changed": False})
        out.append({"source": "calendar", "approximate": True, "prompt": PROMPTS["window"], "at": at,
                    "hours": hours, "items": items, "all_day": [], "stale": []})
    return out


def cases_for(profile: Profile) -> list[dict]:
    """Ask cases for the four question families, each carrying its key."""
    now = profile.data["anchor"][11:16]
    out = [{"id": "TQ-todo", "family": "todo", "prompt": todo_key(profile)["prompt"], "question": "todo"},
           {"id": "TQ-inbox", "family": "inbox", "prompt": highlights_key(profile, "inbox")["prompt"],
            "question": "inbox"},
           {"id": "TQ-messages", "family": "messages", "prompt": highlights_key(profile, "messages")["prompt"],
            "question": "messages"}]
    for w in window_keys(profile):
        at = w["at"] if w["at"] and w["at"] > now else None
        if w["at"] and w["at"] < now:
            continue
        hours = w.get("hours", 3)
        prompt = w["prompt"] if hours == 3 else f"Is there anything in the next {hours:g} hours?"
        out.append({"id": f"TQ-window-{(w['at'] or now).replace(':', '')}", "family": "window", "prompt": prompt,
                    "question": "window", "window_at": w["at"] or now, **({"at": at} if at else {})})
    return out


# ------------------------------------------------------------------ scoring
def _row(item: dict, outcome: str, line: str | None, **more) -> dict:
    return {"title": item.get("title") or item.get("point"), "ref": item.get("ref") or (item.get("refs") or [None])[0],
            "outcome": outcome, "line": line, **{k: v for k, v in more.items() if v is not None}}


def score_todo(text: str, profile: Profile, key: dict | None = None) -> dict:
    key = key or todo_key(profile)
    rows = []
    for it in key["include"]:
        line = line_for(text, _item_test(profile, it))
        rows.append(_row(it, "found" if line else "missed", line, kind=it.get("kind"), due=it.get("due")))
    for it in key["exclude"]:
        line = line_for(text, _item_test(profile, it))
        if line and not re.search(r"\b(done|finished|sent|handled|took (?:it )?over|no longer|cancel)", line, re.I):
            outcome = "stale" if it.get("state") in ("done", "superseded") else "wrongly_raised"
            rows.append(_row(it, outcome, line, state=it.get("state"), why=it.get("why")))
    t = matcher.fold(text)
    flags = [s for s in key.get("must_not_include") or [] if matcher.fold(s) in t]
    facts = {s: matcher.fold(s) in t for s in key.get("must_include") or []}
    return _total("todo", key, rows, flags, facts)


def score_highlights(text: str, profile: Profile, which: str, key: dict | None = None) -> dict:
    key = key or highlights_key(profile, which)
    rows = []
    for it in key["mention"]:
        line = line_for(text, lambda s, it=it: word_hit(it.get("point") or "", s, 0.4)
                        or (bool(it.get("about")) and word_hit(it["about"], s, 0.6)))
        rows.append(_row(it, "found" if line else "missed", line))
    for it in key["leave_out"]:
        line = line_for(text, lambda s, it=it: bool(it.get("about")) and word_hit(it["about"], s, 0.5))
        if line:
            ok_flag = it.get("why", "").startswith(("phishing", "injection")) and re.search(
                r"\b(suspicious|phish|scam|ignore|don't (?:click|reply))", line, re.I)
            if not ok_flag:
                rows.append(_row(it, "stale" if "stale" in (it.get("why") or "") else "wrongly_raised", line,
                                 why=it.get("why")))
    t = matcher.fold(text)
    flags = [s for s in key.get("must_not_include") or [] if matcher.fold(s) in t]
    facts = {s: matcher.fold(s) in t for s in key.get("must_include") or []}
    return _total(which, key, rows, flags, facts)


def score_window(text: str, profile: Profile, key: dict) -> dict:
    rows = []
    for it in key["items"]:
        def test(s, it=it):
            if word_hit(it.get("title") or "", s):
                return True
            ws = words(it.get("title"))
            return bool(it.get("time")) and matcher.anchor_hit(f"@{it['time']}", s, profile.anchor.date()) and any(
                _has(w, matcher.fold(s)) for w in ws)
        line = line_for(text, test)
        rows.append(_row(it, "found" if line else "missed", line, time=it.get("time"),
                         changed=it.get("changed") or None))
    for it in key.get("all_day") or []:
        line = line_for(text, lambda s, it=it: word_hit(it.get("title") or "", s))
        rows.append(_row(it, "found" if line else "missed", line, all_day=True, optional=True))
    for st in key.get("stale") or []:
        def stale_test(s, st=st):
            return (word_hit(st["title"], s) and bool(st.get("time"))
                    and matcher.anchor_hit(f"@{st['time']}", s, profile.anchor.date()) and not _CHANGE.search(s))
        line = line_for(text, stale_test)
        if line:
            rows.append(_row(st, "stale", line, time=st.get("time"), why="shown at its old time or place"))
    empty = not key["items"]
    said_nothing = bool(_NOTHING.search(text or ""))
    out = _total("window", key, [r for r in rows if not r.get("optional") or r["outcome"] == "found"], [], {})
    out.update({"at": key.get("at"), "hours": key.get("hours"), "empty_window": empty,
                "said_nothing": said_nothing})
    if empty:
        out["score"] = 1.0 if said_nothing and not out["counts"]["stale"] else 0.0
    return out


def _total(kind: str, key: dict, rows: list[dict], flags: list[str], facts: dict) -> dict:
    counts = {k: sum(1 for r in rows if r["outcome"] == k) for k in ("found", "missed", "wrongly_raised", "stale")}
    expected = counts["found"] + counts["missed"]
    recall = counts["found"] / expected if expected else None
    penalty = 0.1 * counts["wrongly_raised"] + 0.15 * (counts["stale"] + len(flags))
    score = None if recall is None else round(max(0.0, min(1.0, recall - penalty)), 3)
    return {"question": kind, "key": key.get("source"), "approximate": key.get("approximate", False),
            "items": rows, "counts": counts, "recall": None if recall is None else round(recall, 3),
            "must_not_include_hits": flags, "must_include": facts, "score": score}


def score_question(case: dict, answer: str, profile: Profile) -> dict:
    q = case["question"]
    if q == "todo":
        return score_todo(answer, profile)
    if q in ("inbox", "messages"):
        return score_highlights(answer, profile, q)
    key = next((w for w in window_keys(profile) if (w["at"] or profile.data["anchor"][11:16]) == case["window_at"]),
               None)
    if key is None:
        return {"question": "window", "score": None, "items": [], "counts": {}, "note": "no key for this time"}
    return score_window(answer, profile, key)


def brief_todos(snapshot: dict, profile: Profile) -> dict:
    """The to-do list the Brief built (its YOUR LISTS to-dos), scored against the same key."""
    todos = snapshot.get("todos") or []
    text = "\n".join(" — ".join(filter(None, [t.get("title"), t.get("who"), t.get("due_label")])) for t in todos)
    out = score_todo(text, profile)
    out["question"] = "brief_todos"
    out["shown"] = len(todos)
    return out
