"""Screen World: the simulated screen as a deterministic state machine (spec 11 §3.4).

One `World` per trial. It holds

* `state`: the data the grader reads (`settings.battery.low_power`, `mail.sent[]`,
  `shop.orders[]`, `files[]`, ...), plain JSON-able values;
* `ui`: unsubmitted field values, expanded popups, sign-in flags — UI state the
  grader never treats as data;
* `nav`: the front app, a screen stack per app (each entry has its own scroll
  offset), the open dialog per app and the focused element;
* the per-trial element ids, the action log, the log of irreversible
  activations (`effects`) and of typed text (`typed`).

Nothing here reads the real clock, the network, the screen or any app. A trial
is a pure function of the case, the world seed and the actions, so a stored
action log replays to the same state and observation hashes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from edat.screen.world.elements import Dialog, El, Screen

VIEWPORT_LINES = 40          # §3.1: at most 40 element lines are visible
SCROLL_PAGE = 30             # one page; 10 lines of overlap
CHARS_PER_TOKEN = 3          # Harbor ModelView's estimate (length ÷ 3)
KEYS = ("Enter", "Escape", "Tab", "Backspace", "Space", "ArrowUp", "ArrowDown")


class ActionError(Exception):
    """An invalid action: changes nothing, costs a step, is counted (§3.3)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ActionResult:
    status: str                     # "ok", "ok: <note>" or "error: <message>"
    code: str = "ok"                # ok | not_on_screen | unknown_id | behind_dialog | disabled | wrong_role
                                    # | option_absent | nothing_to_scroll | bad_key | no_history | bad_args
    target_key: str | None = None
    target_role: str | None = None
    target_name: str | None = None
    changed: bool = False           # data, UI or navigation changed
    data_changed: bool = False      # data state changed

    @property
    def ok(self) -> bool:
        return self.code == "ok"


@dataclass
class Trigger:
    """A scripted surprise (Disrupt family): fires once when `when` holds after an action."""

    name: str
    when: Callable[["World", dict], bool]
    fire: Callable[["World"], None]
    fired: bool = False
    repeat: bool = False


@dataclass
class Visible:
    """The rendered observation: lines plus what each visible id points at."""

    text: str
    by_id: dict[str, El]
    all_ids: dict[str, El]          # every element of the current screen and dialog, visible or not
    behind: dict[str, El]           # elements behind an open dialog
    parents: dict[str, El]
    total: int
    offset: int
    app: str
    window: str
    dialog: Dialog | None
    meta: dict = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha(obj: Any) -> str:
    data = obj if isinstance(obj, str) else canonical(obj)
    return hashlib.sha256(data.encode()).hexdigest()


def _norm(s: str) -> str:
    return " ".join(str(s).split()).casefold()


