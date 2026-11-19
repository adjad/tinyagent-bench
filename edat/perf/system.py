"""Read-only probes of the Mac's state: device, power source, thermal, disk reads
(spec 04 §3.2, §3.5, §6.4, §7, §11 items 8 and 11).

Every command here only reads. Each parser takes the command's text so it can be tested
without running anything; the ``SystemProbe`` methods run the commands through an
injectable ``run``. Output formats marked **unverified** have not been checked on macOS 27.
"""
from __future__ import annotations

import platform
import re
import subprocess
from typing import Callable

THERMAL_LEVELS = {0: "nominal", 1: "fair", 2: "serious", 3: "critical", 4: "critical"}


def parse_pmset_batt(text: str) -> dict:
    """``pmset -g batt``: "Now drawing from 'Battery Power'" and "… 91%; discharging; …"."""
    out: dict = {"power_source": None, "battery_level": None, "battery_state": None}
    m = re.search(r"drawing from '([^']+)'", text)
    if m:
        out["power_source"] = "battery" if "Battery" in m.group(1) else "ac"
    m = re.search(r"(\d+)%;\s*([a-zA-Z ]+?);", text)
    if m:
        out["battery_level"] = int(m.group(1)) / 100.0
        out["battery_state"] = m.group(2).strip()
    return out


def parse_pmset_settings(text: str) -> dict:
    """``pmset -g``: ``lowpowermode 0`` and ``powermode N`` (the Mac's energy mode)."""
    out: dict = {}
    m = re.search(r"^\s*lowpowermode\s+(\d+)", text, re.M)
    if m:
        out["low_power_mode"] = m.group(1) == "1"
    m = re.search(r"^\s*powermode\s+(\d+)", text, re.M)
    if m:
        out["power_mode"] = int(m.group(1))
    return out


def parse_thermal_notify(text: str) -> str | None:
    """``notifyutil -g com.apple.system.thermalpressurelevel`` → "<name> <level>". Levels 0–4
    map to nominal / fair / serious / critical (mapping **unverified**)."""
    m = re.search(r"(\d+)\s*$", text.strip())
    return THERMAL_LEVELS.get(int(m.group(1))) if m else None


def parse_iostat_mb(text: str) -> float | None:
    """``iostat -d -I`` (cumulative since boot): sum the ``MB`` column over disks.
    Layout: a disk-name line, a header line ``KB/t xfrs MB`` per disk, then numbers."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return None
    hdr = lines[1].split()
    vals = lines[2].split()
    total, found = 0.0, False
    for i, h in enumerate(hdr):
        if h == "MB" and i < len(vals):
            try:
                total += float(vals[i])
                found = True
            except ValueError:
                pass
    return total if found else None


def parse_boottime(text: str, now_s: float) -> float | None:
    """``sysctl -n kern.boottime`` → "{ sec = 1700000000, usec = 0 } …" → uptime seconds."""
    m = re.search(r"sec\s*=\s*(\d+)", text)
    return now_s - int(m.group(1)) if m else None


class SystemProbe:
    def __init__(self, run: Callable = subprocess.run):
        self.run = run

    def _out(self, argv: list[str]) -> str:
        try:
            return self.run(argv, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def sysctl(self, name: str) -> str:
        return self._out(["/usr/sbin/sysctl", "-n", name]).strip()

    def device(self) -> dict:
        def num(s: str) -> int | None:
            return int(s) if s.isdigit() else None
        wired = self.sysctl("iogpu.wired_limit_mb")
        return {"class": "mac-ref", "model_id": self.sysctl("hw.model") or None,
                "soc": self.sysctl("machdep.cpu.brand_string") or None,
                "ram_bytes": num(self.sysctl("hw.memsize")),
                "os": f"macOS {platform.mac_ver()[0]}" if platform.mac_ver()[0] else None,
                "os_build": self._out(["/usr/bin/sw_vers", "-buildVersion"]).strip() or None,
                "entitlements": None, "gpu_wired_limit_mb": num(wired),
                "battery_nominal_Wh": None, "battery_nominal_source": None, "battery_health_pct": None}

    def thermal(self) -> str:
        return parse_thermal_notify(self._out(["/usr/bin/notifyutil", "-g",
                                               "com.apple.system.thermalpressurelevel"])) or "nominal"

    def disk_read_mb(self) -> float | None:
        return parse_iostat_mb(self._out(["/usr/sbin/iostat", "-d", "-I"]))

    def conditions(self, *, display: str, attest: dict, ambient_C: float | None, now_s: float,
                   extra: dict | None = None) -> dict:
        batt = parse_pmset_batt(self._out(["/usr/bin/pmset", "-g", "batt"]))
        pm = parse_pmset_settings(self._out(["/usr/bin/pmset", "-g"]))
        c = {"power_source": batt["power_source"], "battery_level": batt["battery_level"],
             "battery_state": batt["battery_state"], "low_power_mode": pm.get("low_power_mode"),
             "power_mode": pm.get("power_mode"), "display": display, "brightness": None,
             "thermal_state": self.thermal(),
             "uptime_s": parse_boottime(self.sysctl("kern.boottime"), now_s),
             "ambient_C": ambient_C, "attest": attest, "foreign_gpu_work": None}
        if extra:
            c.update(extra)
        return c
