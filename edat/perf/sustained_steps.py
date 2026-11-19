"""The command line's sustained-use plans (spec 04 §9): one function per plan step.

``python -m edat.perf run --plan s1|s2|s3|reload|long-session|sim-day`` lands here. Each
needs ``--power`` except S1 on AC; the simulated day and the long session need their
script files (``--sim-day-script``, ``--long-session-script``) from the agentic build, and
the long session the model's tokenizer (``--tokenizer-dir``).

Benchmark-engine settings these tests need (the runner cannot set them):
- S3 and the simulated day's ``resident`` policy: the engine's idle auto-unload **off**;
- reload after idle: auto-unload **on** with Harbor's 5 minutes.
"""
from __future__ import annotations


def execute(what: str, runner, entry, mode: str, args) -> None:
    from . import sustained
    if what == "s1":
        sustained.s1_sustained(runner, entry, mode, in_window=runner.i.power is not None)
    elif what == "s2":
        sustained.s2_battery(runner, entry, mode,
                             duty_cycle=getattr(args, "duty_cycle", None) if args is not None else None)
    elif what == "s3":
        sustained.s3_idle_residency(runner, entry, mode)
    elif what == "reload":
        sustained.reload_after_idle(runner, entry, mode)
    elif what == "sim_day":
        from .simday import ReplayExecutor, load_script, run_day
        path = getattr(args, "sim_day_script", None)
        if not path:
            raise SystemExit("the simulated day needs --sim-day-script <edat-sim-day-v1.json>")
        script = load_script(path)
        mode_ = getattr(args, "sim_day_mode", None) or "compressed"
        for rep in range(1, (getattr(args, "reps", None) or (1 if mode_ == "realtime" else 3)) + 1):
            run_day(runner, entry, mode, script, ReplayExecutor(), mode_=mode_, rep=rep)
    elif what == "long_session":
        from .longsession import load_conversation, run_long_session
        from .tokenizer import HFTokenizer
        path = getattr(args, "long_session_script", None)
        tok_dir = getattr(args, "tokenizer_dir", None)
        if not path or not tok_dir:
            raise SystemExit("the long session needs --long-session-script and --tokenizer-dir")
        tok = HFTokenizer.from_model_dir(tok_dir)
        conv = load_conversation(path)
        for rep in range(1, (getattr(args, "reps", None) or 3) + 1):
            run_long_session(runner, entry, mode, conv, tok, rep=rep)
    else:
        raise ValueError(f"unknown step {what!r}")
