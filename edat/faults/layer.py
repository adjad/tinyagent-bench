"""The fault layer (spec 02 §3.7, B4): tool-result faults over any environment.

`FaultyEnvironment(env, faults)` wraps a lane environment. It passes every
method through and changes only what `call` returns, for the calls a fault
names:

    - tool: <name>
      on_call: 1 | 2 | [1, 3] | all     # which calls of that tool (1-based); default 1
      kind: error | empty | timeout | stall | partial | unavailable | unknown_outcome
      text: "..."                       # what the model sees (a lane default if left out)
      applies_effect: false | true | {<arg>: value}
      when_args: {<arg>: <matcher>}     # optional: only calls whose arguments match

* `error`, `empty`, `unavailable`: the tool does not run (unless
  `applies_effect`), the model sees `text`.
* `timeout` and `stall`: the tool does not run; after a *virtual* 10 s (no real
  wait) the model sees the timeout text. A stall is a tool that never answers;
  the loop's per-tool timeout turns it into the same text.
* `partial`: the tool runs; the model sees `text`, or, with no text, the first
  half of the real result and a truncation note.
* `unknown_outcome`: the tool runs only when `applies_effect` is true; the model
  sees `text` either way (the receipt is lost, the effect may have happened).
* `applies_effect: {arg: value}` runs the tool with those argument values
  instead of the model's (a wrong value is applied, as in pev_wrong_result).

Every fault that fired is recorded in `fired` for the trajectory.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from edat.grading.matchers import match_args
from edat.state.clock import Clock
from edat.state.env import ToolEnvironment, ToolInfo, ToolResult

TIMEOUT_S = 10.0


def default_text(kind: str, tool: str, lane: str) -> str:
    neutral = lane == "neutral"
    if kind in ("timeout", "stall"):
        return f'{{"error": "{tool} timed out after 10 s"}}' if neutral else \
            f"(error: {tool} timed out after 10 s)"
    if kind == "error":
        return f'{{"error": "{tool} failed: the service did not respond"}}' if neutral else \
            f"(error running {tool}: the service did not respond)"
    if kind == "empty":
        return '{"items": [], "next_offset": null, "total": 0}' if neutral else "No results."
    if kind == "unavailable":
        return (f'{{"error": "{tool} is unavailable: access to this data has not been granted"}}'
                if neutral else
                f"(error running {tool}: access needed. Allow access in System Settings > Privacy & Security, then try again)")
    if kind == "unknown_outcome":
        return (f'{{"error": "{tool}: the service did not confirm the change before the connection closed"}}'
                if neutral else
                f"(error running {tool}: the app did not confirm the change before the connection closed; it may or may not have happened)")
    return ""


def _selected(on_call, n: int) -> bool:
    if on_call in (None, 1, "1"):
        return n == 1
    if on_call == "all":
        return True
    if isinstance(on_call, list):
        return n in [int(x) for x in on_call]
    return n == int(on_call)


@dataclass
class FaultyEnvironment:
    env: ToolEnvironment
    faults: list[dict]
    clock: Clock | None = None
    counts: dict[str, int] = field(default_factory=dict)
    fired: list[dict] = field(default_factory=list)

    @property
    def lane(self) -> str:
        return self.env.lane

    def __getattr__(self, name):   # everything else passes through (format_error, extras)
        if name in ("env", "faults", "counts", "fired", "clock") or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.env, name)

    def system_prompt(self, **kw) -> str:
        return self.env.system_prompt(**kw)

    def tool_names(self) -> list[str]:
        return self.env.tool_names()

    def schemas(self, names=None) -> list[dict]:
        return self.env.schemas(names)

    def info(self, name: str) -> ToolInfo | None:
        return self.env.info(name)

    def seed(self, records) -> None:
        self.env.seed(records)

    def state(self):
        return self.env.state()

    def _fault_for(self, name: str, arguments: dict, n: int) -> dict | None:
        clock = self.clock or self.env.state().clock
        for f in self.faults:
            if f.get("tool") != name or not _selected(f.get("on_call", 1), n):
                continue
            if f.get("when_args") and match_args(f["when_args"], arguments, clock):
                continue
            return f
        return None

    def call(self, name: str, arguments: dict) -> ToolResult:
        n = self.counts[name] = self.counts.get(name, 0) + 1
        fault = self._fault_for(name, arguments, n)
        if fault is None:
            return self.env.call(name, arguments)
        kind = fault["kind"]
        text = fault.get("text") or default_text(kind, name, self.lane)
        applies = fault.get("applies_effect", kind == "partial")
        real: ToolResult | None = None
        if applies:
            args = dict(arguments)
            if isinstance(applies, dict):
                args.update(applies)
            real = self.env.call(name, args)
        self.fired.append({"tool": name, "call": n, "kind": kind, "applied": bool(applies)})
        effect = bool(real and real.effect)
        if kind in ("timeout", "stall"):
            return ToolResult(content=text, outcome="timeout", effect=effect, virtual_elapsed_s=TIMEOUT_S,
                              meta={"fault": kind})
        if kind == "partial":
            if not fault.get("text") and real is not None:
                body = real.content
                cut = max(1, len(body) // 2)
                text = body[:cut] + f"\n[... partial result: {len(body) - cut} more characters were not returned]"
            return ToolResult(content=text, outcome="succeeded", effect=effect, meta={"fault": kind})
        if kind == "unknown_outcome":
            return ToolResult(content=text, outcome="unknown", effect=effect, meta={"fault": kind})
        if kind == "empty":
            return ToolResult(content=text, outcome="no_match", effect=effect, meta={"fault": kind})
        if kind == "unavailable":
            return ToolResult(content=text, outcome="needs_input", effect=effect, meta={"fault": kind})
        return ToolResult(content=text, outcome="failed", effect=effect, meta={"fault": kind})
