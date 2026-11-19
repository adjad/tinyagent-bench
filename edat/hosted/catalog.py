"""The six hosted cloud-reference models (spec 02 §4.4).

Ids, accepted parameters and prices were verified by the benchmark coordinator
from OpenRouter's public model list on 2026-11-18. `reasoning_off` records
whether the model can switch reasoning off; where it cannot, the thinking-off
entry uses the lowest effort and is labelled "minimal reasoning". That column
is NOT verified yet (the spec gives no value); it defaults to True and must be
checked before the first hosted run.

Cloud rows are unranked, run on the public development split only, and their
efficiency numbers are "not comparable with on-device numbers".
"""
from __future__ import annotations

from dataclasses import dataclass

NOT_COMPARABLE = "not comparable with on-device numbers"


@dataclass(frozen=True)
class HostedModel:
    id: str
    label: str
    accepts_sampling: bool        # temperature / top-p (and top-k, min-p) accepted
    accepts_seed: bool
    price_in_per_m: float         # USD per million input tokens
    price_out_per_m: float
    reasoning_off: bool = True    # can reasoning be disabled? (unverified, see module doc)
    reasoning_off_verified: bool = False


CLOUD_REFERENCES: dict[str, HostedModel] = {m.id: m for m in (
    HostedModel("anthropic/claude-haiku-5.5", "Claude Haiku 5.5", False, False, 0.10, 0.50),
    HostedModel("google/gemini-4-argon", "Gemini 4 Argon", True, True, 2.0, 10.0),
    HostedModel("google/gemini-4-carbon", "Gemini 4 Carbon", True, True, 2.0, 10.0),
    HostedModel("google/gemini-4-neon", "Gemini 4 Neon", True, True, 3.0, 15.0),
    HostedModel("openai/gpt-6-luna", "GPT-6 Luna", False, True, 0.10, 0.50),
    HostedModel("deepseek/deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash 1017", True, True, 0.30, 1.20),
)}


def lookup(model_id: str) -> HostedModel:
    try:
        return CLOUD_REFERENCES[model_id]
    except KeyError:
        raise KeyError(f"{model_id!r} is not one of the six cloud references: "
                       f"{sorted(CLOUD_REFERENCES)}") from None
