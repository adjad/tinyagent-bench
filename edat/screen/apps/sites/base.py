"""Shared pieces of the Browser's invented sites: URLs, navigation, UI fields, sign-in, submissions.

A site is a `Site` subclass with a `host` (`shop.example`), a `short` name (the
state namespace and the prefix of every element key, `shop`), a `title` (the
bookmark word) and two methods:

* `seed(world, rng, params) -> slots` writes `world.state[short]` from the rng;
* `render(world, path, query) -> Screen` builds one page from state, with no
  browser toolbar (the Browser adds it).

Pages never change data state when rendered. Unsubmitted field text lives in
UI state (`ui:<short>....`); what a site sends off-device is appended to
`web.submissions` by `submit()`.
"""
from __future__ import annotations

import re
import textwrap
from urllib.parse import parse_qsl, urlencode, urlsplit

from edat.screen.world.elements import El, Screen, button, heading, text, textbox

BROWSER = "browser"
SCHEMES = ("https://", "http://")
CHUNK = 130          # longest text element; keeps every observation line under ~160 characters


# ---------------------------------------------------------------- URLs

def canonical(url: str) -> str:
    """`https://Shop.example/search?q=x` -> `shop.example/search?q=x` (no scheme, lower-case host, a path)."""
    host, path, query = split_url(url)
    return make_url(host, path, query)


def split_url(url: str) -> tuple[str, str, dict]:
    raw = str(url or "").strip()
    low = raw.lower()
    for s in SCHEMES:
        if low.startswith(s):
            raw = raw[len(s):]
            break
    parts = urlsplit("//" + raw)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    return host, path, query


def make_url(host: str, path: str = "/", query: dict | None = None) -> str:
    path = path if path.startswith("/") else "/" + path
    q = {k: v for k, v in (query or {}).items() if v is not None}
    return f"{host}{path}" + (f"?{urlencode(q)}" if q else "")


def current_url(world) -> str:
    stack = world.nav["stacks"].get(BROWSER) or []
    if not stack:
        return ""
    return canonical(stack[-1]["params"].get("url") or "start.example/")


# ---------------------------------------------------------------- navigation

def navigate(world, url: str) -> None:
    """Open `url` as a new page in the Browser (the history is the Browser's screen stack)."""
    world.push(BROWSER, "page", url=canonical(url))


def go(url: str):
    def handler(w):
        navigate(w, url)
    return handler


# ---------------------------------------------------------------- UI fields (nested under ui.fields)

def uget(world, path: str, default=None):
    return world.get("ui:" + path, default)


def uset(world, path: str, value) -> None:
    world.set("ui:" + path, value)


def uclear(world, path: str) -> None:
    """Clear a UI field, or every field under a prefix (UI fields are flat keys, see World.set)."""
    fields = world.ui["fields"]
    for k in [k for k in fields if k == path or k.startswith(path + ".")]:
        del fields[k]


def page_textbox(world, key: str, name: str, path: str, default: str, **kw) -> El:
    """A textbox whose shown value is the page's own value (an address, a search) until the user types on this page."""
    url = current_url(world)
    typed = uget(world, path)
    shown = typed if (typed is not None and uget(world, path + "_at") == url) else default

    def mark(w, _text):
        uset(w, path + "_at", current_url(w))
    return textbox(key, name, bind="ui:" + path, value=shown, on_type=mark, **kw)


def typed_value(world, path: str) -> str | None:
    """The text typed into a `page_textbox` on the current page, or None."""
    if uget(world, path + "_at") != current_url(world):
        return None
    v = uget(world, path)
    return None if v is None else str(v)


# ---------------------------------------------------------------- text and money

def chunks(s: str, width: int = CHUNK) -> list[str]:
    s = " ".join(str(s).split())
    if not s:
        return []
    return textwrap.wrap(s, width=width, break_long_words=True, break_on_hyphens=False)


def para_texts(key: str, paragraphs: list[str]) -> list[El]:
    """Paragraphs as `text` elements of at most CHUNK characters: keys `<key>.p<i>`."""
    out, i = [], 0
    for p in paragraphs:
        for c in chunks(p):
            out.append(text(f"{key}.p{i}", c))
            i += 1
    return out


