"""Real runs of the screen-control tests on this Mac's oMLX: preflight, plan, run, report.

    python -m edat.screen.live preflight --out OUT --lock LOCK [--engine] [--weights]
    python -m edat.screen.live plan [--plan core] [--measured OUT]
    python -m edat.screen.live run --plan smoke|core --out OUT --lock LOCK --max-minutes N [--track ...] [--only ID]
    python -m edat.screen.live report OUT [--plan core]

The run sheet is `edat/screen/RUN_SHEET.md`. The two Gemma references run, tuned first, each in the
thinking mode it ships in, with the author's sampling sent with every request and recorded. Tracks:
`ax_text` (the text track) and `screenshot_som` (a PNG drawn from the simulated screen).

What a model can touch: only Screen World, the pure-Python simulator in `edat/screen/world/` and
`edat/screen/apps/` (no OS automation, no network, no clock; tests/test_edat_screen_safety.py checks
it). A model's `click`, `type_text` and the other tools change that simulated state and nothing else.
This module is the harness around it: the only things it does outside Python are read-only host
checks (`pgrep`, `memory_pressure`, disk space), loopback HTTP to oMLX (chat requests, and
`engine_prep`'s unload/restore of resident models) and writing results to OUT.

Safety wrapper: refuses OUT inside the repository; refuses to start while Harbor runs or the heavy-job
lock exists (it removes the lock only if it created it); one model resident at a time (`engine_prep
keep`, which saves what was loaded before, once); between trials it stops on the STOP file, the minute
limit, free memory under 15%, Harbor opening, or over 2% of trials lost to engine faults; on any exit,
error included, it restores the resident models (`engine_prep restore`) and then the lock. A rerun of
the same command resumes: recorded trials are never redone.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from edat.screen.adapter import Sampling
from edat.screen.cases.store import load_cases
from edat.screen.loop import ScreenSettings
from edat.screen.records import Entry, perf_sample_for, record_for, restraint_events_for, utc_now
from edat.screen.runner import STUBS, _writer, harness_commit, run_trial

REPO = Path(__file__).resolve().parents[2]
DEV_DIR = REPO / "test_fixtures" / "screen" / "dev"
REFERENCES = ("gemma-4-E4B-it-r5-step-01800-oQ4e-mtp",      # tuned reference (Gemma-Wisp r5): first
              "gemma-4-E4B-it-oQ4e-mtp")                     # base reference
TRACKS = ("ax_text", "screenshot_som")
PLANS = ("smoke", "core")
TRIALS = {"smoke": 1, "core": 3}
SMOKE_CASES = {"ax_text": 12, "screenshot_som": 4}          # dev core cases, spread evenly
BASE_URL = "http://127.0.0.1:8000"

# Estimate basis before the smoke step: spec 11 §3.11 assumes 2.5 s per step. A trial is assumed to take
# 1.7 x its optimal steps (capped at its budget); with these, the core text track comes to the spec's
# 3.7 h a model. Screenshot steps carry an image: 3.9 s per step matches the spec's 2.9 h.
SEC_PER_STEP = {"ax_text": 2.5, "screenshot_som": 3.9}
STEP_FACTOR = 1.7
MODEL_CHANGE_S = 60.0
TRIAL_OVERHEAD_S = 0.3

MEMORY_FLOOR_PCT = 15          # stop between trials below this
MEMORY_START_PCT = 30          # preflight wants this much
DISK_FREE_GB = 5.0
EXCLUDED_LIMIT = 0.02
EXCLUDED_FROM = 50
IMAGE_PROBE_TRIALS = 3         # a screenshot unit whose first 3 trials all fail on the engine is skipped
HOST_STOPS = ("STOP file", "minute limit", "free memory", "Harbor", "engine faults")


# ------------------------------------------------------------------ cases

def private_dir() -> Path | None:
    d = os.environ.get("EDAT_PRIVATE_DIR")
    return Path(d).expanduser() if d else None


def heldout_dir() -> Path:
    d = private_dir()
    if d is None:
        raise RuntimeError("EDAT_PRIVATE_DIR is not set: the core run needs the held-out split")
    h = d / "screen" / "heldout"
    if not any(h.glob("*.yaml")):
        raise RuntimeError("no held-out screen cases under $EDAT_PRIVATE_DIR/screen/heldout")
    return h


def _spread(cases: list[dict], n: int) -> list[dict]:
    cases = sorted(cases, key=lambda c: (c["family"], c["level"], c["id"]))
    if n >= len(cases):
        return cases
    return [cases[math.floor(i * len(cases) / n)] for i in range(n)]


def plan_cases(plan: str, track: str, *, dev: list[dict] | None = None, heldout: list[dict] | None = None) -> list[dict]:
    """The cases a plan runs on a track: core = every core case of both splits that lists the track."""
    dev = load_cases(DEV_DIR) if dev is None else dev
    on = [c for c in dev if c.get("core") and track in (c.get("tracks") or [])]
    if plan == "smoke":
        return _spread(on, SMOKE_CASES[track])
    if plan != "core":
        raise ValueError(f"unknown plan {plan!r}")
    heldout = load_cases(heldout_dir()) if heldout is None else heldout
    return on + [c for c in heldout if c.get("core") and track in (c.get("tracks") or [])]


def selection_for(plan: str, cases: list[dict]) -> dict:
    ids = "\n".join(sorted(c["id"] for c in cases))
    return {"tier": plan, "manifest_sha256": hashlib.sha256(ids.encode()).hexdigest()}


def trial_seed(case_id: str, trial: int) -> int:
    return int(hashlib.sha256(f"{case_id}:{trial}".encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


# ------------------------------------------------------------------ models

def reference_models(only: tuple[str, ...] = ()):
    """The two references from oMLX's model folder (read only), tuned first."""
    from edat.sidekick.ceiling.models import model_dir_from_settings, scan
    mdir = model_dir_from_settings()
    if not mdir:
        raise RuntimeError("oMLX's model folder is not set (~/.omlx/settings.json model_dir)")
    found = {m.engine_id: m for m in scan(mdir)[0]}
    out = []
    for mid in REFERENCES:
        if only and mid not in only:
            continue
        if mid not in found:
            raise RuntimeError(f"{mid} is not a complete model in oMLX's model folder")
        out.append(found[mid])
    return mdir, out


