"""The sandbox app settles Calendar and Reminders writes with the receipt Harbor trusts.

Harbor publishes a reminder or calendar write as a durable event and waits for
the app's claimed, read-back receipt (service/assistant/outbox.py). These tests
run the backend in-process (an ASGI transport, no port), stream its events into
the sandbox's OutboundConsumer the way the app's event stream would, and call
the tools the way the agent does. They check that a write the sandbox really
performed is verified and present in the sandbox world, that a replay never
writes twice, and that a write that did not happen, or whose receipt never came
back, is still not verified.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from sandbox.outbound import NATIVE_ACTIONS, OutboundConsumer
from sandbox.sync import SyncScheduler
from sandbox.world import World
from service import main
from service.assistant import outbox
from service.assistant.hub import hub
from service.assistant.store import assistant_store
from service.router.model_led import trusted_effect_receipt
from service.tools.registry import classify_tool_outcome, get_tool

BASE = "http://127.0.0.1:8775"
UNVERIFIED = "did not return a verified completion receipt"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_SANDBOX_HOME", str(tmp_path / "sandbox"))
    # The sandbox answers in milliseconds; an unanswered write gives up in 2s, not 45s.
    monkeypatch.setattr(outbox.request, "__defaults__", (2.0,))
    return World(path=tmp_path / "world.json", seed=11)


def run_with_app(world: World, body):
    """Run `body(app, sync)` while the sandbox app consumes the backend's events."""
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=BASE)
        sync = SyncScheduler(world, client)
        app = OutboundConsumer(world, client, sync=sync)

        async def stream():
            async for ev in hub.events():
                if ev.get("type") != "heartbeat":
                    asyncio.ensure_future(app._dispatch(ev))

        task = asyncio.ensure_future(stream())
        for _ in range(10):
            await asyncio.sleep(0)
        assert hub.has_subscribers
        try:
            return await body(app, sync)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()
    return asyncio.run(scenario())


async def call(name: str, **args):
    tool = get_tool(name)
    result = await tool.func(**args)
    return result, trusted_effect_receipt(tool, classify_tool_outcome(tool.name, result), result)


