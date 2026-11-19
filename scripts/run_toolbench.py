#!/usr/bin/env python3
"""Hermetic tool-calling scorecard: compare models on Harbor's real model-led Ask turn.

    .venv/bin/python scripts/run_toolbench.py --list                      # cases, no model
    .venv/bin/python scripts/run_toolbench.py --dry-run                   # load + bind cases, no model
    .venv/bin/python scripts/run_toolbench.py                             # the rostered agent model, 3 trials
    .venv/bin/python scripts/run_toolbench.py --models A,B --trials 3
    .venv/bin/python scripts/run_toolbench.py --only mail,safety --ids mail_chase_balance
    .venv/bin/python scripts/run_toolbench.py --models LFM2.5-24B-A2B-MLX-4bit --exclusive
    .venv/bin/python scripts/run_toolbench.py --split gate                    # the frozen fine-tune gate
    .venv/bin/python scripts/run_toolbench.py --world-seed 7                  # a variant world
    .venv/bin/python scripts/run_toolbench.py --models M --thinking default,narration-off,off
    .venv/bin/python scripts/run_toolbench.py --models G,N --profile-as G     # N treated exactly as G
    .venv/bin/python scripts/run_toolbench.py --engine openai --port 8080 --models qwen3-1.7b   # llama.cpp etc.
    .venv/bin/python scripts/run_toolbench.py --engine openai --port 11434 --models M --probe-only
    .venv/bin/python scripts/run_toolbench.py --models A,B --sampling fixed --treatment probe
    .venv/bin/python scripts/run_toolbench.py --export-shingles out.json      # held-out prompts, hashed
    .venv/bin/python scripts/run_toolbench.py --report ~/.moe/evals/toolbench/<run>   # rescored offline
    .venv/bin/python scripts/run_toolbench.py --report <run> --stored        # the verdicts stored at run time
    .venv/bin/python scripts/run_toolbench.py --report <run> --compare <baseline_run>

Every turn runs the way production's model-led Ask does (runner.prepare_turn):
model_only on, model-led discovery from a small preload plus name-only stubs,
the Ask hints and guards, and the case's access mode. Every tool is a fake over
one synthetic world (scripts/toolbench/world.py): nothing real is read, written
or sent, and network/subprocess access is refused. The real Harbor on :8765 is
never contacted: a live run refuses to start while :8765 or :8791 listens
(--allow-harbor-running overrides). Models run one at a time; the harness loads a
model if needed and never evicts one unless --exclusive is passed (which unloads
the others for the run and reloads them afterwards).

A fair comparison: with more than one model, --exclusive is the default
(--shared opts out) and --profile-as defaults to the packaged agent model.
Before any trial the engine's per-model settings of each candidate and of the
profile model are compared on runner.PARITY_KEYS (sampling, thinking, window,
MTP); a mismatch aborts unless --allow-engine-mismatch. The clock is pinned to
the world's "now" for prompts and tools, and trial t sends seed --seed-base + t
to every model. An engine/stream failure is labelled engine_error and excluded
from every rate (the report counts them), not scored as the model's.

Results land in ~/.moe/evals/toolbench/<run_id>/ (results.jsonl + meta.json).
meta.json records the pinned time, the seeds, both sides' engine settings and
their mismatches, and per model the binding (context window, list membership,
--profile-as) and the effective thinking flags of each variant.

FAIR ACROSS MODEL TYPES (see docs/benchmark/07-harness-review.md):
  --engine omlx|openai  oMLX (managed) or any OpenAI-compatible server on
                     127.0.0.1:--port. The network guard allows exactly that port.
  probe              before scoring, each model is qualified (scripts/toolbench/
                     probe.py): served, verified window >= 8,192, structured and
                     streamable tool calls; otherwise it is skipped (unless
                     --allow-unqualified). --probe-only stops after it.
  --sampling         engine-default | fixed | file:<json>; fixed is the default
                     for any comparison or non-oMLX engine (policy.py).
  --treatment        packaged | profile-as | probe: which per-model lists and
                     window each model gets. probe (default for comparisons):
                     its own measured support, window min(--context-window,
                     verified).
  --timeout          hard per-turn limit (default 600s); --latency-budget (180s)
                     is reported separately as "pass <= budget".
meta.json carries the policy (and its fingerprint), the probe, host, engine and
model metadata; --compare refuses two runs with different hard policy keys
unless --allow-policy-mismatch.

`scripts/test_model.py` stays the live smoke test against real data; this is
the repeatable comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.toolbench import cloud  # noqa: E402  (no service imports)
from scripts.toolbench import policy as pol  # noqa: E402  (no service imports)
from scripts.toolbench import report as rep  # noqa: E402  (no service imports)
from scripts.toolbench.cases import case_set, load_cases, select  # noqa: E402

# Empty = the packaged models.yaml agent model (what a fresh install runs).
DEFAULT_MODEL = ""
RESULTS_ROOT = Path.home() / ".moe" / "evals" / "toolbench"  # resolved BEFORE HARBOR_HOME moves
_TRANSIENT = re.compile(r"409|ModelLoadError|not loaded|ConnectError|RemoteProtocolError|choices|"
                        r"IncompleteStreamError|Invalid inference stream data|"
                        r"connection unavailable|couldn't verify the local AI engine", re.I)
# The live Harbor backend and its bundled engine. A run refuses to start while
# either listens: Harbor would contend for the one GPU and may evict or reload
# models mid-run.
HARBOR_PORTS = (8765, 8791)


def _git_sha() -> str:
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def _swap_ok() -> bool:
    """False when free swap is low AND memory pressure is high (same rule as the Ling eval)."""
    out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
    m = re.search(r"free = ([\d.]+)([MG])", out)
    free_mb = float(m.group(1)) * (1024 if m.group(2) == "G" else 1) if m else 1e9
    mp = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
    pct = re.search(r"free percentage: (\d+)%", mp)
    return not (free_mb < 500 and (int(pct.group(1)) if pct else 0) < 25)


def _listening(port: int) -> bool:
    """True when something on this Mac listens on TCP `port`. Asks lsof; never connects."""
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _row(run_id, model, case, trial, traj, verdict, variant="default", profile_as=None, key=None) -> dict:
    from scripts.toolbench.runner import RAW_KEEP
    steps = [{k: s.get(k) for k in ("calls", "error", "tool_errors", "ttft", "seconds", "prompt_tokens_est",
                                     "output_chars", "reasoning_chars", "stubs", "offered", "guard_400",
                                     "sent", "reasoning", "finish", "cloud")}
             | {"content": (s.get("content") or "")[:RAW_KEEP]} for s in traj.get("steps", ())]
    label = model if variant == "default" else f"{model} +{variant}"
    key = _cloud_key(key, traj)
    return {"run": run_id, "model": label, "base_model": model, "variant": variant,
            "profile_as": profile_as, "access": traj.get("access"), "key": key,
            "split": case.split, "case": case.id, "category": case.category,
            "report_only": getattr(case, "report_only", "") or None,
            "trial": trial, "no_tool": case.no_tool, "path": traj.get("path"),
            "route": traj.get("route"), "steps": steps, "final": traj.get("final", "")[:2000],
            "effects": traj.get("effects"), "asked": traj.get("asked"), "escapes": traj.get("escapes"),
            "message_draft": traj.get("message_draft"),
            "error": traj.get("error"), "timeout": traj.get("timeout"), "v": verdict.to_dict()}


def _cloud_key(key, traj) -> dict | None:
    """A cloud row's key with what OpenRouter actually served this trial (the
    results store reads these without special cases): resolved_model and
    upstream_provider, the most frequent over the trial's requests, and every
    one seen when they differ. requested_model and run_date come from the run."""
    recs = [c for s in traj.get("steps", ()) for c in s.get("cloud") or ()]
    if not recs or key is None:
        return key
    def most(field):
        seen = Counter(str(c[field]) for c in recs if c.get(field))
        return (seen.most_common(1)[0][0] if seen else None), sorted(seen)
    resolved, all_resolved = most("model")
    provider, all_providers = most("provider")
    return {**key, "resolved_model": resolved, "upstream_provider": provider,
            "resolved_models": all_resolved, "upstream_providers": all_providers}


def _dry_run(args, cases) -> int:
    """Offline: bind every case to the world and check its tool names and host
    preparation. Runs no model and tests no routing (production has no router
    menu any more; the model chooses). `host-forbids` flags an expected tool the
    host's own guards withhold this turn, which no model can then call."""
    from scripts.toolbench import runner
    from scripts.toolbench.world import build_world
    world = build_world(seed=args.world_seed)
    names = runner.install_fakes(world)
    runner.set_clock(world.now)
    try:
        return _dry_run_bound(args, cases, runner, world, names)
    finally:
        runner.set_clock(None)
        runner.uninstall_fakes()


