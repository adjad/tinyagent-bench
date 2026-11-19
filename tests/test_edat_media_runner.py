"""The media runner with stub models: trials, missing modalities, engine faults, the record envelope."""
from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from edat.media.adapter import (ADAPTER_GAPS, StubMediaClient, capabilities_for, image_part)
from edat.media.audio import commands as cmd
from edat.media.audio.tts import StubEngine
from edat.media.audio.wav import Clip
from edat.media.runner import (JsonlSink, RunConfig, Utterance, run_spoken_commands,
                               run_transcription, run_vision)
from edat.media.vision.generate import build
from edat.media import workloads as wl

REQUIRED = ("schema", "kind", "tier", "run_id", "board_version", "lang", "model", "runtime",
            "settings", "harness_commit", "started_at", "case_id", "split", "trial", "status",
            "facets", "metrics", "safety")


def cfg(**kw) -> RunConfig:
    base = dict(model={"id": "stub-media", "role": "candidate"},
                runtime={"name": "stub", "version": "0"}, thinking=False,
                board_version="2027.1", harness_commit="abc1234", run_id="2027-01-01T00-00-00Z-0000",
                device={"id": "mac-ref", "class": "mac-ref", "ram_bytes": 1, "os": "test"})
    base.update(kw)
    return RunConfig(**base)


def check_envelope(rec):
    for k in REQUIRED:
        assert k in rec, k
    assert rec["schema"] == "edat.result/1.0" and rec["lang"] == "en"
    assert rec["settings"]["thinking"] in ("on", "off")
    assert (rec["raw"] is None) == ("raw_reason" in rec)
    if rec["status"] == "ok":
        assert "pass" in rec["metrics"] or "score" in rec["metrics"]


@pytest.fixture(scope="module")
def items():
    out = []
    for fam, i in (("documents", 0), ("documents", 9)):
        meta, png, _ = build(fam, i, split="dev")
        out.append((meta, png))
    return out


def gold_answerer(items):
    """Answers every question with its gold answer, finding the item by the image it was sent."""
    by_image = {image_part(png)["image_url"]["url"]: meta for meta, png in items}

    def answer(messages, tools, index):
        image, text = messages[-1]["content"]
        meta = by_image[image["image_url"]["url"]]
        if "Read all the text" in text["text"]:
            return {"text": meta["reading_text"]}
        asked = text["text"].split("Question: ", 1)[1]
        q = next(q for q in meta["questions"] if q["question"] == asked)
        return {"text": q["answer"]}
    return answer


def test_vision_runner_three_trials_and_records(tmp_path, items):
    client = StubMediaClient(gold_answerer(items))
    sink = JsonlSink(tmp_path, "r1", tier="vision", suite="v1")
    recs = run_vision(client, items, cfg(), sink)
    nq = sum(len(m["questions"]) for m, _ in items)
    assert len(recs) == 3 * nq
    assert sorted({r["trial"] for r in recs}) == [1, 2, 3]
    assert {r["settings"]["seed"] for r in recs} == {1001, 1002, 1003}
    for r in recs:
        check_envelope(r)
        assert r["status"] == "ok" and r["tier"] == "vision"
    fields = [r for r in recs if "field_exact" in r["metrics"]]
    assert fields and all(r["metrics"]["field_exact"] for r in fields)
    lines = (tmp_path / "runs" / "r1" / "records.jsonl").read_text().splitlines()
    assert len(lines) == len(recs)
    raw = recs[0]["raw"]
    assert (tmp_path / "runs" / "r1" / raw["path"]).exists()
    # Every request carries a unique prefix (prompt-cache busting).
    prompts = [r["messages"][0]["content"][1]["text"] for r in client.requests]
    assert len(set(prompts)) == len(prompts)


