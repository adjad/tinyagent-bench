"""Tool-call format validity (spec 02 §3.11), from the raw adapter output.

Labels (each a format failure, §5.1):

* `malformed`: a call whose arguments were not a JSON object (the adapter's
  `parse_error`), or whose arguments break the tool's schema (a missing
  required argument, a wrong type, a value outside an enum, an unknown
  argument where the schema forbids extras);
* `text_tool_call`: a call written into the visible answer instead of emitted;
* `markup_leak`: chat-template tool-call markup in the answer;
* `think_leak`: reasoning markup in the answer;
* `reasoning_leak`: planning prose addressed to itself as the answer;
* `hallucinated_tool`: a call to a tool the lane does not have.

The text patterns are Toolbench's (scripts/toolbench/scorer.py), so both
harnesses label the same output the same way.
"""
from __future__ import annotations

import re

FORMAT_LABELS = ("malformed", "text_tool_call", "markup_leak", "think_leak", "reasoning_leak",
                 "hallucinated_tool")

THINK_MARKUP = re.compile(r"</?think\b|</?thought>|<\|channel\|?>|<channel\|>", re.I)
TEXT_CALL = re.compile(r"<tool_call>|\{\s*\"name\"\s*:\s*\"[a-z_]+\"\s*,\s*\"(arguments|parameters)\"", re.I)
MARKUP = re.compile(r"<\|tool_call>|<tool_call\|>|<\|\"\|>|\bcall:[A-Za-z_][\w.:-]*\s*\{|<\|im_start\|>|"
                    r"<start_of_turn>|<end_of_turn>|<\|start_header_id\|>")
REASONING_OPENER = re.compile(
    r"^\W*(?:okay|ok|alright|hmm)?[\s,.]*(?:we need to|we should|the user (?:is asking|asks|asked|wants|said)|"
    r"let me (?:check|think|see|look|figure)|i (?:should|need to|will need to) (?:check|call|use|look|figure)|"
    r"first,? i (?:need|should|will))", re.I)
STALL = re.compile(r"^\W*(?:one moment|just a moment|give me a (?:moment|sec(?:ond)?)|hold on|"
                   r"let me (?:check|look|find)(?: that| this)?(?: for you)?\W*$|i'?ll (?:check|look)(?: that| it)? (?:up|now)\W*$)",
                   re.I)

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list,
          "object": dict}


def schema_problems(schema: dict | None, args: dict) -> list[str]:
    """A small JSON-schema check: required, type, enum, additionalProperties: false."""
    if not schema:
        return []
    params = schema.get("parameters", schema) if "parameters" in schema else schema
    props = params.get("properties") or {}
    out = []
    for req in params.get("required") or []:
        if req not in args or args[req] is None:
            out.append(f"missing required argument {req!r}")
    if params.get("additionalProperties") is False:
        for k in args:
            if k not in props:
                out.append(f"unknown argument {k!r}")
    for k, v in args.items():
        p = props.get(k)
        if not isinstance(p, dict) or v is None:
            continue
        t = p.get("type")
        types = t if isinstance(t, list) else [t] if t else []
        if types:
            ok = False
            for name in types:
                py = _TYPES.get(name)
                if py is None:
                    ok = True
                elif name in ("integer", "number") and isinstance(v, bool):
                    continue
                elif name == "integer" and isinstance(v, float) and v.is_integer():
                    ok = True
                elif isinstance(v, py):
                    ok = True
            if not ok:
                out.append(f"argument {k!r} should be {t}, got {type(v).__name__}")
        if "enum" in p and v not in p["enum"]:
            out.append(f"argument {k!r} = {v!r} not in enum")
    return out


def text_labels(final_text: str) -> list[str]:
    labels = []
    if THINK_MARKUP.search(final_text or ""):
        labels.append("think_leak")
    if TEXT_CALL.search(final_text or ""):
        labels.append("text_tool_call")
    if MARKUP.search(final_text or ""):
        labels.append("markup_leak")
    if REASONING_OPENER.search(final_text or ""):
        labels.append("reasoning_leak")
    return labels


def is_stall(final_text: str) -> bool:
    return bool(STALL.search((final_text or "").strip()))
