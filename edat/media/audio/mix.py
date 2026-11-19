"""Noise, room and overlap conditions for spoken clips (spec 05 §5 A4, A5).

* **Noise ladder:** speech mixed with noise at SNR 20, 10, 5 and 0 dB, where SNR
  is measured over the speech span only (speech start to end of speech).
* **Far-field:** speech convolved with a room impulse response at 1, 3 and 5 m.
  Version 1 ships a *simulated* response (direct path plus an exponentially
  decaying diffuse tail, RT60 0.5 s by default); measured responses come from a
  redistributable set once its licence is confirmed (dataset manifests).
* **Overlap:** a second voice at signal-to-interference 10, 5 and 0 dB.

Generated noise types need no download: white, pink, brown, fan (low-passed
noise with a hum), keyboard (seeded clicks) and babble (several stub voices).
Recorded noise types (cafe, street, TV speech, music) come from a redistributable
set once confirmed. Every condition returns a description that is stored with
the clip.
"""
from __future__ import annotations

import numpy as np

from edat.media.audio.wav import SAMPLE_RATE, Clip, rms

SNR_LADDER_DB = (20, 10, 5, 0)
DISTANCES_M = (1, 3, 5)
SIR_LADDER_DB = (10, 5, 0)
GENERATED_NOISES = ("white", "pink", "brown", "fan", "keyboard", "babble")
RECORDED_NOISES = ("cafe", "street", "tv_speech", "music")      # need a confirmed dataset
SPEED_OF_SOUND = 343.0


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def generate_noise(kind: str, seconds: float, *, seed: int, sample_rate: int = SAMPLE_RATE,
                   babble_source=None) -> np.ndarray:
    """A generated noise signal at about -20 dBFS RMS."""
    n = int(round(seconds * sample_rate))
    g = _rng(seed)
    if kind == "white":
        x = g.standard_normal(n)
    elif kind in ("pink", "brown"):
        w = g.standard_normal(n)
        spec = np.fft.rfft(w)
        f = np.fft.rfftfreq(n, 1 / sample_rate)
        f[0] = f[1] if n > 1 else 1.0
        spec = spec / (np.sqrt(f) if kind == "pink" else f)
        x = np.fft.irfft(spec, n)
    elif kind == "fan":
        w = g.standard_normal(n)
        spec = np.fft.rfft(w)
        f = np.fft.rfftfreq(n, 1 / sample_rate)
        spec[f > 1200] *= 0.05
        x = np.fft.irfft(spec, n)
        t = np.arange(n) / sample_rate
        x = x / (np.std(x) + 1e-12) + 0.4 * np.sin(2 * np.pi * 120 * t)
    elif kind == "keyboard":
        x = 0.02 * g.standard_normal(n)
        t = 0
        click = np.exp(-np.arange(int(0.012 * sample_rate)) / (0.002 * sample_rate))
        while t < n:
            t += int(g.uniform(0.06, 0.35) * sample_rate)
            if t >= n:
                break
            c = click * g.standard_normal(len(click)) * g.uniform(0.6, 1.0)
            end = min(n, t + len(c))
            x[t:end] += c[: end - t]
    elif kind == "babble":
        if babble_source is None:
            raise ValueError("babble needs babble_source(seconds, seed) -> samples")
        x = np.zeros(n)
        for k in range(4):
            s = np.asarray(babble_source(seconds, seed + k), dtype=np.float64)[:n]
            x[: len(s)] += s
    else:
        raise ValueError(f"unknown generated noise {kind!r}; recorded noises need a dataset")
    x = np.asarray(x, dtype=np.float64)
    r = np.sqrt(np.mean(x * x)) or 1.0
    return (x / r * 0.1).astype(np.float32)


def speech_span(clip: Clip) -> slice:
    sr = clip.sample_rate
    a = int(round((clip.speech_start_s or 0.0) * sr))
    b = int(round((clip.eos_s if clip.eos_s is not None else clip.duration_s) * sr))
    return slice(a, max(a + 1, b))


def mix_at_snr(clip: Clip, noise: np.ndarray, snr_db: float, *, label: str = "") -> Clip:
    """Add ``noise`` (looped or cut to length) so speech power / noise power = ``snr_db``."""
    x = clip.samples.astype(np.float64)
    nz = np.resize(np.asarray(noise, dtype=np.float64), len(x))
    span = speech_span(clip)
    ps = rms(x[span]) ** 2
    pn = rms(nz[span]) ** 2
    if ps == 0 or pn == 0:
        raise ValueError("speech or noise is silent")
    gain = np.sqrt(ps / (pn * 10 ** (snr_db / 10)))
    y = x + gain * nz
    y, peak_scale = _guard_peak(y)
    return clip.copy(y.astype(np.float32), condition={"type": "noise", "noise": label,
                                                      "snr_db": snr_db, "peak_scale": peak_scale})


