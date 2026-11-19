"""Glue between the sidekick tier and the shared model adapter and reference loop.

* **Model adapter** (`edat/models/`, on `build/agentic`): any object with
  `chat(messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking)`
  returning a `ChatResult`-like object (`text`, `tool_calls`, `reasoning`,
  `finish_reason`, `usage`, `timing`, `raw`, `assistant_message()`). The real
  clients are used when `edat.models` is importable; `StubClient` below
  follows the same contract for tests until then.
* **Reference loop** (`edat/loop.py`, on `build/agentic`): when importable,
  tool mode runs through `ReferenceLoop.run_turn` with the `edat-dev-v1`
  environment and `LoopSettings(max_steps=...)`. Until then `MiniLoop` here
  follows the same rules (02 §2.1): every call in a step runs in order and its
  result goes back as a `role: tool` message; no nudges, repairs or re-prompts;
  malformed calls and unknown tools get an error result and never run; tool
  results are cut at a fixed cap with a note; an engine fault is retried once
  and then the trial is excluded.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

CHARS_PER_TOKEN = 4

try:                                     # build/agentic's loop, once merged
    from edat.loop import LoopSettings as _RefSettings, ReferenceLoop as _RefLoop  # type: ignore
    HAVE_REFERENCE_LOOP = True
except Exception:                        # noqa: BLE001 - absent or not importable on this branch
    _RefSettings = _RefLoop = None
    HAVE_REFERENCE_LOOP = False

try:
    from edat.models import ScriptedClient as _AdapterScripted  # type: ignore  # noqa: F401
    HAVE_ADAPTER = True
except Exception:                        # noqa: BLE001
    HAVE_ADAPTER = False


# ------------------------------------------------------------------ stub client (adapter contract)

class StubEngineError(Exception):
    """Mirrors edat.models.base.EngineError(kind, detail)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail
        self.retryable = kind in ("transport", "timeout", "server", "rate_limit")


@dataclass
class StubToolCall:
    id: str
    name: str
    arguments_raw: str
    arguments: dict | None
    parse_error: str | None = None


@dataclass
class _Rec:
    data: dict

    def to_record(self) -> dict:
        return dict(self.data)


@dataclass
class StubResult:
    text: str
    tool_calls: list[StubToolCall] = field(default_factory=list)
    reasoning: str = ""
    finish_reason: str | None = "stop"
    usage: _Rec = field(default_factory=lambda: _Rec({"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}))
    timing: _Rec = field(default_factory=lambda: _Rec({"ttft_s": None, "total_s": 0.0}))
    raw: Any = None

    @property
    def malformed_calls(self) -> list[StubToolCall]:
        return [c for c in self.tool_calls if c.parse_error is not None]

    def assistant_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.arguments_raw}} for c in self.tool_calls]
        return msg


def _parse_args(raw: str) -> tuple[dict | None, str | None]:
    if raw.strip() == "":
        return {}, None
    try:
        v = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(v, dict):
        return None, "arguments are not a JSON object"
    return v, None


class StubClient:
    """Replays scripted turns, like edat.models.ScriptedClient.

    A turn is {"text", "calls": [{"name", "arguments"} | {"name", "arguments_raw"}],
    "reasoning", "finish_reason"}, or {"engine_error": kind}, or a callable
    (messages, tools, index) -> turn. Past the end it answers "OK.".
    """

    def __init__(self, turns: list | Callable | None = None, *, model_id: str = "stub"):
        self.turns = turns if turns is not None else []
        self.requests: list[dict] = []
        self.model_id = model_id

    def describe(self) -> dict:
        return {"runtime": "stub", "model": self.model_id}

    def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
        i = len(self.requests)
        self.requests.append({"messages": json.loads(json.dumps(messages)), "tools": [t["function"]["name"] for t in tools or []],
                              "seed": seed, "max_tokens": max_tokens, "thinking": thinking})
        if callable(self.turns):
            turn = self.turns(messages, tools, i)
        else:
            turn = self.turns[i] if i < len(self.turns) else {"text": "OK."}
        if isinstance(turn, str):
            turn = {"text": turn}
        if "engine_error" in turn:
            raise StubEngineError(turn["engine_error"], "scripted")
        calls = []
        for k, c in enumerate(turn.get("calls") or []):
            raw = c["arguments_raw"] if "arguments_raw" in c else json.dumps(c.get("arguments", {}))
            args, err = _parse_args(raw)
            calls.append(StubToolCall(f"call_{i}_{k}", c["name"], raw, args, err))
        t0 = time.monotonic()
        return StubResult(turn.get("text", ""), calls, turn.get("reasoning", ""),
                          turn.get("finish_reason", "tool_calls" if calls else "stop"),
                          timing=_Rec({"ttft_s": None, "total_s": time.monotonic() - t0}))


def is_engine_error(exc: BaseException) -> bool:
    return type(exc).__name__ in ("EngineError", "StubEngineError") and hasattr(exc, "kind")


