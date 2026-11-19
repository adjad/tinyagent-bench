"""Case generators, one module per family (or family group).

Each module defines

    FAMILY = "select"                      # or FAMILIES = ("parallel", "mixed")
    def generate(split: str, rng: random.Random) -> list[dict]:
        ...

returning raw cases (with `facts`, `world.seed_records`, checks and an
`oracle`) for both lanes. `split` is "dev" (public seed) or "heldout"
(private seed): same templates, different draws, and held-out ids carry an
`h` marker so ids never collide. `edat.cases.build` runs every generator,
validates every case with the oracle and the null model, and writes the
files. Generators must be deterministic for a given rng.
"""
from __future__ import annotations

import importlib
import pkgutil

def modules() -> dict[str, object]:
    out = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name in ("pools",):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "generate"):
            out[info.name] = mod
    return out


def case_id(prefix: str, lane: str, split: str, template: str, n: int) -> str:
    return f"{prefix}{'h' if split == 'heldout' else ''}_{'ast' if lane == 'assistant' else 'neu'}_{template}_{n:03d}"
