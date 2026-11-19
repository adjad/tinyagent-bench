"""Turn a Profile into a sandbox world and an isolated HARBOR_HOME.

Nothing here starts a process or opens a socket. `prepare_home()` writes:

  <run>/sandbox/world.json   the sandbox world (sandbox/world.py, schema 1),
                             anchored at the profile's "now", so the sandbox
                             server loads it instead of seeding the QA persona
  <run>/moe/user_hours.json  the profile's working and quiet hours
  <run>/moe/config.yaml      the model under test bound to every text role, and
                             the managed endpoint pinned (see serve.py)

and returns the HTTP calls to make once the backend is up (the "about you"
profile, seed memory facts, memory capture off), so they go through the
backend's own validation instead of a hand-written file.

It must never import service.* (or tests.fixtures, which does): several service
modules open their stores under HARBOR_HOME at import time, and in the runner's
own process HARBOR_HOME is not the trial's scratch home.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from scripts.productbench.profile import Profile

WORLD_SCHEMA = 1   # sandbox/world.py SCHEMA_VERSION
TEXT_ROLES = ("fast", "router", "agent", "reasoning", "general", "coding")


@dataclass
class PreparedHome:
    root: Path
    harbor_home: Path
    sandbox_home: Path
    anchor_epoch: float
    post_start: list[tuple[str, str, dict]] = field(default_factory=list)


def _off(profile: Profile, value: str | None) -> float | None:
    return None if value is None else profile.epoch(value) - profile.anchor_epoch


def build_world(profile: Profile) -> dict:
    """The sandbox world for this profile, every timestamp relative to the anchor."""
    d = profile.data
    contacts = {}
    for c in d.get("contacts", []):
        handles = list(c.get("handles") or [])
        email = next((h for h in handles if "@" in h), None)
        contacts[c["id"]] = {"id": c["id"], "name": c["name"], "handles": handles, "email": email}
    name_of = {cid: c["name"] for cid, c in contacts.items()}

    threads = {}
    for t in d.get("threads", []):
        members = [m for m in (t.get("with") or []) if m in name_of]
        is_group = len(members) > 1 or bool(t.get("group"))
        # A chat with no saved member is labelled by the other party's number or address, as
        # the Messages reader labels it (its chat identifier), never "Unknown".
        stranger = next((m["from"] for m in t.get("messages", []) if m["from"] != "me"), None)
        label = None if is_group else (name_of[members[0]] if members else t.get("title") or stranger or "Unknown")
        threads[t["id"]] = {
            "id": t["id"], "kind": "group" if is_group else "dm", "title": t.get("title"),
            "context_label": label, "participants": members, "draft": None,
            # How Messages delivers this chat ("imessage", "sms", "rcs"); the sandbox's Messages
            # feed reports it per message, as the reader does.
            "service": t.get("service") or "imessage",
            "messages": [{"id": f"{t['id']}_m{i}", "ts_off": _off(profile, m["at"]),
                          "from": "me" if m["from"] == "me" else name_of.get(m["from"], m["from"]),
                          # The saved contact who wrote it, so the feed can give their handle.
                          **({"sender_id": m["from"]} if m["from"] in name_of else {}),
                          "text": m["text"], "via": "seed"}
                         for i, m in enumerate(t.get("messages", []))],
        }

    user_email = (profile.user.get("emails") or ["test.user@example.com"])[0]
    emails = {}
    for e in d.get("emails", []):
        ts_off = _off(profile, e["received"])
        emails[e["id"]] = {
            "id": e["id"], "message_id": e.get("message_id") or f"<{e['id']}@bench.harbor.test>",
            "account": e.get("account", "Personal"), "ts_off": ts_off,
            "from_name": e.get("from_name", ""), "from_addr": e.get("from_addr", ""),
            "to": list(e.get("to") or [user_email]), "cc": list(e.get("cc") or []),
            "subject": e.get("subject", ""), "body": e.get("body", ""),
            "unread": bool(e.get("unread", True)), "mailbox": "inbox",
            "deep": ts_off < -30 * 86400, "in_reply_to": None,
        }

    notes = {n["id"]: {"id": n["id"], "ts_off": _off(profile, n.get("modified") or d["anchor"]),
                       "title": n.get("title", ""), "folder": n.get("folder", "Notes"),
                       "body": n.get("body", "")}
             for n in d.get("notes", [])}

    calendar = {}
    for ev in d.get("calendar", []):
        if not ev.get("start"):
            continue
        calendar[ev["id"]] = {
            "id": ev["id"], "source_id": ev["id"], "kind": ev.get("kind", "event"),
            "title": ev["title"], "context": ev.get("context"), "organizer": ev.get("organizer"),
            "account": ev.get("account"), "ts_off": _off(profile, ev["start"]),
            "end_ts_off": _off(profile, ev.get("end")), "all_day": bool(ev.get("all_day")),
            "location": ev.get("location")}
    reminders = {}
    for r in d.get("reminders", []):
        reminders[r["id"]] = {
            "id": r["id"], "source_id": r["id"], "kind": r.get("kind", "reminder"),
            "title": r["title"], "context": r.get("context"), "organizer": None,
            "account": r.get("account"), "ts_off": _off(profile, r.get("due") or d["anchor"]),
            "all_day": bool(r.get("all_day")), "location": None}

    return {
        "version": WORLD_SCHEMA, "rev": 0, "booted_at": profile.anchor_epoch,
        # live: the sandbox server runs on the same shifted clock as the backend
        # (clock.py), so "live" already reads the profile's anchor time.
        "clock": {"mode": "live", "frozen_at": None, "sync_scale": 0.05},
        "persona": {"identity_emails": list(profile.user.get("emails") or [])},
        "contacts": contacts, "threads": threads, "emails": emails, "notes": notes,
        "calendar": calendar, "reminders": reminders,
        # The window the (simulated) calendar read covered: 30 days back, 60 ahead,
        # whole local days, so every planned day is inside confirmed coverage.
        "calendar_coverage": _coverage(profile),
        "browser": _browser(profile), "notifications": [], "action_log": [],
        "injection": {"fail": {}, "stall_ms": {}, "drop_result": [], "unavailable": [],
                      "sync_paused": False, "latency_ms": 0},
        "sync_status": {},
    }


def _browser(profile: Profile) -> dict:
    out: dict = {"safari": [], "chrome": []}
    for b in profile.data.get("browser", []):
        out.setdefault(b.get("browser", "safari"), []).append(
            {"ts_off": _off(profile, b["at"]), "host": b["host"], "path": b.get("path", "/"),
             "title": b.get("title", "")})
    return out


def _coverage(profile: Profile) -> dict:
    midnight = profile.anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    start = (midnight - timedelta(days=30)).timestamp() - profile.anchor_epoch
    end = (midnight + timedelta(days=61)).timestamp() - profile.anchor_epoch
    return {"start_off": start, "end_off": end}


def user_hours(profile: Profile) -> dict:
    hours = profile.data.get("hours", {})
    out: dict = {"version": 1}
    w = hours.get("working")
    if w:
        start, end = profile.working_window()
        out["working"] = {"start_minute": start, "end_minute": end,
                          "days": list(w.get("days", [0, 1, 2, 3, 4]))}
    q = hours.get("quiet")
    if q:
        sh, sm = (int(x) for x in q["start"].split(":"))
        eh, em = (int(x) for x in q["end"].split(":"))
        out["quiet"] = {"start_minute": sh * 60 + sm, "end_minute": eh * 60 + em}
    return out


ABOUT_LIMITS = {"about": 600, "goals": 5, "goal": 200, "struggles": 6, "struggle": 160, "people": 8,
                "boundaries": 600, "notes": 800}


def about_patch(profile: Profile) -> dict:
    """PATCH /profile body: the "About you" answers this persona would give.

    A shared (tinyagent-bench) profile carries Harbor's own fields in person.about_you
    (service/memory/profile.py: about, goals, struggles, people, tone, detail,
    boundaries, notes); stored standing preferences live in several of them.
    Values are cut to Harbor's limits so the PATCH is never refused."""
    u = profile.user
    ay = u.get("about_you") or {}
    name_of = {c["id"]: c for c in profile.data.get("contacts", [])}
    people = []
    for p in ay.get("people") or []:
        people.append({"name": p.get("name", ""), "relation": p.get("relation", ""), "note": p.get("note", "")})
    if not people:
        for cid in u.get("people", []):
            c = name_of[cid]
            people.append({"name": c["name"], "relation": c.get("relation", ""), "note": c.get("note", "")})
    L = ABOUT_LIMITS
    body: dict = {"name": u.get("preferred_name") or u["name"],
                  "about": (ay.get("about") or u.get("about", ""))[:L["about"]]}
    if people:
        body["people"] = people[:L["people"]]
    if ay.get("goals"):
        body["goals"] = [g[:L["goal"]] for g in ay["goals"][:L["goals"]]]
    if ay.get("struggles"):
        body["struggles"] = [g[:L["struggle"]] for g in ay["struggles"][:L["struggles"]]]
    for k in ("tone", "detail"):
        if ay.get(k):
            body[k] = ay[k]
    for k in ("boundaries", "notes"):
        if ay.get(k):
            body[k] = ay[k][:L[k]]
    return body


