"""The guard every real run on this Mac goes through: lock, engine state, signals, Harbor, stops.

One module for every harness that loads models into the user's oMLX:

    from edat.runtime.run_guard import Engine, RunGuard, read_engine_key

    engine = Engine("http://127.0.0.1:8000", api_key=read_engine_key())
    with RunGuard(out, lock=LOCK, engine=engine, max_minutes=600) as g:
        for model in models:
            g.prepare(model)                 # Harbor check, unload the others, load this one
            for trial in trials:
                if g.should_stop():          # STOP file, time, faults, memory, Harbor
                    break
                ...
                g.note(status)               # "ok" | "excluded" | "not_applicable"
    sys.exit(g.exit_code)

What it guarantees:

* **Output folder.** It refuses an output folder inside the repository before
  anything is written.
* **Lock.** It takes the shared heavy-job lock by mkdir. An empty or missing
  lock path is refused. An existing lock stops the start. The lock is removed
  only by the guard that made it, on every exit.
* **Engine state saved fresh.** At each start it saves the engine's state:
  loaded, pinned and default model, with a token and a timestamp. The save
  goes to `<lock>.engine-save.json`, a host-wide path, so a leftover from any
  harness is seen. A leftover save that was never restored refuses the start
  and prints how to restore it.
* **Restore on every exit.** That covers a normal end, a stop, an exception,
  Ctrl-C, SIGTERM, SIGHUP and SIGQUIT. Those signals (and SIGINT) are ignored
  while the restore runs. A restore error is recorded and re-raised. The run
  report is written in every case.
* **Harbor fails closed.** Harbor (the app, a swift-run build or a standalone
  backend, found by process or by its ports 8765/8791) is checked at start,
  before every unload, load and restore, and every few trials. If the check
  itself fails, Harbor is assumed to be running.
* **One engine URL.** Every engine call goes to one base URL, which is checked
  as loopback before any header is built. Proxies are ignored
  (`trust_env=False`). The key goes only in that client's headers, never into
  the environment.
* **Stops.** The STOP file, the time limit, free memory under 15%, and more
  than 2% engine faults after 50 trials all stop the run. A run in which every
  model trial was an engine fault ends as a failure at any trial count.

Outcomes and exit codes:
* `finished` (0);
* `stopped` (3) by the STOP file or the time limit, which a rerun resumes;
* `failed` (1) for everything else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
LOCK = "/private/tmp/claude-501/wisp-heavy.lock"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
HELPER_WORDS = ("embed", "rerank", "bge", "e5-", "minilm", "colbert")
SAVE_SCHEMA = "edat.engine-save/1"
MEMORY_FLOOR_PCT = 15
FAULT_LIMIT = 0.02
FAULT_FROM = 50
HOST_CHECK_EVERY = 10
UNLOAD_WAIT_S = 120.0
GUARDED_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
# Harbor in any form shares oMLX with a run: the app (also by its old name),
# a `swift run` build, or the Python backend started on its own.
HARBOR_PROCESS_PATTERNS = (
    r"/MacOS/(Harbor|Wisp)( |$)",
    r"/\.build/[^ ]*/(Harbor|Wisp)( |$)",
    r"(uvicorn|-m) service(\.| |$)",
)
HARBOR_PORTS = (8765, 8791)
EXIT = {"finished": 0, "failed": 1, "stopped": 3}


class GuardError(RuntimeError):
    """The guard refused to start or to go on; nothing unsafe was done."""


class RunInterrupted(BaseException):
    """A guarded signal arrived. BaseException, so per-trial `except Exception` does not swallow it."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"signal {signal.Signals(signum).name}")
        self.signum = signum


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ the engine

def loopback_base_url(url: str) -> str:
    """`http://<loopback>:<port>` or GuardError. Checked before any header or client exists."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in LOOPBACK_HOSTS or parts.port is None \
            or parts.path not in ("", "/") or parts.username or parts.password or parts.query:
        raise GuardError(f"the engine URL must be plain http on a loopback address with a port, got {url!r}")
    return f"http://{'[::1]' if host == '::1' else host}:{parts.port}"


def port_of(base_url: str) -> int:
    return int(urlsplit(loopback_base_url(base_url)).port)


def read_engine_key(settings: Path | None = None) -> str | None:
    """oMLX's own API key from its settings (read only; never printed, logged or put in the environment)."""
    path = settings or Path.home() / ".omlx" / "settings.json"
    try:
        return (json.loads(path.read_text()).get("auth") or {}).get("api_key") or None
    except (OSError, ValueError):
        return None


def is_helper(row: dict) -> bool:
    mid = str(row.get("id", "")).lower()
    return bool(row.get("is_helper")) or any(w in mid for w in HELPER_WORDS)


