"""Scenarios for family X (Cross-app): information carried from one app into another (spec 11 §3.7).

The setup helpers (`scenarios/flow.py`) plant or align the source record (a friend's email with a new
address, a booking code from the clinic that the site is for, a PDF in Downloads, a short grocery list)
and can open a page in the Browser behind the start app (`browser_open`). An optional
`world.params.disrupt` list adds a surprise (L5: two apps with a surprise), as in family D.

    world:
      generator: cross_setup
      params: {setup: [clinic_align, browser_open], browser_url: "clinic.example/request",
               messages: {with_code: {kind: booking}}}
"""
from __future__ import annotations

import random

from edat.screen.apps import dialogs
from edat.screen.cases.bind import fill, scenario
from edat.screen.scenarios.flow import decode, run_setup


@scenario("cross_setup")
def cross_setup(world, rng: random.Random, params: dict, case: dict) -> dict:
    slots = run_setup(world, rng, params)
    for spec in params.get("disrupt") or []:
        dialogs.install(world, decode(fill(dict(spec), {**world.slots, **slots})))
    return slots
