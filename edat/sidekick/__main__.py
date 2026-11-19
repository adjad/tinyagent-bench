"""Command line: python -m edat.sidekick <command>

  selftest                      prove the sandbox blocks network and outside writes
  validate [--template ID ...] [--prefix P] [--trials N]
                                reference passes, wrong/empty/"OK." fail, mutants caught
  counts                        case counts per type, split, core and tool mode
  export-dev [--out DIR]        write the development split (public) to test_fixtures/sidekick/cases/dev
  export-heldout                write the held-out split under $EDAT_PRIVATE_DIR/sidekick (never the repo)
  human-sheet [--out FILE]      export the 30-case human-graded sheet (development seeds)
  run ...                       run a model (see edat/sidekick/runner.py)
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["run"]:              # argparse's REMAINDER drops a leading --option, so hand over directly
        from edat.sidekick.runner import main as run_main
        return run_main(argv[1:])
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    v = sub.add_parser("validate")
    v.add_argument("--template", action="append", default=[])
    v.add_argument("--prefix", default="")
    v.add_argument("--trials", type=int, default=3)
    sub.add_parser("counts")
    e = sub.add_parser("export-dev")
    e.add_argument("--out", default=None)
    sub.add_parser("export-heldout")
    h = sub.add_parser("human-sheet")
    h.add_argument("--out", default="sidekick-human-sheet.csv")
    r = sub.add_parser("run")
    r.add_argument("rest", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        from edat.sidekick.sandbox.run import SandboxUnavailable
        from edat.sidekick.sandbox.selftest import run_selftest
        try:
            rep = run_selftest()
        except SandboxUnavailable as exc:
            print(f"sandbox unavailable: {exc}")
            return 2
        print(rep.text())
        return 0 if rep.passed else 1
    if a.cmd == "validate":
        from edat.sidekick.gen.registry import all_templates
        from edat.sidekick.validate import validate_templates
        ts = [t for t in all_templates()
              if (not a.template or t.id in a.template) and t.id.startswith(a.prefix)]
        if not ts:
            print("no templates selected")
            return 1
        rep = validate_templates(ts, trials=a.trials)
        for p in rep.problems:
            print(f"PROBLEM {p.template} [{p.seed.rsplit('|', 1)[-1]}]: {p.what}")
        print(f"validated {len(ts)} templates, {rep.checked} instances, {len(rep.problems)} problems")
        return 0 if rep.ok else 1
    if a.cmd == "counts":
        from edat.sidekick.gen.core import counts_table
        print(json.dumps(counts_table(), indent=1))
        return 0
    if a.cmd == "export-dev":
        from edat.sidekick.splits import export_dev
        print(export_dev(a.out))
        return 0
    if a.cmd == "export-heldout":
        from edat.sidekick.splits import export_heldout
        print(export_heldout())
        return 0
    if a.cmd == "human-sheet":
        from edat.sidekick.human import export_sheet
        print(export_sheet(a.out))
        return 0
    if a.cmd == "run":
        from edat.sidekick.runner import main as run_main
        return run_main(a.rest)
    return 1


if __name__ == "__main__":
    sys.exit(main())