# Harbor's own "Everything" cloud limits (service/config/__init__.py
# _EVERYTHING_LIMITS), so a cloud reference runs as Settings > Cloud > Everything would.
CLOUD_LIMITS = {"max_tokens": 16384, "request_bytes": 16 * 1024 * 1024, "messages": 1000,
                "aggregate_bytes": 12 * 1024 * 1024, "tools": 128, "response_bytes": 8 * 1024 * 1024}
OPENROUTER = {"base_url": "https://openrouter.ai", "api_prefix": "/api/v1"}


def overlay_yaml(model: str, engine_port: int, *, cloud: bool = False, context_window: int | None = None,
                 credential_ref: str = "keychain:openrouter") -> str:
    """The config overlay.

    Local: one model in every text role, the managed endpoint pinned to the engine.
    Cloud reference: Harbor's remote-only profile for OpenRouter with remote_parity,
    which is what Settings > Cloud > Everything writes; the backend then needs
    HARBOR_REMOTE_ONLY=1. The token stays in the Keychain under credential_ref.
    """
    if cloud:
        lines = ["remote_parity: true", "inference:", "  remote_only:",
                 f"    base_url: \"{OPENROUTER['base_url']}\"",
                 f"    api_prefix: \"{OPENROUTER['api_prefix']}\"",
                 f"    model_id: {json.dumps(model)}",
                 f"    credential_ref: {credential_ref}",
                 f"    context_window: {int(context_window or 128000)}",
                 "    readiness_timeout: 15", "    enabled: true"]
        lines += [f"    {k}: {v}" for k, v in CLOUD_LIMITS.items()]
        return "\n".join(lines) + "\n"
    lines = ["roles:"] + [f"  {r}: {json.dumps(model)}" for r in TEXT_ROLES]
    lines += ["inference:", "  endpoints:", "    local:",
              f"      base_url: \"http://127.0.0.1:{int(engine_port)}\"",
              "      credential_ref: local_omlx"]
    return "\n".join(lines) + "\n"


