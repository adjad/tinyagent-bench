"""Agent loops. The reference loop is tinyagent-bench's minimal, published-track loop.

Spec 02 §2.1. The reference loop, per user turn:

* at most `max_steps` (8) model steps;
* every call in a step runs, in the order emitted; results go back as
  `role: tool` messages;
* no nudges, re-prompts, dedupe, repairs or claim rewriting;
* a fixed per-tool timeout (10 s, virtual: a tool that reports more simulated
  time than that is answered with the timeout text; nothing waits);
* tool results longer than `result_cap_tokens` (4,000, at 4 characters per
  token) are cut with an announced note (§3.19);
* a malformed call (arguments not a JSON object) is answered with an error
  result and recorded as the model's `malformed` call; it never runs;
* a call to a tool that is not offered is answered with an error and never
  runs (`hallucinated_tool` if the lane has no such tool, `unoffered_tool`
  otherwise);
* a call the host must confirm goes to the approver first;
* an engine fault is retried once; if it repeats, the turn stops and the
  trial is excluded.

Loops are pluggable behind `AgentLoop`. Which loop the board publishes is not
decided by this module: callers choose one by name from `LOOPS`, and nothing
else in tinyagent-bench depends on that choice.

Extension points (docs/benchmark/agent-loop.md), all off by default so the
reference loop is unchanged: the tool set is whatever `ToolEnvironment` the
caller passes; `LoopSettings.observation` rewrites what the model sees of
earlier tool results (spec 11 keeps only the latest screen); `max_steps` can
be set per case with `per_case_step_limit` (spec 11: max(6, 2 x optimal),
ceiling 24); `max_calls` caps executed calls per turn (spec 12: 6);
`stop_tools` end the turn after a call such as `finish`; `guard` can block a
call before it runs (spec 12's static pre-screen), recorded in `trace.events`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Protocol

from edat.faults.approver import Approver
from edat.models.base import ChatResult, EngineError, ModelClient, Sampling
from edat.state.env import ToolEnvironment, ToolResult

CHARS_PER_TOKEN = 4


# observation(messages, step) -> the message list sent to the model this step.
# It must not mutate `messages` (the stored transcript keeps every result in full).
ObservationPolicy = Callable[[list[dict], int], list[dict]]
# guard(name, arguments, step) -> None to allow, or (result_text, event_detail) to block.
CallGuard = Callable[[str, dict, int], "tuple[str, dict] | None"]


@dataclass
class LoopSettings:
    max_steps: int = 8
    tool_timeout_s: float = 10.0
    result_cap_tokens: int = 4000
    max_tokens: int = 1024
    thinking: bool = False
    sampling: Sampling | None = None
    seed: int | None = None
    engine_retries: int = 1
    observation: ObservationPolicy | None = None   # None: every result stays in full
    max_calls: int | None = None                   # executed-call cap per turn; None: no cap
    stop_tools: tuple[str, ...] = ()               # a call to one of these ends the turn after it runs
    guard: CallGuard | None = None                 # pre-run block (never asks the approver)


def per_case_step_limit(optimal_steps: int | None, *, floor: int = 6, factor: int = 2, ceiling: int = 24) -> int:
    """Spec 11's per-case step budget: max(floor, factor x optimal), at most `ceiling`."""
    return min(ceiling, max(floor, factor * int(optimal_steps or 0)))


def keep_latest_results(stub: Callable[[dict, int], str] | None = None, *, keep: int = 1) -> ObservationPolicy:
    """An observation policy that keeps the last `keep` tool results in full and
    replaces each earlier one with `stub(message, ordinal)` (default: its first
    line plus "(earlier output omitted)"). Assistant messages are untouched."""
    def default_stub(m: dict, n: int) -> str:
        first = str(m.get("content") or "").splitlines()[:1]
        return (first[0] + " " if first else "") + "(earlier output omitted)"
    stub_fn = stub or default_stub

    def view(messages: list[dict], step: int) -> list[dict]:
        idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        old = set(idx[:-keep] if keep else idx)
        ordinal = {i: n for n, i in enumerate(idx)}
        return [({**m, "content": stub_fn(m, ordinal[i])} if i in old else m) for i, m in enumerate(messages)]
    return view


