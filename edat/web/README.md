# Web search and retrieval: the capture runbook

Spec: [15-web-retrieval-spec](../../docs/benchmark/15-web-retrieval-spec.md).

Benchmark runs never touch the web. Every model reads a **frozen snapshot**
through the benchmark's own `web_search` and `web_fetch` tools. Most cases use
a synthetic site that is generated locally and needs nothing. The captured
cases need a snapshot of real pages, built **once per benchmark version** by
the capture described here. That is the only step that needs Firecrawl.

## What you need

Pick one:

| Option | You provide | Cost |
|---|---|---|
| **Hosted Firecrawl, free plan** (recommended) | A free API key from firecrawl.dev (no card) | About 380 credits for the version-1 lists (about 450 with retries), out of the free plan's 1,000 a month. About 10–15 minutes at the free plan's 10 requests a minute |
| Self-hosted Firecrawl | Docker Desktop and Firecrawl's own `docker compose` stack, started by you, with `USE_DB_AUTHENTICATION=false`; API on `http://localhost:3002` | No credits. About 8 GB of memory for Docker's VM while it runs. Its search results come from scraped DuckDuckGo unless you also run SearXNG and set `SEARXNG_ENDPOINT` |

Stop the self-hosted stack before any benchmark run: it competes with the model
for memory.

**The key.** Set it yourself, in your own shell, so it never appears in a file
or in shell history:

```bash
read -s FIRECRAWL_API_KEY && export FIRECRAWL_API_KEY
```

Or keep it in the Keychain and name the item in `EDAT_FIRECRAWL_KEYCHAIN`. The
capture reads it at request time and never prints, logs or stores it.

You also need `EDAT_PRIVATE_DIR` set to the private folder, which is outside
this repository. Captured pages are written only there.

## What is sent

- Only the invented queries in `test_fixtures/web/capture/cases.dev.yaml` and
  the private held-out list (`$EDAT_PRIVATE_DIR/web/queries/capture.heldout.yaml`).
  Each is screened before anything is sent: e-mail addresses, phone numbers,
  long digit runs, credentials in URLs and any word in your optional
  `--deny-file` (for example your own name) stop the capture.
- One `POST /v2/search` per query (`limit` 5, `country` US, Markdown for each
  result, `maxAge: 0` so pages are fetched fresh), and a `POST /v2/scrape` only
  for a result that came back without Markdown.
- Nothing about you, your accounts or your data. No model output is sent.

## Steps

1. See the plan and the credit estimate. This sends nothing:

   ```bash
   python -m edat.web.capture plan test_fixtures/web/capture/cases.dev.yaml
   ```

2. Run it. Without `--yes` it only prints the queries it would send:

   ```bash
   python -m edat.web.capture run test_fixtures/web/capture/cases.dev.yaml --service cloud --today 2027-03-15 --manifest-out test_fixtures/web/capture/manifests/dev.json --yes
   ```

   For a self-hosted stack use `--service self-host --base-url http://localhost:3002`.
   Repeat with the held-out list from the private folder, without
   `--manifest-out`. `--today` is the date models are told it is; use the
   capture date.

3. Check it. Cases whose accepted answer is on no captured page, or whose wrong
   value is on one, are dropped and listed in the snapshot's manifest:

   ```bash
   python -m edat.web.capture check "$EDAT_PRIVATE_DIR/web/snapshots/<snapshot id>" test_fixtures/web/capture/cases.dev.yaml
   ```

4. Commit only the public manifest (`test_fixtures/web/capture/manifests/*.json`):
   URLs, hashes and sizes, no page text. The pages stay in the private folder.

## What it costs, again

- Credits: about 7 per query (2 for a 5-result search, 1 per page's Markdown).
  `plan` prints the exact estimate.
- Re-capture only when a new benchmark version needs it or `check` drops too
  many cases. A re-capture gets a new snapshot id. Results from different
  snapshot ids are never compared.

## Running the tests (no key, no network)

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
