"""Power traces, the sampler, SMC decoding, battery and powermetrics parsers (SYNTHETIC inputs only)."""
from __future__ import annotations

import ctypes
import json
import plistlib
import struct
import threading
import time
from pathlib import Path

import pytest

from edat.perf import power
from edat.perf.power import (PowerSampler, PowerTrace, SMCKeyData, SmcPstrReader, TraceWriter,
                             decode_smc_value, full_charge_Wh, parse_ioreg_battery,
                             parse_powermetrics_plist, powermetrics_to_traces, read_battery, read_trace,
                             smc_key_to_int, smc_type_to_str, write_header)

FIX = Path(__file__).resolve().parent.parent / "test_fixtures" / "perf" / "power"
S = 1_000_000_000


# ---------------------------------------------------------------------------- trace files

def test_header_and_roundtrip(tmp_path):
    p = tmp_path / "t.jsonl"
    with TraceWriter(p, source="smc_pstr", rate_hz=20.0, note="SYNTHETIC") as w:
        w.append(300, 7.5)
        w.append(100, 5.0, rate_hz=1.0)
        w.marker(150, "hello")
        w.append(200, 6.0)
    head = json.loads(p.read_text().split("\n")[0])
    assert head == {"schema": "edat.power-samples/1", "source": "smc_pstr", "scope": "whole_device_internal",
                    "rate_hz": 20.0, "clock": "mach_continuous_time", "is_estimate": False, "note": "SYNTHETIC"}
    tr = read_trace(p)
    assert tr.t_ns == [100, 200, 300] and tr.watts == [5.0, 6.0, 7.5]      # sorted
    assert tr.markers == [(150, "hello")] and tr.rate_changes == [(100, 1.0)]
    assert (tr.source, tr.scope, tr.rate_hz, tr.is_estimate) == ("smc_pstr", "whole_device_internal", 20.0, False)
    assert not tr.truncated_tail
    s = tr.slice(150, 300)
    assert s.t_ns == [200, 300] and s.watts == [6.0, 7.5] and s.source == "smc_pstr"
    assert len(tr) == 3


def test_write_header_validates(tmp_path):
    with open(tmp_path / "x", "w") as fp:
        with pytest.raises(ValueError):
            write_header(fp, source="nope", scope="gpu_rail", rate_hz=1, is_estimate=False)
        with pytest.raises(ValueError):
            write_header(fp, source="smc_pstr", scope="nope", rate_hz=1, is_estimate=False)


def test_reader_tolerates_truncated_last_line_only(tmp_path):
    good = tmp_path / "g.jsonl"
    with TraceWriter(good, source="smc_pstr") as w:
        w.append(1, 1.0)
        w.append(2, 2.0)
    text = good.read_text()
    cut = tmp_path / "cut.jsonl"
    cut.write_text(text + '{"t_ns": 3, "wat')            # a crash mid-write
    tr = read_trace(cut)
    assert tr.t_ns == [1, 2] and tr.truncated_tail
    bad = tmp_path / "bad.jsonl"
    lines = text.split("\n")
    bad.write_text("\n".join([lines[0], "{broken", lines[1], lines[2]]) + "\n")
    with pytest.raises(ValueError):
        read_trace(bad)
    for content in ("", '{"schema": "other/1", "source": "smc_pstr", "scope": "gpu_rail"}\n'):
        (tmp_path / "e.jsonl").write_text(content)
        with pytest.raises(ValueError):
            read_trace(tmp_path / "e.jsonl")


def test_fixture_traces_are_labelled_synthetic():
    for name in ("synthetic_pstr_trace.jsonl", "synthetic_battery_trace.jsonl"):
        tr = read_trace(FIX / name)
        assert "SYNTHETIC" in tr.header["note"] and len(tr) >= 5


def test_trace_writer_flushes_every_250ms(tmp_path):
    clock = [0.0]
    p = tmp_path / "f.jsonl"
    w = TraceWriter(p, source="smc_pstr", monotonic=lambda: clock[0])
    w.append(1, 1.0)                                  # 0 s since the header flush: stays buffered
    assert len(p.read_text().strip().split("\n")) == 1
    clock[0] = 0.3
    w.append(2, 2.0)                                  # 300 ms since the last flush: flushed
    assert len(p.read_text().strip().split("\n")) == 3
    w.close()
    with pytest.raises(ValueError):
        w.append(3, 3.0)


