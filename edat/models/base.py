"""The model adapter: one `chat()` interface for every runtime EDAT drives.

Every client (the scripted stub, oMLX on the Mac, the hosted OpenRouter
reference) implements `ModelClient.chat` and returns a `ChatResult`. The
reference loop, the quality tests, the vision and audio tests and the
performance runner all call this one method, so a result means the same thing
whichever runtime produced it. See docs/benchmark/model-adapter.md.

Two rules the rest of EDAT relies on:

* **A malformed tool call is the model's failure, never an engine fault.** If
  the arguments a model wrote are not a JSON object, the call still comes back
  as a `ToolCall` with `arguments=None` and a `parse_error`; the client never
  raises for it. Graders label it `malformed` and count it.
* **Only transport and engine faults raise**, as `EngineError`. Those trials are
  excluded from every rate and counted (spec 02 §5.1).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

ADAPTER_SCHEMA = "edat-model-adapter/v1"

# Where a sampling setting came from (spec 02 §2.3, §4.4).
SAMPLING_SOURCES = ("author_recommended", "fixed_sensitivity", "provider_default",
                    "greedy_diagnostic", "unspecified")


@dataclass(frozen=True)
class Sampling:
    """Sampling parameters for one request. None means "not sent"."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    repetition_penalty: float | None = None
    presence_penalty: float | None = None
    source: str = "unspecified"

    def __post_init__(self) -> None:
        if self.source not in SAMPLING_SOURCES:
            raise ValueError(f"unknown sampling source {self.source!r}")

    def params(self) -> dict[str, float | int]:
        """The numeric parameters that are set, by their wire names."""
        out = {k: v for k, v in asdict(self).items() if k != "source" and v is not None}
        return out

    def to_record(self) -> dict:
        return {**self.params(), "source": self.source}


# The fixed sensitivity setting every model also runs at (spec 02 §2.3).
FIXED_SENSITIVITY = Sampling(temperature=0.6, top_p=0.95, source="fixed_sensitivity")


@dataclass
class ToolCall:
    """One tool call as the model wrote it.

    `arguments_raw` is the exact argument text the runtime returned (for a
    runtime that returns an object, its JSON dump, and `raw_was_object` is
    set). `arguments` is the parsed object, or None with `parse_error` set.
    """

    id: str
    name: str
    arguments_raw: str
    arguments: dict | None
    parse_error: str | None = None
    raw_was_object: bool = False

    @property
    def ok(self) -> bool:
        return self.parse_error is None and self.arguments is not None

    def to_message_entry(self) -> dict:
        """The OpenAI `tool_calls` entry to put back into history (raw text kept)."""
        return {"id": self.id, "type": "function",
                "function": {"name": self.name, "arguments": self.arguments_raw}}

    def to_record(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments_raw": self.arguments_raw,
                "arguments": self.arguments, "parse_error": self.parse_error,
                "raw_was_object": self.raw_was_object}


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    cost_usd: float | None = None          # hosted only; "not comparable with on-device numbers"

    def to_record(self) -> dict:
        return asdict(self)


@dataclass
class Timing:
    """Wall-clock timing measured by the client, in seconds.

    `ttft_s` is the time from sending the request to the first generated
    token (content, reasoning or tool-call delta); None when the client did
    not stream. `total_s` is the time to the end of the response.
    """

    ttft_s: float | None = None
    total_s: float | None = None

    def to_record(self) -> dict:
        return asdict(self)


@dataclass
class ChatResult:
    text: str                                   # assistant content, untouched (may hold markup)
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""                         # separated reasoning text, if the runtime splits it
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    timing: Timing = field(default_factory=Timing)
    raw: Any = None                             # the raw response body (or list of stream chunks)
    request: dict = field(default_factory=dict) # what was sent, secrets removed
    meta: dict = field(default_factory=dict)    # client facts: resolved model, params omitted, ...

    @property
    def malformed_calls(self) -> list[ToolCall]:
        return [c for c in self.tool_calls if not c.ok]

    def assistant_message(self) -> dict:
        """The assistant message to append to history, with raw argument text."""
        msg: dict = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            msg["tool_calls"] = [c.to_message_entry() for c in self.tool_calls]
        return msg

    def to_record(self, *, include_raw: bool = True) -> dict:
        rec = {"schema": ADAPTER_SCHEMA, "text": self.text,
               "tool_calls": [c.to_record() for c in self.tool_calls],
               "reasoning": self.reasoning, "finish_reason": self.finish_reason,
               "usage": self.usage.to_record(), "timing": self.timing.to_record(),
               "request": self.request, "meta": self.meta}
        if include_raw:
            rec["raw"] = self.raw
        return rec


class EngineError(RuntimeError):
    """A transport or engine fault: the trial is excluded, never scored.

    kind: transport | timeout | server | rate_limit | request | config | auth
    """

    def __init__(self, kind: str, detail: str, *, status: int | None = None,
                 retryable: bool = True, raw: Any = None) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status
        self.retryable = retryable
        self.raw = raw


class ConfigurationError(ValueError):
    """The client was set up in a way EDAT forbids (wrong origin, missing key)."""


@runtime_checkable
class ModelClient(Protocol):
    def describe(self) -> dict:
        """Run-key facts about this client (runtime, model id, parser...), no secrets."""
        ...

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             sampling: Sampling | None = None, seed: int | None = None,
             max_tokens: int, thinking: bool) -> ChatResult: ...


# ------------------------------------------------------------------- parsing

def parse_arguments(raw: Any) -> tuple[str, dict | None, str | None, bool]:
    """Parse one call's arguments without repairing anything.

    Returns (arguments_raw, arguments, parse_error, raw_was_object).
    An empty string or None is an empty object (a call with no arguments).
    Anything that is not exactly one JSON object is a parse error.
    """
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False), raw, None, True
    if raw is None:
        return "", {}, None, False
    if not isinstance(raw, str):
        text = json.dumps(raw, ensure_ascii=False, default=str)
        return text, None, f"arguments are a {type(raw).__name__}, not a JSON object", True
    if raw.strip() == "":
        return raw, {}, None, False
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return raw, None, f"invalid JSON: {exc.msg} at char {exc.pos}", False
    if not isinstance(value, dict):
        return raw, None, f"arguments are JSON {type(value).__name__}, not an object", False
    return raw, value, None, False


def make_tool_call(call_id: str | None, name: str | None, raw_arguments: Any, index: int) -> ToolCall:
    arguments_raw, arguments, error, was_object = parse_arguments(raw_arguments)
    if not name:
        error = error or "tool call has no function name"
    return ToolCall(id=call_id or f"call_{index}", name=name or "", arguments_raw=arguments_raw,
                    arguments=arguments if error is None else None, parse_error=error,
                    raw_was_object=was_object)


def tool_names(tools: Iterable[dict] | None) -> list[str]:
    out = []
    for t in tools or []:
        fn = t.get("function", t)
        if fn.get("name"):
            out.append(fn["name"])
    return out
