"""OpenAI-style chat completions: request building, response parsing, transport.

oMLX and OpenRouter both speak `/v1/chat/completions` with `tools`. This module
holds what they share. The transport is pluggable so unit tests run against an
in-process fake with no sockets; the default transport uses httpx.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Protocol

from edat.models.base import (ChatResult, EngineError, Sampling, Timing, ToolCall, Usage,
                              make_tool_call)


# ------------------------------------------------------------------ transport

@dataclass
class HttpResponse:
    status: int
    body: Any = None                                   # parsed JSON (or text) when not streaming
    lines: Iterator[str] = field(default_factory=lambda: iter(()))   # SSE lines when streaming
    headers: dict = field(default_factory=dict)


class Transport(Protocol):
    def post(self, url: str, headers: dict, payload: dict, *, stream: bool,
             timeout_s: float) -> HttpResponse: ...


class HttpxTransport:
    """The default transport. Raises EngineError for connection-level faults."""

    def post(self, url: str, headers: dict, payload: dict, *, stream: bool,
             timeout_s: float) -> HttpResponse:
        import httpx

        try:
            client = httpx.Client(timeout=timeout_s, trust_env=False)
        except Exception as exc:  # pragma: no cover - construction failure
            raise EngineError("transport", f"could not create client: {exc}") from exc
        try:
            if not stream:
                resp = client.post(url, headers=headers, json=payload)
                try:
                    body: Any = resp.json()
                except ValueError:
                    body = resp.text
                client.close()
                return HttpResponse(status=resp.status_code, body=body, headers=dict(resp.headers))
            req = client.build_request("POST", url, headers=headers, json=payload)
            resp = client.send(req, stream=True)
            if resp.status_code >= 400:
                text = resp.read().decode("utf-8", "replace")
                resp.close()
                client.close()
                try:
                    body = json.loads(text)
                except ValueError:
                    body = text
                return HttpResponse(status=resp.status_code, body=body, headers=dict(resp.headers))

            def gen() -> Iterator[str]:
                try:
                    for line in resp.iter_lines():
                        yield line
                except httpx.TimeoutException as exc:
                    raise EngineError("timeout", f"stream timed out: {exc}") from exc
                except httpx.HTTPError as exc:
                    raise EngineError("transport", f"stream broke: {exc}") from exc
                finally:
                    resp.close()
                    client.close()
            return HttpResponse(status=resp.status_code, lines=gen(), headers=dict(resp.headers))
        except httpx.TimeoutException as exc:
            client.close()
            raise EngineError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            client.close()
            raise EngineError("transport", str(exc)) from exc


# ------------------------------------------------------------------ request

def build_payload(model: str, messages: list[dict], *, tools: list[dict] | None,
                  sampling: Sampling | None, seed: int | None, max_tokens: int,
                  stream: bool) -> dict:
    payload: dict = {"model": model, "messages": messages, "max_tokens": int(max_tokens),
                     "stream": stream}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if sampling is not None:
        payload.update(sampling.params())
    if seed is not None:
        payload["seed"] = int(seed)
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


# ------------------------------------------------------------------ response

def usage_from(raw: dict | None) -> Usage:
    raw = raw or {}
    details = raw.get("completion_tokens_details") or {}
    pdetails = raw.get("prompt_tokens_details") or {}
    cost = raw.get("cost")
    return Usage(prompt_tokens=raw.get("prompt_tokens"), completion_tokens=raw.get("completion_tokens"),
                 reasoning_tokens=details.get("reasoning_tokens", raw.get("reasoning_tokens")),
                 cached_prompt_tokens=pdetails.get("cached_tokens"),
                 cost_usd=float(cost) if isinstance(cost, (int, float)) else None)


def _reasoning_of(obj: dict) -> str:
    # oMLX: reasoning_content. OpenRouter: reasoning (string) and/or reasoning_details.
    for key in ("reasoning_content", "reasoning"):
        val = obj.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def parse_completion(body: dict) -> tuple[str, list[ToolCall], str, str | None, Usage]:
    """(text, tool_calls, reasoning, finish_reason, usage) from a non-streamed body."""
    choices = body.get("choices") or []
    if not choices:
        raise EngineError("server", "response has no choices", raw=body)
    choice = choices[0]
    msg = choice.get("message") or {}
    calls = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        calls.append(make_tool_call(tc.get("id"), fn.get("name"), fn.get("arguments"), i))
    return (msg.get("content") or "", calls, _reasoning_of(msg), choice.get("finish_reason"),
            usage_from(body.get("usage")))


@dataclass
class StreamState:
    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calls: dict[int, dict] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict | None = None
    chunks: list[dict] = field(default_factory=list)
    first_token_at: float | None = None
    top: dict = field(default_factory=dict)   # model, provider, id from the first chunk that has them


def accumulate_stream(lines: Iterator[str], *, now: Callable[[], float],
                      on_error: Callable[[dict], None] | None = None) -> StreamState:
    st = StreamState()
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            raise EngineError("server", f"unparsable stream chunk: {data[:80]!r}")
        st.chunks.append(chunk)
        if "error" in chunk and not chunk.get("choices"):
            if on_error is not None:
                on_error(chunk)
            raise EngineError("server", f"stream error: {json.dumps(chunk.get('error'))[:200]}", raw=chunk)
        for key in ("model", "provider", "id"):
            if key in chunk and key not in st.top:
                st.top[key] = chunk[key]
        if chunk.get("usage"):
            st.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            got = False
            if delta.get("content"):
                st.text.append(delta["content"])
                got = True
            r = _reasoning_of(delta)
            if r:
                st.reasoning.append(r)
                got = True
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", len(st.calls))
                slot = st.calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments") is not None:
                    if isinstance(fn["arguments"], dict):
                        slot["arguments"] = fn["arguments"]
                    else:
                        slot["arguments"] = (slot["arguments"] if isinstance(slot["arguments"], str) else "") \
                            + fn["arguments"]
                got = True
            if got and st.first_token_at is None:
                st.first_token_at = now()
            if choice.get("finish_reason"):
                st.finish_reason = choice["finish_reason"]
    return st


def stream_result(st: StreamState) -> tuple[str, list[ToolCall], str, str | None, Usage]:
    calls = [make_tool_call(slot["id"], slot["name"], slot["arguments"], i)
             for i, (_, slot) in enumerate(sorted(st.calls.items()))]
    return "".join(st.text), calls, "".join(st.reasoning), st.finish_reason, usage_from(st.usage)


def classify_http_error(status: int, body: Any) -> EngineError:
    detail = json.dumps(body)[:300] if not isinstance(body, str) else body[:300]
    if status == 429:
        return EngineError("rate_limit", detail, status=status, raw=body)
    if status in (401, 403):
        return EngineError("auth", detail, status=status, retryable=False, raw=body)
    if status == 408:
        return EngineError("timeout", detail, status=status, raw=body)
    if status >= 500:
        return EngineError("server", detail, status=status, raw=body)
    return EngineError("request", detail, status=status, retryable=False, raw=body)


def run_request(*, transport: Transport, url: str, headers: dict, payload: dict, stream: bool,
                timeout_s: float, now: Callable[[], float] = time.perf_counter,
                on_http_error: Callable[[int, Any], ChatResult | None] | None = None) -> ChatResult:
    """Send one request and turn the answer into a ChatResult (request field left empty).

    `on_http_error` lets a client turn a specific HTTP error into a scored
    result (OpenRouter: a provider rejecting the model's own malformed call);
    returning None falls back to an EngineError.
    """
    t0 = now()
    resp = transport.post(url, headers, payload, stream=stream, timeout_s=timeout_s)
    if resp.status >= 400:
        if on_http_error is not None:
            scored = on_http_error(resp.status, resp.body)
            if scored is not None:
                scored.timing = Timing(ttft_s=None, total_s=now() - t0)
                return scored
        raise classify_http_error(resp.status, resp.body)
    if not stream:
        body = resp.body
        if not isinstance(body, dict):
            raise EngineError("server", f"non-JSON response: {str(body)[:120]!r}", raw=body)
        if "error" in body and not body.get("choices"):
            if on_http_error is not None:
                scored = on_http_error(200, body)
                if scored is not None:
                    scored.timing = Timing(ttft_s=None, total_s=now() - t0)
                    return scored
            raise EngineError("server", json.dumps(body["error"])[:300], raw=body)
        text, calls, reasoning, finish, usage = parse_completion(body)
        t1 = now()
        return ChatResult(text=text, tool_calls=calls, reasoning=reasoning, finish_reason=finish,
                          usage=usage, timing=Timing(ttft_s=None, total_s=t1 - t0), raw=body,
                          meta={k: body[k] for k in ("model", "provider", "id") if k in body})
    st = accumulate_stream(resp.lines, now=now)
    t1 = now()
    text, calls, reasoning, finish, usage = stream_result(st)
    ttft = (st.first_token_at - t0) if st.first_token_at is not None else None
    return ChatResult(text=text, tool_calls=calls, reasoning=reasoning, finish_reason=finish,
                      usage=usage, timing=Timing(ttft_s=ttft, total_s=t1 - t0), raw=st.chunks,
                      meta=dict(st.top))