def test_powertrace_sorts_and_checks_lengths():
    tr = PowerTrace("smc_pstr", "whole_device_internal", 1.0, False, [3, 1, 2], [30.0, 10.0, 20.0])
    assert tr.t_ns == [1, 2, 3] and tr.watts == [10.0, 20.0, 30.0]
    with pytest.raises(ValueError):
        PowerTrace("smc_pstr", "whole_device_internal", 1.0, False, [1], [])


# ---------------------------------------------------------------------------- sampler

class ListWriter:
    def __init__(self):
        self.rows, self.flushed = [], 0
        self.lock = threading.Lock()

    def append(self, t_ns, watts, **extra):
        with self.lock:
            self.rows.append((t_ns, watts, extra))

    def flush(self):
        self.flushed += 1


def test_sampler_stamps_and_counts_errors_deterministically():
    values = iter([5.0, RuntimeError("smc busy"), 6.0, float("nan"), 7.0])
    ticks = iter(range(1000, 100000, 1000))

    def read():
        v = next(values)
        if isinstance(v, Exception):
            raise v
        return v

    w = ListWriter()
    s = PowerSampler(read, w, now_ns=lambda: next(ticks), rate_hz=20.0)
    results = [s.sample_once() for _ in range(5)]
    assert results == [True, False, True, False, True]
    assert [r[1] for r in w.rows] == [5.0, 6.0, 7.0]
    assert s.n_samples == 3 and s.n_read_errors == 2 and len(s.gaps_t_ns) == 2
    assert "smc busy" in s.last_error or "non-finite" in s.last_error
    assert all(r[0] % 1000 == 0 for r in w.rows)


def test_sampler_rate_switch_marks_next_reading():
    w = ListWriter()
    s = PowerSampler(lambda: 1.0, w, now_ns=iter(range(10, 1000, 10)).__next__, rate_hz=20.0)
    s.sample_once()
    s.set_rate(1.0)
    s.sample_once()
    s.sample_once()
    s.set_rate(20.0)
    s.sample_once()
    assert [r[2] for r in w.rows] == [{}, {"rate_hz": 1.0}, {}, {"rate_hz": 20.0}]
    assert s.rate_hz == 20.0
    with pytest.raises(ValueError):
        s.set_rate(0)
    with pytest.raises(ValueError):
        PowerSampler(lambda: 1.0, w, now_ns=lambda: 0, rate_hz=-1)


def _wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def test_sampler_thread_runs_switches_rate_and_survives_errors(tmp_path):
    calls = [0]

    def read():
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("transient")
        return 3.0

    p = tmp_path / "s.jsonl"
    w = TraceWriter(p, source="smc_pstr")
    s = PowerSampler(read, w, now_ns=time.monotonic_ns, rate_hz=200.0)
    s.start()
    with pytest.raises(RuntimeError):
        s.start()
    assert _wait_for(lambda: s.n_samples >= 3)
    s.set_rate(100.0)
    assert _wait_for(lambda: s.rate_hz == 100.0 and s.n_samples >= 6)
    s.stop()
    w.close()
    n = s.n_samples
    time.sleep(0.05)
    assert s.n_samples == n                                   # really stopped
    assert s.n_read_errors == 1
    tr = read_trace(p)
    assert len(tr) == n and tr.t_ns == sorted(tr.t_ns)
    assert tr.rate_changes and tr.rate_changes[0][1] == 100.0


def test_sampler_thread_never_crashes_on_constant_errors():
    def read():
        raise OSError("always")

    s = PowerSampler(read, ListWriter(), now_ns=time.monotonic_ns, rate_hz=500.0)
    s.start()
    assert _wait_for(lambda: s.n_read_errors >= 3)
    s.stop()
    assert s.n_samples == 0


# ---------------------------------------------------------------------------- SMC (no hardware)

def test_smc_struct_and_decoding():
    assert ctypes.sizeof(SMCKeyData) == 80
    assert smc_key_to_int("PSTR") == 0x50535452 and smc_type_to_str(0x666C7420) == "flt "
    with pytest.raises(ValueError):
        smc_key_to_int("PST")
    assert decode_smc_value("flt ", struct.pack("<f", 12.5)) == 12.5
    assert decode_smc_value("sp78", bytes([0x0A, 0x80])) == 10.5            # 0x0A80 / 256
    assert decode_smc_value("sp78", bytes([0xFF, 0x00])) == -1.0            # signed
    assert decode_smc_value("fpe2", bytes([0x00, 0x32])) == 12.5            # 50 / 4
    with pytest.raises(ValueError):
        decode_smc_value("ui32", b"\0\0\0\0")
    with pytest.raises(ValueError):
        decode_smc_value("flt ", b"\0")


def test_smc_reader_touches_nothing_until_open():
    r = SmcPstrReader()
    assert r.UNVERIFIED and r.key == "PSTR"
    with pytest.raises(RuntimeError):
        r.read_watts()
    r.close()                                                               # safe when never opened


# ---------------------------------------------------------------------------- battery

def test_parse_ioreg_battery_fixture():
    info = parse_ioreg_battery((FIX / "synthetic_ioreg_battery.plist").read_bytes())
    assert info["voltage_mV"] == 12000 and info["amperage_mA"] == -1500
    # discharge: -(-1500 mA x 12000 mV) / 1e6 = +18 W
    assert info["watts"] == pytest.approx(18.0)
    assert info["max_capacity_mAh"] == 5000 and info["design_capacity_mAh"] == 5200
    assert info["nominal_capacity_mAh"] == 4900 and info["cycle_count"] == 100
    assert info["external_connected"] is False and info["is_charging"] is False
    assert info["missing_keys"] == []
    assert info["keys_used"]["amperage_mA"] == "InstantAmperage"
    assert info["keys_used"]["max_capacity_mAh"] == "AppleRawMaxCapacity"


def test_parse_ioreg_battery_charging_wrapped_and_missing():
    def doc(**kw):
        return plistlib.dumps([kw])
    chg = parse_ioreg_battery(doc(Voltage=12000, InstantAmperage=2000))
    assert chg["watts"] == pytest.approx(-24.0)                              # charging is negative
    wrapped = parse_ioreg_battery(doc(Voltage=10000, InstantAmperage=(1 << 64) - 1500))
    assert wrapped["amperage_mA"] == -1500 and wrapped["watts"] == pytest.approx(15.0)
    fallback = parse_ioreg_battery(doc(Voltage=10000, Amperage=-100, MaxCapacity=4000))
    assert fallback["keys_used"] == {"voltage_mV": "Voltage", "amperage_mA": "Amperage",
                                     "max_capacity_mAh": "MaxCapacity"}
    sparse = parse_ioreg_battery(doc(CycleCount=7))
    assert sparse["watts"] is None and sparse["cycle_count"] == 7
    assert {"voltage_mV", "amperage_mA", "max_capacity_mAh", "temperature"} <= set(sparse["missing_keys"])
    assert parse_ioreg_battery(plistlib.dumps({"Voltage": 11000, "Amperage": 0}))["watts"] == 0
    with pytest.raises(ValueError):
        parse_ioreg_battery(plistlib.dumps([]))


def test_full_charge_Wh():
    info = parse_ioreg_battery((FIX / "synthetic_ioreg_battery.plist").read_bytes())
    # 5000 mAh x 12.0 V / 1000 = 60 Wh (voltage is the instantaneous one here, flagged)
    r = full_charge_Wh(info)
    assert r["full_charge_Wh"] == pytest.approx(60.0) and "voltage_is_instantaneous" in r["flags"]
    assert r["keys_used"] == {"capacity": "AppleRawMaxCapacity", "voltage": "Voltage"}
    # nominal voltage given: 5000 x 11.4 / 1000 = 57 Wh
    r = full_charge_Wh(info, nominal_voltage_V=11.4)
    assert r["full_charge_Wh"] == pytest.approx(57.0) and r["keys_used"]["voltage"] == "argument"
    # MaxCapacity of 100 is a percentage: skipped, NominalChargeCapacity 4900 x 12 / 1000 = 58.8 Wh
    pct = parse_ioreg_battery(plistlib.dumps([{"Voltage": 12000, "MaxCapacity": 100, "NominalChargeCapacity": 4900}]))
    r = full_charge_Wh(pct)
    assert r["full_charge_Wh"] == pytest.approx(58.8) and "capacity_from_nominal_charge" in r["flags"]
    none = full_charge_Wh({"keys_used": {}})
    assert none["full_charge_Wh"] is None and "full_charge_unavailable" in none["flags"]


def test_read_battery_uses_injected_runner():
    seen = []

    class Done:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    def run_ok(argv, **kw):
        seen.append((argv, kw))
        return Done(0, (FIX / "synthetic_ioreg_battery.plist").read_bytes())

    info = read_battery(run_ok)
    assert info["watts"] == pytest.approx(18.0)
    assert seen[0][0] == ["/usr/sbin/ioreg", "-rn", "AppleSmartBattery", "-a"]
    assert seen[0][1]["capture_output"] is True
    with pytest.raises(RuntimeError):
        read_battery(lambda argv, **kw: Done(1, b""))
    reader = power.battery_watts_reader(run_ok)
    assert reader() == pytest.approx(18.0)


# ---------------------------------------------------------------------------- powermetrics

def test_parse_powermetrics_fixture():
    data = (FIX / "synthetic_powermetrics.bin").read_bytes()
    rows = parse_powermetrics_plist(data, start_t_ns=1000)
    assert [r["t_ns"] for r in rows] == [1000 + 100_000_000, 1000 + 200_000_000, 1000 + 300_000_000]
    assert [r["elapsed_ns"] for r in rows] == [100_000_000] * 3
    # mW -> W
    assert [r["cpu_W"] for r in rows] == [1.0, 1.5, 2.0]
    assert [r["gpu_W"] for r in rows] == [2.0, 2.5, 3.0]
    assert [r["ane_W"] for r in rows] == [0.0, 0.0, 0.5]
    assert [r["combined_W"] for r in rows] == [3.0, 4.0, 5.5]
    assert [r["thermal_pressure"] for r in rows] == ["Nominal", "Nominal", "Fair"]
    assert all(r["missing"] == [] for r in rows)


def test_parse_powermetrics_truncated_tail_and_garbage():
    data = (FIX / "synthetic_powermetrics.bin").read_bytes()
    docs = [d for d in data.split(b"\x00") if d]
    cut = b"\x00".join(docs[:2]) + b"\x00" + docs[2][: len(docs[2]) // 2]    # third document cut off
    rows = parse_powermetrics_plist(cut, start_t_ns=0)
    assert len(rows) == 2 and rows[-1]["t_ns"] == 200_000_000
    assert parse_powermetrics_plist(b"", start_t_ns=0) == []
    assert parse_powermetrics_plist(b"\x00\x00garbage\x00", start_t_ns=0) == []


def test_parse_powermetrics_missing_keys_and_energy_fallback():
    sparse = plistlib.dumps({"elapsed_ns": 100_000_000, "processor": {"cpu_power": 2000.0}})
    energy = plistlib.dumps({"elapsed_ns": 100_000_000, "processor": {"cpu_energy": 150.0}})
    no_elapsed = plistlib.dumps({"processor": {"cpu_power": 500.0}})
    rows = parse_powermetrics_plist(b"\x00".join([sparse, energy, no_elapsed]), start_t_ns=0)
    assert rows[0]["cpu_W"] == 2.0 and rows[0]["gpu_W"] is None
    assert {"gpu_W", "ane_W", "combined_W", "thermal_pressure"} <= set(rows[0]["missing"])
    # 150 mJ over 0.1 s = 1.5 W
    assert rows[1]["cpu_W"] == pytest.approx(1.5)
    assert "elapsed_ns" in rows[2]["missing"] and rows[2]["t_ns"] == 200_000_000


def test_powermetrics_to_traces():
    rows = parse_powermetrics_plist((FIX / "synthetic_powermetrics.bin").read_bytes(), start_t_ns=0)
    traces = powermetrics_to_traces(rows)
    assert set(traces) == {"cpu_W", "gpu_W", "ane_W", "combined_W"}
    g = traces["gpu_W"]
    assert (g.source, g.scope, g.is_estimate) == ("powermetrics", "soc_rails", True)
    assert g.rate_hz == pytest.approx(10.0)                                  # 100 ms interval
    assert g.t_ns == [100_000_000, 200_000_000, 300_000_000] and g.watts == [2.0, 2.5, 3.0]
    assert powermetrics_to_traces([]) == {}
