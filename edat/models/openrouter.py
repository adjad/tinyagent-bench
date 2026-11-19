"""The hosted cloud-reference client: OpenRouter chat completions (spec 02 §4.4).

What every request carries:

* `provider: {order: [<pinned provider>], allow_fallbacks: false,
  data_collection: "deny", require_parameters: true}`: one upstream provider
  for the whole run, no silent fallback, data-collection opt-out, and a
  provider that does not apply a sent parameter is refused rather than
  ignoring it.
* `tool_choice: "auto"` and `parallel_tool_calls: true` when tools are given.
  Structured outputs are never used to force calls.
* `usage: {include: true}` so the response carries token counts and cost.
* `reasoning`: `{enabled: true}` for the thinking-on entry; `{enabled: false}`
  for thinking-off, or `{effort: "minimal"}` (labelled "minimal reasoning")
  for a model that cannot switch it off.

Sampling and seed are sent only where the model accepts them
(edat/hosted/catalog.py). Anything omitted is recorded in
`meta.params_omitted`, with `seed: unsupported` and
`sampling.source: provider_default` as the spec asks.

The API key is read from an environment variable at request time
(`OPENROUTER_API_KEY` by default). It is never stored on the client, never
put in `ChatResult.request` or `meta`, and never written anywhere. The client
talks only to https://openrouter.ai; a test may point it at a loopback fake.

A provider error that rejects the model's own malformed tool call is the
model's failure: it comes back as a ChatResult whose single tool call has a
`parse_error`, finish reason `provider_rejected_tool_call`. Transport errors,
rate limits and outages raise EngineError and the trial is excluded.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from edat.hosted.catalog import NOT_COMPARABLE, HostedModel, lookup
from edat.models.base import (ChatResult, ConfigurationError, Sampling, ToolCall, tool_names)
from edat.models.openai_compat import HttpxTransport, Transport, build_payload, run_request

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_KEY_ENV = "OPENROUTER_API_KEY"

# Error text that means the provider refused the call the model wrote.
_MALFORMED_CALL = re.compile(
    r"(tool[ _-]?call|function[ _-]?call|tool_use|arguments?).{0,80}"
    r"(invalid|malformed|parse|json|could not|failed)|"
    r"(invalid|malformed|failed to parse).{0,80}(tool[ _-]?call|function[ _-]?call|arguments?)",
    re.I | re.S)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OpenRouterClient:
    runtime = "openrouter"

    def __init__(self, model: str, *, provider: str, key_env: str = DEFAULT_KEY_ENV,
                 base_url: str = OPENROUTER_BASE_URL, stream: bool = True, timeout_s: float = 300.0,
                 transport: Transport | None = None, catalog_entry: HostedModel | None = None,
                 now: Callable[[], float] = time.perf_counter,
                 utc_now: Callable[[], str] = _utc_now) -> None:
        if not provider:
            raise ConfigurationError("a hosted run pins exactly one upstream provider; pass provider=")
        self.entry = catalog_entry or lookup(model)
        self.model = model
        self.provider = provider
        self.key_env = key_env
        self.base_url = self._check_origin(base_url)
        self.stream = stream
        self.timeout_s = timeout_s
        self.transport = transport or HttpxTransport()
        self._now = now
        self._utc_now = utc_now

    @staticmethod
    def _check_origin(base_url: str) -> str:
        url = base_url.rstrip("/")
        if url == OPENROUTER_BASE_URL:
            return url
        parts = urlsplit(url)
        if parts.scheme == "http" and (parts.hostname or "") in ("127.0.0.1", "::1", "localhost"):
            return url   # a loopback fake server in tests
        raise ConfigurationError(f"the hosted client talks only to {OPENROUTER_BASE_URL}, got {base_url!r}")

    def __repr__(self) -> str:
        return f"OpenRouterClient(model={self.model!r}, provider={self.provider!r})"

    def describe(self) -> dict:
        return {"kind": "cloud_reference", "runtime": self.runtime, "requested_model": self.model,
                "upstream_provider_pinned": self.provider, "allow_fallbacks": False,
                "data_collection": "deny", "accepts_sampling": self.entry.accepts_sampling,
                "accepts_seed": self.entry.accepts_seed, "efficiency_label": NOT_COMPARABLE}

    # ----------------------------------------------------------------- mapping
    def reasoning_option(self, thinking: bool) -> tuple[dict, str]:
        if thinking:
            return {"enabled": True}, "on"
        if self.entry.reasoning_off:
            return {"enabled": False}, "off"
        return {"effort": "minimal"}, "minimal reasoning"

    def _key(self) -> str:
        key = os.environ.get(self.key_env, "")
        if not key:
            raise ConfigurationError(f"set {self.key_env} to run hosted references (it is read, never stored)")
        return key

    def _rejected_call(self, status: int, body: Any) -> ChatResult | None:
        err = body.get("error") if isinstance(body, dict) else body
        text = err.get("message", "") if isinstance(err, dict) else str(err or "")
        if isinstance(err, dict) and isinstance(err.get("metadata"), dict):
            text += " " + str(err["metadata"].get("raw", ""))
        if status not in (200, 400, 422) or not _MALFORMED_CALL.search(text):
            return None
        call = ToolCall(id="rejected_0", name="", arguments_raw=text[:2000], arguments=None,
                        parse_error=f"provider rejected the model's tool call: {text[:300]}")
        return ChatResult(text="", tool_calls=[call], finish_reason="provider_rejected_tool_call",
                          raw=body, meta={"provider_rejected_call": True, "http_status": status})

    # ----------------------------------------------------------------- chat
    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             sampling: Sampling | None = None, seed: int | None = None,
             max_tokens: int, thinking: bool) -> ChatResult:
        omitted: dict[str, Any] = {}
        send_sampling = sampling
        if sampling is not None and sampling.params() and not self.entry.accepts_sampling:
            omitted["sampling"] = sampling.params()
            send_sampling = None
        send_seed = seed
        if seed is not None and not self.entry.accepts_seed:
            omitted["seed"] = seed
            send_seed = None
        payload = build_payload(self.model, messages, tools=tools, sampling=send_sampling,
                                seed=send_seed, max_tokens=max_tokens, stream=self.stream)
        if tools:
            payload["parallel_tool_calls"] = True
        payload["provider"] = {"order": [self.provider], "allow_fallbacks": False,
                               "data_collection": "deny", "require_parameters": True}
        payload["usage"] = {"include": True}
        reasoning, reasoning_label = self.reasoning_option(thinking)
        payload["reasoning"] = reasoning
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._key()}",
                   "X-Title": "EDAT benchmark"}
        started = self._utc_now()
        result = run_request(transport=self.transport, url=f"{self.base_url}/chat/completions",
                             headers=headers, payload=payload, stream=self.stream,
                             timeout_s=self.timeout_s, now=self._now, on_http_error=self._rejected_call)
        del headers
        sampling_record = (send_sampling.to_record() if send_sampling and send_sampling.params()
                           else {"source": "provider_default"})
        result.request = {"model": self.model, "n_messages": len(messages), "tools": tool_names(tools),
                          "sampling": sampling_record,
                          "seed": send_seed if send_seed is not None else ("unsupported" if seed is not None else None),
                          "max_tokens": max_tokens, "thinking": bool(thinking),
                          "reasoning": reasoning, "stream": self.stream}
        resolved = result.meta.get("model") or None
        params_sent = {k: v for k, v in payload.items() if k not in ("messages", "tools", "stream_options")}
        result.meta.update({
            "requested_model": self.model,
            "resolved_model": resolved,
            "resolved_model_differs": bool(resolved) and resolved != self.model,
            "upstream_provider": result.meta.get("provider"),
            "upstream_provider_pinned": self.provider,
            "params_sent": params_sent,
            # require_parameters=true: a provider that cannot apply a sent parameter is refused,
            # so everything sent was accepted. OpenRouter does not echo applied values.
            "params_accepted": {k: v for k, v in params_sent.items()
                                if k in ("temperature", "top_p", "top_k", "min_p", "repetition_penalty",
                                         "presence_penalty", "seed", "max_tokens", "reasoning")},
            "params_omitted": omitted,
            "reasoning_entry": reasoning_label,
            "request_utc": started,
            "efficiency_label": NOT_COMPARABLE,
        })
        result.meta.pop("model", None)
        result.meta.pop("provider", None)
        return result
