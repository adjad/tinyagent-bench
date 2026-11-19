"""The interface between the reference loop and a tool set ("lane").

The loop knows nothing about calendars or mail. It asks the environment for a
system prompt and a tool menu, hands it each call the model made, and gets back
the text the model sees. Two environments implement this:

* the neutral lane, `edat.tools_generic.GenericEnvironment` (edat-generic-v1
  tools over a plain StateStore);
* the assistant lane, `edat.state.assistant_lane.AssistantEnvironment` (Harbor's
  tool names and schemas over Toolbench's fakes).

The fault layer (edat/faults/) wraps an environment and changes what `call`
returns; it never changes the menu.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from edat.state.store import StateStore

# Host-side outcome classes (Harbor's registry uses the same words). The model
# never sees these; it sees `content`.
OUTCOMES = ("succeeded", "failed", "denied", "needs_input", "no_match", "unknown", "timeout")


@dataclass
class ToolResult:
    content: str                       # exactly what the model sees as the tool message
    outcome: str = "succeeded"
    effect: bool = False               # True when this call changed the world
    virtual_elapsed_s: float = 0.0     # simulated time; the loop never sleeps
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")


@dataclass(frozen=True)
class ToolInfo:
    """What the grader needs to know about a tool, beyond its schema."""

    name: str
    effect: bool          # can change the world (writes, sends, deletes)
    egress: bool = False  # sends content out of the user's own stores (§3.13)
    confirm: bool = False # the host raises a confirmation before running it
    domain: str = ""


@runtime_checkable
class ToolEnvironment(Protocol):
    lane: str                          # "assistant" | "neutral"

    def system_prompt(self, *, policy: str | None = None,
                      preferences: list[str] | None = None) -> str: ...

    def tool_names(self) -> list[str]: ...

    def schemas(self, names: list[str] | None = None) -> list[dict]:
        """OpenAI function-tool schemas ({"type": "function", "function": {...}})."""
        ...

    def info(self, name: str) -> ToolInfo | None:
        """None for a name that is not a tool of this lane."""
        ...

    def call(self, name: str, arguments: dict) -> ToolResult: ...

    def seed(self, records: dict[str, list[dict]]) -> None:
        """Add case records (already bound and resolved) to the world."""
        ...

    def state(self) -> StateStore:
        """The world now, in the shared collection vocabulary."""
        ...
