"""The model adapter: stub, oMLX and OpenRouter clients, against fakes only.

No model runs and nothing leaves this machine: the oMLX and OpenRouter clients
are driven through an in-process fake transport, and one test uses a loopback
HTTP server on a free port to exercise the real httpx transport.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from edat.models import (FIXED_SENSITIVITY, ConfigurationError, EngineError, NullClient,
                         OmlxClient, OpenRouterClient, Sampling, ScriptedClient, ScriptExhausted,
                         parse_arguments)
from edat.models.openai_compat import HttpResponse, HttpxTransport

TOOLS = [{"type": "function", "function": {"name": "get_weather", "description": "weather",
          "parameters": {"type": "object", "properties": {"location": {"type": "string"}},
                         "required": ["location"]}}}]
MSGS = [{"role": "user", "content": "weather in oslo"}]


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def post(self, url, headers, payload, *, stream, timeout_s):
        self.sent.append({"url": url, "headers": dict(headers), "payload": json.loads(json.dumps(payload)),
                          "stream": stream})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Tick:
    """A fake clock: each call advances 0.5 s."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.5
        return self.t


def body(content="", calls=None, reasoning=None, finish="stop", usage=None, **top):
    msg = {"role": "assistant", "content": content}
    if calls is not None:
        msg["tool_calls"] = calls
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": usage or {}, **top}


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def sse(*chunks):
    return iter([f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"])


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("raw,ok,expect", [
    ('{"location": "Oslo"}', True, {"location": "Oslo"}),
    ("", True, {}),
    (None, True, {}),
    ({"a": 1}, True, {"a": 1}),
    ('{"location": "Oslo"', False, None),
    ('["Oslo"]', False, None),
    ('"{\\"location\\": \\"Oslo\\"}"', False, None),   # double-encoded: no repair
    ("{location: Oslo}", False, None),
])
def test_parse_arguments_never_repairs(raw, ok, expect):
    text, args, err, _ = parse_arguments(raw)
    assert (err is None) == ok
    assert args == expect
    if isinstance(raw, str):
        assert text == raw          # the raw text is kept byte for byte


# ------------------------------------------------------------------ stub

def test_scripted_client_replays_calls_text_and_malformed_calls():
    c = ScriptedClient([
        {"calls": [{"name": "get_weather", "arguments": {"location": "Oslo"}},
                   {"name": "get_weather", "arguments_raw": '{"location": '}]},
        {"text": "It is 4 C."},
    ])
    r1 = c.chat(MSGS, tools=TOOLS, max_tokens=64, thinking=False)
    assert [tc.name for tc in r1.tool_calls] == ["get_weather", "get_weather"]
    assert r1.tool_calls[0].arguments == {"location": "Oslo"}
    assert r1.tool_calls[1].arguments is None and "invalid JSON" in r1.tool_calls[1].parse_error
    assert len(r1.malformed_calls) == 1 and r1.finish_reason == "tool_calls"
    msg = r1.assistant_message()
    assert msg["tool_calls"][1]["function"]["arguments"] == '{"location": '
    r2 = c.chat(MSGS, max_tokens=64, thinking=False)
    assert r2.text == "It is 4 C." and r2.tool_calls == []
    assert c.requests[0]["tools"] == ["get_weather"]
    with pytest.raises(ScriptExhausted):
        c.chat(MSGS, max_tokens=64, thinking=False)


def test_scripted_engine_error_and_callable_turns():
    c = ScriptedClient([{"engine_error": "transport"},
                        lambda m, t, i: {"text": f"saw {len(m)} messages"}])
    with pytest.raises(EngineError) as e:
        c.chat(MSGS, max_tokens=8, thinking=False)
    assert e.value.kind == "transport"
    assert c.chat(MSGS, max_tokens=8, thinking=False).text == "saw 1 messages"


def test_null_client_never_calls():
    n = NullClient()
    for _ in range(3):
        r = n.chat(MSGS, tools=TOOLS, max_tokens=8, thinking=True)
        assert r.text == "OK." and r.tool_calls == []


# ------------------------------------------------------------------ oMLX

def test_omlx_refuses_non_loopback():
    for url in ("http://192.168.1.10:8000", "https://127.0.0.1:8000", "http://example.com:8000",
                "http://127.0.0.1"):
        with pytest.raises(ConfigurationError):
            OmlxClient("m", base_url=url)


def test_omlx_non_streaming_payload_and_result():
    t = FakeTransport([HttpResponse(200, body(
        "", calls=[call("get_weather", '{"location": "Oslo"}')], reasoning="think", finish="tool_calls",
        usage={"prompt_tokens": 120, "completion_tokens": 9,
               "completion_tokens_details": {"reasoning_tokens": 4}}, model="m-resolved"))])
    c = OmlxClient("m", base_url="http://127.0.0.1:18080", stream=False, transport=t, now=Tick())
    s = Sampling(temperature=1.0, top_p=0.95, top_k=64, source="author_recommended")
    r = c.chat(MSGS, tools=TOOLS, sampling=s, seed=1003, max_tokens=256, thinking=False)
    p = t.sent[0]["payload"]
    assert t.sent[0]["url"] == "http://127.0.0.1:18080/v1/chat/completions"
    assert p["chat_template_kwargs"] == {"enable_thinking": False}
    assert (p["temperature"], p["top_p"], p["top_k"], p["seed"], p["max_tokens"]) == (1.0, 0.95, 64, 1003, 256)
    assert p["tools"] == TOOLS and p["tool_choice"] == "auto"
    assert r.tool_calls[0].arguments == {"location": "Oslo"} and r.reasoning == "think"
    assert (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.reasoning_tokens) == (120, 9, 4)
    assert r.timing.ttft_s is None and r.timing.total_s == pytest.approx(0.5)
    assert r.request["sampling"]["source"] == "author_recommended"
    assert r.meta["resolved_model"] == "m-resolved"


def test_omlx_malformed_arguments_are_the_models_failure_not_an_engine_fault():
    t = FakeTransport([HttpResponse(200, body("", calls=[call("get_weather", '{"location": "Oslo"')],
                                              finish="tool_calls"))])
    r = OmlxClient("m", base_url="http://127.0.0.1:18080", stream=False, transport=t).chat(
        MSGS, tools=TOOLS, max_tokens=64, thinking=True)
    assert len(r.malformed_calls) == 1
    assert r.tool_calls[0].arguments_raw == '{"location": "Oslo"'


def test_omlx_streaming_assembles_calls_reasoning_and_ttft():
    chunks = sse(
        {"choices": [{"delta": {"role": "assistant"}}], "model": "m"},
        {"choices": [{"delta": {"reasoning_content": "hmm "}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "get_weather", "arguments": '{"loca'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'tion": "Oslo"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "b", "function": {"name": "get_weather", "arguments": '{"location": "Rome"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 20}},
    )
    t = FakeTransport([HttpResponse(200, lines=chunks)])
    r = OmlxClient("m", base_url="http://127.0.0.1:18080", transport=t, now=Tick()).chat(
        MSGS, tools=TOOLS, sampling=FIXED_SENSITIVITY, max_tokens=64, thinking=True)
    assert t.sent[0]["payload"]["stream"] is True
    assert t.sent[0]["payload"]["stream_options"] == {"include_usage": True}
    assert [c.arguments for c in r.tool_calls] == [{"location": "Oslo"}, {"location": "Rome"}]
    assert r.reasoning == "hmm " and r.finish_reason == "tool_calls"
    assert r.timing.ttft_s == pytest.approx(0.5) and r.timing.total_s == pytest.approx(1.0)
    assert r.usage.prompt_tokens == 50 and isinstance(r.raw, list) and len(r.raw) == 7


@pytest.mark.parametrize("status,kind", [(500, "server"), (503, "server"), (429, "rate_limit"),
                                         (400, "request"), (401, "auth")])
def test_omlx_http_errors_are_engine_errors(status, kind):
    t = FakeTransport([HttpResponse(status, body={"error": "boom"})])
    with pytest.raises(EngineError) as e:
        OmlxClient("m", base_url="http://127.0.0.1:18080", stream=False, transport=t).chat(
            MSGS, max_tokens=8, thinking=False)
    assert e.value.kind == kind and e.value.status == status


def test_transport_exceptions_propagate_as_engine_errors():
    t = FakeTransport([EngineError("transport", "connection refused")])
    with pytest.raises(EngineError):
        OmlxClient("m", base_url="http://127.0.0.1:18080", transport=t).chat(MSGS, max_tokens=8, thinking=False)


# ------------------------------------------------------------------ OpenRouter

KEY = "sk-or-test-not-a-real-key-0000"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)


def _or(model, responses, **kw):
    t = FakeTransport(responses)
    return OpenRouterClient(model, provider="ProviderA", stream=False, transport=t, now=Tick(),
                            utc_now=lambda: "2026-11-18T12:00:00Z", **kw), t


def test_openrouter_haiku_omits_sampling_and_seed_and_records_it(key):
    c, t = _or("anthropic/claude-haiku-5.5", [HttpResponse(200, body("hi", model="anthropic/claude-haiku-5.5",
                                                                      provider="ProviderA"))])
    r = c.chat(MSGS, tools=TOOLS, sampling=FIXED_SENSITIVITY, seed=7, max_tokens=64, thinking=False)
    p = t.sent[0]["payload"]
    for k in ("temperature", "top_p", "seed"):
        assert k not in p
    assert r.meta["params_omitted"] == {"sampling": {"temperature": 0.6, "top_p": 0.95}, "seed": 7}
    assert r.request["seed"] == "unsupported"
    assert r.request["sampling"] == {"source": "provider_default"}
    assert p["provider"] == {"order": ["ProviderA"], "allow_fallbacks": False, "data_collection": "deny",
                             "require_parameters": True}
    assert p["parallel_tool_calls"] is True and p["tool_choice"] == "auto"
    assert p["usage"] == {"include": True} and p["reasoning"] == {"enabled": False}
    assert "response_format" not in p
    assert r.meta["resolved_model_differs"] is False and r.meta["upstream_provider"] == "ProviderA"


def test_openrouter_gemini_sends_sampling_seed_and_reasoning_on(key):
    c, t = _or("google/gemini-4-argon", [HttpResponse(200, body(
        "hi", model="google/gemini-4-argon-2026-11", provider="ProviderA",
        usage={"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.00042}))])
    r = c.chat(MSGS, sampling=FIXED_SENSITIVITY, seed=7, max_tokens=64, thinking=True)
    p = t.sent[0]["payload"]
    assert (p["temperature"], p["top_p"], p["seed"]) == (0.6, 0.95, 7)
    assert p["reasoning"] == {"enabled": True} and r.meta["reasoning_entry"] == "on"
    assert r.meta["params_omitted"] == {}
    assert r.meta["resolved_model_differs"] is True          # flagged, not hidden
    assert r.usage.cost_usd == pytest.approx(0.00042)
    assert r.meta["efficiency_label"] == "not comparable with on-device numbers"


def test_openrouter_key_is_read_from_env_and_never_recorded(key):
    c, t = _or("openai/gpt-6-luna", [HttpResponse(200, body("hi"))])
    r = c.chat(MSGS, max_tokens=8, thinking=False)
    assert t.sent[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    record = json.dumps(r.to_record()) + repr(c) + json.dumps(c.describe())
    assert KEY not in record
    assert not any(KEY in str(v) for v in vars(c).values())


def test_openrouter_missing_key_and_wrong_origin(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    c, _ = _or("openai/gpt-6-luna", [])
    with pytest.raises(ConfigurationError):
        c.chat(MSGS, max_tokens=8, thinking=False)
    for url in ("https://api.example.com/v1", "http://openrouter.ai/api/v1"):
        with pytest.raises(ConfigurationError):
            OpenRouterClient("openai/gpt-6-luna", provider="P", base_url=url)
    with pytest.raises(ConfigurationError):
        OpenRouterClient("openai/gpt-6-luna", provider="")
    with pytest.raises(KeyError):
        OpenRouterClient("someone/other-model", provider="P")


def test_openrouter_minimal_reasoning_when_it_cannot_be_disabled(key):
    from dataclasses import replace
    from edat.hosted.catalog import lookup
    entry = replace(lookup("deepseek/deepseek-v4.1-flash-1017"), reasoning_off=False)
    c, t = _or("deepseek/deepseek-v4.1-flash-1017", [HttpResponse(200, body("hi"))], catalog_entry=entry)
    r = c.chat(MSGS, max_tokens=8, thinking=False)
    assert t.sent[0]["payload"]["reasoning"] == {"effort": "minimal"}
    assert r.meta["reasoning_entry"] == "minimal reasoning"


def test_openrouter_rejected_malformed_call_is_scored_not_excluded(key):
    err = {"error": {"code": 400, "message": "Provider returned error: failed to parse tool call arguments as JSON"}}
    c, _ = _or("google/gemini-4-neon", [HttpResponse(400, body=err)])
    r = c.chat(MSGS, tools=TOOLS, max_tokens=8, thinking=False)
    assert r.finish_reason == "provider_rejected_tool_call"
    assert len(r.malformed_calls) == 1 and r.meta["provider_rejected_call"] is True


@pytest.mark.parametrize("status", [429, 502, 503])
def test_openrouter_outages_are_excluded(key, status):
    c, _ = _or("google/gemini-4-neon", [HttpResponse(status, body={"error": {"message": "upstream unavailable"}})])
    with pytest.raises(EngineError):
        c.chat(MSGS, tools=TOOLS, max_tokens=8, thinking=False)


def test_openrouter_streaming_captures_ttft_cost_and_provider(key):
    t = FakeTransport([HttpResponse(200, lines=sse(
        {"choices": [{"delta": {"content": "Hel"}}], "model": "openai/gpt-6-luna", "provider": "ProviderA"},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2, "cost": 0.0001}}))])
    c = OpenRouterClient("openai/gpt-6-luna", provider="ProviderA", transport=t, now=Tick())
    r = c.chat(MSGS, max_tokens=8, thinking=False)
    assert r.text == "Hello" and r.timing.ttft_s == pytest.approx(0.5)
    assert r.usage.cost_usd == pytest.approx(0.0001) and r.meta["upstream_provider"] == "ProviderA"


# ------------------------------------------------------------------ real transport, loopback only

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n))
        self.server.seen.append(payload)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in ({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "x", "function": {
                       "name": "get_weather", "arguments": '{"location": "Oslo"}'}}]}}]},
                  {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}):
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def test_httpx_transport_against_a_loopback_fake_server():
    try:
        server = HTTPServer(("127.0.0.1", 0), _Handler)
    except OSError as exc:  # pragma: no cover - sandboxes that forbid binding
        pytest.skip(f"cannot bind a loopback port here: {exc}")
    server.seen = []
    port = server.server_address[1]
    assert port not in (8000, 8765, 8791)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    try:
        c = OmlxClient("fake-model", base_url=f"http://127.0.0.1:{port}", transport=HttpxTransport(),
                       timeout_s=10)
        r = c.chat(MSGS, tools=TOOLS, max_tokens=16, thinking=False)
    finally:
        server.shutdown()
    assert r.tool_calls[0].arguments == {"location": "Oslo"}
    assert server.seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
