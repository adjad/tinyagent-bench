"""Which tools a case offers (spec 02 §3.1, §3.10).

The reference loop always offers full schemas for a fixed menu, so Harbor's
router can never confound a result. By default a case offers 12 tools (Harbor's
preload size): every tool the case needs or tests (its `expect` steps and
alternatives, the oracle's calls, `verify.read_any`, `forbid`, the injection's
attack tools, `menu_extra`), then up to 3 near-miss tools from the same domain,
then random tools from the lane, drawn per (case, world seed). `menu_pin`
offers exactly the listed tools. The scaling probe passes `size` (5, 10, 25,
50, 100); the needed tools are always present.
"""
from __future__ import annotations

import hashlib
import random

DEFAULT_SIZE = 12
NEAR_MISS = 3


def _names(step: dict) -> list[str]:
    if "parallel" in step:
        return [n for m in step["parallel"].get("steps", []) for n in _names(m)]
    if "any_of" in step:
        return list(step["any_of"])
    return [step["tool"]] if "tool" in step else []


def needed_tools(case: dict) -> list[str]:
    out: list[str] = []

    def add(n):
        if n and n not in out:
            out.append(n)
    for s in case.get("sessions", []):
        for t in s.get("turns", []):
            for step in t.get("expect") or []:
                for n in _names(step):
                    add(n)
            for st in t.get("oracle") or []:
                for c in st.get("calls") or []:
                    add(c.get("name"))
            if t.get("verify"):
                for n in t["verify"].get("read_any", []):
                    add(n)
    for n in case.get("forbid") or []:
        add(n)
    inj = case.get("inject") or {}
    for c in (inj.get("attack_success") or {}).get("any_call") or []:
        add(c.get("tool"))
    for n in case.get("menu_extra") or []:
        add(n)
    return out


def select_menu(case: dict, env, world_seed: int, *, size: int | None = None) -> list[str]:
    lane_tools = env.tool_names()
    if case.get("menu_pin"):
        missing = [n for n in case["menu_pin"] if n not in lane_tools]
        if missing:
            raise KeyError(f"{case['id']}: menu_pin names tools the lane lacks: {missing}")
        return list(case["menu_pin"])
    need = [n for n in needed_tools(case) if n in lane_tools]
    size = size or DEFAULT_SIZE
    digest = hashlib.sha256(f"{case.get('variant_of') or case['id']}|{world_seed}|menu|{size}".encode()).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    chosen = list(need)
    domains = {getattr(env.info(n), "domain", "") for n in need}
    near = sorted(n for n in lane_tools if n not in chosen and getattr(env.info(n), "domain", "") in domains - {""})
    rng.shuffle(near)
    for n in near[:NEAR_MISS]:
        if len(chosen) >= size:
            break
        chosen.append(n)
    rest = sorted(n for n in lane_tools if n not in chosen)
    rng.shuffle(rest)
    for n in rest:
        if len(chosen) >= size:
            break
        chosen.append(n)
    rng.shuffle(chosen)
    return chosen
