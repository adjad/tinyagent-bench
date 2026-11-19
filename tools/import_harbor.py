#!/usr/bin/env python3
"""Re-create (or check) the Harbor import in this repository.

The import is defined by import/harbor-import.json: a source commit, a list of
included paths (a trailing "/" means a whole folder), and explicit exclusions.
Files are read from the Harbor commit with git, never from a working tree, so
uncommitted changes in the Harbor checkout cannot leak in. Every file keeps its
Harbor path.

  # write the files and refresh import/harbor-files.json
  python3 tools/import_harbor.py --harbor /path/to/Harbor

  # only report differences between this tree and the recorded import
  python3 tools/import_harbor.py --harbor /path/to/Harbor --check

  # import from another commit (then record it in harbor-import.json)
  python3 tools/import_harbor.py --harbor /path/to/Harbor --ref <commit>

Files that were imported before but are no longer selected are listed and left
alone unless --prune is given. Nothing is committed; review with `git diff`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "import" / "harbor-import.json"
LOCK = REPO / "import" / "harbor-files.json"


def git(harbor: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", "-C", str(harbor), *args], check=True,
                         capture_output=True)
    return out.stdout if binary else out.stdout.decode()


def blob_sha(data: bytes) -> str:
    """Git's blob id for these bytes, so the lock can be checked without git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def select(tree: dict[str, tuple[str, str]], spec: dict) -> list[str]:
    chosen: set[str] = set()
    for inc in spec["include"]:
        hits = [p for p in tree if (p.startswith(inc) if inc.endswith("/") else p == inc)]
        if not hits:
            sys.exit(f"include {inc!r} matches nothing at the source commit")
        chosen.update(hits)
    for exc in spec.get("exclude", []):
        path = exc["path"]
        gone = {p for p in chosen if p == path or (path.endswith("/") and p.startswith(path))}
        if not gone:
            sys.exit(f"exclude {path!r} matches nothing in the selection")
        chosen -= gone
    return sorted(chosen)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harbor", required=True, type=Path, help="a Harbor git checkout")
    ap.add_argument("--ref", help="commit to import (default: source.commit in the spec)")
    ap.add_argument("--check", action="store_true", help="report differences, write nothing")
    ap.add_argument("--prune", action="store_true", help="delete previously imported files no longer selected")
    args = ap.parse_args()

    spec = json.loads(SPEC.read_text())
    ref = args.ref or spec["source"]["commit"]
    commit = git(args.harbor, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    tree: dict[str, tuple[str, str]] = {}
    for line in git(args.harbor, "ls-tree", "-r", "-z", commit).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob":
            tree[path] = (mode, sha)

    files = select(tree, spec)
    previous = {f["path"] for f in json.loads(LOCK.read_text())["files"]} if LOCK.exists() else set()

    changed, missing = [], []
    for path in files:
        mode, sha = tree[path]
        dest = REPO / path
        if not dest.exists():
            missing.append(path)
        elif blob_sha(dest.read_bytes()) != sha:
            changed.append(path)
        if args.check:
            continue
        data = git(args.harbor, "cat-file", "blob", sha, binary=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        dest.chmod(0o755 if mode == "100755" else 0o644)

    stale = sorted(previous - set(files))
    print(f"source {commit}  selected {len(files)} files")
    print(f"  differ from source: {len(changed)}  absent here: {len(missing)}  no longer selected: {len(stale)}")
    report = (("differs", changed), ("absent", missing), ("stale", stale)) if args.check else (("stale", stale),)
    for label, items in report:
        for p in items:
            print(f"  {label}: {p}")

    if args.check:
        return 1 if (changed or missing or stale) else 0

    for p in stale:
        if args.prune and (REPO / p).exists():
            (REPO / p).unlink()
    LOCK.write_text(json.dumps({
        "schema": 1,
        "source_repository": spec["source"]["repository"],
        "source_commit": commit,
        "count": len(files),
        "files": [{"path": p, "mode": tree[p][0], "blob": tree[p][1]} for p in files],
    }, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
