"""Memory measurement for the Mac runner (spec 04 §5.1-§5.3, §2.5, §3.6).

The headline number is the engine process's **physical footprint**, never RSS:
``proc_pid_rusage(RUSAGE_INFO_V4)`` gives ``ri_phys_footprint`` and the kernel-tracked
``ri_lifetime_max_phys_footprint`` (§5.1). It is sampled at 20 Hz on the run clock and
each tick is appended to ``mem-<run_id>.jsonl`` (``{t_ns, footprint, sample_id}``).

Per sample the runner stores ``footprint_start`` (just before submit), ``peak`` (max of
the ticks during the request, or the kernel peak if it rose during the request),
``steady`` (median during decode) and ``end`` (§2.5, §5.2). Diagnostics: swap and
compressor deltas from ``sysctl vm.swapusage`` and ``vm_stat`` (both read-only),
the process's disk-read bytes (for the cold-load check, beside ``iostat``), and the
``footprint`` tool's "neural" ledger when a model uses the Neural Engine (§5.1).

Weights, KV slope and overhead are separated later by the aggregator from these
per-sample values (§5.2).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import statistics
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .constants import FOOTPRINT_RATE_HZ

RUSAGE_INFO_V4 = 4


class RusageInfoV4(ctypes.Structure):
    """``struct rusage_info_v4`` from <sys/resource.h>."""
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
        "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
        "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time",
        "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups", "ri_child_pageins",
        "ri_child_elapsed_abstime", "ri_diskio_bytesread", "ri_diskio_byteswritten",
        "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance", "ri_cpu_time_qos_background",
        "ri_cpu_time_qos_utility", "ri_cpu_time_qos_legacy", "ri_cpu_time_qos_user_initiated",
        "ri_cpu_time_qos_user_interactive", "ri_billed_system_time", "ri_serviced_system_time",
        "ri_logical_writes", "ri_lifetime_max_phys_footprint", "ri_instructions", "ri_cycles",
        "ri_billed_energy", "ri_serviced_energy", "ri_interval_max_phys_footprint",
        "ri_runnable_time")]


@dataclass
class ProcMemory:
    footprint: int
    lifetime_max: int
    interval_max: int
    resident: int
    disk_read_bytes: int


class MemoryProbe(Protocol):
    def read(self) -> ProcMemory: ...
    def alive(self) -> bool: ...


class RusageProbe:
    """Reads one process (the oMLX server) with ``proc_pid_rusage``. Same-user processes only;
    it needs no administrator rights."""

    def __init__(self, pid: int):
        self.pid = pid
        lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libSystem.B.dylib")
        self._fn = lib.proc_pid_rusage
        self._fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self._fn.restype = ctypes.c_int

    def read(self) -> ProcMemory:
        buf = RusageInfoV4()
        if self._fn(self.pid, RUSAGE_INFO_V4, ctypes.byref(buf)) != 0:
            raise ProcessLookupError(f"proc_pid_rusage({self.pid}) failed: errno {ctypes.get_errno()}")
        return ProcMemory(buf.ri_phys_footprint, buf.ri_lifetime_max_phys_footprint,
                          buf.ri_interval_max_phys_footprint, buf.ri_resident_size, buf.ri_diskio_bytesread)

    def alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def find_engine_pid(exe_suffixes: tuple[str, ...] = ("omlx-server", "omlx", "omlx-cli", "oMLX"),
                    run: Callable = subprocess.run) -> int | None:
    """Best-effort: the benchmark oMLX server's pid, from ``ps`` (oMLX's ``serve`` renames its
    process ``omlx-server``). Returns None unless exactly one matches; the runbook says to pass
    ``--engine-pid`` explicitly, since the user's own engine may be running too."""
    out = run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    hits = [int(p) for p, c in (ln.strip().split(None, 1) for ln in out.splitlines() if ln.strip())
            if os.path.basename(c).split()[0] in exe_suffixes]
    return hits[0] if len(hits) == 1 else None


# --- system counters (read-only commands) -------------------------------------------

_SIZE = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}


def parse_swapusage(text: str) -> int | None:
    """``sysctl -n vm.swapusage`` → used bytes. e.g. ``total = 2048.00M  used = 512.25M  free = …``."""
    m = re.search(r"used\s*=\s*([\d.]+)([KMG])", text)
    return int(float(m.group(1)) * _SIZE[m.group(2)]) if m else None


def parse_vm_stat_compressed(text: str) -> int | None:
    """``vm_stat`` → bytes occupied by the compressor."""
    ps = re.search(r"page size of (\d+) bytes", text)
    m = re.search(r"Pages occupied by compressor:\s+(\d+)", text)
    if not (ps and m):
        return None
    return int(m.group(1)) * int(ps.group(1))


def parse_footprint_neural(text: str) -> int | None:
    """``footprint <pid>`` → bytes in the "neural" ledger, if listed (§5.1). Output format
    **unverified** on macOS 27; the parser looks for a size on a line naming "neural"."""
    for ln in text.splitlines():
        if "neural" in ln.lower():
            m = re.search(r"([\d.]+)\s*([KMG])B", ln)
            if m:
                return int(float(m.group(1)) * _SIZE[m.group(2)])
    return None


@dataclass
class SystemCounters:
    swap_used: int | None
    compressed: int | None


