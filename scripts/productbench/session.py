"""A guarded live run: the lock, one model resident, and the engine put back on every exit path.

    python -m scripts.productbench.session --plan plan.json

plan.json (paths absolute; `out` outside the repository):

    {"out": "<results root>", "lock": "<the shared heavy-job lock directory>",
     "engine_port": 8000, "start_engine": true,
     "omlx_cli": "/Applications/oMLX.app/Contents/MacOS/omlx-cli",
     "steps": [{"engine_model": "<engine id>", "out": "<sub folder, optional>",
                "runner": ["--model", "<entry>", "--profile", "student", "--surface", "ask,brief,day,pref"]}]}

What it guarantees, in order:

1. It starts nothing while Harbor runs or a port it needs is taken, and refuses an `out` or
   `$TMPDIR` inside the repository.
2. It takes the lock with mkdir and stops if the lock exists; it removes the lock only because
   it took it.
3. SIGTERM, SIGINT and SIGHUP (unless ignored, as under nohup) unwind through the same `finally`
   as any other exit: the running step gets SIGTERM (its own handler stops its servers), the
   engine's loaded models are put back, oMLX is stopped again if it was found stopped, and the
   lock is removed. Signals are ignored while that clean-up runs.
4. The engine's state is saved once, to a file named for this session's token, and `keep` and
   `restore` refuse any file without that token (engine_prep.py): a state an earlier run left is
   never restored.
5. One engine port for everything: `engine_prep` gets `--engine-port`, and so does each step's
   runner (a step may not set its own).
6. A step that stops (its run folder's meta says `stopped`, or the runner exits non-zero) ends the
   session as a failure; later steps do not start. Harbor opening between steps also stops it.

It never changes an oMLX setting. A record of what happened is written to
`<out>/session-<token>.json`; the exit code is 0 only when every step finished and the engine
was restored.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.productbench import engine_prep  # noqa: E402

RUNNER = ROOT / "scripts" / "run_productbench.py"
REAL_PORTS = (8765, 8791)
RUN_PORTS = (8775, 8766)
OMLX_CLI = "/Applications/oMLX.app/Contents/MacOS/omlx-cli"
HARBOR_PROCS = ("Harbor.app/Contents/MacOS", "Wisp.app/Contents/MacOS")


class Terminated(BaseException):
    def __init__(self, signum: int):
        super().__init__(f"terminated by signal {signal.Signals(signum).name}")
        self.signum = signum


class SessionError(RuntimeError):
    pass


# ------------------------------------------------------------------ host checks (patched in tests)
def harbor_running() -> bool:
    for pat in HARBOR_PROCS:
        if subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0:
            return True
    return False


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def inside_repo(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        Path(path).expanduser().resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def engine_cli(cli: str, verb: str) -> int:
    return subprocess.run([cli, verb], capture_output=True, text=True, timeout=120).returncode


# ------------------------------------------------------------------ the plan
def check_plan(plan: dict) -> list[str]:
    """Problems with a plan, before anything is touched."""
    errs = []
    for k in ("out", "lock", "steps"):
        if not plan.get(k):
            errs.append(f"plan needs {k!r}")
    if inside_repo(plan.get("out")):
        errs.append(f"out {plan.get('out')} is inside the repository")
    if inside_repo(os.environ.get("TMPDIR")):
        errs.append(f"$TMPDIR {os.environ.get('TMPDIR')} is inside the repository")
    for i, st in enumerate(plan.get("steps") or []):
        if not st.get("engine_model"):
            errs.append(f"step {i + 1} needs engine_model")
        args = st.get("runner") or []
        for own in ("--out", "--engine-port"):
            if own in args:
                errs.append(f"step {i + 1} sets {own}; the session sets it")
        if inside_repo(st.get("out")):
            errs.append(f"step {i + 1} out is inside the repository")
    return errs


def blocked(ports=RUN_PORTS) -> str | None:
    if harbor_running():
        return "Harbor is running"
    for p in (*REAL_PORTS, *ports):
        if port_busy(p):
            return f"port {p} is in use"
    return None


def newest_run(out: Path) -> Path | None:
    runs = sorted(out.glob("edat-iae-*"), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def step_stopped(out: Path, before: set) -> str | None:
    run = newest_run(out)
    if run is None or run.name in before:
        return "the step wrote no run folder"
    try:
        return json.loads((run / "meta.json").read_text()).get("stopped")
    except (OSError, ValueError):
        return "the run folder has no readable meta.json"


# ------------------------------------------------------------------ the session
def run(plan: dict, *, python: str = sys.executable, wait_s: float = 120.0) -> dict:
    errs = check_plan(plan)
    if errs:
        raise SessionError("; ".join(errs))
    why = blocked()
    if why:
        raise SessionError(f"{why}; nothing was started")
    out, lock = Path(plan["out"]), Path(plan["lock"])
    port = int(plan.get("engine_port") or engine_prep.PORT)
    cli = plan.get("omlx_cli") or OMLX_CLI
    token = uuid.uuid4().hex[:16]
    log: dict = {"token": token, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "engine_port": port,
                 "steps": [], "result": None}
    save = out / f"engine-before-{token}.json"
    started_engine = created = False
    child: subprocess.Popen | None = None
    old = {}

    def handler(signum, frame):
        raise Terminated(signum)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        cur = signal.getsignal(sig)
        if sig == signal.SIGHUP and cur == signal.SIG_IGN:
            continue
        old[sig] = cur
        signal.signal(sig, handler)
    try:
        try:
            os.mkdir(lock)
        except FileExistsError:
            raise SessionError(f"{lock} exists: another heavy job is running; nothing was started") from None
        created = True
        (lock / "owner.json").write_text(json.dumps({"token": token, "pid": os.getpid()}))
        out.mkdir(parents=True, exist_ok=True)
        if not engine_prep_listening(port):
            if not plan.get("start_engine"):
                raise SessionError(f"no engine on :{port} and the plan does not start one")
            engine_cli(cli, "restart")
            started_engine = True
            deadline = time.monotonic() + wait_s
            while not engine_prep_listening(port) and time.monotonic() < deadline:
                time.sleep(2)
            if not engine_prep_listening(port):
                raise SessionError(f"the engine did not come up on :{port}")
        log["engine_found"] = "stopped" if started_engine else "running"
        for i, st in enumerate(plan["steps"]):
            row = {"step": i + 1, "engine_model": st["engine_model"], "runner": st.get("runner") or []}
            log["steps"].append(row)
            why = blocked(ports=RUN_PORTS)
            if why:
                row["result"] = f"not started: {why}"
                raise SessionError(row["result"])
            rc = engine_prep.main(["keep", st["engine_model"], "--save", str(save), "--token", token,
                                   "--engine-port", str(port)])
            if rc != 0:
                row["result"] = "not started: other models stayed loaded"
                raise SessionError(row["result"])
            sout = Path(st.get("out") or out)
            sout.mkdir(parents=True, exist_ok=True)
            before = {p.name for p in sout.glob("edat-iae-*")}
            cmd = [python, str(RUNNER), *row["runner"], "--out", str(sout), "--engine-port", str(port)]
            t0 = time.monotonic()
            child = subprocess.Popen(cmd, cwd=ROOT)
            rc = child.wait()
            child = None
            stopped = step_stopped(sout, before)
            row.update(rc=rc, elapsed_s=round(time.monotonic() - t0, 1), stopped=stopped,
                       run=(newest_run(sout) or Path("")).name)
            if rc != 0 or stopped:
                row["result"] = "stopped"
                raise SessionError(f"step {i + 1} stopped: {stopped or f'exit {rc}'}")
            row["result"] = "done"
        log["result"] = "done"
    except Terminated as exc:
        log["result"] = str(exc)
    except SessionError as exc:
        log["result"] = f"failed: {exc}"
    finally:
        for sig in old:                       # nothing interrupts the clean-up
            signal.signal(sig, signal.SIG_IGN)
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)  # its handler stops its servers and marks its run folder
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                child.kill()
        log["restore"] = _restore(save, token, port)
        if started_engine:
            log["engine_stopped"] = engine_cli(cli, "stop") == 0
        if created:                           # only the lock this session took
            try:
                (lock / "owner.json").unlink(missing_ok=True)
                lock.rmdir()
                log["lock_released"] = True
            except OSError as exc:
                log["lock_released"] = f"not removed: {exc}"
        else:
            log["lock_released"] = "not taken"
        log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"session-{token}.json").write_text(json.dumps(log, indent=1))
        except OSError:
            pass
        for sig, cur in old.items():
            signal.signal(sig, cur)
    return log


def engine_prep_listening(port: int) -> bool:
    return port_busy(port)


def _restore(save: Path, token: str, port: int) -> str:
    if not save.exists():
        return "nothing saved (no model was touched)"
    try:
        rc = engine_prep.main(["restore", str(save), "--token", token, "--engine-port", str(port)])
    except SystemExit as exc:
        return f"refused: {exc}"
    except Exception as exc:  # noqa: BLE001 - recorded, and the lock is still released
        return f"failed: {exc.__class__.__name__}: {exc}"
    return "restored" if rc == 0 else f"restored with a problem (exit {rc})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", required=True)
    args = ap.parse_args(argv)
    try:
        log = run(json.loads(Path(args.plan).read_text()))
    except SessionError as exc:
        print(f"session not started: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: log[k] for k in ("token", "result", "restore", "lock_released")}, indent=1))
    ok = log["result"] == "done" and log.get("restore") in ("restored", "nothing saved (no model was touched)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
