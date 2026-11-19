"""Spoken-command path with the stub speech engine: audio, conditions, timing and scoring."""
from __future__ import annotations

from datetime import date

import pytest

np = pytest.importorskip("numpy")

from edat.media.adapter import StubMediaClient
from edat.media.audio import commands as cmd
from edat.media.audio.latency import (audio_tokens_per_second, cold_penalty,
                                      reference_endpoint_delay, spoken_command_latency,
                                      transcript_latency, user_perceived_latency)
from edat.media.audio.mix import (DISTANCES_M, SIR_LADDER_DB, SNR_LADDER_DB, apply_room,
                                  condition_grid, direct_to_reverberant_db, generate_noise,
                                  measured_snr_db, mix_at_snr, overlap, simulated_rir)
from edat.media.audio.tts import CANDIDATES, StubEngine, get_engine
from edat.media.audio.wav import SAMPLE_RATE, read_wav, resample, to_wav_bytes


@pytest.fixture(scope="module")
def engine():
    return StubEngine()


def test_stub_engine_is_deterministic_and_marks_placeholder(engine):
    a = engine.synthesize("call amara at five", "stub-03")
    b = engine.synthesize("call amara at five", "stub-03")
    assert np.array_equal(a.samples, b.samples)
    assert a.meta["placeholder"] is True
    assert a.eos_s == pytest.approx(a.duration_s, abs=1e-3)
    assert len(a.meta["word_timings"]) == 4
    assert not np.array_equal(a.samples, engine.synthesize("call amara at five", "stub-04").samples)


def test_real_engines_are_refused_until_approved():
    assert get_engine("stub").id == StubEngine.id
    with pytest.raises(LookupError):
        get_engine("piper")
    assert not any(c.approved for c in CANDIDATES)


def test_wav_round_trip_and_resample():
    x = (0.5 * np.sin(2 * np.pi * 440 * np.arange(SAMPLE_RATE) / SAMPLE_RATE)).astype(np.float32)
    y, rate = read_wav(to_wav_bytes(x))
    assert rate == SAMPLE_RATE and np.max(np.abs(x - y)) < 1e-4
    up = (0.5 * np.sin(2 * np.pi * 440 * np.arange(24000) / 24000)).astype(np.float32)
    down = resample(up, 24000, SAMPLE_RATE)
    assert len(down) == SAMPLE_RATE
    ref = 0.5 * np.sin(2 * np.pi * 440 * np.arange(SAMPLE_RATE) / SAMPLE_RATE)
    assert np.max(np.abs(down[200:-200] - ref[200:-200])) < 0.01


def test_clip_has_exactly_half_a_second_after_end_of_speech(engine):
    case = cmd.entity_commands(8)[0]
    clip = cmd.render_clip(case, engine, "stub-00")
    assert clip.duration_s - clip.eos_s == pytest.approx(0.5, abs=1 / SAMPLE_RATE)
    assert clip.meta["condition"] == {"type": "clean"}


@pytest.mark.parametrize("snr", SNR_LADDER_DB)
@pytest.mark.parametrize("kind", ["white", "pink", "fan", "keyboard"])
def test_noise_is_mixed_at_the_requested_snr(engine, kind, snr):
    clean = cmd.render_clip(cmd.entity_commands(8)[1], engine, "stub-01")
    noise = generate_noise(kind, clean.duration_s, seed=7)
    mixed = mix_at_snr(clean, noise, snr, label=kind)
    assert measured_snr_db(clean, mixed) == pytest.approx(snr, abs=0.05)
    assert mixed.meta["condition"]["snr_db"] == snr


def test_babble_uses_a_supplied_voice_source(engine):
    def source(seconds, seed):
        return engine.synthesize("one two three four five six seven", f"stub-{seed % 12:02d}").samples
    x = generate_noise("babble", 2.0, seed=3, babble_source=source)
    assert len(x) == 2 * SAMPLE_RATE and np.sqrt(np.mean(x ** 2)) == pytest.approx(0.1, rel=1e-3)
    with pytest.raises(ValueError):
        generate_noise("cafe", 1.0, seed=0)          # recorded noises need a dataset


