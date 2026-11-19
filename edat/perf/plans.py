"""What one invocation runs: the plans behind ``python -m edat.perf run --plan …``.

A plan is a list of steps over model entries. Each run is one session on one device
(§3.1), bracketed by the two reference models (§1.3, §1.5):

    Gemma-Wisp, base … candidates … base, Gemma-Wisp

Speed and memory run on AC; energy runs on battery with the display off (§4.3), so they
are separate invocations (``speed`` and ``energy``), each with its own bracket.

Choices made here (the spec leaves them open):
- The bracket's reference cells are the §1.5 per-session set: levels 512, 2,048 and
  10,240, 10 reps, plain mode, for both references, plus as-shipped for Gemma-Wisp so
  as-shipped ratios have a same-session denominator. Ratios at other levels use the
  references' full sweep (plan ``full``) run once per device, OS and runtime version.
- The energy bracket runs pairs at 2,048 (the headline level) for both references.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .constants import (BASELINE_ID, ENERGY_PAIR_K, HEADLINE_ENERGY_LEVEL, LEVELS, REFERENCE_ID,
                        REFERENCE_PAIR_LEVELS, REFERENCE_PAIR_REPS)
from .energy import pair_k
from .manifest import ModelEntry
from .schedule import level_rounds

PLANS = ("smoke", "full", "speed", "energy", "load", "fit16", "fit16-under-load", "pressure-sweep",
         "s1", "s2", "s3", "reload", "long-session", "sim-day")

# Rough speeds for the duration estimate only (never reported): the reference Mac's order of
# magnitude for a 4B 4-bit model. Labelled an assumption wherever printed.
ASSUMED_PREFILL_TPS = 1500.0
ASSUMED_DECODE_TPS = 40.0
ASSUMED_LOAD_S = 8.0


@dataclass
class Step:
    what: str
    entry: ModelEntry
    mode: str
    args: dict
    bracket: str = "candidate"

    def label(self) -> str:
        a = ",".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.what:<16} {self.entry.entry_id:<34} {self.mode:<10} {self.bracket:<9} {a}"


def _refs(entries: list[ModelEntry]) -> tuple[ModelEntry | None, ModelEntry | None, list[ModelEntry]]:
    base = next((e for e in entries if e.role == "baseline" or e.id == BASELINE_ID), None)
    ref = next((e for e in entries if e.role == "reference" or e.id == REFERENCE_ID), None)
    cands = [e for e in entries if e is not base and e is not ref]
    return base, ref, cands


def _bracket_cells(e: ModelEntry, where: str, *, energy: bool) -> list[Step]:
    modes = ["plain"] + (["as_shipped"] if e.role == "baseline" and "as_shipped" in e.modes()
                         and e.engine_ids.get("as_shipped") != e.engine_ids.get("plain") else [])
    out = []
    for m in modes:
        if m not in e.modes() and not (m == "plain" and e.raw.get("speculation") in (None, "none")):
            continue
        if energy:
            out.append(Step("energy_pairs", e, m, {"level": HEADLINE_ENERGY_LEVEL}, where))
        else:
            out.append(Step("levels", e, m, {"levels": REFERENCE_PAIR_LEVELS, "reps": REFERENCE_PAIR_REPS}, where))
    return out


def _modes(e: ModelEntry) -> list[str]:
    ms = e.modes()
    if "plain" not in ms and e.raw.get("speculation") in (None, "none"):
        return ["as_shipped"]
    return ms


def build(plan: str, entries: list[ModelEntry], *, levels: tuple[int, ...] = LEVELS,
          bracket: bool = True) -> list[Step]:
    if plan not in PLANS:
        raise ValueError(f"unknown plan {plan!r}; one of {', '.join(PLANS)}")
    base, ref, cands = _refs(entries)
    steps: list[Step] = []

    def bracketed(inner: list[Step], *, energy: bool = False) -> list[Step]:
        if not bracket:
            return inner
        b = []
        for e in (base, ref):
            if e is not None:
                b += _bracket_cells(e, "start", energy=energy)
        b += inner
        for e in (ref, base):
            if e is not None:
                b += _bracket_cells(e, "end", energy=energy)
        return b

    targets = cands or [e for e in (base, ref) if e is not None]
    if plan == "smoke":
        for e in targets:
            steps += [Step("first_token", e, _modes(e)[0], {}),
                      Step("levels", e, _modes(e)[0], {"levels": (512, 2048), "reps": 2})]
        return steps
    if plan in ("full", "speed"):
        inner = []
        for e in targets if plan == "speed" else [x for x in (base, ref) if x] + cands:
            for m in _modes(e):
                inner += [Step("first_token", e, m, {}), Step("levels", e, m, {"levels": levels})]
            m0 = _modes(e)[0]
            inner += [Step("agent_replay", e, m0, {"cache": "on"}),
                      Step("agent_replay", e, m0, {"cache": "defeated"})]
        return bracketed(inner) if plan == "speed" else inner
    if plan == "energy":
        inner = []
        for e in targets:
            for m in _modes(e):
                for L in levels:
                    inner.append(Step("energy_pairs", e, m, {"level": L}))
        return bracketed(inner, energy=True)
    if plan == "load":
        return [Step("load", e, _modes(e)[0], {}) for e in [x for x in (base, ref) if x] + cands]
    if plan == "fit16":
        return [Step("fit16", e, _modes(e)[0], {}) for e in [x for x in (base, ref) if x] + cands]
    if plan == "fit16-under-load":
        return [Step("fit16_under_load", e, _modes(e)[0], {"app_load_gib": 2.5})
                for e in [x for x in (base, ref) if x] + cands]
    if plan == "pressure-sweep":
        return [Step("pressure_sweep", e, _modes(e)[0], {}) for e in [x for x in (base, ref) if x] + cands]
    one = {"s1": "s1", "s2": "s2", "s3": "s3", "reload": "reload", "long-session": "long_session",
           "sim-day": "sim_day"}[plan]
    return [Step(one, e, _modes(e)[0], {}) for e in targets]


def _req_s(level: int, out: int = 256) -> float:
    return level / ASSUMED_PREFILL_TPS + out / ASSUMED_DECODE_TPS


def estimate_s(steps: list[Step], *, gap_s: float = 5.0, idle_s: float = 60.0, reps: int = 10,
               reps_long: int = 5, n_levels_available: Callable[[ModelEntry], tuple] | None = None) -> float:
    """A rough duration (seconds) at the assumed speeds above. Cooldown waits are not included."""
    total = 0.0
    for s in steps:
        if s.what == "levels":
            lv = s.args.get("levels", LEVELS)
            r = s.args.get("reps", reps)
            for slot in level_rounds(lv, reps=r, reps_long=min(r, reps_long)):
                total += _req_s(slot.level) + gap_s
        elif s.what == "first_token":
            total += ASSUMED_LOAD_S + _req_s(8) + gap_s
        elif s.what == "agent_replay":
            total += 5 * sum(_req_s(4096 + 250 * i) + gap_s for i in range(6))
        elif s.what == "energy_pairs":
            L = s.args["level"]
            k = pair_k(L)
            total += 2 * idle_s + k * (_req_s(L, 1) + _req_s(L, 257) + 2 * gap_s)
        elif s.what == "load":
            total += 10 * (ASSUMED_LOAD_S * 2 + _req_s(8, 1) + gap_s)
        elif s.what in ("fit16", "fit16_under_load"):
            total += ASSUMED_LOAD_S + 3 * sum(_req_s(L) + gap_s for L in (2048, 10240, 32768, 65536))
        elif s.what == "pressure_sweep":
            total += 3 * (ASSUMED_LOAD_S + 3 * sum(_req_s(L) + gap_s for L in (2048, 10240)))
        elif s.what == "s1":
            total += 15 * 60
        elif s.what == "s2":
            total += 2 * 3600
        elif s.what == "s3":
            total += 4 * 30 * 60
        elif s.what == "reload":
            total += 5 * (7 * 60 + _req_s(2048))
        elif s.what == "sim_day":
            total += 3 * 3600
        elif s.what == "long_session":
            total += 3 * 3600
    return total
