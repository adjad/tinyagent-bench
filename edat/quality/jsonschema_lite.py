"""Stdlib-only validator for the JSON Schema (draft 2020-12) subset EDAT cases use.

``validate(instance, schema)`` returns a list of ``Error``. ``BACKEND`` records
whether the full ``jsonschema`` package or this lite validator did the work.
The lite validator is always used for ``validate``; ``validate_full`` is used
only when ``jsonschema`` is importable and ``BACKEND == "jsonschema"``.
Error kinds are derived by the lite validator so results are comparable.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

try:  # pragma: no cover - environment dependent
    import jsonschema as _jsonschema  # type: ignore
    BACKEND = "jsonschema"
except Exception:  # pragma: no cover
    _jsonschema = None
    BACKEND = "lite"

KINDS = ("type", "missing", "extra_key", "enum", "format", "range", "length", "pattern", "count", "other")


@dataclass(frozen=True)
class Error:
    path: str      # JSON pointer, "" for the root
    keyword: str
    message: str
    kind: str = "other"


def _ptr(parts) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _is_type(v, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    if t == "integer":
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer())
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return False


def type_name(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _eq(a, b) -> bool:
    """JSON equality: bool is distinct from numbers; 1 == 1.0."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")
_EMAIL_RX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+$")
_DT_RX = re.compile(r"^(\d{4}-\d{2}-\d{2})[Tt ]((?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?)(Z|z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?$")


def _real_date(s: str) -> bool:
    if not _DATE_RX.match(s):
        return False
    try:
        _dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def check_format(fmt: str, s: str) -> bool | None:
    """True/False, or None when the format is not one we check."""
    if fmt == "date":
        return _real_date(s)
    if fmt == "time":
        return bool(_TIME_RX.match(s))
    if fmt == "date-time":
        m = _DT_RX.match(s)
        return bool(m) and _real_date(m.group(1))
    if fmt == "email":
        return bool(_EMAIL_RX.match(s))
    return None


