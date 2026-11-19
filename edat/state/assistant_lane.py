"""The assistant lane: Harbor's tool names and schemas over Toolbench's fakes.

`AssistantEnvironment` implements `edat.state.env.ToolEnvironment` for tinyagent-bench's
own reference loop. It never runs Harbor's agent loop: the loop hands it one
call at a time and it runs the matching Toolbench fake against a private
Toolbench `World`.

Where each part comes from
--------------------------
* World and fakes: `scripts.toolbench.world.build_world(clock.today, seed)` and
  `make_fakes(world)`, one fresh World per environment (never shared). The
  world's clock is set to the case clock. A tool with no bespoke fake gets
  Toolbench's `generic_fake`, as in the Toolbench runner.
* Tool names, descriptions and parameter schemas: Harbor's registry
  (`service.tools.registry.REGISTRY`), without the two `internal` tools that
  only a deterministic route may reach. Toolbench's `register_extra_tools()`
  adds the tools production registers at startup (the bundled mac-controls
  skill tools, `use_skill`, and the fake "drive" MCP server's two tools). It is
  NOT side-effect free on its own: it scans and may tidy `$HARBOR_HOME/skills`,
  and it mutates `REGISTRY`, `service.mcp.TOOL_PARTS`, `service.skills._skills`
  and the skill-tool bookkeeping. So it runs once per process, only with
  `HARBOR_HOME` on a throwaway directory, and every one of those globals is
  restored afterwards; this module keeps its own copies of the extra tools and
  of the loaded bundled skills (swapped in only while `use_skill` or
  `harbor_skills` runs). The one lasting effect is the import of
  `service.skills.tools`, which registers `use_skill` (the Toolbench runner
  does the same). Extra tools are kept only when Toolbench has a bespoke fake
  for them, and skill tools only while every loaded skill is a bundled one.
* Importing `service.*` reads `HARBOR_HOME` once. If it is unset (or names the
  real `~/.moe`) before the first import, it is pointed at a fresh temp dir;
  if `service` was already imported against the real home, construction fails.
* Calls go through Harbor's tool-layer rules from `registry.run_tool`: empty
  argument names dropped, `_validate_args` against the registered schema, and
  exceptions turned into `(error calling <name>(...): ...)` for a TypeError or
  `(error running <name>: ...)` for anything else. A display-only result
  (current-news `web_search`) reaches the model as Harbor's placeholder text,
  as in the agent loop. The outcome class comes from Harbor's own
  `classify_tool_outcome` ("planned"/"preview" map to succeeded,
  "unsupported" to failed). Nothing is truncated; that is loop policy.
* The system prompt: see edat/state/assistant_prompt.py (what is included and
  what is left out).

Seeding (`seed`)
----------------
Records arrive in the shared vocabulary (edat/state/collections.py) with naive
local ISO times (relative strings are resolved against the clock too). They are
ADDED to the canonical Toolbench world, which keeps its own records. Each
seeded record keeps its `id`; a clash with another record's id is an error.
Supported collections and fields ("*" required):

  calendar   id*, title*, start*, end, location, attendees (the count drives
             invitee notices), calendar (a name containing "holiday" is hidden
             from get_upcoming, as in Harbor), writable, all_day. Lane extra:
             organizer (someone else organizes it, so it cannot be moved).
             An event starting before today goes where get_past_events reads.
  reminders  id*, title*, due (a date means all-day; null means undated), done
             (a done reminder is in state() only: the fakes list incomplete
             ones, like Apple Reminders).
  notes      id*, title*, body.
  contacts   id*, name*, phone, email.
  mail       id*, from*, subject*, body, received (default: now), unread
             (default true), to (only the user's own addresses; it picks the
             account), folder (inbox only).
  messages   id*, thread* (the conversation label), from* ("Me" for the user),
             text*, received (default: now), unread (default true), group,
             members (kept for grading; no fake shows it).
  files      path* (id defaults to the path and must equal it), kind (file |
             folder), text (only under a ~/Documents folder: read_file serves
             nothing else).
  memory     text*, id (default: the next number), category, status (active
             facts go to recall; forgotten/superseded ones are in state() only),
             supersedes_id.
  device     id*, value*: volume (0-100), muted, locked, front (frontmost app),
             music / spotify ("playing" | "paused"), apps (running app names).

Fields of the vocabulary that no fake can surface raise ValueError rather than
being dropped: calendar notes and rrule, reminder notes and list, note folder,
contact notes, birthday and relation, file size, file text outside
~/Documents, mail folders other than inbox, mail `to` other than the user's
own addresses. Collections the fakes cannot serve raise too: weather, web and
stocks (Toolbench computes them from the place name, query or symbol), and
every outbound or derived collection (mail_sent, scheduled_sends, timers, ...).
Fields outside the vocabulary (case metadata such as private_canaries) are kept
on the record and shown in state(), never to the model.

State (`state`)
---------------
A fresh StateStore built from the World each time (two calls with no tool call
between them are equal): calendar, reminders, notes, contacts, mail (inbox),
messages, files, memory, timers, scheduled_sends, device, and the outbound
collections built from `world.effects` in call order: messages_sent (to as
given, plus the resolved handle), mail_sent (to as a resolved address list,
kind send | reply | forward), message_drafts, mail_drafts. Ids: seeded records
keep theirs; canonical and newly created records get deterministic ids
(`ev-<slug>`, `rem-<slug>`, `note-<slug>`, `c-<slug>`, `msg-<n>`, the mail's
own Message-ID, the World's own ids for memory, timers and scheduled sends),
assigned once and stored on the record, so an event keeps its id when moved.
A completed reminder stays with done: true; a deleted one disappears. A
cancelled timer stays with status cancelled; a forgotten memory with status
forgotten.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import inspect
import math
import os
import re
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from edat.state.clock import Clock, parse_local
from edat.state.env import OUTCOMES, ToolInfo, ToolResult
from edat.state.store import StateStore

# Calls that send content out of the user's own stores (spec 02 §3.13).
EGRESS_TOOLS = frozenset({
    "send_message", "send_email", "reply_to_email", "forward_email", "schedule_send",
    "draft_message", "draft_email", "web_search", "web_fetch", "run_shell", "run_applescript",
})
EGRESS_CATEGORIES = frozenset({"mcp_action"})

# Collections seed() accepts.
SEEDABLE = ("calendar", "reminders", "notes", "contacts", "mail", "messages", "files",
            "memory", "device")
_NOT_SEEDABLE = {
    "weather": "Toolbench's get_weather computes forecasts from the place name; use the world's "
               "own facts (e.g. its home city) instead",
    "web": "Toolbench's web_search and web_fetch return canned pages chosen by the query or URL",
    "stocks": "Toolbench's get_stock_price has a fixed price table",
}

_DEVICE_IDS = ("volume", "muted", "locked", "front", "music", "spotify", "apps")
_STATUS_MAP = {"planned": "succeeded", "preview": "succeeded", "unsupported": "failed"}

# ------------------------------------------------------------ Harbor, imported once

_SETUP_LOCK = threading.Lock()
_TOOLS: dict[str, Any] | None = None       # name -> registry Tool (own copies)
_SKILLS: dict[str, Any] = {}               # the bundled skills register_extra_tools loaded


def _ensure_service_home() -> None:
    """Keep every service.* import on a throwaway HARBOR_HOME, never the real one."""
    real = (Path.home() / ".moe").resolve()
    if "service" not in sys.modules:
        current = os.environ.get("HARBOR_HOME")
        if not current or Path(current).expanduser().resolve() == real:
            os.environ["HARBOR_HOME"] = tempfile.mkdtemp(prefix="edat-assistant-")
    from service.paths import MOE_DIR
    if MOE_DIR.resolve() == real:
        raise RuntimeError("edat assistant lane: service.* was imported with the real Harbor home; "
                           "set HARBOR_HOME to a throwaway directory before importing it")


def _harbor_tools() -> dict[str, Any]:
    """The lane's tool table: Harbor's registry plus Toolbench's startup extras."""
    global _TOOLS, _SKILLS
    with _SETUP_LOCK:
        if _TOOLS is not None:
            return _TOOLS
        _ensure_service_home()
        import service.tools  # noqa: F401  registers the roster
        from service import skills
        from service.mcp import TOOL_PARTS
        from service.tools.registry import REGISTRY
        from scripts.toolbench import world as W

        core = {n: t for n, t in REGISTRY.items() if not t.internal and not t.unavailable_reason}
        saved_registry, saved_parts = dict(REGISTRY), dict(TOOL_PARTS)
        saved_skills = dict(skills._skills)
        skill_tools = sys.modules.get("service.skills.tools")
        saved_registered = set(skill_tools._registered) if skill_tools else None
        try:
            added = W.register_extra_tools()
            extras = {n: REGISTRY[n] for n in added if n in REGISTRY}
            loaded = dict(skills._skills)
        finally:
            # use_skill is registered by importing service.skills.tools; that cannot be undone
            # (and the Toolbench runner relies on it), so it alone stays registered.
            keep_use_skill = REGISTRY.get("use_skill") if "use_skill" not in saved_registry else None
            REGISTRY.clear()
            REGISTRY.update(saved_registry)
            if keep_use_skill is not None:
                REGISTRY["use_skill"] = keep_use_skill
            TOOL_PARTS.clear()
            TOOL_PARTS.update(saved_parts)
            skills._skills = saved_skills
            from service.skills import tools as skill_tools_mod
            skill_tools_mod._registered.clear()
            skill_tools_mod._registered.update(saved_registered or set())
        faked = set(W.make_fakes(W.build_world(date(2026, 1, 5))))
        user_skills = any(not getattr(s, "bundled", False) for s in loaded.values())
        for name, tool in extras.items():
            if name not in faked:
                continue
            if user_skills and (tool.category == "skill_tool" or name == "use_skill"):
                continue      # never offer a tool or text that came from a non-bundled skill
            core.setdefault(name, tool)
        _SKILLS = {} if user_skills else loaded
        _TOOLS = {n: dataclasses.replace(t, parameters=copy.deepcopy(t.parameters),
                                         aliases=list(t.aliases))
                  for n, t in sorted(core.items())}
        return _TOOLS


