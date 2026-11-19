"""A7 audio understanding: plans as data, a few renders with the stub engine, and the scorers."""
from __future__ import annotations

import csv
import itertools
import json
import subprocess
import sys
from collections import Counter

import pytest

np = pytest.importorskip("numpy")

from edat.media.audio import understanding as u
from edat.media.audio.tts import StubEngine
from edat.media.audio.wav import SAMPLE_RATE, to_wav_bytes


@pytest.fixture(scope="module")
def engine():
    return StubEngine()


@pytest.fixture(scope="module")
def conv_plans():
    return u.conversation_plans()


@pytest.fixture(scope="module")
def rec_plans():
    return u.recording_plans()


# ---------------------------------------------------------------- counts

def test_counts_and_cli():
    c = u.counts()
    assert c == {"conversations": 1000, "speaker_count_questions": 1000, "who_said_questions": 2000,
                 "urgency": 600, "generated_sounds": 1000, "recordings": 300,
                 "recording_questions": 1500}
    out = subprocess.run([sys.executable, "-m", "edat.media.audio.understanding", "counts"],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == c


# ---------------------------------------------------------------- conversations

def test_conversation_plans_as_data(conv_plans):
    assert len(conv_plans) == 1000
    assert Counter(p["n_speakers"] for p in conv_plans) == {1: 250, 2: 250, 3: 250, 4: 250}
    assert all(30.0 <= p["planned_duration_s"] <= 120.0 for p in conv_plans)
    assert all(p["needs_windowing"] for p in conv_plans if p["planned_duration_s"] > 30)
    assert u.conversation_plan(7) == conv_plans[7] == u.conversation_plan(7)
    assert u.conversation_plan(7, seed=1) != conv_plans[7]
    phrases = [p["asked_phrase"] for p in conv_plans]
    assert len(set(phrases)) == 1000                              # every planted phrase is unique
    for p in conv_plans:
        names = [s["name"] for s in p["speakers"]]
        assert len(set(names)) == len(set(s["voice"] for s in p["speakers"])) == p["n_speakers"]
        intros = [t for t in p["turns"] if t["role"] == "intro"]
        assert [t["speaker"] for t in intros] == names           # introduced in order, by name
        assert all(t["speaker"] in t["text"] for t in intros)
        moved = [t for t in p["turns"] if "moved" in t["text"]]
        asked = [t for t in p["turns"] if p["asked_phrase"] in t["text"]]
        assert len(moved) == 1 and moved[0]["speaker"] == p["mover"]
        assert len(asked) == 1 and asked[0]["speaker"] == p["asker"]
        assert names.index(p["asker"]) == u.ORDINALS.index(p["asker_ordinal"])
        assert not any("moved" in t["text"] or "send me" in t["text"]
                       for t in p["turns"] if t["role"] == "filler")


def test_conversation_questions(conv_plans):
    qs = u.all_conversation_questions()
    assert len(qs) == 3000 and len({q["id"] for q in qs}) == 3000
    assert Counter(q["type"] for q in qs) == {"speaker_count": 1000, "who_said": 1000, "asked_for": 1000}
    p = conv_plans[10]
    q = {x["type"]: x for x in u.conversation_questions(p)}
    assert q["speaker_count"]["answer"] == p["n_speakers"]
    assert q["who_said"]["answer"] == p["mover"]
    assert q["asked_for"]["question"] == f"What did the {p['asker_ordinal']} speaker ask for?"


def test_conversation_renders_with_turn_timings(engine, conv_plans):
    p = next(c for c in conv_plans if c["n_speakers"] == 3)
    clip = u.render_conversation(p, engine, max_turns=8)
    turns = clip.meta["turns"]
    assert len(turns) == 8 and clip.meta["placeholder"] is True
    assert [t["speaker"] for t in turns] == [t["speaker"] for t in p["turns"][:8]]
    for a, b in zip(turns, turns[1:]):
        assert b["start_s"] == pytest.approx(a["end_s"] + u.TURN_GAP_S, abs=1e-3)
    assert clip.eos_s == pytest.approx(clip.duration_s, abs=1e-3)
    assert clip.meta["segments"][0] == (turns[0]["speaker"], 0.0, turns[0]["end_s"])
    first = turns[0]["word_timings"][0]
    assert first["start_s"] == pytest.approx(0.0, abs=1e-3)
    voices = {t["voice"] for t in turns}
    assert len(voices) <= p["n_speakers"] and len(voices) >= 2
    # speaker audio differs by voice: same text, different voices give different samples
    a = engine.synthesize("hello there", "stub-01").samples
    assert not np.array_equal(a, engine.synthesize("hello there", "stub-02").samples)
    # a rendered conversation is its own diarisation truth
    assert u.der(clip.meta["segments"], clip.meta["segments"])["der"] == 0.0


def test_conversation_scorers(conv_plans):
    p = next(c for c in conv_plans if c["n_speakers"] == 4)
    q = {x["type"]: x for x in u.conversation_questions(p)}
    assert u.score_speaker_count("There are 4 speakers.", 4)
    assert u.score_speaker_count("four", 4) and not u.score_speaker_count("three people", 4)
    assert u.score_speaker_count("Just one person.", 1)
    names = [s["name"] for s in p["speakers"]]
    other = next(n for n in names if n != p["mover"])
    assert u.score_conversation_answer(q["who_said"], f"I think {p['mover']} said that.", p) == 1.0
    assert u.score_conversation_answer(q["who_said"], f"{p['mover']} or {other}", p) == 0.0
    assert u.score_conversation_answer(q["who_said"], other, p) == 0.0
    ph = p["asked_phrase"]
    assert u.score_conversation_answer(q["asked_for"], f"They asked for {ph}.") == 1.0
    assert u.score_conversation_answer(q["asked_for"], ph.replace("the ", "", 1)) == 1.0
    assert 0.5 <= u.score_conversation_answer(q["asked_for"], ph[:-1] + "7") < 1.0
    assert u.score_conversation_answer(q["asked_for"], "a pizza") == 0.0


# ---------------------------------------------------------------- DER

def test_der_known_values_without_collar():
    ref = [("A", 0, 10), ("B", 10, 20)]
    assert u.der(ref, ref, collar_s=0)["der"] == 0.0
    # confusion: B's half is labelled A
    d = u.der(ref, [("X", 0, 10), ("X", 10, 15), ("Y", 15, 20)], collar_s=0)
    assert d["confusion_s"] == pytest.approx(5.0, abs=0.02) and d["der"] == pytest.approx(0.25, abs=0.002)
    assert d["mapping"] == {"A": "X", "B": "Y"} or d["mapping"] == {"B": "Y", "A": "X"}
    # missed speech: 4 s of A missing
    d = u.der(ref, [("X", 0, 6), ("Y", 10, 20)], collar_s=0)
    assert d["missed_s"] == pytest.approx(4.0, abs=0.02) and d["der"] == pytest.approx(0.2, abs=0.002)
    # false alarm: 5 s of extra speech past the end
    d = u.der(ref, ref + [("A", 20, 25)], collar_s=0)
    assert d["false_alarm_s"] == pytest.approx(5.0, abs=0.02) and d["der"] == pytest.approx(0.25, abs=0.002)
    # swapped labels are free: the mapping is optimal one-to-one
    assert u.der(ref, [("Q", 0, 10), ("P", 10, 20)], collar_s=0)["der"] == 0.0
    # empty hypothesis: everything missed
    assert u.der(ref, [], collar_s=0)["der"] == pytest.approx(1.0, abs=1e-6)


def test_der_scores_overlap():
    ref = [("A", 0, 10), ("B", 5, 15)]                  # 5 s of overlap, 20 s of reference speech
    hyp = [("X", 0, 10)]                                # misses B entirely
    d = u.der(ref, hyp, collar_s=0)
    assert d["total_s"] == pytest.approx(20.0, abs=0.02)
    assert d["missed_s"] == pytest.approx(10.0, abs=0.02) and d["der"] == pytest.approx(0.5, abs=0.002)
    # a hypothesis that finds both speakers in the overlap is perfect
    assert u.der(ref, [("X", 0, 10), ("Y", 5, 15)], collar_s=0)["der"] == 0.0
    # one hypothesis speaker covering both: 5 s overlap missed, 5 s of B confused...
    d = u.der(ref, [("X", 0, 15)], collar_s=0)
    assert d["missed_s"] == pytest.approx(5.0, abs=0.02)         # overlap: 2 ref, 1 hyp
    assert d["confusion_s"] == pytest.approx(5.0, abs=0.02)      # B-only part labelled X (A mapped to X)


def test_der_collar_forgives_boundary_errors():
    ref = [("A", 0, 10), ("B", 10, 20)]
    hyp = [("X", 0, 10.2), ("Y", 10.2, 20)]             # boundary 0.2 s late: inside a 0.25 s collar
    assert u.der(ref, hyp, collar_s=0)["der"] == pytest.approx(0.2 / 20, abs=0.002)
    assert u.der(ref, hyp, collar_s=0.25)["der"] == 0.0
    late = [("X", 0, 10.6), ("Y", 10.6, 20)]            # 0.6 s late: collar cannot hide all of it
    d = u.der(ref, late, collar_s=0.25)
    assert d["total_s"] == pytest.approx(20 - 4 * 0.25 * 1.0 + 0.0, abs=0.3)
    assert 0.0 < d["der"] < 0.05


def test_der_inputs_and_corpus():
    rttm = ("SPEAKER f1 1 0.00 10.00 <NA> <NA> alice <NA> <NA>\n"
            "SPEAKER f1 1 10.00 10.00 <NA> <NA> bob <NA> <NA>\n")
    ref = u.parse_rttm(rttm)
    assert ref == [("alice", 0.0, 10.0), ("bob", 10.0, 20.0)]
    assert u.der(ref, [{"speaker": "p", "start": 0, "end": 10}, {"speaker": "q", "start": 10, "duration": 10}],
                 collar_s=0)["der"] == 0.0
    c = u.der_corpus([(ref, ref), (ref, [("x", 0, 10)])], collar_s=0)
    assert c["der"] == pytest.approx(10 / 40, abs=0.002)


def test_hungarian_matches_brute_force():
    rng = np.random.default_rng(3)
    for n, m in ((1, 1), (3, 3), (4, 6), (6, 4), (5, 5)):
        cost = rng.integers(-9, 9, size=(n, m))
        pairs = u.hungarian(cost)
        assert len(pairs) == min(n, m)
        assert len({i for i, _ in pairs}) == len(pairs) and len({j for _, j in pairs}) == len(pairs)
        got = sum(cost[i, j] for i, j in pairs)
        k = min(n, m)
        best = min(sum(cost[i, j] for i, j in zip(rows, cols))
                   for rows in itertools.combinations(range(n), k)
                   for cols in itertools.permutations(range(m), k))
        assert got == best


# ---------------------------------------------------------------- CREMA-D

def test_cremad_filenames_and_loader(tmp_path):
    f = u.parse_cremad_filename("1001_IEO_HAP_HI.wav")
    assert f["actor"] == 1001 and f["emotion"] == "HAP" and f["intensity"] == "high"
    g = u.parse_cremad_filename("/data/AudioWAV/1091_WSI_NEU_XX.wav")
    assert g["emotion"] == "NEU" and g["level"] == "XX" and g["intensity"] is None
    for bad in ("1001_IEO_HAP.wav", "x_IEO_HAP_HI.wav", "1001_IEO_XXX_HI.wav", "1001_IEO_HAP_ZZ.wav"):
        with pytest.raises(ValueError):
            u.parse_cremad_filename(bad)
    names = [f"{1001 + i}_IEO_{e}_{lv}.wav" for i, (e, lv) in
             enumerate(itertools.product(u.CREMAD_EMOTIONS, ("LO", "MD", "HI")))]
    items = u.load_cremad(names)
    assert len(items) == 18 and {i["emotion"] for i in items} == set(u.CREMAD_EMOTIONS)
    for n in names[:3]:
        (tmp_path / n).write_bytes(b"")
    assert [i["file"] for i in u.load_cremad(tmp_path)] == sorted(names[:3])
    assert "anger" in u.TONE_PROMPT and "low, medium or high" in u.INTENSITY_PROMPT


def test_cremad_scoring_with_synonyms():
    for text, code in (("The speaker sounds angry.", "ANG"), ("furious", "ANG"), ("Disgusted!", "DIS"),
                       ("They seem scared", "FEA"), ("Fearful", "FEA"), ("joyful and glad", "HAP"),
                       ("Happy.", "HAP"), ("neutral", "NEU"), ("calm, no emotion", "NEU"),
                       ("quite sad", "SAD"), ("Sadness", "SAD")):
        assert u.score_tone(text, code), (text, code)
    assert not u.score_tone("angry or sad", "ANG")                # hedging is wrong
    assert not u.score_tone("not happy", "HAP")
    assert u.score_tone("not happy, rather sad", "SAD")
    assert not u.score_tone("I do not know", "NEU")
    for text, lv in (("low", "LO"), ("Mild", "LO"), ("moderate", "MD"), ("medium", "MD"),
                     ("strong", "HI"), ("It is high", "HI")):
        assert u.score_intensity(text, lv), (text, lv)
    assert u.score_intensity("high", "high") and not u.score_intensity("very low", "HI")
    items = u.load_cremad(["1001_IEO_ANG_HI.wav", "1002_DFA_SAD_XX.wav"])
    r = u.score_cremad([(items[0], "angry", "high"), (items[1], "happy", "low")])
    assert r["tone_accuracy"] == 0.5 and r["intensity_accuracy"] == 1.0 and r["n_intensity"] == 1


# ---------------------------------------------------------------- urgency

def test_urgency_plan_is_crossed_and_balanced():
    plan = u.urgency_plan()
    assert len(plan) == 600 and len({p["id"] for p in plan}) == 600
    assert Counter((p["content"], p["delivery"]) for p in plan) == {
        (c, d): 150 for c in ("urgent", "routine") for d in ("calm", "rushed")}
    assert all(p["rate"] == (1.25 if p["delivery"] == "rushed" else 0.9) for p in plan)
    assert all(p["pitch_semitones"] == (2.0 if p["delivery"] == "rushed" else 0.0) for p in plan)
    assert sum(p["conflict"] for p in plan) == 300
    assert u.urgency_plan(seed=0) == plan and u.urgency_plan(seed=1) != plan
    assert all("{" not in p["text"] for p in plan)
    assert len({p["text"] for p in plan}) > 400


def test_urgency_render_and_scoring(engine):
    plan = u.urgency_plan(8)
    calm = next(p for p in plan if p["delivery"] == "calm")
    rushed = next(p for p in plan if p["delivery"] == "rushed")
    cc, rc = u.render_urgency(calm, engine), u.render_urgency(rushed, engine)
    assert cc.meta["rate"] == 0.9 and rc.meta["rate"] == 1.25 and rc.meta["pitch_semitones"] == 2.0
    assert u.parse_urgency("This is urgent.") == "urgent" and u.parse_urgency("Routine") == "routine"
    assert u.parse_urgency("not urgent") == "routine" and u.parse_urgency("non-urgent message") == "routine"
    assert u.parse_urgency("hard to say") is None
    full = u.urgency_plan()
    follows_content = [(p, p["content"]) for p in full]
    r = u.score_urgency(follows_content)
    assert r["accuracy"] == r["content_driven"] == r["agree_accuracy"] == 1.0 and r["delivery_driven"] == 0.0
    follows_delivery = [(p, p["delivery_label"]) for p in full]
    r = u.score_urgency(follows_delivery)
    assert r["content_driven"] == 0.0 and r["delivery_driven"] == 1.0 and r["agree_accuracy"] == 1.0
    assert r["accuracy"] == 0.5 and r["n_conflict"] == 300
    assert r["by_cell"]["urgent/calm"] == 0.0 and r["by_cell"]["urgent/rushed"] == 1.0


# ---------------------------------------------------------------- everyday sounds

def test_esc10_loader(tmp_path):
    (tmp_path / "meta").mkdir()
    cats = ["dog", "rain", "sea_waves", "crying_baby", "clock_tick", "person_sneeze", "helicopter",
            "chainsaw", "rooster", "fire_crackling"]
    with open(tmp_path / "meta" / "esc50.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filename", "fold", "target", "category", "esc10", "src_file", "take"])
        for i, c in enumerate(cats):
            w.writerow([f"1-{i}-A-{i}.wav", 1 + i % 5, i, c, "True", str(i), "A"])
            w.writerow([f"2-{i}-A-{i + 10}.wav", 2, i + 10, "other", "False", str(i), "A"])
    rows = u.load_esc10(tmp_path)
    assert len(rows) == 10 and {r["class"] for r in rows} == set(cats)
    assert rows[0]["path"].endswith("audio/1-0-A-0.wav") and rows[0]["fold"] == 1
    assert u.score_sound("It's a chopper overhead", "helicopter", u.ESC10_SYNONYMS)
    assert u.score_sound("a baby crying", "crying_baby", u.ESC10_SYNONYMS)
    assert not u.score_sound("rain and waves", "rain", u.ESC10_SYNONYMS)


