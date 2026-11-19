"""The assistant lane: Harbor's own web tools backed by the frozen snapshot (spec 15 §3.5).

`AssistantWebEnvironment` is the agentic build's `AssistantEnvironment`
(Harbor's tool names and schemas over Toolbench's fakes) with Harbor's web
tools answered from the snapshot instead of Toolbench's canned pages:

* `web_search(query, limit)`: the snapshot's ranked hits (edat/web/rank.py,
  the same ranking as the neutral lane), rendered as Harbor's
  `render_search_results` blocks (`[i] title\\nURL: url\\nsnippet`), or
  "(no web results found)". Harbor's local loop shows this display text to the
  model (service/agent/loop.py:2545-2551).
* `web_fetch(url)`: `Title:` / `URL:` / `Published:`, then the page fenced
  with Harbor's own `fence(text, WEB_PAGE)` and cut at Harbor's 10,500
  characters (service/tools/web_tools.py:2825-2841). A page not in the
  snapshot answers as Harbor's failed fetch does.
* `wikipedia_summary`: not available in this test world.

Everything else (mail, messages, the approver, Harbor's confirmation rules)
is the assistant lane as the agentic build defines it. Because
`edat/cases/harbor_loop.py` routes every Harbor tool through the trial's
environment, the same environment serves Harbor's own loop: no live search
code runs, and Toolbench's loopback-only network guard stays on.
"""
from __future__ import annotations

from edat.state.assistant_lane import AssistantEnvironment
from edat.state.clock import Clock
from edat.state.env import ToolResult
from edat.web import rank
from edat.web.snapshot import Snapshot, clean_url, url_key
from edat.web.tools import WebEnvironment

HARBOR_FETCH_CHARS = 10500
HARBOR_TITLE_CHARS = 300
BRIDGE = "edat-web-harbor-bridge/v1"


def render_search(pages) -> str:
    if not pages:
        return "(no web results found)"
    return "\n\n".join(f"[{i}] {p.title}\nURL: {p.url}\n{rank.snippet(p.description, p.text)}"
                       for i, p in enumerate(pages, 1))


def render_fetch(page) -> str:
    from service.tools.untrusted import WEB_PAGE, fence, neutralize
    title = " ".join(str(page.title).split())[:HARBOR_TITLE_CHARS]
    header = f"Title: {title}\nURL: {page.url}"
    if page.published:
        header += f"\nPublished: {page.published}"
    body, note = page.text, ""
    if len(body) > HARBOR_FETCH_CHARS:
        body, note = body[:HARBOR_FETCH_CHARS], f"\n…[truncated {len(page.text) - HARBOR_FETCH_CHARS} characters]"
    return f"{neutralize(header)}\n\n{fence(body, WEB_PAGE)}{note}"


class AssistantWebEnvironment(AssistantEnvironment):
    """Harbor's tools, with web_search / web_fetch served from a snapshot."""

    def __init__(self, snapshot: Snapshot, corpus: str | None, clock: Clock | None = None, *,
                 world_seed: int | None = None) -> None:
        super().__init__(clock or Clock.parse(f"{snapshot.today}T09:00"), world_seed=world_seed)
        self.snapshot = snapshot
        self.web = WebEnvironment(snapshot, corpus)

    @property
    def log(self) -> list[dict]:
        return self.web.log

    def call(self, name: str, arguments: dict) -> ToolResult:
        args = dict(arguments or {})
        if name == "web_search":
            q = str(args.get("query") or "").strip()
            try:
                limit = max(1, min(int(args.get("limit") or 6), 10))
            except (TypeError, ValueError):
                limit = 6
            pages = self.web.search_pages(q, limit=limit) if q else []
            self.web.log.append({"name": name, "arguments": args, "result_keys": [p.key for p in pages],
                                 "outcome": "succeeded" if pages else "no_match"})
            text = render_search(pages)
            return ToolResult(text, outcome="succeeded" if pages else "no_match", meta={"raw_result": text})
        if name == "web_fetch":
            url = str(args.get("url") or "").strip()
            page = self.snapshot.page(url) if url else None
            entry = {"name": name, "arguments": args, "key": url_key(url) if url else ""}
            if page is None:
                entry["outcome"] = "no_match"
                self.web.log.append(entry)
                text = (f"(fetch failed: HTTPStatusError: Client error '404 Not Found' for url "
                        f"'{clean_url(url)}')")
                return ToolResult(text, outcome="no_match", meta={"raw_result": text})
            text = render_fetch(page)
            entry.update(outcome="succeeded", chars=min(len(page.text), HARBOR_FETCH_CHARS))
            self.web.log.append(entry)
            return ToolResult(text, meta={"raw_result": text})
        if name == "wikipedia_summary":
            text = "(error running wikipedia_summary: not available in this test world)"
            return ToolResult(text, outcome="failed", meta={"raw_result": text})
        return super().call(name, args)
