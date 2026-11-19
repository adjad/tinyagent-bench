"""Run one model entry through the sidekick tier (spec 12 §3.3, §3.5, §4).

    python -m edat.sidekick run --model stub:answers.json --split dev --set core
    python -m edat.sidekick run --model omlx:Gemma-Wisp --base-url http://127.0.0.1:<port> ...

The runner:
1. runs the sandbox self-test and refuses to start unless it passes;
2. builds the selected cases (split, core or full set, types, modes);
3. runs 3 trials per case (each with its own seed) in answer-only mode, and
   in tool mode for the tool-mode cases, one trial at a time;
4. grades every trial, writes one results-store record per trial, the
   `edat-restraint-event/v1` events and the trial count, and a summary with
   SK-1 to SK-27.

Models come through the shared adapter (`edat/models/`): any client with
`chat(messages, *, tools, sampling, seed, max_tokens, thinking)`. The `stub:`
and `null` clients here follow that contract; real clients load from
`edat.models` only when it is installed.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from edat.sidekick import GENERATOR_VERSION
from edat.sidekick.extract import visible_answer
from edat.sidekick.gen.core import PUBLIC_MASTER_SEED, Case, Instance, build_cases, materialize
from edat.sidekick.graders import grade_answer
from edat.sidekick.loop_glue import MiniSettings, StubClient, is_engine_error
from edat.sidekick.prescreen import rules_version
from edat.sidekick.records import LocalRunWriter, harness_commit, make_record, new_run_id, results_root, utc_now
from edat.sidekick.report import summarize
from edat.sidekick.restraint import event_record, trials_record
from edat.sidekick.sandbox.denials import read_denials
from edat.sidekick.sandbox.run import ExecResult, Isolation, TrialArea, get_isolation
from edat.sidekick.sandbox.selftest import require_sandbox
from edat.sidekick.toolchain import find_toolchain
from edat.sidekick.tools_dev import MAX_CALLS, SYSTEM_PROMPT, TOOLSET

TRIALS = 3
ANSWER_MAX_TOKENS = 1024


class RecordingIsolation:
    """Wraps an isolation and keeps every ExecResult of the current trial (for denial reading)."""

    def __init__(self, inner: Isolation):
        self.inner = inner
        self.name = inner.name
        self.results: list[ExecResult] = []

    def check(self) -> None:
        self.inner.check()

    def describe(self) -> dict:
        return self.inner.describe()

    def run(self, area, script, **kw) -> ExecResult:
        r = self.inner.run(area, script, **kw)
        self.results.append(r)
        return r

    def take(self) -> list[ExecResult]:
        out, self.results = self.results, []
        return out


def master_seed(split: str) -> str:
    if split == "dev":
        return PUBLIC_MASTER_SEED
    priv = os.environ.get("EDAT_PRIVATE_DIR")
    if not priv:
        raise RuntimeError("the held-out split needs EDAT_PRIVATE_DIR")
    p = Path(priv) / "sidekick" / "master_seed"
    if not p.exists():
        raise RuntimeError(f"no private master seed at $EDAT_PRIVATE_DIR/sidekick/master_seed (run export-heldout)")
    return p.read_text().strip()


def trial_seed(case_id: str, trial: int, split: str) -> int:
    import hashlib
    return int(hashlib.sha256(f"{split}|{case_id}|{trial}".encode()).hexdigest()[:8], 16)


@dataclass
class Entry:
    model: dict
    thinking: str = "off"
    kind: str = "local"
    runtime: dict = field(default_factory=lambda: {"name": "stub", "version": "0"})
    device: dict | None = None
    sampling: dict | None = None
    sampling_source: str = "author_recommended"

    @property
    def key(self) -> str:
        return f"{self.model['id']}|thinking-{self.thinking}"

    def to_dict(self) -> dict:
        return {"model": self.model, "thinking": self.thinking, "kind": self.kind, "runtime": self.runtime,
                "device": self.device, "sampling": self.sampling, "sampling_source": self.sampling_source}


def _chat_with_retry(client, messages, *, tools, seed, max_tokens, thinking, sampling):
    last = None
    for _ in range(2):
        try:
            return client.chat(messages, tools=tools, sampling=sampling, seed=seed, max_tokens=max_tokens,
                               thinking=thinking), None
        except Exception as exc:  # noqa: BLE001
            if not is_engine_error(exc):
                raise
            last = f"{exc.kind}: {getattr(exc, 'detail', '')}"
            if not getattr(exc, "retryable", True):
                break
    return None, last


def _visible_tokens(usage: dict) -> int | None:
    c = usage.get("completion_tokens")
    if c is None:
        return None
    return c - (usage.get("reasoning_tokens") or 0)


def run_answer_trial(client, inst: Instance, *, seed: int, thinking: bool, sampling, iso) -> tuple[dict, dict, list, str, str | None]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": inst.prompt}]
    t0 = time.monotonic()
    res, err = _chat_with_retry(client, messages, tools=None, seed=seed, max_tokens=ANSWER_MAX_TOKENS,
                                thinking=thinking, sampling=sampling)
    model_s = time.monotonic() - t0
    if res is None:
        return {}, {"error": err}, [], "excluded", f"engine_error: {err}"
    g = grade_answer(inst, res.text or "", iso)
    usage = res.usage.to_record() if hasattr(res.usage, "to_record") else dict(res.usage or {})
    metrics = {"pass": g.passed, **{k: v for k, v in g.metrics.items() if isinstance(v, (bool, int, float, str)) or v is None}}
    metrics["time_to_answer_s"] = round(model_s, 4)
    metrics["visible_tokens"] = _visible_tokens(usage)
    metrics["reasoning_tokens"] = usage.get("reasoning_tokens")
    raw = {"answer": res.text, "reasoning": getattr(res, "reasoning", ""), "usage": usage,
           "timing": res.timing.to_record() if hasattr(res.timing, "to_record") else None,
           "grade": g.to_record(), "visible_answer": visible_answer(res.text)}
    return metrics, raw, g.events, "ok", None


def run_tool_trial_full(client, inst: Instance, *, seed: int, thinking: bool, sampling, iso) -> tuple[dict, dict, list, str, str | None]:
    from edat.sidekick.tool_mode import grade_tool_trial, prepare, run_tool_trial
    with TrialArea() as area:
        env, before = prepare(iso, area, inst)
        s = MiniSettings(max_steps=MAX_CALLS + 2, max_tokens=ANSWER_MAX_TOKENS, thinking=thinking, seed=seed,
                         sampling=sampling)
        tt = run_tool_trial(client, inst, env, settings=s)
        if tt.excluded:
            return {}, {"error": tt.engine_error}, [], "excluded", f"engine_error: {tt.engine_error}"
        g = grade_tool_trial(iso, area, inst, env, before, tt)
    metrics = {"pass": g.passed, **{k: v for k, v in g.metrics.items() if isinstance(v, (bool, int, float, str)) or v is None}}
    metrics["time_to_answer_s"] = round(tt.wall_s, 4)
    raw = {"final_text": tt.final_text, "turns": [t.__dict__ for t in tt.turns], "grade": g.to_record(),
           "commands": [c.__dict__ for c in env.commands], "loop": tt.turns[0].loop if tt.turns else None}
    return metrics, raw, g.events, "ok", None


@dataclass
class RunConfig:
    split: str = "dev"
    set: str = "core"                 # core | full
    modes: tuple[str, ...] = ("answer_only", "tool_mode")
    types: tuple[str, ...] = ()
    trials: int = TRIALS
    board_version: str = "dev"
    read_denials: bool = False
    out_root: Path | None = None
    limit: int | None = None


def select_cases(cfg: RunConfig) -> list[Case]:
    cases = build_cases(cfg.split if cfg.split != "all" else None)
    if cfg.set == "core":
        cases = [c for c in cases if c.core]
    if cfg.types:
        cases = [c for c in cases if c.type in cfg.types]
    if cfg.limit:
        cases = cases[:cfg.limit]
    return cases


def run(client, entry: Entry, cfg: RunConfig, *, iso: Isolation | None = None, progress=print) -> dict:
    if entry.kind == "cloud_reference" and cfg.split != "dev":
        raise RuntimeError("cloud references run on the development split only")
    selftest = require_sandbox()                       # refuses unless the sandbox is proven
    rec_iso = RecordingIsolation(iso or get_isolation())
    cases = select_cases(cfg)
    run_id = new_run_id()
    commit, dirty = harness_commit()
    tc = find_toolchain()
    run_key = {"toolchain": tc.run_key(), "sandbox": rec_iso.describe(), "prescreen": rules_version(),
               "generator": GENERATOR_VERSION, "toolset": TOOLSET, "selftest_passed": selftest.passed,
               "split": cfg.split, "set": cfg.set, "trials": cfg.trials}
    denial_log = None
    if cfg.read_denials:
        from edat.sidekick.sandbox.denials import calibrate
        denial_log = calibrate(rec_iso.inner, TrialArea)
        run_key["denial_log"] = "available" if denial_log else "unavailable (profile denials not in the unified log)"
    root = cfg.out_root or results_root(cfg.split)
    sampling_class = "fixed_sensitivity" if entry.sampling_source == "fixed_sensitivity" else "recommended"
    # One run directory per mode: answer-only and tool mode are separate entries (spec §3.5),
    # and the store keys a trial by entry, device, case and trial number.
    writers = {mode: LocalRunWriter(root, f"{run_id}-{'ao' if mode == 'answer_only' else 'tm'}",
                                    board_version=cfg.board_version, kind=entry.kind, commit=commit,
                                    sampling_class=sampling_class, run_key=dict(run_key, mode=mode), suite=mode)
               for mode in cfg.modes}
    thinking = entry.thinking == "on"
    by_mode = {m: 0 for m in cfg.modes}
    events_out: dict[str, list[dict]] = {m: [] for m in cfg.modes}
    records: list[dict] = []
    for case in cases:
        for mode in cfg.modes:
            if mode == "tool_mode" and not case.tool_mode:
                continue
            writer = writers[mode]
            for trial in range(1, cfg.trials + 1):
                inst = materialize(case, trial, master_seed(case.split))
                seed = trial_seed(case.id, trial, case.split)
                started = utc_now()
                rec_iso.take()
                fn = run_answer_trial if mode == "answer_only" else run_tool_trial_full
                metrics, raw, events, status, detail = fn(client, inst, seed=seed, thinking=thinking,
                                                          sampling=entry.sampling, iso=rec_iso)
                execs = rec_iso.take()
                if denial_log is False and status == "ok":
                    metrics["sandbox_denials"] = None
                if denial_log and execs and status == "ok":
                    pids = set().union(*(e.pids for e in execs))
                    rep = read_denials(min(e.started for e in execs), max(e.ended for e in execs), pids)
                    metrics["sandbox_denials"] = len(rep.escapes) if rep.available else None
                    if rep.available:
                        from edat.sidekick.graders.common import Event
                        for d in rep.escapes:
                            events.append(Event("unsafe_action", "sandbox_denial", d.op + " " + d.target, executed=True))
                        if rep.escapes:
                            metrics["pass"] = False
                ev = [event_record(e, case_id=case.id, trial=trial, model_key=entry.key,
                                   board_version=cfg.board_version, mode=mode) for e in events]
                events_out[mode].extend(ev)
                by_mode[mode] += 1
                raw_ref = writer.put_raw(raw)
                rec = make_record(run_id=writer.run_id, board_version=cfg.board_version, entry=entry.to_dict(),
                                  case_meta=case.meta(), trial=trial, suite=mode, grade=None, status=status,
                                  status_detail=detail, metrics=metrics if status == "ok" else {},
                                  events=[{"kind": e["kind"]} for e in ev], started_at=started, seed=seed,
                                  raw_ref=raw_ref, commit=commit, dirty=dirty)
                writer.append(rec)
                records.append(rec)
                if progress:
                    progress(f"{case.id} {mode} t{trial}: {'PASS' if metrics.get('pass') else status if status != 'ok' else 'fail'}"
                             + (f" [{', '.join(sorted({e['kind'] for e in ev}))}]" if ev else ""))
    summary = summarize(records)
    for mode, writer in writers.items():
        writer.attach("restraint-events.jsonl", "".join(json.dumps(e, sort_keys=True) + "\n" for e in events_out[mode]),
                      "edat-restraint-event/v1")
        writer.attach("restraint-trials.jsonl", json.dumps(trials_record(model_key=entry.key, board_version=cfg.board_version,
                                                                         by_mode={mode: by_mode[mode]}), sort_keys=True) + "\n",
                      "edat-restraint-trials/v1")
        writer.attach("sidekick-summary.json", json.dumps(summary, indent=1, default=str), "sidekick_summary")
        writer.close()
    n_events = sum(len(v) for v in events_out.values())
    return {"run_dirs": {m: str(w.dir) for m, w in writers.items()}, "run_dir": str(writers[cfg.modes[0]].dir),
            "records": len(records), "events": n_events, "summary": summary}


# ------------------------------------------------------------------ clients

class ReferenceClient(StubClient):
    """Answers every case with its first reference answer (tests and dry runs only)."""

    def __init__(self, instances_by_prompt: dict[str, Instance]):
        super().__init__(self._turn, model_id="reference-oracle")
        self.by_prompt = instances_by_prompt
        self._tool_turns: dict[str, list] = {}

    def _turn(self, messages, tools, index):
        user = next(m["content"] for m in messages if m["role"] == "user")
        for prompt, inst in self.by_prompt.items():
            if user.startswith(prompt):
                if not tools:
                    return {"text": inst.references[0]}
                from edat.sidekick.tool_mode import _reference_turns
                script = self._tool_turns.setdefault(user, _reference_turns(inst))
                n_assist = sum(1 for m in messages if m["role"] == "assistant")
                return script[n_assist] if n_assist < len(script) else {"text": "Done."}
        return {"text": "OK."}


def client_from_spec(spec: str, base_url: str | None = None):
    if spec == "null":
        return StubClient([]), {"id": "null-model", "role": "candidate"}, {"name": "stub", "version": "0"}
    if spec.startswith("stub:"):
        turns = json.loads(Path(spec[5:]).read_text())
        return StubClient(turns), {"id": "stub-model", "role": "candidate"}, {"name": "stub", "version": "0"}
    kind, _, mid = spec.partition(":")
    try:
        import edat.models as M  # type: ignore
    except ImportError as exc:
        raise SystemExit("real model clients need edat/models (build/agentic); not on this branch") from exc
    if kind == "omlx":
        return M.OmlxClient(mid, base_url=base_url), {"id": mid, "role": "candidate"}, {"name": "omlx"}
    if kind == "openrouter":
        raise SystemExit("cloud references: build the OpenRouterClient with its pinned provider in code (edat/hosted/catalog.py)")
    raise SystemExit(f"unknown model spec {spec!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick run")
    ap.add_argument("--model", required=True, help="null | stub:<turns.json> | omlx:<engine id>")
    ap.add_argument("--base-url")
    ap.add_argument("--role", default="candidate")
    ap.add_argument("--thinking", choices=("on", "off"), default="off")
    ap.add_argument("--split", choices=("dev", "heldout"), default="dev")
    ap.add_argument("--set", choices=("core", "full"), default="core")
    ap.add_argument("--mode", choices=("answer_only", "tool_mode", "both"), default="both")
    ap.add_argument("--type", action="append", default=[])
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--board-version", default="dev")
    ap.add_argument("--read-denials", action="store_true", help="read Seatbelt denials from the unified log (SK-22)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    client, model, runtime = client_from_spec(a.model, a.base_url)
    model["role"] = a.role
    entry = Entry(model=model, thinking=a.thinking, runtime=runtime)
    modes = ("answer_only", "tool_mode") if a.mode == "both" else (a.mode,)
    cfg = RunConfig(split=a.split, set=a.set, modes=modes, types=tuple(a.type), trials=a.trials,
                    board_version=a.board_version, read_denials=a.read_denials,
                    out_root=Path(a.out) if a.out else None, limit=a.limit)
    out = run(client, entry, cfg)
    s = out["summary"]
    print(json.dumps({"run_dirs": out["run_dirs"], "records": out["records"], "restraint_events": out["events"],
                      "sidekick_score": s.get("SK-1_sidekick_score"), "tool_mode_score": s.get("SK-5_tool_mode_score"),
                      "terminal_violation_rate": s.get("SK-19_terminal_violation_rate")}, indent=1))
    return 0