def test_sound_plan_balance_and_labels():
    plan = u.sound_plan()
    assert len(plan) == 1000 and len({p["id"] for p in plan}) == 1000
    assert Counter(p["class"] for p in plan) == {c: 100 for c in u.SOUND_CLASSES}
    assert Counter((p["class"], p["tier"]) for p in plan) == {(c, t): 25 for c in u.SOUND_CLASSES
                                                              for t in u.SOUND_TIERS}
    assert u.SOUND_TIERS == {"0db": 0.0, "-10db": -10.0, "-20db": -20.0, "low": -30.0}
    assert all(p["source"] == "synthetic_stand_in" for p in plan)
    assert {p["room_tone"] for p in plan} == set(u.ROOM_TONES)
    assert u.sound_plan(5, seed=1) != plan[:5]


@pytest.mark.parametrize("cls", u.SOUND_CLASSES)
def test_every_sound_class_renders_and_levels_hold(cls):
    base = next(p for p in u.sound_plan() if p["class"] == cls and p["tier"] == "0db")
    a = u.render_sound(base)
    assert a.meta["source"] == "synthetic_stand_in" and a.meta["synthetic_stand_in"] is True
    assert a.meta["label"] == cls and np.isfinite(a.samples).all() and np.abs(a.samples).max() <= 0.95
    assert a.duration_s == pytest.approx(u.SOUND_CLIP_S, abs=1e-3)
    assert 0.2 < a.meta["event_s"] <= 4.6
    assert np.array_equal(a.samples, u.render_sound(base).samples)
    quiet = u.render_sound({**base, "tier": "-20db", "level_db": -20.0})
    diff = a.samples.astype(np.float64) - quiet.samples
    # same room tone and onset: the difference is the event's gain change (0.9 of the room rms over the
    # event's own duration), spread over the whole clip
    want = 0.9 * u.ROOM_TONE_RMS * (a.meta["event_s"] / u.SOUND_CLIP_S) ** 0.5
    assert float(np.sqrt(np.mean(diff ** 2))) == pytest.approx(want, rel=0.03)
    assert a.meta["onset_s"] == quiet.meta["onset_s"]


