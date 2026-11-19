"""Import the iPhone runner's export into the perf store (spec 04 §2.10; 10 §8.2).

    python -m edat.perf.iphone_import <EDAT-<run_id>.zip | folder> --store <dir> [--force]

The export (``EDAT-<run_id>.zip``, or its unzipped folder) holds ``run-<run_id>.json``
(the ``edat.perf/1.0`` record), ``mem-<run_id>.jsonl``, optionally
``phone-<run_id>.jsonl``, and ``manifest-<run_id>.json`` (``edat.export-manifest/1``:
each file's name, sha256 and bytes). The importer:

1. refuses zip members with absolute paths, ``..``, drive letters or symlinks, before
   extracting anything;
2. reads the manifest and verifies every listed file's sha256 (and byte count);
3. requires ``schema == "edat.perf/1.0"`` and ``runner.name == "iphone-mlx-swift"``, and
   rejects the older ``edgebench.run/0.1`` shape with a clear message;
4. normalises the older additive iPhone statuses (``killed``, ``skipped_after_oom``,
   ``ok_memory_warning``, ``invalid_not_on_battery``), keeping the original in
   ``status_original``;
5. runs :func:`edat.perf.aggregate.aggregate`, so Mac and iPhone documents carry cells and
   verdicts from the same code;
6. writes ``<store>/perf/<run_id>/run-<run_id>.json`` (the aggregated document) beside the
   export's other files, the manifest, and the record exactly as exported
   (``run-<run_id>.export.json``, the file the manifest hash covers).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from . import constants as C
from .aggregate import aggregate

IPHONE_RUNNER = "iphone-mlx-swift"
OLD_SCHEMA = "edgebench.run/0.1"
MAX_UNCOMPRESSED_BYTES = 8 << 30
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")

# Older additive iPhone statuses -> §3.6 / §5.3 statuses (10 §8.3).
STATUS_MAP = {"killed": "oom_killed", "skipped_after_oom": "not_run_above_oom", "ok_memory_warning": "ok",
              "invalid_not_on_battery": "voided_not_on_battery"}


class ImportRejected(ValueError):
    """The export cannot be imported; the message says why."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unsafe_member(name: str) -> str | None:
    if not name:
        return "empty name"
    if "\x00" in name:
        return "NUL in name"
    norm = name.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return "absolute path"
    if any(part == ".." for part in PurePosixPath(norm).parts):
        return "'..' in path"
    return None


def safe_extract(zip_path: Path, dest: Path) -> Path:
    """Extract ``zip_path`` into ``dest`` after checking every member; refuses the whole archive on one bad member."""
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        total = 0
        for info in infos:
            why = _unsafe_member(info.filename)
            if why:
                raise ImportRejected(f"zip member {info.filename!r} refused: {why}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ImportRejected(f"zip member {info.filename!r} refused: symbolic link")
            total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ImportRejected(f"zip expands to {total} bytes, over the {MAX_UNCOMPRESSED_BYTES} limit")
        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        for info in infos:
            norm = info.filename.replace("\\", "/")
            target = (dest / norm).resolve()
            if root != target and root not in target.parents:
                raise ImportRejected(f"zip member {info.filename!r} refused: leaves the extraction folder")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    return dest


def _json_files(root: Path, depth: int = 2):
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if d != "__MACOSX" and not d.startswith(".")]
        if len(rel.parts) >= depth:
            dirnames[:] = []
        for f in filenames:
            if f.endswith(".json") and not f.startswith("._"):
                yield Path(dirpath) / f


def _old_shape(root: Path) -> bool:
    for p in _json_files(root):
        try:
            if json.loads(p.read_text()).get("schema") == OLD_SCHEMA:
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def _old_shape_error() -> ImportRejected:
    return ImportRejected(f"this export is in the older {OLD_SCHEMA} shape; the iPhone app must export "
                          f"{C.PERF_SCHEMA} (update the EDAT iPhone runner and export the run again)")


def find_manifest(root: Path) -> Path:
    found = [p for p in _json_files(root) if p.name.startswith("manifest-")]
    if not found:
        if _old_shape(root):
            raise _old_shape_error()
        raise ImportRejected(f"no manifest-<run_id>.json ({C.EXPORT_MANIFEST_SCHEMA}) in the export")
    if len(found) > 1:
        raise ImportRejected(f"more than one manifest in the export: {sorted(p.name for p in found)}")
    return found[0]