def test_no_image_input_is_not_applicable_not_zero(tmp_path, items):
    client = StubMediaClient(lambda *a: {"text": "x"}, accepts_image=False)
    recs = run_vision(client, items[:1], cfg(), JsonlSink(tmp_path, "r2", tier="vision", suite="v1"))
    assert recs and all(r["status"] == "not_applicable" for r in recs)
    assert all(r["status_detail"] == "no image input" and r["metrics"] == {} for r in recs)
    assert client.requests == []


def test_engine_fault_is_retried_once_then_excluded(tmp_path, items):
    calls = {"n": 0}

    def flaky(messages, tools, index):
        calls["n"] += 1
        return {"engine_error": "transport"} if calls["n"] in (1, 3, 4) else {"text": "not shown"}
    meta, png = items[0]
    one = ({**meta, "questions": meta["questions"][:1]}, png)
    recs = run_vision(StubMediaClient(flaky), [one], cfg(), JsonlSink(tmp_path, "r3", tier="vision", suite="v1"))
    assert [r["status"] for r in recs] == ["ok", "excluded", "ok"]
    assert "transport" in recs[1]["status_detail"]


def test_truncated_empty_reply_is_a_failure(tmp_path, items):
    meta, png = items[0]
    one = ({**meta, "questions": [q for q in meta["questions"] if q["kind"] == "abstain"][:1]
            or meta["questions"][:1]}, png)
    client = StubMediaClient(lambda *a: {"text": "", "finish_reason": "length"})
    recs = run_vision(client, [one], cfg(trials=1), JsonlSink(tmp_path, "r4", tier="vision", suite="v1"))
    assert recs[0]["metrics"]["pass"] is False and recs[0]["status_detail"] == "truncated_empty"


def test_transcription_runner_wer_and_hallucination(tmp_path):
    eng = StubEngine()
    speech = eng.synthesize("set a timer for ten minutes", "stub-00")
    silence = Clip(np.zeros(2 * 16000, dtype=np.float32))
    utts = [Utterance("ls-1", speech, "set a timer for ten minutes"),
            Utterance("ns-1", silence, None, tags=["digital_silence"])]
    replies = iter([{"text": "Set a timer for 10 minutes."}] * 3 + [{"text": "Thank you."}] * 3)
    client = StubMediaClient(lambda *a: next(replies))
    recs = run_transcription(client, utts, cfg(), JsonlSink(tmp_path, "a1", tier="audio", suite="a1"))
    assert len(recs) == 6
    for r in recs:
        check_envelope(r)
    sp = [r for r in recs if r["case_id"] == "ls-1"]
    assert all(r["metrics"]["wer"] == 0.0 and r["metrics"]["ref_words"] == 6 for r in sp)
    assert all(r["metrics"]["rtf"] is not None and r["metrics"]["time_to_final_s"] > 0.5 for r in sp)
    ns = [r for r in recs if r["case_id"] == "ns-1"]
    assert all(r["facets"]["family"] == "a5" and r["metrics"]["hallucinated"] for r in ns)
    deaf = StubMediaClient(lambda *a: {"text": "x"}, accepts_audio=False)
    recs = run_transcription(deaf, utts[:1], cfg(), JsonlSink(tmp_path, "a1b", tier="audio", suite="a1"))
    assert all(r["status"] == "not_applicable" and r["status_detail"] == "no audio input" for r in recs)


def _command_model(case):
    def answer(messages, tools, index):
        if tools is None:
            return {"text": case.text}
        exp = case.expect[0]
        args = {k: (v.get("eq", v.get("icontains", case.entities[0].value)) if isinstance(v, dict)
                    else v) for k, v in exp["args"].items()}
        for e in case.entities:
            args.setdefault(e.arg, e.value)
        return {"calls": [{"name": exp["tool"], "arguments": args}]}
    return answer


