"""The frozen web snapshot: format, normalisation, content hashing, storage.

A snapshot (`edat-web-snapshot/v1`, spec 15 §3.1) is what every model reads:

* `queries`: invented queries, the parameters sent, and the ordered results
  the search service returned (`url`, `title`, `description`);
* `pages`: each page's Markdown after one fixed normalisation, stored
  content-addressed by SHA-256, with its title, description and date;
* `corpora`: named sets of page keys a case searches over;
* `today`: the date the model is told it is (the capture date).

The snapshot id is the SHA-256 of the canonical JSON of everything a model
can be shown, so two snapshots with the same id serve the same bytes.

On disk:

    <dir>/snapshot.json        the full manifest (titles, descriptions: private for captured pages)
    <dir>/pages/<sha256>.md    normalised page text

`public_manifest()` is the part that may be committed for a captured snapshot:
ids, URLs, hashes and sizes, and no third-party text.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

SNAPSHOT_SCHEMA = "edat-web-snapshot/v1"
PUBLIC_MANIFEST_SCHEMA = "edat-web-snapshot-public/v1"
NORMALIZER = "edat-web-normalize/v1"
SOURCES = ("firecrawl-cloud", "firecrawl-self-host", "synthetic", "stand-in")

_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_BLANKS = re.compile(r"\n{3,}")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SnapshotError(ValueError):
    pass


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_markdown(md: str) -> str:
    """The one normalisation every page gets before hashing (spec 15 §3.3).

    Line endings become `\\n`, trailing spaces go, runs of blank lines collapse
    to one, images become `[image: alt]`, links stay `[text](url)`. Unicode is
    put in NFC form; typographic quotes are kept as the page has them.
    """
    text = unicodedata.normalize("NFC", str(md or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _IMAGE.sub(lambda m: f"[image: {m.group(1).strip()}]" if m.group(1).strip() else "", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _BLANKS.sub("\n\n", text)
    return text.strip("\n") + "\n" if text.strip() else ""


def url_key(url: str) -> str:
    """The lookup key for a URL: scheme dropped, host lower-cased without
    `www.`, fragment dropped, trailing slash dropped, query kept."""
    raw = str(url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port else ""
    path = parts.path.rstrip("/")
    key = host + port + path
    if parts.query:
        key += "?" + parts.query
    return key


def host_of(url: str) -> str:
    return url_key(url).split("/", 1)[0].split("?", 1)[0]


def clean_url(url: str) -> str:
    """A URL as shown to models: https scheme kept or added, fragment dropped."""
    raw = str(url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    p = urlsplit(raw)
    return urlunsplit((p.scheme.lower() or "https", p.netloc.lower(), p.path or "/", p.query, ""))


@dataclass
class Page:
    url: str
    title: str
    text: str                      # normalised Markdown
    description: str = ""
    published: str | None = None   # YYYY-MM-DD, when the page states one
    captured_at: str | None = None
    status: int = 200
    sha256: str = ""

    def __post_init__(self) -> None:
        self.url = clean_url(self.url)
        self.text = normalize_markdown(self.text)
        self.title = " ".join(str(self.title or "").split())[:300]
        self.description = " ".join(str(self.description or "").split())[:500]
        if self.published is not None and not _DATE.match(str(self.published)):
            raise SnapshotError(f"published must be YYYY-MM-DD, got {self.published!r}")
        self.sha256 = sha256_text(self.text)

    @property
    def key(self) -> str:
        return url_key(self.url)

    def served(self) -> dict:
        """Everything a model can be shown about this page (hashed into the id)."""
        return {"url": self.url, "title": self.title, "description": self.description,
                "published": self.published, "sha256": self.sha256, "status": self.status}


@dataclass
class QueryRecord:
    qid: str
    query: str
    params: dict = field(default_factory=dict)
    results: list[dict] = field(default_factory=list)   # [{rank, url, title, description}]

    def to_record(self) -> dict:
        return {"qid": self.qid, "query": self.query, "params": dict(self.params),
                "results": [{"rank": int(r.get("rank", i + 1)), "url": clean_url(r["url"]),
                             "title": " ".join(str(r.get("title") or "").split())[:300],
                             "description": " ".join(str(r.get("description") or "").split())[:500]}
                            for i, r in enumerate(self.results)]}


@dataclass
class Snapshot:
    today: str
    source: str
    converter: str
    pages: dict[str, Page] = field(default_factory=dict)          # url_key -> Page
    queries: dict[str, QueryRecord] = field(default_factory=dict)
    corpora: dict[str, list[str]] = field(default_factory=dict)   # name -> [url_key]
    meta: dict = field(default_factory=dict)                      # not hashed: capture notes, drops

    def __post_init__(self) -> None:
        if not _DATE.match(self.today):
            raise SnapshotError(f"today must be YYYY-MM-DD, got {self.today!r}")
        if self.source not in SOURCES:
            raise SnapshotError(f"unknown source {self.source!r}; known: {SOURCES}")

    # ------------------------------------------------------------- building
    @classmethod
    def build(cls, pages: Iterable[Page], *, today: str, source: str, converter: str,
              queries: Iterable[QueryRecord] = (), corpora: dict[str, list[str]] | None = None,
              meta: dict | None = None) -> "Snapshot":
        snap = cls(today=today, source=source, converter=converter, meta=dict(meta or {}))
        for p in pages:
            snap.add_page(p)
        for q in queries:
            snap.queries[q.qid] = q
        for name, keys in (corpora or {}).items():
            snap.set_corpus(name, keys)
        return snap

    def add_page(self, page: Page) -> None:
        old = self.pages.get(page.key)
        if old is not None and old.sha256 != page.sha256:
            raise SnapshotError(f"two different pages for {page.key}")
        self.pages[page.key] = page

    def set_corpus(self, name: str, keys: Iterable[str]) -> None:
        ks = sorted({url_key(k) for k in keys})
        missing = [k for k in ks if k not in self.pages]
        if missing:
            raise SnapshotError(f"corpus {name!r} names pages not in the snapshot: {missing[:5]}")
        self.corpora[name] = ks

    # -------------------------------------------------------------- reading
    def page(self, url: str) -> Page | None:
        return self.pages.get(url_key(url))

    def corpus(self, name: str | None) -> list[Page]:
        if name is None:
            return [self.pages[k] for k in sorted(self.pages)]
        try:
            return [self.pages[k] for k in self.corpora[name]]
        except KeyError:
            raise SnapshotError(f"no corpus named {name!r}") from None

    def captured_rank(self) -> dict[str, int]:
        """Best rank each page got in any recorded query (ties in the ranker)."""
        best: dict[str, int] = {}
        for q in self.queries.values():
            for i, r in enumerate(q.to_record()["results"]):
                k = url_key(r["url"])
                best[k] = min(best.get(k, 10**6), int(r.get("rank") or i + 1))
        return best

    # ------------------------------------------------------------- identity
    def content_index(self) -> dict:
        return {"schema": SNAPSHOT_SCHEMA, "normalizer": NORMALIZER, "today": self.today,
                "source": self.source, "converter": self.converter,
                "pages": {k: self.pages[k].served() for k in sorted(self.pages)},
                "queries": {q: self.queries[q].to_record() for q in sorted(self.queries)},
                "corpora": {c: list(self.corpora[c]) for c in sorted(self.corpora)}}

    @property
    def id(self) -> str:
        return hashlib.sha256(canonical_json(self.content_index())).hexdigest()

    def public_manifest(self) -> dict:
        """Ids, URLs, hashes and sizes only: committable for a captured snapshot."""
        return {"schema": PUBLIC_MANIFEST_SCHEMA, "snapshot_id": self.id, "today": self.today,
                "source": self.source, "converter": self.converter, "normalizer": NORMALIZER,
                "pages": [{"url": self.pages[k].url, "sha256": self.pages[k].sha256,
                           "bytes": len(self.pages[k].text.encode()), "status": self.pages[k].status}
                          for k in sorted(self.pages)],
                "queries": [{"qid": q, "result_urls": [r["url"] for r in self.queries[q].to_record()["results"]]}
                            for q in sorted(self.queries)],
                "corpora": {c: list(self.corpora[c]) for c in sorted(self.corpora)},
                "dropped_cases": list(self.meta.get("dropped_cases") or [])}

    # -------------------------------------------------------------- storage
    def save(self, directory: Path | str) -> Path:
        d = Path(directory)
        (d / "pages").mkdir(parents=True, exist_ok=True)
        for p in self.pages.values():
            target = d / "pages" / f"{p.sha256}.md"
            if not target.exists():
                target.write_text(p.text, encoding="utf-8")
        manifest = {**self.content_index(), "id": self.id, "meta": self.meta,
                    "page_extra": {k: {"captured_at": p.captured_at} for k, p in sorted(self.pages.items())}}
        (d / "snapshot.json").write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
        return d

    @classmethod
    def load(cls, directory: Path | str, *, verify: bool = True) -> "Snapshot":
        d = Path(directory)
        try:
            m = json.loads((d / "snapshot.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SnapshotError(f"no snapshot.json in {d}") from None
        if m.get("schema") != SNAPSHOT_SCHEMA:
            raise SnapshotError(f"{d}: schema {m.get('schema')!r}, expected {SNAPSHOT_SCHEMA}")
        if m.get("normalizer") != NORMALIZER:
            raise SnapshotError(f"{d}: normaliser {m.get('normalizer')!r}, this harness has {NORMALIZER}")
        extra = m.get("page_extra") or {}
        pages = []
        for k, info in m["pages"].items():
            text = (d / "pages" / f"{info['sha256']}.md").read_text(encoding="utf-8")
            p = Page(url=info["url"], title=info["title"], text=text, description=info.get("description", ""),
                     published=info.get("published"), status=int(info.get("status", 200)),
                     captured_at=(extra.get(k) or {}).get("captured_at"))
            if verify and p.sha256 != info["sha256"]:
                raise SnapshotError(f"{d}: page {k} does not match its hash")
            pages.append(p)
        queries = [QueryRecord(q["qid"], q["query"], q.get("params") or {}, q.get("results") or [])
                   for q in m.get("queries", {}).values()]
        snap = cls.build(pages, today=m["today"], source=m["source"], converter=m["converter"],
                         queries=queries, corpora=m.get("corpora") or {}, meta=m.get("meta") or {})
        if verify and m.get("id") != snap.id:
            raise SnapshotError(f"{d}: snapshot id {m.get('id')} does not match its content ({snap.id})")
        return snap
