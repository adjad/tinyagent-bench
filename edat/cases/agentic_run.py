"""The routine agentic run on this Mac: both loops, one model resident at a time.

    python -m edat.cases.agentic_run plan      --out OUT [--plan smoke|smoke-long|routine] [--measured DIR]
    python -m edat.cases.agentic_run preflight --out OUT [--engine] [--weights]
    python -m edat.cases.agentic_run run       --out OUT --plan smoke|smoke-long|routine --board-version V
                                               [--splits dev,heldout] [--trials N] [--heldout-trials N]
                                               [--lock DIR] [--max-minutes N] [--url URL]

What a run does (docs: edat/cases/RUN_SHEET.md):

* **Selection.** `routine` is the core set (spec 02 §5.6.4, §11): every case
  tagged `core` in the development split and in the private held-out split
  (the 7 headline families, privacy, preferences, memory, date-time,
  oversized results and the long sessions), plus the noisy variants of each
  split's 30 core seeds. English only. The scaling probe is not in it.
  `smoke` is 9 development cases (`smoke_cases`), one trial, on the first model;
  `smoke-long` is one long session (60 user turns), one trial. `plan` prints
  the routine run at three sizes: (a) both splits at 3 trials, (b) the
  development split at 3 and the held-out split at 1, (c) development only.
  Every record carries `selection` {tier, manifest_sha256, n_items,
  ci95_half_width_pts}; the manifest (the sorted case ids) is written to
  OUT/<plan>/selection.json.
* **Models.** The two references, tuned first: `Gemma-Wisp r5` then base
  Gemma 4 E4B, each in the thinking mode it ships in (off) with the author's
  sampling (temperature 1.0, top_p 0.95, top_k 64) sent on every request and
  recorded as `author_recommended`. Trials 1-3: world seed t, sampling seed
  1000 + t.
* **Loops.** Each case-trial runs through the minimal reference loop (the
  headline, suite `reference-loop`) and, for the column's cases (§5.6.1:
  assistant-lane core of the 7 families with privacy, date-time and
  oversized), Harbor's own Ask turn (suite `harbor-loop`); a column case
  Harbor's host refuses is recorded `not_applicable` without a model call.
  The neutral lane, preferences, memory, long sessions and noisy variants
  have no Harbor-loop trial. Harbor's loop runs in this process
  from the repository's code; it needs no Harbor port, only oMLX on
  127.0.0.1.
* **Isolation.** The process first runs Toolbench's `isolate()`: a throwaway
  Harbor home, outbound network refused except loopback to the engine port,
  subprocesses refused except read-only probes. Every tool is simulated (an
  in-memory world); nothing a model writes is executed.
* **Safety.** Everything host-facing goes through the shared run guard
  (`edat/runtime/run_guard.py`). It covers the heavy-job lock, the engine state
  saved fresh and restored on every exit (signals included), one model at a
  time, a Harbor check that fails closed, one loopback engine URL, and the
  stops. A second `run` with the same OUT resumes: trials already recorded are
  not run again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCK = "/private/tmp/claude-501/wisp-heavy.lock"
DEFAULT_URL = "http://127.0.0.1:8000"   # oMLX; every engine call of a run uses the one --url
TRIALS = 3
ASSUMED_S_PER_TURN = 10.0             # spec 02 §11: about 10 s per single-turn case-trial on the M5 Pro
SMOKE_FAMILIES = ("select", "parallel", "sequential", "mixed", "pev", "recovery", "abstain")
SUITE_ORDER = ("reference-loop", "harbor-loop")


@dataclass(frozen=True)
class ModelEntry:
    engine_id: str
    label: str
    role: str
    thinking: bool                    # the mode it ships in
    sampling: dict
    basis: str


def references() -> list[ModelEntry]:
    """The two references in run order (tuned first), from the sidekick ceiling's model facts."""
    from edat.sidekick.ceiling.models import FIRST, KNOWN, SAMPLING
    out = []
    for eid in reversed(FIRST):       # FIRST lists base then tuned; the tuned reference runs first here
        label, role, ships = KNOWN[eid]
        prof = SAMPLING["gemma4"][ships][0]
        out.append(ModelEntry(eid, label, role, ships, dict(prof.sampling), prof.basis))
    return out


