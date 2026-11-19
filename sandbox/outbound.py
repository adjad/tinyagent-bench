"""The fake Swift app's other half: consumes GET /assistant/events (SSE) from
the sandbox backend and acts on it exactly like app/Sources/HarborApp's
OverlayModel.handleAssistantEvent + OutboundSender would — mutating the
sandbox world and, for the seven actions the backend actually waits on,
POSTing the result back to /assistant/action_result.

Pinned against source (service/main.py, service/assistant/outbox.py,
OutboundSender.swift) — the only result shape the backend reads is
`{"action_id", "ok", "error"}`, identical across every action type, and
outbox.DEFAULT_TIMEOUT_S is 45.0 — so every result-bearing handler here is
wrapped to ALWAYS post exactly one result, including when it raises
internally. A silent handler bug should look like "the app didn't respond
for 45s", not hang the agent turn forever with no result at all.

Calendar and Reminders writes take a different, durable path (NATIVE_ACTIONS),
the one the app's AssistantDelivery.handle takes for an event that carries an
`event_id`: claim it (POST /assistant/events/{id}/claim), run the write only
when the claim says `execute`, read the item back from the world, post the
read-back receipt ({"event_id", "action_id", "kind", "claim_token", "result"})
to /assistant/action_result, then acknowledge the event. The result has the
exact fields the native writers return (RemindersWriter: source_id, title and
the due minute; Calendar: source_id, or the read-back start and end of a move),
so the backend's receipt checks apply unchanged. A replayed event whose claim
is already recorded is only acknowledged, never written again.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from sandbox import inject
from sandbox.world import World

RESULT_ACTIONS = {"send_message", "send_email", "reply_to_email", "draft_email",
                  "draft_message", "mark_email_read", "archive_email", "prepare_email_reply"}
FIRE_AND_FORGET = {"create_calendar_event", "delete_calendar_event",
                   "create_apple_reminder", "delete_apple_reminder", "sync_emails_now"}
NOTIFY_ONLY = {"reminder", "scheduled_send_result", "scheduled_send_missed",
               "email_summary", "daily_brief"}
# Durable native writes (service/assistant/outbox.py publishes them with an
# event_id and waits for a claimed, read-back receipt).
REMINDER_ACTIONS = {"create_reminder", "update_reminder", "complete_reminder", "delete_reminder"}
CALENDAR_ACTIONS = {"create_calendar_event", "delete_calendar_event", "move_calendar_event",
                    "cancel_calendar_event"}
NATIVE_ACTIONS = REMINDER_ACTIONS | CALENDAR_ACTIONS

_ACTION_LOG_MAX = 500


class OutboundConsumer:
    def __init__(self, world: World, client: httpx.AsyncClient, sync=None) -> None:
        self.world = world
        self.client = client
        self.sync = sync  # sandbox.sync.SyncScheduler, set by server.py post-construction
        self._stopping = False
        self._task: asyncio.Task | None = None
        self.connected = False

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        backoff = 0.5
        while not self._stopping:
            try:
                async with self.client.stream("GET", "/assistant/events", timeout=None) as r:
                    self.connected = True
                    backoff = 0.5
                    await self.world.notify("connection", {"connected": True})
                    async for line in r.aiter_lines():
                        if self._stopping:
                            break
                        if not line.startswith("data: "):
                            continue
                        try:
                            ev = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        asyncio.ensure_future(self._dispatch(ev))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — reconnect on anything
                await self.world.notify("reconnect_log", {"error": str(e), "at": time.time()})
            self.connected = False
            await self.world.notify("connection", {"connected": False})
            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    async def _dispatch(self, ev: dict) -> None:
        etype = ev.get("type", "")
        action_id = ev.get("action_id")
        if etype in NATIVE_ACTIONS and ev.get("event_id"):
            await self._handle_native(etype, ev)
            return
        started = time.time()
        ok, error = True, ""
        extra: dict = {}
        try:
            if etype in RESULT_ACTIONS:
                outcome = await self._handle_result_action(etype, ev)
                ok, error = outcome[:2]
                if len(outcome) == 3:
                    extra = outcome[2]
            elif etype in FIRE_AND_FORGET:
                await self._handle_fire_and_forget(etype, ev)
            elif etype in NOTIFY_ONLY:
                await self.world.notify("notification", {"kind": etype, "payload": ev})
            elif etype == "hello":
                pass
            elif etype == "changed":
                pass
        except Exception as e:  # noqa: BLE001 — a handler bug must not eat the result
            ok, error = False, f"sandbox handler error: {e}"

        if action_id:
            await self._log_action(etype, ev, ok, error, started)
        if etype in RESULT_ACTIONS and action_id:
            await self._post_result(etype, action_id, ok, error, extra)

    async def _log_action(self, etype: str, ev: dict, ok: bool, error: str,
                          started: float) -> None:
        async def _do(state: dict) -> set[str]:
            log = state.setdefault("action_log", [])
            log.append({"action_id": ev.get("action_id"), "type": etype,
                       "payload": {k: v for k, v in ev.items()
                                   if k not in ("type", "action_id")},
                       "ok": ok, "error": error, "started_at": started,
                       "finished_at": time.time(),
                       "latency_ms": int((time.time() - started) * 1000)})
            del log[:-_ACTION_LOG_MAX]
            return set()
        await self.world.mutate(_do)
        await self.world.notify("action", {"type": etype, "ok": ok, "error": error})

    async def _post_result(self, etype: str, action_id: str, ok: bool, error: str, extra: dict | None = None) -> None:
        stall = inject.get_stall_ms(self.world.state, etype)
        if stall:
            await asyncio.sleep(stall / 1000.0)
        if inject.is_dropped(self.world.state, etype):
            return  # exercises outbox.DEFAULT_TIMEOUT_S — deliberately silent
        try:
            await self.client.post("/assistant/action_result",
                                   json={**(extra or {}), "action_id": action_id, "ok": ok, "error": error})
        except Exception:  # noqa: BLE001 — backend may be mid-restart; nothing to do
            pass

    # --- result-bearing actions ------------------------------------------

    async def _handle_result_action(self, etype: str, ev: dict) -> tuple[bool, str]:
        forced = inject.get_fail(self.world.state, etype)
        if forced:
            return False, forced
        handler = getattr(self, f"_h_{etype}")
        return await handler(ev)

    def _find_or_create_thread(self, state: dict, to: str) -> dict:
        norm = _norm_handle(to)
        for c in state["contacts"].values():
            if any(_norm_handle(h) == norm for h in c["handles"]):
                for t in state["threads"].values():
                    if t["kind"] == "dm" and c["id"] in (t.get("participants") or []):
                        return t
                tid = self.world.new_id("t")
                t = {"id": tid, "kind": "dm", "title": None,
                    "context_label": c["name"], "participants": [c["id"]],
                    "draft": None, "messages": []}
                state["threads"][tid] = t
                return t
        # Unsaved handle — matches the real reader's unresolved-handle path.
        for t in state["threads"].values():
            if t["kind"] == "dm" and t.get("context_label") == to:
                return t
        tid = self.world.new_id("t")
        t = {"id": tid, "kind": "dm", "title": None, "context_label": to,
            "participants": [], "draft": None, "messages": []}
        state["threads"][tid] = t
        return t

    async def _h_send_message(self, ev: dict) -> tuple[bool, str]:
        to, text = str(ev.get("to") or ""), str(ev.get("text") or "")
        if not to or not text:
            return False, "missing 'to' or 'text'"

        async def _do(state: dict) -> set[str]:
            thread = self._find_or_create_thread(state, to)
            thread["messages"].append({"id": self.world.new_id("m"),
                                       "ts_off": self.world.rel_ts(),
                                       "from": "me", "text": text, "via": "harbor"})
            return {"messages"}
        await self.world.mutate(_do)
        return True, ""

    async def _h_send_email(self, ev: dict) -> tuple[bool, str]:
        to = ev.get("to") or []
        subject, body = str(ev.get("subject") or ""), str(ev.get("body") or "")
        if not to:
            return False, "missing 'to'"

        async def _do(state: dict) -> set[str]:
            identity = state["persona"]["identity_emails"]
            eid = self.world.new_id("e")
            state["emails"][eid] = {
                "id": eid, "message_id": f"<{self.world.new_id('sent')}@sandbox.harbor.test>",
                "account": "Personal", "ts_off": self.world.rel_ts(),
                "from_name": "Me", "from_addr": identity[0] if identity else "me@sandbox.harbor.test",
                "to": to, "cc": ev.get("cc") or [], "subject": subject, "body": body,
                "unread": False, "mailbox": "sent", "deep": False, "in_reply_to": None,
            }
            return set()  # sent mail is not synced into inbox headers/raw — see settings toggle
        await self.world.mutate(_do)
        return True, ""

    async def _h_prepare_email_reply(self, ev: dict):
        return await self._reply_action(ev, prepare=True)

    async def _h_reply_to_email(self, ev: dict):
        return await self._reply_action(ev, prepare=False)

    async def _reply_action(self, ev: dict, *, prepare: bool):
        from service.tasks.reply_contract import envelope_matches
        message_id = str(ev.get("message_id") or "")
        body = str(ev.get("body") or "")
        account = str(ev.get("account") or "")
        account_id = str(ev.get("account_id") or (ev.get("expected_reply") or {}).get("account_id") or "")
        if not message_id.strip() or not body.strip():
            return False, "missing source Message-ID or reply body", {}
        matches = [e for e in self.world.state["emails"].values()
                   if e["message_id"] == message_id and e["mailbox"] == "inbox"
                   and (not account or e["account"] == account)
                   and (not account_id or (e.get("account_id") or "sandbox:" + e["account"]) == account_id)]
        if len(matches) != 1:
            return False, "couldn't find that message uniquely in the inbox; choose its account", {}
        original = matches[0]
        identity = self.world.state["persona"]["identity_emails"]
        sender = next((a for a in original["to"] + original["cc"] if a in identity), identity[0])
        to = [original.get("reply_to") or original["from_addr"]]
        cc = []
        if ev.get("reply_all"):
            cc = list(dict.fromkeys(a for a in original["to"] + original["cc"] if a not in identity and a not in to))
        subject = original["subject"]
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        envelope = {"message_id": message_id, "account": original["account"],
                    "account_id": original.get("account_id") or "sandbox:" + original["account"], "from": sender,
                    "to": to, "cc": cc, "bcc": [], "subject": subject,
                    "content": body}
        if prepare:
            return True, "", {"reply": envelope, "accepted": False}
        if not envelope_matches(envelope, ev.get("expected_reply")):
            return False, "The reply changed or lacks an approved envelope. Nothing sent.", {}

        async def _do(state: dict) -> set[str]:
            eid = self.world.new_id("e")
            state["emails"][eid] = {
                "id": eid, "message_id": f"<{self.world.new_id('sent')}@sandbox.harbor.test>",
                "account": original["account"], "ts_off": self.world.rel_ts(),
                "from_name": "Me", "from_addr": sender,
                "to": to, "cc": cc, "subject": subject, "body": envelope["content"],
                "unread": False, "mailbox": "sent", "deep": False,
                "in_reply_to": message_id,
            }
            original["unread"] = False
            return {"email_headers", "email_raw"}
        await self.world.mutate(_do)
        return True, "", {"reply": envelope, "accepted": True}

    async def _h_draft_email(self, ev: dict) -> tuple[bool, str]:
        async def _do(state: dict) -> set[str]:
            eid = self.world.new_id("e")
            state["emails"][eid] = {
                "id": eid, "message_id": f"<{self.world.new_id('draft')}@sandbox.harbor.test>",
                "account": "Personal", "ts_off": self.world.rel_ts(),
                "from_name": "Me", "from_addr": "", "to": ev.get("to") or [],
                "cc": ev.get("cc") or [], "subject": str(ev.get("subject") or ""),
                "body": str(ev.get("body") or ""), "unread": False,
                "mailbox": "drafts", "deep": False, "in_reply_to": None,
            }
            return set()
        await self.world.mutate(_do)
        return True, ""

    async def _h_draft_message(self, ev: dict) -> tuple[bool, str]:
        to, text = str(ev.get("to") or ""), str(ev.get("text") or "")
        if not to:
            return False, "missing 'to'"

        async def _do(state: dict) -> set[str]:
            thread = self._find_or_create_thread(state, to)
            thread["draft"] = text
            return set()
        await self.world.mutate(_do)
        return True, ""

    def _find_email(self, message_id: str) -> dict | None:
        for e in self.world.state["emails"].values():
            if e["message_id"] == message_id:
                return e
        return None

    async def _h_mark_email_read(self, ev: dict) -> tuple[bool, str]:
        message_id = str(ev.get("message_id") or "")
        read = bool(ev.get("read", True))
        if self._find_email(message_id) is None:
            return False, ("couldn't find that message in the inbox — it may "
                          "have been moved or is older than the synced window")

        async def _do(state: dict) -> set[str]:
            e = next(e for e in state["emails"].values() if e["message_id"] == message_id)
            e["unread"] = not read
            return {"email_headers", "email_raw"}
        await self.world.mutate(_do)
        return True, ""

    async def _h_archive_email(self, ev: dict) -> tuple[bool, str]:
        message_id = str(ev.get("message_id") or "")
        if self._find_email(message_id) is None:
            return False, ("couldn't find that message in the inbox — it may "
                          "have been moved or is older than the synced window")

        async def _do(state: dict) -> set[str]:
            e = next(e for e in state["emails"].values() if e["message_id"] == message_id)
            e["mailbox"] = "archive"
            return {"email_headers", "email_raw"}
        await self.world.mutate(_do)
        return True, ""

    # --- durable native writes (Calendar, Reminders) ------------------------

    async def _post_json(self, path: str, body: dict) -> dict | None:
        try:
            resp = await self.client.post(path, json=body)
            data = resp.json()
        except Exception:  # noqa: BLE001 — backend mid-restart: the event stays pending and replays
            return None
        return data if isinstance(data, dict) else None

    async def _ack(self, event_id: str, kind: str) -> None:
        await self._post_json(f"/assistant/events/{event_id}/ack", {"kind": kind, "state": "handled"})

    async def _handle_native(self, etype: str, ev: dict) -> None:
        """One durable Calendar/Reminders event, the way AssistantDelivery.handle runs it."""
        event_id = str(ev["event_id"])
        payload = {k: v for k, v in ev.items() if k != "event_id"}
        action_id = payload.get("action_id")
        claim = await self._post_json(f"/assistant/events/{event_id}/claim",
                                      {"kind": etype, "action_id": action_id, "payload": payload})
        if (not claim or claim.get("event_id") != event_id or claim.get("action_id") != action_id
                or claim.get("kind") != etype):
            return  # not claimed: left pending, so the next stream replays it
        if claim.get("recorded") is True:
            # Already has a result (an earlier delivery, or expired before it ran).
            await self._ack(event_id, etype)
            return
        token = claim.get("claim_token")
        if claim.get("execute") is not True or not isinstance(token, str) or not token:
            return  # claimed earlier with no recorded result: never write it a second time
        started = time.time()
        forced = inject.get_fail(self.world.state, etype)
        if forced:
            result = {"ok": False, "status": "failed", "error": forced}
        else:
            rev = self.world.state.get("rev", 0)
            try:
                result = await getattr(self, f"_n_{etype}")(payload)
            except Exception as e:  # noqa: BLE001 — a handler bug must still settle the claim
                # Nothing changed in the world: a verified failure. Otherwise unknown.
                changed = self.world.state.get("rev", 0) != rev
                result = {"ok": False, "status": "unknown" if changed else "failed",
                          "error": f"sandbox handler error: {e}"}
        await self._log_action(etype, payload, result["ok"], result["error"], started)
        stall = inject.get_stall_ms(self.world.state, etype)
        if stall:
            await asyncio.sleep(stall / 1000.0)
        if inject.is_dropped(self.world.state, etype):
            return  # the app never answered: the backend's wait ends as unknown
        response = await self._post_json("/assistant/action_result", {
            "event_id": event_id, "action_id": action_id, "kind": etype,
            "claim_token": token, "result": result})
        if response and response.get("recorded") is True:
            await self._ack(event_id, etype)

    @staticmethod
    def _failed(error: str) -> dict:
        return {"ok": False, "status": "failed", "error": error}

    @staticmethod
    def _due_minute(ts: float) -> float:
        # EventKit keeps a reminder's due date to the minute (RemindersWriter).
        return float(int(ts // 60) * 60)

    async def _write(self, fn) -> object:
        """Run `fn(state) -> (dirty, value)` under the world lock; kick sync for what changed."""
        box: dict = {}

        async def _do(state: dict) -> set[str]:
            dirty, box["value"] = fn(state)
            return dirty
        dirty = await self.world.mutate(_do)
        if self.sync and dirty:
            self.sync.kick(*dirty)
        return box.get("value")

    def _reminder_readback(self, row: dict) -> dict:
        receipt = {"ok": True, "status": "succeeded", "error": "", "source_id": row["source_id"],
                   "title": row["title"]}
        if row.get("all_day"):
            return {**receipt, "all_day": True, "due_date": row["due_date"]}
        return {**receipt, "due_ts": self._due_minute(self.world.abs_ts(row["ts_off"]))}

    def _reminder_due(self, payload: dict) -> dict:
        from service.assistant.verified_reminders import due_timestamp
        if payload.get("all_day") is True:
            return {"all_day": True, "due_date": payload["due_date"],
                    "ts_off": self.world.rel_ts(due_timestamp(payload))}
        return {"all_day": False, "due_date": None,
                "ts_off": self.world.rel_ts(self._due_minute(float(payload["due_ts"])))}

    def _reminder_target(self, state: dict, payload: dict) -> tuple[str, dict] | str:
        """The one incomplete reminder the payload names, still as Harbor last read it."""
        sid = str(payload.get("source_id") or "")
        rows = [(key, row) for key, row in state["reminders"].items() if row.get("source_id") == sid]
        if len(rows) != 1:
            return "That reminder was not found in Reminders"
        key, row = rows[0]
        expected_due = payload.get("expected_due_ts")
        if row.get("title") != payload.get("expected_title"):
            return "That reminder changed in Reminders; nothing was changed"
        if payload.get("expected_due_date"):
            if not row.get("all_day") or row.get("due_date") != payload["expected_due_date"]:
                return "That reminder changed in Reminders; nothing was changed"
        elif expected_due is None or row.get("all_day") or (
                int(self.world.abs_ts(row["ts_off"]) // 60) != int(float(expected_due) // 60)):
            return "That reminder changed in Reminders; nothing was changed"
        return key, row

    async def _n_create_reminder(self, payload: dict) -> dict:
        title = str(payload.get("title") or "").strip()
        if not title:
            return self._failed("Reminder title is required")

        def fn(state: dict):
            rid = self.world.new_id("sbx")
            row = {"id": rid, "source_id": rid, "kind": payload.get("commitment_kind") or "reminder",
                   "title": title, "context": None, "organizer": None, "account": None,
                   "location": None, **self._reminder_due(payload)}
            state["reminders"][rid] = row
            return {"reminders"}, self._reminder_readback(row)
        return await self._write(fn)

    async def _n_update_reminder(self, payload: dict) -> dict:
        def fn(state: dict):
            found = self._reminder_target(state, payload)
            if isinstance(found, str):
                return set(), self._failed(found)
            _, row = found
            row.update(title=str(payload["title"]), **self._reminder_due(payload))
            return {"reminders"}, self._reminder_readback(row)
        return await self._write(fn)

    async def _n_complete_reminder(self, payload: dict) -> dict:
        def fn(state: dict):
            found = self._reminder_target(state, payload)
            if isinstance(found, str):
                return set(), self._failed(found)
            key, row = found
            # The reminders feed lists incomplete reminders only, as the real reader does.
            state.setdefault("completed_reminders", {})[key] = {**state["reminders"].pop(key),
                                                                 "completed": True}
            return {"reminders"}, {"ok": True, "status": "succeeded", "error": "",
                                   "source_id": row["source_id"], "is_completed": True}
        return await self._write(fn)

    async def _n_delete_reminder(self, payload: dict) -> dict:
        def fn(state: dict):
            found = self._reminder_target(state, payload)
            if isinstance(found, str):
                return set(), self._failed(found)
            key, row = found
            del state["reminders"][key]
            return {"reminders"}, {"ok": True, "status": "succeeded", "error": "",
                                   "source_id": row["source_id"], "is_absent": True}
        return await self._write(fn)

    def _calendar_target(self, state: dict, payload: dict) -> tuple[str, dict] | str:
        """The one occurrence a move or cancel names: identifier, start and title."""
        sid = str(payload.get("source_id") or "")
        expected = float(payload["expected_start_ts"])
        rows = [(key, row) for key, row in state["calendar"].items()
                if row.get("source_id") == sid and abs(self.world.abs_ts(row["ts_off"]) - expected) < 1]
        if len(rows) != 1:
            return "That event was not found in Calendar"
        key, row = rows[0]
        if row.get("title") != payload.get("expected_title"):
            return "That event changed in Calendar; nothing was changed"
        if int(row.get("invitee_count") or 0) != payload.get("invitee_count"):
            return "That event's invitees changed; nothing was changed"
        return key, row

    async def _n_create_calendar_event(self, payload: dict) -> dict:
        title = str(payload.get("title") or "").strip()
        if not title:
            return self._failed("Calendar creation payload is invalid")
        when = float(payload["when_ts"])
        duration = int(payload["duration_min"])

        def fn(state: dict):
            cid = self.world.new_id("sbx")
            ts_off = self.world.rel_ts(when)
            state["calendar"][f"{cid}@{ts_off}"] = {
                "id": f"{cid}@{ts_off}", "source_id": cid, "kind": "event", "title": title,
                "context": None, "organizer": None, "account": None, "ts_off": ts_off,
                "end_ts_off": self.world.rel_ts(when + duration * 60),
                "all_day": payload.get("all_day") is True, "location": payload.get("location") or None,
                "duration_min": duration}
            return {"calendar"}, {"ok": True, "status": "succeeded", "error": "", "source_id": cid}
        return await self._write(fn)

    async def _n_delete_calendar_event(self, payload: dict) -> dict:
        sid, when = str(payload.get("source_id") or ""), float(payload["when_ts"])

        def fn(state: dict):
            keys = [k for k, row in state["calendar"].items() if row.get("source_id") == sid
                    and abs(self.world.abs_ts(row["ts_off"]) - when) < 60]
            if not keys:
                return set(), self._failed("That event was not found in Calendar")
            for k in keys:
                del state["calendar"][k]
            return {"calendar"}, {"ok": True, "status": "succeeded", "error": ""}
        return await self._write(fn)

    async def _n_move_calendar_event(self, payload: dict) -> dict:
        def fn(state: dict):
            found = self._calendar_target(state, payload)
            if isinstance(found, str):
                return set(), self._failed(found)
            _, row = found
            if payload.get("duration_min") is not None:
                length = int(payload["duration_min"]) * 60
            elif row.get("end_ts_off") is not None:
                length = row["end_ts_off"] - row["ts_off"]
            else:
                length = int(row.get("duration_min") or 60) * 60
            when = float(payload["when_ts"])
            row.update(ts_off=self.world.rel_ts(when), end_ts_off=self.world.rel_ts(when + length))
            return {"calendar"}, {"ok": True, "status": "succeeded", "error": "",
                                  "when_ts": self.world.abs_ts(row["ts_off"]),
                                  "end_ts": self.world.abs_ts(row["end_ts_off"])}
        return await self._write(fn)

    async def _n_cancel_calendar_event(self, payload: dict) -> dict:
        def fn(state: dict):
            found = self._calendar_target(state, payload)
            if isinstance(found, str):
                return set(), self._failed(found)
            key, _ = found
            del state["calendar"][key]
            return {"calendar"}, {"ok": True, "status": "succeeded", "error": "", "is_absent": True}
        return await self._write(fn)

    # --- fire-and-forget ---------------------------------------------------

    async def _handle_fire_and_forget(self, etype: str, ev: dict) -> None:
        if etype == "sync_emails_now":
            if self.sync:
                await self.sync.force_sync("email_headers")
                await self.sync.force_sync("email_raw")
            return

        async def _do(state: dict) -> set[str]:
            if etype == "create_calendar_event":
                cid = self.world.new_id("sbx")
                key = f"{cid}@{self.world.rel_ts(ev.get('when_ts'))}"
                state["calendar"][key] = {
                    "id": key, "source_id": cid, "kind": "event",
                    "title": str(ev.get("title") or ""), "context": None,
                    "organizer": None, "account": None,
                    "ts_off": self.world.rel_ts(ev.get("when_ts")),
                    "all_day": False, "location": ev.get("location"),
                    "duration_min": ev.get("duration_min", 60),
                }
                return {"calendar"}
            if etype == "delete_calendar_event":
                sid = str(ev.get("source_id") or "")
                when_ts = ev.get("when_ts")
                to_delete = [k for k, row in state["calendar"].items()
                            if row["source_id"] == sid and
                            (when_ts is None or
                             abs(row["ts_off"] - self.world.rel_ts(when_ts)) < 60)]
                for k in to_delete:
                    del state["calendar"][k]
                return {"calendar"} if to_delete else set()
            if etype == "create_apple_reminder":
                rid = self.world.new_id("sbx")
                state["reminders"][rid] = {
                    "id": rid, "source_id": rid, "kind": "reminder",
                    "title": str(ev.get("title") or ""),
                    "ts_off": self.world.rel_ts(ev.get("when_ts")),
                }
                return {"reminders"}
            if etype == "delete_apple_reminder":
                sid = str(ev.get("source_id") or "")
                if sid in state["reminders"]:
                    del state["reminders"][sid]
                    return {"reminders"}
                return set()
            return set()
        dirty = await self.world.mutate(_do)
        if self.sync:
            self.sync.kick(*dirty)


def _norm_handle(h: str) -> str:
    from service.tools.imessage_tools import _norm_handle as norm
    return norm(h)
