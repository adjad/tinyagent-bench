"""Family Long-session (spec 02 §3.15): structure, trimming, and late fact probes that only memory can answer."""
from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from edat.cases.bind import bind
from edat.cases.generators import long_session
from edat.cases.oracle import check_case, oracle_turns
from edat.cases.runner import LONG_SESSION_HISTORY_BUDGET, run_trial
from edat.cases.schema import problems
from edat.models.stub import ScriptedClient

SAVE_TOOLS = ("remember", "memory_save")


@pytest.fixture(scope="module")
def cases():
    return long_session.generate("dev", random.Random("long_session|20261118"))


def turns_of(bound):
    return bound["sessions"][0]["turns"]


def offsets(bound):
    out, i = [], 0
    for t in turns_of(bound):
        out.append(i)
        i += len(t["oracle"])
    return out


def run(case, steps, seed=1):
    client = ScriptedClient(steps)
    return run_trial(case, client, world_seed=seed), client


def turn_requests(client, user_text):
    """The requests the model saw during the turn that starts with `user_text`."""
    out, inside = [], False
    for req in client.requests:
        last = req["messages"][-1]
        if last["role"] == "user":
            inside = last["content"] == user_text
        if inside:
            out.append(req["messages"])
    return out


def text_of(messages):
    return json.dumps(messages, ensure_ascii=False)


def late_fact_probes(bound):
    return [(i, t) for i, t in enumerate(turns_of(bound)) if t.get("band") == "late" and t.get("probe") == "fact"]


def saved_texts(bound):
    return [c["arguments"].get("fact") or c["arguments"].get("text")
            for t in turns_of(bound)[1:8] for st in t["oracle"] for c in st.get("calls") or [] if c["name"] in SAVE_TOOLS]


def test_counts_and_structure(cases):
    assert Counter(c["lane"] for c in cases) == {"assistant": 4, "neutral": 2}
    assert len({c["id"] for c in cases}) == 6
    for c in cases:
        assert problems(c) == [], c["id"]
        assert c["family"] == "long_session" and len(c["turns"]) == 60
        assert len(c["stored_preferences"]) == 3
        assert all(t.get("oracle") for t in c["turns"])
        for band, lo, hi in (("early", 10, 19), ("late", 45, 60)):
            idx = [i + 1 for i, t in enumerate(c["turns"]) if t.get("band") == band]
            assert len(idx) == 10 and lo <= min(idx) and max(idx) <= hi, (c["id"], band, idx)
            kinds = Counter(t["probe"] for t in c["turns"] if t.get("band") == band)
            assert kinds == {"fact": 3, "preference": 3, "tool": 4}, (c["id"], band, kinds)
        plants = " ".join(t["user"] for t in c["turns"][1:8])
        assert plants.count("From now on") == 3 and plants.count("Please remember that.") == 6
    assert sum("core" in c["tags"] for c in cases) == 2


def test_oracle_check_on_one_session_per_lane(cases):
    for c in (cases[0], cases[4]):
        res = check_case(c, seeds=(1,))
        assert res.ok, (c["id"], res.problems)


@pytest.mark.parametrize("which", [0, 4])
def test_history_is_trimmed_and_late_facts_come_only_from_memory(cases, which):
    case = cases[which]
    bound = bind(case, 1)
    result, client = run(case, oracle_turns(bound))
    assert result.verdict.success, result.verdict.reasons[:3]
    trims = result.run.trim_events
    assert trims and trims[0]["before_turn"] >= 15          # trimming starts after the early band
    plants = [t["user"] for t in turns_of(bound)[1:8]]
    # early band: every plant still in the window
    for t in turns_of(bound)[9:19]:
        seen = text_of(turn_requests(client, t["user"])[0])
        assert all(json.dumps(p, ensure_ascii=False)[1:-1] in seen for p in plants), t["user"]
    # late band: the plants are gone; a remembered fact reaches the model only through the memory tool
    saved = saved_texts(bound)
    for ti, t in late_fact_probes(bound):
        reqs = turn_requests(client, t["user"])
        first = text_of(reqs[0])
        assert not any(json.dumps(p, ensure_ascii=False)[1:-1] in first for p in plants)
        if t["oracle"][0].get("calls"):                       # a fact the user asked to remember
            value = t["oracle"][-1]["text"].split(": ")[-1].rstrip(".")
            mem = [s for s in saved if value in s]
            assert mem and mem[0] not in first
            assert mem[0] in text_of(reqs[-1])                 # ... is in the memory tool's result


def test_without_saves_the_late_fact_is_unreachable_and_honesty_passes(cases):
    case = cases[4]
    bound = bind(case, 1)
    turns = turns_of(bound)
    for t in turns[1:8]:                                      # the model saves nothing
        t["oracle"] = [{"text": "Noted."}]
    late = late_fact_probes(bound)
    for _, t in late:
        if t["oracle"][0].get("calls"):
            t["oracle"] = [t["oracle"][0], {"text": "I no longer have that from earlier in our conversation."}]
    steps = [st for t in turns for st in t["oracle"]]
    result, client = run(case, steps)
    saved = saved_texts(bind(case, 1))
    for ti, t in late:
        assert not any(s in text_of(turn_requests(client, t["user"])[-1]) for s in saved)
    by_turn = {x["turn"]: x["ok"] for x in result.verdict.turns}
    assert all(by_turn[ti] for ti, _ in late)                # an honest "no longer have it" passes


def test_a_decoy_value_at_a_late_fact_probe_fails(cases):
    case = cases[4]
    bound = bind(case, 1)
    facts = bound["_facts"]
    off = offsets(bound)
    steps = oracle_turns(bound)
    ti, t = late_fact_probes(bound)[0]
    key = next(k for k, spec in long_session.FACTS.items() if spec[1] == t["user"])
    end = off[ti] + len(t["oracle"]) - 1
    steps[end] = {"text": f"It's {facts['d_' + key]}."}
    result, _ = run(case, steps)
    by_turn = {x["turn"]: x["ok"] for x in result.verdict.turns}
    assert by_turn[ti] is False and not result.verdict.success


def test_budget_constant_is_the_spec_value():
    assert LONG_SESSION_HISTORY_BUDGET == 9300
