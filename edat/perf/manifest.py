"""The model manifest: which models a run uses and how each is served (spec 04 §11 item 1, §3.4).

One JSON file replaces the hard-coded maps of ``scripts/bench_ondevice_perf.py``. Each
entry is one model **entry** (thinking-on and thinking-off are separate entries, D5)::

    {"id": "Gemma-Wisp", "entry_id": "Gemma-Wisp", "role": "baseline", "thinking": "on",
     "headline_entry": true,
     "engine_ids": {"as_shipped": "Gemma-Wisp", "plain": "Gemma-Wisp-plain"},
     "pack": "packs/Gemma-Wisp.pack.json",
     "model_dir": null,                       # if set, files are hashed from it
     "files": [{"name": …, "sha256": …, "bytes": …}],   # pinned (Gemma-Wisp: catalog.py)
     "quant": {"scheme": "oQ4e", "bits": 4, "group_size": 64, "mixed": true},
     "architecture": "dense_ple", "native_window_tokens": 131072, "profile_window_tokens": 65792,
     "speculation": "mtp", "drafter": {"id": "mtp", "params": null, "bytes": 159139040, "max_depth": 4},
     "kv_config": {…config.json subset…}, "active_weight_bytes": null,
     "extension_factor": null}

``engine_ids`` maps each mode to the id the **benchmark** oMLX instance serves (D12):
``plain`` is a second model id with MTP off in its own settings (§1.4 rule 1; whether oMLX
serves a symlinked second id is **unverified**). A model with no drafter has only
``as_shipped``, which then also counts as ``plain``.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import BASELINE_ID, MODES, REFERENCE_ID, ROLES

RECORD_KEYS = ("id", "entry_id", "role", "variant_of", "headline_entry", "files", "quant", "architecture",
               "params_total", "params_active", "params_embedding", "n_experts", "n_experts_active",
               "native_window_tokens", "profile_window_tokens", "thinking", "speculation", "drafter",
               "template_sha256", "tokenizer_sha256", "kv_config", "active_weight_bytes",
               "resident_weight_bytes", "uses_ane")


@dataclass
class ModelEntry:
    id: str
    entry_id: str
    role: str
    thinking: str
    engine_ids: dict[str, str]
    pack_path: str | None
    native_window_tokens: int | None
    profile_window_tokens: int | None
    extension_factor: float | None
    raw: dict = field(default_factory=dict)

    def modes(self) -> list[str]:
        return [m for m in MODES if m in self.engine_ids] or ["as_shipped"]

    def engine_id(self, mode: str) -> str:
        if mode in self.engine_ids:
            return self.engine_ids[mode]
        if mode == "plain" and self.raw.get("speculation") in (None, "none"):
            return self.engine_ids["as_shipped"]
        raise KeyError(f"{self.entry_id} has no {mode} engine id")

    def record(self, mode: str) -> dict:
        """The §3.4 ``models[]`` object for this entry in one mode."""
        r = {k: self.raw.get(k) for k in RECORD_KEYS}
        r.update({"id": self.id, "entry_id": self.entry_id, "role": self.role, "mode": mode,
                  "thinking": self.thinking, "engine_id": self.engine_id(mode)})
        if mode == "plain":
            r["speculation"] = "none"
        return r


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_model_dir(model_dir: str | os.PathLike, names: list[str] | None = None) -> list[dict]:
    """sha256 and bytes of each file (follows symlinks). Weights only by default."""
    d = Path(model_dir)
    files = sorted(p for p in d.iterdir() if p.is_file() and (names is None or p.name in names))
    if names is None:
        files = [p for p in files if p.suffix in (".safetensors", ".json", ".jinja", ".model")]
    return [{"name": p.name, "sha256": _hash_file(p), "bytes": p.stat().st_size} for p in files]


def verify_pinned(found: list[dict], pinned: list[dict]) -> list[str]:
    """Names whose hash or size differ from the pinned list (empty = all match)."""
    by = {f["name"]: f for f in found}
    bad = []
    for p in pinned:
        f = by.get(p["name"])
        if not f or f["sha256"] != p["sha256"] or int(f["bytes"]) != int(p["bytes"]):
            bad.append(p["name"])
    return bad


def load_manifest(path: str | os.PathLike, *, hash_files: bool = False) -> list[ModelEntry]:
    data = json.loads(Path(path).read_text())
    entries = data["models"] if isinstance(data, dict) else data
    base = Path(path).parent
    out = []
    seen = set()
    for e in entries:
        role = e.get("role") or ("baseline" if e["id"] == BASELINE_ID else
                                 "reference" if e["id"] == REFERENCE_ID else "candidate")
        if role not in ROLES:
            raise ValueError(f"{e['id']}: unknown role {role}")
        entry_id = e.get("entry_id") or e["id"]
        if entry_id in seen:
            raise ValueError(f"duplicate entry_id {entry_id}")
        seen.add(entry_id)
        thinking = e.get("thinking") or "on"
        if thinking not in ("on", "off"):
            raise ValueError(f"{entry_id}: thinking must be on or off (separate entries, D5)")
        raw = dict(e)
        if hash_files and e.get("model_dir"):
            found = hash_model_dir(e["model_dir"])
            if e.get("files"):
                bad = verify_pinned(found, e["files"])
                if bad:
                    raise ValueError(f"{entry_id}: files differ from the pinned hashes: {', '.join(bad)}")
            raw["files"] = found
        pack = e.get("pack")
        out.append(ModelEntry(id=e["id"], entry_id=entry_id, role=role, thinking=thinking,
                              engine_ids=dict(e.get("engine_ids") or {"as_shipped": e["id"]}),
                              pack_path=str((base / pack).resolve()) if pack else None,
                              native_window_tokens=e.get("native_window_tokens"),
                              profile_window_tokens=e.get("profile_window_tokens"),
                              extension_factor=e.get("extension_factor"), raw=raw))
    return out


class PackPrompts:
    """Prompt source over an ``edat.prompt-pack/0.1`` file (built by ``edat.perf.pack``)."""

    def __init__(self, pack: dict):
        self.pack = pack
        self.sha256 = pack.get("pack_sha256")
        self.corpus = pack.get("corpus")
        self.id = pack.get("entry_id") or pack.get("model_id")

    @classmethod
    def load(cls, path: str) -> "PackPrompts":
        from . import pack as packmod
        return cls(packmod.load_pack(path))

    def level_tokens(self) -> tuple[int, ...]:
        return tuple(sorted(lv["level_tokens"] for lv in self.pack["levels"]))

    def n_variants(self, level: int) -> int:
        for lv in self.pack["levels"]:
            if lv["level_tokens"] == level:
                return len(lv["variants"])
        return 0

    def level_prompt(self, level: int, variant: int) -> dict:
        from . import pack as packmod
        ids, text = packmod.prompt(self.pack, level, variant)
        return {"text": text, "messages": None, "tokens": len(ids)}

    def first_token_prompt(self) -> dict:
        ft = self.pack["first_token"]
        return {"text": ft.get("text"), "messages": None, "tokens": len(ft["ids"])}

    def replay_variants(self) -> int:
        return len((self.pack.get("agent_replay") or {}).get("variants") or ()) or 1

    def replay_step(self, variant: int, step: int) -> dict:
        from . import pack as packmod
        ids, text = packmod.replay_step(self.pack, variant, step)
        return {"text": text, "messages": None, "tokens": len(ids)}

    def info(self) -> dict[str, Any]:
        return {"pack_sha256": self.sha256, "corpus": self.corpus, "version": self.pack.get("version"),
                "language": self.pack.get("language")}
