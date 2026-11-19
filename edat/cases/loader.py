"""Load case files.

Development cases (public) live under `test_fixtures/agentic/dev/`. Held-out
cases are private: they live under `$EDAT_PRIVATE_DIR/agentic/heldout/` and
are never committed. Files are YAML: a list of cases or
`{defaults: {...}, cases: [...]}`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

import yaml

from edat.cases.schema import CaseError

REPO = Path(__file__).resolve().parents[2]
DEV_DIR = REPO / "test_fixtures" / "agentic" / "dev"
PRIVATE_ENV = "EDAT_PRIVATE_DIR"


def private_dir() -> Path | None:
    p = os.environ.get(PRIVATE_ENV)
    return Path(p) if p else None


def heldout_dir() -> Path | None:
    p = private_dir()
    return p / "agentic" / "heldout" if p else None


def load_file(path: Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text()) or []
    if isinstance(data, dict):
        defaults = data.get("defaults") or {}
        cases = data.get("cases") or []
    else:
        defaults, cases = {}, data
    out = []
    for c in cases:
        merged = {**defaults, **c}
        merged.setdefault("split", "dev")
        out.append(merged)
    return out


def iter_files(paths: Iterable[str | Path]) -> Iterator[Path]:
    for p in paths:
        p = Path(p)
        if p.is_dir():
            yield from sorted(p.rglob("*.yaml"))
        elif p.exists():
            yield p


def iter_cases(paths: Iterable[str | Path]) -> Iterator[dict]:
    seen: dict[str, Path] = {}
    for f in iter_files(paths):
        for c in load_file(f):
            cid = c.get("id")
            if cid in seen:
                raise CaseError(f"duplicate case id {cid!r} in {f} and {seen[cid]}")
            seen[cid] = f
            yield c


def load_split(split: str = "dev", *, families: Iterable[str] | None = None,
               lanes: Iterable[str] | None = None) -> list[dict]:
    if split == "dev":
        roots = [DEV_DIR]
    elif split == "heldout":
        d = heldout_dir()
        if d is None:
            raise CaseError(f"held-out cases need {PRIVATE_ENV}")
        roots = [d]
    else:
        raise CaseError(f"unknown split {split!r}")
    fams = set(families) if families else None
    lns = set(lanes) if lanes else None
    return [c for c in iter_cases(roots)
            if (fams is None or c.get("family") in fams) and (lns is None or c.get("lane") in lns)]
