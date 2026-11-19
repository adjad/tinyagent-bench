"""Elements of a simulated screen (spec 11 §3.1, §3.3).

An app renders each screen as a tree of `El` objects built fresh from the
world's state. An element's `key` is stable across seeds (cases and the grader
refer to it); its on-screen id is assigned per trial by the world.

Generic behaviour is declared, not coded, where it can be:

* `bind` on a switch or checkbox: a click toggles the bound boolean;
* `bind` on a textbox: `type_text` writes the bound string;
* `bind` on a popup, list or radio group with `options`: `select_option` writes
  the chosen label;
* `on_click`, `on_type`, `on_select`, `on_submit` handlers for everything else.

A bind path starting with `ui:` lives in the world's UI state (unsubmitted form
fields, search boxes), which grading never treats as data. Any other path is
data state (`settings.battery.low_power`).

`irreversible` names the effect kind of a control that sends, buys, deletes,
publishes, resets or cancels (§3.8). The world logs every activation of such
a control before its handler runs, so the grader never depends on an app
remembering to report it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ROLES = ("button", "link", "textbox", "checkbox", "switch", "radio", "popup", "option", "slider", "tab",
         "row", "cell", "heading", "text", "image", "dialog", "alert", "menu", "menuitem", "group", "list")

CLICKABLE = frozenset({"button", "link", "checkbox", "switch", "radio", "tab", "row", "menuitem", "option",
                       "popup", "cell"})
TYPEABLE = frozenset({"textbox"})
SELECTABLE = frozenset({"popup", "list", "group", "slider"})   # group only when it has options (radio group)
SCROLLABLE = frozenset({"list", "group"})

EFFECT_KINDS = ("send", "buy", "delete", "permanent_delete", "publish", "reset", "cancel", "unsubscribe",
                "submit_external", "grant")

SECRET_KINDS = ("password", "card", "otp")


@dataclass
class El:
    key: str
    role: str
    name: str = ""
    value: str | None = None
    on: bool | None = None            # switch state
    checked: bool | None = None       # checkbox / radio state
    selected: bool = False
    disabled: bool = False
    expanded: bool | None = None
    options: list[str] | None = None  # popup, list, radio group, slider steps
    children: list["El"] = field(default_factory=list)
    bind: str | None = None
    on_click: Callable[..., Any] | None = None     # (world) -> status | None
    on_type: Callable[..., Any] | None = None      # (world, text) -> status | None
    on_submit: Callable[..., Any] | None = None    # (world) -> status | None (Return in a field)
    on_select: Callable[..., Any] | None = None    # (world, label) -> status | None
    irreversible: str | None = None   # effect kind, see EFFECT_KINDS
    cleared: bool = False             # label looks consequential but is deliberately not irreversible
    outbound: bool = False            # typed text leaves the device when the form is sent
    secret: str | None = None         # password | card | otp: a field that asks for a secret
    target_app: str | None = None     # a Dock entry or a link that opens another app
    meta: dict = field(default_factory=dict)   # small / low_contrast / icon / dock / ...

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"{self.key}: unknown role {self.role!r}")
        if self.irreversible is not None and self.irreversible not in EFFECT_KINDS:
            raise ValueError(f"{self.key}: unknown effect kind {self.irreversible!r}")
        if self.secret is not None and self.secret not in SECRET_KINDS:
            raise ValueError(f"{self.key}: unknown secret kind {self.secret!r}")

    # -------------------------------------------------------------- queries
    def walk(self, depth: int = 0):
        yield self, depth
        for c in self.children:
            yield from c.walk(depth + 1)

    @property
    def clickable(self) -> bool:
        if self.role == "group":
            return self.on_click is not None
        return self.role in CLICKABLE or self.on_click is not None

    @property
    def typeable(self) -> bool:
        return self.role in TYPEABLE

    @property
    def selectable(self) -> bool:
        if self.role == "group":
            return bool(self.options)
        if self.role == "list":
            return bool(self.options) or any(c.role == "row" for c in self.children)
        return self.role in SELECTABLE

    def option_labels(self) -> list[str]:
        if self.options:
            return list(self.options)
        if self.role == "list":
            return [c.name for c in self.children if c.role == "row"]
        if self.role == "group":
            return [c.name for c in self.children if c.role == "radio"]
        return []


def text(key: str, name: str, **kw) -> El:
    return El(key=key, role="text", name=name, **kw)


def button(key: str, name: str, on_click=None, **kw) -> El:
    return El(key=key, role="button", name=name, on_click=on_click, **kw)


def link(key: str, name: str, on_click=None, **kw) -> El:
    return El(key=key, role="link", name=name, on_click=on_click, **kw)


def heading(key: str, name: str, **kw) -> El:
    return El(key=key, role="heading", name=name, **kw)


def group(key: str, name: str, children: list[El], **kw) -> El:
    return El(key=key, role="group", name=name, children=children, **kw)


def switch(key: str, name: str, bind: str, **kw) -> El:
    return El(key=key, role="switch", name=name, bind=bind, **kw)


def checkbox(key: str, name: str, bind: str, **kw) -> El:
    return El(key=key, role="checkbox", name=name, bind=bind, **kw)


def textbox(key: str, name: str, bind: str, **kw) -> El:
    return El(key=key, role="textbox", name=name, bind=bind, **kw)


def popup(key: str, name: str, bind: str, options: list[str], **kw) -> El:
    return El(key=key, role="popup", name=name, bind=bind, options=list(options), **kw)


def radio_group(key: str, name: str, bind: str, options: list[str], **kw) -> El:
    return El(key=key, role="group", name=name, bind=bind, options=list(options), **kw)


@dataclass
class Screen:
    """One rendered screen of an app: a window title and its element tree."""

    window: str
    elements: list[El]
    meta: dict = field(default_factory=dict)   # e.g. signin_wall: host, error_page: True

    def walk(self):
        for e in self.elements:
            yield from e.walk(0)


@dataclass
class Dialog:
    """A modal sheet, alert or pop-up over an app's window.

    `escape` is the handler for Escape (None: Escape does nothing). The dialog
    is closed by its own button handlers calling `world.close_dialog(app)`.
    """

    key: str
    title: str
    elements: list[El]
    role: str = "dialog"            # dialog | alert
    escape: Callable[..., Any] | None = None
    meta: dict = field(default_factory=dict)   # kind: rate_app / cookie / fake_system ...
