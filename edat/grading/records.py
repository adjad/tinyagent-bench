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
REPO = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}-{secrets.token_hex(2)}"


# tinyagent-bench Sampling.source -> the store's sampling_source vocabulary.
SAMPLING_SOURCE = {"author_recommended": "author_recommended", "fixed_sensitivity": "fixed_sensitivity",
                   "provider_default": "provider_default", "unspecified": "engine_default",
                   "greedy_diagnostic": "engine_default"}


# Agent loop -> the store's `suite`. Records of the two loops pair on everything
# else (model, case, trial, world seed, settings), so the store can show the
# Harbor-loop column beside the reference-loop headline.
SUITES = {"edat-reference-v1": "reference-loop", "harbor-ask-v1": "harbor-loop"}


def suite_for(loop: str) -> str:
    return SUITES.get(loop, loop.replace("edat-", "")[:64])


def _tier(family: str) -> str:
    return {"preferences": "preferences", "long_session": "long_session", "memory": "memory"}.get(family, "agentic")


def _thinking(settings, client_meta: dict) -> str:
    entry = client_meta.get("reasoning_entry")
    if entry == "minimal reasoning":
        return "minimal"
    return "on" if settings.thinking else "off"


def trial_record(result, *, run_id: str, board_version: str, harness_commit: str, model: dict,
                 runtime: dict, device: dict | None, kind: str = "local", started_at: str | None = None,
                 raw_ref: dict | None = None, sample: bool = False, seed_supported: bool = True,
                 selection: dict | None = None) -> dict:
    """One `edat.result/1.0` record for a TrialResult (edat.cases.runner).

    `kind` is "local" or "cloud_reference". A cloud record gets `device: null`
    with `device_reason: "cloud"`, and `model` must carry requested_model,
    resolved_model, upstream_provider and run_date (the store checks them).
    `seed_supported=False` records the seed as "unsupported".
    `selection` ({tier, manifest_sha256, n_items, ci95_half_width_pts}) names the
    case selection the record belongs to; the store never pools two selections.
    """
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
        "schema": RESULT_SCHEMA, "kind": kind, "tier": _tier(c["family"]), "suite": suite_for(result.loop),
        "run_id": run_id, "board_version": board_version, "lang": "en",
        "model": model, "runtime": {"version": "unspecified", **runtime}, "device": device,
        "settings": {"thinking": _thinking(s, last_meta),
                     "sampling": {("repeat_penalty" if k == "repetition_penalty" else k): v
                                  for k, v in sampling.items() if k != "source"},
                     "sampling_source": SAMPLING_SOURCE.get(sampling.get("source") or "unspecified", "engine_default"),
                     "seed": s.seed if seed_supported else "unsupported", "world_seed": result.world_seed},
        "harness_commit": harness_commit, "started_at": started_at or utc_now(),
        "case_id": c["id"], "split": c.get("split", "dev"), "trial": result.trial,
        "status": "not_applicable" if v.not_applicable else "excluded" if v.excluded else "ok",
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
            "loop": result.loop, "max_steps": s.max_steps,
            "repairs": ",".join(sorted({r["kind"] for t in result.run.turns for r in t.trace.repairs}))[:64] or None,
        },
        "safety": {"unsafe_actions": v.unsafe_calls + sum(1 for x in v.violations if not x.startswith("forbidden")
                                                            and not x.startswith("privacy")),
                   "privacy_leaks": len(v.leaks),
                   "labels": sorted({l for l in v.labels if l in (
                       "unsafe_call", "unconfirmed_effect", "confirmation_bypass", "injection_followed",
                       "collateral", "wrong_recipient", "unresolved_recipient", "canary_leak", "web_exfil")})},
        "raw": raw_ref,
    }
    if kind == "cloud_reference":
        rec["device"] = None
        rec["device_reason"] = "cloud"
        if rec["settings"]["sampling_source"] == "engine_default":
            rec["settings"]["sampling_source"] = "provider_default"
    if c["family"] == "preferences":
        rec["metrics"]["dimension"] = c.get("dimension")
        rec["metrics"]["arrival"] = c.get("arrival")
    if raw_ref is None:
        rec["raw_reason"] = "not_written"
    if v.not_applicable:
        rec["status_detail"] = v.not_applicable[:64]
        rec["metrics"] = {"pass": None, "loop": result.loop}
    elif v.excluded:
        engine = next((t.trace.engine_error for t in result.run.turns if t.trace.engine_error), "") or ""
        rec["status_detail"] = "harbor_error" if engine.startswith("harbor") else "engine_error"
        rec["metrics"] = {"pass": None, "loop": result.loop}
    if selection:
        rec["selection"] = dict(selection)
    if sample:
        rec["sample"] = True
    return rec


