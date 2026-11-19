#!/usr/bin/env python3
"""Stand-in exporter: Resources/portable-cases.json -> edat-phone-item/v1 JSONL (+ manifest).

The real 120-item parity subset is exported by the Mac harness (spec 02 §6). This script
only gives the app a file in the same format so the phone code can be built and tested.
It is NOT the published parity subset. Standard library only.

    python3 ios-bench/tools/export_phone_items.py --today 2026-11-18

Stand-in notes: `json` cases become `no_call` with the weak text regex `\\{` (the field
matchers are not expressible in the item format); `max_words` is dropped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE.parent / "Resources"
EXPORTER_VERSION = "standin-1"
ITEM_SCHEMA = "edat-phone-item/v1"
MANIFEST_SCHEMA = "edat-phone-items-manifest/v1"
SOURCE_NOTE = "ios-bench stand-in from portable-cases.json; NOT the published 120-item parity subset"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Not portable between Python re and ICU: lookbehind and possessive quantifiers.
UNPORTABLE = re.compile(r"\(\?<[=!]|[*+?}]\+")


def today_text(today: date) -> str:
    """Day text as the old portable lane wrote it: "Wednesday 2026-11-18"."""
    return WEEKDAYS[today.weekday()] + " " + today.isoformat()


def check_regex(pattern: str, where: str) -> None:
    if UNPORTABLE.search(pattern):
        raise SystemExit(f"{where}: regex {pattern!r} uses lookbehind or a possessive quantifier")
    re.compile(pattern, re.IGNORECASE)


def walk_regexes(node, where: str) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "regex" and isinstance(v, str):
                check_regex(v, where)
            else:
                walk_regexes(v, where)
    elif isinstance(node, list):
        for v in node:
            walk_regexes(v, where)


def calls_check(name: str, args: dict | None) -> dict:
    spec = {"name": name}
    if args:
        spec["args"] = args
    return {"kind": "calls", "same_step": True, "match": "all_any_order", "extra_calls": "allowed",
            "calls": [spec], "forbid": []}


def build_check(expect: dict) -> tuple[dict, dict | None]:
    kind = expect["kind"]
    if kind == "tool_call":
        names = [expect["tool"]] if "tool" in expect else list(expect["any_of"])
        args = expect.get("args")
        if len(names) == 1:
            return calls_check(names[0], args), None
        return {"kind": "either", "options": [calls_check(n, args) for n in names]}, None
    if kind == "no_tool":
        return {"kind": "no_call"}, None
    if kind == "text":
        text = {k: expect[k] for k in ("contains_all", "contains_any", "not_contains") if expect.get(k)}
        return {"kind": "no_call"}, text or None
    if kind == "json":
        return {"kind": "no_call"}, {"regex": r"\{"}
    raise SystemExit(f"unknown expectation kind {kind!r}")


def build_items(bundle: dict, today: date) -> list[dict]:
    system = bundle["system_prompt"].replace("{today}", today_text(today))
    items = []
    for index, case in enumerate(bundle["cases"]):
        msgs = list(case["messages"])
        if not msgs or msgs[0]["role"] != "system":
            msgs = [{"role": "system", "content": system}] + msgs
        check, text = build_check(case["expect"])
        tools = bundle["tool_sets"][case["tool_set"]] if case.get("tool_set") else []
        item = {
            "schema": ITEM_SCHEMA, "id": "ph-" + case["id"], "source_case": case["id"],
            "family": case["family"], "lane": "assistant", "world_seed": None,
            "today": today.isoformat(), "tz": "America/Los_Angeles", "thinking": True,
            "sampling": {"source": "author_recommended", "temperature": 1.0, "top_p": 0.95,
                         "top_k": 64, "seed": 1000 + index, "max_tokens": case["max_tokens"]},
            "messages": msgs, "tools": tools,
            "rendered_prompt": None, "rendered_prompt_sha256": None,
            "check": check, "text": text,
        }
        walk_regexes(item["check"], item["id"])
        walk_regexes(item["text"], item["id"])
        items.append(item)
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate item ids")
    return items


def render_jsonl(items: list[dict]) -> bytes:
    lines = (json.dumps(i, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for i in items)
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_manifest(items: list[dict], jsonl: bytes) -> dict:
    return {"schema": MANIFEST_SCHEMA, "item_count": len(items),
            "jsonl_sha256": hashlib.sha256(jsonl).hexdigest(), "exporter_version": EXPORTER_VERSION,
            "template_sha256": None, "weights_sha256": None, "source": SOURCE_NOTE}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", default=str(RES / "portable-cases.json"))
    ap.add_argument("--out", default=str(RES / "phone-items-standin.jsonl"))
    ap.add_argument("--manifest", default=None, help="default: <out stem>.manifest.json next to --out")
    ap.add_argument("--today", default="2026-11-18", help="YYYY-MM-DD, fixed into every prompt and item")
    args = ap.parse_args(argv)
    bundle = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    items = build_items(bundle, date.fromisoformat(args.today))
    jsonl = render_jsonl(items)
    out = Path(args.out)
    manifest_path = Path(args.manifest) if args.manifest else out.with_name(out.stem + ".manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(jsonl)
    manifest_path.write_text(json.dumps(build_manifest(items, jsonl), indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
    print(f"wrote {out} ({len(items)} items) and {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