def _dry_run_bound(args, cases, runner, world, names) -> int:
    if args.engine == "openrouter":
        model = runner.bind_cloud_model(args.models.split(",")[0].strip(),
                                        credential_name=args.cloud_keychain or "toolbench")["model"]
    else:
        model = runner.bind_model(args.models.split(",")[0].strip() or _agent_model(),
                                  profile_as=args.profile_as or None)["model"]
    from service.config.endpoints import role_target
    client = runner.bench_client_class()(target=role_target("agent"))
    bad = 0
    for c in (c.bind(world.facts) for c in cases):
        problems = []
        for field, tools in (("expect", {t for s in c.expect for t in s.tools}), ("forbid", c.forbid),
                             ("confirm", c.confirm), ("no_confirm", c.no_confirm),
                             ("refused", c.refused)):
            if unknown := sorted(set(tools) - names):
                problems.append(f"{field} names unknown {unknown}")
        turn = runner.prepare_turn(client, c.prompt, c.history)
        withheld = set(turn.route["forbidden_tools"])
        if blocked := ["/".join(s.tools) for s in c.expect if not set(s.tools) - withheld]:
            problems.append(f"host-forbids {blocked}")
        bad += bool(problems)
        head = ",".join(turn.route["preload"][:3]) or "-"
        needs = ",".join(sorted(c.access_needed)) or "-"
        print(f"{c.id:<28} {c.access:<7} denied={needs:<9} direct={'y' if turn.route['ask_direct'] else '-'} "
              f"preload={head:<44} {'; '.join(problems) or 'ok'}")
    print(f"\n{len(cases)} cases bound for {model}, {bad} with problems (no model was run)")
    models = list(dict.fromkeys([*_references(args), *([m.strip() for m in args.models.split(",") if m.strip()]
                                                       or [model])]))
    try:
        policy = _resolve_policy(args, models)
    except (ValueError, OSError) as e:
        print(f"policy: {e}")
        return 2
    print(f"engine={policy['engine']}:{policy['port']}  sampling={policy['sampling']} "
          f"{policy['sampling_params'].get(models[0]) or '(engine decides)'}  treatment={policy['treatment']}  "
          f"window={policy['context_window'] or 'production'}  timeout={policy['timeout_s']:g}s  "
          f"budget={policy['latency_budget_s']:g}s  probe={policy['probe']}  "
          f"fingerprint={policy['fingerprint']}  client={type(client).__name__}")
    if args.engine == "openrouter":
        _cloud_estimate(args, cases, models, policy)
    return 0


def _cloud_estimate(args, cases, models, policy) -> dict:
    """Print what a cloud run would send each model and cost (no request is sent)."""
    sizes = None
    if args.cost_from:
        sizes = cloud.measured_sizes(rep.load_rows(Path(args.cost_from).expanduser()))
    variants = [v.strip() for v in args.thinking.split(",") if v.strip()]
    table = cloud.load_models()
    est = cloud.estimate(models, [c.id for c in cases], trials=args.trials, variants=variants,
                         table=table, sizes=sizes)
    print(f"\nCLOUD REFERENCE ESTIMATE ({est['source']} prompt sizes; list prices; no caching; "
          f"{len(cases)} cases x {args.trials} trials x {variants})")
    for m, e in est["models"].items():
        entry = table[m]
        blocked = [v for v in variants if cloud.variant_blocked(entry, v)]
        print(f"  {m:<36} ~${e['usd']:>8.2f}  in {e['input_tokens']:>11,}  out {e['output_tokens']:>10,}  "
              f"sampling sent {policy['sampling_params'].get(m) or '(provider default)'}"
              + (f"  (no {','.join(blocked)} variant)" if blocked else ""))
    line = f"  total ~${est['total_usd']:.2f}"
    if args.cost_budget:
        line += f"  vs --cost-budget ${args.cost_budget:.2f}" + ("  OVER BUDGET" if est["total_usd"] > args.cost_budget
                                                                else "")
    print(line + "  (nothing was sent)")
    return est


def rescore(run_dir: Path, rows: list[dict]) -> tuple[list[dict], dict]:
    """`rows` with every verdict recomputed by the CURRENT scorer, cases and fakes.

    A finished run keeps trajectories (calls, effects, asked, final) but not the
    judgement; scoring is a pure function of those, so a fixed scorer or case file
    applies to old runs without a model. Needs the isolated service import the dry
    run uses (no server, no model). The world is rebuilt from the run's meta.json
    (date, world seed) so `{fact}` placeholders bind to what the model saw. A row
    whose case no longer exists keeps its stored verdict. Returns (rows, summary).
    """
    from datetime import date

    from scripts.toolbench import runner
    from scripts.toolbench.scorer import score
    from scripts.toolbench.world import build_world
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    today = date.fromisoformat(meta["today"]) if meta.get("today") else None
    world = build_world(today, seed=meta.get("world_seed"))
    names = runner.install_fakes(world, seed=False)
    try:
        writes, refused = runner.write_tool_names(), runner.host_refused_tools()
    finally:
        runner.uninstall_fakes()
    cases = {c.id: c.bind(world.facts) for c in load_cases()}
    out, kept, changed = [], 0, 0
    for row in rows:
        case = cases.get(row["case"])
        if case is None:
            kept += 1
            out.append(row)
            continue
        v = score(case, rep.traj_from_row(row, meta), registry_names=names, today=world.today,
                  write_tools=writes, host_refused=refused).to_dict()
        changed += v["passed"] != row["v"]["passed"]
        out.append({**row, "v": v, "v_stored": row["v"], "report_only": case.report_only or None})
    return out, {"rows": len(rows), "rescored": len(rows) - kept, "kept_stored": kept,
                 "verdict_flipped": changed}


