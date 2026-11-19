"""Run web retrieval trials through the reference loop and write records (spec 15 §3, §5).

    from edat.web.runner import run_trial
    r = run_trial(case, client, trial=1)            # synthetic case: the site is generated
    r.verdict.passed, r.verdict.metrics, r.events

Per trial: build the snapshot the trial reads (the synthetic site for its
world seed, plus the case's overlay pages; or a captured snapshot), build the
`edat-web-v1` environment, run one user turn of the reference loop
(`max_steps` 8, `max_calls` 8), score it, and turn violations into
`edat-restraint-event/v1` records (spec "15").

Records are `edat.result/1.0` with tier `web` (requested from the results
store; until it is registered there, `RunWriter` refuses them and
`write_run` says so), suite `reference-loop` or `harbor-loop`.

Hosted cloud references: development split only, no provider web search
(model ids ending `:online` are refused; a response with `url_citation`
annotations is excluded with `status_detail: provider_web_search`).

    python -m edat.web.runner oracle [--set core]   # offline: the scripted oracle over the dev split
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from edat.faults.approver import Approver
from edat.grading.aggregate import restraint_event
from edat.loop import LoopSettings, TurnTrace, get_loop
from edat.models.base import ModelClient, Sampling
from edat.web import site as S
from edat.web.cases import TRIALS, core_ids, load_dev, overlay_pages
from edat.web.score import WebVerdict, score_trial
from edat.web.snapshot import Snapshot, url_key
from edat.web.tools import BUDGET_CHARS, TOOLSET, WINDOW_CHARS, DocCorpus, WebEnvironment

TIER = "web"
SPEC = "15"
SAMPLING_SEED_BASE = 1000
SETTINGS = dict(max_steps=8, max_calls=8, max_tokens=1024)
RAG_CONTEXT_TOP = 8


class HostedSplitError(PermissionError):
    pass


class HostedSearchError(PermissionError):
    pass


@dataclass
class Prepared:
    case: dict
    trial: int
    binding: dict
    snapshot: Snapshot
    env: WebEnvironment
    messages: list[dict]
    context_ids: list[str] = field(default_factory=list)
    docs: DocCorpus | None = None

    @property
    def run_key(self) -> dict:
        web = getattr(self.env, "web", self.env)
        return {"toolset": TOOLSET, "lane": self.env.lane, "snapshot_id": self.snapshot.id,
                "window_chars": web.window_chars, "budget_chars": web.budget_chars,
                "docs_hash": self.docs.hash if self.docs is not None else None}


@dataclass
class WebTrialResult:
    case: dict
    trial: int
    prepared: Prepared
    trace: TurnTrace
    verdict: WebVerdict
    loop: str
    settings: LoopSettings
    events: list[dict] = field(default_factory=list)


_BASE: dict[int, Snapshot] = {}


def synthetic_snapshot(world_seed: int, overlay=()) -> Snapshot:
    if not overlay and world_seed in _BASE:
        return _BASE[world_seed]
    site = S.generate(world_seed)
    pages = {p.key: p for p in site.pages}
    for p in overlay:
        pages[p.key] = p
    snap = Snapshot.build(pages.values(), today=S.TODAY, source="synthetic", converter=S.CONVERTER,
                          corpora={S.WEB_CORPUS: list(pages)},
                          meta={"generator": S.GENERATOR, "world_seed": world_seed})
    if not overlay:
        _BASE[world_seed] = snap
    return snap


ASSISTANT_FAMILIES = {"need_search", "no_search", "read", "not_found", "multi_hop", "freshness", "conflict",
                      "injection"}   # RAG needs the docs tools, which Harbor does not have


def prepare(case: dict, trial: int, *, captured: dict[str, Snapshot] | None = None,
            lane: str = "neutral") -> Prepared:
    b = case["trials"][str(trial)]
    fam = case["family"]
    docs = None
    if case["source"] == "captured":
        snap = _captured_for(case, captured)
        corpus = case["corpus"]
    else:
        site = S.generate(b["world_seed"])
        snap = synthetic_snapshot(b["world_seed"], overlay_pages(case, b, site))
        if fam.startswith("rag"):
            org = b["rag"]["org"]
            docs = DocCorpus(org, site.docs[org])
        corpus = case["corpus"] or S.WEB_CORPUS
    if lane == "assistant":
        if fam not in ASSISTANT_FAMILIES:
            raise ValueError(f"{case['id']}: family {fam} has no assistant-lane form")
        from edat.web.harbor_bridge import AssistantWebEnvironment
        env = AssistantWebEnvironment(snap, corpus)
        system = env.system_prompt(offered=case["menu"])
    else:
        env = WebEnvironment(snap, corpus, docs=docs)
        system = env.system_prompt()
    user = b["question"]
    ctx_ids: list[str] = []
    if fam == "rag_context":
        block, ctx_ids = docs.context_block(b["question"], top=RAG_CONTEXT_TOP, cap_chars=BUDGET_CHARS)
        user = f"Documents:\n\n{block}\nQuestion: {b['question']}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return Prepared(case, trial, b, snap, env, messages, ctx_ids, docs)


def _captured_for(case: dict, captured: dict[str, Snapshot] | None) -> Snapshot:
    for snap in (captured or {}).values():
        if case["corpus"] in snap.corpora:
            return snap
    raise LookupError(f"no captured snapshot holds corpus {case['corpus']!r}; run the capture first "
                      "(edat/web/README.md)")


def hosted_guard(case: dict, client: ModelClient) -> None:
    desc = {}
    try:
        desc = client.describe() or {}
    except Exception:  # noqa: BLE001 — a stub may not describe itself
        desc = {}
    if desc.get("kind") == "cloud_reference" or desc.get("runtime") == "openrouter" or \
            type(client).__name__ == "OpenRouterClient":
        if case.get("split") != "dev":
            raise HostedSplitError(f"{case['id']}: hosted models see the development split only")
        model = str(getattr(client, "model", "") or desc.get("model") or "")
        if model.endswith(":online"):
            raise HostedSearchError(f"{model}: provider web search must be off (no :online models)")


def provider_searched(trace: TurnTrace) -> bool:
    """True when a hosted response carries url_citation annotations (its own web search)."""
    blob = json.dumps([s.raw for s in trace.steps], default=str)
    return "url_citation" in blob


def page_texts(p: Prepared) -> dict[str, str]:
    return {k: pg.text for k, pg in p.snapshot.pages.items()}


def chunk_texts(p: Prepared) -> dict[str, str]:
    return {c.id: c.text for c in p.docs.chunks} if p.docs else {}


def run_trial(case: dict, client: ModelClient, *, trial: int = 1, loop: str = "reference",
              settings: LoopSettings | None = None, captured: dict[str, Snapshot] | None = None,
              model_key: str = "model", board_version: str = "dev", lane: str = "neutral") -> WebTrialResult:
    hosted_guard(case, client)
    p = prepare(case, trial, captured=captured, lane=lane)
    s = settings or LoopSettings(**SETTINGS)
    if s.seed is None:
        s = LoopSettings(**{**s.__dict__, "seed": SAMPLING_SEED_BASE + trial})
    lp = get_loop(loop)
    trace = lp.run_turn(client, p.env, list(p.messages), menu=case["menu"], settings=s, approver=Approver())
    excluded = trace.excluded or provider_searched(trace)
    verdict = score_trial(case, p.binding, final_text=trace.final_text, calls=trace.calls, log=p.env.log,
                          page_text=page_texts(p), chunk_text=chunk_texts(p), context_ids=p.context_ids,
                          lane=getattr(p.env, "lane", "neutral"), steps=trace.steps, excluded=excluded)
    if provider_searched(trace):
        verdict.labels.append("provider_web_search")
    events = [restraint_event(SPEC, case["id"], trial, model_key, board_version, k, d) for k, d in verdict.events]
    return WebTrialResult(case, trial, p, trace, verdict, getattr(lp, "name", loop), s, events)


# ------------------------------------------------------------------ records
SUITES = {"edat-reference-v1": "reference-loop", "harbor-ask-v1": "harbor-loop"}


def trial_record(r: WebTrialResult, *, run_id: str, board_version: str, harness_commit: str, model: dict,
                 runtime: dict, device: dict | None, kind: str = "local", started_at: str,
                 raw_ref: dict | None = None, seed_supported: bool = True) -> dict:
    from edat.grading.records import SAMPLING_SOURCE
    v, c, s = r.verdict, r.case, r.settings
    sampling = s.sampling.to_record() if s.sampling else {}
    status = "excluded" if v.excluded else "ok"
    m = {k: val for k, val in v.metrics.items() if not isinstance(val, (list, dict))}
    m.update({"snapshot_id": r.prepared.snapshot.id[:16], "toolset": TOOLSET, "window_chars": WINDOW_CHARS,
              "budget_chars": BUDGET_CHARS})
    rec = {
        "schema": "edat.result/1.0", "kind": kind, "tier": TIER, "suite": SUITES.get(r.loop, r.loop[:64]),
        "run_id": run_id, "board_version": board_version, "lang": "en", "model": model,
        "runtime": {"version": "unspecified", **runtime}, "device": device,
        "settings": {"thinking": "on" if s.thinking else "off",
                     "sampling": {("repeat_penalty" if k == "repetition_penalty" else k): val
                                  for k, val in sampling.items() if k != "source"},
                     "sampling_source": SAMPLING_SOURCE.get(sampling.get("source") or "unspecified", "engine_default"),
                     "seed": s.seed if seed_supported else "unsupported",
                     "world_seed": r.prepared.binding.get("world_seed")},
        "harness_commit": harness_commit, "started_at": started_at, "case_id": c["id"], "split": c["split"],
        "trial": r.trial, "status": status,
        "facets": {"lane": r.prepared.env.lane, "family": c["family"], "level": int(c.get("level") or 0),
                   "source_split": c["source"], "tags": [t for t in c.get("tags") or []][:32]},
        "metrics": m,
        "safety": {"unsafe_actions": sum(1 for k, _ in v.events if k != "leak"),
                   "privacy_leaks": sum(1 for k, _ in v.events if k == "leak"),
                   "labels": sorted({k for k, _ in v.events})},
        "raw": raw_ref,
    }
    if v.excluded and "provider_web_search" in v.labels:
        rec["status_detail"] = "provider_web_search"
    if kind == "cloud_reference":
        rec["device"] = None
        rec["device_reason"] = "cloud"
    if raw_ref is None:
        rec["raw_reason"] = "not_kept"
    return rec


def write_run(results: list[WebTrialResult], store: Path, run_id: str, **record_kw) -> Path:
    """Write records through the results store's RunWriter (tier `web` must be registered)."""
    from edat.results.schema import load_schema
    from edat.results.writer import RunWriter
    tiers = load_schema("record")["properties"]["tier"]["enum"]
    if TIER not in tiers:
        raise RuntimeError(f"the results store does not list tier {TIER!r} yet (known: {tiers}); "
                           "spec 15 asks for it to be registered")
    suite = SUITES.get(results[0].loop, results[0].loop) if results else "reference-loop"
    with RunWriter.open(store, run_id, tier=TIER, suite=suite) as run:
        for r in results:
            raw = run.put_raw({"trace": r.trace.to_record(), "env_log": r.prepared.env.log,
                               "events": r.events, "run_key": r.prepared.run_key})
            run.append(trial_record(r, run_id=run_id, raw_ref=raw, **record_kw))
        return run.dir


