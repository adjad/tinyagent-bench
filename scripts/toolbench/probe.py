"""Pre-run qualification: what a model and its engine actually support.

Before any case is scored, each model gets a few synthetic requests (nothing
personal, nothing from the world) through the SAME bench client the run uses,
so the answers describe exactly the transport, template and parser its trials
will see. The result is recorded in meta.json["probe"][model] and decides:

  * whether the run is fair at all (`blocking`): a model the engine cannot
    serve, whose window cannot be verified or is below Harbor's 8,192-token
    tool minimum (an engine that silently truncates drops Harbor's
    instructions, which live at the start), or whose tool calls arrive as
    text or cannot be reassembled from a stream (an engine/template fault:
    llama.cpp without --jinja, a parser missing) is stopped before scoring;
  * the benchmark-only per-model treatment (`treatment`), replacing
    models.yaml's hand-kept lists for --treatment probe;
  * the window the run uses: min(policy window, verified window);
  * whether `--thinking off` is a real variant for this model
    (`thinking_off_honoured`).

Reuses service/inference/qualify.py (Harbor's own local-provider
qualification): its tool, prompt, filler text, truncation tolerance, step-down
schedule, minimum window and per-engine hints. The context measurement is
qualify.measure_context's algorithm, sent through the bench client's own HTTP
client so it carries the engine's credential (qualify's own probe sends none,
which oMLX refuses).
"""
from __future__ import annotations

import json
import re
import time

PROBE_VERSION = 1
# A thinking model writes its reasoning out of these; small enough to stay quick.
_TOOL_MAX_TOKENS = 1024
_PLAIN_MAX_TOKENS = 512
_REASONING_TAGS = re.compile(r"<think\b|</think>|<thought>|<\|channel\|?>|<reasoning>|\[THINK\]", re.I)
_SUM = [{"role": "user", "content": "What is 12 plus 30? Reply with just the number."}]


def _message(data: dict | None) -> tuple[dict, str | None]:
    choice = ((data or {}).get("choices") or [{}])[0] or {}
    return (choice.get("message") or {}), choice.get("finish_reason")


def reasoning_of(message: dict) -> tuple[str, str | None]:
    """(reasoning text, where it arrived): reasoning_content, reasoning, or
    inline markup in the content. ("", None) when there is none."""
    for key in ("reasoning_content", "reasoning"):
        if str(message.get(key) or "").strip():
            return str(message[key]), key
    content = str(message.get("content") or "")
    if _REASONING_TAGS.search(content):
        return content, "inline"
    return "", None


def classify_call(message: dict) -> str:
    """How a reply to the probe_multiply request called the tool:
    structured (correct call), wrong_args, text (written as text), none."""
    from scripts.toolbench.scorer import _MARKUP, _TEXT_CALL
    from service.inference.qualify import _TEXT_CALL as QUALIFY_TEXT_CALL
    from service.inference.qualify import _is_multiply
    calls = message.get("tool_calls") or []
    if calls:
        return "structured" if _is_multiply(calls[0]) else "wrong_args"
    content = str(message.get("content") or "")
    if QUALIFY_TEXT_CALL.search(content) or _TEXT_CALL.search(content) or _MARKUP.search(content):
        return "text"
    return "none"


def used_result(message: dict) -> bool:
    return "391" in str(message.get("content") or "").replace(",", "") and not message.get("tool_calls")


def empty(message: dict) -> bool:
    return not str(message.get("content") or "").strip() and not message.get("tool_calls")


async def _post(client, payload: dict) -> dict | None:
    """One non-streaming completion through the bench client's own transport.
    None when the engine refuses it (any non-200), as qualify does."""
    r = await client._client.post(client.api_prefix + "/chat/completions", json=payload)
    if r.status_code != 200:
        return None
    data = r.json()
    return data if isinstance(data, dict) else None


def _prompt_tokens(data):
    from service.inference.qualify import _prompt_tokens as pt
    return pt(data)