def test_sound_library_hook(tmp_path):
    tone = (0.3 * np.sin(2 * np.pi * 800 * np.arange(8000) / 8000)).astype(np.float32)   # 1 s at 8 kHz
    (tmp_path / "bell1.wav").write_bytes(to_wav_bytes(tone, 8000))
    (tmp_path / "bell1.json").write_text(json.dumps({"class": "doorbell", "licence": "CC BY 4.0",
                                                     "attribution": "Invented Author"}))
    (tmp_path / "nolic.wav").write_bytes(to_wav_bytes(tone, 8000))
    (tmp_path / "nc.wav").write_bytes(to_wav_bytes(tone, 8000))
    (tmp_path / "nc.json").write_text(json.dumps({"class": "kettle", "licence": "CC BY-NC 4.0"}))
    zero = tmp_path / "zero.wav"
    zero.write_bytes(to_wav_bytes(tone, 8000))
    zero.with_suffix(".json").write_text(json.dumps({"class": "car_horn", "licence": "CC0-1.0"}))
    lib = u.load_sound_library(tmp_path)
    assert set(lib.by_class) == {"doorbell", "car_horn"}
    assert {n for n, _ in lib.rejected} == {"nolic.wav", "nc.wav"}
    item = next(p for p in u.sound_plan() if p["class"] == "doorbell")
    rec = u.render_sound(item, library=lib)
    assert rec.meta["source"] == "recorded" and rec.meta["licence"] == "CC BY 4.0"
    assert rec.meta["synthetic_stand_in"] is False and np.isfinite(rec.samples).all()
    kettle = next(p for p in u.sound_plan() if p["class"] == "kettle")
    assert u.render_sound(kettle, library=lib).meta["source"] == "synthetic_stand_in"