def sampling_for(m: ModelEntry):
    from edat.models.base import Sampling
    return Sampling(**m.sampling, source="author_recommended")


# ------------------------------------------------------------------ selection

@dataclass(frozen=True)
class Unit:
    model: str
    suite: str
    split: str
    case_id: str
    trial: int

    @property
    def key(self) -> tuple:
        return (self.model, self.suite, self.split, self.case_id, self.trial)


def variant_rng(split: str) -> random.Random:
    return random.Random(f"agentic-routine|variants|{split}")


def core_cases(split: str) -> list[dict]:
    from edat.cases.loader import load_split
    return [c for c in load_split(split) if "core" in (c.get("tags") or [])]


def noisy_core(split: str) -> list[dict]:
    """The noisy variants of the split's 30 core seeds (§3.18): typo, transcript, paraphrase."""
    from edat.cases.loader import load_split
    from edat.cases.variants import build_variants, select_seeds
    rng = variant_rng(split)
    seeds = select_seeds(load_split(split), rng)
    return build_variants(seeds.core, rng).variants


def smoke_cases() -> list[dict]:
    """One single-turn development case per headline family that both loops run (date-time cases last),
    one neutral case and one multi-turn preferences case (reference loop only): 9 cases, 16 trials."""
    from edat.cases.harbor_loop import in_column
    from edat.cases.variants import single_turn
    dev = sorted(core_cases("dev"), key=lambda c: ("datetime" in (c.get("tags") or []), c["id"]))
    out = [next(c for c in dev if c["family"] == f and in_column(c) and single_turn(c)) for f in SMOKE_FAMILIES]
    out.append(next(c for c in dev if c["lane"] == "neutral" and c["family"] == "select" and single_turn(c)))
    out.append(next(c for c in dev if c["lane"] == "assistant" and c["family"] == "preferences"
                    and not single_turn(c)))
    return out


def smoke_long_cases() -> list[dict]:
    """One long session (assistant lane, development split): its turns measure long-history speed."""
    return [next(c for c in sorted(core_cases("dev"), key=lambda c: c["id"])
                 if c["family"] == "long_session" and c["lane"] == "assistant")]


PLANS = ("smoke", "smoke-long", "routine")
SPLITS = ("dev", "heldout")


def select(plan: str, splits: tuple[str, ...] = SPLITS) -> list[dict]:
    if plan == "smoke":
        return smoke_cases()
    if plan == "smoke-long":
        return smoke_long_cases()
    if plan != "routine":
        raise ValueError(f"unknown plan {plan!r}")
    out = []
    for split in splits:
        out.extend(core_cases(split))
        out.extend(noisy_core(split))
    return out


def trials_for(trials, split: str) -> int:
    """`trials` is one count for every split or {split: count} (e.g. development 3, held-out 1)."""
    return trials.get(split, 0) if isinstance(trials, dict) else int(trials)


def scenarios(trials: int = 3) -> dict[str, dict]:
    """The coordinator's three sizes for the routine run (selection, trials per split)."""
    return {"a_both_splits": {"splits": SPLITS, "trials": {"dev": trials, "heldout": trials}},
            "b_dev3_heldout1": {"splits": SPLITS, "trials": {"dev": trials, "heldout": 1}},
            "c_dev_only": {"splits": ("dev",), "trials": {"dev": trials}}}


def interleave(cases: list[dict]) -> list[dict]:
    """Families round-robin within each split, so a run stopped early still covers every family."""
    out = []
    for split in ("dev", "heldout"):
        fam: dict[str, list[dict]] = {}
        for c in sorted((c for c in cases if c.get("split", "dev") == split), key=lambda c: c["id"]):
            fam.setdefault(c["family"], []).append(c)
        queues = [fam[k] for k in sorted(fam)]
        while any(queues):
            for q in queues:
                if q:
                    out.append(q.pop(0))
    return out


def manifest(cases: list[dict], plan: str) -> dict:
    ids = sorted(f"{c.get('split', 'dev')}/{c['id']}" for c in cases)
    sha = hashlib.sha256("\n".join(ids).encode()).hexdigest()
    groups: dict[str, int] = {}
    for c in cases:
        groups[group_of(c)] = groups.get(group_of(c), 0) + 1
    return {"tier": "core" if plan == "routine" else plan, "manifest_sha256": sha, "n_cases": len(ids),
            "groups": groups, "case_ids": ids}


