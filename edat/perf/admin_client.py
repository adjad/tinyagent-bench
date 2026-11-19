"""The runner's side of the EDAT admin helper socket (spec 04 §11.1).

``AdminClient`` talks to the helper over its Unix socket, one JSON line per request.
``NullAdminClient`` is used when no helper is running: every verb raises ``AdminUnavailable``,
so a run records that the verb was unavailable instead of silently skipping it.
``FakeAdminClient`` records calls, for other modules' tests.
"""
from __future__ import annotations

import json
import socket
import subprocess
from typing import Any, Callable, Optional

SYSCTL_KEY = "iogpu.wired_limit_mb"


class AdminError(RuntimeError):
    """The helper answered ``ok: false``."""


class AdminUnavailable(AdminError):
    """No helper is available (not started, or not connected)."""


class _Verbs:
    """The verb methods, written once on top of ``call``."""

    def call(self, verb: str, **args: Any) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def purge(self): return self.call("purge")
    def powermetrics_start(self, interval_ms: int): return self.call("powermetrics_start", interval_ms=interval_ms)
    def powermetrics_stop(self): return self.call("powermetrics_stop")
    def wired_limit_set(self, mb: int): return self.call("wired_limit_set", mb=mb)
    def wired_limit_reset(self): return self.call("wired_limit_reset")
    def balloon_wire(self, gib: float): return self.call("balloon_wire", gib=gib)
    def balloon_release(self): return self.call("balloon_release")
    def status(self): return self.call("status")
    def exit(self): return self.call("exit")


class AdminClient(_Verbs):
    def __init__(self, socket_path: str, timeout_s: float = 180.0) -> None:
        self.socket_path = socket_path
        self.timeout_s = timeout_s
        self._sock: Optional[socket.socket] = None
        self._file = None
        self._next_id = 1

    def connect(self) -> "AdminClient":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_s)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            sock.close()
            raise AdminUnavailable("cannot connect to the admin helper at %s: %s" % (self.socket_path, exc))
        self._sock = sock
        self._file = sock.makefile("rwb")
        return self

    def close(self) -> None:
        for obj in (self._file, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._file = self._sock = None

    def call(self, verb: str, **args: Any) -> Any:
        if self._file is None:
            raise AdminUnavailable("not connected to the admin helper")
        rid, self._next_id = self._next_id, self._next_id + 1
        self._file.write((json.dumps({"id": rid, "verb": verb, "args": args}) + "\n").encode())
        self._file.flush()
        line = self._file.readline()
        if not line:
            raise AdminUnavailable("the admin helper closed the connection")
        resp = json.loads(line.decode())
        if resp.get("id") != rid:
            raise AdminError("response id %r does not match request id %r" % (resp.get("id"), rid))
        if not resp.get("ok"):
            raise AdminError("%s refused: %s" % (verb, resp.get("error")))
        return resp.get("result")

    def exit(self):
        try:
            return self.call("exit")
        finally:
            self.close()


class NullAdminClient(_Verbs):
    """Stands in when the helper is not running."""

    def connect(self) -> "NullAdminClient":
        return self

    def close(self) -> None:
        pass

    def call(self, verb: str, **args: Any) -> Any:
        raise AdminUnavailable("admin helper not running; '%s' is unavailable for this run" % verb)


class FakeAdminClient(_Verbs):
    """Records every call as ``(verb, args)``; answers from ``results`` or ``{}``."""

    def __init__(self, results: Optional[dict] = None, fail: Optional[dict] = None) -> None:
        self.calls: list = []
        self.results = results or {}
        self.fail = fail or {}

    def connect(self) -> "FakeAdminClient":
        return self

    def close(self) -> None:
        pass

    def call(self, verb: str, **args: Any) -> Any:
        self.calls.append((verb, args))
        if verb in self.fail:
            raise AdminError(str(self.fail[verb]))
        return self.results.get(verb, {})


def read_wired_limit_mb() -> Optional[int]:
    """``sysctl -n iogpu.wired_limit_mb`` (a read; changes nothing)."""
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", SYSCTL_KEY], capture_output=True, timeout=30, check=True)
        return int(out.stdout.decode().strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def check_wired_limit(recorded_original_mb: Optional[int],
                      read: Callable[[], Optional[int]] = read_wired_limit_mb) -> Optional[str]:
    """A warning string when the current limit differs from the recorded original, else ``None``."""
    current = read()
    if current is None:
        return "could not read %s; cannot check it against the recorded original" % SYSCTL_KEY
    if recorded_original_mb is None:
        return None
    if current != recorded_original_mb:
        return ("%s is %d MB now but the recorded original is %d MB; a previous run may have been killed "
                "before it restored the limit (reboot or reset it by hand)"
                % (SYSCTL_KEY, current, recorded_original_mb))
    return None
