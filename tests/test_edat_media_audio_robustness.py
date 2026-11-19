"""A4 conditions, A5 non-speech, A8 long-form and entity scoring (stub-engine audio only)."""
from __future__ import annotations

import json
from datetime import date

import pytest

np = pytest.importorskip("numpy")

from edat.media.audio import entities as ent
from edat.media.audio import longform as lf
from edat.media.audio import mix
from edat.media.audio import nonspeech as ns
from edat.media.audio.tts import StubEngine
from edat.media.audio.wav import SAMPLE_RATE, to_wav_bytes


@pytest.fixture(scope="module")
def engine():
    return StubEngine()


@pytest.fixture(scope="module")
def clip(engine):
    return engine.synthesize("please move the review to the later meeting room", "stub-02")


def _utt_ids(n=2620):
    return [f"{100 + i % 40}-{5000 + i // 40}-{i:04d}" for i in range(n)]


# ------------------------------------------------------------------ A4 noise

@pytest.mark.parametrize("kind", mix.A4_NOISE_TYPES)
def test_every_a4_noise_type_is_generated_and_seeded(kind):
    a = mix.generate_a4_noise(kind, 2.0, seed=4)
    b = mix.generate_a4_noise(kind, 2.0, seed=4)
    c = mix.generate_a4_noise(kind, 2.0, seed=5)
    assert len(a) == 2 * SAMPLE_RATE and np.array_equal(a, b) and not np.array_equal(a, c)
    assert np.sqrt(np.mean(a.astype(np.float64) ** 2)) == pytest.approx(0.1, rel=1e-3)
    assert np.all(np.isfinite(a))


def test_v1_generate_noise_still_refuses_recorded_types():
    with pytest.raises(ValueError):
        mix.generate_noise("cafe", 1.0, seed=0)


@pytest.mark.parametrize("snr", mix.A4_SNR_DB)
def test_a4_noise_mix_hits_snr_and_records_source(clip, snr):
    out = mix.mix_noise_a4(clip, "cafe", snr, seed=3)
    assert mix.measured_snr_db(clip, out) == pytest.approx(snr, abs=0.05)
    cond = out.meta["condition"]
    assert cond["noise_type"] == "cafe" and cond["snr_db"] == snr
    assert cond["noise_record"]["source"] == "generated"


def _write_noise(folder, name, kind, licence, seconds=1.0):
    x = (0.3 * np.random.default_rng(1).standard_normal(int(seconds * 8000))).astype(np.float32)
    (folder / f"{name}.wav").write_bytes(to_wav_bytes(x, 8000))
    (folder / f"{name}.json").write_text(json.dumps({"kind": kind, "licence": licence, "source": "test"}))


def test_recorded_noise_hook_uses_only_licensed_files(tmp_path, clip):
    _write_noise(tmp_path, "good", "cafe", "CC BY 4.0")
    _write_noise(tmp_path, "nc", "street", "CC BY-NC 4.0")
    x, rec = mix.recorded_noise("cafe", 3.0, 1, tmp_path)
    assert len(x) == 3 * SAMPLE_RATE
    assert rec == {"source": "recorded", "file": "good.wav", "licence": "CC BY 4.0"}
    with pytest.raises(LookupError):
        mix.recorded_noise("street", 1.0, 1, tmp_path)           # NC licence is refused
    _, rec2 = mix.noise_for("street", 1.0, 1, tmp_path)           # falls back to generated
    assert rec2["source"] == "generated"
    out = mix.mix_noise_a4(clip, "cafe", 10, seed=1, source_dir=tmp_path)
    assert out.meta["condition"]["noise_record"]["source"] == "recorded"
    assert mix.noise_for("cafe", 1.0, 1)[1]["source"] == "generated"      # no folder, no recording


# ------------------------------------------------------------------ rooms

