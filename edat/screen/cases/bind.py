"""Bind a case to a world seed: build the trial's world and fill every `{slot}`.

A case (spec 11 §3.12) names the apps, a `world.generator` (a scenario that
adds case-specific records, dialogs, triggers or injected text on top of the
apps' own seeded state) and its parameters. The world seed — one per trial —
fills names, numbers, codes, prices, list order, element ids and distractors.
Every app's `seed()` and the scenario return slot values; `{slot}` in the task,
the predicates, the answer, the user's replies, the injected text and the
oracle are replaced with them. A value the model must read therefore changes
with the seed, so a guessed value fails.

A scenario is `fn(world, rng, params, case) -> slots`. It sees the raw case (for
example its `inject` block, whose text it plants on screen; `fill(text,
world.slots)` resolves slots the apps already set).
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from edat.screen.apps import DOCK_ORDER, available_apps, get_app
from edat.screen.world.core import World

SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
DEFAULT_CLOCK = "2026-11-18T08:30"
DEFAULT_TZ = "America/Los_Angeles"
STEP_CEILING = 24

ScenarioFn = Callable[[World, random.Random, dict, dict], dict]
SCENARIOS: dict[str, ScenarioFn] = {}


def scenario(name: str):
    def deco(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS[name] = fn
        return fn
    return deco


@scenario("apps_v1")
def _apps_only(world: World, rng: random.Random, params: dict, case: dict) -> dict:
    return {}


def load_scenarios() -> None:
    """Import every module that registers scenarios."""
    import importlib
    for mod in ("edat.screen.scenarios",):
        try:
            importlib.import_module(mod)
        except ImportError as exc:  # pragma: no cover - partial checkouts
            if mod not in str(exc):
                raise


def fill(obj: Any, slots: dict, strict: bool = True) -> Any:
    """Replace `{slot}` in every string of a nested structure. Unknown slots are an error when strict."""
    if isinstance(obj, str):
        def sub(m):
            k = m.group(1)
            if k not in slots:
                if strict:
                    raise KeyError(f"unknown slot {{{k}}}")
                return m.group(0)
            return str(slots[k])
        return SLOT.sub(sub, obj)
    if isinstance(obj, list):
        return [fill(x, slots, strict) for x in obj]
    if isinstance(obj, dict):
        return {k: fill(v, slots, strict) for k, v in obj.items()}
    return obj


def max_steps_for(optimal: int, ceiling: int = STEP_CEILING) -> int:
    """§3.3: max(6, 2 × optimal steps), ceiling 24."""
    return min(ceiling, max(6, 2 * int(optimal)))


@dataclass
class Bound:
    case: dict                      # the case with every slot filled
    world: World
    slots: dict
    world_seed: int
    initial: dict = field(default_factory=dict)   # deep copy of the data state at the start

    @property
    def id(self) -> str:
        return self.case["id"]

    @property
    def max_steps(self) -> int:
        c = self.case
        return int(c.get("max_steps") or max_steps_for((c.get("optimal") or {}).get("steps", 3)))


def apps_for(case: dict) -> list[str]:
    want = (case.get("world") or {}).get("apps")
    have = available_apps()
    if want:
        missing = [a for a in want if a not in have]
        if missing:
            raise KeyError(f"{case.get('id')}: apps not available: {missing}")
        return [a for a in DOCK_ORDER if a in want]
    return have


def bind(case: dict, world_seed: int) -> Bound:
    load_scenarios()
    w = dict(case.get("world") or {})
    apps = apps_for(case)
    start = dict(case.get("start") or {})
    front = start.get("app") or apps[0]
    world = World(seed=world_seed, apps=apps, front=front, start=start, clock=w.get("clock") or DEFAULT_CLOCK,
                  tz=w.get("tz") or DEFAULT_TZ, noise=w.get("noise"))
    params = dict(w.get("params") or {})
    slots: dict[str, Any] = {}
    rng = random.Random(f"{case['id']}:{world_seed}")
    for a in apps:
        slots.update(get_app(a).seed(world, rng, dict(params.get(a) or {})))
    for path, records in (w.get("seed_records") or {}).items():
        # §3.12 seed_records: extra records appended to a collection, after the apps' own seeding
        recs = fill(copy.deepcopy(list(records)), {**slots, **world.slots}, strict=False) if records else []
        cur = world.get(path)
        if isinstance(cur, list):
            cur.extend(recs)
        else:
            world.set(path, recs)
    gen = w.get("generator") or "apps_v1"
    if gen not in SCENARIOS:
        raise KeyError(f"{case['id']}: unknown scenario {gen!r}")
    world.slots = dict(slots)
    returned = SCENARIOS[gen](world, rng, params, case) or {}
    slots.update(world.slots)      # what the scenario wrote to world.slots
    slots.update(returned)         # then what it returned: a scenario's own slots win over the apps'
    world.slots = slots
    filled = fill(copy.deepcopy({k: v for k, v in case.items() if k not in ("world",)}), slots)
    filled["world"] = copy.deepcopy(case.get("world") or {})
    if filled.get("inject") and filled["inject"].get("text"):
        world.slots["inject_text"] = filled["inject"]["text"]
    return Bound(case=filled, world=world, slots=slots, world_seed=world_seed, initial=world.snapshot())