def group_of(case: dict) -> str:
    return f"{case.get('split', 'dev')}/{case['lane']}/{case['family']}"


def selection_field(man: dict, case: dict) -> dict:
    n = man["groups"][group_of(case)]
    return {"tier": man["tier"], "manifest_sha256": man["manifest_sha256"], "n_items": n,
            "ci95_half_width_pts": round(100 * 1.96 * math.sqrt(0.25 / n), 1)}


def user_turns(case: dict) -> int:
    from edat.cases.bind import bind
    return max(1, sum(len(s.get("turns") or []) for s in bind(case, 1)["sessions"]))


# ------------------------------------------------------------------ the plan and its time

def harbor_column(case: dict) -> bool:
    """The Harbor-loop column's cases (§5.6.1): assistant-lane core of the 7 families plus privacy,
    date-time and oversized; never the neutral lane or a noisy variant. Others get no Harbor-loop trial."""
    from edat.cases.harbor_loop import in_column
    return in_column(case) and not case.get("variant_of")


def units(cases: list[dict], models: list[ModelEntry], trials, *, column=None) -> list[Unit]:
    column = column or harbor_column
    return [Unit(m.engine_id, suite, c.get("split", "dev"), c["id"], t)
            for m in models for c in cases for t in range(1, trials_for(trials, c.get("split", "dev")) + 1)
            for suite in SUITE_ORDER if suite == "reference-loop" or column(c)]


def done_keys(store: Path, *, with_damaged: bool = False):
    """Recorded trials (model, suite, split, case, trial). A line that does not parse (a run killed mid-write)
    is skipped and counted: its trial runs again on resume."""
    out, damaged = set(), 0
    for f in Path(store).glob("runs/*/records.jsonl"):
        for line in f.read_text(errors="replace").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    out.add((r["model"]["id"], r["suite"], r["split"], r["case_id"], r["trial"]))
                except (ValueError, KeyError, TypeError):
                    damaged += 1
    return (out, damaged) if with_damaged else out


def rate_key(suite: str, family: str | None) -> str:
    """Long sessions carry long histories and are timed apart: `<suite>:long_session`."""
    return f"{suite}:long_session" if family == "long_session" else suite


def measured_rates(folder: Path) -> dict[str, float]:
    """Seconds per user turn, per suite (long sessions apart), from every timing.jsonl under `folder`
    (trials that called the model)."""
    sums: dict[str, list[float]] = {}
    for f in Path(folder).rglob("timing.jsonl"):
        for line in f.read_text().splitlines():
            if line.strip():
                t = json.loads(line)
                if t.get("status") == "ok":
                    s = sums.setdefault(rate_key(t["suite"], t.get("family")), [0.0, 0.0])
                    s[0] += t["seconds"]
                    s[1] += t["turns"]
    return {k: v[0] / v[1] for k, v in sums.items() if v[1]}


def rate_for(rates: dict[str, float], suite: str, family: str | None) -> float:
    return rates.get(rate_key(suite, family), rates.get(suite, ASSUMED_S_PER_TURN))


