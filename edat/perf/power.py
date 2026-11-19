"""Power samples in and out: trace files, the sampler thread, and the Mac instruments' parsers.

Trace file format (``edat.power-samples/1``, JSONL)
---------------------------------------------------
Line 1 is a header::

    {"schema": "edat.power-samples/1", "source": <one of ENERGY_SOURCES>,
     "scope": <one of ENERGY_SCOPES>, "rate_hz": <float>,
     "clock": "mach_continuous_time", "is_estimate": <bool>}

(extra header keys such as ``note`` are allowed). Every later line is one reading::

    {"t_ns": <int, mach_continuous_time in ns>, "watts": <float>}

A reading may carry extra keys: ``rate_hz`` (the sampler switched rate at this reading, e.g.
20 Hz to 1 Hz, spec 04 section 9.1) and ``marker`` (free text). A line with a ``marker`` and no
``watts`` is a pure marker. A truncated last line (a crash mid-write) is tolerated by the reader.

Instruments (spec 04 sections 4.3 and 9.1)
------------------------------------------
* ``SmcPstrReader``: SMC key ``PSTR`` through IOKit. UNVERIFIED on this macOS; never run in tests.
* AppleSmartBattery accumulator through ``ioreg -rn AppleSmartBattery -a``. Key names are
  UNVERIFIED (spec 04 sections 9.1 and 16), so the parser looks for several candidates.
* ``powermetrics -f plist`` output (NUL-separated plist documents). Key names are UNVERIFIED.

Nothing here runs ``powermetrics``, ``sudo`` or anything that changes settings. Importing this module
has no side effects.
"""
from __future__ import annotations

import ctypes
import json
import math
import plistlib
import statistics
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from edat.perf.constants import (CLOCK, ENERGY_SCOPES, ENERGY_SOURCES, POWER_SAMPLE_SCHEMA,
                                 PSTR_RATE_HZ, SOURCE_SCOPE)

FLUSH_INTERVAL_S = 0.25


# --------------------------------------------------------------------------- traces

@dataclass
class PowerTrace:
    """A time-ordered series of (t_ns, watts) readings from one source."""
    source: str
    scope: str
    rate_hz: float
    is_estimate: bool
    t_ns: list[int] = field(default_factory=list)
    watts: list[float] = field(default_factory=list)
    header: dict[str, Any] = field(default_factory=dict)
    markers: list[tuple[int, str]] = field(default_factory=list)
    rate_changes: list[tuple[int, float]] = field(default_factory=list)
    truncated_tail: bool = False

    def __post_init__(self) -> None:
        if len(self.t_ns) != len(self.watts):
            raise ValueError("t_ns and watts differ in length")
        if any(b < a for a, b in zip(self.t_ns, self.t_ns[1:])):
            order = sorted(range(len(self.t_ns)), key=self.t_ns.__getitem__)
            self.t_ns = [self.t_ns[i] for i in order]
            self.watts = [self.watts[i] for i in order]

    def __len__(self) -> int:
        return len(self.t_ns)

    def slice(self, t0_ns: int, t1_ns: int) -> "PowerTrace":
        """Readings with t0_ns <= t <= t1_ns (inclusive), no interpolation."""
        keep = [i for i, t in enumerate(self.t_ns) if t0_ns <= t <= t1_ns]
        return PowerTrace(self.source, self.scope, self.rate_hz, self.is_estimate,
                          [self.t_ns[i] for i in keep], [self.watts[i] for i in keep],
                          dict(self.header),
                          [m for m in self.markers if t0_ns <= m[0] <= t1_ns],
                          [r for r in self.rate_changes if t0_ns <= r[0] <= t1_ns])


def header_dict(*, source: str, scope: str, rate_hz: float, is_estimate: bool,
                **extra: Any) -> dict[str, Any]:
    if source not in ENERGY_SOURCES:
        raise ValueError(f"unknown energy source {source!r}")
    if scope not in ENERGY_SCOPES:
        raise ValueError(f"unknown energy scope {scope!r}")
    head = {"schema": POWER_SAMPLE_SCHEMA, "source": source, "scope": scope,
            "rate_hz": float(rate_hz), "clock": CLOCK, "is_estimate": bool(is_estimate)}
    head.update(extra)
    return head


