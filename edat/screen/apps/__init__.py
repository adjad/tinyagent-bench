"""The invented apps of Screen World (spec 11 §3.5) and their registry.

An app is an `App` subclass with a `name` (state namespace and Dock key), a
`title` (the generic word shown on screen), a `home` screen, and a
`screen_<name>(world, **params) -> Screen` method per screen. Each app also
has `seed(world, rng, params) -> slots`, which writes its default data state
from the world seed and returns the values a case may refer to as `{slot}`.

Every element key starts with the app's name (`settings.battery.low_power`),
and every control that sends, buys, deletes, publishes, resets or cancels is
marked `irreversible` (the authoring lint checks labels against Harbor's
consequential patterns).
"""
from __future__ import annotations

import importlib
import random
from typing import Any

from edat.screen.world.elements import Screen

APP_MODULES = {
    "settings": "edat.screen.apps.settings",
    "mail": "edat.screen.apps.mail",
    "messages": "edat.screen.apps.messages",
    "calendar": "edat.screen.apps.calendar",
    "notes": "edat.screen.apps.notes",
    "files": "edat.screen.apps.files",
    "browser": "edat.screen.apps.browser",
}
DOCK_ORDER = ("mail", "messages", "calendar", "notes", "files", "settings", "browser")

_CACHE: dict[str, "App"] = {}


class App:
    name = "app"
    title = "App"
    home = "main"

    def render(self, world, screen: str, params: dict) -> Screen:
        fn = getattr(self, f"screen_{screen.replace('.', '_').replace('-', '_')}", None)
        if fn is None:
            raise KeyError(f"{self.name}: no screen {screen!r}")
        return fn(world, **(params or {}))

    def seed(self, world, rng: random.Random, params: dict) -> dict[str, Any]:
        return {}


def get_app(name: str) -> App:
    if name not in _CACHE:
        if name not in APP_MODULES:
            raise KeyError(f"unknown app {name!r}")
        mod = importlib.import_module(APP_MODULES[name])
        _CACHE[name] = mod.APP
    return _CACHE[name]


def available_apps() -> list[str]:
    out = []
    for name in DOCK_ORDER:
        try:
            get_app(name)
        except (ImportError, AttributeError):
            continue
        out.append(name)
    return out
