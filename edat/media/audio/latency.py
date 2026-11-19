"""Audio latency definitions (spec 05 §4.1, A2), computed from adapter results.

Clips are files, so time is virtual: the end of speech (EoS) is time 0 and the
whole clip has been delivered at EoS + 0.5 s (its trailing audio), which is
when the request is sent. The adapter's ``ChatResult.timing`` gives
``ttft_s`` (request sent to first token) and ``total_s`` (request sent to the
end of the response), both measured by the client.

=====================================  ==========================================
TTFT-audio                             clip delivered -> first output token
                                       = ``timing.ttft_s``
Time to final transcript               EoS -> last transcript token
                                       = 0.5 + ``timing.total_s``
Real-time factor (RTF), whole clip     processing time / audio duration
                                       = ``timing.total_s`` / clip seconds; RTFx = 1/RTF
Audio tokens per second                (prompt tokens with the clip - without) / clip seconds
Audio encode time                      TTFT with the clip - TTFT with no audio, per audio second
Spoken-command latency                 EoS -> first correct tool call fully emitted:
                                       0.5 + transcribe ``total_s`` + agent ``tool_call_done_s``
                                       (two-pass); 0.5 + agent ``tool_call_done_s`` (native)
User-perceived command latency         spoken-command latency + reference endpointing delay
Endpointing delay (reference)          EoS -> the reference endpointer decides the turn ended
                                       (Harbor's energy rule: 1.3 s pause, threshold room + 14 dB)
Cold penalty                           first audio request after load - warm median
=====================================  ==========================================

The adapter does not yet report when a tool call finished streaming; until it
does, ``tool_call_done_s`` falls back to ``total_s`` and the record says so
(``tool_call_time_source``). All values are seconds here; records use ``_s``
names and the website converts to milliseconds.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass

import numpy as np

from edat.media.audio.wav import Clip

TRAILING_S = 0.5
ENDPOINT_PAUSE_S = 1.3            # Harbor VoiceCapture: question ends after a 1.3 s pause
ENDPOINT_THRESHOLD_DB = 14.0      # speech threshold: room level + 14 dB
ENDPOINT_FRAME_S = 0.02
LATENCY_CLIP_LENGTHS_S = (2, 5, 10, 20, 30)


@dataclass
class TranscriptLatency:
    ttft_audio_s: float | None
    time_to_final_s: float | None
    rtf: float | None
    rtfx: float | None
    audio_s: float

    def to_json(self) -> dict:
        return asdict(self)


def _timing(result) -> tuple[float | None, float | None]:
    t = getattr(result, "timing", None)
    return (getattr(t, "ttft_s", None), getattr(t, "total_s", None)) if t else (None, None)


def transcript_latency(result, audio_s: float, *, trailing_s: float = TRAILING_S) -> TranscriptLatency:
    ttft, total = _timing(result)
    rtf = (total / audio_s) if (total is not None and audio_s > 0) else None
    rtfx = (1.0 / rtf) if rtf else None
    return TranscriptLatency(ttft, (trailing_s + total) if total is not None else None, rtf, rtfx,
                             audio_s)


def audio_tokens_per_second(prompt_tokens_with: int | None, prompt_tokens_without: int | None,
                            audio_s: float) -> float | None:
    if prompt_tokens_with is None or prompt_tokens_without is None or audio_s <= 0:
        return None
    return (prompt_tokens_with - prompt_tokens_without) / audio_s


def audio_encode_s_per_audio_s(ttft_with: float | None, ttft_without: float | None,
                               audio_s: float) -> float | None:
    if ttft_with is None or ttft_without is None or audio_s <= 0:
        return None
    return (ttft_with - ttft_without) / audio_s


def tool_call_done_s(result) -> tuple[float | None, str]:
    """When the agent's tool call was fully emitted, and where that number came from."""
    meta = getattr(result, "meta", {}) or {}
    t = getattr(getattr(result, "timing", None), "tool_call_done_s", None) or meta.get("tool_call_done_s")
    if t is not None:
        return float(t), "adapter"
    _, total = _timing(result)
    return total, "total_s_fallback"


def spoken_command_latency(results: list, configuration: str, *,
                           trailing_s: float = TRAILING_S) -> tuple[float | None, str]:
    """EoS -> tool call emitted, for two_pass / pipeline ([transcribe, agent]) or native ([agent])."""
    if configuration == "native":
        done, src = tool_call_done_s(results[-1])
        return (trailing_s + done if done is not None else None), src
    _, t_total = _timing(results[0])
    done, src = tool_call_done_s(results[-1])
    if t_total is None or done is None:
        return None, src
    return trailing_s + t_total + done, src


def reference_endpoint_delay(clip: Clip, *, pause_s: float = ENDPOINT_PAUSE_S,
                             threshold_db: float = ENDPOINT_THRESHOLD_DB,
                             frame_s: float = ENDPOINT_FRAME_S) -> float | None:
    """Run the reference energy endpointer on the clip followed by its own room tone.

    The room level is the quietest 10th percentile of frame energies. Speech frames are
    those above room + ``threshold_db``. The turn ends ``pause_s`` after the last speech
    frame once that much non-speech has passed. Returns decision time - EoS, or None
    when the endpointer never hears speech. Trailing room tone is extended by repeating
    the clip's last 0.5 s so a short file can still reach the pause length.
    """
    sr = clip.sample_rate
    x = clip.samples.astype(np.float64)
    tail = x[-int(TRAILING_S * sr):] if len(x) > int(TRAILING_S * sr) else x
    need = int((pause_s + 0.5) * sr)
    reps = int(np.ceil(need / max(1, len(tail))))
    x = np.concatenate([x, np.tile(tail, reps)])
    n = int(frame_s * sr)
    frames = len(x) // n
    e = np.array([np.mean(x[i * n:(i + 1) * n] ** 2) for i in range(frames)]) + 1e-12
    db = 10 * np.log10(e)
    room = float(np.percentile(db, 10))
    speech = db > room + threshold_db
    if not speech.any():
        return None
    last = None
    quiet = 0
    for i, s in enumerate(speech):
        if s:
            last = i
            quiet = 0
        elif last is not None:
            quiet += 1
            if quiet * frame_s >= pause_s:
                decided = (i + 1) * frame_s
                return decided - (clip.eos_s if clip.eos_s is not None else (last + 1) * frame_s)
    return None


def user_perceived_latency(command_latency_s: float | None, endpoint_delay_s: float | None
                           ) -> float | None:
    if command_latency_s is None or endpoint_delay_s is None:
        return None
    return command_latency_s + endpoint_delay_s


def cold_penalty(first_s: float | None, warm_s: list[float]) -> float | None:
    if first_s is None or not warm_s:
        return None
    return first_s - statistics.median(warm_s)
