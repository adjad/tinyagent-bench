> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# EDAT · 04 · Performance, energy and memory spec

Part of **EDAT** (Edge Device Agentic Testing).

Revision 4, 2026-11-18. The project decisions are in §17, and every earlier open question is decided. Scope: **Apple silicon only.**
- **Target device class:** a 16 GB Mac. A variant that does not fit one is not ranked (§5.5).
- **Reference devices:**
  - a MacBook Pro with M5 Pro and 24 GB, running oMLX;
  - an iPhone Air with A19 Pro and 12 GB, running MLX Swift in an app.
- **Baseline model:** Gemma-Wisp.
- **Second reference model:** base Gemma 4 E4B, used as a repeatability check (§1.5).
- **Language:** English only.


This is a design only. Nothing here has been run for this benchmark.

Harbor claims come from a read of Harbor (adjad/Harbor, commit `01f8651`). Imported files keep their Harbor paths in this repository, so `service/...` and `scripts/...` below are paths in this repository. Outside claims link to pages the research agents opened. **Unverified** marks search snippets, recall, or pages that did not render. "Project notes" means earlier informal hand measurements on the reference Mac. They are not part of this repository, and their figures are withheld until results are published.

**Stability contract.** Two sections are stable for the iPhone runner implementation:
- §2 **iPhone runner requirements**;
- §3 the **result record format**.

Changes to them are additive only: new optional fields, or new enum values. A rename or removal bumps `schema` to the next major version and is announced in the changelog.

Revision 4 makes two exceptions, both decided before any record was published:
- the schema id is renamed `edgebench.perf/1.0` → `edat.perf/1.0`;
- the iPhone brightness value changes from 0.25 to 0.0.

Everything else in revision 4 is additive:
- `role: "variant"`, `variant_of`, `entry_id`, `headline_entry`;
- `prompt_pack.language`;
- `is_estimate`;
- the `fit16_emulation` test;
- `verdicts[]`.

---

## 0. The answer in brief

- **Target class: a 16 GB Mac (D1).** A model entry is ranked only if `fits_16gb_mac` is `fits` or `fits_small_context`.
  - The test: peak `phys_footprint` must stay at or below 9.0 GiB, which is 90 % of a derived 10.0 GiB budget. It is checked at 10,240 tokens; passing only at 2,048 gives `fits_small_context`.
  - It is confirmed on the 24 GB reference Mac under emulation. The admin helper wires 10.5 GiB of RAM away and sets the GPU wired limit to the 16 GB value (§5.5, §11.1).
  - Gemma-Wisp is expected to fit. That is unmeasured.
- **Reference setup.**
  - Mac: MacBook Pro M5 Pro 24 GB with oMLX. iPhone: iPhone Air (A19 Pro, 12 GB) with MLX Swift.
  - The Mac is above the under-16 GB target class. Section 10 says what its numbers do and do not say about 16 GB and 8 GB Macs.
  - In short, footprint, KV growth, admission and relative model ranking transfer. Absolute speed, energy, sustained behaviour and memory pressure do not.
- **Baseline.**
  - Every result is shown as an absolute value and as a ratio to Gemma-Wisp, measured on the same device, runtime and build, and in the same session. The baseline runs at the start and the end of each session.
  - Gemma-Wisp's MTP head inflates its decode tok/s. The headline decode ratio is therefore **candidate plain ÷ baseline plain**, with speculation off on both sides. A second column shows **as shipped ÷ as shipped**.
  - The iPhone runtime very likely has no MTP (**unverified**), so iPhone ratios are plain-only.
- **Second reference model.**
  - The second reference is base Gemma 4 E4B (`gemma-4-E4B-it-oQ4e-mtp`). It shares Gemma-Wisp's architecture, quant recipe, MTP head and template.
  - Its plain speed, memory and energy should therefore match the baseline within run-to-run noise. Every session runs the pair, as a **repeatability check**. A gap larger than the §1.5 limits points to a measurement problem, not a model difference.
  - Two differences are expected and are not errors:
    - as-shipped decode, which differs only through MTP acceptance;
    - task-level time and energy, because the two models behave differently.