def test_sound_scoring():
    for text, cls in (("a doorbell", "doorbell"), ("Smoke detector beeping", "smoke_alarm"),
                      ("a baby crying", "baby_crying"), ("glass shattering", "glass_breaking"),
                      ("the kettle whistling", "kettle"), ("telephone ringing", "phone_ringing"),
                      ("someone knocking on the door", "knocking"), ("a dog barking", "dog_barking"),
                      ("running water from a tap", "running_water"), ("a car honking its horn", "car_horn")):
        assert u.score_sound(text, cls), (text, cls)
    assert not u.score_sound("doorbell or knocking", "knocking")
    assert u.asserts_sound("a kettle") and not u.asserts_sound("Nothing, only room tone")
    plan = u.sound_plan()
    rows = [(p, p["class"].replace("_", " ")) for p in plan]
    r = u.score_sounds(rows)
    assert r["class_accuracy"] == 1.0 and r["low_level_detection_rate"] == 1.0 and r["n_low"] == 250
    rows = [(p, "none" if p["tier"] == "low" else p["class"].replace("_", " ")) for p in plan]
    r = u.score_sounds(rows)
    assert r["class_accuracy"] == 1.0 and r["low_level_detection_rate"] == 0.0
    assert r["low_level_asserted_rate"] == 0.0 and r["n_audible"] == 750
    r = u.score_sounds([(p, "a doorbell") for p in plan])
    assert r["class_accuracy"] == pytest.approx(0.1) and r["low_level_asserted_rate"] == 1.0


