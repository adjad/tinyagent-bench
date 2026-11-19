"""Scenarios: case-specific world setup on top of the apps' seeded state.

Each module registers functions with `@scenario("<name>")` from
`edat.screen.cases.bind`. A scenario is `fn(world, rng, params, case) -> slots`.
Importing this package imports every scenario module.
"""
from __future__ import annotations

import importlib
import pkgutil

for _m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_m.name}")
