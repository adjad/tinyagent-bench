"""Converters from existing harness output into edat.result/1.0 records.

- Toolbench (`scripts/run_toolbench.py`): a run directory with results.jsonl
  and meta.json. Reads both the imported (legacy) rows and the rows of the
  fairness branch, which add a per-row `key` and meta.json `policy`, `host`,
  `engine`, `model_info` blocks (07-harness-review.md findings 3, 8, 9).
- Performance (`edat.perf/1.0`, 04 §3): one run document; measured samples
  become `tier: perf` records and `verdicts[]` become fit records.
- In-assistant (08 §7): results.jsonl + meta.json of the productbench harness;
  `captured` output moves to raw files.

Every converter writes through RunWriter, so its output is validated like
any other run, and keeps the source files as run attachments.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edat.results.writer import RunWriter

SAFETY_LABELS = ("unsafe_call", "unconfirmed_effect", "confirmation_bypass", "injection_followed")
LEAK_LABELS = ("wrong_recipient", "canary_leak", "privacy_leak")
MAC_REF = {"soc": "M5 Pro", "ram_gb": 24.0}
_HEX = re.compile(r"^[0-9a-f]{7,40}$")


class ConversionError(ValueError):
    """The source cannot be turned into valid records without more information."""


def iso_utc(value: Any) -> str:
    """ISO 8601 UTC with Z from epoch seconds or an ISO string."""
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str) and value:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        raise ConversionError(f"no usable start time ({value!r})")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _commit(value: Any, override: str | None) -> str:
    c = override or value
    if not isinstance(c, str) or not _HEX.match(c):
        raise ConversionError(f"harness commit {c!r} is not a git SHA; pass --harness-commit")
    return c


def device_from_host(host: dict, device_id: str | None = None) -> dict:
    """The record's `device` from Toolbench's meta.json `host` block (runmeta.host())."""
    soc = str(host.get("chip") or "").replace("Apple ", "") or None
    mem_gb = host.get("memory_gb")
    is_ref = bool(soc and MAC_REF["soc"] in soc and mem_gb == MAC_REF["ram_gb"])
    cls = "mac-ref" if is_ref else "mac"
    ram = host.get("ram_bytes") or (int(mem_gb * 2**30) if isinstance(mem_gb, (int, float)) else None)
    os_ = host.get("os")
    return {"id": device_id or cls, "class": cls, "model_id": host.get("model"), "soc": soc, "ram_bytes": ram,
            "os": (os_ if str(os_ or "").startswith("macOS") else f"macOS {os_}") if os_ else None,
            "os_build": host.get("os_build")}


def _thinking(variant: str, default_thinking: str | None) -> tuple[str, str | None]:
    if variant in ("on", "off"):
        return variant, None
    if variant == "minimal":
        return "minimal", None
    if default_thinking in ("on", "off"):
        return default_thinking, variant
    raise ConversionError(f"thinking variant {variant!r} does not say whether thinking was on or off; "
                          "pass --default-thinking on|off (results-record.md D2)")


_SAMPLING_KEYS = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "min_p": "min_p",
                  "repeat_penalty": "repeat_penalty", "repetition_penalty": "repeat_penalty",
                  "presence_penalty": "presence_penalty", "frequency_penalty": "frequency_penalty",
                  "max_tokens": "max_output_tokens", "max_output_tokens": "max_output_tokens"}


def sampling_values(params: dict | None) -> dict:
    out = {}
    for k, v in (params or {}).items():
        if k in _SAMPLING_KEYS and v is not None and not isinstance(v, bool):
            out[_SAMPLING_KEYS[k]] = v
    return out


def _role(value: str | None, model_id: str) -> str:
    v = (value or "candidate").replace("-", "_")
    return v if v in ("baseline", "reference", "candidate", "variant", "cloud_reference") else "candidate"


# --- Toolbench -------------------------------------------------------------

def convert_toolbench(src: Path | str, store: Path | str, *, board_version: str,
                      device_id: str | None = None, harness_commit: str | None = None,
                      default_thinking: str | None = None, weights: dict[str, str] | None = None,
                      quant: dict[str, str] | None = None, run_dir: Path | str | None = None,
                      sample: bool = False) -> Path:
    """Toolbench run directory -> one tinyagent-bench run. Returns the new run directory.

    `weights` / `quant` map model id -> fingerprint or sha256 / scheme, for
    legacy runs whose rows carry no `key`."""
    src = Path(src).expanduser()
    rows = [json.loads(line) for line in (src / "results.jsonl").read_text().splitlines() if line.strip()]
    meta = json.loads((src / "meta.json").read_text()) if (src / "meta.json").exists() else {}
    if not rows:
        raise ConversionError(f"{src}/results.jsonl has no rows")
    run_id = str(meta.get("run") or rows[0].get("run") or src.name)
    started = iso_utc(meta.get("started") or rows[0].get("started"))
    policy = meta.get("policy") or {}
    host = meta.get("host") or {}
    seeds = {str(k): v for k, v in (meta.get("seeds") or {}).items()}
    budget = policy.get("latency_budget_s")
    weights, quant = weights or {}, quant or {}
    records = []
    raws = []
    for n, row in enumerate(rows, 1):
        key = row.get("key") or {}
        model_id = row.get("base_model") or key.get("model") or row["model"]
        thinking, detail = _thinking(row.get("variant") or "default", default_thinking)
        v = row.get("v") or {}
        kind = "cloud_reference" if (key.get("kind") or policy.get("kind") or "local").replace(
            "-", "_") == "cloud_reference" else "local"
        fp = key.get("weights_fingerprint") or weights.get(model_id)
        model: dict[str, Any] = {"id": model_id, "role": "cloud_reference" if kind == "cloud_reference"
                                 else _role(key.get("role"), model_id)}
        if kind == "local":
            if not fp:
                raise ConversionError(f"row {n} ({model_id}) has no weights fingerprint; "
                                      f"pass --weights {model_id}=<sha256 or fingerprint>")
            model["weights_fingerprint"] = fp
            q = key.get("quantization") or quant.get(model_id)
            model["quant"] = {"scheme": str(q)} if q else None
            if key.get("architecture") in ("dense", "moe"):
                model["architecture"] = key["architecture"]
            if key.get("weights_bytes") is not None:
                model["weights_bytes"] = key["weights_bytes"]
            if key.get("template_sha256"):
                model["template_sha256"] = key["template_sha256"]
        else:
            for f in ("requested_model", "resolved_model", "upstream_provider", "run_date"):
                if not key.get(f):
                    raise ConversionError(f"row {n} is a cloud reference without key.{f}; "
                                          "the Toolbench cloud lane must record it (results-record.md §2.1)")
            model.update({f: key[f] for f in ("requested_model", "resolved_model", "upstream_provider",
                                              "run_date")})
            model["architecture"] = "unknown"
        source = key.get("sampling_source") or ("provider_default" if kind == "cloud_reference"
                                                else "engine_default")
        settings = {"thinking": thinking, "sampling": sampling_values(key.get("sampling")),
                    "sampling_source": source,
                    "seed": (key["seed"] if "seed" in key else seeds.get(str(row["trial"]))),
                    "world_seed": meta.get("world_seed")}
        if detail:
            settings["thinking_detail"] = detail
        if key.get("policy") or policy.get("fingerprint"):
            settings["policy_fingerprint"] = str(key.get("policy") or policy.get("fingerprint"))[:64]
        labels = [lab for lab in v.get("labels") or () if lab in SAFETY_LABELS + LEAK_LABELS]
        metrics = {"pass": bool(v.get("passed")), "selection_ok": v.get("selection_ok"),
                   "args_ok": v.get("args_ok"), "format_ok": v.get("format_ok"), "answer_ok": v.get("answer_ok"),
                   "safe": v.get("safe"), "steps": v.get("steps"), "model_calls": v.get("model_calls"),
                   "discovery_calls": v.get("discovery_calls"), "wall_s": v.get("seconds"),
                   "model_s": v.get("model_seconds"), "ttft_s": v.get("ttft"),
                   "prompt_tokens_est": v.get("prompt_tokens_est"), "output_chars": v.get("output_chars"),
                   "reasoning_chars": v.get("reasoning_chars"), "guard_retries": v.get("guard_retries")}
        if budget and v.get("seconds") is not None:
            metrics["pass_within_budget"] = bool(v.get("passed")) and v["seconds"] <= budget
        metrics = {k: x for k, x in metrics.items() if x is not None}
        rec = {
            "schema": "edat.result/1.0", "kind": kind, "tier": "agentic", "suite": "toolbench",
            "run_id": run_id, "board_version": board_version, "lang": "en", "model": model,
            "runtime": {"name": key.get("runtime") or (meta.get("engine") or {}).get("kind")
                        or ("openrouter" if kind == "cloud_reference" else "omlx"),
                        "version": key.get("runtime_version") or (meta.get("engine") or {}).get("version")},
            "device": None if kind == "cloud_reference" else device_from_host(host, device_id),
            "settings": settings, "harness_commit": _commit(key.get("harness") or meta.get("git"), harness_commit),
            "started_at": started, "case_id": row["case"], "split": "dev", "trial": int(row["trial"]),
            "status": "excluded" if v.get("excluded") else "ok",
            "facets": {"lane": "assistant", "category": row.get("category"),
                       "source_split": row.get("split")},
            "metrics": metrics,
            "safety": {"unsafe_actions": sum(1 for lab in labels if lab in SAFETY_LABELS),
                       "privacy_leaks": None, "labels": sorted(set(labels))},
            "raw": None, "source": {"converter": "toolbench", "format": "results.jsonl", "row": n},
        }
        if v.get("excluded"):
            rec["status_detail"] = "engine_error"
        if kind == "cloud_reference":
            rec["device_reason"] = "cloud"
        if sample:
            rec["sample"] = True
        if (meta.get("host") or {}).get("git_dirty") is not None:
            rec["harness_dirty"] = bool(meta["host"]["git_dirty"])
        raws.append({k: row.get(k) for k in ("route", "steps", "final", "effects", "asked", "escapes",
                                             "message_draft", "error", "timeout", "path", "access")}
                    | {"labels": v.get("labels") or [], "notes": v.get("notes") or [], "no_tool": row.get("no_tool")})
        records.append(rec)
    with RunWriter.open(store, run_id, tier="agentic", suite="toolbench", sample=sample, run_dir=run_dir) as w:
        for rec, raw in zip(records, raws):
            rec["raw"] = w.put_raw(raw)
        w.append_many(records)
        if (src / "meta.json").exists():
            w.attach("toolbench-meta.json", src / "meta.json", "toolbench-meta")
        w.set_source({"converter": "toolbench", "rows": len(rows)})
        return w.dir


# --- performance (edat.perf/1.0) -------------------------------------------

PERF_STATUS = {
    "ok": "ok", "ok_extended": "ok", "truncated": "ok", "memory_pressure": "ok", "timeout": "ok",
    "provider_fallback": "ok",
    "oom_killed": "out_of_memory", "oom_killed_at_load": "out_of_memory",
    "not_run_above_oom": "not_run", "exceeds_native_window": "beyond_window",
    "unsupported_by_runtime": "unsupported_by_runtime",
    "engine_refused": "excluded", "killed_unknown": "excluded", "voided_backgrounded": "excluded",
    "voided_foreign_work": "excluded", "voided_response_cache": "excluded",
    "provider_error": "excluded", "rate_limited": "excluded",
}


def weights_sha256(files: list[dict] | None) -> str | None:
    """sha256 over sorted '<name>\\0<sha256>\\n' lines of every weight file (results-record.md §2.1)."""
    if not files or not all(f.get("sha256") for f in files):
        return None
    lines = "".join(f"{f['name']}\0{f['sha256']}\n" for f in sorted(files, key=lambda f: f["name"]))
    return hashlib.sha256(lines.encode()).hexdigest()


def _perf_model(m: dict, kind: str) -> dict:
    role = _role(m.get("role"), m["id"])
    out: dict[str, Any] = {"id": m["id"], "role": "cloud_reference" if kind == "cloud_reference" else role}
    if kind == "cloud_reference":
        out.update({"requested_model": m["id"], "resolved_model": m.get("resolved_model"),
                    "upstream_provider": m.get("upstream_provider") or m.get("provider_name"),
                    "run_date": m.get("run_date"), "architecture": "unknown"})
        return out
    out["weights_sha256"] = weights_sha256(m.get("files"))
    if m.get("weights_fingerprint"):
        out["weights_fingerprint"] = m["weights_fingerprint"]
    q = m.get("quant") or {}
    out["quant"] = {k: q.get(k) for k in ("scheme", "bits", "group_size", "mixed") if k in q} if q.get("scheme") else None
    for k in ("architecture", "params_total", "params_active", "template_sha256", "variant_of", "headline_entry"):
        if m.get(k) is not None:
            out[k] = m[k]
    if m.get("files"):
        out["weights_bytes"] = sum(int(f.get("bytes") or 0) for f in m["files"])
    return out


def _s(ns_a, ns_b):
    if isinstance(ns_a, int) and isinstance(ns_b, int) and ns_b >= ns_a:
        return (ns_b - ns_a) / 1e9
    return None


def perf_sample_metrics(s: dict) -> dict:
    ttft = _s(s.get("t_submit_ns"), s.get("t_first_token_ns"))
    e2e = _s(s.get("t_submit_ns"), s.get("t_end_ns"))
    prefill_end = s.get("t_prefill_done_ns") or s.get("t_first_token_ns")
    prefill_s = _s(s.get("t_submit_ns"), prefill_end)
    decode_s = _s(s.get("t_first_token_ns"), s.get("t_last_token_ns"))
    out_tok = s.get("output_tokens")
    m = {"ttft_s": ttft, "e2e_s": e2e,
         "prefill_tps": (s["prompt_tokens"] / prefill_s) if s.get("prompt_tokens") and prefill_s else None,
         "decode_tps": ((out_tok - 1) / decode_s) if isinstance(out_tok, int) and out_tok > 1 and decode_s else None,
         "prompt_tokens": s.get("prompt_tokens"), "cached_tokens": s.get("cached_tokens"),
         "output_tokens": out_tok, "reasoning_tokens": s.get("reasoning_tokens"),
         "footprint_peak_bytes": s.get("footprint_peak_bytes"),
         "footprint_steady_bytes": s.get("footprint_steady_bytes"),
         "swap_delta_bytes": s.get("swap_delta_bytes"),
         "net_ttft_s": s.get("net_ttft_s"), "net_total_s": s.get("net_total_s"), "cost_usd": s.get("cost_usd")}
    kill = s.get("kill_evidence") or {}
    if m["footprint_peak_bytes"] is None and isinstance(kill.get("last_footprint_bytes"), int):
        m["footprint_peak_bytes"] = kill["last_footprint_bytes"]
    return {k: v for k, v in m.items() if v is not None}


def convert_perf(doc_path: Path | str, store: Path | str, *, board_version: str, device_id: str | None = None,
                 sampling_source: str = "engine_default", harness_commit: str | None = None,
                 run_dir: Path | str | None = None, sample: bool = False) -> Path:
    """edat.perf/1.0 run document -> one tinyagent-bench run of tier perf."""
    doc_path = Path(doc_path).expanduser()
    doc = json.loads(doc_path.read_text())
    if doc.get("schema") != "edat.perf/1.0":
        raise ConversionError(f"{doc_path}: schema {doc.get('schema')!r} is not edat.perf/1.0 "
                              "(the iPhone runner's edgebench.run/0.1 is not read yet; results-record.md D9)")
    kind = doc.get("kind") or "local"
    run_id = doc["run_id"]
    runner = doc.get("runner") or {}
    rt = doc.get("runtime") or {}
    runtime = {"name": rt.get("name") or ("openrouter" if kind == "cloud_reference" else "omlx"),
               "version": rt.get("version"), "backend": rt.get("backend"), "commit": rt.get("commit")}
    runtime = {k: v for k, v in runtime.items() if v is not None or k in ("name", "version")}
    dev = doc.get("device")
    device = None
    if kind == "local":
        if not dev:
            raise ConversionError(f"{doc_path}: a local perf run needs a device block")
        device = {"id": device_id or dev.get("class") or "device", "class": dev.get("class") or "device",
                  "model_id": dev.get("model_id"), "soc": dev.get("soc"), "ram_bytes": dev.get("ram_bytes"),
                  "os": dev.get("os"), "os_build": dev.get("os_build")}
    commit = _commit(runner.get("commit"), harness_commit)
    started = iso_utc(doc.get("started_utc"))
    models = {}
    for m in doc.get("models") or ():
        models[(m["id"], m.get("mode") or "as_shipped")] = m
        models.setdefault((m["id"], None), m)

    def base(m: dict, case_id: str, trial: int, status: str, detail: str | None, facets: dict,
             metrics: dict) -> dict:
        rec = {"schema": "edat.result/1.0", "kind": kind, "tier": "perf", "suite": runner.get("name") or "perf",
               "run_id": run_id, "board_version": board_version, "lang": "en", "model": _perf_model(m, kind),
               "runtime": runtime, "device": device,
               "settings": {"thinking": m.get("thinking") if m.get("thinking") in ("on", "off") else "off",
                            "sampling": {}, "sampling_source": "provider_default" if kind == "cloud_reference"
                            else sampling_source, "seed": None},
               "harness_commit": commit, "started_at": started, "case_id": case_id, "split": "dev",
               "trial": trial, "status": status, "facets": facets, "metrics": metrics,
               "safety": {"unsafe_actions": None, "privacy_leaks": None}, "raw": None,
               "source": {"converter": "perf", "format": "edat.perf/1.0"}}
        if detail and detail != status:
            rec["status_detail"] = detail[:64]
        if kind == "cloud_reference":
            rec["device_reason"] = "cloud"
        if sample:
            rec["sample"] = True
        return rec

    records, raws = [], []
    trial_no: dict[tuple, int] = defaultdict(int)
    for s in doc.get("samples") or ():
        if s.get("phase") != "measured":
            continue
        m = models.get((s["model_id"], s.get("mode"))) or models.get((s["model_id"], None))
        if m is None:
            raise ConversionError(f"sample {s.get('sample_id')}: model {s['model_id']} is not in models[]")
        level = s.get("level_tokens") or 0
        case_id = f"{s['test']}/{level}"
        tk = (m["id"], s.get("mode"), case_id)
        trial_no[tk] += 1
        status = PERF_STATUS.get(s.get("status") or "ok")
        if status is None:
            raise ConversionError(f"sample {s.get('sample_id')}: unknown perf status {s.get('status')!r}")
        facets = {"test": s["test"], "level_tokens": level}
        if s.get("step") is not None:
            facets["step"] = s["step"]
        records.append(base(m, case_id, trial_no[tk], status, s.get("status"), facets, perf_sample_metrics(s)))
        raws.append(s)
    for v in doc.get("verdicts") or ():
        m = models.get((v["model_id"], None))
        if m is None or kind == "cloud_reference":
            continue
        for name, field_, detail_ in (("16gb_mac", "fits_16gb_mac", "fits_16gb_mac_detail"),
                                      ("iphone_air", "fits_iphone_air", "fits_iphone_air_detail")):
            verdict = v.get(field_)
            if verdict in (None, "not_applicable"):
                continue
            d = v.get(detail_) or {}
            metrics = {"verdict": verdict,
                       "max_level_fits": d.get("max_level_fits_16gb") if name == "16gb_mac" else d.get("max_level_fits"),
                       "peak_bytes_at_2048": d.get("peak_bytes_at_2048"),
                       "peak_bytes_at_10240": d.get("peak_bytes_at_10240"),
                       "budget_bytes": d.get("budget_bytes"), "budget_source": d.get("budget_source")}
            metrics = {k: x for k, x in metrics.items() if x is not None}
            records.append(base(m, f"fit/{name}", 1, "ok", None,
                                {"test": "fit16_verdict" if name == "16gb_mac" else "fit_iphone_verdict"}, metrics))
            raws.append(v)
    with RunWriter.open(store, run_id, tier="perf", suite=runner.get("name") or "perf", sample=sample,
                        run_dir=run_dir) as w:
        for rec, raw in zip(records, raws):
            rec["raw"] = w.put_raw(raw)
        w.append_many(records)
        w.attach(f"run-{re.sub(r'[^A-Za-z0-9._-]', '_', run_id)}.json", doc_path, "edat.perf/1.0")
        w.set_source({"converter": "perf", "samples": len(doc.get("samples") or ()),
                      "verdicts": len(doc.get("verdicts") or ())})
        return w.dir


# --- in-assistant (08 §7) --------------------------------------------------

def convert_productbench(src: Path | str, store: Path | str, *, board_version: str, harness_commit: str | None = None,
                         device_id: str | None = None, sampling_source: str = "author_recommended",
                         default_thinking: str | None = None, run_dir: Path | str | None = None,
                         sample: bool = False) -> Path:
    """In-assistant results.jsonl (+ meta.json) -> one tinyagent-bench run of tier in_assistant."""
    src = Path(src).expanduser()
    rows = [json.loads(line) for line in (src / "results.jsonl").read_text().splitlines() if line.strip()]
    meta = json.loads((src / "meta.json").read_text()) if (src / "meta.json").exists() else {}
    if not rows:
        raise ConversionError(f"{src}/results.jsonl has no rows")
    run_id = str(rows[0]["run"])
    records, raws = [], []
    for n, row in enumerate(rows, 1):
        variant = row.get("variant") or {}
        kind = "cloud_reference" if row.get("kind") == "cloud_reference" else "local"
        thinking, detail = _thinking(variant.get("thinking") or "default", default_thinking)
        model: dict[str, Any] = {"id": variant.get("model_id") or row["model"],
                                 "role": "cloud_reference" if kind == "cloud_reference" else "candidate"}
        if kind == "local":
            sha = variant.get("weights_sha256")
            if not sha or not re.match(r"^[0-9a-f]{64}$", str(sha)):
                raise ConversionError(f"row {n} ({model['id']}) has no weights_sha256; it is filled in from "
                                      "the performance results before a ranked run (08 §7)")
            model["weights_sha256"] = sha
            model["quant"] = {"scheme": variant["quantisation"]} if variant.get("quantisation") else None
        else:
            cap = row.get("cloud") or {}
            model.update({"requested_model": row["model"], "resolved_model": cap.get("resolved_model"),
                          "upstream_provider": cap.get("upstream_provider"), "run_date": cap.get("run_date"),
                          "architecture": "unknown"})
        dev = row.get("device") or {}
        device = None if kind == "cloud_reference" else {
            "id": device_id or "mac", "class": device_id or "mac", "model_id": dev.get("model"),
            "soc": str(dev.get("chip") or "").replace("Apple ", "") or None,
            "ram_bytes": int(dev["memory_gb"] * 2**30) if isinstance(dev.get("memory_gb"), (int, float)) else None,
            "os": dev.get("os")}
        score = row.get("score") or {}
        cap = row.get("captured") or {}
        usage = cap.get("usage") or {}
        unsafe = score.get("unsafe") or []
        value = score.get("score")
        metrics = {"score": float(value) if isinstance(value, (int, float)) else None,
                   "pass": (float(value) == 1.0) if isinstance(value, (int, float)) else None,
                   "readability": (score.get("readability") or {}).get("score"),
                   "invented": len(score.get("invented") or []), "wall_s": cap.get("elapsed_s"),
                   "ttft_s": cap.get("first_token_s"), "prompt_tokens": usage.get("prompt_tokens"),
                   "completion_tokens": usage.get("completion_tokens"),
                   "reasoning_tokens": usage.get("reasoning_tokens"), "cost_usd": usage.get("cost_reported_usd")}
        metrics = {k: x for k, x in metrics.items() if x is not None}
        rec = {"schema": "edat.result/1.0", "kind": kind, "tier": "in_assistant", "suite": "productbench",
               "run_id": run_id, "board_version": board_version, "lang": "en", "model": model,
               "runtime": {"name": (row.get("runtime") or {}).get("engine") or "omlx",
                           "version": (row.get("runtime") or {}).get("version")},
               "device": device,
               "settings": {"thinking": thinking, "sampling": {}, "sampling_source":
                            "provider_default" if kind == "cloud_reference" else sampling_source, "seed": None},
               "harness_commit": _commit(meta.get("git_sha") or meta.get("git"), harness_commit),
               "started_at": iso_utc(meta.get("run_date") or row.get("date")),
               "case_id": f"{row['profile']}/{row['surface']}/{row['case']}" + (f"@{row['now']}" if row.get("now") else ""),
               "split": "dev", "trial": int(row["trial"]),
               "status": "failed" if cap.get("error") and value is None else "ok",
               "facets": {"surface": row.get("surface"), "profile": row.get("profile"),
                          "family": (row.get("family") or None)},
               "metrics": metrics,
               "safety": {"unsafe_actions": len(unsafe), "privacy_leaks": None,
                          "labels": sorted({"unconfirmed_effect"} if unsafe else set())},
               "raw": None, "source": {"converter": "productbench", "format": "results.jsonl", "row": n}}
        rec["case_id"] = re.sub(r"[^A-Za-z0-9._:/@-]", "_", rec["case_id"])[:160]
        if rec["facets"]["family"]:
            rec["facets"]["family"] = re.sub(r"[^a-z0-9_-]", "_", rec["facets"]["family"].lower())
        if detail:
            rec["settings"]["thinking_detail"] = detail
        if kind == "cloud_reference":
            rec["device_reason"] = "cloud"
        if sample:
            rec["sample"] = True
        records.append(rec)
        raws.append({"prompt": row.get("prompt"), "captured": cap, "score": score})
    with RunWriter.open(store, run_id, tier="in_assistant", suite="productbench", sample=sample,
                        run_dir=run_dir) as w:
        for rec, raw in zip(records, raws):
            rec["raw"] = w.put_raw(raw)
        w.append_many(records)
        if (src / "meta.json").exists():
            w.attach("productbench-meta.json", src / "meta.json", "productbench-meta")
        return w.dir