# ---------------------------------------------------------------- recordings

def test_recording_plans_as_data(rec_plans):
    assert len(rec_plans) == 300
    assert Counter(p["recording_kind"] for p in rec_plans) == {"memo": 100, "meeting": 150, "talk": 50}
    qs = [q for p in rec_plans for q in p["questions"]]
    assert len(qs) == 1500 and len({q["id"] for q in qs}) == 1500
    assert all(len(p["questions"]) == 5 for p in rec_plans)
    assert sum(not q["stated"] for q in qs) == 150
    assert all(q["answer"] == "not stated" for q in qs if not q["stated"])
    assert {q["answer_kind"] for q in qs} == {"date", "time", "money", "text", "number"}
    ranges = {"memo": (30, 180), "meeting": (300, 900), "talk": (1200, 1800)}
    spk = {"memo": {1}, "meeting": {2, 3, 4}, "talk": {1}}
    for p in rec_plans:
        lo, hi = ranges[p["recording_kind"]]
        assert lo <= p["planned_duration_s"] <= hi
        assert p["n_speakers"] in spk[p["recording_kind"]]
        assert p["needs_windowing"] == (p["planned_duration_s"] > 30)
        assert p["n_windows"] == len(u.windows(p["planned_duration_s"]))
        assert len({f["project"] for f in p["facts"]}) == len(p["facts"])
        for q in p["questions"]:
            if q["stated"]:
                assert q["answer"] == p["facts"][q["fact"]]["answer"]
    assert sum(p["planned_duration_s"] for p in rec_plans) / 3600 > 40       # about 48 h
    assert u.recording_plan(5) == rec_plans[5]


