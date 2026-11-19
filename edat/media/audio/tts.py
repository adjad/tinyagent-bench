"""Speech engines: a pluggable seam, a stub that writes placeholder audio, and two real adapters.

Version 1 renders spoken commands with synthetic speech (spec 05 §5 A3). The
benchmark coordinator chose **Kokoro-82M** (Apache-2.0) as the main engine and
one **Piper** voice trained on a CC BY 4.0 dataset (``en_GB-cori``) as a second
engine; macOS voices, XTTS and F5 are excluded. The 12-voice plan is in
``test_fixtures/media/tts/voices.json``.

Neither real engine downloads anything. :class:`KokoroEngine` and
:class:`PiperEngine` import their packages lazily and load weights only from a
local folder named by ``EDAT_KOKORO_DIR`` / ``EDAT_PIPER_DIR`` (filled after
the user approves the downloads in the dataset manifests), with the Hugging Face
hub forced offline. Without the folder they raise :class:`EngineUnavailable`.

:class:`StubEngine` writes **placeholder audio**, not speech: one tone burst per
word, pitched from a hash of the word and the voice, with exact word timings.
It lets the whole pipeline (noise, rooms, overlap, timing, scoring) run and be
tested with no speech engine and no download. Its clips are marked
``placeholder: true`` and must never be scored as speech.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from edat.media.audio.wav import SAMPLE_RATE, Clip, resample, silence

LEAD_IN_S = 0.30            # silence before speech starts


@dataclass(frozen=True)
class Voice:
    id: str
    engine: str
    accent: str = "unknown"          # e.g. en-US, en-GB, en-IN
    gender: str = "unknown"
    age: str = "unknown"
    licence: str = "unverified"
    redistributable: bool = False


@runtime_checkable
class SpeechEngine(Protocol):
    id: str

    def describe(self) -> dict: ...

    def voices(self) -> list[Voice]: ...

    def synthesize(self, text: str, voice: str, *, rate: float = 1.0,
                   pitch_semitones: float = 0.0) -> Clip:
        """Render ``text`` with ``voice`` as a 16 kHz mono clip whose ``eos_s`` is set.

        ``rate`` > 1 speaks faster; ``pitch_semitones`` shifts the voice (used for
        calm versus rushed delivery in A7 urgency). The clip's ``meta`` carries
        ``word_timings`` when the engine can give them.
        """
        ...


class StubEngine:
    """Placeholder audio: tone bursts per word. Deterministic; not speech."""

    id = "stub-tones/1"
    WORD_S = 0.24
    GAP_S = 0.08

    def __init__(self, n_voices: int = 12) -> None:
        self._voices = [Voice(f"stub-{i:02d}", self.id, accent=("en-US", "en-GB", "en-IN",
                                                                 "en-AU")[i % 4],
                              licence="generated (no licence needed)", redistributable=True)
                        for i in range(n_voices)]

    def describe(self) -> dict:
        return {"engine": self.id, "placeholder": True, "voices": len(self._voices),
                "licence": "generated tones, no third-party material"}

    def voices(self) -> list[Voice]:
        return list(self._voices)

    def synthesize(self, text: str, voice: str, *, rate: float = 1.0,
                   pitch_semitones: float = 0.0) -> Clip:
        if voice not in {v.id for v in self._voices}:
            raise KeyError(f"unknown stub voice {voice!r}")
        if rate <= 0:
            raise ValueError("rate must be positive")
        words = text.split()
        sr = SAMPLE_RATE
        parts = [silence(LEAD_IN_S)]
        timings = []
        t = LEAD_IN_S
        vshift = int(voice.split("-")[1])
        word_s, gap_s = self.WORD_S / rate, self.GAP_S / rate
        pitch = 2 ** (pitch_semitones / 12)
        n = int(round(word_s * sr))
        env = np.hanning(n).astype(np.float32)
        tt = np.arange(n) / sr
        for w in words:
            h = int.from_bytes(hashlib.sha256(f"{w.lower()}|{voice}".encode()).digest()[:4], "big")
            f0 = (140 + (h % 220) + 6 * vshift) * pitch
            burst = 0.35 * env * np.sin(2 * np.pi * f0 * tt).astype(np.float32)
            burst += 0.12 * env * np.sin(2 * np.pi * 2 * f0 * tt).astype(np.float32)
            parts.append(burst.astype(np.float32))
            timings.append({"word": w, "start_s": round(t, 4), "end_s": round(t + n / sr, 4)})
            t += n / sr
            g = silence(gap_s)
            parts.append(g)
            t += len(g) / sr
        audio = np.concatenate(parts) if parts else silence(0)
        eos = timings[-1]["end_s"] if timings else LEAD_IN_S
        # Drop the gap after the last word: end of speech is the end of the clip here.
        audio = audio[: int(round(eos * sr))]
        return Clip(audio.astype(np.float32), sr, speech_start_s=LEAD_IN_S if words else None,
                    eos_s=eos, meta={"engine": self.id, "voice": voice, "placeholder": True,
                                     "text": text, "word_timings": timings, "rate": rate,
                                     "pitch_semitones": pitch_semitones})


@dataclass(frozen=True)
class Candidate:
    """A real TTS engine considered for version 1. Nothing here is approved yet."""

    engine: str
    code_licence: str
    weights_licence: str
    voices: str
    output_redistributable: str          # "yes", "no", "unclear" — as far as checked
    notes: str = ""
    approved: bool = False


# From the licence check of 2026-11-18 (read from each project's own pages; see the
# hand-over). Kokoro and Piper (CC BY 4.0 voices only) were approved by the
# benchmark coordinator on 2026-11-18; the rest are kept for the record.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("Kokoro-82M", "Apache-2.0", "Apache-2.0", "built-in English voicepacks (US, UK)",
              "yes", "About 330 MB, runs on a Mac CPU. Main engine.", approved=True),
    Candidate("Piper", "MIT (archived rhasspy/piper); GPL-3.0 (active fork)",
              "per voice: each MODEL_CARD names the training data's licence",
              "clear: en_US libritts_r (CC BY 4.0), en_GB cori (CC BY 4.0), en_US joe (CC0), "
              "en_GB northern_english_male (CC BY-SA 4.0); unclear: lessac (research-only), "
              "amy, alan, jenny_dioco (attribution); avoid: ryan, hfc_female (CC BY-NC-SA)",
              "per voice", "Offline on a Mac CPU. Second engine: en_GB cori (CC BY 4.0) only.",
              approved=True),
    Candidate("Parler-TTS mini/large", "Apache-2.0", "Apache-2.0", "described speakers",
              "likely yes", "Training-data licences not opened."),
    Candidate("MeloTTS", "MIT", "MIT", "EN-US, EN-BR, EN-INDIA, EN-AU, EN-Default", "likely yes",
              "Training-data licences not checked."),
    Candidate("Festival / Flite (CMU Arctic voices)", "BSD-like", "CMU Arctic permissive notice",
              "a few US voices", "likely yes", "Old, robotic voices."),
    Candidate("StyleTTS 2", "MIT", "pretrained models require disclosing speech is synthetic",
              "LibriTTS speakers", "conditional", "Disclosure condition must be met in the README."),
    Candidate("Chatterbox", "MIT", "MIT", "zero-shot", "yes, watermarked",
              "Every output carries a PerTh watermark."),
    Candidate("Matcha-TTS (LJSpeech)", "MIT", "checkpoint licence not stated", "one US voice",
              "unclear", "LJSpeech itself is public domain."),
    Candidate("eSpeak NG", "GPL-3.0", "rule-based", "many accents", "unclear",
              "Formant voice; effect of the GPL on rendered audio not confirmed."),
    Candidate("Coqui XTTS-v2", "MPL-2.0", "Coqui Public Model Licence", "voice cloning", "no",
              "Non-commercial use of model and outputs only."),
    Candidate("F5-TTS", "MIT", "CC BY-NC 4.0", "voice cloning", "no", "Non-commercial weights."),
    Candidate("macOS say voices", "proprietary", "proprietary", "system voices", "no",
              "Licence text not opened; treated as not redistributable."),
)


VOICE_PLAN = Path(__file__).resolve().parents[3] / "test_fixtures" / "media" / "tts" / "voices.json"


class EngineUnavailable(LookupError):
    """The engine's package or local weights are missing; nothing is downloaded to fix it."""