async def measure_window(client, model: str, cap: int, sampling: dict) -> tuple[int, str, dict]:
    """qualify.measure_context through `client`: (verified tokens, how, details)."""
    from service.inference.qualify import (_PROBE_OVERSHOOT, _STEP_DOWN, _TRUNCATION_TOLERANCE,
                                           _filler, _verified)

    async def held(text: str) -> int | None:
        return _prompt_tokens(await _post(client, {"model": model, "max_tokens": 4, "stream": False,
                                                   "messages": [{"role": "user", "content": text}],
                                                   **sampling, "temperature": 0}))
    tiny = await held("Say OK.")
    mid_text = _filler(0.25, 600, "CALIBRATE")
    mid = await held(mid_text)
    details = {"tiny_prompt_tokens": tiny, "calibration_prompt_tokens": mid}
    if tiny is None or mid is None or mid <= tiny:
        return 0, "unmeasurable", details
    per_char = (mid - tiny) / max(1, len(mid_text))
    target = int(cap * _PROBE_OVERSHOOT)
    code = "ZEBRA-" + str(int(time.time()) % 9000 + 1000)
    seen = await held(_filler(per_char, target, code) + "\n\nReply with OK.")
    details["sent_tokens_target"] = target
    if seen is not None:
        details["held_tokens"] = seen
        cut = seen < cap and seen < target * _TRUNCATION_TOLERANCE
        return _verified(seen, cap), "truncated" if cut else "measured", details
    for fraction in _STEP_DOWN:
        seen = await held(_filler(per_char, int(cap * fraction), code) + "\n\nReply with OK.")
        if seen is not None:
            details["held_tokens"] = seen
            return _verified(seen, cap), "rejected", details
    return 0, "unmeasurable", details


