"""Noise, room and overlap conditions for spoken clips (spec 05 §5 A4, A5).

* **Noise ladder:** speech mixed with noise at SNR 20, 10, 5 and 0 dB, where SNR
  is measured over the speech span only (speech start to end of speech).
* **Far-field:** speech convolved with a room impulse response at 1, 3 and 5 m.
  Version 1 ships a *simulated* response (direct path plus an exponentially
  decaying diffuse tail, RT60 0.5 s by default); measured responses come from a
  redistributable set once its licence is confirmed (dataset manifests).
* **Overlap:** a second voice at signal-to-interference 10, 5 and 0 dB.

Version 2 (spec 05 §3A, A4) adds, below the v1 functions: stand-in generators for
cafe, street, TV speech and music; an optional recorded-noise hook with per-file
licences; image-source room impulse responses for three fixed rooms; low-volume,
clipping, truncation and narrowband conditions; the 19-condition A4 grid and its
seeded plan; and the A4 scorers. ``condition_grid()`` keeps its v1 meaning unless
called with ``a4=True``.

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


def direct_index(distance_m: float, sample_rate: int = SAMPLE_RATE) -> int:
    """Sample index of the direct sound in a response for a source ``distance_m`` away."""
    return int(round(distance_m / SPEED_OF_SOUND * sample_rate))


def direct_to_reverberant_db(h: np.ndarray, sample_rate: int = SAMPLE_RATE,
                             window_s: float = 0.0025, direct_at: int | None = None) -> float:
    """Energy within ``window_s`` either side of the strongest tap over all the rest, in dB.

    Image-source responses use ``window_s=0.001`` and ``direct_at=direct_index(d)``: their
    strongest tap is often a reflection, and a floor reflection at 5 m arrives under 2 ms
    after the direct sound.
    """
    k = int(np.argmax(np.abs(h))) if direct_at is None else direct_at
    w = int(window_s * sample_rate)
    direct = np.sum(h[max(0, k - w): k + w + 1] ** 2)
    rest = np.sum(h ** 2) - direct
    return float(10 * np.log10(direct / rest))


def apply_room(clip: Clip, distance_m: float, *, rt60_s: float = 0.5, seed: int = 0,
               rir: np.ndarray | None = None, rir_label: str = "simulated",
               room: "str | Room | None" = None) -> Clip:
    """Convolve with a room response; keep the length and the speech timing (delay removed).

    With ``room`` (a name from :data:`ROOMS` or a :class:`Room`) the response is the
    image-source one from :func:`room_rir`; otherwise the v1 simulated response.
    """
    extra: dict = {}
    if room is not None:
        r = ROOMS[room] if isinstance(room, str) else room
        h = room_rir(r, distance_m, seed=seed)
        rir_label = "image_source"
        rt60_s = r.rt60_s
        extra = {"room": room_description(r, distance_m)}
    else:
        h = simulated_rir(distance_m, rt60_s=rt60_s, seed=seed) if rir is None else np.asarray(rir)
    k = direct_index(distance_m) if room is not None else int(np.argmax(np.abs(h)))
    y = np.convolve(clip.samples.astype(np.float64), h.astype(np.float64))[k: k + len(clip.samples)]
    # Match the clean clip's speech level so the room changes clarity, not loudness.
    s_in = rms(clip.samples[speech_span(clip)])
    s_out = rms(y[speech_span(clip)]) or 1.0
    y = y * (s_in / s_out)
    y, peak_scale = _guard_peak(y)
    return clip.copy(y.astype(np.float32), condition={
        "type": "room", "rir": rir_label, "distance_m": distance_m, "rt60_s": rt60_s,
        "drr_db": round(direct_to_reverberant_db(h, window_s=0.001, direct_at=k) if room is not None
                              else direct_to_reverberant_db(h), 2),
        "peak_scale": peak_scale, **extra})


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


def condition_grid(a4: bool = False) -> list[dict]:
    """Every A4 condition in version 1, in a fixed order.

    ``a4=True`` returns the spec 05 §3A grid instead (:func:`a4_condition_grid`).
    """
    if a4:
        return a4_condition_grid()
    out: list[dict] = [{"type": "clean"}]
    for kind in GENERATED_NOISES:
        for snr in SNR_LADDER_DB:
            out.append({"type": "noise", "noise": kind, "snr_db": snr})
    for d in DISTANCES_M:
        out.append({"type": "room", "distance_m": d, "rir": "simulated"})
    for sir in SIR_LADDER_DB:
        out.append({"type": "overlap", "sir_db": sir})
    return out


# =========================================================================== A4 (v2)
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

A4_NOISE_TYPES = ("cafe", "street", "keyboard", "fan", "tv_speech", "music")
A4_SNR_DB = (20, 10, 5, 0)
A4_LOW_VOLUME_DBFS = (-30, -40, -50)
A4_OVERDRIVE_DB = (6, 12)
A4_TRUNCATION_KINDS = ("head_cut", "tail_cut", "dropouts")
TRUNCATION_CUT_S = 0.3
DROPOUT_S = 0.2
DROPOUT_COUNT = 3
AUDIBLE_FRACTION = 0.5
GENERATED_STAND_INS = "generated"


def derive_seed(seed: int, *parts) -> int:
    """A stable 32-bit seed from a base seed and any labels (sha256, platform independent)."""
    h = hashlib.sha256(("|".join(str(p) for p in (seed, *parts))).encode()).digest()
    return int.from_bytes(h[:4], "big")


# ------------------------------------------------------------ generated noise stand-ins

def _filter(x: np.ndarray, lo: float, hi: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Zero-phase band-pass by FFT masking (``lo`` 0 = low-pass, ``hi`` >= Nyquist = high-pass)."""
    n = len(x)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / sr)
    spec[(f < lo) | (f > hi)] = 0.0
    return np.fft.irfft(spec, n)


