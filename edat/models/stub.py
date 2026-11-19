"""Scripted clients for tests and case validation. No model, no network.

`ScriptedClient` replays a fixed list of turns. Each turn is a dict:

    {"text": "...", "calls": [{"name": "get_weather", "arguments": {"location": "Oslo"}}],
     "reasoning": "...", "finish_reason": "stop"}

A call may give `arguments_raw` (a string, sent as is, so a test can script a
malformed call) instead of `arguments` (an object, JSON-encoded). A turn may
instead be `{"engine_error": "transport"}` to raise an EngineError, or a
callable `(messages, tools, index) -> turn dict | ChatResult`.

`NullClient` always answers with the same short text and no calls. It is the
"do nothing" model every case must fail under.

Stub timings are virtual (zero), so nothing waits on a clock.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from edat.models.media import media_parts, summarise_message
from edat.models.base import (ChatResult, EngineError, Sampling, Timing, Usage, make_tool_call,
                              tool_names)

Turn = dict | Callable[[list[dict], list[dict] | None, int], Any]


class ScriptExhausted(RuntimeError):
    pass


def turn_to_result(turn: dict, index: int) -> ChatResult:
    if "engine_error" in turn:
        raise EngineError(str(turn["engine_error"]), str(turn.get("detail", "scripted engine error")))
    calls = []
    for i, c in enumerate(turn.get("calls") or []):
        raw = c["arguments_raw"] if "arguments_raw" in c else json.dumps(c.get("arguments") or {})
        calls.append(make_tool_call(c.get("id") or f"call_{index}_{i}", c.get("name"), raw, i))
    text = turn.get("text") or ""
    finish = turn.get("finish_reason") or ("tool_calls" if calls else "stop")
    usage = Usage(prompt_tokens=turn.get("prompt_tokens"), completion_tokens=turn.get("completion_tokens"))
    return ChatResult(text=text, tool_calls=calls, reasoning=turn.get("reasoning") or "",
                      finish_reason=finish, usage=usage, timing=Timing(ttft_s=0.0, total_s=0.0),
                      raw={"scripted": turn}, meta={"scripted_index": index})


class ScriptedClient:
    runtime = "stub"

    def __init__(self, turns: Iterable[Turn], *, name: str = "scripted",
                 on_exhaust: str = "error") -> None:
        self.turns = list(turns)
        self.name = name
        self.on_exhaust = on_exhaust
        self.index = 0
        self.requests: list[dict] = []

    def describe(self) -> dict:
        return {"kind": "stub", "runtime": self.runtime, "model": self.name}

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             sampling: Sampling | None = None, seed: int | None = None,
             max_tokens: int, thinking: bool) -> ChatResult:
        self.requests.append({"messages": [summarise_message(m) for m in messages], "tools": tool_names(tools),
                              "media": media_parts(messages),
                              "sampling": sampling.to_record() if sampling else None, "seed": seed,
                              "max_tokens": max_tokens, "thinking": thinking})
        if self.index >= len(self.turns):
            if self.on_exhaust == "error":
                raise ScriptExhausted(f"{self.name}: script has {len(self.turns)} turns, asked for more")
            return turn_to_result({"text": ""}, self.index)
        turn = self.turns[self.index]
        idx = self.index
        self.index += 1
        if callable(turn):
            out = turn(messages, tools, idx)
            result = out if isinstance(out, ChatResult) else turn_to_result(out, idx)
        else:
            result = turn_to_result(turn, idx)
        result.request = self.requests[-1] | {"messages": len(messages)}
        return result


class NullClient(ScriptedClient):
    """Answers every request with the same text and no tool calls."""

    def __init__(self, text: str = "OK.") -> None:
        super().__init__([], name="null", on_exhaust="stop")
        self.text = text

    def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
        self.requests.append({"messages": len(messages), "tools": tool_names(tools), "media": media_parts(messages)})
        r = turn_to_result({"text": self.text}, len(self.requests) - 1)
        r.request = self.requests[-1]
        return r
