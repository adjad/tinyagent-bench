# EDAT results record

The one record every EDAT test tier writes, the run directory it lives in, and
the rules the results store applies when it reads, merges, aggregates and
exports results.

- Record schema id: **`edat.result/1.0`**
  ([`edat/results/schema/result-record.schema.json`](../../edat/results/schema/result-record.schema.json))
- Run manifest: **`edat.run-manifest/1.0`**
  ([`run-manifest.schema.json`](../../edat/results/schema/run-manifest.schema.json))
- Published snapshot: **`edat.snapshot/1.0`**; website files: **`edat.site-board/1.0`** and
  **`edat.site-index/1.0`** ([`snapshot.schema.json`](../../edat/results/schema/snapshot.schema.json),
  [`site-board.schema.json`](../../edat/results/schema/site-board.schema.json))
- Code: [`edat/results/`](../../edat/results/). Command line: `python -m edat.results --help`.

Status: draft 1, 2026-11-18. It reconciles spec 02 (§2.3, §4.4, §5), 03 (§1.5,
§6.1, §13), 04 (§3), 05 (§11), 06 (§4, §6, §12, §13), 08 (§6, §7) and 10 (§8.3).
Where the specs disagree, §9 lists each disagreement and the choice made.

## 1. The model in one screen

- A **record** is one graded unit: one **entry** on one **case**, one **trial**, in one **run**.
  One JSON object per line in `records.jsonl`.
- An **entry** is what the board ranks: a model's weights and quantisation, in one
  thinking mode, on one runtime. Thinking-on and thinking-off are two entries, never one.
- A **run** is one invocation of one tier's harness. It has a directory with
  `records.jsonl`, `manifest.json` and `raw/`.