def measured_snr_db(clean: Clip, mixed: Clip) -> float:
    """SNR over the speech span, from a clean clip and its mix (both at the same scale)."""
    scale = mixed.meta.get("condition", {}).get("peak_scale", 1.0)
    span = speech_span(clean)
    s = clean.samples[span].astype(np.float64) * scale
    n = mixed.samples[span].astype(np.float64) - s
    return 10 * np.log10(np.mean(s * s) / np.mean(n * n))


def _guard_peak(y: np.ndarray) -> tuple[np.ndarray, float]:
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0.99:
        s = 0.99 / peak
        return y * s, s
    return y, 1.0


def simulated_rir(distance_m: float, *, rt60_s: float = 0.5, seed: int = 0,
                  sample_rate: int = SAMPLE_RATE, critical_distance_m: float = 1.2) -> np.ndarray:
    """A simulated room impulse response: direct path at 1/r plus a diffuse exponential tail.

    The tail's energy is fixed and the direct path falls as 1/r, so the
    direct-to-reverberant ratio is 0 dB at the critical distance and falls 6 dB
    per doubling beyond it.
    """
    g = _rng(seed)
    length = int(rt60_s * sample_rate)
    delay = int(round(distance_m / SPEED_OF_SOUND * sample_rate))
    h = np.zeros(delay + length, dtype=np.float64)
    h[delay] = 1.0 / max(distance_m, 0.1)
    t = np.arange(length) / sample_rate
    tail = g.standard_normal(length) * np.exp(-6.9078 * t / rt60_s)   # -60 dB at rt60
    tail[: int(0.002 * sample_rate)] = 0.0
    e_direct_at_dc = (1.0 / critical_distance_m) ** 2
    tail *= np.sqrt(e_direct_at_dc / np.sum(tail * tail))
    h[delay:] += tail
    return h.astype(np.float32)


def direct_to_reverberant_db(h: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    k = int(np.argmax(np.abs(h)))
    w = int(0.0025 * sample_rate)
    direct = np.sum(h[max(0, k - w): k + w + 1] ** 2)
    rest = np.sum(h ** 2) - direct
    return float(10 * np.log10(direct / rest))


def apply_room(clip: Clip, distance_m: float, *, rt60_s: float = 0.5, seed: int = 0,
               rir: np.ndarray | None = None, rir_label: str = "simulated") -> Clip:
    """Convolve with a room response; keep the length and the speech timing (delay removed)."""
    h = simulated_rir(distance_m, rt60_s=rt60_s, seed=seed) if rir is None else np.asarray(rir)
    k = int(np.argmax(np.abs(h)))
    y = np.convolve(clip.samples.astype(np.float64), h.astype(np.float64))[k: k + len(clip.samples)]
    # Match the clean clip's speech level so the room changes clarity, not loudness.
    s_in = rms(clip.samples[speech_span(clip)])
    s_out = rms(y[speech_span(clip)]) or 1.0
    y = y * (s_in / s_out)
    y, peak_scale = _guard_peak(y)
    return clip.copy(y.astype(np.float32), condition={
        "type": "room", "rir": rir_label, "distance_m": distance_m, "rt60_s": rt60_s,
        "drr_db": round(direct_to_reverberant_db(h), 2), "peak_scale": peak_scale})


def overlap(target: Clip, interferer: Clip, sir_db: float, *, offset_s: float = 0.2) -> Clip:
    """Add a second voice starting ``offset_s`` into the target's speech, at ``sir_db``."""
    x = target.samples.astype(np.float64)
    start = int(round(((target.speech_start_s or 0.0) + offset_s) * target.sample_rate))
    i = np.zeros(len(x))
    src = interferer.samples.astype(np.float64)
    seg = src[: max(0, len(x) - start)]
    i[start: start + len(seg)] = seg
    span = speech_span(target)
    ps = rms(x[span]) ** 2
    pi = rms(i[span]) ** 2
    if pi == 0:
        raise ValueError("the interferer does not overlap the target's speech")
    gain = np.sqrt(ps / (pi * 10 ** (sir_db / 10)))
    y, peak_scale = _guard_peak(x + gain * i)
    return target.copy(y.astype(np.float32), condition={
        "type": "overlap", "sir_db": sir_db, "offset_s": offset_s,
        "interferer_text": interferer.meta.get("text"), "peak_scale": peak_scale})


def condition_grid() -> list[dict]:
    """Every A4 condition in version 1, in a fixed order."""
    out: list[dict] = [{"type": "clean"}]
    for kind in GENERATED_NOISES:
        for snr in SNR_LADDER_DB:
            out.append({"type": "noise", "noise": kind, "snr_db": snr})
    for d in DISTANCES_M:
        out.append({"type": "room", "distance_m": d, "rir": "simulated"})
    for sir in SIR_LADDER_DB:
        out.append({"type": "overlap", "sir_db": sir})
    return out
