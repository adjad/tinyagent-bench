"""Scenarios for family F (Form): the shared setup steps of scenarios/ground.py under `world.params.form`.

Form cases use `list_index` (the path of a Notes list in `final_state`) and the `probe` with `capture`
for the travel.example search form, which only navigates: each results page it opens copies its query
(`from`, `to`, `date`, `pax`, `class`) into `probe.query`, so the submitted fields can be checked.
"""
from __future__ import annotations

from edat.screen.cases.bind import scenario
from edat.screen.scenarios.ground import apply_setup


@scenario("form_setup")
def form_setup(world, rng, params, case) -> dict:
    return apply_setup(world, dict(params.get("form") or {}))
