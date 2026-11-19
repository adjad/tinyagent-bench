"""Scenarios for family D (Disrupt): an ordinary task with a surprise in the way (spec 11 §3.7).

`world.params.setup` runs the Flow setup helpers (`scenarios/flow.py`); then every entry of
`world.params.disrupt` is installed with `apps/dialogs.py` `install()` after its `{slots}` are
filled, so a trigger or a relabel can name a seeded element. Element keys in params are written with "/"
for "." (`settings/notif_app/{notif_app_key}/banner`): the authoring lint scans params for host names and
would read a dotted key as one; `decode()` turns them back.

    world:
      generator: disrupt_surprise
      params:
        setup: [settings_pick]
        disrupt:
          - {kind: rate_app, app: settings, when: {on_screen: [settings, notif_app]}}
          - {kind: transient_error, key: calendar/sheet/save}
          - {kind: relabel, key: "settings/notif_app/{notif_app_key}/sounds", name: "Alert sounds",
             when: {on_key: settings/main/notifications}}
"""
from __future__ import annotations

import random

from edat.screen.apps import dialogs
from edat.screen.cases.bind import fill, scenario
from edat.screen.scenarios.flow import decode, run_setup


@scenario("disrupt_surprise")
def disrupt_surprise(world, rng: random.Random, params: dict, case: dict) -> dict:
    slots = run_setup(world, rng, params)
    for spec in params.get("disrupt") or []:
        dialogs.install(world, decode(fill(dict(spec), {**world.slots, **slots})))
    return slots
