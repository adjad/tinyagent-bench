"""Size a checkpoint's vision and audio parts from its weight-file headers (spec 05 §9.4, M1).

Reads only the JSON header at the start of each ``.safetensors`` file (8-byte
little-endian length, then JSON with each tensor's dtype, shape and byte
offsets) and ``config.json``. It never reads tensor data and never loads a
model, so it runs on any machine in milliseconds.

Output: bytes, tensor count, dtypes and (for unquantised tensors) parameters per
component, and the weights each loadable configuration keeps:

======  =====================================================================
T+V+A   everything
T+V     drops the audio encoder and audio projector (oMLX: a config copy
        without ``audio_config``; mlx-swift-lm 3.32.3 does this at load)
T       drops both encoders, both projectors and the MTP head (oMLX's text
        path, ``model_type_override: "llm"``, also strips ``mtp.*``)
======  =====================================================================

Component rules are prefix matches after removing a leading ``model.``; they
cover Gemma 4 / Gemma 3n names and common VLM names (``vision_tower``,
``visual``, ``vision_model``, ``audio_tower``, ``audio_encoder``,
``multi_modal_projector``, ``embed_vision``, ``embed_audio``). Unknown names go
to ``other`` and are listed, so a new family's layout is visible rather than
silently misfiled.

``python -m edat.media.checkpoint <checkpoint dir> [--json]``
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_HEADER = 100 * 1024 * 1024

DTYPE_BYTES = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4, "U32": 4, "I16": 2,
               "U16": 2, "I8": 1, "U8": 1, "BOOL": 1, "F8_E4M3": 1, "F8_E5M2": 1}
FLOAT_DTYPES = {"F64", "F32", "F16", "BF16", "F8_E4M3", "F8_E5M2"}

COMPONENTS = ("audio_encoder", "audio_projector", "vision_encoder", "vision_projector",
              "per_layer_embeddings", "token_embeddings", "decoder_layers", "mtp_head",
              "lm_head", "norms_other_text", "other")

_RULES: tuple[tuple[str, str], ...] = (
    (r"^(language_model\.)?(model\.)?mtp\.|^mtp\.", "mtp_head"),
    (r"(^|\.)(audio_tower|audio_encoder|audio_model|audio)\.", "audio_encoder"),
    (r"(^|\.)(embed_audio|audio_projector|audio_projection|multi_modal_projector_audio)\.",
     "audio_projector"),
    (r"(^|\.)(vision_tower|vision_model|visual|vision_encoder|image_encoder)\.", "vision_encoder"),
    (r"(^|\.)(embed_vision|multi_modal_projector|mm_projector|vision_projector|"
     r"vision_embedder|connector)\.", "vision_projector"),
    (r"embed_tokens_per_layer|per_layer_(model_)?projection|per_layer_projection_norm",
     "per_layer_embeddings"),
    (r"(^|\.)embed_tokens\.", "token_embeddings"),
    (r"(^|\.)lm_head\.", "lm_head"),
    (r"(^|\.)layers\.\d+\.", "decoder_layers"),
    (r"(^|\.)(norm|final_norm|final_layernorm|altup|laurel)", "norms_other_text"),
)

CONFIG_DROPS = {
    "T+V+A": set(),
    "T+V": {"audio_encoder", "audio_projector"},
    "T": {"audio_encoder", "audio_projector", "vision_encoder", "vision_projector", "mtp_head"},
}


def classify(name: str) -> str:
    n = name.removeprefix("model.")
    for pattern, comp in _RULES:
        if re.search(pattern, n):
            return comp
    return "other"


@dataclass
class Tensor:
    name: str
    file: str
    dtype: str
    shape: list[int]
    nbytes: int
    component: str


@dataclass
class ComponentSummary:
    component: str
    tensors: int = 0
    bytes: int = 0
    dtypes: dict = field(default_factory=dict)        # dtype -> bytes
    float_params: int = 0                              # elements in float tensors (BF16 towers; also quant scales)
    quantised_bytes: int = 0                           # bytes in packed integer tensors


def read_header(path: Path) -> tuple[dict, int]:
    """The safetensors header dict and the data section's start offset."""
    with Path(path).open("rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise ValueError(f"{path.name}: file is shorter than a safetensors header")
        size = struct.unpack("<Q", head)[0]
        if size > MAX_HEADER:
            raise ValueError(f"{path.name}: header of {size} bytes is too large")
        raw = f.read(size)
        if len(raw) < size:
            raise ValueError(f"{path.name}: header is cut short")
    return json.loads(raw), 8 + size


def tensors_in(path: Path) -> list[Tensor]:
    header, _ = read_header(path)
    out = []
    for name, info in header.items():
        if name == "__metadata__":
            continue
        start, end = info["data_offsets"]
        nbytes = end - start
        shape = list(info.get("shape", []))
        width = DTYPE_BYTES.get(info["dtype"])
        if width is not None:
            elems = 1
            for d in shape:
                elems *= d
            if elems * width != nbytes:
                raise ValueError(f"{path.name}:{name}: offsets give {nbytes} bytes, "
                                 f"shape and dtype give {elems * width}")
        out.append(Tensor(name, path.name, info["dtype"], shape, nbytes, classify(name)))
    return out


def weight_files(root: Path) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root]
    files = sorted(root.glob("*.safetensors"))
    if not files:
        raise FileNotFoundError(f"no .safetensors files in {root}")
    return files


