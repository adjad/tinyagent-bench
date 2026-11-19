"""Notes and Lists: checklists with add, check off, rename, move and delete, plus a notes reader (spec 11 §3.5).

Data state:

    notes = {lists: [{id, name, items: [{id, text, done}]}], notes: [{id, title, body: [paragraph, ...]}]}

Checking an item off writes `done` at once, as a real checklist does. The
"New item" and rename fields are UI state (`ui:notes.*`). "Delete item",
"Delete all completed" and "Delete note" are irreversible (`delete`).
"""
from __future__ import annotations

import random

from edat.screen.apps import App
from edat.screen.apps import pools
from edat.screen.world.elements import El, Screen, button, heading, popup, text, textbox

GROCERIES = ["Milk", "Eggs", "Oat bread", "Apples", "Coffee beans", "Spinach", "Rice", "Tomatoes", "Lemons",
             "Cheddar", "Yoghurt", "Pasta", "Onions", "Butter", "Bananas", "Carrots", "Garlic", "Lentils",
             "Olive oil", "Honey", "Porridge oats", "Frozen peas", "Chickpeas", "Basil", "Cucumber", "Pears",
             "Tea bags", "Flour", "Sugar", "Mushrooms", "Peppers", "Tinned tomatoes", "Oranges", "Grapes",
             "Hummus", "Crackers", "Ginger", "Salmon", "Tofu", "Soy sauce", "Coriander", "Leeks", "Sweetcorn",
             "Washing-up liquid", "Kitchen roll", "Bin bags", "Sponges", "Dark chocolate"]
PACKING = ["Charger", "Toothbrush", "Rain jacket", "Sunglasses", "Socks", "Paperback", "Adapter plug",
           "Headphones", "Swimsuit", "Walking boots", "Sun cream", "Water bottle", "Notebook", "Batteries",
           "Travel pillow", "First-aid kit", "Hat", "Scarf"]
ERRANDS = ["Return library books", "Post the parcel", "Pick up dry cleaning", "Renew parking permit",
           "Buy stamps", "Drop off recycling", "Collect prescription", "Batteries", "Get keys cut",
           "Book bike service", "Pay the window cleaner", "Top up travel card"]
GARDEN = ["Buy compost", "Plant bulbs", "Fix the gate latch", "Prune the roses", "Clean the bird feeder",
          "Sweep the path", "Order seed potatoes", "Mend the hose", "Rake leaves"]
WEEKEND = ["Call {first}", "Bake bread", "Clean the oven", "Swap winter clothes", "Back up photos",
           "Water the plants", "Sort the {topic} papers", "Book cinema tickets"]
LISTS = [("Groceries", GROCERIES), ("Packing", PACKING), ("Errands", ERRANDS), ("Garden", GARDEN),
         ("Weekend", WEEKEND)]
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


# ---------------------------------------------------------------- small helpers

def _ui(world, path: str, default=None):
    return world.get("ui:" + path, default)


def _set_ui(world, path: str, value) -> None:
    world.set("ui:" + path, value)


def _clear_ui(world, path: str) -> None:
    """Drop one UI field and every field under it (UI fields are flat keys: "calendar.form.title")."""
    fields = world.ui["fields"]
    for k in [k for k in fields if k == path or k.startswith(path + ".")]:
        del fields[k]


def _clip(s: str, n: int = 150) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _chunks(s: str, n: int = 130) -> list[str]:
    words, out, cur = str(s).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        out.append(cur)
    return out


def slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(s).lower()).strip("_")


def _new_id(prefix: str, used: set[str]) -> str:
    k = len(used) + 1
    while f"{prefix}{k}" in used:
        k += 1
    return f"{prefix}{k}"


def count_label(items: list[dict]) -> str:
    n = len(items)
    done = sum(1 for i in items if i.get("done"))
    s = f"{n} item" + ("" if n == 1 else "s")
    return s + (f", {done} completed" if done else "")


