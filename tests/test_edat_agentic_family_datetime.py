"""Date and time cases (spec 02 §3.17): counts, structure, a sampled oracle check, and wrong behaviours that fail."""
from __future__ import annotations

import copy
import random
from collections import Counter

import pytest

from edat.cases.bind import bind
from edat.cases.generators import datetime_cases as dt
from edat.cases.oracle import check_case
from edat.cases.runner import run_trial
from edat.cases.schema import problems
from edat.models.stub import ScriptedClient


@pytest.fixture(scope="module")
def cases():
    return dt.generate("dev", random.Random("datetime_cases|20261118"))


def by_prefix(cases, prefix):
    return next(c for c in cases if c["id"].startswith(prefix))


def test_counts(cases):
    lanes = Counter(c["lane"] for c in cases)
    assert lanes == {"assistant": 25, "neutral": 8}
    fams = Counter(c["family"] for c in cases)
    assert set(fams) == {"select", "sequential", "abstain", "pev"}
    assert fams["pev"] == 3 and fams["sequential"] == 2 and fams["abstain"] == 6
    subs = Counter(c["sub"] for c in cases)
    assert subs["recurring"] == 3 and subs["timezone"] == 3 and subs["dst_edge"] == 3
    assert subs["month_end"] == 4 and subs["ambiguous_weekday"] == 6 and subs["duration"] == 3


def test_valid_unique_tagged_and_the_clock_is_overridden(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    held = dt.generate("heldout", random.Random("datetime_cases|424242"))
    assert not {c["id"] for c in held} & set(ids)
    for c in cases:
        assert problems(c) == [], c["id"]
        assert "datetime" in c["tags"] and c["world"].get("clock"), c["id"]
    core = sum("core" in c["tags"] for c in cases)
    assert 0.3 <= core / len(cases) <= 0.5
    assert any(c["world"].get("tz") == "Europe/London" for c in cases if c["lane"] == "neutral")


def test_recurring_cases_are_neutral_only_and_use_rrule(cases):
    rec = [c for c in cases if c["sub"] == "recurring"]
    assert rec and all(c["lane"] == "neutral" for c in rec)
    assert all("rrule" in str(c["final_state"]) for c in rec)


def test_abstain_cases_have_unambiguous_counters(cases):
    ab = [c for c in cases if c["family"] == "abstain"]
    assert any("counter" in c["tags"] and c["actionable"] for c in ab)
    assert any(c.get("clarify") for c in ab)


def test_sampled_oracles_pass_and_null_fails(cases):
    for prefix in ("dt_ast_rel_reminder_001", "dt_ast_dst_edge_001", "dt_ast_follow_up_001", "dt_ast_ambiguous_001",
                   "dt_neu_recur_last_friday_001", "dt_neu_tz_call_001", "dt_neu_leap_day_001"):
        res = check_case(by_prefix(cases, prefix), seeds=(1,))
        assert res.ok, (prefix, res.problems)


def replay(case, mutate=None, seed=1):
    """Replay the oracle, optionally changing the arguments of its first call."""
    bound = bind(case, seed)
    steps = copy.deepcopy([s for sess in bound["sessions"] for t in sess["turns"] for s in t["oracle"]])
    if mutate:
        mutate(steps[0]["calls"][0]["arguments"])
    return run_trial(case, ScriptedClient(steps), world_seed=seed).verdict


def shift_hour(args, hours=1):
    key = "when_iso" if "when_iso" in args else "start"
    day, hhmm = args[key].split("T")
    h, m = hhmm.split(":")
    args[key] = f"{day}T{(int(h) + hours) % 24:02d}:{m}"


def test_wrong_date_fails(cases):
    c = by_prefix(cases, "dt_ast_rel_reminder_001")

    def wrong(args):
        d = args["when_iso"].split("T")
        day = int(d[0][-2:])
        args["when_iso"] = f"{d[0][:-2]}{(day % 27) + 1:02d}T{d[1]}"
    assert replay(c).success and not replay(c, wrong).success


def test_unconverted_time_zone_and_stale_dst_offset_fail(cases):
    for prefix in ("dt_ast_tz_call_001", "dt_ast_dst_edge_001", "dt_ast_dst_edge_002", "dt_neu_dst_edge_001"):
        c = by_prefix(cases, prefix)
        assert replay(c).success, prefix
        assert not replay(c, shift_hour).success, prefix


def test_month_end_and_leap_day_edges(cases):
    c = by_prefix(cases, "dt_ast_leap_day_001")
    assert replay(c).success

    def feb28(args):
        args["when_iso"] = args["when_iso"].replace("-02-29", "-02-28")
    assert not replay(c, feb28).success


def test_ambiguous_weekday_silent_guess_fails_and_always_asking_fails_the_counter(cases):
    amb = by_prefix(cases, "dt_ast_ambiguous_001")
    v = run_trial(amb, ScriptedClient([{"calls": [{"name": "add_calendar_event",
                                                   "arguments": {"title": "Hartwell Joinery fitting", "when_iso": "2027-06-04T15:00"}}]},
                                       {"text": "Added."}]), world_seed=1).verdict
    assert not v.success
    counter = by_prefix(cases, "dt_ast_unambiguous_001")
    v2 = run_trial(counter, ScriptedClient([{"text": "Do you mean this Monday or next week's?"}]), world_seed=1).verdict
    assert not v2.success and v2.needless_question is True


def test_wrong_recurrence_fails(cases):
    c = by_prefix(cases, "dt_neu_recur_biweekly_001")
    assert replay(c).success

    def weekly(args):
        args["recurrence"] = args["recurrence"].replace(";INTERVAL=2", "")
    assert not replay(c, weekly).success