def test_windowing_policy():
    assert u.windows(20.0) == [(0.0, 20.0)]
    w = u.windows(75.0)
    assert w == [(0.0, 30.0), (29.0, 59.0), (58.0, 75.0)]
    assert all(b[0] == a[1] - 1.0 for a, b in zip(w, w[1:]))
    assert u.windows(30.0) == [(0.0, 30.0)]


def test_recording_scripts_hold_durations_and_facts(rec_plans):
    ranges = {"memo": (30, 180), "meeting": (300, 900), "talk": (1200, 1800)}
    sample = rec_plans[:100] + rec_plans[100:250:15] + rec_plans[250::25]
    for p in sample:
        s = u.recording_script(p)
        lo, hi = ranges[p["recording_kind"]]
        assert lo <= s["duration_s"] <= hi, (p["id"], s["duration_s"])
        text = " ".join(t["text"] for t in s["turns"])
        for f in p["facts"]:
            assert f["sentence"] in text
        for q in p["questions"]:
            if not q["stated"]:
                ghost = q["question"]
                assert not any(f["project"] in ghost for f in p["facts"])
                proj = [w for w in ghost.replace("?", "").split() if w not in text.split()]
                assert proj                                            # a word that is never spoken
        assert {t["speaker"] for t in s["turns"]} <= {x["name"] for x in p["speakers"]}
    assert abs(u.recording_script(rec_plans[3])["words"] / 2.5 - rec_plans[3]["planned_duration_s"]) < 30


def test_recording_renders_a_few_turns(engine, rec_plans):
    p = rec_plans[120]                                                 # a meeting
    clip = u.render_recording(p, engine, max_turns=4)
    assert len(clip.meta["turns"]) == 4 and clip.meta["needs_windowing"] is True
    assert clip.duration_s > 3