- **A fit risk to plan for.** Gemma-Wisp's weights are 5.43 GB, two shards per `service/setup/catalog.py:79-80`, plus a 159 MB MTP file.
  - On a 12 GB iPhone, developers report an app memory limit of about 6,144 MB with the increased-memory-limit entitlement ([forum 834805](https://developer.apple.com/forums/thread/834805), user-reported).
  - Harbor's own app needs 5.8 GB free just to load the smaller Ling model (`ios/App/Engines.swift:81,92-96`).
  - So **the baseline may be killed on the iPhone Air at load or at low context levels**, unless the runtime keeps the per-layer embedding tables memory-mapped. That outcome is a result: the runner records it (§2.8) and the board shows it.
- **Energy.**
  - Mac: whole-device power from SMC `PSTR`, which needs no sudo, at 20 Hz. Idle is measured right before and after each window and subtracted.
  - Prefill and decode are separated by **paired requests**, (L,1) against (L,257), K times each. Slicing a sub-second trace is not used.
  - Headline: mJ per prefill token and mJ per decode token at the 2,048 level, plus J per completed task on the overall board.
  - iPhone: **per-phase joules cannot be measured without external hardware**, and battery-bypass rigs are impractical on an iPhone Air. It gets battery-drain energy over long runs instead: a coarse J per decode token and % per hour.
- **Memory.**
  - Report `phys_footprint`, never RSS. Add the `footprint` "neural" ledger if the Neural Engine is used.
  - Split it into weights, KV slope (MiB per 1k tokens) and runtime overhead.
  - Seven levels: 512 to 65,536 tokens. A level that does not fit ends with a status. Failures are results.
- **Harbor's existing script has two likely flaws** (unmeasured). `scripts/bench_ondevice_perf.py` reuses one identical prompt for warm-up and all reps (`:279`, `:285`, `:293`), so oMLX's prefix cache probably serves those reps. Its repeated "quick brown fox" filler (`:73-78`) likely inflates MTP acceptance.

---

## 1. Scope, reference devices and baseline

### 1.1 Reference devices

| Id | Device | Runtime | Role |
|---|---|---|---|
| `mac-ref` | MacBook Pro, M5 Pro, 24 GB unified memory | oMLX (version recorded per run) | Full method: speed, memory, per-phase energy, load, sustained |
| `iphone-ref` | iPhone Air, A19 Pro, 12 GB | MLX Swift in a benchmark app. Harbor's iOS app pins `mlx-swift` 0.32.3 and `mlx-swift-lm` 3.32.3 (`ios/Packages/HarborLing/Package.resolved:14-28`). | Speed, memory, kill limits, load, sustained, battery-drain energy |

Both are reference devices for this phase. Neither defines the target class alone. The Mac is above it (§10). The iPhone sits within the phone part of it. Its app budget, not its 12 GB, is what limits a model.

### 1.2 Baseline: Gemma-Wisp

- Engine id `Gemma-Wisp`; the reference Mac's folder is `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` (`service/setup/catalog.py:89-94`, `:113`).
- Quant: oQ4e, an MLX affine 4-bit group-64 recipe with some 5-bit and 6-bit tensors (the exact tensor split is recorded from the weights).
- Files are pinned by sha256 in `GEMMA_HARBOR_FILES` (`service/setup/catalog.py:75-86`):
  - `model-00001-of-00002.safetensors`: 5,001,242,218 B;
  - `model-00002-of-00002.safetensors`: 425,560,497 B;
  - `model-mtp.safetensors`: 159,139,040 B.

  Every baseline result records these hashes.
- Engine profile, inherited from base Gemma (`service/setup/catalog.py:53-57`):
  - temperature 0.8, top_p 0.95, top_k 64;
  - thinking **on**;
  - `mtp_enabled: true`, `mtp_adaptive_max_depth: 4`;
  - `max_context_window: 16384`.

  The window is an engine-profile cap, not the model's limit. The 32,768 and 65,536 levels need a benchmark profile with a larger window. Both the model's native window and the profile value are recorded.

### 1.3 How results are expressed relative to the baseline

- **Same bracket.** Every session on a device runs the baseline cells first and last (B … candidates … B).
  - The ratio uses the mean of the two baseline medians.
  - If the two disagree by more than 5 % on decode tok/s or `e_dec`, the session is flagged `baseline_drift`. Its ratios are shown greyed.
- **Ratio** `r(m, L) = m_candidate(L) / m_baseline(L)`, per metric and level. Each metric keeps its own direction: a speed ratio of 1.3 is better; a memory or energy ratio of 0.7 is better. The UI shows an arrow, not a flipped number.
- **Summary index.** Take the geometric mean of `r` over the levels 512 to 10,240 where both the candidate and the baseline are `ok`. Levels where the baseline failed show `baseline n/a`, because a ratio there has no meaning. They still show the candidate's absolute value. "Fits where the baseline does not" is a headline outcome of its own.
- **Absolute values always appear beside ratios.** A ratio never stands alone.

### 1.4 What the baseline's MTP head means for tokens per second

- **How it works.** Gemma-Wisp drafts up to four tokens ahead with its MTP head, and the main model verifies them in one pass. On oMLX the server logs report drafts per cycle and per-depth acceptance. Earlier informal readings are "+W+".
  So "as shipped" decode tok/s counts verified tokens emitted per second. It reflects the model and the decoding method together.
- **Prefill is not affected.**
- **Acceptance depends on content and sampling.**
  - Predictable filler raises it.
  - Greedy decoding usually accepts more than temperature 0.8.

  So MTP numbers come only from the realistic corpus (§6.1). The sampling setting is part of the cell.
- **Rules.**
  1. On the Mac, the baseline runs in two modes. `as_shipped` has MTP on. `plain` has MTP off, through a separate engine profile.
     - oMLX caches `model_settings.json` at startup (`service/engine/profiles.py:10`), so `plain` needs its own model id and settings entry, then a reload.
     - Proposed: a second model folder that symlinks the same weight files and has its own settings, run on a benchmark-only oMLX instance. Whether oMLX accepts that is **unverified**.
     - The user's own engine settings are never changed.
  2. **The headline decode ratio is plain ÷ plain.** This compares model and quant and is the same rule for every candidate. A candidate with its own drafter gets the same two modes. A candidate with none has `as_shipped = plain`.
  3. A second column shows `as_shipped ÷ as_shipped`, which is what a user of each model gets. Record acceptance rate, drafts per cycle and drafter memory (the MTP file is 159 MB on disk, plus its KV).
  4. Energy: the speculative path does more compute but fewer full weight reads per emitted token. `e_dec` is reported in both modes, and the plain ratio is the headline there too.
  5. iPhone: if MLX Swift has no Gemma MTP path (very likely; **unverified**), iPhone results are `plain` only, with `speculation: "none"` recorded. They are compared only with plain baselines.

### 1.5 Second reference: base Gemma 4 E4B, the repeatability pair

**The model.**
- Engine id `gemma-4-E4B-it-oQ4e-mtp`, about 5.5 GB (`service/setup/catalog.py:48-62`). Harbor keeps it on Macs that already have it but no longer offers it (`:39-42`).
- Gemma-Wisp is this model with Harbor's r5 LoRA merged in. It was quantized with the same recipe (oQ4e, group 64) and carries **base's own MTP head, copied unchanged**, with the same template and engine profile (`service/setup/catalog.py:65-67`, `:94`).
- Gemma-Wisp has pinned file hashes. Base has none in the catalog. The runner hashes base's files on import and records them; the benchmark pins those values from then on.

**Why the pair checks repeatability.**
- Merging a LoRA changes weight values, not shapes, dtypes or layer layout.
- So at a fixed prompt and a forced 256-token output, the two models do the same work. They run the same kernels and read the same bytes, with the same KV growth.
- Plain-mode speed, memory and energy should therefore agree within run-to-run noise. If they don't, the measurement drifted, not the model.

**Scheduling.**
- **Session bracket.** Every session brackets candidates with both references in ABBA order: Gemma-Wisp, base, … candidates …, base, Gemma-Wisp.
  - Per session, base runs at the 512, 2,048 and 10,240 levels, plain mode, 10 reps each.
- **Full sweep.** Base runs all seven levels, both modes, plus load and memory, once for each new combination of device, OS and runtime version.

**Pass limits** for base ÷ Gemma-Wisp, same session and device, as medians:

| Metric (plain mode) | Expected | Flag `reference_pair_mismatch` if | Likely causes when flagged |
|---|---|---|---|
| Decode tok/s at each level | 1.00 | outside 0.97–1.03 | thermal drift between bracket ends, foreign GPU work, a prefix-cache hit, a different engine profile or kernel path |
| Prefill tok/s, TTFT | 1.00 | outside 0.97–1.03 | as above, plus prompt-length mismatch between packs |
| `mem_loaded` | weight-bytes ratio (~1.00) | differs from the on-disk weight ratio by more than 1 % | different quant layout or dtype (e.g. one model's file isn't really oQ4e), or a different allocator or cache limit |
| KV slope (MiB per 1k) | 1.00 | outside 0.98–1.02 | the runtime handled the two differently: paged cache or SSD spill, preallocation, KV dtype |
| `mem_peak` per level | 1.00 | more than 2 % apart | prefill step size or cache limit differs |
| Highest level that fits (iPhone) | same level | differs by any level | the budget sits right at the limit; re-run the balloon test and both models at that level |
| `e_pre`, `e_dec` | 1.00 | outside 0.95–1.05, or outside the combined error bound if that is wider | idle drift, voided windows, a GPU cap in effect for one model |
| Warm and cold load, first-token-possible | 1.00 | warm outside 0.95–1.05; cold outside 0.90–1.10 | the file cache wasn't actually cold (cold check), SSD thermal state, background I/O |

- **Response to a flag.** The session's candidate ratios are shown greyed. The aggregator records which metric failed. A session with two or more flagged metrics is re-run.

**Differences that are allowed:**
- **As-shipped decode tok/s.** The MTP head was trained on base's outputs. Gemma-Wisp's outputs differ, so its acceptance rate can differ too.
  - The aggregator predicts the as-shipped ratio from each model's measured drafts per cycle and acceptance.
  - Only a gap of more than 5 % between the predicted and measured ratio is flagged.
  - The plain ratio, which is the headline, has no such allowance.
- **Behaviour-driven numbers:**
  - J per completed task;
  - TTFA and reasoning-token counts in unforced runs;
  - agent-replay results at shipped sampling.

  The models write different amounts and take different numbers of steps. Harbor measured a 6.5 s against 8.8 s median turn between them on its tool test (`service/setup/catalog.py:68-69`). That is behaviour, not speed. These numbers are reported per model and never used as a repeatability check.

The pair is also a useful **reference point on the board**. Its ratio is shown as "base vs tuned, same hardware". A reader can see that fine-tuning changed task results without changing cost per token.

### 1.6 iPhone fallback variant (D7)

- **The recorded result.** If `Gemma-Wisp` at its shipped quantisation (oQ4e) ends `oom_killed_at_load` or `oom_killed` at the small level (2,048) on the iPhone Air, that is the recorded iPhone result for the baseline.
- **The fallback.** A smaller quantisation of the **same weights** then runs as a separate entry:
  - `role: "variant"`, `variant_of: "Gemma-Wisp"`;
  - an id that names the quant, for example `Gemma-Wisp-q3-iphone` (the exact quant is chosen when it is built);
  - the label on the board is "Gemma-Wisp, <quant> (iPhone fallback; shipped quant did not fit)".
- **How iPhone ratios work then.**
  - iPhone ratios use this variant as their denominator, and the board says so beside every iPhone ratio.
  - Mac ratios always use the shipped Gemma-Wisp.
  - The variant is also run on the Mac once, so the quant's own cost is visible as variant ÷ Gemma-Wisp on the Mac.
- **The same rule for base.** Base Gemma 4 E4B gets the same fallback treatment if it is also killed. The reference pair check on the iPhone then runs on the two fallback variants.

---

## 2. iPhone runner requirements (STABLE)

This is the contract for the iPhone runner app. The runner **measures and records raw samples**. Statistics, ratios and fit verdicts are computed later on the Mac by the shared aggregator, so the iPhone and the Mac are summarised by one piece of code.

### 2.1 What the app must contain

- **Engine.** MLX Swift, the same packages as Harbor's iOS app or newer, with the version and commit recorded.
  - Model support needed: Gemma 4 E4B with oQ4e mixed-bit quantization for the baseline. Whether `mlx-swift-lm` 3.32.3 implements Gemma 4 and loads oQ4e is **unverified**. Check this first.
  - If it lacks support, record `status: "unsupported_by_runtime"` for that model. Don't patch the model.
- **Entitlement.** `com.apple.developer.kernel.increased-memory-limit`, as in Harbor's app (`ios/App/HarborPhone.entitlements:5`). Record the entitlement set in every run.
- **No memory gate.** Harbor's app refuses to load below 5.8 GB available (`ios/App/Engines.swift:81,93-96`). The runner must **not** gate loads. A kill is the measurement. It records `os_proc_available_memory()` before the load instead.
- **MLX cache limit.** Fixed at 256 MiB and recorded, as Harbor's Ling runtime uses (`ios/Packages/HarborLing/Sources/HarborLing/LingRuntime.swift:44,102`). The forum poster above avoided jetsam by cutting the GPU cache from 512 MB to 64 MB, so this setting changes results.
- **Model import.** Copy the model folder into the app container through Files or Finder. Verify each file's sha256 against the model manifest. Record the file list with hashes and bytes.
- **Prompt pack import** (§2.3), verified by sha256.

### 2.2 Device conditions (checked or attested before each run)

| Condition | How | Recorded as |
|---|---|---|
| Not charging | `UIDevice.batteryState == .unplugged` (monitoring enabled) | `battery_state` |
| Battery 50–95 % at start; stop if below 30 % | `UIDevice.batteryLevel` | `battery_level_start/end` |
| Low Power Mode off | `ProcessInfo.isLowPowerModeEnabled` | `low_power_mode` |
| Screen on, **minimum brightness**, idle timer disabled (D10) | The app sets `UIScreen.brightness = 0.0` and `isIdleTimerDisabled = true`. Auto-brightness off (attested). Revision 2 said 25 %; minimum was chosen (D10). | `brightness` (= 0.0), `auto_brightness_off_attested` |
| App in foreground the whole run | `UIApplication.applicationState`. A background transition voids the in-flight sample. | `app_state` per sample |
| Airplane mode, no case, hard surface, ambient temperature | Operator checkboxes and a typed °C | `attest.*`, `ambient_C` |
| Rested 10 min after boot, thermal nominal | `ProcessInfo.systemUptime`, `ProcessInfo.thermalState` | `uptime_s`, `thermal_start` |
| OS version and build, device model id | `UIDevice` / `uname` (`machine`, e.g. the iPhone Air's identifier) | `device.*` |

### 2.3 Prompts: the shared prompt pack

- **Built once on the Mac per model.**
  - The Python tooling applies the model's chat template with the generation prompt, then tokenizes.
  - Thinking flag: the model's agent default, which is on for Gemma (`service/setup/catalog.py:55`).
- **Contents.**
  - Levels 512; 1,024; 2,048; 5,120; 10,240; 32,768; 65,536 tokens, each exact to the token.
  - Each level holds `prefix_ids`, a `body_ids` array, and **16 nonce variants**: a short `nonce_ids` span placed at the start of the system message.
  - Tokens are padded or trimmed inside the body so every variant hits the exact length.
  - Each level also carries the text form of each variant, for the Mac's text API. The Mac checks that the server's `usage.prompt_tokens` equals the level.
  - The pack also has a 6-step **agent replay** trajectory, and an 8-token "first token" prompt.
- **Corpus.**
  - **English only** (D8): `language: "en"` in the pack.
  - The source is the agent-shaped text and the fixed 6-step trajectory that the agentic spec is adding (`02-agentic-spec.md`).
  - Until they land, the stand-in is English public-domain prose, versioned as `corpus: "provisional-prose-v0"`.
  - Repeated filler is never used.
- **Token counts.** Reasoning and answer tokens are counted separately, in the model's own tokenizer, following the quality spec's shared definitions (`03-output-quality-spec.md`, "Tokens per answer").
- **One pack per model entry.** Thinking-on and thinking-off entries get separate packs, because the template differs (D5).
- **Use on the iPhone.**
  - The iPhone feeds **token ids directly**, so the tokenizer and template are identical across devices.
  - Variant `i` is used for repetition `i`.
  - A **new KV cache for every request**, never reused. Record `prefix_cache: "none"`.
- **Format.** `pack.json` with a `pack_sha256`, `model_id`, `tokenizer_sha256` and `template_sha256`. About 10 MB per model, estimated.

### 2.4 Generation settings per request

- **Greedy decoding:** argmax, seed recorded, no sampling. Repeatability matters more here than shipped sampling. The battery and sustained tests (§2.7) use the same setting, for simplicity.
- **Output length:** exactly 256 tokens, with EOS ignored. Energy-pair shape B is 257 tokens; shape A is 1 token (§4.2). The runner owns the decode loop, so this is easy.
- **Speculation:** none on the iPhone unless the runtime supports it. If it does, run both modes and record `drafted_tokens` and `accepted_tokens`.

### 2.5 What to measure and how, per request (one **sample**)

Timestamps use `mach_continuous_time()` converted to nanoseconds with `mach_timebase_info`. It is monotonic and keeps counting through sleep. Record `clock: "mach_continuous_time"`.

| Field | How |
|---|---|
| `t_submit_ns` | Just before the prompt ids go into the model |
| `t_prefill_done_ns` | After the prefill forward pass finishes. Force evaluation with `eval` / `asyncEval` completion before stamping, because MLX is lazy. |
| `t_first_token_ns` | First token id materialised on the CPU |
| `token_times_ns` | Every later token's materialisation time, as deltas from `t_first_token_ns` (256 values) |
| `t_last_token_ns`, `t_end_ns` | Last token, then after cache release |
| `output_tokens` | Counted by the runner |
| `footprint_*` | A background thread reads `task_vm_info.phys_footprint` every **50 ms** (20 Hz). Store `start` (before submit), `peak` (max of samples), `steady` (median during decode) and `end`. If the SDK's `task_vm_info` has `ledger_phys_footprint_peak`, also store it as `peak_ledger`. The field is in recent SDK headers but **unverified**, so check it. Store the kernel peak before and after the request. |
| `available_min_bytes` | Minimum `os_proc_available_memory()` seen during the request, sampled with the footprint |
| `mlx_active_bytes`, `mlx_cache_bytes`, `mlx_peak_bytes` | MLX Swift `Memory` snapshot at the end. Reset the peak before each request. These are diagnostics, not the headline, because MLX's own peak excludes its buffer pool ([issue mirror](https://upd.dev/ml-explore/mlx/issues/3896), community source). |
| `thermal_start`, `thermal_end` | `ProcessInfo.thermalState`. State changes are also logged as events with timestamps. |
| `battery_level_start/end` | `UIDevice.batteryLevel` |
| `memory_warning` | True if `UIApplication.didReceiveMemoryWarningNotification` fired during the sample |
| `status`, `error` | §2.8 |

The runner also writes the footprint time series to `mem-<run_id>.jsonl`, one line per 50 ms tick, flushed at least every 250 ms. Each line holds `t_ns`, `footprint`, `available` and `sample_id`.

### 2.6 Tests, repetitions and order

| Test | What | Repetitions |
|---|---|---|
| `level` | Each level at greedy with 256 output tokens | 1 warm-up + **10** measured at levels up to 10,240; 1 + **5** at 32,768 and 65,536 |
| `load_warm` | Unload (release arrays, `Memory.clearCache()`), then load again. Time from the load call to ready, and to the first token of the 8-token prompt. | 5 |
| `load_cold` | First load after a reboot plus 5 minutes of rest. The operator reboots and taps "Cold load". | 3 (3 reboots) |
| `agent_replay` | The 6-step trajectory from the pack, fresh cache per step (no prefix reuse on the iPhone) | 5 replays |
| `sustained` | 20 minutes of back-to-back `level` requests at 2,048, then 5 s gaps | 1 run |
| `battery` | 60 minutes unplugged: one agent-replay burst every 3 minutes, idle in between, model resident. Then a matched control hour with the app open and the model unloaded, on the same day. | 1 + 1 |
| `idle_resident` | 30 minutes loaded and idle, 30 minutes unloaded, ABBA, app in front, screen per §2.2. Then one wake request. | 1 |
| `balloon` | Memory budget calibration (§2.9) | 3 |

**Order.**
- Round 0 is the warm-up round, with levels ascending. It also discovers kills early.
- Measured rounds then run every level once each: ascending in odd rounds, descending in even rounds. This spreads thermal drift across levels.
- Before every sample there is a **cooldown gate**: wait until `thermalState == .nominal`, up to 300 s, and record `cooldown_wait_s`. If the state is still not nominal, run the sample anyway and set `thermal_not_nominal: true`.
- After every sample, a fixed 5 s gap.
- **Reference bracket** (§1.5). Each session starts with Gemma-Wisp and then base Gemma 4 E4B, and ends with base and then Gemma-Wisp. Base runs at levels 512, 2,048 and 10,240, with 10 reps each, in plain mode. Base gets the full sweep once per new device, OS and runtime version.

### 2.7 Battery-drain energy on the iPhone

The iPhone gives no current or voltage to apps (**unverified**; no public API found), and no external meter is used (D4). Every iPhone energy number is therefore **an estimate from the system's own tools**: battery level, plus an optional Power Profiler trace. It is labelled `is_estimate: true` and shown on the board with an "estimate" badge.

The runner records only battery level and time. The aggregator derives the following, all with `energy_scope: "whole_device_battery_drain"`:

- `battery_pct_per_h`, test minus control;
- **`e_dec_drain_J_per_tok`**. Take the sustained run's level drop minus the control's drop over the same time, multiply by the battery's nominal energy, and divide by the tokens decoded. Nominal energy is from Apple's published spec and is recorded with its source. The iPhone Air's figure is **unverified**. The result is coarse:
  - battery level reads in steps of 1 % or more (the step is **unverified**);
  - over a run draining about 10 %, expect ±10–15 % (estimated).

  Per-phase prefill energy on the iPhone is `null`, with reason `not_measurable_on_device`.
- Optional: an iOS 26+ Xcode **Power Profiler** trace recorded alongside. Apple reports system power there as a fraction of battery per hour, and per-subsystem impact as relative values that are not comparable across devices ([Apple doc](https://developer.apple.com/documentation/xcode/measuring-your-app-s-power-use-with-power-profiler.md)). Attach it as a diagnostic file only. Its system power reads 0 while the iPhone is charging or wired, so pair the phone wirelessly.

### 2.8 Kills by the memory limit (jetsam)

Jetsam ends the app with SIGKILL. The app gets no callback and no crash log ([forum 105088](https://developer.apple.com/forums/thread/105088)). So:

1. **Before each sample**, write `inflight.json` with `fsync`. It holds `run_id`, `sample_id`, test, level, mode, phase, `t_start_ns`, `footprint_start` and `available_start`.
2. **During the sample**, the footprint log flushes every 250 ms or faster (§2.5), and a memory warning writes an event line at once.
3. **After the sample**, delete `inflight.json`.
4. **On every launch**, if `inflight.json` exists, write that sample with:
   - `status: "oom_killed"` if a memory warning fired, or if the last logged `available` was below 256 MiB;
   - otherwise `status: "killed_unknown"`.

   Attach the last 2 s of footprint lines as `kill_evidence`.
   - Optional confirmation: MetricKit's app-exit metrics count memory-limit exits per day (**unverified** API detail). When that payload arrives later, add it as `metrickit_exit` to the run.
5. **Retry once.** Relaunch and rerun the same sample.
   - If it is killed again, the cell's status is `oom_killed` and every higher level for that model and mode becomes `not_run_above_oom`.
   - A kill during load gives model status `oom_killed_at_load`. Then all levels are `not_run_above_oom`, and the load records `footprint` and `available` at death.
6. A kill is **never** reported as an app crash or retried more than once. It is the result for that cell.
7. If iOS suspends the app (it left the foreground), the status is `voided_backgrounded`, not a kill. The sample is rerun.

### 2.9 Budget calibration (`balloon`)

- **Method.**
  - Allocate `MTLBuffer`s of 64 MiB (`storageModeShared`, as MLX allocates) and write every page.
  - After each step, log the footprint and `os_proc_available_memory()`, flushed.
  - Run until the app is killed. Repeat 3 times, relaunching each time.
- **Results.**
  - `budget_bytes` = median of the last survived footprint.
  - Check: `footprint + available` should stay roughly constant and equal the budget. Record both.
  - The board uses `budget_bytes × 0.9` as the fit threshold for `iphone-ref`.
- **When to re-run:** after each iOS update or entitlement change.

### 2.10 Export

- **Files:** `run-<run_id>.json` (the §3 record), `mem-<run_id>.jsonl`, any Power Profiler trace, and a manifest with each file's sha256.
- **Delivery:** zipped and shared through the share sheet, or saved under "On My iPhone / EDAT".
- **No network upload in v1.**

---

## 3. Result record format (STABLE, shared by Mac and iPhone)

One JSON file per **run** (a session on one device). Units are in the field names. Times use the device's monotonic clock in nanoseconds; wall-clock times are ISO 8601 UTC. `null` means not measured. Every `null` that would otherwise be expected carries a `*_reason`.

### 3.1 Top level

| Field | Type | Req. | Notes |
|---|---|---|---|
| `schema` | string | yes | `"edat.perf/1.0"` |
| `run_id` | string | yes | `<UTC start>-<4 hex>` |
| `runner` | object | yes | `{name: "mac-omlx" \| "iphone-mlx-swift", version, commit}` |
| `clock` | string | yes | `"mach_continuous_time"` on both |
| `started_utc`, `ended_utc` | string | yes | |
| `device` | object | yes | §3.2 |
| `runtime` | object | yes | §3.3 |
| `models` | array | yes | §3.4. Every model used in the run, baseline included. |
| `prompt_pack` | object | yes | `{pack_sha256, corpus, version, language}` per model entry; `language` is `"en"` (D8) |
| `conditions_start`, `conditions_end` | object | yes | §3.5 |
| `samples` | array | yes | §3.6. One per request, warm-ups included. |
| `events` | array | yes | §3.7 |
| `energy_windows` | array | Mac yes, iPhone `[]` | §3.8 |
| `balloon` | object | iPhone if run | `{runs: [{last_survived_bytes, footprint_plus_available_bytes}], budget_bytes}` |
| `attachments` | array | no | `{name, sha256, kind}`: footprint log, power trace, Power Profiler trace |
| `cells` | array | no | Written **by the aggregator**, never by a runner (§3.9) |
| `verdicts` | array | no | Aggregator output, one entry per model entry (§3.10). Holds the first-class `fits_16gb_mac` field. |

### 3.2 `device`

`class` (`"mac-ref"` \| `"iphone-ref"` \| future classes), `model_id` (e.g. `"Mac17,x"`, iPhone machine id), `soc`, `ram_bytes`, `os`, `os_build`, `entitlements` (iPhone list), `gpu_wired_limit_mb` (Mac `iogpu.wired_limit_mb`, as read at `scripts/bench_ondevice_perf.py:119`), `battery_nominal_Wh` with `battery_nominal_source`, `battery_health_pct` (if readable, else null).

### 3.3 `runtime`

`name` (`"omlx"` \| `"mlx-swift"` \| …), `version`, `commit`, `packages` (map name → version, e.g. `mlx`, `mlx-lm`, `mlx-swift`, `mlx-swift-lm`), `backend` (`"metal"`), `mlx_cache_limit_bytes`, `prefill_step_tokens`, `kv_dtype`, `prefix_cache` (`"none"` \| `"omlx-default"` \| `"defeated-by-nonce"`), `ssd_kv_cache` (bool), `expert_offload` (bool), `gpu_power_cap_W` (null if none; see `service/inference/power_tier.py:3-16`), `memory_guard_ceiling_gb` (oMLX), `engine_profile` (verbatim settings object for each model id).

### 3.4 `models[]`

`id`, `entry_id`, `role` (`"baseline"` \| `"reference"` \| `"candidate"` \| `"variant"`; `reference` = base Gemma 4 E4B, §1.5; `variant` = a fallback quantisation, §1.6), `variant_of` (model id or null), `headline_entry` (bool: the mode the model ships in for agent turns, D5), `mode` (`"as_shipped"` \| `"plain"`), `files` (`[{name, sha256, bytes}]`), `quant` (`{scheme, bits, group_size, mixed}`), `architecture` (`"dense"` \| `"moe"` \| `"dense_ple"` \| `"hybrid_ssm"`), `params_total`, `params_active`, `params_embedding`, `n_experts`, `n_experts_active`, `native_window_tokens`, `profile_window_tokens`, `thinking` (`"on"` \| `"off"`), `speculation` (`"none"` \| `"mtp"` \| `"draft_model"`), `drafter` (`{id, params, bytes, max_depth}` or null), `template_sha256`, `tokenizer_sha256`. Parameter counts come from the tensor index (§8).

### 3.5 `conditions_*`

`power_source` (`"ac"` \| `"battery"`), `battery_level`, `battery_state`, `low_power_mode`, `power_mode` (Mac: `pmset` mode), `display` (`"off"` \| `"on"`), `brightness`, `thermal_state`, `uptime_s`, `ambient_C`, `attest` (`{airplane_mode, no_case, hard_surface, auto_brightness_off, other_apps_closed}`), `foreign_gpu_work` (Mac: requests from other clients seen in the oMLX log during the run).

### 3.6 `samples[]`

| Field | Type | Notes |
|---|---|---|
| `sample_id` | string | unique in run |
| `model_id`, `mode` | string | must match `models[]` |
| `test` | enum | `level`, `energy_pair`, `load_warm`, `load_cold`, `first_token`, `agent_replay`, `sustained`, `battery`, `idle_resident`, `wake` |
| `phase` | enum | `warmup` \| `measured` |
| `round`, `rep`, `order_index` | int | schedule position |
| `level_tokens` | int \| null | the target level |
| `prompt_tokens` | int | actually prefilled (Mac: server `usage.prompt_tokens`) |
| `cached_tokens` | int \| null | engine-reported prefix-cache hits |
| `variant` | int | nonce variant index |
| `shape` | `"A"` \| `"B"` \| `"C"` \| null | energy pair shape (§4.2) |
| `step` | int \| null | agent-replay step |
| `t_submit_ns`, `t_prefill_done_ns`, `t_first_token_ns`, `t_last_token_ns`, `t_end_ns` | int | monotonic ns. `t_prefill_done_ns` on the Mac comes from server timings where available, else null. |
| `token_times_ns` | int[] | deltas from first token |
| `output_tokens`, `reasoning_tokens` | int | |
| `drafted_tokens`, `accepted_tokens` | int \| null | speculation only |
| `load` | object \| null | `{t_load_start_ns, t_ready_ns, t_first_token_possible_ns, cold_verified, disk_read_bytes}` for load tests |
| `footprint_start_bytes`, `footprint_peak_bytes`, `footprint_peak_ledger_bytes`, `footprint_steady_bytes`, `footprint_end_bytes` | int | `phys_footprint` |
| `neural_bytes` | int \| null | Mac `footprint` ledger, if ANE used |
| `available_min_bytes` | int \| null | iPhone `os_proc_available_memory` |
| `swap_delta_bytes`, `compressed_delta_bytes` | int \| null | Mac |
| `mlx_active_bytes`, `mlx_cache_bytes`, `mlx_peak_bytes` | int \| null | |
| `thermal_start`, `thermal_end` | enum | `nominal`/`fair`/`serious`/`critical` |
| `cooldown_wait_s` | number | |
| `thermal_not_nominal`, `memory_warning` | bool | |
| `battery_level_start`, `battery_level_end` | number | 0–1 |
| `energy_window_id` | string \| null | |
| `status` | enum | `ok`, `oom_killed`, `oom_killed_at_load`, `killed_unknown`, `not_run_above_oom`, `exceeds_native_window`, `ok_extended`, `engine_refused`, `memory_pressure`, `truncated`, `timeout`, `unsupported_by_runtime`, `voided_backgrounded`, `voided_foreign_work` |
| `error` | string \| null | |
| `kill_evidence` | object \| null | `{last_footprint_bytes, last_available_bytes, warning, tail: [[t_ns, footprint, available], …]}` |

### 3.7 `events[]`

`{t_ns, kind, detail}`. `kind` is one of: `thermal_change`, `memory_warning`, `relaunch_after_kill`, `cooldown_timeout`, `operator_note`, `foreign_request`, `metrickit_exit`.

### 3.8 `energy_windows[]` (Mac)

`{window_id, source ("smc_pstr" | "ioreport_gpu" | "battery_accumulator" | "powermetrics" | "external_meter"), scope ("whole_device_internal" | "whole_device_external" | "gpu_rail" | "soc_rails" | "whole_device_battery_drain"), is_estimate, rate_hz, t_start_ns, t_end_ns, idle_before_W, idle_after_W, idle_window_s, sample_ids[], trace_attachment, energy_total_J, energy_active_J}`.
- `external_meter` and `whole_device_external` stay in the enums for later, but are unused (D4).
- `is_estimate` is `true` for every iPhone energy figure, and for powermetrics rail figures.
- Aggregated iPhone energy values carry `is_estimate: true` too.

### 3.9 `cells[]` (aggregator output)

One per (model, mode, test, level):
- `n_ok`, `n_failed`;
- `status`, the worst over its samples;
- for each speed metric (`ttft_s`, `ttfa_s`, `prefill_tps`, `decode_tps`, `e2e_s`): `{median, iqr, min, max, ci95_median, mean, stdev}`. `p95` is added only when n ≥ 20;
- memory: `mem_loaded`, `mem_peak`, `mem_steady`, and `kv_MiB_per_1k_fit`/`_formula` at model level;
- energy: `e_pre_mJ_per_tok`, `e_dec_mJ_per_tok`, `error_bound_pct`, `scope`;
- `ratio_to_baseline` (per metric), `baseline_drift`;
- `reference_pair_check` (run level): `{metric: {ratio, limit, pass}}` for base ÷ Gemma-Wisp, plus `reference_pair_mismatch` (bool), checked against the §1.5 limits;
- `fits` (per device class: `yes` / `no` / `estimated`).

Statistics: nearest-rank percentiles as in `scripts/release_performance.py:231-250`, and a bootstrap 95 % CI of the median.

### 3.10 `verdicts[]` (aggregator output, first-class fit fields)

One entry per model entry:

```
{
  model_id, entry_id,
  fits_16gb_mac: "fits" | "fits_small_context" | "does_not_fit" | "not_measured",
  fits_16gb_mac_detail: {
    budget_bytes,                       // B16 = 10.0 GiB unless calibrated
    budget_source,                      // "derived-v1" | "calibrated:<device>"
    wired_limit_mb,                     // the emulated value
    balloon_gib,                        // 10.5
    margin: 0.10,
    peak_bytes_at_2048, peak_bytes_at_10240,
    emulation: {
      run_id,
      levels: [{level, status, swap_delta_bytes, decode_slowdown_pct, pass}],
      pass_small, pass_working
    },
    max_level_fits_16gb
  },
  fits_iphone_air: "fits" | "fits_small_context" | "does_not_fit" | "not_measured",
  fits_iphone_air_detail: {budget_bytes, balloon_run_id, peak_bytes_at_2048, peak_bytes_at_10240, max_level_fits},
  ranked: bool,                         // false when fits_16gb_mac is "does_not_fit" or "not_measured"
  headline_entry: bool
}
```

- **Where it lives.** `fits_16gb_mac` is in every run record that has Mac cells, and it is the ranking gate on the board.
- **Runner obligations.** Runners never write `verdicts`. The Mac runner must still produce the emulation samples. These are `samples` with `test: "fit16_emulation"`, an additive enum value, and the conditions field `emulation: {wired_limit_mb, balloon_gib}`.

---

## 4. Energy

### 4.1 Definitions

All headline energies are **active energy**: energy over the window minus `P_idle × duration`. Total energy is stored as well.

| Metric | Definition | Unit |
|---|---|---|
| `e_pre(L)` J per prefill token | `E_prefill(L) / L`, a mean over the prompt. The per-token cost rises with depth. | mJ/token |
| `e_dec(L)` J per decode token | Slope of active energy against output tokens at depth L | mJ/token |
| J per completed task | Active energy across one agentic-suite task's model calls, taken as the median over successful tasks. J per attempted task is also stored. | J |

**Headline.**
- Performance board: `e_dec(2048)` and `e_pre(2048)`, with curves over the levels.
- Overall board: **J per completed task**.

Never compare numbers with different scopes. A GPU-rail figure leaves out most of the cost. On the reference Mac during Gemma decode, the GPU rail was only part of the power above idle; power conversion outside the chip and memory traffic made up much of the rest (measured internally; withheld until results are published). Stanford's Intelligence-per-Watt harness warns that rail-only numbers understate Neural Engine backends ([platform support](https://hazyresearch.stanford.edu/intelligence-per-watt/benchmarking/platform-support/)).

### 4.2 Paired requests instead of trace slicing

- **Why slicing fails.** At 2,048 tokens the reference Mac prefills in well under a second. Harbor's capped-request table lists a TTFT of 1.16 s (`service/inference/power_tier.py:10-16`). At 512 tokens prefill is a fraction of a second. A 20 Hz trace misplaces each phase edge by up to 50 ms, which is a large share of such a short phase (estimated). Getting 2 % from one window needs about 1 kHz and sub-millisecond alignment, which only an external meter gives.
- **Method.**
  - For each level L, run shape **A** (L tokens, `max_tokens = 1`) and shape **B** (L, exactly 257). Each uses a fresh nonce variant, so the prefix cache never hits.
  - Alternate them as A B A B …, **K = 20** of each, inside one energy window. Request boundaries are timestamped.
  - Then:
    - `E_A` = active energy over the A requests ÷ K, and likewise `E_B`;
    - `e_dec(L) = (E_B − E_A) / 256`;
    - `E_prefill(L) = E_A − e_dec(L)`.
  - Edge error is spread over K requests, so it falls to roughly 1–3 % at L = 512 (estimated). It is lower at larger L.
  - Optional shape **C** = (L, 65): regress E on output tokens. A residual above 5 % flags the cell.
- **Cost.** At 64k, each A request still prefills 64k tokens, so cut K to 5 there and above 10k. The error bound reflects the smaller K.

### 4.3 Mac instruments

The headline sources need no administrator rights. `powermetrics` runs only through the admin helper (§11.1). It supplies the CPU, GPU and Neural Engine rail breakdown that the IOReport counters do not publish on macOS 27. No external meter is used (D4).

| Source | Scope | Rate | Use |
|---|---|---|---|
| SMC key `PSTR` | whole device, internal | polled at 20 Hz, live | **headline**. To be validated against the battery accumulator in steady windows (an earlier informal check is (measured internally; withheld until results are published)). |
| AppleSmartBattery accumulator (IORegistry), on battery | whole device | about 1 s or coarser (**unverified**) | validates PSTR once per session; a cross-check above 3 % flags the session |
| IOReport "GPU Energy" (nJ) | GPU rail | live | diagnostic |
| IOReport CPU/DRAM/ANE energy | rails | almost never published on macOS 27 (earlier informal check; [macmon](https://github.com/vladkens/macmon) reports the same) | not used |
| `powermetrics` via the admin helper, 100 ms | CPU, GPU and Neural Engine rail estimates | 100 ms | rail breakdown (diagnostic). Apple calls the values estimates ([man page](https://keith.github.io/xcode-man-pages/powermetrics.1.html)). |

- **Helper.** A small Swift or C helper reads PSTR and IOReport GPU Energy at 20 Hz. Each reading is stamped with `mach_continuous_time` and written to JSONL. The benchmark runner reads the same clock, so no further alignment is needed.
- **Idle.**
  - Measure 60 s before and 60 s after each window, with the model resident and the display in its run state. `P_idle` is the mean of the two medians. If they differ by more than 15 %, flag the window.
  - The Mac runs energy windows with the **display off**, kept awake with `caffeinate -i`, on **battery**. In earlier informal checks, idle power drifted noticeably with the display on and was much lower and steadier with it off (measured internally; withheld until results are published). That drift was the main error source.
  - Speed and memory cells run on AC.
- **Error bound per window:**
  - `√(edge² + idle_drift² + source²)`;
  - `edge = 2·P·Δt / (K·window)`;
  - `idle_drift = |P_before − P_after|·window/2`;
  - `source` = 1 % for PSTR (a planned starting value, to be confirmed by the battery cross-check).
- **Other users of the GPU.** The window is voided if another client used oMLX during it. That has happened on the reference Mac, when another client loaded a model mid-window. Any GPU power cap in effect is also recorded. oMLX's cap is whole-Mac (`service/inference/power_tier.py:3-8`).

### 4.4 What Harbor already has

- A GPU-rail J/token table (0.270 J/token uncapped) in `service/inference/power_tier.py:10-16`.
- Per-request cap plumbing in `service/inference/omlx_client.py:490-520`.
- No energy sampling code: a search for `powermetrics|joule|IOReport|tok/J` over `service`, `scripts` and `docs` finds only that table and the cap.
- From earlier informal work: the no-sudo instrument set (PSTR, IOReport GPU Energy) and some decode energy comparisons between a GPU build and a Neural Engine build (measured internally; withheld until results are published). These are a starting point, not benchmark results.

---

## 5. Memory

### 5.1 Which number

- **Headline: peak `phys_footprint`** of the inference process.
  - Mac: oMLX's process, read with `proc_pid_rusage(RUSAGE_INFO_V4+)` from the runner, with `ri_lifetime_max_phys_footprint` as the kernel-tracked peak.
  - iPhone: in-app `task_vm_info`.
- **Add the `footprint <pid>` "neural" ledger on the Mac** when the Neural Engine is used. That memory sits outside `phys_footprint`: each loaded ANE function held a multiple of its weights there (measured internally; withheld until results are published).
- **Why not RSS.**
  - RSS counts clean file-backed pages that can be evicted, such as mmap'd weights.
  - It can miss Metal memory, which shows as `IOAccelerator` / `IOSurface` regions ([Apple, Metal memory](https://developer.apple.com/documentation/xcode/analyzing-the-memory-usage-of-your-metal-app.md)).
  - An Apple engineer recommends `phys_footprint` ([forum 105088](https://developer.apple.com/forums/thread/105088)).
  - On iOS, jetsam judges apps by footprint, so it is also the number that predicts kills.
- **Harbor's current script.** It sums parent and child RSS at 100 ms (`scripts/bench_ondevice_perf.py:131-161`) and calls the result a proxy (`:353-355`).
- **Diagnostics also stored:** RSS, `vmmap --summary` regions, MLX active, cache and peak, and swap and compressor deltas from `sysctl vm.swapusage` and `vm_stat`.

### 5.2 Weights, KV and overhead

1. `mem_loaded`: footprint after load plus the 8-token first-token prompt.
2. `mem_peak(L)` and `mem_steady(L)`: the peak during the level request, and the median during decode.
3. **KV slope**: the fitted slope of `mem_steady` against L over the levels that ran `ok`, in MiB per 1,024 tokens.
   - It is checked against the architecture formula `2 × Σ_layers(kv_heads × head_dim) × bytes`, computed per layer type.
   - Sliding-window layers are capped at their window. KV-shared layers count once.
   - Sources: [HF KV post](https://huggingface.co/blog/kv-cache-quantization), [Gemma 3 report](https://arxiv.org/html/2503.19786v1).
   - A slope more than 25 % off the formula flags the runtime: it may preallocate, use paged blocks, or have the wrong dtype.
4. `overhead = mem_loaded − resident weight bytes`. Prefill scratch is `mem_peak − mem_steady`. It grows with the prefill step size, which is recorded.

**Gemma-Wisp illustration** (architecture facts from the model's config, not measurements):
- Only 4 global layers hold their own KV, which comes to 16 KiB per token, so about 16 MiB per 1k tokens. That gives about 0.5 GiB at 32,768 and about 1 GiB at 65,536. A fixed sliding-window block (window 512) is added on top.
- oMLX keeps the per-layer embedding tables in Metal memory (size (measured internally; withheld until results are published)). This is the main reason the footprint exceeds what the architecture needs.
- For contrast, a dense model without sliding windows grows much faster. Llama 3.2 3B in fp16 is about 112 MiB per 1k tokens, so about 7 GiB at 64k (from its config as recalled, **unverified**).

**oMLX settings for memory cells:**
- **turn off the paged SSD KV cache**, or record it, because it hides KV growth;
- **expert SSD offload off**;
- record `memory_guard_ceiling_gb`.

### 5.3 Statuses (failures are results)

| Status | Meaning |
|---|---|
| `ok` | Completed |
| `exceeds_native_window` | L + 256 is beyond the model's trained window. Not run, unless the model card documents an extension; then the status is `ok_extended` and the factor is recorded. |
| `engine_refused` | oMLX's memory guard or the engine refused the load or prompt. Harbor's estimate of the guard is in `service/setup/catalog.py:160-187`. |
| `oom_killed`, `oom_killed_at_load`, `killed_unknown`, `not_run_above_oom` | iPhone kill handling, §2.8. On the Mac, an oMLX process death is recorded the same way. |
| `memory_pressure` | Mac: swap or compressor grew by more than 256 MiB during the cell. The numbers are kept but flagged. |
| `truncated` | The engine held fewer prompt tokens than were sent. Detected the way `service/inference/qualify.py:172-218` does. |
| `timeout` | Over 600 s for one request |

### 5.4 What "fits" means

`fits(class, L) = mem_peak(L) ≤ budget(class)` and status `ok`.

| Class | Budget source | Status of the verdict |
|---|---|---|
| `iphone-ref` | balloon-measured (§2.9) × 0.9 | `yes` / `no` (measured) |
| `mac-ref` | no swap growth and status `ok` | measured |
| **16 GB Mac (target class)** | `B16` = 10.0 GiB, with the footprint check and emulation run of §5.5 | `fits` / `fits_small_context` / `does_not_fit` (measured on the reference Mac under emulation) |
| 8 GB Mac (informational) | Harbor's admission estimate (`service/setup/catalog.py:160-187`: weights × 1.05 + 0.5 GiB < RAM − 3 GiB) | `estimated`; not a target class |

By Harbor's estimate, Gemma-Wisp needs about 5.98 GiB (5.6 GB × 1.05 + 0.5 GiB). That **fits a 16 GB Mac** with a ceiling of about 13 GiB, and **is refused on an 8 GB Mac**, where the ceiling is about 5 GiB (`service/setup/catalog.py:163-168`).

The 16 GB row above is superseded by the precise test in §5.5. 8 GB Macs are not a target class (D1); their row is informational only.

### 5.5 The "fits a 16 GB Mac" test (D1)

This is the gate for ranking. It is decided on the 24 GB reference Mac in two steps: a footprint check, then a confirmation run that emulates a 16 GB Mac.

**Budget `B16` = 10.0 GiB** of peak `phys_footprint` for the inference process (plus its neural ledger, if any).
- Derivation: a 16 GB Mac has 16 GiB of RAM.
  - Reserve 3.5 GiB for macOS and system services. Harbor's admission estimate reserves 3 GiB (`service/setup/catalog.py:160-173`); 0.5 GiB is added for WindowServer and caches under load.
  - Reserve 2.5 GiB for a **normal app load**: a browser with about 10 tabs, mail, messages, a chat app and an editor (estimated, not measured).
  - What remains, 10.0 GiB, is the model's budget.
- **GPU wired limit.** Also check the peak against the 16 GB default GPU wired limit. Its value is **unverified**; community sources suggest about 2/3 of RAM, roughly 10,900 MB. The tighter of the two numbers applies.
- `B16` is a constant in the aggregator (`edat.fit16.budget_bytes`). It is replaced by a measured value if anyone calibrates on a real 16 GB Mac.

**Context levels.**
- **Working level W = 10,240 tokens.** Agent turns on a Mac carry tool schemas, the system prompt and history. Harbor's own notes put its tool schemas alone at about 62 % of a 16k window, roughly 10k tokens (`docs/HARBOR_OVERVIEW.md`), so 10,240 is the agent working level.
- **Small level S = 2,048 tokens.**

**Verdict** (field `fits_16gb_mac`):

| Value | Rule | Ranked? |
|---|---|---|
| `fits` | `mem_peak(W) ≤ 0.9 × B16` (9.0 GiB), status `ok` at W, **and** the emulation run passes at W | yes |
| `fits_small_context` | `mem_peak(S) ≤ 0.9 × B16` and the emulation passes at S, but W fails either test | yes, with a "small context only" badge. A model this tight cannot hold a full agent turn with tools on a 16 GB Mac. Whether such a model stays ranked is still a proposal. |
| `does_not_fit` | fails at S, or the engine refuses the load, or the process is killed, under emulation | **not ranked**; listed as "does not fit" |
| `not_measured` | the emulation was not run | not ranked until measured |

- **The 10 % margin.** It separates `fits` from a borderline pass. A peak between 9.0 and 10.0 GiB is reported as `fits_small_context` at best.
  - The margin covers run-to-run variance in peak footprint. Allocator caching and background app growth can each add a few hundred MiB (estimated).
- **Context reach.** The record also carries `max_level_fits_16gb`: the largest of the seven levels where `mem_peak ≤ 0.9 × B16` and the emulation passes. This shows whether 32k or 64k contexts fit.

**Emulation run on the 24 GB Mac** (needs the admin helper, §11.1). It confirms the footprint arithmetic under real memory pressure:
1. **Shrink the RAM.** The helper wires a balloon of **10.5 GiB**. That is 8 GiB to take away the RAM a 16 GB Mac lacks, plus 2.5 GiB standing in for the normal app load.
   - Wired pages cannot be compressed or swapped, so macOS sees about 13.5 GiB, roughly what remains on a 16 GB Mac after the apps.
2. **Shrink the GPU limit.** The helper sets `iogpu.wired_limit_mb` to the 16 GB default (§11.1 allowlist).
3. **Shrink the engine's ceiling.** The benchmark's own oMLX instance gets a memory-guard ceiling of 13 GiB, matching Harbor's RAM − 3 GiB estimate for 16 GB (`scripts/bench_ondevice_perf.py:91-98` reads that setting).
4. **Run.** Load the model, then run levels S and W, 3 reps each, plus every higher level until the first failure.
5. **Pass rule** for a level: status `ok`, no engine refusal, no kill, swap plus compressor growth under 256 MiB, and decode tok/s no more than 10 % below the unconstrained median at that level. A larger slowdown means memory pressure, even without a swap. The emulated run's speed numbers are **never** reported as 16 GB Mac speed (§10).
6. **Restore.** The helper releases the balloon and resets the wired limit. This happens even on a crash, because the helper owns the reset (§11.1).

- **Limits of the emulation.** It cannot reproduce a smaller chip's bandwidth, its thermals, or how other apps' working sets actually behave. It is a memory gate, nothing more.
- **Gemma-Wisp, unmeasured estimate.** An earlier informal reading of oMLX's allocator peak (measured internally; withheld until results are published) suggests a verdict of `fits` is expected. The run decides it.
- **The iPhone Air** keeps its own measured budget (§2.9) and its own `fits_iphone_air` verdict, using the same three-value scale with levels S and W.

---

## 6. Speed

### 6.1 Levels and prompts

- **Levels:** 512; 1,024; 2,048; 5,120; 10,240; 32,768; 65,536 prompt tokens, exact after the chat template. They come from the shared prompt pack (§2.3), so the Mac and the iPhone prefill the same token ids.
- **Exact lengths:** Harbor's binary search (`scripts/bench_ondevice_perf.py:168-204`) reaches ±1–2 tokens. The pack builder pads inside the body to hit the length exactly.
- **Generation:** 256 tokens. Also report **tokens per 1,000 characters** of the corpus, to capture tokenizer efficiency.

### 6.2 Definitions

| Metric | Definition |
|---|---|
| TTFT | Submit to first token of any kind. Matches [Artificial Analysis](https://artificialanalysis.ai/methodology) and [MLPerf Client](https://mlcommons.org/benchmarks/client/). |
| TTFA | Submit to first answer (non-reasoning) token |
| Prefill tok/s | `prompt_tokens / (t_prefill_done − t_submit)`. On the Mac, use server timings where available. Otherwise `prompt_tokens / (TTFT − one median decode interval)`. |
| Decode tok/s | `(n − 1) / (t_last − t_first)`. Excludes the first token, as MLPerf Client and mlx-lm `generation_tps` do ([generate.py](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/generate.py)). |
| E2E | Submit to end |

Harbor's script computes decode as `completion_tokens / (e2e − ttft)` (`:260-268`). That is off by one token and includes the stream close. It also counts tokens by re-tokenizing the text (`:251-254`). The fix is server usage via `stream_options.include_usage`, as `scripts/bench_latency_changes.py:214-229` already does, reading `generation_duration` at `:290`.

### 6.3 Cold, warm and prompt cache

- **Cold request:** the first request after a load is recorded alone, as `test: first_token`.
- **Mac warm reps:** use nonce variants, so oMLX's prefix cache cannot hit. Record `cached_tokens`; any value above 0 voids the rep.
- **Agent replay:**
  - The 6-step trajectory runs on the Mac twice: with the cache on (oMLX default) and with it defeated.
  - Report per-step TTFT, `cached_tokens` and newly prefilled tokens.
  - Headline: median TTFT cached against uncached.
  - Harbor's loop logs per-step `model_ms`, `ttft_ms` and `prefix_reset` (`service/agent/loop.py:951-978`), summarised by `scripts/harness_report.py:41-62,96-98`. Use that to check that the replay matches live Harbor behaviour.
  - The iPhone runs without a prefix cache (§2.3).

### 6.4 Statistics and order

- **Repetitions:** 10 measured per level up to 10,240, and 5 at 32,768 and 65,536, each after 1 warm-up.
- **Reported values:** median, IQR, min, max and a bootstrap 95 % CI. Mean ± stdev is stored for comparison with llama-bench and MELT.
- **p95:** only when n ≥ 20, as in agent replay and the sustained runs. At n = 10, nearest-rank p95 is just the maximum.
- **Order:** round-robin across levels, reversing every other round, after `scripts/release_performance.py:668-708`. A cooldown gate and 5 s gap come before each sample, as in §2.6.

### 6.5 Thinking (D5)

- **Separate entries.** Thinking-on and thinking-off are separate entries (`entry_id`), each with its own prompt pack.
  - A model's **headline entry** is the mode it would ship in for agent turns. Gemma-Wisp's headline is thinking on (`service/setup/catalog.py:55`). Its comment says thinking off returns empty answers after tool results (`:43-45`).
- **What each entry runs.**
  - The full perf matrix runs on the **headline entry**.
  - The other entry runs the 2,048 and 10,240 levels and the agent replay.
  - With greedy decoding at a forced length, the thinking flag changes only the template tokens, which are inside the exact level count. So plain-mode speed and memory at other levels are expected to match. If the two entries differ by more than the §1.5 limits at 2,048 or 10,240, the full matrix runs on both.
- **Counting.** Reasoning tokens count as decode tokens and are also reported separately, along with TTFA.
- **Repetitions.** Perf cells keep greedy decoding, the fixed 256-token length and this spec's timing repetitions (§2.6, §6.4). The other specs' routine 3-trial rule applies to task runs. So J per completed task is measured over the agentic suite's 3 routine trials, with one energy window around each task.

---

## 7. Load time from SSD

| Metric | Definition |
|---|---|
| `t_load_cold` | From the load call (oMLX `POST /v1/models/<id>/load`, `service/inference/omlx_client.py:1008-1012`; iPhone: start of the load call) to ready, with the weight pages not in the file cache |
| `t_load_warm` | Same, right after an unload |
| `t_first_token_possible` | From load start to the first token of the 8-token prompt. It includes lazy page-in and shader compilation, so it is the number users feel. |
| `load_read_GBps` | Disk bytes read during a cold load ÷ time. Also the **cold check** on the Mac: bytes read must be ≥ 90 % of weight bytes. Read the counters with `iostat -d`, which needs no sudo. |

- **Mac purge:** the admin helper's `purge` verb, which runs `sync` then `purge` (§11.1). 5 cold and 5 warm reps, alternating. The `iostat` cold check still applies: if `purge` does not empty the cache for the weight files, the rep is marked `cold_verified: false`. Fallback if that happens: reboot, then wait 5 minutes.
- **iPhone:** reboot only. 3 cold samples, one per reboot. [Apple forum 700606](https://developer.apple.com/forums/thread/700606) asks how to purge and has no answer.
- **Why it matters:** [Xiao et al.](https://arxiv.org/html/2410.03613) found cold preparation about 10× slower than warm on one phone.
- **Harbor today:** the bench script records load failures but not durations (`scripts/bench_ondevice_perf.py:494-505`). Engine startup is timed in `scripts/engine_smoke.py:168-172`.

---

## 8. Mixture-of-experts against dense

- **Record parameters from the weight file's tensor index**, not from the model card:
  - `architecture`, `params_total`, `params_active`, `params_embedding`, `n_experts`, `n_experts_active`;
  - for MoE, active = shared + (`n_experts_active`/`n_experts`) × expert parameters.
  - Ids like `12B-A2.5B` (`service/setup/catalog.py:32-35`) or `8B-A1B` (`scripts/bench_ondevice_perf.py:49`) are only a cross-check.
- **Gemma-Wisp is `dense_ple`:** a dense core plus large per-layer embedding tables.
  - Its "E4B" name means about 4B effective parameters.
  - Embeddings are listed apart, because a runtime can keep them memory-mapped and outside the footprint.
- **Show, don't rank.** Two derived columns:
  - **achieved bandwidth** = active weight bytes per token × decode tok/s, in GB/s and as a % of the chip's peak;
  - **tok/s per resident GB**.

  Decode is memory-bound ([MELT](https://arxiv.org/html/2403.12844v4)). MoE decode cost follows active bytes, and footprint follows total bytes. Both are already visible as speed and memory, so per-active-parameter efficiency is not a ranking column.

---

## 9. Sustained use

| Test | Mac (`mac-ref`) | iPhone (`iphone-ref`) |
|---|---|---|
| **S1 sustained** | 15 min back-to-back 2,048-level requests, on AC and on battery | 20 min (§2.6) |
| Report | initial decode tok/s (first 2 min median), sustained (last 5 min median), **stability = sustained/initial**, time to first throttle (first minute < 90 % of initial), thermal-state trace, per-minute PSTR power (Mac) | same, battery-drain `e_dec` (§2.7) |
| **S2 battery** | 60 min on battery, one agent-replay burst every 3 min (duty cycle to be agreed with the agentic spec), plus a matched control hour | same (§2.6) |
| Report | %/h and Wh/h (battery accumulator), % per 100 tasks | %/h, % per 100 tasks |
| **S3 idle residency** | 30 min loaded and idle vs unloaded (ABBA): Δ power from PSTR, Δ footprint, wake TTFT | same; Δ drain only |

- **Prior evidence on throttling.**
  - [LLM Inference at the Edge](https://arxiv.org/abs/2603.23640) reports an iPhone 16 Pro losing nearly half its throughput within two iterations. That comes from the abstract only.
  - MELT measured 47.9 °C skin temperature on an iPhone 14 Pro.
  - Xiao et al. rest phones 10 minutes and reboot between rounds.
- **Board rule:** the board shows peak and sustained figures for both devices, and ranks the iPhone by sustained.
- The MacBook Pro has fans. A fanless MacBook Air will not behave the same way (§10).

---

## 10. What the 24 GB Mac's numbers can and cannot tell you about 16 GB and 8 GB Macs

| Question | Transfers to a 16 GB Mac? | 8 GB Mac? | Why |
|---|---|---|---|
| Footprint at each level (weights, KV slope, overhead) | **Yes**, closely, with the same oMLX and macOS | **Yes** | Footprint is set by the model, runtime and context, not by installed RAM. The exception is allocator cache behaviour under pressure, which can make a smaller machine's footprint lower. |
| Will the engine admit the model? | **Yes, under emulation** (§5.5) | **Estimated** | Rerun on the Mac with oMLX's memory-guard ceiling set to the class value (`memory_guard_custom_ceiling_gb`, read at `scripts/bench_ondevice_perf.py:91-98`). That reproduces the engine's admission decision, not the OS. Gemma-Wisp: about 5.98 GiB needed. It fits under a ~13 GiB ceiling at 16 GB and is refused under ~5 GiB at 8 GB (§5.4). |
| Which context levels fit? | **Yes**: `fits_16gb_mac` and `max_level_fits_16gb`, from footprint against `B16` plus the emulation run (§5.5) | **Estimated** | The KV slope transfers. The budget is derived (macOS, plus a normal app load, plus the 16 GB GPU wired limit) and enforced by the balloon and the wired limit through the admin helper. |
| Will it swap or slow down because of other apps? | **Partly**: the emulation shows memory pressure against a fixed, wired app load | **No** | Real apps grow, shrink and get compressed. A wired balloon does not. Only a real 16 GB device shows that behaviour. |
| Prefill and decode tok/s, TTFT | **No** (absolute); **likely** for the ranking of models | **No** | Decode is bandwidth-bound and prefill compute-bound. Pro chips have roughly 2–2.5× the bandwidth of the base chip of the same generation (from recall, **unverified**) and more GPU cores. A rough scaling by bandwidth ratio may be shown, labelled "projected", never as a measurement. Rankings of dense models of similar shape probably hold. MoE, MTP and large-KV models may reorder. |
| Ratio to Gemma-Wisp | **Probably**, for decode ratios between bandwidth-bound models | **Partly**: only where both fit, which at 8 GB may be nowhere for the baseline | Ratios cancel much of the chip difference. That is the main reason the board leads with them. |
| Energy per token | **No** | **No** | Different chip, different binning, display and power stage. The share for memory traffic and power conversion differs too. |
| Sustained and thermal | **No** | **No** | MacBook Pro fans against a fanless Air. |
| Load time | **Roughly**, same SSD class | **Roughly** | Smaller Macs have smaller and sometimes slower SSDs (**unverified**). |

On the board:
- Mac speed and energy numbers carry a `measured on mac-ref (24 GB)` badge.
- The 16 GB column shows the **fit verdict**, measured under emulation, and is the ranking gate (D1).
- Any 16 GB speed shown is **projected**, never measured.
- The 8 GB column is informational and estimated only.

---

## 11. The Mac runner: changes to `scripts/bench_ondevice_perf.py`

The script's foundations stay:
- exact templated lengths (`:168-204`);
- warm-up (`:283-289`);
- failures as data (`:239-244`, `:496-505`);
- exclusive single-model load (`:494-495`);
- restoring the production model on exit (`:511-520`).

| # | Change | Where now | Size |
|---|---|---|---|
| 1 | Model manifest instead of hard-coded maps and paths. Gemma-Wisp hashes come from `service/setup/catalog.py:75-86`. | `:42-61` | S |
| 2 | Prompt pack builder (shared with the iPhone, §2.3); 7 levels; nonce variants; 256 forced tokens | `:63-78`, `:168-204` | M |
| 3 | Server usage (`include_usage`), `cached_tokens`, server timings; decode from first to last token timestamps; no re-tokenizing | `:211-268` | S |
| 4 | `plain` vs `as_shipped` modes (MTP off/on) on a benchmark-only oMLX instance | new | S–M |
| 5 | Memory: `proc_pid_rusage` footprint and lifetime max, `footprint` neural ledger, swap and compressor deltas, the status taxonomy | `:131-161` | M |
| 6 | Energy helper (PSTR and IOReport GPU at 20 Hz), A/B pair schedule, idle bracketing, error bound, foreign-request voiding | new | L |
| 7 | Load timing: cold (purge or reboot, `iostat` cold check), warm, first-token-possible | `:494-505` | S |
| 8 | Schedule: round-robin with reversal, cooldown gate (`pmset -g therm` / `ProcessInfo.thermalState`), baseline bracket B … B | `:291-302` | S |
| 9 | Write the §3 record. A separate shared aggregator builds `cells[]`, ratios and the report. | `:304-409` | M |
| 10 | Agent replay with cache on and off; S1, S2 and S3 drivers | new | M |
| 11 | Environment: extend `collect_environment` (`:90-124`) with power source, battery, power mode, display, thermal, uptime, GPU cap and engine profiles | `:90-124` | S |
| 12 | Client for the admin helper (§11.1); the 16 GB emulation run and the `fits_16gb_mac` verdict (§5.5) | new | S |

### 11.1 The EDAT admin helper (D3)

The only process in EDAT that runs with administrator rights. The user starts it before a run:

```bash
sudo edat-admin-helper --run-dir <absolute path of this run's output folder>
```

**Behaviour.**
- It runs as root.
- It accepts one client, the EDAT Mac runner, over a Unix socket at `<run-dir>/admin.sock`. The socket has mode 0600 and is owned by the invoking user (`SUDO_UID`).
- It refuses to start if the run folder isn't owned by that user.
- It exits on `exit`, when the runner disconnects, on SIGINT or SIGTERM, or after 6 hours, whichever comes first.

**The complete list of what it does.** Requests are one JSON object per line. There is no shell, and every command is run with fixed arguments. Only the values listed here are accepted; anything else is refused and logged.

| Verb | Allowed arguments | Exactly what runs |
|---|---|---|
| `purge` | none | `/bin/sync`, then `/usr/sbin/purge` |
| `powermetrics_start` | `interval_ms` ∈ {100, 250, 1000} | `/usr/bin/powermetrics --samplers cpu_power,gpu_power,ane_power,thermal -i <interval_ms> -f plist -o <run-dir>/powermetrics-<n>.plist`. Sampler names and flags must be checked against `powermetrics -h` on the reference Mac (**unverified**). At most one instance at a time. |
| `powermetrics_stop` | none | SIGINT to the instance it started, then wait for it to exit |
| `wired_limit_set` | `mb` ∈ {`EDAT_16GB_WIRED_MB`} (one constant, the 16 GB default; §5.5) | `/usr/sbin/sysctl iogpu.wired_limit_mb=<mb>` |
| `wired_limit_reset` | none | `/usr/sbin/sysctl iogpu.wired_limit_mb=<value read at helper start>` |
| `balloon_wire` | `gib` ∈ {10.5} | In-process: allocates that much anonymous memory, writes every page and `mlock`s it. No external command. |
| `balloon_release` | none | `munlock` and free |
| `status` | none | Returns the helper's state: wired-limit original and current, balloon size, whether powermetrics is running, uptime |
| `exit` | none | Restore (below), then exit |

**Restore.** On `exit`, on disconnect, on SIGINT or SIGTERM, and on timeout, the helper:
1. stops powermetrics;
2. releases the balloon;
3. resets `iogpu.wired_limit_mb` to the value it read at start.

A SIGKILL cannot be caught. In that case the wired limit stays changed until the next reboot (the sysctl is not persistent), and the balloon memory is freed with the process. The runner checks `sysctl -n iogpu.wired_limit_mb` at the start of every run and warns if it differs from the recorded original.

**It never:**
- runs any other command, sysctl or sampler;
- writes outside `<run-dir>`;
- touches the user's oMLX or Harbor settings;
- opens a network connection;
- signals any process it did not start;
- installs anything.

**Audit.** Every request, with its arguments, result, exit code and timestamps, goes to `<run-dir>/admin-audit.jsonl`. The run record lists it in `attachments` (kind `admin_audit`).

**Size:** about 250 lines of Swift, one file, reviewable in one sitting.

---

## 12. Later: other platforms and runtimes (not in this phase)

Kept from revision 1 for when scope widens. None of it is designed in detail.

- **Android.**
  - Energy from `BatteryManager` current and charge counters ([doc](https://developer.android.com/reference/android/os/BatteryManager)) and Perfetto ODPM power rails on Pixel 6 and later ([Perfetto](https://perfetto.dev/docs/data-sources/battery-counters)).
  - Memory from PSS plus graphics rows ([Debug.MemoryInfo](https://developer.android.com/reference/android/os/Debug.MemoryInfo)). lmkd kills by PSI ([AOSP](https://source.android.com/docs/core/perf/lmkd)), so app budgets need a balloon test inside an app.
  - Runtimes: llama.cpp and LiteRT-LM.
- **Linux and Windows laptops.**
  - Linux: RAPL `energy_uj` counters, root-only ([powercap](https://docs.kernel.org/power/powercap/powercap.html)); memory from USS/PSS and cgroup `memory.peak`; real memory limits through `memory.max` ([cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)).
  - Windows: commit and peak working set ([psapi](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex)); energy tools not researched.
- **Other Apple runtimes:** llama.cpp Metal, Core ML / Neural Engine, MLC.
- **Other device classes:** 8 GB and 16 GB Macs, an 8 GB iPhone, Android 8, 12 and 16 GB phones.
- **External meters:** not planned (D4). For reference, MELT used a Monsoon with the battery removed for phone joules.

---

## 13. Metric table

| Name | Definition | Unit | Better | Mac | iPhone | Tool |
|---|---|---|---|---|---|---|
| `ttft_s` | Submit → first token | s | lower | ✔ | ✔ | monotonic clock |
| `ttfa_s` | Submit → first answer token | s | lower | ✔ | ✔ | clock |
| `prefill_tps` | prompt tokens ÷ prefill time | tok/s | higher | ✔ | ✔ | server timings / eval stamp |
| `decode_tps` | (n−1) ÷ (t_last − t_first) | tok/s | higher | ✔ | ✔ | clock |
| `decode_tps` plain vs as_shipped | the same with speculation off and on | tok/s | higher | ✔ | plain only | engine profile |
| `spec_acceptance` | accepted ÷ drafted | ratio | higher | ✔ | if supported | engine stats |
| `e2e_s` | submit → end | s | lower | ✔ | ✔ | clock |
| `e_pre_mJ_per_tok` | active prefill energy ÷ L | mJ/token | lower | ✔ | ✘ | PSTR + A/B pairs |
| `e_dec_mJ_per_tok` | active energy per output token | mJ/token | lower | ✔ | ◐ drain | PSTR / battery level |
| `j_per_task` | active energy per completed agent task | J | lower | ✔ | ✘ (v1) | PSTR |
| `p_idle_W` | bracketed idle power | W | lower | ✔ | ✘ | PSTR |
| `battery_pct_per_h` | drain at the S2 duty cycle, minus control | %/h | lower | ✔ | ✔ | battery APIs |
| `idle_resident_mW` / `_pct_per_h` | resident vs unloaded idle cost | mW, %/h | lower | ✔ | ◐ | PSTR / level |
| `mem_loaded_MiB` | footprint after load | MiB | lower | ✔ | ✔ | phys_footprint |
| `mem_peak_MiB@L` | peak footprint during the request | MiB | lower | ✔ | ✔ | kernel max / 20 Hz |
| `mem_steady_MiB@L` | median footprint during decode | MiB | lower | ✔ | ✔ | 20 Hz |
| `kv_MiB_per_1k` | fitted KV slope (and the formula value) | MiB/1k | lower | ✔ | ✔ | regression |
| `overhead_MiB` | loaded − resident weights | MiB | lower | ✔ | ✔ | derived |
| `neural_MiB` | Neural Engine ledger | MiB | lower | ✔ | n/a (MLX) | `footprint` |
| `swap_delta_MiB` | swap and compressor growth | MiB | 0 | ✔ | n/a | sysctl / vm_stat |
| `available_min_MiB` | minimum app headroom | MiB | higher | n/a | ✔ | os_proc_available_memory |
| `app_budget_MiB` | balloon-measured app limit | MiB | — | n/a | ✔ | balloon |
| `status@L` | §5.3 / §2.8 taxonomy | enum | ok | ✔ | ✔ | runner |
| `max_level_ok` | largest level that ran `ok` | tokens | higher | ✔ | ✔ | derived |
| `fits_<class>@L` | peak ≤ class budget | yes/no/estimated | yes | ✔ | ✔ | derived |
| `t_load_cold_s` / `t_load_warm_s` | load time, cache purged / cached | s | lower | ✔ | ✔ (reboot) | runner |
| `t_first_token_possible_s` | load start → first token | s | lower | ✔ | ✔ | runner |
| `load_read_GBps` | disk bytes during cold load ÷ time | GB/s | higher | ✔ | ✘ | iostat |
| `ttft_cached_s` / `ttft_uncached_s` | agent-replay step TTFT | s | lower | ✔ | uncached only | clock |
| `stability_ratio` | sustained ÷ initial decode | ratio | higher | ✔ | ✔ | S1 |
| `time_to_throttle_s` | first minute < 90 % of initial | s | higher | ✔ | ✔ | S1 |
| `tokens_per_1k_chars` | tokenizer efficiency on the corpus | tok | lower | ✔ | ✔ | pack builder |
| `params_total` / `params_active` / `params_embedding` | from the tensor index | count | — | ✔ | ✔ | index |
| `achieved_bw_GBps` | active bytes per token × decode_tps | GB/s | higher | ✔ | ✔ | derived |
| `ratio_to_baseline.*` | candidate ÷ Gemma-Wisp, same device and session | ratio | per metric | ✔ | ✔ | aggregator |
| **`fits_16gb_mac`** | §5.5 verdict: footprint ≤ 0.9 × 10.0 GiB at 10,240 (or only at 2,048), confirmed under emulation; the ranking gate | enum | `fits` | ✔ (emulated) | n/a | footprint + admin helper |
| `max_level_fits_16gb` | largest level that passes §5.5 | tokens | higher | ✔ | n/a | aggregator |
| `fits_iphone_air` | the same scale against the balloon budget | enum | `fits` | n/a | ✔ | balloon + footprint |
| `reference_pair_check` | base ÷ Gemma-Wisp within §1.5 limits | pass/flag | pass | ✔ | ✔ | aggregator |

---

## 14. Example record (shape only; all values invented)

```json
{
  "schema": "edat.perf/1.0",
  "run_id": "2026-12-01T10-15-00Z-7f3c",
  "runner": {"name": "iphone-mlx-swift", "version": "0.1.0", "commit": "abc1234"},
  "clock": "mach_continuous_time",
  "started_utc": "2026-12-01T10:15:00Z", "ended_utc": "2026-12-01T11:02:41Z",
  "device": {"class": "iphone-ref", "model_id": "iPhoneXX,Y", "soc": "A19 Pro",
             "ram_bytes": 12884901888, "os": "iOS 27.1", "os_build": "27B45",
             "entitlements": ["com.apple.developer.kernel.increased-memory-limit"],
             "battery_nominal_Wh": 12.0, "battery_nominal_source": "example",
             "battery_health_pct": null, "gpu_wired_limit_mb": null},
  "runtime": {"name": "mlx-swift", "version": "0.32.3",
              "packages": {"mlx-swift": "0.32.3", "mlx-swift-lm": "3.32.3"},
              "backend": "metal", "mlx_cache_limit_bytes": 268435456,
              "prefill_step_tokens": 512, "kv_dtype": "bf16", "prefix_cache": "none",
              "ssd_kv_cache": false, "expert_offload": false, "gpu_power_cap_W": null,
              "memory_guard_ceiling_gb": null, "engine_profile": {}},
  "models": [{"id": "Gemma-Wisp", "role": "baseline", "mode": "plain",
              "files": [{"name": "model-00001-of-00002.safetensors", "sha256": "b22e…", "bytes": 5001242218}],
              "quant": {"scheme": "mlx-affine-oQ4e", "bits": 4, "group_size": 64, "mixed": {"5": 84, "6": 18}},
              "architecture": "dense_ple", "params_total": null, "params_active": null,
              "params_embedding": null, "native_window_tokens": null, "profile_window_tokens": 65792,
              "thinking": "on", "speculation": "none", "drafter": null,
              "template_sha256": "0a2c…", "tokenizer_sha256": "cc8d…"}],
  "prompt_pack": {"Gemma-Wisp": {"pack_sha256": "…", "corpus": "provisional-prose-v0", "version": "0.1"}},
  "conditions_start": {"power_source": "battery", "battery_level": 0.91, "battery_state": "unplugged",
                       "low_power_mode": false, "display": "on", "brightness": 0.0,
                       "thermal_state": "nominal", "uptime_s": 742, "ambient_C": 22.0,
                       "attest": {"airplane_mode": true, "no_case": true, "hard_surface": true,
                                  "auto_brightness_off": true, "other_apps_closed": true}},
  "samples": [
    {"sample_id": "s0042", "model_id": "Gemma-Wisp", "mode": "plain", "test": "level",
     "phase": "measured", "round": 3, "rep": 3, "order_index": 42,
     "level_tokens": 2048, "prompt_tokens": 2048, "cached_tokens": null, "variant": 3,
     "shape": null, "step": null,
     "t_submit_ns": 912000000000, "t_prefill_done_ns": 913900000000,
     "t_first_token_ns": 913950000000, "t_last_token_ns": 932100000000, "t_end_ns": 932150000000,
     "token_times_ns": [71000000, 142500000],
     "output_tokens": 256, "reasoning_tokens": 256, "drafted_tokens": null, "accepted_tokens": null,
     "load": null,
     "footprint_start_bytes": 5100000000, "footprint_peak_bytes": 5650000000,
     "footprint_peak_ledger_bytes": 5660000000, "footprint_steady_bytes": 5480000000,
     "footprint_end_bytes": 5470000000, "neural_bytes": null,
     "available_min_bytes": 420000000, "swap_delta_bytes": null, "compressed_delta_bytes": null,
     "mlx_active_bytes": 5300000000, "mlx_cache_bytes": 190000000, "mlx_peak_bytes": 5400000000,
     "thermal_start": "nominal", "thermal_end": "fair", "cooldown_wait_s": 35.0,
     "thermal_not_nominal": false, "memory_warning": false,
     "battery_level_start": 0.88, "battery_level_end": 0.88,
     "energy_window_id": null, "status": "ok", "error": null, "kill_evidence": null},
    {"sample_id": "s0049", "model_id": "Gemma-Wisp", "mode": "plain", "test": "level",
     "phase": "measured", "round": 3, "rep": 3, "order_index": 49, "level_tokens": 10240,
     "status": "oom_killed", "memory_warning": true,
     "kill_evidence": {"last_footprint_bytes": 6050000000, "last_available_bytes": 90000000,
                       "warning": true, "tail": [[1012000000000, 6050000000, 90000000]]}}
  ],
  "events": [{"t_ns": 925000000000, "kind": "thermal_change", "detail": "nominal->fair"},
             {"t_ns": 1013500000000, "kind": "relaunch_after_kill", "detail": "s0049"}],
  "energy_windows": [],
  "balloon": {"runs": [{"last_survived_bytes": 6290000000, "footprint_plus_available_bytes": 6300000000}],
              "budget_bytes": 6290000000},
  "attachments": [{"name": "mem-2026-12-01T10-15-00Z-7f3c.jsonl", "sha256": "…", "kind": "footprint_log"}]
}
```

The second sample is truncated for brevity: a real record carries every field from §3.6.

---

## 15. Build order and size

| Step | Deliverable | Est. size | Depends on |
|---|---|---|---|
| 1 | §3 schema as a JSON Schema file + validator + shared aggregator (cells, ratios, report) | ~500 lines Python, 1.5 days | — |
| 2 | Prompt pack builder (7 levels, nonce variants, agent replay, first-token prompt) | ~250 lines, 1 day | corpus (stand-in until the agentic spec lands) |
| 3 | iPhone runner app (§2), being built separately | ~1,500 lines Swift, 5–7 days | 1–2; Gemma 4 support check in MLX Swift |
| 4 | Mac runner refactor: manifest, pack, usage, modes, schedule, record output | ~600 lines, 2 days | 1–2 |
| 5 | Mac memory: footprint, ledger, swap, statuses | ~250 lines, 1 day | 4 |
| 6 | Mac energy helper (PSTR + IOReport GPU) + A/B windows + validation against the battery accumulator | ~600 lines Swift/C + Python, 3 days | 4 |
| 7 | Load timing (cold check), agent replay, S1–S3 drivers | ~450 lines, 2 days | 4–6 |
| 7b | EDAT admin helper (§11.1) and the 16 GB emulation run (§5.5) | ~250 lines Swift + ~150 lines runner, 1.5 days | 4–5 |
| 8 | First full session: reference bracket on both devices, the 16 GB verdict for both references, then the review of error bounds | 1–2 days of device time | all |

v1 totals about 4,150 lines and 16–19 working days, estimated, not prototyped. Steps 3 and 4–7 can run in parallel once 1 and 2 exist.

---

## 16. What I could not verify

- **MLX Swift and the baseline.** Whether `mlx-swift-lm` 3.32.3 supports Gemma 4, oQ4e mixed-bit weights or the MTP head, and whether it can keep per-layer embeddings memory-mapped. This decides whether the baseline runs on the iPhone at all.
- **iPhone Air hardware.** Its app memory limit (the 6,144 MB figure is for other 12 GB iPhones, and user-reported) and its battery nominal energy.
- **iOS APIs.**
  - Whether `ledger_phys_footprint_peak` is in `task_vm_info`.
  - The `UIDevice.batteryLevel` step.
  - The absence of any iOS current API.
  - MetricKit exit-reason details.
- **macOS tools.**
  - Whether `purge` still needs sudo.
  - The AppleSmartBattery accumulator update rate.
  - The default `iogpu.wired_limit_mb` fraction.
  - SMC PSTR accuracy and IOReport behaviour come from earlier informal hand measurement on the reference Mac and were not re-checked here.
- **oMLX.** Whether it can serve a symlinked second model id with its own settings, for the plain mode.
- **Chips.** Relative bandwidth of Pro and base chips (recalled).
- **Measurements.** Context-scaling measurements at 32k and 64k on any Apple device. None were found.
- **The 16 GB budget.** `B16` = 10.0 GiB is derived: a 3.5 GiB macOS reserve plus a 2.5 GiB app load are estimates. The 16 GB default `iogpu.wired_limit_mb` value is also unverified. Neither has been calibrated on a real 16 GB Mac.
- **Whether `purge` empties the file cache for large mmap'd weight files** on macOS 27. The `iostat` cold check will show it.
- **Exact `powermetrics` sampler names and the `-f plist` flag** on the reference Mac.

---

## 17. Decisions (2026-11-18)

The recommendations stand, with one change: the model should fit on a 16 GB Mac. All earlier open questions are **decided**:

| # | Question | Decision |
|---|---|---|
| D1 | Target device class and fit | **The 16 GB Mac is the target class.** Variants that do not fit a 16 GB Mac are listed as "does not fit" and not ranked. The test is defined in §5.5. The iPhone Air keeps its own measured budget (§2.9). |
| D2 | Name | The benchmark is **EDAT** (Edge Device Agentic Testing). Schema ids are `edat.*`; this spec's record is `edat.perf/1.0`. |
| D3 | sudo on the Mac | Allowed only through the **EDAT admin helper** the user starts before a run (§11.1). It covers `purge`, `powermetrics` and the GPU wired limit, plus the wired balloon used for the 16 GB check, and nothing else. |
| D4 | External meter | None. iPhone energy is an estimate from the system's own tools, labelled `is_estimate: true` (§2.7). Mac energy uses internal sensors only. |
| D5 | Thinking | Thinking-on and thinking-off are **separate entries**. A model's headline entry is the mode it would ship in for agent turns (§6.5). Perf cells keep greedy decoding at a fixed length. Routine runs in the other specs use 3 trials; this spec's timing repetitions are unchanged. |
| D6 | Energy headline | J per completed task on the overall board; the per-token pair on the performance board. |
| D6b | Headline decode ratio | Plain ÷ plain leads; as-shipped ÷ as-shipped sits beside it. |
| D7 | iPhone fallback | If `Gemma-Wisp` at its shipped quantisation is killed on the iPhone Air, that kill is the recorded result. A smaller quantisation of the same weights then runs as a separate, clearly named variant (§1.6). |
| D8 | Language | English only. Prompt packs are English (`language: "en"`). |
| D9 | Context levels | 5,120 and 10,240 accepted. Levels past a model's native window are skipped and recorded as `exceeds_native_window`. |
| D10 | Phone brightness | **Minimum brightness** (the app sets `UIScreen.brightness = 0.0`). The screen stays on. |
| D11 | Community submissions | Out for now. |
| D12 | Benchmark oMLX instance | Accepted: a benchmark-only oMLX instance with its own settings and model ids. The user's everyday engine is never changed. |
| D13 | Duty cycle, corpus, 6-step trajectory | Supplied by the agentic spec (`02-agentic-spec.md`). Token counting follows the quality spec's shared definitions (`03-output-quality-spec.md`, "Tokens per answer"). |
| D14 | Reference-pair cost | Run in every session, as proposed. If base is also killed on the iPhone, the pair check there compares statuses only. |

No decision is left open in this spec. One item is not a decision but limits accuracy. The 16 GB budget in §5.5 is derived, not measured on a real 16 GB Mac. Calibrating it once on a real device would replace the estimate.
