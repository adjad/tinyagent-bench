"""The 40 phone parity items (spec 11 §4): format, quotas, and the oracle's next step passes each check."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from edat.screen.adapter import PolicyClient
from edat.screen.cases.bind import bind
from edat.screen.cases.store import load_cases
from edat.screen.loop import ScreenLoop, ScreenSettings
from edat.screen.phone_export import ITEM_SCHEMA, QUOTAS, _bucket, _Capture
from edat.screen.world.core import estimate_tokens

ROOT = Path(__file__).resolve().parents[1] / "test_fixtures" / "screen"
ITEMS_FILE = ROOT / "phone" / "parity-items.jsonl"
ITEMS = [json.loads(x) for x in ITEMS_FILE.read_text().splitlines()] if ITEMS_FILE.exists() else []
pytestmark = pytest.mark.skipif(not ITEMS, reason="phone items not exported")


def test_forty_items_in_the_quotas_and_the_manifest_matches():
    assert len(ITEMS) == 40
    cases = {c["id"]: c for c in load_cases(ROOT / "dev")}
    assert dict(Counter(_bucket(cases[i["source_case"]]) for i in ITEMS)) == QUOTAS
    m = json.loads((ROOT / "phone" / "parity-manifest.json").read_text())
    assert m["items"] == 40 and m["sha256"] == hashlib.sha256(ITEMS_FILE.read_bytes()).hexdigest()


def test_item_shape_and_prompt_size():
    for it in ITEMS:
        assert it["schema"] == ITEM_SCHEMA and it["spec"] == "11" and it["prefix_source"] == "oracle_trajectory"
        assert [t["function"]["name"] for t in it["tools"]][:2] == ["click", "type_text"]
        assert it["messages"][0]["role"] == "system" and it["messages"][1]["role"] == "user"
        assert it["check"]["kind"] in ("calls", "either")
        assert sum(estimate_tokens(str(m.get("content") or "")) for m in it["messages"]) < 5600


def test_the_oracles_next_step_passes_and_doing_nothing_fails():
    checks = pytest.importorskip("edat.phone.checks")
    cases = {c["id"]: c for c in load_cases(ROOT / "dev")}
    for it in ITEMS:
        b = bind(cases[it["source_case"]], it["world_seed"])
        cap = _Capture(b)
        ScreenLoop().run(PolicyClient(cap), b, ScreenSettings(max_steps=24))
        turn = cap.steps[it["step_index"]]["turn"]
        calls = [{"name": c["name"], "arguments": c["arguments"]} for c in turn.get("calls") or []]
        today = date.fromisoformat(it["today"])
        assert checks.evaluate(it["check"], calls, turn.get("text") or "", today)[0], it["id"]
        assert not checks.evaluate(it["check"], [], "OK.", today)[0], it["id"]