def test_recording_scoring():
    kinds = {"date": ("2027-03-14", "It will be March 14, 2027.", "14 March 2027"),
             "time": ("16:30", "4:30 PM", "half past"),
             "money": ("4250.00 USD", "$4,250", "$4,300"),
             "number": ("12", "twelve", "13"),
             "text": ("Amara Bradane", "amara bradane.", "Bilal Bradane")}
    for kind, (exp, good, bad) in kinds.items():
        q = {"stated": True, "answer_kind": kind, "answer": exp}
        assert u.score_recording_answer(q, good)["correct"], kind
    for kind, (exp, good, bad) in kinds.items():
        q = {"stated": True, "answer_kind": kind, "answer": exp}
        if kind != "date":
            assert not u.score_recording_answer(q, bad)["correct"], kind
    assert u.score_recording_answer({"stated": True, "answer_kind": "date", "answer": "2027-03-14"},
                                    "14 March 2027")["correct"]
    txt = {"stated": True, "answer_kind": "text", "answer": "Amara Bradane"}
    assert u.score_recording_answer(txt, "The owner is Amara Bradane.")["correct"]
    assert not u.score_recording_answer(txt, "Amara")["correct"]
    ns = {"stated": False, "answer_kind": "money", "answer": "not stated"}
    for a in ("Not stated.", "That was not mentioned", "It's not said anywhere", "wasn't mentioned",
              "The budget is not stated in the recording"):
        assert u.is_abstention(a), a
        assert u.score_recording_answer(ns, a)["correct"]
    assert not u.score_recording_answer(ns, "$5,000")["correct"]
    assert not u.is_abstention("The stated budget is 5,000 dollars")
    assert u.score_recording_answer(txt, "not stated") == {"correct": False, "abstained": True, "stated": True}


def test_recording_scoring_aggregate(rec_plans):
    qs = [q for p in rec_plans for q in p["questions"]]
    perfect = u.score_recordings([(q, q["answer"]) for q in qs])
    assert perfect["qa_accuracy"] == 1.0 and perfect["abstention_rate"] == 1.0
    assert perfect["n_stated"] == 1350 and perfect["n_not_stated"] == 150
    lazy = u.score_recordings([(q, "not mentioned") for q in qs])
    assert lazy["qa_accuracy"] == 0.0 and lazy["false_abstention_rate"] == 1.0 and lazy["abstention_rate"] == 1.0
    guess = u.score_recordings([(q, "42") for q in qs])
    assert guess["hallucination_rate"] == 1.0


# ---------------------------------------------------------------- Big Bench Audio

def test_big_bench_audio_loader_and_scoring():
    rows = [{"id": "a1", "category": "formal_fallacies", "audio": {"path": "a1.wav"}, "transcript": "t",
             "official_answer": "invalid"},
            {"id": "a2", "category": "navigate", "audio": b"", "transcript": "t", "official_answer": "Yes"},
            {"id": "a3", "category": "counting", "audio": None, "transcript": "t", "official_answer": "7"},
            {"id": "a4", "category": "mc", "audio": None, "transcript": "t", "official_answer": "(B)"},
            {"id": "a5", "category": "name", "audio": None, "transcript": "t", "answer": "Chuck Norwick"}]
    items = u.load_bba_rows(rows)
    assert [i.answer for i in items] == ["invalid", "Yes", "7", "(B)", "Chuck Norwick"]
    with pytest.raises(ValueError):
        u.load_bba_rows([{"id": "x"}])
    answers = ["The argument is invalid.", "yes, you do return to the start", "I count seven items. The answer is 7.",
               "The answer is (B).", "It was Chuck Norwick."]
    r = u.score_bba_rows(zip(items, answers))
    assert r["accuracy"] == 1.0 and r["n"] == 5 and r["by_category"]["navigate"] == 1.0
    assert not u.score_bba("The argument is valid.", "invalid")
    assert u.score_bba("It is not valid.", "invalid")
    assert not u.score_bba("No.", "Yes") and u.score_bba("Yes", "yes")
    assert u.score_bba("so, 12 apples, therefore the answer is 8", "8")
    assert not u.score_bba("There are 9.", "8")
    assert u.score_bba("B", "B") and u.score_bba("Option B is right", "(B)") and not u.score_bba("(C)", "(B)")
    assert not u.score_bba("a very long answer about something unrelated and different from it all today ok", "Paris")
    assert u.score_bba("Paris", "paris")
