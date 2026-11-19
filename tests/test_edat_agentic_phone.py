"""Phone parity items (spec 02 §6): export, Mac single-step runner, re-grader."""
from __future__ import annotations

import json
import random
from datetime import date

import pytest

from edat.cases.generators import _example
from edat.models.stub import ScriptedClient
from edat.phone.checks import evaluate, parse_gemma4, portable_regex, same_calls
from edat.phone.export import NotExportable, export, item_for
from edat.phone.mac_runner import regrade, run_items

CASES = _example.generate("dev", random.Random(1))
TODAY = date(2026, 11, 18)


def test_items_are_self_contained_and_regrade_matches_the_mac_runner(tmp_path):
    items = [item_for(c, 1, sampling={"source": "fixed_sensitivity", "temperature": 0.6, "top_p": 0.95},
                      thinking=False, n=i + 1) for i, c in enumerate(CASES)]
    for it in items:
        assert it["schema"] == "edat-phone-item/v1" and it["messages"][0]["role"] == "system"
        assert it["messages"][-1]["role"] == "user" and it["tools"]
        json.dumps(it)                                           # plain JSON
    # a model that does the oracle's first step passes; one that calls nothing fails
    good = ScriptedClient([{"calls": [{"name": "complete_reminder", "arguments": {"title": CASES[0]["oracle"][0]["calls"][0]["arguments"]["title"]}}]}] * 3
                          + [{"calls": [{"name": "messages_send", "arguments": CASES[1]["oracle"][0]["calls"][0]["arguments"] | {"text": "late"}}]}] * 3)
    rows = run_items(good, items, trials=3, out=tmp_path / "r.jsonl")
    assert [r["local_pass"] for r in rows[:3]] == [True] * 3
    again = regrade(items, [json.loads(l) for l in (tmp_path / "r.jsonl").read_text().splitlines()])
    assert [g["pass"] for g in again] == [r["local_pass"] for r in rows]
    bad = run_items(ScriptedClient([{"text": "OK."}] * 6), items, trials=3)
    assert not any(r["local_pass"] for r in bad)


def test_multi_turn_cases_are_not_exported():
    case = dict(CASES[1], id="x", followup_turn={"user": "and again", "oracle": [{"text": "ok"}]})
    with pytest.raises(NotExportable):
        item_for(case, 1, sampling={}, thinking=False, n=1)


def test_export_writes_jsonl_and_manifest(tmp_path):
    m = export(CASES, tmp_path)
    assert (tmp_path / "parity-items.jsonl").exists() and m["schema"] == "edat-phone-manifest/v1"
    assert m["items"] == len((tmp_path / "parity-items.jsonl").read_text().splitlines())


@pytest.mark.parametrize("rx,ok", [(r"\b(when|what time)\b[^?]*\?", True), (r"(?<=a)b", False),
                                   (r"a++", False), (r"(?i)x", True), (r"(?s)x", False), (r"\Ax", False)])
def test_regex_portability(rx, ok):
    assert (portable_regex(rx) is None) is ok


def test_checks_and_gemma_parser():
    chk = {"kind": "calls", "calls": [{"name": "get_weather", "args": {"location": {"icontains": "oslo"}}}],
           "forbid": ["web_search"]}
    assert evaluate(chk, [{"name": "get_weather", "arguments": {"location": "Oslo"}}], "", TODAY)[0]
    assert not evaluate(chk, [{"name": "get_weather", "arguments": {"location": "Oslo"}},
                              {"name": "web_search", "arguments": {"query": "x"}}], "", TODAY)[0]
    assert not evaluate(chk, [{"name": "get_weather", "arguments": None}], "", TODAY)[0]
    raw = 'ok <|tool_call>call:get_weather{location:<|"|>Oslo, NO<|"|>,days:2}<tool_call|>'
    calls = parse_gemma4(raw)
    assert calls == [{"name": "get_weather", "arguments": {"location": "Oslo, NO", "days": 2}, "parse_error": None}]
    assert same_calls(calls, [{"name": "get_weather", "arguments": {"days": 2, "location": "Oslo, NO"}}])