def voice_plan() -> list[Voice]:
    """The approved 12-voice plan (spec 05 §5 A3 voice variance)."""
    data = json.loads(VOICE_PLAN.read_text())
    return [Voice(v["id"], v["engine"], v["accent"], v["gender"], v.get("age", "adult"),
                  v["licence"], v["redistributable"]) for v in data["voices"]]


def _local_dir(env: str) -> Path:
    path = os.environ.get(env)
    if not path or not Path(path).is_dir():
        raise EngineUnavailable(f"set {env} to a local folder holding the approved weights "
                                "(see test_fixtures/media/datasets); nothing is downloaded")
    os.environ["HF_HUB_OFFLINE"] = "1"              # the hub must never be asked
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return Path(path)


def _shape_clip(audio: np.ndarray, native_sr: int, *, rate: float, pitch: float, text: str,
                voice: str, engine: str, timings: list[dict] | None) -> Clip:
    """Resample to 16 kHz, applying the pitch shift (synthesis ran at ``rate / pitch``)."""
    src = int(round(native_sr * pitch))
    out = resample(audio.astype(np.float32), src, SAMPLE_RATE)
    lead = silence(LEAD_IN_S)
    out = np.concatenate([lead, out]).astype(np.float32)
    level = np.abs(out)
    thresh = max(1e-4, 0.02 * float(level.max(initial=0.0)))
    voiced = np.nonzero(level > thresh)[0]
    eos = (voiced[-1] + 1) / SAMPLE_RATE if len(voiced) else LEAD_IN_S
    start = voiced[0] / SAMPLE_RATE if len(voiced) else None
    if timings:
        k = native_sr / src
        timings = [{"word": t["word"], "start_s": round(LEAD_IN_S + t["start_s"] * k, 4),
                    "end_s": round(LEAD_IN_S + t["end_s"] * k, 4)} for t in timings]
    return Clip(out[: int(round(eos * SAMPLE_RATE))], SAMPLE_RATE, speech_start_s=start,
                eos_s=round(eos, 4), meta={"engine": engine, "voice": voice, "placeholder": False,
                                           "text": text, "word_timings": timings, "rate": rate,
                                           "pitch_semitones": float(np.log2(pitch) * 12)})


