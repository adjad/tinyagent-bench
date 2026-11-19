"""Write agentic trials as `edat.result/1.0` records. The only record writer here.

The record shape is the results store's (docs/benchmark/results-record.md on
the results-store branch): one JSON object per trial in `records.jsonl`, raw
output in `raw/<sha256>.json`, a `manifest.json` per run. Safety counts sit in
`safety`, never in `metrics`. Until the results store's own writer
(`edat.results.writer`) lands, this module writes the same layout; switching
to it later changes only `RunWriter`.

Held-out runs must be written under `$EDAT_PRIVATE_DIR/results`; `RunWriter`
refuses to write a held-out record anywhere inside the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

RESULT_SCHEMA = "edat.result/1.0"
MANIFEST_SCHEMA = "edat.run-manifest/1.0"
REPO = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}-{secrets.token_hex(2)}"


def _tier(family: str) -> str:
    return {"preferences": "preferences", "long_session": "long_session", "memory": "memory"}.get(family, "agentic")


def _thinking(settings, client_meta: dict) -> str:
    entry = client_meta.get("reasoning_entry")
    if entry == "minimal reasoning":
        return "minimal"
    return "on" if settings.thinking else "off"


def trial_record(result, *, run_id: str, board_version: str, harness_commit: str, model: dict,
                 runtime: dict, device: dict | None, kind: str = "local", started_at: str | None = None,
                 raw_ref: dict | None = None, sample: bool = False) -> dict:
    """One `edat.result/1.0` record for a TrialResult (edat.cases.runner)."""
    v = result.verdict
    c = result.case
    s = result.settings
    steps = [st for t in result.run.turns for st in t.trace.steps]
    usage = lambda k: sum((st.usage.get(k) or 0) for st in steps) if any(st.usage.get(k) is not None for st in steps) else None
    wall = sum((st.timing.get("total_s") or 0.0) for st in steps)
    cost = usage("cost_usd")
    opt = c.get("optimal") or {}
    last_meta = {}
    sampling = s.sampling.to_record() if s.sampling else {}
    rec = {
        "schema": RESULT_SCHEMA, "kind": kind, "tier": _tier(c["family"]), "suite": "reference-loop",
        "run_id": run_id, "board_version": board_version, "lang": "en",
        "model": model, "runtime": runtime, "device": device,
        "settings": {"thinking": _thinking(s, last_meta),
                     "sampling": {k: v for k, v in sampling.items() if k != "source"} or None,
                     "sampling_source": sampling.get("source") or "engine_default",
                     "seed": s.seed, "world_seed": result.world_seed, "loop": result.loop,
                     "max_steps": s.max_steps},
        "harness_commit": harness_commit, "started_at": started_at or utc_now(),
        "case_id": c["id"], "split": c.get("split", "dev"), "trial": result.trial,
        "status": "excluded" if v.excluded else "ok",
        "facets": {"lane": c["lane"], "family": c["family"], "tags": list(c.get("tags") or [])},
        "metrics": {
            "pass": bool(v.success) if not v.excluded else None, "score": v.progress,
            "plan_ok": v.plan, "execute_ok": v.execute, "verify_ok": v.verify,
            "selection_ok": v.selection_ok, "args_ok": v.args_ok, "format_ok": v.format_ok,
            "parallel_strict": v.parallel_strict, "steps": v.steps, "model_calls": v.model_calls,
            "optimal_steps": opt.get("steps"), "optimal_calls": opt.get("calls"),
            "pass_within_budget": bool(v.success and (not c.get("max_calls") or v.model_calls <= c["max_calls"])),
            "wall_s": wall, "prompt_tokens": usage("prompt_tokens"),
            "completion_tokens": usage("completion_tokens"), "reasoning_tokens": usage("reasoning_tokens"),
            "cost_usd": cost, "false_claim": v.false_claim, "clarify_ok": v.clarify_ok,
            "over_refusal": v.over_refusal, "retry_ok": v.retry_ok,
            "malformed_calls": v.malformed_calls, "hallucinated_calls": v.hallucinated_calls,
        },
        "safety": {"unsafe_actions": v.unsafe_calls + sum(1 for x in v.violations if not x.startswith("forbidden")
                                                            and not x.startswith("privacy")),
                   "privacy_leaks": len(v.leaks),
                   "labels": sorted({l for l in v.labels if l in (
                       "unsafe_call", "unconfirmed_effect", "confirmation_bypass", "injection_followed",
                       "collateral", "wrong_recipient", "unresolved_recipient", "canary_leak", "web_exfil")})},
        "raw": raw_ref,
    }
    if c["family"] == "preferences":
        rec["metrics"]["dimension"] = c.get("dimension")
        rec["metrics"]["arrival"] = c.get("arrival")
    if raw_ref is None:
        rec["raw_reason"] = "not_written"
    if v.excluded:
        rec["status_detail"] = "engine_error"
        rec["metrics"] = {"pass": None}
    if sample:
        rec["sample"] = True
    return rec


class RunWriter:
    def __init__(self, root: str | Path, run_id: str | None = None, *, manifest: dict | None = None) -> None:
        self.run_id = run_id or new_run_id()
        self.dir = Path(root) / "runs" / self.run_id
        self.manifest = {"schema": MANIFEST_SCHEMA, "run_id": self.run_id, "tier": "agentic",
                         "created_at": utc_now(), "records": 0, **(manifest or {})}
        self._checked = False

    def _guard(self, split: str) -> None:
        if split == "heldout":
            resolved = self.dir.resolve()
            if resolved.is_relative_to(REPO):
                raise PermissionError("held-out results never go inside the repository")
            priv = os.environ.get("EDAT_PRIVATE_DIR")
            if not priv or not resolved.is_relative_to(Path(priv).resolve()):
                raise PermissionError("held-out results go under $EDAT_PRIVATE_DIR/results")

    def write_raw(self, payload: dict) -> dict:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
        sha = hashlib.sha256(body).hexdigest()
        raw_dir = self.dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{sha}.json"
        if not path.exists():
            path.write_bytes(body)
        return {"path": f"raw/{sha}.json", "sha256": sha, "bytes": len(body)}

    def append(self, result, **kw) -> dict:
        self._guard(result.case.get("split", "dev"))
        self.dir.mkdir(parents=True, exist_ok=True)
        raw = self.write_raw(result.trajectory())
        rec = trial_record(result, run_id=self.run_id, raw_ref=raw, **kw)
        with open(self.dir / "records.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str) + "\n")
        self.manifest["records"] += 1
        tmp = self.dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(self.manifest, indent=2, sort_keys=True))
        tmp.replace(self.dir / "manifest.json")
        return rec
