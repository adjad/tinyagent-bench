"""Fakes for the runner's tests: an engine, a footprint probe, a power reader, a thermal
probe and a system probe, all driven by one ``FakeClock``.

Nothing here talks to a real engine. :class:`FakeEngine` implements the client's
``Transport`` protocol: it answers the oMLX routes the client uses and streams SSE
lines, advancing the fake clock through prefill and decode at the speeds it is given,
so every timestamp, footprint tick and power reading lands at a known fake time and
the derived numbers can be checked by hand.

Prompts in these tests are words: one whitespace-separated word is one token.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator

from .clock import FakeClock
from .constants import GIB, MIB
from .memory import ProcMemory


@dataclass
class FakeModel:
    weights_bytes: int = 5 * GIB
    overhead_bytes: int = 512 * MIB
    kv_bytes_per_token: int = 16 * 1024          # 16 KiB/token = 16 MiB per 1k tokens
    prefill_tps: float = 2000.0
    decode_tps: float = 50.0
    native_window: int = 131072
    reasoning_tokens: int = 0                     # first N output tokens are reasoning
    load_s: float = 2.0
    cold_load_s: float = 6.0
    mtp_drafts_per_cycle: float | None = None


@dataclass
class Activity:
    t0: int
    t1: int
    kind: str          # "prefill" | "decode" | "load"
    model: str


class FakeEngine:
    """An oMLX stand-in. ``count_tokens`` maps a prompt string to its token count."""

    def __init__(self, clock: FakeClock, models: dict[str, FakeModel], *, prefix_cache: bool = False,
                 ceiling_bytes: int | None = None, oom_bytes: int | None = None, base_footprint: int = 300 * MIB,
                 idle_W: float = 4.0, prefill_W: float = 60.0, decode_W: float = 40.0, load_W: float = 15.0,
                 tokenize=None):
        self.clock = clock
        self.tokenize = tokenize or (lambda text: text.split())
        self.models = models
        self.prefix_cache = prefix_cache
        self.ceiling_bytes = ceiling_bytes
        self.oom_bytes = oom_bytes
        self.base_footprint = base_footprint
        self.loaded: dict[str, bool] = {}
        self.cold: set[str] = set(models)   # not in the file cache yet
        self.kv_tokens = 0
        self.alive = True
        self.seen_prompts: list[list[str]] = []
        self.requests: list[dict] = []
        self.activity: list[Activity] = []
        self.idle_W, self.prefill_W, self.decode_W, self.load_W = idle_W, prefill_W, decode_W, load_W
        self.foreign_active_at: list[tuple[int, int]] = []   # (t0, t1) windows with another client
        self.disk_read_bytes = 0
        self.unload_after_idle_s: float | None = None
        self._last_use: dict[str, int] = {}

    # --- state the fake probes read ---
    def footprint(self) -> int:
        fp = self.base_footprint
        for mid, on in self.loaded.items():
            if on:
                m = self.models[mid]
                fp += m.weights_bytes + m.overhead_bytes
        return fp + self.kv_tokens * max((self.models[m].kv_bytes_per_token for m, on in self.loaded.items()
                                          if on), default=0)

    def power_at(self, t_ns: int) -> float:
        for a in self.activity:
            if a.t0 <= t_ns < a.t1:
                return {"prefill": self.prefill_W, "decode": self.decode_W, "load": self.load_W}[a.kind]
        return self.idle_W + (1.0 if any(self.loaded.values()) else 0.0)

    def purge(self) -> None:
        self.cold = set(self.models)

    def _check_idle_unload(self) -> None:
        if self.unload_after_idle_s is None:
            return
        for mid, on in list(self.loaded.items()):
            if on and (self.clock.t - self._last_use.get(mid, self.clock.t)) / 1e9 >= self.unload_after_idle_s:
                self.loaded[mid] = False

    # --- Transport ---
    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 60.0):
        if not self.alive:
            raise ConnectionRefusedError("engine is down")
        self._check_idle_unload()
        if path == "/v1/models/status":
            return 200, {"models": [{"id": m, "loaded": bool(self.loaded.get(m))} for m in self.models]}
        if path == "/api/status":
            t = self.clock.t
            foreign = sum(1 for a, b in self.foreign_active_at if a <= t <= b)
            return 200, {"active_requests": foreign, "waiting_requests": 0}
        if path.startswith("/v1/models/") and path.endswith("/load"):
            mid = path[len("/v1/models/"):-len("/load")]
            if mid not in self.models:
                return 404, {"error": "unknown"}
            m = self.models[mid]
            if self.ceiling_bytes is not None and m.weights_bytes + m.overhead_bytes > self.ceiling_bytes:
                return 507, {"error": "memory"}
            if not self.loaded.get(mid):
                dur = m.cold_load_s if mid in self.cold else m.load_s
                t0 = self.clock.t
                # Activity is recorded before time moves, so samplers listening to the fake
                # clock see the power of the phase they are inside.
                self.activity.append(Activity(t0, t0 + int(round(dur * 1e9)), "load", mid))
                self.clock.advance(dur)
                if mid in self.cold:
                    self.disk_read_bytes += m.weights_bytes
                    self.cold.discard(mid)
                self.loaded[mid] = True
            self._last_use[mid] = self.clock.t
            return 200, {"status": "loaded"}
        if path.startswith("/v1/models/") and path.endswith("/unload"):
            mid = path[len("/v1/models/"):-len("/unload")]
            self.loaded[mid] = False
            self.kv_tokens = 0
            return 200, {"status": "unloaded"}
        return 404, None

    def stream(self, path: str, body: dict, timeout: float) -> Iterator[tuple[int, str]]:
        if not self.alive:
            raise ConnectionRefusedError("engine is down")
        self._check_idle_unload()
        mid = body["model"]
        m = self.models[mid]
        reloaded = False
        if not self.loaded.get(mid):          # oMLX loads on demand
            self.request("POST", f"/v1/models/{mid}/load")
            reloaded = True
        prompt = body.get("prompt")
        if prompt is None:
            prompt = " ".join(str(x.get("content") or "") for x in body.get("messages") or ())
        words = self.tokenize(prompt)
        n_prompt = len(words)
        self.requests.append({"model": mid, "prompt_tokens": n_prompt, "max_tokens": body["max_tokens"],
                              "t": self.clock.t, "reloaded": reloaded, "body": body})
        cached = 0
        if self.prefix_cache:
            for prev in self.seen_prompts:
                k = 0
                for a, b in zip(prev, words):
                    if a != b:
                        break
                    k += 1
                cached = max(cached, k)
        self.seen_prompts.append(words)
        if n_prompt + body["max_tokens"] > m.native_window:
            yield self.clock.t, "__HTTP__ " + json.dumps({"status": 400, "body": "context length exceeded"})
            return
        # prefill
        t0 = self.clock.t
        self.kv_tokens = n_prompt
        if self.oom_bytes is not None and self.footprint() > self.oom_bytes:
            self.clock.advance(0.05)
            self.alive = False
            raise ConnectionResetError("engine died")
        pre = (n_prompt - cached) / m.prefill_tps
        self.activity.append(Activity(t0, t0 + int(round(pre * 1e9)), "prefill", mid))
        self.clock.advance(pre)
        n_out = body["max_tokens"]
        t1 = self.clock.t
        dec = (0.5 + (n_out - 1)) / m.decode_tps
        self.activity.append(Activity(t1, t1 + int(round(dec * 1e9)) + 1, "decode", mid))
        for i in range(n_out):
            if i > 0:
                self.clock.advance(1.0 / m.decode_tps)
            else:
                self.clock.advance(0.5 / m.decode_tps)
            self.kv_tokens += 1
            if mid in self.models and "messages" in body:
                key = "reasoning_content" if i < m.reasoning_tokens else "content"
                delta = {"choices": [{"delta": {key: f"t{i} "}}]}
            else:
                delta = {"choices": [{"text": f"t{i} "}]}
            yield self.clock.t, "data: " + json.dumps(delta)
        usage = {"prompt_tokens": n_prompt, "completion_tokens": n_out,
                 "prompt_tokens_details": {"cached_tokens": cached},
                 "completion_tokens_details": {"reasoning_tokens": min(m.reasoning_tokens, n_out)}}
        yield self.clock.t, "data: " + json.dumps({"choices": [{"text": "", "finish_reason": "length"}]})
        yield self.clock.t, "data: " + json.dumps({"choices": [], "usage": usage})
        self._last_use[mid] = self.clock.t
        self.kv_tokens = 0          # the request's KV is released when it ends
        yield self.clock.t, "data: [DONE]"


class FakeProbe:
    """Footprint probe over a FakeEngine (stands in for ``proc_pid_rusage``)."""

    def __init__(self, engine: FakeEngine):
        self.engine = engine
        self.lifetime_max = 0

    def read(self) -> ProcMemory:
        if not self.engine.alive:
            raise ProcessLookupError("engine gone")
        fp = self.engine.footprint()
        self.lifetime_max = max(self.lifetime_max, fp)
        return ProcMemory(fp, self.lifetime_max, fp, fp, self.engine.disk_read_bytes)

    def alive(self) -> bool:
        return self.engine.alive


class FakeSystem:
    """Swap, compressor, thermal, power source, iostat, battery: scripted values."""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.swap_used = 0
        self.compressed = 0
        self.thermal_script: list[tuple[int, str]] = []   # (t_ns from, state)
        self.power_source = "ac"
        self.battery_level = 1.0
        self.disk_read_total = 0
        self.wired_limit_mb = 16384

    def counters(self):
        from .memory import SystemCounters
        return SystemCounters(self.swap_used, self.compressed)

    def neural_bytes(self, pid: int):
        return None

    def thermal(self) -> str:
        state = "nominal"
        for t, s in self.thermal_script:
            if self.clock.t >= t:
                state = s
        return state


class FakePowerReader:
    """``read_watts()`` over a FakeEngine's activity at the fake clock's time."""

    def __init__(self, engine: FakeEngine):
        self.engine = engine

    def __call__(self) -> float:
        return self.engine.power_at(self.engine.clock.t)


@dataclass
class WordPrompts:
    """A prompt source for tests: prompts are exact word counts with a nonce word up front."""
    levels: tuple[int, ...] = (512, 1024, 2048)
    variants: int = 64
    replay_tokens: tuple[int, ...] = (400, 480, 520, 560, 600, 640)
    first_token_tokens: int = 8
    id: str = "words"
    sha256: str = "0" * 64
    corpus: str = "synthetic-words"
    extra: dict = field(default_factory=dict)

    def level_tokens(self) -> tuple[int, ...]:
        return self.levels

    def n_variants(self, level: int) -> int:
        return self.variants

    def level_prompt(self, level: int, variant: int) -> dict:
        words = [f"nonce{variant}x{level}"] + [f"w{i}" for i in range(level - 1)]
        return {"text": " ".join(words), "messages": [{"role": "user", "content": " ".join(words)}],
                "tokens": level}

    def first_token_prompt(self) -> dict:
        w = " ".join(f"f{i}" for i in range(self.first_token_tokens))
        return {"text": w, "messages": [{"role": "user", "content": w}], "tokens": self.first_token_tokens}

    def replay_variants(self) -> int:
        return self.variants

    def replay_step(self, variant: int, step: int) -> dict:
        n = self.replay_tokens[step - 1]
        words = [f"rnonce{variant}"] + [f"r{i}" for i in range(n - 1)]
        return {"text": " ".join(words), "messages": [{"role": "user", "content": " ".join(words)}],
                "tokens": n}
