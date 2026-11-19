"""Scenarios for family R (Read): the shared setup steps (scenarios/ground.py) plus read-only slots.

    world.params.read = {<ground setup steps>,
                         battery_pair: true,   # bat_a, bat_b: two apps in Battery Usage; bat_more: the higher
                         second_open: true,    # second_open_count: unticked items in the second Notes list
                         unique_largest: true, # one Storage category clearly largest (a tie is broken by 1 GB)
                         invite_line1: true}   # invite_line1: the street part of the Mail invitation's place

Nothing here changes what a Read case may change (nothing: `unchanged: all`); the slots only name values
that are already on some screen.
"""
from __future__ import annotations

from edat.screen.cases.bind import scenario
from edat.screen.scenarios.ground import apply_setup


@scenario("read_setup")
def read_setup(world, rng, params, case) -> dict:
    spec = dict(params.get("read") or {})
    slots = apply_setup(world, spec)
    if spec.get("battery_pair"):
        rows = world.state["settings"]["battery"]["by_app"]
        a, b = rng.sample(rows[:4], k=2)
        slots.update({"bat_a": a["app"], "bat_b": b["app"],
                      "bat_more": a["app"] if a["share"] > b["share"] else b["app"],
                      "bat_less": b["app"] if a["share"] > b["share"] else a["app"]})
    if spec.get("second_open"):
        lists = world.state["notes"]["lists"]
        second = lists[1] if len(lists) > 1 else lists[0]
        slots["second_open_count"] = sum(1 for i in second["items"] if not i["done"])
    if spec.get("unique_largest"):
        st = world.state["settings"]["storage"]
        cats = st["categories"]
        top = max(cats, key=cats.get)
        if list(cats.values()).count(cats[top]) > 1:
            cats[top] += 1
            st["used_gb"] += 1
        world.slots.update({"largest_category": top, "storage_used": st["used_gb"]})   # bind lets world.slots win
    if spec.get("invite_line1"):
        slots["invite_line1"] = str(world.slots["invite_place"]).split(",")[0]
    return slots
