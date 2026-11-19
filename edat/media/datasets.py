"""Download manifests for tinyagent-bench and their fetch script: ``python -m edat.media.datasets``.

Every public set, model, package and font the media spec uses (spec 05 §3.3, §5,
§7.2) has a manifest in ``test_fixtures/media/datasets/<name>.json`` (schema
``edat.media.dataset/1``): name, ``group`` (speech_engine, asr_eval, noise_room,
sounds, vision_eval, fonts, normaliser), suites, source address, licence and where
it was read, whether the licence is verified and the data redistributable,
``needed_v1`` (yes, no or if_confirmed), exact file URLs with bytes and a
checksum (or ``checksum: none published``), and a status:

* ``ready``: plain HTTP files, licence verified and redistributable;
* ``fetch_at_run_time``: may be used on this machine but is never committed
  (unverified licences, third-party screenshots; a project decision);
* ``needs_loader``: fetched through a loader tinyagent-bench does not ship;
* ``blocked_licence``: not used until the licence is confirmed (spec 05 §2.5).

``summary`` prints file counts and bytes per group and status, and the v1 grand
total (``needed_v1 == yes`` and status ``ready`` or ``fetch_at_run_time``).

The fetch script **refuses** to download unless:

* the manifest's status is ``ready`` (licence verified and redistributable) or
  ``fetch_at_run_time``;
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
from urllib.parse import unquote, urlparse

MANIFEST_DIR = Path(__file__).resolve().parents[2] / "test_fixtures" / "media" / "datasets"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "edat.media.dataset/1"
STATUSES = ("ready", "fetch_at_run_time", "needs_loader", "blocked_licence")
FETCHABLE = ("ready", "fetch_at_run_time")
REDISTRIBUTABLE = ("yes", "share-alike", "unverified", "no")
GROUPS = ("speech_engine", "asr_eval", "noise_room", "sounds", "vision_eval", "fonts", "normaliser")
NEEDED_V1 = ("yes", "no", "if_confirmed")


class FetchRefused(RuntimeError):
    pass


def load_manifests(directory: Path = MANIFEST_DIR) -> dict[str, dict]:
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        m = json.loads(p.read_text())
        out[m["name"]] = m
    return out


def dest_relpath(f: dict) -> str:
    """Where a file lands under ``<dest>/<name>/``: ``path`` if given, else derived from the URL."""
    if f.get("path"):
        return f["path"]
    u = urlparse(f["url"])
    parts = [unquote(p) for p in u.path.split("/") if p]
    if u.netloc == "huggingface.co" and "resolve" in parts:
        parts = parts[parts.index("resolve") + 2:]  # keep the folder tree below the revision
    elif u.netloc == "media.githubusercontent.com" and len(parts) > 4:
        parts = parts[4:]  # media/<owner>/<repo>/<rev>/...
    elif u.netloc == "raw.githubusercontent.com" and len(parts) > 3:
        parts = parts[3:]  # <owner>/<repo>/<rev>/...
    elif parts and parts[-1] == "content":
        parts = parts[-2:-1]  # Zenodo .../files/<name>/content
    else:
        parts = parts[-1:]
    return "/".join(parts) or "download"


def file_bytes(f: dict) -> int:
    return int(f.get("bytes") or f.get("approx_bytes") or 0)


def check_manifest(m: dict) -> list[str]:
    """Problems with one manifest (empty when it is well formed)."""
    problems = []
    for key in ("schema", "name", "group", "suites", "homepage", "licence", "licence_url",
                "licence_verified", "redistributable", "access", "files", "status", "needed_v1"):
        if key not in m:
            problems.append(f"missing {key}")
    if m.get("schema") != SCHEMA:
        problems.append("wrong schema")
    if m.get("status") not in STATUSES:
        problems.append(f"bad status {m.get('status')!r}")
    if m.get("group") not in GROUPS:
        problems.append(f"bad group {m.get('group')!r}")
    if m.get("needed_v1") not in NEEDED_V1:
        problems.append(f"bad needed_v1 {m.get('needed_v1')!r}")
    if m.get("redistributable") not in REDISTRIBUTABLE:
        problems.append(f"bad redistributable {m.get('redistributable')!r}")
    if m.get("status") == "ready":
        if not m.get("licence_verified") or m.get("redistributable") not in ("yes", "share-alike"):
            problems.append("ready but the licence is not verified and redistributable")
        if m.get("access") != "http" or not m.get("files"):
            problems.append("ready needs http files")
        for f in m.get("files", []):
            if not isinstance(f.get("bytes"), int):
                problems.append(f"ready file has no bytes: {f.get('url')}")
            if not (f.get("md5") or f.get("sha256") or f.get("checksum") == "none published"):
                problems.append(f"ready file has no checksum or explicit note: {f.get('url')}")
    if m.get("status") == "fetch_at_run_time":
        if m.get("access") != "http" or not m.get("files"):
            problems.append("fetch_at_run_time needs http files")
        if m.get("never_commit") is not True:
            problems.append("fetch_at_run_time must say never_commit")
    seen = set()
    for f in m.get("files", []):
        if not str(f.get("url", "")).startswith("https://"):
            problems.append(f"file url is not https: {f.get('url')}")
        rel = dest_relpath(f) if f.get("url") else ""
        if rel.startswith("/") or ".." in Path(rel).parts:
            problems.append(f"unsafe destination path {rel!r}")
        if rel in seen:
            problems.append(f"two files land at {rel!r}")
        seen.add(rel)
        for algo, n in (("md5", 32), ("sha256", 64)):
            v = f.get(algo)
            if v is not None and (len(v) != n or any(c not in "0123456789abcdef" for c in v)):
                problems.append(f"bad {algo} {v!r}")
    if "bytes_total" in m and m["bytes_total"] != sum(file_bytes(f) for f in m.get("files", [])):
        problems.append("bytes_total does not match the files")
    return problems


def plan(name: str, dest: Path | None = None) -> dict:
    m = load_manifests()[name]
    reasons = []
    if m["status"] not in FETCHABLE:
        reasons.append(f"status is {m['status']}")
    if m["status"] == "ready":
        if not m["licence_verified"]:
            reasons.append("licence not verified")
        if m["redistributable"] not in ("yes", "share-alike"):
            reasons.append(f"redistributable: {m['redistributable']}")
    if dest is not None and _inside_repo(dest):
        reasons.append("destination is inside the repository")
    return {"name": name, "licence": m["licence"], "status": m["status"],
            "never_commit": m["status"] == "fetch_at_run_time", "files": m["files"],
            "approx_bytes": sum(file_bytes(f) for f in m["files"]),
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
    """Download a ``ready`` or ``fetch_at_run_time`` manifest's files into ``dest/name`` and verify them."""
    p = plan(name, dest)
    if not p["allowed"]:
        raise FetchRefused(f"{name}: " + "; ".join(p["refused_because"]))
    if not yes:
        raise FetchRefused(f"{name}: pass --yes to download about {p['approx_bytes'] / 1e9:.2f} GB")
    out_dir = Path(dest) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"name": name, "status": p["status"], "never_commit": p["never_commit"], "files": []}
    for f in p["files"]:
        target = out_dir / dest_relpath(f)
        target.parent.mkdir(parents=True, exist_ok=True)
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