def entry_for(m, *, weights_sha256: str | None, runtime_version: str | None, device: dict, scripted: bool = False) -> tuple[Entry, Sampling, bool]:
    """The model in its shipped thinking mode with the author's sampling (both sent with every request)."""
    from edat.sidekick.ceiling.run import record_sampling
    thinking = bool(m.thinking[0])
    prof = m.profiles(thinking)[0]
    out_tokens = ScreenSettings(thinking=thinking).out_tokens()
    sampling = Sampling(**prof.sampling, source=prof.source)
    fp = None if weights_sha256 else hashlib.sha256(f"screen-stub:{m.engine_id}".encode()).hexdigest()
    entry = Entry(model_id=m.engine_id, role=m.role, kind="local", quant=dict(m.quant),
                  weights_sha256=weights_sha256, weights_fingerprint=fp,
                  runtime={"name": "stub" if scripted else "omlx", "version": "0" if scripted else runtime_version},
                  device=device, thinking="on" if thinking else "off",
                  sampling={**record_sampling(prof.sampling), "max_output_tokens": out_tokens},
                  sampling_source=prof.source)
    return entry, sampling, thinking


def _slug(engine_id: str) -> str:
    return engine_id.replace("/", "_")


def run_id_for(plan: str, engine_id: str, track: str) -> str:
    return f"screen-{plan}-{_slug(engine_id)}-{track}"


# ------------------------------------------------------------------ host checks

def harbor_running() -> bool:
    from edat.sidekick.ceiling.run import harbor_running as _h
    return _h()


def memory_free_pct() -> int | None:
    from edat.sidekick.ceiling.run import memory_free_pct as _m
    return _m()


def outside_repo(out: Path) -> Path:
    out = Path(out).expanduser().resolve()
    if out == REPO or REPO in out.parents:
        raise SystemExit("results go to a private folder outside the repository")
    return out


