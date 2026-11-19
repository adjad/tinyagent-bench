"""edat-generic-v1: the neutral lane's generic tool set (spec 02 §2.1, §3.12).

About 100 tools with neutral `domain_verb_object` names and OpenAI JSON
schemas, over a plain StateStore that uses the shared collection vocabulary
(edat/state/collections.py). Behaviour is deterministic and pure over the
store; nothing touches the network or runs a command.
"""
from __future__ import annotations

TOOLSET_ID = "edat-generic-v1"

# Import order is menu order.
from edat.tools_generic import (  # noqa: E402,F401
    tools_pim, tools_comms, tools_files, tools_device, tools_info, tools_memory,
)
from edat.tools_generic.registry import REGISTRY, ToolError, ToolSpec, schema  # noqa: E402
from edat.tools_generic.env import GenericEnvironment  # noqa: E402
from edat.tools_generic.policy import BASE_PROMPT, build_system_prompt  # noqa: E402


def all_specs() -> list[ToolSpec]:
    """Every tool spec, in menu order."""
    return list(REGISTRY.values())


__all__ = ["TOOLSET_ID", "GenericEnvironment", "ToolSpec", "ToolError", "all_specs", "schema",
           "build_system_prompt", "BASE_PROMPT"]