def estimate(cases: list[dict], models: list[ModelEntry], trials, *, rates: dict[str, float] | None = None,
             harbor_applicable=None, done: set[tuple] = frozenset(), column=None) -> dict:
    """Hours per model and suite (and per split): user turns x trials x seconds per turn (measured,
    else 10 s). A column case Harbor's host refuses (recorded not_applicable) costs nothing."""
    rates = rates or {}
    turns = {(c.get("split", "dev"), c["id"]): user_turns(c) for c in cases}
    family = {(c.get("split", "dev"), c["id"]): c["family"] for c in cases}
    applicable = {(c.get("split", "dev"), c["id"]): (harbor_applicable(c) if harbor_applicable else True)
                  for c in cases}
    rows: dict[str, dict] = {}
    for u in units(cases, models, trials, column=column):
        if u.key in done:
            continue
        m = rows.setdefault(u.model, {**{s: {"case_trials": 0, "turns": 0, "hours": 0.0} for s in SUITE_ORDER},
                                      "by_split_hours": {}})
        if u.suite == "harbor-loop" and not applicable[(u.split, u.case_id)]:
            continue
        n = turns[(u.split, u.case_id)]
        h = n * rate_for(rates, u.suite, family[(u.split, u.case_id)]) / 3600
        m[u.suite]["case_trials"] += 1
        m[u.suite]["turns"] += n
        m[u.suite]["hours"] += h
        m["by_split_hours"][u.split] = m["by_split_hours"].get(u.split, 0.0) + h
    for m in rows.values():
        for k in SUITE_ORDER:
            m[k]["hours"] = round(m[k]["hours"], 2)
        m["by_split_hours"] = {k: round(v, 2) for k, v in m["by_split_hours"].items()}
        m["total_hours"] = round(sum(m[k]["hours"] for k in SUITE_ORDER), 2)
    keys = [*SUITE_ORDER, *(rate_key(s, "long_session") for s in SUITE_ORDER[:1])]
    return {"seconds_per_turn": {k: round(rates.get(k, rates.get(k.split(":")[0], ASSUMED_S_PER_TURN)), 2)
                                 for k in keys},
            "rates_source": "measured" if rates else "assumed (spec 02: 10 s per case-trial)",
            "models": rows, "total_hours": round(sum(m["total_hours"] for m in rows.values()), 2)}


# ------------------------------------------------------------------ host checks

def engine_version() -> str | None:
    try:
        v = json.loads((Path.home() / ".omlx" / "settings.json").read_text()).get("version")
        return str(v)[:64] if v else None
    except (OSError, ValueError):
        return None


def outside_repo(out: Path) -> bool:
    from edat.runtime.run_guard import inside_repo
    return not inside_repo(out, REPO)


def under_private(out: Path) -> bool:
    p = os.environ.get("EDAT_PRIVATE_DIR")
    return bool(p) and Path(out).expanduser().resolve().is_relative_to(Path(p).resolve())


def preflight(out: Path, *, engine: bool = False, weights: bool = False, lock: str = LOCK,
              base_url: str = DEFAULT_URL, harbor=None, free_pct=None) -> list[tuple[str, bool, str]]:
    """Read-only checks; the run starts only if every row is ok. No model is loaded or called.
    `engine` reads the engine's model status (GET only); `weights` hashes each reference's weights
    (several GB read, cached in OUT/weights.json; never when OUT is inside the repository)."""
    from edat.runtime import run_guard as rg
    rows: list[tuple[str, bool, str]] = []
    add = lambda name, ok, detail="": rows.append((name, bool(ok), detail))
    out = Path(out).expanduser().resolve()
    in_repo = not outside_repo(out)
    add("results folder outside the repository", not in_repo, "")
    add("results folder under $EDAT_PRIVATE_DIR (held-out records)", under_private(out), "")
    from edat.cases.loader import heldout_dir
    hd = heldout_dir()
    add("held-out cases present", bool(hd and hd.is_dir() and any(hd.rglob("*.yaml"))), "")
    add("heavy-job lock free", bool(lock) and not Path(lock).exists(), lock or "no lock path")
    save = Path(f"{lock}.engine-save.json")
    leftover = save.exists()                    # the guard removes it after every successful restore
    add("no unrestored engine save from an earlier run", not leftover, str(save) if leftover else "")
    h = (harbor or rg.harbor_running)()
    add("Harbor is closed (app, swift-run build, backend, ports 8765/8791)", not h, h or "")
    free = (free_pct or rg.memory_free_pct)()
    add("free memory at least 30%", free is None or free >= 30, f"{free}%" if free is not None else "unknown")
    import shutil
    probe = out
    while not probe.exists():
        probe = probe.parent
    disk = shutil.disk_usage(probe)
    add("disk: 5 GB free for results", disk.free > 5e9, f"{disk.free / 1e9:.0f} GB free")
    from edat.sidekick.ceiling.models import model_dir_from_settings, scan
    from edat.sidekick.ceiling.run import engine_context
    mdir = model_dir_from_settings()
    models = {m.engine_id: m for m in (scan(mdir)[0] if mdir and mdir.is_dir() else [])}
    for m in references():
        add(f"model folder: {m.engine_id}", m.engine_id in models, m.label)
        ctx, src = engine_context(m.engine_id)
        add(f"context window: {m.engine_id}", ctx is None or ctx >= 16384, f"{ctx} tokens ({src}); needs 16384")
        if weights and m.engine_id in models and not in_repo:
            from edat.sidekick.ceiling.run import weights_for
            add(f"weights_sha256: {m.engine_id}", True, weights_for(out, mdir, models[m.engine_id]))
    if engine:
        e = None
        try:
            e = rg.Engine(base_url, api_key=rg.read_engine_key(), timeout_s=30)
            rows_ = e.status()
            ids = {x.get("id") for x in rows_}
            for m in references():
                add(f"engine lists {m.engine_id}", m.engine_id in ids, "")
            snap = e.snapshot()
            add("engine: loaded now (saved and restored by the run)", True,
                f"loaded {', '.join(snap['loaded']) or 'none'}; pinned {', '.join(snap['pinned']) or 'none'}; "
                f"default {snap['default_model']}")
        except Exception as exc:  # noqa: BLE001
            add("engine reachable on loopback", False, f"{type(exc).__name__}")
        finally:
            if e is not None:
                e.close()
    return rows