def refuse_real_home(path: Path) -> None:
    real = (Path.home() / ".moe").resolve()
    p = path.resolve()
    if p == real or real in p.parents:
        raise SystemExit(f"refusing {path}: it is inside the real ~/.moe")


def prepare_home(profile: Profile, root: Path, *, model: str, engine_port: int, cloud: bool = False,
                 context_window: int | None = None,
                 credential_ref: str = "keychain:openrouter") -> PreparedHome:
    """A fresh run directory for one trial. Deletes `root` first if it exists."""
    refuse_real_home(root)
    if root.exists():
        shutil.rmtree(root)
    harbor_home, sandbox_home = root / "moe", root / "sandbox"
    for p in (harbor_home, sandbox_home):
        p.mkdir(parents=True, mode=0o700)
    (sandbox_home / "world.json").write_text(json.dumps(build_world(profile), indent=1))
    (harbor_home / "user_hours.json").write_text(json.dumps(user_hours(profile)))
    (harbor_home / "config.yaml").write_text(overlay_yaml(
        model, engine_port, cloud=cloud, context_window=context_window, credential_ref=credential_ref))
    for f in (sandbox_home / "world.json", harbor_home / "user_hours.json", harbor_home / "config.yaml"):
        os.chmod(f, 0o600)
    post = [("POST", "/memory/capture", {"enabled": False}),
            ("PATCH", "/profile", about_patch(profile))]
    for fact in profile.data.get("memory_facts", []):
        post.append(("POST", "/memory/facts", {"text": fact["text"],
                                               "category": fact.get("category", "fact")}))
    return PreparedHome(root, harbor_home, sandbox_home, profile.anchor_epoch, post)
