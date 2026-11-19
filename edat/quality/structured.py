"""Structured-output scoring (spec 03 section 8; extraction cases section 7.5-7.6).

``extract_json`` decides whether an answer is exactly one JSON value.
``score_structured`` scores one answer against a case; ``aggregate_structured``
turns a list of per-answer results into the section 8 rates.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from . import jsonschema_lite as jsl
from .normalize import find_dates, find_times, norm_code, parse_decimal
from .textutil import fold

_FENCE_WHOLE = re.compile(r"\A```[ \t]*(?:json|JSON)?[ \t]*\r?\n?(.*?)\r?\n?```\Z", re.S)
_FENCE_ANY = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n?(.*?)\r?\n?```", re.S)


def _no_const(name):
    raise ValueError(f"non-JSON constant {name}")


def _loads(s: str):
    return json.loads(s, parse_constant=_no_const)


def _recover(text: str):
    """Find a JSON object/array inside prose. Returns (found, value, fenced)."""
    for m in _FENCE_ANY.finditer(text):
        try:
            return True, _loads(m.group(1).strip()), True
        except ValueError:
            continue
    dec = json.JSONDecoder(parse_constant=_no_const)
    for m in re.finditer(r"[\[{]", text):
        try:
            v, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        return True, v, False
    return False, None, False


def extract_json(answer_text) -> dict:
    """The answer must be exactly one JSON value (one surrounding fence allowed)."""
    out = {"value": None, "parse_ok": False, "fenced": False, "prose_wrapper": False,
           "error": None, "recovered_value": None}
    text = (answer_text or "").strip()
    if not text:
        out["error"] = "empty answer"
        return out
    body = text
    fenced = False
    m = _FENCE_WHOLE.match(text)
    if m:
        body, fenced = m.group(1).strip(), True
    try:
        out["value"] = _loads(body)
        out["parse_ok"] = True
        out["fenced"] = fenced
        return out
    except ValueError as e:
        out["error"] = str(e)
    found, val, rfenced = _recover(text)
    if found:
        out["prose_wrapper"] = True
        out["recovered_value"] = val
        out["fenced"] = fenced or rfenced
    return out


# --------------------------------------------------------------------------
# value normalization
# --------------------------------------------------------------------------

_TIME_FALLBACK = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\s*$")


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _dec(v, lenient: bool):
    if _is_num(v):
        try:
            return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
        except InvalidOperation:
            return None
    if lenient and isinstance(v, str):
        s = re.sub(r"[^\d.,\-−\s  ]", "", v)
        return parse_decimal(s) if s.strip() else None
    return None


def _cent(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"))


def _norm_date(v):
    if not isinstance(v, str):
        return None
    a = [x for x in find_dates(v) if x.kind == "date"]
    return a[0].value if a else None


def _norm_time(v):
    if not isinstance(v, str):
        return None
    m = _TIME_FALLBACK.match(v)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    a = find_times(v)
    return a[0].value if a else None


def _generic(v):
    if isinstance(v, bool) or v is None:
        return v
    if _is_num(v):
        return ("n", Decimal(repr(v)) if isinstance(v, float) else Decimal(v))
    if isinstance(v, str):
        return fold(v)
    if isinstance(v, list):
        return [_generic(x) for x in v]
    if isinstance(v, dict):
        return {k: _generic(x) for k, x in v.items()}
    return v


def _norm(v, ftype: str, lenient: bool):
    """Normalized comparable form, or a sentinel None-tuple when it cannot be normalized."""
    if ftype == "date":
        return _norm_date(v)
    if ftype == "time":
        return _norm_time(v)
    if ftype == "amount":
        d = _dec(v, lenient)
        return None if d is None else _cent(d)
    if ftype == "number":
        d = _dec(v, lenient)
        return d
    if ftype == "code":
        return norm_code(v).strip() if isinstance(v, str) else None
    if ftype in ("text", "enum"):
        return fold(v) if isinstance(v, str) else (None if v is not None else None)
    if ftype == "bool":
        return v if isinstance(v, bool) else None
    return _generic(v)


def _same(pred, gold, ftype: str, lenient: bool) -> bool:
    if ftype == "text_loose":
        # free-text labels ("what", "description"): every content word of the gold appears in the answer
        if not isinstance(gold, str) or not isinstance(pred, str):
            return pred == gold
        gw = [x for x in re.findall(r"[a-z0-9]+", fold(gold)) if len(x) >= 3]
        pw = set(re.findall(r"[a-z0-9]+", fold(pred)))
        return bool(gw) and all(x in pw for x in gw)
    g = _norm(gold, ftype, True)
    if g is None:
        # gold itself is null / unnormalizable: exact JSON equality
        return pred == gold and type(pred) is type(gold)
    p = _norm(pred, ftype, lenient)
    return p is not None and p == g


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def _item_schema(schema):
    if not isinstance(schema, dict):
        return {}
    s = schema
    root = schema

    def deref(x):
        seen = 0
        while isinstance(x, dict) and "$ref" in x and seen < 8:
            try:
                x = jsl._V(root).resolve(x["$ref"])
            except Exception:
                return {}
            seen += 1
        return x

    s = deref(s)
    if isinstance(s, dict) and s.get("type") == "array" or (isinstance(s, dict) and "items" in s):
        s = deref(s.get("items", {}))
    return s if isinstance(s, dict) else {}


def _key_of(item, key_fields, ftypes, lenient):
    if not isinstance(item, dict):
        return None
    parts = []
    for f in key_fields:
        if f not in item:
            return None
        n = _norm(item[f], ftypes.get(f, "text"), lenient)
        if n is None:
            return None
        parts.append(n)
    return tuple(parts)


def _matches(item, match: dict, ftypes, lenient=True) -> bool:
    if not isinstance(item, dict):
        return False
    for f, want in match.items():
        if f not in item or not _same(item[f], want, ftypes.get(f, "text"), lenient):
            return False
    return True


def score_structured(answer_text, case: dict) -> dict:
    """Score one answer. Reads case keys: schema, gold, key_fields, field_types,
    traps (optional), lenient_types (optional)."""
    schema = case.get("schema")
    gold = case.get("gold")
    key_fields = list(case.get("key_fields") or [])
    ftypes = dict(case.get("field_types") or {})
    lenient = bool(case.get("lenient_types"))
    traps = list(case.get("traps") or [])

    ex = extract_json(answer_text)
    parse_ok = ex["parse_ok"]
    value = ex["value"] if parse_ok else ex["recovered_value"]
    have_value = parse_ok or ex["prose_wrapper"]

    errors = jsl.validate(ex["value"], schema) if (parse_ok and schema is not None) else []
    schema_valid = parse_ok and not errors
    extra_keys = sum(1 for e in errors if e.kind == "extra_key")
    type_paths = {e.path for e in errors if e.kind == "type"}

    gold_items = gold if isinstance(gold, list) else [gold]
    pred_items = value if isinstance(value, list) else ([] if isinstance(gold, list) else [value])
    if not have_value:
        pred_items = []
    gold_is_obj = [isinstance(g, dict) for g in gold_items]

    # alignment
    pairs: dict[int, int] = {}
    if all(gold_is_obj) and key_fields:
        used: set[int] = set()
        pkeys = [_key_of(p, key_fields, ftypes, lenient) for p in pred_items]
        for gi, g in enumerate(gold_items):
            gk = _key_of(g, key_fields, ftypes, True)
            if gk is None:
                continue
            for pi, pk in enumerate(pkeys):
                if pi not in used and pk == gk:
                    pairs[gi] = pi
                    used.add(pi)
                    break
    else:
        for gi in range(min(len(gold_items), len(pred_items))):
            pairs[gi] = gi

    req_default = _item_schema(schema).get("required")
    total = correct = 0
    for gi, g in enumerate(gold_items):
        pi = pairs.get(gi)
        pred = pred_items[pi] if pi is not None else None
        if not gold_is_obj[gi]:
            total += 1
            if pi is not None and _same(pred, g, "text" if isinstance(g, str) else ftypes.get("*", "any"), lenient):
                correct += 1
            continue
        fields = [f for f in (req_default if req_default else g.keys()) if f in g]
        for f in fields:
            total += 1
            ft = ftypes.get(f, "text" if isinstance(g[f], str) else "any")
            if isinstance(pred, dict) and f in pred:
                pv = pred[f]
                if ft in ("amount", "number") and isinstance(pv, str):
                    type_paths.add(f"/{pi}/{f}" if isinstance(value, list) else f"/{f}")
                if _same(pv, g[f], ft, lenient):
                    correct += 1

    spurious = max(0, len(pred_items) - len(pairs))
    n_gold, n_pred, n_match = len(gold_items), len(pred_items), len(pairs)
    if isinstance(gold, list) and not gold:
        field_acc = 1.0 if not pred_items else 0.0
        recall, precision = 1.0, (1.0 if not pred_items else 0.0)
    else:
        field_acc = correct / total if total else 0.0
        recall = n_match / n_gold if n_gold else 1.0
        precision = n_match / n_pred if n_pred else (1.0 if not n_gold else 0.0)

    # traps
    trap_out = []
    for t in traps:
        rule, match = t.get("rule"), t.get("match") or {}
        if not have_value:
            ok, why = False, "no_value"
        elif rule == "must_be_absent":
            hit = [p for p in pred_items if _matches(p, match, ftypes)]
            ok, why = not hit, ("present" if hit else "")
        elif rule == "field_must_equal":
            hit = [p for p in pred_items if _matches(p, match, ftypes)]
            f = t.get("field")
            if not hit:
                ok, why = False, "item_missing"
            else:
                ok = any(f in p and _same(p[f], t.get("value"), ftypes.get(f, "text"), lenient) for p in hit)
                why = "" if ok else "wrong_value"
        else:
            ok, why = False, f"unknown_rule:{rule}"
        trap_out.append({"trap_type": t.get("trap_type"), "rule": rule, "passed": ok, "reason": why})

    return {
        "json_parse_ok": parse_ok,
        "fenced": ex["fenced"],
        "prose_wrapper": ex["prose_wrapper"],
        "schema_valid": schema_valid,
        "extra_keys": extra_keys,
        "type_errors": len(type_paths),
        "field_accuracy": field_acc,
        "item_recall": recall,
        "item_precision": precision,
        "spurious_items": spurious,
        "fields_total": total,
        "fields_correct": correct,
        "trap_failures": sum(1 for t in trap_out if not t["passed"]),
        "traps": trap_out,
        "schema_errors": [{"path": e.path, "keyword": e.keyword, "kind": e.kind, "message": e.message}
                          for e in errors[:10]],
        "parse_error": ex["error"],
        "validator": jsl.backend(),
    }


def aggregate_structured(results) -> dict:
    """Section 8 rates as fractions 0-1. extra_key_rate and type_error_rate are the
    share of answers with at least one extra key / type error."""
    rs = list(results)
    n = len(rs)

    def frac(pred):
        return sum(1 for r in rs if pred(r)) / n if n else 0.0

    traps = [t for r in rs for t in r.get("traps", [])]
    return {
        "n": n,
        "json_parse_rate": frac(lambda r: r["json_parse_ok"]),
        "schema_valid_rate": frac(lambda r: r["schema_valid"]),
        "field_accuracy": (sum(r["field_accuracy"] for r in rs) / n) if n else 0.0,
        "extra_key_rate": frac(lambda r: r["extra_keys"] > 0),
        "type_error_rate": frac(lambda r: r["type_errors"] > 0),
        "prose_wrapper_rate": frac(lambda r: r["prose_wrapper"]),
        "fence_rate": frac(lambda r: r["fenced"]),
        "extra_keys_total": sum(r["extra_keys"] for r in rs),
        "type_errors_total": sum(r["type_errors"] for r in rs),
        "trap_failure_rate": (sum(1 for t in traps if not t["passed"]) / len(traps)) if traps else 0.0,
    }