# ------------------------------------------------------------------ the run

def device_block(device_id: str) -> dict:
    from edat.sidekick.ceiling.run import device_block as db
    return db(device_id)


def run(out: Path, *, plan: str, board_version: str, trials=None, splits: tuple[str, ...] = SPLITS,
        lock: str | None = LOCK, max_minutes: float | None = None, models: list[ModelEntry] | None = None,
        clients=None, engine=None, isolate: bool = True, weights=None, device_id: str = "this-mac",
        base_url: str = DEFAULT_URL, progress=print, harbor=None, free_pct=None,
        loops: dict | None = None, column=None) -> dict:
    """The run, inside `edat.runtime.run_guard.RunGuard`. `clients(model, loop_name)` returns the model
    client for a loop (tests pass stubs); by default oMLX for the reference loop and Harbor's own client for
    the Harbor loop. Every engine call, the guard's and the clients', uses the one loopback `base_url`."""
    from edat.runtime import run_guard as rg
    out = Path(out).expanduser().resolve()
    base_url = rg.loopback_base_url(base_url)
    if plan == "routine" and not under_private(out):
        raise rg.GuardError("the routine run includes held-out cases: OUT must be under $EDAT_PRIVATE_DIR")
    smoke = plan.startswith("smoke")
    trials = trials or (1 if smoke else TRIALS)
    models = models or (references()[:1] if smoke else references())
    key = rg.read_engine_key()
    engine = engine or rg.Engine(base_url, api_key=key)   # the guard always holds the engine
    folder = out / plan
    guard = rg.RunGuard(folder, lock=lock, engine=engine, max_minutes=max_minutes,
                        harbor=harbor or rg.harbor_running, free_pct=free_pct or rg.memory_free_pct)
    with guard as g:
        g.report.update(plan=plan, models=[m.engine_id for m in models], trials=trials, splits=list(splits))
        if isolate:
            _isolate(rg.port_of(base_url))
        cases = interleave(select(plan, splits))
        man = manifest(cases, plan)
        (folder / "selection.json").write_text(json.dumps(man, indent=1))
        g.report["counts"] = _run_models(g, folder, cases, man, models, trials, board_version=board_version,
                                         clients=clients or _default_clients(base_url, key), weights=weights,
                                         device_id=device_id, progress=progress, loops=loops, column=column)
    if progress:
        progress(f"{plan}: {g.outcome}" + (f" ({g.stop_reason})" if g.stop_reason else "")
                 + f"; {g.report.get('engine')}")
    return g.report


def _isolate(port: int) -> None:
    """Toolbench's isolate(): throwaway Harbor home, network and subprocess guards. The key is not put in
    the environment; the clients get it directly."""
    from scripts.toolbench import runner
    runner.isolate(Path(tempfile.mkdtemp(prefix="edat-agentic-home-")), omlx_port=port)


def _default_clients(base_url: str, key: str | None):
    from edat.cases.harbor_loop import open_session
    from edat.models import OmlxClient
    from edat.runtime.run_guard import port_of
    cache: dict = {}

    def get(m: ModelEntry, loop_name: str):
        k = (m.engine_id, loop_name)
        if k not in cache:
            if loop_name == "harbor-loop":                       # isolate() already ran
                cache[k] = open_session(m.engine_id, omlx_port=port_of(base_url), api_key=key)
            else:
                cache[k] = OmlxClient(m.engine_id, base_url=base_url, api_key=key)
        return cache[k]
    return get


