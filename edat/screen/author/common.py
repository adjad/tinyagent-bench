"""Shared helpers and the case counts for the family generators (spec 11 §3.8, §3.11).

A family module `edat/screen/author/<family>.py` defines

    def generate(split: str, rng: random.Random, n: int) -> list[dict]

returning exactly `n` cases for `split` ("dev" or "heldout"), with levels in
the family's range and ids from `case_id()`. `rng` is seeded by the build
(public seed for dev, the private seed for heldout), so the same call always
gives the same cases. Values the model must read are NOT fixed here: they are
`{slots}` filled from the world seed at bind time. Template choices,
phrasings, which control, which app and distractor knobs come from `rng`.

The build fills `optimal`, `max_steps`, `split`, `core` and `tracks`.
"""
from __future__ import annotations

import random

FULL = {"ground": 120, "form": 80, "flow": 110, "read": 80, "disrupt": 70, "cross": 60, "guard": 130}
CORE = {"ground": 30, "form": 25, "flow": 30, "read": 25, "disrupt": 20, "cross": 15, "guard": 55}
DEV = {"ground": 60, "form": 40, "flow": 55, "read": 40, "disrupt": 35, "cross": 30, "guard": 65}
HELDOUT = {f: FULL[f] - DEV[f] for f in FULL}
DEV_CORE = {"ground": 15, "form": 13, "flow": 15, "read": 13, "disrupt": 10, "cross": 8, "guard": 28}
HELDOUT_CORE = {f: CORE[f] - DEV_CORE[f] for f in CORE}

# Guard sub-families (§3.8 table): full / core, and the split of each.
GUARD_FULL = {"confirm": 25, "preauth": 15, "deny": 15, "injection": 50, "overreach": 15, "secrets": 10}
GUARD_CORE = {"confirm": 9, "preauth": 6, "deny": 6, "injection": 24, "overreach": 5, "secrets": 5}
GUARD_DEV = {"confirm": 12, "preauth": 8, "deny": 7, "injection": 26, "overreach": 7, "secrets": 5}
GUARD_HELDOUT = {k: GUARD_FULL[k] - GUARD_DEV[k] for k in GUARD_FULL}
GUARD_DEV_CORE = {"confirm": 5, "preauth": 3, "deny": 3, "injection": 12, "overreach": 3, "secrets": 2}
GUARD_HELDOUT_CORE = {k: GUARD_CORE[k] - GUARD_DEV_CORE[k] for k in GUARD_CORE}

# Screenshot-track and both-track shares of the full set (§3.11).
SCREENSHOT_FULL = 300
SCREENSHOT_CORE = 100
BOTH_FULL = 100
PIXEL_FULL = 300

FAMILY_PREFIX = {"ground": "ground", "form": "form", "flow": "flow", "read": "read", "disrupt": "disrupt",
                 "cross": "cross", "guard": "guard", "pixel": "pixel"}

# Proposed wall-clock budgets per level (§3.10, open question 4).
LEVEL_BUDGET_S = {1: 15, 2: 30, 3: 60, 4: 120, 5: 180}


def counts(split: str) -> dict:
    return DEV if split == "dev" else HELDOUT


def guard_quota(split: str) -> dict:
    return GUARD_DEV if split == "dev" else GUARD_HELDOUT


def case_id(family: str, split: str, template: str, n: int) -> str:
    """`scr_<family>[h]_<template>_<nnn>`; `h` marks the held-out split."""
    tpl = "".join(ch if ch.isalnum() else "_" for ch in template.lower()).strip("_")
    return f"scr_{FAMILY_PREFIX[family]}{'h' if split == 'heldout' else ''}_{tpl}_{n:03d}"


def spread(n: int, levels: list[int], weights: list[float] | None = None) -> list[int]:
    """`n` levels in the given proportions, deterministic, in ladder order."""
    weights = weights or [1.0] * len(levels)
    total = sum(weights)
    raw = [n * w / total for w in weights]
    out = [int(r) for r in raw]
    rest = sorted(range(len(levels)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in rest[: n - sum(out)]:
        out[i] += 1
    return [lv for lv, k in zip(levels, out) for _ in range(k)]


def pick(rng: random.Random, options: list):
    return options[rng.randrange(len(options))]


def base_case(cid: str, family: str, level: int, *, task: str, start: dict, apps: list[str],
              oracle: list[dict], generator: str = "apps_v1", params: dict | None = None, **extra) -> dict:
    c = {"id": cid, "spec": "screen", "family": family, "level": level, "tracks": ["ax_text"], "start": start,
         "world": {"apps": apps, "generator": generator, "params": params or {}}, "task": task}
    if level >= 4:
        c["world"]["noise"] = {"unnamed_icons": 0.1, "nested_groups": True}
    c.update(extra)
    c["oracle"] = oracle
    return c
