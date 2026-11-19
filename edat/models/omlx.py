"""The oMLX client: a local model served by oMLX on this Mac, loopback only.

oMLX speaks OpenAI chat completions and parses tool calls server-side, so a
call's `function.arguments` arrives as the text the model wrote. This client
does no repair: if that text is not a JSON object the call is returned with a
`parse_error` (the model's `malformed` failure), unlike Harbor's own client,
which raised IncompleteStreamError and so hid the failure (spec 02 §1.6).

Thinking is switched with oMLX's chat-template flag
`chat_template_kwargs.enable_thinking`, the same flag Toolbench sends
(scripts/toolbench/runner.py). Reasoning text arrives as `reasoning_content`.

The client refuses any base URL that is not a loopback address; `api_key`
(oMLX's own key, where the engine checks one) goes only to that address. It never
loads, unloads or manages models; the run harness does that, one model
resident at a time.
"""
from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlsplit

from edat.models.base import ChatResult, ConfigurationError, Sampling, tool_names
from edat.models.openai_compat import HttpxTransport, Transport, build_payload, run_request

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
DEFAULT_BASE_URL = "http://127.0.0.1:8000"     # oMLX's default port; the harness passes the real one


def assert_loopback(base_url: str) -> tuple[str, int]:
    parts = urlsplit(base_url)
    if parts.scheme != "http":
        raise ConfigurationError(f"oMLX base URL must be plain http on loopback, got {base_url!r}")
    host = (parts.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise ConfigurationError(f"oMLX base URL must be a loopback address, got host {host!r}")
    if parts.port is None:
        raise ConfigurationError(f"oMLX base URL must name a port, got {base_url!r}")
    return host, parts.port


class OmlxClient:
    runtime = "omlx"

    def __init__(self, model: str, *, base_url: str = DEFAULT_BASE_URL, stream: bool = True,
                 timeout_s: float = 300.0, transport: Transport | None = None,
                 runtime_version: str | None = None, weights_sha256: str | None = None,
                 quantization: str | None = None, template_sha256: str | None = None,
                 api_key: str | None = None, now: Callable[[], float] = time.perf_counter) -> None:
        if not model:
            raise ConfigurationError("an oMLX model id is required")
        self.host, self.port = assert_loopback(base_url)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.stream = stream
        self.timeout_s = timeout_s
        self.transport = transport or HttpxTransport()
        self.runtime_version = runtime_version
        self.weights_sha256 = weights_sha256
        self.quantization = quantization
        self.template_sha256 = template_sha256
        self._api_key = api_key
        self._now = now

    def __repr__(self) -> str:
        return f"OmlxClient(model={self.model!r}, port={self.port})"

    def describe(self) -> dict:
        return {"kind": "local", "runtime": self.runtime, "runtime_version": self.runtime_version,
                "model": self.model, "endpoint": f"loopback:{self.port}",
                "weights_sha256": self.weights_sha256, "quantization": self.quantization,
                "template_sha256": self.template_sha256, "tool_parser": "omlx-server",
                "streaming": self.stream}

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             sampling: Sampling | None = None, seed: int | None = None,
             max_tokens: int, thinking: bool) -> ChatResult:
        payload = build_payload(self.model, messages, tools=tools, sampling=sampling, seed=seed,
                                max_tokens=max_tokens, stream=self.stream)
        payload["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        result = run_request(transport=self.transport, url=f"{self.base_url}/v1/chat/completions",
                             headers=self._headers(), payload=payload,
                             stream=self.stream, timeout_s=self.timeout_s, now=self._now)
        result.request = {"model": self.model, "n_messages": len(messages),
                          "tools": tool_names(tools), "sampling": sampling.to_record() if sampling else None,
                          "seed": seed, "max_tokens": max_tokens, "thinking": bool(thinking),
                          "stream": self.stream}
        result.meta.setdefault("resolved_model", result.meta.get("model", self.model))
        result.meta["params_sent"] = {k: payload[k] for k in payload
                                      if k not in ("messages", "tools", "stream_options")}
        return result
