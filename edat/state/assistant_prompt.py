"""The assistant lane's system prompt: Harbor's own agent-turn prompt, built hermetically.

What Harbor's agent loop sends on an agent turn (service/agent/loop.py, the
`sys_content` + `runtime_content` assembly in `run_agent`) is, in order:

1. `build_system(offered)`: the fixed SYSTEM rules cut down to the tools offered
   this turn (a block whose first line names no registered tool is a
   cross-cutting rule and is always kept). Pure given the registry; used as is.
2. `identity_hint`: `service.memory.identity.identity_prompt_block(with_date=False,
   shape="verbatim", messages=True, preferred_name=...)`. Its text is pure, but
   it reads the user's name from `id -F` and their addresses from the Mail
   sync. Here it is built with Toolbench's synthetic user ("Test User" and the
   three example.com addresses in scripts/toolbench/world.py), exactly as the
   Toolbench runner patches it, and never touches the machine's real identity.
3. `memory_hint`, `skills_hint`: LEFT OUT. Both read Harbor's live stores (saved
   facts, the behaviour profile card, installed skills catalog). In the
   assistant lane the model reaches saved memory through `recall`.
4. The clock line (`prompt_blocks.now_line(resolve_hint=True)`): rebuilt from
   the case's pinned Clock with the same wording, instead of the wall clock.
5. Also LEFT OUT: the capability catalog (a live readiness report), the
   "Human-sounding replies" compose style (a user setting), the test-mode
   suffix, the reminder-task addendum and the model-led stub protocol notes.
   They are either per-install state or loop behaviour the reference loop
   does not have.

EDAT then appends the case's `policy` paragraph and a "User preferences" block.
Nothing here contains private data: every value is Harbor's public prompt text
or Toolbench's invented user.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from edat.state.clock import Clock

_IDENTITY_LOCK = threading.Lock()


@contextmanager
def _synthetic_identity():
    """Point Harbor's identity lookups at Toolbench's invented user for one call."""
    from scripts.toolbench import world as world_mod
    import service.memory.identity as identity

    with _IDENTITY_LOCK:
        saved = identity.user_name, identity.user_emails
        identity.user_name = lambda: world_mod.USER_NAME
        identity.user_emails = lambda: list(world_mod.USER_EMAILS)
        try:
            yield
        finally:
            identity.user_name, identity.user_emails = saved


def now_line(clock: Clock) -> str:
    """Harbor's clock line (service/memory/prompt_blocks.now_line), from the pinned clock."""
    return ("\nThe current date and time is "
            + clock.now.strftime("%A, %B %-d, %Y at %-I:%M %p") + "."
            + " Resolve relative dates/times against this.")


def build_prompt(offered: set[str], clock: Clock, *, policy: str | None = None,
                 preferences: list[str] | None = None) -> str:
    """Harbor's agent-turn prompt for `offered` tools, plus EDAT's policy and preferences."""
    from service.agent.loop import build_system
    from service.memory.identity import identity_prompt_block

    base = build_system(set(offered))
    with _synthetic_identity():
        identity = identity_prompt_block(with_date=False, shape="verbatim", messages=True,
                                         preferred_name=False)
    text = base + identity + "\n" + now_line(clock)
    if policy and policy.strip():
        text += "\n\n" + policy.strip()
    prefs = [p.strip() for p in (preferences or []) if p and p.strip()]
    if prefs:
        text += "\n\nUser preferences:\n" + "\n".join(f"- {p}" for p in prefs)
    return text
