"""Collects every template module's `TEMPLATES` list.

Restraint-set templates (those with `restraint` set) form their own counts row
("restraint") but keep their task type, so they count in that type's pass
rate (spec §3.5).
"""
from __future__ import annotations

import importlib
from functools import lru_cache

from edat.sidekick.gen.core import TASK_TYPES, Template

MODULES = [f"edat.sidekick.gen.t_{t}" for t in TASK_TYPES] + ["edat.sidekick.gen.t_restraint"]


@lru_cache(maxsize=1)
def all_templates() -> tuple[Template, ...]:
    out: list[Template] = []
    seen: set[str] = set()
    for name in MODULES:
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                continue
            raise
        for t in getattr(mod, "TEMPLATES", []):
            if t.id in seen:
                raise ValueError(f"duplicate template id {t.id}")
            if t.type not in TASK_TYPES:
                raise ValueError(f"{t.id}: unknown type {t.type}")
            seen.add(t.id)
            out.append(t)
    return tuple(out)


def templates_by_group() -> dict[str, list[Template]]:
    groups: dict[str, list[Template]] = {}
    for t in all_templates():
        groups.setdefault("restraint" if t.restraint else t.type, []).append(t)
    return groups


def get(template_id: str) -> Template:
    for t in all_templates():
        if t.id == template_id:
            return t
    raise KeyError(template_id)