@dataclass
class Stopper:
    out: Path
    max_minutes: float | None
    started: float = field(default_factory=time.monotonic)
    host: bool = True                  # memory and Harbor checks (off for stub runs in tests)

    def why(self) -> str | None:
        if (self.out / "STOP").exists():
            return "the STOP file exists"
        if self.max_minutes is not None and (time.monotonic() - self.started) / 60 >= self.max_minutes:
            return f"the {self.max_minutes:g}-minute limit was reached"
        if self.host:
            free = memory_free_pct()
            if free is not None and free < MEMORY_FLOOR_PCT:
                return f"free memory {free}% is under {MEMORY_FLOOR_PCT}%"
            if harbor_running():
                return "Harbor was opened during the run"
        return None


# ------------------------------------------------------------------ one model on one track

def _done(run_dir: Path) -> list[dict]:
    f = run_dir / "records.jsonl"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()]


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")


def run_unit(client, entry: Entry, sampling: Sampling, thinking: bool, cases: list[dict], *, plan: str, track: str,
             store: Path, stopper: Stopper, board_version: str = "dev", progress=print) -> dict:
    """Every trial of `cases` on `track` for one entry, resuming past recorded trials."""
    from edat.screen.render.raster import renderer_versions
    run_id = run_id_for(plan, entry.model_id, track)
    selection = selection_for(plan, cases)
    trials = TRIALS[plan]
    commit = harness_commit()
    side = store / "sidecar" / run_id
    state = {"run_id": run_id, "track": track, "plan": plan, "cases": len(cases), "trials_per_case": trials,
             "selection": selection, "written": 0, "resumed_from": 0, "stopped": None, "skipped": None}
    with _writer(store, run_id) as w:
        recs = _done(Path(w.dir))
        state["resumed_from"] = len(recs)
        if recs and any((r.get("selection") or {}).get("manifest_sha256") != selection["manifest_sha256"] for r in recs):
            raise RuntimeError(f"{run_id}: recorded trials are from another case list; use a new OUT")
        done = {(r["case_id"], r["trial"]) for r in recs}
        statuses = [r["status"] for r in recs]
        for case in cases:
            for t in range(1, trials + 1):
                if (case["id"], t) in done:
                    continue
                why = stopper.why()
                if why is None and len(statuses) >= EXCLUDED_FROM:
                    bad = statuses.count("excluded")
                    if bad / len(statuses) > EXCLUDED_LIMIT:
                        why = f"{bad} of {len(statuses)} trials failed on engine faults (over 2%)"
                if why:
                    state["stopped"] = why
                    break
                seed = trial_seed(case["id"], t)
                s = ScreenSettings(thinking=thinking, sampling=sampling, seed=seed, track=track)
                started = utc_now()
                tr = run_trial(case, client, t, track=track, settings=s)
                v = tr.verdict
                raw = w.put_raw({"schema": "edat-screen-trial/v1", "case_id": case["id"], "trial": t,
                                 "world_seed": tr.bound.world_seed, "track": track,
                                 "renderer": renderer_versions() if track != "ax_text" else None,
                                 "episode": tr.episode.to_record(include_messages=True), "verdict": v.to_dict(),
                                 "core": bool(case.get("core")), "final_state": tr.bound.world.state,
                                 "effects": tr.bound.world.effects, "typed": tr.bound.world.typed})
                rec = record_for(entry=entry, run_id=run_id, board_version=board_version, harness_commit=commit,
                                 case=case, track=track, trial=t, world_seed=tr.bound.world_seed, seed=seed,
                                 verdict=v, raw=raw, started_at=started, selection=selection)
                w.append(rec)
                _append_jsonl(side / "restraint-events.jsonl", restraint_events_for(v, entry, board_version))
                _append_jsonl(side / "perf-samples.jsonl", [perf_sample_for(v, case, entry, t)])
                statuses.append(rec["status"])
                state["written"] += 1
                word = "excluded" if v.excluded else ("PASS" if v.success else f"FAIL {_why_failed(v)}")
                if progress:
                    progress(f"{entry.model_id} {track} {case['id']} t{t}: {word} · {v.steps} steps · "
                             f"{tr.episode.wall_s:.1f}s")
                if (track != "ax_text" and len(statuses) == IMAGE_PROBE_TRIALS
                        and all(x == "excluded" for x in statuses)):
                    state["skipped"] = ("the engine failed the first 3 screenshot trials "
                                        f"({tr.episode.engine_error}); images are probably not accepted")
                    break
            if state["stopped"] or state["skipped"]:
                break
        _finish_attachments(w, side, track, board_version, entry)
    state["recorded"] = len(statuses)
    state["complete"] = not state["stopped"] and not state["skipped"] and len(statuses) >= len(cases) * trials
    return state


