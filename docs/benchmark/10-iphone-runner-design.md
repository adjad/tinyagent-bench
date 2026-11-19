> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 10 · tinyagent-bench iPhone runner: design, build and device-run plan

Status: 2026-11-18, revision 2. This revision follows the benchmark's naming (tinyagent-bench; schema ids keep their `edat.*` form until the repository renames them), the performance spec's runner contract (`04-perf-energy-memory-spec.md` §2–§3, revisions 2–6: raw samples only, prompt pack, kill rules), the second reference model (base Gemma 4 E4B), the iPhone fallback variant (04 §1.6) and the phone parity items (`02-agentic-spec.md` §6).

The app and its measurement package are on branch `claude/bench-iphone-runner`, in the new folder `ios-bench/`, based on Harbor's `stage-0.1.7` at `01f8651`.

**Nothing has run on the iPhone.** Every number about the phone below is either a published report (linked) or a prediction, and is labelled as such. Paths are relative to the repository root.

The research behind this file (MLX Swift; iOS memory, energy and install) is summarised in the sections below, with each source linked where it is used.

---

## 0. The answer in brief

- **What it is.** **tinyagent-bench** is the app's display name and the prefix of every export file. It measures and records **raw samples**: one `edat.perf/1.0` record per run, in the shape of 04 §3. It computes no statistics, cells or verdicts; the Mac aggregator does that for the Mac and the phone alike. Phone-item results are written as `edat-phone-result/v1` lines. The schema ids live in one place, the `EDAT` enum in `Sources/EdgeBenchCore/PerfRecord.swift`.
- **Runtime.** MLX Swift directly, with the exact pins Harbor's iPhone app builds with:
  - `mlx-swift-lm` 3.32.3
  - `mlx-swift` 0.32.3
  - `swift-transformers` 1.3.5

  It loads the oMLX folder text-only, with mixed 4/5/6-bit quantisation and Gemma 4's chat template. The MLX buffer cache is fixed at **256 MiB** and recorded. FoundationModels (iOS 27 only) hides per-token times, so it is not used.
- **Models.** **Gemma-Wisp** (`gemma-4-E4B-it-r5-step-01800-oQ4e-mtp`) is the baseline. **Base Gemma 4 E4B** (`gemma-4-E4B-it-oQ4e-mtp`) is the second reference, run in the session bracket (Wisp, base … base, Wisp). Together they need about **11.2 GB** on the phone (§2).
- **MTP head: not used on the phone in v1.** Phone decode is speculation-off and compares with the Mac's spec-off column (§1.3).
- **Will the baseline load?** Probably, with little room. A text-only load materialises 4,373 MiB for Gemma-Wisp and 4,193 MiB for base. The only reported per-app limit on a 12 GB iPhone is about 6,144 MB with the Increased Memory Limit entitlement. Prediction: up to 10k fits, 32k is likely, **64k is likely to be killed** (§3.4).
- **If the shipped quant is killed, that kill is the result.** A smaller quant of the same weights then runs as a clearly named **variant** (`role: "variant"`, `variant_of: "Gemma-Wisp"`). The app handles both outcomes (§3.5).
- **Kills are results** (04 §2.8):
  - `inflight.json` is fsync'd before each sample;
  - the footprint log is flushed every 250 ms;
  - at the next launch, a leftover `inflight.json` becomes `oom_killed`, `oom_killed_at_load` or `killed_unknown`, with the last 2 s of footprint as evidence;
  - the sample is retried once, and a second kill is final. Higher levels then become `not_run_above_oom`.
- **Energy.** No meter: the runner records battery level, state and time with every sample. Every derived iPhone energy figure is an estimate, `is_estimate: true`, computed by the aggregator (§4).
- **Quality.** The app reads `edat-phone-item/v1` JSONL. It renders each prompt with the model's own template (**messages mode**, which gives the published numbers) or tokenizes the Mac-rendered prompt (**rendered mode**, a diagnostic). It writes `edat-phone-result/v1`. Tool calls come from mlx-swift-lm's own `gemma4` parser with recovery disabled. The local pass/fail is for display only; the Mac re-grades (§6).
- **Built and verified within the rules** (§9):
  - the core package: 46 unit tests against a stub generator, all passing;
  - the SwiftUI app;
  - Simulator and generic-device builds;
  - a Simulator run to an exported zip.

  **Not verified:** anything that needs the phone (§10).

---

## 1. Runtime

### 1.1 Packages and versions