@pytest.mark.parametrize("name", mix.ROOM_ORDER)
def test_room_drr_falls_with_distance_and_rt60_matches(name):
    room = mix.ROOMS[name]
    drr, rt = [], []
    for d in mix.DISTANCES_M:
        h = mix.room_rir(room, d, seed=2)
        drr.append(mix.direct_to_reverberant_db(h, window_s=0.001, direct_at=mix.direct_index(d)))
        rt.append(mix.rt60_from_rir(h))
    assert drr[0] > drr[1] > drr[2]
    for r in rt:
        assert r == pytest.approx(room.rt60_s, rel=0.25)


def test_room_description_and_apply_room(clip):
    d = mix.room_description("hall", 3)
    assert d["dims_m"] == [14.0, 9.0, 5.0] and d["rt60_target_s"] == 1.2 and 0 < d["absorption"] < 1
    out = mix.apply_room(clip, 3, seed=1, room="hall")
    assert len(out.samples) == len(clip.samples)
    cond = out.meta["condition"]
    assert cond["rir"] == "image_source" and cond["room"]["name"] == "hall" and cond["distance_m"] == 3
    assert mix.room_rir("hall", 3, seed=1).shape == mix.room_rir("hall", 3, seed=1).shape
    assert np.array_equal(mix.room_rir("hall", 3, seed=1), mix.room_rir("hall", 3, seed=1))
    legacy = mix.apply_room(clip, 3, seed=1)                       # the v1 call still works
    assert legacy.meta["condition"]["rir"] == "simulated"


# ------------------------------------------------------------------ level, clipping, band, truncation

@pytest.mark.parametrize("db", mix.A4_LOW_VOLUME_DBFS)
def test_low_volume_sets_the_peak(clip, db):
    out = mix.set_peak_dbfs(clip, db)
    assert 20 * np.log10(np.max(np.abs(out.samples))) == pytest.approx(db, abs=0.01)
    assert out.meta["condition"] == {"type": "low_volume", "peak_dbfs": db}


@pytest.mark.parametrize("db", mix.A4_OVERDRIVE_DB)
def test_clipping_is_hard_at_full_scale(clip, db):
    out = mix.overdrive_clip(clip, db)
    assert np.max(np.abs(out.samples)) == pytest.approx(1.0)
    assert out.meta["condition"]["clipped_fraction"] > 0.05
    assert mix.overdrive_clip(clip, 12).meta["condition"]["clipped_fraction"] > \
        mix.overdrive_clip(clip, 6).meta["condition"]["clipped_fraction"]


def test_narrowband_removes_energy_above_4khz():
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    from edat.media.audio.wav import Clip
    tone = Clip((0.4 * np.sin(2 * np.pi * 6000 * t) + 0.4 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32))
    out = mix.narrowband(tone)
    spec = np.abs(np.fft.rfft(out.samples[2000:-2000] * np.hanning(len(out.samples) - 4000)))
    f = np.fft.rfftfreq(len(out.samples) - 4000, 1 / SAMPLE_RATE)
    assert spec[(f > 5800) & (f < 6200)].max() < 0.03 * spec[(f > 900) & (f < 1100)].max()
    assert len(out.samples) == len(tone.samples)


def test_truncation_kinds_and_audible_words(clip):
    words = clip.meta["word_timings"]
    tail = mix.truncate(clip, "tail_cut")
    assert tail.duration_s == pytest.approx(clip.duration_s - 0.3, abs=1e-3)
    aud = mix.audible_reference(words, tail.meta["condition"])
    assert aud == [w["word"] for w in words][:-1]                   # the last word (0.24 s) is gone
    head = mix.truncate(clip, "head_cut")
    assert head.duration_s == pytest.approx(clip.duration_s - 0.3, abs=1e-3)
    assert head.meta["condition"]["time_shift_s"] == 0.3
    assert mix.audible_reference(words, head.meta["condition"]) == [w["word"] for w in words]  # lead-in only
    drop = mix.truncate(clip, "dropouts", seed=5)
    cuts = drop.meta["condition"]["cut_intervals_s"]
    assert len(cuts) == 3 and all(abs((b - a) - 0.2) < 1e-6 for a, b in cuts)
    for a, b in cuts:
        assert np.all(drop.samples[int(a * SAMPLE_RATE): int(b * SAMPLE_RATE)] == 0)
    assert np.array_equal(mix.truncate(clip, "dropouts", seed=5).samples, drop.samples)


