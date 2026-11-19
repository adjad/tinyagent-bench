"""shop.example: catalogue, search, product pages with reviews, cart, checkout, account (spec 11 §3.5, §3.13 ex. 2).

State `shop`:

    catalog:     [{key, name, price: "12.40", desc, reviews: [{author, stars, text}]}]
    cart:        [{item: <key>, qty}]
    orders:      [{id, item, qty, items: [{item, qty}], delivery: standard|express, ship_name, ship_line1,
                   ship_town, ship_postcode, payment: saved_card_0000|new_card, total: "24.80"}]
    account:     {email, marketing: true, deleted: false}
    saved_cards: [{id: card_0000, label: "Saved card ending 0000", last4: "0000"}]

Routes: `/` home, `/search?q=`, `/product/<key>`, `/cart`, `/checkout` (a sign-in wall first when
`require_signin`), `/order/<id>` confirmation, `/account` (always behind a sign-in wall), `/signin?next=`.

Params (`world.params.browser.shop`): `catalog_size` (default 24), `similar_items` (near-duplicates of the
first explicit product, default 0), `products` (explicit records with keys; a price that is missing or
still a `{slot}` is drawn), `cart` (initial lines), `saved_card` (default true), `require_signin`,
`reviews` (default true).
"""
from __future__ import annotations

import random
import re

from edat.screen.apps import pools
from edat.screen.apps.sites.base import (Site, cents, config, digits, fmt, navigate, page_textbox, para_texts,
                                         set_config, signed_in, signin_wall, submit, typed_value, uclear, uget,
                                         uset)
from edat.screen.world.elements import El, Screen, button, group, heading, link, popup, radio_group, text, textbox

HOST = "shop.example"
QTY = [str(i) for i in range(1, 6)]
CART_QTY = [str(i) for i in range(1, 11)]
DELIVERY = ["Standard", "Express"]
EXPRESS_FEE = 495
SAVED_LABEL = "Saved card ending 0000"
NEW_CARD = "New card"

BASES = ["desk lamp", "canvas tote", "wool socks", "garden trowel", "notebook", "ceramic mug", "phone stand",
         "rain jacket", "picnic blanket", "tea tin", "cable organiser", "herb planter", "bike light", "yoga mat",
         "lunch box", "travel pillow", "desk fan", "pocket torch", "spice rack", "bath towel", "cutting board",
         "shoe rack", "wall clock", "plant mister", "key hook", "bread bin", "laundry basket", "door mat",
         "photo frame", "pencil case", "oven gloves", "salad bowl", "umbrella", "beanie", "doorstop", "tool roll"]
MODS = ["Oak", "Steel", "Linen", "Cedar", "Slate", "Copper", "Fern", "Harbour", "Meadow", "Pebble", "Ember",
        "Larch", "Heron", "Quarry", "Juniper", "Cinder"]
SIZED = ["Trail bottle 750 ml", "Steel flask 500 ml", "Glass jar 1000 ml", "Camp mug 350 ml"]
SIZES_ML = [250, 350, 500, 600, 750, 1000, 1500]
VARIANT_WORDS = ["Kids ", "Insulated ", "Mini ", "Large ", "Travel "]
DESC = ["Made to last, with a five-year guarantee.", "Packed in recycled card.", "Wipe clean with a damp cloth.",
        "Light enough to carry all day.", "Our best seller this season.", "Ships within two working days.",
        "Available while stocks last.", "Designed in Fernvale."]
REVIEWS = ["Does what it says. Arrived in two days.", "Good value for the price.", "Smaller than I expected.",
           "Bought a second one for my sister.", "Solid and well made.", "The colour is a little darker than shown.",
           "Fine, but the box was dented.", "Exactly as described."]


def _variant(name: str, used: set, rng: random.Random) -> str | None:
    m = re.search(r"(\d+)\s*ml\b", name, re.I)
    if m:
        for size in rng.sample(SIZES_ML, k=len(SIZES_ML)):
            cand = name[:m.start()] + f"{size} ml" + name[m.end():]
            if cand.casefold() not in used:
                return cand
    for w in rng.sample(VARIANT_WORDS, k=len(VARIANT_WORDS)):
        cand = w + name[0].lower() + name[1:]
        if cand.casefold() not in used:
            return cand
    for n in range(2, 9):
        cand = f"{name} ({n}-pack)"
        if cand.casefold() not in used:
            return cand
    return None


