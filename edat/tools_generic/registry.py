"""Tool specs, the registry and the helpers every edat-generic-v1 tool shares.

A tool is a `ToolSpec`: a name, a domain, a description, an OpenAI-style JSON
schema for its parameters, two flags the grader reads (`effect`, `egress`) and
a handler `handler(store, args) -> dict | ToolResult`. A handler signals a
failure by raising `ToolError`; the environment turns it into `{"error": ...}`.

Tools register themselves with the `@tool(...)` decorator when their module is
imported (edat/tools_generic/__init__.py imports them in menu order).

Shared conventions:
* Lists and searches take `offset` and `limit` and return
  `{"items": [...], "total": n, "next_offset": k | null}`.
* Search is case-insensitive and every query word must appear in the searched
  fields (AND). An empty query matches everything.
* Times are naive local ISO strings ("YYYY-MM-DDTHH:MM"); a value with a zone
  offset is converted to the store's zone.
* Long text reads (`files_read`, `email_read`, `notes_read`, `web_fetch`) take
  `offset` and `limit` in characters and return `total_chars` and `next_offset`.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Union

from edat.state.clock import parse_local
from edat.state.env import ToolResult
from edat.state.store import StateStore

TIME_FMT = "%Y-%m-%dT%H:%M"
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class ToolError(Exception):
    """A failure the model should see as {"error": message}."""

    def __init__(self, message: str, outcome: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.outcome = outcome


Handler = Callable[[StateStore, dict], Union[dict, ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    domain: str
    description: str
    parameters: dict          # JSON schema, type object
    effect: bool              # can change the store
    egress: bool              # sends content out of the user's own stores
    handler: Handler

    @property
    def required(self) -> list[str]:
        return list(self.parameters.get("required", []))


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, domain: str, description: str, properties: dict[str, dict],
         required: tuple[str, ...] | list[str] = (), *, effect: bool = False,
         egress: bool = False) -> Callable[[Handler], Handler]:
    """Register a handler as a tool."""

    def deco(fn: Handler) -> Handler:
        if name in REGISTRY:
            raise ValueError(f"duplicate tool name {name!r}")
        missing = [r for r in required if r not in properties]
        if missing:
            raise ValueError(f"{name}: required {missing} not in properties")
        params = {"type": "object", "properties": copy.deepcopy(properties),
                  "required": list(required), "additionalProperties": False}
        REGISTRY[name] = ToolSpec(name=name, domain=domain, description=description,
                                  parameters=params, effect=effect, egress=egress, handler=fn)
        return fn

    return deco


def schema(spec: ToolSpec) -> dict:
    """The OpenAI function-tool schema for one spec."""
    return {"type": "function", "function": {"name": spec.name, "description": spec.description,
                                             "parameters": copy.deepcopy(spec.parameters)}}


def specs() -> list[ToolSpec]:
    return list(REGISTRY.values())


# ------------------------------------------------------------------ schema builders
def S(desc: str, enum: list[str] | tuple[str, ...] | None = None) -> dict:
    d: dict[str, Any] = {"type": "string", "description": desc}
    if enum:
        d["enum"] = list(enum)
    return d


def I(desc: str, minimum: int | None = None, maximum: int | None = None) -> dict:
    d: dict[str, Any] = {"type": "integer", "description": desc}
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


def N(desc: str, minimum: float | None = None, maximum: float | None = None) -> dict:
    d: dict[str, Any] = {"type": "number", "description": desc}
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


def B(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


def A(desc: str, items: dict | None = None) -> dict:
    return {"type": "array", "items": items or {"type": "string"}, "description": desc}


PAGING = {
    "offset": I("Index of the first item to return (default 0).", 0),
    "limit": I(f"Maximum number of items to return (default {DEFAULT_LIMIT}, at most {MAX_LIMIT}).",
               1, MAX_LIMIT),
}

TEXT_WINDOW = {
    "offset": I("Character offset to start reading from (default 0).", 0),
    "limit": I("Maximum number of characters to return (default: the rest of the text).", 1),
}


# ------------------------------------------------------------------ argument access
def arg_str(args: dict, key: str, default: str | None = None) -> str | None:
    v = args.get(key)
    if v is None:
        return default
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if not isinstance(v, str):
        raise ToolError(f"invalid {key}: expected a string")
    return v


def need_str(args: dict, key: str) -> str:
    v = arg_str(args, key)
    if v is None or not v.strip():
        raise ToolError(f"invalid {key}: must not be empty")
    return v


def arg_num(args: dict, key: str, default: float | None = None, *, lo: float | None = None,
            hi: float | None = None) -> float | None:
    v = args.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        raise ToolError(f"invalid {key}: expected a number")
    if isinstance(v, str):
        try:
            v = float(v.strip())
        except ValueError:
            raise ToolError(f"invalid {key}: expected a number") from None
    if not isinstance(v, (int, float)):
        raise ToolError(f"invalid {key}: expected a number")
    if lo is not None and v < lo:
        raise ToolError(f"invalid {key}: must be at least {lo:g}")
    if hi is not None and v > hi:
        raise ToolError(f"invalid {key}: must be at most {hi:g}")
    return int(v) if float(v).is_integer() else float(v)


def arg_int(args: dict, key: str, default: int | None = None, *, lo: int | None = None,
            hi: int | None = None) -> int | None:
    v = arg_num(args, key, None, lo=lo, hi=hi)
    if v is None:
        return default
    if not float(v).is_integer():
        raise ToolError(f"invalid {key}: expected a whole number")
    return int(v)


def arg_bool(args: dict, key: str, default: bool | None = None) -> bool | None:
    v = args.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "yes", "on", "1"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("false", "no", "off", "0"):
        return False
    if isinstance(v, int):
        return bool(v)
    raise ToolError(f"invalid {key}: expected true or false")


def arg_list(args: dict, key: str) -> list[str] | None:
    """A list of strings; a single string is taken as a one-item list."""
    v = args.get(key)
    if v is None:
        return None
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ToolError(f"invalid {key}: expected a list of strings")
    return list(v)


# ------------------------------------------------------------------ results
def page(items: list, args: dict) -> dict:
    offset = arg_int(args, "offset", 0, lo=0)
    limit = arg_int(args, "limit", DEFAULT_LIMIT, lo=1)
    limit = min(limit, MAX_LIMIT)
    total = len(items)
    chunk = items[offset:offset + limit]
    nxt = offset + limit if offset + limit < total else None
    return {"items": chunk, "total": total, "next_offset": nxt}


def window(text: str, args: dict) -> dict:
    text = text or ""
    offset = arg_int(args, "offset", 0, lo=0)
    limit = arg_int(args, "limit", None, lo=1)
    part = text[offset:] if limit is None else text[offset:offset + limit]
    end = offset + len(part)
    return {"text": part, "total_chars": len(text), "next_offset": end if end < len(text) else None}


# Grading-only fields seeded on records; a tool never shows them to the model.
HIDDEN_FIELDS = ("private_canaries", "canary_owners")


def clean(record: dict, *drop: str) -> dict:
    out = copy.deepcopy(record)
    for k in (*HIDDEN_FIELDS, *drop):
        out.pop(k, None)
    return out


def not_found(kind: str, ident: Any) -> ToolError:
    return ToolError(f"no {kind} with id {ident!r}", outcome="no_match")


# ------------------------------------------------------------------ paths
def norm_path(path: str) -> str:
    """A file path in store form: "Downloads/x" and "~/Downloads/x/" are "~/Downloads/x"."""
    p = path.strip() if isinstance(path, str) else ""
    if not p:
        raise ToolError("invalid path: must not be empty")
    if not (p.startswith("~") or p.startswith("/")):
        p = "~/" + p
    return p.rstrip("/") if len(p) > 2 else p


# ------------------------------------------------------------------ search
def words(query: str | None) -> list[str]:
    return [w for w in re.split(r"\s+", (query or "").lower().strip()) if w]


def _flat(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(_flat(x) for x in v)
    if isinstance(v, dict):
        return " ".join(_flat(x) for x in v.values())
    return str(v)


def matches(query: str | None, record: dict, fields: tuple[str, ...] | list[str]) -> bool:
    """Every query word appears (case-insensitively) somewhere in the fields."""
    hay = " ".join(_flat(record.get(f)) for f in fields).lower()
    return all(w in hay for w in words(query))


# ------------------------------------------------------------------ time
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def fmt(dt: datetime) -> str:
    return dt.strftime(TIME_FMT)


def parse_when(store: StateStore, value: Any, key: str) -> tuple[datetime, bool]:
    """Parse an ISO local date or datetime. Returns (naive local datetime, date_only)."""
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"invalid {key}: expected an ISO date or time such as 2026-11-19T15:00")
    v = value.strip()
    if DATE_RE.match(v):
        try:
            return datetime.fromisoformat(v), True
        except ValueError:
            raise ToolError(f"invalid {key}: {v!r} is not a real date") from None
    dt = parse_local(v)
    if dt is None:
        raise ToolError(f"invalid {key}: expected an ISO date or time such as 2026-11-19T15:00")
    if dt.tzinfo is not None:
        dt = dt.astimezone(store.clock.zone).replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0), False


def parse_dt(store: StateStore, value: Any, key: str) -> datetime:
    return parse_when(store, value, key)[0]


def parse_hhmm(value: Any, key: str) -> tuple[int, int]:
    m = HHMM_RE.match(value.strip()) if isinstance(value, str) else None
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ToolError(f"invalid {key}: expected a time such as 09:30")
    return int(m.group(1)), int(m.group(2))


def record_dt(value: Any) -> datetime | None:
    """A stored time value as a naive datetime (None when missing or unparsable)."""
    dt = parse_local(value) if isinstance(value, str) else None
    if dt is not None and dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt
