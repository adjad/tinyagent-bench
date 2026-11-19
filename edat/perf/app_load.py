"""``edat-app-load``: a background load that behaves like apps (spec 04 §5.6, ``L_apps``).

Run as a plain user-level process (never through the admin helper)::

    python -m edat.perf.app_load --gib 2.5 --hot-gib 1.5 --status <path>

It allocates ``--gib`` GiB of anonymous memory (``mmap``), fills it with random bytes so the
compressor cannot make it free, then every second reads one byte per page of the first
``--hot-gib`` GiB, sequentially, and leaves the rest cold. Every second it writes a small
status JSON (atomically: temp file + rename) to ``--status``::

    {"schema": "edat.app-load-status/1", "pid", "t_ns", "ready", "allocated_bytes",
     "hot_bytes", "cold_bytes", "page_bytes", "phys_footprint_bytes", "resident_bytes",
     "baseline_resident_bytes", "swapped_or_compressed_bytes_estimate",
     "swapped_or_compressed_is_estimate": true, "estimate_basis", "touch_passes",
     "touch_ms_last"}

``phys_footprint_bytes`` and ``resident_bytes`` come from ``proc_pid_rusage(getpid())`` (the
same ctypes call as ``edat.perf.memory``; its own process only, no rights needed).

The swapped/compressed figure is an **estimate**: allocated − (resident − resident before the
allocation). The spec's "allocated − resident" would count the interpreter's own resident
pages against the allocation, so the pre-allocation resident size is subtracted first; the
basis is written in the status as ``estimate_basis``.

:class:`AppLoad` is the runner's controller (``MacRunner.fit16(app_load=…)`` calls
``start(gib)``, ``swapped_bytes()`` and ``stop()``). It starts the process with
``subprocess.Popen`` and only ever signals that child.

Choice: for loads other than the §5.6 2.5 GiB (the pressure sweep's 4.0 and 5.5 GiB), the hot
set keeps the §5.6 proportion, 1.5 / 2.5 = 60 % of the load, unless ``hot_gib`` is given.
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .constants import APP_LOAD_GIB, APP_LOAD_HOT_GIB, GIB, MIB

STATUS_SCHEMA = "edat.app-load-status/1"
HOT_FRACTION = APP_LOAD_HOT_GIB / APP_LOAD_GIB
FILL_CHUNK = 4 * MIB
ESTIMATE_BASIS = "allocated - (resident - resident before allocation)"


def _rusage_reader() -> Callable[[], tuple[int | None, int | None]]:
    try:
        from .memory import RusageProbe
        probe = RusageProbe(os.getpid())
    except (OSError, AttributeError):
        return lambda: (None, None)

    def read() -> tuple[int | None, int | None]:
        try:
            m = probe.read()
            return m.footprint, m.resident
        except (OSError, ProcessLookupError):
            return None, None
    return read


class AppLoadProcess:
    """The allocation and touch logic, usable in-process (tests use a few MiB)."""

    def __init__(self, nbytes: int, hot_bytes: int, *, page_bytes: int = mmap.PAGESIZE,
                 read_mem: Callable[[], tuple[int | None, int | None]] | None = None,
                 now_ns: Callable[[], int] | None = None, random_bytes: Callable[[int], bytes] = os.urandom):
        if nbytes <= 0 or hot_bytes < 0:
            raise ValueError("size must be positive and the hot set non-negative")
        self.page = page_bytes
        self.nbytes = -(-int(nbytes) // self.page) * self.page            # whole pages
        self.hot_bytes = min(self.nbytes, -(-int(hot_bytes) // self.page) * self.page)
        self.read_mem = read_mem or _rusage_reader()
        self.now_ns = now_ns or time.monotonic_ns
        self.random_bytes = random_bytes
        self.buf: mmap.mmap | None = None
        self.baseline_resident: int | None = None
        self.touch_passes = 0
        self.touch_ms_last: float | None = None
        self.checksum = 0

    def allocate(self) -> None:
        self.baseline_resident = self.read_mem()[1]
        self.buf = mmap.mmap(-1, self.nbytes, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        off = 0
        while off < self.nbytes:
            n = min(FILL_CHUNK, self.nbytes - off)
            self.buf[off:off + n] = self.random_bytes(n)
            off += n

    def touch_hot(self) -> int:
        """One read of one byte per page of the hot set, in order. Returns pages touched."""
        if self.buf is None:
            raise RuntimeError("allocate first")
        t0 = time.perf_counter()
        acc = 0
        buf = self.buf
        for off in range(0, self.hot_bytes, self.page):
            acc ^= buf[off]
        self.checksum = acc
        self.touch_passes += 1
        self.touch_ms_last = (time.perf_counter() - t0) * 1000.0
        return self.hot_bytes // self.page

    def status(self) -> dict[str, Any]:
        fp, res = self.read_mem()
        est = None
        if self.buf is not None and res is not None:
            attributable = res - (self.baseline_resident or 0)
            est = max(0, self.nbytes - max(0, attributable))
        return {"schema": STATUS_SCHEMA, "pid": os.getpid(), "t_ns": self.now_ns(), "ready": self.buf is not None,
                "allocated_bytes": self.nbytes if self.buf is not None else 0, "hot_bytes": self.hot_bytes,
                "cold_bytes": self.nbytes - self.hot_bytes, "page_bytes": self.page,
                "phys_footprint_bytes": fp, "resident_bytes": res, "baseline_resident_bytes": self.baseline_resident,
                "swapped_or_compressed_bytes_estimate": est, "swapped_or_compressed_is_estimate": True,
                "estimate_basis": ESTIMATE_BASIS, "touch_passes": self.touch_passes,
                "touch_ms_last": self.touch_ms_last}

    def close(self) -> None:
        if self.buf is not None:
            self.buf.close()
            self.buf = None


def write_status(path: str | os.PathLike, status: dict) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(status))
    tmp.replace(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="edat-app-load", description=__doc__.split("\n\n")[0])
    ap.add_argument("--gib", type=float, default=APP_LOAD_GIB)
    ap.add_argument("--hot-gib", type=float, default=None)
    ap.add_argument("--status", required=True)
    ap.add_argument("--period-s", type=float, default=1.0)
    ap.add_argument("--max-seconds", type=float, default=None, help="exit after this long (tests)")
    a = ap.parse_args(argv)
    hot = a.hot_gib if a.hot_gib is not None else a.gib * HOT_FRACTION
    stop = {"flag": False}

    def on_term(signum, frame):  # noqa: ARG001
        stop["flag"] = True
    old_handlers = {sig: signal.signal(sig, on_term) for sig in (signal.SIGTERM, signal.SIGINT)}
    proc = AppLoadProcess(int(a.gib * GIB), int(hot * GIB))
    write_status(a.status, proc.status())
    proc.allocate()
    start = time.monotonic()
    try:
        while not stop["flag"]:
            t0 = time.monotonic()
            proc.touch_hot()
            write_status(a.status, proc.status())
            if a.max_seconds is not None and time.monotonic() - start >= a.max_seconds:
                break
            delay = a.period_s - (time.monotonic() - t0)
            while delay > 0 and not stop["flag"]:
                time.sleep(min(delay, 0.1))
                delay = a.period_s - (time.monotonic() - t0)
    finally:
        st = proc.status()
        st["exiting"] = True
        write_status(a.status, st)
        proc.close()
        for sig, h in old_handlers.items():
            signal.signal(sig, h)
    return 0


class AppLoad:
    """Controller for the runner: ``start(gib)``, ``stop()``, ``swapped_bytes()``, ``status()``."""

    def __init__(self, status_path: str | os.PathLike, *, hot_gib: float | None = None,
                 python: str = sys.executable, popen: Callable[..., Any] = subprocess.Popen,
                 ready_timeout_s: float = 300.0, sleep: Callable[[float], None] = time.sleep,
                 period_s: float = 1.0, extra_args: list[str] | None = None):
        self.status_path = Path(status_path)
        self.hot_gib = hot_gib
        self.python = python
        self.popen = popen
        self.ready_timeout_s = ready_timeout_s
        self.sleep = sleep
        self.period_s = period_s
        self.extra_args = list(extra_args or ())
        self.proc: Any = None
        self.gib: float | None = None

    def start(self, gib: float | None = None) -> dict | None:
        if self.proc is not None:
            raise RuntimeError("app load already running")
        self.gib = float(gib if gib is not None else APP_LOAD_GIB)
        hot = self.hot_gib if self.hot_gib is not None else self.gib * HOT_FRACTION
        if self.status_path.exists():
            self.status_path.unlink()
        root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        argv = [self.python, "-m", "edat.perf.app_load", "--gib", repr(self.gib), "--hot-gib", repr(hot),
                "--status", str(self.status_path), "--period-s", repr(self.period_s), *self.extra_args]
        self.proc = self.popen(argv, env=env, cwd=root, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        waited = 0.0
        while waited < self.ready_timeout_s:
            st = self.status()
            if st and st.get("ready"):
                return st
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode("utf-8", "replace")[-500:] if self.proc.stderr else ""
                self.proc = None
                raise RuntimeError(f"edat-app-load exited before it was ready: {err}")
            self.sleep(0.05)
            waited += 0.05
        self.stop()
        raise TimeoutError("edat-app-load did not finish allocating in time")

    def status(self) -> dict | None:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def swapped_bytes(self) -> int | None:
        st = self.status()
        return st.get("swapped_or_compressed_bytes_estimate") if st else None

    def stop(self, timeout_s: float = 10.0) -> int | None:
        """SIGTERM to our child only, then SIGKILL if it does not exit in time."""
        p, self.proc = self.proc, None
        if p is None:
            return None
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=timeout_s)
        if p.stderr:
            p.stderr.close()
        return p.returncode

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


if __name__ == "__main__":
    sys.exit(main())