class Engine:
    """Every engine call of a run, through one loopback base URL."""

    def __init__(self, base_url: str, *, api_key: str | None = None, transport=None, timeout_s: float = 600.0,
                 sleep=time.sleep, unload_wait_s: float = UNLOAD_WAIT_S) -> None:
        self.base_url = loopback_base_url(base_url)       # before any header is built
        import httpx
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=self.base_url, headers=headers, transport=transport,
                                    timeout=timeout_s, trust_env=False, follow_redirects=False)
        self._sleep, self._unload_wait_s = sleep, unload_wait_s

    def __repr__(self) -> str:
        return f"Engine({self.base_url})"

    @property
    def port(self) -> int:
        return port_of(self.base_url)

    def status(self) -> list[dict]:
        r = self._client.get("/v1/models/status")
        r.raise_for_status()
        return list(r.json().get("models") or [])

    def default_model(self) -> str | None:
        r = self._client.get("/health")               # 503 while pinned models preload; the body still says
        try:
            return r.json().get("default_model")
        except ValueError:
            return None

    def snapshot(self) -> dict:
        rows = self.status()
        return {"loaded": sorted(m["id"] for m in rows if m.get("loaded") and not is_helper(m)),
                "pinned": sorted(m["id"] for m in rows if m.get("pinned")),
                "helpers_loaded": sorted(m["id"] for m in rows if m.get("loaded") and is_helper(m)),
                "default_model": self.default_model()}

    def loaded(self) -> list[str]:
        return [m["id"] for m in self.status() if m.get("loaded") and not is_helper(m)]

    def unload(self, model_id: str) -> None:
        r = self._client.post(f"/v1/models/{model_id}/unload")
        if r.status_code == 400:                       # not loaded (any more)
            return
        r.raise_for_status()
        deadline = time.monotonic() + self._unload_wait_s
        while model_id in self.loaded():               # 202: unloads once its requests are aborted
            if time.monotonic() > deadline:
                raise GuardError(f"{model_id} did not unload within {self._unload_wait_s:g} s")
            self._sleep(1.0)

    def load(self, model_id: str) -> None:
        self._client.post(f"/v1/models/{model_id}/load").raise_for_status()

    def close(self) -> None:
        self._client.close()


# ------------------------------------------------------------------ host checks

def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=20)


def harbor_running(run=_run) -> str | None:
    """What of Harbor is running, or None. Fails closed: a check that errors counts as running."""
    for pat in HARBOR_PROCESS_PATTERNS:
        try:
            p = run(["pgrep", "-f", pat])
        except Exception as exc:  # noqa: BLE001
            return f"unknown (process check failed: {type(exc).__name__})"
        if p.returncode == 0 and p.stdout.strip():
            return f"process matching {pat!r}"
        if p.returncode != 1:
            return f"unknown (pgrep exited {p.returncode})"
    for port in HARBOR_PORTS:
        try:
            p = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        except Exception as exc:  # noqa: BLE001
            return f"unknown (port check failed: {type(exc).__name__})"
        if p.stdout.strip():
            return f"a process listening on port {port}"
        if p.returncode != 1 or p.stderr.strip():
            return f"unknown (lsof exited {p.returncode})"
    return None


def memory_free_pct(run=_run) -> int | None:
    try:
        out = run(["memory_pressure", "-Q"]).stdout
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"free percentage:\s*(\d+)%", out)
    return int(m.group(1)) if m else None


def inside_repo(path: Path, repo: Path = REPO) -> bool:
    p = Path(path).expanduser().resolve()
    return p == repo or repo in p.parents


# ------------------------------------------------------------------ the guard

