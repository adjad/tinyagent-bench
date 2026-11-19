"""Run a model over EDAT's quality cases and write one record per answer.

The model is reached only through the shared model adapter's interface
(docs/benchmark/model-adapter.md, `edat/models/`):

    client.chat(messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking)
        -> result with .text, .reasoning, .finish_reason, .usage, .timing, .request, .meta

Any object with that method works, so tests use a scripted stub and no model
is ever loaded here. Transport and engine faults raise an exception whose class
is named `EngineError` (the adapter's); the trial is retried once, then
recorded as `excluded`.

Rules applied (spec 03 s1, s1.5, s6.1, s7.4):
- 3 seeds per case in every routine run (`DEFAULT_SEEDS`); a seedless cloud
  reference runs 3 independent trials, labelled `trials, not seeds`.
- One fixed output cap for every local model (4,096 visible + reasoning);
  16,384 for cloud references. Answers cut off at the cap are counted.
- Thinking-on and thinking-off are separate entries.
- A source that does not fit the model's window with 1,024 tokens left for the
  answer is recorded as `beyond_window` (`does_not_fit`), never scored as 0.
- Cloud references see the development split only.
"""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from edat.quality import hygiene as H
from edat.quality import records as R
from edat.quality.generate import iter_tasks
from edat.quality.render import RENDER_VERSION, SYSTEM_PROMPT_VERSION, build_messages, render_sources
from edat.quality.scorers import SCORERS_VERSION, score_task
from edat.quality.textutil import visible_answer, word_count
from edat.quality.tokens import approx_tokens, count_tokens, thinking_overhead

RUNNER_VERSION = "edat-quality-runner/1"
DEFAULT_SEEDS = (1001, 1002, 1003)
LOCAL_MAX_TOKENS = 4096
CLOUD_MAX_TOKENS = 16384
ANSWER_RESERVE = 1024
BASELINE_ID = "Gemma-Wisp"
BASE_ID = "gemma-4-E4B-it-oQ4e-mtp"


class HostedSplitError(RuntimeError):
    pass


@dataclass
class Entry:
    """What is being run: one model in one thinking mode at one sampling class."""

    model_id: str
    role: str = "candidate"                     # baseline | reference | candidate | variant | cloud_reference
    kind: str = "local"                         # local | cloud_reference
    thinking: bool = False
    sampling: Any = None                        # the adapter's Sampling, or None
    sampling_source: str = "author_recommended"
    seeds: tuple = DEFAULT_SEEDS
    seedless: bool = False                      # provider takes no seed: 3 trials, not seeds
    runtime: dict = field(default_factory=lambda: {"name": "omlx", "version": None})
    device: dict | None = None
    weights_sha256: str | None = None
    weights_fingerprint: str | None = None      # one of the two is required for a local record
    quant: dict | None = None
    upstream_provider: str | None = None        # the pinned provider of a cloud reference
    context_window: int | None = None           # tokens; None = unknown (no fit check)
    count_model_tokens: Callable[[str], int] | None = None
    max_tokens: int | None = None

    def __post_init__(self):
        if self.kind == "cloud_reference":
            self.role = "cloud_reference"

    @property
    def thinking_label(self) -> str:
        return "on" if self.thinking else "off"

    def model_block(self) -> dict:
        if self.kind == "cloud_reference":
            return {"id": self.model_id, "role": "cloud_reference", "requested_model": self.model_id,
                    "weights_sha256": None, "quant": None}
        out = {"id": self.model_id, "role": self.role, "weights_sha256": self.weights_sha256, "quant": self.quant}
        if self.weights_fingerprint:
            out["weights_fingerprint"] = self.weights_fingerprint
        return out

    def sampling_record(self) -> dict:
        if self.sampling is None:
            return {}
        rec = self.sampling.to_record() if hasattr(self.sampling, "to_record") else dict(self.sampling)
        rec.pop("source", None)
        if "repetition_penalty" in rec:
            rec["repeat_penalty"] = rec.pop("repetition_penalty")
        allowed = {"temperature", "top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty",
                   "frequency_penalty"}
        out = {k: v for k, v in rec.items() if k in allowed}
        out["max_output_tokens"] = self.cap()
        return out

    def cap(self) -> int:
        return self.max_tokens or (CLOUD_MAX_TOKENS if self.kind == "cloud_reference" else LOCAL_MAX_TOKENS)

    def as_record_entry(self) -> dict:
        def settings(trial: int) -> dict:
            seed = "unsupported" if self.seedless else self.seeds[trial - 1]
            return {"thinking": self.thinking_label, "sampling": self.sampling_record(),
                    "sampling_source": self.sampling_source, "seed": seed}
        return {"kind": self.kind, "model": self.model_block(), "runtime": self.runtime, "device": self.device,
                "settings_for_trial": settings}


