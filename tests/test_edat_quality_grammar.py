"""Tests for the offline grammar-signal wrapper. Fakes only: no server, no binary, no network."""
from __future__ import annotations

import json
import subprocess

import pytest

from edat.quality import grammar as G


def lt_response(*matches):
    return {"matches": [{"offset": o, "length": n, "message": "m",
                         "rule": {"id": rid, "category": {"id": cat}}} for o, n, rid, cat in matches]}


def test_lt_posts_expected_fields_and_parses():
    seen = {}

    def transport(url, fields):
        seen["url"], seen["fields"] = url, fields
        return lt_response((0, 3, "R1", "GRAMMAR"), (4, 2, "STYLE1", "STYLE"), (7, 3, "MORFOLOGIK", "TYPOS"))

    lt = G.LanguageToolChecker("http://localhost:8081", transport=transport)
    issues = lt.check("Teh dog xyz")
    assert seen["url"] == "http://localhost:8081/v2/check"
    assert seen["fields"]["language"] == "en-US" and seen["fields"]["enabledCategories"] == "GRAMMAR,TYPOS,PUNCTUATION"
    assert [i.rule_id for i in issues] == ["R1", "MORFOLOGIK"]       # the STYLE category is dropped
    assert issues[0].source == "languagetool" and (issues[0].start, issues[0].end) == (0, 3)


@pytest.mark.parametrize("url", ["http://example.com:8081", "https://languagetool.org", "http://10.0.0.5:8081",
                                 "http://localhost.evil.com"])
def test_lt_refuses_non_local_urls(url):
    with pytest.raises(ValueError):
        G.LanguageToolChecker(url, transport=lambda u, f: {})


def test_default_transport_refuses_non_local():
    with pytest.raises(ValueError):
        G._default_transport("http://example.com/v2/check", {"text": "x"})


def test_lt_unavailable_when_transport_fails():
    def boom(url, fields):
        raise ConnectionRefusedError("down")

    lt = G.LanguageToolChecker("http://127.0.0.1:8081", transport=boom)
    assert lt.check("hello") == [] and lt.available is False
    assert G.grammar_signal("hello world", {}, [lt]) == G.unavailable()


def test_harper_runner_and_missing_binary():
    def runner(argv, stdin):
        assert argv[0] == "harper-cli"
        return json.dumps({"lints": [{"span": {"start": 0, "end": 3}, "rule": "SpellCheck", "kind": "Spelling",
                                      "message": "x"}]})

    h = G.HarperChecker(runner=runner)
    issues = h.check("Teh dog")
    assert len(issues) == 1 and issues[0].source == "harper" and issues[0].rule_id == "SpellCheck"

    def missing(argv, stdin):
        raise FileNotFoundError(argv[0])

    h2 = G.HarperChecker("nope", runner=missing)
    assert h2.check("x") == [] and h2.available is False
    h3 = G.HarperChecker(runner=lambda a, s: (_ for _ in ()).throw(subprocess.CalledProcessError(1, a)))
    assert h3.check("x") == [] and h3.available is False


def iss(s, e, rid, src):
    return G.GrammarIssue(s, e, rid, "GRAMMAR", "m", src)


def test_combine_needs_overlap_or_allow_list():
    lt = [iss(0, 5, "A", "languagetool"), iss(10, 15, "B", "languagetool"), iss(20, 25, "C", "languagetool")]
    hp = [iss(3, 8, "h1", "harper"), iss(40, 45, "h2", "harper")]
    assert [i.rule_id for i in G.combine(lt, hp, [])] == ["A"]                  # only the overlapping one
    assert [i.rule_id for i in G.combine(lt, hp, ["C"])] == ["A", "C"]          # plus the allow-listed rule
    assert G.combine(lt, [], []) == []
    assert [i.rule_id for i in G.combine([iss(0, 2, "A", "languagetool")], [iss(2, 4, "h", "harper")], [])] == []


def test_custom_dictionary_names():
    case = {"sender": {"name": "Marcus Chen", "preferred_name": "Marcus"}, "recipient": {"name": "Dana Okafor"},
            "points": [{"anchors": ["Brightline", "load-test", {"type": "date", "value": "2030-11-25"}]}],
            "facts": [], "entities": ["Ines Park"], "allowed_text": "Quay Lane"}
    d = G.custom_dictionary(case)
    assert {"Marcus", "Chen", "Dana", "Okafor", "Brightline", "Ines", "Park", "Quay", "Lane"} <= d
    assert "load" not in d


class Fake:
    def __init__(self, name, issues, available=True):
        self.name, self.version, self.available = name, "t", available
        self._issues = issues

    def check(self, text):
        return list(self._issues)


def test_signal_combines_counts_per_100_words_and_skips_dictionary_names():
    text = "Teh dog and Okafor barks loud. " * 5          # 30 words
    n = text.index("Okafor")
    lt = Fake("languagetool", [iss(0, 3, "A", "languagetool"),
                               G.GrammarIssue(n, n + 6, "SPELL", "TYPOS", "m", "languagetool"),
                               iss(20, 30, "B", "languagetool")])
    hp = Fake("harper", [iss(0, 3, "h", "harper"), iss(n, n + 6, "h", "harper")])
    case = {"recipient": {"name": "Dana Okafor"}, "points": []}
    sig = G.grammar_signal(text, case, [lt, hp])
    assert sig["available"] and [i["rule_id"] for i in sig["issues"]] == ["A"]
    assert abs(sig["errors_per_100w"] - 100 * 1 / 30) < 1e-9
    assert sig["issues"][0]["text"] == "Teh"
    sig = G.grammar_signal(text, case, [lt, hp], allow_list=["B"])
    assert [i["rule_id"] for i in sig["issues"]] == ["A", "B"]


def test_signal_without_languagetool_is_unavailable():
    assert G.grammar_signal("hello", {}, [Fake("harper", [])]) == {"errors_per_100w": None, "issues": [],
                                                                  "available": False}
    assert G.grammar_signal("hello", {}, []) == G.unavailable()
    assert G.unavailable()["errors_per_100w"] is None and G.unavailable()["available"] is False


def test_rule_precision_and_enabled_rules():
    class Rules:
        name, version = "languagetool", "t"

        def check(self, text):
            out = []
            if "bad" in text:
                i = text.index("bad")
                out.append(iss(i, i + 3, "GOOD_RULE", "languagetool"))
            if "x" in text:
                i = text.index("x")
                out.append(iss(i, i + 1, "NOISY_RULE", "languagetool"))
            return out

    labelled = [{"text": f"a bad one {k}", "errors": [{"start": 2, "end": 5}]} for k in range(9)]
    labelled.append({"text": "a bad one", "errors": []})                  # one false positive: 9/10
    labelled += [{"text": "x marks", "errors": [{"start": 0, "end": 1}]}] + [
        {"text": f"x {k}", "errors": []} for k in range(4)]                # 1/5
    prec = G.rule_precision(Rules(), labelled)
    assert prec["GOOD_RULE"] == 0.9 and prec["NOISY_RULE"] == 0.2
    assert G.enabled_rules(prec) == {"GOOD_RULE"}
    assert G.enabled_rules(prec, threshold=0.95) == set()
    assert G.enabled_rules({"A": 1.0, "B": 0.89}) == {"A"}