| Package | Version | Why |
|---|---|---|
| [mlx-swift-lm](https://github.com/ml-explore/mlx-swift-lm) | exact 3.32.3, `traits: []` | Gemma 4 (`gemma4`, `gemma4_text` in `LLMModelFactory.swift:63-65`). The `gemma4` tool-call format and `ToolCallProcessor` with `ToolCallRecoveryPolicy` (`Tool/ToolCallProcessor.swift`, `Tool/ToolCallRecoveryPolicy.swift`). Traits off, as Harbor's app has them. |
| [mlx-swift](https://github.com/ml-explore/mlx-swift) | exact 0.32.3 | What 3.32.3 resolves. `Memory.activeMemory`, `cacheMemory`, `peakMemory` (its setter resets the peak), `cacheLimit`. |
| [swift-transformers](https://github.com/huggingface/swift-transformers) | 1.3.5 | Tokenizer and Jinja chat template, read from the folder. |

`ios-bench/Packages/EdgeBenchMLX/Package.resolved` is a copy of HarborLing's. That file, `service/setup/catalog.py` and the Toolbench case files are identical at Harbor's public `46da8eb5` and staging `01f8651`, so nothing staging-only is in `ios-bench/`. `service/tools/registry.py` does differ between the two, but only in `classify_tool_outcome`, not in the tool schemas the stand-in items use. 3.33.0 (2026-11-17) is source-breaking; revisit after the first phone numbers.

### 1.2 Loading the oMLX folder

- **Factory.** `LLMModelFactory.shared.load(from:using:)` loads from a local directory with no network.
  - Sanitize drops the vision and audio towers and the k/v projections of the KV-shared layers.
  - The safetensors loader reads headers only; the model is realised before `load` returns, so the load time includes reading the weights.
- **The one blocker, fixed in the app.** The folder's index lists the `language_model.mtp.*` tensors. `verify: [.all]` should refuse them; this is inferred from the code, not run. `TextOnlyFolder` builds `<folder>/.edgebench-textonly/` from symlinks plus an index without the MTP entries. No weights are copied.
- **Prompts.**
  - Perf tests feed the prompt pack's token ids directly (`LMInput(tokens:)`), so tokenizer and template are identical to the Mac (§5.2).
  - Phone items in messages mode render through swift-jinja with `enable_thinking`. Earlier assistant tool calls are passed with their arguments as objects, which is what the Gemma 4 template iterates.
  - Rendered mode tokenizes the Mac's string as given (`addSpecialTokens: false`).
- **Generation loop.**
  - The engine drives `TokenIterator` itself, with a new KV cache per request (`prefix_cache: "none"`).
  - After the iterator's init (the prefill), it synchronises the default stream before stamping `t_prefill_done_ns`, because MLX is lazy.
  - Prefill runs in 512-token steps.
  - The 256-token perf samples ignore end-of-sequence.
  - `Memory.peakMemory` is reset before each request. Active, cache and peak bytes are read before `clearCache()` and recorded as diagnostics.
- **Tool calls.** `ToolCallProcessor(format: .gemma4, tools:, toolCallPolicy: .init(recovery: .disabled, validation: .permissive))` runs over the complete text. Parsed calls and `RejectedToolCall`s both go into the phone result, and the raw text is kept. The core's own Gemma parser (`GemmaOutput`) splits reasoning from answer and serves the stub.

### 1.3 The multi-token-prediction head, and comparability with the Mac

- **What MTP needs in 3.32.3:**
  - the target loaded with `VLMModelFactory`, which keeps the vision tower resident (+~320 MiB);
  - a separate `gemma4_assistant` drafter folder (+~152 MiB) with renamed tensors.

  That is about 0.47 GiB more against a tight budget.
- **v1: speculation off.** The record says `speculation: "none"`, and `drafted_tokens` and `accepted_tokens` are null. Phone decode compares with the Mac's spec-off column. TTFT and prefill compare directly.
- **v2 (not built):** a "Make drafter" step and a VLM-path engine, if the spec-off run shows headroom.

### 1.4 FoundationModels with an MLX backend vs MLX Swift directly

`MLXFoundationModels` needs iOS 27 and exposes usage counts only: no TTFT, no per-token times. Not usable for a benchmark.

---

## 2. Getting models onto the phone

| Route | Practical? | Notes |
|---|---|---|
| **Finder over USB** (chosen) | Yes | File sharing is on. In Finder: the iPhone → **Files** → drag each folder onto **tinyagent-bench**. It lands in Documents, and the Models screen lists it with its role. |
| `xcrun devicectl device copy to` | Yes, scriptable | `--domain-type appDataContainer`, one file at a time. |
| In-app import | Yes, slow | Folders go to `Documents/Models`, `*.json` prompt packs to `Documents/Packs`, `*.jsonl` phone items to `Documents/PhoneItems`. |
| Hub download | Not in v1 | The tuned weights exist only on the Mac, and a run should be in airplane mode. |

**Storage for the pair** (bytes from `ls -la` on the Mac):

| Folder | Bytes on the Mac | Text weights loaded |
|---|---|---|
| Gemma-Wisp `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` | 5,618 MB (5.23 GiB) | 4,373 MiB |
| Base `gemma-4-E4B-it-oQ4e-mtp` | 5,536 MB (5.16 GiB) | 4,193 MiB |
| **Both** | **11.15 GB (10.4 GiB)** | one at a time |
| A fallback variant, if needed (§3.5) | ~4.5 GB (3-bit guess) | ~3.3 GiB (guess) |

Keep about **13.5 GB free** for the pair, prompt packs (~10 MB each, estimated by 04 §2.3) and results. Add about 4.5 GB if a fallback variant is made. `Documents/Models` is excluded from iCloud backup.

The two folders are not byte-identical in layout: the second shard is 426 MB against 335 MB, and Wisp has 180 MiB more text weights. The reference-pair check already compares memory against each model's own on-disk weight ratio (04 §1.5).

**Integrity.**
- **Gemma-Wisp:** Verify SHA-256 in the app compares with Harbor's pinned hashes (`service/setup/catalog.py` `GEMMA_HARBOR_FILES`, copied into `Resources/known-models.json`).
- **Base:** the catalog has no hashes. The base entry in `known-models.json` holds the sha256 of each file as it is on this Mac, taken with `shasum -a 256` (file reads only, no model load); see §9.
- **Records:** the hashes travel in `models[].files[]`. A run hashes any file without a saved hash before it starts (about a minute per 5 GB, once; saved in the folder's `.edat-sha256.json`), so every record carries all of them and the results store can compute `weights_sha256` (sha256 over the sorted `<name>\0<sha256>\n` lines, results-record.md §2.1). Phone results carry the same value.

---

## 3. Memory

### 3.1 The per-app limit on a 12 GB iPhone

- **Apple** publishes no table and says to call `os_proc_available_memory()`.
- **Reported:** about **6,144 MB** with `increased-memory-limit` on a 12 GB iPhone 17 Pro Max ([forum 834805](https://developer.apple.com/forums/thread/834805)), and about **3.3 GB** without it ([SideStore #1616](https://github.com/SideStore/SideStore/issues/1616)).
- **The app records the real budget at run creation** as `device.app_budget_at_start_bytes` (footprint + available).
- **Balloon test.** The `balloon` plan measures the budget the spec's way (04 §2.9):
  - allocate 64 MiB shared `MTLBuffer`s, writing every page;
  - log each step, fsync'd, until iOS kills the app;
  - repeat three times; `budget_bytes` is the median of the last survived footprint.

  It is disabled in the Simulator, where it would only fill the Mac's memory.

### 3.2 The entitlement and a free team

- `App/EdgeBench.entitlements` asks for `com.apple.developer.kernel.increased-memory-limit` only.
- Community reports say Xcode with a free Personal Team keeps it; Apple has not confirmed this.
- If signing refuses it, `EDGEBENCH_ENTITLEMENTS = App/EdgeBench-no-entitlements.entitlements` in `Local.xcconfig` builds without it.
- The granted set, read from `embedded.mobileprovision`, is recorded in `device.entitlements`.

### 3.3 Reading memory inside the app

| Field | Source | Notes |
|---|---|---|
| `footprint_*_bytes` | `task_info(TASK_VM_INFO).phys_footprint` | Read every **50 ms (20 Hz)** during each sample. Start, peak, steady (median during decode) and end. Every tick goes to `mem-<run_id>.jsonl` (`t_ns`, `footprint`, `available`, `sample_id`), flushed and fsync'd at least every 250 ms. |
| `footprint_peak_ledger_bytes` | `ledger_phys_footprint_peak` (TASK_VM_INFO revision ≥ 2) | Kept when it rose during the sample. The field is in the SDK; its behaviour on iOS is unverified. |
| `available_min_bytes` | `os_proc_available_memory()` | Read with each tick. |
| `mlx_active_bytes`, `mlx_cache_bytes`, `mlx_peak_bytes` | `Memory` after the request; peak reset before | Diagnostics only. |
| `memory_warning` | `didReceiveMemoryWarningNotification` | Written to `events.jsonl` at once, with the running sample's id. |

There is **no memory gate** before a load (04 §2.1): a kill is the measurement. The pre-flight screen only warns.

### 3.4 Will the baseline load, and up to which level?

This is a prediction from the folders' tensor sizes and the KV layout:
- **KV:** about 16 MiB per 1k tokens, from 4 own-KV full-attention layers × 2 KV heads × 512 dims × K+V in bf16;
- **runtime, tokenizer, Metal and app:** a guessed 250–450 MiB;
- **MLX cache:** up to 256 MiB.

| Level | Gemma-Wisp predicted peak | vs ~6,144 MB |
|---|---|---|
| 512–2,048 | ~4.8–5.1 GB | fits |
| 10,240 | ~5.0–5.3 GB | fits |
| 32,768 | ~5.4–5.8 GB | likely fits |
| 65,536 | ~5.9–6.5 GB | **likely killed** |

Base is about 180 MiB lighter, so it could pass where Wisp is killed. The reference pair then differs in its highest level that fits, which 04 §1.5 flags; re-run the balloon and that level.

The 256 MiB cache (04 §2.1) costs headroom compared with the 64 MB a Gemma 4 E2B report used to avoid jetsam ([mlx-swift-lm #343](https://github.com/ml-explore/mlx-swift-lm/pull/343)). It is fixed for comparability and recorded.

### 3.5 If the shipped quant does not fit (04 §1.6), designed for both outcomes

1. **The kill is recorded.** Gemma-Wisp at oQ4e ends `oom_killed_at_load`, or `oom_killed` at 2,048, and its `models[].status` says so. That is the recorded iPhone result for the baseline.
2. **The fallback runs as its own entry.**
   - Make a smaller quant of the same weights on the Mac with oMLX's quantiser.
   - Name its folder `Gemma-Wisp-<quant>-iphone`, for example `Gemma-Wisp-q3-iphone`.
   - The app classifies any folder ending `-iphone` whose name starts with `Gemma-Wisp` (or contains `-r5-`) as `role: "variant"`, `variant_of: "Gemma-Wisp"`, labelled "Gemma-Wisp, q3 (iPhone fallback; shipped quant did not fit)". This is `ModelCatalog` in `ModelVariant.swift`, unit-tested.
   - Base gets the same treatment: `gemma-4-E4B-it-<quant>-iphone` becomes a variant of `gemma-4-E4B-it-oQ4e-mtp`.
   - On the Run screen the operator picks the variant for the primary slot, and the reference variant for the reference slot.
3. **The variant needs its own prompt pack** with `model_id` set to the variant's id; its tokenizer and template are the same. It also runs once on the Mac, so the quant's cost is visible as variant ÷ Gemma-Wisp.
4. **If the shipped quant does fit,** nothing changes: there is no variant and `variant_of` is null everywhere.

### 3.6 Kills (04 §2.8)

- **Before each sample:** `inflight.json` is written and fsync'd. It holds `run_id`, `sample_id`, `unit_index`, `attempt`, model, test, level, phase (`load`, `generate`, `idle` or `balloon`), `t_start_ns`, `footprint_start` and `available_start`. It is rewritten when the phase changes, for example while a model loads. During the sample the footprint log flushes at least every 250 ms; afterwards the file is deleted.
- **On every launch,** `RunStore.recoverInterruptedRuns` turns a leftover file into a sample:
  - **`oom_killed`** if a memory warning fired for that sample, or the last logged available memory was below **256 MiB**;
  - **`oom_killed_at_load`**, on the same evidence, when the phase was `load`;
  - otherwise **`killed_unknown`**.

  `kill_evidence` holds the last footprint and available memory, the warning flag, the last 2 s of the log as `[t_ns, footprint, available]`, and the phase.
- **Retry once.** The first kill is not final, and Resume reruns the same sample as `attempt: 2` with id `sNNNN-2`.
  - A second kill is final.
  - Every remaining sample of that model at that level or above becomes `not_run_above_oom`. The same-level reps get it too, with the reason in `error`, because the cell is already decided.
  - A final kill at load makes every later unit of that model `not_run_above_oom`.
  - A kill is never reported as a crash or retried more than once.
- **Background.** Leaving the app marks `inflight.json` `backgrounded` synchronously, then stops the run. That sample becomes `voided_backgrounded`, is not counted as a kill, and is rerun on Resume. iPhone apps get no GPU in the background.
- **Balloon.** A kill is the expected end of each balloon run; its log says how far it got.

---

## 4. Energy and heat

### 4.1 What the app records

| Field | Source |
|---|---|
| `battery_level_start`, `battery_level_end` (0–1) per sample; `conditions_*.battery_level` | `UIDevice.batteryLevel` (5 % steps since iOS 17 per Apple; iOS 26/27 not confirmed) |
| `battery_state`, `power_source` | `UIDevice.batteryState` |
| `thermal_start`, `thermal_end`; `thermal_change` events | `ProcessInfo.thermalState` |
| `low_power_mode` | `ProcessInfo.isLowPowerModeEnabled` |
| `brightness` | The app sets `UIScreen.brightness = 0.0` for the run (D10) and restores it afterwards. `isIdleTimerDisabled` keeps the screen on. |
| `app_state`, `uptime_s` | `UIApplication.applicationState`, `ProcessInfo.systemUptime` |
| `attest.*`, `ambient_C` | Operator checkboxes and a typed °C on the Run screen |

### 4.2 Energy is an estimate, and the aggregator's

- No current or voltage API exists, and no external meter is used (D4). The runner writes `energy_windows: []` and records only battery level and time.
- The aggregator derives `battery_pct_per_h` (test minus control) and `e_dec_drain_J_per_tok`, with `energy_scope: "whole_device_battery_drain"` and **`is_estimate: true`** (04 §2.7).
- Prefill energy on the phone is null, with reason `not_measurable_on_device`.
- `device.battery_nominal_Wh` is null on the device; the aggregator supplies Apple's figure and its source.
- An optional Power Profiler trace can be attached as a diagnostic.

### 4.3 Battery and idle plans

| Plan | What |
|---|---|
| **Battery hour** | Unplugged from 50–95 %. A burst every 3 minutes for 60 minutes, model resident, screen on at minimum brightness. A burst is one 2,048-token request in v1, not the 6-step agent replay 04 §2.6 names (§10). |
| **Battery hour, control** | The same hour with the app open and no model, run the same day. It has no samples; its `conditions_start` and `conditions_end` and UTC times are the reading. |
| **Idle resident** | 30-minute segments in ABBA order: loaded-idle, unloaded, unloaded, loaded-idle, sampled at 1 Hz. Then one wake request. |

The runner pauses below 30 %. The cooldown gate is skipped in sustained and battery tests, which measure the heat.

---

## 5. Timing, prompts and order

### 5.1 Clock and per-sample fields (04 §2.5)

- **Clock.** `mach_continuous_time()` converted with `mach_timebase_info` (`MachClock`), recorded as `clock: "mach_continuous_time"`. It counts from boot, so samples before and after a kill share one timeline.
- **Timestamps:**
  - `t_submit_ns`: just before the ids go in;
  - `t_prefill_done_ns`: after the prefill is evaluated;
  - `t_first_token_ns`: first id on the CPU;
  - `token_times_ns`: 255 deltas from the first token;
  - `t_last_token_ns`;
  - `t_end_ns`: after cache release.
- **Load tests** add `load: {t_load_start_ns, t_ready_ns, t_first_token_possible_ns, cold_verified, disk_read_bytes (null), disk_block_inputs}`.
  - The first-token-possible prompt is the pack's 8-token prompt.
  - `cold_verified` needs all three: the operator ticked "I rebooted and waited 5 minutes", this is the first load since launch, and uptime is between 5 and 60 minutes.
- **Generation:** greedy, seed recorded, exactly 256 output tokens, EOS ignored, a new KV cache every request.
- **Additive fields on every sample:** `attempt`, `device_model_id`, `os`, `power_source`, `low_power_mode`, `app_state`, `finish_reason`, `engine_prompt_s`, and `resident` for idle segments.

### 5.2 Prompt pack

- **The app reads `edat.prompt-pack/0.1`.** 04 §2.3 fixes the contents but not the layout, so this layout is proposed here and documented in `PromptPack.swift`:

  ```json
  {"schema": "edat.prompt-pack/0.1", "model_id": "Gemma-Wisp", "thinking": "on",
   "corpus": "provisional-prose-v0", "version": "0.1", "language": "en",
   "pack_sha256": "…", "tokenizer_sha256": "…", "template_sha256": "…",
   "levels": [{"level_tokens": 512, "prefix_ids": [ … ], "body_ids": [ … ], "suffix_ids": [ … ],
               "variants": [{"nonce_ids": [ … ]}, … 16 …]}],
   "first_token": {"ids": [ … 8 … ]},
   "agent_replay": {"steps": [{"step": 1, "ids": [ … ]}, … 6 …]}}
  ```

- **How a prompt is built.** Variant *i* of a level is `prefix_ids + nonce_ids[i] + body_ids[:n] + suffix_ids`, with *n* chosen so the total is exactly `level_tokens`. A variant may instead give its full `ids` list. Variant *i* is used for repetition *i*, wrapping at 16.
- **Checks on load:** the schema, `language == "en"`, and that every variant's length is exact. The file's sha256 is recorded as `prompt_pack.<model>.file_sha256`, and `pack_sha256` is the pack's own field when it has one.
- **Where the app finds packs.** Copy them to `Documents/Packs`. They are matched to a model by `model_id`: the engine id, or the folder name for a variant.
- **Without a pack,** the app builds stand-in prompts with its own template from `Resources/corpus-agent-v0.txt`, sized to within 2 tokens. These are recorded as `source: "on_device_builder"` and `corpus: "provisional-ios-agent-v0"`, and are **not comparable** with the Mac.
- **In the Simulator** the stub uses a synthetic pack (`source: "synthetic_stub"`).
- **Integration dependency:** the Mac pack builder belongs to the perf harness (04 §11) and does not exist yet. The first device run either waits for it or runs on stand-in prompts and is re-run later (§12).

### 5.3 Order (04 §2.6)

- **Rounds.** Round 0 runs one warm-up per level, ascending; it also finds kills early. Measured round *r* then runs every level once, ascending when *r* is odd and descending when it is even. Levels with fewer reps (5 at 32k and 64k) drop out of later rounds.
- **Cooldown gate before every sample:** wait for thermal nominal, polling every 5 s for up to 300 s. The wait is recorded as `cooldown_wait_s`. If the phone is still warm, the sample runs anyway with `thermal_not_nominal: true` and a `cooldown_timeout` event.
- **Gap.** A fixed 5 s follows every completed sample.
- **Reference bracket.** Gemma-Wisp, then base, then base, then Gemma-Wisp, at 512, 2,048 and 10,240 tokens, 5 reps per half, so 10 per model per level. The two middle blocks run back to back without a reload. Model switches are logged as `model_loaded` events.
- **Window and time limits.** A level beyond the model's window is `exceeds_native_window`. Each sample has a 600 s limit (`timeout`). Any other engine fault is `engine_refused` with the message in `error`, which the results store counts as excluded; the runner writes only 04's status names, so `python -m edat.results convert perf` reads every sample.

### 5.4 Built-in plans

| Plan | Steps | Guess on the Air |
|---|---|---|
| Smoke check | 2 warm loads; 512 and 2,048 × 3; 6 phone items | ~8 min |
| Cold load | 1 cold load after a reboot, run 3 times | ~2 min each |
| Warm loads | 5 | ~5 min |
| **Reference bracket** | Wisp, base, base, Wisp at 512/2,048/10,240 | ~70 min |
| Full level sweep | 7 levels, 10 / 5 reps | ~2.5 h |
| Base Gemma full sweep | 5 warm loads + 7 levels | ~2.7 h |
| Agent replay | 6 steps × 5 replays (needs a pack) | ~25 min |
| Phone parity items | every item × 3 trials, messages mode | ~90 min for 120 items |
| Phone items, rendered slice | 30 items in both modes | ~30 min |
| Sustained 20 min | back-to-back 2,048 | ~22 min |
| Battery hour / control | 60 min each, same day | 61 min each |
| Idle resident | ABBA 4 × 30 min + wake | ~2 h |
| Memory budget (balloon) | 3 kills, reopen after each | ~5 min |

The estimates assume roughly 300–600 prefill tok/s and 12–25 decode tok/s on an A19 Pro. They are unmeasured.

---

## 6. Phone parity items (02 §6)

- **Input:** `edat-phone-item/v1` JSONL, one item per line. Each item has fixed messages (with earlier tool calls and canned results), a tool list, sampling, the item's `today`, an optional `rendered_prompt`, and a deterministic `check`.
- **Where the app looks.** It uses the first `*.jsonl` in `Documents/PhoneItems` and the `.manifest.json` next to it. Otherwise it uses the bundled stand-in, `Resources/phone-items-standin.jsonl`.
  - The stand-in has 35 items converted from the earlier portable cases by `tools/export_phone_items.py`. Its manifest says **"NOT the published 120-item parity subset"**.
  - The real subset comes from the agentic harness's exporter (02 §6, B11).
  - **Any size works without an app change:** the reader has no item cap and does not enforce the manifest's `item_count`. Unknown fields are ignored, and `check.kind` is a plain string, so an unknown kind still loads; it is graded "unknown check kind" locally, and the Mac grades it properly. This covers the 120-item subset and the 40 screen-control items that 11 §0 plans in the same format. Run time grows linearly: about 15 s per item-trial, estimated (90 minutes for 120 items × 3 trials).
- **Run.**
  - **Messages mode** renders with the model's template on the phone. Its results are the published iPhone quality numbers.
  - **Rendered mode** tokenizes `rendered_prompt` as given. It is a diagnostic; an item without a rendered prompt records `status: "error"`.
  - Each trial *k* uses the item's own sampling with `seed + k`.
- **Output:** `phone-<run_id>.jsonl` lines of `edat-phone-result/v1`, with every field in 02 §6.3's example:
  - `raw_text` keeps the markup;
  - `parsed_calls` come from mlx-swift-lm's `gemma4` parser with recovery disabled; `rejected_calls` are kept;
  - `runtime.recovery` is `"disabled"`;
  - `local_pass` and `local_labels` come from `PhoneGrader`.

  Additive fields: `model_id`, `status` (`ok` / `oom_killed` / `killed_unknown` / `error`) and `error`. A kill during an item follows the same retry rule.
- **The Mac grades.** `PhoneGrader` (calls / no_call / either, `all_any_order` with bipartite matching, `forbid`, `extra_calls`, the text block, Toolbench matchers relative to the item's `today`) is for the Results screen only. `either` uses an `options` list; 02 §6.3 does not name the field.
- **The old `edgebench.portable-cases/0.1` bundle** remains only as the stand-in exporter's source. The app no longer scores it.

---

## 7. Vision and audio (hooks only)

- **Hook.** `GenerationRequest` would gain attachments, and the engine would switch to `VLMModelFactory`, the class the MTP path needs too.
- **Memory.** It would add 0.3–0.9 GiB resident, so it would be a separate entry.
- Nothing is built yet.

---

## 8. What was built

### 8.1 Layout (`ios-bench/`, all new; `ios/` and the Mac app are untouched)

| Path | What |
|---|---|
| `Sources/EdgeBenchCore/PerfRecord.swift` | The `EDAT` constants (schema ids, runner name, clock, cache limit, kill threshold) and the `edat.perf/1.0` wire types. Snake_case property names equal the JSON keys, and every field is written, `null` included. |
| `Runner.swift` | The run loop: cooldown gate, gap, battery floor, inflight, model slots and switching, kill skip rules, voiding, phone items, balloon. |
| `RunStore.swift` | Manifest, `units.jsonl` / `samples.jsonl` / `events.jsonl` / `phone-*.jsonl` / `balloon.jsonl` (append-only, fsync'd), `inflight.json`, recovery. |
| `Measurement.swift` | 20 Hz sampler with the footprint log, token timer. |
| `Plan.swift` | Built-in plans, slots, the round-0 / alternating schedule. |
| `PromptPack.swift` | Pack reader, synthetic pack, balloon allocator protocol. |
| `PhoneItems.swift` | `edat-phone-item/v1` reader, grader, `edat-phone-result/v1`. |
| `Export.swift` | The record, the zip export, the on-phone preview. |
| `ModelVariant.swift` | Folder scan and facts, `ModelCatalog` roles. |
| other files | `Probes.swift` (footprint, device, conditions), `BenchClock.swift` (`MachClock`, `ManualClock`), `Quality.swift` (matchers, Gemma parser), `StubGenerator.swift`, `PromptBuilder.swift` (stand-in prompts), `Stats.swift`, `JSONValue.swift` |
| `Tests/EdgeBenchCoreTests/` | 46 tests on a manual clock with the stub generator |
| `Packages/EdgeBenchMLX/` | MLX engine: text-only folder, token loop with the prefill stamp, `gemma4` tool parser, MLX memory |
| `App/` | SwiftUI: Models, Run, Results. Also `MetalBalloonAllocator`, the UIKit conditions probe and launch-argument automation. |
| `Resources/` | Stand-in corpus, stand-in phone items and manifest, the old portable cases (exporter source), known model hashes |
| `scripts/` | `generate_project.py` (writes `EdgeBench.xcodeproj`), `with-host-lock.sh` |
| `tools/` | `export_phone_items.py` and its tests, the earlier portable-case tools, the corpus generator |

### 8.2 The export (04 §2.10)

**Results → run → Export** writes `Exports/tinyagent-bench-<run_id>/` and zips it as `tinyagent-bench-<run_id>.zip` with the system archiver (`NSFileCoordinator .forUploading`). The share sheet offers the zip, and both stay in **Files → On My iPhone → tinyagent-bench → Exports**. The folder holds:

- `run-<run_id>.json`: the `edat.perf/1.0` record;
  - `schema`, `kind: "local"`, `run_id`;
  - `runner: {name: "iphone-mlx-swift", version, commit}`;
  - `clock`, `started_utc`, `ended_utc`;
  - `device` (`class: "iphone-ref"` for the iPhone Air `iPhone18,4`), `runtime` (packages, `mlx_cache_limit_bytes: 268435456`, `prefix_cache: "none"`);
  - `models[]` (id, entry_id, role, variant_of, headline_entry, mode `plain`, files with sha256, quant, and the additive `status`, `label`, `folder`, `tensor_bytes`);
  - `prompt_pack` per model (`language: "en"`);
  - `conditions_start`, `conditions_end`;
  - `samples[]`, `events[]`, `energy_windows: []`, `balloon`;
  - `attachments` (footprint log and phone results with sha256).

  No `cells` or `verdicts`.
- `mem-<run_id>.jsonl`;
- `phone-<run_id>.jsonl`, when the run had items;
- `manifest-<run_id>.json` (`edat.export-manifest/1`): each file's name, sha256 and bytes.

---

## 9. Verification done within the rules

The package and app were built with limited parallelism (`--jobs 2` / `-jobs 2`). No model was loaded on the Mac, no iPhone was used, and the Simulator was not erased. Verification results are not published yet.

---

## 10. Unverified without the phone, and risks

**Unverified (most are answered by the first Smoke run):**

- that mlx-swift-lm 3.32.3 loads both folders through the text-only view;
- that swift-jinja renders Gemma 4's template, tools and tool history as Python does. The rendered-slice plan tests exactly this.
- that `ToolCallProcessor(.gemma4)` over the whole text gives the same calls as streaming;
- the entitlement with a free team; the real budget (the balloon measures it); footprint after load and the KV slope;
- `ledger_phys_footprint_peak` and `ru_inblock` on iOS; the battery step size;
- that synchronising the default stream after `TokenIterator.init` captures the whole prefill;
- every time estimate.

**Deviations from 04 §2, all recorded:**

1. **Battery bursts** are one 2,048-token request, not the 6-step agent replay.
2. **The battery control** has no samples; its readings are the run's conditions and times.
3. **Same-level reps** after a final kill are `not_run_above_oom`, with the reason in `error`.
4. **`disk_read_bytes` is null.** `getrusage` gives block-input counts, recorded additively as `disk_block_inputs`.
5. **Additive events:** `paused`, `resumed`, `started`, `complete`, `model_loaded`, `voided_backgrounded` and `battery_floor` events.
6. **No MetricKit hook** yet (`metrickit_exit`).

**Risks:**

1. **Memory (highest).** About 1.4 GB of headroom over Gemma-Wisp's weights, less the 256 MiB cache. 64k is likely to be killed, and the load itself may be. Both outcomes are designed for (§3.5).
2. **Baseline size.** Each folder is about 5.6 GB, so the pair is 11.2 GB to copy over USB and store. A stalled Finder copy costs an hour; use devicectl per file then.
3. **No prompt pack yet.** Until the Mac builds packs, phone perf numbers come from stand-in prompts and must be re-run.
4. **Free-team churn.** The build expires after 7 days, and a free team allows 3 apps. Deleting the app deletes the results, so export after every run.
5. **Spec-off only;** thermals; and a stand-in item set until the 120-item subset is exported.

---

## 11. Open questions

1. **Phone basics.** Which iOS version, and how much free storage? The pair needs about 13.5 GB.
2. **Prompt-pack layout.** Will the perf harness adopt `edat.prompt-pack/0.1` as written in §5.2? It is defined only here. The Mac perf build is asked to write it or to state the layout it needs; the reader is one file, `PromptPack.swift`.
3. **Fallback quant.** If the baseline is killed, which quant should the fallback be: 3-bit oQ3e of the same weights, or 4-bit without the 5/6-bit overrides?
4. **Battery bursts.** Is a one-request burst acceptable for v1, or should a burst be the 6-step replay, which needs the pack?
5. **Same-level reps after a kill.** Keep `not_run_above_oom` with a reason, or use a new status?
6. **`either` field name.** Should 02 §6.3 adopt `options`?
7. **Air battery figure.** Should the aggregator hold the iPhone Air's nominal Wh, with its source?
8. **`quant.mixed` in the results store.** 04 §3 writes `mixed` as a bit-count map (`{"5": 84, "6": 18}`), and this runner follows it. The store's `result-record.schema.json` types it as boolean or null, so `convert perf` rejects every 04 record with a mixed quant, the Mac's included. results-record.md D6 says 04's object wins, so the fix belongs in the store's schema, or its converter could write `mixed: true` and keep the map elsewhere. Which?

---

## 12. Device-run plan (for the maintainer)

Nothing here has been done; it waits for the maintainer's go-ahead. Allow about 2 hours for the first session, mostly copying two 5.6 GB folders and the Smoke and bracket runs.

**1. Set the free team (Mac, 2 minutes).** Create `ios-bench/Config/Local.xcconfig` in the repository (it is git-ignored):

```
DEVELOPMENT_TEAM = <your Personal Team ID: Xcode → Settings → Accounts → your Apple ID → Personal Team>
EDGEBENCH_BUNDLE_ID = com.<yourname>.edat
```

**2. Connect and trust the phone (5 minutes).**
- Plug the iPhone in, unlock it, and tap **Trust**.
- In Xcode, open Window → Devices and Simulators and let it prepare the phone.
- On the phone, turn on Settings → Privacy & Security → **Developer Mode**, then confirm after the restart.
- A free team allows 3 sideloaded apps per device.

**3. Build and install (10–30 minutes the first time, while MLX compiles).**

```bash
open ios-bench/EdgeBench.xcodeproj
```

- Pick the iPhone and press Run. The scheme builds Release.
- If signing refuses the Increased Memory Limit capability, add `EDGEBENCH_ENTITLEMENTS = App/EdgeBench-no-entitlements.entitlements` to `Local.xcconfig` and expect the baseline not to load.
- On the phone, trust the developer: Settings → General → **VPN & Device Management**.

**4. Copy both models (30–80 minutes over USB).**
- In Finder, select the iPhone, open **Files**, and expand **tinyagent-bench**. Drag these one at a time, keeping the phone unlocked and plugged in:
  - the local `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` folder (Gemma-Wisp, 5.6 GB)
  - the local `gemma-4-E4B-it-oQ4e-mtp` folder (base, 5.5 GB)
- In the app, open **Models** and pull to refresh. They should show as *Gemma-Wisp (Harbor r5, oQ4e)*, role baseline, and *Base Gemma 4 E4B (oQ4e)*, role reference.
- Tap each, then **Verify SHA-256**: Gemma-Wisp should match Harbor's pinned files, and base should match the hashes taken on this Mac.
- **If the Mac has prompt packs by then,** copy them into Files → tinyagent-bench → Packs. Each model then shows "prompt pack". Without packs the runs use stand-in prompts, which are recorded as such and must be re-run later for published numbers.

**5. Prepare the conditions.**
- Charge to about 90 %, then **unplug**. Turn Low Power Mode off.
- No case, a hard surface, a 20–26 °C room. Close other apps, turn auto-brightness off, and switch airplane mode on.
- Wait 10 minutes after any reboot, except for the cold-load test. The app sets minimum brightness itself.

**6. First run: Smoke check (about 8 minutes).**
- In **Run**, choose **Smoke check**. The primary model defaults to Gemma-Wisp.
- Tick the checkboxes that are true, type the room temperature, and tap **Start**.
- Watch the live panel: step, phase, tok/s, footprint and available memory, thermal state, battery.
- If the app disappears, that is the memory limit. Reopen it: Results shows the sample as killed with its evidence; tap **Resume**, which retries it once. **That file is the most important result.** If Gemma-Wisp is killed at load twice, the run records `oom_killed_at_load`; see §3.5 for the fallback variant.

**7. Check, export and send.**
- In Results → the run, check **App budget at start**: about 6,000 MiB means the entitlement worked, about 3,300 MiB means it did not.
- Tap **Export**, then **Share**, and AirDrop the `tinyagent-bench-<run_id>.zip` to the Mac. Exports also stay in Files → On My iPhone → tinyagent-bench → Exports.
- Put the file in `data/results/iphone/`.

**8. If Smoke passes, in this order (one per sitting, export after each):**
1. **Memory budget (balloon):** about 5 minutes; reopen the app after each of the three kills.
2. **Reference bracket:** Gemma-Wisp and base, about 70 minutes, with both models chosen on the Run screen.
3. **Cold load:** reboot, wait 5 minutes, open the app, tick "I rebooted and waited 5 minutes", run it. Repeat on three separate reboots.
4. **Phone parity items:** about 90 minutes for 120 items once the real subset is in Files → tinyagent-bench → PhoneItems (35 stand-in items otherwise). Then the **rendered slice**.
5. **Full level sweep:** about 2.5 hours. Expect 64k, and maybe 32k, to be killed; reopen and Resume each time.
6. **Base Gemma full sweep:** once per new device, iOS or runtime version.
7. **Sustained 20 min**, then **Battery hour** and **Battery hour, control** on the same day, then **Idle resident**.

The free-team build expires after 7 days; rebuild from Xcode as in step 3. Runs and models stay in the app's container until the app is deleted.
