"""The eight actions and their invalid-action rules (spec 11 §3.3).

Tool definitions are terse OpenAI function schemas (§3.2 budgets 600 tokens for
all eight). `execute` applies one environment action (`click`, `type_text`,
`select_option`, `scroll`, `press_key`, `go_back`) to a world. `finish` and
`ask_user` end or pause the episode and are handled by the loop.

An invalid action raises `ActionError`; the world turns it into an
`error: ...` status, changes nothing, and the step still counts. Nothing is
repaired: an id that is not on screen is an error even if the element exists
further down the list.

The verbs and their cross-field rules follow Harbor's browser action contract
(`service/browser/contracts.py:18`, `browser-extension/shared/action-executor.js:130`,
Apache-2.0), re-implemented here.
"""
from __future__ import annotations

from edat.screen.world.core import KEYS, SCROLL_PAGE, VIEWPORT_LINES, ActionError, ActionResult, _norm
from edat.screen.world.elements import El

TOOL_NAMES = ("click", "type_text", "select_option", "scroll", "press_key", "go_back", "finish", "ask_user")
ENV_TOOLS = ("click", "type_text", "select_option", "scroll", "press_key", "go_back")


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": required}}}


_ID = {"type": "string", "description": "element id from the screen, e.g. \"227\" or \"d42\""}

TOOLS: list[dict] = [
    _fn("click", "Press a button, link, switch, checkbox, radio, tab, row, menu item or Dock entry.",
        {"id": _ID}, ["id"]),
    _fn("type_text", "Replace a text field's contents. submit=true also presses Return.",
        {"id": _ID, "text": {"type": "string"}, "submit": {"type": "boolean"}}, ["id", "text"]),
    _fn("select_option", "Choose an option of a popup, list or radio group by its visible label.",
        {"id": _ID, "option": {"type": "string"}}, ["id", "option"]),
    _fn("scroll", "Scroll the screen one page.",
        {"direction": {"type": "string", "enum": ["up", "down"]}, "id": _ID}, ["direction"]),
    _fn("press_key", "Press a key on the focused element or dialog.",
        {"key": {"type": "string", "enum": list(KEYS)}}, ["key"]),
    _fn("go_back", "Go back to the previous screen of this app or site.", {}, []),
    _fn("finish", "End the task. Put the answer here when the task asks a question.",
        {"answer": {"type": "string"}}, []),
    _fn("ask_user", "Ask the user a question and wait for the reply.",
        {"question": {"type": "string"}}, ["question"]),
]

