"""Run metadata for meta.json: the host, the model files, what the user declared.

`host()` runs read-only system probes (sysctl, sw_vers, pmset, git) and must be
called BEFORE runner.isolate(), which refuses most subprocesses afterwards.
Nothing here reads personal data: only hardware, OS, power state, the repo's
own git state, and model files the user points at.
"""
from __future__ import annotations

import json
import platform
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Packages whose versions change what a turn does or how it is measured.
_PACKAGES = ("httpx", "pyyaml", "mlx", "mlx-lm", "pydantic")


def _run(*argv: str) -> str:
    try:
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _sysctl(name: str) -> str:
    return _run("sysctl", "-n", name)


def power_state(pmset_batt: str, pmset_g: str, therm: str) -> dict:
    """Parse `pmset -g batt`, `pmset -g` and `pmset -g therm` text."""
    out: dict = {"power_source": None, "battery_percent": None, "low_power_mode": None,
                 "thermal_warning": None, "cpu_speed_limit": None}
    if m := re.search(r"Now drawing from '([^']+)'", pmset_batt):
        out["power_source"] = {"AC Power": "ac", "Battery Power": "battery"}.get(m.group(1), m.group(1))
    if m := re.search(r"(\d+)%", pmset_batt):
        out["battery_percent"] = int(m.group(1))
    if m := re.search(r"lowpowermode\s+(\d)", pmset_g):
        out["low_power_mode"] = m.group(1) == "1"
    if m := re.search(r"thermal warning level.*?:\s*(\S+)", therm, re.I):
        out["thermal_warning"] = m.group(1)
    elif re.search(r"No thermal warning level has been recorded", therm, re.I):
        out["thermal_warning"] = "none"
    if m := re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", therm):
        out["cpu_speed_limit"] = int(m.group(1))
    return out


OMLX_APP_PLIST = Path("/Applications/oMLX.app/Contents/Info.plist")
# tinyagent-bench's reference Mac (decision 2026-11-18): MacBook Pro, M5 Pro, 24 GB.
MAC_REF = {"soc": "M5 Pro", "ram_gb": 24}


def omlx_version(plist: Path = OMLX_APP_PLIST) -> str | None:
    """The installed oMLX app's version, from its Info.plist (no subprocess)."""
    import plistlib
    try:
        with plist.open("rb") as f:
            info = plistlib.load(f)
        return str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "") or None
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def battery_state(pmset_batt: str) -> str | None:
    m = re.search(r"\d+%;\s*([a-zA-Z ]+?);", pmset_batt)
    return m.group(1).strip().replace(" ", "_") if m else None


def edat_blocks(info: dict, *, engine: str) -> dict:
    """The perf spec's record blocks (04-perf-energy-memory-spec.md §3.2,
    §3.3, §3.5, `edat.perf/1.0` field names) from host(), so Mac Toolbench
    runs and iPhone runner results sit in one store under one vocabulary."""
    soc = str(info.get("chip") or "").replace("Apple ", "")
    ram = info.get("ram_bytes")
    is_ref = MAC_REF["soc"] in soc and info.get("memory_gb") == float(MAC_REF["ram_gb"])
    wired = info.get("gpu_wired_limit_mb")
    return {
        "device": {"class": "mac-ref" if is_ref else "mac", "model_id": info.get("model"), "soc": soc or None,
                   "ram_bytes": ram, "os": f"macOS {info.get('os')}" if info.get("os") else None,
                   "os_build": info.get("os_build"), "entitlements": None,
                   "gpu_wired_limit_mb": int(wired) if str(wired or "").isdigit() else None,
                   "battery_nominal_Wh": None, "battery_nominal_source": None, "battery_health_pct": None},
        "runtime": {"name": "omlx" if engine == "omlx" else engine,
                    "version": info.get("omlx_version") if engine == "omlx" else None,
                    "commit": None, "packages": dict(info.get("packages") or {}), "backend": "metal",
                    "engine_profile": {}},
        "conditions_start": {"power_source": info.get("power_source"),
                             "battery_level": (info["battery_percent"] / 100
                                               if info.get("battery_percent") is not None else None),
                             "battery_state": info.get("battery_state"), "low_power_mode": info.get("low_power_mode"),
                             "thermal_state": info.get("thermal_warning"), "uptime_s": info.get("uptime_s")},
    }


def model_record(key: dict, *, thinking: list[str], window: int | None = None) -> dict:
    """One entry of the perf spec's `models[]` (§14 shape) from a result key,
    so a Toolbench run lists its models under the same names as a perf run.
    Fields Toolbench cannot know (per-file sha256, parameter counts) are None."""
    arch = key.get("architecture")
    return {"id": key.get("model"), "role": key.get("role"), "kind": key.get("kind", "local"),
            "mode": "plain", "files": None, "weights_fingerprint": key.get("weights_fingerprint"),
            "weights_bytes": key.get("weights_bytes"),
            "quant": {"scheme": key.get("quantization")} if key.get("quantization") else None,
            "architecture": arch, "n_experts": key.get("n_experts"),
            "n_experts_active": key.get("n_experts_active"), "params_total": None, "params_active": None,
            "params_embedding": None, "native_window_tokens": None, "profile_window_tokens": window,
            "thinking": list(thinking), "speculation": None, "drafter": None,
            "template_sha256": key.get("template_sha256"), "tokenizer_sha256": None}


