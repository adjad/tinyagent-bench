"""Glue between the sidekick tier and the shared reference loop (`edat/loop.py`).

Tool mode runs every user turn through `edat.loop`'s `edat-reference-v1`
loop, unedited, using its extension points (docs/benchmark/agent-loop.md):

* `max_calls`: the 6-call cap. The cap is per trial, and a trial can have two
  user turns (the scripted approve or deny reply), so each turn gets the
  calls that are left. A call past the cap gets the loop's `call_limit` error
  and the turn ends with `stop: max_calls`.
* `guard`: the static pre-screen (`DevEnvironment.guard`). A hard-deny command,
  a path outside the working folder or a hard-deny file body never runs; the
  model sees `blocked: not permitted in this environment`.

Models come through the shared adapter (`edat/models/`); engine faults are
`edat.models.EngineError`, which the loop retries once and then excludes.
`TurnResult` is the sidekick's JSON-ready view of the loop's `TurnTrace`.
"""
from __future__ import annotations

from dataclasses import dataclass

from edat.loop import LoopSettings, TurnTrace, get_loop

LOOP_NAME = "reference"
RESULT_CAP_TOKENS = 1024        # 4 KB of tool output, spec 12 §3.3


@dataclass
class TurnResult:
    """What tool mode keeps from one user turn."""
    final_text: str
    stop: str                         # answer | empty | max_steps | max_calls | engine_error
    excluded: bool
    engine_error: str | None
    steps: list[dict]
    calls: list[dict]
    events: list[dict]                # guard blocks
    loop: str

    @classmethod
    def from_trace(cls, tr: TurnTrace, loop_name: str) -> "TurnResult":
        steps = [s.to_record(include_raw=False) for s in tr.steps]
        calls = [c.to_record() for s in tr.steps for c in s.calls]
        for s in steps:
            s["n_calls"] = len(s.pop("calls"))
        return cls(tr.final_text, tr.stop, tr.excluded, tr.engine_error, steps, calls, list(tr.events), loop_name)

    def to_record(self) -> dict:
        return dict(self.__dict__)


def settings(*, max_steps: int, max_calls: int, guard=None, max_tokens: int = 1024, thinking: bool = False,
             sampling=None, seed: int | None = None) -> LoopSettings:
    return LoopSettings(max_steps=max_steps, result_cap_tokens=RESULT_CAP_TOKENS, max_tokens=max_tokens,
                        thinking=thinking, sampling=sampling, seed=seed, max_calls=max_calls, guard=guard)


def run_turn(client, env, messages: list[dict], *, menu: list[str], loop_settings: LoopSettings) -> TurnResult:
    loop = get_loop(LOOP_NAME)
    tr = loop.run_turn(client, env, messages, menu=menu, settings=loop_settings)
    return TurnResult.from_trace(tr, loop.name)
