"""A thin oMLX client for performance runs (spec 04 §6.2, §6.3, §7, §11 items 3 and 7).

Why not the agentic adapter (``edat/models/``)? Performance needs every streamed
chunk's arrival time on the run clock, the server's usage block (prompt, cached,
completion and reasoning tokens) and the engine's load and residency routes. Those
are thin here; nothing is re-tokenised (§6.2).

What it sends:

- ``prompt_api="completions"`` (default): ``POST /v1/completions`` with the pack's
  already-templated ``text``, so the server prefills exactly the pack's tokens.
  Whether the benchmark oMLX instance serves ``/v1/completions`` is **unverified**;
  ``prompt_api="chat"`` sends ``/v1/chat/completions`` messages instead.
- Greedy decoding (temperature 0, top_p 1, top_k 0, a recorded seed) and
  ``max_tokens`` = the forced length. ``ignore_eos`` and ``min_tokens`` are sent so
  an engine that supports them emits exactly that many tokens; whether oMLX honours
  them is **unverified**, so the actual ``completion_tokens`` is always recorded and
  the aggregator uses it.
- ``stream_options.include_usage`` for the usage block.

Safety: only loopback hosts, never the ports in ``FORBIDDEN_PORTS`` (Harbor's
backend and gateway, and the user's own engine). The benchmark runs its own oMLX
instance (D12).
"""
from __future__ import annotations

import http.client
import json
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Protocol
from urllib.parse import urlparse

from .constants import FORBIDDEN_PORTS, REQUEST_TIMEOUT_S

LOOPBACK = ("127.0.0.1", "localhost", "::1")


