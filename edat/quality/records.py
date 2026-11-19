"""The quality tier's one record writer (shared envelope `edat.result/1.0`).

The results-store chat owns docs/benchmark/results-record.md and
`edat/results/writer.py`. This module builds records in that envelope and,
when `edat.results.writer.RunWriter` is importable, writes through it (schema
checks, locking, content-addressed raw files, manifest). Until that branch is
on `main`, a small fallback writes the same layout:

    <store>/runs/<run_id>/records.jsonl
    <store>/runs/<run_id>/raw/<sha256>.json
    <store>/runs/<run_id>/manifest.json   (fallback: a reduced manifest, marked so)

Nested score details (per-trap results, fabricated atoms, hygiene checks)
never go into `metrics`, which holds only flat scalars; they go into the raw
file next to the model output.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECORD_SCHEMA = "edat.result/1.0"
_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TAG = re.compile(r"[^a-z0-9_-]")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_store(split: str) -> Path:
    """Held-out records live only in the private store (results-record.md s6)."""
    if split == "heldout":
        p = os.environ.get("EDAT_PRIVATE_DIR")
        if not p:
            raise RuntimeError("held-out results need EDAT_PRIVATE_DIR (the private store)")
        return Path(p).expanduser() / "results"
    return Path(os.environ.get("EDAT_RESULTS_DIR") or Path.home() / ".edat" / "results").expanduser()


def flat_metrics(m: dict) -> tuple[dict, dict]:
    """Split a scorer dict into (flat scalar metrics, nested details)."""
    flat, details = {}, {}
    for k, v in m.items():
        name = re.sub(r"[^a-z0-9_]", "_", k.lower())
        if not _METRIC_NAME.match(name):
            details[k] = v
            continue
        if v is None or isinstance(v, (bool, int, float)):
            flat[name] = v
        elif isinstance(v, str) and len(v) <= 64:
            flat[name] = v
        else:
            details[k] = v
    return flat, details


def tag(s: str) -> str:
    t = _TAG.sub("_", s.lower())
    return t if t[:1].isalpha() else "t_" + t


def make_record(*, run_id: str, board_version: str, harness_commit: str, entry: dict, case_id: str, split: str,
                trial: int, status: str, metrics: dict, facets: dict, raw: dict | None, safety: dict,
                started_at: str | None = None, status_detail: str | None = None, suite: str = "quality",
                raw_reason: str | None = None, sample: bool | None = None) -> dict:
    """One `edat.result/1.0` record. `entry` carries model, runtime, device and settings."""
    rec: dict[str, Any] = {
        "schema": RECORD_SCHEMA, "kind": entry["kind"], "tier": "quality", "suite": suite, "run_id": run_id,
        "board_version": board_version, "lang": "en", "model": entry["model"], "runtime": entry["runtime"],
        "device": entry.get("device"), "settings": entry["settings_for_trial"](trial),
        "harness_commit": harness_commit, "started_at": started_at or utc_now(), "case_id": case_id,
        "split": split, "trial": trial, "status": status, "facets": facets, "metrics": metrics,
        "safety": safety, "raw": raw,
    }
    if entry.get("device") is None:
        rec["device_reason"] = "cloud"
    if status_detail:
        rec["status_detail"] = status_detail[:64]
    if raw is None:
        rec["raw_reason"] = raw_reason or "not_kept"
    if sample is not None:
        rec["sample"] = sample
    return rec


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class QualityRunSink:
    """Collects records and raw outputs for one run, then writes them in one place."""

    def __init__(self, *, run_id: str, store: Path | None = None, split: str = "dev", keep_in_memory: bool = True):
        self.run_id = run_id
        self.split = split
        self.store = Path(store) if store else None
        self.records: list[dict] = []
        self.raw: dict[str, bytes] = {}
        self.keep = keep_in_memory

    def add_raw(self, obj: dict) -> dict:
        data = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        sha = _sha(data)
        self.raw[sha] = data
        return {"path": f"raw/{sha}.json", "sha256": sha, "bytes": len(data)}

    def add(self, rec: dict) -> None:
        self.records.append(rec)

    def write(self) -> Path | None:
        """Write the run. Uses the shared writer when available."""
        if self.store is None:
            return None
        try:  # pragma: no cover - depends on the results-store branch being merged
            from edat.results.writer import RunWriter  # type: ignore
        except Exception:  # noqa: BLE001
            RunWriter = None
        if RunWriter is not None:  # pragma: no cover
            with RunWriter.open(self.store, self.run_id, tier="quality", suite="quality") as w:
                for rec in self.records:
                    if rec.get("raw"):
                        rec = dict(rec, raw=w.put_raw(json.loads(self.raw[rec["raw"]["sha256"]])))
                    w.append(rec)
            return self.store / "runs" / self.run_id
        run_dir = self.store / "runs" / self.run_id
        (run_dir / "raw").mkdir(parents=True, exist_ok=True)
        for sha, data in self.raw.items():
            p = run_dir / "raw" / f"{sha}.json"
            if not p.exists():
                p.write_bytes(data)
        lines = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
                        for r in self.records)
        (run_dir / "records.jsonl").write_text(lines, encoding="utf-8")
        manifest = {"schema": "edat.run-manifest/1.0", "run_id": self.run_id, "tier": "quality", "suite": "quality",
                    "kind": self.records[0]["kind"] if self.records else "local",
                    "board_version": self.records[0]["board_version"] if self.records else "",
                    "harness_commit": self.records[0]["harness_commit"] if self.records else "0000000",
                    "created_at": utc_now(), "updated_at": utc_now(),
                    "records": {"path": "records.jsonl", "count": len(self.records),
                                "sha256": _sha(lines.encode())},
                    "raw": {"dir": "raw", "count": len(self.raw)},
                    "x_writer": "edat.quality.records fallback (results-store writer not on this branch)"}
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return run_dir


def read_records(path: Path) -> list[dict]:
    p = Path(path)
    if p.is_dir():
        p = p / "records.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
