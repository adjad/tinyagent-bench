"""Prompt-pack builder tests (edat.perf.pack): the stub tokenizer only, no model, no network."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from edat.perf import pack as P
from edat.perf.constants import LEVELS, PROMPT_PACK_SCHEMA
from edat.perf.tokenizer import HFTokenizer, ModelTokenizer, StubTokenizer

FIX = Path(__file__).resolve().parent.parent / "test_fixtures" / "perf"
TEST_LEVELS = (512, 1024, 2048)
BUILT = "2026-11-18T00:00:00Z"


def _records() -> list[dict]:
    return [json.loads(line) for line in (FIX / "agent-corpus-synthetic.jsonl").read_text().splitlines() if line.strip()]


def _trajectory() -> dict:
    return json.loads((FIX / "agent-trajectory-synthetic.json").read_text())


def _build(tok=None, **kw):
    args = dict(model_id="stub-model", entry_id="stub-entry", thinking="on", levels=TEST_LEVELS,
                replay_variants=32, built_utc=BUILT)
    args.update(kw)
    return P.build_pack(_records(), _trajectory(), tok or StubTokenizer(), **args)


@pytest.fixture(scope="module")
def built() -> dict:
    return _build()


def _record(level: int, history_words: list[str], *, head: str = "Be brief.", lang: str = "en") -> dict:
    return {"schema": "edat-agent-corpus-v1", "level": level, "lang": lang, "generator_seed": 1,
            "messages": [{"role": "system", "content": head}, {"role": "user", "content": "hello there"},
                         {"role": "assistant", "content": " ".join(history_words)},
                         {"role": "user", "content": "what next"}],
            "tools": [], "trim": {"message_range": [2, -2], "unit": "whole_records_then_tokens"}}


# --------------------------------------------------------------------------- stub tokenizer

def test_stub_roundtrip_and_protocol():
    tok = StubTokenizer()
    assert isinstance(tok, ModelTokenizer)
    text = tok.apply_chat_template([{"role": "system", "content": "a b c"}, {"role": "user", "content": "d."}],
                                   thinking=True)
    ids = tok.encode(text)
    assert tok.encode(tok.decode(ids)) == ids
    assert tok.encode("<turn>") == tok.encode("<turn>") and len(tok.encode("<turn>")) == 1
    assert tok.apply_chat_template([{"role": "user", "content": "x"}], thinking=True) != \
        tok.apply_chat_template([{"role": "user", "content": "x"}], thinking=False)


def test_hf_adapter_needs_jinja_message(tmp_path):
    # no model files are read in tests; just the error path for a missing directory
    with pytest.raises(FileNotFoundError):
        HFTokenizer.from_model_dir(tmp_path)


# --------------------------------------------------------------------------- exactness

def test_every_variant_exact_under_formula_and_text(built):
    tok = StubTokenizer()
    assert [lv["level_tokens"] for lv in built["levels"]] == list(TEST_LEVELS)
    for lvl in built["levels"]:
        assert len(lvl["variants"]) == 16
        for i, v in enumerate(lvl["variants"]):
            ids, text = P.prompt(built, lvl["level_tokens"], i)
            assert len(ids) == lvl["level_tokens"]
            assert len(tok.encode(text)) == lvl["level_tokens"]
            assert tok.encode(text) == ids
            assert tok.decode(ids) == text
            assert v["roundtrip"] == "exact"
            n = lvl["level_tokens"] - len(lvl["prefix_ids"]) - len(v["nonce_ids"]) - len(lvl["suffix_ids"])
            assert ids == lvl["prefix_ids"] + v["nonce_ids"] + lvl["body_ids"][:n] + lvl["suffix_ids"]


def test_level_metadata(built):
    for lvl, tools in zip(built["levels"], (2, 4, 6)):
        assert lvl["tools_count"] == tools
        if lvl["level_tokens"] == 512:                  # trim null: no history, the system tail is cut
            assert lvl["history_messages_kept"] == lvl["history_messages_total"] == 0
            assert lvl["trim_target"] == "system_tail"
            continue
        assert lvl["trim_target"] == "history_tail"
        assert 0 < lvl["history_messages_kept"] < lvl["history_messages_total"]
        ct = lvl["content_tokens"]
        assert set(ct) == {"head", "menu", "history", "final", "other"}
        assert sum(ct.values()) == lvl["level_tokens"]
        assert ct["head"] > 0 and ct["menu"] > 0 and ct["final"] > 0
        assert (ct["history"] > 0) == (lvl["level_tokens"] != 512)
        assert lvl["text_chars"] == len(lvl["variants"][0]["text"])


def test_prompt_wraps_variants(built):
    assert P.prompt(built, 512, 16) == P.prompt(built, 512, 0)
    assert P.prompt(built, 512, 1) != P.prompt(built, 512, 0)
    with pytest.raises(P.PackError):
        P.prompt(built, 4096, 0)


# --------------------------------------------------------------------------- nonces

def test_nonces_distinct_same_length_at_start_of_system(built):
    seen: set[str] = set()
    for lvl in built["levels"]:
        for v in lvl["variants"]:
            assert v["nonce_text"] not in seen
            seen.add(v["nonce_text"])
            assert len(v["nonce_ids"]) == built["nonce_tokens"] == 8
            assert v["text"].startswith("<bos><turn>system " + v["nonce_text"])
    for rv in built["agent_replay"]["variants"]:
        assert rv["nonce_text"] not in seen
        seen.add(rv["nonce_text"])
        assert len(rv["nonce_ids"]) == 8
    assert len(seen) == 3 * 16 + 32


def test_history_trimmed_only_in_range_whole_records_first(built):
    recs = {r["level"]: r for r in _records()}
    for lvl in built["levels"]:
        if lvl["level_tokens"] == 512:
            continue
        rec = recs[lvl["level_tokens"]]
        msgs = rec["messages"]
        assert rec["trim"]["message_range"] == [1, -2]
        history = msgs[1:-1]                      # range [1, -2], end inclusive
        kept = lvl["history_messages_kept"]
        assert lvl["history_messages_total"] == len(history)
        norm = " ".join(lvl["variants"][0]["text"].split())

        def n(s: str) -> str:
            return " ".join(s.split())

        assert n(msgs[0]["content"]) in norm and n(msgs[-1]["content"]) in norm
        for m in history[:kept - 1]:
            assert n(m["content"] or "") in norm
        tail = history[kept - 1]["content"].split()
        assert tail, "the last kept message must have content"
        m_words = max(m for m in range(len(tail) + 1) if m == 0 or " ".join(tail[:m]) in norm)
        if lvl["tail_padded"]:                    # gap rule: the tail was extended with dropped text
            assert m_words == len(tail)
            continue
        for m in history[kept:]:
            if m["content"]:
                assert n(m["content"]) not in norm
        assert 1 <= m_words < len(tail)           # trimmed, and only inside the tail message


def test_missing_level_reported_not_invented():
    pack = _build(levels=(512, 4096, 2048), replay_variants=1)
    assert pack["missing_levels"] == [4096]
    assert pack["missing_reasons"] == [{"level": 4096, "reason": "absent"}]
    assert [lv["level_tokens"] for lv in pack["levels"]] == [512, 2048]
    pack = P.build_pack(_records(), None, StubTokenizer(), model_id="m", entry_id="e", thinking="off",
                        built_utc=BUILT)
    assert pack["missing_levels"] == [5120, 10240, 32768, 65536]
    assert "agent_replay" not in pack and pack["trajectory_sha256"] is None


def test_underfilled_level_is_reported_and_the_others_still_build():
    recs = _records() + [_record(2000, ["w%d" % i for i in range(50)])]
    pack = P.build_pack(recs, None, StubTokenizer(), model_id="m", entry_id="e", thinking="on",
                        levels=(512, 2000, 1024), n_variants=2, built_utc=BUILT)
    assert [lv["level_tokens"] for lv in pack["levels"]] == [512, 1024]
    assert pack["missing_levels"] == [2000]
    (reason,) = pack["missing_reasons"]
    assert reason["reason"] == "underfilled" and reason["level"] == 2000
    # the whole prompt of that record is the number of tokens it can reach; the shortfall is exact
    tok = StubTokenizer()
    msgs = _record(2000, ["w%d" % i for i in range(50)])["messages"]
    reach = len(tok.encode(tok.apply_chat_template(msgs, None, add_generation_prompt=True, thinking=True))) + 8
    assert reason["short_by_tokens"] == 2000 - reach
    assert str(reason["short_by_tokens"]) in reason["detail"]
    # a level whose fixed parts alone are over the target still raises
    with pytest.raises(P.PackError, match="fixed parts"):
        P.build_pack([_record(10, ["w%d" % i for i in range(50)])], None, StubTokenizer(), model_id="m",
                     entry_id="e", thinking="on", levels=(10,))


def test_trim_null_cuts_the_system_tail_after_the_nonce(built):
    rec = next(r for r in _records() if r["level"] == 512)
    assert rec["trim"] is None and len(rec["messages"]) == 2
    lvl = built["levels"][0]
    head = rec["messages"][0]["content"]
    for v in lvl["variants"]:
        words = v["text"].split()
        assert v["text"].startswith("<bos><turn>system " + v["nonce_text"])
        assert v["roundtrip"] == "exact"
        # the final user turn and the menu are whole; the head lost its end
        assert " ".join(rec["messages"][-1]["content"].split()) in " ".join(words)
    full = " ".join(head.split())
    text = " ".join(lvl["variants"][0]["text"].split())
    kept = max(m for m in range(len(head.split()) + 1) if m == 0 or " ".join(head.split()[:m]) in text)
    assert 0 < kept < len(head.split())
    assert full not in text and "current date and time" not in text      # the date line was in the cut tail


# --------------------------------------------------------------------------- adjusted path

def _quirk_outcomes() -> dict[int, dict | P.PackError]:
    # Words cycle dotted, dotted, plain, plain. With the merge quirk the re-encoded length of a cut is
    # n + c - 1 after a dotted word, n + c after a plain one: a cut after the first dotted word is fixed
    # by moving one token on, a cut after the second dotted word has no neighbour that reaches the level.
    words = [("alpha.", "beta.", "gamma", "delta")[i % 4] for i in range(400)]
    out: dict[int, dict | P.PackError] = {}
    for level in range(60, 68):
        try:
            out[level] = P.build_pack([_record(level, words)], None, StubTokenizer(merge_quirk=True), model_id="m",
                                      entry_id="e", thinking="on", levels=(level,), n_variants=4, built_utc=BUILT)
        except P.PackError as exc:
            out[level] = exc
    return out


def test_merge_quirk_exercises_adjustment():
    tok = StubTokenizer(merge_quirk=True)
    adjusted = exact = 0
    for level, pack in _quirk_outcomes().items():
        if isinstance(pack, P.PackError):
            continue
        for i, v in enumerate(pack["levels"][0]["variants"]):
            ids, text = P.prompt(pack, level, i)
            assert len(ids) == level
            assert tok.encode(text) == ids and len(tok.encode(text)) == level
            if v["roundtrip"] == "adjusted":
                adjusted += 1
                assert v["ids"] == ids                    # the variant carries its own full ids
            else:
                exact += 1
                assert v["roundtrip"] == "exact" and "ids" not in v
    assert adjusted > 0 and exact > 0


def test_adjustment_failure_is_loud():
    failures = [e for e in _quirk_outcomes().values() if isinstance(e, P.PackError)]
    assert failures and all("round-trips" in str(e) for e in failures)


# --------------------------------------------------------------------------- agent replay

def test_replay_prefix_exact_steps_grow_nonce_scoping(built):
    tok = StubTokenizer()
    replay = built["agent_replay"]
    target = replay["prefix_tokens"]
    assert target == 800 and len(replay["variants"]) == 32 and replay["tools_count"] == 6
    for v in replay["variants"]:
        steps = v["steps"]
        assert [s["step"] for s in steps] == [1, 2, 3, 4, 5, 6]
        sizes = [s["prompt_tokens"] for s in steps]
        assert sizes == sorted(set(sizes))                       # strictly growing
        assert all(len(s["ids"]) == s["prompt_tokens"] for s in steps)
        first = steps[0]["ids"][:target]
        assert len(first) == target
        for s in steps:
            assert s["ids"][:target] == first                   # shared prefix, so a cache can hit
            assert v["nonce_text"] in s["text"]                 # same nonce in every step
            assert s["roundtrip"] == "exact" and tok.encode(s["text"]) == s["ids"]
        assert len(tok.encode(tok.decode(first))) == target
    prefixes = {tuple(v["steps"][0]["ids"][:target]) for v in replay["variants"]}
    assert len(prefixes) == 32
    texts = [v["steps"][0]["text"] for v in replay["variants"]]
    for i, v in enumerate(replay["variants"]):
        for j, t in enumerate(texts):
            if i != j:
                assert v["nonce_text"] not in t                 # variants never share a nonce
    # Swift-compatible steps are variant 0
    assert [s["ids"] for s in replay["steps"]] == [s["ids"] for s in replay["variants"][0]["steps"]]
    assert P.replay_step(built, 0, 3) == (replay["variants"][0]["steps"][2]["ids"], replay["variants"][0]["steps"][2]["text"])
    assert P.replay_step(built, 32, 1) == P.replay_step(built, 0, 1)
    with pytest.raises(P.PackError):
        P.replay_step(built, 0, 7)


def test_replay_follows_the_export_format(built):
    traj = _trajectory()
    assert set(traj["prefix"]) >= {"messages", "tools", "trim"} and "messages" in traj["steps"][0]
    assert all(st["tools"] == traj["steps"][0]["tools"] for st in traj["steps"])
    v = built["agent_replay"]["variants"][0]
    # step k+1's input holds step k's canned assistant call and tool result (they are step k+1's messages)
    for k in range(1, 6):
        canned = traj["steps"][k - 1]["canned_assistant"]["tool_calls"][0]["id"]
        later = " ".join(v["steps"][k]["text"].split())
        earlier = " ".join(v["steps"][k - 1]["text"].split())
        assert canned in later and canned not in earlier
    # the request arrives with step 1 only, after the prefix
    request = " ".join(traj["task"].split())
    assert request in " ".join(v["steps"][0]["text"].split())
    assert request not in " ".join(v["steps"][0]["text"].split()[:built["agent_replay"]["prefix_tokens"] - 5])
    # the menu is filtered to the names in step.tools, in prefix.tools order
    bad = copy.deepcopy(traj)
    bad["steps"][0]["tools"] = bad["steps"][0]["tools"] + ["not_a_tool"]
    for st in bad["steps"][1:]:
        st["tools"] = bad["steps"][0]["tools"]
    with pytest.raises(P.PackError, match="does not define"):
        P.build_pack(_records(), bad, StubTokenizer(), model_id="m", entry_id="e", thinking="on", levels=(512,))


def test_replay_prefix_override_and_trim_inside_earlier_exchange():
    pack = _build(levels=(512,), replay_variants=2, replay_prefix_tokens=700)
    assert pack["agent_replay"]["prefix_tokens"] == 700
    v = pack["agent_replay"]["variants"][0]
    assert len(StubTokenizer().encode(v["steps"][0]["text"])) == v["steps"][0]["prompt_tokens"]
    assert v["steps"][0]["ids"][:700] == v["steps"][5]["ids"][:700]
    with pytest.raises(P.PackError, match="too short"):
        _build(levels=(512,), replay_variants=1, replay_prefix_tokens=40000)
    with pytest.raises(P.PackError, match="fixed parts"):
        _build(levels=(512,), replay_variants=1, replay_prefix_tokens=300)


# --------------------------------------------------------------------------- first token

def test_first_token_is_eight_tokens_when_possible(built):
    ft = built["first_token"]
    tok = StubTokenizer()
    assert ft["exact"] is True and ft["prompt_tokens"] == 8 and len(ft["ids"]) == 8
    assert tok.encode(ft["text"]) == ft["ids"]
    assert ft["text"].startswith("<bos><turn>user")


def test_first_token_inexact_when_template_overhead_is_too_big():
    class Heavy(StubTokenizer):
        def apply_chat_template(self, messages, tools=None, *, add_generation_prompt=True, thinking):
            return "<turn> " * 12 + super().apply_chat_template(messages, tools, add_generation_prompt=add_generation_prompt, thinking=thinking)
    ft = P._build_first_token(Heavy(), True)
    assert ft["exact"] is False and ft["prompt_tokens"] > 8
    assert ft["prompt_tokens"] == len(ft["ids"])


# --------------------------------------------------------------------------- hash and load

def test_pack_sha256_stable_and_verified_by_load(built, tmp_path):
    pack = copy.deepcopy(built)
    assert "pack_sha256" not in pack
    h1 = P.compute_pack_sha256(pack)
    assert h1 == P.compute_pack_sha256(copy.deepcopy(pack))
    path = tmp_path / "pack.json"
    assert P.write_pack(pack, path) == h1 == pack["pack_sha256"]
    loaded = P.load_pack(path, StubTokenizer())
    assert loaded["schema"] == PROMPT_PACK_SCHEMA and loaded["language"] == "en"
    assert loaded["pack_sha256"] == h1
    assert P.prompt(loaded, 1024, 5) == P.prompt(built, 1024, 5)
    assert P.replay_step(loaded, 3, 2) == P.replay_step(built, 3, 2)
    # tampering is caught by the hash
    doc = json.loads(path.read_text())
    doc["levels"][0]["variants"][0]["text"] += " x"
    (tmp_path / "bad.json").write_text(json.dumps(doc))
    with pytest.raises(P.PackError, match="pack_sha256"):
        P.load_pack(tmp_path / "bad.json")
    # a recomputed hash over a wrong-length variant is caught by the length check
    doc = json.loads(path.read_text())
    doc["levels"][0]["body_ids"] = doc["levels"][0]["body_ids"][:5]
    doc["pack_sha256"] = P.compute_pack_sha256(doc)
    (tmp_path / "short.json").write_text(json.dumps(doc))
    with pytest.raises(P.PackError, match="too short"):
        P.load_pack(tmp_path / "short.json")
    # wrong tokenizer
    with pytest.raises(P.PackError, match="different tokenizer"):
        P.load_pack(path, StubTokenizer(merge_quirk=True))


def test_load_refuses_wrong_schema_and_language(built, tmp_path):
    for key, val, msg in (("schema", "other/1", "not a prompt pack"), ("language", "fr", "English only")):
        doc = copy.deepcopy(built)
        doc[key] = val
        doc["pack_sha256"] = P.compute_pack_sha256(doc)
        (tmp_path / f"{key}.json").write_text(json.dumps(doc))
        with pytest.raises(P.PackError, match=msg):
            P.load_pack(tmp_path / f"{key}.json")


def test_top_level_fields(built):
    assert built["schema"] == PROMPT_PACK_SCHEMA and built["version"] == "0.1" and built["language"] == "en"
    assert built["thinking"] == "on" and built["model_id"] == "stub-model" and built["entry_id"] == "stub-entry"
    assert built["corpus"] == "synthetic-test-v0"
    assert built["generator_seed"] == P.DEFAULT_SEED
    assert built["tokenizer_sha256"] == StubTokenizer().tokenizer_sha256
    assert built["template_sha256"] == StubTokenizer().template_sha256
    assert len(built["corpus_sha256"]) == 64 and len(built["trajectory_sha256"]) == 64
    assert built["builder"]["name"] == "edat.perf.pack" and built["built_utc"] == BUILT
    assert built["missing_levels"] == []
    off = _build(thinking="off", levels=(512,), replay_variants=1)
    assert off["thinking"] == "off" and off["template_sha256"] == built["template_sha256"]
    assert off["levels"][0]["suffix_ids"] != built["levels"][0]["suffix_ids"]   # generation prompt differs
    with pytest.raises(P.PackError):
        _build(thinking="maybe")


# --------------------------------------------------------------------------- tokens per 1k chars

def test_tokens_per_1k_chars_hand_made():
    rec = {"schema": "edat-agent-corpus-v1", "level": 1, "lang": "en",
           "messages": [{"role": "system", "content": "ignored head words"},
                        {"role": "user", "content": "alpha beta gamma"},
                        {"role": "assistant", "content": "delta epsilon"},
                        {"role": "assistant", "content": "delta epsilon"},   # duplicate: counted once
                        {"role": "user", "content": "final request words"}],
           "tools": [], "trim": {"message_range": [1, -2], "unit": "whole_records_then_tokens"}}
    out = P.tokens_per_1k_chars([rec], StubTokenizer())
    assert (out["tokens"], out["chars"], out["messages"]) == (5, 29, 2)
    assert out["value"] == pytest.approx(1000 * 5 / 29, abs=1e-4)


# --------------------------------------------------------------------------- refusals, determinism, CLI

def test_non_english_corpus_refused():
    recs = _records()
    recs[0]["lang"] = "de"
    with pytest.raises(P.PackError, match="English only"):
        P.build_pack(recs, None, StubTokenizer(), model_id="m", entry_id="e", thinking="on", levels=TEST_LEVELS)
    traj = _trajectory()
    traj["lang"] = "fr"
    with pytest.raises(P.PackError, match="English only"):
        P.build_pack(_records(), traj, StubTokenizer(), model_id="m", entry_id="e", thinking="on", levels=(512,))


def test_bad_corpus_records_refused():
    recs = _records()
    recs[0]["schema"] = "something-else"
    with pytest.raises(P.PackError, match="schema"):
        P.build_pack(recs, None, StubTokenizer(), model_id="m", entry_id="e", thinking="on")
    recs = _records()
    recs.append(copy.deepcopy(recs[0]))
    with pytest.raises(P.PackError, match="two records"):
        P.build_pack(recs, None, StubTokenizer(), model_id="m", entry_id="e", thinking="on")


def test_deterministic_for_fixed_seed():
    a = _build(levels=(512, 1024), replay_variants=3)
    b = _build(levels=(512, 1024), replay_variants=3)
    assert a == b and P.compute_pack_sha256(a) == P.compute_pack_sha256(b)
    c = _build(levels=(512, 1024), replay_variants=3, seed=P.DEFAULT_SEED + 1)
    assert c["levels"][0]["variants"][0]["nonce_text"] != a["levels"][0]["variants"][0]["nonce_text"]
    assert c["levels"][0]["prefix_ids"] == a["levels"][0]["prefix_ids"]


def test_cli_writes_a_loadable_pack(tmp_path, capsys):
    out = tmp_path / "pack.json"
    rc = P.main(["--corpus", str(FIX / "agent-corpus-synthetic.jsonl"),
                 "--trajectory", str(FIX / "agent-trajectory-synthetic.json"), "--stub-tokenizer",
                 "--model-id", "stub", "--entry-id", "stub-on", "--thinking", "on", "--out", str(out),
                 "--levels", "512,5120", "--replay-variants", "2"])
    assert rc == 0
    pack = P.load_pack(out, StubTokenizer())
    assert pack["missing_levels"] == [5120] and pack["model_id"] == "stub"
    assert len(pack["agent_replay"]["variants"]) == 2
    assert len(pack["corpus_sha256"]) == 64
    assert "missing level 5120: absent" in capsys.readouterr().err


def test_seven_spec_levels_constant():
    assert LEVELS == (512, 1024, 2048, 5120, 10240, 32768, 65536)
