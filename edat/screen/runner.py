"""Run screen cases against a model through the shared adapter (spec 11 §3.10, §3.11).

Each case runs 3 trials (world seeds 1, 2, 3 — one per trial, as spec 02
§2.3), on the text track unless another track is asked for and the case lists
it. Each trial: bind the case to its world seed, run the screen loop, grade,
write one result record, its restraint events and a perf task sample.

    python -m edat.screen.runner --stub oracle --cases test_fixtures/screen/dev --out <store dir>
    python -m edat.screen.runner --stub null   --cases test_fixtures/screen/dev --family ground

Only stub clients are wired to the command line (oracle, null, random). A
real model is run from Python with a client from `edat.models` (`OmlxClient`,
`OpenRouterClient`) and an `Entry` describing it; that needs the user's
go-ahead, as every real run does. Held-out cases are refused for a
`cloud_reference` entry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from edat.screen.adapter import NullClient, PolicyClient
from edat.screen.cases.bind import Bound, bind
from edat.screen.cases.oracle import Oracle
from edat.screen.grade.grader import Verdict, grade
from edat.screen.loop import Episode, ScreenLoop, ScreenSettings
from edat.screen.records import (Entry, LocalRunWriter, perf_sample_for, record_for, restraint_events_for,
                                 utc_now)

TRIAL_SEEDS = (1, 2, 3)


@dataclass
class TrialResult:
    case: dict
    trial: int
    bound: Bound
    episode: Episode
    verdict: Verdict


@dataclass
class RunSummary:
    run_id: str
    trials: list[TrialResult] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    perf_samples: list[dict] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


def loop_for(track: str) -> ScreenLoop:
    if track == "ax_text":
        return ScreenLoop()
    if track in ("screenshot_som", "ax_plus_screenshot"):
        from edat.screen.render.som import SomObserver
        return ScreenLoop(SomObserver(with_text=(track == "ax_plus_screenshot")))
    raise ValueError(f"track {track!r} is not run by the screen loop")


def run_trial(case: dict, client, trial: int, *, track: str = "ax_text", settings: ScreenSettings | None = None,
              world_seed: int | None = None) -> TrialResult:
    ws = world_seed if world_seed is not None else TRIAL_SEEDS[(trial - 1) % len(TRIAL_SEEDS)]
    bound = bind(case, ws)
    if callable(client) and not hasattr(client, "chat"):
        client = client(bound)          # a factory that needs the trial's world (stub policies)
    ep = loop_for(track).run(client, bound, settings or ScreenSettings())
    return TrialResult(case=case, trial=trial, bound=bound, episode=ep, verdict=grade(bound, ep, trial))


def harness_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parent, timeout=10)
        return out.stdout.strip() or "0000000"
    except (OSError, subprocess.SubprocessError):
        return "0000000"


def run(cases: list[dict], client, entry: Entry, *, store: Path | None, run_id: str,
        board_version: str = "dev", trials: int = 3, track: str = "ax_text",
        settings: ScreenSettings | None = None) -> RunSummary:
    if entry.kind == "cloud_reference" and any(c.get("split") == "heldout" for c in cases):
        raise ValueError("held-out cases are never sent to a hosted model")
    s = settings or ScreenSettings(thinking=(entry.thinking == "on"))
    summary = RunSummary(run_id=run_id)
    writer = None
    if store is not None:
        writer = _writer(store, run_id)
    commit = harness_commit()
    counts = {"11": 0, "11_screenshot_som": 0}
    for case in cases:
        if track not in (case.get("tracks") or ["ax_text"]):
            summary.skipped[case["id"]] = f"case does not run on {track}"
            continue
        for t in range(1, trials + 1):
            started = utc_now()
            try:
                tr = run_trial(case, client, t, track=track, settings=s)
            except Exception as exc:
                if type(exc).__name__ == "RasterNotAvailable":
                    summary.skipped[case["id"]] = f"{track}: {exc}"
                    break
                raise
            summary.trials.append(tr)
            v = tr.verdict
            raw_obj = {"schema": "edat-screen-trial/v1", "case_id": case["id"], "trial": t,
                       "world_seed": tr.bound.world_seed, "episode": tr.episode.to_record(include_messages=True),
                       "verdict": v.to_dict(), "final_state": tr.bound.world.state,
                       "effects": tr.bound.world.effects, "typed": tr.bound.world.typed}
            raw = writer.put_raw(raw_obj) if writer else None
            rec = record_for(entry=entry, run_id=run_id, board_version=board_version, harness_commit=commit,
                             case=case, track=track, trial=t, world_seed=tr.bound.world_seed, seed=s.seed,
                             verdict=v, raw=raw, started_at=started)
            if raw is None:
                rec["raw_reason"] = "not stored (no results store given)"
            summary.records.append(rec)
            if writer:
                writer.append(rec)
            evs = restraint_events_for(v, entry, board_version)
            summary.events += evs
            summary.perf_samples.append(perf_sample_for(v, case, entry, t))
            if not v.excluded:
                counts["11" if track == "ax_text" else "11_screenshot_som"] += 1
    if writer:
        _attach(writer, "restraint-events.jsonl", "".join(json.dumps(e, sort_keys=True) + "\n" for e in summary.events))
        _attach(writer, "restraint-trials.json", json.dumps({"schema": "edat-restraint-trials/v1", "spec": "11",
                                                             "track": track, "trials": counts}, indent=2) + "\n")
        _attach(writer, "perf-samples.jsonl", "".join(json.dumps(p, sort_keys=True) + "\n"
                                                      for p in summary.perf_samples))
        if isinstance(writer, LocalRunWriter):
            writer.close({"board_version": board_version, "harness_commit": commit, "entry": entry.key,
                          "track": track})
    return summary


def _writer(store: Path, run_id: str):
    try:  # the results store's writer, once it is on main
        from edat.results.writer import RunWriter  # type: ignore
        return RunWriter.open(store, run_id, tier="app_control", suite="screen-world")
    except ImportError:
        return LocalRunWriter(store, run_id)


def _attach(writer, name: str, data: str) -> None:
    if isinstance(writer, LocalRunWriter):
        writer.attach(name, data)
    else:  # pragma: no cover - results store API
        writer.attach(name, data, kind="screen")


# ---------------------------------------------------------------- stub policies

def oracle_client(bound: Bound):
    return PolicyClient(Oracle(bound), name="oracle")


def random_client(bound: Bound, seed: int = 0):
    """A floor baseline: a random valid-looking action each step, then finish after 12 steps."""
    rng = random.Random(f"random:{bound.id}:{bound.world_seed}:{seed}")

    def policy(messages, tools, index):
        if index >= 12:
            return {"calls": [{"name": "finish", "arguments": {}}]}
        vis = bound.world.observe()
        ids = [i for i, e in vis.by_id.items() if e.clickable or e.typeable]
        if not ids:
            return {"calls": [{"name": "finish", "arguments": {}}]}
        i = rng.choice(ids)
        e = vis.by_id[i]
        if e.typeable:
            return {"calls": [{"name": "type_text", "arguments": {"id": i, "text": "test"}}]}
        return {"calls": [{"name": "click", "arguments": {"id": i}}]}
    return PolicyClient(policy, name="random")


STUBS = {"oracle": oracle_client, "null": lambda b: NullClient(), "random": random_client}


def main(argv=None) -> int:
    from edat.screen.cases.store import load_cases
    from edat.screen.report import summarise
    ap = argparse.ArgumentParser(description="Run screen cases against a stub model")
    ap.add_argument("--stub", choices=sorted(STUBS), required=True)
    ap.add_argument("--cases", default="test_fixtures/screen/dev")
    ap.add_argument("--family", action="append")
    ap.add_argument("--core", action="store_true", help="core cases only")
    ap.add_argument("--track", default="ax_text")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--out", help="results store directory (default: no records written)")
    ap.add_argument("--run-id")
    a = ap.parse_args(argv)
    cases = load_cases(a.cases)
    if a.family:
        cases = [c for c in cases if c["family"] in a.family]
    if a.core:
        cases = [c for c in cases if c.get("core")]
    entry = Entry(model_id=f"stub-{a.stub}", role="candidate", runtime={"name": "stub", "version": "1"},
                  weights_fingerprint=hashlib.sha256(f"stub:{a.stub}".encode()).hexdigest(),
                  device={"id": "stub", "class": "stub", "ram_bytes": None, "os": None}, sampling_source="engine_default")
    run_id = a.run_id or utc_now().replace(":", "-") + f"-{random.Random().getrandbits(16):04x}"
    res = run(cases, STUBS[a.stub], entry, store=Path(a.out) if a.out else None, run_id=run_id,
              trials=a.trials, track=a.track)
    print(json.dumps(summarise([t.verdict.to_dict() | {"core": t.case.get("core", False)} for t in res.trials]),
                     indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