def test_audible_reference_half_rule_and_missing_timings():
    words = [{"word": "a", "start_s": 0.0, "end_s": 0.4}, {"word": "b", "start_s": 0.5, "end_s": 0.9},
             {"word": "c", "start_s": 1.0, "end_s": 1.4}]
    cond = {"cut_intervals_s": [[0.0, 0.2], [0.5, 0.71]]}          # a: 50 % survives; b: 47.5 % does
    assert mix.audible_reference(words, cond) == ["a", "c"]
    assert mix.audible_reference(None, cond) is None
    assert mix.audible_reference([{"word": "a"}], cond) is None    # human recording, no aligner
    assert mix.audible_reference(words, {"type": "noise"}) == ["a", "b", "c"]


# ------------------------------------------------------------------ the grid and plan

def test_a4_grid_order_and_dispatch(clip, engine):
    grid = mix.a4_condition_grid()
    assert len(grid) == 19 and mix.condition_grid(a4=True) == grid
    assert [g["type"] for g in grid] == ["noise"] * 4 + ["room"] * 3 + ["overlap"] * 3 + \
        ["low_volume"] * 3 + ["clipping"] * 2 + ["truncation"] * 3 + ["narrowband"]
    assert len({mix.condition_key(g) for g in grid}) == 19
    assert mix.condition_grid()[0] == {"type": "clean"}            # v1 grid unchanged
    other = engine.synthesize("a second voice reading something else", "stub-07")
    for g in grid:
        out = mix.apply_condition(clip, g, seed=3, noise_type="street", room="living_room", interferer=other)
        assert out.meta["condition"]["type"] == g["type"]
        assert len(out.samples) > 0 and np.all(np.isfinite(out.samples))


def test_a4_plan_covers_2620_utterances_and_rotates_evenly():
    ids = _utt_ids()
    plan = mix.a4_plan(ids, seed=11)
    assert len(plan) == 2620 * 19
    assert plan == mix.a4_plan(ids, seed=11) and plan != mix.a4_plan(ids, seed=12)
    per = {}
    for r in plan:
        per.setdefault(r["utt_id"], []).append(r)
    assert all(len(v) == 19 for v in per.values())
    a = mix.a4_assignments(ids, 11)
    for key in ("noise_type", "room"):
        counts = {}
        for v in a.values():
            counts[v[key]] = counts.get(v[key], 0) + 1
        assert max(counts.values()) - min(counts.values()) <= 1
    assert set(a[ids[0]]) == {"noise_type", "room", "interferer_id"}
    assert all(v["interferer_id"].split("-")[0] != u.split("-")[0] for u, v in a.items())
    noisy = [r for r in plan if r["condition"]["type"] == "noise"]
    assert all(r["noise_type"] in mix.A4_NOISE_TYPES for r in noisy)
    # the same utterance keeps its noise type across the four SNR levels
    first = [r["noise_type"] for r in noisy if r["utt_id"] == ids[7]]
    assert len(first) == 4 and len(set(first)) == 1
    assert len({r["seed"] for r in plan}) > 0.99 * len(plan)


# ------------------------------------------------------------------ A4 scorers