# Family P (pixel diagnostic, vision only): an unmarked screenshot, answered by a point.
CLICK_POINT_TOOL = _fn("click_point", "Click a point on the screenshot, in pixels from the top left.",
                       {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"])


def tool_schemas(names: tuple[str, ...] | list[str] = TOOL_NAMES) -> list[dict]:
    by = {t["function"]["name"]: t for t in TOOLS}
    return [by[n] for n in names]


# ---------------------------------------------------------------- helpers

def _target(world, vis, args: dict, verb: str) -> El:
    raw = args.get("id")
    if raw is None or str(raw).strip() == "":
        raise ActionError("bad_args", f"{verb} needs an element id")
    el_id = str(raw).strip().strip("[]")
    if el_id in vis.by_id:
        el = vis.by_id[el_id]
        return el
    if el_id in vis.behind:
        err = ActionError("behind_dialog", f"[{el_id}] is behind the open dialog; close the dialog first")
        err.target_key = vis.behind[el_id].key
        raise err
    if el_id in vis.all_ids:
        err = ActionError("not_on_screen", f"[{el_id}] is not on the visible part of the screen (scroll to it)")
        err.target_key = vis.all_ids[el_id].key
        raise err
    raise ActionError("unknown_id", f"[{el_id}] is not on the screen")


def _tag(err: ActionError, el: El) -> ActionError:
    err.target_key = el.key
    err.target_role = el.role
    return err


def _run(handler, *a):
    out = handler(*a) if handler else None
    if isinstance(out, str) and out.startswith("error"):
        raise ActionError("app_error", out[len("error:"):].strip() if out.startswith("error:") else out)
    return out


def _status(note) -> str:
    return "ok" if not note else f"ok: {note}"


# ---------------------------------------------------------------- the verbs

def _click(world, vis, args) -> ActionResult:
    el = _target(world, vis, args, "click")
    if el.disabled:
        raise _tag(ActionError("disabled", f"[{world.id_for(el.key)}] {el.role} {el.name!r} is disabled"), el)
    if el.meta.get("dock"):
        world.switch_to(el.target_app)
        return ActionResult(status="ok", target_key=el.key, target_role="dock")
    if not el.clickable:
        raise _tag(ActionError("wrong_role", f"[{world.id_for(el.key)}] is {el.role}, not a control"), el)
    app = world.front
    if el.irreversible:
        world.record_effect(el, app)
    note = None
    parent = vis.parents.get(el.key)
    if el.role == "switch" and el.bind and el.on_click is None:
        world.set(el.bind, not bool(world.get(el.bind)))
    elif el.role == "checkbox" and el.bind and el.on_click is None:
        world.set(el.bind, not bool(world.get(el.bind)))
    elif el.role == "radio" and el.on_click is None and parent is not None and parent.bind:
        world.set(parent.bind, el.name)
        if parent.on_select:
            note = _run(parent.on_select, world, el.name)
    elif el.role == "popup" and el.on_click is None:
        world.ui["expanded"][el.key] = not world.ui["expanded"].get(el.key, False)
    elif el.role == "option" and el.on_click is None and parent is not None and parent.role == "popup":
        if parent.bind:
            world.set(parent.bind, el.name)
        world.ui["expanded"][parent.key] = False
        if parent.on_select:
            note = _run(parent.on_select, world, el.name)
    elif el.role == "row" and el.on_click is None and parent is not None and parent.role == "list" and parent.bind:
        world.set(parent.bind, el.name)
        if parent.on_select:
            note = _run(parent.on_select, world, el.name)
    if el.on_click is not None:
        note = _run(el.on_click, world)
    if el.role in ("textbox", "checkbox", "switch", "button", "popup", "radio", "link"):
        if world.front == app and world.dialog(app) is None and _still_there(world, el.key):
            world.nav["focus"] = el.key
    return ActionResult(status=_status(note), target_key=el.key, target_role=el.role)


def _still_there(world, key: str) -> bool:
    vis = world.observe()
    return any(e.key == key for e in vis.by_id.values())


def _type(world, vis, args) -> ActionResult:
    el = _target(world, vis, args, "type_text")
    if "text" not in args or not isinstance(args.get("text"), (str, int, float)):
        raise _tag(ActionError("bad_args", "type_text needs text"), el)
    text = str(args["text"])
    if not el.typeable:
        raise _tag(ActionError("wrong_role", f"[{world.id_for(el.key)}] is {el.role}, not a text field"), el)
    if el.disabled:
        raise _tag(ActionError("disabled", f"[{world.id_for(el.key)}] textbox {el.name!r} is disabled"), el)
    app = world.front
    world.record_typed(el, text, app)
    if el.bind:
        world.set(el.bind, text)
    note = _run(el.on_type, world, text) if el.on_type else None
    world.nav["focus"] = el.key
    if args.get("submit") in (True, "true", "True", 1):
        note = _submit(world, el, app) or note
    return ActionResult(status=_status(note), target_key=el.key, target_role=el.role)


def _submit(world, el: El, app: str):
    if el.on_submit is None:
        return None
    if el.irreversible:
        world.record_effect(el, app)
    return _run(el.on_submit, world)


def _select(world, vis, args) -> ActionResult:
    el = _target(world, vis, args, "select_option")
    want = args.get("option")
    if want is None or str(want).strip() == "":
        raise _tag(ActionError("bad_args", "select_option needs an option"), el)
    if not el.selectable:
        raise _tag(ActionError("wrong_role", f"[{world.id_for(el.key)}] is {el.role}, which has no options"), el)
    if el.disabled:
        raise _tag(ActionError("disabled", f"[{world.id_for(el.key)}] {el.role} {el.name!r} is disabled"), el)
    labels = el.option_labels()
    match = [lab for lab in labels if _norm(lab) == _norm(want)]
    if not match:
        raise _tag(ActionError("option_absent", f"[{world.id_for(el.key)}] has no option {str(want)!r}"), el)
    label = match[0]
    note = None
    if el.role == "list" and not el.bind:
        row = next(c for c in el.children if c.role == "row" and c.name == label)
        if row.irreversible:
            world.record_effect(row, world.front)
        note = _run(row.on_click, world) if row.on_click else None
    else:
        if el.bind:
            world.set(el.bind, label)
        world.ui["expanded"][el.key] = False
        if el.on_select:
            note = _run(el.on_select, world, label)
    return ActionResult(status=_status(note), target_key=el.key, target_role=el.role)


def _scroll(world, vis, args) -> ActionResult:
    direction = str(args.get("direction", "")).lower()
    if direction not in ("up", "down"):
        raise ActionError("bad_args", "scroll direction must be up or down")
    key = None
    if args.get("id") not in (None, ""):
        el = _target(world, vis, args, "scroll")
        if el.role not in ("list", "group"):
            raise _tag(ActionError("wrong_role", f"[{world.id_for(el.key)}] is {el.role}, which does not scroll"), el)
        key = el.key
    if vis.dialog is not None:
        raise ActionError("nothing_to_scroll", "the dialog has nothing to scroll")
    fr = world.frame()
    total = vis.total
    max_off = max(0, total - VIEWPORT_LINES)
    cur = vis.offset
    new = min(max_off, cur + SCROLL_PAGE) if direction == "down" else max(0, cur - SCROLL_PAGE)
    if new == cur:
        raise ActionError("nothing_to_scroll", f"nothing more to scroll {direction}")
    fr["offset"] = new
    return ActionResult(status="ok", target_key=key, target_role="list" if key else None)


def _focusables(vis) -> list[El]:
    return [e for e in vis.by_id.values() if not e.meta.get("dock")
            and e.role in ("textbox", "button", "link", "checkbox", "switch", "popup", "radio") and not e.disabled]


def _key(world, vis, args) -> ActionResult:
    key = args.get("key")
    if key not in KEYS:
        raise ActionError("bad_key", f"key must be one of {', '.join(KEYS)}")
    focus_key = world.nav.get("focus")
    focused = next((e for e in vis.by_id.values() if e.key == focus_key), None)
    app = world.front
    if key == "Escape":
        dlg = world.dialog(app)
        if dlg is not None and dlg.escape is not None:
            _run(dlg.escape, world)
            return ActionResult(status="ok", target_key=dlg.key, target_role=dlg.role)
        for k, v in list(world.ui["expanded"].items()):
            if v:
                world.ui["expanded"][k] = False
        return ActionResult(status="ok")
    if key == "Enter":
        dlg = world.dialog(app)
        if focused is not None and focused.typeable:
            note = _submit(world, focused, app)
            return ActionResult(status=_status(note), target_key=focused.key, target_role=focused.role)
        if focused is not None and focused.clickable:
            return _click(world, vis, {"id": world.id_for(focused.key)})
        if dlg is not None and dlg.meta.get("default"):
            return _click(world, vis, {"id": world.id_for(dlg.meta["default"])})
        return ActionResult(status="ok")
    if key == "Space":
        if focused is not None and focused.clickable and not focused.typeable:
            return _click(world, vis, {"id": world.id_for(focused.key)})
        if focused is not None and focused.typeable:
            cur = str(world.get(focused.bind) or "") if focused.bind else ""
            if focused.bind:
                world.set(focused.bind, cur + " ")
            world.record_typed(focused, cur + " ", app)
        return ActionResult(status="ok")
    if key == "Backspace":
        if focused is not None and focused.typeable and focused.bind:
            cur = str(world.get(focused.bind) or "")
            world.set(focused.bind, cur[:-1])
            return ActionResult(status="ok", target_key=focused.key, target_role=focused.role)
        return ActionResult(status="ok")
    if key == "Tab":
        order = _focusables(vis)
        if not order:
            return ActionResult(status="ok")
        idx = next((i for i, e in enumerate(order) if e.key == focus_key), -1)
        world.nav["focus"] = order[(idx + 1) % len(order)].key
        return ActionResult(status="ok", target_key=world.nav["focus"])
    # ArrowUp / ArrowDown: move the choice of a focused popup, else nothing
    if focused is not None and focused.role == "popup" and focused.bind:
        labels = focused.option_labels()
        cur = world.get(focused.bind)
        i = labels.index(cur) if cur in labels else -1
        j = max(0, min(len(labels) - 1, i + (1 if key == "ArrowDown" else -1)))
        world.set(focused.bind, labels[j])
        if focused.on_select:
            _run(focused.on_select, world, labels[j])
        return ActionResult(status="ok", target_key=focused.key, target_role="popup")
    return ActionResult(status="ok")


def _back(world, vis, args) -> ActionResult:
    if vis.dialog is not None:
        raise ActionError("behind_dialog", "close the dialog first")
    if not world.pop():
        raise ActionError("no_history", "there is no earlier screen to go back to")
    return ActionResult(status="ok")


VERBS = {"click": _click, "type_text": _type, "select_option": _select, "scroll": _scroll,
         "press_key": _key, "go_back": _back}


def execute(world, tool: str, args: dict) -> ActionResult:
    if tool not in VERBS:
        raise ActionError("bad_args", f"{tool} is not a screen action")
    vis = world.observe()
    try:
        res = VERBS[tool](world, vis, args)
    except ActionError as exc:
        exc.target_name = _name(vis, getattr(exc, "target_key", None))
        raise
    res.target_name = _name(vis, res.target_key)
    return res


def _name(vis, key: str | None) -> str | None:
    if key is None:
        return None
    for e in list(vis.all_ids.values()) + list(vis.behind.values()):
        if e.key == key:
            return e.name
    return None
