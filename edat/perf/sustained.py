"""Sustained-use drivers: S1, S2, S3, reload after idle, the time budget (spec 04 §9, §9.3, §9.4).

Every driver takes a :class:`edat.perf.runner.MacRunner` and calls its methods; measured
requests go through ``MacRunner.request`` only, so every sample carries the usual §3.6
fields and statuses. Long waits go through the runner's clock (``runner.i.clock.sleep``),
so the drivers run instantly under ``FakeClock``.

Choices made here (the spec leaves them open)
---------------------------------------------
* **Idle windows** (:func:`idle_window`) are energy windows with no requests. The power
  sampler is switched to 1 Hz for the window and back to 20 Hz after (§9.1). The window
  record carries the §3.8 fields with ``idle_window_s = 0`` (an idle window has no idle
  bracket of its own: its mean power *is* the number), plus ``tag``, ``state``, the
  loaded-model list and the engine footprint at its start and end. When a ``sample_test`` is
  given, one no-request sample is written too (``t_submit_ns``/``t_end_ns`` = the window,
  footprint start and end), so idle windows show up in the samples like every other test.
* **Window ids.** Windows written here use the runner's own id sequence (``w<NNN>-<kind>``,
  the runner's ``_windows`` list). Event windows that need a bracket other than the
  runner's 60 s (the simulated day's shared 30 s gaps) are opened with :func:`begin_window`
  / :func:`end_window`, which set the same runner fields ``open_window`` sets but do not
  sleep. A public ``open_window(kind, idle_s=…, sleep=False)`` on the runner would replace
  these (reported to the integrator).
* **S1**: the gap after each request is 0 inside the run and the cooldown gate runs only
  before the first request (the run is *meant* to heat the Mac; gating it would hide the
  throttle). The whole run is one 20 Hz energy window so per-minute PSTR power can be read.
  ``power_state`` ("ac" | "battery") is taken from the argument, else
  ``runner.conditions_extra["power_source"]``, else ``runner.i.sysmem.power_source``.
* **Replay jobs** (S2 bursts, the simulated day's stand-in jobs): the 6 teacher-forced
  steps run back to back (gap 0) with the prefix cache on and one replay variant per job,
  taken from the runner's shared ``replay_next`` counter so no two jobs in a run share a
  variant. Spec 02 §11.3 says "burst i uses nonce variant i"; the counter gives burst i
  variant ``offset + i`` (offset = replays already run), which keeps the intent (distinct
  variants) without hitting a cache left by an earlier ``agent_replay``. The cooldown gate
  is off inside S2 and the day: the schedule is the experiment.
* **S2**: battery readings go to the side file ``battery-<run_id>.jsonl`` (attachment kind
  ``battery_readings``), one per minute plus one at each hour's end:
  ``{t_ns, phase: "test"|"control", entry_id, mode, minute, level, remaining_Wh, …}``. The
  reader returns a dict with ``level`` (fraction 0–1) and optionally ``remaining_Wh``; the
  default reads the AppleSmartBattery registry (read-only ``ioreg``; keys unverified).
* **S3**: after each state change the runner waits ``settle_s`` (default 60 s) outside the
  window, so the load or unload is not inside it. "Unloaded" unloads every model the engine
  reports, aux models included. The wake request is a 2,048-level request sent right after
  the last (resident) window with the gate off.
* **Reload after idle**: each rep's warm request runs in its own energy window too, so
  ``E_reload`` can be the reload request's active energy minus the warm request's (both at
  2,048): the whole reload request's energy would double-count prefill and decode the day
  composition already has. ``reload_ms`` = TTFA − median warm TTFA at the same level; the
  basis is the run's ``level`` samples at that level when there are any
  (``level_cell_same_run``), else this test's warm requests (``reload_warm_requests``).
  ``weights_cached`` is null when the unload was not confirmed or the disk counter is missing.
* **Time budget**: ``within_budget`` on a task is the time test alone (``wall_s <= budget``);
  ``within_budget_rate`` needs both completed and within budget, over the tasks whose
  completion is known. A kind outside :data:`TIME_BUDGET_S` (after :data:`BUDGET_KIND_ALIASES`)
  gets ``wall_budget_s = None`` and is counted as ``unbudgeted``.
"""
from __future__ import annotations

import contextlib
import json
import os
import statistics
from typing import Any, Callable, Iterator

