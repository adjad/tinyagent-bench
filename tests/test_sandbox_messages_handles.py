"""The sandbox Messages feed ties a text to the saved contact who sent it, as Harbor's real
Messages reader does.

Harbor decides who wrote a text from the native record the reader puts at the head of the
Messages carrier (chat GUID, delivery service, sender handle), joined to each plain line by
time. A feed without those records leaves every sandbox text "not a saved contact".

In-process only: the benchmark's student profile is built into a sandbox world, its sync
pushes go to the backend through FastAPI's TestClient, and Harbor's own deciders read the
result. No server, no socket, no model.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from scripts.productbench import loader
from scripts.productbench.profile import load

PROFILE = "test_fixtures/bench_profiles/student/profile.json"
RAFA = "+17735550124"            # ct-rafa's saved "+1 (773) 555-0124", as chat.db stores it
STRANGER = "+17735550193"        # the profile's unsaved SMS sender


@pytest.fixture()
def synced(tmp_path, monkeypatch):
    """(profile, world, text items) after the sandbox pushed contacts and messages."""
    monkeypatch.setenv("HARBOR_SANDBOX_HOME", str(tmp_path / "sandbox"))
    from fastapi.testclient import TestClient

    import service.main
    from sandbox.sync import SyncScheduler
    from sandbox.world import World
    from service.proactive import sources
    from service.tools import cache_store

    monkeypatch.setattr(cache_store, "save", lambda *a, **k: None)
    profile = load(PROFILE)
    path = tmp_path / "sandbox" / "world.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(loader.build_world(profile)), encoding="utf-8")
    world = World(path=path)
    api = TestClient(service.main.app, base_url="http://127.0.0.1:8775")

    class Client:
        async def post(self, route, json):
            resp = api.post(route, json=json)
            return httpx.Response(resp.status_code, request=httpx.Request("POST", "http://127.0.0.1:8775" + route))

    scheduler = SyncScheduler(world, Client())
    asyncio.run(scheduler._push("contacts"))
    asyncio.run(scheduler._push("messages"))
    items = sources.text_items(sources.text_records(), now=profile.anchor_epoch, own="Personal")
    return profile, world, items


def _item(items, title):
    return next(i for i in items if i["title"] == title)


def test_feed_carries_the_native_records(synced):
    from service.tools import imessage_tools as im
    snapshot = im.structured_messages_snapshot()
    assert snapshot["state"] == "ready"
    assert snapshot["coverage"]["status"] == "complete" and snapshot["coverage"]["malformed"] == 0
    profile, world, _ = synced
    assert len(snapshot["records"]) == sum(len(t["messages"]) for t in world.state["threads"].values())
    rafa = [r for r in snapshot["records"] if r["conversation"] == "iMessage;-;" + RAFA]
    assert rafa and all(r["service"] == "iMessage" for r in rafa)
    assert {r["sender"] for r in rafa if r["direction"] == "incoming"} == {RAFA}


def test_a_text_from_a_saved_contact_is_that_contact(synced):
    from service.proactive import autoadd, sources
    profile, _, items = synced
    item = _item(items, "Rafa (café)")
    assert sources.text_origin(item) == (RAFA, "iMessage")
    assert autoadd._plan_handle(item) == RAFA
    # Harbor shows a saved name title-cased ("Rafa (Café)"); the contact is the same.
    assert autoadd.strict_contact_name(RAFA).casefold() == "rafa (café)"
    assert autoadd._saved_contact(item)
    _, reason = autoadd.plan_for(item, {}, now=profile.anchor_epoch, tz=profile.data["timezone"])
    assert reason != "not a saved contact: tap to add"


def test_a_group_line_is_its_own_saved_sender(synced):
    from service.proactive import autoadd, sources
    profile, world, items = synced
    thread = world.state["threads"]["th-roomies"]
    item = next(i for i in items if i["group"] and i["title"] == thread["title"])
    last = thread["messages"][-1]
    assert last["from"] != "me"
    line_ts = item["line_ts"][-1]
    chat, service, sender = sources.line_origin(item, line_ts)
    contact = world.state["contacts"][last["sender_id"]]
    assert chat.startswith("iMessage;+;chat") and service == "iMessage"
    assert autoadd.phones_match(sender, contact["handles"][0])
    assert autoadd._group_sender(item, line_ts).casefold() == contact["name"].casefold()
    assert sources.text_origin(item) == ("", "")      # a group is never one-to-one


def test_an_unsaved_number_is_still_not_a_saved_contact(synced):
    from service.proactive import autoadd, sources
    from service.tools import imessage_tools as im
    _, _, items = synced
    rows = [r for r in im.structured_messages_snapshot()["records"] if r["sender"] == STRANGER]
    assert rows and {r["service"] for r in rows} == {"SMS"}
    assert {r["conversation"] for r in rows} == {"SMS;-;" + STRANGER}
    assert autoadd.strict_contact_name(STRANGER) == ""
    # Harbor may drop the thread as an automated sender; when it keeps it, it is no contact.
    for item in (i for i in items if autoadd._plan_handle(i) == STRANGER):
        assert sources.text_origin(item) == (STRANGER, "SMS")
        assert not autoadd._saved_contact(item)
        _, reason = autoadd.plan_for(item, {}, now=0.0, tz="UTC")
        assert reason in ("not a person", "not a saved contact: tap to add", "withheld")


def test_same_minute_messages_keep_distinct_times():
    """chat.db times are nanoseconds; a profile's are minutes. Ties would leave lines unjoined."""
    from sandbox import sync
    world = type("W", (), {})()
    world.state = {"contacts": {"c1": {"id": "c1", "name": "Ana", "handles": ["+1 (555) 010-0001"]}},
                   "threads": {"t1": {"id": "t1", "kind": "dm", "title": None, "context_label": "Ana",
                                      "participants": ["c1"], "service": "sms",
                                      "messages": [{"id": "a", "ts_off": 0, "from": "Ana", "text": "one"},
                                                   {"id": "b", "ts_off": 0, "from": "Ana", "text": "two"}]}}}
    world.abs_ts = lambda off: 1_800_000_000.0 + off
    rows = sync._message_rows(world)
    assert [r["text"] for r in sorted(rows, key=lambda r: r["ts"])] == ["one", "two"]
    assert len({round(r["ts"], 3) for r in rows}) == 2
    assert {r["chat_guid"] for r in rows} == {"SMS;-;+15550100001"} and {r["service"] for r in rows} == {"SMS"}
