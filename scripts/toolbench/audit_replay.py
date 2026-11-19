#!/usr/bin/env python3
"""Replay a blind case audit's answers through the real loop: an oracle check on the cases.

PRIVATE INPUT, PUBLISHABLE OUTPUT. The findings file of a blind audit (one JSON
object per line: set, id, kind, class, severity, blind_calls [{tool, args}],
blind_final, ...) holds held-out case text and must stay private. This script
reads it, plays each Toolbench answer through production's model-led loop with
the world fakes (runner.scripted_client_class: no model, no engine, no
network, every confirmation approved, a temp Harbor home, the canonical world
on CANONICAL_TODAY), scores it with the
CURRENT cases and scorer, and writes ids, kinds, classes, verdicts and labels
only. No prompt, answer or check text is written or printed.

    .venv/bin/python scripts/toolbench/audit_replay.py --findings <private findings.jsonl> --out <report.jsonl>

How to read it, after changing cases:
  * a `flag` (the blind answer agreed with the case; the audit flagged
    something else, e.g. a weak check) must still PASS: a strengthened check
    that fails a correct answer is a regression;
  * a `disagreement` of class a, b, c or e (the audit judged the case wrong)
    should now PASS, unless its fix changed the prompt so that the blind answer
    no longer fits it (for example the prompt now carries the missing detail
    the blind solver asked for); the case changelog says which;
  * class f (the auditor was wrong) may stay failed.
Exit 0 when every `flag` passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODEL = "gemma-4-E4B-it-oQ4e-mtp"


def _plan(finding: dict) -> list[dict]:
    calls = [c for c in finding.get("blind_calls") or () if isinstance(c, dict) and c.get("tool")]
    plan = [{"think": "", "calls": [(c["tool"], dict(c.get("args") or {}))]} for c in calls]
    return plan + [{"think": "", "text": str(finding.get("blind_final") or "")}]


def replay(findings: list[dict]) -> list[dict]:
    from scripts.toolbench import runner
    home = Path(tempfile.mkdtemp(prefix="toolbench-audit-"))
    runner.isolate(home, omlx_port=runner.DEFAULT_OMLX_PORT)
    from scripts.toolbench.cases import load_cases
    from scripts.toolbench.scorer import score
    from scripts.toolbench.world import CANONICAL_TODAY, build_world
    from service import config
    from service.config.endpoints import role_target
    settings = home / "model_settings.json"
    settings.write_text(json.dumps({"models": {MODEL: {"max_context_window": 16384}}}))
    config.OMLX_MODEL_SETTINGS = settings
    # The audit was written against the canonical facts (CANONICAL_TODAY), so the
    # replay world must stand on that date or every relative date in an answer drifts.
    world = build_world(today=CANONICAL_TODAY)
    names = runner.install_fakes(world)
    writes, refused = runner.write_tool_names(), runner.host_refused_tools()
    runner.bind_model(MODEL)
    client = runner.scripted_client_class()(api_key="toolbench-audit", target=role_target("agent"))
    runner.set_clock(world.now)
    cases = {c.id: c for c in load_cases()}
    out = []
    try:
        for f in findings:
            base = {k: f.get(k) for k in ("id", "kind", "class", "severity", "split")}
            case = cases.get(f["id"])
            if case is None:
                out.append({**base, "result": "no such case"})
                continue
            if not (f.get("blind_calls") or f.get("blind_final")):
                out.append({**base, "result": "no blind answer"})
                continue
            bound = case.bind(world.facts)
            client.plan, client.failure = _plan(f), ""
            traj = asyncio.run(runner.run_case(client, MODEL, bound, world, timeout=60))
            v = score(bound, traj, registry_names=names, today=world.today, write_tools=writes,
                      host_refused=refused)
            out.append({**base, "result": "pass" if v.passed else "fail", "labels": v.labels,
                        "report_only": bool(case.report_only), "plan_followed": not client.failure,
                        "error": bool(traj.get("error"))})
    finally:
        runner.set_clock(None)
        runner.uninstall_fakes()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--findings", required=True, help="the audit's PRIVATE findings JSONL")
    ap.add_argument("--out", required=True, help="where to write the replay (ids and verdicts only)")
    args = ap.parse_args(argv)
    rows = [json.loads(line) for line in Path(args.findings).expanduser().read_text().splitlines() if line.strip()]
    findings = [r for r in rows if str(r.get("set", "")).lower() == "toolbench"]
    result = replay(findings)
    Path(args.out).expanduser().write_text("".join(json.dumps(r) + "\n" for r in result))
    tally = Counter((r["kind"], r["class"], r["result"]) for r in result)
    for (kind, cls, res), n in sorted(tally.items()):
        print(f"{kind:<13} {cls:<4} {res:<16} {n}")
    bad = [r["id"] for r in result if r["kind"] == "flag" and r["result"] == "fail" and not r.get("report_only")]
    if bad:
        print(f"\nflagged cases whose agreed blind answer now FAILS: {', '.join(sorted(set(bad)))}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
