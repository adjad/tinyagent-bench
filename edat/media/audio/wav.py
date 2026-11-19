"""16 kHz mono 16-bit PCM WAV in and out, and resampling to it (spec 05 §1.2, §10.1).

Clips are float32 NumPy arrays in [-1, 1] at :data:`SAMPLE_RATE` while they are
mixed, and 16-bit PCM WAV bytes when sent to a model (base64 ``input_audio``).
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16_000


@dataclass
class Clip:
    samples: np.ndarray                     # float32, mono, [-1, 1]
    sample_rate: int = SAMPLE_RATE
    speech_start_s: float | None = None     # first speech sample, when known
    eos_s: float | None = None              # end of speech: the last speech sample
    meta: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate

    def copy(self, samples: np.ndarray | None = None, **meta) -> "Clip":
        return Clip(self.samples.copy() if samples is None else samples, self.sample_rate,
                    self.speech_start_s, self.eos_s, {**self.meta, **meta})


def silence(seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    return np.zeros(int(round(seconds * sample_rate)), dtype=np.float32)


def to_wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    pcm = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    pcm16 = np.round(pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Read PCM WAV bytes (8/16/32-bit, any channel count) as mono float32 and its rate."""
    with wave.open(io.BytesIO(data), "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x.astype(np.float32), rate


def resample(x: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE, taps: int = 64) -> np.ndarray:
    """Band-limited resampling with a Hann-windowed sinc kernel (deterministic, NumPy only)."""
    if src_rate == dst_rate:
        return np.asarray(x, dtype=np.float32)
    x = np.asarray(x, dtype=np.float64)
    ratio = dst_rate / src_rate
    cutoff = min(1.0, ratio)                       # low-pass at the lower Nyquist
    n_out = int(np.floor(len(x) * ratio))
    t = np.arange(n_out) / ratio                   # output sample positions in input samples
    half = taps // 2
    out = np.zeros(n_out)
    base = np.floor(t).astype(int)
    for k in range(-half + 1, half + 1):
        idx = base + k
        d = t - idx
        w = cutoff * np.sinc(cutoff * d) * (0.5 + 0.5 * np.cos(np.pi * d / half))
        valid = (idx >= 0) & (idx < len(x))
        out[valid] += x[idx[valid]] * w[valid]
    return out.astype(np.float32)


def rms(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


def dbfs(x: np.ndarray) -> float:
    r = rms(x)
    return 20 * np.log10(r) if r > 0 else float("-inf")