def test_interferer_leak_and_empty_output_and_slope():
    target = "please call the front desk"
    other = "book the blue harbor room tomorrow"
    assert mix.interferer_leak(other, target, "please call the front desk") == 0.0
    # words the target also has ("the") are not counted either way
    assert mix.interferer_leak(other, target, "please call the blue room") == pytest.approx(2 / 5)
    assert mix.interferer_leak("call the desk", target, "anything") is None
    assert mix.empty_output_rate(["", "[silence]", "hello", "no speech"]) == 0.75
    assert mix.degradation_slope([20, 10, 5, 0], [0.1, 0.2, 0.3, 0.5], step=5) == pytest.approx(
        np.polyfit([20, 10, 5, 0], [0.1, 0.2, 0.3, 0.5], 1)[0] * 5)
    assert mix.degradation_slope([0, 1, 2], [0.0, 0.1, 0.2]) == pytest.approx(0.1)


def test_gap_invention_counts_unmatched_output_words(clip):
    words = clip.meta["word_timings"]
    cond = mix.truncate(clip, "tail_cut").meta["condition"]
    aud = mix.audible_reference(words, cond)
    ok = mix.gap_invention(aud, " ".join(aud), 1)
    assert ok == {"invented": 0, "gaps": 1, "per_gap": 0.0}
    bad = mix.gap_invention(aud, " ".join(aud) + " and then some more", 1)
    assert bad["invented"] == 4
    assert mix.gap_invention(aud, "", 3)["per_gap"] == 0.0


def test_align_ops_matches_edit_counts_and_bands_long_pairs():
    from edat.media.textdist import align
    ref = "the quick brown fox jumps over the lazy dog".split()
    hyp = "the quick red fox jumps the lazy lazy dog".split()
    ops = mix.align_ops(ref, hyp)
    c = align(ref, hyp)
    assert sum(o[0] == "sub" for o in ops) == c.substitutions
    assert sum(o[0] == "del" for o in ops) == c.deletions
    assert sum(o[0] == "ins" for o in ops) == c.insertions
    g = np.random.default_rng(0)
    long_ref = [f"w{int(i)}" for i in g.integers(0, 500, 3000)]
    long_hyp = long_ref[:1000] + long_ref[1010:]
    ops = mix.align_ops(long_ref, long_hyp, band=60)
    assert sum(o[0] == "del" for o in ops) == 10 and sum(o[0] == "ins" for o in ops) == 0


# ------------------------------------------------------------------ A5

def test_nonspeech_plan_counts_kinds_and_durations():
    plan = ns.nonspeech_plan(seed=1)
    assert len(plan) == 1000 == ns.NONSPEECH_COUNT and plan == ns.nonspeech_plan(seed=1)
    kinds = {}
    for p in plan:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
        assert 2.0 <= p["duration_s"] <= 30.0
    assert set(kinds) == set(ns.NONSPEECH_KINDS) and max(kinds.values()) - min(kinds.values()) <= 1
    assert all(p["label"] == "synthetic_stand_in" for p in plan if p["kind"] in ("cough", "breathing"))
    assert all(p["label"] == "generated" for p in plan if p["kind"] == "traffic")
    assert len({p["seed"] for p in plan}) == 1000


@pytest.mark.parametrize("kind", ns.NONSPEECH_KINDS)
def test_every_nonspeech_kind_renders(kind):
    item = {"item_id": "x", "kind": kind, "duration_s": 3.0, "seed": 9, "label": "generated"}
    c = ns.render_nonspeech(item)
    assert len(c.samples) == 3 * SAMPLE_RATE and c.meta["text"] == "" and np.all(np.isfinite(c.samples))
    if kind == "silence":
        assert not np.any(c.samples)
    else:
        assert np.max(np.abs(c.samples)) > 0
    assert np.array_equal(c.samples, ns.render_nonspeech(item).samples)


