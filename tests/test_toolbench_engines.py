"""Toolbench's engine abstraction: an OpenAI-compatible server as the model under test.

Every engine here is an in-memory fake (httpx.MockTransport): no socket is
opened (a no_network fixture fails any connect), no model or oMLX runs, and the
Harbor home is a temp directory.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HARBOR_HOME", str(Path(tempfile.mkdtemp(prefix="toolbench-engines-")) / ".moe"))
for _var in ("HARBOR_MODEL_ONLY", "HARBOR_MODEL_ONLY_NARRATION_THINKING", "HARBOR_MODEL_LED_ROUTING", "HARBOR_PLANNER"):
    os.environ.pop(_var, None)

import httpx  # noqa: E402

from scripts.toolbench.cases import load_cases  # noqa: E402
from scripts.toolbench.world import build_world  # noqa: E402

MODEL = "qwen3-1.7b-q4_k_m"     # an invented id served by the fake engine
PORT = 18080


def sse(*chunks) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def delta(**d):
    return {"choices": [{"index": 0, "delta": d}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


class FakeEngine:
    """An OpenAI-compatible server: /v1/models, /v1/chat/completions (stream or not)."""

    def __init__(self, models=(MODEL,)):
        self.models, self.requests = list(models), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": m, "owned_by": "llamacpp"}
                                                                         for m in self.models]})
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404, json={"error": "not found"})
        body = json.loads(request.content)
        self.requests.append(body)
        after_tool = any(m.get("role") == "tool" for m in body["messages"])
        offered = {t["function"]["name"] for t in body.get("tools") or ()}
        if body.get("stream"):
            if not after_tool and "get_upcoming" in offered:
                args = json.dumps({"period": "tomorrow"})
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
                    delta(reasoning="The user wants tomorrow's events."),
                    delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                                       "function": {"name": "get_upcoming", "arguments": args}}]),
                    finish("tool_calls")))
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
                delta(content="Tomorrow you have your appointment."), finish("stop")))
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "OK"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 1}})


@pytest.fixture
def no_network(monkeypatch):
    import socket
    attempts = []

    def refuse(sock, address):
        attempts.append(address)
        raise PermissionError(f"test: network to {address!r} is blocked")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


@pytest.fixture
def openai_engine(tmp_path, monkeypatch, no_network):
    """The local endpoint pinned to the fake engine's port, an OpenAI bench client class on it."""
    from scripts.toolbench import runner
    from service import config
    (tmp_path / "model_settings.json").write_text(json.dumps({"models": {}}))
    monkeypatch.setattr(config, "OMLX_MODEL_SETTINGS", tmp_path / "model_settings.json")
    runner.pin_endpoint(PORT)
    engine = FakeEngine()
    Client = runner.bench_client_class("openai")
    Client.transport_factory = staticmethod(lambda: httpx.MockTransport(engine.handler))
    yield {"runner": runner, "engine": engine, "Client": Client, "net": no_network}
    runner.uninstall_fakes()
    runner.bind_model(MODEL)
    runner._restore_aliases()
    runner.pin_endpoint(runner.DEFAULT_OMLX_PORT)


def test_network_rule_allows_exactly_the_engine_port(monkeypatch):
    import socket
    from scripts.toolbench import runner
    monkeypatch.setattr(runner, "_OMLX_PORT", PORT)
    assert runner.net_allowed(socket.AF_INET, ("127.0.0.1", PORT))
    assert runner.net_allowed(socket.AF_INET6, ("::1", PORT, 0, 0))
    for address in (("127.0.0.1", 8000), ("127.0.0.1", 8765), ("10.0.0.2", PORT), ("example.com", 443)):
        assert not runner.net_allowed(socket.AF_INET, address), address
    assert runner.net_allowed(socket.AF_UNIX, "/tmp/sock")   # not IP: untouched, as before


def test_isolate_refuses_harbor_ports_and_unknown_engines():
    from scripts.toolbench import runner
    for port in runner.HARBOR_PORTS:
        with pytest.raises(ValueError, match="Harbor's own"):
            runner.isolate(Path(tempfile.mkdtemp()), omlx_port=port, engine="openai")
    with pytest.raises(ValueError, match="engine must be"):
        runner.isolate(Path(tempfile.mkdtemp()), omlx_port=PORT, engine="vllm-remote")
    with pytest.raises(ValueError):
        runner.bench_client_class("nope")


def test_openai_client_owns_no_residency_and_skips_the_omlx_transport(openai_engine):
    from service.inference.attributed_transport import CredentialTransport
    from service.inference.inference_errors import ModelNotDownloadedError
    client = openai_engine["Client"](timeout=5)
    assert client.engine_kind == "openai" and client.managed and client.provider.name == "openai-compatible"
    assert client.base_url == f"http://127.0.0.1:{PORT}"
    assert not isinstance(client._client._transport, CredentialTransport)

    async def go():
        assert await client.models() == [MODEL]
        assert await client.loaded_models() == [MODEL]
        await client.make_resident(MODEL)          # listed: resident, nothing sent
        with pytest.raises(ModelNotDownloadedError):
            await client.make_resident("not-served")
        for call in (client.load(MODEL), client.unload(MODEL), client.status()):
            with pytest.raises(openai_engine["runner"].ResidencyNotManaged):
                await call
    asyncio.run(go())
    assert openai_engine["engine"].requests == [] and openai_engine["net"] == []


