"""The Mac runner: raw samples against the benchmark oMLX instance (spec 04 §5–§7, §11).

It measures and records; it never summarises. Every request becomes one §3.6 sample
written the moment it ends, and every failure is a sample with a status (§5.3:
failures are results). Statistics, ratios and verdicts come later from the aggregator.

Tests (each a method of :class:`MacRunner`):

- ``levels``          §6: each level, 1 warm-up + 10 (5 at 32k/64k) measured, rounds
                      alternating ascending/descending, fresh nonce per request.
- ``first_token``     §6.3: the first request after a load, alone; also gives ``mem_loaded``.
- ``load``            §7: warm (after an unload) and cold (after the helper's ``purge``,
                      with the ``iostat`` cold check), alternating, time to ready and to
                      first token possible.
- ``energy_pairs``    §4.2: shapes A (L,1) and B (L,257) alternating, K each, inside one
                      energy window with 60 s idle before and after (§4.3).
- ``agent_replay``    §6.3: the 6-step trajectory with the prefix cache on and defeated.
- ``fit16``           §5.5: the emulation run through the admin helper.
- ``fit16_under_load``, ``pressure_sweep``  §5.6: with the ``edat-app-load`` process.

Before every request the cooldown gate runs and after it a fixed gap (§2.6, §6.4).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from . import schedule
from .client import EngineError, OmlxPerfClient, StreamResult
from .constants import (BALLOON_GIB, EDAT_16GB_WIRED_MB, EMULATION_REPS, ENERGY_PAIR_K, FIRST_TOKEN_PROMPT_TOKENS,
                        GAP_AFTER_SAMPLE_S, IDLE_WINDOW_S, LEVELS, MEASURED_REPS, MEASURED_REPS_LONG,
                        MEMORY_PRESSURE_BYTES, OUTPUT_TOKENS, PSTR_RATE_HZ, REPLAY_STEPS, SHAPE_A_TOKENS,
                        SHAPE_B_TOKENS, SHAPE_C_TOKENS, SMALL_LEVEL, SOURCE_SCOPE, WORKING_LEVEL)
from .manifest import ModelEntry
from .memory import FootprintMonitor, delta
from .record import RunWriter, blank_sample


@dataclass
class RunConfig:
    output_tokens: int = OUTPUT_TOKENS
    reps: int = MEASURED_REPS
    reps_long: int = MEASURED_REPS_LONG
    gap_s: float = GAP_AFTER_SAMPLE_S
    cooldown_max_s: float = 300.0
    cooldown_poll_s: float = 5.0
    seed: int = 1234
    idle_window_s: float = IDLE_WINDOW_S
    wait_engine_s: float = 0.0          # how long to wait for the engine to come back after a death
    record_token_times: bool = True
    check_foreign: bool = True


@dataclass
class PowerControl:
    """The runner's handle on the power sampler (``edat.perf.power.PowerSampler``)."""
    source: str = "smc_pstr"
    trace_name: str | None = None
    set_rate: Callable[[float], None] = lambda hz: None
    is_estimate: bool = False

    @property
    def scope(self) -> str:
        return SOURCE_SCOPE[self.source]


@dataclass
class Instruments:
    clock: Any                                   # SystemClock or FakeClock
    client: OmlxPerfClient
    monitor: FootprintMonitor
    sysmem: Any                                  # memory.SystemMemory or testing.FakeSystem
    thermal: Callable[[], str]
    probe_alive: Callable[[], bool]
    engine_pid: int | None = None
    power: PowerControl | None = None
    admin: Any = None                            # admin_client.AdminClient / NullAdminClient
    disk_read_mb: Callable[[], float | None] = lambda: None
    purge_fake: Callable[[], None] | None = None


@dataclass
class _ModelState:
    dead_at_level: int | None = None              # a confirmed kill: higher levels are not run
    deaths: dict = field(default_factory=dict)    # level -> count


