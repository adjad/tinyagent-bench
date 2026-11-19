"""Adapt a profile in the shared EDAT schema (09-profile-schema.md, v1) to productbench/1.

The shared profile is two files: profile.json (what Harbor sees) and
ledger.json (the answer key, never shown to the model). This module:

* renders the date tokens ({{dow:+2}}, {{date:+2}}, {{date:+2:short|num|iso|long}})
  against the anchor date, English only, as render.load_profile does;
* turns days/times into local ISO strings in the anchor's zone (the backend runs
  with TZ set to it, see capture.py), so loader.build_world can place them;
* maps the ledger's entries onto productbench ledger items, and writes Ask
  cases from every entry that targets Ask: its own prompt, `must_include` as
  facts that must all appear, `must_not_include` as contradictions, the
  `unknowable` type as a refusal case, `injection` as a must-not-act case.

The mapping of `day_constraint` details and Brief noise anchors is provisional:
it was written from the schema before any shared profile existed (see the
design file's open questions) and is checked by `coverage_report()`.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.productbench.profile import SCHEMA

_TOKEN = re.compile(r"\{\{(dow|date):([+-]?\d+)(?::([a-z]+))?(?::([a-z]{2}))?\}\}")
_HHMM = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def render(text: str, anchor_date: date) -> str:
    def sub(m: re.Match) -> str:
        kind, off, fmt, _lang = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        d = anchor_date + timedelta(days=off)
        if kind == "dow":
            return d.strftime("%A")
        if fmt == "short":
            return f"{d.strftime('%b')} {d.day}"
        if fmt == "num":
            return f"{d.month}/{d.day}"
        if fmt == "iso":
            return d.isoformat()
        return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}"
    return _TOKEN.sub(sub, text or "")


def _deep_render(value, anchor_date: date):
    if isinstance(value, str):
        return render(value, anchor_date)
    if isinstance(value, list):
        return [_deep_render(v, anchor_date) for v in value]
    if isinstance(value, dict):
        return {k: _deep_render(v, anchor_date) for k, v in value.items()}
    return value


def _iso(anchor_date: date, day: int | None, hhmm: str | None) -> str | None:
    if day is None:
        return None
    d = anchor_date + timedelta(days=int(day))
    return f"{d.isoformat()}T{hhmm or '00:00'}"


TYPE_MAP = {
    "today_agenda": "fact", "calendar_correction": "change", "commitment_user": "owed",
    "request_to_user": "waiting_on_me", "deadline": "deadline", "brief_attention": "task",
    "brief_noise": "noise", "day_constraint": "event", "two_source_fact": "fact",
    "unknowable": "fact", "injection": "noise", "similar_names": "fact", "changed_detail": "change",
}
FAMILY_MAP = {
    "today_agenda": "today", "calendar_correction": "changed", "changed_detail": "changed",
    "commitment_user": "owe", "request_to_user": "waiting", "deadline": "owe",
    "brief_attention": "today", "two_source_fact": "cross_source", "similar_names": "cross_source",
    "unknowable": "no_answer", "injection": "act", "day_constraint": "plan",
}


def _anchor_for(phrase: str) -> str:
    """A must_include string that is exactly a clock time becomes a time anchor."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*(am|pm)?\s*", phrase, re.I)
    if not m:
        return phrase
    h, mi, mer = int(m.group(1)), int(m.group(2)), m.group(3)
    if mer:
        h = h % 12 + (12 if mer.lower() == "pm" else 0)
    elif 1 <= h <= 6:
        h += 12
    return f"@{h:02d}:{mi:02d}"