@dataclass
class CallRecord:
    step: int                 # 1-based model step within the turn
    index: int                # position within the step
    id: str
    name: str
    arguments_raw: str
    arguments: dict | None
    parse_error: str | None
    known: bool               # the lane has a tool of this name
    offered: bool             # it was in this turn's menu
    confirm_asked: bool = False
    approved: bool | None = None
    executed: bool = False
    outcome: str | None = None
    effect: bool = False
    content: str = ""
    truncated: bool = False
    virtual_elapsed_s: float = 0.0
    origin: str = "model"     # "host": a call the host loop made itself (Harbor's loop only)

    def to_record(self) -> dict:
        return dict(self.__dict__)


@dataclass
class StepRecord:
    step: int
    text: str
    reasoning: str
    finish_reason: str | None
    calls: list[CallRecord]
    usage: dict
    timing: dict
    raw: object = None
    malformed: int = 0

    def to_record(self, include_raw: bool = True) -> dict:
        out = {"step": self.step, "text": self.text, "reasoning": self.reasoning,
               "finish_reason": self.finish_reason, "calls": [c.to_record() for c in self.calls],
               "usage": self.usage, "timing": self.timing, "malformed": self.malformed}
        if include_raw:
            out["raw"] = self.raw
        return out


@dataclass
class TurnTrace:
    steps: list[StepRecord] = field(default_factory=list)
    final_text: str = ""
    stop: str = "answer"      # answer | max_steps | engine_error | empty | max_calls | stop_tool
    excluded: bool = False
    engine_error: str | None = None
    engine_retries: int = 0
    messages_added: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)   # guard blocks: {step, index, name, detail}
    # Set when the host, not the model, made the turn impossible (a host loop
    # that refuses a tool the case needs, a case feature it has no field for).
    # The trial is then not applicable, never the model's failure.
    not_applicable: str | None = None
    repairs: list[dict] = field(default_factory=list)   # host loops: repairs that fired (B28)

    @property
    def calls(self) -> list[CallRecord]:
        return [c for s in self.steps for c in s.calls]

    def to_record(self, include_raw: bool = True) -> dict:
        return {"steps": [s.to_record(include_raw) for s in self.steps], "final_text": self.final_text,
                "stop": self.stop, "excluded": self.excluded, "engine_error": self.engine_error,
                "engine_retries": self.engine_retries, "events": self.events,
                "not_applicable": self.not_applicable, "repairs": self.repairs}


class AgentLoop(Protocol):
    name: str

    def run_turn(self, client: ModelClient, env: ToolEnvironment, messages: list[dict], *,
                 menu: list[str], settings: LoopSettings, approver: Approver) -> TurnTrace: ...


def _error_text(env, name: str, message: str) -> str:
    fmt = getattr(env, "format_error", None)
    if callable(fmt):
        return fmt(name, message)
    if getattr(env, "lane", "") == "assistant":
        return f"(error calling {name}: {message})"
    return json.dumps({"error": message})


def cap_result(text: str, cap_tokens: int) -> tuple[str, bool]:
    limit = cap_tokens * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text, False
    cut = len(text) - limit
    return (text[:limit] + f"\n[… {cut} more characters were cut. Call again with offset={limit} "
            "or a narrower query.]"), True


