"""Tests for export_phone_items.py (run: python3 -m pytest tools/test_export_phone_items.py -q)."""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_phone_items as ex  # noqa: E402

RES = Path(__file__).resolve().parents[1] / "Resources"
BUNDLE = json.loads((RES / "portable-cases.json").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 18)


def items():
    return ex.build_items(BUNDLE, TODAY)


def test_today_text():
    assert ex.today_text(TODAY) == "Wednesday 2026-11-18"


def test_one_item_per_case_with_fixed_fields():
    out = items()
    assert len(out) == len(BUNDLE["cases"]) == 35
    first = out[0]
    assert first["schema"] == "edat-phone-item/v1" and first["thinking"] is True
    assert first["tz"] == "America/Los_Angeles" and first["lane"] == "assistant"
    assert first["rendered_prompt"] is None and first["rendered_prompt_sha256"] is None
    assert first["sampling"] == {"source": "author_recommended", "temperature": 1.0, "top_p": 0.95,
                                 "top_k": 64, "seed": 1000, "max_tokens": 1024}
    assert [i["sampling"]["seed"] for i in out] == list(range(1000, 1035))


def test_system_prompt_has_today_filled():
    for item in items():
        assert item["messages"][0]["role"] == "system"
        assert "{today}" not in item["messages"][0]["content"]
        assert "Wednesday 2026-11-18" in item["messages"][0]["content"]


def test_expectation_mapping():
    by = {i["source_case"]: i for i in items()}
    sel = by["rem_water_tonight"]
    assert sel["check"]["kind"] == "calls" and sel["check"]["calls"][0]["name"] == "add_reminder"
    assert sel["check"]["calls"][0]["args"]["when_iso"] == {"date_offset": 0}
    assert sel["tools"] and by["chat_joke"]["check"] == {"kind": "no_call"}
    either = by["msg_mom_said"]["check"]
    assert either["kind"] == "either"
    assert [o["calls"][0]["name"] for o in either["options"]] == ["view_messages", "summarize_messages"]
    assert by["summary_ferry"]["text"]["contains_all"] == ["12 October", "five", "Wren"]
    assert by["summary_ferry"]["text"]["not_contains"] == ["cash only"]
    assert by["extract_receipt"]["text"] == {"regex": r"\{"} and by["extract_receipt"]["tools"] == []


def test_regex_portability_refused():
    with pytest.raises(SystemExit):
        ex.check_regex(r"(?<=a)b", "x")
    with pytest.raises(SystemExit):
        ex.check_regex(r"a++", "x")
    ex.check_regex(r"T14:00|2:30", "x")


def test_committed_files_match_exporter():
    jsonl = ex.render_jsonl(items())
    assert (RES / "phone-items-standin.jsonl").read_bytes() == jsonl
    manifest = json.loads((RES / "phone-items-standin.manifest.json").read_text(encoding="utf-8"))
    assert manifest == ex.build_manifest(items(), jsonl)
    assert manifest["jsonl_sha256"] == hashlib.sha256(jsonl).hexdigest()
    assert manifest["schema"] == "edat-phone-items-manifest/v1" and manifest["item_count"] == 35
    assert manifest["template_sha256"] is None and manifest["weights_sha256"] is None
    assert "NOT the published" in manifest["source"]
