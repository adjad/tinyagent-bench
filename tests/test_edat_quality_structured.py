"""Tests for structured-output scoring (spec 03 section 8). All data invented."""
from __future__ import annotations

import json

import pytest

from edat.quality.structured import aggregate_structured, extract_json, score_structured

# ---- J-001: events ---------------------------------------------------------
EV_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["name", "date", "start", "end", "location"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "date": {"type": "string", "format": "date"},
            "start": {"type": "string", "format": "time"},
            "end": {"type": "string", "format": "time"},
            "location": {"type": "string"},
        },
    },
}
EV_GOLD = [
    {"name": "Dentist", "date": "2027-03-09", "start": "09:30", "end": "10:30", "location": "Harbourview Clinic"},
    {"name": "Choir practice", "date": "2027-03-09", "start": "19:00", "end": "20:30", "location": "St Ida Hall"},
]
EV_CASE = {"schema": EV_SCHEMA, "gold": EV_GOLD, "key_fields": ["name", "date"],
           "field_types": {"name": "text", "date": "date", "start": "time", "end": "time", "location": "text"}}


def ev_answer(**over):
    items = json.loads(json.dumps(EV_GOLD))
    for k, v in over.items():
        items[0][k] = v
    return json.dumps(items)


def test_perfect_events():
    r = score_structured(json.dumps(EV_GOLD), EV_CASE)
    assert r["json_parse_ok"] and r["schema_valid"] and not r["fenced"] and not r["prose_wrapper"]
    assert r["field_accuracy"] == 1.0 and r["item_recall"] == 1.0 and r["item_precision"] == 1.0
    assert r["extra_keys"] == 0 and r["type_errors"] == 0 and r["schema_errors"] == []


def test_j001_9am_fails_format_but_value_normalizes():
    r = score_structured(ev_answer(start="9am"), EV_CASE)
    assert not r["schema_valid"]
    assert any(e["kind"] == "format" and e["path"] == "/0/start" for e in r["schema_errors"])
    assert r["field_accuracy"] < 1.0  # 9am != 09:30 here
    r2 = score_structured(ev_answer(start="9:30am"), EV_CASE)
    assert not r2["schema_valid"] and r2["field_accuracy"] == 1.0  # normalized value still right


def test_j001_notes_is_extra_key():
    items = json.loads(json.dumps(EV_GOLD))
    items[1]["notes"] = "bring score"
    r = score_structured(json.dumps(items), EV_CASE)
    assert r["extra_keys"] == 1 and not r["schema_valid"]
    assert r["field_accuracy"] == 1.0


def test_items_reordered_missing_and_spurious():
    items = [EV_GOLD[1], {"name": "Gym", "date": "2027-03-10", "start": "07:00", "end": "08:00", "location": "x"}]
    r = score_structured(json.dumps(items), EV_CASE)
    assert r["item_recall"] == 0.5 and r["item_precision"] == 0.5 and r["spurious_items"] == 1
    assert r["field_accuracy"] == 0.5  # unmatched gold item: all 5 fields wrong


def test_code_and_text_normalization():
    case = {"schema": None, "gold": {"ref": "fx-20913", "city": "São Paulo"}, "key_fields": [],
            "field_types": {"ref": "code", "city": "text"}}
    r = score_structured('{"ref": "FX-20913", "city": "são  paulo"}', case)
    assert r["field_accuracy"] == 1.0


# ---- J-002: classification ---------------------------------------------------
LABELS = ["bill", "plan", "news", "spam"]
CL_SCHEMA = {"type": "array", "minItems": 8, "maxItems": 8,
             "items": {"type": "object", "required": ["id", "label"], "additionalProperties": False,
                       "properties": {"id": {"type": "integer"}, "label": {"enum": LABELS}}}}
CL_GOLD = [{"id": i + 1, "label": LABELS[i % 4]} for i in range(8)]
CL_CASE = {"schema": CL_SCHEMA, "gold": CL_GOLD, "key_fields": ["id"], "field_types": {"id": "number", "label": "enum"}}


def test_classification_perfect():
    r = score_structured(json.dumps(CL_GOLD), CL_CASE)
    assert r["schema_valid"] and r["field_accuracy"] == 1.0


def test_classification_ninth_invented_label():
    ans = json.loads(json.dumps(CL_GOLD))
    ans[2]["label"] = "reminder"
    r = score_structured(json.dumps(ans), CL_CASE)
    assert not r["schema_valid"]
    assert any(e["kind"] == "enum" and e["path"] == "/2/label" for e in r["schema_errors"])
    assert r["field_accuracy"] == pytest.approx(15 / 16)


