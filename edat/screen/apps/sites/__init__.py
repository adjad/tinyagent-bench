"""The Browser's five invented sites (spec 11 §3.5) and their registry: host -> site object.

Each module defines `SITE = <Site subclass>()` with `host`, `short`, `title`,
`seed(world, rng, params) -> slots` and `render(world, path, query) -> Screen`.
Shared helpers (URLs, navigation, sign-in wall, `web.submissions`) are in `base.py`.
"""
from __future__ import annotations

import importlib

SITE_MODULES = ("shop", "travel", "clinic", "forum", "news")   # bookmark order

_SITES: list = []


def all_sites() -> list:
    if not _SITES:
        for m in SITE_MODULES:
            _SITES.append(importlib.import_module(f"{__name__}.{m}").SITE)
    return list(_SITES)


def by_host() -> dict:
    return {s.host: s for s in all_sites()}


def by_short() -> dict:
    return {s.short: s for s in all_sites()}