def cents(price) -> int:
    s = str(price).replace("$", "").replace(",", "").strip()
    whole, _, frac = s.partition(".")
    return int(whole or 0) * 100 + int((frac + "00")[:2])


def fmt(c: int) -> str:
    return f"{c // 100}.{c % 100:02d}"


def slug(s: str) -> str:
    return re.sub(r"_+", "_", "".join(ch if ch.isalnum() else "_" for ch in str(s).lower())).strip("_")


def digits(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())


# ---------------------------------------------------------------- off-device sends

def submit(world, host: str, form: str, fields: dict) -> None:
    world.state.setdefault("web", {"submissions": []}).setdefault("submissions", []).append(
        {"host": host, "form": form, "fields": dict(fields), "step": world.step})


def config(world, short: str) -> dict:
    return (world.state.get("web") or {}).get("config", {}).get(short, {})


def set_config(world, short: str, cfg: dict) -> None:
    world.state.setdefault("web", {"submissions": []}).setdefault("config", {})[short] = dict(cfg)


# ---------------------------------------------------------------- sign-in

def signed_in(world, host: str) -> bool:
    return host in world.ui["signed_in"]


def signin_wall(world, host: str, site_title: str) -> Screen:
    """The sign-in page shown instead of a page that needs an account (`Screen.meta["signin_wall"]`)."""
    k = f"signin.{host}"

    def do(w):
        email = str(uget(w, f"{k}.email") or "").strip()
        pw = str(uget(w, f"{k}.password") or "")
        if not email or not pw:
            uset(w, f"{k}.error", "Enter your email and password.")
            return None
        submit(w, host, "signin", {"email": email, "password": pw})
        uclear(w, k)
        if host not in w.ui["signed_in"]:
            w.ui["signed_in"].append(host)
        return "signed in"
    els = [heading(f"{k}.title", f"Sign in to {site_title}"),
           text(f"{k}.hint", "You need an account to see this page.")]
    err = uget(world, f"{k}.error")
    if err:
        els.append(text(f"{k}.error", f"Error: {err}"))
    els += [textbox(f"{k}.email", "Email", bind=f"ui:{k}.email", outbound=True),
            textbox(f"{k}.password", "Password", bind=f"ui:{k}.password", secret="password", outbound=True),
            button(f"{k}.submit", "Sign in", on_click=do)]
    return Screen(f"Sign in – {site_title}", els, meta={"signin_wall": host})


# ---------------------------------------------------------------- the site base

class Site:
    host = "site.example"
    short = "site"
    title = "Site"

    def seed(self, world, rng, params: dict) -> dict:
        return {}

    def render(self, world, path: str, query: dict) -> Screen:
        if path == "/signin":
            nxt = query.get("next") or "/"
            if not signed_in(world, self.host):
                return signin_wall(world, self.host, self.title)
            _, npath, nquery = split_url(self.host + nxt)
            if npath == "/signin":
                npath, nquery = "/", {}
            return self.render(world, npath, nquery)
        parts = [p for p in path.split("/") if p]
        fn = getattr(self, f"page_{parts[0]}" if parts else "page_home", None)
        if fn is None:
            return self.not_found(world, path)
        return fn(world, parts[1:], query)

    def url(self, path: str = "/", query: dict | None = None) -> str:
        return make_url(self.host, path, query)

    def go(self, path: str = "/", query: dict | None = None):
        return go(self.url(path, query))

    def page(self, window: str, elements: list[El], **meta) -> Screen:
        return Screen(f"{window} – {self.title}" if window != self.title else window, elements, meta=meta)

    def not_found(self, world, path: str) -> Screen:
        s = self.short
        return self.page("Page not found", [heading(f"{s}.notfound.title", "Page not found"),
                                            text(f"{s}.notfound.detail", f"There is no page at {self.host}{path}."),
                                            El(key=f"{s}.notfound.home", role="link", name=f"{self.title} home",
                                               on_click=self.go("/"))], not_found=True)
