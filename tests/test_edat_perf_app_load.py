"""``edat-app-load``: allocation and touch logic in-process, and a short real child process."""
from __future__ import annotations

import json
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from edat.perf.app_load import HOT_FRACTION, AppLoad, AppLoadProcess, main
from edat.perf.constants import MIB

ROOT = Path(__file__).resolve().parents[1]


def test_allocation_is_incompressible_and_touch_covers_the_hot_set():
    mem = iter([(None, 10 * MIB), (20 * MIB, 12 * MIB)])         # resident before, then after
    p = AppLoadProcess(4 * MIB, 2 * MIB, page_bytes=16384, read_mem=lambda: next(mem), now_ns=lambda: 123)
    p.allocate()
    assert p.nbytes == 4 * MIB and p.hot_bytes == 2 * MIB
    chunk = bytes(p.buf[: 1 * MIB])
    assert len(zlib.compress(chunk, 6)) > 0.99 * len(chunk)        # random bytes: compression gains nothing
    assert p.touch_hot() == 128                                    # 2 MiB / 16 KiB pages, one byte each
    assert p.touch_passes == 1 and p.touch_ms_last is not None
    st = p.status()
    assert st["allocated_bytes"] == 4 * MIB and st["cold_bytes"] == 2 * MIB and st["ready"] is True
    assert st["phys_footprint_bytes"] == 20 * MIB and st["resident_bytes"] == 12 * MIB
    # estimate = allocated - (resident - resident before) = 4 - (12 - 10) = 2 MiB
    assert st["swapped_or_compressed_bytes_estimate"] == 2 * MIB
    assert st["swapped_or_compressed_is_estimate"] is True and st["estimate_basis"].startswith("allocated")
    p.close()


def test_sizes_round_to_pages_and_hot_set_is_capped():
    p = AppLoadProcess(100_000, 10**9, page_bytes=16384, read_mem=lambda: (None, None))
    assert p.nbytes == 7 * 16384 and p.hot_bytes == p.nbytes
    with pytest.raises(RuntimeError):
        p.touch_hot()
    with pytest.raises(ValueError):
        AppLoadProcess(0, 0)
    assert HOT_FRACTION == pytest.approx(0.6)


def test_cli_child_writes_status_and_exits(tmp_path):
    st = tmp_path / "status.json"
    rc = subprocess.run([sys.executable, "-m", "edat.perf.app_load", "--gib", "0.004", "--hot-gib", "0.002",
                         "--status", str(st), "--period-s", "0.2", "--max-seconds", "0.5"],
                        cwd=ROOT, timeout=30, capture_output=True)
    assert rc.returncode == 0, rc.stderr
    d = json.loads(st.read_text())
    assert d["schema"] == "edat.app-load-status/1" and d["ready"] is True and d.get("exiting") is True
    assert d["allocated_bytes"] >= int(0.004 * (1 << 30)) and d["touch_passes"] >= 1
    assert d["phys_footprint_bytes"] is None or d["phys_footprint_bytes"] > 0


def test_controller_starts_and_stops_only_its_child(tmp_path):
    al = AppLoad(tmp_path / "s.json", hot_gib=0.002, period_s=0.2, ready_timeout_s=30)
    st = al.start(0.004)
    assert st["ready"] is True and al.running
    pid = al.proc.pid
    assert isinstance(al.swapped_bytes(), int)
    assert al.status()["pid"] == pid
    rc = al.stop()
    assert rc == 0 and not al.running                             # SIGTERM handled: clean exit
    assert al.stop() is None                                      # nothing left to stop
    with pytest.raises(RuntimeError):
        AppLoad(tmp_path / "x.json", ready_timeout_s=5, extra_args=["--bogus"]).start(0.004)


def test_main_in_process_with_max_seconds(tmp_path):
    st = tmp_path / "m.json"
    assert main(["--gib", "0.002", "--status", str(st), "--period-s", "0.05", "--max-seconds", "0.1"]) == 0
    d = json.loads(st.read_text())
    assert d["hot_bytes"] == pytest.approx(0.002 * HOT_FRACTION * (1 << 30), rel=0.02)