def test_classification_array_of_seven():
    r = score_structured(json.dumps(CL_GOLD[:7]), CL_CASE)
    assert not r["schema_valid"]
    assert any(e["kind"] == "count" for e in r["schema_errors"])
    assert r["item_recall"] == 7 / 8 and r["field_accuracy"] == 14 / 16


# ---- E-002: payments with traps ---------------------------------------------
PAY_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["payee", "amount", "currency", "due"],
              "additionalProperties": False,
              "properties": {"payee": {"type": "string"}, "amount": {"type": "number"},
                             "currency": {"type": "string"}, "due": {"type": "string", "format": "date"}}}}
PAY_GOLD = [
    {"payee": "Northfield Energy", "amount": 1250.40, "currency": "EUR", "due": "2027-04-15"},
    {"payee": "Test Water Co", "amount": 312.75, "currency": "EUR", "due": "2027-04-20"},
]
PAY_TRAPS = [
    {"trap_type": "status", "match": {"payee": "Paid Gym Ltd"}, "rule": "must_be_absent"},
    {"trap_type": "owner", "match": {"payee": "Flatmate Rent"}, "rule": "must_be_absent"},
    {"trap_type": "superseded", "match": {"payee": "Test Water Co"}, "rule": "field_must_equal",
     "field": "due", "value": "2027-04-20"},
]
PAY_CASE = {"schema": PAY_SCHEMA, "gold": PAY_GOLD, "key_fields": ["payee"], "traps": PAY_TRAPS,
            "field_types": {"payee": "text", "amount": "amount", "currency": "code", "due": "date"}}


def test_payments_clean_and_amount_cents():
    ans = [dict(PAY_GOLD[0], amount=1250.4), PAY_GOLD[1]]  # 1250.4 == 1250.40
    r = score_structured(json.dumps(ans), PAY_CASE)
    assert r["field_accuracy"] == 1.0 and r["trap_failures"] == 0 and len(r["traps"]) == 3


def test_rounded_amount_counted_wrong():
    ans = [dict(PAY_GOLD[0], amount=1250), PAY_GOLD[1]]
    r = score_structured(json.dumps(ans), PAY_CASE)
    assert r["schema_valid"] and r["field_accuracy"] == 7 / 8


def test_owner_and_status_traps_fail():
    ans = PAY_GOLD + [{"payee": "Paid Gym Ltd", "amount": 40, "currency": "EUR", "due": "2027-04-01"},
                      {"payee": "Flatmate Rent", "amount": 600, "currency": "EUR", "due": "2027-04-02"}]
    r = score_structured(json.dumps(ans), PAY_CASE)
    assert r["trap_failures"] == 2 and r["spurious_items"] == 2
    types = {t["trap_type"] for t in r["traps"] if not t["passed"]}
    assert types == {"status", "owner"}
    assert r["item_precision"] == 0.5


def test_field_must_equal_wrong_and_missing():
    ans = [PAY_GOLD[0], dict(PAY_GOLD[1], due="2027-04-10")]
    r = score_structured(json.dumps(ans), PAY_CASE)
    assert r["trap_failures"] == 1 and r["traps"][2]["reason"] == "wrong_value"
    r2 = score_structured(json.dumps([PAY_GOLD[0]]), PAY_CASE)
    assert r2["traps"][2]["reason"] == "item_missing"


def test_amount_string_is_type_error_unless_lenient():
    ans = [dict(PAY_GOLD[0], amount="1250.40"), PAY_GOLD[1]]
    r = score_structured(json.dumps(ans), PAY_CASE)
    assert r["type_errors"] == 1 and not r["schema_valid"] and r["field_accuracy"] == 7 / 8
    r2 = score_structured(json.dumps(ans), dict(PAY_CASE, lenient_types=True))
    assert r2["type_errors"] == 1 and r2["field_accuracy"] == 1.0


def test_bool_not_a_number_amount():
    ans = [dict(PAY_GOLD[0], amount=True), PAY_GOLD[1]]
    r = score_structured(json.dumps(ans), PAY_CASE)
    assert r["type_errors"] == 1 and r["field_accuracy"] == 7 / 8


