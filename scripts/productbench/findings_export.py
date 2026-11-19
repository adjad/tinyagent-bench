"""The findings as results-store records (docs/benchmark/results-record.md, edat.result/1.0).

    python -m scripts.productbench.findings_export <run dir> --store <store> --board-version <v> \
        [--harness-commit <sha>] [--default-thinking on|off] [--sample]

One record per entry, profile, trial and item found or missed: tier in_assistant, suite
productbench-findings, case_id <profile>/findings/<section>/<item>, metrics {outcome, found,
pass, score}. The captured text that decided the mark goes in the record's raw output
(put_raw), never in the record itself. Actions beyond what act-for-me allows count in
safety.unsafe_actions, never in a score. Held-out records are never exported.

A local record needs the weights' SHA-256 (filled from the performance results before a
ranked run). A stub dry run is exported only with --sample, and its stub "model" gets a
fingerprint of its own name instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edat.results.convert import ConversionError, _commit, _thinking, iso_utc  # noqa: E402
from edat.results.writer import RunWriter  # noqa: E402
from scripts.productbench import findings  # noqa: E402

SUITE = "productbench-findings"
PASS = {"found", "right", "not_done"}
UNSCORED = {"not_in_key", "offered", "passed_over"}
SURFACE_OF = {"brief": "brief", "brief_todos": "brief", "todo": "ask", "inbox": "ask", "messages": "ask",
              "window": "ask", "act": None, "act_gate": None}       # act for me runs in the Brief's cycle, on no surface


def _slug(text: str, n: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text or "").strip("-")
    return (s[:n].rstrip("-") or "item") + "-" + hashlib.sha256((text or "").encode()).hexdigest()[:6]


def item_records(records: list[dict], profiles: dict) -> list[tuple[dict, str, str, str, str | None]]:
    """(source row, section, item key, outcome, text) for every mark in every row."""
    out = []
    for r in records:
        if r.get("split") == "heldout" or r.get("heldout"):
            continue
        rep = findings.Report()
        findings.collect([r], profiles, rep)
        key = (r.get("entry") or r.get("model"), r["profile"])
        for section, cells in rep.cells.get(key, {}).items():
            for k, cell in cells.items():
                if k.startswith("~"):
                    continue
                for outcome, n in cell.outcomes.items():
                    for _ in range(n):
                        out.append((r, section, k, outcome, cell.text))
    return out


def export(src: Path | str, store: Path | str, *, board_version: str, harness_commit: str | None = None,
           default_thinking: str | None = None, sample: bool = False, device_id: str | None = None,
           run_dir: Path | str | None = None, weights: dict[str, str] | None = None) -> Path:
    """`weights` maps an entry or engine id to its weights' sha256 (engine_prep weights <folder>)."""
    src = Path(src)
    weights = weights or {}
    rows, meta = findings.load_records(src)
    if not rows:
        raise ConversionError(f"{src}/results.jsonl has no rows")
    stub = any(r.get("stub") for r in rows)
    if stub and not sample:
        raise ConversionError("a stub dry run is exported only as a sample (--sample)")
    pids = {r["profile"] for r in rows}
    profs = {p: findings.find_profile(p, [meta]) for p in pids}
    run_id = f"{rows[0]['run']}-findings"
    recs, raws = [], []
    for row, section, key, outcome, text in item_records(rows, profs):
        variant = row.get("variant") or {}
        kind = "cloud_reference" if row.get("kind") == "cloud_reference" else "local"
        thinking, detail = _thinking(variant.get("thinking") or "default", default_thinking)
        model: dict = {"id": variant.get("model_id") or row["model"],
                       "role": "cloud_reference" if kind == "cloud_reference" else "candidate"}
        if kind == "local":
            sha = (variant.get("weights_sha256") or weights.get(row.get("entry") or "") or weights.get(row["model"])
                   or weights.get(variant.get("model_id") or ""))
            if sha and re.match(r"^[0-9a-f]{64}$", str(sha)):
                model["weights_sha256"] = sha
            elif sample:
                model["weights_fingerprint"] = hashlib.sha256(f"stub:{model['id']}".encode()).hexdigest()
            else:
                raise ConversionError(f"{model['id']} has no weights_sha256 (08 §7)")
        else:
            cap = row.get("cloud") or {}
            model.update({"requested_model": row["model"], "resolved_model": cap.get("resolved_model"),
                          "upstream_provider": cap.get("upstream_provider"), "run_date": cap.get("run_date"),
                          "architecture": "unknown"})
        dev = row.get("device") or {}
        device = None if kind == "cloud_reference" else {
            "id": device_id or "mac", "class": device_id or "mac", "model_id": dev.get("model"),
            "soc": str(dev.get("chip") or "").replace("Apple ", "") or None,
            "ram_bytes": int(dev["memory_gb"] * 2**30) if isinstance(dev.get("memory_gb"), (int, float)) else None,
            "os": dev.get("os")}
        scored = outcome not in UNSCORED and outcome != "unsafe"
        metrics = {"outcome": outcome, "found": outcome in PASS}
        if scored:
            metrics["pass"] = outcome in PASS
            metrics["score"] = 1.0 if outcome in PASS else 0.0
        base_section = section.split("@")[0]
        rec = {"schema": "edat.result/1.0", "kind": kind, "tier": "in_assistant", "suite": SUITE, "run_id": run_id,
               "board_version": board_version, "lang": "en", "model": model,
               "runtime": {"name": (row.get("runtime") or {}).get("engine") or "omlx",
                           "version": (row.get("runtime") or {}).get("version")},
               "device": device,
               "settings": {"thinking": thinking, "sampling": {}, "seed": None,
                            "sampling_source": "provider_default" if kind == "cloud_reference" else "author_recommended"},
               "harness_commit": _commit(meta.get("git_sha"), harness_commit),
               "started_at": iso_utc(meta.get("started") or row.get("date")),
               "case_id": re.sub(r"[^A-Za-z0-9._:/@-]", "_",
                                 f"{row['profile']}/findings/{section}/{_slug(key)}")[:160],
               "split": "dev", "trial": int(row.get("trial") or 1),
               "status": "ok" if scored else "not_applicable",
               "facets": {"surface": SURFACE_OF.get(base_section), "profile": row["profile"],
                          "family": base_section},
               "metrics": metrics,
               "safety": {"unsafe_actions": 1 if outcome == "unsafe" else 0, "privacy_leaks": None,
                          "labels": ["beyond_act_for_me"] if outcome == "unsafe" else []}}
        if not scored:
            rec["status_detail"] = "unsafe: counted under safety" if outcome == "unsafe" else outcome
        if detail:
            rec["settings"]["thinking_detail"] = detail
        if kind == "cloud_reference":
            rec["device_reason"] = "cloud"
        if sample:
            rec["sample"] = True
        recs.append(rec)
        raws.append({"section": section, "item": key, "outcome": outcome, "text": text,
                     "source_case": row.get("case"), "now": row.get("now")})
    with RunWriter.open(store, run_id, tier="in_assistant", suite=SUITE, sample=sample, run_dir=run_dir) as w:
        for rec, raw in zip(recs, raws):
            rec["raw"] = w.put_raw(raw)
        w.append_many(recs)
        return w.dir


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Export a run's findings to the results store")
    ap.add_argument("run_dir")
    ap.add_argument("--store", required=True)
    ap.add_argument("--board-version", required=True)
    ap.add_argument("--harness-commit", default=None)
    ap.add_argument("--default-thinking", choices=("on", "off"), default=None)
    ap.add_argument("--sample", action="store_true", help="a stub dry run, marked sample")
    ap.add_argument("--weights", action="append", default=[], metavar="ID=SHA256",
                    help="weights hash for an entry (engine_prep weights <folder>); repeatable")
    args = ap.parse_args(argv)
    weights = dict(w.split("=", 1) for w in args.weights)
    out = export(args.run_dir, args.store, board_version=args.board_version, harness_commit=args.harness_commit,
                 default_thinking=args.default_thinking, sample=args.sample, weights=weights)
    print(f"wrote {out.name}")


if __name__ == "__main__":
    main()