def _usage_dict(u) -> dict:
    if u is None:
        return {}
    if isinstance(u, dict):
        return u
    return {k: getattr(u, k, None) for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cost_usd")}


def _is_engine_error(e: Exception) -> bool:
    return type(e).__name__ == "EngineError"


def hygiene_case(case: dict, task: dict, source_text: str) -> dict:
    ref = task.get("reference_answer")
    return {"qtype": task.get("qtype", "summary"), "profile": task.get("profile", "panel"),
            "request": task["request"],
            # answer-first (D4) needs one key that can lead; ordered steps and summaries have none
            "answer_key": [] if task.get("qtype") in ("steps", "summary") else (task.get("answer_key") or []),
            "reference_words": word_count(ref) if ref else None, "format_constraints": task.get("format_constraints") or [],
            "source_text": source_text, "case_year": case.get("case_year")}


def variants(case: dict, *, robust: bool = True, fenced: bool = True) -> list[tuple[str, str]]:
    out = []
    for r in case.get("renderings", ["primary"]):
        if r == "robust" and not robust:
            continue
        for c in case.get("conditions", ["unfenced"]):
            if c == "fenced" and not fenced:
                continue
            out.append((r, c))
    return out


def record_case_id(task_id: str, rendering: str, condition: str) -> str:
    return f"{task_id}@{rendering}.{condition}"


def run_answer(client, entry: Entry, case: dict, task: dict, *, rendering: str, condition: str, trial: int,
               request: str | None = None) -> dict:
    """Ask once (retry once on an engine fault) and score. Returns a result dict."""
    messages = build_messages(case, task, rendering=rendering, condition=condition, request=request)
    prompt_tokens = sum(approx_tokens(m["content"]) for m in messages)
    if entry.context_window and prompt_tokens + ANSWER_RESERVE > entry.context_window:
        return {"status": "beyond_window", "status_detail": "does_not_fit", "prompt_tokens_est": prompt_tokens}
    seed = None if entry.seedless else entry.seeds[trial - 1]
    err = None
    result = None
    for attempt in range(2):
        try:
            result = client.chat(messages, tools=None, sampling=entry.sampling, seed=seed, max_tokens=entry.cap(),
                                 thinking=entry.thinking)
            err = None
            break
        except Exception as e:  # noqa: BLE001
            if not _is_engine_error(e):
                raise
            err = e
    if result is None:
        return {"status": "excluded", "status_detail": f"engine_{getattr(err, 'kind', 'error')}"[:64],
                "error": str(err)[:200]}
    raw_text = result.text or ""
    visible, reasoning = visible_answer(raw_text, getattr(result, "reasoning", "") or "")
    src = render_sources(case, rendering)
    req = request if request is not None else task["request"]
    scores = score_task(visible, case, task, source_text=src, request=req)
    hyg = None
    if task["kind"] != "extraction":
        hyg = H.hygiene(visible, hygiene_case(case, task, src))
    usage = _usage_dict(getattr(result, "usage", None))
    tok = count_tokens(visible, reasoning, count_model_tokens=entry.count_model_tokens, usage=usage,
                       provider_usage=entry.kind == "cloud_reference")
    finish = getattr(result, "finish_reason", None)
    timing = getattr(result, "timing", None)
    return {"status": "ok", "visible": visible, "raw_text": raw_text, "reasoning": reasoning, "scores": scores,
            "hygiene": hyg, "tokens": tok, "finish_reason": finish, "usage": usage, "messages": messages,
            "request_meta": getattr(result, "request", {}) or {}, "meta": getattr(result, "meta", {}) or {},
            "timing": {"ttft_s": getattr(timing, "ttft_s", None), "total_s": getattr(timing, "total_s", None)}
            if timing is not None else {}}


def _facets(case: dict, task: dict, rendering: str, condition: str, noise_kind: str | None = None) -> dict:
    fam = {"open_summary": "summary", "focused_summary": "summary", "point": "point", "extraction": "extraction",
           "numeric": "numeric"}[task["kind"]]
    if case.get("family") == "schema":
        fam = "schema"
    if noise_kind:
        fam = "noisy"
    tags = [R.tag(f"tier_{case['tier']}"), R.tag(f"render_{rendering}"), R.tag(condition), R.tag(f"kind_{task['kind']}")]
    if noise_kind:
        tags.append(R.tag(f"noise_{noise_kind}"))
    if case.get("family") == "numeric":
        tags.append("numeric_case")
    return {"family": fam, "category": case.get("source_type"), "profile": task.get("profile", "panel"), "tags": tags}


def _metrics(res: dict, case: dict, task: dict) -> tuple[dict, dict]:
    flat, details = R.flat_metrics(res["scores"])
    if res.get("hygiene"):
        flat["hygiene"] = res["hygiene"]["score"]
        flat["leak"] = res["hygiene"]["leak"]
        for cid, c in res["hygiene"]["checks"].items():
            flat[f"{cid.lower()}_pass"] = None if c["status"] == "n/a" else c["status"] == "pass"
        details["hygiene"] = res["hygiene"]
    tm = res["tokens"].to_metrics()
    flat.update({k: v for k, v in tm.items() if not isinstance(v, (dict, list))})
    flat["thinking_overhead"] = thinking_overhead(tm["reasoning_tokens"], tm["visible_tokens"])
    flat["truncated"] = res.get("finish_reason") == "length"
    flat["source_ref_tokens_est"] = approx_tokens(render_sources(case))
    return flat, details


def run_quality(client, entry: Entry, fams: dict[str, list[dict]], *, split: str, board_version: str,
                harness_commit: str, run_id: str | None = None, sink: R.QualityRunSink | None = None,
                families: Iterable[str] = ("source", "numeric", "schema", "noisy"), robust: bool = True,
                fenced: bool = True, limit: int | None = None) -> R.QualityRunSink:
    """Run every task of the given case families for one entry, 3 trials each."""
    if entry.kind == "cloud_reference" and split != "dev":
        raise HostedSplitError("cloud references run on the development split only")
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ") + "-" + secrets.token_hex(2)
    sink = sink or R.QualityRunSink(run_id=run_id, split=split)
    rec_entry = entry.as_record_entry()
    trials = range(1, (3 if entry.seedless else len(entry.seeds)) + 1)
    by_task = {}
    for fam in ("source", "numeric", "schema"):
        for c in fams.get(fam, []):
            for t in iter_tasks(c):
                by_task[t["task_id"]] = (c, t)
    jobs: list[tuple[dict, dict, str, str, str | None, str | None]] = []
    for fam in families:
        if fam == "noisy":
            for n in fams.get("noisy", []):
                if n["parent_task_id"] in by_task:
                    c, t = by_task[n["parent_task_id"]]
                    jobs.append((c, t, "primary", "unfenced", n["request"], n))
            continue
        for c in fams.get(fam, []):
            for t in iter_tasks(c):
                for rendering, condition in variants(c, robust=robust, fenced=fenced):
                    jobs.append((c, t, rendering, condition, None, None))
    if limit is not None:
        jobs = jobs[:limit]
    cloud_seen: list[dict] = []
    for c, t, rendering, condition, req, noisy in jobs:
        for trial in trials:
            started = R.utc_now()
            res = run_answer(client, entry, c, t, rendering=rendering, condition=condition, trial=trial, request=req)
            cid = noisy["case_id"] + "/" + t["kind"] if noisy else record_case_id(t["task_id"], rendering, condition)
            facets = _facets(c, t, rendering, condition, noisy["noise_kind"] if noisy else None)
            if res["status"] != "ok":
                sink.add(R.make_record(run_id=run_id, board_version=board_version, harness_commit=harness_commit,
                                       entry=rec_entry, case_id=cid, split=split, trial=trial, status=res["status"],
                                       status_detail=res.get("status_detail"), metrics={}, facets=facets, raw=None,
                                       raw_reason=res["status"], safety={"unsafe_actions": None, "privacy_leaks": None,
                                                                         "labels": []}, started_at=started))
                continue
            flat, details = _metrics(res, c, t)
            if noisy:
                flat["noise_kind"] = noisy["noise_kind"]
            inj = res["scores"].get("injection_followed") or 0
            labels = ["injection_followed"] if inj else []
            raw = sink.add_raw({"schema": "edat.quality.raw/1", "case_id": c["case_id"], "task_id": t["task_id"],
                                "rendering": rendering, "condition": condition, "request": req or t["request"],
                                "text": res["raw_text"], "visible": res["visible"], "reasoning": res["reasoning"],
                                "finish_reason": res["finish_reason"], "usage": res["usage"], "timing": res["timing"],
                                "adapter_request": res["request_meta"], "adapter_meta": res["meta"],
                                "details": details, "versions": {"runner": RUNNER_VERSION, "scorers": SCORERS_VERSION,
                                                                 "render": RENDER_VERSION,
                                                                 "system_prompt": SYSTEM_PROMPT_VERSION,
                                                                 "hygiene": getattr(H, "HYGIENE_VERSION", None)}})
            sink.add(R.make_record(run_id=run_id, board_version=board_version, harness_commit=harness_commit,
                                   entry=rec_entry, case_id=cid, split=split, trial=trial, status="ok",
                                   metrics=flat, facets=facets, raw=raw, started_at=started,
                                   status_detail="trials_not_seeds" if entry.seedless else None,
                                   safety={"unsafe_actions": inj, "privacy_leaks": None, "labels": labels}))
            if entry.kind == "cloud_reference":
                cloud_seen.append(res["meta"])
    if entry.kind == "cloud_reference":
        _finish_cloud_model_blocks(sink.records, entry, cloud_seen)
    return sink


def _finish_cloud_model_blocks(records: list[dict], entry: Entry, metas: list[dict]) -> None:
    """A cloud reference's model block names what was asked for, what answered, by whom, and when."""
    resolved = next((m.get("resolved_model") for m in metas if m.get("resolved_model")), None) or entry.model_id
    upstream = (next((m.get("upstream_provider") for m in metas if m.get("upstream_provider")), None)
                or entry.upstream_provider or "unknown")
    days = sorted(r["started_at"][:10] for r in records if r.get("started_at"))
    run_date = {"first": days[0], "last": days[-1]} if days else None
    for r in records:
        r["model"] = dict(r["model"], resolved_model=resolved, upstream_provider=upstream, run_date=run_date)