# ------------------------------------------------------------------ loop

@dataclass
class MiniSettings:
    max_steps: int = 8
    tool_timeout_s: float = 10.0
    result_cap_tokens: int = 1024
    max_tokens: int = 1024
    thinking: bool = False
    sampling: Any = None
    seed: int | None = None
    engine_retries: int = 1


@dataclass
class TurnResult:
    """What tool mode needs from one user turn, whichever loop ran it."""
    final_text: str
    stop: str                         # answer | max_steps | engine_error | empty
    excluded: bool
    engine_error: str | None
    steps: list[dict]
    calls: list[dict]
    usage: list[dict]
    timings: list[dict]
    loop: str


def cap_result(text: str, cap_tokens: int) -> str:
    limit = cap_tokens * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[… {len(text) - limit} more characters were cut.]"


class MiniLoop:
    name = "edat-sidekick-mini-v1"

    def run_turn(self, client, env, messages: list[dict], *, menu: list[str], settings: MiniSettings) -> TurnResult:
        tools = env.schemas(menu)
        offered = set(menu)
        steps, calls, usage, timings = [], [], [], []
        for step in range(1, settings.max_steps + 1):
            res, err = None, None
            for attempt in range(settings.engine_retries + 1):
                try:
                    res = client.chat(messages, tools=tools, sampling=settings.sampling, seed=settings.seed,
                                      max_tokens=settings.max_tokens, thinking=settings.thinking)
                    break
                except Exception as exc:          # noqa: BLE001
                    if not is_engine_error(exc):
                        raise
                    err = f"{exc.kind}: {getattr(exc, 'detail', '')}"
                    if not getattr(exc, "retryable", True):
                        break
            if res is None:
                return TurnResult("", "engine_error", True, err, steps, calls, usage, timings, self.name)
            messages.append(res.assistant_message())
            usage.append(res.usage.to_record() if hasattr(res.usage, "to_record") else dict(res.usage or {}))
            timings.append(res.timing.to_record() if hasattr(res.timing, "to_record") else dict(res.timing or {}))
            steps.append({"step": step, "text": res.text, "reasoning": getattr(res, "reasoning", ""),
                          "finish_reason": res.finish_reason, "n_calls": len(res.tool_calls)})
            if not res.tool_calls:
                text = res.text or ""
                return TurnResult(text, "answer" if text.strip() else "empty", False, None, steps, calls, usage, timings, self.name)
            for i, c in enumerate(res.tool_calls):
                rec = {"step": step, "index": i, "name": c.name, "arguments": c.arguments,
                       "arguments_raw": c.arguments_raw, "parse_error": c.parse_error,
                       "known": env.info(c.name) is not None, "offered": c.name in offered, "executed": False}
                if c.parse_error is not None:
                    content = f"error: arguments were not valid JSON ({c.parse_error})"
                elif env.info(c.name) is None:
                    content = f"error: there is no tool named {c.name!r}"
                elif c.name not in offered:
                    content = f"error: the tool {c.name!r} is not available"
                else:
                    r = env.call(c.name, dict(c.arguments or {}))
                    rec["executed"], rec["outcome"] = True, r.outcome
                    content = r.content
                content = cap_result(content, settings.result_cap_tokens)
                rec["content"] = content
                calls.append(rec)
                messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": content})
        return TurnResult("", "max_steps", False, None, steps, calls, usage, timings, self.name)


class ReferenceLoopAdapter:
    """Runs build/agentic's ReferenceLoop and converts its TurnTrace."""

    name = "edat-reference-v1"

    def run_turn(self, client, env, messages, *, menu, settings: MiniSettings) -> TurnResult:
        s = _RefSettings(max_steps=settings.max_steps, tool_timeout_s=settings.tool_timeout_s,
                         result_cap_tokens=settings.result_cap_tokens, max_tokens=settings.max_tokens,
                         thinking=settings.thinking, sampling=settings.sampling, seed=settings.seed,
                         engine_retries=settings.engine_retries)
        tr = _RefLoop().run_turn(client, env, messages, menu=menu, settings=s)
        steps, calls, usage, timings = [], [], [], []
        for st in tr.steps:
            steps.append({"step": st.step, "text": st.text, "reasoning": st.reasoning,
                          "finish_reason": st.finish_reason, "n_calls": len(st.calls)})
            usage.append(st.usage)
            timings.append(st.timing)
            for c in st.calls:
                calls.append({"step": c.step, "index": c.index, "name": c.name, "arguments": c.arguments,
                              "arguments_raw": c.arguments_raw, "parse_error": c.parse_error, "known": c.known,
                              "offered": c.offered, "executed": c.executed, "outcome": c.outcome, "content": c.content})
        return TurnResult(tr.final_text, tr.stop, tr.excluded, tr.engine_error, steps, calls, usage, timings, self.name)


def get_loop(prefer_reference: bool = True):
    if prefer_reference and HAVE_REFERENCE_LOOP:
        return ReferenceLoopAdapter()
    return MiniLoop()