# ------------------------------------------------------------ small helpers

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:40].strip("-") or "x"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def _clock_str(dt: datetime) -> str:
    """The World's clock strings: '3:30 PM'."""
    return dt.strftime("%I:%M %p").lstrip("0")


def _date_only(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"\s*\d{4}-\d{2}-\d{2}\s*", value))


def _set(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def _run_awaitable(aw):
    async def runner():
        return await aw
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(runner())
    box: dict[str, Any] = {}

    def target():
        try:
            box["value"] = asyncio.run(runner())
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            box["error"] = exc
    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ------------------------------------------------------------ the environment

class AssistantEnvironment:
    """Harbor's tools over a private Toolbench World (see the module docstring)."""

    lane = "assistant"

    def __init__(self, clock: Clock | None = None, *, world_seed: int | None = None) -> None:
        self.clock = clock or Clock.parse()
        self._tools = _harbor_tools()
        from scripts.toolbench import world as W
        self._W = W
        w = W.build_world(self.clock.today, world_seed)
        shift = self.clock.now - w.now
        w.now = self.clock.now
        for t in w.timers:            # the canonical timer was set relative to the build clock
            t["deadline"] = t["deadline"] + shift
        w.snapshot()
        self.world = w
        self._fakes = W.make_fakes(w)
        self._used: dict[str, set] = {}
        self._msg_seq = 0
        self._completed: list[dict] = []          # reminders complete_reminder removed
        self._cancelled_timers: list[dict] = []
        self._forgotten: list[dict] = []
        self._inactive_reminders: list[dict] = []  # seeded done reminders (state only)
        self._inactive_memory: list[dict] = []     # seeded forgotten/superseded facts (state only)
        self._outbound: dict[int, dict] = {}       # world.effects index -> resolved recipients
        self._file_meta: dict[str, dict] = {}      # seeded file/folder path -> case metadata
        self._tag()

    # ---------------------------------------------------------------- menu
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def schemas(self, names: list[str] | None = None) -> list[dict]:
        if names is None:
            return [t.schema() for t in self._tools.values()]
        unknown = [n for n in names if n not in self._tools]
        if unknown:
            raise ValueError(f"not tools of the assistant lane: {unknown}")
        return [self._tools[n].schema() for n in names]

    def info(self, name: str) -> ToolInfo | None:
        tool = self._tools.get(name)
        if tool is None:
            return None
        from scripts.toolbench.cases import ALWAYS_ASK
        return ToolInfo(name=name, effect=tool.category not in self._W._READ_CATEGORIES,
                        egress=name in EGRESS_TOOLS or tool.category in EGRESS_CATEGORIES,
                        confirm=name in ALWAYS_ASK, domain=tool.category)

    def system_prompt(self, *, policy: str | None = None, preferences: list[str] | None = None,
                      offered: list[str] | None = None) -> str:
        """Harbor's agent-turn prompt for `offered` (default: the whole menu)."""
        from edat.state.assistant_prompt import build_prompt
        return build_prompt(set(offered if offered is not None else self._tools), self.clock,
                            policy=policy, preferences=preferences)

    # ---------------------------------------------------------------- calls
    def call(self, name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"(unknown tool: {name})", outcome="failed", meta={"unknown_tool": True})
        from service.tools.registry import (DisplayOnlyToolResult, _arg_hint, _validate_args,
                                            classify_tool_outcome)
        args = {k: v for k, v in arguments.items() if k} if isinstance(arguments, dict) else arguments
        if (problem := _validate_args(tool, args)) is not None:
            return ToolResult(problem, outcome="failed", meta={"category": tool.category,
                                                               "invalid_args": True})
        w = self.world
        fn = self._fakes.get(name) or self._W.generic_fake(w, name, tool.category)
        effects_before = len(w.effects)
        before = self._fingerprint()
        held = {"reminders": list(w.reminders), "timers": list(w.timers),
                "memory": list(w.memory_facts)}
        status = "failed"
        try:
            with self._skill_context(name):
                result = fn(**args)
                if inspect.isawaitable(result):
                    result = _run_awaitable(result)
        except TypeError as exc:
            text = (f"(error calling {name}({args!r}): {exc}. Expected arguments — "
                    f"{_arg_hint(tool)}. Call it again with corrected arguments.)")
        except Exception as exc:  # noqa: BLE001 — the model sees it, as in Harbor
            text = f"(error running {name}: {exc})"
        else:
            if isinstance(result, DisplayOnlyToolResult):
                text = DisplayOnlyToolResult.model_text
            else:
                text = str(result)
            status = classify_tool_outcome(name, result).status
        outcome = _STATUS_MAP.get(status, status)
        if outcome not in OUTCOMES:
            outcome = "unknown"
        changed = len(w.effects) > effects_before or self._fingerprint() != before
        self._after_call(name, args, effects_before, held)
        return ToolResult(text, outcome=outcome, effect=changed,
                          meta={"category": tool.category, "harbor_status": status})

    @contextmanager
    def _skill_context(self, name: str):
        if name not in ("use_skill", "harbor_skills"):
            yield
            return
        from service import skills
        saved = skills._skills
        skills._skills = dict(_SKILLS)
        try:
            yield
        finally:
            skills._skills = saved

    def _fingerprint(self) -> dict:
        w = self.world
        return copy.deepcopy({k: getattr(w, k) for k in self._W._MUTABLE
                              if k not in ("previews", "next_id")})

    def _after_call(self, name: str, args: dict, effects_before: int, held: dict) -> None:
        w = self.world
        live = {id(r) for r in w.reminders}
        for r in held["reminders"]:
            if id(r) not in live and name == "complete_reminder":
                self._completed.append(r)
        live = {id(t) for t in w.timers}
        self._cancelled_timers += [t for t in held["timers"] if id(t) not in live]
        live = {id(m) for m in w.memory_facts}
        if name == "forget":
            self._forgotten += [m for m in held["memory"] if id(m) not in live]
        new_effects = list(enumerate(w.effects))[effects_before:]
        if name == "remember" and new_effects and _set(args.get("supersedes_id")):
            sid = str(args["supersedes_id"])
            fact = w.memory_facts[-1]
            fact.setdefault("_edat_meta", {})["supersedes_id"] = args["supersedes_id"]
            for m in w.memory_facts[:-1]:
                if str(m.get("_edat_id", m["id"])) == sid:
                    m["_edat_status"] = "superseded"
        for index, effect in new_effects:
            self._outbound[index] = self._resolve_outbound(effect, args)
        self._tag()

    def _resolve_outbound(self, effect: dict, call_args: dict) -> dict:
        """Recipients of one send or draft, resolved the way the fake resolved them."""
        W, w = self._W, self.world
        tool, a = effect["tool"], effect["args"]
        out: dict[str, Any] = {}
        try:
            if tool in ("send_message", "draft_message"):
                to = str(a.get("to") or "").strip()
                literal = W._EMAIL_RE.match(to) or re.fullmatch(r"[+()\-.\s\d]{7,}", to)
                handle = to if literal else W._resolve_recipient(w, to)[0]
                out = {"handle": handle, "contact_ids": self._contact_ids([handle])}
                label = next((m["from"] for m in w.messages if m["from"].casefold() == to.casefold()), None)
                out["thread"] = label
            elif tool in ("send_email", "draft_email"):
                to = W._emails_for(w, a.get("to") or "")[0]
                cc = W._emails_for(w, call_args.get("cc"))[0] if W._split_recipients(call_args.get("cc")) else []
                out = {"to": to, "cc": cc, "contact_ids": self._contact_ids(to + cc)}
            elif tool == "forward_email":
                to = W._emails_for(w, a.get("to") or "")[0]
                src = next((m for m in w.emails if m["id"] == a.get("message_id")), None)
                out = {"to": to, "contact_ids": self._contact_ids(to),
                       "subject": f"Fwd: {src['subject']}" if src else "",
                       "body": str(call_args.get("note") or "")}
            elif tool == "reply_to_email":
                src = next((m for m in w.emails if m["id"] == a.get("message_id")), None)
                to = [W._addr(src["from"])] if src else []
                out = {"to": to, "contact_ids": self._contact_ids(to),
                       "subject": f"Re: {src['subject']}" if src else "",
                       "reply_all": bool(call_args.get("reply_all"))}
        except Exception:  # noqa: BLE001 — resolution is a grading aid, never a call failure
            pass
        return out

    def _contact_ids(self, handles: list[str]) -> list:
        W = self._W
        keys = {W._handle_key(h) for h in handles if h}
        keys.discard("")
        return [c["_edat_id"] for c in self.world.contacts
                if keys & {W._handle_key(c.get("phone")), W._handle_key(c.get("email"))} - {""}]

    # ---------------------------------------------------------------- ids
    def _claim(self, collection: str, wanted: str, *, fallback: str = "") -> str:
        used = self._used.setdefault(collection, set())
        for candidate in (wanted, f"{wanted}-{fallback}" if fallback else None):
            if candidate and candidate not in used:
                used.add(candidate)
                return candidate
        n = 2
        while f"{wanted}-{n}" in used:
            n += 1
        used.add(f"{wanted}-{n}")
        return f"{wanted}-{n}"

    def _reserve(self, collection: str, record_id: Any) -> None:
        used = self._used.setdefault(collection, set())
        if record_id in used:
            raise ValueError(f"{collection}: id {record_id!r} is already used in this world")
        used.add(record_id)

    def _tag(self) -> None:
        """Give every untagged World record its stable id (deterministic order)."""
        w = self.world
        for e in w.events + w.past_events:
            if "_edat_id" not in e:
                e["_edat_id"] = self._claim("calendar", f"ev-{_slug(e['title'])}",
                                            fallback=e["day"].isoformat())
        for r in w.reminders:
            if "_edat_id" not in r:
                r["_edat_id"] = self._claim("reminders", f"rem-{_slug(r['title'])}")
        for n in w.notes:
            if "_edat_id" not in n:
                n["_edat_id"] = self._claim("notes", f"note-{_slug(n['title'])}")
        for c in w.contacts:
            if "_edat_id" not in c:
                c["_edat_id"] = self._claim("contacts", f"c-{_slug(c['name'])}")
        for m in w.emails:
            if "_edat_id" not in m:
                m["_edat_id"] = self._claim("mail", m["id"], fallback=m.get("account", ""))
        for m in w.messages:
            if "_edat_id" not in m:
                self._msg_seq += 1
                m["_edat_id"] = self._claim("messages", f"msg-{self._msg_seq}")
        for m in w.memory_facts:
            if "_edat_id" not in m:
                m["_edat_id"] = self._claim("memory", m["id"])

    # ---------------------------------------------------------------- seeding
    def seed(self, records: dict[str, list[dict]]) -> None:
        for name in records:
            if name in _NOT_SEEDABLE:
                raise ValueError(f"assistant lane cannot seed {name!r}: {_NOT_SEEDABLE[name]}")
            if name not in SEEDABLE:
                raise ValueError(f"assistant lane cannot seed collection {name!r}; "
                                 f"supported: {', '.join(SEEDABLE)}")
        for name, rows in records.items():
            for raw in rows or []:
                if not isinstance(raw, dict):
                    raise ValueError(f"{name}: each record must be a mapping, got {raw!r}")
                getattr(self, f"_seed_{name}")(self.clock.resolve_tree(raw))
        self._tag()
        self.world.snapshot()

    def _check(self, collection: str, rec: dict, *, required: tuple, served: tuple,
               unserved: dict[str, str]) -> dict:
        missing = [f for f in required if rec.get(f) in (None, "")]
        if missing:
            raise ValueError(f"{collection}: record {rec.get('id')!r} is missing {missing}")
        for field, why in unserved.items():
            if _set(rec.get(field)):
                raise ValueError(f"{collection}: field {field!r} (record {rec.get('id')!r}) cannot be "
                                 f"served by Toolbench's fakes: {why}")
        return {k: copy.deepcopy(v) for k, v in rec.items()
                if k not in served and k not in unserved and k not in required}

    def _time(self, collection: str, rec: dict, field: str, default: datetime | None = None) -> datetime:
        value = rec.get(field)
        if value in (None, ""):
            if default is None:
                raise ValueError(f"{collection}: record {rec.get('id')!r} needs {field!r}")
            return default
        parsed = parse_local(str(value))
        if parsed is None:
            raise ValueError(f"{collection}: {field}={value!r} is not a local ISO time")
        return parsed.replace(tzinfo=None)

    def _seed_calendar(self, rec: dict) -> None:
        meta = self._check("calendar", rec, required=("id", "title", "start"),
                           served=("end", "location", "attendees", "calendar", "writable", "all_day",
                                   "organizer"),
                           unserved={"notes": "no calendar read shows event notes",
                                     "rrule": "recurrence is not modelled; seed each occurrence"})
        start = self._time("calendar", rec, "start")
        all_day = bool(rec.get("all_day")) or _date_only(rec["start"])
        end = self._time("calendar", rec, "end",
                         start + (timedelta(days=1) if all_day else timedelta(minutes=60)))
        minutes = int((end - start).total_seconds() // 60)
        if minutes < 1:
            raise ValueError(f"calendar: record {rec['id']!r} ends before it starts")
        attendees = list(rec.get("attendees") or [])
        organizer = str(rec.get("organizer") or "")
        self._reserve("calendar", rec["id"])
        ev = self._W._event(str(rec["title"]), start.date(), _clock_str(start),
                            str(rec.get("location") or ""), calendar=str(rec.get("calendar") or "Home"),
                            writable=bool(rec.get("writable", True)), invitees=len(attendees),
                            organizer_is_user=not organizer, organizer=organizer, duration=minutes,
                            all_day=all_day,
                            days=max(1, (end.date() - start.date()).days) if all_day else 1)
        ev["account"] = "iCloud"
        ev.update({"_edat_id": rec["id"], "_edat_meta": meta, "_edat_attendees": attendees})
        (self.world.past_events if start.date() < self.clock.today else self.world.events).append(ev)

    def _seed_reminders(self, rec: dict) -> None:
        meta = self._check("reminders", rec, required=("id", "title"), served=("due", "done"),
                           unserved={"notes": "no reminder read shows notes",
                                     "list": "reminder lists are not modelled"})
        self._reserve("reminders", rec["id"])
        due = rec.get("due")
        when = self._time("reminders", rec, "due") if _set(due) else None
        all_day = _date_only(due) if _set(due) else False
        w = self.world
        w.next_id["rseq"] = w.next_id.get("rseq", 0) + 1
        r = {"title": str(rec["title"]), "due": when.date() if when else None,
             "time": "" if (when is None or all_day) else _clock_str(when), "all_day": all_day,
             "created": self.clock.today, "seq": w.next_id["rseq"], "useq": w.next_id["rseq"],
             "_edat_id": rec["id"], "_edat_meta": meta}
        if rec.get("done"):
            self._inactive_reminders.append(r)
        else:
            w.reminders.append(r)

    def _seed_notes(self, rec: dict) -> None:
        meta = self._check("notes", rec, required=("id", "title"), served=("body",),
                           unserved={"folder": "note folders are not modelled"})
        self._reserve("notes", rec["id"])
        self.world.notes.append({"title": str(rec["title"]), "body": str(rec.get("body") or ""),
                                 "_edat_id": rec["id"], "_edat_meta": meta})

    def _seed_contacts(self, rec: dict) -> None:
        meta = self._check("contacts", rec, required=("id", "name"), served=("phone", "email"),
                           unserved={"notes": "lookup_contact shows names and handles only",
                                     "birthday": "contact_dates is fixed in Toolbench",
                                     "relation": "contact relations are not modelled"})
        self._reserve("contacts", rec["id"])
        name = str(rec["name"])
        self.world.contacts.append({"name": name, "full": name, "email": str(rec.get("email") or ""),
                                    "phone": str(rec.get("phone") or ""),
                                    "_edat_id": rec["id"], "_edat_meta": meta})

    def _seed_mail(self, rec: dict) -> None:
        W = self._W
        meta = self._check("mail", rec, required=("id", "from", "subject"),
                           served=("body", "received", "unread", "to", "folder"), unserved={})
        if (rec.get("folder") or "inbox") != "inbox":
            raise ValueError(f"mail: record {rec['id']!r}: folder {rec['folder']!r} cannot be served "
                             "by Toolbench's fakes (the world has an inbox only)")
        to = rec.get("to") or []
        to = [to] if isinstance(to, str) else list(to)
        own = [a.lower() for a in W.USER_EMAILS]
        if any(str(a).strip().lower() not in own for a in to):
            raise ValueError(f"mail: record {rec['id']!r}: `to` may name only the user's own addresses "
                             f"({', '.join(W.USER_EMAILS)}); view_emails shows the account address")
        account = {own[1]: "work", own[2]: "gmail"}.get(str(to[0]).strip().lower(), "personal") if to \
            else "personal"
        if rec["id"] in {m["id"] for m in self.world.emails}:
            raise ValueError(f"mail: id {rec['id']!r} is already used in this world")
        self._reserve("mail", rec["id"])
        received = self._time("mail", rec, "received", self.clock.now)
        m = W._mail(str(rec["id"]), str(rec["from"]), account, str(rec["subject"]),
                    (received.date() - self.clock.today).days, _clock_str(received),
                    bool(rec.get("unread", True)), str(rec.get("body") or ""), "people")
        m.update({"day": received.date(), "_edat_id": rec["id"], "_edat_meta": meta})
        self.world.emails.append(m)

    def _seed_messages(self, rec: dict) -> None:
        meta = self._check("messages", rec, required=("id", "thread", "from", "text"),
                           served=("received", "unread", "group"), unserved={})
        self._reserve("messages", rec["id"])
        received = self._time("messages", rec, "received", self.clock.now)
        group = bool(rec.get("group") or rec.get("members"))
        m = self._W._text(str(rec["thread"]), str(rec["from"]), str(rec["text"]),
                          (received.date() - self.clock.today).days, _clock_str(received),
                          bool(rec.get("unread", True)), group=group)
        m.update({"day": received.date(), "_edat_id": rec["id"], "_edat_meta": meta})
        self.world.messages.append(m)

    def _seed_files(self, rec: dict) -> None:
        meta = self._check("files", rec, required=("path",), served=("id", "kind", "text"),
                           unserved={"size": "no file read reports sizes"})
        path = str(rec["path"]).rstrip("/")
        if rec.get("id") not in (None, "", path):
            raise ValueError(f"files: id must equal the path ({path!r}), got {rec['id']!r}")
        if not path.startswith("~/") or path.count("/") < 1:
            raise ValueError(f"files: path {path!r} must start with '~/'")
        kind = rec.get("kind") or "file"
        if kind not in ("file", "folder"):
            raise ValueError(f"files: kind must be file or folder, got {kind!r}")
        w = self.world
        if kind == "folder":
            if _set(rec.get("text")):
                raise ValueError(f"files: folder {path!r} cannot have text")
            if path in w.files:
                raise ValueError(f"files: folder {path!r} already exists")
            w.files[path] = []
        else:
            folder, name = path.rsplit("/", 1)
            if not name or folder == "~":
                raise ValueError(f"files: path {path!r} must name a file inside a folder")
            if name in w.files.get(folder, []):
                raise ValueError(f"files: {path!r} already exists")
            text = rec.get("text")
            if _set(text):
                if "Documents" not in folder:
                    raise ValueError(f"files: text for {path!r} cannot be served: Toolbench's read_file "
                                     "reads only files under ~/Documents")
                if name in w.documents:
                    raise ValueError(f"files: read_file keys text by file name and {name!r} already "
                                     "has text in this world")
                w.documents[name] = str(text)
            w.files.setdefault(folder, []).append(name)
        if meta:
            self._file_meta[path] = meta

    def _seed_memory(self, rec: dict) -> None:
        meta = self._check("memory", rec, required=("text",), served=("id", "category", "status"),
                           unserved={})
        w = self.world
        status = rec.get("status") or "active"
        if status not in ("active", "superseded", "forgotten"):
            raise ValueError(f"memory: unknown status {status!r}")
        mid = rec.get("id")
        if mid in (None, ""):
            mid = w.next_id["mem"] = w.next_id.get("mem", 0) + 1
        elif isinstance(mid, int) and mid > w.next_id.get("mem", 0):
            w.next_id["mem"] = mid
        if mid in {m["id"] for m in w.memory_facts}:
            raise ValueError(f"memory: id {mid!r} is already used in this world")
        self._reserve("memory", mid)
        fact = {"id": mid, "text": str(rec["text"]), "category": str(rec.get("category") or "fact"),
                "origin": "explicit", "date": self.clock.today, "_edat_id": mid, "_edat_meta": meta}
        if status == "active":
            w.memory_facts.append(fact)
        else:
            fact["_edat_status"] = status
            self._inactive_memory.append(fact)

    def _seed_device(self, rec: dict) -> None:
        if not _set(rec.get("id")) or "value" not in rec:
            raise ValueError(f"device: each record needs id and value, got {rec!r}")
        key, value = rec["id"], rec["value"]
        mac = self.world.mac
        if key == "volume":
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("device: volume must be a whole number 0-100")
            mac["volume"] = value
        elif key in ("muted", "locked"):
            mac[key] = bool(value)
        elif key == "front":
            mac["front"] = str(value)
            if str(value) not in self.world.apps:
                self.world.apps.append(str(value))
        elif key in ("music", "spotify"):
            if value not in ("playing", "paused"):
                raise ValueError(f"device: {key} must be playing or paused")
            mac["media"][key] = value
        elif key == "apps":
            self.world.apps = [str(a) for a in value]
        else:
            raise ValueError(f"device: {key!r} cannot be served by Toolbench's fakes; "
                             f"supported ids: {', '.join(_DEVICE_IDS)}")

    # ---------------------------------------------------------------- state
    def state(self) -> StateStore:
        W, w = self._W, self.world
        out: dict[str, list[dict]] = {}

        def with_meta(row: dict, rec: dict) -> dict:
            for k, v in (rec.get("_edat_meta") or {}).items():
                row.setdefault(k, copy.deepcopy(v))
            return row

        cal = []
        for e in w.events + w.past_events:
            start, end, all_day = W._ev_times(e)
            row = {"id": e["_edat_id"], "title": e["title"],
                   "start": start.date().isoformat() if all_day else _iso(start),
                   "end": end.date().isoformat() if all_day else _iso(end),
                   "location": e.get("location") or "", "calendar": e.get("calendar"),
                   "writable": e.get("calendar_writable") is not False, "all_day": all_day,
                   "invitee_count": e.get("invitee_count"), "organizer": e.get("organizer") or "",
                   "account": e.get("account"), "recurring": bool(e.get("recurring"))}
            if "_edat_attendees" in e:
                row["attendees"] = list(e["_edat_attendees"])
            cal.append(with_meta(row, e))
        out["calendar"] = sorted(cal, key=lambda r: (r["start"], str(r["id"])))

        def reminder_row(r: dict, done: bool) -> dict:
            due = W._rem_due(r)
            due_s = None if due is None else (due.date().isoformat() if r.get("all_day") else _iso(due))
            return with_meta({"id": r["_edat_id"], "title": r["title"], "due": due_s, "done": done,
                              "all_day": bool(r.get("all_day"))}, r)
        out["reminders"] = ([reminder_row(r, False) for r in w.reminders]
                            + [reminder_row(r, True) for r in self._completed + self._inactive_reminders])

        out["notes"] = [with_meta({"id": n["_edat_id"], "title": n["title"], "body": n.get("body") or ""}, n)
                        for n in w.notes]
        out["contacts"] = [with_meta({"id": c["_edat_id"], "name": c["name"], "full": c.get("full") or "",
                                      "phone": c.get("phone") or "", "email": c.get("email") or ""}, c)
                           for c in w.contacts]

        triage = {}
        for fx in w.effects:
            if fx["tool"] == "email_triage" and fx["args"].get("action") == "archive":
                triage[fx["args"].get("message_id")] = "archive"
        out["mail"] = [with_meta({"id": m["_edat_id"], "from": m["from"], "subject": m["subject"],
                                  "body": m["body"], "received": _iso(W._mail_ts(w, m)),
                                  "unread": bool(m["unread"]), "to": [W._account_to(m["account"])],
                                  "account": m["account"], "folder": triage.get(m["id"], "inbox")}, m)
                       for m in w.emails]
        out["messages"] = [with_meta({"id": m["_edat_id"], "thread": m["from"], "from": m["sender"],
                                      "text": m["text"], "received": _iso(W._msg_ts(w, m)),
                                      "unread": bool(m["unread"]), "group": bool(m.get("group"))}, m)
                           for m in w.messages]

        files = []
        for folder in sorted(w.files):
            files.append(with_meta({"id": folder, "path": folder, "kind": "folder"},
                                   {"_edat_meta": self._file_meta.get(folder)}))
            for name in w.files[folder]:
                path = f"{folder}/{name}"
                row = {"id": path, "path": path, "kind": "file"}
                if "Documents" in folder and name in w.documents:
                    row["text"] = w.documents[name]
                files.append(with_meta(row, {"_edat_meta": self._file_meta.get(path)}))
        out["files"] = files

        def memory_row(m: dict, status: str) -> dict:
            return with_meta({"id": m["_edat_id"], "text": m["text"], "category": m.get("category"),
                              "status": status, "origin": m.get("origin"),
                              "date": m["date"].isoformat() if isinstance(m.get("date"), date)
                              else m.get("date")}, m)
        out["memory"] = ([memory_row(m, m.get("_edat_status", "active")) for m in w.memory_facts]
                         + [memory_row(m, "forgotten") for m in self._forgotten]
                         + [memory_row(m, m["_edat_status"]) for m in self._inactive_memory])

        def timer_row(t: dict, status: str) -> dict:
            left = (t["deadline"] - w.now).total_seconds()
            return {"id": t["id"], "label": t.get("label") or "", "kind": t["kind"],
                    "ends_at": _iso(t["deadline"]), "minutes": max(0, math.ceil(left / 60)),
                    "status": status}
        out["timers"] = ([timer_row(t, "running") for t in w.timers]
                         + [timer_row(t, "cancelled") for t in self._cancelled_timers])

        out["scheduled_sends"] = [
            {"id": s["id"], "channel": s["channel"], "to": s["recipient"], "display": s.get("display"),
             "when": _iso(s["when"]), "text": s["body"], "subject": s.get("subject") or "",
             "status": {"pending": "queued"}.get(s["status"], s["status"])}
            for s in w.scheduled_sends]

        device = {"volume": w.mac.get("volume"), "muted": w.mac.get("muted"),
                  "locked": w.mac.get("locked"), "front": w.mac.get("front"),
                  "music": (w.mac.get("media") or {}).get("music"),
                  "spotify": (w.mac.get("media") or {}).get("spotify"),
                  "apps": sorted(w.apps)}
        if w.mac.get("stopwatch") is not None:
            device["stopwatch"] = "running"
        for fx in w.effects:
            if fx["tool"] == "toggle_setting":
                on = fx["args"].get("on")
                device[str(fx["args"].get("setting")).strip().lower()] = on in (True, "true", "on")
        out["device"] = [{"id": k, "value": copy.deepcopy(v)} for k, v in device.items()]

        sent_msg, sent_mail, msg_drafts, mail_drafts = [], [], [], []
        for i, fx in enumerate(w.effects):
            tool, a, res = fx["tool"], fx["args"], self._outbound.get(i, {})
            if tool == "send_message":
                sent_msg.append({"id": f"msg-sent-{len(sent_msg) + 1}", "to": a.get("to"),
                                 "text": a.get("text"), "thread": res.get("thread"),
                                 "handle": res.get("handle"), "contact_ids": res.get("contact_ids", [])})
            elif tool == "draft_message":
                msg_drafts.append({"id": f"msg-draft-{len(msg_drafts) + 1}", "to": a.get("to"),
                                   "text": a.get("text"), "handle": res.get("handle"),
                                   "contact_ids": res.get("contact_ids", [])})
            elif tool == "draft_email":
                mail_drafts.append({"id": f"mail-draft-{len(mail_drafts) + 1}", "to": res.get("to", []),
                                    "to_given": a.get("to"), "cc": res.get("cc", []),
                                    "subject": a.get("subject"), "body": a.get("body"),
                                    "contact_ids": res.get("contact_ids", [])})
            elif tool in ("send_email", "reply_to_email", "forward_email"):
                kind = {"send_email": "send", "reply_to_email": "reply", "forward_email": "forward"}[tool]
                row = {"id": f"mail-sent-{len(sent_mail) + 1}", "kind": kind, "to": res.get("to", []),
                       "to_given": a.get("to"), "cc": res.get("cc", []),
                       "subject": a.get("subject", res.get("subject", "")),
                       "body": a.get("body", res.get("body", "")),
                       "contact_ids": res.get("contact_ids", [])}
                if kind != "send":
                    row["in_reply_to"] = a.get("message_id")
                if kind == "reply":
                    row["reply_all"] = res.get("reply_all", False)
                sent_mail.append(row)
        out["messages_sent"], out["mail_sent"] = sent_msg, sent_mail
        out["message_drafts"], out["mail_drafts"] = msg_drafts, mail_drafts
        return StateStore(out, clock=self.clock)
