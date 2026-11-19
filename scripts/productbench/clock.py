"""A shifted wall clock for an isolated Harbor process, without touching service/.

Harbor's backend reads the wall clock directly (`time.time()` in
service/assistant/today_api.py, the scheduler, the proactive cycle, sync
freshness checks) and has no "now" override. A benchmark needs every model to
see the same "now": the profile's anchor time, e.g. a Tuesday at 07:45.

`install(offset)` moves this process's wall clock by `offset` seconds before
any Harbor module is imported. The clock still ticks at the real rate, so
timeouts, freshness windows and debounces keep working; only the reading is
shifted. Monotonic clocks (asyncio's event loop, timeouts) are untouched.

Patched: time.time, time.time_ns, time.localtime/gmtime/ctime/asctime/strftime
with no explicit time, datetime.datetime.now/utcnow/today and
datetime.date.today. The replacement classes answer isinstance() for real
datetime/date objects, so code that type-checks values from C libraries keeps
working (the same approach freezegun takes).

scripts/productbench/serve.py installs it in each server process it starts.
Every process of one run gets the same offset (anchor epoch minus the real
epoch the runner picked as the anchor's moment), so the backend and the sandbox
server agree on "now".
"""
from __future__ import annotations

import datetime as _dt
import os
import time as _time

OFFSET_ENV = "HARBOR_BENCH_CLOCK_OFFSET"

_real_time = _time.time
_real_time_ns = _time.time_ns
_real_localtime = _time.localtime
_real_gmtime = _time.gmtime
_real_ctime = _time.ctime
_real_asctime = _time.asctime
_real_strftime = _time.strftime
_real_datetime = _dt.datetime
_real_date = _dt.date

_offset = 0.0
_installed = False


def offset() -> float:
    return _offset


def now() -> float:
    return _real_time() + _offset


class _DateMeta(type(_real_date)):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _real_date)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _real_date)


class _DatetimeMeta(_DateMeta):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _real_datetime)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _real_datetime)


class FakeDate(_real_date, metaclass=_DateMeta):
    @classmethod
    def today(cls):
        t = _real_localtime(now())
        return _real_date(t.tm_year, t.tm_mon, t.tm_mday)


class FakeDatetime(_real_datetime, FakeDate, metaclass=_DatetimeMeta):
    @classmethod
    def now(cls, tz=None):
        return _real_datetime.fromtimestamp(now(), tz)

    @classmethod
    def utcnow(cls):
        return _real_datetime.fromtimestamp(now(), _dt.timezone.utc).replace(tzinfo=None)

    @classmethod
    def today(cls):
        return _real_datetime.fromtimestamp(now())


def install(seconds: float) -> None:
    """Shift this process's wall clock by `seconds`. Call before importing Harbor."""
    global _offset, _installed
    _offset = float(seconds)
    if _installed:
        return
    _installed = True
    _time.time = now
    _time.time_ns = lambda: _real_time_ns() + int(_offset * 1e9)
    _time.localtime = lambda secs=None: _real_localtime(now() if secs is None else secs)
    _time.gmtime = lambda secs=None: _real_gmtime(now() if secs is None else secs)
    _time.ctime = lambda secs=None: _real_ctime(now() if secs is None else secs)
    _time.asctime = lambda t=None: _real_asctime(_real_localtime(now()) if t is None else t)
    _time.strftime = lambda fmt, t=None: _real_strftime(fmt, _real_localtime(now()) if t is None else t)
    _dt.datetime = FakeDatetime
    _dt.date = FakeDate


def install_from_env() -> bool:
    raw = os.environ.get(OFFSET_ENV)
    if not raw:
        return False
    install(float(raw))
    return True


def offset_for(anchor_epoch: float, real_start_epoch: float) -> float:
    return float(anchor_epoch) - float(real_start_epoch)
