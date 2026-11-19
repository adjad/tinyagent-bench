"""The media tests' seam onto the shared model adapter (``edat/models/``).

The media runner needs exactly what ``docs/benchmark/model-adapter.md`` defines:
``client.describe()`` and ``client.chat(messages, *, tools, sampling, seed,
max_tokens, thinking)`` returning a result with ``text``, ``tool_calls``,
``finish_reason``, ``usage`` (``prompt_tokens``, ``completion_tokens``),
``timing`` (``ttft_s``, ``total_s``) and ``to_record()``. Media goes inside the
OpenAI message content as ``image_url`` and ``input_audio`` parts, which the
adapter passes through unchanged.

Until ``edat/models/`` is on ``main`` this module carries no import of it: the
runner duck-types the result, and :class:`StubMediaClient` stands in for a model
in tests. What the adapter still has to add for media is listed in
:data:`ADAPTER_GAPS` (and in the hand-over).
"""
from __future__ import annotations

import base64
import io
import json
import wave
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

ADAPTER_GAPS = (
    "describe() should state the model's input modalities: accepts_image, accepts_audio, and "
    "whether audio and tools are accepted in one request (audio_with_tools)",
    "a way to send extra request headers, for oMLX's X-oMLX-Cache-Store: off on media requests",
    "timing.tool_call_done_s: when the first tool call's arguments finished streaming "
    "(spoken-command latency needs it; total_s is the fallback)",
    "per-request media limits for hosted models (5 MB per image, 9 images, 16 MB per request, "
    "from Harbor's remote policy) checked before sending",
    "a raised max_tokens budget for hosted reasoning models on media requests, and "
    "finish_reason 'length' with no text reported as a failed item, not silence",
)

# Hosted models that accept audio (spec 05 §10.5); all six accept images.
HOSTED_AUDIO = {"google/gemini-4-argon", "google/gemini-4-carbon", "google/gemini-4-neon"}
HOSTED_ALL = HOSTED_AUDIO | {"anthropic/claude-haiku-5.5", "openai/gpt-6-luna",
                             "deepseek/deepseek-v4.1-flash-1017"}


@dataclass(frozen=True)
class MediaCapabilities:
    image: bool
    audio: bool
    audio_with_tools: bool | None      # None = unverified (oMLX 0.7.3, spec 05 §14)
    source: str


def capabilities_for(client, *, declared: dict | None = None) -> MediaCapabilities:
    """What a model accepts: declared by the run, else ``describe()``, else the hosted table."""
    if declared is not None:
        return MediaCapabilities(bool(declared["image"]), bool(declared["audio"]),
                                 declared.get("audio_with_tools"), "declared")
    d = client.describe() if hasattr(client, "describe") else {}
    if "accepts_image" in d or "accepts_audio" in d:
        return MediaCapabilities(bool(d.get("accepts_image")), bool(d.get("accepts_audio")),
                                 d.get("audio_with_tools"), "describe")
    model = d.get("model") or d.get("requested_model")
    if model in HOSTED_ALL:
        return MediaCapabilities(True, model in HOSTED_AUDIO, None, "hosted_table")
    raise ValueError(f"no input modalities known for {model!r}: declare them in the run config "
                     "(a local model's come from its checkpoint: edat.media.checkpoint)")