def test_speech_then_nonspeech_and_commands_in_noise_plans_and_renderers(clip):
    ids = _utt_ids()
    plan = ns.speech_then_nonspeech_plan(ids, seed=2)
    assert len(plan) == 1000 and len({p["utt_id"] for p in plan}) == 1000
    assert all(10.0 <= p["tail_s"] <= 30.0 for p in plan)
    item = {**plan[0], "tail_s": 2.0, "kind": "room_tone"}
    out = ns.render_speech_then_nonspeech(item, clip)
    assert out.duration_s == pytest.approx(clip.duration_s + 2.0, abs=1e-3)
    assert out.meta["last_speech_s"] == clip.eos_s and out.meta["text"] == clip.meta["text"]

    cmds = [f"cmd-{i:04d}" for i in range(300)]
    cplan = ns.commands_in_noise_plan(cmds, seed=3)
    assert len(cplan) == 500 and set(p["noise_type"] for p in cplan) == set(mix.A4_NOISE_TYPES)
    assert all(5.0 <= p["before_s"] <= 20.0 and 5.0 <= p["after_s"] <= 20.0 for p in cplan)
    small = {**cplan[0], "before_s": 1.0, "after_s": 1.5}
    mixed = ns.render_command_in_noise(small, clip)
    assert mixed.duration_s == pytest.approx(clip.duration_s + 2.5, abs=1e-3)
    assert mixed.speech_start_s == pytest.approx(clip.speech_start_s + 1.0)
    assert mixed.meta["condition"]["type"] == "command_in_noise"


def test_answer_leak_prompts_are_distinct_seeded_and_rendered(engine):
    prompts = ns.answer_leak_prompts(seed=4)
    assert len(prompts) == 500 and len({p["text"] for p in prompts}) == 500
    assert prompts == ns.answer_leak_prompts(seed=4)
    assert {p["kind"] for p in prompts} == {"question", "instruction"}
    assert any(p["text"] == "delete my notes" for p in prompts) or \
        any(p["text"].startswith("delete my notes") for p in prompts)
    assert any(p["text"].startswith("what's the weather") for p in prompts)
    assert all(p["expected_tool"] for p in prompts if p["kind"] == "instruction")
    c = ns.render_answer_leak(prompts[0], engine, "stub-01")
    assert c.duration_s - c.eos_s == pytest.approx(0.5, abs=1e-3) and c.meta["text"] == prompts[0]["text"]


def test_a5_scorers():
    assert ns.hallucination_rate(["", "[silence]", "thank you", "(no speech)"]) == 0.25
    assert ns.invented_words_per_minute(["", "thank you so much"], [30.0, 30.0]) == pytest.approx(4.0)
    assert ns.looping_run("so thank you for watching thank you for watching thank you for watching") == \
        {"ngram": "thank you for watching", "repeats": 3, "start": 1}
    assert ns.looping_run("a b c a b c a b") is None
    assert ns.looping_run("hello hello hello hello") is None            # 4 single-word repeats: below 5
    assert ns.looping_run("no no no no no go")["ngram"] == "no"
    assert ns.looping_rate(["x y x y x y", "fine text here", ""]) == pytest.approx(1 / 3)
    ref = "please move the review"
    assert ns.trailing_insertions(ref, "please move the review") == 0
    assert ns.trailing_insertions(ref, "please move the review thanks for watching") == 3
    assert ns.trailing_insertions(ref, "something unrelated") == 2
    assert ns.trailing_insertions("", "words here") == 2
    q = "what's the weather today"
    assert ns.answer_leak_result(q, "what's the weather today")["leak"] is False
    a = ns.answer_leak_result(q, "it is sunny and warm with light wind")
    assert a == {"answered": True, "acted": False, "leak": True}
    b = ns.answer_leak_result("delete my notes", "delete my notes", [{"name": "delete_note"}])
    assert b == {"answered": False, "acted": True, "leak": True}
    assert ns.answer_leak_rate([a, b, {"leak": False}]) == pytest.approx(2 / 3)


# ------------------------------------------------------------------ A8

