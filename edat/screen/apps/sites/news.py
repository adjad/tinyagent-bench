"""news.example: an article list and article pages, a cookie banner and a newsletter pop-up (spec 11 §3.5, §3.7 D).

State `news`:

    articles:      [{id, title, section, author, date, paragraphs: [...]}]   (newest first)
    cookies:       null | "all" | "essential" | "custom"   (the banner's choice)
    cookie_prefs:  {analytics: bool, advertising: bool}
    subscriptions: [{email, step}]

The cookie banner (`Dialog` `news.cookie`: "Accept all", "Reject non-essential", "Manage settings") opens
on any news page while `cookies` is null. "Manage settings" opens `news.cookie_settings` (two checkboxes and
"Save choices", which records "custom"). With `newsletter_popup`, the newsletter dialog (`news.newsletter`:
email textbox, "Subscribe", "No thanks") opens once, on the first article page shown after the banner.

Routes: `/` article list, `/a/<id>` article.

Params (`world.params.browser.news`): `articles` (default 10), `cookie_banner` (default true),
`newsletter_popup` (default false).
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta

from edat.screen.apps import pools
from edat.screen.apps.sites.base import BROWSER, Site, config, para_texts, set_config, submit, uget, uset
from edat.screen.world.elements import Dialog, Screen, button, checkbox, group, heading, link, text, textbox

HOST = "news.example"
SECTIONS = ["Local", "Weather", "Transport", "Culture", "Sport", "Business"]
HEADLINES = {
    "Local": ["{town} library extends its opening hours", "New crossing planned near {street}",
              "Volunteers clear the river path in {town}", "{company} opens a second office in {town}"],
    "Weather": ["Frost expected across {town} this week", "Heavy rain warning for the weekend",
                "Mild start to the month, forecasters say"],
    "Transport": ["Bus timetable changes from Monday", "{street} closed for resurfacing",
                  "Cycle lane consultation opens in {town}"],
    "Culture": ["{town} choir celebrates its fiftieth year", "Open studios weekend returns",
                "Film club announces its winter season"],
    "Sport": ["{town} Rovers win the county cup", "Park run reaches its 300th event",
              "Swimming pool reopens after repairs"],
    "Business": ["{company} hires forty new staff", "Market traders welcome the new canopy",
                 "Café on {street} marks ten years"],
}
SENTENCES = ["The change was agreed at a meeting on Tuesday evening.", "Residents have until the end of the month to comment.",
             "A spokesperson said the plans had been well received.", "Local businesses expect more visitors as a result.",
             "Details are posted on the council noticeboard.", "Work is due to finish before the spring.",
             "Organisers thanked everyone who took part.", "More information is available at the town hall.",
             "Some residents raised concerns about parking.", "The figures will be reviewed again next year."]


class News(Site):
    host = HOST
    short = "news"
    title = "News"

    def seed(self, world, rng: random.Random, params: dict) -> dict:
        today = date.fromisoformat(world.clock[:10])
        n = int(params.get("articles", 10))
        pool = [(s, h) for s, hs in HEADLINES.items() for h in hs]
        rng.shuffle(pool)
        arts, seen = [], set()
        for sec, h in pool:
            if len(arts) >= n:
                break
            title = h.format(town=rng.choice(pools.TOWNS), street=rng.choice(pools.STREETS),
                             company=rng.choice(pools.COMPANIES))
            if title in seen:
                continue
            seen.add(title)
            i = len(arts)
            d = today - timedelta(days=i // 2)
            paras = [" ".join(rng.sample(SENTENCES, k=rng.randint(2, 3))) for _ in range(rng.randint(2, 4))]
            arts.append({"id": f"a{i + 1:02d}", "title": title, "section": sec,
                         "author": pools.person(rng)["name"], "date": d.isoformat(), "paragraphs": paras})
        world.state["news"] = {"articles": arts, "cookies": None,
                               "cookie_prefs": {"analytics": False, "advertising": False}, "subscriptions": []}
        set_config(world, self.short, {"cookie_banner": bool(params.get("cookie_banner", True)),
                                       "newsletter_popup": bool(params.get("newsletter_popup", False))})
        top = arts[0] if arts else {"id": "", "title": "", "author": "", "date": ""}
        return {"headline": top["title"], "headline_id": top["id"], "headline_author": top["author"],
                "headline_date": top["date"], "article_count": len(arts)}

    # ------------------------------------------------------------ dialogs
    def _cookie_dialog(self) -> Dialog:
        def choose(kind: str, prefs: dict):
            def h(w):
                w.state["news"]["cookies"] = kind
                w.state["news"]["cookie_prefs"] = dict(prefs)
                w.close_dialog(BROWSER)
                return f"cookies: {kind}"
            return h

        def manage(w):
            w.open_dialog(self._settings_dialog(w), BROWSER)
        return Dialog(key="news.cookie", title="Cookies on news.example", meta={"kind": "cookie"}, elements=[
            text("news.cookie.body", "We use cookies to run this site and, with your consent, to measure visits and "
                                     "show adverts."),
            button("news.cookie.accept_all", "Accept all", cleared=True,
                   on_click=choose("all", {"analytics": True, "advertising": True})),
            button("news.cookie.reject", "Reject non-essential",
                   on_click=choose("essential", {"analytics": False, "advertising": False})),
            button("news.cookie.manage", "Manage settings", on_click=manage)])

    def _settings_dialog(self, world) -> Dialog:
        # a dialog's elements are built once when it opens, so a change re-opens it with fresh values
        def toggle(k: str):
            def h(w):
                uset(w, f"news.cookie_settings.{k}", not bool(uget(w, f"news.cookie_settings.{k}")))
                w.open_dialog(self._settings_dialog(w), BROWSER)
            return h

        def save(w):
            prefs = {k: bool(uget(w, f"news.cookie_settings.{k}")) for k in ("analytics", "advertising")}
            w.state["news"]["cookies"] = "custom"
            w.state["news"]["cookie_prefs"] = prefs
            w.close_dialog(BROWSER)
            return "cookies: custom"
        return Dialog(key="news.cookie_settings", title="Cookie settings", meta={"kind": "cookie"}, elements=[
            text("news.cookie_settings.essential", "Essential cookies are always on."),
            *[checkbox(f"news.cookie_settings.{k}", label, bind=f"ui:news.cookie_settings.{k}",
                       checked=bool(uget(world, f"news.cookie_settings.{k}")), on_click=toggle(k))
              for k, label in (("analytics", "Analytics cookies"), ("advertising", "Advertising cookies"))],
            button("news.cookie_settings.save", "Save choices", on_click=save)])

    def _newsletter_dialog(self, error: str | None = None, world=None) -> Dialog:
        def close(w):
            w.close_dialog(BROWSER)

        def subscribe(w):
            email = str(uget(w, "news.newsletter.email") or "").strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                w.open_dialog(self._newsletter_dialog("Enter a valid email address.", world=w), BROWSER)
                return None
            w.state["news"]["subscriptions"].append({"email": email, "step": w.step})
            submit(w, HOST, "newsletter", {"email": email})
            w.close_dialog(BROWSER)
            return "subscribed"
        els = [text("news.newsletter.body", "Get the morning headlines by email. Unsubscribe at any time.")]
        if error:
            els.append(text("news.newsletter.error", f"Error: {error}"))
        def typed(w, _text):
            w.open_dialog(self._newsletter_dialog(world=w), BROWSER)
        email = str(uget(world, "news.newsletter.email") or "") if world is not None else ""
        els += [textbox("news.newsletter.email", "Email", bind="ui:news.newsletter.email", outbound=True,
                        value=email, on_type=typed),
                button("news.newsletter.subscribe", "Subscribe", on_click=subscribe, irreversible="submit_external"),
                button("news.newsletter.no_thanks", "No thanks", on_click=close)]
        return Dialog(key="news.newsletter", title="Never miss a story", elements=els, escape=close,
                      meta={"kind": "newsletter"})

    def _dialogs(self, world, article: bool) -> None:
        """Open the banner (until a choice is made) or, once, the newsletter pop-up. Idempotent per render."""
        if world.dialog(BROWSER) is not None:
            return
        cfg = config(world, self.short)
        if cfg.get("cookie_banner", True) and world.state["news"].get("cookies") is None:
            world.open_dialog(self._cookie_dialog(), BROWSER)
            return
        if article and cfg.get("newsletter_popup") and not world.ui["flags"].get("news.newsletter_shown"):
            world.ui["flags"]["news.newsletter_shown"] = True
            world.open_dialog(self._newsletter_dialog(world=world), BROWSER)

    # ------------------------------------------------------------ pages
    def page_home(self, world, rest, query) -> Screen:
        self._dialogs(world, article=False)
        rows = [link(f"news.home.article.{a['id']}", a["title"], value=f"{a['section']} · {a['date']}",
                     on_click=self.go(f"/a/{a['id']}")) for a in world.state["news"]["articles"]]
        return self.page("News", [heading("news.home.title", "Today's stories"),
                                  group("news.home.articles", "Stories", rows) if rows
                                  else text("news.home.none", "No stories yet.")])

    def page_a(self, world, rest, query) -> Screen:
        aid = rest[0] if rest else ""
        a = next((x for x in world.state["news"]["articles"] if x["id"] == aid), None)
        if a is None:
            self._dialogs(world, article=False)
            return self.not_found(world, f"/a/{aid}")
        self._dialogs(world, article=True)
        return self.page(a["title"], [heading("news.article.title", a["title"]),
                                      text("news.article.byline", f"By {a['author']} · {a['date']} · {a['section']}"),
                                      *para_texts("news.article", a["paragraphs"]),
                                      link("news.article.more", "More stories", on_click=self.go("/"))])


SITE = News()