def image_part(data: bytes, mime: str = "image/png") -> dict:
    return {"type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}}


def wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


# ------------------------------------------------------------------ stub client

@dataclass
class StubToolCall:
    id: str
    name: str
    arguments_raw: str
    arguments: dict | None
    parse_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.parse_error is None and self.arguments is not None


@dataclass
class StubUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class StubTiming:
    ttft_s: float | None = None
    total_s: float | None = None
    tool_call_done_s: float | None = None


@dataclass
class StubResult:
    text: str
    tool_calls: list[StubToolCall] = field(default_factory=list)
    finish_reason: str | None = "stop"
    usage: StubUsage = field(default_factory=StubUsage)
    timing: StubTiming = field(default_factory=StubTiming)
    meta: dict = field(default_factory=dict)
    request: dict = field(default_factory=dict)

    def to_record(self, include_raw: bool = True) -> dict:
        d = asdict(self)
        d["schema"] = "edat-model-adapter/v1"
        return d


class StubEngineError(RuntimeError):
    """Stands in for edat.models.EngineError: a transport fault, never scored."""

    def __init__(self, kind: str = "transport", detail: str = "stub") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


def count_media(messages: list[dict]) -> tuple[int, float, int]:
    """(images, audio seconds, text words) in a message list."""
    images, audio_s, words = 0, 0.0, 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            words += len(content.split())
            continue
        for part in content or []:
            if part.get("type") == "image_url":
                images += 1
            elif part.get("type") == "input_audio":
                audio_s += wav_seconds(base64.b64decode(part["input_audio"]["data"]))
            elif part.get("type") == "text":
                words += len(part.get("text", "").split())
    return images, audio_s, words


class StubMediaClient:
    """A scripted model for media tests: no network, no weights.

    ``answer(messages, tools, index)`` returns a dict with ``text`` and optional
    ``calls`` ([{name, arguments}]), ``finish_reason``, or ``engine_error``. Token
    counts mimic Gemma 4: 280 tokens per image, 25 per audio second, 1.3 per word.
    Timings are virtual: ``ttft_s`` = 0.05 + 0.001 * prompt tokens, ``total_s`` =
    ttft + 0.02 * output words.
    """

    runtime = "stub"

    def __init__(self, answer: Callable[[list[dict], list[dict] | None, int], dict], *,
                 model: str = "stub-media", accepts_image: bool = True,
                 accepts_audio: bool = True, audio_with_tools: bool | None = True) -> None:
        self.answer = answer
        self.model = model
        self.caps = {"accepts_image": accepts_image, "accepts_audio": accepts_audio,
                     "audio_with_tools": audio_with_tools}
        self.requests: list[dict] = []

    def describe(self) -> dict:
        return {"kind": "stub", "runtime": self.runtime, "model": self.model, **self.caps}

    def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
        images, audio_s, words = count_media(messages)
        if images and not self.caps["accepts_image"]:
            raise AssertionError("stub: an image was sent to a model without image input")
        if audio_s and not self.caps["accepts_audio"]:
            raise AssertionError("stub: audio was sent to a model without audio input")
        index = len(self.requests)
        self.requests.append({"messages": json.loads(json.dumps(messages)), "tools": tools,
                              "seed": seed, "max_tokens": max_tokens, "thinking": thinking,
                              "images": images, "audio_s": audio_s})
        out = self.answer(messages, tools, index)
        if out.get("engine_error"):
            raise StubEngineError(out["engine_error"])
        text = out.get("text", "")
        calls = [StubToolCall(f"call_{index}_{i}", c["name"], json.dumps(c.get("arguments", {})),
                              c.get("arguments"))
                 for i, c in enumerate(out.get("calls", []))]
        prompt = int(280 * images + round(25 * audio_s) + round(1.3 * words))
        completion = max(1, len(text.split())) + 10 * len(calls)
        ttft = 0.05 + 0.001 * prompt
        total = ttft + 0.02 * completion
        return StubResult(text=text, tool_calls=calls,
                          finish_reason=out.get("finish_reason") or ("tool_calls" if calls else "stop"),
                          usage=StubUsage(prompt, completion),
                          timing=StubTiming(round(ttft, 6), round(total, 6),
                                            round(total, 6) if calls else None),
                          request={"model": self.model, "seed": seed, "max_tokens": max_tokens,
                                   "thinking": thinking})


def is_engine_error(exc: BaseException) -> bool:
    """True for edat.models.EngineError (or the stub's) without importing edat.models."""
    return type(exc).__name__ in ("EngineError", "StubEngineError")