class ReferenceLoop:
    name = "edat-reference-v1"

    def _chat(self, client: ModelClient, messages, tools, s: LoopSettings, trace: TurnTrace) -> ChatResult | None:
        attempts = 0
        while True:
            try:
                return client.chat(messages, tools=tools or None, sampling=s.sampling, seed=s.seed,
                                   max_tokens=s.max_tokens, thinking=s.thinking)
            except EngineError as exc:
                attempts += 1
                trace.engine_retries = attempts
                if attempts > s.engine_retries or not exc.retryable:
                    trace.excluded = True
                    trace.engine_error = f"{exc.kind}: {exc.detail}"
                    trace.stop = "engine_error"
                    return None

    def run_turn(self, client: ModelClient, env: ToolEnvironment, messages: list[dict], *,
                 menu: list[str], settings: LoopSettings | None = None,
                 approver: Approver | None = None) -> TurnTrace:
        s = settings or LoopSettings()
        approver = approver or Approver()
        trace = TurnTrace()
        tools = env.schemas(menu) if menu else []
        offered = set(menu)
        executed = 0
        for step in range(1, s.max_steps + 1):
            view = s.observation(messages, step) if s.observation else messages
            result = self._chat(client, view, tools, s, trace)
            if result is None:
                return trace
            msg = result.assistant_message()
            messages.append(msg)
            trace.messages_added.append(msg)
            rec = StepRecord(step=step, text=result.text, reasoning=result.reasoning,
                             finish_reason=result.finish_reason, calls=[],
                             usage=result.usage.to_record(), timing=result.timing.to_record(),
                             raw=result.raw, malformed=len(result.malformed_calls))
            trace.steps.append(rec)
            if not result.tool_calls:
                trace.final_text = result.text or ""
                trace.stop = "answer" if (result.text or "").strip() else "empty"
                return trace
            for i, call in enumerate(result.tool_calls):
                info = env.info(call.name) if call.name else None
                cr = CallRecord(step=step, index=i, id=call.id, name=call.name,
                                arguments_raw=call.arguments_raw, arguments=call.arguments,
                                parse_error=call.parse_error, known=info is not None,
                                offered=call.name in offered)
                if call.parse_error is not None:
                    content = _error_text(env, call.name or "?", f"arguments were not valid JSON ({call.parse_error})")
                elif info is None:
                    content = _error_text(env, call.name, f"there is no tool named {call.name!r}")
                elif not cr.offered:
                    content = _error_text(env, call.name, f"the tool {call.name!r} is not available")
                elif s.max_calls is not None and executed >= s.max_calls:
                    content, cr.outcome = _error_text(env, call.name, f"call limit of {s.max_calls} reached"), "call_limit"
                elif s.guard and (blocked := s.guard(call.name, dict(call.arguments or {}), step)):
                    content, cr.outcome = blocked[0], "blocked"
                    trace.events.append({"step": step, "index": i, "name": call.name, "detail": blocked[1]})
                else:
                    approved, deny_text = True, None
                    if info.confirm:
                        cr.confirm_asked = True
                        approved, deny_text = approver.decide(call.name, call.arguments or {}, step)
                        cr.approved = approved
                    if not approved:
                        content, cr.outcome = deny_text or "", "denied"
                    else:
                        res: ToolResult = env.call(call.name, dict(call.arguments or {}))
                        cr.executed = True
                        executed += 1
                        cr.outcome, cr.effect = res.outcome, res.effect
                        cr.virtual_elapsed_s = res.virtual_elapsed_s
                        content = res.content
                        if res.virtual_elapsed_s > s.tool_timeout_s and res.outcome != "timeout":
                            content = _error_text(env, call.name,
                                                  f"{call.name} timed out after {int(s.tool_timeout_s)} s")
                            cr.outcome = "timeout"
                content, cr.truncated = cap_result(content, s.result_cap_tokens)
                cr.content = content
                rec.calls.append(cr)
                tool_msg = {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}
                messages.append(tool_msg)
                trace.messages_added.append(tool_msg)
            if s.stop_tools and any(c.executed and c.name in s.stop_tools for c in rec.calls):
                trace.final_text = result.text or ""
                trace.stop = "stop_tool"
                return trace
            if s.max_calls is not None and executed >= s.max_calls:
                trace.final_text = result.text or ""
                trace.stop = "max_calls"
                return trace
        trace.stop = "max_steps"
        return trace


LOOPS: dict[str, Callable[[], AgentLoop]] = {ReferenceLoop.name: ReferenceLoop, "reference": ReferenceLoop}


def get_loop(name: str = "reference") -> AgentLoop:
    try:
        return LOOPS[name]()
    except KeyError:
        raise KeyError(f"unknown loop {name!r}; known: {sorted(LOOPS)}") from None


def register_loop(name: str, factory: Callable[[], AgentLoop]) -> None:
    LOOPS[name] = factory
