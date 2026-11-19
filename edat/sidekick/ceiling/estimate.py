"""Duration estimate for the coding-ceiling run, per model entry and in total.

    python -m edat.sidekick.ceiling plan                 # every plan, planning guesses only
    python -m edat.sidekick.ceiling plan --measured OUT  # redone from the smoke step's records
    python -m edat.sidekick.ceiling plan --plan quick --per-rung 4 --rungs 1-8 --thinking off
                                                         # one plan: its models, entries and estimate

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
from edat.sidekick.ceiling.run import (BUDGET, SAMPLE_PER_RUNG, Quick, add_quick_args, plan_units, quick_entries,
                                       quick_from_args)

CALL_OVERHEAD_S = 1.5                 # request, prefill of a short prompt, first token
LOAD_S = 60.0                         # loading a model before its first answer
SITTING_H = 12.0
GRADE_S = {"answer_only": 1.0, "ladder": 2.0, "tool_mode": 4.0, "ladder_tool": 4.0}
OUT_TOKENS = {False: {"answer_only": 300, "ladder": 900, "tool_mode": 180, "ladder_tool": 180},
              True: {"answer_only": 2200, "ladder": 3500, "tool_mode": 900, "ladder_tool": 900}}
STEPS = {"answer_only": 1, "ladder": 1, "tool_mode": 4, "ladder_tool": 7}


def unit_counts(plan: str, quick: Quick | None = None) -> dict[str, int]:
    """Trials per suite for one entry. first-pass assumes pass B covers three rungs of 12 cases."""
    out: dict[str, int] = defaultdict(int)
    for u in plan_units(plan, quick=quick):
        out[u.suite] += 1
    if plan == "first-pass":
        out["ladder"] += 3 * SAMPLE_PER_RUNG * 2       # pass B (3 rungs x 2 more trials), costed as probe rungs
    return dict(out)


def seconds(model: LocalModel, thinking: bool, plan: str, *, speed_factor: float = 1.0,
            out_tokens: dict | None = None, load: bool = True, quick: Quick | None = None) -> float:
    tok = dict(OUT_TOKENS[thinking])
    if out_tokens:
        tok.update(out_tokens)
    rate = model.decode_tok_s * speed_factor
    total = LOAD_S if load else 0.0
    for suite, n in unit_counts(plan, quick).items():
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


def table(entries: list[Entry], plan: str, factor: float = 1.0, toks: dict | None = None,
          quick: Quick | None = None) -> list[tuple[Entry, float]]:
    """(entry, hours) in run order; a load is counted whenever the model changes."""
    rows, prev = [], None
    for e in entries:
        ot = {s: v for (th, s), v in (toks or {}).items() if th == e.thinking}
        h = seconds(e.model, e.thinking, plan, speed_factor=factor, out_tokens=ot,
                    load=e.model.engine_id != prev, quick=quick) / 3600
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


def _print_plan(title: str, rows: list[tuple[Entry, float]], *, sittings_of: float | None = SITTING_H) -> None:
    total = sum(h for _, h in rows)
    print(f"\n{title} ({len(rows)} entries): {total:.1f} h")
    for i, sit in enumerate(sittings(rows, sittings_of) if sittings_of else [rows], 1):
        if sittings_of:
            print(f"  sitting {i}: {sum(h for _, h in sit):.1f} h")
        for e, h in sit:
            print(f"    {e.label:<72} {h * 60:5.0f} min")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling plan")
    ap.add_argument("--measured", help="results folder with a smoke run")
    ap.add_argument("--model-dir", help="default: oMLX's model folder")
    ap.add_argument("--plan", choices=("quick", "first-pass", "full"), help="show one plan (default: all of them)")
    ap.add_argument("--tiers", default="0", help="with --plan first-pass or full: entry tiers, e.g. 0 or 0,1,2,3")
    add_quick_args(ap)
    a = ap.parse_args(argv)
    mdir = Path(a.model_dir) if a.model_dir else model_dir_from_settings()
    if not mdir:
        print("oMLX's model folder is not set", file=sys.stderr)
        return 2
    try:
        quick = quick_from_args(a)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    models, skipped = scan(mdir)
    factor, toks = measured(Path(a.measured), models) if a.measured else (1.0, {})
    print(f"basis: {'measured from the smoke step' if a.measured else 'planning guesses'}; speed factor {factor:.2f}")
    print(f"\nmodels found ({len(models)}):")
    for m in models:
        modes = "/".join("on" if t else "off" for t in m.thinking)
        print(f"  {m.label:<36} {m.engine_id:<44} {m.quant['scheme']:<16} "
              f"{(m.bits_per_weight or 0):4.2f} bpw  {m.disk_gb:5.1f} GB  thinking {modes} (ships {modes.split('/')[0]})"
              + ("" if m.standard else "  [other quantisation]"))
    print(f"folders left out: {len(skipped)}")
    for f, why in skipped:
        print(f"  {f}: {why}")
    if a.plan == "quick":
        ents = quick_entries(models, quick)
        rows = table(ents, "quick", factor, toks, quick=quick)
        q = quick.to_dict()
        _print_plan(f"quick look: {q['per_rung']} cases a rung, rungs {','.join(map(str, q['rungs']))}, 1 trial, "
                    f"answer only, thinking {'off for every model' if q['thinking'] == 'off' else 'as each model ships'}",
                    rows, sittings_of=None)
        sm = table(ents[:1], "smoke", factor, toks)
        print(f"\nplus the smoke step: about {sm[0][1] * 60:.0f} minutes" if sm else "")
        return 0
    if a.plan in ("first-pass", "full"):
        tiers = tuple(int(t) for t in a.tiers.split(","))
        for t in tiers:
            ents = ordered_entries(models, tiers=(t,))
            if ents:
                _print_plan(f"{a.plan}, tier {t}", table(ents, a.plan, factor, toks))
        return 0
    for plan, tiers in (("first-pass", (0,)), ("full", (0,)), ("full", (1,)), ("full", (2,)), ("full", (3,))):
        ents = ordered_entries(models, tiers=tiers)
        if ents:
            _print_plan(f"{plan}, tier {tiers[0]}", table(ents, plan, factor, toks))
    for th in ("off", "shipped"):
        q = Quick(per_rung=quick.per_rung, rungs=quick.rungs, thinking=th)
        rows = table(quick_entries(models, q), "quick", factor, toks, quick=q)
        print(f"\nquick look, thinking {th}, {q.per_rung} cases a rung: {sum(h for _, h in rows):.1f} h "
              f"(--plan quick --thinking {th} lists its entries)")
    sm = table(ordered_entries(models, tiers=(0,)), "smoke", factor, toks)
    if sm:
        print(f"\nsmoke on one entry: about {min(h for _, h in sm) * 60:.0f}-{max(h for _, h in sm) * 60:.0f} minutes")
    return 0
