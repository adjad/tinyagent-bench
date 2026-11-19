"""Scaling probe (spec 02 §3.10): conditions, probe set, menu checks, history padding, fits, robustness."""
from __future__ import annotations

import json
import random

import pytest

from edat.cases.bind import bind
from edat.cases.generators import _example
from edat.cases.oracle import check_case, oracle_turns
from edat.cases.probe import (CONDITIONS, HISTORY_TOKENS, MENU_SIZES, NOT_RUN_CONTEXT, PADDING,
                              Condition, check_menu, estimate_tokens, fits, history_fact_case, history_padding,
                              padding_tool_names, probe_set, robustness, run_discovery, run_probe)
from edat.cases.runner import default_env_factory, run_trial
from edat.models.stub import ScriptedClient
from edat.state.clock import Clock

CASES = _example.generate("dev", random.Random("x"))
AST = next(c for c in CASES if c["lane"] == "assistant")
NEU = next(c for c in CASES if c["lane"] == "neutral")
LANE_TOOLS = {"assistant": 105, "neutral": 108}


def oracle_client(case, ws=1):
    return ScriptedClient(oracle_turns(bind(case, ws)), name="oracle")


def test_conditions():
    assert [c.name for c in CONDITIONS] == ["M5", "M10", "M25", "M50", "M100", "H2k", "H8k", "H16k"]
    assert [c.menu_size for c in CONDITIONS if c.history_tokens == 0] == list(MENU_SIZES) == [5, 10, 25, 50, 100]
    assert [c.history_tokens for c in CONDITIONS if c.name.startswith("H")] == [2000, 8000, 16000]
    assert HISTORY_TOKENS == (0, 2000, 8000, 16000)
    assert {c.menu_size for c in CONDITIONS if c.name.startswith("H")} == {10}


@pytest.mark.parametrize("case", [AST, NEU], ids=["assistant", "neutral"])
def test_menu_keeps_needed_tools_and_caps_at_the_lane(case):
    for m in MENU_SIZES:
        assert check_menu(case, m) == []
    from edat.cases.probe import menu_for
    assert len(menu_for(case, 5)) == 5 and len(menu_for(case, 100)) == 100
    assert len(menu_for(case, 500)) == LANE_TOOLS[case["lane"]]
    # a case that needs more tools than M offers them all
    many = dict(case, id=case["id"] + "_many", menu_extra=list(menu_for(case, 100)[:8]))
    assert len(menu_for(many, 5)) >= 8 and check_menu(many, 5) == []


def lane_env(lane):
    return default_env_factory(lane, Clock.parse(), 1)


@pytest.mark.parametrize("lane", ["assistant", "neutral"])
def test_padding_calls_real_tools_with_schema_arguments(lane):
    env = lane_env(lane)
    names = padding_tool_names(lane)
    assert names and names <= set(env.tool_names())
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in env.schemas(sorted(names))}
    for seed in range(12):
        for fn in PADDING[lane]:
            for rows in (1, 4, 12):
                turn = fn(random.Random(seed), rows)
                for name, args, result in turn["calls"]:
                    params = schemas[name]
                    assert set(args) <= set(params["properties"]), (name, args)
                    assert set(params.get("required") or []) <= set(args), (name, args)
                    assert isinstance(result, str) and result


@pytest.mark.parametrize("lane", ["assistant", "neutral"])
@pytest.mark.parametrize("tokens", [2000, 8000, 16000])
def test_history_padding_size_and_structure(lane, tokens):
    msgs = history_padding(tokens, random.Random(3), lane)
    est = estimate_tokens(msgs)
    assert tokens * 0.93 <= est <= tokens * 1.05, est
    assert msgs[0]["role"] == "user" and msgs[-1]["role"] == "assistant" and not msgs[-1].get("tool_calls")
    open_calls = {}
    for m in msgs:
        if m["role"] == "assistant" and m.get("tool_calls"):
            for c in m["tool_calls"]:
                assert c["function"]["name"] in lane_env(lane).tool_names()
                json.loads(c["function"]["arguments"])
                open_calls[c["id"]] = c["function"]["name"]
        elif m["role"] == "tool":
            assert open_calls.pop(m["tool_call_id"]) == m["name"]
    assert not open_calls
    ids = [c["id"] for m in msgs for c in m.get("tool_calls") or []]
    assert len(ids) == len(set(ids)) and len(ids) > 3


def test_history_padding_is_deterministic_and_zero_is_empty():
    assert history_padding(2000, random.Random(1), "neutral") == history_padding(2000, random.Random(1), "neutral")
    assert history_padding(2000, random.Random(1), "neutral") != history_padding(2000, random.Random(2), "neutral")
    assert history_padding(0, random.Random(1), "assistant") == []
    turn = {"user": "note this", "assistant": "noted"}
    got = history_padding(0, random.Random(1), "assistant", first_turn=turn)
    assert got == [{"role": "user", "content": "note this"}, {"role": "assistant", "content": "noted"}]
    got = history_padding(2000, random.Random(1), "assistant", first_turn=turn)
    assert got[:2] == [{"role": "user", "content": "note this"}, {"role": "assistant", "content": "noted"}]