class RunGuard:
    def __init__(self, out: str | Path, *, lock: str | None, engine: Engine | None, max_minutes: float | None = None,
                 save_path: str | Path | None = None, report_name: str = "run-report.json",
                 harbor=harbor_running, free_pct=memory_free_pct, repo: Path = REPO,
                 memory_floor_pct: int = MEMORY_FLOOR_PCT, host_check_every: int = HOST_CHECK_EVERY,
                 install_signals: bool = True) -> None:
        self.out = Path(out).expanduser().resolve()
        if inside_repo(self.out, repo):                 # before anything is written
            raise GuardError(f"the output folder is inside the repository: {self.out}")
        if not lock or not str(lock).strip():
            raise GuardError("a heavy-job lock path is required (an empty path would skip the lock)")
        self.lock = Path(lock)
        self.engine = engine
        self.save_path = Path(save_path) if save_path else Path(f"{self.lock}.engine-save.json")
        self.report_path = self.out / report_name
        self.max_minutes = max_minutes
        self._harbor, self._free = harbor, free_pct
        self.memory_floor_pct, self.host_check_every = memory_floor_pct, host_check_every
        self.install_signals = install_signals
        self.report: dict = {}
        self.statuses: list[str] = []
        self.trials = 0
        self.stop_reason: str | None = None
        self.stop_resumable = False
        self.outcome: str | None = None
        self._lock_made = False
        self._old_handlers: dict = {}
        self._t0 = time.monotonic()
        self._token: str | None = None

    # -------------------------------------------------- entry and exit
    def __enter__(self) -> "RunGuard":
        self.report.update(started_at=utc_now(), out=str(self.out), lock=str(self.lock))
        try:
            self._refuse_if_harbor("start")
            try:
                os.mkdir(self.lock)
            except FileExistsError:
                raise GuardError(f"{self.lock} exists: another heavy job is running; not starting") from None
            self._lock_made = True
            self._install()
            if self.engine is not None:
                self._save_fresh()
            self.out.mkdir(parents=True, exist_ok=True)
        except BaseException:
            self._release_lock()
            self._uninstall()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ignored = self._ignore_signals()
        restore_error: BaseException | None = None
        try:
            try:
                self.report["engine"] = self._restore()
            except BaseException as e:  # noqa: BLE001  (recorded below, re-raised after the report)
                restore_error = e
                self.report["engine"] = f"restore FAILED: {type(e).__name__}: {e}"[:500]
                self.report["restore_with"] = self.restore_hint()
            self.outcome = self._outcome(exc, restore_error)
            self.report.update(outcome=self.outcome, exit_code=EXIT[self.outcome], stopped=self.stop_reason,
                               trials_recorded=self.trials, excluded=self.statuses.count("excluded"),
                               finished_at=utc_now())
            if exc is not None:
                self.report["failed"] = f"{exc_type.__name__}: {exc}"[:500]
        finally:
            try:
                self._write_report()
            finally:
                self._release_lock()
                self._restore_handlers({s: h for s, h in ignored.items() if s not in self._old_handlers})
                self._uninstall()
        if restore_error is not None and exc is None:
            raise restore_error
        return False

    @property
    def exit_code(self) -> int:
        return EXIT.get(self.outcome or "failed", 1)

    # -------------------------------------------------- the run's calls
    def prepare(self, model_id: str) -> None:
        """Make `model_id` the one chat model loaded: Harbor checked before every unload and the load."""
        if self.engine is None:
            return
        for m in self.engine.loaded():
            if m != model_id:
                self._refuse_if_harbor(f"unloading {m}")
                self.engine.unload(m)
        left = [m for m in self.engine.loaded() if m != model_id]
        if left:
            raise GuardError(f"still loaded after unloading: {left}; {model_id} was not started")
        self._refuse_if_harbor(f"loading {model_id}")
        self.engine.load(model_id)
        self.report.setdefault("models_prepared", []).append(model_id)

    def note(self, status: str) -> None:
        """One recorded trial: 'ok', 'excluded' (engine fault) or 'not_applicable' (no model call)."""
        if status != "not_applicable":
            self.statuses.append(status)
        self.trials += 1

    def should_stop(self) -> str | None:
        """Call before each trial. A STOP-file or time-limit stop ends `stopped` (resumable);
        a memory, fault or Harbor stop ends `failed`."""
        why, resumable = self._stop_reason()
        if why:
            self.stop_reason, self.stop_resumable = why, resumable
        return why

    def stop(self, reason: str, *, resumable: bool = False) -> None:
        """A stop the harness decides (e.g. repeated errors); not resumable ends `failed`."""
        self.stop_reason, self.stop_resumable = reason, resumable

    def restore_hint(self) -> str:
        return (f"close Harbor, then: python -m edat.runtime.run_guard restore --save {self.save_path}"
                + (f" --url {self.engine.base_url}" if self.engine else ""))

    # -------------------------------------------------- internals
    def _stop_reason(self) -> tuple[str | None, bool]:
        if (self.out / "STOP").exists():
            return "the STOP file exists", True
        if self.max_minutes and (time.monotonic() - self._t0) / 60 >= self.max_minutes:
            return f"the {self.max_minutes:g}-minute limit was reached", True
        n, bad = len(self.statuses), self.statuses.count("excluded")
        if n >= FAULT_FROM and bad / n > FAULT_LIMIT:
            return f"{bad} of {n} model trials hit engine faults (over 2%)", False
        if self.trials % self.host_check_every == 0:
            free = self._free()
            if free is not None and free < self.memory_floor_pct:
                return f"free memory {free}% is under {self.memory_floor_pct}%", False
            h = self._harbor()
            if h:
                return f"Harbor appears to be running ({h})", False
        return None, False

    def _outcome(self, exc, restore_error) -> str:
        if exc is not None or restore_error is not None:
            return "failed"
        if self.stop_reason:
            return "stopped" if self.stop_resumable else "failed"
        if self.statuses and all(s == "excluded" for s in self.statuses):
            self.report["why"] = f"every model trial ({len(self.statuses)}) hit an engine fault"
            return "failed"
        return "finished"

    def _refuse_if_harbor(self, before: str) -> None:
        h = self._harbor()
        if h:
            raise GuardError(f"Harbor appears to be running ({h}); not {before}. Close it first.")

    def _save_fresh(self) -> None:
        if self.save_path.exists():
            try:
                old = json.loads(self.save_path.read_text())
            except (OSError, ValueError):
                old = {}
            if not old.get("restored_at"):
                raise GuardError(f"an earlier run's engine state was never restored ({self.save_path}, "
                                 f"saved {old.get('created_at', '?')}): {self.restore_hint()}; then start again")
        self._token = secrets.token_hex(8)
        state = {"schema": SAVE_SCHEMA, "token": self._token, "created_at": utc_now(), "pid": os.getpid(),
                 "base_url": self.engine.base_url, "out": str(self.out), **self.engine.snapshot(),
                 "restored_at": None}
        _write_json(self.save_path, state)
        self.report["engine_before"] = {k: state[k] for k in ("token", "created_at", "loaded", "pinned",
                                                                "default_model")}

    def _restore(self) -> str:
        if self.engine is None or self._token is None:
            return "engine untouched"
        return restore(self.engine, self.save_path, harbor=self._harbor, token=self._token)

    def _write_report(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        _write_json(self.report_path, self.report)

    def _release_lock(self) -> None:
        if self._lock_made:
            self._lock_made = False
            os.rmdir(self.lock)

    def _install(self) -> None:
        if not self.install_signals:
            return

        def handler(signum, frame):
            raise RunInterrupted(signum)
        for s in GUARDED_SIGNALS:
            self._old_handlers[s] = signal.signal(s, handler)

    def _uninstall(self) -> None:
        for s, h in self._old_handlers.items():
            signal.signal(s, h)
        self._old_handlers = {}

    def _ignore_signals(self) -> dict:
        if not self.install_signals:
            return {}
        return {s: signal.signal(s, signal.SIG_IGN) for s in (*GUARDED_SIGNALS, signal.SIGINT)}

    @staticmethod
    def _restore_handlers(saved: dict) -> None:
        for s, h in saved.items():
            signal.signal(s, h)


def restore(engine: Engine, save_path: Path, *, harbor=harbor_running, token: str | None = None) -> str:
    """Return the engine to the saved state: unload what was not loaded then, load what was. Harbor is
    checked before every call (failing closed). Marks the save restored only when the state matches."""
    saved = json.loads(Path(save_path).read_text())
    if token is not None and saved.get("token") != token:
        raise GuardError(f"{save_path} belongs to another run (token mismatch); not restoring from it")
    if loopback_base_url(saved["base_url"]) != engine.base_url:
        raise GuardError(f"{save_path} was saved from {saved['base_url']}, not {engine.base_url}")
    if saved.get("restored_at"):
        return "engine already restored"
    want = list(saved.get("loaded") or [])

    def check(before: str) -> None:
        h = harbor()
        if h:
            raise GuardError(f"Harbor appears to be running ({h}); not {before}")
    check("restoring")
    for m in engine.loaded():
        if m not in want:
            check(f"unloading {m}")
            engine.unload(m)
    for m in want:
        if m not in engine.loaded():
            check(f"loading {m}")
            engine.load(m)
    now = engine.snapshot()
    missing = [m for m in want if m not in now["loaded"]]
    extra = [m for m in now["loaded"] if m not in want]
    if missing or extra:
        raise GuardError(f"engine state differs after restore: missing {missing}, extra {extra}")
    saved["restored_at"] = utc_now()
    saved["after"] = now
    _write_json(Path(save_path), saved)
    note = "" if now.get("default_model") == saved.get("default_model") else \
        f"; default model is {now.get('default_model')!r}, was {saved.get('default_model')!r}"
    return f"engine restored ({', '.join(want) or 'nothing loaded'}){note}"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.runtime.run_guard",
                                 description="Restore or inspect the engine state a guarded run saved.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("restore", "show"):
        p = sub.add_parser(name)
        p.add_argument("--save", default=f"{LOCK}.engine-save.json")
        p.add_argument("--url", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        print(Path(a.save).read_text())
        return 0
    engine = Engine(a.url, api_key=read_engine_key())
    try:
        print(restore(engine, Path(a.save)))
    except GuardError as exc:
        print(f"not restored: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