from . import schedule
from .constants import (IDLE_RATE_HZ, IDLE_UNLOAD_MINUTES, IDLE_WINDOW_S, OK_STATUSES, PSTR_RATE_HZ,
                        RELOAD_REPS, REPLAY_STEPS, S1_MINUTES_MAC, S2_BURST_EVERY_S, S2_MINUTES, S3_MINUTES,
                        SMALL_LEVEL, TIME_BUDGET_S, TIME_BUDGET_SCREEN_S, TIME_BUDGET_SIDEKICK_S)
from .record import blank_sample, latest_per_id, read_jsonl

_NS = 1_000_000_000

# Event kinds (04 §3.12) and other names mapped onto the §9.4 budget keys.
BUDGET_KIND_ALIASES = {"question": "question", "one_tool": "one_tool", "multi_step": "multi_step",
                       "action": "multi_step", "digest": "digest", "brief": "brief", "review": "brief",
                       "mail_digest": "digest", "message_digest": "digest"}


# ----------------------------------------------------------------------------- helpers

def _power(runner):
    if runner.i.power is None:
        raise RuntimeError("energy windows need a power sampler")
    return runner.i.power


def _sleep(runner, seconds: float) -> None:
    if seconds > 0:
        runner.i.clock.sleep(seconds)


def _footprint(runner) -> int | None:
    try:
        return runner.i.monitor.probe.read().footprint
    except (OSError, ProcessLookupError, AttributeError):
        return None


def _loaded(runner) -> list[str] | None:
    try:
        return list(runner.i.client.loaded_models())
    except Exception:  # noqa: BLE001 - an unreachable engine is recorded as unknown
        return None


def power_source(runner, given: str | None = None) -> str | None:
    """"ac" | "battery" from the argument, the run's conditions, or the system probe."""
    return (given or runner.conditions_extra.get("power_source")
            or getattr(runner.i.sysmem, "power_source", None))


@contextlib.contextmanager
def no_gap(runner) -> Iterator[None]:
    """Gap after each request set to 0 for the duration (restored after)."""
    old = runner.cfg.gap_s
    runner.cfg.gap_s = 0.0
    try:
        yield
    finally:
        runner.cfg.gap_s = old


def next_window_id(runner, kind: str) -> str:
    """The next id in the runner's own window sequence (as ``MacRunner.open_window`` makes it)."""
    ws = list(getattr(runner, "_windows", []))
    wid = f"w{len(ws) + 1:03d}-{kind}"
    runner._windows = ws + [wid]
    return wid


def window_record(runner, wid: str, t0: int, t1: int, *, rate_hz: float, idle_window_s: float,
                  sample_ids: list[str], extra: dict | None = None) -> dict:
    p = _power(runner)
    w = {"window_id": wid, "source": p.source, "scope": p.scope, "is_estimate": p.is_estimate,
         "rate_hz": rate_hz, "t_start_ns": t0, "t_end_ns": t1, "idle_before_W": None, "idle_after_W": None,
         "idle_window_s": idle_window_s, "sample_ids": list(sample_ids), "trace_attachment": p.trace_name,
         "energy_total_J": None, "energy_active_J": None}
    if extra:
        w.update(extra)
    return w


def begin_window(runner, kind: str) -> str:
    """Open an energy window **without** the runner's idle sleeps: the caller owns the idle
    bracket (the simulated day's shared gaps, the long session's 5-turn brackets)."""
    _power(runner).set_rate(PSTR_RATE_HZ)
    wid = next_window_id(runner, kind)
    runner._window_id = wid
    runner._win_t0 = runner.now()
    runner.window_samples = []
    return wid


def end_window(runner, *, idle_window_s: float, extra: dict | None = None, write: bool = True,
               t_start_ns: int | None = None, t_end_ns: int | None = None) -> dict:
    t1 = runner.now() if t_end_ns is None else t_end_ns
    t0 = runner._win_t0 if t_start_ns is None else t_start_ns
    w = window_record(runner, runner._window_id, t0, t1, rate_hz=PSTR_RATE_HZ, idle_window_s=idle_window_s,
                      sample_ids=list(runner.window_samples or ()), extra=extra)
    runner.window_samples = None
    if write:
        runner.w.window(w)
    return w


