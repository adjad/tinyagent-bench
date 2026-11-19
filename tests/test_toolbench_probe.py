"""Toolbench's pre-run probe against fake engines that misbehave in known ways.

Each engine is an in-memory httpx.MockTransport (no socket, model or oMLX):
one that serves a model well, one that truncates prompts like Ollama's 4k
default, one that writes tool calls as text (llama.cpp without --jinja), one
whose stream ends tool calls with finish_reason "stop", one that ignores
enable_thinking=false, and one that writes nothing after a tool result with
thinking off (Gemma 4 E4B's measured habit).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HARBOR_HOME", str(Path(tempfile.mkdtemp(prefix="toolbench-probe-")) / ".moe"))

import httpx  # noqa: E402

MODEL = "lfm2-1.2b-tool"    # invented id
PORT = 18081


@dataclass
class Behaviour:
    window: int = 32768
    overflow: str = "truncate"       # truncate | reject
    usage: bool = True
    calls: str = "structured"        # structured | text | none
    thinking: str = "optional"       # optional | always | none
    empty_after_tool_off: bool = False
    stream_finish: str = "tool_calls"
    text_first_n: int = 0             # the first N tool requests answer with a text call, then structured
    stream_answers: bool = False      # the streamed tool request answers directly (one bad draw)
    refuse_template_kwargs: bool = False   # HTTP 400 on any chat_template_kwargs


class Engine:
    def __init__(self, b: Behaviour, served=(MODEL,)):
        self.b, self.requests, self.served = b, [], list(served)

    def tokens(self, body) -> int:
        return 5 + sum(len(str(m.get("content") or "")) for m in body["messages"]) // 4

    def message(self, body) -> dict:
        b = self.b
        if body.get("tools") and not any(m.get("role") == "tool" for m in body["messages"]):
            self.tool_requests = getattr(self, "tool_requests", 0) + 1
            if self.tool_requests <= b.text_first_n:
                return {"role": "assistant", "content": '<tool_call>{"name": "probe_multiply"}</tool_call>'}
            if body.get("stream") and b.stream_answers:
                return {"role": "assistant", "content": "It is 391."}
        off = (body.get("chat_template_kwargs") or {}).get("enable_thinking") is False
        thinks = b.thinking == "always" or (b.thinking == "optional" and not off)
        reasoning = "Let me work it out." if thinks else ""
        after_tool = any(m.get("role") == "tool" for m in body["messages"])
        if body.get("tools") and not after_tool:
            if b.calls == "structured":
                return {"role": "assistant", "content": "", "reasoning_content": reasoning,
                        "tool_calls": [{"id": "call_7", "type": "function", "function": {
                            "name": "probe_multiply", "arguments": json.dumps({"a": 17, "b": 23})}}]}
            if b.calls == "text":
                return {"role": "assistant", "reasoning_content": reasoning,
                        "content": '<tool_call>{"name": "probe_multiply", "arguments": {"a": 17, "b": 23}}</tool_call>'}
            return {"role": "assistant", "content": "It is 391.", "reasoning_content": reasoning}
        if after_tool:
            if off and b.empty_after_tool_off:
                return {"role": "assistant", "content": "", "reasoning_content": ""}
            return {"role": "assistant", "content": "17 times 23 is 391.", "reasoning_content": reasoning}
        return {"role": "assistant", "content": "42", "reasoning_content": reasoning}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": m} for m in self.served]})
        body = json.loads(request.content)
        self.requests.append(body)
        if self.b.refuse_template_kwargs and "chat_template_kwargs" in body:
            return httpx.Response(400, json={"error": "unknown field chat_template_kwargs"})
        held = self.tokens(body)
        if held > self.b.window:
            if self.b.overflow == "reject":
                return httpx.Response(400, json={"error": "context length exceeded"})
            held = self.b.window
        msg = self.message(body)
        if body.get("stream"):
            chunks = []
            if msg.get("reasoning_content"):
                chunks.append({"choices": [{"index": 0, "delta": {"reasoning_content": msg["reasoning_content"]}}]})
            if msg.get("content"):
                chunks.append({"choices": [{"index": 0, "delta": {"content": msg["content"]}}]})
            for i, tc in enumerate(msg.get("tool_calls") or ()):
                chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [{**tc, "index": i}]}}]})
            reason = self.b.stream_finish if msg.get("tool_calls") else "stop"
            chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]})
            data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)
        out = {"choices": [{"index": 0, "message": msg,
                            "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]}
        if self.b.usage:
            out["usage"] = {"prompt_tokens": held, "completion_tokens": 3}
        return httpx.Response(200, json=out)


@pytest.fixture
def probe_env(monkeypatch):
    import socket
    from scripts.toolbench import runner

    def refuse(sock, address):
        raise PermissionError(f"test: network to {address!r} is blocked")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    runner.pin_endpoint(PORT)

    def run(b: Behaviour, served=(MODEL,), **kw):
        from scripts.toolbench.probe import probe_model
        engine = Engine(b, served)
        Client = runner.bench_client_class("openai")
        Client.transport_factory = staticmethod(lambda: httpx.MockTransport(engine.handler))
        client = Client(timeout=5)
        return asyncio.run(probe_model(client, MODEL, window=16384, **kw)), engine
    yield run
    runner.pin_endpoint(runner.DEFAULT_OMLX_PORT)


def test_a_capable_model_qualifies_and_its_treatment_is_measured(probe_env):
    result, engine = probe_env(Behaviour())
    c = result["classification"]
    assert result["qualified"] and not result["blocking"], result["blocking"]
    assert c["context_verified"] == 16384 and c["context_how"] == "measured"
    assert c["tool_calls"] == "structured" and c["streams_calls"] and c["uses_tool_result"]
    assert c["thinks_by_default"] and c["thinking_off_honoured"] and c["reasoning_field"] == "reasoning_content"
    assert not c["empty_after_tool_when_off"] and c["seed_repeatable"]
    assert result["treatment"] == {"tool_capable": True, "no_thinking_capable": True,
                                   "thinking_required_after_tool": False, "explicit_thinking": False}
    assert all(r["model"] == MODEL for r in engine.requests)


def test_silent_truncation_below_the_tool_minimum_blocks_with_the_engine_hint(probe_env):
    result, _ = probe_env(Behaviour(window=4096))
    assert not result["qualified"]
    assert result["classification"]["context_how"] == "truncated"
    assert result["classification"]["context_verified"] == 4096
    assert any("below Harbor's 8,192-token tool minimum" in b for b in result["blocking"])


def test_a_rejecting_engine_steps_down_and_a_mid_window_only_warns(probe_env):
    result, _ = probe_env(Behaviour(window=12000, overflow="reject"))
    c = result["classification"]
    assert c["context_how"] == "rejected" and 8192 <= c["context_verified"] < 12000
    assert result["qualified"] and any("runs at" in w for w in result["warnings"])
    from scripts.toolbench.probe import run_window
    assert run_window(result, 16384) == c["context_verified"] and run_window(result, None) is None


def test_no_usage_means_the_window_is_unverifiable(probe_env):
    result, _ = probe_env(Behaviour(usage=False))
    assert not result["qualified"] and result["classification"]["context_how"] == "unmeasurable"


def test_text_tool_calls_block_as_the_engines_fault(probe_env):
    result, _ = probe_env(Behaviour(calls="text"))
    assert result["classification"]["tool_calls"] == "text" and not result["qualified"]
    assert any("--jinja" in b for b in result["blocking"])
    assert not result["treatment"]["tool_capable"]


def test_a_model_that_ignores_the_tool_is_scored_not_blocked(probe_env):
    result, _ = probe_env(Behaviour(calls="none"))
    assert result["qualified"] and result["classification"]["tool_calls"] == "none"
    assert any("model's ability" in w for w in result["warnings"])


def test_a_stream_harbor_cannot_reassemble_blocks(probe_env):
    result, _ = probe_env(Behaviour(stream_finish="stop"))
    assert result["classification"]["tool_calls"] == "structured"
    assert not result["classification"]["streams_calls"] and not result["qualified"]
    assert any("stream parser" in b for b in result["blocking"])


def test_ignored_thinking_off_blocks_only_that_variant(probe_env):
    result, _ = probe_env(Behaviour(thinking="always"), need_thinking_off=True)
    assert result["qualified"] and not result["classification"]["thinking_off_honoured"]
    assert "off" in result["variant_blocking"] and not result["treatment"]["no_thinking_capable"]


def test_empty_after_a_tool_with_thinking_off_is_detected(probe_env):
    result, _ = probe_env(Behaviour(empty_after_tool_off=True))
    assert result["classification"]["empty_after_tool_when_off"]
    assert result["treatment"]["thinking_required_after_tool"]


def test_a_model_the_engine_does_not_serve_blocks_at_once(probe_env):
    result, engine = probe_env(Behaviour(), served=("another-model",))
    assert not result["qualified"] and engine.requests == []
    assert "does not serve" in result["blocking"][0]


def test_one_text_draw_is_redrawn_and_does_not_block(probe_env):
    result, _ = probe_env(Behaviour(text_first_n=1))
    assert result["qualified"] and result["classification"]["tool_calls"] == "structured"
    assert result["checks"]["call"]["draws"][0] == "text"


def test_a_streamed_draw_that_answers_directly_only_warns(probe_env):
    result, _ = probe_env(Behaviour(stream_answers=True))
    assert result["qualified"] and not result["classification"]["streams_calls"]
    assert any("one draw" in w for w in result["warnings"])


def test_an_engine_refusing_template_kwargs_has_not_honoured_thinking_off(probe_env):
    result, _ = probe_env(Behaviour(refuse_template_kwargs=True))
    c = result["classification"]
    assert c["thinking_off_honoured"] is False and c["empty_after_tool_when_off"] is False
    assert "engine refused" in result["variant_blocking"]["off"]


def test_run_window_caps_a_production_window_to_the_verified_one():
    from scripts.toolbench.probe import run_window
    probe = {"classification": {"context_verified": 16384}}
    assert run_window(probe, None, production=65536) == 16384
    assert run_window(probe, None, production=8000) is None and run_window(None, None, production=8000) is None


def test_this_file_is_in_the_simulation_manifest():
    path = ROOT / "scripts" / "run_simulation_qa.py"
    if not path.exists():
        pytest.skip("scripts/run_simulation_qa.py is Harbor's release tooling and is absent here, "
                    "so there is no Simulation QA manifest to check")
    manifest = path.read_text()
    assert '"tests/test_toolbench_probe.py"' in manifest