def tomorrow_at(hour: int, minute: int) -> datetime:
    return (datetime.now() + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def world_rows(world: World, coll: str, title: str) -> list[dict]:
    return [row for row in world.state[coll].values() if row.get("title") == title]


def store_rows(title: str) -> list[dict]:
    return [dict(r) for r in assistant_store._db.execute(
        "SELECT source, source_id, when_ts, status FROM commitments WHERE title=?", (title,))]


def pending_native(title: str) -> list[dict]:
    """Native events for `title` still waiting on the app (the store is shared by these tests)."""
    return [r for r in assistant_store.pending_events()
            if r["kind"] in NATIVE_ACTIONS and r["payload"].get("title") == title]


def logged(world: World, kind: str) -> list[dict]:
    return [a for a in world.state.get("action_log", []) if a["type"] == kind]


def test_add_reminder_is_verified_and_lands_in_the_world_once(world):
    when = tomorrow_at(8, 15)

    async def body(app, sync):
        result, verified = await call("add_reminder", title="check the bus times",
                                      when_iso=when.strftime("%Y-%m-%dT%H:%M"), kind="reminder")
        assert verified, result
        assert result.startswith("Reminder set:") and "in Apple Reminders and Harbor" in result
        assert UNVERIFIED not in result
        rows = world_rows(world, "reminders", "check the bus times")
        assert len(rows) == 1
        assert world.abs_ts(rows[0]["ts_off"]) == when.timestamp()
        assert pending_native("check the bus times") == []          # claimed, recorded and acknowledged

        # A replay of the same event (a stream reconnect) never writes it again.
        [entry] = logged(world, "create_reminder")
        row = assistant_store.event_by_key("action:" + entry["action_id"])
        assert row["result"]["status"] == "succeeded"
        await app._dispatch({**row["payload"], "event_id": row["id"]})
        assert len(world_rows(world, "reminders", "check the bus times")) == 1
        assert len(logged(world, "create_reminder")) == 1

        # The next Reminders feed carries the same item; Harbor keeps one row for it.
        await sync._push("reminders")
        stored = [r for r in store_rows("check the bus times") if r["status"] == "active"]
        assert [(r["source"], r["source_id"]) for r in stored] == [("reminders", rows[0]["source_id"])]
    run_with_app(world, body)


def test_all_day_reminder_receipt_reads_back_its_date(world):
    day = (datetime.now() + timedelta(days=2)).date().isoformat()

    async def body(app, sync):
        result, verified = await call("add_reminder", title="return the library books",
                                      when_iso=day, all_day=True)
        assert verified, result
        assert "in Apple Reminders and Harbor" in result
        [row] = world_rows(world, "reminders", "return the library books")
        assert row["all_day"] is True and row["due_date"] == day
    run_with_app(world, body)


def test_unanswered_reminder_write_is_not_verified(world):
    world.state["injection"]["drop_result"] = ["create_reminder"]

    async def body(app, sync):
        result, verified = await call("add_reminder", title="call the plumber",
                                      when_iso=tomorrow_at(9, 0).strftime("%Y-%m-%dT%H:%M"))
        assert not verified
        assert "Native result was not confirmed" in result
    run_with_app(world, body)


def test_refused_reminder_write_leaves_the_world_unchanged(world):
    world.state["injection"]["fail"]["create_reminder"] = "Reminders access is off"
    before = len(world.state["reminders"])

    async def body(app, sync):
        result, _ = await call("add_reminder", title="book the dentist",
                               when_iso=tomorrow_at(10, 30).strftime("%Y-%m-%dT%H:%M"))
        # A verified native failure: Harbor keeps its own reminder and says Reminders was not changed.
        assert "Apple Reminders was not changed" in result and "Reminders access is off" in result
        assert len(world.state["reminders"]) == before
        assert pending_native("book the dentist") == []
    run_with_app(world, body)


def test_update_of_a_reminder_changed_in_the_world_is_not_verified(world):
    async def body(app, sync):
        created, verified = await call("add_reminder", title="water the ferns",
                                       when_iso=tomorrow_at(18, 0).strftime("%Y-%m-%dT%H:%M"))
        assert verified, created
        [row] = world_rows(world, "reminders", "water the ferns")
        row["title"] = "water the ferns and the palm"      # edited in Reminders since Harbor read it
        result, verified = await call("update_reminder", title="water the ferns",
                                      when_iso=tomorrow_at(19, 0).strftime("%Y-%m-%dT%H:%M"))
        assert not verified, result
        assert result.startswith("(error:")
        assert world.abs_ts(row["ts_off"]) == tomorrow_at(18, 0).timestamp()

        # Harbor's own read of the item still matches: the move is verified and applied.
        row["title"] = "water the ferns"
        result, verified = await call("update_reminder", title="water the ferns",
                                      when_iso=tomorrow_at(19, 0).strftime("%Y-%m-%dT%H:%M"))
        assert verified, result
        assert world.abs_ts(row["ts_off"]) == tomorrow_at(19, 0).timestamp()
    run_with_app(world, body)


def test_add_calendar_event_is_verified_with_its_end(world):
    start = tomorrow_at(15, 0)

    async def body(app, sync):
        result, verified = await call("add_calendar_event", title="Coffee with Priya",
                                      when_iso=start.strftime("%Y-%m-%dT%H:%M"), duration_min=45)
        assert verified, result
        assert result.startswith("Added “Coffee with Priya” to your calendar")
        [row] = world_rows(world, "calendar", "Coffee with Priya")
        assert world.abs_ts(row["ts_off"]) == start.timestamp()
        assert world.abs_ts(row["end_ts_off"]) == start.timestamp() + 45 * 60
        assert pending_native("Coffee with Priya") == []

        [entry] = logged(world, "create_calendar_event")
        event = assistant_store.event_by_key("action:" + entry["action_id"])
        await app._dispatch({**event["payload"], "event_id": event["id"]})
        assert len(world_rows(world, "calendar", "Coffee with Priya")) == 1

        await sync._push("calendar")
        stored = [r for r in store_rows("Coffee with Priya") if r["status"] == "active"]
        assert [(r["source"], r["source_id"]) for r in stored] == [("calendar", row["source_id"])]
    run_with_app(world, body)


def test_unanswered_calendar_write_is_not_verified(world):
    world.state["injection"]["drop_result"] = ["create_calendar_event"]

    async def body(app, sync):
        result, verified = await call("add_calendar_event", title="Team lunch",
                                      when_iso=tomorrow_at(12, 0).strftime("%Y-%m-%dT%H:%M"))
        assert not verified
        assert "not confirmed" in result
    run_with_app(world, body)
