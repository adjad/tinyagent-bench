"""Pushes the sandbox world to the sandbox backend as the real Swift readers
would — one asyncio.Task per source, same independent cadences (see the
module-level SOURCES table), same "fire immediately at boot" behavior. A
`sync_scale` multiplier compresses those cadences proportionally so a test
run doesn't have to wait 5 real minutes to see a messages sync land; ratios
are preserved on purpose; a source dropping to a shorter *relative* wait
than another would look like a fidelity difference that isn't real.

Endpoints/payloads here are pinned against the actual handlers in
service/main.py (verified 2026-08 against source, not inferred):
  sync/messages   {"lines": str, "diagnostics": {...}}  AND, separately,
                  {"contacts": {handle: name}}           (short-circuits);
                  `lines` leads with MessagesReader's V3 block (one record
                  per message: chat GUID, delivery service, sender handle),
                  which is how Harbor ties a text to a saved contact
  sync/emails     up to 4 independent single-key payloads: headers/raw/
                  history/identity_emails, plus a `diagnostics` object
  sync/notes      {"raw": str}
  sync/browser_history  {"browser", "lines", "diagnostics"} per browser
  sync/calendar   {"events":[...], "diagnostics":{...}}  (source="calendar"),
                  and again with "source":"reminders" for reminders
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid

import httpx

from sandbox.world import World
from tests.fixtures import wire

# (base_period_seconds, push_fn_name) — base_period is the REAL cadence;
# effective period = max(2.0, base * sync_scale).
CADENCES: dict[str, float] = {
    "messages": 300.0,
    "contacts": 21600.0,
    "email_headers": 300.0,
    "email_raw": 900.0,
    "email_history": 1800.0,
    "notes": 86400.0,
    "browser": 1800.0,
    "calendar": 60.0,
    "reminders": 60.0,
}


_SERVICE_NAMES = {s.casefold(): s for s in wire.SERVICES}


def _chat_handle(raw: str) -> str:
    """A handle as chat.db stores it: an address lowercased, a phone number in
    full international form ("+17735550124"). A number written without a
    country code is taken as a North American one when it has 10 digits (the
    benchmark profiles' numbers); a short code stays as its digits; anything
    else that is not a number or an address is no handle ("")."""
    raw = (raw or "").strip()
    if "@" in raw:
        return raw.casefold()
    digits = re.sub(r"\D", "", raw)
    if not digits or not re.fullmatch(r"[+(\d][\d\s().-]*", raw):
        return ""
    if len(digits) < 7:
        return digits
    if raw.startswith("+"):
        return "+" + digits
    if raw.startswith("00"):
        return "+" + digits[2:]
    if len(digits) == 10:
        return "+1" + digits
    return "+" + digits


def _contact_handle(contact: dict) -> str:
    """The handle a saved contact texts from: their first phone number, else
    their first address."""
    handles = [str(h) for h in contact.get("handles") or [] if h]
    phones = [h for h in handles if "@" not in h]
    return _chat_handle((phones or handles or [""])[0])


def _sender_handle(world: World, thread: dict, m: dict) -> str:
    """The handle an incoming message came from. A world built from a profile
    names the contact (`sender_id`); otherwise `from` is a display name, matched
    to the thread's own members first, or a raw number or address."""
    contacts = world.state["contacts"]
    if m.get("sender_id") in contacts:
        return _contact_handle(contacts[m["sender_id"]])
    name = str(m.get("from") or "").strip()
    members = [contacts[p] for p in thread.get("participants") or [] if p in contacts]
    for pool in (members, list(contacts.values())):
        found = [c for c in pool if str(c.get("name") or "").strip().casefold() == name.casefold()]
        if len(found) == 1:
            return _contact_handle(found[0])
    return _chat_handle(name)


def _chat_guid(thread: dict, service: str, incoming: list[str], world: World) -> str:
    """The chat's GUID as chat.db has it: "<service>;-;<handle>" for a one-to-one
    chat, "<service>;+;chat<digits>" for a group. "" when a one-to-one chat's
    other party has no handle."""
    if thread["kind"] == "group":
        digits = int(hashlib.sha256(str(thread["id"]).encode()).hexdigest()[:15], 16)
        return f"{service};+;chat{digits:018d}"
    contacts = world.state["contacts"]
    members = [contacts[p] for p in thread.get("participants") or [] if p in contacts]
    handle = (_contact_handle(members[0]) if members else "") \
        or next((h for h in incoming if h), "") or _chat_handle(thread.get("context_label") or "")
    return f"{service};-;{handle}" if handle else ""


def _message_rows(world: World) -> list[dict]:
    """Every message, oldest first, with what the real reader knows of it: the
    conversation label, the sender as shown, and its chat GUID, message GUID,
    delivery service and sender handle.

    Two messages are never given the same time: chat.db times are nanoseconds,
    but a profile's are whole minutes, and proactive/sources.text_records joins
    a plain line to its structured record by time, so a tie would leave both
    lines with no known chat. A tie is broken by a millisecond, in order."""
    found = []
    for thread in world.state["threads"].values():
        for index, m in enumerate(thread["messages"]):
            found.append((world.abs_ts(m["ts_off"]), str(thread["id"]), index, thread, m))
    found.sort(key=lambda r: r[:3])
    used: set[float] = set()
    rows: list[dict] = []
    guids: dict[str, str] = {}
    for base, tid, index, thread, m in found:
        ts, k = base, 0
        while round(ts, 3) in used:
            k += 1
            ts = base + k / 1000
        used.add(round(ts, 3))
        label = thread.get("context_label")
        if not label:
            names = [world.state["contacts"].get(p, {}).get("name", p)
                    for p in thread.get("participants") or []]
            label = wire.thread_context(thread.get("title"), names,
                                        is_group=(thread["kind"] == "group"))
        mine = m["from"] == "me"
        service = _SERVICE_NAMES.get(str(m.get("service") or thread.get("service") or "iMessage").casefold(), "")
        rows.append({"ts": ts, "context": label, "who": "Me" if mine else m["from"], "text": m["text"],
                     "thread": thread, "direction": "outgoing" if mine else "incoming",
                     "sender": "Me" if mine else _sender_handle(world, thread, m),
                     "service": service,
                     "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"sandbox-message:{tid}:{m.get('id') or index}")).upper()})
    for thread in world.state["threads"].values():
        tid = str(thread["id"])
        incoming = [r["sender"] for r in rows if r["thread"] is thread and r["direction"] == "incoming"]
        service = next((r["service"] for r in rows if r["thread"] is thread and r["service"]), "iMessage")
        guids[tid] = _chat_guid(thread, service, incoming, world)
    for r in rows:
        r["chat_guid"] = guids[str(r.pop("thread")["id"])]
    return rows


def _messages_lines(world: World) -> str:
    """The Messages carrier as MessagesReader.swift posts it: the V3 block of
    structured records (chat GUID, delivery service, sender handle) first, then
    one plain line per message. A message whose chat has no GUID gets no record;
    its line still goes."""
    rows = _message_rows(world)
    structured = wire.messages_structured([r for r in rows if r["chat_guid"] and r["sender"]])
    return "\n".join(structured + [line for line in [wire.messages_lines(rows)] if line])


def _messages_count(world: World) -> int:
    return sum(len(t["messages"]) for t in world.state["threads"].values())


def _contacts_raw(world: World) -> dict[str, str]:
    raw: dict[str, str] = {}
    for c in world.state["contacts"].values():
        for h in c["handles"]:
            raw[h] = c["name"]
    return raw


def _email_header_rows(world: World, *, deep: bool | None) -> list[dict]:
    out = []
    for e in world.state["emails"].values():
        if e["mailbox"] != "inbox":
            continue
        if deep is not None and bool(e.get("deep")) != deep:
            continue
        out.append({"ts": world.abs_ts(e["ts_off"]), "unread": e["unread"],
                    "account": e["account"], "sender_name": e["from_name"],
                    "sender_addr": e["from_addr"], "subject": e["subject"]})
    return out


def _email_raw_rows(world: World) -> list[dict]:
    out = []
    for e in world.state["emails"].values():
        if e["mailbox"] != "inbox":
            continue
        out.append({"ts": world.abs_ts(e["ts_off"]), "unread": e["unread"],
                    "account": e["account"], "sender_name": e["from_name"],
                    "sender_addr": e["from_addr"], "to": e["to"],
                    "account_id": e.get("account_id") or "sandbox:" + e["account"],
                    "subject": e["subject"], "message_id": e["message_id"],
                    "body": e["body"]})
    return out


def _notes_rows(world: World) -> list[dict]:
    return [{"ts": world.abs_ts(n["ts_off"]), "title": n["title"],
             "folder": n["folder"], "body": n["body"]}
            for n in world.state["notes"].values()]


def _browser_rows(world: World, browser: str) -> list[dict]:
    return [{"ts": world.abs_ts(b["ts_off"]), "host": b["host"],
             "path": b["path"], "title": b["title"]}
            for b in world.state["browser"].get(browser, [])]


def _calendar_events(world: World, coll: dict) -> list[dict]:
    out = []
    for row in coll.values():
        event = {"source_id": row["source_id"], "kind": row["kind"],
                 "title": row["title"], "context": row.get("context"),
                 "organizer": row.get("organizer"), "account": row.get("account"),
                 "when_ts": world.abs_ts(row["ts_off"]),
                 "all_day": bool(row.get("all_day")),
                 "location": row.get("location")}
        # An end time, as EventKit gives one: only rows that carry `end_ts_off`
        # (benchmark worlds) send it, so the seeded world's payload is unchanged.
        if row.get("end_ts_off") is not None:
            event["end_ts"] = world.abs_ts(row["end_ts_off"])
        # An all-day reminder's floating date, as RemindersWriter sends it: only rows
        # Harbor wrote as all-day carry `due_date`.
        if row.get("all_day") and row.get("due_date"):
            event["due_date"] = row["due_date"]
        out.append(event)
    return out


def _calendar_coverage(world: World) -> dict:
    """The window the calendar read covered, as CalendarReader reports it.

    Only a world that declares `calendar_coverage` ({"start_off", "end_off"},
    benchmark worlds) sends it; the seeded world's diagnostics are unchanged.
    Without it the Day planner treats the selected day as unconfirmed."""
    cov = world.state.get("calendar_coverage")
    if not isinstance(cov, dict):
        return {}
    return {"coverage_start": world.abs_ts(cov["start_off"]),
            "coverage_end": world.abs_ts(cov["end_off"])}


class SyncScheduler:
    def __init__(self, world: World, client: httpx.AsyncClient) -> None:
        self.world = world
        self.client = client
        self._events: dict[str, asyncio.Event] = {s: asyncio.Event() for s in CADENCES}
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        # One push per source at a time, so a reminders snapshot's start time
        # reaches the backend in order (it refuses an older or equal one).
        self._locks: dict[str, asyncio.Lock] = {s: asyncio.Lock() for s in CADENCES}

    def start(self) -> None:
        for source in CADENCES:
            self._tasks.append(asyncio.ensure_future(self._loop(source)))

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def kick(self, *sources: str) -> None:
        for s in sources:
            if s in self._events:
                self._events[s].set()

    async def push_all(self) -> None:
        await asyncio.gather(*(self._push(s) for s in CADENCES))

    def _period(self, source: str) -> float:
        scale = self.world.state["clock"].get("sync_scale", 0.05)
        return max(2.0, CADENCES[source] * scale)

    async def _loop(self, source: str) -> None:
        ev = self._events[source]
        # Fire immediately at boot, same as the real readers.
        await self._push_with_retry(source)
        while not self._stopping:
            try:
                await asyncio.wait_for(ev.wait(), timeout=self._period(source))
            except asyncio.TimeoutError:
                pass
            # A burst of kicks within ~250ms collapses into one push.
            ev.clear()
            await asyncio.sleep(0.25)
            await self._push_with_retry(source)

    async def _push_with_retry(self, source: str) -> None:
        if self.world.state["injection"].get("sync_paused"):
            return
        try:
            await self._push(source)
        except Exception as e:  # noqa: BLE001 — one bad push must not kill the loop
            self.world.state.setdefault("sync_status", {})[source] = {
                "ok": False, "status": str(e), "count": 0, "at": time.time()}

    async def force_sync(self, source: str) -> None:
        """POST /sandbox/sync/{source} — bypasses the debounce entirely."""
        await self._push(source)

    async def sync_now(self, source: str) -> None:
        """A read the backend asked for (sync_assistant_sources_now): runs at once,
        but like a timed read it honours sync_paused and records a failure."""
        await self._push_with_retry(source)

    async def _post(self, path: str, body: dict) -> httpx.Response:
        latency = self.world.state["injection"].get("latency_ms", 0)
        if latency:
            await asyncio.sleep(latency / 1000.0)
        resp = await self.client.post(path, json=body)
        # A non-2xx must fail loudly, not be recorded as a successful sync —
        # this is exactly the bug that let a wrong URL silently 404 while
        # sync_status still reported "ok" (caught in Phase 2 smoke testing).
        resp.raise_for_status()
        return resp

    async def _diag(self, source: str, ok: bool = True, reason: str = "") -> dict:
        unavailable = source in self.world.state["injection"].get("unavailable", [])
        return {"available": not unavailable,
               "reason": reason or ("sandbox: marked unavailable" if unavailable else "")}

    async def _push(self, source: str) -> None:
        async with self._locks[source]:
            await self._push_locked(source)

    async def _push_locked(self, source: str) -> None:
        world = self.world
        count = 0
        if source == "messages":
            diag = await self._diag("messages")
            lines = _messages_lines(world)
            count = _messages_count(world)
            await self._post("/assistant/sync/messages",
                             {"lines": lines, "diagnostics": {**diag, "count": count}})
        elif source == "contacts":
            raw = _contacts_raw(world)
            count = len(raw)
            await self._post("/assistant/sync/messages", {"contacts": raw})
        elif source == "email_headers":
            diag = await self._diag("email")
            headers = wire.email_headers(_email_header_rows(world, deep=False))
            body = {"headers": headers, "diagnostics": diag}
            count = headers.count("\n")
            await self._post("/assistant/sync/emails", body)
            # Identity rides along with headers, same as MailReader.syncIdentity().
            await self._post("/assistant/sync/emails",
                             {"identity_emails": world.state["persona"]["identity_emails"]})
        elif source == "email_raw":
            accounts = sorted({e["account"] for e in world.state["emails"].values()})
            diag = await self._diag("email")
            rows = _email_raw_rows(world) if diag["available"] else []
            failed = set(accounts) if not diag["available"] else set()
            safe_rows = []
            for row in rows:
                fields = [str(row[k]) for k in (
                    "account", "account_id", "sender_name", "sender_addr", "subject", "message_id", "body")]
                fields.extend(row["to"])
                if any("\x01" in field or "\x02" in field for field in fields):
                    failed.add(row["account"])
                else:
                    safe_rows.append(row)
            raw = wire.email_raw(safe_rows)
            count = raw.count("\x02")
            await self._post("/assistant/sync/emails", {"raw": raw, "raw_coverage": {
                "accounts": [account for account in accounts if account not in failed],
                "failed_accounts": sorted(failed), "complete": diag["available"] and not failed}})
        elif source == "email_history":
            headers = wire.email_headers(_email_header_rows(world, deep=None))
            count = headers.count("\n")
            await self._post("/assistant/sync/emails", {"history": headers})
        elif source == "notes":
            raw = wire.notes_raw(_notes_rows(world))
            count = raw.count("\x02")
            await self._post("/assistant/sync/notes", {"raw": raw})
        elif source == "browser":
            for browser in ("safari", "chrome"):
                diag = await self._diag("browser")
                lines = wire.browser_lines(_browser_rows(world, browser))
                await self._post("/assistant/sync/browser_history",
                                 {"browser": browser, "lines": lines,
                                  "diagnostics": {**diag, "count": lines.count(chr(10)) + (1 if lines else 0)}})
            count = sum(len(world.state["browser"].get(b, [])) for b in ("safari", "chrome"))
        elif source == "calendar":
            events = _calendar_events(world, world.state["calendar"])
            count = len(events)
            diagnostics = {"authorized": True, "calendar_count": 1, "events_found": count}
            diagnostics.update(_calendar_coverage(world))
            await self._post("/assistant/sync/calendar",
                             {"events": events, "diagnostics": diagnostics})
        elif source == "reminders":
            # When this read began, as RemindersWriter reports it: Harbor counts
            # Reminders as current only for a read begun after its own request.
            started = time.time()
            events = _calendar_events(world, world.state["reminders"])
            count = len(events)
            await self._post("/assistant/sync/calendar",
                             {"source": "reminders", "events": events,
                              "diagnostics": {"authorized": True, "count": count,
                                              "snapshot_started_at": started}})
        world.state.setdefault("sync_status", {})[source] = {
            "ok": True, "status": "ok", "count": count, "at": time.time()}
