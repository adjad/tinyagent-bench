"""Append EDAT result records to a run directory, safely.

    <store>/runs/<run_id>/records.jsonl   one edat.result/1.0 per line
    <store>/runs/<run_id>/manifest.json   edat.run-manifest/1.0
    <store>/runs/<run_id>/raw/<sha>.json  raw outputs, content-addressed

Safety properties:
- every record is validated (schema + cross-field rules) before it is written;
- a run holds one comparison key (board version, harness commit, sampling
  class, language, tier); a record with another is refused;
- a second record for the same entry, device, case and trial is refused;
- records are appended under an exclusive lock with a single write() of whole
  lines on an O_APPEND descriptor, then fsync'd, so a reader never sees half a
  line from a finished append and two writers never interleave;
- raw files are written (temp file, fsync, rename) before the record that
  points at them, so a record never references a missing raw file;
- the manifest is rewritten atomically (temp file + rename) after each append.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from edat.results import keys
from edat.results.schema import MANIFEST_SCHEMA_ID, ValidationError, validate, validate_record

RECORDS = "records.jsonl"
MANIFEST = "manifest.json"
RAW_DIR = "raw"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ATTACH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_store() -> Path:
    """$EDAT_RESULTS_DIR, else ~/.edat/results. Never inside the repository."""
    return Path(os.environ.get("EDAT_RESULTS_DIR") or Path.home() / ".edat" / "results").expanduser()


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def record_line(rec: dict) -> bytes:
    return canonical_json(rec) + b"\n"


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class RunWriter:
    """One run directory. Use `RunWriter.open(store, run_id, ...)` as a context manager."""

    def __init__(self, run_dir: Path, run_id: str, tier: str, suite: str | None, sample: bool | None):
        if not _RUN_ID.match(run_id):
            raise ValueError(f"run_id {run_id!r} must match {_RUN_ID.pattern}")
        self.dir = Path(run_dir)
        self.run_id, self.tier, self.suite, self.sample = run_id, tier, suite, sample
        self._lock_fh = None
        self._hash = hashlib.sha256()
        self._count = 0
        self._seen: set[tuple] = set()
        self._entries: dict[str, dict] = {}
        self._key: dict | None = None
        self._head: dict | None = None
        self._created: str | None = None
        self._attachments: dict[str, dict] = {}
        self._source: dict | None = None

    # -- opening -------------------------------------------------------------
    @classmethod
    @contextmanager
    def open(cls, store: Path | str, run_id: str, *, tier: str, suite: str | None = None,
             sample: bool | None = None, run_dir: Path | str | None = None):
        """Create or reopen `<store>/runs/<run_id>/` (or `run_dir`) and hold its lock."""
        path = Path(run_dir) if run_dir else Path(store).expanduser() / "runs" / run_id
        w = cls(path, run_id, tier, suite, sample)
        w._acquire()
        try:
            w._load()
            yield w
        finally:
            w._release()

    def _acquire(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / RAW_DIR).mkdir(exist_ok=True)
        self._lock_fh = open(self.dir / ".lock", "a+")
        try:
            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_fh.close()
            self._lock_fh = None
            raise RuntimeError(f"run {self.run_id} is open in another writer ({self.dir})") from None

    def _release(self) -> None:
        if self._lock_fh is not None:
            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
            self._lock_fh.close()
            self._lock_fh = None

    def _load(self) -> None:
        manifest_path = self.dir / MANIFEST
        if manifest_path.exists():
            m = json.loads(manifest_path.read_text())
            if m.get("run_id") != self.run_id:
                raise ValueError(f"{self.dir} belongs to run {m.get('run_id')!r}, not {self.run_id!r}")
            if m.get("tier") != self.tier:
                raise ValueError(f"run {self.run_id} is tier {m.get('tier')!r}, not {self.tier!r}")
            self._created = m.get("created_at")
            self._attachments = {a["name"]: a for a in m.get("attachments") or ()}
            self._source = m.get("source")
            if self.suite is None:
                self.suite = m.get("suite")
            if self.sample is None:
                self.sample = m.get("sample")
        path = self.dir / RECORDS
        if not path.exists():
            return
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError(f"{path} ends in a partial line (an interrupted write); "
                             "repair or remove the last line before appending")
        self._hash.update(data)
        for n, line in enumerate(data.splitlines(), 1):
            if line.strip():
                self._note(json.loads(line), where=f"{path}:{n}")

    # -- bookkeeping ---------------------------------------------------------
    def _note(self, rec: dict, where: str) -> None:
        key = keys.comparison_key(rec)
        if self._key is None:
            self._key, self._head = key, rec
        else:
            keys.check_same(self._key, key, what=f"append {where} to run {self.run_id}",
                            a_name="run", b_name="record")
        tk = keys.trial_key(rec)
        if tk in self._seen:
            raise ValueError(f"{where}: duplicate trial {tk} in run {self.run_id}")
        self._seen.add(tk)
        ek = keys.entry_key(rec)
        e = self._entries.setdefault(ek, {"entry_key": ek, "model_id": rec["model"]["id"],
                                          "thinking": rec["settings"]["thinking"],
                                          "device_id": keys.device_id(rec) if rec.get("device") else None,
                                          "sampling_source": rec["settings"]["sampling_source"],
                                          "records": 0})
        e["records"] += 1
        self._count += 1

    def _check(self, rec: dict) -> dict:
        validate_record(rec)
        if rec["run_id"] != self.run_id:
            raise ValidationError([f"$.run_id: {rec['run_id']!r} written to run {self.run_id!r}"])
        if rec["tier"] != self.tier:
            raise ValidationError([f"$.tier: {rec['tier']!r} written to a {self.tier!r} run"])
        if self.sample is not None and bool(rec.get("sample")) != bool(self.sample):
            raise ValidationError(["$.sample: sample and real records never share a run"])
        raw = rec.get("raw")
        if raw and not (self.dir / raw["path"]).exists():
            raise ValidationError([f"$.raw.path: {raw['path']} is not in {self.dir}; call put_raw first"])
        return rec

    # -- writing -------------------------------------------------------------
    def put_raw(self, obj: Any) -> dict:
        """Store one raw output beside the records; returns the record's `raw` value.
        A str is stored as text, anything else as canonical JSON."""
        if isinstance(obj, str):
            data, ext = obj.encode(), "txt"
        else:
            data, ext = canonical_json(obj), "json"
        digest = hashlib.sha256(data).hexdigest()
        rel = f"{RAW_DIR}/{digest}.{ext}"
        path = self.dir / rel
        if not path.exists():
            atomic_write(path, data)
        return {"path": rel, "sha256": digest, "bytes": len(data)}

    def append(self, rec: dict) -> None:
        self.append_many([rec])

    def append_many(self, recs: Iterable[dict]) -> int:
        """Validate every record first; write all of them or none."""
        recs = list(recs)
        if not recs:
            return 0
        decided = self.sample
        if self.sample is None:
            self.sample = bool(recs[0].get("sample"))
        try:
            for rec in recs:
                self._check(rec)
        except Exception:
            self.sample = decided       # a refused batch decides nothing
            raise
        saved = (set(self._seen), {k: dict(v) for k, v in self._entries.items()}, self._key, self._head,
                 self._count)
        try:
            for i, rec in enumerate(recs):
                self._note(rec, where=f"record {i + 1} of this append")
        except Exception:
            self._seen, self._entries, self._key, self._head, self._count = saved
            self.sample = decided
            raise
        data = b"".join(record_line(r) for r in recs)
        fd = os.open(self.dir / RECORDS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self._hash.update(data)
        self._write_manifest()
        return len(recs)

    def attach(self, name: str, data: bytes | str | Path, kind: str) -> dict:
        """Keep a run-level file (an edat.perf/1.0 document, a Toolbench meta.json) beside the records."""
        if not _ATTACH.match(name) or name in (RECORDS, MANIFEST):
            raise ValueError(f"attachment name {name!r} is not allowed")
        if isinstance(data, Path):
            data = data.read_bytes()
        elif isinstance(data, str):
            data = data.encode()
        atomic_write(self.dir / name, data)
        self._attachments[name] = {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "kind": kind}
        self._write_manifest()
        return self._attachments[name]

    def set_source(self, source: dict) -> None:
        self._source = source
        self._write_manifest()

    def manifest(self) -> dict:
        head = self._head or {}
        now = utc_now()
        m = {
            "schema": MANIFEST_SCHEMA_ID, "run_id": self.run_id, "tier": self.tier, "suite": self.suite,
            "kind": head.get("kind", "local"), "board_version": head.get("board_version", "unknown"),
            "harness_commit": head.get("harness_commit", "0000000"),
            "created_at": self._created or now, "updated_at": now, "sample": bool(self.sample),
            "comparison_key": self._key or {"board_version": "unknown", "harness_commit": "0000000",
                                            "sampling_class": "recommended", "lang": "en", "tier": self.tier},
            "entries": sorted(self._entries.values(), key=lambda e: e["entry_key"]),
            "records": {"path": RECORDS, "count": self._count, "sha256": self._hash.hexdigest()},
            "raw": {"dir": RAW_DIR, "count": sum(1 for p in (self.dir / RAW_DIR).iterdir()
                                                  if not p.name.startswith("."))},
            "attachments": sorted(self._attachments.values(), key=lambda a: a["name"]),
        }
        if self._source is not None:
            m["source"] = self._source
        return m

    def _write_manifest(self) -> None:
        m = self.manifest()
        self._created = m["created_at"]
        validate(m, "manifest")
        atomic_write(self.dir / MANIFEST, (json.dumps(m, indent=1, sort_keys=True) + "\n").encode())