class _LocalRun:
    """Fallback run directory for when the results store's package is not installed.

    Same layout (records.jsonl, raw/<sha256>.json, manifest.json) but a manifest
    that does not claim the store's schema, so the store's validator will not
    mistake it for its own. Import the store package to write real runs.
    """

    def __init__(self, root: Path, run_id: str, tier: str, suite: str = "reference-loop") -> None:
        self.run_id, self.tier = run_id, tier
        self.dir = Path(root) / "runs" / run_id
        self.manifest = {"schema": "edat-agentic-local-run/0", "run_id": run_id, "tier": tier,
                         "suite": suite, "created_at": utc_now(), "records": 0}

    def put_raw(self, payload) -> dict:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
        sha = hashlib.sha256(body).hexdigest()
        raw_dir = self.dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{sha}.json"
        if not path.exists():
            path.write_bytes(body)
        return {"path": f"raw/{sha}.json", "sha256": sha, "bytes": len(body)}

    def append(self, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "records.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str) + "\n")
        self.manifest["records"] += 1
        tmp = self.dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(self.manifest, indent=2, sort_keys=True))
        tmp.replace(self.dir / "manifest.json")

    def close(self) -> None:
        pass


class _StoreRun:
    """A run in the results store, through its own writer (edat.results.writer)."""

    def __init__(self, root: Path, run_id: str, tier: str, suite: str = "reference-loop") -> None:
        from edat.results.writer import RunWriter as StoreWriter
        self.run_id, self.tier = run_id, tier
        self._cm = StoreWriter.open(root, run_id, tier=tier, suite=suite)
        self._w = self._cm.__enter__()
        self.dir = self._w.dir

    def put_raw(self, payload) -> dict:
        return self._w.put_raw(json.loads(json.dumps(payload, default=str)))

    def append(self, rec: dict) -> None:
        self._w.append(rec)

    def close(self) -> None:
        self._cm.__exit__(None, None, None)


def store_available() -> bool:
    try:
        import edat.results.writer  # noqa: F401
    except ImportError:
        return False
    return True


class RunWriter:
    """Writes agentic trials, one run per (tier, suite): tiers agentic,
    preferences, memory, long_session; suites reference-loop and harbor-loop.
    The store keeps one tier and one suite per run.

    Uses the results store's writer when `edat.results` is importable, else a
    local fallback with the same layout. Raw trajectories go through
    `put_raw`, never into the record.
    """

    def __init__(self, root: str | Path, run_id: str | None = None, *, prefer_store: bool = True,
                 manifest: dict | None = None) -> None:
        self.root = Path(root)
        self.base_id = run_id or new_run_id()
        self.prefer_store = prefer_store and store_available()
        self.runs: dict[str, object] = {}

    @property
    def run_id(self) -> str:
        return self.base_id

    @property
    def dir(self) -> Path:
        return self.root / "runs" / self.base_id

    def _run(self, tier: str, suite: str):
        key = (tier, suite)
        if key not in self.runs:
            rid = self.base_id if not self.runs else new_run_id()
            cls = _StoreRun if self.prefer_store else _LocalRun
            self.runs[key] = cls(self.root, rid, tier, suite)
        return self.runs[key]

    def _guard(self, split: str, path: Path) -> None:
        if split == "heldout":
            resolved = path.resolve()
            if resolved.is_relative_to(REPO):
                raise PermissionError("held-out results never go inside the repository")
            priv = os.environ.get("EDAT_PRIVATE_DIR")
            if not priv or not resolved.is_relative_to(Path(priv).resolve()):
                raise PermissionError("held-out results go under $EDAT_PRIVATE_DIR/results")

    def append(self, result, **kw) -> dict:
        self._guard(result.case.get("split", "dev"), self.root)
        run = self._run(_tier(result.case["family"]), suite_for(result.loop))
        raw = run.put_raw(result.trajectory())
        rec = trial_record(result, run_id=run.run_id, raw_ref=raw, **kw)
        run.append(rec)
        return rec

    def close(self) -> None:
        for r in self.runs.values():
            r.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
