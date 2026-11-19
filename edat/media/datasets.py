"""Dataset manifests and their fetch script: ``python -m edat.media.datasets``.

Each public set the spec selects (spec 05 §3.3, §5, §7.2) has a manifest in
``test_fixtures/media/datasets/<name>.json`` (schema ``edat.media.dataset/1``):
name, suites, split, source address, licence and where it was read, whether the
licence is verified and the data redistributable, files with any published
checksum, and a status: ``ready`` (plain HTTP files), ``needs_loader`` (fetched
through a Hugging Face or git-LFS loader that EDAT does not ship yet) or
``blocked_licence`` (not used until the licence is confirmed, spec 05 §2.5).

The fetch script **refuses** to download unless:

* the manifest's status is ``ready``, its licence is verified and redistributable;
* the destination is outside the repository (datasets are never committed);
* ``--yes`` is given (nothing is downloaded by accident, or by a test).

Every file is checked against its published md5 or sha256; a file with no
published checksum gets its sha256 recorded in ``<dest>/<name>/FETCHED.json``.
``plan`` prints what would be fetched, without any network access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

MANIFEST_DIR = Path(__file__).resolve().parents[2] / "test_fixtures" / "media" / "datasets"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "edat.media.dataset/1"
STATUSES = ("ready", "needs_loader", "blocked_licence")
REDISTRIBUTABLE = ("yes", "share-alike", "unverified", "no")


class FetchRefused(RuntimeError):
    pass


def load_manifests(directory: Path = MANIFEST_DIR) -> dict[str, dict]:
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        m = json.loads(p.read_text())
        out[m["name"]] = m
    return out


def check_manifest(m: dict) -> list[str]:
    """Problems with one manifest (empty when it is well formed)."""
    problems = []
    for key in ("schema", "name", "suites", "homepage", "licence", "licence_url",
                "licence_verified", "redistributable", "access", "files", "status"):
        if key not in m:
            problems.append(f"missing {key}")
    if m.get("schema") != SCHEMA:
        problems.append("wrong schema")
    if m.get("status") not in STATUSES:
        problems.append(f"bad status {m.get('status')!r}")
    if m.get("redistributable") not in REDISTRIBUTABLE:
        problems.append(f"bad redistributable {m.get('redistributable')!r}")
    if m.get("status") == "ready":
        if not m.get("licence_verified") or m.get("redistributable") not in ("yes", "share-alike"):
            problems.append("ready but the licence is not verified and redistributable")
        if m.get("access") != "http" or not m.get("files"):
            problems.append("ready needs http files")
    if m.get("status") != "blocked_licence" and not m.get("licence_verified"):
        problems.append("unverified licence must be blocked_licence")
    for f in m.get("files", []):
        if not str(f.get("url", "")).startswith("https://"):
            problems.append(f"file url is not https: {f.get('url')}")
        for algo, n in (("md5", 32), ("sha256", 64)):
            v = f.get(algo)
            if v is not None and (len(v) != n or any(c not in "0123456789abcdef" for c in v)):
                problems.append(f"bad {algo} {v!r}")
    return problems


def plan(name: str, dest: Path | None = None) -> dict:
    m = load_manifests()[name]
    reasons = []
    if m["status"] != "ready":
        reasons.append(f"status is {m['status']}")
    if not m["licence_verified"]:
        reasons.append("licence not verified")
    if m["redistributable"] not in ("yes", "share-alike"):
        reasons.append(f"redistributable: {m['redistributable']}")
    if dest is not None and _inside_repo(dest):
        reasons.append("destination is inside the repository")
    return {"name": name, "licence": m["licence"], "files": m["files"],
            "approx_bytes": sum((f.get("approx_bytes") or 0) for f in m["files"]),
            "allowed": not reasons, "refused_because": reasons}


def _inside_repo(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def _hash_file(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name: str, dest: Path, *, yes: bool, opener=urllib.request.urlopen) -> dict:
    """Download a ``ready`` dataset's files into ``dest/name`` and verify them."""
    p = plan(name, dest)
    if not p["allowed"]:
        raise FetchRefused(f"{name}: " + "; ".join(p["refused_because"]))
    if not yes:
        raise FetchRefused(f"{name}: pass --yes to download about {p['approx_bytes'] / 1e9:.2f} GB")
    out_dir = Path(dest) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"name": name, "files": []}
    for f in p["files"]:
        target = out_dir / f["url"].rsplit("/", 1)[-1]
        tmp = target.with_suffix(target.suffix + ".part")
        with opener(f["url"]) as resp, tmp.open("wb") as fh:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                fh.write(chunk)
        for algo in ("sha256", "md5"):
            want = f.get(algo)
            if want and _hash_file(tmp, algo) != want:
                tmp.unlink()
                raise FetchRefused(f"{name}: {target.name} failed its published {algo}")
        tmp.rename(target)
        record["files"].append({"file": target.name, "sha256": _hash_file(target, "sha256"),
                                "checked_against": "md5" if f.get("md5") else
                                ("sha256" if f.get("sha256") else "none published")})
    (out_dir / "FETCHED.json").write_text(json.dumps(record, indent=1))
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EDAT media dataset manifests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sp = sub.add_parser("plan")
    sp.add_argument("name")
    sp.add_argument("--dest")
    sf = sub.add_parser("fetch")
    sf.add_argument("name")
    sf.add_argument("--dest", required=True, help="a folder outside the repository")
    sf.add_argument("--yes", action="store_true", help="really download")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        for n, m in load_manifests().items():
            print(f"{n:28} {m['status']:16} {m['licence']}")
        return 0
    if args.cmd == "plan":
        print(json.dumps(plan(args.name, Path(args.dest) if args.dest else None), indent=1))
        return 0
    try:
        rec = fetch(args.name, Path(args.dest), yes=args.yes)
    except FetchRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(rec, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
