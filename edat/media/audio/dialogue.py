"""The multi-turn spoken driver for A3 follow-ups (spec 05 §5 A3, "Spoken follow-ups").

A :class:`SpokenDialogue` is 2-4 :class:`SpokenTurn` s. Each turn is spoken
(one clip per turn), has its own expected calls, and describes the world state
it leaves behind (``state``: for example the event turn 1 created), so a turn
such as "move it to Friday" is scored against what came before.

:func:`run_spoken_dialogue` keeps one message history across the turns:

* the user's turn is the **transcript** in ``two_pass`` and ``pipeline`` and the
  **audio part** itself in ``native`` (earlier turns' audio stays in history);
* the assistant's turn is the model's own tool calls (``history="model"``, the
  default) or the case's oracle calls (``history="oracle"``, which scores each
  turn given a correct past); each call gets the case's scripted tool result,
  and a scripted reply closes the turn so the next user turn follows an
  assistant message.

Each turn is scored through the same seam as single commands
(:func:`edat.media.audio.commands.score_calls`: matcher, ``no_tool``,
``forbid``, corrections) with entity accuracy; a dialogue passes when every
scored turn passes. Timings per turn follow §4.1 (spoken-command latency from
the adapter's ``tool_call_done_s``, falling back to ``total_s``).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from edat.media.audio import commands as cmd
from edat.media.audio.latency import spoken_command_latency
from edat.media.audio.wav import Clip


@dataclass
class SpokenTurn:
    text: str                                    # typed twin of this turn
    spoken: str                                  # words said (no digits)
    expect: list[dict] | None                    # None: a context turn, not scored
    entities: list[cmd.Entity] = field(default_factory=list)
    script: str | None = None                    # render text with <pause s> markers
    no_tool: bool = False
    forbid: list[str] = field(default_factory=list)
    abandoned: list[cmd.Entity] = field(default_factory=list)
    abandoned_tools: list[str] = field(default_factory=list)
    oracle: list[dict] = field(default_factory=list)      # [{name, arguments}] a right answer makes
    results: dict[str, str] = field(default_factory=dict)  # tool name -> scripted tool result
    reply: str = "Done."                         # scripted assistant reply that closes the turn
    state: dict = field(default_factory=dict)    # world state after this turn
    tags: list[str] = field(default_factory=list)

    @property
    def render_text(self) -> str:
        return self.script if self.script is not None else self.spoken


@dataclass
class SpokenDialogue:
    dialogue_id: str
    turns: list[SpokenTurn]
    today: str = cmd.TODAY.isoformat()
    world: dict = field(default_factory=dict)    # state before turn 1
    source: str = "template"
    tags: list[str] = field(default_factory=list)
    family: str = "follow_ups"
    tool_menu: str = "a3"
    system_policy: str = ""
    origin: dict = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return self.dialogue_id

    def turn_case(self, i: int) -> cmd.CommandCase:
        """Turn ``i`` as a :class:`~edat.media.audio.commands.CommandCase`, for scoring."""
        t = self.turns[i]
        return cmd.CommandCase(f"{self.dialogue_id}/t{i}", t.text, t.spoken, t.expect or [],
                               list(t.entities), today=self.today, source=self.source,
                               tags=list(self.tags) + list(t.tags), family=self.family,
                               script=t.script, abandoned=list(t.abandoned),
                               abandoned_tools=list(t.abandoned_tools), forbid=list(t.forbid),
                               no_tool=t.no_tool, tool_menu=self.tool_menu,
                               system_policy=self.system_policy)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class TurnOutcome:
    index: int
    scored: bool
    passed: bool | None
    transcript: str | None
    calls: list[dict]
    mismatches: list[str]
    entities_right: int
    entities_total: int
    entity_detail: list[dict]
    command_latency_s: float | None
    tool_call_time_source: str | None
    ttft_s: float | None
    results: list[Any]                       # ChatResults: transcription (if any), then agent


@dataclass
class DialogueOutcome:
    dialogue_id: str
    configuration: str
    history: str
    turns: list[TurnOutcome]
    messages: list[dict]                     # the final message history (audio kept as parts)

    @property
    def scored_turns(self) -> list[TurnOutcome]:
        return [t for t in self.turns if t.scored]

    @property
    def turns_passed(self) -> int:
        return sum(1 for t in self.scored_turns if t.passed)

    @property
    def passed(self) -> bool:
        """Per-dialogue pass: every scored turn passed."""
        return all(t.passed for t in self.scored_turns)

    @property
    def timings(self) -> dict:
        lat = [t.command_latency_s for t in self.turns]
        total = 0.0
        for t in self.turns:
            for r in t.results:
                v = getattr(getattr(r, "timing", None), "total_s", None)
                total += v or 0.0
        return {"turn_command_latency_s": lat, "model_time_s": round(total, 6),
                "turn_ttft_s": [t.ttft_s for t in self.turns]}

    def summary(self) -> dict:
        return {"dialogue_id": self.dialogue_id, "configuration": self.configuration,
                "history": self.history, "passed": self.passed,
                "turns_scored": len(self.scored_turns), "turns_passed": self.turns_passed,
                "turn_pass": [t.passed for t in self.turns], **self.timings}


def _tool_calls_message(text: str, calls: list[dict], turn_index: int) -> dict:
    return {"role": "assistant", "content": text or "",
            "tool_calls": [{"id": f"call_t{turn_index}_{k}", "type": "function",
                            "function": {"name": c["name"],
                                         "arguments": json.dumps(c.get("arguments") or {})}}
                           for k, c in enumerate(calls)]}


def default_tool_result(turn: SpokenTurn, call: dict) -> str:
    return turn.results.get(call["name"], "Done.")


HISTORY_MODES = ("model", "oracle")


def run_spoken_dialogue(dialogue: SpokenDialogue, clips: list[Clip], *, configuration: str,
                        client, matcher: cmd.CommandMatcher, recogniser=None, sampling=None,
                        seed=None, thinking: bool = False, transcribe_max_tokens: int = 240,
                        agent_max_tokens: int = 512, nonce: str = "",
                        tools: list[dict] | None = None, history: str = "model",
                        tool_result: Callable[[SpokenTurn, dict], str] | None = None
                        ) -> DialogueOutcome:
    """Run one spoken dialogue in one configuration; ``clips[i]`` is turn ``i``'s audio.

    Engine errors propagate to the caller, as in :func:`~edat.media.audio.commands.run_spoken_command`.
    """
    if configuration not in cmd.CONFIGURATIONS:
        raise ValueError(f"unknown configuration {configuration!r}")
    if history not in HISTORY_MODES:
        raise ValueError(f"history must be one of {HISTORY_MODES}")
    if len(clips) != len(dialogue.turns):
        raise ValueError(f"{dialogue.dialogue_id}: {len(dialogue.turns)} turns, {len(clips)} clips")
    rec = client if configuration == "two_pass" else recogniser
    if configuration == "pipeline" and rec is None:
        raise ValueError("the pipeline configuration needs a recogniser client")
    tools = cmd.TOOLS if tools is None else tools
    result_for = tool_result or default_tool_result
    messages: list[dict] = [{"role": "system", "content": cmd.agent_system(dialogue, nonce=nonce)}]
    outcomes: list[TurnOutcome] = []
    for i, (turn, clip) in enumerate(zip(dialogue.turns, clips)):
        results = []
        transcript = None
        if configuration == "native":
            user = {"role": "user", "content": [cmd.clip_part(clip)]}
        else:
            t = rec.chat(cmd.transcribe_messages(clip, nonce=f"{nonce}t{i}" if nonce else ""),
                         sampling=sampling, seed=seed, max_tokens=transcribe_max_tokens,
                         thinking=False if rec is not client else thinking)
            results.append(t)
            transcript = (t.text or "").strip()
            user = {"role": "user", "content": transcript}
        messages.append(user)
        r = client.chat(messages, tools=tools, sampling=sampling, seed=seed,
                        max_tokens=agent_max_tokens, thinking=thinking)
        results.append(r)
        calls = cmd.calls_of(r)
        case = dialogue.turn_case(i)
        scored = turn.expect is not None or turn.no_tool
        if scored:
            m = cmd.score_calls(case, calls, matcher)
            passed, mismatches = m.passed, m.mismatches
        else:
            passed, mismatches = None, []
        right, total, detail = cmd.entity_accuracy(case, calls)
        latency, src = spoken_command_latency(results, configuration)
        if not passed:
            latency = None              # only a correct call has a command latency
        outcomes.append(TurnOutcome(i, scored, passed, transcript, calls, mismatches, right, total,
                                    detail, latency, src,
                                    getattr(getattr(results[0], "timing", None), "ttft_s", None),
                                    results))
        shown = calls if history == "model" else [{"name": c["name"],
                                                   "arguments": c.get("arguments") or {}}
                                                  for c in turn.oracle]
        shown = [c for c in shown if c.get("arguments") is not None] if history == "model" else shown
        if shown:
            messages.append(_tool_calls_message(r.text if history == "model" else "", shown, i))
            for k, c in enumerate(shown):
                messages.append({"role": "tool", "tool_call_id": f"call_t{i}_{k}",
                                 "content": result_for(turn, c)})
            messages.append({"role": "assistant", "content": turn.reply})
        else:
            text = (r.text or "").strip() if history == "model" else ""
            messages.append({"role": "assistant", "content": text or turn.reply})
    return DialogueOutcome(dialogue.dialogue_id, configuration, history, outcomes, messages)
