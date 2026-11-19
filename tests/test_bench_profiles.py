"""The synthetic benchmark profiles (test_fixtures/bench_profiles) are valid and
load into both of Harbor's synthetic worlds."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts.bench_profiles import build, validate
from scripts.bench_profiles.adapters import to_sandbox_state, to_toolbench_world
from scripts.bench_profiles.render import FIXTURES, PROFILE_IDS, read_raw, render, render_tokens

each = pytest.mark.parametrize("pid", PROFILE_IDS)


@each
def test_validator_passes(pid):
    errs = validate.validate_all([pid], check_volume=True)
    assert errs == [], "\n".join(errs[:40])


@each
def test_fixture_matches_generator(pid):
    """profile.json and ledger.json are exactly what build.py writes from source."""
    profile, ledger = build.build_one(pid)
    on_disk, on_disk_ledger = read_raw(pid)
    assert json.loads(build._dump(profile)) == on_disk
    assert json.loads(build._dump(ledger)) == on_disk_ledger


@each
def test_rebase_keeps_order_and_renders_tokens(pid):
    profile, _ = read_raw(pid)
    anchor = date.fromisoformat(profile["anchor"]["date"])
    moved = render(profile, today=anchor + timedelta(days=35))  # same weekday, five weeks on
    assert moved["anchor"]["weekday"] == profile["anchor"]["weekday"]
    blob = json.dumps(moved, ensure_ascii=False)
    assert "{{" not in blob
    now = moved["anchor"]["now_ts"]
    for th in moved["threads"]:
        assert all(m["ts"] <= now for m in th["messages"])
    assert all(e["ts"] <= now for e in moved["emails"])


@each
def test_sandbox_state_shape(pid):
    profile, _ = read_raw(pid)
    state = to_sandbox_state(profile)
    assert state["clock"]["mode"] == "frozen"
    inbox = [e for e in state["emails"].values() if e["mailbox"] == "inbox"]
    assert inbox and all(e["ts_off"] <= 0 for e in inbox)
    assert sum(len(t["messages"]) for t in state["threads"].values()) == \
        sum(len(t["messages"]) for t in profile["threads"])
    assert len(state["calendar"]) == len(profile["events"])
    # The sandbox's own sync renderers accept the state.
    from tests.fixtures import wire
    rows = [{"ts": state["booted_at"] + m["ts_off"], "context": t["context_label"] or t["title"] or "Group",
             "who": "Me" if m["from"] == "me" else m["from"], "text": m["text"]}
            for t in state["threads"].values() for m in t["messages"]]
    assert wire.messages_lines(rows).count("\n") == len(rows) - 1


@each
def test_toolbench_world_shape(pid):
    profile, _ = read_raw(pid)
    w = to_toolbench_world(profile)
    assert w.today.isoformat() == profile["anchor"]["date"]
    assert w.events and w.past_events and w.emails and w.messages and w.contacts
    w.reset()  # snapshot/reset round-trips
    assert len(w.messages) == sum(len(t["messages"]) for t in profile["threads"])


@each
def test_ledger_is_never_inside_profile(pid):
    """The answer key must stay out of what Harbor sees."""
    profile, ledger = read_raw(pid)
    blob = json.dumps(profile)
    assert "L-" + pid + "-" not in blob
    for ent in ledger["entries"]:
        assert ent["why"] not in blob


def test_token_renderer():
    d = date(2026, 10, 13)
    assert render_tokens("{{dow:+2}} / {{date:-1:short}} / {{date:+3:long:es}}", d) == \
        "Thursday / Oct 12 / viernes 16 de octubre"
    assert FIXTURES.name == "bench_profiles"


# ---- windows.py: the true schedule and time windows -------------------------------------

def _tiny():
    ev = lambda id, day, t, e, **kw: {"id": id, "series_id": None, "calendar": kw.get("cal", "Home"),
                                      "account": None, "kind": "event", "title": id, "day": day,
                                      "time": t, "end": e, "all_day": False, "days": 1,
                                      "location": kw.get("loc"), "organizer": None,
                                      "organizer_is_user": True, "invitee_count": 0,
                                      "calendar_writable": True, "context": None, "notes": None,
                                      "status": "confirmed"}
    profile = {"events": [ev("ev-lab", 0, "14:00", "16:50"), ev("ev-shift", 0, "17:00", "21:00"),
                          ev("ev-lecture", 0, "09:30", "10:45"), ev("ev-partner", 0, "07:00", "19:30"),
                          ev("ev-late", 0, "23:00", "23:59")],
               "reminders": [{"id": "rem-a", "title": "pay", "day": 0, "time": "12:00", "completed": False},
                             {"id": "rem-b", "title": "done", "day": 0, "time": "12:30", "completed": True}]}
    ledger = {"schedule_ops": [
        {"id": "op-1", "op": "move", "ref": "ev-lab", "day": 2, "time": "15:30", "end": None,
         "location": "B07", "title": None, "because": ["em-x"]},
        {"id": "op-2", "op": "cancel", "ref": "ev-shift", "because": ["msg-y"]},
        {"id": "op-3", "op": "add", "ref": None, "title": "Study", "day": 0, "time": "19:00",
         "end": "20:45", "location": "Library", "because": ["msg-z"]},
        {"id": "op-4", "op": "exclude", "ref": "ev-partner", "because": ["ev-partner"]},
        {"id": "op-5", "op": "add", "ref": None, "title": "Call", "day": 1, "time": "00:30",
         "end": "01:00", "location": None, "because": ["msg-w"]}]}
    return profile, ledger


def test_true_schedule_applies_every_op():
    from scripts.bench_profiles.windows import true_schedule, stale_calendar
    profile, ledger = _tiny()
    sched = {i["ref"]: i for i in true_schedule(profile, ledger)}
    assert "ev-shift" not in sched and "ev-partner" not in sched
    lab = sched["ev-lab"]
    assert (lab["day"], lab["time"], lab["end"], lab["location"]) == (2, "15:30", "18:20", "B07")
    assert sched["op-3"]["time"] == "19:00" and sched["op-3"]["because"] == ["msg-z"]
    assert {s["ref"] for s in stale_calendar(profile, ledger)} == {"ev-lab", "ev-shift", "ev-partner"}


def test_in_window_counts_running_events_reminders_and_stale_rows():
    from scripts.bench_profiles.windows import in_window
    profile, ledger = _tiny()
    w = in_window(profile, ledger, 0, "10:00", 3)
    assert [i["ref"] for i in w["items"]] == ["ev-lecture"] and w["items"][0]["running"]
    assert [r["id"] for r in w["reminders"]] == ["rem-a"]          # the completed one is left out
    assert {s["ref"] for s in w["stale"]} == {"ev-partner"}
    w = in_window(profile, ledger, 0, "15:00", 3)
    assert w["items"] == [] and {s["ref"] for s in w["stale"]} == {"ev-lab", "ev-shift", "ev-partner"}
    w = in_window(profile, ledger, 0, "22:30", 3)                  # crosses midnight
    assert [i["ref"] for i in w["items"]] == ["ev-late", "op-5"]


@each
def test_generated_windows_match_the_helper(pid):
    from scripts.bench_profiles.windows import window_entry
    profile, ledger = read_raw(pid)
    wins = [e for e in ledger["entries"] if e["type"] == "time_window"]
    assert len(wins) >= 3
    for e in wins:
        w = e["expected"]["window"]
        fresh = window_entry(profile, ledger, w["day"], w["at"], w["hours"])
        assert fresh["expected"] == e["expected"], e["id"]