def test_ladder_plan_shape_and_seams():
    g = np.random.default_rng(0)
    utts = [(u, float(g.uniform(1.5, 35.0))) for u in _utt_ids()]
    plan = lf.ladder_plan(utts, seed=1)
    assert len(plan) == 200 and plan == lf.ladder_plan(utts, seed=1)
    assert {p["rung_s"] for p in plan} == set(lf.LADDER_S)
    dur = dict(utts)
    for p in plan:
        assert p["total_s"] <= p["rung_s"] + 1e-6 and p["rung_s"] - p["total_s"] < 1.6 + 1e-6 + lf.UTTERANCE_GAP_S
        ids = [u["utt_id"] for u in p["utterances"]]
        assert len(ids) == len(set(ids))
        assert p["seams_s"] == [u["start_s"] for u in p["utterances"][1:]]
        for a, b in zip(p["utterances"], p["utterances"][1:]):
            assert b["start_s"] == pytest.approx(a["end_s"] + lf.UTTERANCE_GAP_S, abs=1e-3)
        for u in p["utterances"]:
            assert u["end_s"] - u["start_s"] == pytest.approx(dur[u["utt_id"]], abs=1e-3)
    assert sum(1 for p in plan if p["rung_s"] == 3600) == 20


def test_build_long_clip_offsets_and_timings(engine):
    texts = ["alpha bravo charlie", "delta echo", "foxtrot golf hotel india"]
    parts = [(engine.synthesize(t, f"stub-0{i}"), t) for i, t in enumerate(texts)]
    c = lf.build_long_clip(parts, gap_s=0.3)
    assert c.meta["text"] == " ".join(texts)
    wt = c.meta["word_timings"]
    assert [w["word"] for w in wt] == " ".join(texts).split()
    assert wt[0]["start_s"] == pytest.approx(0.0, abs=1e-3)
    assert c.meta["seams_s"][0] == pytest.approx(wt[3]["start_s"], abs=1e-3)
    assert wt[3]["start_s"] - wt[2]["end_s"] == pytest.approx(0.3, abs=1e-3)
    assert c.eos_s == pytest.approx(wt[-1]["end_s"], abs=1e-3)
    assert c.duration_s == pytest.approx(c.eos_s, abs=1e-3)


def test_windows_and_merge():
    assert lf.windows(5) == [(0.0, 5.0)] and lf.windows(30) == [(0.0, 30.0)]
    assert lf.windows(60) == [(0.0, 30.0), (29.0, 59.0), (58.0, 60.0)]
    assert len(lf.windows(3600)) == 125 and lf.windows(3600)[-1][1] == 3600.0
    assert lf.window_boundaries(60) == [29.5, 58.5]
    assert lf.merge_transcripts(["the cat sat on the mat", "on the mat and slept"]) == \
        "the cat sat on the mat and slept"
    assert lf.merge_transcripts(["one two three", "four five"]) == "one two three four five"
    assert lf.merge_transcripts(["we said that", "that is all"]) == "we said that that is all"
    assert lf.merge_transcripts(["Go to the, store.", "to the store, now"]) == "Go to the, store. now"
    long = "a b c d e f g h i j k l"
    assert lf.merge_transcripts([long, "e f g h i j k l m n"], max_overlap=8) == long + " m n"
    assert lf.merge_transcripts(["x y"]) == "x y" and lf.merge_transcripts([]) == ""


def _window_texts(timings, duration):
    out = []
    for s, e in lf.windows(duration):
        out.append(" ".join(w["word"] for w in timings if s <= (w["start_s"] + w["end_s"]) / 2 < e))
    return out


