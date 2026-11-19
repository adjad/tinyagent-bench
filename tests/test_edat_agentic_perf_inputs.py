"""The performance-spec inputs (02-agentic-spec.md section 11): levels, trajectory, simulated day."""
from __future__ import annotations

import json
import re
from functools import lru_cache

import pytest

from edat.cases import perf_inputs as P
from edat.state.assistant_lane import AssistantEnvironment
from edat.state.clock import Clock

CANONICAL = ["Mom", "Dad", "Sam Rivera", "Alex Kim", "Priya Shah", "Dr. Patel", "Lakeside Auto", "Chase",
             "Nopa", "Oakland"]


@lru_cache(maxsize=1)
def corpus():
    return P.build_corpus()


@lru_cache(maxsize=1)
def trajectory():
    return P.build_trajectory()


def _fake_cases():
    cases = []
    for fam, n in P.TASK_MIX:
        for i in range(n + 2):
            cases.append({"id": f"{fam}_{i:03d}", "lane": "assistant", "family": fam, "tags": ["core"] if i <= n else []})
    return cases


def _non_system_text(rec):
    out = []
    for m in rec["messages"][1:]:
        out.append(m.get("content") or "")
        out.append(json.dumps(m.get("tool_calls") or ""))
    return "\n".join(out)


def test_levels_present_and_increasing():
    levels = [r["level"] for r in corpus()]
    assert levels == list(P.LEVELS)
    assert levels == sorted(set(levels))


def test_estimates_within_five_percent_below_each_level():
    for r in corpus():
        est = P.estimate_tokens(r["messages"], r["tools"])
        assert est == r["estimate"]["tokens"]
        assert est <= r["level"], (r["level"], est)
        assert est >= 0.95 * r["level"], (r["level"], est)


def test_menus_and_shape():
    menus = {r["level"]: len(r["tools"]) for r in corpus()}
    assert menus == {512: 2, 1024: 4, 2048: 6, 5120: 12, 10240: 12, 32768: 12, 65536: 12}
    env = AssistantEnvironment(Clock.parse())
    real = {t["function"]["name"] for t in env.schemas()}
    for r in corpus():
        assert {t["function"]["name"] for t in r["tools"]} <= real
        assert r["messages"][0]["role"] == "system" and r["messages"][-1]["role"] == "user"
        assert r["schema"] == "edat-agent-corpus-v1" and r["generator_seed"] == P.GENERATOR_SEED
        if r["level"] == 512:
            assert r["trim"] is None
        else:
            lo, hi = r["trim"]["message_range"]
            assert (lo, hi) == (1, -2) and r["trim"]["unit"] == "whole_records_then_tokens"
            assert r["trim"]["records"] * 4 == len(r["messages"]) - 2
    # the biggest levels use the assistant lane's full prompt for the menu
    full = AssistantEnvironment(Clock.parse()).system_prompt(offered=P.MENU_12)
    assert corpus()[-1]["messages"][0]["content"] == full


def test_history_calls_use_menu_tools_and_ids_match():
    for r in corpus():
        names = {t["function"]["name"] for t in r["tools"]}
        pending = {}
        for m in r["messages"]:
            for tc in m.get("tool_calls") or []:
                assert tc["function"]["name"] in names
                json.loads(tc["function"]["arguments"])
                pending[tc["id"]] = True
            if m["role"] == "tool":
                assert pending.pop(m["tool_call_id"])
        assert not pending


def test_no_paragraph_repeats_inside_a_level():
    for r in corpus():
        seen = {}
        for i, m in enumerate(r["messages"][1:], start=1):
            for ln in (m.get("content") or "").split("\n"):
                ln = ln.strip()
                if len(ln) >= 40 and not ln.startswith("<<<"):  # the tool's fixed untrusted-body wrapper tags
                    assert ln not in seen, (r["level"], ln)
                    seen[ln] = i
        ids = r["estimate"]["history_records"]
        assert len(ids) == len(set(ids))


def test_pool_is_about_two_thousand_distinct_records():
    pool = P.build_pool()
    assert 1900 <= len(pool) <= 2100
    assert len({r["id"] for r in pool}) == len(pool)
    longs = [ln for r in pool for ln in P._lines_long(r["user"], r["result"], r["summary"]) if not ln.startswith("<<<")]
    assert len(longs) == len(set(longs))