def _pass_counts(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """model -> (passed, scored trials), engine-excluded trials left out."""
    out: dict[str, list[int]] = {}
    for r in rows:
        if r["v"].get("excluded"):
            continue
        n = out.setdefault(r["model"], [0, 0])
        n[0] += bool(r["v"]["passed"])
        n[1] += 1
    return {m: (a, b) for m, (a, b) in out.items()}


def _report(run_dir: Path, *, stored: bool) -> list[dict]:
    """Rows for a finished run: rescored by default, the stored verdicts with --stored."""
    rows = rep.load_rows(run_dir)
    if stored:
        return rows
    from scripts.toolbench import runner
    if "service" not in sys.modules:  # --compare rescores a second run in the same process
        runner.isolate(Path(tempfile.mkdtemp(prefix="toolbench-")), omlx_port=runner.DEFAULT_OMLX_PORT)
    rows, summary = rescore(run_dir, rows)
    before = _pass_counts([{**r, "v": r.get("v_stored", r["v"])} for r in rows])
    after = _pass_counts(rows)
    print(f"rescored {summary['rescored']}/{summary['rows']} rows with the current scorer and cases "
          f"({summary['kept_stored']} kept their stored verdict, {summary['verdict_flipped']} changed):")
    for m in after:
        print(f"  {m}: pass {before.get(m, (0, 0))[0]}/{before.get(m, (0, 0))[1]} stored -> "
              f"{after[m][0]}/{after[m][1]} rescored")
    print()
    return rows


def _sessions_note(*run_dirs: Path) -> str:
    """A warning when a run predates per-case session clearing (runner.CASE_SESSIONS).

    Before it, every case's question stayed in the run's session store and came
    back as HISTORICAL MEMORY in later cases' prompts, so those pass rates are not
    comparable with a run that cleared sessions per case."""
    from scripts.toolbench.runner import CASE_SESSIONS
    old = []
    for d in run_dirs:
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        if meta.get("case_sessions") != CASE_SESSIONS:
            old.append(d.name)
    if not old or len(old) == len(run_dirs):
        return ""
    return (f"WARNING: {', '.join(old)} ran before each case got a clean session store (earlier cases' "
            "questions reached later prompts). These pass rates are not comparable; rerun it.")


def _meta(run_dir: Path) -> dict:
    path = run_dir / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _agent_model() -> str:
    from service.config import _packaged_config
    return str(_packaged_config()["roles"]["agent"])


async def _recover(client, model, *, wait: float = 240.0) -> None:
    """Wait for the engine to come back and the model to be resident again.

    oMLX can restart on its own mid-run (observed 2026-10-14: the server died
    during a prefill with nothing in its log and was back 5s later). Polls
    instead of failing on the first refused connection.
    """
    from service.inference.inference_errors import ModelNotDownloadedError
    deadline = time.monotonic() + wait
    last = None
    while time.monotonic() < deadline:
        try:
            await client.make_resident(model)
            return
        except ModelNotDownloadedError:
            # The engine answered: it has no such model. Waiting cannot fix that
            # (seen 2026-10-29: its model folder was renamed under a running oMLX).
            try:
                served = await client.models()
            except Exception:  # noqa: BLE001
                served = ["(inventory unavailable)"]
            raise RuntimeError(f"oMLX at {client.base_url} does not serve {model}; it serves {served}. "
                               "Rescan or restart oMLX if the files are on disk.") from None
        except Exception as e:  # noqa: BLE001 — engine down or still loading; keep waiting
            last = e
            await asyncio.sleep(3.0)
    raise RuntimeError(f"engine did not recover within {wait:.0f}s: {last}")


async def _one(runner, score, client, model, case, world, names, writes, args, run_id, trial,
               variant, host_refused=frozenset()) -> dict:
    """One trial, retried once after a transient engine error (409, restart, dropped stream)."""
    from scripts.toolbench.scorer import MODEL_STREAM_ERROR
    traj = await runner.run_case(client, model, case, world, timeout=args.timeout, max_steps=args.max_steps)
    # A stream error the model's own output caused (arguments that are not
    # JSON) is scored, so it is never retried: a retry would be a free re-roll.
    if (traj.get("error") and _TRANSIENT.search(traj["error"])
            and not MODEL_STREAM_ERROR.search(traj["error"])):
        await _recover(client, model)
        traj = await runner.run_case(client, model, case, world, timeout=args.timeout,
                                     max_steps=args.max_steps)
    v = score(case, traj, registry_names=names, today=world.today, write_tools=writes,
              host_refused=host_refused)
    profile = (args.profile_as if args.treatment == "profile-as" and args.profile_as
               and args.profile_as != model else None)
    key = {**(getattr(args, "result_keys", {}) or {}).get(model, {}), "thinking": variant}
    return _row(run_id, model, case, trial, traj, v, variant, profile, key)


def _update_meta(out: Path, key: str, model: str, value) -> None:
    """meta.json[key][model] = value, kept across --resume."""
    path = out / "meta.json"
    meta = json.loads(path.read_text()) if path.exists() else {}
    meta.setdefault(key, {})[model] = value
    path.write_text(json.dumps(_scrubbed(meta), indent=2, default=str))


def _scrubbed(value):
    """`value` with the cloud key (if this run holds one) and any bearer token removed."""
    runner = sys.modules.get("scripts.toolbench.runner")
    if runner is None or not getattr(runner, "CLOUD_KEY", None):
        return value
    from scripts.toolbench import cloud
    return cloud.scrub(value, runner.CLOUD_KEY)


async def _run(args, cases) -> int:
    from scripts.toolbench import runner
    from scripts.toolbench.world import build_world

    world = build_world(seed=args.world_seed)
    names = runner.install_fakes(world)
    # Every model sees the same pinned date and time, in its prompt and its tools.
    runner.set_clock(world.now)
    try:
        return await _trials(args, cases, runner, world, names)
    finally:
        runner.set_clock(None)
        runner.uninstall_fakes()


async def _parity(runner, client, models, profile_as, allow, *, keys=None) -> tuple[dict, dict, bool]:
    """Engine settings of every model and of `profile_as`, and their mismatches
    on runner.PARITY_KEYS (or `keys`: under a fixed sampling policy only what
    the policy does not set). A profiled candidate is only treated like the
    profile model fairly when the engine samples and thinks for both the same
    way. The bool is False (stop the run) on a mismatch or unreadable settings,
    unless `allow`."""
    settings, mismatches, ok = {}, {}, True
    for m in dict.fromkeys([*models, *([profile_as] if profile_as else [])]):
        settings[m] = await runner.engine_settings(client, m)
    for m in models:
        if not profile_as or m == profile_as:
            continue
        unread = [x for x in (m, profile_as) if not settings[x]["source"]]
        if unread:
            mismatches[m] = {"unreadable": unread}
            print(f"engine parity: could not read oMLX settings for {unread}")
            ok = False
            continue
        diff = runner.engine_mismatches(settings[m], settings[profile_as])
        if keys is not None:
            diff = {k: v for k, v in diff.items() if k in keys}
        mismatches[m] = {k: list(v) for k, v in diff.items()}
        for k, (a, b) in diff.items():
            print(f"engine parity: {m} {k}={a!r} but {profile_as} {k}={b!r}")
        ok = ok and not diff
    if not ok:
        print(("--allow-engine-mismatch: continuing although " if allow else "ABORT: ")
              + "engine settings differ between a candidate and the profile model "
              "(align ~/.omlx/model_settings.json, or pass --allow-engine-mismatch)")
    return settings, mismatches, ok or allow


def _resolve_policy(args, models: list[str]) -> dict:
    """The run's policy from the CLI (policy.py). Sets args.treatment/sampling."""
    args.sampling = pol.sampling_name(args.sampling, models=models, engine=args.engine)
    args.treatment = pol.treatment_name(args.treatment, models=models, engine=args.engine,
                                        profile_as=args.profile_as)
    if args.engine == "openrouter":
        args.treatment = "cloud"          # Harbor's own cloud mode; no local lists or window apply
    table = pol.load_sampling_file(args.sampling[5:]) if args.sampling.startswith("file:") else None
    params = {m: pol.sampling_for(args.sampling, m, table) for m in models}
    kind = "local"
    if args.engine == "openrouter":
        from scripts.toolbench import cloud
        kind, ctable = cloud.KIND, cloud.load_models()
        # What each cloud model is actually sent: its author settings it accepts.
        params = {m: cloud.sent_sampling(params[m], cloud.entry_for(m, ctable))[0] for m in models}
    window = args.context_window if args.context_window else (
        pol.DEFAULT_WINDOW if args.treatment == "probe" else None)
    if args.treatment == "probe" and args.skip_probe:
        raise ValueError("--skip-probe with --treatment probe would bind every model with no measured support "
                         "(all lists off, unverified window); drop --skip-probe or use --treatment packaged")
    if getattr(args, "model_info", ""):
        from scripts.toolbench import runmeta
        runmeta.declared(args.model_info)          # validate before any model is touched
    return pol.build(engine=args.engine, port=cloud.ORIGIN if kind != "local" else args.omlx_port,
                     sampling=args.sampling, sampling_params=params,
                     treatment=args.treatment, profile_as=args.profile_as or None, context_window=window,
                     max_output_tokens=args.max_output_tokens or None, timeout_s=args.timeout,
                     latency_budget_s=args.latency_budget, max_steps=args.max_steps,
                     thinking=[v.strip() for v in args.thinking.split(",") if v.strip()],
                     world_seed=args.world_seed, seed_base=args.seed_base,
                     case_sessions=_case_sessions(),
                     probe=_probe_mode(args, models),
                     references=[m for m in _references(args) if m in models],
                     reference_treatment=args.reference_treatment, kind=kind)


def _probe_mode(args, models: list[str]) -> str:
    """skipped: --skip-probe, or a plain single-model oMLX run with production's
    treatment (the old default, no extra requests) unless --probe. Otherwise
    required (a candidate the probe blocks is skipped) or allow-unqualified.
    Under required, a model with production's own treatment (packaged, e.g.
    the references) is probed and recorded but never blocked."""
    if args.skip_probe or args.engine == "openrouter":
        return "skipped"                  # a cloud model has no local template or window to qualify
    if (not getattr(args, "probe", False) and len(models) <= 1 and args.engine == "omlx"
            and args.treatment == "packaged"):
        return "skipped"
    return "allow-unqualified" if args.allow_unqualified else "required"


def _references(args) -> list[str]:
    refs = [m.strip() for m in (getattr(args, "references", "") or "").split(",") if m.strip()]
    if getattr(args, "with_references", False):
        refs = list(dict.fromkeys([*pol.DEFAULT_REFERENCES, *refs]))
    return refs


def _treatment_for(args, model: str, references: list[str]) -> str:
    """References keep production's own treatment (--reference-treatment,
    default packaged) for a production-fidelity baseline; candidates get the
    run's --treatment."""
    return args.reference_treatment if model in references else args.treatment


def _case_sessions() -> str:
    from scripts.toolbench.runner import CASE_SESSIONS
    return CASE_SESSIONS


def _model_info(args, model: str, engine_info: dict) -> dict:
    """Everything known about `model`'s files and size: the engine's report,
    a local model path's config/header, and the user's declarations."""
    from scripts.toolbench import runmeta
    info: dict = {"engine_reported": {k: engine_info.get(k) for k in
                                      ("runtime", "model_row", "props", "ollama_show", "lmstudio_model")
                                      if engine_info.get(k) is not None}}
    paths = runmeta.model_paths(args.model_path)
    if path := paths.get(model) or paths.get("*"):
        info["files"] = runmeta.model_files(path)
    if args.model_info:
        table = runmeta.declared(args.model_info)
        if decl := table.get(model) or table.get("*"):
            info["declared"] = decl
    return info


async def _probe_one(runner, client, args, model, policy) -> dict:
    from scripts.toolbench import probe
    print(f"-- probe {model} (window policy {policy['context_window'] or 'production'})…")
    result = await probe.probe_model(
        client, model, window=int(policy["context_window"] or pol.DEFAULT_WINDOW),
        sampling=policy["sampling_params"].get(model) or {}, seed=args.seed_base,
        need_thinking_off="off" in policy["thinking"])
    print(f"   {probe.summary_line(result)}  ({result['seconds']}s)")
    for w in result["warnings"]:
        print(f"   note: {w}")
    for b in result["blocking"]:
        print(f"   BLOCKING: {b}")
    return result


async def _trials(args, cases, runner, world, names) -> int:
    from scripts.toolbench import probe as probe_mod
    from scripts.toolbench.scorer import score
    from service.config.endpoints import role_target

    writes, refused = runner.write_tool_names(), runner.host_refused_tools()
    cases = [c.bind(world.facts) for c in cases]
    variants = [v.strip() for v in args.thinking.split(",") if v.strip()]
    if bad := [v for v in variants if v not in runner.THINKING_MODES]:
        print(f"unknown --thinking {bad}; choose from {runner.THINKING_MODES}")
        return 2
    Client = runner.bench_client_class(args.engine)
    models = [m.strip() for m in args.models.split(",") if m.strip()] or [_agent_model()]
    cloud_run = args.engine == "openrouter"
    if cloud_run:
        runner.bind_cloud_model(models[0], credential_name=args.cloud_keychain or "toolbench")
    client = Client(timeout=args.timeout)       # inventory and residency; turns use a per-model client
    references = _references(args)
    # References run first, in every run, under the same clock, cases and seeds,
    # so every other model is reported as a paired difference from them.
    models = list(dict.fromkeys([*references, *models]))
    if args.engine != "omlx" and args.exclusive:
        print("--exclusive needs oMLX: an openai engine's server owns residency (load one model there)")
        return 2
    if len(models) > 1:
        # A comparison runs every candidate under the same policy, each alone
        # on the engine (oMLX: --exclusive unless --shared).
        if args.treatment == "profile-as" or (args.profile_as and not args.treatment):
            args.profile_as = args.profile_as or _agent_model()
        if not args.shared and args.engine == "omlx":
            args.exclusive = True
    try:
        policy = _resolve_policy(args, models)
    except (ValueError, OSError) as e:
        print(f"policy: {e}")
        return 2
    if args.treatment == "profile-as" and not args.profile_as:
        args.profile_as = _agent_model()
        policy = _resolve_policy(args, models)
    installed = await client.models()
    if args.profile_as and args.treatment == "profile-as" and args.profile_as not in installed:
        print(f"note: --profile-as {args.profile_as} is not installed; its per-model treatment "
              "still applies, it just cannot run itself")
    missing = [m for m in models if m not in installed]
    if missing:
        print(f"not served by the {args.engine} engine on :{args.omlx_port}: {missing}")
        close = [i for i in installed if any(m.lower()[:10] in i.lower() for m in missing)]
        if close:
            print(f"  close matches: {close}")
        return 2
    done: set[tuple] = set()
    if args.resume:
        out = Path(args.resume).expanduser()
        prior = rep.load_rows(out)
        run_id = prior[0]["run"] if prior else out.name
        done = {(r.get("base_model", r["model"]), r.get("variant", "default"), r["case"], r["trial"])
                for r in prior}
        started = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
        if started.get("case_sessions") != runner.CASE_SESSIONS:
            print(f"refusing to resume {out}: it started before each case got a clean session store, "
                  "so its earlier trials saw other cases' questions in their prompts. Start a new run.")
            return 2
        if pol.of_run(started).get("fingerprint") != policy["fingerprint"]:
            print(f"refusing to resume {out}: its policy differs from this command's "
                  f"({'; '.join(pol.conflicts(started, {**started, 'policy': policy})[0]) or 'fingerprint'}).")
            return 2
        print(f"resuming {out}: {len(done)} trials already recorded")
    else:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path(args.out).expanduser() if args.out else RESULTS_ROOT / run_id
    out.mkdir(parents=True, exist_ok=True)
    parity_keys = None if policy["sampling"] == "engine-default" else (
        tuple(k for k in runner.PARITY_KEYS if k not in pol.SAMPLING_KEYS))
    profile = args.profile_as if args.treatment == "profile-as" else None
    settings, parity, go = await _parity(runner, client, models, profile or None,
                                         args.allow_engine_mismatch, keys=parity_keys)
    seeds = {t: args.seed_base + t for t in range(1, args.trials + 1)}
    meta = {"run": run_id, "git": _git_sha(), "models": models, "trials": args.trials,
            "cases": [c.id for c in cases], "started": time.time(), "exclusive": args.exclusive,
            "today": world.today.isoformat(), "max_steps": args.max_steps,
            "world_seed": args.world_seed, "thinking": variants, "split": args.split or "all",
            "path": "model_led", "profile_as": profile or None, "omlx_port": args.omlx_port,
            "engine_kind": args.engine, "timeout": args.timeout, "latency_budget": args.latency_budget,
            "cleared_env": sorted(runner.CLEARED_ENV), "case_sessions": runner.CASE_SESSIONS,
            "access": dict(sorted(Counter(c.access for c in cases).items())),
            "now": world.now.isoformat(), "seed_base": args.seed_base, "seeds": seeds,
            "engine_settings": settings, "engine_parity": parity,
            "allow_engine_mismatch": args.allow_engine_mismatch, "policy": policy,
            "references": references,
            "author_sampling": (pol.author_notes() if policy["sampling"] == "author" else None),
            "case_set": case_set(),
            "host": getattr(args, "host_meta", None) or {}}
    if cloud_run:
        from service.inference import remote_policy as rp
        meta["cloud"] = {
            "kind": cloud.KIND, "origin": cloud.ORIGIN, "api_prefix": cloud.API_PREFIX,
            "key_source": getattr(args, "cloud_key_source", None),    # where it came from, never the value
            "splits": sorted({c.split for c in cases}), "run_date": datetime.now().date().isoformat(),
            "harbor_mode": "cloud-everything (config.set_cloud_everything: remote-only profile + remote_parity)",
            "harbor_replay_models": sorted(rp.REPLAY_REASONING_MODELS), "cost": None}
    # The perf spec's device / runtime / conditions blocks (edat.perf/1.0 names),
    # so Mac Toolbench runs and iPhone runner results share one store.
    from scripts.toolbench import runmeta as _rm
    edat = _rm.edat_blocks(meta["host"], engine=args.engine)
    edat["runtime"]["engine_profile"] = settings
    meta.update(edat)
    if not args.resume:
        (out / "meta.json").write_text(json.dumps(_scrubbed(meta), indent=2, default=str))
    if not go:
        return 4
    rows: list[dict] = rep.load_rows(out) if args.resume else []
    started_loaded = await client.loaded_models() if args.engine == "omlx" else []
    skipped: dict[str, list[str]] = {}            # whole models the probe blocked
    skipped_variants: dict[str, list[str]] = {}   # thinking variants it blocked
    try:
        with (out / "results.jsonl").open("a") as fh:
            for model in models:
                if args.exclusive:
                    for other in await client.loaded_models():
                        if other != model:
                            print(f"--exclusive: unloading {other}")
                            await client.unload(other)
                from scripts.toolbench import cloud, runmeta
                info = await runner.engine_info(client, model)
                minfo = _model_info(args, model, info)
                _update_meta(out, "engine", model, info)
                _update_meta(out, "model_info", model, minfo)
                args.result_keys = {**(getattr(args, "result_keys", None) or {}),
                                    model: runmeta.result_key(policy, model, info, minfo, meta["git"])}
                _update_meta(out, "result_keys", model, args.result_keys[model])
                if args.engine == "omlx":
                    await _recover(client, model)      # the probe needs it resident
                probe = None
                treatment = _treatment_for(args, model, references)
                if policy["probe"] != "skipped" or args.probe_only:
                    probe = await _probe_one(runner, client, args, model, policy)
                    _update_meta(out, "probe", model, probe)
                    # Production's own treatment (packaged) is the fidelity baseline:
                    # probed and recorded, never blocked.
                    binding_gate = treatment != "packaged" or args.engine != "omlx"
                    if probe["blocking"] and not args.allow_unqualified and binding_gate:
                        skipped[model] = probe["blocking"]
                        print(f"== {model}: SKIPPED, not qualified (pass --allow-unqualified to score it anyway)")
                        continue
                if args.probe_only:
                    for variant in variants:
                        if why := ((probe or {}).get("variant_blocking") or {}).get(variant):
                            skipped_variants.setdefault(model, []).append(f"--thinking {variant}: {why}")
                    continue
                window = probe_mod.run_window(probe, policy["context_window"])
                if cloud_run:
                    treatment = "cloud"
                if treatment == "packaged":
                    window = None                      # production's own window and lists
                if args.engine != "omlx" and treatment != "probe":
                    from service import config as service_config
                    production = service_config.model_context_window(
                        profile if treatment == "profile-as" and profile else model)
                    window = probe_mod.run_window(probe, None, production=production)
                if cloud_run:
                    binding = runner.bind_cloud_model(model, credential_name=args.cloud_keychain or "toolbench",
                                                      window=policy["context_window"])
                    plan = cloud.plan(model, runner.CLOUD_TABLE[model],
                                      sampling=pol.sampling_for(policy["sampling"], model)
                                      if policy["sampling"] in ("author", "fixed") else {},
                                      harbor_reasoning=binding["harbor_reasoning"],
                                      replayed_by_harbor=binding["harbor_replays_reasoning"])
                    _update_meta(out, "cloud_plan", model, plan)
                    args.result_keys[model] = {**args.result_keys[model], **cloud.key_fields(plan),
                                               "run_date": datetime.now().date().isoformat()}
                    _update_meta(out, "result_keys", model, args.result_keys[model])
                elif treatment == "probe":
                    binding = runner.bind_model(model, lists=(probe or {}).get("treatment") or {},
                                                context_window=window)
                elif treatment == "profile-as":
                    binding = runner.bind_model(model, profile_as=profile or None, context_window=window)
                else:
                    binding = runner.bind_model(model)
                turn_client = Client(timeout=args.timeout, target=role_target("agent"))
                turn_client.sampling = dict(policy["sampling_params"].get(model) or {})
                turn_client.max_output_tokens = policy["max_output_tokens"]
                print(f"\n== {model}: loading…  window={binding['context_window']}  "
                      f"treatment={binding['treatment']}  sampling={policy['sampling']}"
                      + (f"  profiled as {binding['profile_as']}" if binding["profile_as"] else ""))
                await _recover(turn_client, model)
                _update_meta(out, "bindings", model, binding)
                _update_meta(out, "model_records", model,
                             runmeta.model_record(args.result_keys[model], thinking=variants,
                                                  window=binding.get("context_window")))
                for variant in variants:
                    why = ((probe or {}).get("variant_blocking") or {}).get(variant)
                    if cloud_run:
                        mandatory = (((info.get("model_row") or {}).get("reasoning") or {}).get("mandatory")
                                     if isinstance((info.get("model_row") or {}).get("reasoning"), dict) else None)
                        why = cloud.variant_blocked(runner.CLOUD_TABLE[model], variant, mandatory=mandatory)
                    if why and not args.allow_unqualified:
                        skipped_variants.setdefault(model, []).append(f"--thinking {variant}: {why}")
                        print(f"-- thinking: {variant}  SKIPPED: {why}")
                        continue
                    flags = runner.set_thinking(turn_client, model, variant)
                    _update_meta(out, "thinking_flags", f"{model} +{variant}", flags)
                    print(f"-- thinking: {variant}  narration={flags['narration_after_tool_thinks']} "
                          f"direct={flags['direct_answer_thinks']}")
                    total, k = len(cases) * args.trials, 0
                    for trial in range(1, args.trials + 1):
                        for case in cases:
                            k += 1
                            if (model, variant, case.id, trial) in done:
                                continue
                            if not _swap_ok():
                                print("STOP: memory pressure (free swap < 500MB and pressure high)")
                                return 3
                            ledger = runner.CLOUD_LEDGER[0] if cloud_run else None
                            if ledger is not None and ledger.over():
                                print(f"STOP: cost budget ${ledger.budget:.2f} spent (${ledger.spent:.4f})")
                                return 7
                            turn_client.seed = seeds[trial]   # same schedule for every model
                            row = await _one(runner, score, turn_client, model, case, world, names,
                                             writes, args, run_id, trial, variant, refused)
                            if ledger is not None and ledger.refused:
                                # The budget ran out inside this turn: it is not a result.
                                print(f"STOP: cost budget ${ledger.budget:.2f} spent inside {case.id} t{trial}; "
                                      "that trial is not recorded")
                                return 7
                            row = _scrubbed(row)
                            rows.append(row)
                            fh.write(json.dumps(row, default=str) + "\n")
                            fh.flush()
                            v = row["v"]
                            mark = ("EXCLUDED engine_error" if v.get("excluded") else
                                    "PASS" if v["passed"] else "FAIL " + ",".join(v["labels"]))
                            if v.get("guard_retries"):
                                mark += f"  guard400x{v['guard_retries']}"
                            calls = [c["name"] for s in row["steps"] for c in s.get("calls", ())]
                            print(f"[{k}/{total}] {case.id:<28} t{trial} {v['seconds']:>6.1f}s "
                                  f"(model {v['model_seconds']:.1f}s, {v['steps']} steps)  {mark}  {calls}")
    finally:
        if cloud_run and runner.CLOUD_LEDGER[0] is not None:
            _update_meta(out, "cloud", "cost", runner.CLOUD_LEDGER[0].to_dict())
        _update_meta(out, "skipped", "models", skipped)
        _update_meta(out, "skipped", "variants", skipped_variants)
        if args.exclusive:
            for m in started_loaded:
                if m not in await client.loaded_models():
                    print(f"--exclusive: restoring {m}")
                    try:
                        await client.make_resident(m)
                    except Exception as e:  # noqa: BLE001
                        print(f"  could not restore {m}: {e}")
    for m, why in skipped.items():
        print(f"SKIPPED {m}: " + " | ".join(why))
    for m, why in skipped_variants.items():
        print(f"SKIPPED variant of {m}: " + " | ".join(why))
    if args.probe_only:
        print(f"\nprobe results: {out / 'meta.json'}")
        return 4 if (skipped or skipped_variants) else 0
    print("\n" + rep.render(rows, latency_budget=args.latency_budget, references=references))
    if cloud_run and runner.CLOUD_LEDGER[0] is not None:
        led = runner.CLOUD_LEDGER[0]
        print(f"\ncloud cost: ${led.spent:.4f} of ${led.budget:.2f} over {led.requests} requests"
              + (f" ({led.estimated} priced from the list: no usage.cost)" if led.estimated else ""))
    print(f"\nresults: {out}")
    # Non-zero whenever a requested model or variant was not scored: the
    # comparison is then not the one asked for.
    return 4 if (skipped or skipped_variants) else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=DEFAULT_MODEL,
                    help="comma-separated oMLX model ids (default: the packaged agent model)")
    ap.add_argument("--profile-as", default="",
                    help="give every other --models entry exactly the per-model treatment production "
                         "gives this rostered model id (models.yaml lists, context window)")
    ap.add_argument("--engine", default="omlx", choices=["omlx", "openai", "openrouter"],
                    help="omlx: Harbor's managed oMLX (default). openai: any OpenAI-compatible server on "
                         "127.0.0.1:--port (llama.cpp, Ollama, LM Studio, mlx_lm.server); never loads or unloads. "
                         "openrouter: tinyagent-bench cloud references (scripts/toolbench/cloud.py), a separate kind of "
                         "result; costs money; needs --cost-budget and a key")
    ap.add_argument("--cloud-keychain", default="",
                    help="--engine openrouter: the name of the Keychain item Harbor saved the OpenRouter key under "
                         f"(default: the {cloud.KEY_ENV} environment variable)")
    ap.add_argument("--cost-budget", type=float, default=0.0,
                    help="--engine openrouter: USD; the next request is refused and the run stops once spent "
                         "(required for a live cloud run)")
    ap.add_argument("--cost-from", default="",
                    help="--engine openrouter --dry-run: a finished local run whose recorded prompt sizes "
                         "price the estimate (default: rough per-step sizes)")
    ap.add_argument("--allow-heldout-to-cloud", action="store_true",
                    help="--engine openrouter: also send held-out (gate, voice) cases; by default only the "
                         "public dev split leaves the Mac")
    ap.add_argument("--cloud", default="",
                    help="with --report: cloud_reference run directory(s), comma-separated, shown as their own "
                         "table after the local scorecard (never ranked or pooled with it)")
    ap.add_argument("--port", "--omlx-port", dest="omlx_port", type=int, default=0,
                    help="engine port on 127.0.0.1 (oMLX default 8000; required for --engine openai); the only "
                         "port the network guard allows")
    ap.add_argument("--sampling", default="",
                    help="author | engine-default | fixed | file:<json map model -> params> (default: author, i.e. "
                         "scripts/toolbench/author_sampling.json, for any comparison; engine-default for one oMLX "
                         "model). fixed (0.6/0.95) is the labelled sensitivity check")
    ap.add_argument("--treatment", default="", choices=["", "packaged", "profile-as", "probe"],
                    help="per-model lists and window: packaged (models.yaml), profile-as (--profile-as's), probe "
                         "(each model's own measured support; default for comparisons and non-oMLX engines)")
    ap.add_argument("--context-window", type=int, default=0,
                    help=f"window policy: every model runs at min(this, its verified window) (default "
                         f"{pol.DEFAULT_WINDOW} under --treatment probe; production's own otherwise)")
    ap.add_argument("--max-output-tokens", type=int, default=0,
                    help="cap every step's max_tokens (default: none, as production)")
    ap.add_argument("--latency-budget", type=float, default=pol.DEFAULT_LATENCY_BUDGET,
                    help="seconds; reported as 'pass <= budget' beside the untimed pass rate")
    ap.add_argument("--references", default="",
                    help="comma-separated reference models run first in this same run; every other model is "
                         "reported as a paired difference from each")
    ap.add_argument("--with-references", action="store_true",
                    help=f"run tinyagent-bench's two references first: {pol.PRIMARY_REFERENCE} (primary baseline) and "
                         f"{pol.BASE_REFERENCE} (untuned base)")
    ap.add_argument("--reference-treatment", default="packaged", choices=["packaged", "profile-as", "probe"],
                    help="treatment for reference models (default packaged: production fidelity)")
    ap.add_argument("--baseline", default="",
                    help="with --report: pinned baseline run directory(s), comma-separated (up to two); every "
                         "model in the report is shown as a paired difference from each run's reference models")
    ap.add_argument("--probe-only", action="store_true", help="qualify each model, record it, run no case")
    ap.add_argument("--probe", action="store_true",
                    help="probe even a plain single-model oMLX run (recorded; packaged models are never blocked)")
    ap.add_argument("--skip-probe", action="store_true",
                    help="do not qualify models first (unfair runs are then not stopped; recorded)")
    ap.add_argument("--allow-unqualified", action="store_true",
                    help="score a model (or thinking variant) the probe found unfair to run; recorded")
    ap.add_argument("--model-info", default="",
                    help="JSON {model: {params_b, active_params_b, quantization, format, source, license, ...}}")
    ap.add_argument("--model-path", default="",
                    help="id=/path[,id2=/path2]: a local MLX/HF model dir or GGUF file to read config/header from")
    ap.add_argument("--allow-policy-mismatch", action="store_true",
                    help="with --compare: compare runs whose hard policy keys differ (sampling, timeout, ...)")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--only", default="", help="categories (case file stems), comma-separated")
    ap.add_argument("--ids", default="", help="case ids, comma-separated")
    ap.add_argument("--timeout", type=float, default=pol.DEFAULT_TIMEOUT,
                    help="hard per-turn seconds (a hung-engine bound; see --latency-budget for responsiveness)")
    ap.add_argument("--max-steps", type=int, default=8, help="agent loop step ceiling (production: 8)")
    ap.add_argument("--exclusive", action="store_true",
                    help="unload every other model while a model runs; restore them afterwards "
                         "(the default when more than one model runs)")
    ap.add_argument("--shared", action="store_true",
                    help="with several --models: do not unload the others (unfair: they contend)")
    ap.add_argument("--allow-engine-mismatch", action="store_true",
                    help="run even when a candidate's oMLX settings differ from --profile-as's on "
                         "sampling, thinking, window or MTP (recorded in meta.json either way)")
    ap.add_argument("--allow-harbor-running", action="store_true",
                    help=f"run even while something listens on {HARBOR_PORTS} (the live Harbor)")
    ap.add_argument("--seed-base", type=int, default=1000,
                    help="trial t sends seed seed_base+t, the same for every model")
    ap.add_argument("--out", default="", help="results directory (default ~/.moe/evals/toolbench/<run>)")
    ap.add_argument("--split", default="", choices=["", "gate", "dev", "voice"], help="only this split")
    ap.add_argument("--world-seed", type=int, default=None,
                    help="variant world (default: the canonical gate world)")
    ap.add_argument("--thinking", default="default",
                    help="comma-separated variants: default, narration-on, narration-off, off "
                         "(see runner.set_thinking)")
    ap.add_argument("--export-shingles", default="",
                    help="write hashed n-grams of every held-out (gate, voice) prompt (canonical + 50 "
                         "seeded worlds) and exit")
    ap.add_argument("--resume", default="", help="continue an interrupted run directory in place "
                    "(same --models/--thinking/--trials/--split); finished trials are skipped")
    ap.add_argument("--list", action="store_true", help="list cases and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="offline: bind every case and check tool names and host preparation; no model, "
                         "no routing test")
    ap.add_argument("--report", default="", help="re-render a finished run directory")
    ap.add_argument("--compare", default="", help="with --report: baseline run directory")
    ap.add_argument("--stored", action="store_true",
                    help="with --report: show the verdicts stored when the run finished instead of "
                         "rescoring its trajectories with the current scorer and cases")
    args = ap.parse_args()

    if args.report:
        cur_dir = Path(args.report).expanduser()
        cur_meta = _meta(cur_dir)
        cloud_dirs = [Path(c.strip()).expanduser() for c in args.cloud.split(",") if c.strip()]
        for other in [*filter(None, [args.compare]), *[b for b in args.baseline.split(",") if b.strip()]]:
            # No override: a cloud run is never paired with a local one, and runs on
            # different case sets saw different prompts and checks.
            if never := pol.never_paired(_meta(Path(other.strip()).expanduser()), cur_meta):
                print(f"REFUSING to compare or pair {other} with {cur_dir.name} ({'; '.join(never)}). "
                      "Cloud references are never compared or paired with local models (use --cloud to show "
                      "them apart), and runs on different case-set versions are never paired "
                      "(see scripts/toolbench/cases/CHANGELOG.md); --allow-policy-mismatch does not apply.")
                return 6
        if args.compare:
            base_dir = Path(args.compare).expanduser()
            hard, soft = pol.conflicts(_meta(base_dir), cur_meta)
            for line in soft:
                print(f"policy note: {line}")
            if hard:
                print(("--allow-policy-mismatch: comparing although " if args.allow_policy_mismatch
                       else "REFUSING to compare: ") + "the two runs were made under different policies:")
                for line in hard:
                    print(f"  {line}")
                if not args.allow_policy_mismatch:
                    return 6
        baselines = [Path(b.strip()).expanduser() for b in args.baseline.split(",") if b.strip()]
        if len(baselines) > 2:
            ap.error("--baseline takes at most two runs (the tuned and the base reference)")
        for b in baselines:
            hard, soft = pol.conflicts(_meta(b), cur_meta)
            for line in soft:
                print(f"baseline {b.name}: policy note: {line}")
            if hard:
                print(f"baseline {b.name}: " + ("--allow-policy-mismatch: pairing although " if
                                                args.allow_policy_mismatch else "REFUSING to pair: ")
                      + "it was made under a different policy:")
                for line in hard:
                    print(f"  {line}")
                if not args.allow_policy_mismatch:
                    return 6
        cur = _report(cur_dir, stored=args.stored)
        print(rep.render(cur, latency_budget=pol.of_run(cur_meta)["latency_budget_s"],
                         references=cur_meta.get("references") or ()))
        for b in baselines:
            bmeta = _meta(b)
            print("\n" + rep.versus(_report(b, stored=args.stored), cur, bmeta.get("references") or (),
                                    title=f"pinned baseline run {b.name}, world date {bmeta.get('today')}"))
        if args.compare:
            print("\n" + rep.compare(_report(base_dir, stored=args.stored), cur))
            if note := _sessions_note(base_dir, cur_dir):
                print("\n" + note)
        for c in cloud_dirs:
            cmeta = _meta(c)
            if pol.of_run(cmeta).get("kind") != cloud.KIND:
                print(f"REFUSING --cloud {c}: not a cloud_reference run")
                return 6
            print("\n" + rep.render(_report(c, stored=args.stored),
                                    latency_budget=pol.of_run(cmeta)["latency_budget_s"]))
        return 0
    cases = select(load_cases(), only=args.only, ids=args.ids, split=args.split)
    if not cases:
        print("no cases selected")
        return 2
    if args.export_shingles:
        from scripts.toolbench.cases import heldout_shingles
        from scripts.toolbench.world import make_facts
        worlds = [make_facts(None)] + [make_facts(i) for i in range(50)]
        data = heldout_shingles(cases, worlds)
        Path(args.export_shingles).expanduser().write_text(json.dumps(data, separators=(",", ":")) + "\n")
        print(f"wrote {data['n_prompts']} hashed gate prompts to {args.export_shingles}")
        return 0
    if args.list:
        for c in cases:
            want = "no tool" if c.no_tool else " > ".join("/".join(s.tools) for s in c.expect) or "-"
            print(f"{c.split:<5} {c.category:<10} {c.id:<28} {want:<40} {c.prompt[:60]}")
        print(f"\n{len(cases)} cases")
        return 0
    if args.trials < 1:
        ap.error("--trials must be >= 1")

    if args.engine == "openrouter":
        try:
            ctable = cloud.load_models()
            for m in [m.strip() for m in args.models.split(",") if m.strip()]:
                cloud.entry_for(m, ctable)
        except ValueError as e:
            ap.error(str(e))
        if args.models == DEFAULT_MODEL:
            ap.error("--engine openrouter needs --models (exact OpenRouter ids from scripts/toolbench/cloud_models.json)")
        if args.omlx_port:
            ap.error("--engine openrouter takes no --port: its one origin is https://openrouter.ai")
        if args.references or args.with_references:
            ap.error("cloud references never run beside local references; run the local references "
                     "in their own run and show this one with --report <local run> --cloud <this run>")
        if bad := [v for v in args.thinking.split(",") if v.strip() and v.strip() not in cloud.VARIANTS]:
            ap.error(f"--engine openrouter runs only --thinking {','.join(cloud.VARIANTS)} (got {bad})")
        held = [c.id for c in cases if c.split not in cloud.CLOUD_SPLITS]
        if held and not args.allow_heldout_to_cloud:
            if args.split or args.ids:
                ap.error(f"{len(held)} selected case(s) are held out ({', '.join(held[:5])}…); held-out prompts "
                         "stay on the Mac unless --allow-heldout-to-cloud")
            cases = [c for c in cases if c.split in cloud.CLOUD_SPLITS]
            print(f"cloud reference: only the public {'/'.join(cloud.CLOUD_SPLITS)} split leaves the Mac "
                  f"({len(cases)} cases; {len(held)} held-out cases kept back)")
            if not cases:
                print("no cases selected")
                return 2
        if not args.dry_run and not (args.cost_budget > 0):
            ap.error("--engine openrouter needs --cost-budget <USD> for a live run (real requests cost money)")
    if args.engine == "openai" and not args.omlx_port:
        ap.error("--engine openai needs --port (the OpenAI-compatible server on 127.0.0.1)")
    if args.omlx_port in HARBOR_PORTS:
        ap.error(f"port {args.omlx_port} is Harbor's own (backend or bundled engine), never an engine under test")
    if args.probe_only and args.skip_probe:
        ap.error("--probe-only and --skip-probe contradict each other")
    if not args.dry_run and not args.allow_harbor_running:
        if busy := [p for p in HARBOR_PORTS if _listening(p)]:
            print(f"refusing to start: something listens on {busy} (the live Harbor or its engine). "
                  "Quit Harbor, or pass --allow-harbor-running.")
            return 5
    if not args.dry_run:
        # Before isolate(): it refuses the system probes (sysctl, pmset, git) afterwards.
        from scripts.toolbench import runmeta
        args.host_meta = runmeta.host()
    from scripts.toolbench import runner
    if args.engine == "openrouter":
        runner.CLOUD_TABLE.update(cloud.load_models())
        if not args.dry_run:
            try:            # before isolate(): the Keychain read is a subprocess
                key, args.cloud_key_source = cloud.resolve_key(keychain=args.cloud_keychain)
            except cloud.KeyUnavailable as e:
                print(f"cloud reference: {e}")
                return 2
            runner.CLOUD_KEY[:] = [key]
            runner.CLOUD_LEDGER[0] = cloud.Ledger(args.cost_budget, runner.CLOUD_TABLE)
    runner.isolate(Path(tempfile.mkdtemp(prefix="toolbench-")),
                   omlx_port=args.omlx_port or runner.DEFAULT_OMLX_PORT, engine=args.engine)
    args.omlx_port = args.omlx_port or runner.DEFAULT_OMLX_PORT
    if args.dry_run:
        return _dry_run(args, cases)
    return asyncio.run(_run(args, cases))


if __name__ == "__main__":
    raise SystemExit(main())