def test_spoken_commands_two_pass_with_typed_twin(tmp_path):
    case = next(c for c in cmd.entity_commands(16) if c.expect[0]["tool"] == "send_email")
    clip = cmd.render_clip(case, StubEngine(), "stub-01")
    client = StubMediaClient(_command_model(case))
    recs = run_spoken_commands(client, [(case, clip)], cfg(), JsonlSink(tmp_path, "a3", tier="audio", suite="a3"),
                               matcher=cmd.ToolbenchMatcher())
    spoken = [r for r in recs if r["facets"]["lane"] == "two_pass"]
    typed = [r for r in recs if r["facets"]["lane"] == "typed"]
    assert len(spoken) == 3 and len(typed) == 3
    for r in spoken:
        check_envelope(r)
        m = r["metrics"]
        assert m["pass"] and m["entities_right"] == m["entities_total"] == 1
        assert m["command_latency_s"] > 0.5 and m["user_perceived_latency_s"] > m["command_latency_s"]
        assert m["tool_call_time_source"] == "adapter"


def test_spoken_commands_without_audio_use_the_shared_recogniser(tmp_path):
    case = cmd.entity_commands(8)[4]
    clip = cmd.render_clip(case, StubEngine(), "stub-02")
    text_model = StubMediaClient(_command_model(case), accepts_audio=False, model="cloud-text")
    recogniser = StubMediaClient(lambda *a: {"text": case.text}, model="shared-asr")
    c = cfg(kind="cloud_reference", device=None, recogniser={"id": "shared-asr"})
    recs = run_spoken_commands(text_model, [(case, clip)], c,
                               JsonlSink(tmp_path, "a3c", tier="audio", suite="a3"),
                               matcher=cmd.ToolbenchMatcher(), recogniser=recogniser, typed_twin=False)
    assert all(r["status"] == "ok" and r["metrics"]["pass"] for r in recs)
    assert all("shared recogniser: shared-asr" == r["status_detail"] for r in recs)
    assert all(r["device"] is None and r["device_reason"] == "cloud" for r in recs)
    assert len(recogniser.requests) == 3 and all(q["audio_s"] == 0 for q in text_model.requests)
    native = run_spoken_commands(text_model, [(case, clip)], c,
                                 JsonlSink(tmp_path, "a3d", tier="audio", suite="a3"),
                                 matcher=cmd.ToolbenchMatcher(), configuration="native")
    assert all(r["status"] == "not_applicable" and r["status_detail"] == "no audio input" for r in native)
    hears = StubMediaClient(_command_model(case), audio_with_tools=False)
    native = run_spoken_commands(hears, [(case, clip)], cfg(),
                                 JsonlSink(tmp_path, "a3e", tier="audio", suite="a3"),
                                 matcher=cmd.ToolbenchMatcher(), configuration="native")
    assert all(r["status"] == "unsupported_by_runtime" and r["status_detail"] == "not supported"
               for r in native)


def test_capabilities():
    hosted = type("H", (), {"describe": lambda self: {"model": "openai/gpt-6-luna"}})()
    assert capabilities_for(hosted).audio is False and capabilities_for(hosted).image is True
    gem = type("G", (), {"describe": lambda self: {"model": "google/gemini-4-neon"}})()
    assert capabilities_for(gem).audio is True
    unknown = type("U", (), {"describe": lambda self: {"model": "local-x"}})()
    with pytest.raises(ValueError):
        capabilities_for(unknown)
    assert capabilities_for(unknown, declared={"image": True, "audio": False}).source == "declared"
    assert image_part(b"\x89PNG")["image_url"]["url"].startswith("data:image/png;base64,")
    assert len(ADAPTER_GAPS) >= 3


