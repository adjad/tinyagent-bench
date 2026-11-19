"""Firecrawl client, stand-in and capture pipeline (spec 15 §6.1). No network."""
import json
from pathlib import Path

import pytest
import yaml

from edat.web import capture as cap
from edat.web.firecrawl import (FirecrawlClient, FirecrawlError, ScrapedPage, StandInFirecrawl,
                                estimate_credits)
from edat.web.snapshot import Snapshot

KEY = "fc-test-NOT-A-REAL-KEY-123"
ENV = "FIRECRAWL_API_KEY"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise AssertionError("test tried to reach the network")
    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("EDAT_PRIVATE_DIR", raising=False)


class Fake:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r


def client(fake, **kw):
    kw.setdefault("min_interval_s", 0)
    return FirecrawlClient(transport=fake, **kw)


V2 = {"success": True, "data": {"web": [
    {"title": "Boil", "description": "d", "url": "https://a.example/x", "markdown": "# Boil\nwater",
     "metadata": {"statusCode": 200, "publishedTime": "2026-03-04T10:00:00Z"}},
    {"url": "", "metadata": {"url": "https://b.example/y", "title": "Meta title", "statusCode": 404}},
]}}


# ---------------------------------------------------------------- client
def test_search_payload_and_mapping(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    f = Fake((200, V2))
    c = client(f)
    hits = c.search("boil water", limit=2, tbs="qdr:y")
    m, url, headers, body, _ = f.calls[0]
    assert (m, url) == ("POST", "https://api.firecrawl.dev/v2/search")
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body == {"query": "boil water", "limit": 2, "country": "US", "tbs": "qdr:y",
                    "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True, "maxAge": 0}}
    assert hits[0].published == "2026-03-04" and hits[0].markdown.startswith("# Boil")
    assert hits[1].url == "https://b.example/y" and hits[1].title == "Meta title"
    assert hits[1].status == 404 and hits[1].markdown is None
    assert c.source == "firecrawl-cloud" and c.converter == "firecrawl-v2-markdown"


def test_search_without_markdown_and_list_shape(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    f = Fake((200, {"success": True, "data": [{"title": "T", "description": "D", "url": "https://c.example"}]}))
    hits = client(f).search("q", with_markdown=False)
    assert "scrapeOptions" not in f.calls[0][3]
    assert hits[0].url == "https://c.example" and hits[0].markdown is None


def test_scrape_payload_and_mapping(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    f = Fake((200, {"success": True, "data": {"markdown": "hello", "metadata": {
        "title": "T", "description": "D", "sourceURL": "https://a.example/p", "statusCode": 200}}}))
    page = client(f).scrape("https://a.example/p")
    assert f.calls[0][1].endswith("/v2/scrape")
    assert f.calls[0][3] == {"url": "https://a.example/p", "formats": ["markdown"],
                             "onlyMainContent": True, "maxAge": 0}
    assert (page.url, page.title, page.markdown, page.status) == ("https://a.example/p", "T", "hello", 200)
    assert page.published is None


def test_key_never_leaks(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    f = Fake((401, {"success": False, "error": f"bad key {KEY}"}))
    c = client(f)
    assert KEY not in repr(c) and KEY not in str(vars(c))
    with pytest.raises(FirecrawlError) as ei:
        c.search("q")
    assert KEY not in ei.value.detail and KEY not in str(ei.value) and ei.value.kind == "auth"
    assert KEY not in json.dumps(c.requests) and "Authorization" not in json.dumps(c.requests)
    assert c.requests[0]["endpoint"] == "/v2/search"


def test_transport_exception_hides_key(monkeypatch):
    monkeypatch.setenv(ENV, KEY)

    def bad(*a):
        raise RuntimeError(f"connection to {KEY} failed")
    with pytest.raises(FirecrawlError) as ei:
        client(bad).scrape("https://a.example")
    assert ei.value.kind == "transport" and KEY not in str(ei.value)


def test_default_transport_is_unreachable_in_tests(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    with pytest.raises(FirecrawlError) as ei:
        FirecrawlClient(min_interval_s=0).search("q")
    assert ei.value.kind == "transport"


def test_base_url_allowlist():
    assert FirecrawlClient("https://api.firecrawl.dev/", transport=Fake((200, {}))).source == "firecrawl-cloud"
    for ok in ("http://127.0.0.1:3002", "http://localhost:3002", "https://localhost:8443", "http://[::1]:3002"):
        assert FirecrawlClient(ok, transport=Fake((200, {}))).source == "firecrawl-self-host"
    for bad in ("https://api.firecrawl.dev.evil.example", "https://example.com", "http://127.0.0.1",
                "http://10.0.0.5:3002", "http://api.firecrawl.dev"):
        with pytest.raises(FirecrawlError) as ei:
            FirecrawlClient(bad)
        assert ei.value.kind == "config"


def test_self_host_needs_no_key_and_sends_none():
    f = Fake((200, {"success": True, "data": {"web": []}}))
    FirecrawlClient("http://127.0.0.1:3002", transport=f, min_interval_s=0).search("q")
    assert "Authorization" not in f.calls[0][2]


def test_missing_key_is_config_error():
    f = Fake((200, {}))
    with pytest.raises(FirecrawlError) as ei:
        client(f).search("q")
    assert ei.value.kind == "config" and "FIRECRAWL_API_KEY" in ei.value.detail and not f.calls


def test_keychain_reader_is_injected_not_run(monkeypatch):
    asked = []
    f = Fake((200, {"success": True, "data": {"web": []}}))
    c = client(f, keychain_service="svc-name", key_reader=lambda s: asked.append(s) or KEY)
    c.search("q")
    assert asked == ["svc-name"] and f.calls[0][2]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in repr(c) and KEY not in str(vars(c))


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (402, "payment"),
                                         (429, "rate_limit"), (500, "server"), (503, "server"),
                                         (400, "request")])
def test_error_mapping(monkeypatch, status, kind):
    monkeypatch.setenv(ENV, KEY)
    with pytest.raises(FirecrawlError) as ei:
        client(Fake((status, {"error": "nope"}))).search("q")
    assert ei.value.kind == kind


def test_success_false_is_request_error(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    with pytest.raises(FirecrawlError) as ei:
        client(Fake((200, {"success": False, "error": "bad"}))).scrape("https://a.example")
    assert ei.value.kind == "request"


def test_request_spacing(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    now = [100.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s
    c = FirecrawlClient(transport=Fake((200, {"success": True, "data": {"web": []}})),
                        min_interval_s=6.5, sleep=sleep, clock=lambda: now[0])
    c.search("a")
    assert slept == []
    now[0] += 2.0
    c.search("b")
    assert slept == [pytest.approx(4.5)]
    now[0] += 10.0
    c.search("c")
    assert len(slept) == 1


# -------------------------------------------------------------- stand-in
PAGES = {
    "https://water.example/boil": {"title": "Boiling point of water", "description": "water boils",
                                   "markdown": "Water boils at 212 degrees Fahrenheit at sea level."},
    "https://cats.example/care": {"title": "Cat care", "markdown": "Feed cats twice a day."},
    "https://water.example/freeze": {"title": "Freezing", "markdown": "Water freezes at 32 degrees."},
}


def test_standin_ranking_and_scrape():
    s = StandInFirecrawl(PAGES)
    hits = s.search("boiling point of water", limit=2)
    assert hits[0].url == "https://water.example/boil" and len(hits) == 2
    assert hits[0].markdown and s.search("cat care", with_markdown=False)[0].markdown is None
    assert s.scrape("https://cats.example/care").markdown.startswith("Feed")
    gone = s.scrape("https://nope.example/")
    assert gone.status == 404 and gone.markdown == ""
    assert s.source == "stand-in" and s.converter == "stand-in" and len(s.requests) == 4
    assert s.credits_used == estimate_credits([2], 0) + estimate_credits([5], 0, with_markdown=False) + 2


def test_standin_explicit_results():
    s = StandInFirecrawl(PAGES, results={"q": ["https://cats.example/care"]})
    assert [h.url for h in s.search("q")] == ["https://cats.example/care"]


def test_estimate_credits():
    assert estimate_credits([5], 0) == 7
    assert estimate_credits([5], 0, with_markdown=False) == 2
    assert estimate_credits([10, 11], 3) == (2 + 10) + (4 + 11) + 3
    assert estimate_credits([], 4) == 4


# --------------------------------------------------------------- privacy
@pytest.mark.parametrize("text", ["write to jo.bloke@mail.example", "call 415-555-0134 now", "dial 555 0199",
                                  "ring (212) 867-5309", "card 4111 1111 1111 1111",
                                  "https://user:pw@host.example/x", "ask about Zorblax today"])
def test_privacy_screen_flags(text):
    assert cap.privacy_screen(text, deny_terms=["zorblax"])


@pytest.mark.parametrize("text", ["what temperature does water boil at sea level",
                                  "events on 2026-11-18 in 1990-2005", "population of 8,000,000 people"])
def test_privacy_screen_passes_clean(text):
    assert cap.privacy_screen(text, deny_terms=["zorblax"]) == []


def test_deny_term_is_whole_word():
    assert cap.privacy_screen("zorblaxian visitors", deny_terms=["zorblax"]) == []


# ---------------------------------------------------------- capture e2e
CASES = [
    {"id": "wcap_need_01", "family": "need_search", "split": "dev", "level": 1,
     "question": "At what temperature does water boil?", "queries": ["boiling point of water"], "limit": 5,
     "follow_links_matching": r"water\.example/(freeze|gone)", "follow_limit": 3,
     "accept": [r"\b212\b"], "reject": [], "support": [r"\b212\b"], "query_terms": [["boil"]]},
    {"id": "wcap_need_02", "family": "need_search", "split": "dev", "level": 1,
     "question": "How often to feed cats?", "queries": ["cat care"], "limit": 3,
     "follow_links_matching": None, "follow_limit": 3,
     "accept": [r"\bthrice\b"], "reject": [], "support": [r"\bthrice\b"], "query_terms": []},
    {"id": "wcap_need_03", "family": "need_search", "split": "dev", "level": 1,
     "question": "Water freezing?", "queries": ["freezing water"], "limit": 3,
     "follow_links_matching": None, "follow_limit": 3,
     "accept": [r"\b32\b"], "reject": [r"degrees"], "support": [r"\b32\b"], "query_terms": []},
]
CPAGES = {**PAGES, "https://water.example/gone": {"title": "Gone", "markdown": "x", "status": 404}}
CPAGES["https://water.example/boil"] = {"title": "Boiling point of water", "description": "water boils",
    "markdown": "Water boils at 212 degrees Fahrenheit. See [freezing](https://water.example/freeze) "
                "and [gone](https://water.example/gone) and [other](https://cats.example/care)."}


def capture(today="2026-11-18"):
    svc = StandInFirecrawl(CPAGES, results={"boiling point of water": ["https://water.example/boil"]})
    snap = cap.run_capture(CASES, svc, today=today, repo_root=cap.REPO_ROOT, now=lambda: "2026-11-18T00:00:00Z")
    return snap, svc


def test_run_capture_end_to_end():
    snap, svc = capture()
    assert snap.source == "stand-in"
    assert set(snap.corpora["wcap_need_01"]) == {"water.example/boil", "water.example/freeze"}
    assert snap.queries["wcap_need_01:0"].params == {"limit": 5, "country": "US"}
    assert snap.queries["wcap_need_01:0"].results[0]["rank"] == 1
    assert {"url": "https://water.example/gone", "reason": "status 404"} in snap.meta["dropped_pages"]
    assert "water.example/gone" not in snap.pages
    # the cats page is not followed (link does not match the pattern)
    assert [r["body"]["url"] for r in svc.requests if r["endpoint"] == "/v2/scrape"] == [
        "https://water.example/freeze", "https://water.example/gone"]
    reasons = {d["id"]: d["reason"] for d in snap.meta["dropped_cases"]}
    assert set(reasons) == {"wcap_need_02", "wcap_need_03"}


def test_snapshot_id_is_stable():
    assert capture()[0].id == capture()[0].id
    assert capture("2026-11-19")[0].id != capture()[0].id


def test_check_keeps_and_drops():
    snap, _ = capture()
    kept, dropped = cap.check(snap, CASES)
    assert kept == ["wcap_need_01"]
    reason = {d["id"]: d["reason"] for d in dropped}
    assert "accept" in reason["wcap_need_02"] and "reject" in reason["wcap_need_03"]
    kept, dropped = cap.check(snap, [{"id": "missing", "accept": [], "reject": []}])
    assert dropped[0]["reason"] == "empty corpus"


def test_run_capture_screens_before_sending():
    bad = [{**CASES[0], "queries": ["email me at a.b@mail.example"]}]
    svc = StandInFirecrawl(CPAGES)
    with pytest.raises(cap.CaptureError):
        cap.run_capture(bad, svc, today="2026-11-18", repo_root=cap.REPO_ROOT)
    assert svc.requests == []


# -------------------------------------------------------------- storage
def test_save_private_refuses_inside_repo(tmp_path):
    snap, _ = capture()
    with pytest.raises(cap.CaptureError):
        cap.save_private(snap, cap.REPO_ROOT / "private_out", repo_root=cap.REPO_ROOT)
    assert not (cap.REPO_ROOT / "private_out").exists()
    with pytest.raises(cap.CaptureError):
        cap.save_private(snap, None, repo_root=cap.REPO_ROOT)   # EDAT_PRIVATE_DIR unset


def test_save_private_writes_under_tmp(tmp_path, monkeypatch):
    snap, _ = capture()
    out = cap.save_private(snap, tmp_path / "out", repo_root=cap.REPO_ROOT)
    assert out == (tmp_path / "out" / snap.id).resolve() and (out / "snapshot.json").exists()
    assert Snapshot.load(out).id == snap.id
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(tmp_path / "priv"))
    out2 = cap.save_private(snap, None, repo_root=cap.REPO_ROOT)
    assert out2 == (tmp_path / "priv" / "web" / "snapshots" / snap.id).resolve()


def test_synthetic_may_live_in_repo_root(tmp_path):
    snap, _ = capture()
    snap.source = "synthetic"
    root = tmp_path / "repo"
    assert cap.save_private(snap, root / "x", repo_root=root).exists()


def test_public_manifest_has_no_text_or_titles(tmp_path):
    snap, _ = capture()
    p = cap.write_public_manifest(snap, tmp_path / "m" / "manifest.json")
    raw = p.read_text()
    m = json.loads(raw)
    assert m["snapshot_id"] == snap.id and all(len(x["sha256"]) == 64 for x in m["pages"])
    assert "https://water.example/boil" in raw
    for leak in ("Boiling point of water", "212 degrees", "water boils", "Feed cats"):
        assert leak not in raw


# ------------------------------------------------------------------ file
def write_capture(tmp_path, cases=None):
    p = tmp_path / "cap.yaml"
    p.write_text(yaml.safe_dump({"schema": cap.CAPTURE_SCHEMA, "cases": cases or CASES}))
    return p


def test_load_capture_file_validation(tmp_path):
    assert [c["id"] for c in cap.load_capture_file(write_capture(tmp_path))] == [c["id"] for c in CASES]
    for broken in ([CASES[0], CASES[0]], [{**CASES[0], "family": "nope"}], [{**CASES[0], "accept": ["("]}],
                   [{**CASES[0], "limit": 11}], [{**CASES[0], "limit": 0}], [{**CASES[0], "queries": []}],
                   [{k: v for k, v in CASES[0].items() if k != "id"}]):
        with pytest.raises(cap.CaptureError):
            cap.load_capture_file(write_capture(tmp_path, broken))


def test_support_defaults_to_accept(tmp_path):
    c = {k: v for k, v in CASES[0].items() if k != "support"}
    assert cap.load_capture_file(write_capture(tmp_path, [c]))[0]["support"] == [r"\b212\b"]


def test_plan_arithmetic():
    p = cap.plan([CASES[0], CASES[1]])
    assert p["searches"] == 2 and p["scrapes_estimate"] == 3
    assert p["credits_estimate"] == (2 + 5) + (2 + 3) + 3 == 15
    assert p["credits_with_margin"] == 18


# ------------------------------------------------------------------- CLI
def test_cli_plan(tmp_path, capsys):
    assert cap.main(["plan", str(write_capture(tmp_path))]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["credits_estimate"] > 0 and out["searches"] == 3


def test_cli_run_without_yes_sends_nothing(tmp_path, capsys, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("a client must not be built without --yes")
    monkeypatch.setattr(cap, "FirecrawlClient", fail)
    rc = cap.main(["run", str(write_capture(tmp_path)), "--service", "cloud", "--today", "2026-11-18"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["would_send"] == ["boiling point of water", "cat care", "freezing water"]


def test_cli_run_screen_failure_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "FirecrawlClient", lambda *a, **k: pytest.fail("built"))
    deny = tmp_path / "deny.txt"
    deny.write_text("# names\ncats\n")
    rc = cap.main(["run", str(write_capture(tmp_path)), "--service", "cloud", "--today", "2026-11-18",
                   "--deny-file", str(deny), "--yes"])
    assert rc == 2


def test_cli_run_check_and_manifest(tmp_path, capsys, monkeypatch):
    svc = StandInFirecrawl(CPAGES, results={"boiling point of water": ["https://water.example/boil"]})
    monkeypatch.setattr(cap, "FirecrawlClient", lambda *a, **k: svc)
    f = write_capture(tmp_path)
    rc = cap.main(["run", str(f), "--service", "cloud", "--today", "2026-11-18", "--out", str(tmp_path / "o"),
                   "--manifest-out", str(tmp_path / "m.json"), "--yes"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["kept_cases"] == 1 and (tmp_path / "m.json").exists()
    snap_dir = Path(out["saved"])
    assert cap.main(["check", str(snap_dir), str(f)]) == 1
    assert cap.main(["public-manifest", str(snap_dir), "--out", str(tmp_path / "m2.json")]) == 0
    assert cap.main([]) == 3 and cap.main(["run"]) == 3