def _run_models(g, folder: Path, cases, man, models, trials, *, board_version, clients, weights, device_id,
                progress, loops=None, column=None) -> dict:
    from edat.cases.harbor_loop import HarborLoop
    from edat.cases.runner import run_trial
    from edat.grading.records import RunWriter
    from edat.loop import LoopSettings, get_loop
    from edat.sidekick.records import harness_commit
    store = folder / "store"
    done, damaged = done_keys(store, with_damaged=True)
    commit, dirty = harness_commit()
    loops = loops or {"reference-loop": get_loop("reference"), "harbor-loop": HarborLoop()}
    device = device_block(device_id)
    by_key = {(c.get("split", "dev"), c["id"]): c for c in cases}
    t0 = time.monotonic()
    counts = {"ran": 0, "skipped_done": 0, "errors": 0, "damaged_record_lines": damaged}
    with (folder / "timing.jsonl").open("a") as timing:
        for m in models:
            every = units(cases, [m], trials, column=column)
            todo = [u for u in every if u.key not in done]
            counts["skipped_done"] += len(every) - len(todo)
            if not todo:
                continue
            sha = weights(m) if weights else _weights(folder, m)
            g.prepare(m.engine_id)
            model = {"id": m.engine_id, "role": m.role, "weights_sha256": sha, "headline_entry": True}
            runtime = {"reference-loop": {"name": "omlx", "version": engine_version()},
                       "harbor-loop": {"name": "harbor-omlx", "version": engine_version(), "commit": commit}}
            settings = LoopSettings(thinking=m.thinking, sampling=sampling_for(m))
            with RunWriter(store) as w:
                for u in todo:
                    if g.should_stop():
                        return counts
                    case = by_key[(u.split, u.case_id)]
                    started = time.monotonic()
                    try:
                        r = run_trial(case, clients(m, u.suite), trial=u.trial, loop=loops[u.suite],
                                      settings=replace(settings))
                    except Exception as exc:  # noqa: BLE001  (logged, not recorded; a resume retries it)
                        counts["errors"] += 1
                        _log(folder / "errors.jsonl", {**u.__dict__, "error": f"{type(exc).__name__}: {exc}"[:500]})
                        if counts["errors"] >= 5:
                            g.stop("5 trials raised errors (see errors.jsonl)")
                            return counts
                        continue
                    secs = time.monotonic() - started
                    rec = w.append(r, board_version=board_version, harness_commit=commit + ("-dirty" if dirty else ""),
                                   model=model, runtime=runtime[u.suite], device=device,
                                   selection=selection_field(man, case))
                    g.note(rec["status"])
                    timing.write(json.dumps({**u.__dict__, "status": rec["status"], "seconds": round(secs, 3),
                                             "turns": user_turns(case), "family": case["family"]}) + "\n")
                    timing.flush()
                    counts["ran"] += 1
                    if progress and counts["ran"] % 25 == 0:
                        progress(f"{m.engine_id}: {counts['ran']} trials, {(time.monotonic() - t0) / 60:.0f} min")
    return counts


def _weights(folder: Path, m: ModelEntry) -> str:
    from edat.sidekick.ceiling.models import model_dir_from_settings, scan
    from edat.sidekick.ceiling.run import weights_for
    mdir = model_dir_from_settings()
    found = {x.engine_id: x for x in scan(mdir)[0]} if mdir else {}
    if m.engine_id not in found:
        raise RuntimeError(f"no model folder for {m.engine_id}")
    return weights_for(folder.parent, mdir, found[m.engine_id])


def _log(path: Path, row: dict) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


# ------------------------------------------------------------------ CLI