def write_header(fp, *, source: str, scope: str, rate_hz: float, is_estimate: bool,
                 **extra: Any) -> dict[str, Any]:
    """Write the header line to an open text file and return the header dict."""
    head = header_dict(source=source, scope=scope, rate_hz=rate_hz, is_estimate=is_estimate, **extra)
    fp.write(json.dumps(head, sort_keys=False) + "\n")
    return head


def read_trace(path: str | Path) -> PowerTrace:
    """Read a trace file. A truncated last line is dropped (``truncated_tail`` is set);
    a malformed line anywhere else raises ValueError."""
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise ValueError("empty trace file")
    try:
        head = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"bad trace header: {exc}") from exc
    if head.get("schema") != POWER_SAMPLE_SCHEMA:
        raise ValueError(f"unexpected trace schema {head.get('schema')!r}")
    if head.get("source") not in ENERGY_SOURCES or head.get("scope") not in ENERGY_SCOPES:
        raise ValueError("trace header has an unknown source or scope")
    ts: list[int] = []
    ws: list[float] = []
    markers: list[tuple[int, str]] = []
    rates: list[tuple[int, float]] = []
    truncated = False
    last = len(lines) - 1
    for i, line in enumerate(lines[1:], start=1):
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict) or "t_ns" not in rec:
                raise ValueError("reading without t_ns")
            t = int(rec["t_ns"])
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            if i == last:
                truncated = True
                break
            raise ValueError(f"bad trace line {i + 1}: {exc}") from exc
        if "marker" in rec:
            markers.append((t, str(rec["marker"])))
        if "rate_hz" in rec:
            rates.append((t, float(rec["rate_hz"])))
        if "watts" in rec:
            ts.append(t)
            ws.append(float(rec["watts"]))
    return PowerTrace(head["source"], head["scope"], float(head.get("rate_hz", 0.0)),
                      bool(head.get("is_estimate", False)), ts, ws, head, markers, rates, truncated)


class TraceWriter:
    """Appends readings to a trace file, flushing at least every 250 ms. Thread-safe."""

    def __init__(self, path: str | Path, *, source: str, scope: str | None = None,
                 rate_hz: float = PSTR_RATE_HZ, is_estimate: bool = False,
                 flush_interval_s: float = FLUSH_INTERVAL_S,
                 monotonic: Callable[[], float] = time.monotonic, **header_extra: Any) -> None:
        self.path = Path(path)
        self._interval = flush_interval_s
        self._mono = monotonic
        self._lock = threading.Lock()
        self._fp = open(self.path, "w", encoding="utf-8")
        self.header = write_header(self._fp, source=source, scope=scope or SOURCE_SCOPE[source],
                                   rate_hz=rate_hz, is_estimate=is_estimate, **header_extra)
        self._fp.flush()
        self._last_flush = self._mono()

    def append(self, t_ns: int, watts: float, **extra: Any) -> None:
        rec: dict[str, Any] = {"t_ns": int(t_ns), "watts": float(watts)}
        rec.update(extra)
        self._write(rec)

    def marker(self, t_ns: int, text: str) -> None:
        self._write({"t_ns": int(t_ns), "marker": text})

    def _write(self, rec: dict[str, Any]) -> None:
        with self._lock:
            if self._fp is None:
                raise ValueError("trace writer is closed")
            self._fp.write(json.dumps(rec) + "\n")
            if self._mono() - self._last_flush >= self._interval:
                self._fp.flush()
                self._last_flush = self._mono()

    def flush(self) -> None:
        with self._lock:
            if self._fp is not None:
                self._fp.flush()
                self._last_flush = self._mono()

    def close(self) -> None:
        with self._lock:
            if self._fp is not None:
                self._fp.flush()
                self._fp.close()
                self._fp = None

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- sampler

