"""Model entries for the tinyagent-bench in-assistant evaluation (test_fixtures/productbench/models.json).

An entry is what one result row is about: a model, how it runs and the facts
that make results comparable. Kinds:

  local            a model served by the Mac's oMLX (Apple silicon only this round)
  cloud_reference  a cloud model through OpenRouter, run with the backend in
                   Harbor's remote mode. Shown apart, never ranked with local models.

Fields that join these results to the performance results: model_id, folder,
quantisation, weights_sha256, engine. `fits_16gb` decides whether a local entry
is ranked. `thinking` records which thinking regime ran ("harbor": Harbor's own
rules, tool steps think and narration does not; "narration-on": narration
thinks too). Ids never appear in code: an unknown --model is a local entry with
nothing known about it, and is marked unranked.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "test_fixtures" / "productbench" / "models.json"
KINDS = ("local", "cloud_reference")
THINKING = ("harbor", "narration-on")
_BANNED_CLOUD_IDS = re.compile(r":batch\b|~|-latest\b")


@dataclass
class Entry:
    id: str
    kind: str = "local"
    model_id: str = ""
    engine: str = "omlx"
    provider: str | None = None
    label: str = ""
    folder: str | None = None
    quantisation: str | None = None
    weights_sha256: str | None = None
    fits_16gb: bool | None = None
    thinking: str = "harbor"
    sampling: dict = field(default_factory=dict)
    accepts: dict = field(default_factory=dict)
    price_usd_per_mtok: dict = field(default_factory=dict)
    context_window: int | None = None
    id_confirmed: bool = True
    known: bool = True

    @property
    def cloud(self) -> bool:
        return self.kind == "cloud_reference"

    @property
    def ranked(self) -> bool:
        return self.kind == "local" and self.fits_16gb is True

    def variant(self) -> dict:
        return {"model_id": self.model_id, "folder": self.folder, "quantisation": self.quantisation,
                "weights_sha256": self.weights_sha256, "engine": self.engine, "provider": self.provider,
                "thinking": self.thinking, "fits_16gb": self.fits_16gb}

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        p = self.price_usd_per_mtok or {}
        if p.get("input") is None or p.get("output") is None:
            return None
        return (prompt_tokens * float(p["input"]) + completion_tokens * float(p["output"])) / 1e6


class ModelFileError(ValueError):
    pass


def load(path: str | Path | None = None) -> tuple[dict[str, Entry], list[str]]:
    """(entries by id, reference ids in order) from a models file."""
    data = json.loads(Path(path or DEFAULT_FILE).read_text(encoding="utf-8"))
    entries: dict[str, Entry] = {}
    for raw in data.get("entries", []):
        e = Entry(**{k: v for k, v in raw.items() if k in Entry.__dataclass_fields__})
        if e.kind not in KINDS:
            raise ModelFileError(f"{e.id}: kind must be one of {KINDS}")
        if e.thinking not in THINKING:
            raise ModelFileError(f"{e.id}: thinking must be one of {THINKING}")
        if not e.model_id:
            raise ModelFileError(f"{e.id}: model_id is required")
        if e.cloud:
            if e.provider != "openrouter":
                raise ModelFileError(f"{e.id}: cloud references run through OpenRouter only")
            if _BANNED_CLOUD_IDS.search(e.model_id):
                raise ModelFileError(f"{e.id}: no :batch variants or ~/-latest aliases")
        if e.id in entries:
            raise ModelFileError(f"duplicate entry {e.id}")
        entries[e.id] = e
    refs = list(data.get("references", []))
    for r in refs:
        if r not in entries:
            raise ModelFileError(f"reference {r!r} has no entry")
    return entries, refs


def resolve(name: str, entries: dict[str, Entry]) -> Entry:
    if name in entries:
        return entries[name]
    for e in entries.values():
        if e.model_id == name:
            return e
    if name.startswith("cloud/"):
        raise ModelFileError(f"{name}: a cloud reference must have an entry in the models file")
    return Entry(id=name, model_id=name, known=False)