def _finish_attachments(w, side: Path, track: str, board_version: str, entry: Entry) -> None:
    recs = _done(Path(w.dir))
    n = sum(1 for r in recs if r["status"] != "excluded")
    key = "11" if track == "ax_text" else "11_screenshot_som"
    files = {
        "restraint-trials.json": json.dumps({"schema": "edat-restraint-trials/v1", "spec": "11", "track": track,
                                             "model_key": entry.key, "board_version": board_version,
                                             "trials": {key: n}}, indent=2) + "\n",
    }
    for name in ("restraint-events.jsonl", "perf-samples.jsonl"):
        p = side / name
        files[name] = p.read_text() if p.exists() else ""
    local = hasattr(w, "close")          # LocalRunWriter: only when edat.results is not importable
    for name, data in files.items():
        if local:
            w.attach(name, data)
        else:
            w.attach(name, data, kind="screen")
    if local:
        w.close({"board_version": board_version, "entry": entry.key, "track": track})


def _why_failed(v) -> str:
    if v.violation:
        return "violation"
    if not v.finished:
        return v.stop or "not finished"
    if v.answer_ok is False:
        return "wrong answer"
    if not v.final_state_ok:
        return "wrong final state"
    if not v.unchanged_ok:
        return "changed what it should not"
    return v.stop or "failed"


# ------------------------------------------------------------------ the whole run

def run(out: Path, *, plan: str, tracks: tuple[str, ...] = TRACKS, only: tuple[str, ...] = (),
        lock: str | None = None, max_minutes: float | None = None, client_kind: str = "omlx",
        base_url: str = BASE_URL, board_version: str = "dev", progress=print, models=None,
        dev: list[dict] | None = None, heldout: list[dict] | None = None) -> dict:
    out = outside_repo(out)
    if plan not in PLANS:
        raise SystemExit(f"unknown plan {plan!r}")
    scripted = client_kind != "omlx"
    case_lists = {t: plan_cases(plan, t, dev=dev, heldout=heldout) for t in tracks}   # held-out refusal first
    if models is None:
        mdir, models = reference_models(only)
    else:
        mdir = None
    if not scripted and harbor_running():
        raise SystemExit("Harbor is running (it shares oMLX): close it first; nothing was changed")
    created = False
    if lock:
        try:
            os.mkdir(lock)
            created = True
        except FileExistsError:
            raise SystemExit(f"{lock} exists: another heavy job is running; not starting") from None
    store = out / plan
    store.mkdir(parents=True, exist_ok=True)
    log = {"plan": plan, "tracks": list(tracks), "started_at": utc_now(), "max_minutes": max_minutes,
           "units": [], "restored": None}
    log_file = store / f"sitting-{log['started_at'].replace(':', '-')}.json"
    stopper = Stopper(out, max_minutes, host=not scripted)
    kept = False
    try:
        device = _device(scripted)
        runtime_version = None if scripted else _omlx_version()
        for m in models:
            why = stopper.why()
            if why:
                log["stopped"] = why
                break
            sha = None
            if not scripted:
                from scripts.productbench import engine_prep
                from edat.sidekick.ceiling.run import engine_check, weights_for
                kept = True                     # set first: a keep that fails half way is still put back
                if engine_prep.main(["keep", m.engine_id, "--save", str(out / "resident-before.json")]) != 0:
                    raise RuntimeError(f"could not make {m.engine_id} the only chat model; stopping")
                problem = engine_check(m, base_url)
                if problem:
                    raise RuntimeError(problem)
                sha = weights_for(out, mdir, m)
            entry, sampling, thinking = entry_for(m, weights_sha256=sha, runtime_version=runtime_version,
                                                  device=device, scripted=scripted)
            client = _client(client_kind, m, base_url)
            for track in tracks:
                row = {"model": m.engine_id, "track": track, "thinking": entry.thinking,
                       "sampling_sent": dict(entry.sampling), "sampling_source": entry.sampling_source}
                log["units"].append(row)
                if track != "ax_text" and m.loader != "mlx-vlm":
                    row["result"] = f"skipped: {m.engine_id} has no vision loader ({m.loader})"
                    continue
                st = run_unit(client(), entry, sampling, thinking, case_lists[track], plan=plan, track=track,
                              store=store, stopper=stopper, board_version=board_version, progress=progress)
                row.update(st, result="done" if st["complete"] else ("skipped" if st["skipped"] else "stopped"))
                _write_json(log_file, log)
                if st["stopped"]:
                    log["stopped"] = st["stopped"]
                    break
            if log.get("stopped"):
                break
    finally:
        if kept:
            from scripts.productbench import engine_prep
            try:
                log["restored"] = engine_prep.main(["restore", str(out / "resident-before.json")]) == 0
            except Exception as exc:  # noqa: BLE001 - report it, still release the lock
                log["restored"] = f"restore failed: {exc.__class__.__name__}: {exc}"
        if created:
            os.rmdir(lock)
        log["finished_at"] = utc_now()
        _write_json(log_file, log)
    if progress:
        for r in log["units"]:
            progress(f"{r['model']} {r['track']}: {r.get('result')}"
                     + (f" ({r.get('stopped') or r.get('skipped')})" if r.get("stopped") or r.get("skipped") else ""))
        if log.get("stopped"):
            progress(f"stopped: {log['stopped']}")
        progress("done" if log["units"] and not log.get("stopped") and all(r.get("result") in ("done",) or
                 str(r.get("result")).startswith("skipped") for r in log["units"]) else "not complete: rerun to resume")
    return log


