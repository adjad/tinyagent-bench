"""``python -m edat.perf``: build packs, rehearse, run, aggregate and import (spec 04).

Subcommands:

    pack           build a prompt pack (edat.perf.pack)
    plan           print what a run would do and a rough duration; contacts nothing
    check          read-only pre-flight: engine reachable, only the benchmark instance's
                   ports, wired limit, power source, thermal state, admin helper status
    run            run a plan against the benchmark oMLX instance and write raw samples
    aggregate      raw samples -> cells and verdicts (edat.perf.aggregate)
    import-iphone  bring an iPhone export into the same store (edat.perf.iphone_import)

``run`` never starts, stops or reconfigures oMLX, never changes system settings itself
(only the admin helper does, when the user has started it), and refuses the ports in
``FORBIDDEN_PORTS``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import plans as plans_mod
from .constants import LEVELS, RUNNER_VERSION


def _entries(args):
    from .manifest import load_manifest
    entries = load_manifest(args.manifest, hash_files=getattr(args, "hash_files", False))
    if getattr(args, "models", None):
        keep = set(args.models.split(","))
        entries = [e for e in entries if e.entry_id in keep or e.id in keep or e.role in ("baseline", "reference")]
    return entries


def cmd_plan(args) -> int:
    entries = _entries(args)
    steps = plans_mod.build(args.plan, entries, levels=tuple(args.levels), bracket=not args.no_bracket)
    for s in steps:
        print(s.label())
    est = plans_mod.estimate_s(steps, gap_s=args.gap_s)
    print(f"\n{len(steps)} steps; rough duration {est / 3600:.1f} h at assumed speeds "
          f"(prefill {plans_mod.ASSUMED_PREFILL_TPS:.0f} tok/s, decode {plans_mod.ASSUMED_DECODE_TPS:.0f} tok/s), "
          "cooldown waits not included.")
    return 0


def cmd_check(args) -> int:
    from .admin_client import AdminClient, AdminUnavailable, check_wired_limit
    from .client import OmlxPerfClient, check_base_url
    from .clock import now_ns
    from .system import SystemProbe
    ok = True
    host, port = check_base_url(args.engine)
    print(f"engine: {host}:{port} (benchmark instance)")
    c = OmlxPerfClient.connect(args.engine, now_ns=now_ns)
    try:
        print("loaded models:", c.loaded_models())
        print("engine activity:", c.activity())
    except Exception as e:  # noqa: BLE001
        print("engine NOT reachable:", e)
        ok = False
    p = SystemProbe()
    print("device:", json.dumps(p.device()))
    print("conditions:", json.dumps(p.conditions(display=args.display, attest={}, ambient_C=None,
                                                 now_s=time.time())))
    if args.recorded_wired_limit_mb is not None:
        warn = check_wired_limit(args.recorded_wired_limit_mb)
        if warn:
            print("WARNING:", warn)
            ok = False
    if args.admin_socket:
        try:
            a = AdminClient(args.admin_socket).connect()
            print("admin helper:", a.status())
            a.close()
        except AdminUnavailable as e:
            print("admin helper not available:", e)
    return 0 if ok else 1


def build_real_rig(args, run_dir_writer):
    """Wire the real instruments. Nothing here runs at import time."""
    from .admin_client import AdminClient, AdminUnavailable, NullAdminClient
    from .client import OmlxPerfClient
    from .clock import SystemClock
    from .memory import FootprintMonitor, RusageProbe, SystemMemory, find_engine_pid
    from .runner import Instruments, PowerControl
    from .system import SystemProbe
    clock = SystemClock()
    pid = args.engine_pid or find_engine_pid()
    if not pid:
        raise SystemExit("could not find the benchmark engine's pid; pass --engine-pid")
    probe = RusageProbe(pid)
    mon = FootprintMonitor(probe, clock.now_ns, run_dir_writer.mem_log)
    run_dir_writer.attach(Path(run_dir_writer.mem_log).name, "footprint_log")
    sysp = SystemProbe()
    power = None
    sampler = None
    if args.power:
        from .power import PowerSampler, SmcPstrReader, TraceWriter
        name = Path(run_dir_writer.power_log("smc_pstr")).name
        writer = TraceWriter(run_dir_writer.power_log("smc_pstr"), source="smc_pstr")
        sampler = PowerSampler(SmcPstrReader().open().read_watts, writer, now_ns=clock.now_ns)
        power = PowerControl(source="smc_pstr", trace_name=name, set_rate=sampler.set_rate)
        run_dir_writer.attach(name, "power_trace")
    admin = NullAdminClient()
    if args.admin_socket:
        try:
            admin = AdminClient(args.admin_socket).connect()
        except AdminUnavailable as e:
            print("admin helper not available:", e, file=sys.stderr)
    client = OmlxPerfClient.connect(args.engine, now_ns=clock.now_ns, prompt_api=args.prompt_api)
    inst = Instruments(clock=clock, client=client, monitor=mon, sysmem=SystemMemory(), thermal=sysp.thermal,
                       probe_alive=probe.alive, engine_pid=pid, power=power, admin=admin,
                       disk_read_mb=sysp.disk_read_mb)
    return inst, sampler, sysp


def execute(step, runner, *, args=None) -> None:
    """Run one plan step on a MacRunner."""
    w = step.what
    e, m = step.entry, step.mode
    if w == "levels":
        runner.levels(e, m, step.args.get("levels", LEVELS), reps=step.args.get("reps"))
    elif w == "first_token":
        runner.first_token(e, m)
    elif w == "agent_replay":
        runner.agent_replay(e, m, cache=step.args["cache"])
    elif w == "energy_pairs":
        runner.energy_pairs(e, m, step.args["level"])
    elif w == "load":
        runner.load(e, m)
    elif w == "fit16":
        runner.fit16(e, m)
    elif w == "fit16_under_load":
        from .app_load import AppLoad
        from .constants import BALLOON_UNDER_LOAD_GIB
        runner.fit16(e, m, balloon_gib=BALLOON_UNDER_LOAD_GIB, test="fit16_under_load",
                     app_load_gib=step.args["app_load_gib"], app_load=AppLoad(runner.w.dir))
    elif w == "pressure_sweep":
        from .app_load import AppLoad
        from .constants import BALLOON_UNDER_LOAD_GIB, PRESSURE_SWEEP_GIB, SMALL_LEVEL, WORKING_LEVEL
        for gib in PRESSURE_SWEEP_GIB:
            runner.fit16(e, m, balloon_gib=BALLOON_UNDER_LOAD_GIB, test="pressure_sweep", app_load_gib=gib,
                         app_load=AppLoad(runner.w.dir), levels=(SMALL_LEVEL, WORKING_LEVEL))
    else:
        from . import sustained_steps
        sustained_steps.execute(w, runner, e, m, args)


def cmd_run(args) -> int:
    from .clock import utc_now
    from .manifest import PackPrompts
    from .record import RunWriter, new_run_id
    from .runner import MacRunner, RunConfig
    entries = _entries(args)
    steps = plans_mod.build(args.plan, entries, levels=tuple(args.levels), bracket=not args.no_bracket)
    needs_power = any(s.what in ("energy_pairs", "s2", "s3", "sim_day", "reload") for s in steps)
    if needs_power and not args.power:
        raise SystemExit(f"plan {args.plan} needs the power sampler: add --power")
    started = utc_now()
    run_id = new_run_id(started)
    w = RunWriter(args.out, run_id, started_utc=started)
    w.header["commit"] = args.commit
    inst, sampler, sysp = build_real_rig(args, w)
    used = {s.entry.entry_id for s in steps}
    prompts = {e.entry_id: PackPrompts.load(e.pack_path) for e in entries if e.entry_id in used}
    cfg = RunConfig(gap_s=args.gap_s, seed=args.seed, wait_engine_s=args.wait_engine_s)
    runner = MacRunner(w, inst, [e for e in entries if e.entry_id in used], prompts, cfg)
    attest = {k: True for k in (args.attest or [])}
    cond0 = sysp.conditions(display=args.display, attest=attest, ambient_C=args.ambient_c, now_s=time.time())
    inst.monitor.start()
    if sampler:
        sampler.start()
    print(f"run {run_id}: {len(steps)} steps -> {w.dir}")
    try:
        for i, s in enumerate(steps, 1):
            print(f"[{i}/{len(steps)}] {s.label()}", flush=True)
            execute(s, runner, args=args)
    except KeyboardInterrupt:
        w.event(inst.clock.now_ns(), "operator_note", "interrupted by the operator")
    finally:
        runner.unload_current()
        inst.monitor.stop()
        if sampler:
            sampler.stop()
        cond1 = sysp.conditions(display=args.display, attest=attest, ambient_C=args.ambient_c, now_s=time.time(),
                                extra=runner.conditions_extra or None)
        device = sysp.device()
        runtime = {"name": "omlx", "version": args.engine_version, "commit": None, "packages": {},
                   "backend": "metal", "mlx_cache_limit_bytes": None, "prefill_step_tokens": None,
                   "kv_dtype": None, "prefix_cache": "defeated-by-nonce", "ssd_kv_cache": args.ssd_kv_cache,
                   "expert_offload": False, "gpu_power_cap_W": None,
                   "memory_guard_ceiling_gb": args.memory_guard_ceiling_gb, "engine_profile": {}}
        if args.admin_socket:
            import shutil
            audit = Path(args.admin_socket).parent / "admin-audit.jsonl"
            if audit.exists():
                shutil.copy(audit, w.dir / "admin-audit.jsonl")
                w.attach("admin-audit.jsonl", "admin_audit")
            for pm in Path(args.admin_socket).parent.glob("powermetrics-*.plist"):
                shutil.copy(pm, w.dir / pm.name)
                w.attach(pm.name, "powermetrics")
        packs = {eid: p.info() for eid, p in prompts.items()}
        path = w.finalize(ended_utc=utc_now(), device=device, runtime=runtime, models=runner.finish_models(),
                          prompt_pack=packs, conditions_start=cond0, conditions_end=cond1)
        print("wrote", path)
    return 0


PASSTHROUGH = {"aggregate": "edat.perf.aggregate", "import-iphone": "edat.perf.iphone_import",
               "pack": "edat.perf.pack"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in PASSTHROUGH:
        import importlib
        return importlib.import_module(PASSTHROUGH[argv[0]]).main(argv[1:])
    ap = argparse.ArgumentParser(prog="python -m edat.perf", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--manifest", required=True, help="model manifest JSON (edat/perf/manifest.py)")
        p.add_argument("--models", help="comma-separated entry ids to run (references are always kept)")
        p.add_argument("--plan", choices=plans_mod.PLANS, default="smoke")
        p.add_argument("--levels", type=int, nargs="+", default=list(LEVELS))
        p.add_argument("--no-bracket", action="store_true", help="skip the reference bracket (rehearsal only)")
        p.add_argument("--gap-s", type=float, default=5.0)

    p = sub.add_parser("plan", help="print the plan and a rough duration; contacts nothing")
    common(p)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("check", help="read-only pre-flight")
    p.add_argument("--engine", required=True, help="http://127.0.0.1:<benchmark port>")
    p.add_argument("--display", choices=("on", "off"), default="on")
    p.add_argument("--admin-socket")
    p.add_argument("--recorded-wired-limit-mb", type=int)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("run", help="run a plan and write raw samples")
    common(p)
    p.add_argument("--engine", required=True, help="http://127.0.0.1:<benchmark port>")
    p.add_argument("--engine-pid", type=int)
    p.add_argument("--engine-version")
    p.add_argument("--out", default="results/perf")
    p.add_argument("--prompt-api", choices=("completions", "chat"), default="completions")
    p.add_argument("--power", action="store_true", help="sample SMC PSTR at 20 Hz (energy plans)")
    p.add_argument("--admin-socket", help="<run-dir>/admin.sock of the running admin helper")
    p.add_argument("--display", choices=("on", "off"), default="on")
    p.add_argument("--attest", nargs="*", choices=("other_apps_closed", "hard_surface", "airplane_mode"))
    p.add_argument("--ambient-c", type=float)
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--wait-engine-s", type=float, default=0.0)
    p.add_argument("--ssd-kv-cache", action="store_true", help="say the engine's SSD KV cache is on")
    p.add_argument("--memory-guard-ceiling-gb", type=float)
    p.add_argument("--commit", default=os.environ.get("EDAT_COMMIT"))
    p.add_argument("--hash-files", action="store_true", help="hash each model_dir's files and check pins")
    p.add_argument("--sim-day-script", help="edat-sim-day-v1.json from the agentic build")
    p.add_argument("--sim-day-mode", choices=("compressed", "realtime"), default="compressed")
    p.add_argument("--duty-cycle", help="edat-duty-cycle-v1.json for S2's bursts")
    p.add_argument("--reps", type=int, help="repetitions for sim-day and long-session (default 3; realtime 1)")
    p.add_argument("--long-session-script")
    p.add_argument("--tokenizer-dir", help="model folder whose tokenizer the long session counts with")
    p.set_defaults(fn=cmd_run)

    for name, help_ in (("aggregate", "raw samples -> cells and verdicts (edat.perf.aggregate --help)"),
                        ("import-iphone", "import an iPhone export (edat.perf.iphone_import --help)"),
                        ("pack", "build a prompt pack (edat.perf.pack --help)")):
        sub.add_parser(name, help=help_)
    ap.add_argument("--version", action="version", version=RUNNER_VERSION)
    args = ap.parse_args(argv)
    return args.fn(args)
