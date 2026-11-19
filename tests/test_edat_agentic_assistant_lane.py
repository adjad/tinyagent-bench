"""The assistant lane: Harbor's tools over Toolbench's fakes (edat/state/assistant_lane.py).

Every record here is invented. Nothing starts a model, a server or the app.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from edat.state import assistant_lane
from edat.state.assistant_lane import AssistantEnvironment
from edat.state.clock import Clock
from edat.state.env import ToolEnvironment


@pytest.fixture
def env() -> AssistantEnvironment:
    return AssistantEnvironment(Clock.parse())


# ------------------------------------------------------------------ menu

def test_is_a_tool_environment(env):
    assert isinstance(env, ToolEnvironment)
    assert env.lane == "assistant"


def test_schemas_carry_harbors_tools(env):
    schemas = env.schemas()
    assert schemas and len(schemas) == len(env.tool_names())
    names = {s["function"]["name"] for s in schemas}
    for wanted in ("get_upcoming", "add_calendar_event", "update_event", "send_message", "draft_message",
                   "send_email", "view_emails", "add_reminder", "complete_reminder", "get_weather",
                   "web_search"):
        assert wanted in names
    for s in schemas:
        assert s["type"] == "function"
        assert set(s["function"]) == {"name", "description", "parameters"}
        assert s["function"]["parameters"].get("type") == "object"
    # Internal-only tools stay off the menu; Toolbench's startup extras are on it.
    assert "search_coverage" not in names and "collect_task_evidence" not in names
    assert {"mac_set_volume", "mcp_drive_create_document", "use_skill"} <= names
    assert [s["function"]["name"] for s in env.schemas(["send_email", "get_upcoming"])] == \
        ["send_email", "get_upcoming"]
    with pytest.raises(ValueError):
        env.schemas(["no_such_tool"])


def test_extra_tools_leave_the_registry_as_found(monkeypatch):
    from service.mcp import TOOL_PARTS
    from service.tools.registry import REGISTRY
    monkeypatch.setattr(assistant_lane, "_TOOLS", None)
    before, parts = set(REGISTRY) - {"use_skill"}, dict(TOOL_PARTS)
    built = AssistantEnvironment()
    assert set(REGISTRY) - {"use_skill"} == before
    assert dict(TOOL_PARTS) == parts
    assert "mac_set_volume" in built.tool_names()


@pytest.mark.parametrize("name", ["send_message", "send_email", "reply_to_email", "forward_email",
                                  "schedule_send"])
def test_send_tools_are_confirmed_egress_effects(env, name):
    info = env.info(name)
    assert info is not None and info.name == name
    assert info.effect and info.egress and info.confirm


def test_other_flags(env):
    drafts = [env.info(n) for n in ("draft_message", "draft_email")]
    assert all(i.egress and i.effect and not i.confirm for i in drafts)
    web = env.info("web_search")
    assert web.egress and not web.effect and not web.confirm
    assert env.info("web_fetch").egress
    assert env.info("run_shell").egress and env.info("run_applescript").egress
    assert env.info("mcp_drive_create_document").egress
    assert env.info("add_calendar_event").confirm and not env.info("add_calendar_event").egress
    assert env.info("update_event").confirm and env.info("cancel_event").confirm
    rem = env.info("complete_reminder")
    assert rem.effect and not rem.confirm and not rem.egress
    read = env.info("get_upcoming")
    assert not read.effect and not read.egress and not read.confirm
    assert env.info("no_such_tool") is None


# ------------------------------------------------------------------ prompt

def test_system_prompt_is_harbors_with_policy_and_preferences(env):
    text = env.system_prompt(policy="Confirm before anything irreversible.",
                             preferences=["Answer in one sentence.", "Use 24-hour times."])
    assert text.startswith("You are Harbor")
    assert "The current date and time is Wednesday, November 18, 2026 at 8:30 AM." in text
    assert "Test User" in text                       # Toolbench's invented user, never the real one
    assert str(Path.home()) not in text               # Harbor's own text says only `/Users/<name>/`
    assert text.rstrip().endswith("- Answer in one sentence.\n- Use 24-hour times.")
    assert "Confirm before anything irreversible." in text
    assert "User preferences:" not in env.system_prompt()
    small = env.system_prompt(offered=["get_weather"])
    assert len(small) < len(env.system_prompt())


# ------------------------------------------------------------------ seeding

SEEDS = [
    ("calendar", {"id": "ev-501", "title": "Pottery wheel session", "start": "+1d 15:00",
                  "location": "Clay Loft"}, "get_upcoming", {"days": 3}, "Pottery wheel session"),
    ("reminders", {"id": "rem-501", "title": "Renew the bike lock code", "due": "+2d 09:00"},
     "search_reminders", {"query": "bike lock"}, "Renew the bike lock code"),
    ("notes", {"id": "note-501", "title": "Sourdough ratios", "body": "levain 20 percent"},
     "search_notes", {"query": "sourdough"}, "levain 20 percent"),
    ("contacts", {"id": "c-501", "name": "Quinn Marlow", "phone": "+1 555 0151"},
     "lookup_contact", {"name": "Quinn Marlow"}, "555 0151"),
    ("mail", {"id": "m-501", "from": "Brightline Supplies <billing@brightline.example>",
              "subject": "Invoice 4471", "body": "Invoice 4471 for $380.00 is due on the 30th.",
              "received": "-1d 10:02"}, "view_emails", {"query": "brightline"}, "$380.00"),
    ("messages", {"id": "t-501", "thread": "Quinn Marlow", "from": "Quinn Marlow",
                  "text": "Kayak rental is booked for noon"}, "view_messages", {"query": "kayak"},
     "Kayak rental is booked"),
    ("files", {"path": "~/Documents/garden_plan.txt", "text": "Plant the tulips by the fence."},
     "read_file", {"path": "~/Documents/garden_plan.txt"}, "tulips by the fence"),
    ("memory", {"id": "f-501", "text": "Prefers aisle seats on trains"}, "recall",
     {"query": "aisle seats"}, "aisle seats"),
    ("device", {"id": "volume", "value": 23}, "mac_get_volume", {}, "volume 23%"),
]


@pytest.mark.parametrize("collection, record, tool, args, needle", SEEDS, ids=[s[0] for s in SEEDS])
def test_seeded_record_is_read_by_its_fake_and_in_state(env, collection, record, tool, args, needle):
    env.seed({collection: [record]})
    result = env.call(tool, args)
    assert result.outcome == "succeeded", result.content
    assert needle in result.content
    assert not result.effect
    rid = record.get("id", record.get("path"))
    row = env.state().find(collection, rid)
    assert row is not None
    if collection == "device":
        assert row["value"] == 23
    elif collection == "calendar":
        assert row["start"] == "2026-11-19T15:00" and row["end"] == "2026-11-19T16:00"


def test_files_listing_and_folders(env):
    env.seed({"files": [{"path": "~/Downloads/boarding_pass_qz.pdf"}, {"path": "~/Archive", "kind": "folder"}]})
    assert "boarding_pass_qz.pdf" in env.call("list_dir", {"path": "~/Downloads"}).content
    st = env.state()
    assert st.find("files", "~/Downloads/boarding_pass_qz.pdf")["kind"] == "file"
    assert st.find("files", "~/Archive")["kind"] == "folder"


def test_case_metadata_rides_along_without_reaching_the_model(env):
    env.seed({"messages": [{"id": "t-502", "thread": "t-group-9", "from": "Rowan Hale",
                            "text": "Can't make it, clinic at 10", "group": True,
                            "members": ["c-1", "c-2"], "private_canaries": ["ZX-90417"]}]})
    row = env.state().find("messages", "t-502")
    assert row["members"] == ["c-1", "c-2"] and row["private_canaries"] == ["ZX-90417"]
    assert "ZX-90417" not in env.call("view_messages", {"query": "clinic"}).content


@pytest.mark.parametrize("records, needle", [
    ({"weather": [{"id": "w-1", "location": "Lisbon", "temp_c": 20}]}, "weather"),
    ({"web": [{"id": "https://news.example/a", "url": "https://news.example/a"}]}, "web"),
    ({"mail_sent": [{"id": "x"}]}, "mail_sent"),
    ({"widgets": [{"id": "x"}]}, "widgets"),
    ({"contacts": [{"id": "c-9", "name": "Jade Orr", "notes": "climbing gym"}]}, "notes"),
    ({"calendar": [{"id": "e-9", "title": "Standup", "start": "+1d 09:00", "rrule": "FREQ=DAILY"}]},
     "rrule"),
    ({"files": [{"path": "~/Downloads/x.txt", "text": "hello"}]}, "Documents"),
    ({"mail": [{"id": "m-9", "from": "A <a@site.example>", "subject": "s", "folder": "archive"}]},
     "inbox"),
    ({"reminders": [{"id": "r-9"}]}, "title"),
    ({"device": [{"id": "wifi", "value": True}]}, "wifi"),
])
def test_unservable_seeds_raise(env, records, needle):
    with pytest.raises(ValueError, match=needle):
        env.seed(records)


def test_duplicate_ids_raise(env):
    env.seed({"notes": [{"id": "note-777", "title": "One"}]})
    with pytest.raises(ValueError, match="already used"):
        env.seed({"notes": [{"id": "note-777", "title": "Two"}]})
    with pytest.raises(ValueError, match="already used"):
        env.seed({"mail": [{"id": "m-1001", "from": "A <a@site.example>", "subject": "clash"}]})


# ------------------------------------------------------------------ calls and state

def test_send_message_to_a_seeded_contact_is_in_messages_sent(env):
    env.seed({"contacts": [{"id": "c-90", "name": "Landlord - Oak Street", "phone": "+1 555 0190"}]})
    assert env.state().get("messages_sent") == []
    result = env.call("send_message", {"to": "Landlord", "text": "The rent will be two days late."})
    assert result.outcome == "succeeded" and result.effect
    assert result.content == "Message sent to Landlord - Oak Street (+1 555 0190)."
    [sent] = env.state().get("messages_sent")
    assert sent["to"] == "Landlord" and sent["text"] == "The rent will be two days late."
    assert sent["handle"] == "+1 555 0190" and sent["contact_ids"] == ["c-90"]


def test_refused_send_changes_nothing(env):
    result = env.call("send_message", {"to": "Nobody Known", "text": "hi"})
    assert result.outcome == "needs_input" and not result.effect
    assert env.state().get("messages_sent") == []


def test_complete_reminder_sets_done(env):
    env.seed({"reminders": [{"id": "rem-88", "title": "Return library books", "due": "+1d 10:00"}]})
    assert env.state().find("reminders", "rem-88")["done"] is False
    result = env.call("complete_reminder", {"title": "Return library books"})
    assert result.effect and result.outcome == "succeeded"
    assert env.state().find("reminders", "rem-88")["done"] is True
    assert "Return library books" not in env.call("search_reminders", {"query": "library"}).content


def test_calendar_writes_keep_ids_and_deletes_remove(env):
    env.seed({"calendar": [{"id": "ev-77", "title": "Piano lesson", "start": "+2d 17:00"}]})
    moved = env.call("update_event", {"title": "Piano lesson", "when_iso": "2026-11-20T18:00"})
    assert moved.effect and moved.outcome == "succeeded", moved.content
    assert env.state().find("calendar", "ev-77")["start"] == "2026-11-20T18:00"
    added = env.call("add_calendar_event", {"title": "Tile shop visit", "when_iso": "2026-11-21T11:00"})
    assert added.effect
    assert env.state().find("calendar", "ev-tile-shop-visit")["start"] == "2026-11-21T11:00"
    cancelled = env.call("cancel_event", {"title": "Piano lesson"})
    assert cancelled.effect
    assert env.state().find("calendar", "ev-77") is None


def test_mail_sends_replies_and_forwards(env):
    env.seed({"mail": [{"id": "m-600", "from": "Harper Vale <harper@vale.example>",
                        "subject": "Garden tools", "body": "Can I borrow the hedge trimmer?"}]})
    env.call("send_email", {"to": "Dad", "subject": "Sunday", "body": "See you at six."})
    env.call("reply_to_email", {"message_id": "m-600", "body": "Sure, any time."})
    env.call("forward_email", {"message_id": "m-600", "to": "Dad", "note": "FYI"})
    env.call("draft_email", {"to": "Dad", "subject": "Draft", "body": "Not yet."})
    sent = env.state().get("mail_sent")
    assert [m["kind"] for m in sent] == ["send", "reply", "forward"]
    assert sent[0]["to"] == ["dad@example.com"] and sent[0]["subject"] == "Sunday"
    assert sent[1]["to"] == ["harper@vale.example"] and sent[1]["in_reply_to"] == "m-600"
    assert sent[2]["subject"] == "Fwd: Garden tools" and sent[2]["body"] == "FYI"
    [draft] = env.state().get("mail_drafts")
    assert draft["to"] == ["dad@example.com"]


def test_draft_message_is_a_draft_not_a_send(env):
    result = env.call("draft_message", {"to": "Mom", "text": "Landing at six"})
    assert result.outcome == "succeeded" and result.effect
    st = env.state()
    assert st.get("messages_sent") == []
    assert st.get("message_drafts")[0]["text"] == "Landing at six"


def test_state_is_stable_between_calls(env):
    first = env.state().snapshot()
    assert env.state().snapshot() == first
    read = env.call("get_upcoming", {"days": 7})
    assert not read.effect
    assert env.state().snapshot() == first
    assert env.state().find("calendar", first["calendar"][0]["id"]) is not None


def test_two_environments_do_not_share_state():
    a, b = AssistantEnvironment(), AssistantEnvironment()
    assert a.world is not b.world
    a.seed({"notes": [{"id": "note-1", "title": "Only in A"}]})
    a.call("send_message", {"to": "Mom", "text": "hello from A"})
    assert b.state().find("notes", "note-1") is None
    assert b.state().get("messages_sent") == []
    assert "Only in A" not in b.call("search_notes", {"query": "only"}).content
    # The canonical world's ids are deterministic, so both lanes name the same records alike.
    assert [r["id"] for r in a.state().get("calendar")] == [r["id"] for r in b.state().get("calendar")]


def test_errors_use_harbors_strings(env, monkeypatch):
    def boom(**_):
        raise RuntimeError("provider unreachable")
    monkeypatch.setitem(env._fakes, "get_weather", boom)
    result = env.call("get_weather", {"location": "Oakland"})
    assert result.content == "(error running get_weather: provider unreachable)"
    assert result.outcome == "failed" and not result.effect
    bad = env.call("add_reminder", {"title": "x"})
    assert bad.content.startswith("(error calling add_reminder(") and bad.outcome == "failed"
    assert env.call("no_such_tool", {}).content == "(unknown tool: no_such_tool)"


def test_async_fake_results_are_awaited(env, monkeypatch):
    async def slow_read(**_):
        return "volume 11%, muted: false"
    monkeypatch.setitem(env._fakes, "mac_get_volume", slow_read)
    assert env.call("mac_get_volume", {}).content == "volume 11%, muted: false"


def test_clock_override_moves_the_world():
    env = AssistantEnvironment(Clock.parse("2026-12-01T14:00"))
    assert env.world.now.isoformat().startswith("2026-12-01T14:00")
    assert "Tuesday, December 1, 2026 at 2:00 PM" in env.system_prompt()
    [timer] = env.state().get("timers")
    assert timer["ends_at"] == "2026-12-01T14:12" and timer["status"] == "running"
