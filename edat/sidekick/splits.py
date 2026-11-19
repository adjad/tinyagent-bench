"""Development and held-out splits (spec 12 §3.5; 03 §1.4).

* **Development (25 %, public):** built with the public master seed and
  written to `test_fixtures/sidekick/cases/dev/<group>.json`: every case with
  its three trial instances, prompts, checks and reference answers. The
  200-input differential lists are replaced by their count and sha256 (the
  generator rebuilds them); the runner always rebuilds cases from the
  generator, and a test checks the export matches it.
* **Held-out (75 %, private):** the same generator with a private master
  seed kept at `$EDAT_PRIVATE_DIR/sidekick/master_seed` (created on first
  export) and written only under `$EDAT_PRIVATE_DIR/sidekick/heldout/`. It
  refuses to write inside the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

from edat.sidekick import GENERATOR_VERSION
from edat.sidekick.gen.core import PUBLIC_MASTER_SEED, build_cases, counts_table, materialize

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "test_fixtures" / "sidekick" / "cases" / "dev"


def _slim(inst: dict) -> dict:
    d = json.loads(json.dumps(inst))
    di = d.get("check", {}).get("diff_inputs")
    if di:
        blob = json.dumps(di, sort_keys=True).encode()
        d["check"]["diff_inputs"] = {"n": len(di), "sha256": hashlib.sha256(blob).hexdigest(), "regenerate": True}
    return d


def export_cases(split: str, seed: str, out: Path, trials: int = 3) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list] = {}
    for c in build_cases(split):
        groups.setdefault(c.group, []).append({
            "meta": c.meta(),
            "trials": {str(t): _slim(materialize(c, t, seed).to_dict()) for t in range(1, trials + 1)},
        })
    for g, rows in groups.items():
        (out / f"{g}.json").write_text(json.dumps({"generator": GENERATOR_VERSION, "split": split, "group": g,
                                                   "cases": rows}, indent=1, sort_keys=True) + "\n")
    counts = counts_table(build_cases(split))
    (out / "counts.json").write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n")
    return counts


def export_dev(out: str | None = None) -> str:
    target = Path(out) if out else DEV_DIR
    counts = export_cases("dev", PUBLIC_MASTER_SEED, target)
    return f"development split: {counts['total']['full']} cases written to {target}"


def private_dir() -> Path:
    priv = os.environ.get("EDAT_PRIVATE_DIR")
    if not priv:
        raise SystemExit("set EDAT_PRIVATE_DIR to the private folder; held-out cases never go in the repository")
    p = Path(priv).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit("EDAT_PRIVATE_DIR is inside the repository; refusing to write held-out cases there")
    return p


def private_seed(create: bool = True) -> str:
    p = private_dir() / "sidekick" / "master_seed"
    if not p.exists():
        if not create:
            raise SystemExit("no private master seed")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(16) + "\n")
        p.chmod(0o600)
    return p.read_text().strip()


def export_heldout() -> str:
    seed = private_seed()
    target = private_dir() / "sidekick" / "heldout"
    counts = export_cases("heldout", seed, target)
    return f"held-out split: {counts['total']['full']} cases written under $EDAT_PRIVATE_DIR/sidekick/heldout"