def test_room_response_gets_less_direct_with_distance(engine):
    drr = [direct_to_reverberant_db(simulated_rir(d, seed=1)) for d in DISTANCES_M]
    assert drr[0] > drr[1] > drr[2]
    clean = cmd.render_clip(cmd.entity_commands(8)[2], engine, "stub-02")
    far = apply_room(clean, 5, seed=1)
    assert len(far.samples) == len(clean.samples)
    assert far.meta["condition"]["distance_m"] == 5


@pytest.mark.parametrize("sir", SIR_LADDER_DB)
def test_overlap_at_sir(engine, sir):
    target = cmd.render_clip(cmd.entity_commands(8)[3], engine, "stub-03")
    other = engine.synthesize("please book the blue room for tomorrow afternoon", "stub-07")
    mixed = overlap(target, other, sir)
    span = slice(int(target.speech_start_s * SAMPLE_RATE), int(target.eos_s * SAMPLE_RATE))
    scale = mixed.meta["condition"]["peak_scale"]
    s = target.samples[span] * scale
    i = mixed.samples[span] - s
    assert 10 * np.log10(np.mean(s ** 2) / np.mean(i ** 2)) == pytest.approx(sir, abs=0.05)


def test_condition_grid_covers_version_one():
    grid = condition_grid()
    assert grid[0] == {"type": "clean"}
    assert sum(1 for g in grid if g["type"] == "noise") == 6 * len(SNR_LADDER_DB)
    assert [g["distance_m"] for g in grid if g["type"] == "room"] == list(DISTANCES_M)


def test_entity_command_set():
    cases = cmd.entity_commands()
    assert len(cases) == cmd.ENTITY_SET_SIZE == 300
    assert len({c.case_id for c in cases}) == 300
    assert cmd.entity_commands()[17].to_json() == cases[17].to_json()      # seeded
    kinds = {e.kind for c in cases for e in c.entities}
    assert kinds == {"phone", "name", "time", "date", "duration", "money", "email", "address"}
    for c in cases:
        assert not any(ch.isdigit() for ch in c.spoken), c.spoken   # spoken forms are words
        assert c.text.isascii() and c.expect
    phones = [e.value for c in cases for e in c.entities if e.kind == "phone"]
    assert all(p[3:6] == "555" and p[6:8] == "01" for p in phones)   # fictional range


def test_spoken_number_words():
    assert cmd.say_int(2027) == "two thousand twenty seven"
    assert cmd.say_ordinal(23) == "twenty third"
    assert cmd.say_ordinal(20) == "twentieth"
    assert cmd.say_money(23.5) == "twenty three dollars and fifty cents"
    assert cmd.say_time("16:30", 1) == "half past four in the afternoon"
    assert cmd.say_time("09:05", 0) == "nine oh five a m"
    assert cmd.say_email("ines.calford@example.com") == "ines dot calford at example dot com"


def _agent_answer(case):
    """A stub model: transcribes with the case's typed text, then calls the expected tool."""
    def answer(messages, tools, index):
        if tools is None:
            return {"text": case.text}
        exp = case.expect[0]
        args = {}
        for e in case.entities:
            args[e.arg] = {"phone": "(415) 555-0100", "time": "16:30"}.get(e.kind, e.value)
        for k, v in exp["args"].items():
            if isinstance(v, dict):
                args[k] = v.get("eq", v.get("icontains", args.get(k)))
            else:
                args[k] = v
        return {"calls": [{"name": exp["tool"], "arguments": args}]}
    return answer