def test_deterministic_for_a_fixed_seed():
    a = json.dumps(P.build_corpus(41), sort_keys=True)
    assert a == json.dumps(P.build_corpus(41), sort_keys=True)
    assert a != json.dumps(P.build_corpus(42), sort_keys=True)
    assert json.dumps(P.build_trajectory(41), sort_keys=True) == json.dumps(P.build_trajectory(41), sort_keys=True)
    cases = _fake_cases()
    assert P.build_sim_day(cases, 41) == P.build_sim_day(cases, 41)
    assert P.build_sim_day(cases, 41) != P.build_sim_day(cases, 42)


def test_trajectory_is_a_valid_assistant_lane_run():
    t = trajectory()
    env = AssistantEnvironment(Clock.parse())
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in env.schemas()}
    assert t["prefix"]["estimated_tokens"] <= P.PREFIX_TOKENS
    assert t["prefix"]["estimated_tokens"] >= P.PREFIX_TOKENS - 8
    assert {s["function"]["name"] for s in t["prefix"]["tools"]} == set(P.TRAJECTORY_TOOLS)
    assert [s["step"] for s in t["steps"]] == [1, 2, 3, 4, 5, 6]
    expect = [["get_upcoming", "view_emails"], ["add_reminder"], ["get_weather"], ["draft_email"],
              ["search_reminders"], []]
    for step, names in zip(t["steps"], expect):
        calls = step["canned_assistant"].get("tool_calls") or []
        assert [c["function"]["name"] for c in calls] == names
        for c in calls:
            params = schemas[c["function"]["name"]]
            args = json.loads(c["function"]["arguments"])
            assert set(args) <= set(params["properties"]), (c["function"]["name"], args)
            assert set(params.get("required", [])) <= set(args)
            for k, v in args.items():
                enum = params["properties"][k].get("enum")
                if enum:
                    assert v in enum
        assert [r["tool_call_id"] for r in step["canned_tool_results"]] == [c["id"] for c in calls]
    assert t["steps"][-1]["canned_assistant"]["content"] and not t["steps"][-1]["canned_tool_results"]
    args2 = json.loads(t["steps"][1]["canned_assistant"]["tool_calls"][0]["function"]["arguments"])
    assert args2["when_iso"] < "2026-11-18T14:00" and args2["when_iso"] > "2026-11-18T08:30"
    args3 = json.loads(t["steps"][2]["canned_assistant"]["tool_calls"][0]["function"]["arguments"])
    assert args3["location"] == "Porto" and "tomorrow" in args3["period"]
    args4 = json.loads(t["steps"][3]["canned_assistant"]["tool_calls"][0]["function"]["arguments"])
    assert args4["to"].startswith("lena") and "reminder" in args4["body"] and "forecast" in args4["body"]
    # input grows from about 4k at step 1 to about 5.3k at step 6, and step k+1 carries step k's output
    est = [s["estimated_input_tokens"] for s in t["steps"]]
    assert est == sorted(est) and 4096 <= est[0] < 4300 and 5000 <= est[-1] <= 5700
    assert t["steps"][1]["messages"][0] == t["steps"][0]["canned_assistant"]
    assert t["steps"][0]["messages"] == [{"role": "user", "content": P.TASK_TEXT}]


def test_trajectory_result_sizes_roughly_match_the_table():
    t = trajectory()
    sizes = [s["estimated_result_tokens"] for s in t["steps"]]
    assert 600 <= sizes[0] <= 1000          # calendar (~300) + email (~500)
    assert 10 <= sizes[1] <= 40             # reminder set (~20)
    assert 80 <= sizes[2] <= 160            # forecast (~120)
    assert 30 <= sizes[3] <= 90             # draft receipt (~60)
    assert 30 <= sizes[4] <= 90             # one matching reminder (~60)