def test_history_padding_avoids_given_words():
    msgs = history_padding(8000, random.Random(4), "neutral", avoid={"Tallinn", "bakery", "Fernhill"})
    blob = json.dumps(msgs).lower()
    assert "tallinn" not in blob and "bakery" not in blob and "fernhill" not in blob


# ------------------------------------------------------------------ the history-fact rewrite


def test_history_fact_case_moves_the_name_out_of_the_prompt():
    new, turn = history_fact_case(NEU)
    first = NEU["world"]["seed_records"]["contacts"][0]["name"].split()[0]
    assert first in NEU["prompt"] and first not in new["prompt"]
    assert new["id"] == NEU["id"] + "__hist" and new["variant_of"] == NEU["id"] and new["variant"] == "history_fact"
    assert NEU["world"]["seed_records"]["contacts"][0]["name"] in turn["user"] + turn["assistant"]
    assert "the person I mentioned earlier" in new["prompt"] and "{mins}" in new["prompt"]
    assert check_case(new, seeds=(1,)).ok            # the oracle and the null model still behave
    assert history_fact_case(AST) is None            # nothing to move


def test_history_fact_case_city_placeholder_and_unclean_cases():
    city = dict(NEU, id="w_neu_city_001", prompt="What's the weather in {city} today?",
                facts={"city": {"choice": ["Porto", "Lyon"]}}, world={})
    new, turn = history_fact_case(city)
    assert "{city}" not in new["prompt"] and "{city}" in turn["user"]
    lit = dict(city, prompt="Is it raining in Porto or Lyon?", facts={})
    assert history_fact_case(lit) is None                        # two places: not clean
    poss = json.loads(json.dumps(NEU))
    poss["prompt"] = poss["prompt"].replace("Text ", "Is ").replace(" that I'm", "'s phone ok? I'm")
    assert history_fact_case(poss) is None                       # possessive: not clean
    assert history_fact_case(dict(NEU, turns=[{"user": "x"}], prompt=None)) is None if "turns" in NEU else True


def test_fact_case_needs_the_history_turn_to_be_solved():
    new, turn = history_fact_case(NEU)
    seen = []

    def spy(messages, tools, i):
        seen.append(list(messages))
        return {"text": "OK."}

    out = run_probe(new, ScriptedClient([spy], on_exhaust="stop"), Condition("H0", 10, 0), 1, fact_turn=turn)
    assert out.row["fact_in_history"] and out.row["success"] is False
    msgs = seen[0]
    assert msgs[1]["role"] == "user" and "get in touch with" in msgs[1]["content"]
    assert NEU["world"]["seed_records"]["contacts"][0]["name"] in msgs[1]["content"]
    assert msgs[-1]["content"] == bind(new, 1)["sessions"][0]["turns"][0]["user"]


# ------------------------------------------------------------------ the probe set


def fake_cases(per=40):
    names = ["Ines Duarte", "Tomas Okafor", "Wren Lindqvist", "Kofi Mensah", "Mei Tanaka", "Rafael Ferreira"]
    out = []
    for lane in ("assistant", "neutral"):
        for fam in ("select", "parallel", "sequential", "mixed"):
            for i in range(per):
                nm = names[i % len(names)]
                out.append({"id": f"{fam[:3]}_{lane[:3]}_{i:03d}", "lane": lane, "family": fam, "sub": f"s{i % 2}",
                            "prompt": f"Text {nm.split()[0]} that I'm late" if i % 2 == 0 else "What is the date?",
                            "world": {"seed_records": {"contacts": [{"id": "c-1", "name": nm, "phone": "+1 555 0101"}]}},
                            "expect": [{"tool": "messages_send" if lane == "neutral" else "send_message"}] * (1 + i % 3 * 3),
                            "oracle": [{"text": "x"}]})
    out.append({"id": "sel_neu_pin_001", "lane": "neutral", "family": "select", "prompt": "x", "menu_pin": ["math_calculate"]})
    return out


def test_probe_set_counts_quarter_and_seed():
    cases = fake_cases()
    ps = probe_set(cases, random.Random("probe|20261118"), n=20)
    assert ps.seed == "probe|20261118"
    for lane in ("assistant", "neutral"):
        got = ps.cases(lane)
        assert len(got) == 20 and len({c["id"] for c in got}) == 20 and all(c["lane"] == lane for c in got)
        assert {c["family"] for c in got} == {"select", "parallel", "sequential"}
        fact = [c for c in got if c["id"] in ps.fact_turns]
        assert len(fact) == 5 and all(c["variant"] == "history_fact" for c in fact)
        assert all(c["id"] != "sel_neu_pin_001" for c in got)
    assert ps.warnings == [] or all("needs more than" in w or "only" in w for w in ps.warnings)
    again = probe_set(list(reversed(cases)), random.Random("probe|20261118"), n=20)
    assert [c["id"] for c in again.cases()] == [c["id"] for c in ps.cases()]
    assert probe_set(cases, n=20).seed == "probe|20261118"
    assert probe_set(cases, n=20, seed="other|1").seed == "other|1"