class PowerSampler:
    """Calls ``read_watts()`` at ``rate_hz`` on a thread and appends stamped readings to ``writer``.

    ``now_ns`` is any callable returning mach_continuous_time ns (the integrator passes
    ``edat.perf.clock.now_ns``). The rate may be switched at runtime (20 Hz during events, 1 Hz in idle
    windows, spec 04 section 9.1); the first reading written after a switch carries ``rate_hz``. A
    reading whose read began before the switch is not marked, and a failed read or write leaves the
    mark for the next reading. A read error (exception or a non-finite value) is counted as a gap and
    never stops the run.
    """

    def __init__(self, read_watts: Callable[[], float], writer: Any, *,
                 now_ns: Callable[[], int], rate_hz: float = PSTR_RATE_HZ,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        self._read = read_watts
        self._writer = writer
        self._now_ns = now_ns
        self._mono = monotonic
        self._rate = float(rate_hz)
        self._rate_pending: tuple[float] | None = None   # a fresh tuple per switch, compared by identity
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self.n_samples = 0
        self.n_read_errors = 0
        self.n_write_errors = 0
        self.gaps_t_ns: list[int] = []
        self.last_error: str | None = None

    @property
    def rate_hz(self) -> float:
        return self._rate

    def set_rate(self, rate_hz: float) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        with self._cond:
            self._rate = float(rate_hz)
            self._rate_pending = (float(rate_hz),)
            self._cond.notify_all()

    def sample_once(self) -> bool:
        """One read and one write. Returns True when a reading was written."""
        with self._cond:
            pending = self._rate_pending      # only a switch made before this read is marked on it
        try:
            value = float(self._read())
            t = int(self._now_ns())
            if not math.isfinite(value):
                raise ValueError(f"non-finite reading {value!r}")
        except Exception as exc:  # noqa: BLE001 - a bad read is a gap, not a crash
            self.n_read_errors += 1
            self.last_error = repr(exc)
            try:
                self.gaps_t_ns.append(int(self._now_ns()))
            except Exception:  # noqa: BLE001
                pass
            return False
        extra: dict[str, Any] = {} if pending is None else {"rate_hz": pending[0]}
        try:
            self._writer.append(t, value, **extra)
        except Exception as exc:  # noqa: BLE001
            self.n_write_errors += 1
            self.last_error = repr(exc)
            return False                      # the mark stays pending for the next reading
        if pending is not None:
            with self._cond:
                if self._rate_pending is pending:   # a later switch keeps its own mark
                    self._rate_pending = None
        self.n_samples += 1
        return True

    def _run(self) -> None:
        next_t = self._mono()
        last_rate = self._rate
        while True:
            with self._cond:
                if self._stopping:
                    return
            self.sample_once()
            with self._cond:
                if self._rate != last_rate:
                    last_rate = self._rate
                    next_t = self._mono()
                next_t += 1.0 / self._rate
                delay = next_t - self._mono()
                if delay < 0:
                    next_t = self._mono()   # fell behind: do not burst to catch up
                    delay = 0.0
                if delay > 0 and not self._stopping:
                    self._cond.wait(timeout=delay)
                if self._stopping:
                    return

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("sampler already started")
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="edat-power-sampler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        flush = getattr(self._writer, "flush", None)
        if callable(flush):
            flush()


# --------------------------------------------------------------------------- SMC PSTR

class _SMCVersion(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint8), ("minor", ctypes.c_uint8), ("build", ctypes.c_uint8),
                ("reserved", ctypes.c_uint8), ("release", ctypes.c_uint16)]


class _SMCPLimitData(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16),
                ("cpuPLimit", ctypes.c_uint32), ("gpuPLimit", ctypes.c_uint32),
                ("memPLimit", ctypes.c_uint32)]


class _SMCKeyInfoData(ctypes.Structure):
    _fields_ = [("dataSize", ctypes.c_uint32), ("dataType", ctypes.c_uint32),
                ("dataAttributes", ctypes.c_uint8)]


class SMCKeyData(ctypes.Structure):
    """The standard 80-byte SMC call structure."""
    _fields_ = [("key", ctypes.c_uint32), ("vers", _SMCVersion), ("pLimitData", _SMCPLimitData),
                ("keyInfo", _SMCKeyInfoData), ("result", ctypes.c_uint8), ("status", ctypes.c_uint8),
                ("data8", ctypes.c_uint8), ("data32", ctypes.c_uint32),
                ("bytes", ctypes.c_uint8 * 32)]


SMC_SELECTOR = 2          # kSMCHandleYPCEvent
SMC_CMD_READ_BYTES = 5
SMC_CMD_READ_KEYINFO = 9
_IOKIT_PATH = "/System/Library/Frameworks/IOKit.framework/IOKit"
_LIBSYSTEM_PATH = "/usr/lib/libSystem.B.dylib"


