"""Held-out shingles (spec 02 §7, B13): hashed n-grams of every held-out prompt.

Same format as Toolbench's export (scripts/toolbench/cases.py
`heldout_shingles`): lowercase word tokens, sha1 of the space-joined n-gram cut
to 12 hex characters, n = 1..6, plus the full-prompt hash. Training sets can
filter held-out prompts out without seeing them. The prompts are not stored.
Prompts are bound in world seeds 1-3, the routine trials' seeds.

    EDAT_PRIVATE_DIR=... python -m edat.cases.shingles --out <file outside the repo>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from edat.cases.bind import bind


def _norm(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _h(tokens: list[str]) -> str:
    return hashlib.sha1(" ".join(tokens).encode()).hexdigest()[:12]


def heldout_shingles(cases: list[dict], seeds=(1, 2, 3)) -> dict:
    prompts = set()
    for c in cases:
        if c.get("split") != "heldout":
            continue
        for ws in seeds:
            b = bind(c, ws)
            for s in b["sessions"]:
                for t in s["turns"]:
                    prompts.add(t["user"])
    items = []
    for p in sorted(prompts):
        t = _norm(p)
        if t:
            items.append({"ntok": len(t), "full": _h(t),
                          "sh": {str(n): sorted({_h(t[i:i + n]) for i in range(len(t) - n + 1)})
                                 for n in range(1, 7) if len(t) >= n}})
    return {"schema": "edat-heldout-shingles/v1", "n_prompts": len(items), "items": items}


def main(argv=None) -> int:
    from edat.cases.loader import REPO, load_split
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    if out.is_relative_to(REPO):
        raise SystemExit("write the shingle file outside the repository; publish it only after review")
    out.write_text(json.dumps(heldout_shingles(load_split("heldout")), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
