"""Spec 15: the snapshot format, the ranker and the edat-web-v1 tools. Offline."""
from __future__ import annotations

import json

import pytest

from edat.web import rank
from edat.web.snapshot import (Page, QueryRecord, Snapshot, SnapshotError, clean_url, normalize_markdown,
                               url_key)
from edat.web.tools import BUDGET_TEXT, DocCorpus, WebEnvironment


def _pages():
    return [
        Page("https://Ferry.example/timetable/", "Ferry timetable", "# Ferry\n\nFirst Saturday sailing 07:40.\n",
             description="Official ferry times.", published="2027-01-04"),
        Page("https://forum.example/t/ferry", "Ferry on Saturdays?", "Someone said the boat is at 08:10."),
        Page("https://www.news.example/2026/fees", "Fees rise", "Food stalls pay $45."),
    ]


def _snap(**kw):
    return Snapshot.build(_pages(), today="2027-03-15", source="synthetic", converter="test",
                          queries=[QueryRecord("q1", "ferry saturday", {"limit": 5},
                                               [{"url": "https://forum.example/t/ferry", "title": "x"}])],
                          corpora={"all": [p.key for p in _pages()]}, **kw)


def test_normalize_markdown_is_fixed():
    raw = "# T\r\n\r\n\r\n\r\nline   \n![logo](a.png) and ![](b.png)\n[link](https://x.example)\n"
    out = normalize_markdown(raw)
    assert out == "# T\n\nline\n[image: logo] and\n[link](https://x.example)\n"
    assert normalize_markdown(out) == out


def test_url_key_and_clean_url():
    assert url_key("https://WWW.Example.org/a/b/#frag") == "example.org/a/b"
    assert url_key("http://example.org/a?x=1") == "example.org/a?x=1"
    assert url_key("example.org/a/") == "example.org/a"
    assert clean_url("Example.ORG") == "https://example.org/"


def test_snapshot_id_covers_what_models_see(tmp_path):
    a, b = _snap(), _snap()
    assert a.id == b.id and len(a.id) == 64
    pages = _pages()
    pages[0] = Page(pages[0].url, "Ferry timetable (new)", pages[0].text)
    c = Snapshot.build(pages, today="2027-03-15", source="synthetic", converter="test")
    assert c.id != a.id                                   # a title change changes the id
    d = Snapshot.build(_pages(), today="2027-03-16", source="synthetic", converter="test")
    assert d.id != Snapshot.build(_pages(), today="2027-03-15", source="synthetic", converter="test").id


def test_snapshot_round_trip_and_tamper_check(tmp_path):
    s = _snap()
    s.save(tmp_path / "snap")
    back = Snapshot.load(tmp_path / "snap")
    assert back.id == s.id
    page = next((tmp_path / "snap" / "pages").glob("*.md"))
    page.write_text(page.read_text() + "tampered\n")
    with pytest.raises(SnapshotError):
        Snapshot.load(tmp_path / "snap")


def test_public_manifest_has_no_third_party_text():
    m = _snap().public_manifest()
    blob = json.dumps(m)
    assert m["snapshot_id"] == _snap().id
    for secret in ("Ferry timetable", "First Saturday", "Someone said", "Official ferry"):
        assert secret not in blob
    assert {p["url"] for p in m["pages"]} == {p.url for p in _pages()}
    assert all(len(p["sha256"]) == 64 for p in m["pages"])


def test_snapshot_refuses_bad_input():
    with pytest.raises(SnapshotError):
        Snapshot.build([], today="15 March", source="synthetic", converter="x")
    with pytest.raises(SnapshotError):
        Snapshot.build([], today="2027-03-15", source="the-web", converter="x")
    s = _snap()
    with pytest.raises(SnapshotError):
        s.add_page(Page("https://ferry.example/timetable", "Other", "different text"))
    with pytest.raises(SnapshotError):
        s.set_corpus("bad", ["missing.example/page"])


def test_bm25_is_deterministic_and_prefers_title_matches():
    docs = [rank.Doc("long", "Harrow Quay Library", "", "filler words here. " * 400 + "late opening"),
            rank.Doc("short", "Harrow Quay festival", "", "Crowds came to the festival."),
            rank.Doc("other", "Brindlemoor Library", "", "Brindlemoor library hours.")]
    docs += [rank.Doc(f"z{i}", f"Garden notes {i}", "", "Seasonal planting and pruning.") for i in range(12)]
    idx = rank.BM25Index(docs)
    first = idx.search("Harrow Quay library", 3)
    assert first == idx.search("Harrow Quay library", 3)
    assert first[0][0] == "long"
    assert idx.search("nothing matches zzz", 3) == []