def test_two_pass_native_and_pipeline_through_the_matcher_seam(engine):
    case = next(c for c in cmd.entity_commands(8) if c.expect[0]["tool"] == "add_calendar_event")
    clip = cmd.render_clip(case, engine, "stub-05")
    client = StubMediaClient(_agent_answer(case))
    out = cmd.run_spoken_command(case, clip, configuration="two_pass", client=client,
                                 matcher=cmd.ToolbenchMatcher(), seed=1001)
    assert out.match.passed, out.match.mismatches
    assert out.transcript == case.text
    assert len(client.requests) == 2
    first = client.requests[0]["messages"][0]["content"]
    assert first[0]["type"] == "input_audio" and first[0]["input_audio"]["format"] == "wav"
    assert client.requests[1]["tools"] == cmd.TOOLS
    assert out.entities_total == 2
    native = cmd.run_spoken_command(case, clip, configuration="native", client=client,
                                    matcher=cmd.ToolbenchMatcher())
    assert len(native.results) == 1 and native.transcript is None
    rec = StubMediaClient(lambda m, t, i: {"text": case.text}, model="recogniser")
    text_only = StubMediaClient(_agent_answer(case), accepts_audio=False)
    piped = cmd.run_spoken_command(case, clip, configuration="pipeline", client=text_only,
                                   recogniser=rec, matcher=cmd.StubMatcher())
    assert piped.transcript == case.text and len(text_only.requests) == 1


def test_matchers_fail_wrong_or_malformed_calls():
    case = cmd.entity_commands(8)[2]
    wrong = [{"name": "add_calendar_event", "arguments": {"title": "x", "date": "2027-01-01"}}]
    bad = [{"name": "add_calendar_event", "arguments": None}]
    for m in (cmd.ToolbenchMatcher(), cmd.StubMatcher()):
        assert not m.match(case.expect, wrong, today=date(2027, 3, 1)).passed
        assert not m.match(case.expect, bad, today=date(2027, 3, 1)).passed
        assert not m.match(case.expect, [], today=date(2027, 3, 1)).passed


def test_entity_accuracy_canonicalises():
    case = cmd.CommandCase("t", "x", "x", [{"tool": "add_reminder", "args": {}}],
                           [cmd.Entity("date", "2027-03-09", "date"),
                            cmd.Entity("time", "16:30", "time"),
                            cmd.Entity("money", "12.50 USD", "amount"),
                            cmd.Entity("phone", "4155550142", "phone")])
    calls = [{"name": "add_reminder", "arguments": {"date": "March 9, 2027", "time": "4:30 PM",
                                                    "amount": 12.5, "phone": "+1 415-555-0142"}}]
    right, total, _ = cmd.entity_accuracy(case, calls)
    assert (right, total) == (4, 4)
    calls[0]["arguments"]["time"] = "16:45"
    assert cmd.entity_accuracy(case, calls)[0] == 3


class _T:
    def __init__(self, ttft, total, done=None):
        self.ttft_s, self.total_s, self.tool_call_done_s = ttft, total, done


class _R:
    def __init__(self, ttft, total, done=None):
        self.timing = _T(ttft, total, done)
        self.meta = {}


def test_latency_definitions():
    lat = transcript_latency(_R(0.4, 0.9), 5.0)
    assert lat.ttft_audio_s == 0.4
    assert lat.time_to_final_s == pytest.approx(1.4)
    assert lat.rtf == pytest.approx(0.18) and lat.rtfx == pytest.approx(1 / 0.18)
    assert audio_tokens_per_second(173, 48, 5.0) == pytest.approx(25.0)
    two, src = spoken_command_latency([_R(0.4, 0.9), _R(0.3, 1.2, 1.0)], "two_pass")
    assert two == pytest.approx(0.5 + 0.9 + 1.0) and src == "adapter"
    nat, src = spoken_command_latency([_R(0.3, 1.2)], "native")
    assert nat == pytest.approx(1.7) and src == "total_s_fallback"
    assert user_perceived_latency(2.4, 1.3) == pytest.approx(3.7)
    assert cold_penalty(2.0, [0.5, 0.6, 0.7]) == pytest.approx(1.4)


def test_reference_endpointer_waits_for_the_pause(engine):
    clip = cmd.render_clip(cmd.entity_commands(8)[4], engine, "stub-02")
    noisy = mix_at_snr(clip, generate_noise("white", clip.duration_s, seed=2), 20, label="white")
    delay = reference_endpoint_delay(clip)
    assert delay is not None and 1.3 - 1e-9 <= delay <= 1.34
    # In noise the word's quiet tail falls under the threshold, so the turn ends a little sooner.
    delay = reference_endpoint_delay(noisy)
    assert delay is not None and 1.15 <= delay <= 1.34