def test_seam_errors_clean_dropped_and_duplicated(engine):
    text = " ".join(f"w{i}" for i in range(100))
    c = engine.synthesize(text, "stub-03")
    wt = c.meta["word_timings"]
    bounds = lf.window_boundaries(c.duration_s)
    assert len(bounds) == 1
    texts = _window_texts(wt, c.duration_s)
    merged = lf.merge_transcripts(texts)
    assert merged == text
    clean = lf.seam_errors(wt, merged, bounds)
    assert clean == {"dropped": 0, "duplicated": 0, "boundaries": 1, "rate": 0.0}
    near = min(range(len(wt)), key=lambda i: abs((wt[i]["start_s"] + wt[i]["end_s"]) / 2 - bounds[0]))
    words = merged.split()
    dropped = lf.seam_errors(wt, " ".join(words[:near] + words[near + 1:]), bounds)
    assert dropped["dropped"] == 1 and dropped["duplicated"] == 0 and dropped["rate"] == 1.0
    far = lf.seam_errors(wt, " ".join(words[:5] + words[6:]), bounds)       # a drop far from the seam
    assert far["dropped"] == 0
    naive = lf.seam_errors(wt, " ".join(texts), bounds)                    # no overlap removal
    assert naive["duplicated"] >= 2 and naive["rate"] >= 2.0
    assert lf.seam_errors(wt, merged, []) == {"dropped": 0, "duplicated": 0, "boundaries": 0, "rate": 0.0}


def test_past_limit_classifier():
    ref = " ".join(f"word{i}" for i in range(100))
    assert lf.classify_past_limit(ref, None, error="HTTP 400 too many tokens") == "error"
    assert lf.classify_past_limit(ref, None) == "error"
    assert lf.classify_past_limit(ref, "I'm sorry, I cannot process audio this long.") == "refusal"
    assert lf.classify_past_limit(ref, "") == "refusal"
    assert lf.classify_past_limit(ref, " ".join(f"word{i}" for i in range(40))) == "truncated"
    tail = " ".join(f"word{i}" for i in range(60)) + " " + " ".join(f"invented{i}" for i in range(15))
    assert lf.classify_past_limit(ref, tail) == "hallucinated_tail"
    assert lf.classify_past_limit(ref, ref) == "ok"
    assert lf.classify_past_limit(ref, " ".join(f"word{i}" for i in range(90))) == "ok"
    assert lf.audio_tokens_per_minute(3100, 100, 120.0) == pytest.approx(1500.0)
    assert lf.audio_tokens_per_minute(None, 100, 120.0) is None


# ------------------------------------------------------------------ entities

ANCHOR = date(2026, 11, 18)        # a Wednesday


def _types(text, **kw):
    return {(e.type, e.canonical) for e in ent.extract_entities(text, anchor_date=ANCHOR, **kw)}


def test_extraction_of_each_entity_type():
    got = _types("pay twenty five dollars and ten cents, up three point five percent, room 12, "
                 "of the two hundred", terms=())
    assert ("number", "usd:25.1") in got and ("number", "pct:3.5") in got and ("number", "num:12") in got
    assert ("number", "num:200") in got
    assert _types("4:30 PM") == {("time", "16:30")}
    assert _types("four thirty p m") == {("time", "16:30")}
    assert _types("half past three") == {("time", "03:30")}
    assert _types("quarter to six") == {("time", "05:45")}
    assert _types("see you at 9 am") == {("time", "09:00")} and _types("noon") == {("time", "12:00")}
    assert _types("sixteen hundred hours") == {("time", "16:00")}
    assert _types("march third twenty twenty seven") == {("date", "2027-03-03")}
    assert _types("the 3rd of March") == {("date", "2027-03-03")}      # already past in 2026: next year
    assert _types("december 5th") == {("date", "2026-12-05")}
    assert _types("3/4/2027") == {("date", "2027-03-04")} and _types("2027-03-04") == {("date", "2027-03-04")}
    assert _types("tomorrow") == {("date", "2026-11-19")} and _types("yesterday") == {("date", "2026-11-17")}
    assert _types("friday") == {("date", "2026-11-20")} and _types("next friday") == {("date", "2026-11-27")}
    assert _types("wednesday") == {("date", "2026-11-25")}
    assert _types("in two weeks") == {("date", "2026-12-02")}
    assert _types("free cash flow and the agenda looked fine") == {("term", "free cash flow"), ("term", "agenda")}
    assert _types("send it to Quillon Aberfell", names=["Quillon Aberfell"]) == {("name", "quillon aberfell")}