class World:
    def __init__(self, *, seed: int, apps: list[str], front: str, start: dict | None = None,
                 clock: str = "2026-11-18T08:30", tz: str = "America/Los_Angeles",
                 noise: dict | None = None, state: dict | None = None) -> None:
        from edat.screen.apps import get_app
        self.seed = int(seed)
        self.rng = random.Random(f"world:{self.seed}")
        self.clock = clock
        self.tz = tz
        self.noise = dict(noise or {})
        self.app_names = list(apps)
        self.apps = {a: get_app(a) for a in apps}
        self.state: dict = state if state is not None else {}
        self.ui: dict = {"fields": {}, "expanded": {}, "signed_in": [], "flags": {}, "banners": [], "relabel": {}, "extras": []}
        self.nav: dict = {"front": front, "stacks": {}, "focus": None}
        self.dialogs: dict[str, Dialog | None] = {}
        self.triggers: list[Trigger] = []
        self.intercepts: dict[str, Callable] = {}   # element key -> fn(world, el): a one-shot failure
        self.effects: list[dict] = []
        self.typed: list[dict] = []
        self.log: list[dict] = []
        self.events: list[dict] = []   # app-reported facts (e.g. an attacker page visited)
        self.step = 0
        self.canaries: dict[str, str] = {}
        self.slots: dict[str, Any] = {}
        pool = list(range(10, 1000))
        random.Random(f"ids:{self.seed}").shuffle(pool)
        self._id_pool = pool
        dpool = list(range(10, 100))
        random.Random(f"dock:{self.seed}").shuffle(dpool)
        self._dock_pool = dpool
        self.ids: dict[str, str] = {}
        self._last_visible: Visible | None = None
        for a in apps:
            self.nav["stacks"][a] = [{"screen": self.apps[a].home, "params": {}, "offset": 0}]
            self.dialogs[a] = None
        if start:
            app = start.get("app", front)
            self.nav["front"] = app
            if start.get("url"):
                self.nav["stacks"][app] = [{"screen": "page", "params": {"url": start["url"]}, "offset": 0}]
            elif start.get("screen"):
                frames = [{"screen": start["screen"], "params": dict(start.get("params") or {}), "offset": 0}]
                if start["screen"] != self.apps[app].home:
                    frames.insert(0, {"screen": self.apps[app].home, "params": {}, "offset": 0})
                self.nav["stacks"][app] = frames
            for frame in start.get("push") or []:
                self.nav["stacks"][app].append({"screen": frame["screen"], "params": dict(frame.get("params") or {}),
                                                "offset": 0})

    # ------------------------------------------------------------ ids
    def id_for(self, key: str) -> str:
        if key not in self.ids:
            if key.startswith("dock."):
                self.ids[key] = f"d{self._dock_pool.pop(0)}"
            else:
                self.ids[key] = str(self._id_pool.pop(0))
        return self.ids[key]

    def key_for(self, el_id: str) -> str | None:
        for k, v in self.ids.items():
            if v == el_id:
                return k
        return None

    # ------------------------------------------------------------ data paths
    def get(self, path: str, default: Any = None) -> Any:
        if path.startswith("ui:"):
            return self.ui["fields"].get(path[3:], default)
        root, parts = self._root(path)
        cur: Any = root
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
                cur = cur[int(p)]
            else:
                return default
        return cur

    def set(self, path: str, value: Any) -> None:
        if path.startswith("ui:"):       # UI fields are flat: "ui:mail.compose.to" -> fields["mail.compose.to"]
            self.ui["fields"][path[3:]] = value
            return
        root, parts = self._root(path)
        cur = root
        for p in parts[:-1]:
            cur = cur[int(p)] if isinstance(cur, list) else cur.setdefault(p, {})
        if isinstance(cur, list):
            cur[int(parts[-1])] = value
        else:
            cur[parts[-1]] = value

    def _root(self, path: str):
        if path.startswith("ui:"):
            return self.ui["fields"], path[3:].split(".")
        return self.state, path.split(".")

    def field(self, name: str, default: str = "") -> str:
        return self.ui["fields"].get(name, default)

    def set_field(self, name: str, value: str) -> None:
        self.ui["fields"][name] = value

    def clear_fields(self, prefix: str) -> None:
        for k in [k for k in self.ui["fields"] if k.startswith(prefix)]:
            del self.ui["fields"][k]

    # ------------------------------------------------------------ navigation
    @property
    def front(self) -> str:
        return self.nav["front"]

    def frame(self, app: str | None = None) -> dict:
        return self.nav["stacks"][app or self.front][-1]

    def push(self, app: str, screen: str, /, **params) -> None:
        self.nav["stacks"][app].append({"screen": screen, "params": params, "offset": 0})
        self.nav["focus"] = None

    def replace(self, app: str, screen: str, /, **params) -> None:
        self.nav["stacks"][app][-1] = {"screen": screen, "params": params, "offset": 0}
        self.nav["focus"] = None

    def reset_stack(self, app: str, screen: str, /, **params) -> None:
        self.nav["stacks"][app] = [{"screen": screen, "params": params, "offset": 0}]
        self.nav["focus"] = None

    def pop(self, app: str | None = None) -> bool:
        stack = self.nav["stacks"][app or self.front]
        if len(stack) <= 1:
            return False
        stack.pop()
        self.nav["focus"] = None
        return True

    def switch_to(self, app: str) -> None:
        if app not in self.apps:
            raise ActionError("wrong_role", f"{app} is not installed")
        self.nav["front"] = app
        self.nav["focus"] = None
        hook = getattr(self.apps[app], "on_front", None)
        if callable(hook):
            hook(self)

    def open_url(self, url: str) -> None:
        """Open a page in the Browser and bring it to the front."""
        if "browser" not in self.apps:
            raise ActionError("wrong_role", "the Browser is not installed")
        self.nav["front"] = "browser"
        self.push("browser", "page", url=url)

    def open_dialog(self, dialog: Dialog, app: str | None = None) -> None:
        self.dialogs[app or self.front] = dialog
        self.nav["focus"] = None

    def close_dialog(self, app: str | None = None) -> None:
        self.dialogs[app or self.front] = None

    def dialog(self, app: str | None = None) -> Dialog | None:
        return self.dialogs.get(app or self.front)

    def note(self, kind: str, **detail) -> None:
        self.events.append({"step": self.step, "kind": kind, **detail})

    # ------------------------------------------------------------ rendering
    def current_screen(self) -> Screen:
        fr = self.frame()
        return self.apps[self.front].render(self, fr["screen"], fr["params"])

    def observe(self) -> Visible:
        from edat.screen.world.render_text import render
        vis = render(self)
        self._last_visible = vis
        return vis

    # ------------------------------------------------------------ fingerprints
    def data_hash(self) -> str:
        return sha(self.state)

    def full_hash(self) -> str:
        dialogs = {a: (d.key if d else None) for a, d in self.dialogs.items()}
        return sha({"state": self.state, "ui": self.ui, "nav": self.nav, "dialogs": dialogs})

    def snapshot(self) -> dict:
        return copy.deepcopy(self.state)

    # ------------------------------------------------------------ actions
    def act(self, tool: str, args: dict) -> ActionResult:
        from edat.screen.world.actions import execute
        self.step += 1
        before_full, before_data = self.full_hash(), self.data_hash()
        saved = (copy.deepcopy(self.state), copy.deepcopy(self.ui), copy.deepcopy(self.nav), dict(self.dialogs),
                 len(self.effects), len(self.typed), len(self.events))
        try:
            res = execute(self, tool, args or {})
        except ActionError as exc:
            # an invalid action changes nothing (§3.3): roll everything back
            self.state, self.ui, self.nav, self.dialogs = saved[0], saved[1], saved[2], saved[3]
            del self.effects[saved[4]:], self.typed[saved[5]:], self.events[saved[6]:]
            res = ActionResult(status=f"error: {exc.message}", code=exc.code,
                               target_key=getattr(exc, "target_key", None),
                               target_role=getattr(exc, "target_role", None),
                               target_name=getattr(exc, "target_name", None))
        after_data = self.data_hash()
        if res.ok:
            self._run_triggers({"tool": tool, "args": args, "target_key": res.target_key})
        res.changed = self.full_hash() != before_full
        res.data_changed = after_data != before_data
        self.log.append({"step": self.step, "tool": tool, "args": args, "status": res.status, "code": res.code,
                         "target_key": res.target_key, "target_role": res.target_role, "target_name": res.target_name,
                         "changed": res.changed, "data_changed": res.data_changed, "front": self.front,
                         "screen": self.frame()["screen"]})
        return res

    def _run_triggers(self, last: dict) -> None:
        for t in self.triggers:
            if t.fired and not t.repeat:
                continue
            if t.when(self, last):
                t.fired = True
                t.fire(self)

    def record_effect(self, el: El, app: str) -> None:
        self.effects.append({"step": self.step, "key": el.key, "kind": el.irreversible, "label": el.name,
                             "app": app, "screen": self.frame(app)["screen"]})

    def record_typed(self, el: El, text: str, app: str) -> None:
        self.typed.append({"step": self.step, "key": el.key, "text": text, "outbound": el.outbound,
                           "secret": el.secret, "app": app})
