"""Build the benchmark user profiles: python -m scripts.bench_profiles.build [ids...]

Writes test_fixtures/bench_profiles/<id>/{profile.json,ledger.json,files/...} and
index.json. Deterministic: the same source gives the same bytes. Optionally also writes
the viewer's data bundle (``--viewer-data PATH``), rendered for each anchor date.
"""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from scripts.bench_profiles.render import FIXTURES, PROFILE_IDS, render, render_ledger
from datetime import date


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def counts(profile: dict, ledger: dict) -> dict:
    msgs = sum(len(t["messages"]) for t in profile["threads"])
    return {
        "contacts": len(profile["contacts"]), "threads": len(profile["threads"]),
        "messages": msgs, "emails": len(profile["emails"]), "events": len(profile["events"]),
        "reminders": len(profile["reminders"]), "notes": len(profile["notes"]),
        "documents": len(profile["documents"]), "browser": len(profile["browser"]),
        "memory": len(profile["memory"]), "ledger": len(ledger["entries"]),
        "ledger_by_type": dict(sorted(Counter(e["type"] for e in ledger["entries"]).items())),
    }


def build_one(pid: str) -> tuple[dict, dict]:
    mod = importlib.import_module(f"scripts.bench_profiles.profiles.{pid}")
    b = mod.build()
    return b.profile_dict(), b.ledger_dict()


def _file_path(doc: dict) -> str:
    rel = doc["path"].removeprefix("~/")
    stem, _, ext = rel.rpartition(".")
    if doc["kind"] in ("csv",):
        return rel
    if doc["kind"] in ("markdown", "text"):
        return rel
    # binary nominal formats (docx/pdf/xlsx) are written as their text
    return f"{stem}.{ 'csv' if doc['kind'] == 'xlsx' else 'md'}" if stem else rel + ".md"


def write(pid: str, profile: dict, ledger: dict, out: Path = FIXTURES) -> None:
    base = out / pid
    base.mkdir(parents=True, exist_ok=True)
    (base / "profile.json").write_text(_dump(profile), encoding="utf-8")
    (base / "ledger.json").write_text(_dump(ledger), encoding="utf-8")
    files = base / "files"
    if files.exists():
        shutil.rmtree(files)
    anchor = date.fromisoformat(profile["anchor"]["date"])
    rendered = render(profile)
    for doc in rendered["documents"]:
        p = files / _file_path(doc)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(doc["content"].rstrip("\n") + "\n", encoding="utf-8")
    _ = anchor


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", default=list(PROFILE_IDS))
    ap.add_argument("--viewer-data", type=Path, default=None,
                    help="also write a JS data bundle for the profiles viewer")
    args = ap.parse_args(argv)
    index = {}
    idx_path = FIXTURES / "index.json"
    if idx_path.exists():
        index = {p["profile_id"]: p for p in json.loads(idx_path.read_text())["profiles"]}
    bundle = {}
    for pid in args.ids:
        profile, ledger = build_one(pid)
        write(pid, profile, ledger)
        c = counts(profile, ledger)
        index[pid] = {"profile_id": pid, "label": profile["label"],
                      "name": profile["person"].get("name"), "anchor": profile["anchor"],
                      "counts": c}
        print(pid, json.dumps(c))
    ordered = [index[p] for p in PROFILE_IDS if p in index]
    idx_path.write_text(_dump({"schema_version": 1, "synthetic": True, "profiles": ordered}),
                        encoding="utf-8")
    if args.viewer_data:
        for pid in PROFILE_IDS:
            if not (FIXTURES / pid / "profile.json").exists():
                continue
            prof = json.loads((FIXTURES / pid / "profile.json").read_text())
            led = json.loads((FIXTURES / pid / "ledger.json").read_text())
            anchor = date.fromisoformat(prof["anchor"]["date"])
            bundle[pid] = {"profile": render(prof, tz=prof["anchor"]["tz"]),
                           "ledger": render_ledger(led, anchor)}
        args.viewer_data.parent.mkdir(parents=True, exist_ok=True)
        args.viewer_data.write_text("window.BENCH_PROFILES = " +
                                    json.dumps(bundle, ensure_ascii=False) + ";\n",
                                    encoding="utf-8")
        print("viewer data ->", args.viewer_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
