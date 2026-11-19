"""The run's settings policy: who decides sampling, thinking, window and time.

Pure functions, no service imports. A run's policy is resolved once from the
CLI (`resolve`), written to meta.json under "policy" with a fingerprint of the
keys that change what a model is asked to do, and checked again by --compare
(`conflicts`), which refuses two runs whose hard keys differ.

SAMPLING. Each preset is sent identically, on every request, to every model and
every engine (runner.BenchClient.sampling):

  author          DEFAULT for comparisons (tinyagent-bench decision 2026-11-18): each
                  model's author-recommended settings from AUTHOR_FILE
                  (scripts/toolbench/author_sampling.json, kept with the
                  benchmark). A model with no entry is refused.
  engine-default  nothing is sent; the engine and the model's profile decide
                  (oMLX ~/.omlx/model_settings.json, llama.cpp's own defaults,
                  ...). Production fidelity on oMLX; NOT comparable across
                  engines or families, because each decides differently.
  fixed           FIXED below: one decoder for everyone, the sensitivity
                  check (tinyagent-bench: temperature 0.6, top-p 0.95), not the default. top_k 0 and min_p 0
                  mean "disabled" on oMLX (mlx-lm), llama.cpp and LM Studio, so
                  an engine's own top_k/min_p defaults cannot apply silently.
  file:<path>     a JSON map {model id: {key: value}, "*": {...}} of each model's
                  published settings, for a model-card comparison.

An engine may ignore a key (Ollama's OpenAI endpoint ignores top_k and min_p);
the probe records what it could verify, and meta.json what was sent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

POLICY_VERSION = 1
AUTHOR_FILE = Path(__file__).resolve().parent / "author_sampling.json"
# How each sampling policy is labelled on result rows (tinyagent-bench §2.3 vocabulary).
SAMPLING_SOURCE = {"author": "author_recommended", "engine-default": "engine_default",
                   "fixed": "fixed_sensitivity"}
FIXED = {"temperature": 0.6, "top_p": 0.95, "top_k": 0, "min_p": 0.0}
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty",
                 "repetition_penalty", "repeat_penalty")
# "cloud": a cloud reference run through Harbor's own cloud mode (cloud.py); set by --engine openrouter.
TREATMENTS = ("packaged", "profile-as", "probe", "cloud")
# Harbor's recommended window for tool workloads (service/inference/qualify.py
# RECOMMENDED_CONTEXT); every model is run at min(this, its verified window).
DEFAULT_WINDOW = 16384
DEFAULT_TIMEOUT = 600.0
DEFAULT_LATENCY_BUDGET = 180.0
# tinyagent-bench's two reference models (decisions 2026-11-18): Harbor's tuned Gemma 4
# E4B r5 is the primary baseline, untuned base Gemma 4 E4B the second; their
# gap is what Harbor tuning adds. --with-references runs both first.
PRIMARY_REFERENCE = "Gemma-Wisp"
BASE_REFERENCE = "gemma-4-E4B-it-oQ4e-mtp"
DEFAULT_REFERENCES = (PRIMARY_REFERENCE, BASE_REFERENCE)
# Other engine ids of the same weights.
REFERENCE_ALIASES = {"gemma-4-E4B-it-r5-step-01800-oQ4e-mtp": PRIMARY_REFERENCE}

# Keys whose difference makes two runs' pass rates incomparable: --compare
# refuses them unless --allow-policy-mismatch.
# `sampling` (the CLI string, e.g. a file path) is not compared: its source
# and the params actually sent are. `thinking` lists the variants a run
# made; rows are compared per variant label, so it is only a soft difference.
HARD_KEYS = ("kind", "case_set", "sampling_source", "sampling_params", "seed_mode", "treatment", "reference_treatment",
             "profile_as",
             "context_window", "max_output_tokens", "timeout_s", "max_steps", "world_seed",
             "case_sessions")
# Keys that are worth a warning but do not by themselves invalidate a comparison.
SOFT_KEYS = ("engine", "thinking", "latency_budget_s", "probe", "seed_base", "references")


def load_sampling_file(path: str | Path) -> dict[str, dict]:
    data = json.loads(Path(path).expanduser().read_text())
    if not isinstance(data, dict):
        raise ValueError("--sampling file must be a JSON object {model: {key: value}}")
    out: dict[str, dict] = {}
    for model, params in data.items():
        if str(model).startswith("_"):
            continue                       # "_about" and other notes
        if isinstance(params, dict) and isinstance(params.get("params"), dict):
            params = params["params"]      # {params, source, verified} form
        if not isinstance(params, dict):
            raise ValueError(f"--sampling file: {model!r} must map to an object")
        if bad := sorted(set(params) - set(SAMPLING_KEYS)):
            raise ValueError(f"--sampling file: {model!r} has unknown keys {bad}; allowed {SAMPLING_KEYS}")
        out[str(model)] = {k: params[k] for k in SAMPLING_KEYS if k in params}
    return out


def sampling_name(arg: str, *, models: list[str], engine: str) -> str:
    """The --sampling value, or the default: engine-default only for a
    single-model oMLX run (production fidelity), fixed for every comparison."""
    if arg:
        if arg not in ("author", "engine-default", "fixed") and not arg.startswith("file:"):
            raise ValueError("--sampling must be author, engine-default, fixed or file:<path>")
        return arg
    return "engine-default" if (len(models) <= 1 and engine == "omlx") else "author"


def sampling_source(name: str) -> str:
    return SAMPLING_SOURCE.get(name, "author_recommended" if name.startswith("file:") else name)


def author_notes(path: str | Path = AUTHOR_FILE) -> dict[str, dict]:
    """{model: {source, verified}} from the author file, for meta.json."""
    data = json.loads(Path(path).read_text())
    return {m: {"source": v.get("source"), "verified": bool(v.get("verified"))}
            for m, v in data.items() if not m.startswith("_") and isinstance(v, dict) and "params" in v}


def sampling_for(name: str, model: str, table: dict[str, dict] | None = None) -> dict:
    """What every request of `model` carries under policy `name`."""
    if name == "engine-default":
        return {}
    if name == "fixed":
        return dict(FIXED)
    if name == "author" and table is None:
        table = load_sampling_file(AUTHOR_FILE)
    table = table or {}
    if model not in table and "*" not in table:
        where = AUTHOR_FILE.name if name == "author" else "--sampling file"
        raise ValueError(f"{where} has no entry for {model!r}: add its author-recommended settings "
                         "(or run --sampling fixed as a labelled sensitivity check)")
    return dict(table.get(model, table.get("*")))


def treatment_name(arg: str, *, models: list[str], engine: str, profile_as: str) -> str:
    """--treatment, or the default: profile-as when --profile-as was given;
    probe for a comparison or a non-oMLX engine; packaged for one oMLX model."""
    if arg:
        if arg not in TREATMENTS:
            raise ValueError(f"--treatment must be one of {TREATMENTS}")
        return arg
    if profile_as:
        return "profile-as"
    return "packaged" if (len(models) <= 1 and engine == "omlx") else "probe"


# Runs whose policy has no case_set were made on the case set before versioning.
LEGACY_CASE_SET = 1
# Keys whose difference --allow-policy-mismatch cannot override: a cloud run is
# never paired with a local one, and runs on different case sets saw
# different prompts and checks.
NEVER_PAIRED = ("kind", "case_set")


def _case_set_version() -> int:
    from scripts.toolbench.cases import CASE_SET_VERSION
    return CASE_SET_VERSION


def fingerprint(policy: dict) -> str:
    hard = {k: policy.get(k) for k in HARD_KEYS}
    return hashlib.sha256(json.dumps(hard, sort_keys=True, default=str).encode()).hexdigest()[:16]


def build(*, engine: str, port: int, sampling: str, sampling_params: dict[str, dict], treatment: str,
          profile_as: str | None, context_window: int | None, max_output_tokens: int | None,
          timeout_s: float, latency_budget_s: float, max_steps: int, thinking: list[str],
          world_seed, seed_base: int, case_sessions: str, probe: str, references: list[str] = (),
          reference_treatment: str = "packaged", kind: str = "local", case_set: int | None = None) -> dict:
    policy = {"version": POLICY_VERSION, "engine": engine, "port": port, "sampling": sampling,
              "sampling_params": sampling_params, "seed_mode": "per_request", "seed_base": seed_base,
              "thinking": list(thinking), "treatment": treatment,
              "profile_as": profile_as if treatment == "profile-as" else None,
              "context_window": context_window, "max_output_tokens": max_output_tokens,
              "timeout_s": float(timeout_s), "latency_budget_s": float(latency_budget_s),
              "max_steps": int(max_steps), "world_seed": world_seed, "case_sessions": case_sessions,
              "probe": probe, "references": list(references),
              "reference_treatment": reference_treatment if references else None,
              "sampling_source": sampling_source(sampling), "kind": kind,
              "case_set": case_set if case_set is not None else _case_set_version()}
    policy["fingerprint"] = fingerprint(policy)
    return policy


def of_run(meta: dict) -> dict:
    """The policy a run was made under. A run from before policies existed is
    read as what the harness did then: engine-default sampling, one seed per
    trial, a 180 s limit, oMLX, its --profile-as (or the packaged lists)."""
    if isinstance(meta.get("policy"), dict):
        return meta["policy"]
    legacy = {"version": 0, "engine": "omlx", "port": meta.get("omlx_port"), "sampling": "engine-default",
              "sampling_params": {}, "seed_mode": "per_trial", "seed_base": meta.get("seed_base"),
              "thinking": list(meta.get("thinking") or ["default"]),
              "treatment": "profile-as" if meta.get("profile_as") else "packaged",
              "profile_as": meta.get("profile_as"), "context_window": None, "max_output_tokens": None,
              "timeout_s": float(meta.get("timeout") or 180.0), "latency_budget_s": DEFAULT_LATENCY_BUDGET,
              "max_steps": int(meta.get("max_steps") or 8), "world_seed": meta.get("world_seed"),
              "case_sessions": meta.get("case_sessions"), "probe": "none", "legacy": True,
              "references": [], "reference_treatment": None, "sampling_source": "engine_default", "kind": "local",
              "case_set": LEGACY_CASE_SET}
    legacy["fingerprint"] = fingerprint(legacy)
    return legacy


def never_paired(base_meta: dict, cur_meta: dict) -> list[str]:
    """The NEVER_PAIRED differences between two runs (no override applies)."""
    a, b = of_run(base_meta), of_run(cur_meta)
    out = []
    for key in NEVER_PAIRED:
        va = a.get(key) or (LEGACY_CASE_SET if key == "case_set" else "local")
        vb = b.get(key) or (LEGACY_CASE_SET if key == "case_set" else "local")
        if va != vb:
            out.append(f"{key}: {va!r} vs {vb!r}")
    return out


def conflicts(base_meta: dict, cur_meta: dict) -> tuple[list[str], list[str]]:
    """(hard, soft) differences between two runs' policies, as readable lines.
    Sampling params are compared per model the two runs share."""
    a, b = of_run(base_meta), of_run(cur_meta)
    hard, soft = [], []
    shared = set(base_meta.get("models") or ()) & set(cur_meta.get("models") or ())
    for key in HARD_KEYS:
        va, vb = a.get(key), b.get(key)
        if key == "case_set":
            va, vb = va or LEGACY_CASE_SET, vb or LEGACY_CASE_SET
        if key == "sampling_source":
            va, vb = va or sampling_source(a.get("sampling") or ""), vb or sampling_source(b.get("sampling") or "")
        if key == "sampling_params":
            va = {m: v for m, v in (va or {}).items() if m in shared}
            vb = {m: v for m, v in (vb or {}).items() if m in shared}
            if a.get("sampling") == "engine-default" and b.get("sampling") == "engine-default":
                continue
        if key == "profile_as" and a.get("treatment") != "profile-as" and b.get("treatment") != "profile-as":
            continue
        if va != vb:
            hard.append(f"{key}: {va!r} vs {vb!r}")
    for key in SOFT_KEYS:
        if a.get(key) != b.get(key):
            soft.append(f"{key}: {a.get(key)!r} vs {b.get(key)!r}")
    if a.get("legacy") or b.get("legacy"):
        soft.append("a run predates recorded policies; its policy is inferred (policy.of_run)")
    ha, hb = (base_meta.get("host") or {}), (cur_meta.get("host") or {})
    for key in ("chip", "memory_gb", "os_build", "power_source", "low_power_mode"):
        if ha.get(key) != hb.get(key) and (ha or hb):
            soft.append(f"host {key}: {ha.get(key)!r} vs {hb.get(key)!r}")
    if base_meta.get("git") != cur_meta.get("git"):
        soft.append(f"git: {base_meta.get('git')} vs {cur_meta.get('git')}")
    if base_meta.get("today") != cur_meta.get("today"):
        soft.append(f"world date: {base_meta.get('today')} vs {cur_meta.get('today')}")
    ca, cb = set(base_meta.get("cases") or ()), set(cur_meta.get("cases") or ())
    if ca and cb and ca != cb:
        soft.append(f"case sets differ: {len(ca & cb)} shared, {len(ca - cb)} only in baseline, "
                    f"{len(cb - ca)} only in current (only shared cases are compared)")
    return hard, soft