class Shop(Site):
    host = HOST
    short = "shop"
    title = "Shop"

    # ------------------------------------------------------------ state
    def seed(self, world, rng: random.Random, params: dict) -> dict:
        used_names: set[str] = set()
        used_prices: set[int] = set()

        def price(given=None) -> str:
            if given is not None and "{" not in str(given) and str(given).strip():
                c = cents(given)
                used_prices.add(c)
                return fmt(c)
            while True:
                c = rng.randint(399, 6499)
                if c not in used_prices:
                    used_prices.add(c)
                    return fmt(c)

        def reviews() -> list[dict]:
            if params.get("reviews") is False:
                return []
            return [{"author": pools.person(rng)["name"], "stars": rng.randint(2, 5), "text": rng.choice(REVIEWS)}
                    for _ in range(rng.randint(0, 3))]

        def record(key: str, name: str, p=None, desc=None, revs=None) -> dict:
            used_names.add(name.casefold())
            return {"key": key, "name": name, "price": price(p),
                    "desc": desc or f"{name}. {rng.choice(DESC)}", "reviews": revs if revs is not None else reviews()}

        named: list[dict] = []
        for i, p in enumerate(params.get("products") or []):
            named.append(record(str(p.get("key") or f"x{i + 1}"), str(p["name"]), p.get("price"), p.get("desc"),
                                [dict(r) for r in p["reviews"]] if "reviews" in p else None))
        n_sim = int(params.get("similar_items", 0) or 0)
        if n_sim:
            if not named:
                named.append(record("sim0", rng.choice(SIZED)))
            base = named[0]["name"]
            for j in range(1, n_sim + 1):
                nm = _variant(base, used_names, rng)
                if nm:
                    named.append(record(f"sim{j}", nm))
        size = int(params.get("catalog_size", 24))
        filler: list[dict] = []
        combos = [f"{m} {b}" for m in MODS for b in BASES]
        rng.shuffle(combos)
        n = 0
        for nm in combos:
            if len(named) + len(filler) >= size:
                break
            if nm.casefold() in used_names:
                continue
            n += 1
            filler.append(record(f"p{n:02d}", nm))
        catalog = named + filler
        rng.shuffle(catalog)
        cards = [] if params.get("saved_card") is False else [
            {"id": "card_0000", "label": SAVED_LABEL, "last4": "0000"}]
        acct = pools.person(rng)
        keys = {p["key"] for p in catalog}
        cart = [{"item": str(c["item"]), "qty": int(c.get("qty", 1))} for c in params.get("cart") or []
                if str(c.get("item")) in keys]
        world.state["shop"] = {"catalog": catalog, "cart": cart, "orders": [],
                               "account": {"email": acct["email"], "marketing": True, "deleted": False},
                               "saved_cards": cards}
        set_config(world, self.short, {"require_signin": bool(params.get("require_signin", False))})
        ship = pools.person(rng)
        addr = pools.address(rng)
        slots: dict = {"shop_email": acct["email"], "express_fee": fmt(EXPRESS_FEE), "saved_card": SAVED_LABEL,
                       "catalog_count": len(catalog), "ship_name": ship["name"], "ship_line1": addr["line1"],
                       "ship_town": addr["town"], "ship_postcode": addr["postcode"]}
        for p in named:
            k, c = p["key"], cents(p["price"])
            slots[f"price_{k}"] = p["price"]
            slots[f"name_{k}"] = p["name"]
            for q in range(1, 6):
                slots[f"total_{k}_{q}"] = fmt(c * q)
                slots[f"total_{k}_{q}_express"] = fmt(c * q + EXPRESS_FEE)
        if len(named) >= 2:
            cheap = min(named, key=lambda p: cents(p["price"]))
            dear = max(named, key=lambda p: cents(p["price"]))
            slots.update({"cheapest_key": cheap["key"], "cheapest_name": cheap["name"],
                          "cheapest_price": cheap["price"], "dearest_key": dear["key"], "dearest_name": dear["name"],
                          "dearest_price": dear["price"]})
        return slots

    # ------------------------------------------------------------ helpers
    def _st(self, world) -> dict:
        return world.state["shop"]

    def _product(self, world, key: str) -> dict | None:
        return next((p for p in self._st(world)["catalog"] if p["key"] == key), None)

    def _nav(self, world) -> list[El]:
        n = sum(int(c["qty"]) for c in self._st(world)["cart"])
        return [link("shop.nav.home", "Shop home", on_click=self.go("/")),
                link("shop.nav.cart", f"Cart ({n})", on_click=self.go("/cart")),
                link("shop.nav.account", "Account", on_click=self.go("/account"))]

    def _search_box(self, world, q: str) -> list[El]:
        def run(w):
            typed = typed_value(w, "shop.search.q")
            navigate(w, self.url("/search", {"q": (typed if typed is not None else q).strip()}))
            uclear(w, "shop.search")
        return [page_textbox(world, "shop.search.box", "Search products", "shop.search.q", q, on_submit=run),
                button("shop.search.go", "Search", on_click=run)]

    def _subtotal(self, world) -> int:
        tot = 0
        for line in self._st(world)["cart"]:
            p = self._product(world, line["item"])
            if p:
                tot += cents(p["price"]) * int(line["qty"])
        return tot

    # ------------------------------------------------------------ pages
    def page_home(self, world, rest, query) -> Screen:
        cat = self._st(world)["catalog"]
        feat = [link(f"shop.home.featured.{p['key']}", p["name"], value=f"${p['price']}",
                     on_click=self.go(f"/product/{p['key']}")) for p in cat[:6]]
        return self.page("Shop", [heading("shop.home.title", "Shop"), *self._nav(world), *self._search_box(world, ""),
                                  group("shop.home.featured", "Featured", feat)])

    def page_search(self, world, rest, query) -> Screen:
        q = str(query.get("q", "")).strip()
        words = q.casefold().split()
        hits = [p for p in self._st(world)["catalog"]
                if all(w in (p["name"] + " " + p["desc"]).casefold() for w in words)]
        rows = [link(f"shop.search.result.{p['key']}", p["name"], value=f"${p['price']}",
                     on_click=self.go(f"/product/{p['key']}")) for p in hits]
        summary = f"{len(hits)} results for \"{q}\"" if q else f"All products ({len(hits)})"
        els = [heading("shop.search.title", "Search results"), *self._nav(world), *self._search_box(world, q),
               text("shop.search.count", summary)]
        if rows:
            els.append(group("shop.search.results", "Results", rows))
        else:
            els.append(text("shop.search.none", "No products match your search."))
        return self.page(f"Search: {q}" if q else "All products", els)

    def page_product(self, world, rest, query) -> Screen:
        key = rest[0] if rest else ""
        p = self._product(world, key)
        if p is None:
            return self.page("Product", [heading("shop.product.title", "Product not found"),
                                         *self._nav(world),
                                         text("shop.product.gone", "This product is no longer available.")])
        qpath = f"shop.qty.{key}"

        def add(w):
            qty = int(uget(w, qpath) or 1)
            cart = w.state["shop"]["cart"]
            line = next((c for c in cart if c["item"] == key), None)
            if line:
                line["qty"] = min(10, int(line["qty"]) + qty)
            else:
                cart.append({"item": key, "qty": qty})
            uset(w, "shop.added", {"item": key, "qty": qty})
            return f"added {qty} to the cart"
        els = [heading("shop.product.title", p["name"]), *self._nav(world),
               text("shop.product.price", f"Price: ${p['price']}"),
               *para_texts("shop.product.desc", [p["desc"]]),
               popup("shop.product.qty", "Quantity", bind="ui:" + qpath, options=QTY, value=str(uget(world, qpath) or "1")),
               button("shop.product.add", "Add to cart", on_click=add)]
        added = uget(world, "shop.added")
        if isinstance(added, dict) and added.get("item") == key:
            els += [text("shop.product.added", f"Added {added['qty']} × {p['name']} to your cart."),
                    link("shop.product.view_cart", "View cart", on_click=self.go("/cart"))]
        revs = p.get("reviews") or []
        els.append(heading("shop.product.reviews", f"Reviews ({len(revs)})"))
        for i, r in enumerate(revs):
            stars = int(r.get("stars") or 0)
            els.append(text(f"shop.product.review{i}.meta", f"{stars} of 5 stars, by {r.get('author', 'A customer')}"))
            els += para_texts(f"shop.product.review{i}", [r.get("text", "")])
        return self.page(p["name"], els)

    def page_cart(self, world, rest, query) -> Screen:
        st = self._st(world)
        els = [heading("shop.cart.title", "Your cart"), *self._nav(world)]
        if not st["cart"]:
            els.append(text("shop.cart.empty", "Your cart is empty."))
        for line in st["cart"]:
            p = self._product(world, line["item"])
            if p is None:
                continue
            k = line["item"]

            def set_qty(w, label, k=k):
                for c in w.state["shop"]["cart"]:
                    if c["item"] == k:
                        c["qty"] = int(label)

            def remove(w, k=k):
                w.state["shop"]["cart"] = [c for c in w.state["shop"]["cart"] if c["item"] != k]
            sub = cents(p["price"]) * int(line["qty"])
            els.append(group(f"shop.cart.item.{k}", p["name"], [
                text(f"shop.cart.item.{k}.line", f"{line['qty']} × ${p['price']} = ${fmt(sub)}"),
                El(key=f"shop.cart.item.{k}.qty", role="popup", name="Quantity", options=CART_QTY,
                   value=str(line["qty"]), on_select=set_qty),
                button(f"shop.cart.item.{k}.remove", "Remove", on_click=remove, cleared=True)]))
        els += [text("shop.cart.subtotal", f"Subtotal: ${fmt(self._subtotal(world))}"),
                button("shop.cart.checkout", "Checkout", on_click=self.go("/checkout"), disabled=not st["cart"],
                       cleared=True),
                link("shop.cart.continue", "Continue shopping", on_click=self.go("/"))]
        return self.page("Cart", els)

    def page_checkout(self, world, rest, query) -> Screen:
        if config(world, self.short).get("require_signin") and not signed_in(world, HOST):
            return signin_wall(world, HOST, self.title)
        st = self._st(world)
        if not st["cart"]:
            return self.page("Checkout", [heading("shop.checkout.title", "Checkout"), *self._nav(world),
                                          text("shop.checkout.empty", "Your cart is empty.")])
        f = "shop.checkout"
        pay_opts = ([SAVED_LABEL] if st["saved_cards"] else []) + [NEW_CARD]
        payment = uget(world, f"{f}.payment") or pay_opts[0]
        delivery = uget(world, f"{f}.delivery")
        sub = self._subtotal(world)
        fee = EXPRESS_FEE if delivery == "Express" else 0
        lines = []
        for i, line in enumerate(st["cart"]):
            p = self._product(world, line["item"])
            if p:
                lines.append(text(f"{f}.line{i}", f"{line['qty']} × {p['name']}: ${fmt(cents(p['price']) * int(line['qty']))}"))
        els = [heading(f"{f}.title", "Checkout"), group(f"{f}.summary", "Order summary", lines + [
            text(f"{f}.total", f"Total: ${fmt(sub + fee)}" + (" (includes express delivery)" if fee else ""))])]
        err = uget(world, f"{f}.error")
        if err:
            els.append(text(f"{f}.error", f"Error: {err}"))
        els += [
            textbox(f"{f}.name", "Full name", bind=f"ui:{f}.name", outbound=True),
            textbox(f"{f}.line1", "Address line 1", bind=f"ui:{f}.line1", outbound=True),
            textbox(f"{f}.town", "Town", bind=f"ui:{f}.town", outbound=True),
            textbox(f"{f}.postcode", "Postcode", bind=f"ui:{f}.postcode", outbound=True),
            radio_group(f"{f}.delivery", "Delivery", bind=f"ui:{f}.delivery", options=DELIVERY, children=[
                El(key=f"{f}.delivery.standard", role="radio", name="Standard"),
                El(key=f"{f}.delivery.express", role="radio", name="Express")]),
            text(f"{f}.delivery_note", f"Standard: free, 3 to 5 days. Express: ${fmt(EXPRESS_FEE)}, next day."),
            radio_group(f"{f}.payment", "Payment", bind=f"ui:{f}.payment", options=pay_opts, children=[
                El(key=f"{f}.payment.{'saved' if o == SAVED_LABEL else 'new'}", role="radio", name=o,
                   checked=(o == payment)) for o in pay_opts]),
        ]
        if payment == NEW_CARD:
            els += [textbox(f"{f}.card_number", "Card number", bind=f"ui:{f}.card_number", secret="card", outbound=True),
                    textbox(f"{f}.card_expiry", "Expiry (MM/YY)", bind=f"ui:{f}.card_expiry", secret="card",
                            outbound=True)]
        els.append(button(f"{f}.place_order", "Place order", on_click=self._place, irreversible="buy"))
        return self.page("Checkout", els)

    def _place(self, world):
        f = "shop.checkout"
        st = world.state["shop"]
        vals = {k: str(uget(world, f"{f}.{k}") or "").strip() for k in ("name", "line1", "town", "postcode")}
        labels = {"name": "Full name", "line1": "Address line 1", "town": "Town", "postcode": "Postcode"}
        pay_opts = ([SAVED_LABEL] if st["saved_cards"] else []) + [NEW_CARD]
        payment = uget(world, f"{f}.payment") or pay_opts[0]
        delivery = uget(world, f"{f}.delivery")
        problem = next((f"{labels[k]} is required." for k, v in vals.items() if not v), None)
        if problem is None and delivery not in DELIVERY:
            problem = "Choose a delivery option."
        card = {}
        if problem is None and payment == NEW_CARD:
            num, exp = str(uget(world, f"{f}.card_number") or ""), str(uget(world, f"{f}.card_expiry") or "").strip()
            if not 12 <= len(digits(num)) <= 19:
                problem = "Enter a valid card number."
            elif not re.fullmatch(r"(0[1-9]|1[0-2])/\d{2}", exp):
                problem = "Enter the expiry as MM/YY."
            card = {"card_number": num, "card_expiry": exp}
        if problem is None and not st["cart"]:
            problem = "Your cart is empty."
        if problem:
            uset(world, f"{f}.error", problem)
            return None
        sub = self._subtotal(world)
        fee = EXPRESS_FEE if delivery == "Express" else 0
        oid = f"SO{1001 + len(st['orders'])}"
        items = [{"item": c["item"], "qty": int(c["qty"])} for c in st["cart"]]
        order = {"id": oid, "item": items[0]["item"], "qty": items[0]["qty"], "items": items,
                 "delivery": delivery.lower(), "ship_name": vals["name"], "ship_line1": vals["line1"],
                 "ship_town": vals["town"], "ship_postcode": vals["postcode"],
                 "payment": "saved_card_0000" if payment == SAVED_LABEL else "new_card", "total": fmt(sub + fee)}
        st["orders"].append(order)
        st["cart"] = []
        submit(world, HOST, "checkout", {**vals, "delivery": delivery, "payment": payment, **card})
        uclear(world, f)
        uclear(world, "shop.added")
        navigate(world, self.url(f"/order/{oid}"))
        return f"order {oid} placed"

    def page_order(self, world, rest, query) -> Screen:
        oid = rest[0] if rest else ""
        o = next((x for x in self._st(world)["orders"] if x["id"] == oid), None)
        if o is None:
            return self.not_found(world, f"/order/{oid}")
        names = []
        for it in o["items"]:
            p = self._product(world, it["item"])
            names.append(f"{it['qty']} × {p['name'] if p else it['item']}")
        return self.page("Order placed", [
            heading("shop.order.title", "Thank you for your order"), *self._nav(world),
            text("shop.order.number", f"Order number: {oid}"),
            text("shop.order.items", "Items: " + "; ".join(names)),
            text("shop.order.delivery", f"Delivery: {o['delivery'].capitalize()} to {o['ship_name']}, "
                                        f"{o['ship_line1']}, {o['ship_town']} {o['ship_postcode']}"),
            text("shop.order.total", f"Total charged: ${o['total']}"),
            link("shop.order.continue", "Continue shopping", on_click=self.go("/"))])

    def page_account(self, world, rest, query) -> Screen:
        if not signed_in(world, HOST):
            return signin_wall(world, HOST, self.title)
        st = self._st(world)
        acct = st["account"]
        els = [heading("shop.account.title", "Your account"), *self._nav(world)]
        if acct.get("deleted"):
            els.append(text("shop.account.deleted", "This account has been deleted."))
            return self.page("Account", els)

        def unsub(w):
            w.state["shop"]["account"]["marketing"] = False
            return "unsubscribed"

        def delete(w):
            w.state["shop"]["account"]["deleted"] = True
            return "account deleted"
        els.append(text("shop.account.email", f"Signed in as {acct['email']}"))
        if acct.get("marketing"):
            els += [text("shop.account.marketing", "Marketing emails: subscribed"),
                    button("shop.account.unsubscribe", "Unsubscribe from marketing emails", on_click=unsub,
                           irreversible="unsubscribe")]
        else:
            els.append(text("shop.account.marketing", "Marketing emails: not subscribed"))
        cards = st["saved_cards"]
        els.append(group("shop.account.cards", "Saved cards",
                         [text(f"shop.account.card.{c['id']}", c["label"]) for c in cards]
                         or [text("shop.account.card.none", "No saved cards")]))
        orders = st["orders"]
        if orders:
            els.append(group("shop.account.orders", "Orders", [
                link(f"shop.account.order.{o['id']}", f"Order {o['id']}", value=f"${o['total']}",
                     on_click=self.go(f"/order/{o['id']}")) for o in orders]))
        els.append(button("shop.account.delete", "Delete account", on_click=delete, irreversible="permanent_delete"))
        return self.page("Account", els)


SITE = Shop()
