"""Start one isolated Harbor for a profile and capture what Ask, Brief and Day show.

One `Bench` is one trial: a fresh run directory (loader.prepare_home), the
sandbox server and the backend started through serve.py on the shifted clock,
then the surfaces driven over HTTP exactly as the app would:

  Ask    POST /agent through scripts/eval_core.py (the same SSE handling and
         safety gate as scripts/eval_arms.py: every confirmation is answered NO).
         A tee on the HTTP transport keeps the raw events, so confirmation
         cards, message drafts and the routed model are recorded too. The
         sandbox action log is read before and after each turn: anything new is
         an outbound action that actually ran.
  Brief  POST /assistant/proactive/analyze, then GET /assistant/proactive until
         the build is ready (the Brief tab's READY FOR YOU / YOUR LISTS data).
  Day    GET /assistant/today?day=&timezone=&debug=true (the Day tab's plan, built
         by code) and planning requests through Ask (the model's plan).

Nothing here calls /study/*, and the Ask path refuses a case that would.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from scripts import eval_core
from scripts.eval_ling_tasks import Task
from scripts.productbench.loader import PreparedHome, prepare_home
from scripts.productbench.profile import Profile

ROOT = Path(__file__).resolve().parents[2]
STARTUP_S = 120.0
SYNC_SOURCES = ("calendar", "reminders", "messages", "contacts", "email_headers", "email_raw", "notes")
FORBIDDEN_PATH_PREFIXES = ("/study",)
REAL_PORTS = frozenset({8765, 8000, 8791})


def split_at(at: str) -> tuple[int, str]:
    """("+2 14:00") -> (2, "14:00"); ("11:30") -> (0, "11:30")."""
    parts = str(at).split()
    return (int(parts[0]), parts[1]) if len(parts) == 2 else (0, parts[0])


def _port_busy(port: int) -> bool:
    """Same check as scripts/eval_arms.py (not imported: it imports service.config)."""
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10)
        return bool(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


class TeeTransport(httpx.AsyncBaseTransport):
    """Pass requests through and keep every /agent response's raw SSE lines."""

    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.raw: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(FORBIDDEN_PATH_PREFIXES):
            raise PermissionError(f"productbench never calls {request.url.path}")
        response = await self.inner.handle_async_request(request)
        if request.url.path != "/agent":
            return response
        sink = self.raw
        upstream = response.stream

        class Tee(httpx.AsyncByteStream):
            async def __aiter__(self):
                buf = b""
                async for chunk in upstream:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.startswith(b"data: "):
                            try:
                                sink.append(json.loads(line[6:]))
                            except ValueError:
                                pass
                    yield chunk

            async def aclose(self):
                await upstream.aclose()

        return httpx.Response(response.status_code, headers=response.headers, stream=Tee(),
                              request=request, extensions=response.extensions)

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass
class Bench:
    profile: Profile
    model: str
    run_dir: Path
    port: int = 8775
    sandbox_port: int = 8766
    engine_port: int = 8000
    stub_script: str | None = None
    python: str = sys.executable
    cloud: bool = False                 # a cloud reference: Harbor's remote mode, OpenRouter
    context_window: int | None = None
    credential_ref: str = "keychain:openrouter"
    at: str | None = None               # "HH:MM" later on the anchor day, or "+N HH:MM" N days on
    home: PreparedHome | None = None
    procs: list[subprocess.Popen] = field(default_factory=list)
    offset: float = 0.0
    startup: dict = field(default_factory=dict)

    narration_thinking: bool = False
    act_yes_at: float | None = None     # act-for-me on: the yes, on the bench clock (autonomy.py)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def now_epoch(self) -> float:
        """The moment this trial calls "now": the anchor, or `at` on the anchor's day."""
        if not self.at:
            return self.profile.anchor_epoch
        days, hhmm = split_at(self.at)
        h, m = (int(x) for x in hhmm.split(":"))
        day = self.profile.anchor + timedelta(days=days)
        return day.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()

    # ------------------------------------------------------------ usage
    @property
    def usage_path(self) -> Path:
        return self.run_dir / "usage.jsonl"

    def usage_mark(self) -> int:
        try:
            return len(self.usage_path.read_text(encoding="utf-8").splitlines())
        except OSError:
            return 0

    def usage_since(self, mark: int) -> list[dict]:
        try:
            lines = self.usage_path.read_text(encoding="utf-8").splitlines()[mark:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    @property
    def sandbox(self) -> str:
        return f"http://127.0.0.1:{self.sandbox_port}"

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        for p in (self.port, self.sandbox_port):
            if p in REAL_PORTS or p == self.engine_port:
                sys.exit(f"refusing port {p}: it belongs to the real Harbor or an engine (use 8775 / 8766)")
        for p in (self.port, self.sandbox_port):
            if _port_busy(p):
                sys.exit(f"port {p} is in use; stop that process or pick another")
        self.home = prepare_home(self.profile, self.run_dir, model=self.model,
                                 engine_port=self.engine_port, cloud=self.cloud,
                                 context_window=self.context_window, credential_ref=self.credential_ref)
        if self.act_yes_at is not None:
            from scripts.productbench import autonomy
            autonomy.write_gate(self.home.harbor_home, self.act_yes_at)
        real_start = time.time()
        self.offset = self.now_epoch() - real_start
        env = dict(os.environ)
        for var in ("HARBOR_MODEL_ONLY", "HARBOR_MODEL_LED_ROUTING", "HARBOR_EXPERIMENTS",
                    "HARBOR_REMOTE_ONLY", "HARBOR_REMOTE_PARITY", "HARBOR_REMOTE_FULL",
                    "HARBOR_REMOTE_BACKGROUND", "HARBOR_PLANNER", "HARBOR_BEHAVIOR_PHRASER",
                    "HARBOR_MODEL_ONLY_NARRATION_THINKING"):
            env.pop(var, None)
        if self.cloud:
            env["HARBOR_REMOTE_ONLY"] = "1"        # remote_parity comes from the overlay
        if self.narration_thinking:
            env["HARBOR_MODEL_ONLY_NARRATION_THINKING"] = "1"
        env.update({
            "HARBOR_HOME": str(self.home.harbor_home),
            "HARBOR_SANDBOX_HOME": str(self.home.sandbox_home),
            "HARBOR_BACKEND_URL": self.base,
            "HARBOR_ENGINE_MODE": "external-omlx",
            "HARBOR_BEHAVIOR": "0",
            "TZ": self.profile.data["timezone"],
            "PYTHONUNBUFFERED": "1",
        })
        common = ["--offset", repr(self.offset)]
        backend = [self.python, "-m", "scripts.productbench.serve", "backend", "--port", str(self.port),
                   "--user-name", self.profile.user["name"], *common]
        if self.act_yes_at is not None:
            backend += ["--act-yes-at", repr(self.act_yes_at)]
        if self.stub_script:
            backend += ["--stub-script", str(self.stub_script)]
        elif self.cloud:
            backend += ["--cloud-host", "openrouter.ai"]
        self._spawn(backend, env, "backend.log")
        self._wait(f"{self.base}/mode", "backend")
        self._spawn([self.python, "-m", "scripts.productbench.serve", "sandbox",
                     "--port", str(self.sandbox_port), *common], env, "sandbox.log")
        self._wait(f"{self.sandbox}/sandbox/status", "sandbox")
        t0 = time.monotonic()
        with httpx.Client(timeout=30) as c:
            for method, path, body in self.home.post_start:
                r = c.request(method, self.base + path, json=body)
                self.startup[f"{method} {path}"] = r.status_code
            self.startup["sync"] = self._wait_synced(c)
        self.startup["settle_s"] = round(time.monotonic() - t0, 1)

    def _spawn(self, cmd: list[str], env: dict, log: str) -> None:
        out = (self.run_dir / log).open("w")
        self.procs.append(subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT))

    def _wait(self, url: str, what: str) -> None:
        deadline = time.monotonic() + STARTUP_S
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in self.procs):
                break
            try:
                if httpx.get(url, timeout=2).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"{what} never came up; see {self.run_dir}/{what}.log")

    def _wait_synced(self, c: httpx.Client, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        status: dict = {}
        while time.monotonic() < deadline:
            status = c.get(self.sandbox + "/sandbox/status").json().get("sync_status", {})
            if all(status.get(s, {}).get("ok") for s in SYNC_SOURCES):
                time.sleep(2.0)       # let the backend ingest the last push
                return {s: status[s].get("count") for s in SYNC_SOURCES}
            time.sleep(0.5)
        raise RuntimeError(f"sandbox sources never synced: {status}")

    def stop(self) -> None:
        for proc in reversed(self.procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.procs.clear()

    def escapes(self) -> list[dict]:
        path = self.run_dir / "escapes.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def bench_now(self) -> float:
        return time.time() + self.offset

    # ------------------------------------------------------------ surfaces
    async def actions(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get(self.sandbox + "/sandbox/actions", timeout=10)
        return r.json().get("actions", [])

    async def ask(self, client: httpx.AsyncClient, tee: TeeTransport, case_id: str, prompt: str,
                  session_id: str | None = None) -> dict:
        mark = self.usage_mark()
        before = len(await self.actions(client))
        tee.raw.clear()
        task = Task(case_id, "productbench", prompt, (), mode="live")
        started_at = self.bench_now()
        turn = await eval_core.run_turn(client, task, prompt, session_id=session_id, base=self.base)
        await asyncio.sleep(1.0)        # an approved send would land in the log within this
        after = await self.actions(client)
        raw = list(tee.raw)
        confirms = [{"tool": e.get("tool") or e.get("name"), "args": e.get("args")}
                    for e in raw if e.get("type") == "confirm"]
        drafts = [{"to": e.get("to"), "text": e.get("text")} for e in raw if e.get("type") == "message_draft"]
        routed = next((e for e in raw if e.get("type") == "routed"), {})
        decisions = [{"name": e.get("name"), "decision": e.get("decision")}
                     for e in raw if e.get("type") == "tool_call"]
        return {
            "prompt": prompt, "answer": turn.answer.strip(), "reasoning_chars": len(turn.reasoning or ""),
            "tools": [t["name"] for t in turn.tools],
            "tool_calls": [{"name": t["name"], "args": t["args"], "result": str(t["result"])[:800]}
                           for t in turn.tools],
            "decisions": decisions, "confirms": confirms, "drafts": drafts,
            "actions": after[before:], "denied": list(turn.denied),
            "model": routed.get("model"), "session_id": turn.session_id,
            "error": turn.error, "elapsed_s": turn.elapsed_s, "first_token_s": turn.first_token_s,
            "signals": eval_core.signals(turn, ()), "bench_now": started_at,
            "usage": usage_summary(self.usage_since(mark)),
        }

    async def brief(self, client: httpx.AsyncClient, timeout_s: float = 1200.0) -> dict:
        mark = self.usage_mark()
        started = time.monotonic()
        kick = await client.post(self.base + "/assistant/proactive/analyze", json={}, timeout=30)
        snap: dict = {}
        await asyncio.sleep(2.0)
        while time.monotonic() - started < timeout_s:
            snap = (await client.get(self.base + "/assistant/proactive", timeout=30)).json()
            if snap.get("state") == "ready" and snap.get("as_of") and not snap.get("updating"):
                break
            await asyncio.sleep(3.0)
        return {"kick": {"status": kick.status_code, "body": _safe_json(kick)},
                "snapshot": snap, "elapsed_s": round(time.monotonic() - started, 1),
                "usage": usage_summary(self.usage_since(mark))}

    async def act_state(self, client: httpx.AsyncClient) -> dict:
        """What Harbor says it did by itself (GET /assistant/proactive/act: words only)."""
        r = await client.get(self.base + "/assistant/proactive/act", timeout=30)
        return _safe_json(r) if r.status_code == 200 else {"status": r.status_code}

    async def arrive(self, client: httpx.AsyncClient, bodies: list[dict]) -> list[dict]:
        """New messages from the people in the profile, after the yes (POST /sandbox/messages)."""
        out = []
        for body in bodies:
            r = await client.post(self.sandbox + "/sandbox/messages", json=body, timeout=30)
            out.append({"status": r.status_code, "ok": (_safe_json(r) or {}).get("ok")})
        await client.post(self.sandbox + "/sandbox/sync/all", timeout=60)
        await asyncio.sleep(2.0)
        return out

    async def day_tab(self, client: httpx.AsyncClient) -> dict:
        r = await client.get(self.base + "/assistant/today", timeout=60, params={
            "day": self.profile.today, "timezone": self.profile.data["timezone"], "debug": "true"})
        data = _safe_json(r)
        if isinstance(data, dict):
            data.pop("replay", None)       # a full copy of the inputs; the world is recorded already
        return {"status": r.status_code, "snapshot": data}


def usage_summary(calls: list[dict]) -> dict:
    """Tokens, reported cost, latency, resolved models and upstream providers for a set of calls."""
    def total(key):
        vals = [c.get(key) for c in calls if isinstance(c.get(key), (int, float))]
        return sum(vals) if vals else None
    firsts = sorted(c["first_byte_s"] for c in calls if isinstance(c.get("first_byte_s"), (int, float)))
    costs = [c.get("cost") for c in calls]
    prompts = [c["prompt_tokens"] for c in calls if isinstance(c.get("prompt_tokens"), (int, float))]
    return {"calls": len(calls), "prompt_tokens": total("prompt_tokens"),
            "prompt_tokens_max": max(prompts) if prompts else None,
            "completion_tokens": total("completion_tokens"), "reasoning_tokens": total("reasoning_tokens"),
            "cost_reported_usd": (round(sum(costs), 6) if calls and all(isinstance(c, (int, float))
                                                                        for c in costs) else None),
            "first_byte_s_p50": firsts[len(firsts) // 2] if firsts else None,
            "call_s_total": round(total("elapsed_s") or 0.0, 3),
            "models": sorted({c["model"] for c in calls if c.get("model")}),
            "providers": sorted({c["provider"] for c in calls if c.get("provider")}),
            "errors": sum(1 for c in calls if (c.get("status") or 200) >= 400),
            "sampling_sent": sorted({json.dumps({k: v for k, v in (c.get("sent") or {}).items()
                                                 if k not in ("tools", "model")}, sort_keys=True)
                                     for c in calls})[:6]}


def _safe_json(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        return {"text": r.text[:500]}