def test_workloads_are_complete_and_paired():
    ws = wl.all_workloads()
    ids = [w["id"] for w in ws]
    assert len(ids) == len(set(ids))
    for w in ws:
        assert w["schema"] == wl.WORKLOAD_SCHEMA
        assert w["pair"] is None or w["pair"] in ids
    tokens = [w for w in ws if w["test"] == "image_tokens"]
    assert sorted({w["level"]["long_side"] for w in tokens}) == list(wl.LONG_SIDES)
    assert sorted({w["level"]["images"] for w in ws if w["test"] == "ttft_images"}) == [1, 2, 4, 8, 9]
    assert sorted({w["level"]["audio_s"] for w in ws if w["test"] == "ttft_audio"}) == list(wl.AUDIO_LENGTHS_S)
    rungs = [w for w in ws if w["test"] == "max_images"]
    assert [w["level"]["images"] for w in rungs] == [1, 2, 4, 8, 9, 12, 16]
    assert all(w["cases"] == 100 for w in rungs)
    assert len([w for w in ws if w["test"] == "resolution_accuracy"]) == len(wl.LONG_SIDES)
    assert len([w for w in ws if w["test"] == "token_budget"]) == 5
    json.loads(wl.dump(ws))


def test_max_images_case_asks_about_one_image_by_position():
    w = next(w for w in wl.vision_workloads() if w["id"] == "v4/max_images/n4")
    case = wl.case_request(w, 2)
    assert len(case["images"]) == 4 and case["target_image"] == 3
    assert "In image 3 of 4" in case["prompt"]
    assert case["question"]["kind"] in ("field", "anls", "relaxed")
    with pytest.raises(IndexError):
        wl.case_request(w, 100)


def test_workload_images_materialize_with_harbor_tiling():
    tiled = next(w for w in wl.vision_workloads() if w["level"].get("tiling") == "harbor_overview_plus_sections")
    imgs = wl.materialize_images(tiled)
    assert 2 <= len(imgs) <= 9                      # overview + up to 8 sections
    two = next(w for w in wl.vision_workloads() if w["id"] == "v4/ttft_images/n2")
    assert len(wl.materialize_images(two)) == 2


# ---------------------------------------------------------------- V7, V5, V3 runners

def test_run_reasoning_sends_every_image_and_scores_points(tmp_path):
    from edat.media.runner import run_reasoning
    from edat.media.vision import reasoning
    items = [reasoning.build("multi_image", 0, split="dev"), reasoning.build("counting", 0, split="dev")]
    gold = {m["prompt"]: m["question"]["answer"] for m, _ in items}

    def answer(messages, tools, i):
        text = messages[0]["content"][-1]["text"]
        return {"text": next(v for k, v in gold.items() if text.endswith(k))}
    client = StubMediaClient(answer)
    recs = run_reasoning(client, items, cfg(trials=1), JsonlSink(tmp_path, "r7", tier="vision", suite="v7"))
    assert [r["metrics"]["pass"] for r in recs] == [True, True]
    assert client.requests[0]["images"] == len(items[0][1]) >= 2
    for r in recs:
        check_envelope(r)
        assert r["facets"]["family"] == "v7"
    none = run_reasoning(StubMediaClient(answer, accepts_image=False), items[:1], cfg(trials=2),
                         JsonlSink(tmp_path, "r7b", tier="vision", suite="v7"))
    assert [r["status"] for r in none] == ["not_applicable"] * 2


def test_run_robustness_and_actions(tmp_path):
    from edat.media.runner import run_actions, run_robustness
    from edat.media.vision import actions, robustness
    meta, data = robustness.build_unreadable(1, split="dev")
    client = StubMediaClient(lambda m, t, i: {"text": "not shown"})
    recs = run_robustness(client, [(meta, data)], cfg(trials=1),
                          JsonlSink(tmp_path, "r5", tier="vision", suite="v5"))
    assert recs[0]["metrics"]["pass"] and recs[0]["facets"]["category"].startswith("unreadable")
    v3 = [actions.build(i, split="dev") for i in range(3)]
    replies = ["Sure.\n" + json.dumps({"proposal": m["expect"]}) if m["expect"] else "No event."
               for m, _ in v3]
    client = StubMediaClient(lambda m, t, i: {"text": replies[i]})
    recs = run_actions(client, v3, cfg(trials=1), JsonlSink(tmp_path, "r3", tier="vision", suite="v3"))
    assert all(r["metrics"]["pass"] for r in recs)
    assert client.requests[0]["messages"][0]["role"] == "system"