def test_sim_day_has_47_jobs_at_the_right_times():
    day = P.build_sim_day(_fake_cases(), 41)
    ev = day["events"]
    assert len(ev) == 47 and day["totals"]["jobs"] == 47
    kinds = [e["kind"] for e in ev]
    assert {k: kinds.count(k) for k in set(kinds)} == {"brief": 1, "task": 32, "mail_digest": 9, "message_digest": 4, "review": 1}
    ts = [e["t"] for e in ev]
    assert ts == sorted(ts) and all(re.fullmatch(r"\d\d:\d\d:\d\d", t) for t in ts)
    at = lambda k: [e["t"] for e in ev if e["kind"] == k]
    assert at("brief") == ["08:00:00"] and at("review") == ["17:30:00"]
    assert at("mail_digest") == [f"{h:02d}:00:00" for h in range(9, 18)]
    assert at("message_digest") == ["10:00:00", "12:00:00", "14:00:00", "16:00:00"]
    tasks = [e for e in ev if e["kind"] == "task"]
    secs = lambda t: int(t[:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:])
    assert all("08:15" <= e["t"][:5] < "17:00" for e in tasks)
    assert not any("12:00" <= e["t"][:5] < "13:00" for e in tasks)  # lunch
    by_cluster = {}
    for e in tasks:
        by_cluster.setdefault(e["cluster"], []).append(secs(e["t"]))
    assert sorted(len(v) for v in by_cluster.values()) == [3, 3, 3, 3, 3, 3, 3, 3, 4, 4]
    assert all(max(v) - min(v) <= 300 for v in by_cluster.values())
    morning = [e for e in tasks if e["t"] < "09:00:00"]
    assert len(morning) == 4
    assert len([e for e in tasks if "09:00" <= e["t"][:5] < "12:00"]) == 12
    assert len([e for e in tasks if e["t"] >= "13:00:00"]) == 16
    mix = {}
    for e in tasks:
        mix[e["family"]] = mix.get(e["family"], 0) + 1
    assert mix == dict(P.TASK_MIX)
    assert day["mode"] == "forced" and day["compressed"]["idle_scale"] == 0.2 and day["missing"] == []
    for e in ev:
        if e["kind"] == "mail_digest":
            assert 2 <= len(e["world_events_before"]) <= 5
            assert all(a["type"] == "email" for a in e["world_events_before"])
        if e["kind"] == "message_digest":
            assert 2 <= len(e["world_events_before"]) <= 4
    ids = [e["ref"] for e in tasks]
    assert len(ids) == len(set(ids)) and all(isinstance(i, str) for i in ids)


def test_sim_day_prefers_core_and_reports_missing():
    cases = _fake_cases()
    day = P.build_sim_day(cases, 41)
    core = {c["id"] for c in cases if "core" in c["tags"]}
    assert all(e["ref"] in core for e in day["events"] if e["kind"] == "task")
    # a family with too few cases: null refs and a `missing` entry
    few = [c for c in cases if not (c["family"] == "recovery" and not c["id"].endswith("000"))]
    day = P.build_sim_day(few, 41)
    assert day["missing"] == [{"family": "recovery", "wanted": 2, "found": 1}]
    assert len(day["events"]) == 47
    assert sum(1 for e in day["events"] if e["kind"] == "task" and e["ref"] is None) == 1
    none = P.build_sim_day([], 41)
    assert len(none["missing"]) == 7 and all(e["ref"] is None for e in none["events"] if e["kind"] == "task")


def test_duty_cycle_has_20_bursts():
    d = P.build_duty_cycle()
    assert [b["burst"] for b in d["bursts"]] == list(range(1, 21))
    assert [b["t"] for b in d["bursts"]][:3] == ["00:00:00", "00:03:00", "00:06:00"]
    assert [b["nonce_variant"] for b in d["bursts"]][:16] == list(range(16))
    assert [b["nonce_variant"] for b in d["bursts"]][16:] == [0, 1, 2, 3]
    assert all(b["nonce_reused"] for b in d["bursts"][16:]) and not any(b["nonce_reused"] for b in d["bursts"][:16])


def test_everything_json_serialisable_and_clean(tmp_path):
    sizes = P.write_all(tmp_path)
    assert set(sizes) == {"edat-agent-corpus-v1.jsonl", "edat-agent-trajectory-v1.json", "edat-sim-day-v1.json",
                          "edat-duty-cycle-v1.json"}
    for name in sizes:
        text = (tmp_path / name).read_text()
        if name.endswith(".jsonl"):
            rows = [json.loads(ln) for ln in text.splitlines()]
            assert len(rows) == 7
        else:
            json.loads(text)
        assert "/Users/" not in text and "/private/" not in text
    # generated (non-system) text carries no canonical-world names; the system prompt is the real lane prompt
    for r in corpus():
        txt = _non_system_text(r)
        for name in CANONICAL:
            assert not re.search(rf"\b{re.escape(name)}\b", txt), (r["level"], name)
    t = json.dumps([trajectory()["steps"], trajectory()["prefix"]["messages"][1:]])
    for name in CANONICAL:
        assert not re.search(rf"\b{re.escape(name)}\b", t), name
    day = json.dumps(P.build_sim_day(_fake_cases(), 41))
    for name in CANONICAL:
        assert not re.search(rf"\b{re.escape(name)}\b", day), name


def test_cli_writes_files(tmp_path, capsys):
    assert P.main(["--out", str(tmp_path / "x")]) == 0
    out = capsys.readouterr().out
    assert "edat-agent-corpus-v1.jsonl" in out and (tmp_path / "x" / "edat-sim-day-v1.json").exists()
