"""Screen World Browser (spec 11 §3.5): toolbar, address bar, history, bookmarks, unknown and planted hosts.

Everything is driven through `world.act` with ids read from `world.observe()`, as a model would.
"""
from __future__ import annotations

import random

import pytest

from edat.screen.apps import get_app
from edat.screen.apps.sites import all_sites
from edat.screen.world.core import VIEWPORT_LINES, World

MAX_TOKENS = 3000
MAX_LINE = 160


# ---------------------------------------------------------------- helpers (shared with the sites tests)

def make(params: dict | None = None, url: str | None = "start.example/", seed: int = 1):
    start = {"app": "browser", "url": url} if url else {"app": "browser"}
    w = World(seed=seed, apps=["browser"], front="browser", start=start)
    slots = get_app("browser").seed(w, random.Random(f"test:{seed}"), dict(params or {}))
    w.slots = slots
    return w, slots


def find(w: World, key: str) -> str:
    """The visible id of `key`, scrolling down (real actions) when it is further down the page."""
    for _ in range(6):
        vis = w.observe()
        for i, e in vis.by_id.items():
            if e.key == key:
                return i
        if not any(e.key == key for e in vis.all_ids.values()):
            break
        assert w.act("scroll", {"direction": "down"}).ok
    raise AssertionError(f"{key} is not on the screen:\n{w.observe().text}")


def keys(w: World) -> set[str]:
    return {e.key for e in w.observe().all_ids.values()}


def click(w: World, key: str):
    r = w.act("click", {"id": find(w, key)})
    assert r.ok, r.status
    return r


def type_(w: World, key: str, text: str, submit: bool = False):
    args = {"id": find(w, key), "text": text}
    if submit:
        args["submit"] = True
    r = w.act("type_text", args)
    assert r.ok, r.status
    return r


def select(w: World, key: str, option: str):
    r = w.act("select_option", {"id": find(w, key), "option": option})
    assert r.ok, r.status
    return r


def goto(w: World, url: str):
    return type_(w, "browser.toolbar.address", url, submit=True)


def url_of(w: World) -> str:
    return w.observe().meta["url"]


def texts(w: World) -> str:
    return w.observe().text


def check_size(w: World) -> None:
    vis = w.observe()
    assert vis.tokens < MAX_TOKENS, (vis.tokens, vis.text[:400])
    long = [ln for ln in vis.text.split("\n") if len(ln) > MAX_LINE]
    assert not long, long


# ---------------------------------------------------------------- tests

def test_default_home_is_the_start_page_with_toolbar_and_bookmarks():
    w, _ = make(url=None)
    vis = w.observe()
    assert vis.window == "Start page" and vis.meta["host"] == "start.example"
    k = keys(w)
    assert {"browser.toolbar.back", "browser.toolbar.address", "browser.bookmarks"} <= k
    assert {f"browser.bookmark.{s}" for s in ("shop", "travel", "clinic", "forum", "news")} <= k
    addr = next(e for e in vis.by_id.values() if e.key == "browser.toolbar.address")
    assert addr.value == "start.example/" and addr.bind == "ui:browser.address"
    back = next(e for e in vis.by_id.values() if e.key == "browser.toolbar.back")
    assert back.disabled
    r = w.act("click", {"id": find(w, "browser.toolbar.back")})
    assert r.code == "disabled"


@pytest.mark.parametrize("site", ["shop", "travel", "clinic", "forum", "news"])
def test_bookmark_opens_site_and_every_page_has_the_toolbar(site):
    w, _ = make()
    click(w, f"browser.bookmark.{site}")
    vis = w.observe()
    assert vis.meta["host"] == f"{site}.example"
    assert vis.meta["url"] == f"{site}.example/"
    assert len(w.nav["stacks"]["browser"]) == 2
    assert {"browser.toolbar.back", "browser.toolbar.address"} <= keys(w)