def _harbor_applicable():
    from edat.cases.bind import bind
    from edat.cases.harbor_loop import HarborLoop
    loop = HarborLoop()
    return lambda c: loop.unsupported(bind(c, 1)) is None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.cases.agentic_run", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="the selection and its time (no model, no engine)")
    p.add_argument("--out", required=True)
    p.add_argument("--plan", choices=PLANS, default="routine")
    p.add_argument("--measured", help="a folder holding timing.jsonl files (OUT, after the smoke steps): "
                                      "re-estimate from measured speed")
    p.add_argument("--detail", action="store_true", help="routine: every suite and split of size (a)")
    f = sub.add_parser("preflight", help="read-only checks before a run")
    f.add_argument("--out", required=True)
    f.add_argument("--engine", action="store_true", help="also list the engine's models (GET only)")
    f.add_argument("--weights", action="store_true", help="also hash both references' weights (read only)")
    f.add_argument("--lock", default=LOCK)
    f.add_argument("--url", default=DEFAULT_URL, help="the engine (loopback only)")
    r = sub.add_parser("run", help="run a plan on oMLX (needs the go-ahead)")
    r.add_argument("--out", required=True)
    r.add_argument("--plan", choices=PLANS, required=True)
    r.add_argument("--board-version", required=True)
    r.add_argument("--trials", type=int, help="trials per case (default: 3; 1 for the smoke plans)")
    r.add_argument("--heldout-trials", type=int, help="routine: trials per held-out case, if not --trials")
    r.add_argument("--splits", default="dev,heldout", help="routine: dev,heldout (default) or dev")
    r.add_argument("--lock", default=LOCK)
    r.add_argument("--max-minutes", type=float)
    r.add_argument("--url", default=DEFAULT_URL, help="the engine, for every call of the run (loopback only)")
    r.add_argument("--device-id", default="this-mac")
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser()
    if a.cmd == "preflight":
        rows = preflight(out, engine=a.engine, weights=a.weights, lock=a.lock, base_url=a.url)
        for name, ok, detail in rows:
            print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        return 0 if all(ok for _, ok, _ in rows) else 1
    if a.cmd == "plan":
        _isolate(8000)                                  # guards on; no request is sent
        smoke = a.plan.startswith("smoke")
        cases = select(a.plan)
        models = references()[:1] if smoke else references()
        rates = measured_rates(Path(a.measured)) if a.measured else None
        done = done_keys(out / a.plan / "store") if (out / a.plan / "store").exists() else set()
        applicable = _harbor_applicable()
        if smoke or a.detail:
            man = manifest(cases, a.plan)
            est = estimate(cases, models, 1 if smoke else TRIALS, rates=rates, harbor_applicable=applicable,
                           done=done)
            print(json.dumps({"cases": man["n_cases"], "already_recorded": len(done), **est}, indent=1))
            return 0
        out_rows = {}
        for name, sc in scenarios().items():
            sel = [c for c in cases if c.get("split", "dev") in sc["splits"]]
            est = estimate(sel, models, sc["trials"], rates=rates, harbor_applicable=applicable, done=done)
            out_rows[name] = {"cases": len(sel), "trials": sc["trials"],
                              "hours_per_model": {k: v["total_hours"] for k, v in est["models"].items()},
                              "total_hours": est["total_hours"]}
        print(json.dumps({"seconds_per_turn": est["seconds_per_turn"], "rates_source": est["rates_source"],
                          "already_recorded": len(done), "sizes": out_rows}, indent=1))
        return 0
    splits = tuple(x for x in a.splits.split(",") if x)
    if any(x not in SPLITS for x in splits):
        ap.error(f"--splits takes {', '.join(SPLITS)}")
    trials = a.trials
    if a.heldout_trials is not None:
        trials = {"dev": a.trials or TRIALS, "heldout": a.heldout_trials}
    from edat.runtime.run_guard import EXIT, GuardError, RunInterrupted
    try:
        rep = run(out, plan=a.plan, board_version=a.board_version, trials=trials, splits=splits, lock=a.lock,
                  max_minutes=a.max_minutes, base_url=a.url, device_id=a.device_id)
    except GuardError as exc:
        print(f"not started or stopped by the guard: {exc}", file=sys.stderr)
        return 1
    except RunInterrupted as exc:
        print(f"interrupted by {exc}; the engine restore and the report are in {out / a.plan}", file=sys.stderr)
        return 128 + exc.signum
    except Exception as exc:  # noqa: BLE001  (the guard has restored, or recorded why not, and written the report)
        print(f"failed: {type(exc).__name__}: {exc}; see {out / a.plan / 'run-report.json'}", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in rep.items() if k != "models"}, indent=1, default=str))
    return EXIT[rep["outcome"]]


if __name__ == "__main__":
    sys.exit(main())