class MacRunner:
    def __init__(self, writer: RunWriter, inst: Instruments, entries: list[ModelEntry],
                 prompts: dict[str, Any], cfg: RunConfig | None = None):
        self.w = writer
        self.i = inst
        self.entries = {e.entry_id: e for e in entries}
        self.prompts = prompts                     # entry_id -> prompt source
        self.cfg = cfg or RunConfig()
        self.current: str | None = None            # engine id currently loaded by us
        self.state: dict[tuple, _ModelState] = {}
        self.pools = {eid: schedule.VariantPool(p.n_variants) for eid, p in prompts.items()}
        self.replay_next: dict[str, int] = {}
        self.models_used: dict[tuple, dict] = {}
        self.window_samples: list[str] | None = None
        self.last_thermal = None
        self.conditions_extra: dict = {}

    # ------------------------------------------------------------------ helpers
    def now(self) -> int:
        return self.i.clock.now_ns()

    def _st(self, e: ModelEntry, mode: str) -> _ModelState:
        return self.state.setdefault((e.entry_id, mode), _ModelState())

    def _use(self, e: ModelEntry, mode: str) -> None:
        self.models_used[(e.entry_id, mode)] = e.record(mode)

    def select(self, e: ModelEntry, mode: str) -> tuple[int, int] | None:
        """Load exactly this model in this mode (exclusive, §5.7 rule 1). Returns load timing
        when a load happened."""
        eng = e.engine_id(mode)
        self._use(e, mode)
        if self.current == eng:
            return None
        self.i.client.ensure_only(eng)
        t = self.i.client.load(eng)
        self.current = eng
        return t

    def unload_current(self) -> None:
        if self.current:
            self.i.client.unload(self.current)
            self.current = None

    def _thermal(self) -> str:
        s = self.i.thermal()
        if self.last_thermal is not None and s != self.last_thermal:
            self.w.event(self.now(), "thermal_change", f"{self.last_thermal}->{s}")
        self.last_thermal = s
        return s

    def _gate(self) -> tuple[float, bool, str]:
        waited, timed_out, state0 = schedule.cooldown_gate(
            self._thermal, self.i.clock.sleep, lambda: self.now() / 1e9,
            max_wait_s=self.cfg.cooldown_max_s, poll_s=self.cfg.cooldown_poll_s)
        if timed_out:
            self.w.event(self.now(), "cooldown_timeout", f"waited {waited:.0f}s, state {self._thermal()}")
        return waited, timed_out, state0

    def _foreign(self) -> bool:
        if not self.cfg.check_foreign:
            return False
        a = self.i.client.activity()
        busy = int(a.get("active_requests") or 0) + int(a.get("waiting_requests") or 0)
        if busy > 0:
            self.w.event(self.now(), "foreign_request", {"active": a.get("active_requests"),
                                                         "waiting": a.get("waiting_requests")})
        return busy > 0

    def _sid(self) -> str:
        return f"s{len(self.w._ids) + 1:05d}"

    # ------------------------------------------------------------------ one request
    def request(self, e: ModelEntry, mode: str, *, test: str, phase: str, prompt: dict, level: int | None,
                variant: int | None, round_: int = 0, rep: int = 0, max_tokens: int | None = None,
                shape: str | None = None, step: int | None = None, gate: bool = True,
                extra: dict | None = None, force_length: bool = True) -> dict:
        max_tokens = max_tokens or self.cfg.output_tokens
        s = blank_sample(sample_id=self._sid(), model_id=e.id, mode=mode, entry_id=e.entry_id, test=test,
                         phase=phase, round=round_, rep=rep, order_index=self.w.next_order(),
                         level_tokens=level, variant=variant, shape=shape, step=step, status="ok",
                         energy_window_id=self.window_id if self.window_samples is not None else None)
        if extra:
            s.update(extra)
        st = self._st(e, mode)
        self._use(e, mode)
        n_prompt = prompt.get("tokens")
        # Statuses decided before sending (§5.3).
        if level is not None and st.dead_at_level is not None and level >= st.dead_at_level:
            s["status"] = "not_run_above_oom"
            return self.w.sample(s)
        win = e.native_window_tokens
        if win is not None and n_prompt is not None and n_prompt + max_tokens > win:
            if e.extension_factor:
                s["status"] = "ok_extended"
            else:
                s["status"] = "exceeds_native_window"
                s["error"] = f"{n_prompt}+{max_tokens} > native window {win}"
                return self.w.sample(s)
        prof = e.profile_window_tokens
        if prof is not None and n_prompt is not None and n_prompt + max_tokens > prof:
            s["status"] = "engine_refused"
            s["error"] = f"beyond the benchmark engine profile window {prof}"
            return self.w.sample(s)
        try:
            self.select(e, mode)
        except EngineError as err:
            s["status"], s["error"] = err.status, str(err)
            return self.w.sample(s)
        if gate:
            waited, timed_out, state0 = self._gate()
            s["cooldown_wait_s"], s["thermal_not_nominal"] = waited, timed_out
        s["thermal_start"] = self._thermal()
        foreign_before = self._foreign()
        c0 = self.i.sysmem.counters()
        mem = self.i.monitor.begin(s["sample_id"])
        res = self.i.client.generate(e.engine_id(mode), text=prompt.get("text"), messages=prompt.get("messages"),
                                     max_tokens=max_tokens, seed=self.cfg.seed, force_length=force_length,
                                     thinking=e.thinking == "on")
        mem = self.i.monitor.end(mem, t_first_ns=res.t_first_token_ns, t_last_ns=res.t_last_token_ns)
        c1 = self.i.sysmem.counters()
        foreign_after = self._foreign()
        s["thermal_end"] = self._thermal()
        self._fill(s, res, mem)
        s["swap_delta_bytes"] = delta(c0.swap_used, c1.swap_used)
        s["compressed_delta_bytes"] = delta(c0.compressed, c1.compressed)
        if self.i.engine_pid and e.raw.get("uses_ane"):
            s["neural_bytes"] = self.i.sysmem.neural_bytes(self.i.engine_pid)
        self._status(s, res, n_prompt, foreign_before or foreign_after, mem, e, mode, level)
        if self.window_samples is not None:
            self.window_samples.append(s["sample_id"])
        out = self.w.sample(s)
        if s["status"] in ("killed_unknown", "oom_killed") and self._recover():
            # Retry once (§2.8 rule 5, applied to an engine death on the Mac, §5.3).
            again = self.request(e, mode, test=test, phase=phase, prompt=prompt, level=level, variant=variant,
                                 round_=round_, rep=rep, max_tokens=max_tokens, shape=shape, step=step,
                                 gate=gate, extra=extra, force_length=force_length)
            if again["status"] in ("killed_unknown", "oom_killed") and level is not None:
                st.dead_at_level = level
            return again
        if s["status"] in ("killed_unknown", "oom_killed") and level is not None:
            st.dead_at_level = level
        if self.cfg.gap_s:
            self.i.clock.sleep(self.cfg.gap_s)
        return out

    def _fill(self, s: dict, res: StreamResult, mem) -> None:
        s.update({"t_submit_ns": res.t_submit_ns, "t_first_token_ns": res.t_first_token_ns,
                  "t_first_answer_ns": res.t_first_answer_ns, "t_last_token_ns": res.t_last_token_ns,
                  "t_end_ns": res.t_end_ns, "prompt_tokens": res.prompt_tokens, "cached_tokens": res.cached_tokens,
                  "output_tokens": res.completion_tokens, "reasoning_tokens": res.reasoning_tokens,
                  "finish_reason": res.finish_reason, "server_timings": res.server_timings or None})
        if self.cfg.record_token_times:
            s["token_times_ns"] = res.token_times_rel_ns()
            s["token_times_kind"] = "chunk"
        st = res.server_timings
        for key in ("prompt_time", "prefill_time", "prefill_duration"):
            if isinstance(st.get(key), (int, float)) and res.t_submit_ns is not None:
                s["t_prefill_done_ns"] = res.t_submit_ns + int(st[key] * 1e9)
                s["prefill_timing_source"] = f"server:{key}"
                break
        s["drafted_tokens"] = st.get("drafted_tokens", st.get("draft_tokens"))
        s["accepted_tokens"] = st.get("accepted_tokens", st.get("accepted_draft_tokens"))
        s.update(mem.as_fields())

    def _status(self, s: dict, res: StreamResult, n_prompt: int | None, foreign: bool, mem, e, mode, level) -> None:
        if res.error_status:
            s["status"], s["error"] = res.error_status, res.error
            if res.error_status == "killed_unknown" and not self.i.probe_alive():
                pressure = max(s.get("swap_delta_bytes") or 0, s.get("compressed_delta_bytes") or 0)
                s["status"] = "oom_killed" if pressure > MEMORY_PRESSURE_BYTES else "killed_unknown"
                s["kill_evidence"] = {"last_footprint_bytes": mem.tail[-1][1] if mem.tail else None,
                                      "last_available_bytes": None, "warning": None,
                                      "tail": [[t, fp, None] for t, fp in mem.tail]}
                self.current = None
            return
        if foreign:
            s["status"] = "voided_foreign_work"
            return
        if isinstance(n_prompt, int) and isinstance(res.prompt_tokens, int):
            if res.prompt_tokens < n_prompt:
                s["status"], s["error"] = "truncated", f"engine held {res.prompt_tokens} of {n_prompt} tokens"
                return
            if res.prompt_tokens != n_prompt:
                s["error"] = f"prompt_length_mismatch: sent {n_prompt}, engine counted {res.prompt_tokens}"
        growth = (s.get("swap_delta_bytes") or 0) + (s.get("compressed_delta_bytes") or 0)
        if growth > MEMORY_PRESSURE_BYTES and s["status"] == "ok":
            s["status"] = "memory_pressure"

    def _recover(self) -> bool:
        """After an engine death: wait (up to ``wait_engine_s``) for the operator's engine to come
        back. The runner never starts or restarts oMLX itself."""
        self.w.event(self.now(), "relaunch_after_kill", "engine process gone")
        waited = 0.0
        while waited < self.cfg.wait_engine_s:
            try:
                self.i.client.loaded_models()
                if self.i.probe_alive():
                    self.current = None
                    return True
            except EngineError:
                pass
            self.i.clock.sleep(5.0)
            waited += 5.0
        return False

    # ------------------------------------------------------------------ tests
    def levels(self, e: ModelEntry, mode: str, levels: tuple[int, ...] | list[int] = LEVELS, *,
               reps: int | None = None, reps_long: int | None = None, test: str = "level",
               extra: dict | None = None) -> list[dict]:
        p = self.prompts[e.entry_id]
        have = set(p.level_tokens())
        out = []
        for slot in schedule.level_rounds([L for L in levels if L in have], reps=reps or self.cfg.reps,
                                          reps_long=reps_long or self.cfg.reps_long):
            v, wrapped = self.pools[e.entry_id].take(e.entry_id, slot.level)
            if wrapped:
                self.w.event(self.now(), "operator_note", f"nonce variants wrapped at {e.entry_id}/{slot.level}")
            out.append(self.request(e, mode, test=test, phase=slot.phase, prompt=p.level_prompt(slot.level, v),
                                    level=slot.level, variant=v, round_=slot.round, rep=slot.rep, extra=extra))
        for L in levels:
            if L not in have:
                s = blank_sample(sample_id=self._sid(), model_id=e.id, mode=mode, entry_id=e.entry_id, test=test,
                                 phase="measured", round=0, rep=0, order_index=self.w.next_order(),
                                 level_tokens=L, status="unsupported_by_runtime",
                                 error="level missing from the prompt pack")
                out.append(self.w.sample(s))
        return out

    def first_token(self, e: ModelEntry, mode: str, *, phase: str = "measured") -> dict:
        """The first request after a fresh load, recorded alone (§6.3); its end footprint is
        ``mem_loaded`` (§5.2)."""
        self.unload_current()
        self.select(e, mode)
        return self.request(e, mode, test="first_token", phase=phase,
                            prompt=self.prompts[e.entry_id].first_token_prompt(),
                            level=None, variant=None, gate=False, max_tokens=self.cfg.output_tokens)

    def load(self, e: ModelEntry, mode: str, *, warm_reps: int = 5, cold_reps: int = 5) -> list[dict]:
        """Warm and cold loads alternating (§7). Cold needs the admin helper's ``purge``."""
        out = []
        weights = sum(int(f.get("bytes") or 0) for f in e.raw.get("files") or ()
                      if str(f.get("name", "")).endswith(".safetensors"))
        order = []
        for i in range(max(warm_reps, cold_reps)):
            if i < cold_reps:
                order.append(("load_cold", i + 1))
            if i < warm_reps:
                order.append(("load_warm", i + 1))
        for test, rep in order:
            self.unload_current()
            if test == "load_cold":
                try:
                    if self.i.purge_fake:
                        self.i.purge_fake()
                    else:
                        self.i.admin.purge()
                    self.w.event(self.now(), "admin_action", "purge")
                except Exception as err:  # noqa: BLE001 - recorded, the rep is not cold
                    s = blank_sample(sample_id=self._sid(), model_id=e.id, mode=mode, entry_id=e.entry_id,
                                     test=test, phase="measured", round=0, rep=rep, order_index=self.w.next_order(),
                                     status="unsupported_by_runtime", error=f"purge unavailable: {err}")
                    out.append(self.w.sample(s))
                    continue
            self._gate()
            disk0 = self.i.disk_read_mb()
            eng = e.engine_id(mode)
            self.i.client.ensure_only(eng)
            try:
                t0, t1 = self.i.client.load(eng)
            except EngineError as err:
                s = blank_sample(sample_id=self._sid(), model_id=e.id, mode=mode, entry_id=e.entry_id, test=test,
                                 phase="measured", round=0, rep=rep, order_index=self.w.next_order(),
                                 status="oom_killed_at_load" if err.status == "killed_unknown" else err.status,
                                 error=str(err))
                out.append(self.w.sample(s))
                continue
            self.current = eng
            self._use(e, mode)
            ft = self.request(e, mode, test=test, phase="measured", prompt=self.prompts[e.entry_id].first_token_prompt(),
                              level=None, variant=None, rep=rep, gate=False, max_tokens=1, force_length=False)
            disk1 = self.i.disk_read_mb()
            read = int((disk1 - disk0) * (1 << 20)) if disk0 is not None and disk1 is not None else None
            ft["load"] = {"t_load_start_ns": t0, "t_ready_ns": t1, "t_first_token_possible_ns": ft["t_first_token_ns"],
                          "cold_verified": (read is not None and weights > 0 and read >= 0.9 * weights)
                          if test == "load_cold" else None,
                          "disk_read_bytes": read}
            self._rewrite_last(ft)
            out.append(ft)
        return out

    def _rewrite_last(self, s: dict) -> None:
        """Samples are append-only; a field known only after the sample line was written
        (the load block) is appended as a replacement line with the same sample_id, which
        :func:`record.read_jsonl`'s consumer resolves by keeping the last line per id."""
        with open(self.w.samples_path, "a") as f:
            f.write(json.dumps(s, separators=(",", ":")) + "\n")

    # ---- energy
    @property
    def window_id(self) -> str:
        return self._window_id

    def open_window(self, kind: str) -> str:
        if self.i.power is None:
            raise RuntimeError("energy windows need a power sampler")
        self._window_id = f"w{len(getattr(self, '_windows', [])) + 1:03d}-{kind}"
        self._windows = getattr(self, "_windows", []) + [self._window_id]
        self.i.power.set_rate(PSTR_RATE_HZ)
        self.i.clock.sleep(self.cfg.idle_window_s)          # idle before (§4.3)
        self._win_t0 = self.now()
        self.window_samples = []
        return self._window_id

    def close_window(self) -> dict:
        t1 = self.now()
        self.i.clock.sleep(self.cfg.idle_window_s)          # idle after (§4.3)
        p = self.i.power
        w = {"window_id": self._window_id, "source": p.source, "scope": p.scope, "is_estimate": p.is_estimate,
             "rate_hz": PSTR_RATE_HZ, "t_start_ns": self._win_t0, "t_end_ns": t1,
             "idle_before_W": None, "idle_after_W": None, "idle_window_s": self.cfg.idle_window_s,
             "sample_ids": list(self.window_samples or ()), "trace_attachment": p.trace_name,
             "energy_total_J": None, "energy_active_J": None}
        self.w.window(w)
        self.window_samples = None
        return w

    def energy_pairs(self, e: ModelEntry, mode: str, level: int, *, k: int | None = None,
                     with_c: bool = False) -> dict:
        from .energy import pair_k
        k = k or pair_k(level)
        p = self.prompts[e.entry_id]
        self.select(e, mode)
        self.open_window("pair")
        shapes = []
        for i in range(k):
            shapes += [("A", SHAPE_A_TOKENS), ("B", SHAPE_B_TOKENS)]
            if with_c:
                shapes.append(("C", SHAPE_C_TOKENS))
        for i, (shape, n_out) in enumerate(shapes):
            v, wrapped = self.pools[e.entry_id].take(e.entry_id, level)
            if wrapped:
                self.w.event(self.now(), "operator_note", f"nonce variants wrapped at {e.entry_id}/{level}")
            self.request(e, mode, test="energy_pair", phase="measured", prompt=p.level_prompt(level, v), level=level,
                         variant=v, rep=i // (3 if with_c else 2) + 1, shape=shape, max_tokens=n_out)
        return self.close_window()

    # ---- agent replay
    def agent_replay(self, e: ModelEntry, mode: str, *, cache: str, reps: int = 5, in_window: bool = False) -> list:
        """§6.3. ``cache="on"``: one nonce variant per replay, so steps share their prefix and
        oMLX's cache can hit. ``cache="defeated"``: a fresh variant for every step."""
        if cache not in ("on", "defeated"):
            raise ValueError(cache)
        p = self.prompts[e.entry_id]
        n = p.replay_variants()
        out = []
        for r in range(1, reps + 1):
            v = self.replay_next.get(e.entry_id, 0)
            self.replay_next[e.entry_id] = v + (1 if cache == "on" else REPLAY_STEPS)
            if in_window:
                self.open_window("replay")
            for step in range(1, REPLAY_STEPS + 1):
                vv = (v if cache == "on" else v + step - 1) % n
                prm = p.replay_step(vv, step)
                s = self.request(e, mode, test="agent_replay", phase="measured", prompt=prm, level=None,
                                 variant=vv, rep=r, step=step, gate=(step == 1),
                                 extra={"replay_cache": cache})
                if s.get("prompt_tokens") is not None:
                    s["new_prefill_tokens"] = s["prompt_tokens"] - (s.get("cached_tokens") or 0)
                    self._rewrite_last(s)
                out.append(s)
            if in_window:
                self.close_window()
        return out

    # ---- 16 GB emulation (§5.5) and fit under load (§5.6)
    def fit16(self, e: ModelEntry, mode: str = "as_shipped", *, levels: tuple[int, ...] = LEVELS,
              reps: int = EMULATION_REPS, balloon_gib: float = BALLOON_GIB, test: str = "fit16_emulation",
              app_load_gib: float | None = None, app_load=None) -> list[dict]:
        """Shrink RAM (balloon), the GPU wired limit and the engine ceiling, then run S and W and
        every higher level until the first failure. The helper restores everything, even on a
        crash (§11.1). The engine ceiling (13 GiB) is the benchmark instance's own setting; the
        runner records it but cannot set it."""
        admin = self.i.admin
        emu = {"wired_limit_mb": EDAT_16GB_WIRED_MB, "balloon_gib": balloon_gib, "app_load_gib": app_load_gib}
        self.conditions_extra.setdefault("emulation_runs", []).append(emu)
        out: list[dict] = []
        try:
            admin.balloon_wire(balloon_gib)
            self.w.event(self.now(), "admin_action", f"balloon_wire {balloon_gib}")
            admin.wired_limit_set(EDAT_16GB_WIRED_MB)
            self.w.event(self.now(), "admin_action", f"wired_limit_set {EDAT_16GB_WIRED_MB}")
            if app_load is not None:
                app_load.start(app_load_gib)
            self.unload_current()
            p = self.prompts[e.entry_id]
            try:
                self.select(e, mode)
            except EngineError as err:
                s = blank_sample(sample_id=self._sid(), model_id=e.id, mode=mode, entry_id=e.entry_id, test=test,
                                 phase="measured", round=0, rep=0, order_index=self.w.next_order(),
                                 status="oom_killed_at_load" if err.status == "killed_unknown" else err.status,
                                 error=str(err), app_load_gib=app_load_gib)
                return [self.w.sample(s)]
            # S and W always; then every higher level until the first failure (§5.5 step 4).
            run_levels = [L for L in sorted(levels) if (L == SMALL_LEVEL or L >= WORKING_LEVEL)
                          and L in p.level_tokens()]
            for L in run_levels:
                failed = False
                for r in range(1, reps + 1):
                    v, _ = self.pools[e.entry_id].take(e.entry_id, L)
                    extra = {"app_load_gib": app_load_gib}
                    if app_load is not None:
                        extra["app_load_swapped_bytes"] = app_load.swapped_bytes()
                    s = self.request(e, mode, test=test, phase="measured", prompt=p.level_prompt(L, v),
                                     level=L, variant=v, rep=r, extra=extra)
                    if app_load is not None:
                        s["app_load_swapped_bytes"] = app_load.swapped_bytes()
                        self._rewrite_last(s)
                    out.append(s)
                    if s["status"] not in ("ok", "ok_extended"):
                        failed = True
                if failed and L >= WORKING_LEVEL:
                    break
        finally:
            if app_load is not None:
                app_load.stop()
            for verb in ("balloon_release", "wired_limit_reset"):
                try:
                    getattr(admin, verb)()
                    self.w.event(self.now(), "admin_action", verb)
                except Exception as err:  # noqa: BLE001 - the helper restores on exit anyway
                    self.w.event(self.now(), "admin_action", f"{verb} failed: {err}")
        return out

    def finish_models(self) -> list[dict]:
        return list(self.models_used.values())