def adapt(profile: dict, ledger: dict | None) -> tuple[dict, dict]:
    """(productbench/1 profile, generated cases) from the shared profile and ledger."""
    a = profile["anchor"]
    anchor_date = date.fromisoformat(a["date"])
    p = _deep_render(profile, anchor_date)
    accounts = {acc["id"]: acc for acc in p.get("accounts", [])}
    person = p["person"]
    about = " ".join(filter(None, [person.get("summary"),
                                   (person.get("about_you") or {}).get("notes")]))
    out: dict = {
        "schema": SCHEMA, "id": p["profile_id"], "label": p.get("label", ""),
        "source_schema": "edat/1", "timezone": a["tz"],
        "anchor": f"{a['date']}T{a['now']}:00",
        "user": {"name": person["name"], "preferred_name": person.get("preferred_name"),
                 "emails": [acc["address"] for acc in accounts.values() if acc.get("is_identity")],
                 "about": about[:600], "people": [],
                 "about_you": dict(person.get("about_you") or {})},
        "hours": {}, "memory_facts": [], "contacts": [], "calendar": [], "reminders": [],
        "emails": [], "threads": [], "notes": [], "browser": [], "ledger": [],
        "edat_entries": [],      # the rendered ledger entries, for preferences and privacy
    }
    s = p.get("settings", {})
    if s.get("working_hours"):
        w = s["working_hours"]
        out["hours"]["working"] = {"start": w["start"], "end": w["end"], "days": w.get("days", [0, 1, 2, 3, 4])}
    if s.get("quiet_hours"):
        out["hours"]["quiet"] = dict(s["quiet_hours"])
    for m in p.get("memory", []):
        out["memory_facts"].append({"text": m["text"], "category": m.get("category", "fact")})
    for c in p.get("contacts", []):
        out["contacts"].append({"id": c["id"], "name": c["name"],
                                "handles": list(c.get("phones") or []) + list(c.get("emails") or []),
                                "relation": c.get("relation") or "", "note": c.get("note") or ""})
    for e in p.get("events", []):
        end_day = e.get("end_day", e["day"])
        row = {"id": e["id"], "title": e["title"], "kind": e.get("kind", "event"),
               "start": _iso(anchor_date, e["day"], None if e.get("all_day") else e.get("time")),
               "end": None if e.get("all_day") else _iso(anchor_date, end_day, e.get("end")),
               "all_day": bool(e.get("all_day")), "location": e.get("location"),
               "organizer": e.get("organizer"), "context": e.get("context"),
               "account": (accounts.get(e.get("account")) or {}).get("name")}
        out["calendar"].append(row)
    for r in p.get("reminders", []):
        if r.get("completed") or r.get("day") is None:
            continue           # EventKit sends neither; an undated reminder has no when_ts
        out["reminders"].append({"id": r["id"], "title": r["title"],
                                 "due": _iso(anchor_date, r["day"], r.get("time") or "09:00"),
                                 "all_day": bool(r.get("all_day"))})
    for e in p.get("emails", []):
        if e.get("mailbox", "inbox") != "inbox":
            continue           # Harbor's mail sync reads only the inbox
        out["emails"].append({"id": e["id"], "message_id": e.get("message_id"),
                              "account": (accounts.get(e.get("account")) or {}).get("name", "Mail"),
                              "from_name": e["from"]["name"], "from_addr": e["from"]["addr"],
                              "to": e.get("to") or [], "cc": e.get("cc") or [],
                              "subject": e.get("subject", ""), "body": e.get("body", ""),
                              "received": _iso(anchor_date, e["day"], e["time"]),
                              "unread": bool(e.get("unread"))})
    for t in p.get("threads", []):
        out["threads"].append({"id": t["id"], "title": t.get("title"), "with": list(t.get("participants") or []),
                               "group": t.get("kind") == "group",
                               "messages": [{"from": m["from"], "text": m["text"],
                                             "at": _iso(anchor_date, m["day"], m["time"])}
                                            for m in t.get("messages", [])]})
    for n in p.get("notes", []):
        out["notes"].append({"id": n["id"], "title": n.get("title", ""), "folder": n.get("folder", "Notes"),
                             "body": n.get("body", ""), "modified": _iso(anchor_date, n["day"], n.get("time"))})
    for b in p.get("browser", []):
        out["browser"].append({"id": b["id"], "browser": b.get("browser", "safari"), "host": b["host"],
                               "path": b.get("path", "/"), "title": b.get("title", ""),
                               "at": _iso(anchor_date, b["day"], b["time"])})

    known = {row["id"] for coll in ("contacts", "calendar", "reminders", "emails", "threads", "notes")
             for row in out[coll]}
    events = {e["id"]: e for e in out["calendar"]}
    cases: dict = {"profile": out["id"], "generated_from": "ledger.json (edat/1)",
                   "ask": [], "brief": {}, "day": {"plans": [
                       {"id": "D-plan", "kind": "plan",
                        "prompt": "Plan my day for today with times, including when to work on what's due soon."}]}}
    for entry in (ledger or {}).get("entries", []):
        entry = _deep_render(entry, anchor_date)
        exp = entry.get("expected") or {}
        etype = entry["type"]
        out["edat_entries"].append(entry)
        if etype == "preference":
            continue            # scored by preferences.py on its own conversations
        if etype == "privacy_leak":
            if entry.get("prompt"):
                cases["ask"].append({"id": f"P-{entry['id']}", "family": "privacy", "prompt": entry["prompt"],
                                     "ledger_type": etype, "privacy": {"rule": entry["id"],
                                                                       "kind": exp.get("kind")}})
            continue            # reported with the unsafe actions, never scored
        surfaces = entry.get("surfaces") or []
        must = [_anchor_for(x) for x in exp.get("must_include") or []]
        item = {"id": entry["id"], "type": TYPE_MAP.get(etype, "fact"), "title": entry.get("truth", "")[:160],
                "edat_type": etype, "status": exp.get("status"),
                "anchors": must or [entry.get("truth", "")[:40]],
                "stale_anchors": [x for x in exp.get("must_not_include") or []],
                "sources": [x for x in entry.get("evidence", []) if x in known],
                "attention": "brief" in surfaces and etype not in ("brief_noise", "injection"),
                "priority": {"hard": 1, "medium": 2, "easy": 3}.get(entry.get("difficulty"), 2)}
        if exp.get("status") in ("done", "superseded", "ignore"):
            item["attention"] = False
        if etype in ("brief_noise", "injection"):
            item["type"] = "noise"
        ev = next((events[x] for x in entry.get("evidence", []) if x in events), None)
        if etype == "day_constraint" and ev and ev.get("start") and ev.get("end"):
            item.update({"start": ev["start"], "end": ev["end"], "fixed": True})
        out["ledger"].append(item)
        if "ask" in surfaces and entry.get("prompt"):
            case: dict = {"id": f"Q-{entry['id']}", "family": FAMILY_MAP.get(etype, "today"),
                          "qtype": "refuse/limit" if etype == "unknowable" else "explain",
                          "prompt": entry["prompt"], "ledger_type": etype}
            if etype == "unknowable":
                case["expect_refusal"] = True
            elif etype == "injection":
                case["act"] = {"tools_any": [], "draft_ok": False, "must_not_act": True}
                case["forbidden"] = list(exp.get("must_not_include") or [])
            else:
                if must:
                    case["required"] = [{"ledger": entry["id"], "all": must}]
                case["forbidden"] = list(exp.get("must_not_include") or [])
            if exp.get("answer"):
                case["reference_words"] = len(exp["answer"].split())
            cases["ask"].append(case)
    return out, cases


def load_pair(profile_path: Path) -> tuple[dict, dict]:
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    ledger_path = Path(profile_path).with_name("ledger.json")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
    return adapt(profile, ledger)


def coverage_report(cases: dict, minimum: int = 25) -> dict:
    """Scored Ask cases per family (privacy cases are counted apart: they are never scored)."""
    families = {}
    scored = [c for c in cases.get("ask", []) if c.get("family") != "privacy"]
    for c in scored:
        families[c["family"]] = families.get(c["family"], 0) + 1
    return {"ask_cases": len(scored), "families": families,
            "privacy_cases": len(cases.get("ask", [])) - len(scored),
            "meets_minimum": len(scored) >= minimum}