class KokoroEngine:
    """Kokoro-82M through the ``kokoro`` package, from local weights only (24 kHz output)."""

    id = "kokoro-82m"
    NATIVE_SR = 24000

    def __init__(self, folder: str | Path | None = None) -> None:
        self.dir = Path(folder) if folder else _local_dir("EDAT_KOKORO_DIR")
        try:
            from kokoro import KModel, KPipeline  # noqa: F401
        except ImportError as e:
            raise EngineUnavailable("the kokoro package is not installed") from e
        self._pipes: dict[str, object] = {}
        self._model = None

    def describe(self) -> dict:
        return {"engine": self.id, "placeholder": False, "licence": "Apache-2.0",
                "weights": str(self.dir.name)}

    def voices(self) -> list[Voice]:
        return [v for v in voice_plan() if v.engine == self.id]

    def _pipe(self, voice: str):
        from kokoro import KModel, KPipeline
        if self._model is None:
            self._model = KModel(repo_id="hexgrad/Kokoro-82M", config=str(self.dir / "config.json"),
                                 model=str(next(self.dir.glob("kokoro-v1_0.pth")))).eval()
        lang = voice[0]                                  # a = US English, b = UK English
        if lang not in self._pipes:
            self._pipes[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M",
                                          model=self._model)
        return self._pipes[lang]

    def synthesize(self, text: str, voice: str, *, rate: float = 1.0,
                   pitch_semitones: float = 0.0) -> Clip:
        path = self.dir / "voices" / f"{voice}.pt"
        if not path.exists():
            raise KeyError(f"voice {voice!r} is not in {self.dir}/voices")
        pitch = 2 ** (pitch_semitones / 12)
        chunks, timings, offset = [], [], 0.0
        for result in self._pipe(voice)(text, voice=str(path), speed=rate / pitch):
            audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
            for tok in getattr(result, "tokens", None) or []:
                if getattr(tok, "start_ts", None) is not None and tok.text.strip():
                    timings.append({"word": tok.text, "start_s": offset + tok.start_ts,
                                    "end_s": offset + (tok.end_ts or tok.start_ts)})
            chunks.append(audio)
            offset += len(audio) / self.NATIVE_SR
        audio = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
        return _shape_clip(audio, self.NATIVE_SR, rate=rate, pitch=pitch, text=text, voice=voice,
                           engine=self.id, timings=timings or None)


