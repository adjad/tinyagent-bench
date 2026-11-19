"""Speech engines: a pluggable seam, a stub that writes placeholder audio, and the candidates.

Version 1 renders spoken commands with synthetic speech (spec 05 §5 A3). Which
real engines and voices may be used is **not decided** (§13.2 item 1): each
must allow redistribution of the rendered audio. Until a voice is approved,
:data:`CANDIDATES` lists them with their licences and :func:`get_engine`
refuses to build one.

:class:`StubEngine` writes **placeholder audio**, not speech: one tone burst per
word, pitched from a hash of the word and the voice, with exact word timings.
It lets the whole pipeline (noise, rooms, overlap, timing, scoring) run and be
tested with no speech engine and no download. Its clips are marked
``placeholder: true`` and must never be scored as speech.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

from edat.media.audio.wav import SAMPLE_RATE, Clip, silence

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
# hand-over). "approved" stays False until the benchmark coordinator decides.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("Piper", "MIT (archived rhasspy/piper); GPL-3.0 (active fork)",
              "per voice: each MODEL_CARD names the training data's licence",
              "clear: en_US libritts_r (CC BY 4.0), en_GB cori (CC BY 4.0), en_US joe (CC0), "
              "en_GB northern_english_male (CC BY-SA 4.0); unclear: lessac (research-only), "
              "amy, alan, jenny_dioco (attribution); avoid: ryan, hfc_female (CC BY-NC-SA)",
              "per voice", "Offline on a Mac CPU. Recommended first, with the clear voices only."),
    Candidate("Kokoro-82M", "Apache-2.0", "Apache-2.0", "built-in English voicepacks",
              "likely yes", "About 330 MB, runs on a Mac CPU. Voice provenance to confirm."),
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


def get_engine(name: str) -> SpeechEngine:
    """Build a speech engine by name. Only the stub exists until engines and voices are approved."""
    if name in ("stub", StubEngine.id):
        return StubEngine()
    known = ", ".join(c.engine for c in CANDIDATES)
    raise LookupError(f"speech engine {name!r} is not approved for EDAT yet (candidates: {known}); "
                      "the benchmark coordinator must approve an engine and its voices first")


def candidates_json() -> list[dict]:
    return [asdict(c) for c in CANDIDATES]
