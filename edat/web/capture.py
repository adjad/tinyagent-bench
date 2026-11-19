"""Capture a web snapshot from Firecrawl, once (spec 15 §6.1).

A capture file (`edat-web-capture/v1`, YAML) lists cases: invented queries
that are sent to the service, and answer patterns used afterwards to keep or
drop each case. Nothing here is sent until `run` is given `--yes`; queries and
questions are screened for personal data first.

    python -m edat.web.capture plan FILE
    python -m edat.web.capture run FILE --today YYYY-MM-DD --yes      # self-hosted stack on 127.0.0.1:3002
    python -m edat.web.capture run FILE --service cloud --today YYYY-MM-DD --yes   # hosted, needs a key
    python -m edat.web.capture check SNAPSHOT_DIR FILE
    python -m edat.web.capture public-manifest SNAPSHOT_DIR --out FILE

The main path is a self-hosted Firecrawl stack, started only for the capture
and shut down after it; the hosted service stays possible behind the same
client. Searches that come back empty (the self-hosted stack scrapes
DuckDuckGo, which can block it) and pages that fail plain scraping are
dropped and counted in the snapshot's `meta.counts`. With $EDAT_HEAVY_LOCK
naming a lock that exists, `run` refuses: the capture never runs beside a
model run or a measurement.

Exit codes: 0 ok, 1 check failures, 2 refused, 3 usage.
Captured page text is third-party content: it is saved outside the repository
(`$EDAT_PRIVATE_DIR`); only the public manifest may be committed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from edat.web.firecrawl import FirecrawlClient, FirecrawlError, SearchScrapeService, estimate_credits
from edat.web.snapshot import Page, QueryRecord, Snapshot, SnapshotError, normalize_markdown, url_key

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_SCHEMA = "edat-web-capture/v1"
FAMILIES = ("need_search", "read", "not_found", "multi_hop")
SPLITS = ("dev", "heldout")
COUNTRY = "US"


class CaptureError(ValueError):
    pass


# ------------------------------------------------------------------- loading
def _compile(pattern: str, where: str) -> re.Pattern:
    try:
        return re.compile(pattern, re.I)
    except re.error as exc:
        raise CaptureError(f"{where}: pattern {pattern!r} does not compile: {exc}") from None


def load_capture_file(path: Path | str) -> list[dict]:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CaptureError(f"cannot read {path}: {exc}") from None
    if not isinstance(doc, dict) or doc.get("schema") != CAPTURE_SCHEMA:
        raise CaptureError(f"{path}: expected schema {CAPTURE_SCHEMA}")
    raw = doc.get("cases")
    if not isinstance(raw, list) or not raw:
        raise CaptureError(f"{path}: no cases")
    cases, seen = [], set()
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            raise CaptureError(f"case {i}: not a mapping")
        cid = c.get("id")
        if not isinstance(cid, str) or not cid:
            raise CaptureError(f"case {i}: missing id")
        if cid in seen:
            raise CaptureError(f"duplicate case id {cid}")
        seen.add(cid)
        for key in ("family", "question", "queries"):
            if not c.get(key):
                raise CaptureError(f"{cid}: missing {key}")
        if c["family"] not in FAMILIES:
            raise CaptureError(f"{cid}: family {c['family']!r} not in {FAMILIES}")
        case = {"id": cid, "family": c["family"], "split": c.get("split", "dev"),
                "level": int(c.get("level", 1)), "question": str(c["question"]),
                "queries": [str(q) for q in c["queries"]], "limit": int(c.get("limit", 5)),
                "follow_links_matching": c.get("follow_links_matching"),
                "follow_limit": int(c.get("follow_limit", 3)),
                "accept": [str(p) for p in (c.get("accept") or [])],
                "reject": [str(p) for p in (c.get("reject") or [])],
                "query_terms": c.get("query_terms") or []}
        case["support"] = [str(p) for p in c["support"]] if c.get("support") else list(case["accept"])
        if case["split"] not in SPLITS:
            raise CaptureError(f"{cid}: split {case['split']!r} not in {SPLITS}")
        if not 1 <= case["limit"] <= 10:
            raise CaptureError(f"{cid}: limit must be 1-10")
        if not case["accept"] and case["family"] != "not_found":
            raise CaptureError(f"{cid}: accept is required")
        if any(len(q) > 500 for q in case["queries"]):
            raise CaptureError(f"{cid}: a query is over 500 characters")
        for field in ("accept", "reject", "support"):
            for p in case[field]:
                _compile(p, f"{cid}.{field}")
        if case["follow_links_matching"]:
            _compile(case["follow_links_matching"], f"{cid}.follow_links_matching")
        cases.append(case)
    return cases


# ------------------------------------------------------------------- privacy
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONES = (re.compile(r"(?<!\d)(?:\+?\d{1,3}[-. ])?(?:\(\d{3}\)\s?|\d{3}[-. ])\d{3}[-. ]\d{4}(?!\d)"),
           re.compile(r"(?<!\d)\d{3}[-. ]\d{4}(?!\d)"),
           re.compile(r"\+\d[\d ().-]{7,}\d"),
           re.compile(r"(?<!\d)555[-. ]?\d{3,4}(?!\d)|(?<!\d)\d{3}[-. ]555(?!\d)"))
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_USERINFO = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.I)


def privacy_screen(text: str, deny_terms: Iterable[str] = ()) -> list[str]:
    """Reasons this text must not be sent to a third party (empty = clean)."""
    s = str(text or "")
    reasons = []
    if _EMAIL.search(s):
        reasons.append("e-mail address")
    flat = _ISO_DATE.sub(" ", s)
    if any(p.search(flat) for p in _PHONES):
        reasons.append("phone-number-like digits")
    if _LONG_DIGITS.search(flat):
        reasons.append("13-19 digit number")
    if _USERINFO.search(s):
        reasons.append("URL with credentials")
    for term in deny_terms:
        t = str(term).strip()
        if t and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", s, re.I):
            reasons.append("deny term")
    return reasons


def _screen_cases(cases: list[dict], deny_terms: Iterable[str]) -> None:
    deny = list(deny_terms)
    bad = []
    for c in cases:
        for label, text in [("question", c["question"])] + [(f"query {i}", q) for i, q in enumerate(c["queries"])]:
            for reason in privacy_screen(text, deny):
                bad.append(f"{c['id']} {label}: {reason}")
    if bad:
        raise CaptureError("privacy screen failed; nothing was sent: " + "; ".join(bad))


# ---------------------------------------------------------------------- plan
def plan(cases: list[dict]) -> dict:
    limits = [c["limit"] for c in cases for _ in c["queries"]]
    scrapes = sum(c["follow_limit"] for c in cases if c.get("follow_links_matching"))
    credits = estimate_credits(limits, scrapes, with_markdown=True)
    return {"cases": len(cases), "searches": len(limits), "scrapes_estimate": scrapes,
            "self_host_credits": 0,
            "minutes_estimate": math.ceil((len(limits) + scrapes) * MIN_INTERVAL_S / 60),
            "credits_estimate": credits, "credits_with_margin": math.ceil(credits * 1.2),
            "note": "credits apply to the hosted service only; a self-hosted stack uses none"}


# ------------------------------------------------------------------- capture
_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?(https?://[^\s)>]+)")


SELF_HOST_URL = "http://127.0.0.1:3002"
MIN_INTERVAL_S = 6.5   # the client's spacing between requests (gentle on the search backend)
HEAVY_LOCK_ENV = "EDAT_HEAVY_LOCK"


def heavy_lock_held() -> str | None:
    """The capture never runs beside a model run or a measurement: when
    $EDAT_HEAVY_LOCK names a lock path that exists, the capture refuses."""
    path = os.environ.get(HEAVY_LOCK_ENV)
    return path if path and Path(path).exists() else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_capture(cases: list[dict], service: SearchScrapeService, *, today: str,
                deny_terms: Iterable[str] = (), repo_root: Path = REPO_ROOT,
                now: Callable[[], str] = _utc_now) -> Snapshot:
    _screen_cases(cases, deny_terms)
    pages: dict[str, Page] = {}
    queries: list[QueryRecord] = []
    corpora: dict[str, list[str]] = {}
    dropped_pages: list[dict] = []
    dropped_seen: set[str] = set()
    empty_queries: list[dict] = []

    def drop(url: str, reason: str) -> None:
        if url not in dropped_seen:
            dropped_seen.add(url)
            dropped_pages.append({"url": url, "reason": reason})

    def keep(url: str, title: str, description: str, markdown: str, status: int,
             published: str | None, case_keys: dict[str, str]) -> None:
        if status >= 400:
            return drop(url, f"status {status}")
        if not normalize_markdown(markdown).strip():
            return drop(url, "empty markdown")
        page = Page(url=url, title=title, text=markdown, description=description,
                    published=published, captured_at=now(), status=status)
        pages.setdefault(page.key, page)
        case_keys[page.key] = pages[page.key].text

    for case in cases:
        case_keys: dict[str, str] = {}
        for i, q in enumerate(case["queries"]):
            hits = service.search(q, limit=case["limit"], with_markdown=True)
            if not hits:   # self-hosted search returns nothing when DuckDuckGo blocks it
                empty_queries.append({"qid": f"{case['id']}:{i}", "query": q})
            queries.append(QueryRecord(
                qid=f"{case['id']}:{i}", query=q, params={"limit": case["limit"], "country": COUNTRY},
                results=[{"rank": r + 1, "url": h.url, "title": h.title, "description": h.description}
                         for r, h in enumerate(hits)]))
            for h in hits:
                if h.markdown is None:
                    try:
                        sp = service.scrape(h.url)
                    except FirecrawlError as exc:
                        if exc.kind != "request":
                            raise
                        drop(h.url, f"scrape failed: {exc.detail}")
                        continue
                    keep(sp.url or h.url, sp.title or h.title, sp.description or h.description,
                         sp.markdown, sp.status, sp.published or h.published, case_keys)
                else:
                    keep(h.url, h.title, h.description, h.markdown, h.status, h.published, case_keys)
        pattern = case.get("follow_links_matching")
        if pattern:
            rx = re.compile(pattern, re.I)
            seen = set(case_keys) | {url_key(d["url"]) for d in dropped_pages}
            todo: list[str] = []
            for text in list(case_keys.values()):
                for url in _LINK.findall(text):
                    url = url.rstrip(".,;")
                    if rx.search(url) and url_key(url) not in seen:
                        seen.add(url_key(url))
                        todo.append(url)
            for url in todo[:case["follow_limit"]]:
                try:
                    sp = service.scrape(url)
                except FirecrawlError as exc:
                    if exc.kind != "request":
                        raise
                    drop(url, f"scrape failed: {exc.detail}")
                    continue
                keep(sp.url or url, sp.title, sp.description, sp.markdown, sp.status, sp.published, case_keys)
        corpora[case["id"]] = sorted(case_keys)
    snap = Snapshot.build(pages.values(), today=today, source=service.source, converter=service.converter,
                          queries=queries, corpora=corpora,
                          meta={"dropped_pages": dropped_pages, "empty_queries": empty_queries})
    _, dropped = check(snap, cases)
    snap.meta["dropped_cases"] = dropped
    snap.meta["counts"] = {"queries": len(queries), "empty_queries": len(empty_queries),
                           "pages_kept": len(pages), "pages_dropped": len(dropped_pages),
                           "cases": len(cases), "cases_dropped": len(dropped)}
    return snap


def check(snapshot: Snapshot, cases: list[dict]) -> tuple[list[str], list[dict]]:
    """Keep a case only if its corpus has a page matching an accept pattern
    (or, with no accept patterns, any page) and no page matching a reject one."""
    kept, dropped = [], []
    for c in cases:
        try:
            texts = [p.text for p in snapshot.corpus(c["id"])]
        except SnapshotError:
            texts = []
        accept = [re.compile(p, re.I) for p in c.get("accept", [])]
        reject = [re.compile(p, re.I) for p in c.get("reject", [])]
        if not texts:
            dropped.append({"id": c["id"], "reason": "empty corpus"})
        elif any(rx.search(t) for rx in reject for t in texts):
            dropped.append({"id": c["id"], "reason": "a reject pattern appears in the corpus"})
        elif accept and not any(rx.search(t) for rx in accept for t in texts):
            dropped.append({"id": c["id"], "reason": "no accept pattern appears in the corpus"})
        else:
            kept.append(c["id"])
    return kept, dropped


# ------------------------------------------------------------------- storage
def save_private(snapshot: Snapshot, out_root: Path | None, *, repo_root: Path = REPO_ROOT) -> Path:
    if out_root is None:
        env = os.environ.get("EDAT_PRIVATE_DIR")
        if not env:
            raise CaptureError("set EDAT_PRIVATE_DIR (or pass --out): captured pages are not stored in the repository")
        out_root = Path(env) / "web" / "snapshots"
    target = (Path(out_root) / snapshot.id).resolve()
    root = Path(repo_root).resolve()
    if snapshot.source != "synthetic" and (target == root or root in target.parents):
        raise CaptureError(f"refusing to write captured pages inside the repository: {target}")
    return snapshot.save(target)


def write_public_manifest(snapshot: Snapshot, path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot.public_manifest(), indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    return p


# ----------------------------------------------------------------------- CLI
def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.web.capture")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan")
    p.add_argument("file")
    r = sub.add_parser("run")
    r.add_argument("file")
    r.add_argument("--service", choices=("self-host", "cloud"), default="self-host")
    r.add_argument("--base-url", default=SELF_HOST_URL)
    r.add_argument("--today", required=True)
    r.add_argument("--out")
    r.add_argument("--deny-file")
    r.add_argument("--manifest-out")
    r.add_argument("--yes", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("snapshot_dir")
    c.add_argument("file")
    m = sub.add_parser("public-manifest")
    m.add_argument("snapshot_dir")
    m.add_argument("--out", required=True)
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 3
    if not args.cmd:
        ap.print_usage(sys.stderr)
        return 3
    try:
        if args.cmd == "plan":
            _print(plan(load_capture_file(args.file)))
            return 0
        if args.cmd == "run":
            cases = load_capture_file(args.file)
            deny = []
            if args.deny_file:
                deny = [ln.strip() for ln in Path(args.deny_file).read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.startswith("#")]
            _screen_cases(cases, deny)
            if (held := heavy_lock_held()):
                print(f"refused: a heavy job holds the lock ({held}); capture after it finishes", file=sys.stderr)
                return 2
            if not args.yes:
                _print({"plan": plan(cases), "would_send": [q for cs in cases for q in cs["queries"]],
                        "note": "nothing sent; add --yes to send"})
                return 2
            if args.service == "cloud":
                service = FirecrawlClient(keychain_service=os.environ.get("EDAT_FIRECRAWL_KEYCHAIN") or None)
            else:
                service = FirecrawlClient(args.base_url)
            snap = run_capture(cases, service, today=args.today, deny_terms=deny)
            path = save_private(snap, Path(args.out) if args.out else None)
            if args.manifest_out:
                write_public_manifest(snap, args.manifest_out)
            dropped = snap.meta.get("dropped_cases", [])
            _print({"snapshot_id": snap.id, "saved": str(path), "counts": snap.meta["counts"],
                    "dropped_cases": dropped, "empty_queries": snap.meta["empty_queries"]})
            return 1 if dropped else 0
        if args.cmd == "check":
            snap = Snapshot.load(args.snapshot_dir)
            kept, dropped = check(snap, load_capture_file(args.file))
            _print({"kept": kept, "dropped": dropped})
            return 1 if dropped else 0
        if args.cmd == "public-manifest":
            write_public_manifest(Snapshot.load(args.snapshot_dir), args.out)
            return 0
    except (CaptureError, SnapshotError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except FirecrawlError as exc:
        print(f"firecrawl {exc.kind}: {exc.detail}", file=sys.stderr)
        return 2
    return 3


if __name__ == "__main__":
    sys.exit(main())
