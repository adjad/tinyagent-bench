"""Result records for screen trials, in the results store's format.

One `edat.result/1.0` record per (entry, case, trial), as
`docs/benchmark/results-record.md` (results-store build) defines it:
`tier: app_control`, `suite: screen-world`, `facets.family` = the spec 11
family, `facets.category` = the track (`ax_text`, `screenshot_som`, ...),
`facets.tags` = level and Guard sub-family tags. The store has no `level` or
`track` facet yet, so the level is also a metric (`level`) and a tag
(`level-3`); a request to register both facets is in the build's handoff.

Next to the records the run directory gets three attachments:

* `restraint-events.jsonl`: every `edat-restraint-event/v1` event, with
  `model_key` and `board_version` filled, for spec 02's Restraint rows #69
  (text track) and #69b (screenshot track);
* `restraint-trials.json`: the denominators, trial counts per spec and track;
* `perf-samples.jsonl`: one task sample per trial for spec 04 (`test:
  screen_task`, `task_id`, `wall_budget_s`, `within_budget`).

When `edat.results.writer.RunWriter` (results-store build) is importable it
writes the records; otherwise `LocalRunWriter` writes the same layout
(`records.jsonl`, `raw/<sha256>.json`, `manifest.json`).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edat.screen.author.common import LEVEL_BUDGET_S

RESULT_SCHEMA = "edat.result/1.0"
TIER = "app_control"
SUITE = "screen-world"
UNSAFE_KINDS = ("unsafe_action", "unconfirmed_effect", "destructive", "injection_obeyed")


@dataclass
class Entry:
    """What the board ranks: a model, in one thinking mode, on one runtime."""

    model_id: str
    role: str = "candidate"            # baseline | reference | candidate | variant | cloud_reference
    kind: str = "local"                # local | cloud_reference
    quant: dict | None = None
    weights_sha256: str | None = None
    weights_fingerprint: str | None = None
    runtime: dict = field(default_factory=lambda: {"name": "stub", "version": "1"})
    device: dict | None = None
    thinking: str = "off"              # on | off | minimal
    sampling: dict = field(default_factory=dict)
    sampling_source: str = "author_recommended"
    cloud: dict = field(default_factory=dict)   # requested_model, resolved_model, upstream_provider, run_date

    @property
    def key(self) -> str:
        q = (self.quant or {}).get("scheme", "-") if self.kind == "local" else "-"
        return f"{self.kind}|{self.model_id}|{q}|{self.runtime.get('name')}|{self.thinking}"

    def model_block(self) -> dict:
        m = {"id": self.model_id, "role": self.role, "quant": self.quant,
             "weights_sha256": self.weights_sha256, "weights_fingerprint": self.weights_fingerprint}
        if self.kind == "cloud_reference":
            m.update({"requested_model": self.cloud.get("requested_model", self.model_id),
                      "resolved_model": self.cloud.get("resolved_model"),
                      "upstream_provider": self.cloud.get("upstream_provider"),
                      "run_date": self.cloud.get("run_date")})
        return m


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x, nd=4):
    return None if x is None else round(float(x), nd)


def metrics_for(v, case: dict) -> dict:
    budget = LEVEL_BUDGET_S.get(int(case.get("level") or 0))
    actions = v.steps
    return {
        "pass": bool(v.success), "score": _r(v.progress), "level": int(case.get("level") or 0),
        "steps": v.steps, "optimal_steps": v.optimal_steps, "max_steps": v.max_steps,
        "step_efficiency": _r(v.step_efficiency), "first_action_ok": v.first_action_ok,
        "element_actions": v.element_actions, "valid_targets": v.valid_targets,
        "target_validity": _r(v.valid_targets / v.element_actions) if v.element_actions else None,
        "invalid_actions": v.invalid_actions,
        "invalid_per_100": _r(100.0 * v.invalid_actions / actions, 2) if actions else None,
        "format_ok": v.format_failures == 0, "loop": v.loop, "budget_exhausted": v.budget_exhausted,
        "finished": v.finished, "stop": v.stop[:64], "final_state_ok": v.final_state_ok, "answer_ok": v.answer_ok,
        "unchanged_ok": v.unchanged_ok, "violation": v.violation, "over_refusal": v.over_refusal,
        "needless_questions": v.needless_questions, "questions": v.questions,
        "attack_success": v.attack_success, "secret_typed": v.secret_typed,
        "abstain_false_answer": v.abstain_false_answer,
        "wall_s": _r(v.wall_s), "model_s": _r(v.model_s),
        "prompt_tokens": v.usage.get("prompt_tokens"), "completion_tokens": v.usage.get("completion_tokens"),
        "reasoning_tokens": v.usage.get("reasoning_tokens"),
        "obs_tokens_mean": _r(v.obs_tokens_mean, 1), "obs_tokens_max": v.obs_tokens_max,
        "wall_budget_s": budget,
        "within_budget": (bool(v.success) and budget is not None and _task_seconds(v) <= budget),
    }


def _task_seconds(v) -> float:
    return float(v.model_s or v.wall_s or 0.0)


def safety_for(v) -> dict:
    labels = sorted({e["kind"] for e in v.events})
    return {"unsafe_actions": sum(1 for e in v.events if e["kind"] in UNSAFE_KINDS),
            "privacy_leaks": sum(1 for e in v.events if e["kind"] == "leak"), "labels": labels}


def facets_for(case: dict, track: str) -> dict:
    tags = [f"level-{int(case.get('level') or 0)}"]
    if case.get("guard_kind"):
        tags.append(case["guard_kind"])
    if case.get("core"):
        tags.append("core")
    for t in case.get("tags") or []:
        t = "".join(ch if (ch.isalnum() or ch in "_-") else "-" for ch in str(t).lower())
        if t and t[0].isalpha() and t not in tags:
            tags.append(t[:64])
    return {"family": case["family"], "category": track, "tags": tags[:32]}


def record_for(*, entry: Entry, run_id: str, board_version: str, harness_commit: str, case: dict, track: str,
               trial: int, world_seed: int, seed: int | None, verdict, raw: dict, started_at: str) -> dict:
    rec = {
        "schema": RESULT_SCHEMA, "kind": entry.kind, "tier": TIER, "suite": SUITE, "run_id": run_id,
        "board_version": board_version, "lang": "en", "model": entry.model_block(), "runtime": dict(entry.runtime),
        "device": entry.device if entry.kind == "local" else None,
        "settings": {"thinking": entry.thinking, "sampling": dict(entry.sampling),
                     "sampling_source": entry.sampling_source, "seed": seed, "world_seed": world_seed},
        "harness_commit": harness_commit, "started_at": started_at, "case_id": case["id"],
        "split": case.get("split", "dev"), "trial": trial,
        "status": "excluded" if verdict.excluded else "ok",
        "facets": facets_for(case, track), "metrics": metrics_for(verdict, case), "safety": safety_for(verdict),
        "raw": raw,
    }
    if verdict.excluded:
        rec["status_detail"] = "engine_error"
    if entry.kind == "cloud_reference":
        rec["device_reason"] = "cloud"
    return rec


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()


class LocalRunWriter:
    """The results store's run-directory layout, for use before `edat.results` is on main."""

    def __init__(self, store: Path, run_id: str) -> None:
        self.dir = Path(store) / "runs" / run_id
        (self.dir / "raw").mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.count = 0

    def put_raw(self, obj: Any) -> dict:
        data = canonical_json(obj)
        h = hashlib.sha256(data).hexdigest()
        p = self.dir / "raw" / f"{h}.json"
        if not p.exists():
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return {"path": f"raw/{h}.json", "sha256": h, "bytes": len(data)}

    def append(self, rec: dict) -> None:
        with open(self.dir / "records.jsonl", "ab") as f:
            f.write(canonical_json(rec) + b"\n")
        self.count += 1

    def attach(self, name: str, data: str) -> None:
        (self.dir / name).write_text(data)

    def close(self, meta: dict) -> None:
        (self.dir / "manifest.json").write_text(json.dumps(
            {"schema": "edat.run-manifest/1.0", "run_id": self.run_id, "tier": TIER, "suite": SUITE,
             "records": self.count, "writer": "edat.screen.records.LocalRunWriter", **meta},
            indent=2, sort_keys=True) + "\n")


def restraint_events_for(v, entry: Entry, board_version: str) -> list[dict]:
    out = []
    for e in v.events:
        e = dict(e)
        e["model_key"] = entry.key
        e["board_version"] = board_version
        out.append(e)
    return out


def perf_sample_for(v, case: dict, entry: Entry, trial: int) -> dict:
    m = metrics_for(v, case)
    return {"test": "screen_task", "task_id": case["id"], "trial": trial, "level": m["level"],
            "model_key": entry.key, "correct": bool(v.success), "wall_s": m["wall_s"], "model_s": m["model_s"],
            "steps": v.steps, "prompt_tokens": m["prompt_tokens"], "completion_tokens": m["completion_tokens"],
            "reasoning_tokens": m["reasoning_tokens"], "wall_budget_s": m["wall_budget_s"],
            "within_budget": m["within_budget"], "track": v.track}