def smc_key_to_int(key: str) -> int:
    raw = key.encode("ascii")
    if len(raw) != 4:
        raise ValueError("an SMC key is four characters")
    return int.from_bytes(raw, "big")


def smc_type_to_str(code: int) -> str:
    return code.to_bytes(4, "big").decode("ascii", errors="replace")


def decode_smc_value(type_str: str, raw: bytes) -> float:
    """Decode an SMC value to a float. ``flt `` is little-endian float32 on Apple silicon
    (UNVERIFIED on this macOS); ``sp78`` is signed 8.8 big-endian; ``fpe2`` is unsigned 14.2 big-endian."""
    if type_str == "flt ":
        if len(raw) < 4:
            raise ValueError("flt value needs 4 bytes")
        return struct.unpack("<f", bytes(raw[:4]))[0]
    if type_str == "sp78":
        return int.from_bytes(bytes(raw[:2]), "big", signed=True) / 256.0
    if type_str == "fpe2":
        return int.from_bytes(bytes(raw[:2]), "big", signed=False) / 4.0
    raise ValueError(f"unsupported SMC type {type_str!r}")


class SmcPstrReader:
    """Reads the SMC key ``PSTR`` (whole-device system power, watts) through IOKit and ctypes.

    UNVERIFIED on this macOS: the AppleSMC service, selector 2 and the key's type are the commonly
    documented ones and have not been run against this machine by this module. It is not exercised by
    any test. Constructing it does not touch hardware; ``open()`` does. Callable as ``read_watts``.
    """
    UNVERIFIED = True

    def __init__(self, key: str = "PSTR") -> None:
        self.key = key
        self._iokit: Any = None
        self._conn = 0
        self._info: tuple[int, str] | None = None

    def open(self) -> "SmcPstrReader":
        iokit = ctypes.CDLL(_IOKIT_PATH)
        libsys = ctypes.CDLL(_LIBSYSTEM_PATH)
        iokit.IOServiceMatching.restype = ctypes.c_void_p
        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
        iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        iokit.IOServiceOpen.restype = ctypes.c_int
        iokit.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                        ctypes.POINTER(ctypes.c_uint32)]
        iokit.IOConnectCallStructMethod.restype = ctypes.c_int
        iokit.IOConnectCallStructMethod.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                                    ctypes.c_size_t, ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_size_t)]
        iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
        iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
        service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
        if not service:
            raise OSError("AppleSMC service not found")
        task = ctypes.c_uint32.in_dll(libsys, "mach_task_self_").value
        conn = ctypes.c_uint32(0)
        rc = iokit.IOServiceOpen(service, task, 0, ctypes.byref(conn))
        iokit.IOObjectRelease(service)
        if rc != 0:
            raise OSError(f"IOServiceOpen failed: {rc}")
        self._iokit, self._conn = iokit, conn.value
        return self

    def close(self) -> None:
        if self._iokit is not None and self._conn:
            self._iokit.IOServiceClose(self._conn)
        self._iokit, self._conn, self._info = None, 0, None

    def _call(self, inp: SMCKeyData) -> SMCKeyData:
        out = SMCKeyData()
        size = ctypes.c_size_t(ctypes.sizeof(SMCKeyData))
        rc = self._iokit.IOConnectCallStructMethod(self._conn, SMC_SELECTOR, ctypes.byref(inp),
                                                   ctypes.sizeof(SMCKeyData), ctypes.byref(out),
                                                   ctypes.byref(size))
        if rc != 0 or out.result != 0:
            raise OSError(f"SMC call failed: kern {rc}, result {out.result}")
        return out

    def read_watts(self) -> float:
        if self._iokit is None:
            raise RuntimeError("SmcPstrReader.open() has not been called")
        if self._info is None:
            req = SMCKeyData()
            req.key = smc_key_to_int(self.key)
            req.data8 = SMC_CMD_READ_KEYINFO
            info = self._call(req).keyInfo
            self._info = (info.dataSize, smc_type_to_str(info.dataType))
        size, type_str = self._info
        req = SMCKeyData()
        req.key = smc_key_to_int(self.key)
        req.keyInfo.dataSize = size
        req.data8 = SMC_CMD_READ_BYTES
        out = self._call(req)
        return decode_smc_value(type_str, bytes(out.bytes[:size]))

    __call__ = read_watts

    def __enter__(self) -> "SmcPstrReader":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- battery