def _unit_rms(x: np.ndarray, rms_out: float = 0.1) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    r = np.sqrt(np.mean(x * x)) or 1.0
    return (x / r * rms_out).astype(np.float32)


def _slow_envelope(n: int, g: np.random.Generator, cutoff_hz: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    e = _filter(g.standard_normal(n), 0.0, cutoff_hz, sr)
    e = e / (np.std(e) + 1e-12)
    return np.maximum(0.0, e + 0.3) ** 2


def _babble_stand_in(seconds: float, seed: int, voices: int = 6, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Babble without any speech engine: drifting harmonic voices with syllable-rate envelopes."""
    n = int(round(seconds * sr))
    g = _rng(seed)
    t_sum = np.zeros(n)
    for _ in range(voices):
        f0 = g.uniform(95, 240) * (1 + 0.08 * _filter(g.standard_normal(n), 0.0, 3.0, sr)
                                    / 0.3 * 0.1)
        phase = np.cumsum(2 * np.pi * f0 / sr)
        v = np.zeros(n)
        for k in range(1, 13):
            v += np.sin(k * phase + g.uniform(0, 6.28)) / k
        v += 0.15 * g.standard_normal(n)
        lo, hi = g.uniform(250, 500), g.uniform(2200, 3400)
        v = _filter(v, lo, hi, sr)
        t_sum += v * _slow_envelope(n, g, g.uniform(3.0, 6.0), sr)
    return t_sum


def _clatter(n: int, g: np.random.Generator, per_s: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    x = np.zeros(n)
    t = 0.0
    while True:
        t += g.exponential(1 / per_s)
        i = int(t * sr)
        if i >= n:
            break
        ln = int(g.uniform(0.015, 0.05) * sr)
        tt = np.arange(ln) / sr
        f1, f2 = g.uniform(1800, 4200), g.uniform(3500, 7000)
        ev = (np.sin(2 * np.pi * f1 * tt) + 0.6 * np.sin(2 * np.pi * f2 * tt)) * np.exp(-tt / g.uniform(0.004, 0.012))
        ev = ev * g.uniform(0.2, 1.0)
        end = min(n, i + ln)
        x[i:end] += ev[: end - i]
    return x


def _street(n: int, g: np.random.Generator, sr: int = SAMPLE_RATE) -> np.ndarray:
    seconds = n / sr
    brown = generate_noise("brown", seconds, seed=int(g.integers(1 << 30)), sample_rate=sr).astype(np.float64)
    rumble = _filter(brown, 0.0, 400.0, sr)
    rumble = rumble / (np.std(rumble) + 1e-12)
    out = rumble.copy()
    t = g.uniform(0.5, 3.0)
    tt = np.arange(n) / sr
    while t < seconds + 1.0:
        w = _filter(g.standard_normal(n), 200.0, 2500.0, sr)
        w = w / (np.std(w) + 1e-12)
        sigma = g.uniform(0.4, 1.0)
        out += g.uniform(0.7, 1.4) * w * np.exp(-0.5 * ((tt - t) / sigma) ** 2)
        t += g.uniform(3.0, 8.0)
    return out


def _music(n: int, g: np.random.Generator, sr: int = SAMPLE_RATE) -> np.ndarray:
    out = np.zeros(n)
    chord_s = g.choice([1.5, 2.0, 2.4])
    beat_s = chord_s / 4
    scale = np.array([0, 2, 4, 5, 7, 9, 11])
    root = int(g.integers(45, 57))
    t = 0.0
    while t * sr < n:
        deg = int(g.integers(0, 7))
        notes = [root + int(scale[(deg + k) % 7]) + 12 * ((deg + k) // 7) for k in (0, 2, 4)]
        i = int(t * sr)
        ln = int(chord_s * sr)
        tt = np.arange(ln) / sr
        env = np.minimum(1, tt / 0.03) * np.exp(-tt / (chord_s * 0.8))
        for m in notes:
            f = 440.0 * 2 ** ((m - 69) / 12)
            note = np.sin(2 * np.pi * f * tt) + 0.5 * np.sin(2 * np.pi * 2 * f * tt) \
                + 0.25 * np.sin(2 * np.pi * 3 * f * tt)
            end = min(n, i + ln)
            out[i:end] += (0.3 * note * env)[: end - i]
        for b in range(4):
            j = int((t + b * beat_s) * sr)
            if j >= n:
                break
            kl = int(0.18 * sr)
            kt = np.arange(kl) / sr
            kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-kt / 0.03)) * kt) * np.exp(-kt / 0.07)
            end = min(n, j + kl)
            out[j:end] += (0.9 * kick)[: end - j] if b % 2 == 0 else 0
            hl = int(0.04 * sr)
            hat = _filter(g.standard_normal(hl), 5000.0, 8000.0, sr) * np.exp(-np.arange(hl) / (0.01 * sr))
            h0 = j + int(beat_s * sr / 2)
            end2 = min(n, h0 + hl)
            if h0 < n:
                out[h0:end2] += (0.5 * hat / (np.std(hat) + 1e-12) * 0.3)[: end2 - h0]
        t += chord_s
    return out


def generate_a4_noise(kind: str, seconds: float, *, seed: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generated noise for any of the six A4 types (and the v1 types except babble) at -20 dBFS RMS.

    cafe = babble plus clatter; street = brown rumble plus passing whooshes; TV speech =
    band-limited babble (300-3400 Hz); music = seeded chords and percussion; keyboard
    and fan are the v1 generators. These are stand-ins: no recorded material is used.
    """
    sr = sample_rate
    n = int(round(seconds * sr))
    g = _rng(seed)
    if kind in ("keyboard", "fan", "white", "pink", "brown"):
        return generate_noise(kind, seconds, seed=seed, sample_rate=sr)
    if kind == "cafe":
        b = _babble_stand_in(seconds, seed, voices=7, sr=sr)
        b = b / (np.std(b) + 1e-12)
        c = _clatter(n, g, per_s=2.5, sr=sr)
        c = c / (np.std(c) + 1e-12)
        x = b + 0.5 * c
    elif kind == "street":
        x = _street(n, g, sr)
    elif kind == "tv_speech":
        x = _filter(_babble_stand_in(seconds, seed, voices=2, sr=sr), 300.0, 3400.0, sr)
    elif kind == "music":
        x = _music(n, g, sr)
    else:
        raise ValueError(f"unknown A4 noise type {kind!r}")
    return _unit_rms(x)


# ------------------------------------------------------------ recorded-noise hook

_ALLOWED_LICENCE = re.compile(r"^(cc0|cc[- ]by)(?![- ](nc|nd|sa))", re.I)


def _licence_ok(licence: str) -> bool:
    lic = (licence or "").strip().lower().replace("_", "-")
    return bool(_ALLOWED_LICENCE.match(lic)) and not re.search(r"\b(nc|nd|sa)\b|-(nc|nd|sa)\b", lic)


def recorded_noise(kind: str, seconds: float, seed: int, source_dir: str | Path
                   ) -> tuple[np.ndarray, dict]:
    """Load a CC0 / CC BY noise file of ``kind`` from ``source_dir`` (used only when a folder is given).

    The folder holds ``<name>.wav`` files each with ``<name>.json`` beside it:
    ``{"kind": "cafe", "licence": "CC BY 4.0", "source": "<url or title>"}``. A file
    without a JSON, without a ``kind`` that equals ``kind``, or whose licence is not
    CC0 / CC BY (no NC, ND or SA) is skipped. The file is picked by ``seed``, resampled
    to 16 kHz, looped to ``seconds`` from a seeded offset, and scaled to -20 dBFS RMS.
    Returns the samples and ``{"source": "recorded", "file": name, "licence": ...}``.
    Raises ``LookupError`` when no usable file exists.
    """
    from edat.media.audio.wav import read_wav, resample
    folder = Path(source_dir)
    usable = []
    for wav in sorted(folder.glob("*.wav")):
        meta_path = wav.with_suffix(".json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("kind") != kind or not _licence_ok(str(meta.get("licence", ""))):
            continue
        usable.append((wav, meta))
    if not usable:
        raise LookupError(f"no licensed {kind!r} noise file in {folder}")
    g = _rng(seed)
    wav, meta = usable[int(g.integers(len(usable)))]
    x, rate = read_wav(wav.read_bytes())
    x = resample(x, rate, SAMPLE_RATE)
    n = int(round(seconds * SAMPLE_RATE))
    if len(x) == 0:
        raise LookupError(f"{wav.name} is empty")
    off = int(g.integers(len(x)))
    x = np.resize(np.roll(x, -off), n)
    return _unit_rms(x), {"source": "recorded", "file": wav.name, "licence": meta["licence"]}


def noise_for(kind: str, seconds: float, seed: int, source_dir: str | Path | None = None
              ) -> tuple[np.ndarray, dict]:
    """Recorded noise when ``source_dir`` is given and has a licensed file, else the generated one."""
    if source_dir is not None:
        try:
            return recorded_noise(kind, seconds, seed, source_dir)
        except LookupError:
            pass
    return (generate_a4_noise(kind, seconds, seed=seed),
            {"source": "generated", "file": None, "licence": "generated (no licence needed)"})


def mix_noise_a4(clip: Clip, kind: str, snr_db: float, *, seed: int,
                 source_dir: str | Path | None = None) -> Clip:
    """Noise of one A4 type at ``snr_db`` over the speech span, with the noise record kept."""
    nz, rec = noise_for(kind, clip.duration_s, seed, source_dir)
    out = mix_at_snr(clip, nz, snr_db, label=kind)
    out.meta["condition"] = {**out.meta["condition"], "noise_type": kind, "noise_record": rec}
    return out


# ------------------------------------------------------------ image-source rooms

@dataclass(frozen=True)
class Room:
    """A shoebox room. Mic and source sit on a line along x, symmetric about the room's
    centre (0.3 m off the middle in y, at 1.3 m height), so a longer distance never puts
    the source against a wall and the direct-to-reverberant ratio falls with distance."""

    name: str
    dims_m: tuple[float, float, float]
    rt60_s: float

    @property
    def volume_m3(self) -> float:
        x, y, z = self.dims_m
        return x * y * z

    @property
    def surface_m2(self) -> float:
        x, y, z = self.dims_m
        return 2 * (x * y + y * z + x * z)

    @property
    def absorption(self) -> float:
        """Mean wall absorption from Eyring's formula for the target RT60."""
        return 1.0 - math.exp(-0.161 * self.volume_m3 / (self.surface_m2 * self.rt60_s))

    @property
    def critical_distance_m(self) -> float:
        return 0.057 * math.sqrt(self.volume_m3 / self.rt60_s)


ROOMS: dict[str, Room] = {
    "small_office": Room("small_office", (7.0, 5.0, 2.7), 0.40),
    "living_room": Room("living_room", (7.5, 5.5, 2.6), 0.60),
    "hall": Room("hall", (14.0, 9.0, 5.0), 1.20),
}
ROOM_ORDER = tuple(ROOMS)
IMAGE_SOURCE_ORDER = 10
MIXING_TIME_S = 0.08


def room_description(room: Room | str, distance_m: float) -> dict:
    r = ROOMS[room] if isinstance(room, str) else room
    return {"name": r.name, "dims_m": list(r.dims_m), "absorption": round(r.absorption, 4),
            "rt60_target_s": r.rt60_s, "distance_m": distance_m,
            "critical_distance_m": round(r.critical_distance_m, 3),
            "method": f"image-source order {IMAGE_SOURCE_ORDER} to {MIXING_TIME_S * 1000:.0f} ms, "
                      "then exponential diffuse tail at the target RT60"}


def room_rir(room: Room | str, distance_m: float, *, seed: int = 0, order: int = IMAGE_SOURCE_ORDER,
             mixing_time_s: float = MIXING_TIME_S, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Room impulse response from a shoebox image-source model plus a diffuse tail (NumPy only).

    Images with at most ``order`` wall reflections and delay below ``mixing_time_s``
    are placed with two-tap linear-interpolation fractional delays and amplitude
    ``sqrt(1 - absorption) ** reflections / (4 pi d)``. Order 10 cannot reach a full
    reverberation time, so after the mixing time a Gaussian tail with the target RT60
    (amplitude exp(-6.9078 t / RT60)) continues it. The tail's total energy is the
    diffuse-field energy ``1 / (4 pi r_c) ** 2`` (``r_c`` the critical distance) that
    remains after the mixing time, so the direct-to-reverberant ratio is 0 dB at
    ``r_c`` and falls with distance.
    """
    r = ROOMS[room] if isinstance(room, str) else room
    sr = sample_rate
    L = np.array(r.dims_m)
    centre = np.array([L[0] / 2, L[1] / 2 + 0.3, 1.3])
    mic = centre - np.array([distance_m / 2, 0.0, 0.0])
    src = centre + np.array([distance_m / 2, 0.0, 0.0])
    if np.any(src < 0) or np.any(src > L) or np.any(mic < 0):
        raise ValueError(f"a source {distance_m} m from the mic does not fit in the {r.name}")
    beta = math.sqrt(1.0 - r.absorption)
    n_img = np.arange(-order, order + 1)
    # per axis: (position of the image, reflections) for every (n, q)
    axis_pos, axis_ref = [], []
    for a in range(3):
        pos, ref = [], []
        for q in (0, 1):
            pos.append((1 - 2 * q) * src[a] + 2 * n_img * L[a])
            ref.append(np.abs(n_img - q) + np.abs(n_img))
        axis_pos.append(np.concatenate(pos))
        axis_ref.append(np.concatenate(ref))
    px, py, pz = np.meshgrid(axis_pos[0], axis_pos[1], axis_pos[2], indexing="ij")
    rx, ry, rz = np.meshgrid(axis_ref[0], axis_ref[1], axis_ref[2], indexing="ij")
    refl = (rx + ry + rz).ravel()
    dist = np.sqrt((px - mic[0]) ** 2 + (py - mic[1]) ** 2 + (pz - mic[2]) ** 2).ravel()
    keep = (refl <= order) & (dist / SPEED_OF_SOUND < mixing_time_s)
    refl, dist = refl[keep], np.maximum(dist[keep], 0.1)
    amp = beta ** refl / (4 * math.pi * dist)
    pos = dist / SPEED_OF_SOUND * sr
    total = int((mixing_time_s + 1.6 * r.rt60_s) * sr) + 8
    h = np.zeros(total, dtype=np.float64)
    i0 = np.floor(pos).astype(int)
    frac = pos - i0
    np.add.at(h, i0, amp * (1 - frac))
    np.add.at(h, i0 + 1, amp * frac)
    # diffuse tail after the mixing time
    g = _rng(derive_seed(seed, r.name, distance_m))
    t = np.arange(total) / sr
    start = int(mixing_time_s * sr)
    env = np.exp(-6.9078 * t / r.rt60_s)
    ramp = np.clip((np.arange(total) - start) / (0.005 * sr), 0, 1)
    tail = g.standard_normal(total) * env * ramp
    tail[:start] = 0.0
    e_rev = 1.0 / (4 * math.pi * r.critical_distance_m) ** 2
    e_tail = e_rev * math.exp(-13.8155 * mixing_time_s / r.rt60_s)
    tail *= math.sqrt(e_tail / np.sum(tail * tail))
    h += tail
    return h.astype(np.float32)


def rt60_from_rir(h: np.ndarray, sample_rate: int = SAMPLE_RATE, lo_db: float = -5.0,
                  hi_db: float = -25.0) -> float:
    """RT60 from Schroeder backward integration: a line over -5 to -25 dB, extrapolated to -60 dB."""
    e = np.asarray(h, dtype=np.float64) ** 2
    edc = np.cumsum(e[::-1])[::-1]
    db = 10 * np.log10(edc / edc[0] + 1e-30)
    idx = np.where((db <= lo_db) & (db >= hi_db))[0]
    t = idx / sample_rate
    slope, _ = np.polyfit(t, db[idx], 1)
    return float(-60.0 / slope)


# ------------------------------------------------------------ level, clipping, band, truncation

def set_peak_dbfs(clip: Clip, peak_dbfs: float) -> Clip:
    """Scale so the clip's peak is ``peak_dbfs`` (low-volume condition)."""
    x = clip.samples.astype(np.float64)
    peak = float(np.max(np.abs(x))) or 1.0
    y = x * (10 ** (peak_dbfs / 20) / peak)
    return clip.copy(y.astype(np.float32), condition={"type": "low_volume", "peak_dbfs": peak_dbfs})


def overdrive_clip(clip: Clip, overdrive_db: float) -> Clip:
    """Normalise to full scale, add ``overdrive_db`` of gain, then hard clip at +-1."""
    x = clip.samples.astype(np.float64)
    peak = float(np.max(np.abs(x))) or 1.0
    y = x / peak * 10 ** (overdrive_db / 20)
    clipped = float(np.mean(np.abs(y) > 1.0))
    y = np.clip(y, -1.0, 1.0)
    return clip.copy(y.astype(np.float32), condition={
        "type": "clipping", "overdrive_db": overdrive_db, "clipped_fraction": round(clipped, 4)})


def narrowband(clip: Clip, band_rate: int = 8000) -> Clip:
    """Telephone / Bluetooth band: resample to 8 kHz and back to the clip's rate."""
    from edat.media.audio.wav import resample
    sr = clip.sample_rate
    down = resample(clip.samples, sr, band_rate)
    up = resample(down, band_rate, sr)
    y = np.zeros(len(clip.samples), dtype=np.float32)
    m = min(len(y), len(up))
    y[:m] = up[:m]
    return clip.copy(y, condition={"type": "narrowband", "band_rate": band_rate})


def dropout_intervals(clip: Clip, *, seed: int = 0) -> list[tuple[float, float]]:
    """Three 200 ms dropout intervals, one in each third of the speech span, seeded."""
    sr = clip.sample_rate
    a = (clip.speech_start_s or 0.0)
    b = clip.eos_s if clip.eos_s is not None else clip.duration_s
    third = (b - a) / DROPOUT_COUNT
    g = _rng(derive_seed(seed, "dropouts"))
    out = []
    for k in range(DROPOUT_COUNT):
        lo, hi = a + k * third, a + (k + 1) * third - DROPOUT_S
        s = lo if hi <= lo else g.uniform(lo, hi)
        out.append((round(s, 4), round(s + DROPOUT_S, 4)))
    return out


def truncate(clip: Clip, kind: str, *, seed: int = 0) -> Clip:
    """A4 truncation. ``cut_intervals_s`` (original timeline) lists the removed audio.

    * ``head_cut``: the first 0.3 s is removed (the clip starts 0.3 s later, timings shift);
    * ``tail_cut``: the last 0.3 s is removed (of the whole clip, trailing silence included);
    * ``dropouts``: three 200 ms spans inside the speech are zeroed (length kept).
    """
    sr = clip.sample_rate
    x = clip.samples
    shift = 0.0
    if kind == "head_cut":
        k = int(round(TRUNCATION_CUT_S * sr))
        y = x[k:].copy()
        cuts = [(0.0, round(min(TRUNCATION_CUT_S, clip.duration_s), 4))]
        shift = TRUNCATION_CUT_S
    elif kind == "tail_cut":
        k = int(round(TRUNCATION_CUT_S * sr))
        y = x[: max(0, len(x) - k)].copy()
        cuts = [(round(max(0.0, clip.duration_s - TRUNCATION_CUT_S), 4), round(clip.duration_s, 4))]
    elif kind == "dropouts":
        y = x.copy()
        cuts = dropout_intervals(clip, seed=seed)
        for s, e in cuts:
            y[int(round(s * sr)): int(round(e * sr))] = 0.0
    else:
        raise ValueError(f"unknown truncation kind {kind!r}")
    out = clip.copy(y.astype(np.float32), condition={
        "type": "truncation", "kind": kind, "cut_intervals_s": [list(c) for c in cuts],
        "time_shift_s": shift})
    if shift:
        if clip.speech_start_s is not None:
            out.speech_start_s = max(0.0, clip.speech_start_s - shift)
        if clip.eos_s is not None:
            out.eos_s = max(0.0, clip.eos_s - shift)
    elif clip.eos_s is not None:
        out.eos_s = min(clip.eos_s, out.duration_s)
    return out


def _timing_fields(w) -> tuple[float, float] | None:
    try:
        s, e = w["start_s"], w["end_s"]
    except (KeyError, TypeError):
        return None
    if s is None or e is None:
        return None
    return float(s), float(e)


def audible_mask(words_with_timings, condition: dict) -> list[bool] | None:
    """For each reference word, whether at least 50 % of its span survives the condition.

    ``words_with_timings`` is a list of ``{"word", "start_s", "end_s"}`` on the original
    timeline (the stub engine's ``meta["word_timings"]``). The removed spans are the
    condition's ``cut_intervals_s``. Conditions without cuts keep every word audible.
    Human recordings have no word timings; a pinned forced aligner is needed to get
    them, and without timings this returns None (callers then score against the full
    reference and say so).
    """
    if not words_with_timings:
        return None
    cuts = (condition or {}).get("cut_intervals_s", [])
    mask = []
    for w in words_with_timings:
        span = _timing_fields(w)
        if span is None:
            return None
        s, e = span
        lost = 0.0
        for a, b in cuts:
            lost += max(0.0, min(e, b) - max(s, a))
        mask.append((e - s) <= 0 or (1.0 - lost / (e - s)) >= AUDIBLE_FRACTION - 1e-9)
    return mask


def audible_reference(words_with_timings, condition: dict) -> list[str] | None:
    """The reference words still audible after the condition (see :func:`audible_mask`)."""
    mask = audible_mask(words_with_timings, condition)
    if mask is None:
        return None
    return [w["word"] for w, keep in zip(words_with_timings, mask) if keep]


# ------------------------------------------------------------ the A4 grid and plan

def a4_condition_grid() -> list[dict]:
    """The A4 conditions in spec order (noise, room, overlap, low volume, clipping, truncation,
    narrowband). That is 19; spec 05 §12 says "21" but its own 103 h total is 19 x 5.4 h.
    The clean baseline is not part of the grid."""
    out: list[dict] = []
    out += [{"type": "noise", "snr_db": s} for s in A4_SNR_DB]
    out += [{"type": "room", "distance_m": d} for d in DISTANCES_M]
    out += [{"type": "overlap", "sir_db": s} for s in SIR_LADDER_DB]
    out += [{"type": "low_volume", "peak_dbfs": v} for v in A4_LOW_VOLUME_DBFS]
    out += [{"type": "clipping", "overdrive_db": d} for d in A4_OVERDRIVE_DB]
    out += [{"type": "truncation", "kind": k} for k in A4_TRUNCATION_KINDS]
    out += [{"type": "narrowband"}]
    return out


def condition_key(cond: dict) -> str:
    t = cond["type"]
    for field_ in ("snr_db", "distance_m", "sir_db", "peak_dbfs", "overdrive_db", "kind"):
        if field_ in cond:
            return f"{t}_{field_.split('_')[0]}{cond[field_]}"
    return t


def _rotation(ids: list[str], seed: int, label: str, n_values: int) -> dict[str, int]:
    """An even rotation: shuffle the ids with the seed, then deal the values out in turn."""
    order = sorted(ids)
    g = _rng(derive_seed(seed, label))
    perm = g.permutation(len(order))
    return {order[int(p)]: k % n_values for k, p in enumerate(perm)}


def _speaker(utt_id: str) -> str:
    return utt_id.split("-")[0] if "-" in utt_id else utt_id


def a4_assignments(utterance_ids: list[str], seed: int) -> dict[str, dict]:
    """Per utterance: noise type, room and interferer id. Types and rooms rotate evenly
    (counts differ by at most one); the interferer is another speaker's utterance."""
    ids = list(utterance_ids)
    noise = _rotation(ids, seed, "noise", len(A4_NOISE_TYPES))
    room = _rotation(ids, seed, "room", len(ROOM_ORDER))
    by_speaker: dict[str, int] = {}
    for u in ids:
        by_speaker[_speaker(u)] = by_speaker.get(_speaker(u), 0) + 1
    g = _rng(derive_seed(seed, "interferer"))
    out = {}
    for u in ids:
        pick = None
        if len(by_speaker) > 1:
            for _ in range(64):
                c = ids[int(g.integers(len(ids)))]
                if _speaker(c) != _speaker(u):
                    pick = c
                    break
        out[u] = {"noise_type": A4_NOISE_TYPES[noise[u]], "room": ROOM_ORDER[room[u]],
                  "interferer_id": pick}
    return out


def a4_plan(utterance_ids: list[str], seed: int) -> list[dict]:
    """Deterministic A4 plan: one row per (utterance, condition) in utterance-then-grid order.

    For 2,620 test-clean utterances that is 2,620 x 19 = 49,780 rows. Each row carries the
    condition, the utterance's noise type, room and interferer (only the ones its condition
    uses), and an item seed derived from (seed, utterance, condition).
    """
    assign = a4_assignments(list(utterance_ids), seed)
    grid = a4_condition_grid()
    rows = []
    for u in utterance_ids:
        a = assign[u]
        for cond in grid:
            row = {"utt_id": u, "condition": cond, "condition_key": condition_key(cond),
                   "seed": derive_seed(seed, u, condition_key(cond))}
            if cond["type"] == "noise":
                row["noise_type"] = a["noise_type"]
            elif cond["type"] == "room":
                row["room"] = a["room"]
            elif cond["type"] == "overlap":
                row["interferer_id"] = a["interferer_id"]
            rows.append(row)
    return rows


def apply_condition(clip: Clip, cond: dict, *, seed: int = 0, noise_type: str | None = None,
                    room: str | None = None, interferer: Clip | None = None,
                    noise_dir: str | Path | None = None) -> Clip:
    """Render one A4 condition on a clip, from a plan row's fields."""
    t = cond["type"]
    if t == "noise":
        return mix_noise_a4(clip, noise_type or "white", cond["snr_db"], seed=seed, source_dir=noise_dir)
    if t == "room":
        return apply_room(clip, cond["distance_m"], seed=seed, room=room or ROOM_ORDER[0])
    if t == "overlap":
        if interferer is None:
            raise ValueError("overlap needs the interferer clip")
        return overlap(clip, interferer, cond["sir_db"])
    if t == "low_volume":
        return set_peak_dbfs(clip, cond["peak_dbfs"])
    if t == "clipping":
        return overdrive_clip(clip, cond["overdrive_db"])
    if t == "truncation":
        return truncate(clip, cond["kind"], seed=seed)
    if t == "narrowband":
        return narrowband(clip)
    if t == "clean":
        return clip
    raise ValueError(f"unknown condition {t!r}")


# ------------------------------------------------------------ alignment with operations

def align_ops(ref: list[str], hyp: list[str], band: int | None = None) -> list[tuple[str, int | None, int | None]]:
    """Minimum-cost word alignment as operations ``(op, ref_index, hyp_index)``.

    ``op`` is "hit", "sub", "del" or "ins" (the missing index is None). Costs are all 1 and
    ties go substitution, deletion, insertion, as in :func:`edat.media.textdist.align`.
    Long pairs (more than 4 million cells) use a diagonal band of width ``band`` (default
    ``|n - m| + 150`` words) around the line from (0, 0) to (n, m).
    """
    n, m = len(ref), len(hyp)
    if band is None and n * m > 4_000_000:
        band = abs(n - m) + 150
    INF = 1 << 30
    rows: list[tuple[int, list[int], bytearray]] = []     # (j_lo, costs, moves)
    for i in range(n + 1):
        if band is None:
            lo, hi = 0, m
        else:
            c = int(round(i * m / n)) if n else 0
            lo, hi = max(0, c - band), min(m, c + band)
        costs = [INF] * (hi - lo + 1)
        mv = bytearray(hi - lo + 1)
        for j in range(lo, hi + 1):
            k = j - lo
            if i == 0 and j == 0:
                costs[k] = 0
                continue
            best, mov = INF, 0
            if i > 0 and j > 0:
                plo, pc, _ = rows[i - 1]
                kk = j - 1 - plo
                if 0 <= kk < len(pc) and pc[kk] < INF:
                    cst = pc[kk] + (0 if ref[i - 1] == hyp[j - 1] else 1)
                    best, mov = cst, 1 if ref[i - 1] == hyp[j - 1] else 2
            if i > 0:
                plo, pc, _ = rows[i - 1]
                kk = j - plo
                if 0 <= kk < len(pc) and pc[kk] < INF and pc[kk] + 1 < best:
                    best, mov = pc[kk] + 1, 3
            if j > lo and costs[k - 1] < INF and costs[k - 1] + 1 < best:
                best, mov = costs[k - 1] + 1, 4
            costs[k], mv[k] = best, mov
        rows.append((lo, costs, mv))
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        lo, _, mv = rows[i]
        mov = mv[j - lo]
        if mov in (1, 2):
            ops.append(("hit" if mov == 1 else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif mov == 3:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


# ------------------------------------------------------------ A4 scorers

def _words(text: str) -> list[str]:
    from edat.media.audio.normalize import normalize
    return normalize(text or "").split()


def interferer_leak(interferer_text: str, target_ref: str, hyp: str) -> float | None:
    """Share of the interferer's words that appear in the output but not in the target reference.

    Interferer words that are also target words cannot be told apart, so they are left out
    of the denominator; None when nothing is left (every interferer word is in the target).
    """
    tgt = set(_words(target_ref))
    out = set(_words(hyp))
    cand = [w for w in _words(interferer_text) if w not in tgt]
    if not cand:
        return None
    return sum(1 for w in cand if w in out) / len(cand)


def empty_output_rate(hyps: list[str]) -> float:
    """Share of outputs with no letter or digit left after removing "no speech" markers."""
    from edat.media.audio.wer import has_content
    if not hyps:
        return 0.0
    return sum(1 for h in hyps if not has_content(h)) / len(hyps)


def gap_invention(audible_words: list[str], hyp: str, n_gaps: int = 1) -> dict:
    """Words the model made up where audio was cut.

    Approximation: align the audible reference with the output; output words left unmatched
    by the alignment (insertions) count as invented. That also counts any insertion outside
    the gaps, so the figure is an upper bound on gap invention. ``per_gap`` divides by the
    number of cut intervals (3 for dropouts, 1 for a head or tail cut).
    """
    ref = [w for w in _words(" ".join(audible_words))]
    ops = align_ops(ref, _words(hyp))
    inv = sum(1 for o in ops if o[0] == "ins")
    return {"invented": inv, "gaps": n_gaps, "per_gap": inv / max(1, n_gaps)}


def degradation_slope(levels: list[float], wers: list[float], step: float = 1.0) -> float:
    """Least-squares slope of WER against level, scaled to ``step`` units of level.

    For SNR with ``step=5`` it is the WER change per +5 dB (negative: WER falls as SNR rises).
    """
    if len(levels) != len(wers) or len(levels) < 2:
        raise ValueError("need at least two (level, wer) points of equal length")
    x = np.asarray(levels, dtype=np.float64)
    y = np.asarray(wers, dtype=np.float64)
    xm = x - x.mean()
    denom = float(np.sum(xm * xm))
    if denom == 0:
        raise ValueError("levels are all equal")
    return float(np.sum(xm * (y - y.mean())) / denom * step)
