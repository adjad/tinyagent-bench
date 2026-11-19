"""Order of requests: rounds, reversal, nonce variants, cooldown gate (spec 04 §2.6, §6.4).

- Round 0 is the warm-up round, levels ascending (it also finds kills early).
- Measured rounds then run every level once each, ascending in odd rounds and descending
  in even rounds, after ``scripts/release_performance.py:668-708``. Levels at 32,768 and
  65,536 take part only in the first 5 measured rounds (5 reps instead of 10).
- Every request uses a nonce variant no other request of that model and level has used
  in this run, so oMLX's prefix cache cannot hit (§6.3). Variants are handed out by
  :class:`VariantPool`; when a pack runs out it wraps, the runner logs it, and a hit is
  still caught by ``cached_tokens > 0`` (the aggregator voids that rep).
- Before every request: the cooldown gate (wait for ``nominal`` up to 300 s, then run
  anyway with ``thermal_not_nominal``), and after every request a fixed 5 s gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .constants import (COOLDOWN_MAX_S, LONG_LEVELS, MEASURED_REPS, MEASURED_REPS_LONG, WARMUP_REPS)


@dataclass(frozen=True)
class Slot:
    level: int
    phase: str      # "warmup" | "measured"
    round: int
    rep: int        # 0 for the warm-up, 1..n for measured reps


def reps_for(level: int, reps: int = MEASURED_REPS, reps_long: int = MEASURED_REPS_LONG) -> int:
    return reps_long if level in LONG_LEVELS else reps


def level_rounds(levels: list[int] | tuple[int, ...], *, reps: int = MEASURED_REPS,
                 reps_long: int = MEASURED_REPS_LONG, warmup: int = WARMUP_REPS) -> list[Slot]:
    lv = sorted(levels)
    out: list[Slot] = []
    for w in range(warmup):
        out += [Slot(L, "warmup", 0, 0) for L in lv]
    n_rounds = max((reps_for(L, reps, reps_long) for L in lv), default=0)
    for r in range(1, n_rounds + 1):
        order = lv if r % 2 == 1 else list(reversed(lv))
        out += [Slot(L, "measured", r, r) for L in order if r <= reps_for(L, reps, reps_long)]
    return out


class VariantPool:
    """Hands out unused nonce variants per (model, mode, key)."""

    def __init__(self, n_variants: Callable[[int], int]):
        self.n_variants = n_variants
        self.next: dict[tuple, int] = {}
        self.wrapped: set[tuple] = set()

    def take(self, model: str, level: int, key: str = "level") -> tuple[int, bool]:
        """Returns (variant, wrapped). Keys share one counter per model and level, so the
        level test, the energy pairs and the emulation never reuse each other's nonces."""
        k = (model, level)
        i = self.next.get(k, 0)
        self.next[k] = i + 1
        n = self.n_variants(level)
        wrapped = i >= n
        if wrapped:
            self.wrapped.add(k)
        return i % n, wrapped


def cooldown_gate(thermal: Callable[[], str], sleep: Callable[[float], None], now_s: Callable[[], float],
                  max_wait_s: float = COOLDOWN_MAX_S, poll_s: float = 5.0) -> tuple[float, bool, str]:
    """Wait until the thermal state is nominal, up to ``max_wait_s``.
    Returns (waited_s, timed_out, state_at_start)."""
    start = now_s()
    state0 = thermal()
    state = state0
    while state != "nominal":
        if now_s() - start >= max_wait_s:
            return now_s() - start, True, state0
        sleep(poll_s)
        state = thermal()
    return now_s() - start, False, state0


def abba(a: str, b: str, n_pairs: int = 1) -> list[str]:
    """ABBA order for paired idle windows (§9 S3, §9.1)."""
    return [a, b, b, a] * n_pairs


def bracket(baseline: str, reference: str, candidates: list[str]) -> list[tuple[str, str]]:
    """The session bracket of §1.5: Gemma-Wisp, base, …candidates…, base, Gemma-Wisp.
    Returns (model_id, bracket_position) pairs: "start", "candidate" or "end"."""
    return ([(baseline, "start"), (reference, "start")] + [(c, "candidate") for c in candidates]
            + [(reference, "end"), (baseline, "end")])
