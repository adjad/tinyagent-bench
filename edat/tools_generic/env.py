"""`GenericEnvironment`: the neutral lane (edat-generic-v1 tools over a StateStore)."""
from __future__ import annotations

import json

from edat.state.clock import Clock
from edat.state.env import ToolInfo, ToolResult
from edat.state.store import StateStore, diff
from edat.tools_generic.policy import build_system_prompt
from edat.tools_generic.registry import REGISTRY, ToolError, schema


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)


def _error(message: str, outcome: str = "failed") -> ToolResult:
    return ToolResult(content=_dump({"error": message}), outcome=outcome, effect=False)


class GenericEnvironment:
    """Implements edat.state.env.ToolEnvironment for the neutral lane."""

    lane = "neutral"

    def __init__(self, store: StateStore | None = None, *, clock: Clock | None = None) -> None:
        if store is None:
            store = StateStore(clock=clock)
        elif clock is not None:
            store.clock = clock
        self.store = store
        from edat.tools_generic import TOOLSET_ID   # registers every tool module
        self.toolset_id = TOOLSET_ID

    # ------------------------------------------------------------ menu
    def system_prompt(self, *, policy: str | None = None, preferences: list[str] | None = None) -> str:
        return build_system_prompt(policy, preferences, clock=self.store.clock)

    def tool_names(self) -> list[str]:
        return list(REGISTRY)

    def schemas(self, names: list[str] | None = None) -> list[dict]:
        if names is None:
            return [schema(s) for s in REGISTRY.values()]
        out = []
        for n in names:
            if n not in REGISTRY:
                raise KeyError(n)
            out.append(schema(REGISTRY[n]))
        return out

    def info(self, name: str) -> ToolInfo | None:
        spec = REGISTRY.get(name)
        if spec is None:
            return None
        return ToolInfo(name=spec.name, effect=spec.effect, egress=spec.egress, confirm=False, domain=spec.domain)

    # ------------------------------------------------------------ calls
    def call(self, name: str, arguments: dict | str | None) -> ToolResult:
        spec = REGISTRY.get(name)
        if spec is None:
            return _error(f"unknown tool: {name}")
        args = arguments if arguments is not None else {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                return _error("arguments are not valid JSON")
        if not isinstance(args, dict):
            return _error("arguments must be a JSON object")
        props = spec.parameters.get("properties", {})
        for key in args:
            if key not in props:
                return _error(f"unexpected argument: {key}")
        for key in spec.required:
            if args.get(key) is None:
                return _error(f"missing required argument: {key}")
        # effect = the store really changed (a write that sets a value it already had is not one).
        before = self.store.snapshot() if spec.effect else None

        def changed() -> bool:
            return before is not None and diff(before, self.store.snapshot()) != {}
        try:
            out = spec.handler(self.store, args)
        except ToolError as exc:
            return ToolResult(content=_dump({"error": exc.message}), outcome=exc.outcome, effect=changed())
        except Exception as exc:   # a handler bug or bad input it did not foresee
            return ToolResult(content=_dump({"error": f"tool failed: {type(exc).__name__}: {exc}"}),
                              outcome="failed", effect=changed())
        if isinstance(out, ToolResult):
            return out
        return ToolResult(content=_dump(out), outcome="succeeded", effect=changed())

    # ------------------------------------------------------------ world
    def seed(self, records: dict[str, list[dict]]) -> None:
        for collection, rows in (records or {}).items():
            for row in rows:
                self.store.add(collection, row, log=False)

    def state(self) -> StateStore:
        return self.store