def test_name_tagger_hook_and_term_file(tmp_path):
    tagger = lambda text: ["Marwenna Thornquist"]
    got = ent.extract_entities("ask marwenna thornquist", anchor_date=ANCHOR, name_tagger=tagger)
    assert [(e.type, e.canonical) for e in got] == [("name", "marwenna thornquist")]
    f = tmp_path / "terms.txt"
    f.write_text("# my terms\nzeta function\n\nflux capacitor\n")
    terms = ent.load_terms(f)
    assert terms == ("zeta function", "flux capacitor")
    assert _types("the flux capacitor failed", terms=terms) == {("term", "flux capacitor")}
    j = tmp_path / "terms.json"
    j.write_text(json.dumps(["alpha beta"]))
    assert ent.load_terms(j) == ("alpha beta",)


def test_entity_scores_on_hand_made_pairs():
    ref = ("call Quillon Aberfell at four thirty p m tomorrow about the gross margin, "
           "it was twenty five percent on three hundred units")
    names = ["Quillon Aberfell"]
    perfect = ent.entity_scores(ref, "Call Quillon Aberfell at 4:30 PM tomorrow about the gross margin, "
                                     "it was 25% on 300 units.", anchor_date=ANCHOR, names=names)
    assert perfect["error_rate"] == 0.0
    assert perfect["by_type"]["name"] == {"right": 1, "total": 1}
    assert perfect["by_type"]["time"] == {"right": 1, "total": 1}
    assert perfect["by_type"]["date"] == {"right": 1, "total": 1}
    assert perfect["by_type"]["term"] == {"right": 1, "total": 1}
    assert perfect["by_type"]["number"] == {"right": 2, "total": 2}
    bad = ent.entity_scores(ref, "call Quillon Abberfell at four fifteen p m tomorrow about the gross margin, "
                                 "it was fifteen percent on three hundred units",
                            anchor_date=ANCHOR, names=names)
    assert bad["by_type"]["name"]["right"] == 0 and bad["by_type"]["time"]["right"] == 0
    assert bad["by_type"]["date"]["right"] == 1 and bad["by_type"]["term"]["right"] == 1
    assert bad["by_type"]["number"] == {"right": 1, "total": 2}
    assert bad["total"] == {"right": 3, "total": 6} and bad["error_rate"] == pytest.approx(0.5)
    wrong = [e for e in bad["entities"] if not e["right"]]
    assert {e["type"] for e in wrong} == {"name", "time", "number"}
    # a hypothesis number cannot cover two reference numbers
    dup = ent.entity_scores("two cats and two dogs", "two pets", anchor_date=ANCHOR)
    assert dup["by_type"]["number"] == {"right": 1, "total": 2}
    none = ent.entity_scores("hello there", "hello", anchor_date=ANCHOR)
    assert none["error_rate"] is None and none["total"]["total"] == 0
    empty = ent.entity_scores("meet at 5 pm", "", anchor_date=ANCHOR)
    assert empty["error_rate"] == 1.0


def test_entity_scores_aggregate():
    a = ent.entity_scores("five apples", "5 apples", anchor_date=ANCHOR)
    b = ent.entity_scores("six pears", "seven pears", anchor_date=ANCHOR)
    agg = ent.aggregate_entity_scores([a, b])
    assert agg["by_type"]["number"]["right"] == 1 and agg["by_type"]["number"]["total"] == 2
    assert agg["by_type"]["number"]["accuracy"] == 0.5 and agg["error_rate"] == 0.5
    assert agg["by_type"]["date"]["accuracy"] is None
