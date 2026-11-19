"""Case validation: a scripted oracle must pass, doing nothing must fail.

Every case carries an `oracle`: the model output that solves it, per turn,
as ScriptedClient turns (`{calls: [{name, arguments}]}` or `{text: ...}`),
written with the case's `{fact}` placeholders. For each checked world seed:

* the oracle, replayed through the reference loop and the real lane, must give
  success with no violation;
* the null model (answers "OK." and calls nothing) must fail.

Run as a module to check case files:

    python -m edat.cases.oracle test_fixtures/agentic/dev
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from edat.cases.bind import bind
from edat.cases.loader import iter_cases
from edat.cases.runner import run_trial
from edat.cases.schema import problems
from edat.models.stub import NullClient, ScriptedClient

CHECK_SEEDS = (1, 2, 3)


def oracle_turns(bound: dict) -> list[dict]:
    steps = []
    for s in bound["sessions"]:
        for t in s.get("turns", []):
            steps.extend(t.get("oracle") or [{"text": "OK."}])
    return steps


@dataclass
class CaseCheck:
    case_id: str
    ok: bool
    problems: list[str] = field(default_factory=list)


def check_case(case: dict, *, seeds=CHECK_SEEDS, env_factory=None) -> CaseCheck:
    errs = problems(case)
    if errs:
        return CaseCheck(case.get("id", "?"), False, errs)
    if not any(t.get("oracle") for s in bind(case, seeds[0])["sessions"] for t in s["turns"]):
        return CaseCheck(case["id"], False, ["no oracle"])
    out = []
    kw = {"env_factory": env_factory} if env_factory else {}
    for ws in seeds:
        try:
            bound = bind(case, ws)
            oracle = ScriptedClient(oracle_turns(bound), name="oracle")
            r = run_trial(case, oracle, world_seed=ws, **kw)
            v = r.verdict
            if not v.success:
                out.append(f"seed {ws}: oracle fails: {v.reasons[:3]} {v.violations[:2]} {v.labels[:4]}")
            if oracle.index < len(oracle.turns):
                out.append(f"seed {ws}: oracle has {len(oracle.turns) - oracle.index} unused steps")
            n = run_trial(case, NullClient(), world_seed=ws, **kw).verdict
            if n.success:
                out.append(f"seed {ws}: doing nothing passes")
        except Exception as exc:  # report, do not stop the sweep
            out.append(f"seed {ws}: {type(exc).__name__}: {exc}")
    return CaseCheck(case["id"], not out, out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--seeds", default="1,2,3")
    a = ap.parse_args(argv)
    seeds = tuple(int(x) for x in a.seeds.split(","))
    bad = 0
    n = 0
    for case in iter_cases(a.paths):
        n += 1
        res = check_case(case, seeds=seeds)
        if not res.ok:
            bad += 1
            print(f"FAIL {res.case_id}: " + " | ".join(res.problems))
    print(f"{n} cases checked, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