def inspect(root: Path) -> dict:
    """Component sizes and per-configuration totals for a checkpoint folder or one file."""
    root = Path(root)
    tensors: list[Tensor] = []
    for f in weight_files(root):
        tensors.extend(tensors_in(f))
    comps: dict[str, ComponentSummary] = {c: ComponentSummary(c) for c in COMPONENTS}
    for t in tensors:
        c = comps[t.component]
        c.tensors += 1
        c.bytes += t.nbytes
        c.dtypes[t.dtype] = c.dtypes.get(t.dtype, 0) + t.nbytes
        if t.dtype in FLOAT_DTYPES:
            c.float_params += t.nbytes // DTYPE_BYTES[t.dtype]
        else:
            c.quantised_bytes += t.nbytes
    total = sum(c.bytes for c in comps.values())
    config = {}
    cfg_path = (root if root.is_dir() else root.parent) / "config.json"
    if cfg_path.is_file():
        try:
            config = json.loads(cfg_path.read_text())
        except ValueError:
            config = {}
    text_cfg = config.get("text_config", config)
    configs = {name: total - sum(comps[c].bytes for c in drops)
               for name, drops in CONFIG_DROPS.items()}
    return {
        "schema": "edat.media.checkpoint/1",
        "files": [{"name": f.name, "bytes": f.stat().st_size} for f in weight_files(root)],
        "total_tensor_bytes": total,
        "components": {k: asdict(v) for k, v in comps.items() if v.tensors},
        "configurations": configs,
        "declares": {"vision_config": bool(config.get("vision_config")),
                     "audio_config": bool(config.get("audio_config")),
                     "quantization": config.get("quantization") or text_cfg.get("quantization")},
        "has_towers": {"vision": comps["vision_encoder"].tensors > 0,
                       "audio": comps["audio_encoder"].tensors > 0},
        "other_tensors": sorted(t.name for t in tensors if t.component == "other")[:50],
    }


def strip_plan(root: Path, configuration: str) -> dict:
    """Which tensors and config sections a stripped copy would drop (names only; writes nothing)."""
    if configuration not in CONFIG_DROPS:
        raise ValueError(f"unknown configuration {configuration!r}")
    drops = CONFIG_DROPS[configuration]
    tensors = [t for f in weight_files(root) for t in tensors_in(f)]
    drop = sorted(t.name for t in tensors if t.component in drops)
    sections = []
    if "audio_encoder" in drops:
        sections.append("audio_config")
    if "vision_encoder" in drops:
        sections.append("vision_config")
    return {"configuration": configuration, "drop_tensors": drop,
            "drop_bytes": sum(t.nbytes for t in tensors if t.component in drops),
            "drop_config_sections": sections, "keep_tensors": len(tensors) - len(drop)}


def _mb(n: int) -> str:
    return f"{n / 1e6:,.1f} MB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Size vision and audio parts from safetensors headers.")
    ap.add_argument("checkpoint", help="checkpoint folder or one .safetensors file")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strip-plan", choices=sorted(CONFIG_DROPS))
    args = ap.parse_args(argv)
    if args.strip_plan:
        print(json.dumps(strip_plan(Path(args.checkpoint), args.strip_plan), indent=1))
        return 0
    rep = inspect(Path(args.checkpoint))
    if args.json:
        print(json.dumps(rep, indent=1))
        return 0
    total = rep["total_tensor_bytes"]
    print(f"{'component':24} {'tensors':>8} {'size':>14} {'share':>7}  dtypes")
    for name, c in rep["components"].items():
        dts = ", ".join(sorted(c["dtypes"]))
        print(f"{name:24} {c['tensors']:>8} {_mb(c['bytes']):>14} {c['bytes'] / total:>6.1%}  {dts}")
    print(f"{'total':24} {'':>8} {_mb(total):>14}")
    for name, b in rep["configurations"].items():
        print(f"configuration {name:6} keeps {_mb(b)}")
    if rep["other_tensors"]:
        print(f"unclassified tensors (first {len(rep['other_tensors'])}): "
              + ", ".join(rep["other_tensors"][:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
