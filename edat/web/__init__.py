"""Web search and retrieval tests (spec 15).

Every model reads the same frozen pages: a snapshot (captured once from
Firecrawl, or generated locally) replayed through the benchmark's own
`web_search` and `web_fetch` tools. Nothing in this package reaches the live
web during a benchmark run. See docs/benchmark/15-web-retrieval-spec.md and
edat/web/README.md (the capture runbook).
"""
