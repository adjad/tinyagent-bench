"""The text observation (`ax_text` track, spec 11 §3.1).

Adapted from Harbor's ModelView line format (`browser-extension/shared/model-view.js:351-366`,
Apache-2.0): one line per element, `[id] role "name"`, then state words and a
value. Indentation shows grouping. At most 40 element lines are visible; the
rest are counted. The Dock line is always shown.

    App: Settings · Window: "Battery" · Screen 2 · Visible 1–18 of 31
    Dock: [d42] Mail · [d17] Messages · [d88] Settings (front) · [d05] Browser
    [318] button "Back"
    [227] switch "Low Power Mode" off
    [133] popup "Turn display off after" value "5 minutes"
    … 13 more below (scroll down)
"""
from __future__ import annotations

import hashlib

from edat.screen.world.core import VIEWPORT_LINES, Visible
from edat.screen.world.elements import El


def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _pick(world, key: str, salt: str, p: float) -> bool:
    h = hashlib.sha256(f"{world.seed}:{salt}:{key}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2 ** 32 < p


def apply_noise(world, elements: list[El]) -> list[El]:
    """Accessibility-noise knobs (levels 4-5): unnamed icon buttons and generic nested groups."""
    noise = world.noise or {}
    p_icon = float(noise.get("unnamed_icons") or 0)
    nested = bool(noise.get("nested_groups"))
    if not p_icon and not nested:
        return elements

    def fix(els: list[El], depth: int) -> list[El]:
        out: list[El] = []
        for e in els:
            e.children = fix(e.children, depth + 1)
            if p_icon and e.role == "button" and e.meta.get("icon") and _pick(world, e.key, "icon", p_icon):
                out.append(El(key=e.key + ".label", role="text", name=e.name))
                e.name = ""
            if nested and depth == 0 and e.role not in ("group", "list", "dialog") and _pick(world, e.key, "wrap", 0.25):
                out.append(El(key=e.key + ".wrap", role="group", name="", children=[e]))
            else:
                out.append(e)
        return out

    return fix(elements, 0)


def apply_banners(world, app: str, elements: list[El]) -> list[El]:
    """Banners (notification or promo strips) and relabelled controls, from UI state.

    `world.ui["banners"]`: [{key, app ("*" for every app), title, text, buttons: [{label, key?, close?: bool}]}]
    `world.ui["relabel"]`: {element key: new name} (a layout change after an action).
    `world.ui["extras"]`: [{key, app, screen?, url?, role (text|button|link), name, meta, position: top|bottom}]
    — planted elements, e.g. an injection in an accessible name or in small page text.
    """
    relabel = world.ui.get("relabel") or {}
    if relabel:
        for e in elements:
            for x, _ in e.walk():
                if x.key in relabel:
                    x.name = relabel[x.key]
    top: list[El] = []
    for b in world.ui.get("banners") or []:
        if b.get("app", "*") not in ("*", app):
            continue
        kids = [El(key=f"{b['key']}.text", role="text", name=b.get("text", ""), meta=dict(b.get("meta") or {}))]
        for i, btn in enumerate(b.get("buttons") or [{"label": "Dismiss", "close": True}]):
            def act(w, bkey=b["key"], spec=btn):
                if spec.get("close", True):
                    w.ui["banners"] = [x for x in w.ui.get("banners") or [] if x["key"] != bkey]
                if spec.get("note"):
                    w.note(spec["note"], banner=bkey)
            kids.append(El(key=btn.get("key") or f"{b['key']}.btn{i}", role="button", name=btn["label"],
                           on_click=act, cleared=True))
        top.append(El(key=b["key"], role="group", name=b.get("title", "Notification"), children=kids))
    extras = [x for x in world.ui.get("extras") or [] if _extra_applies(world, app, x)]
    for x in extras:
        def clicked(w, key=x["key"]):
            w.note("extra_clicked", key=key)
        el = El(key=x["key"], role=x.get("role", "text"), name=x["name"], meta=dict(x.get("meta") or {}),
                on_click=clicked if x.get("role", "text") in ("button", "link") else None, cleared=True)
        if x.get("position", "top") == "top":
            top.append(el)
        else:
            elements = elements + [el]
    return top + elements if top else elements


def _extra_applies(world, app: str, x: dict) -> bool:
    if x.get("app", "*") not in ("*", app):
        return False
    fr = world.frame(app)
    if x.get("screen") and fr["screen"] != x["screen"]:
        return False
    if x.get("url") and x["url"] not in str(fr["params"].get("url", "")):
        return False
    return True


def _expand(world, el: El) -> None:
    """A clicked popup shows its options as `option` children until one is chosen."""
    if el.role == "popup" and world.ui["expanded"].get(el.key):
        el.expanded = True
        el.children = [El(key=f"{el.key}.opt.{i}", role="option", name=lab,
                          selected=(lab == (el.value if el.value is not None else world.get(el.bind or "", None))))
                       for i, lab in enumerate(el.option_labels())]
    for c in el.children:
        _expand(world, c)


def _bound_value(world, el: El) -> None:
    if el.bind is None:
        return
    v = world.get(el.bind)
    if el.role == "switch" and el.on is None:
        el.on = bool(v)
    elif el.role == "checkbox" and el.checked is None:
        el.checked = bool(v)
    elif el.role in ("textbox", "popup", "slider") and el.value is None:
        el.value = "" if v is None else str(v)
    elif el.role == "group" and el.options:
        for c in el.children:
            if c.role == "radio" and c.checked is None:
                c.checked = (c.name == v)
    elif el.role == "list" and v is not None:
        for c in el.children:
            if c.role == "row":
                c.selected = c.selected or (c.name == v)


def line_for(world, el: El, depth: int, focus: str | None) -> str:
    parts = [f"{'  ' * depth}[{world.id_for(el.key)}] {el.role} {_q(el.name)}"]
    if el.role == "switch":
        parts.append("on" if el.on else "off")
    if el.role in ("checkbox", "radio") and el.checked:
        parts.append("checked")
    if el.selected:
        parts.append("selected")
    if el.expanded:
        parts.append("expanded")
    if el.disabled:
        parts.append("disabled")
    if focus and el.key == focus:
        parts.append("focused")
    if el.role in ("textbox", "popup", "slider") and el.value is not None:
        parts.append(f"value {_q(el.value)}")
    elif el.value is not None and el.role not in ("switch", "checkbox"):
        parts.append(f"value {_q(el.value)}")
    return " ".join(parts)


def flatten(world, elements: list[El]) -> tuple[list[tuple[El, int]], dict[str, El]]:
    rows: list[tuple[El, int]] = []
    parents: dict[str, El] = {}

    def go(els: list[El], depth: int, parent: El | None) -> None:
        for e in els:
            _bound_value(world, e)
            _expand(world, e)
            if parent is not None:
                parents[e.key] = parent
            rows.append((e, depth))
            go(e.children, depth + 1, e)

    go(elements, 0, None)
    return rows, parents


def app_title(world, app: str) -> str:
    return world.apps[app].title


def dock_line(world) -> tuple[str, dict[str, El]]:
    items, by_id = [], {}
    for a in world.app_names:
        el = El(key=f"dock.{a}", role="button", name=app_title(world, a), target_app=a, meta={"dock": True})
        i = world.id_for(el.key)
        by_id[i] = el
        items.append(f"[{i}] {el.name}" + (" (front)" if a == world.front else ""))
    return "Dock: " + " · ".join(items), by_id


def render(world) -> Visible:
    app = world.front
    fr = world.frame()
    screen = world.current_screen()
    elements = apply_banners(world, app, screen.elements)
    elements = apply_noise(world, elements)
    rows, parents = flatten(world, elements)
    focus = world.nav.get("focus")
    dialog = world.dialog(app)
    dock, dock_ids = dock_line(world)
    lines_all = [(e, d, line_for(world, e, d, focus)) for e, d in rows]
    all_ids: dict[str, El] = {world.id_for(e.key): e for e, _ in rows}
    by_id: dict[str, El] = dict(dock_ids)
    behind: dict[str, El] = {}
    total = len(lines_all)
    depth_label = len(world.nav["stacks"][app])
    header = f"App: {app_title(world, app)} · Window: {_q(screen.window)} · Screen {depth_label}"
    body: list[str] = []
    offset = 0
    if dialog is not None:
        drows, dparents = flatten(world, [El(key=dialog.key, role=dialog.role, name=dialog.title,
                                              children=dialog.elements)])
        parents.update(dparents)
        header += f" · Dialog open"
        for e, d in drows:
            i = world.id_for(e.key)
            by_id[i] = e
            all_ids[i] = e
            body.append(line_for(world, e, d, focus))
        for e, _ in rows:
            behind[world.id_for(e.key)] = e
        body.append("(The window behind the dialog cannot be used until the dialog is closed.)")
    else:
        offset = max(0, min(int(fr.get("offset", 0)), max(0, total - VIEWPORT_LINES)))
        fr["offset"] = offset
        shown = lines_all[offset: offset + VIEWPORT_LINES]
        if total:
            header += f" · Visible {offset + 1}–{offset + len(shown)} of {total}"
        else:
            header += " · Visible 0 of 0"
        if offset:
            body.append(f"… {offset} more above (scroll up)")
        for e, _, ln in shown:
            by_id[world.id_for(e.key)] = e
            body.append(ln)
        below = total - offset - len(shown)
        if below > 0:
            body.append(f"… {below} more below (scroll down)")
    text = "\n".join([header, dock] + body)
    return Visible(text=text, by_id=by_id, all_ids={**all_ids, **dock_ids}, behind=behind, parents=parents,
                   total=total, offset=offset, app=app, window=screen.window, dialog=dialog,
                   meta=dict(screen.meta))


def stub_line(vis: Visible, label: str) -> str:
    """The one-line stub that replaces an earlier observation in history (§3.1)."""
    return f"(screen {label}: {app_title_from(vis)} · {_q(vis.window)})"


def app_title_from(vis: Visible) -> str:
    first = vis.text.split("\n", 1)[0]
    if first.startswith("App: "):
        return first[5:].split(" · ", 1)[0]
    return vis.app
