"""The small local models for the coding-ceiling run, found in oMLX's model folder at run time.

`scan()` reads the folder (files only; it never asks the engine) and returns every complete
chat model that fits, plus every folder it skipped and why:

* **complete**: `config.json`, a chat template, a tokenizer, and every weight shard the index
  names (or a `*.safetensors` file); nothing still downloading (`*.incomplete`, `*.part`,
  `*.download`, Hugging Face's `.cache/huggingface/download`);
* **a chat model**: not an embedder, reranker, drafter (`*_assistant`, `*_mtp`, DFlash),
  adapter, or a model with no chat template (an image generator, a bare checkpoint);
* **small**: at most `MAX_WEIGHTS_GB` of weights (the 9B upper bound is 10.7 GB).

Whether oMLX can serve it is checked twice: here, against the loaders in the installed oMLX
(a type with no loader, or one that needs remote code, is kept but marked unverified), and at run
time, when the engine has to list the id; an entry the engine cannot serve is reported as
skipped, never a failed run.

Each model records its quantisation (method, nominal bits, group size, whether mixed, and the
bits per weight measured from the weight files). Where the same model is on disk at several
quantisations, the one closest to 4 bits is the standard entry and the others run after.

Sampling is sent by the runner itself (never left to oMLX's per-model settings), from
`SAMPLING` below: each family's author-recommended setting per thinking mode. Where an author
gives a separate coding setting (Qwen3.5 and its derivatives: "precise coding" with thinking),
it is a labelled second entry. Where an author gives none, the author's sample-code setting
(greedy) is the standard entry and the fixed sensitivity setting (temperature 0.6, top-p 0.95)
runs beside it. Every record stores what was sent.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAX_WEIGHTS_GB = 11.0
OMLX_SITE = Path("/Applications/oMLX.app/Contents/Resources/Python/framework-mlx-base/lib/python3.11/site-packages")
DEFAULT_GPU_SHARE_16GB = 2 / 3      # macOS lets the GPU wire about two-thirds of RAM by default on a 16 GB Mac
FIXED = {"temperature": 0.6, "top_p": 0.95}
GREEDY = {"temperature": 0.0}


@dataclass(frozen=True)
class Profile:
    name: str                 # author | coding | greedy | fixed
    sampling: dict
    source: str               # the record's sampling_source: author_recommended | fixed_sensitivity
    basis: str


def _p(name, sampling, basis, source="author_recommended") -> Profile:
    return Profile(name, dict(sampling), source, basis)


_QWEN_ON = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "repetition_penalty": 1.0}
_QWEN_CODE = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0}
_QWEN_OFF = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5}
_GEMMA = {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
NO_RECOMMENDATION = [_p("greedy", GREEDY, "no author recommendation: the author's sample code (greedy)"),
                     _p("fixed", FIXED, "fixed sensitivity setting", "fixed_sensitivity")]

# family -> {thinking on: [profiles], thinking off: [profiles]}; the first profile is the standard entry.
SAMPLING: dict[str, dict[bool, list[Profile]]] = {
    "gemma4": {t: [_p("author", _GEMMA, "Google: the same for every size and both modes")] for t in (True, False)},
    "nemotron_bolt": {t: [_p("author", _GEMMA, "NVIDIA card: temperature 1.0, top_p 0.95 in both modes; top_k 64 from generation_config")]
                      for t in (True, False)},
    "qwen3_5": {True: [_p("author", _QWEN_ON, "Qwen3.5 card, thinking"),
                       _p("coding", _QWEN_CODE, "Qwen3.5 card, precise coding with thinking")],
                False: [_p("author", _QWEN_OFF, "Qwen3.5 card, thinking off")]},
    "ornith": {True: [_p("author", _QWEN_ON, "Ornith card (Qwen3.5 base), general tasks"),
                      _p("coding", _QWEN_CODE, "Ornith card, precise coding tasks")],
               False: [_p("author", _QWEN_OFF, "Qwen3.5 card, thinking off (the Ornith card gives none)")]},
    "lfm3": {t: [_p("author", {"temperature": 0.1, "top_k": 50, "repetition_penalty": 1.1},
                    "card: general use including coding")] for t in (True, False)},
    "lfm3_omni": {t: [_p("author", GREEDY, "card: text answers use greedy decoding")] for t in (True, False)},
    "spark2_5": {t: [_p("author", {"temperature": 1.0, "top_p": 0.95}, "card and generation_config")] for t in (True, False)},
    "ling": {True: [_p("author", {"temperature": 1.0, "top_p": 0.95, "top_k": 20}, "Ling card, thinking")],
             False: NO_RECOMMENDATION},
    "phi": {False: NO_RECOMMENDATION},
    "sensenova": {t: NO_RECOMMENDATION for t in (True, False)},
}
# Known facts a folder cannot tell: label, role, the thinking mode it ships in.
KNOWN = {
    "gemma-4-E4B-it-oQ4e-mtp": ("Gemma 4 E4B (base reference)", "reference", False),
    "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp": ("Gemma-Wisp r5 (tuned reference)", "baseline", False),
    # Nemotron: its model card says thinking is on by default; the chat template it ships with defaults to off.
    # The card is followed, and the difference is recorded with every result (template_thinking_default).
    "NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-oQ4e-mtp": ("Nemotron 3.5 Bolt E4B", "candidate", True),
    "NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-oQ4e": ("Nemotron 3.5 Bolt E4B (no MTP head)", "candidate", True),
    "Ornith-1.5-9B-oQ8e-mtp": ("Ornith 1.5 9B (upper bound)", "candidate", True),
    "LFM3-1.7B-MLX-oQ6e": ("LFM3 1.7B", "candidate", True),
    "LFM3-Omni-2B-MLX-8bit": ("LFM3 Omni 2B", "candidate", False),
    "Spark-X2.6-4B-mlx-4bit": ("Spark X2.6 4B", "candidate", True),
    "Spark-X2.6-4B-MLX-6bit": ("Spark X2.6 4B", "candidate", True),
}
# By name prefix, for builds not listed above (other quantisations, new downloads): label, ships thinking.
FAMILY_LABELS = (
    ("gemma-4-e2b", "Gemma 4 E2B", False), ("gemma-4-12b", "Gemma 4 12B", False), ("gemma-4-e4b", "Gemma 4 E4B", False),
    ("qwen3.5-4b", "Qwen3.5 4B", True), ("qwen3.5-9b", "Qwen3.5 9B", True), ("phi-4-mini", "Phi-4-mini", False),
    ("sensenova-loop", "SenseNova-Loop 9B", True), ("ling-3.0-tiny", "Ling 3.0 tiny", True),
    ("nvidia-nemotron-3.5-bolt", "Nemotron 3.5 Bolt E4B", True), ("ornith-1.5-9b", "Ornith 1.5 9B", True),
    ("lfm3-omni", "LFM3 Omni 2B", False), ("lfm3-1.7b", "LFM3 1.7B", True), ("spark-x2.6-4b", "Spark X2.6 4B", True),
)
FIRST = ("gemma-4-E4B-it-oQ4e-mtp", "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp")   # the two references run first
HELPER_WORDS = ("embed", "rerank", "colbert", "dflash", "drafter", "donor")


@dataclass(frozen=True)
class LocalModel:
    engine_id: str
    folder: str
    label: str
    role: str
    model_type: str
    family: str
    loader: str                       # mlx-lm | mlx-vlm | unverified (no loader found, or remote code)
    disk_gb: float
    quant: dict                       # results-record quant: scheme, bits, group_size, mixed
    bits_per_weight: float | None
    thinking: tuple[bool, ...]        # supported modes, the one it ships in first
    standard: bool                    # the standard quantisation of its model (closest to 4 bits)
    group: str                        # same model at other quantisations shares this
    decode_tok_s: float
    notes: str = ""
    template_thinking: bool | None = None   # the chat template's own default, where it has both modes

    def profiles(self, thinking: bool) -> list[Profile]:
        fam = SAMPLING.get(self.family) or {}
        return list(fam.get(thinking) or NO_RECOMMENDATION)

    def entries(self) -> list["Entry"]:
        out = []
        for i, th in enumerate(self.thinking):
            for j, prof in enumerate(self.profiles(th)):
                out.append(Entry(self, th, prof, shipped=(i == 0), primary=(j == 0)))
        return out

    def fits_16gb(self) -> bool:
        return self.disk_gb < 16 * DEFAULT_GPU_SHARE_16GB - 2.5     # room for the context and the app


@dataclass(frozen=True)
class Entry:
    model: LocalModel
    thinking: bool
    profile: Profile
    shipped: bool                     # the thinking mode the model ships in
    primary: bool                     # the standard sampling for that mode

    @property
    def key(self) -> str:
        return f"{self.model.engine_id}|thinking-{'on' if self.thinking else 'off'}|{self.profile.name}"

    @property
    def slug(self) -> str:
        return f"{self.model.engine_id}.think-{'on' if self.thinking else 'off'}.{self.profile.name}"

    @property
    def label(self) -> str:
        extra = {"author": "", "coding": ", coding sampling", "greedy": ", greedy", "fixed": ", fixed sampling"}
        q = self.model.quant.get("scheme")
        return f"{self.model.label} [{q}], thinking {'on' if self.thinking else 'off'}{extra[self.profile.name]}"

    @property
    def tier(self) -> int:
        """Run order: 0 standard entries; 1 the other thinking mode; 2 second sampling settings; 3 other quantisations."""
        if not self.model.standard:
            return 3
        if not self.primary:
            return 2
        return 0 if self.shipped else 1


# ------------------------------------------------------------------ reading a folder

def _family(model_type: str, name: str) -> str:
    n = name.lower()
    if "nemotron" in n:
        return "nemotron_bolt"
    if "ornith" in n:
        return "ornith"
    if "sensenova" in n or model_type.startswith("sensenova"):
        return "sensenova"
    if "ling" in n.split("-")[0] or model_type.startswith("bailing"):
        return "ling"
    if model_type.startswith(("phi", "phi3", "phi4")):
        return "phi"
    if model_type.startswith("gemma4"):
        return "gemma4"
    if model_type.startswith("qwen3_5"):
        return "qwen3_5"
    if model_type == "lfm3_omni":
        return "lfm3_omni"
    if model_type.startswith("lfm3"):
        return "lfm3"
    if model_type.startswith("spark"):
        return "spark2_5"
    return model_type or "unknown"


def _template(folder: Path) -> str | None:
    for name in ("chat_template.jinja", "chat_template.json"):
        p = folder / name
        if p.is_file():
            return p.read_text(errors="replace")
    try:
        t = json.loads((folder / "tokenizer_config.json").read_text()).get("chat_template")
    except (OSError, ValueError):
        return None
    if isinstance(t, list):
        t = next((x.get("template") for x in t if isinstance(x, dict) and x.get("name") == "default"), None)
    return t if isinstance(t, str) else None


def thinking_modes(template: str, ships: bool | None) -> tuple[bool, ...]:
    """Modes the template supports, the shipped one first (from KNOWN, else the template's default)."""
    if "enable_thinking" not in template and "<think>" not in template:
        return (False,)
    if ships is None:
        if re.search(r"enable_thinking\s*\|\s*default\(\s*true", template, re.I) or \
                "enable_thinking is defined and enable_thinking is false" in template:
            ships = True
        elif re.search(r"enable_thinking\s*\|\s*default\(\s*false", template, re.I):
            ships = False
        else:
            ships = "<think>" in template
    return (ships, not ships)


def template_default(template: str) -> bool | None:
    """The thinking mode the chat template itself defaults to; None when it has only one mode."""
    modes = thinking_modes(template, None)
    return modes[0] if len(modes) == 2 else None


def _mode_note(ships: bool, template: bool | None) -> str | None:
    if template is None or template == ships:
        return None
    on = lambda b: "on" if b else "off"   # noqa: E731
    return f"ships with thinking {on(ships)} per its model card; its chat template defaults to {on(template)}"


def _loader(model_type: str, cfg: dict, folder: Path) -> str:
    vlm = OMLX_SITE / "mlx_vlm" / "models"
    lm = OMLX_SITE / "mlx_lm" / "models"
    if not OMLX_SITE.is_dir():
        return "unverified"
    if (vlm / model_type).is_dir():
        return "mlx-vlm"
    if (lm / f"{model_type}.py").is_file():
        return "mlx-lm"
    return "unverified"


def _safetensor_header(path: Path) -> dict:
    with path.open("rb") as fh:
        n = struct.unpack("<Q", fh.read(8))[0]
        return json.loads(fh.read(n))


def quantisation(folder: Path, cfg: dict) -> tuple[dict, float | None]:
    """(results-record quant block, measured bits per weight)."""
    q = cfg.get("quantization") or cfg.get("quantization_config") or (cfg.get("text_config") or {}).get("quantization") or {}
    bits = q.get("bits") if isinstance(q, dict) else None
    gs = q.get("group_size") if isinstance(q, dict) else None
    overrides = {k: v for k, v in q.items() if isinstance(v, dict)} if isinstance(q, dict) else {}
    name = folder.name
    m = re.search(r"oQ(\d+)(e?)", name)
    if m or (folder / "oq_imatrix_report.json").exists():
        scheme = f"oQ{m.group(1)}{m.group(2)}" if m else "oQ"
    elif bits:
        scheme = f"mlx-affine-{bits}bit"
    else:
        scheme = "bf16"
    # Bits per weight from the weight files: stored bytes over logical parameters. A quantised
    # tensor is a packed uint32 `weight` with `scales` of shape [..., in / group_size].
    stored = logical = 0
    mixed_bits: dict[str, int] = {}
    try:
        for p in sorted(folder.glob("*.safetensors")):
            if p.name.startswith("model-mtp"):
                continue
            h = _safetensor_header(p)
            for k, v in h.items():
                if k == "__metadata__":
                    continue
                shape = v["shape"]
                numel = 1
                for d in shape:
                    numel *= d
                size = v["data_offsets"][1] - v["data_offsets"][0]
                stored += size
                if k.endswith((".scales", ".biases")):
                    continue
                sc = h.get(k[:-len(".weight")] + ".scales") if k.endswith(".weight") else None
                if sc and v["dtype"] in ("U32", "U8"):
                    tgs = (overrides.get(k[:-len(".weight")]) or {}).get("group_size") or gs or 64
                    n_log = 1
                    for d in sc["shape"]:
                        n_log *= d
                    n_log *= tgs
                    logical += n_log
                    b = round(size * 8 / n_log)
                    mixed_bits[str(b)] = mixed_bits.get(str(b), 0) + 1
                else:
                    logical += numel
    except (OSError, ValueError, KeyError, struct.error):
        logical = 0
    bpw = round(stored * 8 / logical, 3) if logical else None
    quant = {"scheme": scheme, "bits": bits, "group_size": gs, "mixed": mixed_bits if len(mixed_bits) > 1 else False}
    return quant, bpw


def _group(engine_id: str) -> str:
    s = engine_id.lower()
    s = re.sub(r"[-_.](mlx|mtp|oq\d+e?|\d+bit|bf16|fp16|q\d(_k_m)?)(?=$|[-_.])", "", s)
    s = re.sub(r"[-_.](mlx|mtp|oq\d+e?|\d+bit)(?=$|[-_.])", "", s)
    return s


def complete(folder: Path) -> str | None:
    """None when the folder holds a complete model, else why not."""
    if not (folder / "config.json").is_file():
        return "no config.json"
    partial = [p.name for p in folder.rglob("*") if p.name.endswith((".incomplete", ".part", ".download"))]
    if partial:
        return f"still downloading ({len(partial)} partial files)"
    idx = folder / "model.safetensors.index.json"
    if idx.is_file():
        try:
            shards = set(json.loads(idx.read_text())["weight_map"].values())
        except (OSError, ValueError, KeyError):
            return "unreadable weight index"
        missing = [s for s in shards if not (folder / s).is_file() or (folder / s).stat().st_size == 0]
        if missing:
            return f"missing {len(missing)} of {len(shards)} weight shards"
    elif not list(folder.glob("*.safetensors")):
        return "no weight files"
    if not ((folder / "tokenizer.json").is_file() or (folder / "tokenizer.model").is_file()):
        return "no tokenizer"
    return None


def scan(model_dir: Path) -> tuple[list[LocalModel], list[tuple[str, str]]]:
    """Every complete small chat model under model_dir (one or two levels deep), and what was skipped."""
    model_dir = Path(model_dir)
    candidates: list[Path] = []
    for p in sorted(model_dir.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        if (p / "config.json").exists() or list(p.glob("*.safetensors")):
            candidates.append(p)
        else:
            subs = [q for q in sorted(p.iterdir()) if q.is_dir() and not q.name.startswith(".")]
            candidates += subs
    found, skipped = [], []
    for p in sorted(model_dir.iterdir()):
        if p.is_dir() and not p.name.startswith(".") and p not in candidates and \
                not any(c.parent == p for c in candidates):
            skipped.append((p.name, "empty folder (a download not started, or nothing in it)"))
    for folder in candidates:
        rel = folder.relative_to(model_dir).as_posix()
        cfg = None
        if (folder / "config.json").is_file():
            try:
                cfg = json.loads((folder / "config.json").read_text())
            except ValueError:
                skipped.append((rel, "unreadable config.json (still downloading?)"))
                continue
        mt = str((cfg or {}).get("model_type") or "")
        arch = " ".join((cfg or {}).get("architectures") or [])
        low = (folder.name + " " + mt + " " + arch).lower()
        if cfg is not None and (mt.endswith(("_assistant", "_mtp")) or "dflash" in low or "dflash_config" in cfg
                                or any(w in low for w in HELPER_WORDS)):
            skipped.append((rel, "a helper model (drafter, embedder or reranker), not a chat model"))
            continue
        if (folder / "adapter_config.json").exists():
            skipped.append((rel, "an adapter, not a whole model"))
            continue
        why = complete(folder)
        if why:
            skipped.append((rel, "not a model folder (no config.json)" if why == "no config.json" else why))
            continue
        tmpl = _template(folder)
        if not tmpl:
            skipped.append((rel, "no chat template (not a chat model)"))
            continue
        gb = sum(p.stat().st_size for p in folder.glob("*.safetensors")) / 1e9
        if gb > MAX_WEIGHTS_GB:
            skipped.append((rel, f"{gb:.1f} GB of weights: over the {MAX_WEIGHTS_GB:g} GB limit for small models"))
            continue
        quant, bpw = quantisation(folder, cfg)
        known = KNOWN.get(folder.name) or next(((lab, "candidate", ships) for pre, lab, ships in FAMILY_LABELS
                                                 if folder.name.lower().startswith(pre)), None)
        loader = _loader(mt, cfg, folder)
        notes = []
        if loader == "unverified":
            notes.append("no loader for this model type in the installed oMLX" + (" (it needs remote code)" if cfg.get("auto_map") else ""))
        modes = thinking_modes(tmpl, known[2] if known else None)
        tdef = template_default(tmpl)
        if len(modes) == 2 and _mode_note(modes[0], tdef):
            notes.append(_mode_note(modes[0], tdef))
        mtp = (folder / "model-mtp.safetensors").exists()
        rate = max(12.0, min(150.0, 200.0 / max(gb, 0.5))) * (1.3 if mtp else 1.0)
        found.append(LocalModel(
            engine_id=folder.name, folder=rel, label=known[0] if known else folder.name,
            role=known[1] if known else "candidate", model_type=mt, family=_family(mt, folder.name), loader=loader,
            disk_gb=round(gb, 2), quant=quant, bits_per_weight=bpw,
            thinking=modes, standard=True, group=_group(folder.name),
            decode_tok_s=round(rate, 1), notes="; ".join(notes), template_thinking=tdef))
    # The same model at several quantisations: the one closest to 4 bits per weight is the standard entry.
    by_group: dict[str, list[LocalModel]] = {}
    for m in found:
        by_group.setdefault(m.group, []).append(m)
    out = []
    for m in found:
        peers = by_group[m.group]
        if len(peers) > 1:
            best = min(peers, key=lambda x: (abs((x.quant.get("bits") or x.bits_per_weight or 16) - 4.0),
                                            abs((x.bits_per_weight or 16) - 4.0),
                                            "mtp" not in x.engine_id.lower(), x.engine_id))
            if best is not m:
                m = LocalModel(**{**m.__dict__, "standard": False,
                                  "notes": "; ".join(filter(None, [m.notes, f"other quantisation of {best.engine_id}"]))})
        out.append(m)
    return out, skipped


def ordered_entries(models: list[LocalModel], *, tiers: tuple[int, ...] = (0, 1, 2, 3)) -> list[Entry]:
    """Every entry, in run order: by tier, then the references first, then by size (smallest first)."""
    rank = {mid: i for i, mid in enumerate(FIRST)}
    ents = [e for m in models for e in m.entries() if e.tier in tiers]
    return sorted(ents, key=lambda e: (e.tier, rank.get(e.model.engine_id, len(FIRST)), e.model.disk_gb, e.key))


def model_dir_from_settings() -> Path | None:
    try:
        s = json.loads((Path.home() / ".omlx" / "settings.json").read_text())
        d = s.get("model", {}).get("model_dir") or s.get("model_dir")
        return Path(d) if d else None
    except (OSError, ValueError):
        return None


def find_entry(models: list[LocalModel], key: str) -> Entry:
    for m in models:
        for e in m.entries():
            if e.key == key:
                return e
    raise KeyError(f"no entry {key!r}; known: {[e.key for m in models for e in m.entries()][:40]}")
