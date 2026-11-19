"""Edit-distance alignment shared by the transcript scorer (WER, CER) and full reading (CER).

``align(ref, hyp)`` counts substitutions, deletions and insertions of the
minimum-cost alignment (all costs 1). Ties are broken the same way every time
(substitution, then deletion, then insertion), so counts are reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class EditCounts:
    hits: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def ref_len(self) -> int:
        return self.hits + self.substitutions + self.deletions

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    def rate(self) -> float:
        """Errors over reference length; an empty reference gives 0 or 1 (any insertion)."""
        if self.ref_len == 0:
            return 0.0 if self.insertions == 0 else 1.0
        return self.errors / self.ref_len

    def __add__(self, other: "EditCounts") -> "EditCounts":
        return EditCounts(self.hits + other.hits, self.substitutions + other.substitutions,
                          self.deletions + other.deletions, self.insertions + other.insertions)


ZERO = EditCounts(0, 0, 0, 0)


def align(ref: Sequence, hyp: Sequence) -> EditCounts:
    n, m = len(ref), len(hyp)
    # Each cell holds (cost, subs, dels, ins); compare by cost, then fixed tie order.
    prev = [(j, 0, 0, j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)] + [None] * m  # type: ignore[list-item]
        ri = ref[i - 1]
        for j in range(1, m + 1):
            c, s, d, ins = prev[j - 1]
            if ri == hyp[j - 1]:
                best = (c, s, d, ins)
            else:
                best = (c + 1, s + 1, d, ins)
            c, s, d, ins = prev[j]
            cand = (c + 1, s, d + 1, ins)
            if cand[0] < best[0]:
                best = cand
            c, s, d, ins = cur[j - 1]
            cand = (c + 1, s, d, ins + 1)
            if cand[0] < best[0]:
                best = cand
            cur[j] = best
        prev = cur
    cost, subs, dels, ins = prev[m]
    return EditCounts(hits=n - subs - dels, substitutions=subs, deletions=dels, insertions=ins)


def levenshtein(a: Sequence, b: Sequence) -> int:
    return align(a, b).errors


def cer(ref: str, hyp: str) -> float:
    return align(list(ref), list(hyp)).rate()