class EngineError(Exception):
    """A failure that becomes a sample status (never a crash). ``status`` is a §3.6 status."""

    def __init__(self, status: str, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.status = status
        self.http_status = http_status


class ForbiddenEndpoint(ValueError):
    pass


def check_base_url(base_url: str) -> tuple[str, int]:
    u = urlparse(base_url)
    if u.scheme != "http" or u.hostname not in LOOPBACK or u.port is None:
        raise ForbiddenEndpoint(f"benchmark engine must be http://127.0.0.1:<port>, got {base_url!r}")
    if u.port in FORBIDDEN_PORTS:
        raise ForbiddenEndpoint(f"port {u.port} is reserved (Harbor or the user's own engine); "
                                "start the benchmark oMLX instance on another port")
    return u.hostname, u.port


# --- transport -----------------------------------------------------------------

class Transport(Protocol):
    """What the client needs from HTTP. ``stream`` yields ``(t_ns, line)`` per SSE line,
    stamped on arrival with the run clock."""

    def request(self, method: str, path: str, body: dict | None = None,
                timeout: float = 60.0) -> tuple[int, Any]: ...

    def stream(self, path: str, body: dict, timeout: float) -> Iterator[tuple[int, str]]: ...


class HttpTransport:
    def __init__(self, base_url: str, now_ns: Callable[[], int]):
        self.host, self.port = check_base_url(base_url)
        self.now_ns = now_ns

    def _conn(self, timeout: float) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def request(self, method: str, path: str, body: dict | None = None,
                timeout: float = 60.0) -> tuple[int, Any]:
        c = self._conn(timeout)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            c.request(method, path, body=payload, headers={"Content-Type": "application/json"})
            r = c.getresponse()
            raw = r.read(8 * 1024 * 1024)
            try:
                data = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                data = None
            return r.status, data
        finally:
            c.close()

    def stream(self, path: str, body: dict, timeout: float) -> Iterator[tuple[int, str]]:
        c = self._conn(timeout)
        try:
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
            r = c.getresponse()
            if r.status != 200:
                raw = r.read(64 * 1024)
                yield self.now_ns(), "__HTTP__ " + json.dumps({"status": r.status,
                                                               "body": raw.decode("utf-8", "replace")[:2000]})
                return
            while True:
                line = r.readline()
                if not line:
                    return
                yield self.now_ns(), line.decode("utf-8", "replace").rstrip("\r\n")
        finally:
            c.close()


# --- results -----------------------------------------------------------------

@dataclass
class StreamResult:
    """Raw timing of one request. Times are run-clock nanoseconds."""
    t_submit_ns: int
    t_first_token_ns: int | None = None    # first token of any kind (TTFT, §6.2)
    t_first_answer_ns: int | None = None   # first non-reasoning token (TTFA)
    t_last_token_ns: int | None = None
    t_end_ns: int | None = None
    chunk_times_ns: list[int] = field(default_factory=list)   # every chunk with text, absolute
    chunk_kinds: list[str] = field(default_factory=list)      # "reasoning" | "content"
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None
    text_chars: int = 0
    reasoning_chars: int = 0
    server_timings: dict = field(default_factory=dict)
    error_status: str | None = None
    error: str | None = None

    @property
    def prompt_tokens(self) -> int | None:
        return self.usage.get("prompt_tokens")

    @property
    def completion_tokens(self) -> int | None:
        return self.usage.get("completion_tokens")

    @property
    def cached_tokens(self) -> int | None:
        d = self.usage.get("prompt_tokens_details") or {}
        v = d.get("cached_tokens")
        return v if isinstance(v, int) else None

    @property
    def reasoning_tokens(self) -> int | None:
        d = self.usage.get("completion_tokens_details") or {}
        v = d.get("reasoning_tokens")
        return v if isinstance(v, int) else None

    def token_times_rel_ns(self) -> list[int]:
        """Chunk arrival times as deltas from the first token (§3.6 ``token_times_ns``).
        One value per streamed chunk after the first; a chunk can carry more than one token
        when the engine drafts (MTP), so the runner records ``token_times_kind: "chunk"``."""
        if self.t_first_token_ns is None:
            return []
        return [t - self.t_first_token_ns for t in self.chunk_times_ns[1:]]


_TIMING_KEYS = ("prompt_eval_duration", "prompt_time", "prefill_time", "prefill_duration",
                "generation_duration", "generation_time", "prompt_tps", "generation_tps",
                "drafted_tokens", "accepted_tokens", "draft_tokens", "accepted_draft_tokens")


def _server_timings(obj: dict) -> dict:
    out = {}
    for src in (obj, obj.get("usage") or {}, obj.get("timings") or {}):
        for k in _TIMING_KEYS:
            if isinstance(src.get(k), (int, float)):
                out[k] = src[k]
    return out


# --- the client -----------------------------------------------------------------

class OmlxPerfClient:
    def __init__(self, transport: Transport, *, now_ns: Callable[[], int], prompt_api: str = "completions",
                 timeout_s: float = REQUEST_TIMEOUT_S, thinking_end_marker: str | None = None):
        if prompt_api not in ("completions", "chat"):
            raise ValueError(prompt_api)
        self.t = transport
        self.now_ns = now_ns
        self.prompt_api = prompt_api
        self.timeout_s = timeout_s
        self.thinking_end_marker = thinking_end_marker

    @classmethod
    def connect(cls, base_url: str, *, now_ns: Callable[[], int], **kw) -> "OmlxPerfClient":
        return cls(HttpTransport(base_url, now_ns), now_ns=now_ns, **kw)

    # -- residency (§7, §5.7) --
    def load(self, model: str) -> tuple[int, int]:
        """POST /v1/models/<id>/load. Returns (t_start_ns, t_ready_ns); raises EngineError."""
        t0 = self.now_ns()
        try:
            status, data = self.t.request("POST", f"/v1/models/{model}/load", None, timeout=self.timeout_s)
        except (OSError, http.client.HTTPException) as e:
            raise EngineError("killed_unknown", f"load: {type(e).__name__}") from None
        t1 = self.now_ns()
        if status == 507:
            raise EngineError("engine_refused", "engine memory guard refused the load", http_status=507)
        if status == 404:
            raise EngineError("unsupported_by_runtime", "engine does not have this model id", http_status=404)
        if status >= 400:
            raise EngineError("engine_refused", f"load returned HTTP {status}", http_status=status)
        return t0, t1

    def _req(self, method: str, path: str) -> tuple[int, Any]:
        try:
            return self.t.request(method, path, None)
        except (OSError, http.client.HTTPException) as e:
            raise EngineError("killed_unknown", f"engine unreachable: {type(e).__name__}") from None

    def unload(self, model: str) -> None:
        status, _ = self._req("POST", f"/v1/models/{model}/unload")
        if status >= 400 and status != 404:
            raise EngineError("engine_refused", f"unload returned HTTP {status}", http_status=status)

    def loaded_models(self) -> list[str]:
        status, data = self._req("GET", "/v1/models/status")
        if status != 200 or not isinstance(data, dict):
            return []
        return [m.get("id") for m in data.get("models") or () if isinstance(m, dict) and m.get("loaded")]

    def activity(self) -> dict:
        """GET /api/status: whole-server counters (active and waiting requests), read-only.
        Used to detect other clients' work during a window (§4.3, ``voided_foreign_work``)."""
        try:
            status, data = self.t.request("GET", "/api/status")
        except (OSError, http.client.HTTPException):
            return {}
        return data if status == 200 and isinstance(data, dict) else {}

    def ensure_only(self, model: str) -> None:
        """Unload everything else so only the model under test is resident (§5.7 rule 1)."""
        for m in self.loaded_models():
            if m and m != model:
                self.unload(m)

    # -- generation --
    def generate(self, model: str, *, text: str | None = None, messages: list | None = None,
                 tools: list | None = None, max_tokens: int, seed: int = 0, force_length: bool = True,
                 thinking: bool | None = None, sampling: dict | None = None) -> StreamResult:
        if self.prompt_api == "completions":
            if text is None:
                raise ValueError("completions API needs the templated text")
            path, body = "/v1/completions", {"model": model, "prompt": text}
        else:
            if messages is None:
                raise ValueError("chat API needs messages")
            path, body = "/v1/chat/completions", {"model": model, "messages": messages}
            if tools:
                body["tools"] = tools
            if thinking is not None:
                body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        body.update({"stream": True, "stream_options": {"include_usage": True}, "max_tokens": max_tokens,
                     "seed": seed})
        body.update(sampling if sampling is not None else {"temperature": 0.0, "top_p": 1.0, "top_k": 0})
        if force_length:
            body.update({"ignore_eos": True, "min_tokens": max_tokens})
        res = StreamResult(t_submit_ns=self.now_ns())
        try:
            self._consume(self.t.stream(path, body, self.timeout_s), res)
        except ConnectionRefusedError:
            res.error_status, res.error = "killed_unknown", "engine unreachable: ConnectionRefusedError"
        except socket.timeout:
            res.error_status, res.error = "timeout", "request exceeded the timeout"
        except (ConnectionError, http.client.HTTPException, OSError) as e:
            res.error_status, res.error = "killed_unknown", f"connection lost: {type(e).__name__}"
        if res.t_end_ns is None:
            res.t_end_ns = self.now_ns()
        return res

    def _consume(self, lines: Iterator[tuple[int, str]], res: StreamResult) -> None:
        in_think = self.thinking_end_marker is not None
        buf = ""
        for t_ns, line in lines:
            if line.startswith("__HTTP__ "):
                info = json.loads(line[9:])
                st = info.get("status")
                res.error = f"HTTP {st}"
                res.error_status = "engine_refused" if st in (507, 413) else (
                    "exceeds_native_window" if st == 400 and "context" in info.get("body", "").lower()
                    else "engine_refused")
                return
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                res.t_end_ns = t_ns
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                res.error_status, res.error = "killed_unknown", "invalid stream data"
                return
            if not isinstance(chunk, dict):
                continue
            if "error" in chunk:
                err = chunk["error"] if isinstance(chunk["error"], dict) else {"message": str(chunk["error"])}
                code = str(err.get("code") or "")
                res.error_status = "engine_refused" if code.startswith("prefill_memory_") or "memory" in code \
                    else "engine_refused"
                res.error = f"stream error {code or 'unknown'}"
                return
            if chunk.get("usage"):
                res.usage = chunk["usage"]
                res.server_timings.update(_server_timings(chunk))
            choices = chunk.get("choices") or []
            if not choices:
                continue
            ch = choices[0] if isinstance(choices[0], dict) else {}
            if ch.get("finish_reason"):
                res.finish_reason = ch["finish_reason"]
            pieces: list[tuple[str, str]] = []
            if "text" in ch:  # completions
                txt = ch.get("text") or ""
                if txt:
                    if in_think:
                        buf += txt
                        if self.thinking_end_marker in buf:
                            in_think = False
                            pieces.append(("reasoning", txt))
                        else:
                            pieces.append(("reasoning", txt))
                    else:
                        pieces.append(("content", txt))
            else:
                d = ch.get("delta") or {}
                r = d.get("reasoning_content") or d.get("reasoning") or ""
                c = d.get("content") or ""
                if r:
                    pieces.append(("reasoning", r))
                if c:
                    pieces.append(("content", c))
            for kind, txt in pieces:
                if res.t_first_token_ns is None:
                    res.t_first_token_ns = t_ns
                if kind == "content" and res.t_first_answer_ns is None:
                    res.t_first_answer_ns = t_ns
                res.t_last_token_ns = t_ns
                if kind == "content":
                    res.text_chars += len(txt)
                else:
                    res.reasoning_chars += len(txt)
            if pieces:
                res.chunk_times_ns.append(t_ns)
                res.chunk_kinds.append(pieces[-1][0])
        # Stream ended without [DONE]: the engine went away mid-request.
        if res.error_status is None and not res.usage:
            res.error_status, res.error = "killed_unknown", "stream ended without [DONE]"