def rewrite(runner, s: dict) -> None:
    """Append a replacement line for a sample whose fields changed after it was written."""
    runner._rewrite_last(s)


def unload_all(runner) -> list[str] | None:
    """Unload the runner's model and anything else the engine reports (aux models too)."""
    runner.unload_current()
    loaded = _loaded(runner) or []
    for m in loaded:
        if m:
            runner.i.client.unload(m)
    return _loaded(runner)


def ttft_s(s: dict) -> float | None:
    a, b = s.get("t_submit_ns"), s.get("t_first_token_ns")
    return (b - a) / _NS if isinstance(a, int) and isinstance(b, int) else None


def ttfa_s(s: dict) -> float | None:
    a, b = s.get("t_submit_ns"), s.get("t_first_answer_ns")
    return (b - a) / _NS if isinstance(a, int) and isinstance(b, int) else None


def _sent(s: dict) -> bool:
    return s.get("t_submit_ns") is not None


def _weights_bytes(entry) -> int:
    return sum(int(f.get("bytes") or 0) for f in entry.raw.get("files") or ()
               if str(f.get("name", "")).endswith(".safetensors"))


# ----------------------------------------------------------------------------- idle window

def idle_window(runner, seconds: float, tag: str, *, rate_hz: float = IDLE_RATE_HZ,
                sample_test: str | None = None, entry=None, mode: str | None = None,
                extra: dict | None = None) -> dict:
    """An energy window with no requests (§4.3, §9.1): sampler at ``rate_hz`` (1 Hz) for the
    window, back to 20 Hz after. Writes the §3.8 window (``tag`` e.g. ``idle_resident``) and,
    with ``sample_test``, one no-request sample for it."""
    p = _power(runner)
    p.set_rate(rate_hz)
    wid = next_window_id(runner, tag)
    fp0, loaded0 = _footprint(runner), _loaded(runner)
    t0 = runner.now()
    _sleep(runner, seconds)
    t1 = runner.now()
    fp1, loaded1 = _footprint(runner), _loaded(runner)
    p.set_rate(PSTR_RATE_HZ)
    sample_ids: list[str] = []
    ex = dict(extra or {})
    if sample_test is not None and entry is not None:
        s = blank_sample(sample_id=runner._sid(), model_id=entry.id, mode=mode, entry_id=entry.entry_id,
                         test=sample_test, phase="measured", round=0, rep=ex.get("rep", 0),
                         order_index=runner.w.next_order(), t_submit_ns=t0, t_end_ns=t1, status="ok",
                         footprint_start_bytes=fp0, footprint_end_bytes=fp1, energy_window_id=wid,
                         power_state=power_source(runner), idle_tag=tag,
                         **{k: v for k, v in ex.items() if k in ("event_kind", "task_id", "script_time_s")})
        if ex.get("state") is not None:
            s["idle_state"] = ex["state"]
        runner.w.sample(s)
        sample_ids.append(s["sample_id"])
    w = window_record(runner, wid, t0, t1, rate_hz=rate_hz, idle_window_s=0.0, sample_ids=sample_ids,
                      extra={"tag": tag, "kind": "idle", "footprint_start_bytes": fp0, "footprint_end_bytes": fp1,
                             "loaded_models_start": loaded0, "loaded_models_end": loaded1,
                             "entry_id": entry.entry_id if entry is not None else None, "mode": mode, **ex})
    runner.w.window(w)
    return w


# ----------------------------------------------------------------------------- replay job

def replay_job(runner, entry, mode: str, *, test: str, rep: int, variant: int | None = None,
               extra: dict | None = None, gate_first: bool = False) -> list[dict]:
    """One teacher-forced 6-step agent replay (02 §11.2) with the prefix cache on: one variant
    for all steps, steps back to back. Returns the step samples."""
    p = runner.prompts[entry.entry_id]
    n = p.replay_variants()
    if variant is None:
        variant = runner.replay_next.get(entry.entry_id, 0)
        runner.replay_next[entry.entry_id] = variant + 1
    v = variant % n
    if variant >= n:
        runner.w.event(runner.now(), "operator_note", f"replay variants wrapped at {entry.entry_id} ({variant})")
    out = []
    with no_gap(runner):
        for step in range(1, REPLAY_STEPS + 1):
            s = runner.request(entry, mode, test=test, phase="measured", prompt=p.replay_step(v, step), level=None,
                               variant=v, rep=rep, step=step, gate=(gate_first and step == 1),
                               extra={"replay_cache": "on", **(extra or {})})
            if s.get("prompt_tokens") is not None:
                s["new_prefill_tokens"] = s["prompt_tokens"] - (s.get("cached_tokens") or 0)
                rewrite(runner, s)
            out.append(s)
            if not _sent(s):
                break
    return out


