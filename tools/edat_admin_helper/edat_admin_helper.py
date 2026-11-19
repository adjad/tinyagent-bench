#!/usr/bin/python3
"""tinyagent-bench admin helper: the only tinyagent-bench process that runs with administrator rights.

Start it (see README.md):  sudo /usr/bin/python3 edat_admin_helper.py --run-dir <abs path> [--max-hours 6|18] [--dry-run]

It never:
  - runs any command other than the fixed ones in the verb table (no shell, fixed argv lists);
  - writes outside --run-dir;
  - opens a network socket (only one Unix socket inside --run-dir);
  - signals a process it did not start (only its own powermetrics child);
  - installs anything;
  - touches oMLX or Harbor settings.

Standard library only. Python 3.9 compatible. It does not import the edat package.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import json
import mmap
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

EDAT_16GB_WIRED_MB = 10900  # must equal edat.perf.constants.EDAT_16GB_WIRED_MB (a test checks)
INTERVALS_MS = (100, 250, 1000)
BALLOON_GIBS = (8, 10.5)
MAX_HOURS_ALLOWED = (6, 18)
SYSCTL_KEY = "iogpu.wired_limit_mb"
MAX_LINE = 65536
POLL_S = 0.2

SYNC = ["/bin/sync"]
PURGE = ["/usr/sbin/purge"]
SYSCTL_READ = ["/usr/sbin/sysctl", "-n", SYSCTL_KEY]


def powermetrics_argv(run_dir: str, interval_ms: int, n: int) -> List[str]:
    return ["/usr/bin/powermetrics", "--samplers", "cpu_power,gpu_power,ane_power,thermal",
            "-i", str(interval_ms), "-f", "plist", "-o", os.path.join(run_dir, "powermetrics-%d.plist" % n)]


def sysctl_set_argv(mb: Any) -> List[str]:
    return ["/usr/sbin/sysctl", "%s=%s" % (SYSCTL_KEY, mb)]


# verb -> {argument name: allowed values}
VERBS = {
    "purge": {}, "powermetrics_start": {"interval_ms": INTERVALS_MS}, "powermetrics_stop": {},
    "wired_limit_set": {"mb": (EDAT_16GB_WIRED_MB,)}, "wired_limit_reset": {},
    "balloon_wire": {"gib": BALLOON_GIBS}, "balloon_release": {}, "status": {}, "exit": {},
}


def plan_lines(run_dir: str = "<run-dir>") -> List[str]:
    """The full verb table with the exact argv, for --print-plan and the README."""
    j = lambda a: " ".join(a)  # noqa: E731
    out = ["purge: %s ; %s" % (j(SYNC), j(PURGE))]
    for i in INTERVALS_MS:
        out.append("powermetrics_start interval_ms=%d: %s" % (i, j(powermetrics_argv(run_dir, i, 1))))
    out.append("powermetrics_stop: SIGINT to the powermetrics process this helper started, then wait")
    out.append("wired_limit_set mb=%d: %s" % (EDAT_16GB_WIRED_MB, j(sysctl_set_argv(EDAT_16GB_WIRED_MB))))
    out.append("wired_limit_reset: %s   (value read at start with: %s)"
               % (j(sysctl_set_argv("<original>")), j(SYSCTL_READ)))
    out.append("balloon_wire gib in {8, 10.5}: in-process anonymous mmap, one byte written per page, mlock (no command)")
    out.append("balloon_release: munlock and free (no command)")
    out.append("status: report state (no command)")
    out.append("exit: restore (stop powermetrics, release balloon, reset wired limit), then exit")
    return out


# ---------------------------------------------------------------- clocks
def _load_mach() -> Optional[Callable[[], int]]:
    try:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")

        class Timebase(ctypes.Structure):
            _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

        lib.mach_continuous_time.restype = ctypes.c_uint64
        tb = Timebase()
        if lib.mach_timebase_info(ctypes.byref(tb)) != 0 or tb.denom == 0:
            return None
        return lambda: lib.mach_continuous_time() * tb.numer // tb.denom
    except Exception:
        return None


_MACH = _load_mach()
CLOCK_NAME = "mach_continuous_time" if _MACH else "monotonic_ns_fallback"


def now_ns() -> int:
    return _MACH() if _MACH else time.monotonic_ns()


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds")


# ---------------------------------------------------------------- real side effects
def real_run(argv: List[str]) -> int:
    return subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=120).returncode


def real_read_sysctl() -> Optional[int]:
    try:
        out = subprocess.run(SYSCTL_READ, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                             timeout=30).stdout
        return int(out.decode().strip())
    except Exception:
        return None


def real_popen(argv: List[str]) -> Any:
    return subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


class RealBalloon:
    """Anonymous memory, every page written, then mlock()ed. Freed with the process."""

    def __init__(self, nbytes: int) -> None:
        self.nbytes = nbytes
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mlock.argtypes = self.libc.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.mm = mmap.mmap(-1, nbytes)
        for off in range(0, nbytes, mmap.PAGESIZE):
            self.mm[off] = 1
        self.anchor = ctypes.c_char.from_buffer(self.mm)
        self.addr = ctypes.addressof(self.anchor)
        if self.libc.mlock(self.addr, nbytes) != 0:
            err = ctypes.get_errno()
            self._free()
            raise OSError(err, "mlock failed: " + os.strerror(err))
        self.locked = True

    def _free(self) -> None:
        self.anchor = None
        self.mm.close()

    def release(self) -> None:
        if self.locked:
            self.libc.munlock(self.addr, self.nbytes)
            self.locked = False
        self._free()


class DryBalloon:
    def __init__(self, nbytes: int) -> None:
        self.nbytes = nbytes

    def release(self) -> None:
        pass


class Refusal(Exception):
    pass


def check_run_dir(run_dir: str, owner_uid: int) -> None:
    if not os.path.isabs(run_dir):
        raise Refusal("--run-dir must be an absolute path")
    if not os.path.isdir(run_dir):
        raise Refusal("--run-dir is not an existing directory")
    if os.stat(run_dir).st_uid != owner_uid:
        raise Refusal("--run-dir is not owned by the invoking user")


class Helper:
    def __init__(self, run_dir: str, max_hours: int = 6, dry_run: bool = False, uid: Optional[int] = None,
                 gid: Optional[int] = None, lifetime_s: Optional[float] = None, run: Callable = real_run,
                 read_sysctl: Callable = real_read_sysctl, balloon_factory: Optional[Callable] = None,
                 popen: Callable = real_popen, sock_path: Optional[str] = None, out=None) -> None:
        if max_hours not in MAX_HOURS_ALLOWED:
            raise Refusal("--max-hours accepts only 6 or 18")
        self.run_dir, self.max_hours, self.dry_run = run_dir, max_hours, dry_run
        self.uid = os.getuid() if uid is None else uid
        self.gid = os.getgid() if gid is None else gid
        self.lifetime_s = max_hours * 3600.0 if lifetime_s is None else lifetime_s
        self._run, self._read, self._popen = run, read_sysctl, popen
        self._balloon_factory = DryBalloon if dry_run else (balloon_factory or RealBalloon)
        self.sock_path = sock_path or os.path.join(run_dir, "admin.sock")
        self.out = out or sys.stdout
        self.started = time.monotonic()
        self.stop_reason: Optional[str] = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.cmds: List[Dict[str, Any]] = []
        self.audit_path = os.path.join(run_dir, "admin-audit.jsonl")
        self.audit_fh = None
        self.pm_proc: Any = None
        self.pm_path: Optional[str] = None
        self.pm_count = 0
        self.balloon: Any = None
        self.balloon_bytes = 0
        self.limit_changed = False
        self.original_mb: Optional[int] = None
        self.current_mb: Optional[int] = None

    # ---- audit
    def audit(self, record: Dict[str, Any]) -> None:
        record = dict(record, t_wall_utc=now_utc(), t_ns=now_ns(), clock=CLOCK_NAME)
        if self.audit_fh is None:
            new = not os.path.exists(self.audit_path)
            self.audit_fh = open(self.audit_path, "a", encoding="utf-8")
            if new:
                self._chown(self.audit_path)
        self.audit_fh.write(json.dumps(record, sort_keys=True) + "\n")
        self.audit_fh.flush()

    def _chown(self, path: str) -> None:
        try:
            os.chown(path, self.uid, self.gid)
        except OSError:
            pass

    def start(self) -> None:
        if not self.dry_run:
            self.original_mb = self._read()
            self.current_mb = self.original_mb
        self.audit({"event": "start", "dry_run": self.dry_run, "max_hours": self.max_hours,
                    "original_wired_limit_mb": self.original_mb, "clock_note": CLOCK_NAME})
        if self.dry_run:
            self.say("would run: " + " ".join(SYSCTL_READ))

    def say(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    # ---- commands
    def cmd(self, argv: List[str]) -> int:
        if self.dry_run:
            self.say("would run: " + " ".join(argv))
            self.cmds.append({"argv": argv, "executed": False})
            return 0
        rc = self._run(argv)
        self.cmds.append({"argv": argv, "executed": True, "exit_code": rc})
        return rc

    # ---- requests
    def handle_line(self, line: bytes) -> Dict[str, Any]:
        rid, verb, args, error = None, None, None, None
        try:
            req = json.loads(line.decode("utf-8"))
            if not isinstance(req, dict) or set(req) - {"id", "verb", "args"}:
                raise ValueError("request must be an object with id, verb, args")
            rid, verb, args = req.get("id"), req.get("verb"), req.get("args", {})
            if isinstance(rid, bool) or not isinstance(rid, int):
                rid = None
                raise ValueError("id must be an integer")
            if not isinstance(verb, str) or not isinstance(args, dict):
                raise ValueError("verb must be a string and args an object")
        except (ValueError, UnicodeDecodeError) as exc:
            error = "malformed request: %s" % exc
        self.cmds = []
        result, ok = None, False
        if error is None:
            try:
                error = self.validate(verb, args)
                if error is None:
                    with self.lock:
                        result = getattr(self, "v_" + verb)(**args)
                    ok = True
            except Refusal as exc:
                error = str(exc)
            except Exception as exc:  # a failing step must not kill the helper
                error = "%s: %s" % (type(exc).__name__, exc)
        self.audit({"event": "request", "id": rid, "verb": verb if isinstance(verb, str) else None,
                    "args": args if isinstance(args, dict) else None, "ok": ok, "result": result,
                    "error": error, "commands": self.cmds,
                    "raw": None if error is None or verb else line[:200].decode("utf-8", "replace")})
        return {"id": rid, "ok": ok, "result": result, "error": error}

    @staticmethod
    def validate(verb: Any, args: Dict[str, Any]) -> Optional[str]:
        if verb not in VERBS:
            return "unknown verb"
        allowed = VERBS[verb]
        for key in args:
            if key not in allowed:
                return "argument not allowed: %s" % key
        for key, values in allowed.items():
            if key not in args:
                return "missing argument: %s" % key
            val = args[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val not in values:
                return "value not allowed for %s" % key
        return None

    # ---- verbs
    def v_purge(self) -> Dict[str, Any]:
        for argv in (SYNC, PURGE):
            rc = self.cmd(argv)
            if rc != 0:
                raise Refusal("%s exited with %d" % (argv[0], rc))
        return {"exit_codes": [c.get("exit_code") for c in self.cmds]}

    def v_powermetrics_start(self, interval_ms: int) -> Dict[str, Any]:
        if self.pm_proc is not None and (self.dry_run or self.pm_proc.poll() is None):
            raise Refusal("powermetrics is already running")
        self.pm_count += 1
        argv = powermetrics_argv(self.run_dir, int(interval_ms), self.pm_count)
        self.pm_path = argv[-1]
        t_ns, t_wall = now_ns(), now_utc()
        if self.dry_run:
            self.say("would run: " + " ".join(argv))
            self.pm_proc = DryBalloon(0)  # a marker only; nothing is started
            self.cmds.append({"argv": argv, "executed": False})
            return {"path": self.pm_path, "t_ns": t_ns, "t_wall_utc": t_wall, "pid": None}
        self.pm_proc = self._popen(argv)
        self.cmds.append({"argv": argv, "executed": True, "pid": self.pm_proc.pid})
        return {"path": self.pm_path, "t_ns": t_ns, "t_wall_utc": t_wall, "pid": self.pm_proc.pid}

    def v_powermetrics_stop(self) -> Dict[str, Any]:
        proc = self.pm_proc
        if proc is None:
            raise Refusal("powermetrics is not running")
        self.pm_proc = None
        if self.dry_run:
            self.say("would send SIGINT to the powermetrics process it started")
            return {"exit_code": None}
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()  # only our own child
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        if self.pm_path and os.path.exists(self.pm_path):
            self._chown(self.pm_path)
        self.cmds.append({"signal": "SIGINT", "pid": proc.pid, "exit_code": proc.returncode})
        return {"exit_code": proc.returncode, "path": self.pm_path}

    def v_wired_limit_set(self, mb: int) -> Dict[str, Any]:
        if self.original_mb is None and not self.dry_run:
            raise Refusal("original wired limit unknown; refusing to change it")
        self.limit_changed = True  # restore must try even if the command fails half way
        mb = int(mb)
        rc = self.cmd(sysctl_set_argv(mb))
        if rc != 0:
            raise Refusal("sysctl exited with %d" % rc)
        self.current_mb = mb
        return {"mb": mb}

    def v_wired_limit_reset(self) -> Dict[str, Any]:
        if self.original_mb is None and not self.dry_run:
            raise Refusal("original wired limit unknown")
        rc = self.cmd(sysctl_set_argv(self.original_mb if self.original_mb is not None else "<original>"))
        if rc != 0:
            raise Refusal("sysctl exited with %d" % rc)
        self.limit_changed, self.current_mb = False, self.original_mb
        return {"mb": self.original_mb}

    def v_balloon_wire(self, gib: float) -> Dict[str, Any]:
        if self.balloon is not None:
            raise Refusal("a balloon is already wired")
        nbytes = int(gib * (1 << 30))
        self.balloon = self._balloon_factory(nbytes)
        self.balloon_bytes = nbytes
        return {"bytes": nbytes, "wired": not self.dry_run}

    def v_balloon_release(self) -> Dict[str, Any]:
        if self.balloon is None:
            raise Refusal("no balloon is wired")
        freed = self.balloon_bytes
        self.balloon.release()
        self.balloon, self.balloon_bytes = None, 0
        return {"bytes_freed": freed}

    def v_status(self) -> Dict[str, Any]:
        return {"original_wired_limit_mb": self.original_mb, "current_wired_limit_mb": self.current_mb,
                "balloon_bytes": self.balloon_bytes,
                "powermetrics_running": self.pm_proc is not None and (self.dry_run or self.pm_proc.poll() is None),
                "powermetrics_path": self.pm_path if self.pm_proc is not None else None,
                "uptime_s": round(time.monotonic() - self.started, 3), "max_hours": self.max_hours,
                "dry_run": self.dry_run, "clock": CLOCK_NAME}

    def v_exit(self) -> Dict[str, Any]:
        steps = self.restore("exit")
        self.stop_event.set()
        return {"restored": steps}

    # ---- restore (idempotent; every step runs even if an earlier one fails)
    def restore(self, reason: str) -> List[Dict[str, Any]]:
        steps = []
        with self.lock:
            self.audit({"event": "restore_begin", "reason": reason})
            for name, fn in (("powermetrics_stop", self._restore_pm), ("balloon_release", self._restore_balloon),
                             ("wired_limit_reset", self._restore_limit)):
                self.cmds = []
                try:
                    did = fn()
                    step = {"step": name, "ok": True, "did_something": did}
                except Exception as exc:
                    step = {"step": name, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
                steps.append(step)
                self.audit({"event": "restore_step", "commands": self.cmds, **step})
        return steps

    def _restore_pm(self) -> bool:
        if self.pm_proc is None:
            return False
        self.v_powermetrics_stop()
        return True

    def _restore_balloon(self) -> bool:
        if self.balloon is None:
            return False
        self.v_balloon_release()
        return True

    def _restore_limit(self) -> bool:
        if not self.limit_changed:
            return False
        self.v_wired_limit_reset()
        return True

    def request_stop(self, reason: str) -> None:
        """Signal-handler entry point: only flags; serve() restores."""
        self.stop_reason = self.stop_reason or reason
        self.stop_event.set()

    # ---- socket server
    def serve(self) -> None:
        deadline = self.started + self.lifetime_s
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        reason = "disconnect"
        conn = None
        try:
            old = os.umask(0o177)
            try:
                listener.bind(self.sock_path)
            finally:
                os.umask(old)
            os.chmod(self.sock_path, 0o600)
            self._chown(self.sock_path)
            listener.listen(1)
            self.audit({"event": "listening", "socket": self.sock_path})
            self.say("listening on " + self.sock_path)
            listener.settimeout(POLL_S)
            while conn is None:
                if self.stop_event.is_set() or time.monotonic() >= deadline:
                    raise StopIteration
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    pass
            listener.close()  # exactly one client
            conn.settimeout(POLL_S)
            buf = b""
            while not self.stop_event.is_set():
                if time.monotonic() >= deadline:
                    reason = "timeout"
                    break
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                while b"\n" in buf and not self.stop_event.is_set():
                    line, buf = buf.split(b"\n", 1)
                    resp = self.handle_line(line)
                    try:
                        conn.sendall((json.dumps(resp) + "\n").encode())
                    except OSError:
                        self.request_stop("disconnect")
                if len(buf) > MAX_LINE:
                    self.audit({"event": "refused", "error": "line too long"})
                    break
            if self.stop_event.is_set():
                reason = self.stop_reason or "exit"
        except StopIteration:
            reason = self.stop_reason or "timeout"
        finally:
            self.restore(reason)
            for obj in (conn, listener):
                try:
                    if obj is not None:
                        obj.close()
                except OSError:
                    pass
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
            self.audit({"event": "exit", "reason": reason})
            self.audit_fh.close()
            self.audit_fh = None


def main(argv: Optional[List[str]] = None, is_root: Callable[[], bool] = lambda: os.geteuid() == 0,
         env: Optional[Dict[str, str]] = None, **helper_kwargs: Any) -> int:
    p = argparse.ArgumentParser(description="tinyagent-bench admin helper")
    p.add_argument("--run-dir")
    p.add_argument("--max-hours", type=int, default=6)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--print-plan", action="store_true")
    ns = p.parse_args(argv)
    if ns.print_plan:
        print("\n".join(plan_lines(ns.run_dir or "<run-dir>")))
        return 0
    env = os.environ if env is None else env
    try:
        if ns.max_hours not in MAX_HOURS_ALLOWED:
            raise Refusal("--max-hours accepts only 6 or 18")
        if not ns.run_dir:
            raise Refusal("--run-dir is required")
        if ns.dry_run:
            uid, gid = os.getuid(), os.getgid()
        else:
            if not is_root():
                raise Refusal("must run as root (use sudo), or use --dry-run")
            try:
                uid, gid = int(env["SUDO_UID"]), int(env["SUDO_GID"])
            except (KeyError, ValueError):
                raise Refusal("SUDO_UID and SUDO_GID must be set (start it with sudo)")
        check_run_dir(ns.run_dir, uid)
        helper = Helper(ns.run_dir, ns.max_hours, ns.dry_run, uid, gid, **helper_kwargs)
    except Refusal as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda s, f, n=signal.Signals(sig).name: helper.request_stop(n))
    helper.start()
    helper.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
