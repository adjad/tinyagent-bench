# Web search and retrieval: the capture runbook

Spec: [15-web-retrieval-spec](../../docs/benchmark/15-web-retrieval-spec.md).

Benchmark runs never touch the web. Every model reads a **frozen snapshot**
through the benchmark's own `web_search` and `web_fetch` tools. Most cases use
a synthetic site that is generated locally and needs nothing.

The captured cases need a snapshot of real pages. It is built **once per
benchmark version**, by the capture described here, from a **self-hosted
Firecrawl** stack. That needs **no API key** and no search service: with
nothing configured, the stack gets its search results by scraping
DuckDuckGo's HTML results page itself.

The capture is its own step:

1. Start the stack.
2. Capture.
3. Shut the stack down.

Never run the stack during a model run or a measurement.

## What gets installed (tell the user before the first start)

- **A container runtime,** if the Mac has none: Docker Desktop, or Colima
  plus the Docker command line from Homebrew (lighter, open source).
- **Firecrawl's Compose stack** from a checkout of a pinned Firecrawl
  release. The spec was read at commit `1510bc4f`. The stack runs five
  services:
  - the API and workers;
  - Playwright with Chromium;
  - Redis;
  - RabbitMQ;
  - PostgreSQL.

  The optional FoundationDB services are not started.
- **Disk:** about 4–8 GB for images and build cache (estimate).
- **Memory:** give the container VM about 8 GB. The stack uses about
  1.5–2.5 GB idle and 4–6 GB while scraping (estimate).
- **Time:** the first build takes about 10–20 minutes. The capture itself
  takes about 6–10 minutes.

## Before you start

- **No heavy job may be running.** No model run, no measurement, no build
  holding the shared heavy-job lock. Set `EDAT_HEAVY_LOCK` to that lock's path
  and the capture refuses while the lock exists.
- **The private folder.** `EDAT_PRIVATE_DIR` must be set to the private
  folder outside this repository. Captured pages are written only there.

## Steps

1. Start the stack, bound to this machine only. The default Firecrawl API is
   unauthenticated, and its own Compose file publishes it on every interface.
   From the Firecrawl checkout, with this repository's override:

   ```bash
   docker compose -f docker-compose.yaml -f <tinyagent-bench>/edat/web/compose.localhost.yaml up -d api
   ```

   `up -d api` starts the API and its dependencies, and leaves FoundationDB
   out. The override needs Docker Compose 2.24 or later.

2. See the plan. This sends nothing:

   ```bash
   python -m edat.web.capture plan test_fixtures/web/capture/cases.dev.yaml
   ```

3. Run the capture. Without `--yes` it only prints the queries it would send:

   ```bash
   python -m edat.web.capture run test_fixtures/web/capture/cases.dev.yaml --today 2027-03-15 --manifest-out test_fixtures/web/capture/manifests/dev.json --yes
   ```

   - The default service is the self-hosted stack at `http://127.0.0.1:3002`.
     Only loopback addresses are accepted for it.
   - Repeat with the held-out list from
     `$EDAT_PRIVATE_DIR/web/queries/capture.heldout.yaml`, without
     `--manifest-out`.
   - `--today` is the date models are told it is. Use the capture date.

4. Stop the stack:

   ```bash
   docker compose -f docker-compose.yaml -f <tinyagent-bench>/edat/web/compose.localhost.yaml down
   ```

5. Read the counts the run printed (also stored in the snapshot's
   `meta.counts`):
   - empty searches (DuckDuckGo can block a self-hosted stack);
   - pages dropped because plain scraping failed (an error status, or empty
     Markdown);
   - cases dropped because no kept page holds their answer, or a page holds a
     wrong value.

   Re-check any time:

   ```bash
   python -m edat.web.capture check "$EDAT_PRIVATE_DIR/web/snapshots/<snapshot id>" test_fixtures/web/capture/cases.dev.yaml
   ```

   If many searches came back empty, wait and re-run. Adding a keyless
   SearXNG container and setting `SEARXNG_ENDPOINT` is the fallback (an open
   question in the spec).

6. Commit only the public manifest (`test_fixtures/web/capture/manifests/*.json`):
   URLs, hashes and sizes, no page text. The pages stay in the private folder.

## What is sent

- **The queries:** only the invented queries in
  `test_fixtures/web/capture/cases.dev.yaml` and the private held-out list.
- **The screen:** every query is screened before anything is sent. E-mail
  addresses, phone numbers, long digit runs, credentials in URLs and any word
  in your optional `--deny-file` (for example your own name) stop the capture.
- **The requests:** the capture sends one `POST /v2/search` per query to the
  local stack (`limit` 5, Markdown for each result, `maxAge: 0`), 6.5 s apart.
  It sends a `POST /v2/scrape` only for a result without Markdown.
- **What leaves the Mac:** the stack sends each query to DuckDuckGo and
  fetches the result pages from their own sites. Nothing about you, your
  accounts or your data is sent, and no model output is sent.

## The hosted service (not the main path)

The same client can talk to Firecrawl's hosted service: `--service cloud`, with
a key you set yourself (`FIRECRAWL_API_KEY`, or a Keychain item named in
`EDAT_FIRECRAWL_KEYCHAIN`). The key is read at request time and never printed,
logged or stored. The version-1 lists cost about 380 credits (about 450 with
retries) of the free plan's 1,000 a month; `plan` prints the estimate.

## Running the tests (no stack, no network)

```bash
python -m pytest -q tests/test_edat_web_core.py
```

```bash
python -m pytest -q tests/test_edat_web_cases.py
```

```bash
python -m pytest -q tests/test_edat_web_capture.py
```

```bash
python -m pytest -q tests/test_edat_web_runner.py
```

```bash
python -m edat.web.runner oracle --set core
```

Rebuild the development split after changing the generator or the templates:

```bash
python -m edat.web.cases build
```