def host() -> dict:
    """Hardware, OS, power and software state of this run. Best effort."""
    from importlib import metadata
    mem = _sysctl("hw.memsize")
    info = {
        "chip": _sysctl("machdep.cpu.brand_string") or platform.processor(),
        "model": _sysctl("hw.model"),
        "memory_gb": round(int(mem) / 2**30, 1) if mem.isdigit() else None,
        "ram_bytes": int(mem) if mem.isdigit() else None,
        "gpu_wired_limit_mb": _sysctl("iogpu.wired_limit_mb") or None,
        "omlx_version": omlx_version(),
        "cpu_cores": _sysctl("hw.ncpu"),
        "perf_cores": _sysctl("hw.perflevel0.physicalcpu"),
        "efficiency_cores": _sysctl("hw.perflevel1.physicalcpu"),
        "os": _run("sw_vers", "-productVersion") or platform.platform(),
        "os_build": _run("sw_vers", "-buildVersion"),
        "arch": platform.machine(),
        "python": sys.version.split()[0],
        "packages": {},
        "git_sha": _run("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        "git_dirty": bool(_run("git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no")),
    }
    for name in _PACKAGES:
        try:
            info["packages"][name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    batt = _run("pmset", "-g", "batt")
    info.update(power_state(batt, _run("pmset", "-g"), _run("pmset", "-g", "therm")))
    info["battery_state"] = battery_state(batt)
    import time
    boot = re.search(r"sec = (\d+)", _sysctl("kern.boottime"))
    info["uptime_s"] = int(time.time()) - int(boot.group(1)) if boot else None
    return info


# config.json keys that describe the architecture and quantisation of an
# MLX / Hugging Face model directory.
_CONFIG_KEYS = ("model_type", "architectures", "num_hidden_layers", "hidden_size", "intermediate_size",
                "num_attention_heads", "num_key_value_heads", "vocab_size", "max_position_embeddings",
                "num_experts", "num_local_experts", "n_routed_experts", "num_experts_per_tok",
                "moe_intermediate_size", "tie_word_embeddings", "sliding_window", "torch_dtype")


def _gguf_header(path: Path) -> dict:
    """GGUF version, tensor count and a few general.* keys, read from the header only."""
    out: dict = {}
    try:
        with path.open("rb") as f:
            if f.read(4) != b"GGUF":
                return {"error": "not a GGUF file"}
            version, n_tensors, n_kv = struct.unpack("<IQQ", f.read(20))
            out.update(gguf_version=version, tensors=n_tensors)

            def s() -> str:
                (n,) = struct.unpack("<Q", f.read(8))
                return f.read(n).decode("utf-8", "replace")
            scalars = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q",
                       11: "<q", 12: "<d"}
            wanted = ("general.architecture", "general.name", "general.size_label", "general.file_type",
                      "general.quantization_version", "general.parameter_count")
            for _ in range(min(n_kv, 256)):
                key = s()
                (vtype,) = struct.unpack("<I", f.read(4))
                if vtype == 8:
                    val = s()
                elif vtype in scalars:
                    fmt = scalars[vtype]
                    (val,) = struct.unpack(fmt, f.read(struct.calcsize(fmt)))
                elif vtype == 9:            # array: skip it
                    (atype,) = struct.unpack("<I", f.read(4))
                    (count,) = struct.unpack("<Q", f.read(8))
                    if atype == 8:
                        for _ in range(count):
                            (n,) = struct.unpack("<Q", f.read(8))
                            f.seek(n, 1)
                    elif atype in scalars:
                        f.seek(struct.calcsize(scalars[atype]) * count, 1)
                    else:
                        break
                    continue
                else:
                    break
                if key in wanted or key.endswith((".context_length", ".expert_count", ".expert_used_count",
                                                  ".block_count")):
                    out[key] = val
    except (OSError, struct.error, UnicodeDecodeError) as e:
        out["error"] = type(e).__name__
    return out


_FP_CHUNK = 1 << 20


def weights_fingerprint(files: list[Path]) -> str:
    """sha256 over each weight file's name, size and first and last MiB. Not a
    full-content hash (hashing GBs would take minutes); it tells two different
    weight files apart in practice, and is labelled as such in meta.json."""
    import hashlib
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.name):
        size = f.stat().st_size
        h.update(f"{f.name}\0{size}\0".encode())
        with f.open("rb") as fh:
            h.update(fh.read(_FP_CHUNK))
            if size > 2 * _FP_CHUNK:
                fh.seek(size - _FP_CHUNK)
                h.update(fh.read(_FP_CHUNK))
    return h.hexdigest()


