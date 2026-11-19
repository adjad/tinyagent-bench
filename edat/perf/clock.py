"""The run's one clock: ``mach_continuous_time`` in nanoseconds (spec 04 §2.5, §3.1).

The power sampler, the memory sampler, the admin helper's audit and the request
timestamps all read this clock, so no alignment step is needed between streams
(§4.3). It is monotonic and keeps counting through sleep.

Tests use :class:`FakeClock`, which advances only when told to (or when a fake
``sleep`` is called), so whole schedules run instantly and deterministically.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import time
from datetime import datetime, timezone
from typing import Callable, Protocol


class _Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


def _load_mach() -> Callable[[], int] | None:
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib")
        fn = lib.mach_continuous_time
        fn.restype = ctypes.c_uint64
        tb = _Timebase()
        if lib.mach_timebase_info(ctypes.byref(tb)) != 0 or not tb.denom:
            return None
        numer, denom = tb.numer, tb.denom
        return lambda: fn() * numer // denom
    except (OSError, AttributeError):
        return None


_mach_ns = _load_mach()
CLOCK_NAME = "mach_continuous_time" if _mach_ns else "monotonic_ns_fallback"


def now_ns() -> int:
    """Nanoseconds on ``mach_continuous_time`` (falls back to ``time.monotonic_ns`` off macOS)."""
    return _mach_ns() if _mach_ns else time.monotonic_ns()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock(Protocol):
    def now_ns(self) -> int: ...
    def sleep(self, seconds: float) -> None: ...
    def utc(self) -> str: ...


class SystemClock:
    """The real clock. ``sleep`` really sleeps."""

    name = CLOCK_NAME

    def now_ns(self) -> int:
        return now_ns()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def utc(self) -> str:
        return utc_now()


class FakeClock:
    """A clock for tests: time moves only through :meth:`advance` and :meth:`sleep`."""

    name = "mach_continuous_time"

    def __init__(self, start_ns: int = 1_000_000_000_000, utc_start: datetime | None = None):
        self.t = int(start_ns)
        self._t0 = int(start_ns)
        self._utc0 = utc_start or datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        self.slept: list[float] = []
        # Called as listener(old_ns, new_ns) on every advance: fake samplers use this to
        # take their 20 Hz readings in fake time.
        self.listeners: list[Callable[[int, int], None]] = []

    def now_ns(self) -> int:
        return self.t

    def advance(self, seconds: float) -> None:
        old = self.t
        self.t += int(round(seconds * 1e9))
        for fn in list(self.listeners):
            fn(old, self.t)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if seconds > 0:
            self.advance(seconds)

    def utc(self) -> str:
        from datetime import timedelta
        return (self._utc0 + timedelta(seconds=(self.t - self._t0) / 1e9)).strftime("%Y-%m-%dT%H:%M:%SZ")
