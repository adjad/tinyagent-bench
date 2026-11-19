"""The model adapter as Screen World uses it.

The shared adapter lives in `edat/models/` (agentic build,
contract in docs/benchmark/model-adapter.md). Until it is on `main`, this module
supplies a minimal copy of the same interface so the screen tests run on their
own: `ChatResult`, `ToolCall`, `Usage`, `Timing`, `EngineError`, `Sampling`,
`ScriptedClient` and `NullClient`, with the same fields and the same rules
(a malformed call comes back as a `ToolCall` with `parse_error`; only
`EngineError` means a transport or engine fault).

When `edat.models` is importable, every name below is the shared one.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

try:  # pragma: no cover - exercised once edat/models is on main
    from edat.models.base import (ChatResult, EngineError, Sampling, Timing, ToolCall,  # noqa: F401
                                  Usage, make_tool_call)
    from edat.models.stub import NullClient, ScriptedClient  # noqa: F401
    SHARED_ADAPTER = True
except ImportError:
    SHARED_ADAPTER = False

    SAMPLING_SOURCES = ("author_recommended", "fixed_sensitivity", "provider_default",
                        "greedy_diagnostic", "unspecified")

    @dataclass(frozen=True)
    class Sampling:  # type: ignore[no-redef]
        temperature: float | None = None
        top_p: float | None = None
        top_k: int | None = None
        min_p: float | None = None
        repetition_penalty: float | None = None
        presence_penalty: float | None = None
        source: str = "unspecified"

        def params(self) -> dict:
            return {k: v for k, v in asdict(self).items() if k != "source" and v is not None}

        def to_record(self) -> dict:
            return {**self.params(), "source": self.source}

    @dataclass
    class ToolCall:  # type: ignore[no-redef]
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
            return {"id": self.id, "type": "function",
                    "function": {"name": self.name, "arguments": self.arguments_raw}}

        def to_record(self) -> dict:
            return asdict(self)

    @dataclass
    class Usage:  # type: ignore[no-redef]
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        reasoning_tokens: int | None = None
        cached_prompt_tokens: int | None = None
        cost_usd: float | None = None

        def to_record(self) -> dict:
            return asdict(self)

    @dataclass
    class Timing:  # type: ignore[no-redef]
        ttft_s: float | None = None
        total_s: float | None = None

        def to_record(self) -> dict:
            return asdict(self)

    @dataclass
    class ChatResult:  # type: ignore[no-redef]
        text: str
        tool_calls: list = field(default_factory=list)
        reasoning: str = ""
        finish_reason: str | None = None
        usage: Usage = field(default_factory=Usage)
        timing: Timing = field(default_factory=Timing)
        raw: Any = None
        request: dict = field(default_factory=dict)
        meta: dict = field(default_factory=dict)

        @property
        def malformed_calls(self) -> list:
            return [c for c in self.tool_calls if not c.ok]

        def assistant_message(self) -> dict:
            msg: dict = {"role": "assistant", "content": self.text or ""}
            if self.tool_calls:
                msg["tool_calls"] = [c.to_message_entry() for c in self.tool_calls]
            return msg

    class EngineError(RuntimeError):  # type: ignore[no-redef]
        def __init__(self, kind: str, detail: str, *, status: int | None = None,
                     retryable: bool = True, raw: Any = None) -> None:
            super().__init__(f"{kind}: {detail}")
            self.kind, self.detail, self.status, self.retryable, self.raw = kind, detail, status, retryable, raw

    def _parse(raw: Any):
        if isinstance(raw, dict):
            return json.dumps(raw, ensure_ascii=False), raw, None, True
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return raw or "", {}, None, False
        if not isinstance(raw, str):
            return json.dumps(raw, default=str), None, f"arguments are a {type(raw).__name__}", True
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            return raw, None, f"invalid JSON: {exc.msg} at char {exc.pos}", False
        if not isinstance(value, dict):
            return raw, None, f"arguments are JSON {type(value).__name__}, not an object", False
        return raw, value, None, False

    def make_tool_call(call_id, name, raw_arguments, index):  # type: ignore[no-redef]
        arguments_raw, arguments, error, was_object = _parse(raw_arguments)
        if not name:
            error = error or "tool call has no function name"
        return ToolCall(id=call_id or f"call_{index}", name=name or "", arguments_raw=arguments_raw,
                        arguments=arguments if error is None else None, parse_error=error,
                        raw_was_object=was_object)

    def _tool_names(tools):
        return [(t.get("function", t)).get("name") for t in tools or []]

    def _turn_to_result(turn: dict, index: int) -> ChatResult:
        if "engine_error" in turn:
            raise EngineError(str(turn["engine_error"]), str(turn.get("detail", "scripted engine error")))
        calls = []
        for i, c in enumerate(turn.get("calls") or []):
            raw = c["arguments_raw"] if "arguments_raw" in c else json.dumps(c.get("arguments") or {})
            calls.append(make_tool_call(c.get("id") or f"call_{index}_{i}", c.get("name"), raw, i))
        return ChatResult(text=turn.get("text") or "", tool_calls=calls, reasoning=turn.get("reasoning") or "",
                          finish_reason=turn.get("finish_reason") or ("tool_calls" if calls else "stop"),
                          usage=Usage(prompt_tokens=turn.get("prompt_tokens"),
                                      completion_tokens=turn.get("completion_tokens")),
                          timing=Timing(ttft_s=0.0, total_s=0.0), raw={"scripted": turn},
                          meta={"scripted_index": index})

    class ScriptedClient:  # type: ignore[no-redef]
        runtime = "stub"

        def __init__(self, turns: Iterable, *, name: str = "scripted", on_exhaust: str = "error") -> None:
            self.turns = list(turns)
            self.name = name
            self.on_exhaust = on_exhaust
            self.index = 0
            self.requests: list[dict] = []

        def describe(self) -> dict:
            return {"kind": "stub", "runtime": self.runtime, "model": self.name}

        def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
            self.requests.append({"messages": [dict(m) for m in messages], "tools": _tool_names(tools),
                                  "seed": seed, "max_tokens": max_tokens, "thinking": thinking})
            if self.index >= len(self.turns):
                if self.on_exhaust == "error":
                    raise RuntimeError(f"{self.name}: script has {len(self.turns)} turns, asked for more")
                return _turn_to_result({"text": ""}, self.index)
            turn = self.turns[self.index]
            idx = self.index
            self.index += 1
            if callable(turn):
                out = turn(messages, tools, idx)
                return out if isinstance(out, ChatResult) else _turn_to_result(out, idx)
            return _turn_to_result(turn, idx)

    class NullClient(ScriptedClient):  # type: ignore[no-redef]
        def __init__(self, text: str = "OK.") -> None:
            super().__init__([], name="null", on_exhaust="stop")
            self.text = text

        def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
            self.requests.append({"messages": len(messages)})
            return _turn_to_result({"text": self.text}, len(self.requests) - 1)


class PolicyClient:
    """A stub client driven by a Python function of the latest observation.

    `policy(messages, tools, index) -> turn dict` (the ScriptedClient turn shape).
    Used by the scripted oracle, which must read per-trial element ids off the
    screen it is shown, exactly as a model would.
    """

    runtime = "stub"

    def __init__(self, policy: Callable[[list[dict], list | None, int], dict], *, name: str = "policy") -> None:
        self.policy = policy
        self.name = name
        self.index = 0

    def describe(self) -> dict:
        return {"kind": "stub", "runtime": self.runtime, "model": self.name}

    def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
        turn = self.policy(messages, tools, self.index)
        self.index += 1
        return ScriptedClient([turn], name=self.name).chat(messages, tools=tools, sampling=sampling, seed=seed,
                                                            max_tokens=max_tokens, thinking=thinking)
