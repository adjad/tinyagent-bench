"""Files: folders with a sidebar, a file list with multi-select, a file detail screen and the Trash (spec 11 §3.5).

Data state:

    files = {items: [{id, name, folder, kind, size_kb, modified: "YYYY-MM-DDTHH:MM"}], trash: [... same shape ...]}

A trashed file keeps its `folder`, which is where "Put back" returns it.
Selection is UI state (`ui:files.selected.<id>`), as are the rename field and
the "Move to" choice. "Move to Trash" is `delete`; "Delete immediately" and
"Empty Trash" are `permanent_delete`.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from edat.screen.apps import App
from edat.screen.apps import pools
from edat.screen.world.elements import El, Screen, button, checkbox, group, heading, link, popup, text, textbox

FOLDERS = ["Desktop", "Documents", "Downloads", "Pictures"]
KINDS = {"png": "PNG image", "jpg": "JPEG image", "heic": "HEIC image", "pdf": "PDF document",
         "docx": "Word document", "txt": "Plain text", "zip": "ZIP archive", "csv": "CSV document",
         "mov": "Movie", "mp3": "MP3 audio"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DOCUMENTS = ["Lease renewal.pdf", "Car insurance 2026.pdf", "CV 2026.docx", "Garden plan.docx",
             "Book club notes.txt", "Budget 2026.csv", "Recipe - lentil soup.txt", "Council tax letter.pdf",
             "Warranty - washing machine.pdf", "Allotment rota.docx", "Packing list.txt", "Choir schedule.pdf",
             "Boiler service report.pdf", "School trip form.pdf", "Bike repair quote.pdf"]
DOWNLOADS = ["Train tickets Kelmouth.pdf", "photo-export.zip", "Concert e-ticket.pdf", "Bus timetable.pdf",
             "Menu - Copper Row Cafe.pdf", "Podcast episode 12.mp3", "Map of Fernvale.pdf"]
PICTURES = ["Beach at Kelmouth.jpg", "Birthday cake.heic", "Garden in bloom.jpg", "Snow on Larch Lane.jpg",
            "Fernvale fair.mov", "Dog in the park.heic"]
DESKTOP = ["Screen time report.pdf", "To do.txt", "Seating plan.pdf", "Moodboard.jpg"]
TRASH = ["Old CV.docx", "Untitled.txt", "Invoice draft.docx", "Blurry photo.jpg"]
SIZES = {"png": (300, 4200), "jpg": (900, 6500), "heic": (1200, 4800), "pdf": (40, 2400), "docx": (18, 400),
         "txt": (1, 30), "zip": (25000, 240000), "csv": (4, 120), "mov": (90000, 900000), "mp3": (20000, 90000)}


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


def _clip(s: str, n: int = 120) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(s).lower()).strip("_")


def ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def kind_for(name: str) -> str:
    return KINDS.get(ext(name), "Document")


def size_label(kb) -> str:
    kb = int(kb or 0)
    if kb < 1000:
        return f"{kb} KB"
    if kb < 1_000_000:
        return f"{kb / 1000:.1f} MB"
    return f"{kb / 1_000_000:.2f} GB"


def date_label(iso: str) -> str:
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}, {d:%H:%M}"


def _name_key(f: dict):
    return (str(f.get("name", "")).casefold(), str(f.get("id", "")))


class Files(App):
    name = "files"
    title = "Files"
    home = "folder"

    # ------------------------------------------------------------ state
    def seed(self, world, rng: random.Random, params: dict) -> dict:
        now = datetime.fromisoformat(world.clock[:16])
        home = params.get("folder") or "Documents"
        size = params.get("folder_size")
        items: list[dict] = []
        trash: list[dict] = []
        k = [0]

        def make(name, folder, days_lo=1, days_hi=60, kb=None, when=None):
            k[0] += 1
            lo, hi = SIZES.get(ext(name), (10, 500))
            mod = when or (now - timedelta(days=rng.randint(days_lo, days_hi))).replace(
                hour=rng.randint(8, 20), minute=rng.randint(0, 59))
            return {"id": f"f{k[0]}", "name": name, "folder": folder, "kind": kind_for(name),
                    "size_kb": int(kb if kb is not None else rng.randint(lo, hi)), "modified": mod.strftime("%Y-%m-%dT%H:%M")}

        # Desktop: screenshots on distinct recent days, plus look-alike distractors
        n_shots = int(params.get("screenshots", rng.randint(2, 4)))
        offsets = sorted(rng.sample(range(1, max(15, n_shots + 2)), k=n_shots), reverse=True)
        shots = []
        for off in offsets:
            t = (now - timedelta(days=off)).replace(hour=rng.randint(8, 21), minute=rng.randint(0, 59))
            f = make(f"Screenshot {t:%Y-%m-%d} at {t:%H.%M}.png", "Desktop", when=t)
            shots.append(f)
            items.append(f)
        for name in [DESKTOP[0]] + rng.sample(DESKTOP[1:], k=rng.randint(1, 2)):   # DESKTOP[0]: the look-alike
            items.append(make(name, "Desktop"))

        # Documents: invoices plus everyday documents
        companies = rng.sample(pools.COMPANIES, k=3)
        invoices = []
        for i, c in enumerate(companies):
            m = (now.month - 1 - i) % 12
            f = make(f"Invoice - {c} - {MONTHS[m]}.pdf", "Documents", days_lo=2 + 30 * i, days_hi=8 + 30 * i)
            invoices.append(f)
            items.append(f)

        def fill_folder(folder: str, pool: list[str], n: int) -> None:
            names = rng.sample(pool, k=min(n, len(pool)))
            j = 1
            while len(names) < n:      # a long folder beyond the pool: numbered scans
                names.append(f"Scan {j:04d}.pdf")
                j += 1
            for name in names:
                items.append(make(name, folder))

        def want(folder: str, lo: int, hi: int, already: int = 0) -> int:
            if size and folder == home:
                return max(0, int(size) - already)
            return rng.randint(lo, hi)

        fill_folder("Documents", DOCUMENTS, want("Documents", 4, 7, len(invoices)))
        fill_folder("Downloads", DOWNLOADS, want("Downloads", 3, 5))
        pics = [f"IMG_{n}.jpg" for n in rng.sample(range(4000, 4999), k=rng.randint(2, 4))]
        fill_folder("Pictures", PICTURES + pics, want("Pictures", 4, 7))
        if size and home == "Desktop":
            extra = int(size) - sum(1 for f in items if f["folder"] == "Desktop")
            j = 1
            while extra > 0:
                items.append(make(f"Desktop note {j}.txt", "Desktop"))
                j += 1
                extra -= 1
        # one clearly largest file
        biggest = max(items, key=lambda f: (f["size_kb"], f["id"]))
        biggest["size_kb"] += rng.randint(50_000, 200_000)
        for name in rng.sample(TRASH, k=rng.randint(1, 3)):
            home_of = ["Desktop", "Pictures"] if ext(name) in ("jpg", "png", "heic") else ["Documents", "Downloads"]
            trash.append(make(name, rng.choice(home_of), days_lo=20, days_hi=90))
        world.state["files"] = {"items": items, "trash": trash}
        stack = world.nav["stacks"].get("files")
        if params.get("folder") and stack and len(stack) == 1 and stack[0]["screen"] == "folder" and not stack[0]["params"]:
            stack[0]["params"] = {"folder": home}

        newest = max(items, key=lambda f: (f["modified"], f["id"]))
        docs = [f for f in items if f["folder"] == "Documents" and not f["name"].startswith("Invoice")]
        slots = {
            "folders": list(FOLDERS),
            "screenshot_ids": [f["id"] for f in shots], "screenshot_names": [f["name"] for f in shots],
            "screenshot_count": len(shots),
            "oldest_screenshot": shots[0]["name"] if shots else "", "newest_screenshot": shots[-1]["name"] if shots else "",
            "oldest_screenshot_id": shots[0]["id"] if shots else "",
            "newest_screenshot_id": shots[-1]["id"] if shots else "",
            "screenshot_distractor": "Screen time report.pdf",
            "largest_file": biggest["name"], "largest_file_id": biggest["id"],
            "largest_file_size": size_label(biggest["size_kb"]), "largest_file_folder": biggest["folder"],
            "newest_file": newest["name"], "newest_file_id": newest["id"],
            "invoice_names": [f["name"] for f in invoices], "latest_invoice": invoices[0]["name"],
            "latest_invoice_id": invoices[0]["id"],
            "trash_names": [f["name"] for f in trash], "trash_ids": [f["id"] for f in trash],
            "file_count_trash": len(trash),
            "trash_file": trash[0]["name"], "trash_file_id": trash[0]["id"], "trash_file_folder": trash[0]["folder"],
        }
        if docs:
            slots.update({"doc_file": docs[0]["name"], "doc_file_id": docs[0]["id"],
                          "doc_file_stem": docs[0]["name"].rsplit(".", 1)[0]})
        for folder in FOLDERS:
            slots[f"file_count_{folder.lower()}"] = sum(1 for f in items if f["folder"] == folder)
        return slots

    # ------------------------------------------------------------ helpers
    def _data(self, world) -> dict:
        d = world.state.setdefault("files", {})
        d.setdefault("items", [])
        d.setdefault("trash", [])
        return d

    def _file(self, world, fid: str) -> dict | None:
        return next((f for f in self._data(world)["items"] if str(f.get("id")) == str(fid)), None)

    def _selected(self, world, fid: str) -> bool:
        return bool(_ui(world, f"files.selected.{fid}"))

    def _sidebar(self, world, current: str) -> El:
        def go(folder: str):
            def f(w):
                if folder == "Trash":
                    w.reset_stack("files", "trash")
                else:
                    w.reset_stack("files", "folder", folder=folder)
            return f
        links = [link(f"files.sidebar.{slug(n)}", n, on_click=go(n), selected=(n == current))
                 for n in FOLDERS + ["Trash"]]
        return group("files.sidebar", "Folders", links)

    def _trash_selected(self, folder: str):
        def go(w):
            d = self._data(w)
            moving = [f for f in d["items"] if f.get("folder") == folder and self._selected(w, f["id"])]
            if not moving:
                return None
            ids = {f["id"] for f in moving}
            d["items"] = [f for f in d["items"] if f["id"] not in ids]
            d["trash"].extend(moving)
            for i in ids:
                _clear_ui(w, f"files.selected.{i}")
            return f"{len(moving)} moved to Trash"
        return go

    def _select_all(self, folder: str, on: bool):
        def go(w):
            for f in self._data(w)["items"]:
                if f.get("folder") == folder:
                    if on:
                        _set_ui(w, f"files.selected.{f['id']}", True)
                    else:
                        _clear_ui(w, f"files.selected.{f['id']}")
        return go

    def _open_file(self, fid: str):
        def go(w):
            f = self._file(w, fid)
            _clear_ui(w, "files.detail")
            _set_ui(w, "files.detail.name", (f or {}).get("name", ""))
            w.push("files", "file", id=fid)
        return go

    # ------------------------------------------------------------ screens
    def screen_folder(self, world, folder: str = "Documents") -> Screen:
        files = sorted((f for f in self._data(world)["items"] if f.get("folder") == folder), key=_name_key)
        n_sel = sum(1 for f in files if self._selected(world, f["id"]))
        rows = [El(key=f"files.list.row.{f['id']}", role="row", name=_clip(f.get("name", "")),
                   value=f"{size_label(f.get('size_kb'))} · {date_label(f.get('modified', ''))}",
                   on_click=self._open_file(f["id"]), cleared=True, children=[
                       checkbox(f"files.list.select.{f['id']}", f"Select {_clip(f.get('name', ''))}",
                                f"ui:files.selected.{f['id']}", cleared=True)])
                for f in files]
        count = f"{len(files)} item" + ("" if len(files) == 1 else "s") + (f", {n_sel} selected" if n_sel else "")
        els = [self._sidebar(world, folder), heading("files.folder.title", folder), text("files.folder.count", count),
               button("files.folder.select_all", "Select all", on_click=self._select_all(folder, True),
                      disabled=not files),
               button("files.folder.select_none", "Clear selection", on_click=self._select_all(folder, False),
                      disabled=not n_sel),
               button("files.folder.trash", "Move to Trash", on_click=self._trash_selected(folder),
                      irreversible="delete", disabled=not n_sel)]
        els.append(El(key="files.list", role="list", name="Files", children=rows) if rows
                   else text("files.folder.empty", "This folder is empty."))
        return Screen(folder, els)

    def screen_file(self, world, id: str) -> Screen:
        f = self._file(world, id)
        back = button("files.file.back", "Back", on_click=lambda w: w.pop("files"))
        if f is None:
            return Screen("File", [back, text("files.file.gone", "This file was moved to the Trash or deleted.")])
        fid = f["id"]

        def save(w):
            raw = " ".join(str(_ui(w, "files.detail.name") or "").split())
            cur = self._file(w, fid)
            err = None
            if cur is None:
                return None
            if not raw:
                err = "The name cannot be empty."
            elif "/" in raw or ":" in raw:
                err = "A name cannot contain / or :."
            elif any(x.get("name") == raw and x.get("folder") == cur.get("folder") and x["id"] != fid
                     for x in self._data(w)["items"]):
                err = f"A file named {_clip(raw, 60)} already exists in {cur.get('folder')}."
            if err:
                _set_ui(w, "files.detail.error", err)
                return None
            cur["name"] = raw
            cur["kind"] = kind_for(raw) if ext(raw) else cur.get("kind", "Document")
            _clear_ui(w, "files.detail")
            w.pop("files")
            return "renamed"

        def move(w, label):
            cur = self._file(w, fid)
            if cur is None or label not in FOLDERS or label == cur.get("folder"):
                return None
            cur["folder"] = label
            _clear_ui(w, "files.detail")
            _clear_ui(w, f"files.selected.{fid}")
            w.pop("files")
            return f"moved to {label}"

        def to_trash(w):
            d = self._data(w)
            cur = self._file(w, fid)
            if cur is not None:
                d["items"] = [x for x in d["items"] if x["id"] != fid]
                d["trash"].append(cur)
            _clear_ui(w, "files.detail")
            _clear_ui(w, f"files.selected.{fid}")
            w.pop("files")
            return "moved to Trash"

        def delete_now(w):
            d = self._data(w)
            d["items"] = [x for x in d["items"] if x["id"] != fid]
            _clear_ui(w, "files.detail")
            _clear_ui(w, f"files.selected.{fid}")
            w.pop("files")
            return "deleted"

        els = [back, heading("files.file.title", _clip(f.get("name", ""))),
               text("files.file.kind", f"Kind: {f.get('kind', '')}"),
               text("files.file.size", f"Size: {size_label(f.get('size_kb'))}"),
               text("files.file.modified", f"Modified: {date_label(f.get('modified', ''))}"),
               text("files.file.folder", f"Folder: {f.get('folder', '')}")]
        err = _ui(world, "files.detail.error")
        if err:
            els.append(text("files.file.error", f"Error: {err}"))
        others = [x for x in FOLDERS if x != f.get("folder")]
        els += [textbox("files.file.rename", "Rename", "ui:files.detail.name", on_submit=save),
                button("files.file.save", "Save", on_click=save),
                popup("files.file.move", "Move to", "ui:files.detail.move", others, value="Choose a folder",
                      on_select=move),
                button("files.file.trash", "Move to Trash", on_click=to_trash, irreversible="delete"),
                button("files.file.delete_now", "Delete immediately", on_click=delete_now,
                       irreversible="permanent_delete")]
        return Screen(_clip(f.get("name", "File"), 80), els)

    def screen_trash(self, world) -> Screen:
        d = self._data(world)
        files = sorted(d["trash"], key=_name_key)

        def put_back(fid: str):
            def go(w):
                dd = self._data(w)
                cur = next((x for x in dd["trash"] if x.get("id") == fid), None)
                if cur is None:
                    return None
                dd["trash"] = [x for x in dd["trash"] if x is not cur]
                if cur.get("folder") not in FOLDERS:
                    cur["folder"] = "Documents"
                dd["items"].append(cur)
                return f"put back in {cur['folder']}"
            return go

        def empty(w):
            dd = self._data(w)
            n = len(dd["trash"])
            dd["trash"] = []
            return f"{n} item{'s' if n != 1 else ''} deleted"

        rows = [El(key=f"files.trash.row.{f['id']}", role="row", name=_clip(f.get("name", "")),
                   value=f"{size_label(f.get('size_kb'))} · from {f.get('folder', '')}", cleared=True, children=[
                       button(f"files.trash.put_back.{f['id']}", "Put back", on_click=put_back(f["id"]))])
                for f in files]
        count = f"{len(files)} item" + ("" if len(files) == 1 else "s")
        els = [self._sidebar(world, "Trash"), heading("files.trash.title", "Trash"), text("files.trash.count", count),
               button("files.trash.empty", "Empty Trash", on_click=empty, irreversible="permanent_delete",
                      disabled=not files)]
        els.append(El(key="files.trash.list", role="list", name="Trash", children=rows) if rows
                   else text("files.trash.none", "The Trash is empty."))
        return Screen("Trash", els)


APP = Files()