async def probe_model(client, model: str, *, window: int, sampling: dict | None = None,
                      seed: int = 4242, need_thinking_off: bool = False) -> dict:
    """Run every check for `model`; never raises for an engine's behaviour.

    `window`: the run's window policy (the most it will ask the engine to hold).
    `need_thinking_off`: the run asks for --thinking off, so a model that does
    not honour enable_thinking=false is blocked for that variant."""
    from service.inference.qualify import _ASK, _TOOL, MIN_TOOL_CONTEXT, context_hint
    sampling = dict(sampling or {})
    started = time.monotonic()
    out: dict = {"version": PROBE_VERSION, "model": model, "window_policy": window, "sampling": sampling,
                 "checks": {}, "classification": {}, "treatment": {}, "blocking": [], "warnings": [],
                 "variant_blocking": {}, "errors": []}
    checks, cls = out["checks"], out["classification"]

    def req(messages, *, tools=None, thinking=None, max_tokens=_TOOL_MAX_TOKENS, s=seed):
        p = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False,
             "seed": s, **sampling}
        if tools:
            p["tools"], p["tool_choice"] = tools, "auto"
        if thinking is not None:
            p["chat_template_kwargs"] = {"enable_thinking": thinking}
        return p

    try:
        served = await client.models()
        checks["served"] = model in served
        if not checks["served"]:
            out["blocking"].append(f"the engine does not serve {model!r} (it lists {served[:8]})")
            return _finish(out, started)

        # 1. Window: what the engine holds, from its own token counts.
        verified, how, details = await measure_window(client, model, window, sampling)
        checks["context"] = {"verified": verified, "how": how, **details}
        cls["context_verified"], cls["context_how"] = verified, how
        cls["usage_reported"] = how != "unmeasurable"
        if how == "unmeasurable":
            out["blocking"].append("the engine reports no usable usage.prompt_tokens, so its context window "
                                   "cannot be verified (an engine that silently truncates would drop "
                                   "Harbor's instructions)")
        elif verified < MIN_TOOL_CONTEXT:
            out["blocking"].append(f"verified window {verified:,} tokens ({how}) is below Harbor's "
                                   f"{MIN_TOOL_CONTEXT:,}-token tool minimum. {context_hint(client.base_url, verified)}")
        elif verified < window:
            out["warnings"].append(f"verified window {verified:,} < policy {window:,}: this model runs at "
                                   f"{verified:,}")

        # 2. Structured tool call, thinking as the template defaults. One sample
        # is one draw: a text call is re-drawn twice (other seeds) and blocks
        # only when most draws are text, never on one unlucky sample.
        first, finish = _message(await _post(client, req(_ASK, tools=[_TOOL])))
        kinds = [classify_call(first)]
        if kinds[0] == "text":
            for extra in (1, 2):
                again, _ = _message(await _post(client, req(_ASK, tools=[_TOOL], s=seed + 100 * extra)))
                kinds.append(classify_call(again))
                if again.get("tool_calls") and not first.get("tool_calls"):
                    first = again
        cls["tool_calls"] = ("text" if kinds.count("text") * 2 > len(kinds)
                             else next((k for k in kinds if k != "text"), kinds[0]))
        checks["call"] = {"kind": cls["tool_calls"], "draws": kinds, "finish_reason": finish}
        if cls["tool_calls"] == "text":
            out["blocking"].append("tool calls arrive as text, not structured calls: the engine's template or "
                                   "tool parser is missing (llama.cpp: start it with --jinja), so every case "
                                   "would fail for the engine's reason")
        elif cls["tool_calls"] in ("none", "wrong_args"):
            out["warnings"].append(f"the probe's explicit tool request got {cls['tool_calls']!r}; scored as the "
                                   "model's ability, not blocked")
        if finish not in (None, "tool_calls") and first.get("tool_calls"):
            out["warnings"].append(f"tool calls end with finish_reason={finish!r}; Harbor's managed stream "
                                   "requires 'tool_calls' and would reject them")

        # 3. After a tool result: with thinking default and off.
        if first.get("tool_calls"):
            call = first["tool_calls"][0]
            history = _ASK + [{"role": "assistant", "content": first.get("content") or "", "tool_calls": [call]},
                              {"role": "tool", "tool_call_id": call.get("id") or "call_0", "content": "391"}]
            on, _ = _message(await _post(client, req(history, tools=[_TOOL])))
            off_data = await _post(client, req(history, tools=[_TOOL], thinking=False))
            off, _ = _message(off_data)
            if off_data is None:
                checks["after_tool_off_refused"] = True
            cls["uses_tool_result"] = used_result(on)
            cls["empty_after_tool_when_off"] = off_data is not None and empty(off) and not empty(on)
            checks["after_tool"] = {"default_used_result": used_result(on), "default_empty": empty(on),
                                    "off_used_result": used_result(off), "off_empty": empty(off)}
            if not cls["uses_tool_result"]:
                out["warnings"].append("did not answer with the tool result (391) after it arrived")

        # 4. Thinking: does it think by default, and is thinking-off honoured?
        plain, _ = _message(await _post(client, req(_SUM, max_tokens=_PLAIN_MAX_TOKENS)))
        off_data = await _post(client, req(_SUM, thinking=False, max_tokens=_PLAIN_MAX_TOKENS))
        plain_off, _ = _message(off_data)
        text_on, where = reasoning_of(plain)
        text_off, where_off = reasoning_of(plain_off)
        cls["thinks_by_default"] = bool(text_on)
        cls["reasoning_field"] = where or where_off
        # An engine that refuses chat_template_kwargs (HTTP error) has not
        # honoured thinking-off: it never answered with it.
        cls["thinking_off_honoured"] = off_data is not None and not text_off
        checks["thinking"] = {"default_reasoning_chars": len(text_on), "off_reasoning_chars": len(text_off),
                              "where": where, "where_off": where_off,
                              "answer_ok": "42" in str(plain.get("content") or "")}
        if where == "inline" or where_off == "inline":
            out["warnings"].append("reasoning arrives inline in content (no reasoning parser): Harbor strips "
                                   "<think> but other markup may leak into answers")
        if not cls["thinking_off_honoured"]:
            msg = ("enable_thinking=false is not honoured (the engine refused the request)" if off_data is None
                   else "enable_thinking=false is not honoured (the model still reasons)") + \
                  ": --thinking off would not be what it says"
            out["variant_blocking"]["off"] = msg
            if need_thinking_off:
                out["warnings"].append(msg)

        # 5. Streaming: Harbor's own stream parser reassembles the call.
        streamed = None
        try:
            async for ev in client.stream_events(model, _ASK, tools=[_TOOL], max_tokens=_TOOL_MAX_TOKENS,
                                                 seed=seed, **sampling):
                if ev.get("kind") == "final":
                    streamed = ev.get("message") or {}
        except Exception as e:  # noqa: BLE001 — recorded, judged below
            checks["stream_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            if hasattr(client, "steps"):
                client.steps = []
        stream_kind = classify_call(streamed or {}) if streamed else None
        cls["streams_calls"] = stream_kind == "structured"
        checks["stream"] = {"kind": stream_kind}
        # Blocks only on what is the engine's: a stream Harbor cannot parse, or
        # calls that arrive as text when streamed. A streamed sample that just
        # answered directly or got the arguments wrong is one draw of the model.
        if cls["tool_calls"] == "structured" and (checks.get("stream_error") or stream_kind == "text"):
            out["blocking"].append("a structured call works without streaming but the streamed one "
                                   f"{'fails in Harbor stream parser' if checks.get('stream_error') else 'arrives as text'}"
                                   f" ({checks.get('stream_error') or stream_kind}): trials would end as excluded "
                                   "engine errors or text calls")
        elif cls["tool_calls"] == "structured" and not cls["streams_calls"]:
            out["warnings"].append(f"the streamed probe call came back {stream_kind!r} (one draw; not blocked)")

        # 6. Seed (advisory): the same seeded request twice at temperature 0.8.
        a, _ = _message(await _post(client, {**req(_SUM, max_tokens=64, s=seed + 1), "temperature": 0.8}))
        b, _ = _message(await _post(client, {**req(_SUM, max_tokens=64, s=seed + 1), "temperature": 0.8}))
        cls["seed_repeatable"] = (a.get("content"), reasoning_of(a)[0]) == (b.get("content"), reasoning_of(b)[0])
    except Exception as e:  # noqa: BLE001 — a misbehaving engine is a blocking finding, not a crash
        out["errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
        out["blocking"].append(f"the probe could not finish ({type(e).__name__})")

    # Benchmark-only treatment, derived from what was measured (never models.yaml).
    out["treatment"] = {
        "tool_capable": cls.get("tool_calls") == "structured",
        "no_thinking_capable": bool(cls.get("thinking_off_honoured")),
        "thinking_required_after_tool": bool(cls.get("empty_after_tool_when_off")),
        "explicit_thinking": False,
    }
    return _finish(out, started)


def _finish(out: dict, started: float) -> dict:
    out["qualified"] = not out["blocking"]
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def run_window(probe: dict | None, policy_window: int | None, *, production: int | None = None) -> int | None:
    """The window a model runs at: min(policy, verified). Without a policy
    window: None (production's own), except that a `production` window larger
    than what the engine verified is capped to the verified one (a non-oMLX
    server started with a smaller context would otherwise reject or truncate)."""
    verified = int(((probe or {}).get("classification") or {}).get("context_verified") or 0)
    if not policy_window:
        return verified if (production and verified and verified < production) else None
    return min(int(policy_window), verified) if verified else int(policy_window)


def summary_line(probe: dict) -> str:
    c = probe.get("classification") or {}
    return (f"window {c.get('context_verified', '?')} ({c.get('context_how', '?')}), calls {c.get('tool_calls', '?')}, "
            f"streams {c.get('streams_calls', '?')}, thinks {c.get('thinks_by_default', '?')}, "
            f"off honoured {c.get('thinking_off_honoured', '?')}, "
            f"empty-after-tool-off {c.get('empty_after_tool_when_off', '?')}")


def dumps(probe: dict) -> str:
    return json.dumps(probe, indent=2, default=str)