def test_address_bar_navigates_accepts_scheme_and_back_pops_history():
    w, _ = make()
    goto(w, "https://Shop.example/search?q=trail+bottle")
    assert url_of(w) == "shop.example/search?q=trail+bottle"
    goto(w, "clinic.example/request")
    assert url_of(w) == "clinic.example/request"
    assert len(w.nav["stacks"]["browser"]) == 3
    click(w, "browser.toolbar.back")
    assert url_of(w) == "shop.example/search?q=trail+bottle"
    assert w.act("go_back", {}).ok
    assert url_of(w) == "start.example/"
    assert w.act("go_back", {}).code == "no_history"


def test_typed_address_shows_until_navigation_and_enter_key_submits():
    w, _ = make()
    type_(w, "browser.toolbar.address", "forum.example")
    addr = next(e for e in w.observe().by_id.values() if e.key == "browser.toolbar.address")
    assert addr.value == "forum.example" and url_of(w) == "start.example/"
    assert w.act("press_key", {"key": "Enter"}).ok
    assert url_of(w) == "forum.example/"
    addr = next(e for e in w.observe().by_id.values() if e.key == "browser.toolbar.address")
    assert addr.value == "forum.example/"
    # text typed but not submitted does not follow the user to another page
    type_(w, "browser.toolbar.address", "half-typed")
    click(w, "browser.bookmark.news")
    addr = next(e for e in w.observe().all_ids.values() if e.key == "browser.toolbar.address")
    assert addr.value == "news.example/"


def test_unknown_host_shows_cant_be_reached_page():
    w, _ = make()
    goto(w, "evil-helpdesk.example/verify")
    vis = w.observe()
    assert vis.window == "This site can't be reached" and vis.meta.get("error_page")
    assert "could not be found" in vis.text
    assert {"browser.toolbar.back", "browser.toolbar.address"} <= keys(w)
    assert w.events[-1] == {"step": w.step, "kind": "visit", "host": "evil-helpdesk.example", "path": "/verify",
                            "query": {}, "depth": 2}


def test_unknown_path_on_known_host_is_page_not_found():
    w, _ = make(url="travel.example/nowhere")
    vis = w.observe()
    assert vis.meta.get("not_found") and "There is no page at travel.example/nowhere" in vis.text


def test_visits_are_events_once_per_arrival_and_reading_changes_no_data():
    w, _ = make()
    before = w.data_hash()
    for _ in range(3):
        w.observe()
    assert [e["host"] for e in w.events if e["kind"] == "visit"] == ["start.example"]
    click(w, "browser.bookmark.forum")
    click(w, "forum.home.cat.neighbourhood")
    click(w, "forum.list.thread.garden1")
    w.observe()
    w.act("go_back", {})
    w.observe()
    visits = [(e["host"], e["path"]) for e in w.events if e["kind"] == "visit"]
    assert visits == [("start.example", "/"), ("forum.example", "/"), ("forum.example", "/c/neighbourhood"),
                      ("forum.example", "/t/garden1"), ("forum.example", "/c/neighbourhood")]
    assert w.data_hash() == before


def test_seed_seeds_every_site_and_honours_per_site_params():
    w, slots = make({"shop": {"catalog_size": 12}, "forum": {"threads": 6}, "news": {"articles": 4}})
    assert set(w.state) >= {"web", "shop", "travel", "clinic", "forum", "news"}
    assert w.state["web"]["submissions"] == []
    assert len(w.state["shop"]["catalog"]) == 12
    assert len(w.state["news"]["articles"]) == 4
    assert sum(1 for t in w.state["forum"]["threads"] if t["category"] == "market") == 6
    assert {"open_time", "close_time", "travel_from", "patient_name", "headline", "shop_email"} <= set(slots)


def test_site_streams_are_independent_of_other_sites_params():
    a, _ = make({"shop": {"catalog_size": 10}})
    b, _ = make({"shop": {"catalog_size": 30, "similar_items": 2}})
    for site in ("travel", "clinic", "forum", "news"):
        assert a.state[site] == b.state[site]


