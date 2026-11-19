"""Read run directories and merged sets, check their integrity, and merge runs.

A *run* is a directory written by RunWriter (records.jsonl + manifest.json +
raw/). A *merged set* is a directory written by merge_runs (records.jsonl +
merge.json + raw/): several runs of one tier under one comparison key, for
example one run per world seed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from edat.results import keys
from edat.results.schema import ValidationError, record_errors, validate
from edat.results.writer import MANIFEST, RAW_DIR, RECORDS, atomic_write, record_line, utc_now

MERGE = "merge.json"
MERGE_SCHEMA_ID = "edat.merge/1.0"


class IntegrityError(ValueError):
    """A run directory is damaged: checksum mismatch, partial line, missing raw file."""


@dataclass
class RunSet:
    """Records from one run or one merged set, with where they came from."""
    path: Path
    kind: str                      # "run" or "merged"
    run_ids: list[str]
    records: list[dict]
    manifest: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)   # [{run_id, records, records_sha256}]

    @property
    def tier(self) -> str:
        return self.records[0]["tier"] if self.records else self.manifest.get("tier", "")

    @property
    def comparison_key(self) -> dict | None:
        return keys.comparison_key(self.records[0]) if self.records else None

    def raw_path(self, rec: dict) -> Path | None:
        raw = rec.get("raw")
        return self.path / raw["path"] if raw else None


def parse_jsonl(path: Path) -> list[dict]:
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise IntegrityError(f"{path}: the last line is incomplete (an interrupted write)")
    out = []
    for n, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise IntegrityError(f"{path}:{n}: not JSON ({e})") from None
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else hashlib.sha256(b"").hexdigest()


def check_records(records: Iterable[dict], where: Path) -> None:
    problems = []
    seen: set[tuple] = set()
    for n, rec in enumerate(records, 1):
        errs = record_errors(rec)
        if errs:
            problems.append(f"{where}:{n}: " + "; ".join(errs[:4]))
            continue
        tk = keys.trial_key(rec)
        if tk in seen:
            problems.append(f"{where}:{n}: duplicate trial {tk}")
        seen.add(tk)
    if problems:
        raise ValidationError(problems, what=f"records in {where}")


def check_raw(rs: RunSet, deep: bool = False) -> list[str]:
    """Missing (and with deep=True, altered) raw files."""
    problems = []
    for rec in rs.records:
        p = rs.raw_path(rec)
        if p is None:
            continue
        if not p.exists():
            problems.append(f"{rec['case_id']} t{rec['trial']}: raw file {rec['raw']['path']} is missing")
        elif deep and hashlib.sha256(p.read_bytes()).hexdigest() != rec["raw"]["sha256"]:
            problems.append(f"{rec['case_id']} t{rec['trial']}: raw file {rec['raw']['path']} was altered")
    return problems


def read_set(path: Path | str, *, verify: bool = True, deep: bool = False) -> RunSet:
    """Read a run directory or a merged set. With verify, check the manifest's
    checksum and count, every record against the schema and rules, one
    comparison key, no duplicate trial, and that raw files exist."""
    path = Path(path).expanduser()
    rec_path = path / RECORDS
    if not rec_path.exists():
        raise FileNotFoundError(f"{path}: no {RECORDS}; not a run directory or merged set")
    records = parse_jsonl(rec_path)
    if (path / MERGE).exists():
        merge = json.loads((path / MERGE).read_text())
        rs = RunSet(path, "merged", [s["run_id"] for s in merge["runs"]], records, merge, merge["runs"])
        expected_sha, expected_n = merge["records"]["sha256"], merge["records"]["count"]
    elif (path / MANIFEST).exists():
        manifest = json.loads((path / MANIFEST).read_text())
        if verify:
            validate(manifest, "manifest")
        rs = RunSet(path, "run", [manifest["run_id"]], records, manifest,
                    [{"run_id": manifest["run_id"], "records": manifest["records"]["count"],
                      "records_sha256": manifest["records"]["sha256"]}])
        expected_sha, expected_n = manifest["records"]["sha256"], manifest["records"]["count"]
    else:
        raise FileNotFoundError(f"{path}: no {MANIFEST} or {MERGE}")
    if verify:
        actual = _sha(rec_path)
        if actual != expected_sha or len(records) != expected_n:
            raise IntegrityError(f"{path}: records.jsonl does not match its manifest "
                                 f"({len(records)} records, sha256 {actual[:12]}; manifest says "
                                 f"{expected_n}, {expected_sha[:12]})")
        check_records(records, rec_path)
        if records:
            first = keys.comparison_key(records[0])
            for rec in records[1:]:
                keys.check_same(first, keys.comparison_key(rec), what=f"read {path} as one set",
                                a_name="first record", b_name=f"{rec['case_id']} t{rec['trial']}")
            if rs.kind == "run":
                for rec in records:
                    if rec["run_id"] != rs.run_ids[0]:
                        raise IntegrityError(f"{path}: record of run {rec['run_id']} in run {rs.run_ids[0]}")
        missing = check_raw(rs, deep=deep)
        if missing:
            raise IntegrityError(f"{path}: " + "; ".join(missing[:5]))
    return rs


def thinking_by_model(records: Iterable[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rec in records:
        out.setdefault(rec["model"]["id"], set()).add(rec["settings"]["thinking"])
    return out


def merge_runs(sets: list[RunSet], out_dir: Path | str, *, allow_thinking_mismatch: bool = False) -> RunSet:
    """Combine several runs of one tier into one merged set at `out_dir`.

    Refuses when the runs differ in board version, harness commit, sampling
    class, language or tier; when one model appears with different thinking
    modes in different runs (unless that is explicitly allowed: two entries of
    one model, each whole in its own run); and on a duplicate trial."""
    if len(sets) < 2:
        raise ValueError("merge needs at least two runs")
    nonempty = [s for s in sets if s.records]
    if not nonempty:
        raise ValueError("every run to merge is empty")
    base = nonempty[0]
    for s in nonempty[1:]:
        keys.check_same(base.comparison_key, s.comparison_key, what="merge these runs",
                        a_name=base.path.name, b_name=s.path.name)
        if not allow_thinking_mismatch:
            a, b = thinking_by_model(base.records), thinking_by_model(s.records)
            for model in set(a) & set(b):
                keys.check_same_thinking(a[model], b[model], what=f"merge runs of {model}")
    out = Path(out_dir).expanduser()
    if (out / RECORDS).exists():
        raise FileExistsError(f"{out} already holds records; merge into a new directory")
    merged: list[dict] = []
    seen: dict[tuple, str] = {}
    for s in sets:
        for rec in s.records:
            tk = keys.trial_key(rec)
            if tk in seen:
                raise keys.IncompatibleResults(
                    f"refusing to merge: trial {tk} is in both {seen[tk]} and {s.path.name}")
            seen[tk] = s.path.name
            merged.append(rec)
    (out / RAW_DIR).mkdir(parents=True, exist_ok=True)
    for s in sets:
        for rec in s.records:
            src = s.raw_path(rec)
            if src is not None and src.exists():
                dst = out / rec["raw"]["path"]
                if not dst.exists():
                    shutil.copyfile(src, dst)
    merged.sort(key=lambda r: (keys.entry_key(r), keys.device_id(r), r["case_id"], r["trial"]))
    data = b"".join(record_line(r) for r in merged)
    atomic_write(out / RECORDS, data)
    sources = [x for s in sets for x in s.sources]
    merge = {"schema": MERGE_SCHEMA_ID, "created_at": utc_now(), "comparison_key": base.comparison_key,
             "runs": sources,
             "records": {"path": RECORDS, "count": len(merged), "sha256": hashlib.sha256(data).hexdigest()}}
    atomic_write(out / MERGE, (json.dumps(merge, indent=1, sort_keys=True) + "\n").encode())
    return read_set(out)