def test_ties_break_on_prior_then_id():
    docs = [rank.Doc("b", "same words"), rank.Doc("a", "same words"), rank.Doc("c", "same words")]
    idx = rank.BM25Index(docs)
    assert [i for i, _ in idx.search("same words", 3)] == ["a", "b", "c"]
    assert [i for i, _ in idx.search("same words", 3, prior={"c": 1})] == ["c", "a", "b"]


def test_window_cuts_at_line_break_and_chunks_are_bounded():
    text = "".join(f"line {i:04d} of the page\n" for i in range(600))
    first, nxt = rank.window(text, 0, 6000)
    assert first.endswith("\n") and len(first) <= 6000 and nxt == len(first)
    rest, nxt2 = rank.window(text, nxt, 100000)
    assert first + rest == text and nxt2 is None
    chunks = rank.chunk_text("\n\n".join(["word " * 50] * 10 + ["x" * 2000]))
    assert all(len(c) <= rank.CHUNK_CHARS for c in chunks)
    assert rank.chunk_text("a\n\nb") == ["a\n\nb"]


def test_web_tools_search_fetch_and_404():
    env = WebEnvironment(_snap(), "all")
    r = json.loads(env.call("web_search", {"query": "ferry saturday"}).content)
    assert [url_key(x["url"]) for x in r["results"]][:2] == ["ferry.example/timetable", "forum.example/t/ferry"]
    assert r["results"][0]["snippet"] == "Official ferry times."
    site = json.loads(env.call("web_search", {"query": "ferry", "site": "forum.example"}).content)
    assert [x["url"] for x in site["results"]] == ["https://forum.example/t/ferry"]
    page = json.loads(env.call("web_fetch", {"url": "ferry.example/timetable"}).content)
    assert page["published"] == "2027-01-04" and "07:40" in page["text"]
    miss = env.call("web_fetch", {"url": "https://nowhere.invalid/x"})
    assert miss.outcome == "no_match" and "404" in miss.content
    bad = env.call("web_search", {"query": "x", "limit": 50})
    assert bad.outcome == "failed" and "limit" in bad.content
    assert [e["name"] for e in env.log] == ["web_search", "web_search", "web_fetch", "web_fetch", "web_search"]


def test_reading_budget_is_identical_bytes():
    big = Page("https://big.example/p", "Big", "".join(f"row {i} of a long table\n" for i in range(3000)))
    snap = Snapshot.build([big], today="2027-03-15", source="synthetic", converter="t")
    env = WebEnvironment(snap, None, budget_chars=10000)
    a = json.loads(env.call("web_fetch", {"url": big.url}).content)
    b = json.loads(env.call("web_fetch", {"url": big.url, "offset": a["next_offset"]}).content)
    assert len(a["text"]) <= 6000 and len(a["text"]) + len(b["text"]) <= 10000
    c = env.call("web_fetch", {"url": big.url, "offset": b["next_offset"] or 0})
    assert BUDGET_TEXT in c.content and env.log[-1]["budget_exhausted"]


def test_docs_corpus_chunks_and_context_block_cap():
    docs = [{"id": f"doc{i}", "title": f"Doc {i}", "text": f"Policy {i}.\n\n" + ("Plain words. " * 120)}
            for i in range(10)]
    corpus = DocCorpus("org", docs)
    assert corpus.chunk("doc3#1").doc_id == "doc3"
    block, ids = corpus.context_block("Policy 3", top=8, cap_chars=3000)
    assert len(block) <= 3000 + len(ids) and ids and ids[0].startswith("doc")
    again = DocCorpus("org", docs)
    assert again.hash == corpus.hash and again.context_block("Policy 3", top=8, cap_chars=3000) == (block, ids)
    env = WebEnvironment(_snap(), "all", docs=corpus)
    hits = json.loads(env.call("docs_search", {"query": "Policy 7"}).content)["results"]
    assert hits and all(set(h) == {"chunk_id", "document", "start"} for h in hits)
    assert "Policy" in json.loads(env.call("docs_read", {"chunk_id": hits[0]["chunk_id"]}).content)["text"]


def test_schemas_and_info():
    env = WebEnvironment(_snap(), "all")
    names = [s["function"]["name"] for s in env.schemas(["web_search", "web_fetch", "send_message"])]
    assert names == ["web_search", "web_fetch", "send_message"]
    assert env.info("send_message").effect and not env.info("web_fetch").effect
    assert env.info("nope") is None
    assert "2027-03-15" in env.system_prompt()
