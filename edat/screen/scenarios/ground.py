"""Scenarios for family G (Ground), plus the setup steps the Read and Form scenarios share.

`apply_setup(world, spec)` runs a case's `world.params.<family>` block in this order; every string is
filled from the slots the apps have set (`{slot}`), and slots it creates are returned:

    force:        {state path: value}          set data state before the trial (an initial value)
    mail_plant:   [{id, from_name, subject, days_ago, time, body}]   extra received mail
    trash_plant:  [{id, name, folder}]         extra files in the Files Trash
    file_ids:     {slot: file name}            the id of the file with that name, as {slot}
    slugs:        {slot: text}                 the element-key slug of a seeded name (`Trail Buds` -> trail_buds)
    list_index:   true                         `list_idx_<slug>` = the position of each Notes list
    push:         [{app, screen, params}]      extra screens on an app's stack (a slot-dependent start)
    probe:        {app, screen, params} | {ui: [path parts], value}   sets `probe.reached` once it holds
                  (+ `capture: true`: copy the Browser URL query into `probe.query` each time)

`probe` is a test hook in data state (`probe.reached`, `probe.query`) for targets whose activation only
navigates or changes UI state: the case checks it in `final_state`, so the do-nothing model fails and a
model that reaches the same place by another route still succeeds.
"""
from __future__ import annotations

from datetime import date, timedelta

from edat.screen.cases.bind import fill, scenario
from edat.screen.world.core import Trigger


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(s).lower()).strip("_")


def _probe_holds(world, p: dict) -> bool:
    if "ui" in p:      # a list of path parts (a dotted string reads like a host name to the lint)
        path = p["ui"] if isinstance(p["ui"], str) else ".".join(p["ui"])
        return world.get("ui:" + path) == p.get("value", True)
    app = p["app"]
    if world.front != app or world.dialog(app) is not None:
        return False
    fr = world.frame(app)
    if p.get("screen") and fr["screen"] != p["screen"]:
        return False
    for k, v in (p.get("params") or {}).items():
        have = fr["params"].get(k)
        if k == "url":
            if str(v) not in str(have or ""):
                return False
        elif str(have) != str(v):
            return False
    return True


def _install_probe(world, p: dict) -> None:
    world.state["probe"] = {"reached": False}
    capture = bool(p.get("capture"))

    def fire(w):
        w.state["probe"]["reached"] = True
        if capture:
            from edat.screen.apps.sites.base import split_url
            _, path, query = split_url(w.frame("browser")["params"].get("url", ""))
            w.state["probe"]["query"] = {"path": path, **{k: str(v) for k, v in query.items()}}

    world.triggers.append(Trigger(name="probe", when=lambda w, last: _probe_holds(w, p), fire=fire,
                                  repeat=capture))


def apply_setup(world, spec: dict) -> dict:
    slots: dict = {}

    def f(x):
        return fill(x, {**world.slots, **slots})

    for path, value in (spec.get("force") or {}).items():
        world.set(f(path), f(value))

    today = date.fromisoformat(world.clock[:10])
    for m in spec.get("mail_plant") or []:
        m = f(m)
        d = today - timedelta(days=int(m.pop("days_ago", 1)))
        body = m.pop("body", None) or ["Hi,", "Just a short note.", m["from_name"].split()[0]]
        first, last = m["from_name"].split()[0].lower(), m["from_name"].split()[-1].lower()
        world.state["mail"]["inbox"].append({
            "from_email": f"{first}.{last}@mail.example", "to": world.slots.get("my_email", ""),
            "date": d.isoformat(), "time": m.pop("time", "10:15"), "unread": False, "body": body, **m})

    for t in spec.get("trash_plant") or []:
        t = f(t)
        name = t["name"]
        ext = name.rsplit(".", 1)[-1].lower()
        from edat.screen.apps.files import kind_for
        world.state["files"]["trash"].append({
            "id": t["id"], "name": name, "folder": t.get("folder", "Documents"), "kind": kind_for(name),
            "size_kb": int(t.get("size_kb", 120 if ext != "jpg" else 2400)),
            "modified": (today - timedelta(days=int(t.get("days_ago", 30)))).isoformat() + "T11:20"})

    for slot, name in (spec.get("file_ids") or {}).items():
        name = f(name)
        hit = next((x for x in world.state["files"]["items"] if x["name"] == name), None)
        if hit is None:
            raise KeyError(f"no file named {name!r}")
        slots[slot] = hit["id"]

    if spec.get("list_index"):
        for i, l in enumerate(world.state["notes"]["lists"]):
            slots[f"list_idx_{_slug(l['name'])}"] = i

    for slot, value in (spec.get("slugs") or {}).items():
        slots[slot] = _slug(f(value))

    for fr in spec.get("push") or []:
        fr = f(fr)
        world.nav["stacks"][fr["app"]].append({"screen": fr["screen"], "params": dict(fr.get("params") or {}),
                                               "offset": 0})

    if spec.get("probe"):
        _install_probe(world, f(spec["probe"]))
    return slots


@scenario("ground_setup")
def ground_setup(world, rng, params, case) -> dict:
    return apply_setup(world, dict(params.get("ground") or {}))