def verify_manifest(manifest_path: Path) -> tuple[dict, dict[str, Path]]:
    try:
        manifest = json.loads(manifest_path.read_text())
    except ValueError as e:
        raise ImportRejected(f"{manifest_path.name} is not JSON: {e}") from e
    if manifest.get("schema") != C.EXPORT_MANIFEST_SCHEMA:
        if manifest.get("record_schema") == OLD_SCHEMA or manifest.get("schema") == OLD_SCHEMA:
            raise _old_shape_error()
        raise ImportRejected(f"manifest schema {manifest.get('schema')!r} is not {C.EXPORT_MANIFEST_SCHEMA}")
    if manifest.get("record_schema") == OLD_SCHEMA:
        raise _old_shape_error()
    folder = manifest_path.parent
    files: dict[str, Path] = {}
    for f in manifest.get("files") or ():
        name = f.get("name")
        if not isinstance(name, str) or not SAFE_NAME.match(name):
            raise ImportRejected(f"manifest lists an unsafe file name {name!r}")
        p = folder / name
        if not p.is_file() or p.is_symlink():
            raise ImportRejected(f"manifest lists {name}, which is missing from the export")
        digest = _sha256(p)
        if digest != str(f.get("sha256") or "").lower():
            raise ImportRejected(f"sha256 mismatch for {name}: manifest {f.get('sha256')}, file {digest}")
        if f.get("bytes") is not None and int(f["bytes"]) != p.stat().st_size:
            raise ImportRejected(f"size mismatch for {name}: manifest {int(f['bytes'])}, file {p.stat().st_size}")
        files[name] = p
    if not files:
        raise ImportRejected("the manifest lists no files")
    return manifest, files


def normalise_statuses(doc: dict) -> int:
    """Map the older additive iPhone statuses in place; returns how many samples changed."""
    changed = 0
    for s in doc.get("samples") or ():
        st = s.get("status")
        if st not in STATUS_MAP:
            continue
        s["status_original"] = st
        s["status"] = STATUS_MAP[st]
        if st == "ok_memory_warning":
            s["memory_warning"] = True
        if st == "invalid_not_on_battery":
            s["voided"] = True
        changed += 1
    return changed


def import_export(src: str | Path, store: str | Path, *, overwrite: bool = False,
                  energy_fn: Callable | None = None, extra_derivers=()) -> Path:
    """Import one iPhone export into ``<store>/perf/<run_id>/``; returns that folder."""
    src = Path(src).expanduser()
    store = Path(store).expanduser()
    with tempfile.TemporaryDirectory(prefix="edat-iphone-import-") as tmp:
        if src.is_dir():
            root = src
        elif src.is_file() and zipfile.is_zipfile(src):
            root = safe_extract(src, Path(tmp) / "export")
        else:
            raise ImportRejected(f"{src} is neither a folder nor a zip file")
        manifest_path = find_manifest(root)
        manifest, files = verify_manifest(manifest_path)
        run_id = manifest.get("run_id")
        if not isinstance(run_id, str) or not SAFE_RUN_ID.match(run_id):
            raise ImportRejected(f"unsafe or missing run_id {run_id!r} in the manifest")
        record_name = f"run-{run_id}.json"
        if record_name not in files:
            raise ImportRejected(f"the manifest does not list {record_name}")
        raw = files[record_name].read_bytes()
        try:
            doc = json.loads(raw)
        except ValueError as e:
            raise ImportRejected(f"{record_name} is not JSON: {e}") from e
        if doc.get("schema") == OLD_SCHEMA:
            raise _old_shape_error()
        if doc.get("schema") != C.PERF_SCHEMA:
            raise ImportRejected(f"{record_name}: schema {doc.get('schema')!r} is not {C.PERF_SCHEMA}")
        runner = (doc.get("runner") or {}).get("name")
        if runner != IPHONE_RUNNER:
            raise ImportRejected(f"{record_name}: runner {runner!r} is not {IPHONE_RUNNER}")
        if doc.get("run_id") != run_id:
            raise ImportRejected(f"{record_name}: run_id {doc.get('run_id')!r} differs from the manifest's {run_id!r}")
        if doc.get("cells") or doc.get("verdicts"):
            raise ImportRejected(f"{record_name} already carries cells or verdicts; runners never write them")
        n_changed = normalise_statuses(doc)

        dest = store / "perf" / run_id
        if dest.exists() and not overwrite:
            raise ImportRejected(f"{dest} already exists (pass --force to replace it)")
        stage = Path(tempfile.mkdtemp(prefix=f".{run_id}.import-", dir=_ensure(store / "perf")))
        try:
            for name, p in files.items():
                target = stage / (f"run-{run_id}.export.json" if name == record_name else name)
                shutil.copyfile(p, target)
            shutil.copyfile(manifest_path, stage / manifest_path.name)
            result = aggregate(doc, run_dir=stage, energy_fn=energy_fn, extra_derivers=extra_derivers)
            result["import"] = {"source": "iphone-export", "manifest": manifest_path.name,
                                "manifest_sha256": _sha256(manifest_path),
                                "record_sha256": hashlib.sha256(raw).hexdigest(),
                                "record_as_exported": f"run-{run_id}.export.json",
                                "statuses_normalised": n_changed}
            (stage / record_name).write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
            if dest.exists():
                shutil.rmtree(dest)
            stage.rename(dest)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return dest


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.perf.iphone_import",
                                 description="Import an EDAT iPhone export (zip or folder) into the perf store.")
    ap.add_argument("export", type=Path, help="EDAT-<run_id>.zip or its folder")
    ap.add_argument("--store", type=Path, required=True, help="the results store root")
    ap.add_argument("--force", action="store_true", help="replace an existing run with the same run_id")
    args = ap.parse_args(argv)
    try:
        dest = import_export(args.export, args.store, overwrite=args.force)
    except (ImportRejected, OSError, zipfile.BadZipFile) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