def test_a_model_led_turn_runs_through_the_openai_engine(openai_engine):
    """Production's model-led Ask turn, unchanged, against an OpenAI-compatible
    server: the call is reassembled by Harbor's own stream parser, the fixed
    policy and per-request seeds are on every request, the reasoning delta is
    read, and oMLX's power-cap field is never sent."""
    from scripts.toolbench import policy
    from scripts.toolbench.scorer import score
    from service.config.endpoints import role_target
    runner, engine = openai_engine["runner"], openai_engine["engine"]
    world = build_world()
    names = runner.install_fakes(world)
    binding = runner.bind_model(MODEL, lists={"tool_capable": True}, context_window=16384)
    assert binding["treatment"] == "probe" and binding["context_window"] == 16384
    assert binding["lists"]["tool_capable"] and not binding["lists"]["thinking_required_after_tool"]
    client = openai_engine["Client"](timeout=30, target=role_target("agent"))
    client.sampling = policy.sampling_for("fixed", MODEL)
    client.seed = 1001
    case = next(c for c in load_cases() if c.id == "cal_tomorrow").bind(world.facts)

    traj = asyncio.run(runner.run_case(client, MODEL, case, world, timeout=30))

    assert traj["error"] is None and not traj["timeout"], traj["error"]
    assert [c["name"] for s in traj["steps"] for c in s["calls"]] == ["get_upcoming"]
    assert traj["steps"][0]["reasoning_chars"] > 0          # `reasoning` delta read as reasoning
    assert "appointment" in traj["final"]
    assert len(engine.requests) == len(traj["steps"]) >= 2
    for i, body in enumerate(engine.requests):
        assert {k: body[k] for k in policy.FIXED} == policy.FIXED
        assert body["seed"] == 1001 * 1000 + i                  # per request: a retry re-samples
        assert "gpu_power_limit_w" not in body
    assert traj["steps"][0]["sent"]["seed"] == 1001000 and traj["steps"][0]["sent"]["temperature"] == 0.6
    v = score(case, traj, registry_names=names, today=world.today, write_tools=runner.write_tool_names(),
              host_refused=runner.host_refused_tools())
    assert not v.excluded and "engine_error" not in v.labels
    assert openai_engine["net"] == []


def test_engine_info_reads_llama_cpp_props_without_the_template(openai_engine):
    runner = openai_engine["runner"]

    def handler(request):
        if request.url.path == "/props":
            return httpx.Response(200, json={"chat_template": "{{ x }}" * 500, "build_info": "b5000",
                                             "model_path": "/models/qwen3.gguf",
                                             "default_generation_settings": {"n_ctx": 16384,
                                                                             "params": {"top_k": 40}}})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": MODEL, "owned_by": "llamacpp"}]})
        return httpx.Response(404)
    Client = runner.bench_client_class("openai")
    Client.transport_factory = staticmethod(lambda: httpx.MockTransport(handler))
    client = Client(timeout=5)
    info = asyncio.run(runner.engine_info(client, MODEL))
    assert info["runtime"] == "llama.cpp" and info["model_row"]["id"] == MODEL
    assert "chat_template" not in info["props"] and len(info["props"]["chat_template_sha256"]) == 64
    assert info["props"]["model_path"] == "/models/qwen3.gguf"
    settings = asyncio.run(runner.engine_settings(client, MODEL))
    assert settings["source"] == "llama.cpp /props"
    assert settings["settings"]["default_generation_settings.params.top_k"] == 40


def test_bench_client_policy_keywords():
    from scripts.toolbench import runner
    client = runner.bench_client_class("omlx")(api_key="toolbench-test", base_url="http://127.0.0.1:8000")
    client.seed, client.sampling, client.max_output_tokens = 7, {"temperature": 0.6, "top_k": 0}, 1500
    kw = client._thinking_kw({"max_tokens": 3000, "temperature": None})
    assert kw["seed"] == 7000 and kw["temperature"] == 0.6 and kw["top_k"] == 0 and kw["max_tokens"] == 1500
    client.steps = [{}, {}]
    assert client._thinking_kw({})["seed"] == 7002
    client.seed_mode = "per_trial"
    assert client._thinking_kw({})["seed"] == 7
    client.sampling, client.max_output_tokens = {}, None
    assert "temperature" not in client._thinking_kw({}) and client._thinking_kw({"max_tokens": 3000})["max_tokens"] == 3000


def test_this_file_is_in_the_simulation_manifest():
    path = ROOT / "scripts" / "run_simulation_qa.py"
    if not path.exists():
        pytest.skip("scripts/run_simulation_qa.py is Harbor's release tooling and is absent here, "
                    "so there is no Simulation QA manifest to check")
    manifest = path.read_text()
    assert '"tests/test_toolbench_engines.py"' in manifest
