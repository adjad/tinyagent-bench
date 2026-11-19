"""Run phone parity items on the Mac in single-step mode, and re-grade phone results.

The Mac sends each item's messages and tools through the model adapter (one
generation, no loop, no tool execution) and writes `edat-phone-result/v1`
lines. `regrade` reads result lines from either device and grades them with
the same checks (edat/phone/checks.py), so there is one scorer. It also
re-parses `raw_text` with EDAT's Gemma 4 text parser; a disagreement with
`parsed_calls` is a parser difference, not a model difference (§6.3, #44).

The phone's own `local_pass` is for display only; the Mac's verdict counts.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from edat.models.base import EngineError, ModelClient, Sampling
from edat.phone.checks import evaluate, parse_gemma4, same_calls

RESULT_SCHEMA = "edat-phone-result/v1"


def _sampling(item: dict) -> Sampling | None:
    s = dict(item.get("sampling") or {})
    src = s.pop("source", "unspecified")
    s.pop("seed", None)
    s.pop("max_tokens", None)
    keep = {k: v for k, v in s.items() if k in ("temperature", "top_p", "top_k", "min_p", "repetition_penalty")}
    return Sampling(**keep, source=src) if keep else None


def run_item(client: ModelClient, item: dict, *, trial: int, max_tokens: int = 512,
             device: str = "Mac") -> dict:
    seed = (item.get("sampling") or {}).get("seed")
    try:
        r = client.chat(item["messages"], tools=item["tools"], sampling=_sampling(item),
                        seed=(seed + trial) if isinstance(seed, int) else None,
                        max_tokens=(item.get("sampling") or {}).get("max_tokens", max_tokens),
                        thinking=bool(item.get("thinking")))
    except EngineError as exc:
        return {"schema": RESULT_SCHEMA, "id": item["id"], "trial": trial, "mode": "messages", "device": device,
                "status": "excluded", "error": f"{exc.kind}: {exc.detail}"}
    parsed = [{"name": c.name, "arguments_raw": c.arguments_raw, "arguments": c.arguments} for c in r.tool_calls]
    ok, labels = evaluate(item["check"], parsed, r.text, date.fromisoformat(item["today"]))
    return {"schema": RESULT_SCHEMA, "id": item["id"], "trial": trial, "mode": "messages", "device": device,
            "runtime": client.describe(), "raw_text": r.text, "reasoning_text": r.reasoning,
            "parsed_calls": parsed, "rejected_calls": [], "finish_reason": r.finish_reason,
            "prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens,
            "ttft_s": r.timing.ttft_s, "total_s": r.timing.total_s, "local_pass": ok, "local_labels": labels}


def run_items(client: ModelClient, items: list[dict], *, trials: int = 3, out: Path | None = None) -> list[dict]:
    rows = [run_item(client, it, trial=t) for it in items for t in range(1, trials + 1)]
    if out is not None:
        out.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows))
    return rows


def regrade(items: list[dict], results: list[dict]) -> list[dict]:
    """The Mac's verdict for each result line, plus parser agreement on its raw text."""
    by_id = {it["id"]: it for it in items}
    out = []
    for r in results:
        it = by_id[r["id"]]
        if r.get("status") == "excluded":
            out.append({"id": r["id"], "trial": r["trial"], "excluded": True})
            continue
        calls = [{"name": c["name"], "arguments": c.get("arguments")} for c in r.get("parsed_calls") or []]
        ok, labels = evaluate(it["check"], calls, r.get("raw_text") or "", date.fromisoformat(it["today"]))
        reparsed = parse_gemma4(r.get("raw_text") or "")
        agree = same_calls(reparsed, calls) if reparsed else None
        out.append({"id": r["id"], "trial": r["trial"], "device": r.get("device"), "family": it["family"],
                    "lane": it["lane"], "pass": ok, "labels": labels, "parser_agreement": agree,
                    "local_pass": r.get("local_pass")})
    return out
