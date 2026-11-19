"""Browser: an address field, Back, bookmarks and the five invented sites (spec 11 §3.5).

One screen, `page(url)`. The Browser's history is its screen stack: every link pushes a page
(`world.push("browser", "page", url=...)`) and Back pops it. A page is the toolbar

    [..] button "Back"                        browser.toolbar.back (disabled on the first page)
    [..] textbox "Address" value "shop.example/search?q=bottle"   browser.toolbar.address
    [..] group "Bookmarks"                    browser.bookmarks
      [..] link "Shop" ... link "News"        browser.bookmark.<site>

followed by the site's own elements. Return in the address field opens the typed `host/path?query`
(`https://` is accepted); an unknown host shows "This site can't be reached".

Hosts: `start.example` (the start page, the default), the five sites in `sites/`, and any host in
`world.state["web"]["extra_hosts"]` (`{host: {title, paragraphs, form_fields, links, submit_label}}`), which
renders a generic page whose form appends to `web.submissions`. Scenarios plant attacker sites there.

Every page shown is noted as a world event `{kind: visit, host, path, query}` (not data state), once per
arrival. Data state: `web = {submissions: [{host, form, fields, step}], extra_hosts, config}` plus each
site's own namespace. Seed params: `world.params.browser = {shop: {...}, travel: {...}, clinic: {...},
forum: {...}, news: {...}, extra_hosts: {...}}`.
"""
from __future__ import annotations

import copy
import random

from edat.screen.apps import App
from edat.screen.apps.sites import all_sites, by_host
from edat.screen.apps.sites.base import (BROWSER, canonical, current_url, go, navigate, page_textbox,
                                         para_texts, slug, split_url, submit, typed_value, uclear, uget)
from edat.screen.world.elements import El, Screen, button, group, heading, link, text, textbox

START_HOST = "start.example"
START_URL = "start.example/"


class Browser(App):
    name = "browser"
    title = "Browser"
    home = "page"

    # ------------------------------------------------------------ state
    def seed(self, world, rng: random.Random, params: dict) -> dict:
        world.state["web"] = {"submissions": [], "extra_hosts": copy.deepcopy(params.get("extra_hosts") or {}),
                              "config": {}}
        sites = all_sites()
        seeds = {s.short: rng.getrandbits(64) for s in sites}   # one stream per site: params of one never shift another
        slots: dict = {}
        for s in sites:
            slots.update(s.seed(world, random.Random(seeds[s.short]), dict(params.get(s.short) or {})))
        return slots

    # ------------------------------------------------------------ the toolbar
    def _toolbar(self, world, url: str) -> list[El]:
        depth = len(world.nav["stacks"].get(BROWSER) or [])

        def back(w):
            w.pop(BROWSER)

        def open_typed(w):
            typed = typed_value(w, "browser.address")
            uclear(w, "browser")
            if typed is None or not typed.strip() or canonical(typed) == current_url(w):
                return None
            navigate(w, typed.strip())
            return None
        marks = [link(f"browser.bookmark.{s.short}", s.title, on_click=go(f"{s.host}/")) for s in all_sites()]
        return [button("browser.toolbar.back", "Back", on_click=back, disabled=depth <= 1),
                page_textbox(world, "browser.toolbar.address", "Address", "browser.address", url,
                             on_submit=open_typed),
                group("browser.bookmarks", "Bookmarks", marks)]

    # ------------------------------------------------------------ the page
    def screen_page(self, world, url: str | None = None, **_ignored) -> Screen:
        url = canonical(url or START_URL)
        host, path, query = split_url(url)
        self._note_visit(world, host, path, query)
        site = by_host().get(host)
        extra = (world.state.get("web") or {}).get("extra_hosts") or {}
        if site is not None:
            page = site.render(world, path, query)
        elif host == START_HOST:
            page = self._start(world)
        elif host in extra:
            page = self._extra(world, host, extra[host])
        else:
            page = self._unreachable(host)
        return Screen(page.window, self._toolbar(world, url) + page.elements,
                      meta={"url": url, "host": host, "path": path, **page.meta})

    def _note_visit(self, world, host: str, path: str, query: dict) -> None:
        last = next((e for e in reversed(world.events) if e.get("kind") == "visit"), None)
        here = {"host": host, "path": path, "query": dict(query), "depth": len(world.nav["stacks"].get(BROWSER) or [])}
        if last is not None and all(last.get(k) == v for k, v in here.items()):
            return
        world.note("visit", **here)

    def _start(self, world) -> Screen:
        rows = [link(f"start.home.site.{s.short}", f"{s.title} ({s.host})", on_click=go(f"{s.host}/"))
                for s in all_sites()]
        return Screen("Start page", [heading("start.home.title", "Start page"),
                                     text("start.home.hint", "Type an address above or choose a site."),
                                     group("start.home.sites", "Sites", rows)])

    def _unreachable(self, host: str) -> Screen:
        return Screen("This site can't be reached", [
            heading("browser.error.title", "This site can't be reached"),
            text("browser.error.detail", f"The address {host or '(empty)'} could not be found. Check the spelling.")],
            meta={"error_page": True})

    def _extra(self, world, host: str, spec: dict) -> Screen:
        """A generic page for a planted host: paragraphs, optional links and an optional outbound form."""
        hk = f"web.{slug(host)}"
        title = str(spec.get("title") or host)
        els: list[El] = [heading(f"{hk}.title", title), *para_texts(hk, list(spec.get("paragraphs") or []))]
        for i, ln in enumerate(spec.get("links") or []):
            els.append(link(f"{hk}.link{i}", str(ln.get("label") or ln.get("url")), on_click=go(str(ln["url"]))))
        fields = []
        for f in spec.get("form_fields") or []:
            f = {"label": f} if isinstance(f, str) else dict(f)
            fields.append({"label": str(f["label"]), "slug": slug(f.get("name") or f["label"]),
                           "secret": f.get("secret")})
        if fields:
            sent = [s for s in world.state["web"]["submissions"] if s["host"] == host]

            def send(w):
                vals = {f["label"]: str(uget(w, f"{hk}.{f['slug']}") or "") for f in fields}
                submit(w, host, "page", vals)
                uclear(w, hk)
                return "form sent"
            if sent:
                els.append(text(f"{hk}.sent", "Thank you. Your details have been sent."))
            els += [textbox(f"{hk}.field.{f['slug']}", f["label"], bind=f"ui:{hk}.{f['slug']}", outbound=True,
                            secret=f["secret"]) for f in fields]
            els.append(button(f"{hk}.submit", str(spec.get("submit_label") or "Submit"), on_click=send,
                              irreversible="submit_external"))
        return Screen(title, els)


APP = Browser()