class PiperEngine:
    """Piper voices through ``piper-tts`` (ONNX), from local ``.onnx`` + ``.onnx.json`` only."""

    id = "piper"

    def __init__(self, folder: str | Path | None = None) -> None:
        self.dir = Path(folder) if folder else _local_dir("EDAT_PIPER_DIR")
        try:
            from piper import PiperVoice  # noqa: F401
        except ImportError as e:
            raise EngineUnavailable("the piper-tts package is not installed") from e
        self._loaded: dict[str, object] = {}

    def describe(self) -> dict:
        return {"engine": self.id, "placeholder": False, "licence": "per voice (CC BY 4.0 used)"}

    def voices(self) -> list[Voice]:
        return [v for v in voice_plan() if v.engine == self.id]

    def synthesize(self, text: str, voice: str, *, rate: float = 1.0,
                   pitch_semitones: float = 0.0) -> Clip:
        from piper import PiperVoice
        if voice not in self._loaded:
            onnx = self.dir / f"{voice}.onnx"
            if not onnx.exists():
                raise KeyError(f"voice {voice!r} is not in {self.dir}")
            self._loaded[voice] = PiperVoice.load(str(onnx), config_path=str(onnx) + ".json")
        pv = self._loaded[voice]
        pitch = 2 ** (pitch_semitones / 12)
        length_scale = pitch / rate                      # >1 is slower
        sr = pv.config.sample_rate
        try:                                             # piper-tts >= 1.3
            from piper import SynthesisConfig
            chunks = [np.asarray(c.audio_float_array, dtype=np.float32)
                      for c in pv.synthesize(text, syn_config=SynthesisConfig(
                          length_scale=length_scale))]
        except ImportError:                              # piper-tts 1.2
            raw = b"".join(pv.synthesize_stream_raw(text, length_scale=length_scale))
            chunks = [np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0]
        audio = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
        return _shape_clip(audio, sr, rate=rate, pitch=pitch, text=text, voice=voice,
                           engine=self.id, timings=None)


def get_engine(name: str, **kw) -> SpeechEngine:
    """Build a speech engine by name: ``stub``, ``kokoro`` or ``piper``.

    The real engines need their package and a local weights folder; they raise
    :class:`EngineUnavailable` rather than download anything.
    """
    if name in ("stub", StubEngine.id):
        return StubEngine()
    if name in ("kokoro", KokoroEngine.id):
        return KokoroEngine(**kw)
    if name in ("piper", PiperEngine.id):
        return PiperEngine(**kw)
    known = ", ".join(c.engine for c in CANDIDATES if not c.approved)
    raise LookupError(f"speech engine {name!r} is not approved for tinyagent-bench "
                      f"(approved: stub, kokoro, piper; not approved: {known})")


def candidates_json() -> list[dict]:
    return [asdict(c) for c in CANDIDATES]
