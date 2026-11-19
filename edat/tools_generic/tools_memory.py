"""Long-term memory (collection `memory`) and the web (collection `web`).

Memory rows carry `status`: active, superseded or forgotten. A correction
(`memory_update`, or `memory_save` with `supersedes_id`) writes a new active
row with `supersedes_id` and marks the old row superseded. `memory_delete`
marks the row forgotten, together with every row it superseded, so a forgotten
fact's correction history goes with it. Forgotten rows are never returned.

The web is the seeded `web` collection only: `web_search` matches seeded
pages, `web_fetch` returns a seeded page or a not-found error. Nothing touches
the network.
"""
from __future__ import annotations

from edat.state.store import StateStore
from edat.tools_generic.registry import (
    PAGING, TEXT_WINDOW, B, S, ToolError, arg_bool, arg_str, matches, need_str, not_found, page, tool, window,
)

_MEM_ID = S("The memory id, from memory_search or memory_list.")


def _mem_view(r: dict) -> dict:
    out = {"id": r["id"], "text": r.get("text"), "category": r.get("category"), "status": r.get("status", "active")}
    if r.get("supersedes_id"):
        out["supersedes_id"] = r["supersedes_id"]
    return out


def _active(store: StateStore, mid: str) -> dict:
    r = store.find("memory", mid)
    if r is None or r.get("status", "active") == "forgotten":
        raise not_found("memory", mid)
    if r.get("status", "active") != "active":
        raise ToolError(f"memory {mid!r} is {r.get('status')}; use the current fact's id")
    return r


def _save(store: StateStore, text: str, category: str | None, supersedes: str | None) -> dict:
    old = _active(store, supersedes) if supersedes else None
    row = {"text": text, "category": category or (old.get("category") if old else None) or "fact",
           "status": "active", "saved_at": store.clock.iso()}
    if old is not None:
        row["supersedes_id"] = old["id"]
    new = store.add("memory", row, prefix="mem")
    if old is not None:
        store.update("memory", old["id"], status="superseded", superseded_by=new["id"])
    return {"ok": True, "memory": _mem_view(new), **({"superseded": old["id"]} if old is not None else {})}


@tool("memory_save", "memory", "Save a fact about the user to long-term memory, for use in later conversations. "
      "To correct a fact already saved, pass its id as supersedes_id (or use memory_update).",
      {"text": S("The fact, stated exactly, with its qualifiers."),
       "category": S("Kind of fact, e.g. preference, routine, person, fact."),
       "supersedes_id": S("Id of an existing memory this one replaces.")},
      ("text",), effect=True)
def memory_save(store: StateStore, args: dict) -> dict:
    return _save(store, need_str(args, "text"), arg_str(args, "category"), arg_str(args, "supersedes_id"))


@tool("memory_update", "memory", "Correct a saved memory: the old fact is kept as superseded and the new text becomes current.",
      {"memory_id": _MEM_ID, "text": S("The corrected fact."), "category": S("New category (default: unchanged).")},
      ("memory_id", "text"), effect=True)
def memory_update(store: StateStore, args: dict) -> dict:
    return _save(store, need_str(args, "text"), arg_str(args, "category"), need_str(args, "memory_id"))


@tool("memory_search", "memory", "Search long-term memory by words in the fact or its category. Returns current facts only, "
      "unless include_superseded is true.",
      {"query": S("Words to look for."), "include_superseded": B("Also return facts that were corrected (default false)."),
       **PAGING}, ("query",))
def memory_search(store: StateStore, args: dict) -> dict:
    old_too = arg_bool(args, "include_superseded", False)
    rows = [_mem_view(r) for r in store.get("memory")
            if matches(args["query"], r, ("text", "category"))
            and (r.get("status", "active") == "active" or (old_too and r.get("status") == "superseded"))]
    return page(rows, args)


@tool("memory_list", "memory", "List current facts in long-term memory.",
      {"category": S("Only this category."), **PAGING})
def memory_list(store: StateStore, args: dict) -> dict:
    cat = arg_str(args, "category")
    rows = [_mem_view(r) for r in store.get("memory") if r.get("status", "active") == "active"
            and (not cat or str(r.get("category", "")).lower() == cat.lower())]
    return page(rows, args)


@tool("memory_delete", "memory", "Forget a saved fact, including the earlier versions it corrected.",
      {"memory_id": _MEM_ID}, ("memory_id",), effect=True)
def memory_delete(store: StateStore, args: dict) -> dict:
    r = store.find("memory", args["memory_id"])
    if r is None or r.get("status", "active") == "forgotten":
        raise not_found("memory", args["memory_id"])
    chain, seen = [], set()
    while r is not None and r["id"] not in seen:
        seen.add(r["id"])
        if r.get("status", "active") != "forgotten":
            chain.append(r["id"])
        r = store.find("memory", r["supersedes_id"]) if r.get("supersedes_id") else None
    for mid in chain:
        store.update("memory", mid, status="forgotten", forgotten_at=store.clock.iso())
    return {"ok": True, "forgotten": chain}


# ================================================================== web
def _url_key(u: str) -> str:
    u = u.strip().lower()
    for pre in ("https://", "http://"):
        if u.startswith(pre):
            u = u[len(pre):]
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


@tool("web_search", "web", "Search the web. Returns page titles, URLs and snippets.",
      {"query": S("Search terms."), "site": S("Only results from this domain, e.g. example.org."), **PAGING},
      ("query",), egress=True)
def web_search(store: StateStore, args: dict) -> dict:
    q = need_str(args, "query")
    site = _url_key(arg_str(args, "site") or "")
    rows = [{"url": p.get("url") or p.get("id"), "title": p.get("title"), "snippet": str(p.get("text") or "")[:200]}
            for p in store.get("web") if matches(q, p, ("title", "text", "url"))
            and (not site or _url_key(str(p.get("url") or p.get("id") or "")).split("/")[0].endswith(site))]
    return page(rows, args)


@tool("web_fetch", "web", "Fetch a web page's text by URL. Long pages can be read in parts with offset and limit.",
      {"url": S("The page URL."), **TEXT_WINDOW}, ("url",), egress=True)
def web_fetch(store: StateStore, args: dict) -> dict:
    url = need_str(args, "url")
    key = _url_key(url)
    for p in store.get("web"):
        if _url_key(str(p.get("url") or p.get("id") or "")) == key:
            return {"url": p.get("url") or p.get("id"), "title": p.get("title"), **window(str(p.get("text") or ""), args)}
    raise ToolError(f"could not fetch {url}: page not found (404)", outcome="no_match")
