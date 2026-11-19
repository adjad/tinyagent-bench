# Website data (SAMPLE)

Everything in this folder is **synthetic sample data** written by
`python -m edat.results sample --data-dir data/results --site-dir site/data --no-records`.
Model names ("Sample Model D 8B", "sample-cloud/model-a"), hashes and numbers are
invented. The two references are "Sample Tuned 4B" (stands in for the tuned
baseline) and "Sample Base 4B" (stands in for the base reference).

Formats are defined in [docs/benchmark/results-record.md](../../docs/benchmark/results-record.md)
§8 and [`edat/results/schema/site-board.schema.json`](../../edat/results/schema/site-board.schema.json).

| File | What the page reads |
|---|---|
| `index.json` | `edat.site-index/1.0`: board versions, devices, file paths, references per device, `sample` flag |
| `<board>/<device>.json` | `edat.site-board/1.0`: `models`, `variants` (one per entry: model × quantisation × runtime × thinking), `runs` (status, fit verdict, `ranked`, `rank_group`), `results` (variant × tier × metric, value ± 95 % interval, `vs.tuned` / `vs.base`), `safety` (unsafe actions and privacy leaks, never in a score), `curves` (perf metric over context levels with failure statuses), `ranking` (rank ranges) |
| `<board>/<device>.trajectories.json` | Short trajectories per variant, case and trial: steps, final answer, checks, outcome `pass` / `fail` / `unsafe`. Development split only |
| `<board>/cloud.json` | Cloud references in the same shape; every `ranked` is false and `rank_group` is `cloud` |

Conventions:

- Rates (`pass1`, `pass_all_trials`, `agentic_score`, `assistant_score`, `score_mean`,
  `unsafe_rate`, `leak_rate`) are **percentages**; differences in `vs` are
  percentage points. Memory is bytes, times are seconds.
- `facet` is `null` for a tier total, or `lane=assistant`, `family=select`,
  `surface=ask`, `profile=student` for a breakdown.
- `vs.<role>` is `{delta, interval, tag, n_cases, wins, losses}`, `{self: true}` on
  the reference's own row, `{refused: "<reason>"}` when the two are not comparable,
  or `null` when there is nothing to compare.
- `runs[].rank_group`: `ranked` (fits or fits with small context), `invalid` (fits, but
  over 2 % of trials excluded), `does_not_fit`
  and `not_measured` (listed, never ranked), `cloud`.
- `curves[].points[].status`: `ok`, `out_of_memory` (with `need_bytes`),
  `beyond_window`, `not_run`, … never a silent gap.

The sample deliberately includes: a thinking-on and thinking-off pair (Model D), a
mixture-of-experts model that fits only at small context (Model E), a model that does
not fit a 16 GB Mac (Model G), one with no fit measurement (Model H), one marked
invalid for more than 2 % excluded trials (Model F), out-of-memory and beyond-window
points on the context curves, an iPhone board, and two cloud references.