# ----------------------------------------------------------------------------- S1

def s1_sustained(runner, entry, mode: str, *, minutes: float = S1_MINUTES_MAC, level: int = SMALL_LEVEL,
                 power_source_: str | None = None, in_window: bool = True) -> list[dict]:
    """S1 (§9): back-to-back ``level`` requests for ``minutes`` (test ``sustained``). Gap 0 and
    no cooldown gate inside the run (the gate runs before the first request only). Thermal
    state per request is in each sample; ``power_state`` records AC or battery."""
    p = runner.prompts[entry.entry_id]
    src = power_source(runner, power_source_)
    runner.select(entry, mode)
    win = in_window and runner.i.power is not None
    if win:
        runner.open_window("sustained")
    out: list[dict] = []
    noted = False
    with no_gap(runner):
        t0 = runner.now()
        end = t0 + int(minutes * 60 * _NS)
        i = 0
        while runner.now() < end:
            v, wrapped = runner.pools[entry.entry_id].take(entry.entry_id, level)
            if wrapped and not noted:
                runner.w.event(runner.now(), "operator_note", f"nonce variants wrapped at {entry.entry_id}/{level}")
                noted = True
            s = runner.request(entry, mode, test="sustained", phase="measured", prompt=p.level_prompt(level, v),
                               level=level, variant=v, rep=i + 1, gate=(i == 0), extra={"power_state": src})
            out.append(s)
            i += 1
            if not _sent(s) or s["status"] in ("oom_killed", "killed_unknown"):
                break
        t1 = runner.now()
    wid = runner.close_window()["window_id"] if win else None
    runner.conditions_extra.setdefault("sustained_runs", []).append(
        {"entry_id": entry.entry_id, "mode": mode, "power_source": src, "level": level, "minutes": minutes,
         "t_start_ns": t0, "t_end_ns": t1, "gap_s": 0.0, "cooldown_gate": "first request only",
         "energy_window_id": wid, "n_requests": len(out)})
    return out


# ----------------------------------------------------------------------------- S2

def default_battery_reader() -> dict:
    """AppleSmartBattery through read-only ``ioreg`` (key names unverified, §9.1/§16)."""
    from .power import read_battery
    info = read_battery()
    cur, mx, v = info.get("current_capacity_mAh"), info.get("max_capacity_mAh"), info.get("voltage_mV")
    level = remaining = None
    if isinstance(cur, (int, float)) and isinstance(mx, (int, float)) and mx > 0:
        level = cur / mx if mx > 100 else cur / 100.0
        if cur > 100 and isinstance(v, (int, float)):
            remaining = cur * v / 1e6
    return {"level": level, "remaining_Wh": remaining, "watts": info.get("watts"),
            "external_connected": info.get("external_connected")}