IOREG_BATTERY_ARGV = ("/usr/sbin/ioreg", "-rn", "AppleSmartBattery", "-a")

# normalised name -> candidate registry keys, best first (all UNVERIFIED, spec 04 sections 9.1, 16)
_BATTERY_KEYS: dict[str, tuple[str, ...]] = {
    "voltage_mV": ("Voltage", "AppleRawBatteryVoltage"),
    "amperage_mA": ("InstantAmperage", "Amperage"),
    "current_capacity_mAh": ("AppleRawCurrentCapacity", "CurrentCapacity"),
    "max_capacity_mAh": ("AppleRawMaxCapacity", "MaxCapacity"),
    "nominal_capacity_mAh": ("NominalChargeCapacity",),
    "design_capacity_mAh": ("DesignCapacity",),
    "cycle_count": ("CycleCount",),
    "external_connected": ("ExternalConnected",),
    "is_charging": ("IsCharging",),
    "temperature": ("Temperature",),
}


def _signed64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def parse_ioreg_battery(plist_bytes: bytes) -> dict[str, Any]:
    """Parse ``ioreg -rn AppleSmartBattery -a`` output (a plist array of one dict).

    Returns normalised fields for whatever was found, ``keys_used`` (normalised name -> registry key),
    ``missing_keys`` and ``watts`` = V x I with discharge positive (None if V or I is missing).
    Unsigned 64-bit wrapped amperage is converted to signed.
    """
    doc = plistlib.loads(plist_bytes)
    if isinstance(doc, list):
        if not doc:
            raise ValueError("ioreg returned no AppleSmartBattery entry")
        doc = doc[0]
    if not isinstance(doc, dict):
        raise ValueError("unexpected ioreg plist shape")
    out: dict[str, Any] = {}
    used: dict[str, str] = {}
    missing: list[str] = []
    for name, candidates in _BATTERY_KEYS.items():
        for key in candidates:
            if key in doc and doc[key] is not None:
                val = doc[key]
                if name == "amperage_mA" and isinstance(val, int):
                    val = _signed64(val)
                out[name] = val
                used[name] = key
                break
        else:
            missing.append(name)
    out["keys_used"] = used
    out["missing_keys"] = missing
    v, i = out.get("voltage_mV"), out.get("amperage_mA")
    out["watts"] = None if v is None or i is None else -(i * v) / 1e6
    return out


def full_charge_Wh(info: dict[str, Any], *, nominal_voltage_V: float | None = None) -> dict[str, Any]:
    """Full-charge capacity = max capacity (mAh) x nominal voltage (V) / 1000 (spec 04 section 9.1).

    Capacity comes from ``max_capacity_mAh`` (a value of 100 or less is a percentage, not mAh, and is
    skipped) else ``nominal_capacity_mAh``. The voltage is ``nominal_voltage_V`` if given, else the
    instantaneous pack voltage (flag ``voltage_is_instantaneous``). Returns the keys used.
    """
    flags: list[str] = []
    used = dict(info.get("keys_used", {}))
    cap, cap_name = None, None
    for name in ("max_capacity_mAh", "nominal_capacity_mAh"):
        v = info.get(name)
        if isinstance(v, (int, float)) and v > 100:
            cap, cap_name = float(v), name
            break
    if cap_name == "nominal_capacity_mAh":
        flags.append("capacity_from_nominal_charge")
    if nominal_voltage_V is not None:
        volts, volt_src = float(nominal_voltage_V), "argument"
    elif isinstance(info.get("voltage_mV"), (int, float)):
        volts, volt_src = info["voltage_mV"] / 1000.0, used.get("voltage_mV", "voltage_mV")
        flags.append("voltage_is_instantaneous")
    else:
        volts, volt_src = None, None
    if cap is None or volts is None:
        return {"full_charge_Wh": None, "capacity_mAh": cap, "voltage_V": volts,
                "keys_used": {}, "flags": flags + ["full_charge_unavailable"]}
    return {"full_charge_Wh": cap * volts / 1000.0, "capacity_mAh": cap, "voltage_V": volts,
            "keys_used": {"capacity": used.get(cap_name, cap_name), "voltage": volt_src},
            "flags": flags}