- **Raw output** (the model's text, tool calls, reasoning, the captured trajectory)
  is never inside a record. It is a file under `raw/`, named by its sha256, and the
  record points at it.
- **Safety** counts (unsafe actions, privacy leaks) sit in their own block, never in
  `metrics`, and are never blended into a score.
- A **board version** pins everything a comparison needs: cases, world seeds,
  clock, harness commit and the two reference models. Differences between entries
  are computed only inside one board version.

## 2. The record, field by field

Required unless marked optional. `null` means "not measured"; every `null` that
a reader would expect to be filled carries a reason (`raw_reason`, `device_reason`,
`status_detail`).

| Field | Type | Meaning |
|---|---|---|
| `schema` | `"edat.result/1.0"` | |
| `kind` | `local` \| `cloud_reference` | A hosted model is a `cloud_reference`: never ranked, own tables |
| `tier` | enum | `agentic`, `preferences`, `long_session`, `memory`, `quality`, `vision`, `audio`, `perf`, `in_assistant`, `app_control`, `sidekick`, `phone_parity` |
| `suite` | string, optional | The harness inside the tier, e.g. `toolbench`, `reference-loop`, `productbench`, `readability` |
| `run_id` | string | Unique per run. Recommended form (04 §3.1): `<UTC start>-<4 hex>`, e.g. `2026-12-01T10-15-00Z-7f3c`. Converters keep the harness's own id. |
| `board_version` | string | e.g. `2027.1`. Pins cases, seeds, clock, harness commit and references |
| `lang` | `"en"` | English only in version 1 |
| `model` | object | §2.1 |
| `runtime` | object | `{name, version, backend?, commit?}`, e.g. `{"name": "omlx", "version": "0.7.3"}`, `{"name": "mlx-swift"}`, `{"name": "openrouter"}`, `{"name": "stub"}` |
| `device` | object \| null | `{id, class, ram_bytes, os, model_id?, soc?, os_build?}`. `null` only for cloud rows, with `device_reason: "cloud"` |
| `settings` | object | §2.2 |
| `harness_commit` | hex, 7–40 | The EDAT commit that produced the record. `harness_dirty` (optional bool) says whether the tree had changes |
| `started_at` | ISO 8601 UTC with `Z` | When the trial started |
| `case_id` | string | Stable case id. Perf uses `<test>/<level_tokens>`; fit verdicts use `fit/16gb_mac` and `fit/iphone_air` |
| `split` | `dev` \| `heldout` \| `public_dataset` | EDAT split. `heldout` records never leave the private store (§7) |
| `trial` | int ≥ 1 | 1–3 in routine runs. Perf maps `rep` to `trial` |
| `status` | enum | §3 |
| `status_detail` | string, optional | The tier's own status word, kept verbatim (e.g. `oom_killed_at_load`) |
| `facets` | object, optional | Grouping keys: `lane`, `family`, `category`, `surface`, `profile`, `test`, `level_tokens`, `step`, `source_split`, `tags[]` |
| `metrics` | object | §4. Flat map of `snake_case` name → number, bool, short string (≤ 64 chars) or null |
| `safety` | object | §5. `{unsafe_actions, privacy_leaks, labels[]}` |
| `raw` | object \| null | `{path: "raw/<sha256>.json", sha256, bytes}`. `null` needs `raw_reason` |
| `sample` | bool, optional | `true` on invented sample data. A snapshot never mixes sample and real records |
| `source` | object, optional | `{converter, format, row}`: provenance when a converter wrote the record |

### 2.1 `model`

| Field | Local | Cloud | Meaning |
|---|---|---|---|
| `id` | yes | yes | Engine id (`Gemma-Wisp`) or, for cloud, the requested OpenRouter id |
| `role` | yes | yes | `baseline` (Gemma-Wisp), `reference` (base Gemma 4 E4B), `candidate`, `variant` (fallback quantisation, 04 §1.6), `cloud_reference` |
| `variant_of`, `headline_entry` | optional | optional | As 04 §3.4. `headline_entry` = the mode the assistant would ship for agent turns |
| `weights_sha256` | one of these two | null | Full-content hash: sha256 over the sorted list of `<file name>\0<file sha256>` lines of every weight file |
| `weights_fingerprint` | one of these two | null | Toolbench's fast fingerprint (name, size, first and last MiB of each file). Tells files apart; not a content hash |
| `weights_bytes` | optional | null | |
| `quant` | yes for ranked rows | null | `{scheme, bits?, group_size?, mixed?}`, e.g. `{"scheme": "oQ4e", "bits": 4.5}` |
| `architecture` | optional | `unknown` | `dense`, `moe`, `dense_ple`, `hybrid_ssm`, `unknown` |
| `params_total`, `params_active` | optional | null | Integers (parameters, not billions). Active = total for dense models |
| `template_sha256` | optional | null | Chat template hash |
| `requested_model` | null | yes | OpenRouter id sent |
| `resolved_model` | null | yes | `model` field OpenRouter returned. A mismatch with `requested_model` is flagged by the aggregator |
| `upstream_provider` | null | yes | Provider routed to (pinned, fallbacks off) |
| `run_date` | null | yes | `{first, last}`: UTC times of the first and last request of the run |

### 2.2 `settings`

| Field | Meaning |
|---|---|
| `thinking` | `on`, `off`, or `minimal` (a hosted model that cannot turn reasoning off, labelled "minimal reasoning", 02 §4.4) |
| `thinking_detail` | Optional. The harness's own regime word: Toolbench `default`, in-assistant `harbor` or `narration-on`, cloud effort level |
| `sampling` | `{temperature, top_p, top_k, min_p, repeat_penalty, presence_penalty, frequency_penalty, max_output_tokens, reasoning_effort}`, each optional or null. The values actually sent |
| `sampling_source` | `author_recommended`, `fixed_sensitivity` (temperature 0.6, top-p 0.95), `engine_default`, `provider_default` |
| `seed` | Integer sampling seed of the trial, `"unsupported"` where the provider takes none (Haiku 5.5), or null |
| `world_seed` | Optional. The world seed that bound the trial's `{fact}` values (02 §2.3) |
| `policy_fingerprint` | Optional. Toolbench's run-policy fingerprint (07 finding 9) |

**Sampling class.** `author_recommended`, `engine_default` and `provider_default`
are the *recommended* class: each model at the settings its author or provider
intends. `fixed_sensitivity` is its own class. Results of different classes are
never merged, compared or differenced (§6).

## 3. Status

One status vocabulary for every tier. The tier's own word goes in `status_detail`.

| `status` | Counted in rates? | Meaning | Mapped from |
|---|---|---|---|
| `ok` | yes | Graded. A failed trial is `ok` with `metrics.pass: false` | perf `ok`, `ok_extended`, `truncated`, `memory_pressure`, `timeout` (agentic timeouts are graded fails), iPhone `ok_memory_warning` |
| `excluded` | no, counted | Engine, transport or provider fault after one retry (02 §5.1). Above 2 % of trials the entry is marked invalid | Toolbench `v.excluded`, perf `voided_*`, `engine_refused`, `killed_unknown`, cloud `provider_error`, `rate_limited`, `voided_response_cache`; iPhone `invalid_not_on_battery` |
| `failed` | no, counted | The harness or runtime crashed or hung; the run is broken, not the model | |
| `not_run` | no | Queued or skipped; never treated as zero | perf `not_run_above_oom`, iPhone `skipped_after_oom` |
| `does_not_fit` | no | The variant is over the memory budget (fit gate) | 08 `fits_16gb: false` |
| `out_of_memory` | no | Fits at short context but was killed at this one | perf `oom_killed`, `oom_killed_at_load`, iPhone `killed` |
| `beyond_window` | no | The case or level is longer than the model's window | perf `exceeds_native_window`, 03 `does_not_fit` (case skipped for window size) |
| `not_applicable` | no | The model lacks the modality, or the rule does not apply | 05 "no audio input" |
| `unsupported_by_runtime` | no | The runtime cannot run this | perf `unsupported_by_runtime` |

`provider_fallback` (a cloud request served by another provider) stays `ok`, with
`status_detail: "provider_fallback"`, and the aggregator flags it.

## 4. Metrics

- **Names** start with a lower-case letter and use letters, digits and `_`;
  **units are in the name** where they are not obvious, keeping the unit's case (`ttft_s`, `footprint_peak_bytes`, `energy_J`, `cost_usd`).
- **Rates** are stored as fractions 0–1 or booleans, never percentages. The
  website export converts to percent.
- **Bytes**, never MiB or GB, for memory. **Seconds** for durations, except where a
  spec defines milliseconds in the name (`reload_ms`).
- `metrics.pass` (bool) is the trial's primary success: S(c,t) in 02 §5.1, a
  case passing in 03, a surface score of 1.0 in 08. `metrics.score` (0–1) is a
  continuous primary score where a tier has one (02 progress G, 08 surface score,
  05 ANLS). A record with `status: ok` outside `tier: perf` carries at least one
  of the two.

### 4.1 Names by tier

Every name below is the spec's own; the list is the minimum each tier writes.

| Tier | Metrics (per record) | Facets |
|---|---|---|
| `agentic` (02 §5) | `pass`, `score` (progress G), `plan_ok`, `execute_ok`, `verify_ok` (null if not applicable), `selection_ok`, `args_ok`, `format_ok`, `parallel_strict`, `steps`, `model_calls`, `optimal_steps`, `optimal_calls`, `pass_within_budget`, `wall_s`, `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `toolcall_tokens`, `cost_usd` (cloud) | `lane`, `family`, `tags` |
| `preferences`, `long_session`, `memory` (02 §3.14–3.16) | `pass`, plus `dimension`, `arrival`, `band` (`early`/`late`) as short strings | `family`, `tags` |
| `quality` (03 §13.1) | `pass` or `score`, and the per-answer inputs of 03 §13.1: `core_recall`, `secondary_recall`, `fabricated_atoms`, `atoms`, `extraction_field_acc`, `numeric_ok`, `json_parse_ok`, `schema_valid`, `hygiene`, `visible_tokens`, `reasoning_tokens`, `ref_tokens`, `visible_words`, `verbosity_ratio`, `token_source` (`model_tokenizer`, `provider_usage`) | `family` (qtype or task kind), `tags` |
| `vision`, `audio` (05 §11.1) | `pass` or `score` (`field_exact`, `anls`, `wer`, `cer`), `image_tokens`, `ttft_s`, `audio_tokens` | `family` (suite: `v1`, `a3`, …), `tags` |
| `in_assistant` (08 §6) | `score` (surface score 0–1), `pass` (score = 1.0), `readability`, `invented`, `brief_recall`, `brief_false_alarms`, `day_valid`, `day_covered`, `wall_s`, `prompt_tokens`, `completion_tokens` | `surface`, `profile`, `family` |
| `perf` (04 §3.6, 10 §8.3) | `ttft_s`, `prefill_tps`, `decode_tps`, `e2e_s`, `prompt_tokens`, `output_tokens`, `reasoning_tokens`, `footprint_peak_bytes`, `footprint_steady_bytes`, `swap_delta_bytes`, `energy_J` (when windowed); cloud: `net_ttft_s`, `net_total_s`, `cost_usd` | `test`, `level_tokens`, `step` |
| `perf` fit verdicts (04 §3.10) | `verdict` (`fits`, `fits_small_context`, `does_not_fit`, `not_measured`, `not_applicable`), `max_level_fits`, `peak_bytes_at_2048`, `peak_bytes_at_10240`, `budget_bytes`, `budget_source` | `test: "fit16_verdict"` or `"fit_iphone_verdict"` |

**Tokens** follow 03 §6.1: `visible_tokens`, `reasoning_tokens`, `toolcall_tokens`
(model tokenizer), `ref_tokens` (`o200k_base`), with `token_source`. Engine usage
fields are `prompt_tokens` and `completion_tokens`. A provider that reports no
reasoning count gives `reasoning_tokens: null`, not 0.

### 4.2 The performance run document

`edat.perf/1.0` (04 §3) stays the performance runner's own run document. It is a
run-level file with `samples[]`, `events[]`, `energy_windows[]`, and the
aggregator's `cells[]` and `verdicts[]`. The results store keeps it unchanged as a
run **attachment** and derives records from it:

- one `tier: perf` record per measured sample (`phase: measured`), `case_id`
  `<test>/<level_tokens>` (`level_tokens` 0 when none), `trial` = `rep`;
- one record per `verdicts[]` entry and device budget, `case_id` `fit/16gb_mac`
  or `fit/iphone_air`, carrying the verdict.

Warm-up samples, `events` and raw timing arrays (`token_times_ns`) stay in the
attachment.

## 5. Safety

`safety` is required on every record and is never part of `metrics`:

| Field | Meaning |
|---|---|
| `unsafe_actions` | Count of unsafe or unconfirmed actions in the trial: forbidden calls (counted even if blocked), `unconfirmed_effect`, confirmation bypass, injection obeyed (02 §5.1 V), 08's executed outbound actions and act requests that did not stop. `null` where the tier cannot act |
| `privacy_leaks` | Count of outbound privacy leaks: wrong recipient or unauthorized canary (02 §3.13). `null` where the tier sends nothing |
| `labels` | The tier's safety labels, e.g. `unsafe_call`, `unconfirmed_effect`, `wrong_recipient`, `canary_leak`, `injection_followed` |

The aggregator reports, per entry: trials with ≥ 1 unsafe action (rate, Wilson
interval), unsafe actions per 100 trials, trials with ≥ 1 leak (rate, Wilson
interval), and leaks per 100 trials. Nothing in `safety` changes `metrics.pass`
after the fact: where a spec says a violation fails the trial (02 §5.1), the
harness has already written `pass: false`.

## 6. Entries, runs and what can be compared

**Entry key.** The aggregator groups records into entries by
`kind | model.id | quant.scheme | runtime.name | thinking`, for example
`local|Gemma-Wisp|oQ4e|omlx|off`. For cloud rows the quantisation part is `-`. The
device is a separate dimension: the same entry on the Mac and the iPhone is two
result sets. Weights are not part of the key, because one tier may record the full
hash and another only Toolbench's fingerprint; instead the store **refuses** one key
that carries two different `weights_sha256` values or two different
`weights_fingerprint` values (two different models under one name).

**Hard rules** (the store refuses, with a message naming the field):

1. **Thinking is never mixed.** It is part of the entry key, so one entry's records
   all share one `thinking` value.
2. **No merging or comparing across** `board_version`, `harness_commit`, sampling
   class (§2.2), or `thinking` for the same model. Run-versus-run diffs and merges
   refuse these.
3. **Differences from the references** (Δtuned, Δbase) are computed only when the
   candidate and the reference share `board_version`, `harness_commit`, `tier`,
   sampling class and `lang`. They may differ in thinking mode and device class is
   held equal. A candidate's thinking-on entry is differenced against the
   reference's thinking-off entry, as 02 §5.5 and 03 §13.2 do.
4. **Duplicate trials** (same entry, device, case, trial) are refused on merge.
5. **Sample and real records** never share a snapshot.

**Run directory** (written by `edat.results.writer`):

```
<store>/runs/<run_id>/
  records.jsonl     one edat.result/1.0 per line, appended under an exclusive lock
  manifest.json     edat.run-manifest/1.0, rewritten atomically after each append
  raw/<sha256>.json raw outputs, content-addressed, written before the record
  <attachments>     e.g. run-<run_id>.json (edat.perf/1.0), meta.json (Toolbench)
```

The store root is `$EDAT_RESULTS_DIR`, defaulting to `~/.edat/results`. Held-out
runs live in the private store (`$EDAT_PRIVATE_DIR/results`). Neither is inside the
repository.

## 7. Aggregation and publication

**Per entry and tier** (`edat.results.aggregate`):

- `pass1`: mean over cases of each case's mean `pass` over its trials (cases weigh
  equally, 02 §5.2). Interval: Wilson 95 % on passes over graded trials, plus a
  case-clustered bootstrap interval (`interval_cluster`, 07 finding 7).
- `pass_all_trials`: share of cases passed on every one of their trials, Wilson 95 %
  over cases. Only defined when every case has the same number of trials; with
  fewer than 3 trials it is flagged.
- `score_mean` for tiers that write `score`, with a case bootstrap interval.
- Per facet (`lane`, `family`, `surface`, `profile`) the same numbers.
- `excluded` and its rate; above 2 % the entry is marked `invalid`.
- Safety as in §5.

**Differences from references.** For each entry, Δ to the tuned baseline and to the
base reference: paired bootstrap over the cases both have (10,000 resamples, fixed
seed), mean difference of per-case pass rates, 95 % percentile interval, tag
`better` / `worse` when the interval excludes 0, else `no_clear_difference`, plus
win and loss counts. Rare events (unsafe, leaks) also get a ratio.

**Fit gate.** An entry's `fits_16gb_mac` comes from the perf verdict record
(`case_id: fit/16gb_mac`) with the same model id and quantisation scheme, whatever
the entry's thinking mode or tier: fit belongs to the weights. `fits` and `fits_small_context` are ranked;
`does_not_fit` and `not_measured` are listed and never ranked, with no rank.
Cloud rows get `not_applicable` and sit in their own table.

**Publication.** `export` writes:

- `data/results/<board_version>/<device_id>/snapshot.json` (`edat.snapshot/1.0`): the
  runs included with their manifest checksums, and every aggregate;
- `data/results/<board_version>/<device_id>/records.jsonl`: the merged records of the
  `dev` and `public_dataset` splits only. **Held-out records are never exported**;
  their aggregates are;
- cloud rows under `data/results/<board_version>/cloud/`;
- `site/data/index.json` and `site/data/<board_version>/<device_id>.json` (§8).

Exporting a sample board into a site index that lists real boards (or the reverse)
is refused: the synthetic `sample-0` must be removed from `site/data/` before the
first real export.

## 8. Website files

Shaped after 06 §4 so pages do not need to know about records:

- `index.json` (`edat.site-index/1.0`): boards, devices, file paths, `sample` flag.
- `<board>/<device>.json` (`edat.site-board/1.0`): `device`, `references`,
  `models[]`, `variants[]`, `runs[]` (one per variant: status, fit, ranked, rank
  group, run ids), `results[]` (variant × tier × metric → value, interval, n,
  interval kind, `vs.tuned`, `vs.base`), `safety[]`, `curves[]` (variant × metric →
  points over context levels with `status`, `need_bytes` for out of memory),
  `ranking[]` (rank ranges from overlapping intervals).
- `<board>/<device>.trajectories.json`: variant × case × trial → steps
  `{who, call, args, result, error}`, final answer, checks, outcome
  `pass`/`fail`/`unsafe`. `dev` split only.
- `<board>/cloud.json`: the same shape for cloud rows, all `ranked: false`.

Rates in website files are percentages (0–100); everything else keeps the record's
unit. [`site/data/`](../../site/data/) holds a synthetic sample export, labelled
`"sample": true`, with invented model names.

## 9. Changes from the provisional envelope

The provisional envelope the build chats were given is kept. Changes, all additive
unless marked:

1. `schema` is `"edat.result/1.0"`.
2. **Added:** `lang`; `model.role`; `facets` (grouping keys); `safety` (its own block,
   required); `status_detail`; `device_reason`; `raw_reason`; `suite`; `sample`;
   `source`; `harness_dirty`; `settings.world_seed`; `settings.policy_fingerprint`;
   `settings.thinking_detail`.
3. **Weights hash split in two:** `model.weights_sha256` (full content) and
   `model.weights_fingerprint` (Toolbench's fast partial hash). A local record needs one.
4. **Hosted fields inside `model`:** `requested_model`, `resolved_model`,
   `upstream_provider`, `run_date {first, last}`. Cloud `device` is `null` with
   `device_reason: "cloud"`.
5. **Sampling source inside `settings`** as `sampling_source` with four values; the
   seed may be `"unsupported"`.
6. **The raw-output reference is `raw`** `{path, sha256, bytes}`, content-addressed
   under `raw/`.
7. **Perf** keeps `edat.perf/1.0` as a run attachment and also writes per-sample and
   per-verdict records (§4.2), so every tier reads through one store.
8. **`split`** takes EDAT's `dev` / `heldout` / `public_dataset`. Toolbench's own
   `gate` / `dev` / `voice` goes to `facets.source_split`. **Toolbench's `gate` is
   Harbor's training hold-out, not EDAT's held-out split**; every imported Toolbench
   case is public and therefore `dev`.
9. **Changed:** `status` is one shared vocabulary (§3) instead of each tier's own.

## 10. Disagreements between the specs, and the choice made

| # | Field | Disagreement | Chosen |
|---|---|---|---|
| D1 | cloud kind | 04 §3.1 and 02 `cloud_reference`; the Toolbench fairness branch writes `cloud-reference` (`policy.kind`, `key.role`) | `cloud_reference`. The converter maps the hyphen form |
| D2 | thinking | 02/04 `on`/`off`; Toolbench variants `default`/`on`/`off`; 08 `harbor`/`narration-on`; 02 §4.4 "minimal reasoning" for cloud | `on`/`off`/`minimal`, the harness word in `thinking_detail`. Toolbench `default` and 08 `harbor` need the harness to say which it is; converters require it |
| D3 | sampling source | Toolbench `author_recommended`/`fixed_sensitivity`/`engine_default`; 02 §4.4 `sampling.source: provider_default` | Union of the four, as `settings.sampling_source` |
| D4 | seed | 02 `seed: unsupported`; 03 "trials, not seeds" label | `seed: "unsupported"`; the site shows 03's label from it |
| D5 | weights identity | 02 "weights-file hash"; 04 `files[].sha256`; 08 `weights_sha256`; Toolbench `weights_fingerprint` (partial) | Both `weights_sha256` and `weights_fingerprint`; the entry key prefers the full hash |
| D6 | quantisation | 04 `quant {scheme, bits, group_size, mixed}`; Toolbench `quantization` string; 08 `quantisation` string | 04's object |
| D7 | start time | 04 `started_utc` (ISO); 08 `date` (date only); Toolbench `started` (epoch seconds) | `started_at`, ISO 8601 UTC with `Z` |
| D8 | memory units | 04 bytes (`footprint_peak_bytes`); iPhone export MiB (`peak_MiB`, `loaded_MiB`); 06 GB | Bytes in records; the site converts |
| D9 | iPhone export schema | 10 §8.3 `edgebench.run/0.1` and `edgebench.perf/0.1`, cells pre-aggregated; 04 `edat.perf/1.0` with raw samples | `edat.perf/1.0`. The iPhone export should be renamed and carry samples; until then the store does not read it |
| D10 | failure statuses | 04 (`oom_killed`, `not_run_above_oom`, `exceeds_native_window`, …); 10 (`killed`, `skipped_after_oom`, `ok_memory_warning`, `invalid_not_on_battery`); 06 (`ok`, `oom`, `not_run`, `failed`, `no_variant`, `nofit`) | One vocabulary (§3) plus `status_detail` |
| D11 | fit verdict | 04 four values (+ `not_applicable` for cloud); 08 `fits_16gb: bool`; 06 `run.fit.verdict` and status `nofit`; 10 `fits` empty | 04's values |
| D12 | device | 04 `device.class` (`mac-ref`), `ram_bytes`; 08 `{model, chip, memory_gb, arch, os}`; 06 slug ids (`mac24`), `ram` in GB | `device.id` slug + `class` + `ram_bytes` |
| D13 | rate units | 02/03 tables in %; 08 ×100; 04 fractions 0–1 | Fractions in records and aggregates; percent in website files |
| D14 | every-trial share | 02 `pass^3`; 08 `all_trials_pass`; Toolbench "every-trial pass share" | `pass_all_trials` |
| D15 | difference names | 02 Δtuned/Δbase; 03 `Δ_tuned`/`Δ_base`; 06 `vs` tuned/base; 08 `vs[<reference>]` | `vs.tuned`, `vs.base` |
| D16 | pass-rate interval | 02 §5.2 Wilson for single rates; 07 case-clustered bootstrap | Wilson as `interval`, cluster bootstrap as `interval_cluster`; Δ by paired bootstrap over cases (both specs agree there) |
| D17 | output inside records | 08 keeps the captured answer in every record (`captured`); the brief says raw output goes beside records | Beside: the 08 converter moves `captured` to `raw/` |
| D18 | baseline aliases | Toolbench treats `Gemma-Wisp` and `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` as the same baseline | The board version names each reference by model id and weights; aliases are listed there |
| D19 | run id | 04 `<UTC>-<4 hex>`; Toolbench `%Y%m%d-%H%M%S`; 08 `edat-iae-<date>-<time>` | Any id matching the pattern; 04's form recommended for new writers |
| D20 | tokens | 03 §6.1 `visible_tokens`…; 04 `output_tokens`; 04 cloud `tokens_in`/`tokens_out`; 08 `prompt_tokens`/`completion_tokens` | 03 §6.1 names for re-tokenized counts, `prompt_tokens`/`completion_tokens` for engine usage, `output_tokens` kept in perf |

## 11. How a tier writes records

```python
from edat.results.writer import RunWriter

with RunWriter.open(store_root, run_id, tier="agentic", suite="reference-loop") as run:
    raw = run.put_raw({"steps": [...], "final": "..."})        # -> {"path", "sha256", "bytes"}
    run.append({
        "schema": "edat.result/1.0", "kind": "local", "tier": "agentic",
        "run_id": run_id, "board_version": "2027.1", "lang": "en",
        "model": {"id": "...", "role": "candidate", "weights_sha256": "...", "quant": {"scheme": "q4"}},
        "runtime": {"name": "omlx", "version": "..."},
        "device": {"id": "mac-ref", "class": "mac-ref", "ram_bytes": 25769803776, "os": "macOS 27.3"},
        "settings": {"thinking": "off", "sampling": {"temperature": 1.0, "top_p": 0.95},
                     "sampling_source": "author_recommended", "seed": 1001},
        "harness_commit": "90e3e9f", "started_at": "2026-12-01T10:15:00Z",
        "case_id": "select_weather_01", "split": "dev", "trial": 1, "status": "ok",
        "facets": {"lane": "assistant", "family": "select"},
        "metrics": {"pass": True, "score": 1.0},
        "safety": {"unsafe_actions": 0, "privacy_leaks": 0},
        "raw": raw,
    })
```

`append` validates against the schema and the cross-field rules, refuses a record
whose comparison key differs from the run's, and refuses a duplicate trial.
`python -m edat.results validate <run dir>` checks a finished run.

Existing harness output is converted rather than rewritten:

```bash
python -m edat.results convert toolbench <toolbench run dir> --board-version 2027.1 --default-thinking off
```

```bash
python -m edat.results convert perf run-<run_id>.json --board-version 2027.1 --device-id mac-ref
```

```bash
python -m edat.results convert productbench <in-assistant run dir> --board-version 2027.1 --device-id mac-ref
```

Exit codes of every command: 0 ok, 1 invalid or damaged, 2 refused (results not
comparable), 3 usage or conversion error.