# ------------------------------------------------------------------ suites
def select_cases(cases: list[dict], *, set_name: str = "core", families=None) -> list[dict]:
    core = core_ids(cases) if set_name == "core" else None
    return [c for c in cases if (core is None or c["id"] in core) and (not families or c["family"] in families)]


def run_suite(cases: list[dict], client_for, *, trials: int = TRIALS, **kw) -> list[WebTrialResult]:
    """`client_for(case, trial)` returns the client for one trial (a fresh ScriptedClient
    for the oracle; the same model client for a real run)."""
    out = []
    for case in cases:
        for t in range(1, trials + 1):
            out.append(run_trial(case, client_for(case, t), trial=t, **kw))
    return out


def summary(results: list[WebTrialResult]) -> dict:
    from collections import defaultdict
    fam: dict[str, list[bool]] = defaultdict(list)
    events = 0
    for r in results:
        if not r.verdict.excluded:
            fam[r.case["family"]].append(bool(r.verdict.passed))
        events += bool(r.events)
    rates = {f: sum(v) / len(v) for f, v in sorted(fam.items())}
    headline = [rates[f] for f in rates if f != "injection"]
    return {"trials": len(results), "family_pass": rates,
            "web_retrieval_score": (sum(headline) / len(headline)) if headline else None,
            "trials_with_events": events}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.web.runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("oracle", help="offline: run the scripted oracle over the development split")
    o.add_argument("--set", default="core", choices=["core", "full"])
    a = ap.parse_args(argv)
    if a.cmd == "oracle":
        from edat.web.oracle import oracle_client
        cases = select_cases(load_dev(), set_name=a.set)
        res = run_suite([c for c in cases if c["source"] == "synthetic"], oracle_client)
        print(json.dumps(summary(res), indent=1))
        return 0 if all(r.verdict.passed for r in res) else 1
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
