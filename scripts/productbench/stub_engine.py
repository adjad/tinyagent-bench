"""A scripted stand-in for the local engine, patched into one isolated backend.

For the harness's own dry runs only: it replaces the methods of
service.inference.omlx_client.OMLXClient inside the backend process, so the
real agent loop, tools, confirmations and Brief/Day code run against a model
that answers from a script and never opens a socket. It says nothing about
any real model's quality.

Script (JSON):

    {"rules": [
        {"match": "what('?s| is) on (today|my calendar)", "tool": "get_upcoming",
         "family": "calendar", "args": {"days": 1}, "answer": "Today: {result}"},
        {"match": "text (\\\\w+)", "tool": "send_message", "family": "messages_contacts",
         "args": {"to": "Maya Ortiz", "text": "On my way"}},
        {"match": "capital of", "say": "I can't find that in your mail or calendar."}
     ],
     "default": "I don't have that information.",
     "system_rules": [{"match": "asks_of_me", "say": "{}"}]}

Per request: the last user message picks the first matching rule (case-
insensitive regex). A `say` rule answers with text. A `tool` rule calls the
tool when it is offered; when only the discovery tool (get_tool_schemas) is
offered it asks for the rule's family first. Once the tool's result is in the
conversation it answers with `answer`, where {result} is the tool result
squeezed to one paragraph (so the scorers see real facts from the world).
`system_rules` match the system prompt of tool-free background calls (Brief
triage, judge, gists) before the user rules do.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DISCOVERY = "get_tool_schemas"
STUB_MODEL_PREFIX = "stub/"
CALLS: list[dict] = []          # every request the stub answered, for the dry-run report


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def _offered(tools: list[dict] | None) -> list[str]:
    out = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            out.append(fn["name"])
    return out


def _squeeze(result: str, limit: int = 600) -> str:
    flat = re.sub(r"\s+", " ", result).strip()
    return flat[:limit]


class Script:
    def __init__(self, data: dict):
        self.rules = [dict(r, _re=re.compile(r["match"], re.I)) for r in data.get("rules", [])]
        self.system_rules = [dict(r, _re=re.compile(r["match"], re.I))
                             for r in data.get("system_rules", [])]
        self.default = data.get("default", "I don't have that information.")

    @classmethod
    def load(cls, path: str | Path) -> "Script":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def reply(self, messages: list[dict], tools: list[dict] | None) -> dict:
        """The assistant message for this request: {"content": str} or {"tool_calls": [...]}."""
        offered = _offered(tools)
        system = " ".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
        last_user_i = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        user = _text(messages[last_user_i].get("content")) if last_user_i >= 0 else ""
        if not offered:
            for r in self.system_rules:
                if r["_re"].search(system) or r["_re"].search(user):
                    return {"content": r.get("say", "")}
        rule = next((r for r in self.rules if r["_re"].search(user)), None)
        if rule is None:
            return {"content": self.default}
        if "tool" not in rule:
            return {"content": rule.get("say", self.default)}
        after = messages[last_user_i + 1:]
        called = {}
        for m in after:
            for call in m.get("tool_calls") or []:
                fn = call.get("function", {})
                called[call.get("id")] = fn.get("name")
        results = [(called.get(m.get("tool_call_id")), _text(m.get("content")))
                   for m in after if m.get("role") == "tool"]
        got = [res for name, res in results if name == rule["tool"]]
        if got:
            return {"content": rule.get("answer", "{result}").replace("{result}", _squeeze(got[-1]))}
        step = len(results)
        if rule["tool"] in offered:
            return {"tool_calls": [{"id": f"stub_{step}", "type": "function", "function": {
                "name": rule["tool"], "arguments": json.dumps(rule.get("args", {}))}}]}
        if DISCOVERY in offered and not any(name == DISCOVERY for name, _ in results):
            args = {"families": [rule.get("family", "other")], "tools": [rule["tool"]]}
            return {"tool_calls": [{"id": f"stub_{step}", "type": "function", "function": {
                "name": DISCOVERY, "arguments": json.dumps(args)}}]}
        return {"content": rule.get("unavailable", self.default)}


def install(script: Script, usage_log: Path | None = None) -> None:
    """Replace the engine client's network methods with the script. Backend process only.

    With `usage_log`, each answered request appends a usage line shaped like the
    real tee's (serve.install_usage_tee), with sizes estimated at 4 characters a
    token, so a dry run exercises the token, cost and spend-limit accounting."""
    import time
    from service.inference import omlx_client as oc

    def _message(model, messages, tools):
        reply = script.reply(messages, tools)
        CALLS.append({"model": model, "tools": len(tools or []), "reply": reply})
        if usage_log is not None:
            prompt = sum(len(_text(m.get("content"))) for m in messages) // 4
            done = len(json.dumps(reply)) // 4
            try:
                with usage_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": time.time(), "host": "stub", "status": 200,
                                        "sent": {"model": model, "tools": len(tools or [])},
                                        "prompt_tokens": prompt, "completion_tokens": done,
                                        "model": model, "provider": "stub", "elapsed_s": 0.0,
                                        "first_byte_s": 0.0, "stub": True}) + "\n")
            except OSError:
                pass
        msg = {"role": "assistant", "content": reply.get("content"),
               "reasoning_content": None, "tool_calls": reply.get("tool_calls")}
        return msg, ("tool_calls" if reply.get("tool_calls") else "stop")

    async def chat(self, model, messages, *, tools=None, tool_choice=None, temperature=None,
                   max_tokens=2048, engine_headers=None, use_remaining_context=False, **extra):
        msg, finish = _message(model, messages, tools)
        return {"id": "stub", "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    async def stream_events(self, model, messages, *, tools=None, tool_choice=None,
                            temperature=None, max_tokens=2048, use_remaining_context=False, **extra):
        msg, _ = _message(model, messages, tools)
        if msg.get("content"):
            yield {"kind": "content", "text": msg["content"]}
        yield {"kind": "final", "message": msg}

    async def stream(self, model, messages, **kwargs):
        msg, _ = _message(model, messages, kwargs.get("tools"))
        if msg.get("content"):
            yield msg["content"]

    async def _noop(self, *args, **kwargs):
        return None

    async def health(self):
        return {"status": "ok", "stub": True}

    async def models(self):
        return [m for m in _roster()]

    async def loaded_models(self):
        return [m for m in _roster()]

    async def status(self):
        return {"status": "ok", "stub": True, "loaded": _roster()}

    async def engine_activity(self):
        return {"active": 0, "stub": True}

    for name, fn in {"chat": chat, "stream_events": stream_events, "stream": stream,
                     "ensure_only": _noop, "_ensure_managed": _noop, "warm": _noop,
                     "load": _noop, "unload": _noop, "health": health, "models": models,
                     "loaded_models": loaded_models, "status": status,
                     "engine_activity": engine_activity,
                     "_remote_models_ready": _noop}.items():
        if hasattr(oc.OMLXClient, name):
            setattr(oc.OMLXClient, name, fn)


def _roster() -> list[str]:
    try:
        from service.config import role_to_model
        return sorted({role_to_model(r) for r in ("agent", "fast")})
    except Exception:  # noqa: BLE001
        return []
