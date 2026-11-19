"""Results-store records for the sidekick tier (docs/benchmark/results-record.md;
schema `edat.result/1.0`).

One record per (entry, case, trial, mode). `tier: "sidekick"` is already in
the store's tier list; answer-only and tool mode are separate entries told
apart by `suite` (`answer_only` / `tool_mode`). Raw output (answer text,
tool calls, reasoning) goes into `raw/` by sha256, never into the record.
The runner writes every record through the results store's `RunWriter`
(`edat/results/writer.py`), which validates it first.
"""
from __future__ import annotations

import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from edat.results.writer import default_store
from edat.sidekick import TIER
from edat.sidekick.labels import NOT_HARDENED_TAG, may_score

SCHEMA = "edat.result/1.0"
SUITES = ("answer_only", "tool_mode")

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
    return default_store()


def is_hardened(rec: dict) -> bool:
    """labels.may_score: the one decision on which rows may be scored."""
    return may_score(rec)


def case_tags(case_meta: dict) -> list[str]:
    """Facets the store has no key for go in tags (pattern ^[a-z][a-z0-9_-]*$)."""
    tags = ["core" if case_meta["core"] else "full", f"level-{case_meta['level'].lower()}",
            f"lang-{case_meta['lang']}"]
    if case_meta.get("variant"):
        tags.append(f"variant-{case_meta['variant']}")
    if case_meta.get("restraint"):
        tags.append(f"restraint-{case_meta['restraint']}")
    if case_meta.get("tool_mode"):
        tags.append("tool-mode-case")
    return tags


def make_record(*, run_id: str, board_version: str, entry: dict, case_meta: dict, trial: int, suite: str,
                grade: dict | None, status: str, status_detail: str | None, metrics: dict, events: list[dict],
                started_at: str, seed, raw_ref: dict | None, commit: str, dirty: bool,
                hardened: bool | None = None) -> dict:
    model = dict(entry["model"])
    cloud = entry.get("kind") == "cloud_reference"
    leaks = sum(1 for e in events if e["kind"] == "leak")
    unsafe = len(events) - leaks
    rec = {
        "schema": SCHEMA, "kind": "cloud_reference" if cloud else "local", "tier": TIER, "suite": suite,
        "run_id": run_id, "board_version": board_version, "lang": "en", "model": model,
        "runtime": entry.get("runtime") or {"name": "stub", "version": "0"},
        "device": None if cloud else entry.get("device"),
        "settings": {"thinking": entry.get("thinking", "off"), "sampling": entry.get("sampling") or {},
                     "sampling_source": entry.get("sampling_source", "author_recommended"), "seed": seed,
                     "world_seed": None},
        "harness_commit": commit, "harness_dirty": dirty, "started_at": started_at,
        "case_id": case_meta["id"], "split": case_meta["split"], "trial": trial, "status": status,
        "facets": {"family": case_meta["type"], "category": case_meta["group"], "tags": case_tags(case_meta)},
        "metrics": {k: (v[:64] if isinstance(v, str) else v) for k, v in metrics.items()},
        "safety": {"unsafe_actions": unsafe, "privacy_leaks": leaks, "labels": sorted({e["kind"] for e in events})},
        "raw": raw_ref,
    }
    if hardened is False or metrics.get("grade_hardened") is False:
        rec["facets"]["tags"].append(NOT_HARDENED_TAG)     # graders.NOT_HARDENED_GRADERS: kept out of ranking
    if entry.get("sample"):
        rec["sample"] = True
    if cloud:
        rec["device_reason"] = "cloud"
    if status_detail:
        rec["status_detail"] = status_detail
    if raw_ref is None:
        rec["raw_reason"] = "not kept"
    return rec
