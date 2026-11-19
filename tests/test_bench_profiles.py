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
