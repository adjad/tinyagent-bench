# Website

The public site. It is static: no build step, no framework, no server code. Any plain file server can host this
folder (GitHub Pages included). It renders every table and chart from the results store's export in
[`data/`](data/README.md); nothing is hard-coded.

```
python3 -m http.server 8742 --bind 127.0.0.1    # from this folder, then open http://127.0.0.1:8742/
node tests/check.mjs                            # the checks: schema fields, then every page in headless Chrome
```

Opening `index.html` straight from disk does not work: browsers refuse `fetch()` on `file://`.

## Files

| File | What it holds |
|---|---|
| `index.html` | The shell: sample-data band, top bar, footer, script order |
| `css/tokens.css`, `css/base.css`, `css/site.css` | Light tokens, base rules, components and dark tokens |
| `js/config.js` | **The benchmark's name** (`name`, `longName`), the data root, labels for devices, runtimes and quantisations the export does not carry yet |
| `js/registry.js` | The metric registry, column groups, test areas, facet words and every status's words and icon |
| `js/store.js` | The data layer: loads the export, normalises statuses, answers cell, difference and rank questions |
| `js/ui.js` | Shared HTML builders: icons, cells, badges, trays, tables, "How to read this chart", loading, empty and error states |
| `js/charts.js` | SVG chart kit: scatter, dot rows, context curves, stacked bar, budget bars, horizontal bars, sustained line |
| `js/app.js` | Hash router, shell, shared events (trays, sort, theme, menu, retry) |
| `js/pages/*.js` | One file per page type |
| `tests/check.mjs` | The checks |

## Renaming the benchmark

Change `name` and `longName` in `js/config.js`. Page titles, the wordmark, the footer and the methodology read them
from there; `index.html` holds no name.

## Adding a metric, a column group or a test area

- **Metric:** one entry in `metrics` in `js/registry.js`, with `src` pointing at where the store writes it
  (`{r: tier, m: metric, f: facet}` for a result row, `{s: tier, k: field}` for a safety row,
  `{c: curveMetric, level}` for a performance curve point, `{fit: field}` for the fit gate). The leaderboard's
  columns, sort keys, the compare rows and the difference cells pick it up. Mark it `planned: true` until the store
  writes it; its cells then read "Not in this board yet".
- **Column group:** one entry in `groups`. `main: true` puts it in the tray; the rest are under "More groups".
- **Test area:** one entry in `tiers`.
- **Device:** nothing here. The device list comes from `data/index.json`; `config.devices` only adds display words
  until the export carries them.

## How pages talk to the data

A page module registers `EDAT.pages[name] = { render(ctx) -> { title, html }, after?(root, ctx) }`. `render` may be
`async`. `ctx` holds:

- `parts`, `q`: the route segments (decoded) and the query (`URLSearchParams`);
- `link(path, params, keepLeaderboardFilters)`, `linkHere(params)`, `setQ(params)`, `go(hash)`: build segments with
  `EDAT.ui.seg(id)`, since ids can hold spaces and slashes;
- `index`, `boardInfo` (the chosen board from the index, with `devices`), `devId`;
- `device`: the chosen device's `Board`; `cloud`: the cloud references' `Board` or `null`.

A `Board` (`js/store.js`) has `device`, `references {tuned, base}`, `entries` (one per variant), `entry(key)`,
`refEntry(role)`, `result(key, tier, metric, facet)`, `safety(key, tier)`, `curve(key, metric)`,
`rank(key, tier, metric)`, `has(tier[, metric, facet])`, `facets(tier, kind)`, `tiers()`.

An entry is `{ key, model, variant, run, cloud, refRole, modelRole, name, thinking, thinkingText, line, group, ranked, scored, board }`.
`group` is the store's `rank_group`: `ranked`, `invalid`, `does_not_fit`, `not_measured` or `cloud`.

- `EDAT.store.value(entry, metricId)` returns `{status: "ok", value, interval, half, n, ...}` or `{status}` where
  status is any store status (`does_not_fit`, `out_of_memory`, `beyond_window`, `not_applicable`, `excluded`,
  `failed`, `not_run`, `unsupported_by_runtime`) or one the site adds: `not_in_board` (the tier has no rows in this
  board), `not_applicable_cloud`, `not_applicable_local`, `not_applicable_iphone`, `not_measured`, `no_value`.
- `EDAT.store.diff(entry, metricId, role, refBoard?)` returns the difference from the `tuned` or `base` reference:
  the store's paired difference when it has one, otherwise an unpaired estimate flagged `paired: false`.
- `EDAT.store.rankRanges(entries, metricId)` applies the store's rank-range rule to any set of entries.
- `EDAT.store.bestPerModel(entries, metricId)`, `EDAT.store.curveFor(entry, metric)`,
  `EDAT.store.loadTrajectories(version, deviceId)`, `EDAT.store.loadAllDevices(version)`.

`EDAT.ui.cell(entry, metricId)` renders any cell, status included; `scoreCell`, `deltaCell`, `safetyCell`,
`fitBadge`, `refBadge` and `status(key)` cover the rest. Never print a raw value without going through them, so a
missing result always reads as a status with an icon and words, never a blank or a zero.

## Rules the pages keep

- Only entries that fit are ranked. "Does not fit" entries are listed with the memory they need and never scored.
- Unsafe actions and privacy leaks sit beside every agentic score and are never blended into one.
- Cloud references are never ranked, sit in their own band, and carry their run date. They never share an axis with
  on-device cost.
- Every chart has a "Show the numbers as a table" disclosure and a "How to read this chart" expander, and never
  relies on colour alone.
- Held-out cases never appear. The store exports development-split trajectories only; the data layer also drops any
  record marked held out.
- The sample board is labelled as sample data everywhere it shows.
