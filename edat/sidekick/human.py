"""The human-graded set and the key-fact audit sheet (spec 12 §3.2, S12; SK-17, SK-18).

* **Human-graded set:** 15 `explain_error` and 15 `commit_msg` instances,
  rated 1-3 for clarity by a person blind to the model. Reported apart and
  never in the score. Drawn from the public (development) seed only. The
  development split holds only 6 explain_error and 3 commit_msg cases, so
  the 30 instances are taken from the human-graded templates' public-seed
  trials, development cases first (see the hand-over: the spec's "30 from
  the development split" cannot be met by cases alone).
* **Key-fact audit:** a random 10 % sample of a run's explain_error,
  commit_msg and diff_review answers, graded blind; agreement with the
  patterns is SK-17.

Both export CSV for the quality spec's vote tool (03 §4.3) when it exists.
No judge model is used.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from edat.sidekick.gen.core import PUBLIC_MASTER_SEED, build_cases, materialize

HUMAN_TYPES = {"explain_error": 15, "commit_msg": 15}


def human_set() -> list[dict]:
    rows = []
    cases = build_cases()
    for typ, want in HUMAN_TYPES.items():
        pool = [c for c in cases if c.type == typ and c.template.human_graded]
        pool.sort(key=lambda c: (c.split != "dev", c.index))
        picked = []
        trial = 1
        while len(picked) < want and pool:
            for c in pool:
                if len(picked) >= want:
                    break
                picked.append((c, trial))
            trial += 1
        for c, t in picked:
            inst = materialize(c, t, PUBLIC_MASTER_SEED)
            rows.append({"case_id": c.id, "trial": t, "type": typ, "split_of_case": c.split, "prompt": inst.prompt})
    return rows


def export_sheet(out: str, answers: dict | None = None) -> str:
    """CSV: case_id, trial, type, prompt, answer (from a run, if given), rating_1_3 (blank)."""
    rows = human_set()
    p = Path(out)
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item", "case_id", "trial", "type", "prompt", "answer", "rating_1_3"])
        w.writeheader()
        rng = random.Random(7)
        order = list(range(len(rows)))
        rng.shuffle(order)                      # blind: no model order, no type blocks
        for i, k in enumerate(order, 1):
            r = rows[k]
            ans = (answers or {}).get((r["case_id"], r["trial"]), "")
            w.writerow({"item": i, "case_id": r["case_id"], "trial": r["trial"], "type": r["type"],
                        "prompt": r["prompt"], "answer": ans, "rating_1_3": ""})
    return f"{len(rows)} items written to {p}"


def audit_sample(run_dir: str, share: float = 0.10, seed: int = 17) -> list[dict]:
    """A blind 10 % sample of key-fact graded answers from a run (SK-17)."""
    d = Path(run_dir)
    recs = [json.loads(l) for l in (d / "records.jsonl").read_text().splitlines() if l.strip()]
    pool = [r for r in recs if r["facets"]["family"] in ("explain_error", "commit_msg", "diff_review")
            and r.get("status") == "ok" and r.get("raw")]
    rng = random.Random(seed)
    k = max(1, round(share * len(pool))) if pool else 0
    out = []
    for r in rng.sample(pool, k):
        raw = json.loads((d / r["raw"]["path"]).read_text())
        out.append({"case_id": r["case_id"], "trial": r["trial"], "answer": raw.get("visible_answer", ""),
                    "pattern_pass": r["metrics"].get("pass")})
    return out


def agreement(sample: list[dict], human_pass: list[bool]) -> float | None:
    if not sample or len(sample) != len(human_pass):
        return None
    return sum(1 for s, h in zip(sample, human_pass) if bool(s["pattern_pass"]) == h) / len(sample)
