"""Tests for the stdlib JSON Schema subset validator (all data invented)."""
from __future__ import annotations

import pytest

from edat.quality.jsonschema_lite import backend, validate


def kinds(inst, sch):
    return [(e.path, e.keyword, e.kind) for e in validate(inst, sch)]


def test_backend_recorded():
    assert backend() in ("lite", "jsonschema")


@pytest.mark.parametrize("t,good,bad", [
    ("string", "a", 1), ("integer", 3, 3.5), ("number", 3.5, "3.5"), ("boolean", True, 1),
    ("null", None, 0), ("array", [], {}), ("object", {}, []),
])
def test_type(t, good, bad):
    assert validate(good, {"type": t}) == []
    e = validate(bad, {"type": t})
    assert [x.kind for x in e] == ["type"] and e[0].path == "" and e[0].keyword == "type"


def test_type_list_and_bool_not_number():
    assert validate(None, {"type": ["string", "null"]}) == []
    assert validate(5, {"type": ["string", "null"]})[0].kind == "type"
    assert validate(True, {"type": "number"})[0].kind == "type"
    assert validate(True, {"type": "integer"})[0].kind == "type"
    assert validate(2.0, {"type": "integer"}) == []
    assert validate(2, {"type": "number"}) == []


def test_properties_required_paths():
    s = {"type": "object", "required": ["a", "b"], "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}
    assert validate({"a": "x", "b": 1}, s) == []
    assert kinds({"a": "x"}, s) == [("/b", "required", "missing")]
    assert kinds({"a": 1, "b": 1}, s) == [("/a", "type", "type")]


def test_additional_properties():
    s = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
    assert validate({"a": 1}, s) == []
    assert kinds({"a": 1, "notes": "x"}, s) == [("/notes", "additionalProperties", "extra_key")]
    s2 = {"properties": {"a": {}}, "additionalProperties": {"type": "integer"}}
    assert validate({"a": "x", "z": 1}, s2) == []
    assert kinds({"z": "s"}, s2) == [("/z", "type", "type")]
    assert validate({"q": 1}, {"additionalProperties": True}) == []


def test_items_and_pointer_escaping():
    s = {"type": "array", "items": {"type": "integer"}}
    assert validate([1, 2], s) == []
    assert kinds([1, "x"], s) == [("/1", "type", "type")]
    assert kinds({"a/b": "x"}, {"properties": {"a/b": {"type": "integer"}}}) == [("/a~1b", "type", "type")]


def test_prefix_items():
    s = {"prefixItems": [{"type": "string"}, {"type": "integer"}], "items": {"type": "boolean"}}
    assert validate(["a", 1, True], s) == []
    assert kinds(["a", "b", 3], s) == [("/1", "type", "type"), ("/2", "type", "type")]


def test_min_max_items_unique():
    s = {"minItems": 2, "maxItems": 3}
    assert validate([1, 2], s) == []
    assert kinds([1], s) == [("", "minItems", "count")]
    assert kinds([1, 2, 3, 4], s) == [("", "maxItems", "count")]
    assert validate([1, 2], {"uniqueItems": True}) == []
    assert kinds([1, 1], {"uniqueItems": True}) == [("", "uniqueItems", "count")]
    assert validate([1, True], {"uniqueItems": True}) == []
    assert kinds([{"a": 1}, {"a": 1}], {"uniqueItems": True})[0][2] == "count"


def test_enum_const():
    s = {"enum": ["bill", "plan", 3, None]}
    assert validate("bill", s) == [] and validate(None, s) == []
    assert kinds("spam", s) == [("", "enum", "enum")]
    assert validate(True, {"enum": [1]}) != []
    assert validate("x", {"const": "x"}) == []
    assert kinds("y", {"const": "x"}) == [("", "const", "enum")]


def test_numeric_bounds():
    assert validate(5, {"minimum": 5, "maximum": 5}) == []
    assert kinds(4, {"minimum": 5}) == [("", "minimum", "range")]
    assert kinds(6, {"maximum": 5}) == [("", "maximum", "range")]
    assert kinds(5, {"exclusiveMinimum": 5}) == [("", "exclusiveMinimum", "range")]
    assert kinds(5, {"exclusiveMaximum": 5}) == [("", "exclusiveMaximum", "range")]
    assert validate(4.99, {"exclusiveMaximum": 5}) == []
    assert validate("9", {"minimum": 10}) == []  # bounds ignore non-numbers


def test_string_length_pattern():
    s = {"minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"}
    assert validate("abc", s) == []
    assert kinds("a", s) == [("", "minLength", "length")]
    assert kinds("abcde", s) == [("", "maxLength", "length")]
    assert kinds("AB", s) == [("", "pattern", "pattern")]


@pytest.mark.parametrize("fmt,good,bad", [
    ("date", "2027-03-09", "2027-02-30"), ("date", "2027-03-09", "09/03/2027"),
    ("time", "09:30", "9am"), ("time", "23:59:59", "24:00"), ("time", "08:05", "8:5"),
    ("date-time", "2027-03-09T09:30:00Z", "2027-03-09"), ("date-time", "2027-03-09T09:30:00+01:00", "2027-13-09T09:30"),
    ("email", "test.user@mail.example", "test.user@"), ("email", "a@b.example", "no at sign"),
])
def test_formats(fmt, good, bad):
    assert validate(good, {"format": fmt}) == []
    e = validate(bad, {"format": fmt})
    assert [x.kind for x in e] == ["format"]


def test_unknown_format_ignored_and_non_string():
    assert validate("x", {"format": "uuid"}) == []
    assert validate(5, {"format": "date"}) == []


def test_any_one_all_not():
    any_s = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert validate(1, any_s) == [] and validate("a", any_s) == []
    assert kinds(1.5, any_s) == [("", "anyOf", "other")]
    one = {"oneOf": [{"type": "integer"}, {"minimum": 0}]}
    assert validate(-1, one) == []
    assert kinds(5, one) == [("", "oneOf", "other")]
    assert kinds(-1.5, one)[0][1] == "oneOf"
    all_s = {"allOf": [{"type": "integer"}, {"minimum": 3}]}
    assert validate(4, all_s) == []
    assert kinds(1, all_s) == [("", "minimum", "range")]
    assert validate("a", {"not": {"type": "integer"}}) == []
    assert kinds(1, {"not": {"type": "integer"}}) == [("", "not", "other")]


def test_defs_and_ref():
    s = {"$defs": {"ev": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
         "type": "array", "items": {"$ref": "#/$defs/ev"}}
    assert validate([{"name": "Lunch"}], s) == []
    assert kinds([{"name": 1}, {}], s) == [("/0/name", "type", "type"), ("/1/name", "required", "missing")]
    bad = validate(1, {"$ref": "#/$defs/nope"})
    assert bad and bad[0].keyword == "$ref"


def test_boolean_schemas_and_empty_schema():
    assert validate(1, {}) == []
    assert validate(1, True) == []
    assert validate(1, False)[0].kind == "other"


def test_error_kinds_in_declared_set():
    from edat.quality.jsonschema_lite import KINDS
    s = {"type": "object", "required": ["x"], "additionalProperties": False, "properties": {"y": {"type": "integer", "minimum": 3}}}
    for e in validate({"y": 1, "z": 2}, s):
        assert e.kind in KINDS
