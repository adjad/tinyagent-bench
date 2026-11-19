"""The speech-engine seam: the approved voice plan and the Kokoro and Piper adapters.

No engine package or weight is needed: fake ``kokoro`` and ``piper`` modules
stand in, and the real adapters are checked to refuse rather than download.
"""
from __future__ import annotations

import sys
import types

import pytest

np = pytest.importorskip("numpy")

from edat.media.audio import tts  # noqa: E402
from edat.media.audio.wav import SAMPLE_RATE  # noqa: E402


def test_voice_plan_is_twelve_approved_voices():
    plan = tts.voice_plan()
    assert len(plan) == 12
    assert {v.engine for v in plan} == {"kokoro-82m", "piper"}
    assert sum(v.engine == "piper" for v in plan) == 1
    assert all(v.redistributable for v in plan)
    assert {v.gender for v in plan} == {"female", "male"}
    approved = {c.engine for c in tts.CANDIDATES if c.approved}
    assert approved == {"Kokoro-82M", "Piper"}
    for c in tts.CANDIDATES:
        if c.engine.startswith(("Coqui", "F5", "macOS")):
            assert not c.approved and c.output_redistributable == "no"


def test_real_engines_refuse_without_local_weights(monkeypatch):
    monkeypatch.delenv("EDAT_KOKORO_DIR", raising=False)
    monkeypatch.delenv("EDAT_PIPER_DIR", raising=False)
    for name in ("kokoro", "piper"):
        with pytest.raises(tts.EngineUnavailable):
            tts.get_engine(name)
    with pytest.raises(LookupError):
        tts.get_engine("xtts")


def _tone(sr, seconds, f=220.0):
    t = np.arange(int(sr * seconds)) / sr
    return (0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32)


def test_kokoro_adapter_with_a_fake_package(tmp_path, monkeypatch):
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "bf_emma.pt").write_bytes(b"x")
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "kokoro-v1_0.pth").write_bytes(b"x")
    calls = {}

    class KModel:
        def __init__(self, **kw):
            calls["model"] = kw

        def eval(self):
            return self

    class Tok:
        def __init__(self, text, a, b):
            self.text, self.start_ts, self.end_ts = text, a, b

    class Result:
        def __init__(self, audio):
            self.audio = audio
            self.tokens = [Tok("hello", 0.1, 0.5), Tok("there", 0.6, 1.0)]

    class KPipeline:
        def __init__(self, lang_code, repo_id, model):
            calls["lang"] = lang_code

        def __call__(self, text, voice, speed):
            calls["speed"] = speed
            yield Result(_tone(24000, 1.0 / speed))     # a real engine speaks slower

    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KModel=KModel,
                                                                      KPipeline=KPipeline))
    eng = tts.get_engine("kokoro", folder=tmp_path)
    clip = eng.synthesize("hello there", "bf_emma")
    assert calls["lang"] == "b" and calls["speed"] == 1.0
    assert clip.sample_rate == SAMPLE_RATE
    assert clip.meta["placeholder"] is False and clip.meta["engine"] == "kokoro-82m"
    assert clip.eos_s == pytest.approx(tts.LEAD_IN_S + 1.0, abs=0.02)
    assert clip.meta["word_timings"][0]["start_s"] == pytest.approx(tts.LEAD_IN_S + 0.1, abs=1e-3)
    up = eng.synthesize("hello there", "bf_emma", pitch_semitones=12)
    assert calls["speed"] == pytest.approx(0.5)              # spoken slower, then shifted up
    assert up.eos_s == pytest.approx(clip.eos_s, abs=0.02)   # same duration, higher pitch
    with pytest.raises(KeyError):
        eng.synthesize("x", "af_missing")


def test_piper_adapter_with_a_fake_package(tmp_path, monkeypatch):
    (tmp_path / "en_GB-cori-high.onnx").write_bytes(b"x")
    (tmp_path / "en_GB-cori-high.onnx.json").write_text("{}")
    seen = {}

    class Chunk:
        def __init__(self, a):
            self.audio_float_array = a

    class SynthesisConfig:
        def __init__(self, length_scale):
            seen["length_scale"] = length_scale

    class PiperVoice:
        config = types.SimpleNamespace(sample_rate=22050)

        @classmethod
        def load(cls, path, config_path):
            seen["path"] = path
            return cls()

        def synthesize(self, text, syn_config):
            yield Chunk(_tone(22050, 0.8))

    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(
        PiperVoice=PiperVoice, SynthesisConfig=SynthesisConfig))
    eng = tts.get_engine("piper", folder=tmp_path)
    clip = eng.synthesize("call the office", "en_GB-cori-high", rate=1.25)
    assert seen["length_scale"] == pytest.approx(0.8)
    assert clip.eos_s == pytest.approx(tts.LEAD_IN_S + 0.8, abs=0.02)
    assert clip.meta["word_timings"] is None
