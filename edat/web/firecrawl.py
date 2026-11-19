"""A small key-safe Firecrawl v2 client, and an offline stand-in (spec 15 §6.1).

Written from the public API description only. It does two things: `search`
(`POST /v2/search`, optionally with each result's Markdown) and `scrape`
(`POST /v2/scrape`). It talks only to https://api.firecrawl.dev or to a
loopback self-hosted instance.

The API key is read at request time (an environment variable, else the macOS
keychain when a service name is given). It is never stored on the client and
never appears in `repr`, errors, logs or the `.requests` record, which keeps
endpoints and bodies but no headers.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

CLOUD_URL = "https://api.firecrawl.dev"
DEFAULT_KEY_ENV = "FIRECRAWL_API_KEY"
CONVERTER = "firecrawl-v2-markdown"
ERROR_KINDS = ("auth", "payment", "rate_limit", "server", "transport", "request", "config")

_LOOPBACK = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\]):\d{1,5}$")


class FirecrawlError(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown error kind {kind!r}")
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


@dataclass
class SearchHit:
    url: str
    title: str
    description: str
    markdown: str | None
    status: int = 200
    published: str | None = None


@dataclass
class ScrapedPage:
    url: str
    title: str
    description: str
    markdown: str
    status: int = 200
    published: str | None = None


class SearchScrapeService(Protocol):
    name: str
    source: str       # firecrawl-cloud | firecrawl-self-host | stand-in
    converter: str

    def search(self, query: str, *, limit: int = 5, country: str = "US", tbs: str | None = None,
               with_markdown: bool = True) -> list[SearchHit]: ...

    def scrape(self, url: str) -> ScrapedPage: ...


def estimate_credits(searches: list[int], scrapes: int, *, with_markdown: bool = True) -> int:
    """Search: 2 credits per 10 results (rounded up), plus 1 per result when
    Markdown is scraped with it. Scrape: 1 per page."""
    total = int(scrapes)
    for limit in searches:
        total += 2 * math.ceil(limit / 10) + (limit if with_markdown else 0)
    return total


def parse_date(value: Any) -> str | None:
    """YYYY-MM-DD from a metadata date string, or None."""
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})", str(value or ""))
    if not m:
        return None
    try:
        date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return m.group(1)


# --------------------------------------------------------------------- client
Transport = Callable[[str, str, dict, dict | None, float], tuple[int, dict]]


def _httpx_transport(method: str, url: str, headers: dict, json_body: dict | None,
                     timeout: float) -> tuple[int, dict]:
    import httpx

    try:
        client = httpx.Client(timeout=timeout, trust_env=False)
        try:
            resp = client.request(method, url, headers=headers, json=json_body)
        finally:
            client.close()
    except Exception as exc:
        raise FirecrawlError("transport", f"{type(exc).__name__} talking to {urlsplit(url).netloc}") from None
    try:
        body = resp.json()
    except ValueError:
        body = {"error": resp.text[:300]}
    return resp.status_code, body if isinstance(body, dict) else {"data": body}


def _read_keychain(service: str) -> str | None:
    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    key = out.stdout.strip()
    return key if out.returncode == 0 and key else None


class FirecrawlClient:
    name = "firecrawl"
    converter = CONVERTER

    def __init__(self, base_url: str = CLOUD_URL, *, key_env: str = DEFAULT_KEY_ENV,
                 keychain_service: str | None = None, transport: Transport | None = None,
                 timeout_s: float = 60.0, min_interval_s: float = 6.5,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 key_reader: Callable[[str], str | None] = _read_keychain) -> None:
        url = str(base_url).rstrip("/")
        if url == CLOUD_URL:
            self.source = "firecrawl-cloud"
        elif _LOOPBACK.match(url):
            self.source = "firecrawl-self-host"
        else:
            raise FirecrawlError("config", f"base URL must be {CLOUD_URL} or a loopback address with a port")
        self.base_url = url
        self.key_env = key_env
        self.keychain_service = keychain_service
        self.transport = transport or _httpx_transport
        self.timeout_s = timeout_s
        self.min_interval_s = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._key_reader = key_reader
        self._last: float | None = None
        self.requests: list[dict] = []
        self.credits_used = 0

    def __repr__(self) -> str:
        return f"FirecrawlClient(base_url={self.base_url!r}, source={self.source!r})"

    # ----------------------------------------------------------------- internals
    def _key(self) -> str | None:
        key = os.environ.get(self.key_env, "")
        if not key and self.keychain_service:
            key = self._key_reader(self.keychain_service) or ""
        if not key and self.source == "firecrawl-cloud":
            raise FirecrawlError("config", f"set {self.key_env} (read at request time, never stored)")
        return key or None

    def _wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval_s - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()

    def _post(self, endpoint: str, body: dict) -> dict:
        key = self._key()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._wait()
        self.requests.append({"endpoint": endpoint, "body": body})
        try:
            status, data = self.transport("POST", self.base_url + endpoint, headers, body, self.timeout_s)
        except FirecrawlError:
            raise
        except Exception as exc:
            raise FirecrawlError("transport", type(exc).__name__) from None
        finally:
            del headers, key
        if not isinstance(data, dict):
            data = {}
        if status >= 400:
            raise FirecrawlError(self._kind(status), self._detail(status, data))
        if data.get("success") is False:
            raise FirecrawlError("request", self._detail(status, data))
        if isinstance(data.get("creditsUsed"), int):
            self.credits_used += data["creditsUsed"]
        return data

    @staticmethod
    def _kind(status: int) -> str:
        if status in (401, 403):
            return "auth"
        if status == 402:
            return "payment"
        if status == 429:
            return "rate_limit"
        return "server" if status >= 500 else "request"

    def _detail(self, status: int, data: dict) -> str:
        msg = data.get("error") or data.get("message") or ""
        text = f"HTTP {status}" + (f": {str(msg)[:200]}" if msg else "")
        key = os.environ.get(self.key_env, "")
        return text.replace(key, "[redacted]") if key else text

    # ----------------------------------------------------------------- public
    def search(self, query: str, *, limit: int = 5, country: str = "US", tbs: str | None = None,
               with_markdown: bool = True) -> list[SearchHit]:
        if not 1 <= int(limit) <= 100:
            raise FirecrawlError("request", "limit must be 1-100")
        body: dict[str, Any] = {"query": str(query)[:500], "limit": int(limit), "country": country}
        if tbs:
            body["tbs"] = tbs
        if with_markdown:
            body["scrapeOptions"] = {"formats": ["markdown"], "onlyMainContent": True, "maxAge": 0}
        data = self._post("/v2/search", body).get("data")
        items = data.get("web", []) if isinstance(data, dict) else (data or [])
        hits = []
        for item in items:
            if not isinstance(item, dict):
                continue
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            url = item.get("url") or meta.get("url") or meta.get("sourceURL") or ""
            if not url:
                continue
            md = item.get("markdown")
            hits.append(SearchHit(
                url=url, title=item.get("title") or meta.get("title") or "",
                description=item.get("description") or meta.get("description") or "",
                markdown=md if isinstance(md, str) else None,
                status=int(meta.get("statusCode") or 200),
                published=parse_date(meta.get("publishedTime") or meta.get("article:published_time")
                                     or meta.get("publishedDate"))))
        return hits

    def scrape(self, url: str) -> ScrapedPage:
        body = {"url": url, "formats": ["markdown"], "onlyMainContent": True, "maxAge": 0}
        data = self._post("/v2/scrape", body).get("data")
        data = data if isinstance(data, dict) else {}
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return ScrapedPage(
            url=meta.get("url") or meta.get("sourceURL") or url,
            title=meta.get("title") or data.get("title") or "",
            description=meta.get("description") or "",
            markdown=data.get("markdown") if isinstance(data.get("markdown"), str) else "",
            status=int(meta.get("statusCode") or 200),
            published=parse_date(meta.get("publishedTime") or meta.get("article:published_time")
                                 or meta.get("publishedDate")))


# -------------------------------------------------------------------- stand-in
class StandInFirecrawl:
    """An offline service for tests and dry runs: no network, no key."""
    name = "stand-in"
    source = "stand-in"
    converter = "stand-in"

    def __init__(self, pages: dict[str, dict | ScrapedPage], *,
                 results: dict[str, list[str]] | None = None) -> None:
        self.pages: dict[str, ScrapedPage] = {}
        for url, p in pages.items():
            if isinstance(p, ScrapedPage):
                self.pages[url] = p
            else:
                self.pages[url] = ScrapedPage(url=url, title=p.get("title", ""),
                                              description=p.get("description", ""),
                                              markdown=p.get("markdown", ""), status=int(p.get("status", 200)),
                                              published=p.get("published"))
        self.results = results
        self.requests: list[dict] = []
        self.credits_used = 0

    def _hit(self, p: ScrapedPage, with_markdown: bool) -> SearchHit:
        return SearchHit(p.url, p.title, p.description, p.markdown if with_markdown else None,
                         p.status, p.published)

    def search(self, query: str, *, limit: int = 5, country: str = "US", tbs: str | None = None,
               with_markdown: bool = True) -> list[SearchHit]:
        self.requests.append({"endpoint": "/v2/search", "body": {"query": query, "limit": limit}})
        self.credits_used += estimate_credits([limit], 0, with_markdown=with_markdown)
        if self.results is not None and query in self.results:
            urls = [u for u in self.results[query] if u in self.pages]
        else:
            from edat.web.rank import BM25Index, Doc
            idx = BM25Index(Doc(id=u, title=p.title, description=p.description, text=p.markdown)
                            for u, p in sorted(self.pages.items()))
            urls = [u for u, _ in idx.search(query, limit)]
        return [self._hit(self.pages[u], with_markdown) for u in urls[:limit]]

    def scrape(self, url: str) -> ScrapedPage:
        self.requests.append({"endpoint": "/v2/scrape", "body": {"url": url}})
        self.credits_used += estimate_credits([], 1)
        p = self.pages.get(url)
        return p if p is not None else ScrapedPage(url=url, title="", description="", markdown="", status=404)