def _client(kind: str, m, base_url: str):
    if kind == "omlx":
        from edat.models import OmlxClient
        c = OmlxClient(m.engine_id, base_url=base_url, timeout_s=300.0, quantization=m.quant.get("scheme"))
        return lambda: c
    if kind in STUBS:
        return lambda: STUBS[kind]
    raise SystemExit(f"unknown client {kind!r}")


def _device(scripted: bool) -> dict:
    if scripted:
        return {"id": "stub-host", "class": "stub", "ram_bytes": None, "os": None}
    from edat.sidekick.ceiling.run import device_block
    return device_block("mac-ref")


def _omlx_version() -> str | None:
    try:
        from scripts.toolbench.runmeta import omlx_version
        return omlx_version()
    except Exception:  # noqa: BLE001
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


# ------------------------------------------------------------------ estimate

def measured_pace(out: Path) -> dict:
    """Seconds per step, steps per optimal step and exclusions per track, from the smoke step's records."""
    out = Path(out).expanduser()
    opt = {c["id"]: c for c in load_cases(DEV_DIR)}
    pace: dict = {}
    for f in sorted((out / "smoke").glob("runs/*/records.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            tr = (r.get("facets") or {}).get("category") or "ax_text"
            p = pace.setdefault(tr, {"trials": 0, "excluded": 0, "steps": 0, "optimal": 0, "wall_s": 0.0, "passed": 0})
            p["trials"] += 1
            if r["status"] == "excluded":
                p["excluded"] += 1
                continue
            m = r["metrics"]
            p["steps"] += int(m.get("steps") or 0)
            p["optimal"] += int((opt.get(r["case_id"]) or {}).get("optimal", {}).get("steps") or 0)
            p["wall_s"] += float(m.get("wall_s") or 0.0)
            p["passed"] += int(bool(m.get("pass")))
    for p in pace.values():
        p["sec_per_step"] = round(p["wall_s"] / p["steps"], 2) if p["steps"] else None
        p["step_factor"] = round(p["steps"] / p["optimal"], 2) if p["optimal"] else None
    return pace


def estimate(plan: str = "core", *, measured: dict | None = None, models: int = len(REFERENCES),
             dev: list[dict] | None = None, heldout: list[dict] | None = None) -> dict:
    rows = {}
    total = 0.0
    for track in TRACKS:
        cases = plan_cases(plan, track, dev=dev, heldout=heldout)
        m = (measured or {}).get(track) or {}
        sps = m["sec_per_step"] if m.get("sec_per_step") is not None else SEC_PER_STEP[track]
        factor = m["step_factor"] if m.get("step_factor") else STEP_FACTOR
        steps = sum(min(c["max_steps"], math.ceil(factor * c["optimal"]["steps"])) for c in cases) * TRIALS[plan]
        sec = steps * sps + len(cases) * TRIALS[plan] * TRIAL_OVERHEAD_S
        rows[track] = {"cases": len(cases), "trials": len(cases) * TRIALS[plan], "steps": steps,
                       "sec_per_step": sps, "step_factor": factor, "basis": "measured" if m.get("sec_per_step") is not None else "assumed",
                       "hours_per_model": round(sec / 3600, 2)}
        total += sec
    total = total * models + MODEL_CHANGE_S * models
    return {"plan": plan, "models": models, "tracks": rows, "hours_total": round(total / 3600, 2)}


# ------------------------------------------------------------------ preflight

def preflight(out: Path, *, lock: str, engine: bool = False, weights: bool = False, base_url: str = BASE_URL,
              print_=print) -> bool:
    """Read-only checks; nothing is loaded, unloaded or sent to a model."""
    ok = True

    def check(cond: bool, good: str, bad: str, *, fatal: bool = True) -> None:
        nonlocal ok
        print_(("ok    " if cond else ("FAIL  " if fatal else "note  ")) + (good if cond else bad))
        if not cond and fatal:
            ok = False

    try:
        out = outside_repo(out)
        check(True, f"results folder is outside the repository ({out.name})", "")
    except SystemExit as exc:
        check(False, "", str(exc))
    try:
        ho = load_cases(heldout_dir())
        check(True, f"held-out split found ({len(ho)} cases)", "")
    except RuntimeError as exc:
        ho = None
        check(False, "", str(exc))
    for track in TRACKS:
        try:
            n = len(plan_cases("core", track, heldout=ho)) if ho is not None else None
        except RuntimeError:
            n = None
        check(n is not None, f"core {track}: {n} cases x {TRIALS['core']} trials", f"core {track}: cases not loadable")
    check(not Path(lock).exists(), f"heavy-job lock is free ({lock})", f"{lock} exists: another heavy job is running")
    check(not harbor_running(), "Harbor is not running", "Harbor is running: close it before the run")
    free = memory_free_pct()
    check(free is not None and free >= MEMORY_START_PCT, f"free memory {free}%",
          f"free memory {free}% (want {MEMORY_START_PCT}%)")
    probe = out if out.exists() else out.parent
    while not probe.exists():
        probe = probe.parent
    gb = shutil.disk_usage(probe).free / 1e9
    check(gb >= DISK_FREE_GB, f"free disk {gb:.0f} GB", f"free disk {gb:.1f} GB (want {DISK_FREE_GB:g})")
    try:
        mdir, models = reference_models()
    except RuntimeError as exc:
        check(False, "", str(exc))
        return False
    for m in models:
        entry, _, thinking = entry_for(m, weights_sha256=None, runtime_version=None, device={})
        check(True, f"{m.engine_id}: {m.quant.get('scheme')} {m.bits_per_weight} bpw, {m.disk_gb} GB, loader {m.loader}, "
                    f"thinking {'on' if thinking else 'off'} (shipped), sampling {entry.sampling} ({entry.sampling_source})", "")
        check(m.loader == "mlx-vlm", f"{m.engine_id}: has a vision loader (screenshot track can run)",
              f"{m.engine_id}: no vision loader; its screenshot track will be skipped", fatal=False)
        if weights:
            from edat.sidekick.ceiling.run import weights_for
            check(True, f"{m.engine_id}: weights_sha256 {weights_for(out, mdir, m)}", "")
        if engine:
            from edat.sidekick.ceiling.run import engine_check
            problem = engine_check(m, base_url)
            check(problem is None or "other chat models are loaded" in problem,
                  f"{m.engine_id}: the engine lists it" + (f" (note: {problem}; the run unloads them)" if problem else ""),
                  f"{m.engine_id}: {problem}")
    # The harness end to end, with no model: the oracle stub on two cases per track, nothing written.
    try:
        from edat.screen.render.raster import draw  # noqa: F401 - Pillow present
        for track in TRACKS:
            for c in plan_cases("smoke", track)[:2]:
                tr = run_trial(c, STUBS["oracle"], 1, track=track, settings=ScreenSettings(track=track))
                if not tr.verdict.success:
                    raise RuntimeError(f"oracle failed {c['id']} on {track}")
        check(True, "harness dry run: the oracle stub passes 2 smoke cases on each track (no model)", "")
    except Exception as exc:  # noqa: BLE001
        check(False, "", f"harness dry run failed: {exc}")
    return ok


# ------------------------------------------------------------------ report

def report(out: Path, plan: str = "core") -> dict:
    from edat.screen.report import summarise
    out = Path(out).expanduser()
    rows = {}
    for d in sorted((out / plan).glob("runs/*")):
        recs = _done(d)
        verdicts = []
        for r in recs:
            if r["status"] == "excluded" or not r.get("raw"):
                continue
            raw = json.loads((d / r["raw"]["path"]).read_text())
            verdicts.append(raw["verdict"] | {"core": raw.get("core", True)})
        s = summarise(verdicts) if verdicts else {}
        rows[d.name] = {"trials": len(recs), "excluded": sum(1 for r in recs if r["status"] == "excluded"),
                        "passed": sum(1 for r in recs if (r.get("metrics") or {}).get("pass")), "summary": s}
    return rows


# ------------------------------------------------------------------ command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Real runs of the screen-control tests (see edat/screen/RUN_SHEET.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("--out", required=True)
    p.add_argument("--lock", required=True)
    p.add_argument("--engine", action="store_true", help="also ask the engine (GET only) which models it lists")
    p.add_argument("--weights", action="store_true", help="also hash the weights (cached in OUT/weights.json)")
    p.add_argument("--base-url", default=BASE_URL)
    e = sub.add_parser("plan")
    e.add_argument("--plan", default="core", choices=PLANS)
    e.add_argument("--measured", help="OUT of a finished smoke step: re-estimate from its pace")
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True, choices=PLANS)
    r.add_argument("--out", required=True)
    r.add_argument("--lock", required=True)
    r.add_argument("--max-minutes", type=float, required=True)
    r.add_argument("--track", action="append", choices=TRACKS, help="default: both, text first")
    r.add_argument("--only", action="append", default=[], help="an engine id (repeatable); default: both references")
    r.add_argument("--base-url", default=BASE_URL)
    r.add_argument("--board-version", default="dev")
    r.add_argument("--client", default="omlx", help=argparse.SUPPRESS)      # stubs, for tests
    o = sub.add_parser("report")
    o.add_argument("out")
    o.add_argument("--plan", default="core", choices=PLANS)
    a = ap.parse_args(argv)
    if a.cmd == "preflight":
        return 0 if preflight(Path(a.out), lock=a.lock, engine=a.engine, weights=a.weights, base_url=a.base_url) else 1
    if a.cmd == "plan":
        measured = measured_pace(Path(a.measured)) if a.measured else None
        res = estimate(a.plan, measured=measured, models=1 if a.plan == "smoke" else len(REFERENCES))
        if measured is not None:
            res["measured"] = measured
        print(json.dumps(res, indent=1))
        return 0
    if a.cmd == "run":
        tracks = tuple(a.track) if a.track else (("ax_text", "screenshot_som") if a.plan == "core" else TRACKS)
        only = tuple(a.only) or ((REFERENCES[0],) if a.plan == "smoke" else ())
        log = run(Path(a.out), plan=a.plan, tracks=tracks, only=only, lock=a.lock, max_minutes=a.max_minutes,
                  client_kind=a.client, base_url=a.base_url, board_version=a.board_version)
        return 0 if log["units"] and not log.get("stopped") and all(u.get("result") == "done" for u in log["units"]) else 2
    print(json.dumps(report(Path(a.out), a.plan), indent=1, default=str))
    return 0


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        sys.exit(main())
