"""Long sessions: the stand-in trimming policy, the per-turn driver, and the §3.12 derivation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edat.perf.longsession import (DIGEST_TEXT, TrimmingConversation, derive_long_session, load_conversation,
                                   run_long_session, turn_energies)
from edat.perf.power import PowerTrace
from edat.perf.tokenizer import StubTokenizer
from tests.test_edat_perf_sustained import NS, events, power_rig, windows

FIXTURE = Path(__file__).resolve().parents[1] / "test_fixtures" / "perf" / "long-session-synthetic.json"


def conv_doc() -> dict:
    return json.loads(FIXTURE.read_text())


def stub_count(text: str) -> list[str]:
    return [str(i) for i in StubTokenizer().encode(text)]


def test_trimming_policy_shortens_tool_results_then_condenses():
    tok = StubTokenizer()
    c = TrimmingConversation(conv_doc(), tok)
    assert c.window == 1024 and c.target == 768
    trims = []
    for i in range(len(c.turns)):
        tp = c.prepare(i)
        assert tp.fits                                           # prompt + 256 always within the window
        if tp.trim_event:
            trims.append((i, tp))
            assert tp.trim_event["prefix_changed"] is True
            assert tp.tokens + 256 <= c.target                   # trimmed down to 75 % of the window
            assert tp.trim_event["tokens_removed"] == (tp.trim_event["tokens_before"] - tp.tokens
                                                       + tp.trim_event["digest_tokens_added"])
        elif i > 0:
            assert tp.prefix_changed is False                    # an untrimmed turn extends the prefix
        c.commit(i)
    assert len(trims) >= 10
    first = trims[0][1].trim_event
    assert first["ops"] == {"shorten_tool_result": first["ops"]["shorten_tool_result"]}   # tool results first
    assert first["digest_tokens_added"] == 0
    digests = [tp.trim_event["digest_tokens_added"] for _, tp in trims if tp.trim_event["digest_tokens_added"]]
    assert len(digests) == 1                                     # the fixed digest is added once
    assert digests[0] == len(DIGEST_TEXT.split()) + 0            # stub: one token per word ("\n\n" is whitespace)
    assert DIGEST_TEXT in trims[-1][1].messages[0]["content"]
    assert c.system_prefix_tokens < trims[-1][1].tokens


def test_load_conversation_checks():
    d = conv_doc()
    d["lang"] = "fr"
    with pytest.raises(ValueError):
        load_conversation(d)
    with pytest.raises(ValueError):
        load_conversation({"schema": "edat-long-session-v1", "lang": "en", "turns": []})


def test_run_long_session_samples_windows_and_idle_groups(tmp_path):
    r, eng, clock, _, w, _ = power_rig(tmp_path, prefix_cache=True, tokenize=stub_count)
    e = r.entries["M"]
    e.thinking = "off"
    out = run_long_session(r, e, "as_shipped", conv_doc(), StubTokenizer(), max_turns=12, min_trims=10)
    ss = out["samples"]
    assert [s["turn_index"] for s in ss] == list(range(12)) and {s["test"] for s in ss} == {"long_session_turn"}
    assert all(s["status"] == "ok" for s in ss)
    assert all(s["context_tokens"] == s["prompt_tokens"] for s in ss)
    assert all(s["new_prefill_tokens"] == s["prompt_tokens"] - s["cached_tokens"] for s in ss)
    trim_turns = [s["turn_index"] for s in ss if s["trim_event"]]
    assert trim_turns == [6, 8, 11]
    # condensing turns rewrites the system message: the cache falls back to the fixed prefix (a reset);
    # shortening old tool results alone keeps the cache up to the first shortened result (no reset)
    assert [(s["turn_index"], s["cache_reset"]) for s in ss if s["trim_event"]] == [(6, False), (8, True), (11, True)]
    assert ss[6]["cached_tokens"] > ss[6]["system_prefix_tokens"]
    assert all(s["cached_tokens"] <= s["system_prefix_tokens"] for s in (ss[8], ss[11]))
    assert all(s["cache_reset"] is False for s in ss[1:] if not s["trim_event"])
    assert ss[0]["cache_reset"] is None
    assert out["trims"] == 3 and out["short_of_min_trims"] is True
    assert any(x["kind"] == "operator_note" and "fewer than 10" in x["detail"] for x in events(w))
    assert [x["detail"]["turn_index"] for x in events(w) if x["kind"] == "trim"] == [6, 8, 11]
    # one window per turn over [t_submit, t_end]; idle measured once per 5 turns
    ws = [x for x in windows(w) if x.get("purpose") == "long_session"]
    assert len(ws) == 12 and [x["sample_ids"] for x in ws] == [[s["sample_id"]] for s in ss]
    assert all(x["t_start_ns"] == s["t_submit_ns"] and x["t_end_ns"] == s["t_end_ns"] for x, s in zip(ws, ss))
    groups = [tuple(map(tuple, (x["idle_ref"]["before"], x["idle_ref"]["after"]))) for x in ws]
    assert len(set(groups[0:5])) == 1 and len(set(groups[5:10])) == 1 and len(set(groups[10:12])) == 1
    b, a = ws[0]["idle_ref"]["before"], ws[4]["idle_ref"]["after"]
    assert b[1] - b[0] == 60 * NS and a[1] - a[0] == 60 * NS
    assert b[1] <= ss[0]["t_submit_ns"] and a[0] >= ss[4]["t_end_ns"]
    # the text sent is the chat template of the turn's messages
    assert eng.requests[0]["body"]["prompt"].startswith("<bos><turn>system\n")
    assert eng.requests[0]["body"]["prompt"].endswith("<turn>model\n<nothink>\n")
    rec = r.conditions_extra["long_sessions"][0]
    assert rec["policy"].startswith("stand-in") and rec["trims"] == 3


def _s(k, ttft, fp, trim=False, ctx=None):
    t0 = 1000 * NS + k * 100 * NS
    return {"sample_id": f"s{k}", "test": "long_session_turn", "turn_index": k, "t_submit_ns": t0,
            "t_first_token_ns": t0 + int(ttft * NS), "t_last_token_ns": t0 + int(ttft * NS) + 5 * NS,
            "output_tokens": 251, "context_tokens": ctx or 100 * (k + 1), "footprint_steady_bytes": fp,
            "trim_event": {"tokens_removed": 300, "digest_tokens_added": 0, "prefix_changed": True} if trim else None}


def test_derive_long_session_hand_numbers():
    ttft = [1.0, 1.2, 1.4, 1.6, 3.0, 1.1, 1.3, 2.5]
    fps = [100, 110, 120, 130, 140, 140, 141, 146]
    ss = [_s(k, t, f, trim=k in (4, 7)) for k, (t, f) in enumerate(zip(ttft, fps))]
    en = {f"s{k}": v for k, v in enumerate([10, 11, 12, 13, 30, 11, 12, 25])}
    out = derive_long_session(ss, en, window_tokens=1024)
    # trim at 4: 3.0 - median(1.2, 1.4, 1.6) = 1.6; trim at 7: 2.5 - median(3.0, 1.1, 1.3) = 1.2 -> median 1.4
    assert out["trim_ttft_cost_s"] == pytest.approx(1.4)
    # energy: 30 - median(11, 12, 13) = 18; 25 - median(30, 11, 12) = 13 -> median 15.5
    assert out["trim_energy_cost_J"] == pytest.approx(15.5)
    # plateau: median of turns 4..7 = median(140, 140, 141, 146) = 140.5; 146 <= 1.05 x 140 = 147 -> no leak
    assert out["plateau_footprint_bytes"] == pytest.approx(140.5)
    assert out["leak_flag"] is False and out["leak_growth_frac"] == pytest.approx(6 / 140)
    assert (out["turns"], out["trims"], out["window_tokens"]) == (8, 2, 1024)
    assert out["ttft_by_context"][0] == [100, pytest.approx(1.0)]
    assert out["decode_by_context"][0] == [100, pytest.approx(250 / 5)]       # (251 - 1) tokens / 5 s
    ss[7]["footprint_steady_bytes"] = 148                                     # > 147
    assert derive_long_session(ss, en)["leak_flag"] is True
    one = derive_long_session([_s(k, 1.0, 100, trim=k == 4) for k in range(6)])
    assert one["leak_flag"] is None and one["leak_reason"] == "fewer_than_two_trims"
    assert one["trim_energy_cost_J"] is None and one["trim_energy_cost_reason"] == "no_turn_energies"


def test_turn_energies_uses_group_idle():
    # idle 4 W in both spans; the turn at 10 W for 2 s -> total 20 J, active 20 - 4 x 2 = 12 J
    ts = [i * NS // 10 for i in range(0, 201)]
    ws = [10.0 if 100 * NS // 10 <= t <= 120 * NS // 10 else 4.0 for t in ts]
    tr = PowerTrace("smc_pstr", "whole_device_internal", 10.0, False, ts, ws)
    win = {"purpose": "long_session", "sample_ids": ["s1"], "t_start_ns": 10 * NS, "t_end_ns": 12 * NS,
           "idle_ref": {"before": [0, 5 * NS], "after": [15 * NS, 20 * NS]}}
    assert turn_energies(tr, [win])["s1"] == pytest.approx(12.0)
