"""Writing the §3 run document, raw samples first (spec 04 §3).

The runner writes **raw samples only** (§2, §3.9): every request becomes one sample line
in ``samples.jsonl`` the moment it ends, and events go to ``events.jsonl``, so a crash or
an engine death loses at most the request in flight. :meth:`RunWriter.finalize` then
assembles ``run-<run_id>.json`` (``edat.perf/1.0``), with ``cells`` and ``verdicts`` left
out: the aggregator writes those.

Layout of one run directory::

    <out>/<run_id>/
        run-<run_id>.json        the §3 document (after finalize)
        samples.jsonl            raw samples, one per request (warm-ups included)
        events.jsonl             §3.7 events
        windows.jsonl            §3.8 energy windows
        mem-<run_id>.jsonl       20 Hz footprint ticks
        power-<run_id>-<source>.jsonl   power traces
        admin.sock, admin-audit.jsonl   the admin helper's socket and audit (when used)
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any, Iterable

from .constants import (CLOCK, EVENT_KINDS, PERF_SCHEMA, PHASES, RUNNER_NAME, RUNNER_VERSION, STATUSES,
                        TESTS)

# Every §3.6 field (plus §3.12 additions and this runner's additive fields), so a real
# record carries every field, null when not measured.
SAMPLE_FIELDS = (
    "sample_id", "model_id", "mode", "entry_id", "test", "phase", "round", "rep", "order_index",
    "level_tokens", "prompt_tokens", "cached_tokens", "variant", "shape", "step",
    "t_submit_ns", "t_prefill_done_ns", "t_first_token_ns", "t_first_answer_ns", "t_last_token_ns",
    "t_end_ns", "token_times_ns", "token_times_kind",
    "output_tokens", "reasoning_tokens", "drafted_tokens", "accepted_tokens", "load",
    "footprint_start_bytes", "footprint_peak_bytes", "footprint_peak_ledger_bytes",
    "footprint_steady_bytes", "footprint_end_bytes", "kernel_peak_before_bytes", "kernel_peak_after_bytes",
    "neural_bytes", "available_min_bytes", "swap_delta_bytes", "compressed_delta_bytes",
    "mlx_active_bytes", "mlx_cache_bytes", "mlx_peak_bytes",
    "thermal_start", "thermal_end", "cooldown_wait_s", "thermal_not_nominal", "memory_warning",
    "battery_level_start", "battery_level_end", "energy_window_id", "status", "error", "kill_evidence",
    # §3.12 (revision 6)
    "event_kind", "script_time_s", "task_id", "turn_index", "context_tokens", "new_prefill_tokens",
    "trim_event", "cache_reset", "reloaded", "reload_ms", "weights_cached", "aux_models_resident",
    "footprint_model_bytes", "aux", "wall_budget_s", "within_budget", "app_load_gib",
    "app_load_swapped_bytes",
    # additive, this runner
    "replay_cache", "prefill_timing_source", "server_timings", "finish_reason", "power_state",
    "completed",
)


def new_run_id(utc: str) -> str:
    """``<UTC start>-<4 hex>`` (§3.1), e.g. ``2026-12-01T10-15-00Z-7f3c``."""
    return utc.replace(":", "-") + "-" + secrets.token_hex(2)


def blank_sample(**kw: Any) -> dict:
    s = {k: None for k in SAMPLE_FIELDS}
    s.update(kw)
    return s


def check_sample(s: dict) -> None:
    if s.get("test") not in TESTS:
        raise ValueError(f"unknown test {s.get('test')!r}")
    if s.get("phase") not in PHASES:
        raise ValueError(f"unknown phase {s.get('phase')!r}")
    if s.get("status") not in STATUSES:
        raise ValueError(f"unknown status {s.get('status')!r}")


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _append(path: Path, obj: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            break  # a torn last line after a crash
    return out


class RunWriter:
    def __init__(self, out_dir: str | os.PathLike, run_id: str, *, started_utc: str):
        self.run_id = run_id
        self.dir = Path(out_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self.started_utc = started_utc
        self.samples_path = self.dir / "samples.jsonl"
        self.events_path = self.dir / "events.jsonl"
        self.windows_path = self.dir / "windows.jsonl"
        self.header: dict = {}
        self.attachments: dict[str, str] = {}   # name -> kind
        self._order = 0
        self._ids: set[str] = set()

    @property
    def mem_log(self) -> str:
        return str(self.dir / f"mem-{self.run_id}.jsonl")

    def power_log(self, source: str) -> str:
        return str(self.dir / f"power-{self.run_id}-{source}.jsonl")

    def next_order(self) -> int:
        self._order += 1
        return self._order

    def sample(self, s: dict) -> dict:
        check_sample(s)
        if s["sample_id"] in self._ids:
            raise ValueError(f"duplicate sample_id {s['sample_id']}")
        self._ids.add(s["sample_id"])
        _append(self.samples_path, s)
        return s

    def event(self, t_ns: int, kind: str, detail: Any) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        _append(self.events_path, {"t_ns": t_ns, "kind": kind, "detail": detail})

    def window(self, w: dict) -> None:
        _append(self.windows_path, w)

    def attach(self, name: str, kind: str) -> None:
        self.attachments[name] = kind

    def finalize(self, *, ended_utc: str, device: dict, runtime: dict, models: list[dict],
                 prompt_pack: dict, conditions_start: dict, conditions_end: dict,
                 extra: dict | None = None) -> Path:
        atts = []
        for name, kind in sorted(self.attachments.items()):
            p = self.dir / name
            if p.exists():
                atts.append({"name": name, "sha256": sha256_file(p), "kind": kind})
        doc = {
            "schema": PERF_SCHEMA, "kind": "local", "run_id": self.run_id,
            "runner": {"name": RUNNER_NAME, "version": RUNNER_VERSION, "commit": self.header.get("commit")},
            "clock": CLOCK, "started_utc": self.started_utc, "ended_utc": ended_utc,
            "device": device, "runtime": runtime, "models": models, "prompt_pack": prompt_pack,
            "conditions_start": conditions_start, "conditions_end": conditions_end,
            "samples": latest_per_id(read_jsonl(self.samples_path)), "events": read_jsonl(self.events_path),
            "energy_windows": read_jsonl(self.windows_path), "attachments": atts,
        }
        if extra:
            doc.update(extra)
        path = self.dir / f"run-{self.run_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(path)
        return path


def latest_per_id(samples: list[dict]) -> list[dict]:
    """Samples in first-written order, each id's LAST line winning (a field known only after
    the line was written, such as the load block, is appended as a replacement line)."""
    pos: dict[str, int] = {}
    out: list[dict] = []
    for s in samples:
        sid = s.get("sample_id")
        if sid in pos:
            out[pos[sid]] = s
        else:
            pos[sid] = len(out)
            out.append(s)
    return out


def iter_levels_samples(samples: Iterable[dict], test: str) -> Iterable[dict]:
    return (s for s in samples if s.get("test") == test)