def test_date_normalized_to_iso_value():
    ans = [dict(PAY_GOLD[0], due="15 April 2027"), PAY_GOLD[1]]
    r = score_structured(json.dumps(ans), PAY_CASE)
    assert r["field_accuracy"] == 1.0  # value right
    assert any(e["kind"] == "pattern" or e["kind"] == "format" for e in r["schema_errors"])


# ---- fences, prose, empty ------------------------------------------------------
def test_extract_plain_and_fences():
    assert extract_json('{"a": 1}')["parse_ok"]
    e = extract_json('```json\n{"a": 1}\n```')
    assert e["parse_ok"] and e["fenced"] and e["value"] == {"a": 1} and not e["prose_wrapper"]
    e = extract_json('```\n[1,2]\n```\n')
    assert e["parse_ok"] and e["fenced"]


def test_prose_wrapper_fails_parse_but_recovers():
    e = extract_json('Sure! Here you go:\n{"a": 1}\nHope that helps.')
    assert not e["parse_ok"] and e["prose_wrapper"] and e["recovered_value"] == {"a": 1} and e["value"] is None
    e = extract_json('Here:\n```json\n{"a": 1}\n```')
    assert not e["parse_ok"] and e["prose_wrapper"] and e["fenced"] and e["recovered_value"] == {"a": 1}
    e = extract_json('{"a": 1} trailing')
    assert not e["parse_ok"] and e["prose_wrapper"]


def test_no_json_at_all_is_not_a_wrapper():
    e = extract_json("I cannot help with that.")
    assert not e["parse_ok"] and not e["prose_wrapper"] and e["recovered_value"] is None


def test_prose_wrapped_scoring():
    r = score_structured("Here is the list:\n" + json.dumps(EV_GOLD), EV_CASE)
    assert not r["json_parse_ok"] and r["prose_wrapper"] and not r["schema_valid"]
    assert r["field_accuracy"] == 1.0  # recovered for field scoring only


def test_fenced_answer_scores_fully():
    r = score_structured("```json\n" + json.dumps(EV_GOLD) + "\n```", EV_CASE)
    assert r["json_parse_ok"] and r["fenced"] and r["schema_valid"] and r["field_accuracy"] == 1.0


def test_empty_answer():
    for a in ("", "   \n", None):
        r = score_structured(a, PAY_CASE)
        assert not r["json_parse_ok"] and not r["schema_valid"] and not r["prose_wrapper"]
        assert r["field_accuracy"] == 0.0 and r["item_recall"] == 0.0
        assert r["trap_failures"] == 3  # no value: traps are not passed vacuously


def test_truncated_json():
    r = score_structured('[{"payee": "Test Water Co", "amount": 3', PAY_CASE)
    assert not r["json_parse_ok"] and r["field_accuracy"] == 0.0


def test_nan_rejected():
    assert not extract_json('{"a": NaN}')["parse_ok"]


def test_empty_gold_list():
    case = {"schema": {"type": "array"}, "gold": [], "key_fields": [], "field_types": {}}
    assert score_structured("[]", case)["field_accuracy"] == 1.0
    assert score_structured('[{"a": 1}]', case)["field_accuracy"] == 0.0


def test_single_object_gold_and_optional_required():
    case = {"schema": {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}, "b": {}}},
            "gold": {"a": "x", "b": "y"}, "key_fields": [], "field_types": {}}
    r = score_structured('{"a": "X"}', case)
    assert r["field_accuracy"] == 1.0  # b is optional, not required


# ---- aggregate -----------------------------------------------------------------
def test_aggregate():
    rs = [
        score_structured(json.dumps(EV_GOLD), EV_CASE),
        score_structured("```json\n" + json.dumps(EV_GOLD) + "\n```", EV_CASE),
        score_structured("Here:\n" + json.dumps(EV_GOLD), EV_CASE),
        score_structured(ev_answer(notes="x") if False else json.dumps([dict(EV_GOLD[0], notes="n"), EV_GOLD[1]]), EV_CASE),
    ]
    a = aggregate_structured(rs)
    assert a["n"] == 4
    assert a["json_parse_rate"] == 0.75 and a["schema_valid_rate"] == 0.5
    assert a["fence_rate"] == 0.25 and a["prose_wrapper_rate"] == 0.25
    assert a["extra_key_rate"] == 0.25 and a["type_error_rate"] == 0.0
    assert a["field_accuracy"] == 1.0
    assert aggregate_structured([])["json_parse_rate"] == 0.0