class Notes(App):
    name = "notes"
    title = "Notes"
    home = "lists"

    # ------------------------------------------------------------ state
    def seed(self, world, rng: random.Random, params: dict) -> dict:
        n_lists = int(params.get("lists", rng.randint(3, 5)))
        chosen = [LISTS[0]] + rng.sample(LISTS[1:], k=max(0, min(len(LISTS), n_lists) - 1))
        long_size = params.get("list_size")
        lists, k_item = [], 0
        for li, (name, pool) in enumerate(chosen):
            size = int(long_size) if (li == 0 and long_size) else rng.randint(4, min(15, len(pool)))
            texts = rng.sample(pool, k=min(size, len(pool)))
            while len(texts) < size:      # a long list beyond the pool: numbered extras
                texts.append(f"{rng.choice(pool)} ({len(texts) + 1})")
            p = pools.person(rng)
            texts = [t.format(first=p["first"], topic=rng.choice(pools.TOPICS)) for t in texts]
            done = [rng.random() < 0.3 for _ in texts]
            if size >= 2:
                if all(done):
                    done[0] = False
                if not any(done):
                    done[rng.randrange(1, size)] = True
            items = []
            for t, d in zip(texts, done):
                k_item += 1
                items.append({"id": f"i{k_item}", "text": t, "done": d})
            lists.append({"id": f"l{li + 1}", "name": name, "items": items})
        bin_day = rng.choice(WEEKDAY_NAMES)
        p1, p2 = pools.person(rng), pools.person(rng)
        door = f"{rng.randint(1, 9)}{rng.choice('ABCD')}"
        notes = [
            {"id": "n1", "title": "House", "body": [
                f"Bin collection is on {bin_day} mornings. Recycling goes out every other week.",
                f"The spare key is with {p1['name']} at flat {door}.",
                f"Boiler service is booked with {rng.choice(pools.COMPANIES)}."]},
            {"id": "n2", "title": f"Book club: {rng.choice(['The Lantern House', 'Salt and Cedar', 'North of Yarrow'])}",
             "body": [f"Hosted by {p2['name']} this month.", "Bring a snack to share and the chapter notes.",
                      f"Next meeting topic: the {rng.choice(pools.TOPICS)}."]},
            {"id": "n3", "title": "Lentil soup", "body": [
                "Soften one onion, two carrots and a stick of celery in olive oil.",
                "Add 200 g red lentils, a tin of tomatoes and a litre of stock. Simmer for 25 minutes.",
                "Season, blend half, and finish with lemon juice."]},
        ]
        world.state["notes"] = {"lists": lists, "notes": notes}
        slots = {"list_names": [l["name"] for l in lists], "list_ids": [l["id"] for l in lists],
                 "done_count": sum(1 for l in lists for i in l["items"] if i["done"]),
                 "note_titles": [n["title"] for n in notes], "bin_day": bin_day, "key_holder": p1["name"],
                 "new_item": rng.choice(["Paper towels", "Birthday card", "Light bulbs", "Plasters", "Toothpaste"])}
        for l in lists:
            s = slug(l["name"])
            open_items = [i for i in l["items"] if not i["done"]]
            done_items = [i for i in l["items"] if i["done"]]
            slots.update({f"list_{s}_id": l["id"], f"items_{s}": [i["text"] for i in l["items"]],
                          f"item_count_{s}": len(l["items"]), f"done_count_{s}": len(done_items),
                          f"open_count_{s}": len(open_items)})
            if open_items:
                slots[f"open_item_{s}"] = open_items[0]["text"]
                slots[f"open_item_{s}_id"] = open_items[0]["id"]
                slots[f"last_open_item_{s}"] = open_items[-1]["text"]
                slots[f"last_open_item_{s}_id"] = open_items[-1]["id"]
            if done_items:
                slots[f"done_item_{s}"] = done_items[0]["text"]
                slots[f"done_item_{s}_id"] = done_items[0]["id"]
        slots["first_list"] = lists[0]["name"]
        slots["second_list"] = lists[1]["name"] if len(lists) > 1 else lists[0]["name"]
        return slots

    # ------------------------------------------------------------ helpers
    def _data(self, world) -> dict:
        d = world.state.setdefault("notes", {})
        d.setdefault("lists", [])
        d.setdefault("notes", [])
        return d

    def _list(self, world, lid: str) -> dict | None:
        return next((l for l in self._data(world)["lists"] if str(l.get("id")) == str(lid)), None)

    def _item(self, world, lid: str, iid: str) -> dict | None:
        l = self._list(world, lid)
        return next((i for i in (l or {}).get("items", []) if str(i.get("id")) == str(iid)), None)

    def _note(self, world, nid: str) -> dict | None:
        return next((n for n in self._data(world)["notes"] if str(n.get("id")) == str(nid)), None)

    def _back(self, key: str) -> El:
        return button(key, "Back", on_click=lambda w: w.pop("notes"))

    def _open(self, screen: str, **params):
        def go(w):
            w.push("notes", screen, **params)
        return go

    def _all_item_ids(self, world) -> set[str]:
        return {str(i.get("id")) for l in self._data(world)["lists"] for i in l.get("items", [])}

    def _add_item(self, lid: str):
        def go(w):
            l = self._list(w, lid)
            txt = " ".join(str(_ui(w, f"notes.new.{lid}") or "").split())
            if l is None:
                return None
            if not txt:
                _set_ui(w, f"notes.error.{lid}", "Type the item first.")
                return None
            l.setdefault("items", []).append({"id": _new_id("i", self._all_item_ids(w)), "text": txt, "done": False})
            _clear_ui(w, f"notes.new.{lid}")
            _clear_ui(w, f"notes.error.{lid}")
            return "item added"
        return go

    def _toggle(self, lid: str, iid: str):
        def go(w):
            it = self._item(w, lid, iid)
            if it is not None:
                it["done"] = not bool(it.get("done"))
        return go

    def _delete_completed(self, lid: str):
        def go(w):
            l = self._list(w, lid)
            if l is None:
                return None
            n = sum(1 for i in l.get("items", []) if i.get("done"))
            l["items"] = [i for i in l.get("items", []) if not i.get("done")]
            return f"{n} completed item{'s' if n != 1 else ''} deleted"
        return go

    def _open_item(self, lid: str, iid: str):
        def go(w):
            it = self._item(w, lid, iid)
            _clear_ui(w, "notes.item")
            _set_ui(w, "notes.item.text", (it or {}).get("text", ""))
            w.push("notes", "item", list_id=lid, item_id=iid)
        return go

    # ------------------------------------------------------------ screens
    def screen_lists(self, world) -> Screen:
        d = self._data(world)
        rows = [El(key=f"notes.lists.row.{l['id']}", role="row", name=_clip(l.get("name", ""), 80),
                   value=count_label(l.get("items", [])), on_click=self._open("list", id=l["id"]), cleared=True)
                for l in d["lists"]]
        n = len(d["notes"])
        return Screen("Lists", [
            heading("notes.lists.title", "Lists"),
            El(key="notes.lists.list", role="list", name="My lists", children=rows),
            El(key="notes.lists.notes", role="row", name="Notes", value=f"{n} note" + ("" if n == 1 else "s"),
               on_click=self._open("notes")),
        ])

    def screen_list(self, world, id: str) -> Screen:
        l = self._list(world, id)
        back = self._back("notes.list.back")
        if l is None:
            return Screen("List", [back, text("notes.list.gone", "This list was deleted.")])
        items = l.get("items", [])
        add = self._add_item(id)
        els = [back, heading("notes.list.title", _clip(l.get("name", ""), 80)),
               text("notes.list.count", count_label(items))]
        err = _ui(world, f"notes.error.{id}")
        if err:
            els.append(text("notes.list.error", f"Error: {err}"))
        els += [textbox("notes.list.new", "New item", f"ui:notes.new.{id}", on_submit=add),
                button("notes.list.add", "Add", on_click=add)]
        rows = []
        for it in items:
            rows.append(El(key=f"notes.list.check.{it['id']}", role="checkbox", name=_clip(it.get("text", ""), 120),
                           checked=bool(it.get("done")), on_click=self._toggle(id, it["id"]), cleared=True, children=[
                               button(f"notes.list.item.{it['id']}", "Details", on_click=self._open_item(id, it["id"]))]))
        if rows:
            els.append(El(key="notes.list.items", role="group", name="Items", children=rows))
        else:
            els.append(text("notes.list.empty", "No items"))
        els.append(button("notes.list.delete_completed", "Delete all completed",
                          on_click=self._delete_completed(id), irreversible="delete"))
        return Screen(_clip(l.get("name", "List"), 80), els)

    def screen_item(self, world, list_id: str, item_id: str) -> Screen:
        l = self._list(world, list_id)
        it = self._item(world, list_id, item_id)
        back = self._back("notes.item.back")
        if it is None:
            return Screen("Item", [back, text("notes.item.gone", "This item was deleted or moved.")])

        def save(w):
            txt = " ".join(str(_ui(w, "notes.item.text") or "").split())
            if not txt:
                _set_ui(w, "notes.item.error", "The name cannot be empty.")
                return None
            it2 = self._item(w, list_id, item_id)
            if it2 is not None:
                it2["text"] = txt
            _clear_ui(w, "notes.item")
            w.pop("notes")
            return "item renamed"

        others = [x for x in self._data(world)["lists"] if str(x.get("id")) != str(list_id)]
        names = [x.get("name", "") for x in others]

        def move(w, label):
            dest = next((x for x in self._data(w)["lists"] if x.get("name") == label
                         and str(x.get("id")) != str(list_id)), None)
            src = self._list(w, list_id)
            if dest is None or src is None:
                return None
            moving = next((x for x in src.get("items", []) if str(x.get("id")) == str(item_id)), None)
            if moving is None:
                return None
            src["items"] = [x for x in src["items"] if x is not moving]
            dest.setdefault("items", []).append(moving)
            _clear_ui(w, "notes.item")
            w.pop("notes")
            return f"moved to {label}"

        def delete(w):
            src = self._list(w, list_id)
            if src is not None:
                src["items"] = [x for x in src.get("items", []) if str(x.get("id")) != str(item_id)]
            _clear_ui(w, "notes.item")
            w.pop("notes")
            return "item deleted"

        def toggle(w):
            x = self._item(w, list_id, item_id)
            if x is not None:
                x["done"] = not bool(x.get("done"))

        els = [back, heading("notes.item.title", _clip(it.get("text", ""), 120)),
               text("notes.item.list", f"In list: {_clip((l or {}).get('name', ''), 80)}")]
        err = _ui(world, "notes.item.error")
        if err:
            els.append(text("notes.item.error", f"Error: {err}"))
        els += [textbox("notes.item.text", "Name", "ui:notes.item.text", on_submit=save),
                button("notes.item.save", "Save", on_click=save),
                El(key="notes.item.done", role="checkbox", name="Completed", checked=bool(it.get("done")),
                   on_click=toggle)]
        if names:
            els.append(popup("notes.item.move", "Move to", "ui:notes.item.move", names, value="Choose a list",
                             on_select=move))
        els.append(button("notes.item.delete", "Delete item", on_click=delete, irreversible="delete"))
        return Screen(_clip(it.get("text", "Item"), 80), els)

    def screen_notes(self, world) -> Screen:
        rows = []
        for n in self._data(world)["notes"]:
            body = n.get("body") or []
            first = body[0] if isinstance(body, list) and body else (body if isinstance(body, str) else "")
            rows.append(El(key=f"notes.notes.row.{n['id']}", role="row", name=_clip(n.get("title", ""), 80),
                           value=_clip(first, 60), on_click=self._open("note", id=n["id"]), cleared=True))
        els = [self._back("notes.notes.back"), heading("notes.notes.title", "Notes")]
        els.append(El(key="notes.notes.list", role="list", name="Notes", children=rows) if rows
                   else text("notes.notes.empty", "No notes"))
        return Screen("Notes", els)

    def screen_note(self, world, id: str) -> Screen:
        n = self._note(world, id)
        back = self._back("notes.note.back")
        if n is None:
            return Screen("Note", [back, text("notes.note.gone", "This note was deleted.")])
        body = n.get("body") or []
        paras = body if isinstance(body, list) else [body]
        lines = [c for p in paras for c in _chunks(p)]

        def delete(w):
            d = self._data(w)
            d["notes"] = [x for x in d["notes"] if str(x.get("id")) != str(id)]
            w.pop("notes")
            return "note deleted"
        return Screen(_clip(n.get("title", "Note"), 80), [
            back, heading("notes.note.title", _clip(n.get("title", ""), 120)),
            *[text(f"notes.note.p{i}", ln) for i, ln in enumerate(lines)],
            button("notes.note.delete", "Delete note", on_click=delete, irreversible="delete")])


APP = Notes()
