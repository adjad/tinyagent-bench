"""Duration estimate for the coding-ceiling run, per model entry and in total.

    python -m edat.sidekick.ceiling plan                 # planning guesses only
    python -m edat.sidekick.ceiling plan --measured OUT  # redone from the smoke step's records

The entries are the ones the model folder holds now (`models.scan`), in run
order and by tier (0 standard, 1 other thinking mode, 2 second sampling
setting, 3 other quantisations). Seconds per model call = overhead + output
tokens / decode speed. Output tokens per call depend on the part and the
thinking mode (guesses below, replaced by the smoke step's measured means).
Decode speed per model is a planning guess (`models.py`), scaled for every
model by how far the smoke model's measured speed is from its guess. Grading
time in the sandbox is added per trial; a model load is added whenever the
model changes. Entries are packed, in order, into sittings of at most 12 hours.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from edat.sidekick.ceiling.models import Entry, LocalModel, model_dir_from_settings, ordered_entries, scan
from edat.sidekick.ceiling.run import BUDGET, SAMPLE_PER_RUNG, plan_units

CALL_OVERHEAD_S = 1.5                 # request, prefill of a short prompt, first token
LOAD_S = 60.0                         # loading a model before its first answer
SITTING_H = 12.0
GRADE_S = {"answer_only": 1.0, "ladder": 2.0, "tool_mode": 4.0, "ladder_tool": 4.0}
OUT_TOKENS = {False: {"answer_only": 300, "ladder": 900, "tool_mode": 180, "ladder_tool": 180},
              True: {"answer_only": 2200, "ladder": 3500, "tool_mode": 900, "ladder_tool": 900}}
STEPS = {"answer_only": 1, "ladder": 1, "tool_mode": 4, "ladder_tool": 7}


def unit_counts(plan: str) -> dict[str, int]:
    """Trials per suite for one entry. first-pass assumes pass B covers three rungs of 12 cases."""
    out: dict[str, int] = defaultdict(int)
    for u in plan_units(plan):
        out[u.suite] += 1
    if plan == "first-pass":
        out["ladder"] += 3 * SAMPLE_PER_RUNG * 2       # pass B (3 rungs x 2 more trials), costed as probe rungs
    return dict(out)


def seconds(model: LocalModel, thinking: bool, plan: str, *, speed_factor: float = 1.0,
            out_tokens: dict | None = None, load: bool = True) -> float:
    tok = dict(OUT_TOKENS[thinking])
    if out_tokens:
        tok.update(out_tokens)
    rate = model.decode_tok_s * speed_factor
    total = LOAD_S if load else 0.0
    for suite, n in unit_counts(plan).items():
        budget = BUDGET[thinking]["tool" if suite in ("tool_mode", "ladder_tool") else
                                  ("ladder" if suite == "ladder" else "answer_only")]
        per_call = CALL_OVERHEAD_S + min(tok[suite], budget) / rate
        total += n * (STEPS[suite] * per_call + GRADE_S[suite])
    return total


def measured(out: Path, models: list[LocalModel]) -> tuple[float, dict]:
    """From the smoke records: (speed factor vs the guess, measured mean output tokens per (thinking, suite))."""
    by_id = {m.engine_id: m for m in models}
    factors, toks = [], defaultdict(list)
    for store in sorted((out / "smoke").glob("*")):
        mid, _, rest = store.name.partition(".think-")
        mode = rest.partition(".")[0]
        m = by_id.get(mid)
        if not m:
            continue
        rate_n = rate_t = 0.0
        for f in store.glob("runs/*/records.jsonl"):
            for line in f.read_text().splitlines():
                r = json.loads(line) if line.strip() else None
                if not r or r["status"] != "ok":
                    continue
                mt = r["metrics"]
                ct = mt.get("completion_tokens")
                if ct and mt.get("time_s") and r["suite"] in ("answer_only", "ladder"):
                    rate_n += ct
                    rate_t += max(0.1, mt["time_s"] - CALL_OVERHEAD_S)
                    toks[(mode == "on", r["suite"])].append(ct)
        if rate_t:
            factors.append((rate_n / rate_t) / m.decode_tok_s)
    factor = sum(factors) / len(factors) if factors else 1.0
    return factor, {k: sum(v) / len(v) for k, v in toks.items()}


def table(entries: list[Entry], plan: str, factor: float = 1.0, toks: dict | None = None) -> list[tuple[Entry, float]]:
    """(entry, hours) in run order; a load is counted whenever the model changes."""
    rows, prev = [], None
    for e in entries:
        ot = {s: v for (th, s), v in (toks or {}).items() if th == e.thinking}
        h = seconds(e.model, e.thinking, plan, speed_factor=factor, out_tokens=ot,
                    load=e.model.engine_id != prev) / 3600
        prev = e.model.engine_id
        rows.append((e, h))
    return rows


def sittings(rows: list[tuple[Entry, float]], limit_h: float = SITTING_H) -> list[list[tuple[Entry, float]]]:
    out: list[list] = [[]]
    for e, h in rows:
        if out[-1] and sum(x for _, x in out[-1]) + h > limit_h:
            out.append([])
        out[-1].append((e, h))
    return [s for s in out if s]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling plan")
    ap.add_argument("--measured", help="results folder with a smoke run")
    ap.add_argument("--model-dir", help="default: oMLX's model folder")
    a = ap.parse_args(argv)
    mdir = Path(a.model_dir) if a.model_dir else model_dir_from_settings()
    if not mdir:
        print("oMLX's model folder is not set", file=sys.stderr)
        return 2
    models, skipped = scan(mdir)
    factor, toks = measured(Path(a.measured), models) if a.measured else (1.0, {})
    print(f"basis: {'measured from the smoke step' if a.measured else 'planning guesses'}; speed factor {factor:.2f}")
    print(f"{len(models)} models found; {len(skipped)} folders left out")
    for plan, tiers in (("first-pass", (0,)), ("full", (0,)), ("full", (1,)), ("full", (2,)), ("full", (3,))):
        ents = ordered_entries(models, tiers=tiers)
        if not ents:
            continue
        rows = table(ents, plan, factor, toks)
        total = sum(h for _, h in rows)
        print(f"\n{plan}, tier {tiers[0]} ({len(ents)} entries): {total:.1f} h")
        for i, sit in enumerate(sittings(rows), 1):
            print(f"  sitting {i}: {sum(h for _, h in sit):.1f} h")
            for e, h in sit:
                print(f"    {e.label:<70} {h:5.1f} h")
    sm = table(ordered_entries(models, tiers=(0,)), "smoke", factor, toks)
    if sm:
        print(f"\nsmoke on one entry: about {min(h for _, h in sm) * 60:.0f}-{max(h for _, h in sm) * 60:.0f} minutes")
    return 0