class BatteryLog:
    """Battery accumulator readings to ``battery-<run_id>.jsonl`` (attachment ``battery_readings``)."""

    def __init__(self, runner, reader: Callable[[], dict | float] | None):
        self.runner = runner
        self.reader = reader or default_battery_reader
        self.name = f"battery-{runner.w.run_id}.jsonl"
        self.path = runner.w.dir / self.name
        self.rows: list[dict] = []

    def read(self, phase: str, **ctx: Any) -> dict:
        t = self.runner.now()
        try:
            r = self.reader()
            reading = {"level": float(r)} if isinstance(r, (int, float)) else dict(r)
        except Exception as err:  # noqa: BLE001 - a failed read is recorded, never fatal
            reading = {"level": None, "error": repr(err)}
        row = {"t_ns": t, "phase": phase, **ctx, **reading}
        with open(self.path, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.runner.w.attach(self.name, "battery_readings")
        self.rows.append(row)
        return row


def _hour(runner, log: BatteryLog, phase: str, seconds: float, read_every_s: float, ctx: dict,
          offsets_s: list[float] | None = None, burst: Callable[[int], None] | None = None) -> tuple[int, int]:
    """One period: battery read every ``read_every_s`` (and once at the end), ``burst(i)`` at
    each offset in ``offsets_s`` (seconds from the start). Bursts never start after the end."""
    t0 = runner.now()
    end = t0 + int(seconds * _NS)
    offs = [t0 + int(o * _NS) for o in (offsets_s or ())]
    next_read, minute, b = t0, 0, 0
    while True:
        t = runner.now()
        if t >= next_read and next_read <= end:
            log.read(phase, minute=minute, **ctx)
            minute += 1
            next_read = t0 + int(minute * read_every_s * _NS)
            if next_read > end and t < end:
                next_read = end
        if burst is not None and b < len(offs) and t >= offs[b] and t < end:
            burst(b)
            b += 1
            continue
        if t >= end:
            if log.rows and log.rows[-1]["phase"] == phase and log.rows[-1]["t_ns"] < end:
                log.read(phase, minute=minute, final=True, **ctx)
            break
        nxt = [end] + ([next_read] if next_read <= end else []) + ([offs[b]] if b < len(offs) else [])
        _sleep(runner, (max(min(nxt), t) - t) / _NS)
    return t0, runner.now()


DUTY_CYCLE_SCHEMA = "edat-duty-cycle-v1"


def _hms_s(t: str) -> float:
    parts = [float(x) for x in str(t).split(":")]
    while len(parts) < 3:
        parts.append(0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def load_duty_cycle(src) -> dict:
    """Read 02 §11.3's ``edat-duty-cycle-v1.json``: ``{schema, lang, period_minutes,
    duration_minutes, bursts: [{burst, t: "HH:MM:SS" from the start, nonce_variant,
    nonce_reused, fresh_cache}]}``. Returns ``{duration_s, bursts: [{offset_s, variant,
    nonce_reused, fresh_cache, burst}]}``."""
    if isinstance(src, dict):
        doc = src
    else:
        with open(src, encoding="utf-8") as f:
            doc = json.load(f)
    if doc.get("schema") != DUTY_CYCLE_SCHEMA:
        raise ValueError(f"not an {DUTY_CYCLE_SCHEMA} file: schema {doc.get('schema')!r}")
    if doc.get("lang", "en") != "en":
        raise ValueError(f"duty cycle lang must be 'en', got {doc.get('lang')!r}")
    bursts = [{"burst": b.get("burst", i + 1), "offset_s": _hms_s(b["t"]), "variant": b.get("nonce_variant"),
               "nonce_reused": bool(b.get("nonce_reused")), "fresh_cache": b.get("fresh_cache")}
              for i, b in enumerate(doc.get("bursts") or ())]
    bursts.sort(key=lambda b: b["offset_s"])
    dur = doc.get("duration_minutes")
    return {"duration_s": float(dur) * 60 if dur is not None else None, "bursts": bursts,
            "period_minutes": doc.get("period_minutes")}


def s2_battery(runner, entry, mode: str, *, minutes: float = S2_MINUTES, every_s: float = S2_BURST_EVERY_S,
               read_battery: Callable[[], dict | float] | None = None, read_every_s: float = 60.0,
               control: bool = True, control_minutes: float | None = None, duty_cycle=None) -> dict:
    """S2 (§9, 02 §11.3): one agent-replay burst (cache on, its own variant) every ``every_s``
    for ``minutes`` with the model resident and idle between (test ``battery``), then a matched
    control period with the model unloaded. The battery is read once a minute.

    With ``duty_cycle`` (a path or the parsed ``edat-duty-cycle-v1`` document) the bursts,
    their offsets, their nonce variants and the period length come from that file. A burst
    the file marks ``nonce_reused`` asks for a fresh cache, which the runner cannot give
    without a reload inside the measured hour: it runs as is, an ``operator_note`` says so,
    and step 1's ``cached_tokens`` shows whether the cache hit."""
    log = BatteryLog(runner, read_battery)
    ctx = {"entry_id": entry.entry_id, "mode": mode}
    runner.select(entry, mode)
    if duty_cycle is not None:
        dc = load_duty_cycle(duty_cycle)
        plan = dc["bursts"]
        seconds = dc["duration_s"] or minutes * 60
    else:
        plan = [{"burst": i + 1, "offset_s": i * every_s, "variant": None, "nonce_reused": False}
                for i in range(int(minutes * 60 // every_s))]
        seconds = minutes * 60
    samples: list[dict] = []

    def burst(i: int) -> None:
        b = plan[i]
        if b.get("nonce_reused"):
            runner.w.event(runner.now(), "operator_note",
                           f"s2 burst {b['burst']} reuses nonce variant {b['variant']}; fresh cache not possible "
                           "without a reload, cached_tokens records any hit")
        samples.extend(replay_job(runner, entry, mode, test="battery", rep=b["burst"], variant=b.get("variant"),
                                  extra={"power_state": power_source(runner),
                                         "nonce_reused": b.get("nonce_reused") or None}))

    runner.w.event(runner.now(), "operator_note", f"s2 test period start {entry.entry_id}")
    t0, t1 = _hour(runner, log, "test", seconds, read_every_s, ctx, [b["offset_s"] for b in plan], burst)
    rec = {"entry_id": entry.entry_id, "mode": mode, "t_test_start_ns": t0, "t_test_end_ns": t1,
           "bursts": len(plan), "every_s": every_s if duty_cycle is None else None,
           "duty_cycle": duty_cycle is not None, "battery_log": log.name, "t_control_start_ns": None,
           "t_control_end_ns": None, "control_loaded_models": None}
    if control:
        rec["control_loaded_models"] = unload_all(runner)
        runner.w.event(runner.now(), "operator_note", f"s2 control period start {entry.entry_id}")
        c0, c1 = _hour(runner, log, "control", (control_minutes * 60 if control_minutes else seconds),
                       read_every_s, ctx)
        rec.update({"t_control_start_ns": c0, "t_control_end_ns": c1})
    runner.conditions_extra.setdefault("s2_runs", []).append(rec)
    rec = dict(rec)
    rec["samples"] = samples
    rec["readings"] = log.rows
    return rec


# ----------------------------------------------------------------------------- S3

def s3_idle_residency(runner, entry, mode: str, *, minutes: float = S3_MINUTES, settle_s: float = IDLE_WINDOW_S,
                      wake_level: int = SMALL_LEVEL) -> dict:
    """S3 (§9): ``minutes`` loaded-idle vs unloaded windows in ABBA order (resident, unloaded,
    unloaded, resident), footprint at each window's start and end, then one wake request."""
    windows = []
    for k, state in enumerate(schedule.abba("resident", "unloaded")):
        if state == "resident":
            runner.select(entry, mode)
        else:
            unload_all(runner)
        _sleep(runner, settle_s)
        w = idle_window(runner, minutes * 60, f"idle_{state}", sample_test="idle_resident", entry=entry, mode=mode,
                        extra={"purpose": "s3", "state": state, "abba_index": k})
        if state == "resident" and w["loaded_models_end"] is not None \
                and entry.engine_id(mode) not in w["loaded_models_end"]:
            runner.w.event(runner.now(), "engine_unload", {"window_id": w["window_id"], "during": "resident idle"})
            runner.current = None
        windows.append(w)
    p = runner.prompts[entry.entry_id]
    v, _ = runner.pools[entry.entry_id].take(entry.entry_id, wake_level)
    wake = runner.request(entry, mode, test="wake", phase="measured", prompt=p.level_prompt(wake_level, v),
                          level=wake_level, variant=v, rep=1, gate=False)
    return {"windows": windows, "wake": wake}


# ----------------------------------------------------------------------------- reload after idle (§9.3)

def _warm_basis(runner, entry, mode: str, level: int, warm: list[dict]) -> tuple[list[float], str]:
    lv = [ttfa_s(s) for s in latest_per_id(read_jsonl(runner.w.samples_path))
          if s.get("test") == "level" and s.get("phase") == "measured" and s.get("entry_id") == entry.entry_id
          and s.get("mode") == mode and s.get("level_tokens") == level and s.get("status") in OK_STATUSES
          and not (s.get("cached_tokens") or 0)]
    lv = [x for x in lv if x is not None]
    if lv:
        return lv, "level_cell_same_run"
    return [x for x in (ttfa_s(s) for s in warm if s.get("status") in OK_STATUSES) if x is not None], \
        "reload_warm_requests"


def reload_after_idle(runner, entry, mode: str, *, reps: int = RELOAD_REPS,
                      idle_minutes: float = IDLE_UNLOAD_MINUTES + 1, level: int = SMALL_LEVEL,
                      in_window: bool = True) -> list[dict]:
    """§9.3: load, one warm request, idle ``idle_minutes`` (inside an idle energy window),
    confirm the engine unloaded the model, then a ``level`` request inside an energy window
    (test ``reload_after_idle``). Returns the reload samples (warm ones are phase ``warmup``)."""
    p = runner.prompts[entry.entry_id]
    eng = entry.engine_id(mode)
    weights = _weights_bytes(entry)
    win = in_window and runner.i.power is not None
    warm, out = [], []
    for r in range(1, reps + 1):
        runner.select(entry, mode)
        if win:
            runner.open_window("reload_warm")
        v, _ = runner.pools[entry.entry_id].take(entry.entry_id, level)
        warm.append(runner.request(entry, mode, test="reload_after_idle", phase="warmup",
                                   prompt=p.level_prompt(level, v), level=level, variant=v, rep=r,
                                   extra={"reloaded": False, "reload_role": "warm"}))
        if win:
            runner.close_window()
        if runner.i.power is not None:
            idle_window(runner, idle_minutes * 60, "reload_idle", entry=entry, mode=mode,
                        extra={"purpose": "reload_idle", "rep": r})
        else:
            _sleep(runner, idle_minutes * 60)
        loaded = _loaded(runner)
        unloaded = loaded is not None and eng not in loaded
        runner.w.event(runner.now(), "engine_unload", {"engine_id": eng, "confirmed": unloaded, "loaded": loaded,
                                                       "after_idle_s": idle_minutes * 60})
        if win:
            runner.open_window("reload")
        d0 = runner.i.disk_read_mb()
        v, _ = runner.pools[entry.entry_id].take(entry.entry_id, level)
        s = runner.request(entry, mode, test="reload_after_idle", phase="measured", prompt=p.level_prompt(level, v),
                           level=level, variant=v, rep=r, gate=False,
                           extra={"reloaded": unloaded, "reload_role": "reload"})
        d1 = runner.i.disk_read_mb()
        read = int((d1 - d0) * (1 << 20)) if d0 is not None and d1 is not None else None
        s["weights_cached"] = (read < 0.1 * weights) if (unloaded and read is not None and weights > 0) else None
        s["load"] = {"disk_read_bytes": read, "weights_bytes": weights or None}
        rewrite(runner, s)
        if win:
            runner.close_window()
        out.append(s)
    vals, basis = _warm_basis(runner, entry, mode, level, warm)
    med = statistics.median(vals) if vals else None
    for s in out:
        t = ttfa_s(s)
        s["reload_ms"] = (t - med) * 1000.0 if (t is not None and med is not None and s.get("reloaded")) else None
        s["reload_ms_basis"] = {"basis": basis, "median_warm_ttfa_s": med, "n": len(vals), "level": level}
        rewrite(runner, s)
    return out


# ----------------------------------------------------------------------------- time budget (§9.4)

def budget_kind(kind: str | None) -> str | None:
    return BUDGET_KIND_ALIASES.get(kind or "")


def task_budget(t: dict) -> tuple[str, str | None, float | None]:
    """(test, budget key, budget seconds) for one task row.

    ``test: "screen_task"`` is budgeted by ``level`` (L1–L5, 11 §3.10), ``test: "sidekick_task"``
    by ``mode`` (answer_only | tool_mode, 12 SK-25); anything else by ``kind`` through
    :data:`BUDGET_KIND_ALIASES` onto the §9.4 table (test ``agentic`` when none is given)."""
    test = t.get("test") or "agentic"
    if test == "screen_task":
        key = str(t.get("level")) if t.get("level") is not None else None
        if key is not None and not key.upper().startswith("L"):
            key = f"L{key}"
        key = key.upper() if key else None
        return test, key, TIME_BUDGET_SCREEN_S.get(key) if key else None
    if test == "sidekick_task":
        key = t.get("mode")
        return test, key, TIME_BUDGET_SIDEKICK_S.get(key) if key else None
    key = budget_kind(t.get("kind"))
    return test, key, TIME_BUDGET_S.get(key) if key else None


def _rate_block() -> dict:
    return {"n": 0, "known": 0, "hits": 0, "walls": [], "unbudgeted": 0}


def _finish_rate(b: dict) -> dict:
    return {"within_budget_rate": b["hits"] / b["known"] if b["known"] else None,
            "median_wall_s_correct": statistics.median(b["walls"]) if b["walls"] else None,
            "n_tasks": b["n"], "n_completion_known": b["known"], "n_completed_within_budget": b["hits"],
            "unbudgeted": b["unbudgeted"]}


def time_budget(tasks: list[dict]) -> dict:
    """§9.4 (plus 11 §3.10 and 12 SK-25). ``tasks`` = ``[{task_id, kind, completed, wall_s}]``,
    or rows with ``test: "screen_task"`` + ``level`` or ``test: "sidekick_task"`` + ``mode``.
    Sets ``wall_budget_s`` and ``within_budget`` (wall_s <= budget) on each task.
    ``within_budget_rate`` = completed AND within budget, over the tasks whose completion is
    known (None when none is); reported overall, per test value (``by_test``) and per budget
    key (``per_kind``)."""
    per_kind: dict[str, dict] = {}
    overall = _rate_block()
    by_test: dict[str, dict] = {}
    for t in tasks:
        test, key, budget = task_budget(t)
        wall = t.get("wall_s")
        t["wall_budget_s"] = budget
        t["within_budget"] = (wall <= budget) if (budget is not None and wall is not None) else None
        row = per_kind.setdefault(f"{test}:{key}" if test != "agentic" else (key or "unbudgeted"),
                                  {"n": 0, "completed": 0, "within_budget": 0, "completed_within_budget": 0,
                                   "budget_s": budget, "test": test})
        row["n"] += 1
        for b in (overall, by_test.setdefault(test, _rate_block())):
            b["n"] += 1
            if budget is None:
                b["unbudgeted"] += 1
            if t.get("completed") and wall is not None:
                b["walls"].append(wall)
            if t.get("completed") is not None:
                b["known"] += 1
                if t.get("completed") and t["within_budget"]:
                    b["hits"] += 1
        if t.get("completed"):
            row["completed"] += 1
        if t["within_budget"]:
            row["within_budget"] += 1
        if t.get("completed") and t["within_budget"]:
            row["completed_within_budget"] += 1
    out = _finish_rate(overall)
    out.update({"per_kind": per_kind, "by_test": {k: _finish_rate(b) for k, b in by_test.items()},
                "budgets_s": {"agentic": dict(TIME_BUDGET_S), "screen_task": dict(TIME_BUDGET_SCREEN_S),
                              "sidekick_task": dict(TIME_BUDGET_SIDEKICK_S)},
                "budget_scope": "mac-ref"})
    return out


def task_sample(row: dict, *, sample_id: str, model_id: str | None = None, mode: str | None = None,
                entry_id: str | None = None, order_index: int | None = None, rep: int = 0) -> dict:
    """A timed task row (screen-control, sidekick, or an agentic task) as a §3.6 sample, so the
    chats that run those tasks can write them through ``RunWriter.sample``. The budget is
    set first (:func:`time_budget` on the one row) if the row does not carry one. ``mode`` is
    the model's run mode (as_shipped | plain); a sidekick row's own ``mode`` is its budget key
    and is kept as ``sidekick_mode``."""
    t0, t1 = row.get("t_submit_ns"), row.get("t_end_ns")
    if row.get("wall_s") is None and isinstance(t0, int) and isinstance(t1, int):
        row["wall_s"] = (t1 - t0) / _NS
    wall = row.get("wall_s")
    if "wall_budget_s" not in row:
        time_budget([row])
    test = row.get("test")
    if not test:
        raise ValueError("task_sample needs the row's test (screen_task, sidekick_task or a §3.12 test)")
    s = blank_sample(sample_id=sample_id, model_id=model_id, mode=mode, entry_id=entry_id, test=test,
                     phase="measured", round=0, rep=rep, order_index=order_index, task_id=row.get("task_id"),
                     t_submit_ns=t0, t_end_ns=t1, wall_budget_s=row.get("wall_budget_s"),
                     within_budget=row.get("within_budget"), completed=row.get("completed"),
                     status=row.get("status") or "ok", wall_s=wall)
    if test == "screen_task":
        s["screen_level"] = row.get("level")
    if test == "sidekick_task":
        s["sidekick_mode"] = row.get("mode")
    if row.get("kind") is not None:
        s["task_kind"] = row.get("kind")
    return s