def read_battery(run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Run the read-only ioreg query through an injectable runner and parse it."""
    proc = run(list(IOREG_BATTERY_ARGV), capture_output=True, timeout=10, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ioreg exited {proc.returncode}")
    return parse_ioreg_battery(proc.stdout)


def battery_watts_reader(run: Callable[..., Any] = subprocess.run) -> Callable[[], float]:
    """A ``read_watts`` for PowerSampler backed by the battery accumulator (discharge positive)."""
    def read() -> float:
        w = read_battery(run)["watts"]
        if w is None:
            raise ValueError("battery voltage or amperage missing")
        return float(w)
    return read


# --------------------------------------------------------------------------- powermetrics

_PM_POWER_KEYS = {"cpu_W": "cpu_power", "gpu_W": "gpu_power", "ane_W": "ane_power",
                  "combined_W": "combined_power"}
_PM_ENERGY_KEYS = {"cpu_W": "cpu_energy", "gpu_W": "gpu_energy", "ane_W": "ane_energy",
                   "combined_W": "combined_energy"}


def _pm_watts(doc: dict, name: str, elapsed_ns: int | None) -> float | None:
    sections = [doc.get("processor"), doc.get("gpu"), doc]
    for sec in sections:
        if isinstance(sec, dict) and isinstance(sec.get(_PM_POWER_KEYS[name]), (int, float)):
            return sec[_PM_POWER_KEYS[name]] / 1000.0           # mW -> W
    if elapsed_ns:
        for sec in sections:
            if isinstance(sec, dict) and isinstance(sec.get(_PM_ENERGY_KEYS[name]), (int, float)):
                return sec[_PM_ENERGY_KEYS[name]] / 1000.0 / (elapsed_ns / 1e9)   # mJ -> J over interval
    return None


def parse_powermetrics_plist(data: bytes, *, start_t_ns: int) -> list[dict[str, Any]]:
    """Parse a ``powermetrics -f plist`` stream (NUL-separated plist documents; a trailing partial
    document is dropped). Sample i ends at ``start_t_ns`` + the cumulative ``elapsed_ns``.
    Key names are UNVERIFIED, so each sample lists what it lacked in ``missing``."""
    out: list[dict[str, Any]] = []
    cumulative = 0
    for chunk in data.split(b"\x00"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            doc = plistlib.loads(chunk)
        except Exception:  # noqa: BLE001 - truncated or garbled document
            continue
        if not isinstance(doc, dict):
            continue
        missing: list[str] = []
        elapsed = doc.get("elapsed_ns")
        if isinstance(elapsed, (int, float)):
            elapsed = int(elapsed)
        else:
            elapsed = None
            missing.append("elapsed_ns")
        cumulative += elapsed or 0
        row: dict[str, Any] = {"t_ns": start_t_ns + cumulative, "elapsed_ns": elapsed}
        for name in _PM_POWER_KEYS:
            row[name] = _pm_watts(doc, name, elapsed)
            if row[name] is None:
                missing.append(name)
        row["thermal_pressure"] = doc.get("thermal_pressure")
        if row["thermal_pressure"] is None:
            missing.append("thermal_pressure")
        row["missing"] = missing
        out.append(row)
    return out


def powermetrics_to_traces(samples: Iterable[dict[str, Any]]) -> dict[str, PowerTrace]:
    """One PowerTrace per rail (cpu_W, gpu_W, ane_W, combined_W), scope ``soc_rails``, is_estimate True
    (spec 04 section 3.8). A reading is the mean over the interval that ends at its timestamp."""
    samples = list(samples)
    elapsed = [s["elapsed_ns"] for s in samples if s.get("elapsed_ns")]
    rate = 1e9 / statistics.median(elapsed) if elapsed else 0.0
    traces: dict[str, PowerTrace] = {}
    for name in _PM_POWER_KEYS:
        pts = [(s["t_ns"], s[name]) for s in samples if s.get(name) is not None]
        if not pts:
            continue
        traces[name] = PowerTrace("powermetrics", SOURCE_SCOPE["powermetrics"], rate, True,
                                  [p[0] for p in pts], [float(p[1]) for p in pts])
    return traces
