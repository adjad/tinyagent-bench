"""Validation of tinyagent-bench result records, run manifests and export files.

The JSON Schema files in `schema/` are the contract. This module checks them
with a small built-in validator for exactly the keyword subset those files use
(type, enum, const, required, properties, additionalProperties,
propertyNames, maxProperties, items, minItems, maxItems, minimum, maximum,
minLength, maxLength, pattern, anyOf), so no extra dependency is needed. It
then applies the cross-field rules a schema cannot say (record_rules).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).with_name("schema")
RECORD_SCHEMA_ID = "edat.result/1.0"
MANIFEST_SCHEMA_ID = "edat.run-manifest/1.0"
SNAPSHOT_SCHEMA_ID = "edat.snapshot/1.0"
SITE_BOARD_SCHEMA_ID = "edat.site-board/1.0"
SITE_INDEX_SCHEMA_ID = "edat.site-index/1.0"

_FILES = {
    "record": "result-record.schema.json",
    "manifest": "run-manifest.schema.json",
    "snapshot": "snapshot.schema.json",
    "site_board": "site-board.schema.json",
}


class ValidationError(ValueError):
    """A record or file does not match its schema. `errors` lists every problem."""

    def __init__(self, errors: list[str], what: str = "record"):
        self.errors = list(errors)
        super().__init__(f"invalid {what}: " + "; ".join(self.errors[:8])
                         + (f" (+{len(self.errors) - 8} more)" if len(self.errors) > 8 else ""))


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / _FILES[name]).read_text())


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _check(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    if "anyOf" in schema:
        if not any(not _errors_of(value, sub, path) for sub in schema["anyOf"]):
            errors.append(f"{path}: {value!r} matches none of the allowed forms")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}, got {value!r}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
        return
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_TYPES[t](value) for t in types):
            errors.append(f"{path}: expected {'/'.join(types)}, got {type(value).__name__}")
            return
    if value is None:
        return
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value[:80]!r} does not match {schema['pattern']}")
    if _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{i}]", errors)
    if isinstance(value, dict):
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: more than {schema['maxProperties']} fields")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        names = schema.get("propertyNames")
        for key, sub in value.items():
            if names is not None:
                _check(key, {"type": "string", **names}, f"{path} key", errors)
            if key in props:
                _check(sub, props[key], f"{path}.{key}", errors)
            elif extra is False:
                errors.append(f"{path}.{key}: unknown field")
            elif isinstance(extra, dict):
                _check(sub, extra, f"{path}.{key}", errors)


def _errors_of(value: Any, schema: dict, path: str) -> list[str]:
    errors: list[str] = []
    _check(value, schema, path, errors)
    return errors


def schema_errors(value: Any, name: str) -> list[str]:
    """Problems with `value` against the named schema file ("record", "manifest", ...)."""
    return _errors_of(value, load_schema(name), "$")


# --- cross-field rules -------------------------------------------------------

_RANKED_ROLES = {"baseline", "reference", "candidate", "variant"}


def record_rules(rec: dict) -> list[str]:
    """The rules of docs/benchmark/results-record.md that a schema cannot express."""
    errors: list[str] = []
    model = rec.get("model") or {}
    settings = rec.get("settings") or {}
    kind = rec.get("kind")
    if kind == "cloud_reference":
        if rec.get("device") is not None:
            errors.append("$.device: must be null for a cloud_reference (with device_reason: cloud)")
        if rec.get("device_reason") != "cloud":
            errors.append("$.device_reason: must be 'cloud' for a cloud_reference")
        if model.get("role") != "cloud_reference":
            errors.append("$.model.role: must be 'cloud_reference' for a cloud_reference")
        for key in ("requested_model", "resolved_model", "upstream_provider", "run_date"):
            if not model.get(key):
                errors.append(f"$.model.{key}: required for a cloud_reference")
        if rec.get("split") == "heldout":
            errors.append("$.split: held-out cases are never sent to a hosted provider")
        if settings.get("sampling_source") == "engine_default":
            errors.append("$.settings.sampling_source: a hosted model uses provider_default, not engine_default")
    elif kind == "local":
        if rec.get("device") is None:
            errors.append("$.device: required for a local record")
        if model.get("role") not in _RANKED_ROLES:
            errors.append(f"$.model.role: {model.get('role')!r} is not a local role")
        if not (model.get("weights_sha256") or model.get("weights_fingerprint")):
            errors.append("$.model: a local record needs weights_sha256 or weights_fingerprint")
        for key in ("requested_model", "resolved_model", "upstream_provider", "run_date"):
            if model.get(key) is not None:
                errors.append(f"$.model.{key}: only for cloud_reference records")
        if settings.get("sampling_source") == "provider_default":
            errors.append("$.settings.sampling_source: provider_default is for hosted models only")
    if rec.get("raw") is None and not rec.get("raw_reason"):
        errors.append("$.raw: null needs a raw_reason")
    if rec.get("status") == "ok" and rec.get("tier") != "perf":
        metrics = rec.get("metrics") or {}
        if not isinstance(metrics.get("pass"), bool) and not _is_unit(metrics.get("score")):
            errors.append("$.metrics: a graded (status ok) record needs pass (bool) or score (0-1)")
    score = (rec.get("metrics") or {}).get("score")
    if score is not None and not _is_unit(score):
        errors.append(f"$.metrics.score: {score!r} is not a number from 0 to 1")
    run_date = model.get("run_date") or {}
    if run_date and run_date.get("first", "") > run_date.get("last", ""):
        errors.append("$.model.run_date: first is after last")
    return errors


def _is_unit(v: Any) -> bool:
    return _TYPES["number"](v) and 0.0 <= v <= 1.0


def record_errors(rec: Any) -> list[str]:
    if not isinstance(rec, dict):
        return ["$: a record is a JSON object"]
    errors = schema_errors(rec, "record")
    if not errors:
        errors = record_rules(rec)
    return errors


def validate_record(rec: Any) -> dict:
    """Return `rec` unchanged, or raise ValidationError listing every problem."""
    errors = record_errors(rec)
    if errors:
        raise ValidationError(errors)
    return rec


def validate(value: Any, name: str) -> Any:
    errors = schema_errors(value, name)
    if errors:
        raise ValidationError(errors, what=name.replace("_", " "))
    return value
