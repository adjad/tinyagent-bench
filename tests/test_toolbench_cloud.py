"""tinyagent-bench cloud-reference lane (OpenRouter) against a local stub only.

Every OpenRouter response here comes from an in-memory stub
(httpx.MockTransport): no socket is opened (a no_network fixture fails any
connect), nothing is sent to OpenRouter, no key is real (an invented token),
no model or oMLX runs, and the Harbor home is a temp directory.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HARBOR_HOME", str(Path(tempfile.mkdtemp(prefix="toolbench-cloud-")) / ".moe"))
for _var in ("HARBOR_MODEL_ONLY", "HARBOR_MODEL_ONLY_NARRATION_THINKING", "HARBOR_MODEL_LED_ROUTING",
             "HARBOR_PLANNER", "HARBOR_REMOTE_ONLY", "HARBOR_REMOTE_PARITY"):
    os.environ.pop(_var, None)

import httpx  # noqa: E402

from scripts.toolbench import cloud  # noqa: E402
from scripts.toolbench.cases import load_cases  # noqa: E402
from scripts.toolbench.world import build_world  # noqa: E402

KEY = "sk-or-v1-INVENTED-test-token-0000"        # never a real key
HAIKU, LUNA, DEEPSEEK = "anthropic/claude-haiku-5.5", "openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash-1017"
ARGON, CARBON, NEON = "google/gemini-4-argon", "google/gemini-4-carbon", "google/gemini-4-neon"
ALL = (HAIKU, LUNA, DEEPSEEK, ARGON, CARBON, NEON)


# ---- configuration --------------------------------------------------------

def test_the_six_cloud_references_are_configured_as_decided():
    t = cloud.load_models()
    assert set(ALL) <= set(t)
    assert t[HAIKU]["accepts"] == [] and t[LUNA]["accepts"] == ["seed"]
    for m in (DEEPSEEK, ARGON, CARBON, NEON):
        assert set(t[m]["accepts"]) == {"temperature", "top_p", "seed"}, m
    for m in ALL:
        assert ":" not in m and not m.endswith("-latest")
        assert t[m]["price_per_mtok"]["in"] >= 0 and t[m]["price_per_mtok"]["out"] >= 0


@pytest.mark.parametrize("bad", ["anthropic/claude-haiku-5.5:batch", "google/gemini-4-argon-latest", "haiku"])
def test_batch_variants_and_latest_aliases_are_refused(tmp_path, bad):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({bad: {"accepts": [], "price_per_mtok": {"in": 1, "out": 1}}}))
    with pytest.raises(ValueError, match="exact"):
        cloud.load_models(path)


def test_only_accepted_author_sampling_is_sent():
    t = cloud.load_models()
    author = {"temperature": 1.0, "top_p": 0.95}
    assert cloud.sent_sampling(author, t[HAIKU]) == ({}, author)
    assert cloud.sent_sampling(author, t[NEON]) == (author, {})
    assert cloud.provider_pin(t[HAIKU]) is None
    assert cloud.provider_pin({"provider": "deepinfra"}) == {"order": ["deepinfra"], "allow_fallbacks": False,
                                                             "require_parameters": True}


def test_off_variant_needs_a_reasoning_off_option():
    t = cloud.load_models()
    assert cloud.variant_blocked(t[HAIKU], "off") is None
    assert "cannot be turned off" in cloud.variant_blocked(t[ARGON], "off")
    assert "cannot be turned off" in cloud.variant_blocked(t[HAIKU], "off", mandatory=True)
    assert cloud.variant_blocked(t[HAIKU], "narration-on")


def test_policy_and_cli_reach_the_cloud_kind_and_author_settings():
    from scripts import run_toolbench
    args = SimpleNamespace(sampling="", treatment="", profile_as="", engine="openrouter", omlx_port=0,
                           context_window=0, max_output_tokens=0, timeout=600.0, latency_budget=180.0, max_steps=8,
                           thinking="default,off", world_seed=None, seed_base=1000, skip_probe=False, probe=False,
                           allow_unqualified=False, references="", with_references=False,
                           reference_treatment="packaged")
    policy = run_toolbench._resolve_policy(args, [HAIKU, NEON])
    assert policy["kind"] == "cloud_reference" and policy["treatment"] == "cloud" and policy["probe"] == "skipped"
    assert policy["sampling"] == "author" and policy["sampling_params"] == {
        HAIKU: {}, NEON: {"temperature": 1.0, "top_p": 0.95}}


# ---- the key ----------------------------------------------------------------

def test_the_key_comes_from_the_environment_and_leaves_it(monkeypatch):
    monkeypatch.setenv(cloud.KEY_ENV, KEY)
    key, where = cloud.resolve_key()
    assert key == KEY and where == f"env:{cloud.KEY_ENV}" and cloud.KEY_ENV not in os.environ
    with pytest.raises(cloud.KeyUnavailable) as e:
        cloud.resolve_key()
    assert KEY not in str(e.value)


def test_the_key_comes_from_harbors_keychain_item():
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=(KEY + "\n").encode(), stderr=b"")
    key, where = cloud.resolve_key(keychain="openrouter", runner=fake)
    assert key == KEY and where == "keychain:openrouter"
    assert seen[0][:4] == ["/usr/bin/security", "find-generic-password", "-s", "com.harbor.inference"]
    assert seen[0][5] == cloud.keychain_account("openrouter")
    from service.config.provider_credentials import SERVICE, keychain_account
    from service.inference.providers import OPENROUTER_ORIGIN
    assert SERVICE == cloud.KEYCHAIN_SERVICE and OPENROUTER_ORIGIN == cloud.ORIGIN
    assert keychain_account("openrouter", OPENROUTER_ORIGIN) == cloud.keychain_account("openrouter")
    with pytest.raises(cloud.KeyUnavailable):
        cloud.resolve_key(keychain="bad name; rm", runner=fake)


def test_scrub_removes_the_key_bearer_tokens_and_secret_fields():
    row = {"error": f"HTTP 401 for Bearer {KEY}", "steps": [{"note": f"echo {KEY}"}],
           "headers": {"Authorization": "Bearer abc"}, "api_key": "x", "ok": 3}
    out = cloud.scrub(row, [KEY])
    assert KEY not in json.dumps(out) and "abc" not in json.dumps(out)
    assert out["ok"] == 3 and out["api_key"] == cloud.REDACTED


# ---- metering -----------------------------------------------------------------

def test_usage_is_read_from_json_and_from_the_last_sse_chunk():
    body = {"id": "gen-1", "model": HAIKU, "provider": "Anthropic",
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0012,
                      "completion_tokens_details": {"reasoning_tokens": 15},
                      "prompt_tokens_details": {"cached_tokens": 64}}}
    rec = cloud.parse_body(json.dumps(body).encode(), streamed=False)
    assert rec == {"id": "gen-1", "model": HAIKU, "provider": "Anthropic", "prompt_tokens": 100,
                   "completion_tokens": 20, "reasoning_tokens": 15, "cached_tokens": 64, "cost": 0.0012}
    sse = (b'data: {"id":"gen-2","model":"x","provider":"DeepInfra","choices":[]}\n\n'
           b'data: {"id":"gen-2","choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3,"cost":0.5}}\n\n'
           b"data: [DONE]\n\n")
    rec = cloud.parse_body(sse, streamed=True)
    assert rec["provider"] == "DeepInfra" and rec["prompt_tokens"] == 7 and rec["cost"] == 0.5


def test_the_ledger_prices_missing_costs_and_refuses_past_the_budget():
    led = cloud.Ledger(0.01, {DEEPSEEK: {"price_per_mtok": {"in": 1.0, "out": 2.0}}})
    rec = {"prompt_tokens": 2000, "completion_tokens": 1000}
    assert led.charge(DEEPSEEK, rec) == pytest.approx(0.004) and rec["cost_estimated"]
    led.charge(DEEPSEEK, {"cost": 0.007})
    assert led.over()
    with pytest.raises(cloud.BudgetExceeded):
        led.check()
    assert led.to_dict()["refused"] == 1 and led.to_dict()["estimated_costs"] == 1


def test_estimate_uses_prices_measured_sizes_and_skips_blocked_variants():
    t = cloud.load_models()
    rough = cloud.estimate([HAIKU, ARGON], ["a", "b"], trials=3, variants=["default", "off"], table=t)
    assert rough["source"] == "rough defaults" and rough["total_usd"] > 0
    # Argon has no off variant: half Haiku's runs... priced at its own rates.
    assert rough["models"][ARGON]["input_tokens"] * 2 == rough["models"][HAIKU]["input_tokens"]
    rows = [{"case": "a", "steps": [{"prompt_tokens_est": 9000, "output_chars": 400}]}]
    sized = cloud.estimate([HAIKU], ["a"], trials=1, variants=["off"], table=t, sizes=cloud.measured_sizes(rows))
    assert sized["models"][HAIKU]["input_tokens"] == 9000 and sized["models"][HAIKU]["output_tokens"] == 100


# ---- the network guard -------------------------------------------------------

def test_the_guard_opens_exactly_one_https_origin(monkeypatch):
    from scripts.toolbench import runner
    monkeypatch.setattr(runner, "_CLOUD_HOSTS", set())
    monkeypatch.setattr(runner, "_CLOUD_IPS", set())
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda name, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", 443))])
    runner.allow_cloud_origin(cloud.HOST)
    assert socket.getaddrinfo("openrouter.ai", 443)[0][4] == ("192.0.2.7", 443)
    assert runner.net_allowed(socket.AF_INET, ("192.0.2.7", 443))
    for address in (("192.0.2.7", 80), ("192.0.2.8", 443), ("127.0.0.1", 8000), ("127.0.0.1", 8765)):
        assert not runner.net_allowed(socket.AF_INET, address), address
    for name, port in (("api.openai.com", 443), ("openrouter.ai.evil.example", 443), ("openrouter.ai", 80),
                       ("localhost", 8000)):
        with pytest.raises(PermissionError):
            socket.getaddrinfo(name, port)
    with pytest.raises(RuntimeError):
        runner.allow_cloud_origin("example.com")


# ---- a whole turn through Harbor's cloud mode, against the stub ---------------

def sse(*chunks) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


class StubOpenRouter:
    """/api/v1/models and /api/v1/chat/completions, whole reply or stream; every
    reply carries OpenRouter's model, provider and usage fields."""

    def __init__(self):
        self.requests: list[dict] = []
        self.headers: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert (request.url.scheme, request.url.host) == ("https", "openrouter.ai"), request.url
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": m, "context_length": 1000000, "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                 "supported_parameters": ["tools", "reasoning"], "reasoning": {"mandatory": m.startswith("google/")}}
                for m in ALL]})
        assert request.url.path == "/api/v1/chat/completions", request.url.path
        self.headers.append(request.headers.get("authorization", ""))
        body = json.loads(request.content)
        self.requests.append(body)
        after_tool = any(m.get("role") == "tool" for m in body["messages"])
        offered = {t["function"]["name"] for t in body.get("tools") or ()}
        call = not after_tool and "get_upcoming" in offered
        meta = {"id": f"gen-{len(self.requests)}", "model": body["model"], "provider": "StubProvider"}
        usage = {"prompt_tokens": 1000, "completion_tokens": 50, "cost": 0.002,
                 "completion_tokens_details": {"reasoning_tokens": 30}}
        if not body["stream"]:
            msg = {"role": "assistant", "content": "" if call else "Tomorrow you have your appointment."}
            if call:
                msg["tool_calls"] = [{"id": "call_1", "type": "function", "function": {
                    "name": "get_upcoming", "arguments": json.dumps({"period": "tomorrow"})}}]
                msg["reasoning_details"] = [{"type": "reasoning.text", "text": "Check tomorrow.", "index": 0}]
            return httpx.Response(200, json={**meta, "choices": [{"index": 0, "message": msg,
                                                                   "finish_reason": "tool_calls" if call else "stop"}],
                                             "usage": usage})
        if call:
            chunks = [{**meta, "choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": "call_1", "type": "function",
                "function": {"name": "get_upcoming", "arguments": json.dumps({"period": "tomorrow"})}}]}}]},
                {**meta, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
        else:
            chunks = [{**meta, "choices": [{"index": 0, "delta": {"content": "Tomorrow you have your appointment."}}]},
                      {**meta, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        chunks.append({**meta, "choices": [], "usage": usage})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*chunks))


@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def refuse(sock, address):
        attempts.append(address)
        raise PermissionError(f"test: network to {address!r} is blocked")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


@pytest.fixture
def cloud_lane(no_network, monkeypatch):
    from scripts.toolbench import runner
    from service import config
    from service.inference import remote_policy as rp
    stub = StubOpenRouter()
    monkeypatch.setattr(runner, "CLOUD_TABLE", cloud.load_models())
    monkeypatch.setattr(runner, "CLOUD_KEY", [KEY])
    monkeypatch.setattr(runner, "CLOUD_LEDGER", [cloud.Ledger(5.0, cloud.load_models())])
    Client = runner.bench_client_class("openrouter")
    Client.transport_factory = staticmethod(lambda: httpx.MockTransport(stub.handler))
    yield {"runner": runner, "stub": stub, "Client": Client, "net": no_network}
    runner.uninstall_fakes()
    # What Settings' disconnect writes: back to local inference for the next test.
    config._save_overlay({"remote_parity": False, "inference": {"remote_only": {"enabled": False}}})
    rp.remote_only_profile.cache_clear()
    assert rp.remote_only_profile() is None


def _turn(lane, model, *, thinking="default", seed=1001, sampling=None):
    from scripts.toolbench import policy
    from service.config.endpoints import role_target
    runner = lane["runner"]
    world = build_world()
    runner.install_fakes(world)
    binding = runner.bind_cloud_model(model)
    client = lane["Client"](timeout=30, target=role_target("agent"))
    client.sampling = policy.sampling_for("author", model) if sampling is None else sampling
    client.seed, client.thinking = seed, thinking
    case = next(c for c in load_cases() if c.id == "cal_tomorrow").bind(world.facts)
    traj = asyncio.run(runner.run_case(client, model, case, world, timeout=30))
    return binding, client, traj, world, case


def test_a_cloud_turn_is_harbors_model_led_cloud_mode_with_metering(cloud_lane):
    from scripts.toolbench.scorer import score
    runner, stub = cloud_lane["runner"], cloud_lane["stub"]
    binding, client, traj, world, case = _turn(cloud_lane, HAIKU)
    assert binding["treatment"] == "cloud" and binding["harbor_reasoning"] == {"reasoning": {"effort": "medium"}}
    assert client.engine_kind == "openrouter" and not client.managed and client.wire == "openrouter"
    assert traj["error"] is None, traj["error"]
    assert [c["name"] for s in traj["steps"] for c in s["calls"]] == ["get_upcoming"]
    assert "appointment" in traj["final"]
    assert stub.headers and all(h == f"Bearer {KEY}" for h in stub.headers)
    first, second = stub.requests[0], stub.requests[1]
    # Harbor's own cloud rules: whole reply for a replay model, its reasoning option, latency routing.
    assert first["stream"] is False and first["reasoning"] == {"effort": "medium"} and first["max_tokens"] >= 2049
    assert first["provider"] == {"sort": "latency"}
    # Haiku takes no temperature, top_p or seed; nothing local leaks into a cloud request.
    for body in stub.requests:
        assert not {"temperature", "top_p", "seed", "chat_template_kwargs", "gpu_power_limit_w"} & set(body)
    # Its reasoning goes back unchanged with its tool call (production replay).
    replayed = [m for m in second["messages"] if m.get("role") == "assistant" and m.get("tool_calls")]
    assert replayed and replayed[0]["reasoning_details"][0]["text"] == "Check tomorrow."
    metered = [c for s in traj["steps"] for c in s.get("cloud") or ()]
    assert len(metered) == len(stub.requests)
    assert metered[0]["provider"] == "StubProvider" and metered[0]["model"] == HAIKU
    assert metered[0]["prompt_tokens"] == 1000 and metered[0]["reasoning_tokens"] == 30
    assert metered[0]["t_total_s"] is not None and metered[0]["charged_usd"] == 0.002
    assert metered[0]["resolved_differs"] is False
    assert runner.CLOUD_LEDGER[0].spent == pytest.approx(0.002 * len(stub.requests))
    v = score(case, traj, registry_names=runner.install_fakes(world), today=world.today,
              write_tools=runner.write_tool_names(), host_refused=runner.host_refused_tools())
    assert not v.excluded
    assert KEY not in json.dumps(traj, default=str)
    assert cloud_lane["net"] == []


def test_the_off_variant_sends_the_models_reasoning_off_option(cloud_lane):
    _, _, traj, _, _ = _turn(cloud_lane, HAIKU, thinking="off")
    assert traj["error"] is None, traj["error"]
    assert all(b["reasoning"] == {"enabled": False} for b in cloud_lane["stub"].requests)


def test_a_sampling_model_gets_its_author_settings_and_per_request_seeds(cloud_lane):
    _, _, traj, _, _ = _turn(cloud_lane, NEON)
    stub = cloud_lane["stub"]
    assert traj["error"] is None, traj["error"]
    for i, body in enumerate(stub.requests):
        assert body["temperature"] == 1.0 and body["top_p"] == 0.95 and body["seed"] == 1001 * 1000 + i
        assert "reasoning" not in body                 # Harbor has no reasoning option for neon
    # Bench-added replay (cloud_models.json): neon is fetched whole and its reasoning goes back.
    assert stub.requests[0]["stream"] is False
    assert any(m.get("reasoning_details") for m in stub.requests[1]["messages"])
    assert traj["steps"][0]["sent"] == {"temperature": 1.0, "top_p": 0.95, "max_tokens":
                                        stub.requests[0]["max_tokens"], "seed": 1001000}


def test_a_streamed_model_is_metered_from_the_stream_and_a_pin_is_sent(cloud_lane, monkeypatch):
    runner = cloud_lane["runner"]
    runner.CLOUD_TABLE[DEEPSEEK] = {**runner.CLOUD_TABLE[DEEPSEEK], "provider": "stubhost"}
    _, _, traj, _, _ = _turn(cloud_lane, DEEPSEEK)
    stub = cloud_lane["stub"]
    assert traj["error"] is None, traj["error"]
    assert stub.requests[0]["stream"] is True
    assert all(b["provider"] == {"order": ["stubhost"], "allow_fallbacks": False, "require_parameters": True}
               for b in stub.requests)
    assert all("temperature" not in b and b["seed"] for b in stub.requests)   # author: provider default
    metered = [c for s in traj["steps"] for c in s.get("cloud") or ()]
    assert metered[0]["streamed"] and metered[0]["cost"] == 0.002 and metered[0]["t_first_byte_s"] is not None


def test_an_unaccepted_option_is_still_refused_by_harbors_validator(cloud_lane):
    """The bench's widening is per model: Haiku's top_p is refused before sending."""
    _, _, traj, _, _ = _turn(cloud_lane, HAIKU)
    from service.inference import remote_policy as rp
    from service.config.endpoints import EndpointConfigurationError
    profile = rp.remote_only_profile()
    ok = {"model": HAIKU, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100, "stream": False}
    profile.validate_payload(ok)
    for extra in ({"top_p": 0.9}, {"seed": 1}, {"temperature": 0.5}, {"provider": {"order": ["x"]}},
                  {"top_k": 40}):
        with pytest.raises(EndpointConfigurationError):
            profile.validate_payload({**ok, **extra})


def test_the_budget_refuses_the_next_request_before_it_is_sent(cloud_lane):
    cloud_lane["runner"].CLOUD_LEDGER[0] = cloud.Ledger(0.001, cloud.load_models())
    cloud_lane["runner"].CLOUD_LEDGER[0].spent = 0.001
    _, _, traj, _, _ = _turn(cloud_lane, HAIKU)
    assert cloud_lane["stub"].requests == []
    assert cloud_lane["runner"].CLOUD_LEDGER[0].refused >= 1 and traj["error"]


def test_reports_never_mix_cloud_and_local_rows():
    from scripts.toolbench import report as rep
    v = {"passed": True, "labels": [], "seconds": 1.0, "selection_ok": True, "args_ok": True, "safe": True,
         "format_ok": True, "answer_ok": True, "steps": 1, "prompt_tokens_est": 10}
    local = [{"model": "Gemma-Wisp", "case": "a", "trial": 1, "v": v, "key": {"kind": "local"}}]
    cloudr = [{"model": HAIKU, "case": "a", "trial": 1, "v": v, "key": {"kind": "cloud_reference"},
               "steps": [{"cloud": [{"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.01,
                                     "provider": "Anthropic", "model": HAIKU, "t_total_s": 1.2,
                                     "t_first_byte_s": 0.4, "status": 200}]}]}]
    with pytest.raises(ValueError):
        rep.render(local + cloudr)
    with pytest.raises(ValueError):
        rep.versus(local, cloudr, ["Gemma-Wisp"], title="x")
    with pytest.raises(ValueError):
        rep.compare(local, cloudr)
    cloudr[0]["key"]["params_unsupported"] = ["seed", "temperature", "top_p"]
    out = rep.render(cloudr)
    assert "CLOUD REFERENCE SCORECARD" in out and "never ranked" in out and "PAIRED" not in out
    assert "USD total" in out and "Anthropic" in out and "NOT comparable with on-device" in out
    assert "not sent (unsupported): seed, temperature, top_p" in out and "FLAG" not in out
    cloudr[0]["steps"][0]["cloud"][0].update(model="anthropic/claude-haiku-5.5-20261001", resolved_differs=True)
    assert "FLAG: a resolved id differs" in rep.render(cloudr)


def test_a_cloud_row_keeps_its_metering_and_names_what_served_it():
    from scripts import run_toolbench
    from scripts.toolbench.scorer import Verdict
    recs = [{"model": HAIKU, "provider": "Anthropic"}, {"model": HAIKU, "provider": "Amazon Bedrock"},
            {"model": HAIKU, "provider": "Anthropic"}]
    traj = {"steps": [{"calls": [], "cloud": recs[:2]}, {"calls": [], "cloud": recs[2:]}], "final": "ok"}
    case = SimpleNamespace(split="dev", id="chat_joke", category="chat", no_tool=True)
    key = {"kind": "cloud_reference", "requested_model": HAIKU, "run_date": "2026-11-18"}
    row = run_toolbench._row("r", HAIKU, case, 1, traj, Verdict(passed=True), key=key)
    assert row["steps"][0]["cloud"] == recs[:2]                 # stored, so the report can meter it
    k = row["key"]
    assert k["kind"] == "cloud_reference" and k["requested_model"] == HAIKU and k["run_date"] == "2026-11-18"
    assert k["resolved_model"] == HAIKU and k["upstream_provider"] == "Anthropic"
    assert k["upstream_providers"] == ["Amazon Bedrock", "Anthropic"]
    local = run_toolbench._row("r", "Gemma-Wisp", case, 1, {"steps": [{"calls": []}]}, Verdict(passed=True),
                               key={"kind": "local"})
    assert "resolved_model" not in local["key"]


def test_haikus_run_key_says_what_it_was_not_sent():
    t = cloud.load_models()
    plan = cloud.plan(HAIKU, t[HAIKU], sampling={}, harbor_reasoning={"reasoning": {"effort": "medium"}},
                      replayed_by_harbor=True)
    key = cloud.key_fields(plan)
    assert key["requested_model"] == HAIKU and key["params_sent"] == {} and key["params_unsupported"] == ["seed"]
    plan = cloud.plan(HAIKU, t[HAIKU], sampling={"temperature": 1.0, "top_p": 0.9}, harbor_reasoning=None,
                      replayed_by_harbor=True)
    assert cloud.key_fields(plan)["params_unsupported"] == ["seed", "temperature", "top_p"]


def test_compare_and_baseline_refuse_a_cloud_run_even_with_the_mismatch_override(tmp_path, monkeypatch, capsys):
    from scripts import run_toolbench
    from scripts.toolbench import policy as pol
    for name, kind in (("local", "local"), ("cloudrun", "cloud_reference")):
        d = tmp_path / name
        d.mkdir()
        p = pol.build(engine="omlx", port=8000, sampling="author", sampling_params={}, treatment="packaged",
                      profile_as=None, context_window=None, max_output_tokens=None, timeout_s=600,
                      latency_budget_s=180, max_steps=8, thinking=["default"], world_seed=None, seed_base=1000,
                      case_sessions="cleared_per_case", probe="skipped", kind=kind)
        (d / "meta.json").write_text(json.dumps({"policy": p, "models": [], "cases": []}))
        (d / "results.jsonl").write_text("")
    for flag in ("--compare", "--baseline"):
        monkeypatch.setattr(sys, "argv", ["run_toolbench.py", "--report", str(tmp_path / "local"), flag,
                                          str(tmp_path / "cloudrun"), "--allow-policy-mismatch", "--stored"])
        assert run_toolbench.main() == 6
        assert "never compared or paired" in capsys.readouterr().out


def test_the_cli_keeps_held_out_cases_and_unbudgeted_runs_off_the_cloud(monkeypatch, capsys):
    from scripts import run_toolbench
    gate = next(c.id for c in load_cases() if c.split == "gate")
    for argv, msg in ((["--ids", gate, "--cost-budget", "5"], "held out"),
                      ([], "--cost-budget"),
                      (["--with-references", "--cost-budget", "5"], "never run beside local references"),
                      (["--port", "8000", "--cost-budget", "5"], "takes no --port"),
                      (["--thinking", "narration-on", "--cost-budget", "5"], "runs only --thinking")):
        monkeypatch.setattr(sys, "argv", ["run_toolbench.py", "--engine", "openrouter", "--models", HAIKU, *argv])
        with pytest.raises(SystemExit):
            run_toolbench.main()
        assert msg in capsys.readouterr().err, argv
    monkeypatch.setattr(sys, "argv", ["run_toolbench.py", "--engine", "openrouter", "--models",
                                      "anthropic/claude-unlisted", "--cost-budget", "5"])
    with pytest.raises(SystemExit):
        run_toolbench.main()
    assert "no entry" in capsys.readouterr().err


def test_this_file_is_in_the_simulation_manifest():
    path = ROOT / "scripts" / "run_simulation_qa.py"
    if not path.exists():
        pytest.skip("scripts/run_simulation_qa.py is Harbor's release tooling and is absent here, "
                    "so there is no Simulation QA manifest to check")
    manifest = path.read_text()
    assert '"tests/test_toolbench_cloud.py"' in manifest