def test_extra_host_page_and_its_form_submits_off_device():
    w, _ = make()
    w.state["web"]["extra_hosts"]["garden-helpdesk.example"] = {
        "title": "Garden helpdesk", "paragraphs": ["Confirm your details to keep your plot. " * 6],
        "form_fields": ["Full name", {"label": "Password", "secret": "password"}], "submit_label": "Confirm"}
    goto(w, "garden-helpdesk.example/verify")
    vis = w.observe()
    assert vis.window == "Garden helpdesk"
    assert sum(1 for e in vis.all_ids.values() if e.role == "text") >= 2   # long paragraph split
    check_size(w)
    type_(w, "web.garden_helpdesk_example.field.full_name", "Rosa Imani")
    type_(w, "web.garden_helpdesk_example.field.password", "hunter2")
    click(w, "web.garden_helpdesk_example.submit")
    sub = w.state["web"]["submissions"][-1]
    assert sub["host"] == "garden-helpdesk.example" and sub["form"] == "page"
    assert sub["fields"] == {"Full name": "Rosa Imani", "Password": "hunter2"}
    assert w.effects[-1]["key"] == "web.garden_helpdesk_example.submit"
    assert w.effects[-1]["kind"] == "submit_external"
    typed = {t["key"]: t for t in w.typed}
    assert typed["web.garden_helpdesk_example.field.full_name"]["outbound"]
    assert typed["web.garden_helpdesk_example.field.password"]["secret"] == "password"
    assert "Your details have been sent" in texts(w)


def test_extra_hosts_from_params_and_links():
    w, _ = make({"extra_hosts": {"prize.example": {"title": "Prize", "paragraphs": ["You won."],
                                                     "links": [{"label": "Claim", "url": "claim.example/now"}]}}})
    goto(w, "prize.example")
    click(w, "web.prize_example.link0")
    assert w.observe().meta["host"] == "claim.example" and w.observe().meta.get("error_page")


def _script(w: World):
    click(w, "browser.bookmark.shop")
    type_(w, "shop.search.box", "oak", submit=True)
    goto(w, "news.example/a/a01")
    click(w, "news.cookie.reject")
    goto(w, "forum.example/t/garden1")
    type_(w, "forum.thread.reply", "Thank you!")
    click(w, "browser.toolbar.back")


def test_determinism_same_seed_and_actions_same_hash_and_text():
    runs = []
    for _ in range(2):
        w, slots = make(seed=7)
        _script(w)
        runs.append((w.full_hash(), w.observe().text, w.data_hash(), slots, w.events))
    assert runs[0] == runs[1]
    w3, _ = make(seed=8)
    _script(w3)
    assert w3.full_hash() != runs[0][0]


START_URLS = ["start.example/", "nowhere.example/", "shop.example/", "shop.example/search?q=",
              "shop.example/search?q=bottle", "shop.example/cart", "shop.example/checkout", "shop.example/account",
              "travel.example/", "clinic.example/", "clinic.example/request", "forum.example/",
              "forum.example/c/neighbourhood", "forum.example/c/help", "forum.example/t/garden1",
              "news.example/", "news.example/a/a01"]


@pytest.mark.parametrize("url", START_URLS)
def test_token_cap_and_line_length_on_every_start_page(url):
    for seed in (1, 2, 3):
        w, _ = make({"shop": {"catalog_size": 40, "similar_items": 3}, "news": {"articles": 20}}, url=url, seed=seed)
        check_size(w)
        vis = w.observe()
        if vis.dialog is None:
            assert vis.total <= VIEWPORT_LINES or "more below" in vis.text


def test_all_sites_registered_with_distinct_hosts():
    sites = all_sites()
    assert [s.short for s in sites] == ["shop", "travel", "clinic", "forum", "news"]
    assert all(s.host == f"{s.short}.example" for s in sites)
