"""A synthetic sample snapshot for the website to render.

SAMPLE DATA: every model name, hash and number here is invented and generated
from a seeded function. Nothing describes a real model. The two references are
invented too ("Sample Tuned 4B", "Sample Base 4B"); in a real board they are
Gemma-Wisp and base Gemma 4 E4B.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

from edat.results.aggregate import Options
from edat.results.export import export
from edat.results.reader import read_set
from edat.results.writer import RunWriter

BOARD = "sample-0"
COMMIT = "0000000"            # deliberately not a real commit
STARTED = "2026-11-18T09:00:00Z"
MAC = {"id": "mac-ref", "class": "mac-ref", "model_id": "SampleMac", "soc": "M5 Pro", "ram_bytes": 24 * 2**30,
       "os": "macOS 27"}
PHONE = {"id": "iphone-ref", "class": "iphone-ref", "model_id": "SamplePhone", "soc": "A19 Pro",
         "ram_bytes": 12 * 2**30, "os": "iOS 27"}
REFERENCES = {"tuned": ("Sample Tuned 4B",), "base": ("Sample Base 4B",)}
FAMILIES = ("select", "parallel", "sequential", "mixed", "pev", "recovery", "abstain")
LANES = ("assistant", "neutral")
LEVELS = (512, 1024, 2048, 5120, 10240, 32768, 65536)
GiB = 2**30

# id, role, params (B), active (B), skill 0..1, safety 0..1, weights GiB at 4-bit, fit verdict, thinking modes
MODELS = [
    ("Sample Tuned 4B", "baseline", 4.0, 4.0, 0.62, 0.80, 3.0, "fits", ("off",)),
    ("Sample Base 4B", "reference", 4.0, 4.0, 0.52, 0.86, 3.0, "fits", ("off",)),
    ("Sample Model C 3B", "candidate", 3.0, 3.0, 0.50, 0.90, 2.1, "fits", ("off",)),
    ("Sample Model D 8B", "candidate", 8.0, 8.0, 0.68, 0.84, 5.2, "fits", ("off", "on")),
    ("Sample Model E 12B MoE", "candidate", 12.0, 2.5, 0.64, 0.78, 7.4, "fits_small_context", ("off",)),
    ("Sample Model F 1B", "candidate", 1.2, 1.2, 0.34, 0.70, 0.9, "fits", ("off",)),
    ("Sample Model G 14B", "candidate", 14.0, 14.0, 0.74, 0.88, 9.6, "does_not_fit", ("off",)),
    ("Sample Model H 6B", "candidate", 6.0, 6.0, 0.58, 0.82, 4.1, "not_measured", ("off",)),
]
CLOUD = [("sample-cloud/model-a", 0.86, 0.95), ("sample-cloud/model-b", 0.80, 0.93)]
INVENTED_TOOLS = ("get_weather", "search_calendar", "send_message", "draft_message", "create_event")


def _h(text: str) -> str:
    return hashlib.sha256(("sample:" + text).encode()).hexdigest()


def _base(model: dict, *, kind: str, tier: str, run_id: str, device: dict | None, thinking: str,
          case_id: str, trial: int, runtime: dict) -> dict:
    rec = {"schema": "edat.result/1.0", "kind": kind, "tier": tier, "suite": "sample", "run_id": run_id,
           "board_version": BOARD, "lang": "en", "model": model, "runtime": runtime, "device": device,
           "settings": {"thinking": thinking, "sampling": {} if kind == "cloud_reference"
                        else {"temperature": 0.7, "top_p": 0.95},
                        "sampling_source": "provider_default" if kind == "cloud_reference" else "author_recommended",
                        "seed": 1000 + trial}, "harness_commit": COMMIT, "started_at": STARTED,
           "case_id": case_id, "split": "dev", "trial": trial, "status": "ok", "facets": {},
           "metrics": {}, "safety": {"unsafe_actions": 0, "privacy_leaks": 0}, "raw": None,
           "sample": True}
    if kind == "cloud_reference":
        rec["device_reason"] = "cloud"
    return rec


def _local_model(m) -> dict:
    mid, role, total, active, *_ = m
    return {"id": mid, "role": role, "weights_sha256": _h(mid), "quant": {"scheme": "q4", "bits": 4.5},
            "architecture": "moe" if active < total else "dense", "params_total": int(total * 1e9),
            "params_active": int(active * 1e9), "headline_entry": True}


def _trajectory(rng: random.Random, family: str, ok: bool, unsafe: bool) -> dict:
    tool = INVENTED_TOOLS[rng.randrange(len(INVENTED_TOOLS))]
    calls = [{"name": tool, "args": {"query": "sample query"}, "result": "(sample tool result)"}]
    if unsafe:
        calls.append({"name": "send_message", "args": {"to": "sample-contact", "text": "sample text"},
                      "result": "(sent without asking)"})
    return {"steps": [{"calls": calls, "content": ""}],
            "final": "Sample answer." if ok else "Sample answer that misses the point.",
            "labels": [] if ok else (["unsafe_call"] if unsafe else ["wrong_answer"])}


def build_sample_store(store: Path) -> list[Path]:
    """Write the synthetic runs into `store`; return their directories."""
    rng = random.Random(20261118)
    runs: list[Path] = []
    omlx = {"name": "omlx", "version": "sample-0.7"}
    mlxs = {"name": "mlx-swift", "version": "sample-0.25"}

    def agentic_run(run_id: str, entries, device, runtime, kind="local", n_cases=2):
        with RunWriter.open(store, run_id, tier="agentic", suite="sample", sample=True) as w:
            recs = []
            for model, thinking, skill, safe in entries:
                for fam in FAMILIES:
                    for lane in LANES:
                        for c in range(n_cases):
                            case = f"{lane}-{fam}-{c + 1:02d}"
                            difficulty = (int(_h(f"{fam}/{lane}/{c}")[:6], 16) % 100) / 250.0
                            for t in (1, 2, 3):
                                p_ok = max(0.02, min(0.98, skill + (0.06 if thinking == "on" else 0) - difficulty
                                                     + (0.05 if lane == "assistant" and model["id"].startswith(
                                                         "Sample Tuned") else 0)))
                                ok = rng.random() < p_ok
                                unsafe = rng.random() > safe and fam in ("select", "sequential", "mixed")
                                leak = rng.random() > (safe + 0.08)
                                rec = _base(model, kind=kind, tier="agentic", run_id=run_id, device=device,
                                            thinking=thinking, case_id=case, trial=t, runtime=runtime)
                                rec["facets"] = {"lane": lane, "family": fam}
                                rec["metrics"] = {"pass": ok and not unsafe, "score": 1.0 if ok else
                                                  round(rng.random() * 0.8, 3), "format_ok": rng.random() < 0.97,
                                                  "steps": 1 + rng.randrange(4), "wall_s": round(2 + rng.random() * 9, 2)}
                                rec["safety"] = {"unsafe_actions": int(unsafe), "privacy_leaks": int(leak),
                                                 "labels": (["unsafe_call"] if unsafe else []) +
                                                 (["canary_leak"] if leak else [])}
                                if model["id"] == "Sample Model F 1B" and fam == "pev" and t == 3:
                                    # Deliberate: 4 of 84 trials excluded (4.8 %) marks this entry invalid.
                                    rec["status"], rec["status_detail"] = "excluded", "engine_error"
                                    rec["metrics"] = {}
                                rec["raw"] = w.put_raw(_trajectory(rng, fam, ok, unsafe))
                                recs.append(rec)
            w.append_many(recs)
            return w.dir

    def quality_run(run_id: str, entries, device, runtime, kind="local"):
        with RunWriter.open(store, run_id, tier="quality", suite="sample", sample=True) as w:
            recs = []
            for model, thinking, skill, _ in entries:
                for fam in ("readability", "email_summary", "extraction"):
                    for c in range(3):
                        for t in (1, 2, 3):
                            rec = _base(model, kind=kind, tier="quality", run_id=run_id, device=device,
                                        thinking=thinking, case_id=f"{fam}-{c + 1:02d}", trial=t, runtime=runtime)
                            score = max(0.0, min(1.0, skill + 0.15 + (rng.random() - 0.5) * 0.3))
                            rec["facets"] = {"family": fam}
                            rec["metrics"] = {"score": round(score, 3), "pass": score >= 0.7,
                                              "visible_tokens": int(120 + rng.random() * 200),
                                              "fabricated_atoms": int(rng.random() < (1 - skill) * 0.5)}
                            rec["safety"] = {"unsafe_actions": None, "privacy_leaks": None}
                            rec["raw"] = w.put_raw({"final": "Sample summary text."})
                            recs.append(rec)
            w.append_many(recs)
            return w.dir

    def assistant_run(run_id: str, entries, device, runtime, kind="local"):
        with RunWriter.open(store, run_id, tier="in_assistant", suite="sample", sample=True) as w:
            recs = []
            for model, thinking, skill, safe in entries:
                for profile in ("student", "tech-worker"):
                    for surface in ("ask", "brief", "day"):
                        for c in range(2):
                            for t in (1, 2, 3):
                                rec = _base(model, kind=kind, tier="in_assistant", run_id=run_id, device=device,
                                            thinking=thinking, case_id=f"{profile}/{surface}/{c + 1:02d}",
                                            trial=t, runtime=runtime)
                                score = max(0.0, min(1.0, skill + 0.1 + (rng.random() - 0.5) * 0.5))
                                score = 1.0 if score > 0.85 else round(score, 3)
                                rec["facets"] = {"surface": surface, "profile": profile}
                                rec["metrics"] = {"score": score, "pass": score == 1.0}
                                rec["safety"] = {"unsafe_actions": int(rng.random() > safe + 0.1),
                                                 "privacy_leaks": None}
                                rec["raw"] = w.put_raw({"captured": {"answer": "Sample answer about the day."}})
                                recs.append(rec)
            w.append_many(recs)
            return w.dir

    def perf_run(run_id: str, device, runtime, budget_gib: float, phone: bool = False):
        with RunWriter.open(store, run_id, tier="perf", suite="sample", sample=True) as w:
            recs = []
            for m in MODELS:
                model = _local_model(m)
                weights_gib, verdict = m[6], m[7]
                if phone and weights_gib > 4.5:
                    continue
                for level in LEVELS:
                    kv_gib = level / 1024 * 0.03 * (m[3] / 4)
                    peak = (weights_gib + 0.6 + kv_gib) * GiB
                    for rep in (1, 2):
                        rec = _base(model, kind="local", tier="perf", run_id=run_id, device=device, thinking="off",
                                    case_id=f"level/{level}", trial=rep, runtime=runtime)
                        rec["settings"]["sampling_source"] = "engine_default"
                        rec["settings"]["sampling"] = {}
                        rec["facets"] = {"test": "level", "level_tokens": level}
                        rec["safety"] = {"unsafe_actions": None, "privacy_leaks": None}
                        cap = (6 if phone else 16) * GiB      # memory the model may use (sample values)
                        if peak > cap:
                            rec["status"], rec["status_detail"] = "out_of_memory", "oom_killed"
                            rec["metrics"] = {"footprint_peak_bytes": int(peak)}
                        elif level > 32768 and m[2] < 2:
                            rec["status"], rec["status_detail"] = "beyond_window", "exceeds_native_window"
                        else:
                            speed = (1.0 if not phone else 0.35) / (m[3] ** 0.8)
                            jitter = 1 + (rng.random() - 0.5) * 0.06
                            rec["metrics"] = {"ttft_s": round(level / (900 * speed * 4) * jitter + 0.05, 4),
                                              "prefill_tps": round(900 * speed * 4 / jitter, 1),
                                              "decode_tps": round(55 * speed * 4 / jitter / (1 + level / 65536), 2),
                                              "footprint_peak_bytes": int(peak * jitter)}
                        rec["raw"] = w.put_raw({"sample_id": f"{m[0]}-{level}-{rep}"})
                        recs.append(rec)
                if not phone:
                    rec = _base(model, kind="local", tier="perf", run_id=run_id, device=device, thinking="off",
                                case_id="fit/16gb_mac", trial=1, runtime=runtime)
                    rec["settings"]["sampling_source"] = "engine_default"
                    rec["settings"]["sampling"] = {}
                    rec["facets"] = {"test": "fit16_verdict"}
                    rec["safety"] = {"unsafe_actions": None, "privacy_leaks": None}
                    p2 = int((weights_gib + 0.6 + 0.06 * m[3] / 4) * GiB)
                    p10 = int((weights_gib + 0.6 + 0.3 * m[3] / 4) * GiB)
                    rec["metrics"] = {"budget_bytes": int(budget_gib * GiB), "budget_source": "derived-v1",
                                      "peak_bytes_at_2048": p2, "peak_bytes_at_10240": p10}
                    if verdict != "not_measured":
                        rec["metrics"]["verdict"] = verdict
                        rec["metrics"]["max_level_fits"] = {"fits": 32768, "fits_small_context": 2048,
                                                            "does_not_fit": 0}[verdict]
                        rec["raw"] = w.put_raw({"verdict": verdict})
                        recs.append(rec)
            w.append_many(recs)
            return w.dir

    local_entries = []
    for m in MODELS:
        for thinking in m[8]:
            local_entries.append((_local_model(m), thinking, m[4], m[5]))
    runs.append(agentic_run("sample-agentic-mac", local_entries, MAC, omlx))
    runs.append(quality_run("sample-quality-mac", local_entries, MAC, omlx))
    runs.append(assistant_run("sample-assistant-mac", local_entries, MAC, omlx))
    runs.append(perf_run("sample-perf-mac", MAC, omlx, 10.0))
    phone_entries = [(_local_model(m), "off", m[4] - 0.02, m[5]) for m in MODELS if m[6] <= 4.5]
    runs.append(agentic_run("sample-agentic-phone", phone_entries, PHONE, mlxs, n_cases=1))
    runs.append(perf_run("sample-perf-phone", PHONE, mlxs, 5.5, phone=True))
    cloud_entries = []
    for cid, skill, safe in CLOUD:
        for thinking in ("off", "on"):
            model = {"id": cid, "role": "cloud_reference", "requested_model": cid, "resolved_model": cid,
                     "upstream_provider": "sample-provider", "architecture": "unknown",
                     "run_date": {"first": STARTED, "last": "2026-11-18T11:00:00Z"}}
            cloud_entries.append((model, thinking, skill, safe))
    cloud_runtime = {"name": "openrouter", "version": None}
    runs.append(agentic_run("sample-agentic-cloud", cloud_entries, None, cloud_runtime, kind="cloud_reference"))
    runs.append(quality_run("sample-quality-cloud", cloud_entries, None, cloud_runtime, kind="cloud_reference"))
    return runs


def build_sample_export(store: Path, data_dir: Path, site_dir: Path, *, resamples: int = 2000,
                        write_records: bool = True) -> dict:
    """Build the synthetic runs and export them as board version sample-0."""
    runs = build_sample_store(store)
    sets = [read_set(r) for r in runs]
    return export(sets, data_dir=data_dir, site_dir=site_dir,
                  options=Options(references=REFERENCES, resamples=resamples, facet_resamples=500),
                  trajectories_per_entry=12, now=STARTED, write_records=write_records)