class _V:
    def __init__(self, root):
        self.root = root

    def resolve(self, ref: str):
        if not ref.startswith("#"):
            raise ValueError(f"only local $ref supported: {ref}")
        node = self.root
        frag = ref[1:]
        if frag in ("", "/"):
            return node
        for part in frag.lstrip("/").split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list):
                node = node[int(part)]
            else:
                node = node[part]
        return node

    def run(self, inst, sch, path: tuple) -> list[Error]:
        if sch is True or sch is None:
            return []
        if sch is False:
            return [Error(_ptr(path), "false", "no value is allowed here", "other")]
        errs: list[Error] = []
        p = _ptr(path)

        def add(kw, msg, kind):
            errs.append(Error(p, kw, msg, kind))

        if "$ref" in sch:
            try:
                errs += self.run(inst, self.resolve(sch["$ref"]), path)
            except (KeyError, IndexError, ValueError) as e:
                add("$ref", f"unresolvable $ref {sch['$ref']}: {e}", "other")

        if "type" in sch:
            ts = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
            if not any(_is_type(inst, t) for t in ts):
                add("type", f"expected {'|'.join(ts)}, got {type_name(inst)}", "type")
                # remaining keywords are type-specific; they skip on a wrong type naturally
        if "enum" in sch and not any(_eq(inst, e) for e in sch["enum"]):
            add("enum", f"{inst!r} is not one of {sch['enum']!r}", "enum")
        if "const" in sch and not _eq(inst, sch["const"]):
            add("const", f"expected constant {sch['const']!r}", "enum")

        num = isinstance(inst, (int, float)) and not isinstance(inst, bool)
        if num:
            if "minimum" in sch and inst < sch["minimum"]:
                add("minimum", f"{inst} < minimum {sch['minimum']}", "range")
            if "maximum" in sch and inst > sch["maximum"]:
                add("maximum", f"{inst} > maximum {sch['maximum']}", "range")
            if "exclusiveMinimum" in sch and inst <= sch["exclusiveMinimum"]:
                add("exclusiveMinimum", f"{inst} <= exclusiveMinimum {sch['exclusiveMinimum']}", "range")
            if "exclusiveMaximum" in sch and inst >= sch["exclusiveMaximum"]:
                add("exclusiveMaximum", f"{inst} >= exclusiveMaximum {sch['exclusiveMaximum']}", "range")
            if "multipleOf" in sch and sch["multipleOf"]:
                q = inst / sch["multipleOf"]
                if abs(q - round(q)) > 1e-9:
                    add("multipleOf", f"{inst} is not a multiple of {sch['multipleOf']}", "range")

        if isinstance(inst, str):
            if "minLength" in sch and len(inst) < sch["minLength"]:
                add("minLength", f"length {len(inst)} < {sch['minLength']}", "length")
            if "maxLength" in sch and len(inst) > sch["maxLength"]:
                add("maxLength", f"length {len(inst)} > {sch['maxLength']}", "length")
            if "pattern" in sch and not re.search(sch["pattern"], inst):
                add("pattern", f"{inst!r} does not match {sch['pattern']!r}", "pattern")
            if "format" in sch and check_format(sch["format"], inst) is False:
                add("format", f"{inst!r} is not a valid {sch['format']}", "format")

        if isinstance(inst, list):
            n = len(inst)
            if "minItems" in sch and n < sch["minItems"]:
                add("minItems", f"{n} items < minItems {sch['minItems']}", "count")
            if "maxItems" in sch and n > sch["maxItems"]:
                add("maxItems", f"{n} items > maxItems {sch['maxItems']}", "count")
            if sch.get("uniqueItems"):
                seen: list = []
                for x in inst:
                    if any(_eq(x, y) for y in seen):
                        add("uniqueItems", "items are not unique", "count")
                        break
                    seen.append(x)
            pre = sch.get("prefixItems") or []
            for i, sub in enumerate(pre):
                if i < n:
                    errs += self.run(inst[i], sub, path + (i,))
            if "items" in sch:
                for i in range(len(pre), n):
                    errs += self.run(inst[i], sch["items"], path + (i,))

        if isinstance(inst, dict):
            props = sch.get("properties", {})
            for k in sch.get("required", []):
                if k not in inst:
                    errs.append(Error(_ptr(path + (k,)), "required", f"missing required property {k!r}", "missing"))
            for k, sub in props.items():
                if k in inst:
                    errs += self.run(inst[k], sub, path + (k,))
            if "additionalProperties" in sch:
                ap = sch["additionalProperties"]
                for k in inst:
                    if k in props:
                        continue
                    if ap is False:
                        errs.append(Error(_ptr(path + (k,)), "additionalProperties", f"unexpected property {k!r}", "extra_key"))
                    elif isinstance(ap, dict):
                        errs += self.run(inst[k], ap, path + (k,))
            if "minProperties" in sch and len(inst) < sch["minProperties"]:
                add("minProperties", "too few properties", "count")
            if "maxProperties" in sch and len(inst) > sch["maxProperties"]:
                add("maxProperties", "too many properties", "count")

        if "allOf" in sch:
            for sub in sch["allOf"]:
                errs += self.run(inst, sub, path)
        if "anyOf" in sch:
            branches = [self.run(inst, sub, path) for sub in sch["anyOf"]]
            if not any(not b for b in branches):
                add("anyOf", "matches none of the anyOf branches", "other")
        if "oneOf" in sch:
            ok = sum(1 for sub in sch["oneOf"] if not self.run(inst, sub, path))
            if ok != 1:
                add("oneOf", f"matches {ok} oneOf branches, expected exactly 1", "other")
        if "not" in sch and not self.run(inst, sch["not"], path):
            add("not", "matches a schema it must not match", "other")
        return errs


def validate(instance, schema) -> list[Error]:
    """Validate with the lite validator. Returns [] when valid."""
    return _V(schema).run(instance, schema, ())


def is_valid(instance, schema) -> bool:
    return not validate(instance, schema)


def backend() -> str:
    """Which implementation is available: "jsonschema" or "lite" (results are always lite-derived)."""
    return BACKEND