class SystemMemory:
    def __init__(self, run: Callable = subprocess.run):
        self.run = run

    def _out(self, argv: list[str]) -> str:
        try:
            return self.run(argv, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def counters(self) -> SystemCounters:
        return SystemCounters(parse_swapusage(self._out(["/usr/sbin/sysctl", "-n", "vm.swapusage"])),
                              parse_vm_stat_compressed(self._out(["/usr/bin/vm_stat"])))

    def neural_bytes(self, pid: int) -> int | None:
        return parse_footprint_neural(self._out(["/usr/bin/footprint", str(pid)]))


def delta(a: int | None, b: int | None) -> int | None:
    return b - a if isinstance(a, int) and isinstance(b, int) else None


# --- the sampler -------------------------------------------------------------------

@dataclass
class SampleMemory:
    footprint_start_bytes: int | None = None
    footprint_peak_bytes: int | None = None
    footprint_peak_ledger_bytes: int | None = None
    footprint_steady_bytes: int | None = None
    footprint_end_bytes: int | None = None
    kernel_peak_before_bytes: int | None = None
    kernel_peak_after_bytes: int | None = None
    disk_read_delta_bytes: int | None = None
    ticks: int = 0
    tail: list = field(default_factory=list)   # last ticks, for kill evidence

    def as_fields(self) -> dict:
        return {k: getattr(self, k) for k in (
            "footprint_start_bytes", "footprint_peak_bytes", "footprint_peak_ledger_bytes",
            "footprint_steady_bytes", "footprint_end_bytes", "kernel_peak_before_bytes",
            "kernel_peak_after_bytes")}


class FootprintMonitor:
    """20 Hz ``phys_footprint`` ticks on the run clock, logged to ``mem-<run_id>.jsonl``.

    Two ways to drive it: :meth:`start` runs a background thread (real runs); with a
    ``FakeClock`` call :meth:`attach_fake_clock` so ticks happen as fake time advances.
    """

    def __init__(self, probe: MemoryProbe, now_ns: Callable[[], int], log_path: str | None,
                 rate_hz: float = FOOTPRINT_RATE_HZ, flush_every_s: float = 0.25):
        self.probe = probe
        self.now_ns = now_ns
        self.period_ns = int(1e9 / rate_hz)
        self.flush_ns = int(flush_every_s * 1e9)
        self.log = open(log_path, "a", buffering=1) if log_path else None
        self.series: list[tuple[int, int]] = []   # (t_ns, footprint)
        self.sample_id: str | None = None
        self.errors = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_flush = 0
        self._next_tick: int | None = None

    def tick(self, t_ns: int | None = None) -> int | None:
        t = self.now_ns() if t_ns is None else t_ns
        try:
            fp = self.probe.read().footprint
        except (OSError, ProcessLookupError):
            self.errors += 1
            return None
        with self._lock:
            self.series.append((t, fp))
            if self.log:
                self.log.write(json.dumps({"t_ns": t, "footprint": fp, "sample_id": self.sample_id}) + "\n")
                if t - self._last_flush >= self.flush_ns:
                    self.log.flush()
                    self._last_flush = t
        return fp

    # thread mode
    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(self.period_ns / 1e9)
        self._thread = threading.Thread(target=loop, name="edat-footprint", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.log:
            self.log.flush()

    # fake-clock mode
    def attach_fake_clock(self, clock) -> None:
        def on_advance(old: int, new: int) -> None:
            if self._next_tick is None:
                self._next_tick = old
            while self._next_tick <= new:
                self.tick(self._next_tick)
                self._next_tick += self.period_ns
        clock.listeners.append(on_advance)

    def close(self) -> None:
        if self.log:
            self.log.close()
            self.log = None

    # per-sample summary
    def begin(self, sample_id: str) -> SampleMemory:
        self.sample_id = sample_id
        m = SampleMemory()
        try:
            r = self.probe.read()
            m.footprint_start_bytes, m.kernel_peak_before_bytes = r.footprint, r.lifetime_max
            m._disk0 = r.disk_read_bytes  # type: ignore[attr-defined]
        except (OSError, ProcessLookupError):
            m._disk0 = None  # type: ignore[attr-defined]
        m._t0 = self.now_ns()  # type: ignore[attr-defined]
        return m

    def end(self, m: SampleMemory, *, t_first_ns: int | None, t_last_ns: int | None) -> SampleMemory:
        t0, t1 = m._t0, self.now_ns()  # type: ignore[attr-defined]
        with self._lock:
            window = [fp for t, fp in self.series if t0 <= t <= t1]
            decode = [fp for t, fp in self.series
                      if t_first_ns is not None and t_last_ns is not None and t_first_ns <= t <= t_last_ns]
            m.tail = [[t, fp] for t, fp in self.series if t >= t1 - 2_000_000_000][-40:]
        try:
            r = self.probe.read()
            m.footprint_end_bytes, m.kernel_peak_after_bytes = r.footprint, r.lifetime_max
            if m._disk0 is not None:  # type: ignore[attr-defined]
                m.disk_read_delta_bytes = r.disk_read_bytes - m._disk0  # type: ignore[attr-defined]
            window.append(r.footprint)
        except (OSError, ProcessLookupError):
            pass
        if m.footprint_start_bytes is not None:
            window.append(m.footprint_start_bytes)
        m.ticks = len(window)
        m.footprint_peak_bytes = max(window) if window else None
        # The kernel's lifetime max only tells us the request's own peak when it rose during it.
        if (m.kernel_peak_before_bytes is not None and m.kernel_peak_after_bytes is not None
                and m.kernel_peak_after_bytes > m.kernel_peak_before_bytes):
            m.footprint_peak_ledger_bytes = m.kernel_peak_after_bytes
            m.footprint_peak_bytes = max(m.footprint_peak_bytes or 0, m.kernel_peak_after_bytes)
        m.footprint_steady_bytes = int(statistics.median(decode)) if decode else None
        self.sample_id = None
        return m