def summary(manifests: dict[str, dict] | None = None) -> dict:
    """Files and bytes per group and status, and the v1 grand total.

    The v1 total counts manifests with ``needed_v1 == "yes"`` whose status is ``ready``
    or ``fetch_at_run_time``; files with no known size add to ``unsized_files``, not bytes.
    """
    ms = load_manifests() if manifests is None else manifests
    by_group: dict[str, dict[str, dict]] = {}
    by_status: dict[str, dict] = {}
    v1 = {"manifests": 0, "files": 0, "bytes": 0, "unsized_files": 0, "by_group": {}}
    total = {"manifests": 0, "files": 0, "bytes": 0}

    def bump(d, files, nbytes):
        d["manifests"] = d.get("manifests", 0) + 1
        d["files"] = d.get("files", 0) + files
        d["bytes"] = d.get("bytes", 0) + nbytes

    for m in ms.values():
        files = m.get("files", [])
        nbytes = sum(file_bytes(f) for f in files)
        group, status = m.get("group", "ungrouped"), m["status"]
        bump(by_group.setdefault(group, {}).setdefault(status, {}), len(files), nbytes)
        bump(by_status.setdefault(status, {}), len(files), nbytes)
        bump(total, len(files), nbytes)
        if m.get("needed_v1") == "yes" and status in FETCHABLE:
            bump(v1, len(files), nbytes)
            v1["unsized_files"] += sum(1 for f in files if not file_bytes(f))
            v1["by_group"][group] = v1["by_group"].get(group, 0) + nbytes
    return {"by_group": by_group, "by_status": by_status, "v1": v1, "total": total}


def format_summary(s: dict) -> str:
    rows = [f"{'group':14} {'status':18} {'manifests':>9} {'files':>7} {'bytes':>16} {'GB':>8}"]
    for group in sorted(s["by_group"]):
        for status in STATUSES:
            d = s["by_group"][group].get(status)
            if d:
                rows.append(f"{group:14} {status:18} {d['manifests']:9d} {d['files']:7d} "
                            f"{d['bytes']:16,d} {d['bytes'] / 1e9:8.2f}")
    t, v = s["total"], s["v1"]
    rows.append(f"{'all':14} {'(every status)':18} {t['manifests']:9d} {t['files']:7d} {t['bytes']:16,d} {t['bytes'] / 1e9:8.2f}")
    rows.append("")
    for group in sorted(v["by_group"]):
        rows.append(f"v1 {group:14} {v['by_group'][group]:16,d} {v['by_group'][group] / 1e9:8.2f} GB")
    rows.append(f"v1 GRAND TOTAL (needed_v1 yes, status ready or fetch_at_run_time): {v['manifests']} manifests, "
                f"{v['files']} files, {v['bytes']:,} bytes ({v['bytes'] / 1e9:.2f} GB)")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tinyagent-bench media download manifests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    ss = sub.add_parser("summary", help="files and bytes per group and status, and the v1 grand total")
    ss.add_argument("--json", action="store_true")
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
            print(f"{n:32} {m['group']:14} {m['status']:18} {m['licence']}")
        return 0
    if args.cmd == "summary":
        s = summary()
        print(json.dumps(s, indent=1) if args.json else format_summary(s))
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
