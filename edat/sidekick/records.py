"""Results-store records for the sidekick tier (docs/benchmark/results-record.md
on build/results-store; schema `edat.result/1.0`).

One record per (entry, case, trial, mode). `tier: "sidekick"` is already in
the store's tier list; answer-only and tool mode are separate entries told
apart by `suite` (`answer_only` / `tool_mode`). Raw output (answer text,
tool calls, reasoning) goes into `raw/` by sha256, never into the record.
When `edat.results.writer.RunWriter` is importable it writes the run;
otherwise `LocalRunWriter` writes the same layout (records.jsonl,
manifest.json, raw/<sha256>.json) for the store to ingest.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from edat.sidekick import GENERATOR_VERSION, TIER

SCHEMA = "edat.result/1.0"
SUITES = ("answer_only", "tool_mode")

try:
    from edat.results.writer import RunWriter  # type: ignore
    HAVE_STORE = True
except Exception:  # noqa: BLE001 - not on this branch yet
    RunWriter = None
    HAVE_STORE = False


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ") + "-" + secrets.token_hex(2)


def harness_commit() -> tuple[str, bool]:
    here = Path(__file__).resolve().parent
    try:
        sha = subprocess.run(["git", "-C", str(here), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(here), "status", "--porcelain", "--", "."], capture_output=True,
                                    text=True, timeout=10).stdout.strip())
        return sha or "0000000", dirty
    except Exception:  # noqa: BLE001
        return "0000000", True


def results_root(split: str) -> Path:
    if split == "heldout":
        priv = os.environ.get("EDAT_PRIVATE_DIR")
        if not priv:
            raise RuntimeError("held-out runs need EDAT_PRIVATE_DIR (results stay private)")
        return Path(priv) / "results"
    return Path(os.environ.get("EDAT_RESULTS_DIR") or (Path.home() / ".edat" / "results"))


def make_record(*, run_id: str, board_version: str, entry: dict, case_meta: dict, trial: int, suite: str,
                grade: dict | None, status: str, status_detail: str | None, metrics: dict, events: list[dict],
                started_at: str, seed, raw_ref: dict | None, commit: str, dirty: bool) -> dict:
    model = dict(entry["model"])
    cloud = entry.get("kind") == "cloud_reference"
    leaks = sum(1 for e in events if e["kind"] == "leak")
    unsafe = len(events) - leaks
    rec = {
        "schema": SCHEMA, "kind": "cloud_reference" if cloud else "local", "tier": TIER, "suite": suite,
        "run_id": run_id, "board_version": board_version, "lang": "en", "model": model,
        "runtime": entry.get("runtime") or {"name": "stub"},
        "device": None if cloud else entry.get("device"),
        "settings": {"thinking": entry.get("thinking", "off"), "sampling": entry.get("sampling") or {},
                     "sampling_source": entry.get("sampling_source", "author_recommended"), "seed": seed,
                     "world_seed": None},
        "harness_commit": commit, "harness_dirty": dirty, "started_at": started_at,
        "case_id": case_meta["id"], "split": case_meta["split"], "trial": trial, "status": status,
        "facets": {"family": case_meta["type"], "category": case_meta["group"], "level": case_meta["level"],
                   "lang": case_meta["lang"], "variant": case_meta.get("variant") or None,
                   "tags": [t for t in ("core" if case_meta["core"] else "full", case_meta.get("restraint") and f"restraint:{case_meta['restraint']}") if t]},
        "metrics": metrics,
        "safety": {"unsafe_actions": unsafe, "privacy_leaks": leaks, "labels": sorted({e["kind"] for e in events})},
        "raw": raw_ref,
    }
    if cloud:
        rec["device_reason"] = "cloud"
    if status_detail:
        rec["status_detail"] = status_detail
    if raw_ref is None:
        rec["raw_reason"] = "not kept"
    return rec


class LocalRunWriter:
    """The results store's run layout, for use until edat.results is on main."""

    def __init__(self, root: Path, run_id: str, manifest: dict):
        self.dir = Path(root) / run_id
        (self.dir / "raw").mkdir(parents=True, exist_ok=True)
        self.manifest = manifest
        self.n = 0

    def put_raw(self, obj) -> dict:
        data = json.dumps(obj, sort_keys=True, default=str).encode()
        sha = hashlib.sha256(data).hexdigest()
        p = self.dir / "raw" / f"{sha}.json"
        if not p.exists():
            p.write_bytes(data)
        return {"path": f"raw/{sha}.json", "sha256": sha, "bytes": len(data)}

    def append(self, rec: dict) -> None:
        with (self.dir / "records.jsonl").open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        self.n += 1

    def close(self) -> None:
        m = dict(self.manifest)
        m["records"] = self.n
        (self.dir / "manifest.json").write_text(json.dumps(m, indent=1, sort_keys=True))


def manifest(run_id: str, *, board_version: str, entry: dict, run_key: dict) -> dict:
    return {"schema": "edat.run-manifest/1.0", "run_id": run_id, "tier": TIER, "board_version": board_version,
            "generator": GENERATOR_VERSION, "entry": {"model": entry["model"].get("id"), "thinking": entry.get("thinking")},
            "run_key": run_key, "created_at": utc_now()}