def test_probe_set_prefers_cases_that_fit_five_tools_and_reports_shortfalls():
    ps = probe_set(fake_cases(per=12), random.Random(1), n=20)
    assert len(ps.cases("neutral")) <= 20
    ps = probe_set([c for c in fake_cases() if not c["prompt"].startswith("Text ")], random.Random(1), n=20)
    assert not ps.fact_turns and any("history-fact" in w for w in ps.warnings)
    assert len(ps.cases("assistant")) == 20


# ------------------------------------------------------------------ fits and running


def test_fits():
    assert fits(None, 10**6)
    assert fits(8192, 6000) and not fits(8192, 6200) and fits(8192, 6144) and not fits(8192, 6145)
    assert fits(8192, 7000, output_reserve=1000) and not fits(4096, 3000)


def test_run_probe_passes_menu_and_history_and_grades():
    out = run_probe(AST, oracle_client(AST), Condition("H2k", 10, 2000), 1)
    assert out.row["status"] == "ok" and out.row["success"] is True and out.row["menu_actual"] == 10
    assert 1800 <= out.row["prompt_tokens_est"] and out.result.verdict.success
    assert out.row["case_id"] == AST["id"] and out.row["trial"] == 1 and out.row["history_tokens"] == 2000
    client = oracle_client(NEU)
    out = run_probe(NEU, client, Condition("M25", 25, 0), 1)
    assert out.row["success"] is True and len(client.requests[0]["tools"]) == 25
    seen = []
    client = ScriptedClient([lambda m, t, i: seen.append(list(m)) or {"text": "OK."}], on_exhaust="stop")
    run_probe(NEU, client, Condition("H8k", 10, 8000), 1)
    assert estimate_tokens(seen[0]) > 8000 and seen[0][-1]["role"] == "user" and seen[0][0]["role"] == "system"


def test_a_small_window_is_not_run_never_failed():
    client = oracle_client(NEU)
    out = run_probe(NEU, client, Condition("M100", 100, 0), 1, context_window=4096)
    assert out.row["status"] == NOT_RUN_CONTEXT and out.row["success"] is None and out.result is None
    assert client.requests == []
    big = run_probe(NEU, oracle_client(NEU), Condition("M100", 100, 0), 1, context_window=200_000)
    assert big.row["status"] == "ok" and big.row["prompt_tokens_est"] > 4000
    small = run_probe(NEU, oracle_client(NEU), Condition("M5", 5, 0), 1, context_window=4096)
    assert small.row["prompt_tokens_est"] < big.row["prompt_tokens_est"]
    # the window can also come from the client
    class Windowed(ScriptedClient):
        def describe(self):
            return super().describe() | {"context_window": 2048}
    assert run_probe(NEU, Windowed([]), Condition("M25", 25, 0), 1).row["status"] == NOT_RUN_CONTEXT


def test_discovery_is_not_built():
    with pytest.raises(NotImplementedError, match="Discovery"):
        run_discovery(NEU, None)


# ------------------------------------------------------------------ metrics


def cell(case, trial, m, h, ok, **kw):
    return {"case_id": case, "trial": trial, "menu_size": m, "history_tokens": h, "success": ok,
            "status": "ok" if ok is not None else NOT_RUN_CONTEXT, **kw}


def test_robustness():
    rows = []
    for i in range(10):
        c = f"c{i}"
        rows.append(cell(c, 1, 10, 0, i < 8, fact_in_history=i >= 8))
        rows.append(cell(c, 1, 50, 0, i < 4, fact_in_history=i >= 8))
        rows.append(cell(c, 1, 10, 8000, i < 6, fact_in_history=i >= 8))
    r = robustness(rows)
    assert r["menu"]["n"] == 10 and abs(r["menu"]["ratio"] - 0.5) < 1e-9
    assert r["history"]["n"] == 0 or r["history"]["ratio"] is not None
    # history cells at H=0 are the M=10 cells: they pair with the H=8k ones
    assert abs(r["history"]["ratio"] - 6 / 8) < 1e-9
    assert r["history_fact"]["n"] == 2 and r["history_fact"]["ratio"] is None      # no H=0 successes to divide by


def test_robustness_leaves_not_run_cells_out():
    rows = []
    for i in range(6):
        rows.append(cell(f"c{i}", 1, 10, 0, True))
        rows.append(cell(f"c{i}", 1, 50, 0, i < 3 or None) if i < 3 else cell(f"c{i}", 1, 50, 0, None))
    r = robustness(rows)
    assert r["menu"]["n"] == 3 and r["menu"]["ratio"] == 1.0
    assert (50, 0) in r["not_run"]
    assert robustness([])["menu"]["ratio"] is None