def model_files(path: str | Path) -> dict:
    """Architecture, quantisation and size of a local model: an MLX / HF
    directory (config.json + weight files) or a single GGUF file."""
    p = Path(path).expanduser()
    out: dict = {"path": str(p)}
    if not p.exists():
        out["error"] = "not found"
        return out
    if p.is_file():
        out["bytes"] = p.stat().st_size
        out["weights_fingerprint"] = weights_fingerprint([p])
        if p.suffix.lower() == ".gguf":
            out["format"] = "gguf"
            out["gguf"] = _gguf_header(p)
        return out
    weights = [f for f in p.iterdir() if f.suffix in (".safetensors", ".gguf", ".bin", ".npz")]
    out["format"] = "gguf" if any(f.suffix == ".gguf" for f in weights) else "safetensors/mlx"
    out["bytes"] = sum(f.stat().st_size for f in weights)
    out["weights_fingerprint"] = weights_fingerprint(weights) if weights else None
    cfg_path = p / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            text = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
            out["config"] = {k: cfg.get(k, text.get(k)) for k in _CONFIG_KEYS if k in cfg or k in text}
            q = cfg.get("quantization") or cfg.get("quantization_config")
            if isinstance(q, dict):
                out["quantization"] = {k: v for k, v in q.items() if not isinstance(v, (dict, list))}
            out["multimodal"] = any(k in cfg for k in ("vision_config", "audio_config"))
        except (OSError, ValueError) as e:
            out["config_error"] = type(e).__name__
    return out


# Fields a --model-info file may declare per model (or under "*").
DECLARED_KEYS = ("params_b", "active_params_b", "quantization", "format", "source", "license", "family",
                 "context_native", "multimodal", "notes")


def declared(path: str | Path) -> dict[str, dict]:
    data = json.loads(Path(path).expanduser().read_text())
    if not isinstance(data, dict):
        raise ValueError("--model-info must be a JSON object {model: {field: value}}")
    out = {}
    for model, fields in data.items():
        if not isinstance(fields, dict):
            raise ValueError(f"--model-info: {model!r} must map to an object")
        if bad := sorted(set(fields) - set(DECLARED_KEYS)):
            raise ValueError(f"--model-info: {model!r} has unknown fields {bad}; allowed {DECLARED_KEYS}")
        out[str(model)] = dict(fields)
    return out


def model_paths(arg: str) -> dict[str, str]:
    """--model-path "id=/path,id2=/path2" (or one bare path for a single model: key "*")."""
    out = {}
    for part in (p.strip() for p in (arg or "").split(",") if p.strip()):
        model, sep, path = part.partition("=")
        out[model.strip() if sep else "*"] = (path if sep else model).strip()
    return out


def result_key(policy: dict, model: str, engine_info: dict, model_info: dict, git: str) -> dict:
    """What identifies one model's result (carried on every row): the weights,
    the runtime and template that served them, the sampling and its source,
    the harness commit and policy. Thinking mode is added per variant."""
    props = engine_info.get("props") or {}
    files = model_info.get("files") or {}
    declared = model_info.get("declared") or {}
    gguf = files.get("gguf") or {}
    lms = engine_info.get("lmstudio_model") or {}
    show = (engine_info.get("ollama_show") or {}).get("details") or {}
    quant = (declared.get("quantization") or files.get("quantization") or gguf.get("general.file_type")
             or lms.get("quantization") or show.get("quantization_level"))
    from scripts.toolbench.policy import sampling_source
    name = policy.get("sampling") or ""
    cfg = files.get("config") or {}
    experts = cfg.get("num_experts") or cfg.get("num_local_experts") or cfg.get("n_routed_experts")
    role = ("cloud_reference" if policy.get("kind") == "cloud_reference"
            else "baseline" if model in ("Gemma-Wisp", "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp")
            else "reference" if model == "gemma-4-E4B-it-oQ4e-mtp"
            else "reference" if model in (policy.get("references") or ()) else "candidate")
    return {"model": model, "role": role, "kind": policy.get("kind", "local"),
            "architecture": ("moe" if experts else "dense") if cfg else declared.get("architecture"),
            "n_experts": experts, "n_experts_active": cfg.get("num_experts_per_tok"), "engine": policy.get("engine"), "runtime": engine_info.get("runtime"),
            "runtime_version": (props.get("build_info") or (engine_info.get("ollama_version") or {}).get("version")
                                or (engine_info.get("health") or {}).get("version")),
            "template_sha256": props.get("chat_template_sha256"),
            "quantization": quant, "weights_fingerprint": files.get("weights_fingerprint"),
            "weights_bytes": files.get("bytes"),
            "sampling": (policy.get("sampling_params") or {}).get(model) or {},
            "sampling_source": sampling_source(name),
            "harness": git, "policy": policy.get("fingerprint"), "lang": "en"}
