"""python -m edat.sidekick.ceiling <command>

  list                              rungs and their case counts
  validate [--rung R] [--template ID] [--trials 3]
  preflight --out OUT [--engine] [--weights]   read-only checks before a run
  plan [--measured OUT]             duration estimate per model and in total
  run --entry KEY ...               run one model entry over the ladder (see run.py)
  run-all --plan P --tiers 0 ...    every entry found in the model folder, in run order
  omlx-profile [--apply]            write the authors' sampling into oMLX for new models (its own go-ahead)
  report RESULTS_DIR [--out DIR]    the one-page ceiling report (see report.py)
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["omlx-profile"]:
        from edat.sidekick.ceiling import omlx_profile
        return omlx_profile.main(argv[1:])
    if argv[:1] in (["run"], ["run-all"], ["report"], ["plan"], ["preflight"]):
        from edat.sidekick.ceiling import report, run
        return {"run": run.main, "run-all": run.run_all_main, "plan": run.plan_main, "report": report.main,
                "preflight": run.preflight_main}[argv[0]](argv[1:])
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    v = sub.add_parser("validate")
    v.add_argument("--rung", action="append", default=[])
    v.add_argument("--template", action="append", default=[])
    v.add_argument("--trials", type=int, default=3)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        from edat.sidekick.ceiling.rungs import RUNGS, ladder_cases
        cases = ladder_cases()
        for r in RUNGS:
            n = sum(1 for c in cases if c.rung.id == r.id)
            print(f"{r.n}  {r.id:<9} {n:>3} cases  {r.mode:<11} {r.title}")
        return 0
    from edat.sidekick.ceiling.validate import validate
    rep = validate(a.rung or None, a.template or None, trials=a.trials)
    for p in rep.problems:
        print(f"PROBLEM {p.case} trial {p.trial}: {p.what}")
    print(f"validated {rep.checked} instances, {len(rep.problems)} problems")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
