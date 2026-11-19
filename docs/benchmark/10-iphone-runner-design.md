> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 10 · iPhone runner: design, build and device-run plan

Status: 2026-11-18. Design written first, then built. The app and its measurement package are on branch `claude/bench-iphone-runner` of the Harbor repo (base `stage-0.1.7` at `01f8651`), in the new folder `ios-bench/`. **Nothing has run on the iPhone.** Every number about the phone below is either a published report (linked) or a prediction, and is labelled as such. Paths are relative to the repository root unless they start with `/`.

The research behind this file (MLX Swift runtime; iOS memory, energy, install) is cited inline with its sources.

---

## 0. The answer in brief

- **Runtime.** MLX Swift directly: `mlx-swift-lm` **3.32.3** + `mlx-swift` **0.32.3** + `swift-transformers` **1.3.5**, the exact pins Harbor's iPhone app already builds with (`ios/Packages/HarborLing/Package.swift`). It loads the same oMLX folder text-only (`LLMModelFactory`, model types `gemma4` / `gemma4_text`), with the per-layer mixed 4/5/6-bit quantisation, Gemma 4's Jinja chat template and its `<|tool_call>call:name{…}<tool_call|>` format. Apple's FoundationModels adapter (`MLXFoundationModels`, iOS 27 only) hides TTFT, per-token times and token counts, so it is not a base for a benchmark.
- **MTP head: not used on the phone in v1.** In 3.32.3 it needs the VLM model class (vision tower resident, +~320 MiB) and a separate drafter folder with renamed tensors; acceptance against oMLX is untested upstream. Phone numbers are therefore **speculation-off** and compare with the Mac's `decode_tps_spec_off` column, not its as-shipped MTP number (§1.3).
- **Will the 5.6 GB baseline load? Probably yes, with little room.** The folder is 5.4 GB on disk, but a text-only load materialises **4.27 GiB** (4,373 MiB; audio 0.58 GiB, vision 0.31 GiB and the 0.15 GiB MTP head stay on flash). The only per-app limit reported for a 12 GB iPhone is **~6,144 MB with the Increased Memory Limit entitlement** and ~3.3 GB without it ([forum 834805](https://developer.apple.com/forums/thread/834805), [SideStore #1616](https://github.com/SideStore/SideStore/issues/1616)). Predicted footprint ≈ 4.6–4.8 GB after load, so 512–10k tokens should fit, 32k is likely, and **64k is likely to be killed** (§3.4). Without the entitlement it will not load.
- **Free team.** Community reports say Xcode with a free Personal Team keeps the increased-memory-limit entitlement (it is the sideloading tools that drop it); not confirmed by Apple. If Xcode refuses it, one line in `Local.xcconfig` switches to a build without it, and the fallback is a smaller quantisation of the same model recorded as a separate variant (§3.5).
- **Getting the model on.** USB cable: Finder → iPhone → Files → drag the model folder onto *EdgeBench* (the app enables file sharing), or `xcrun devicectl device copy to` per file. No hub download in v1: the baseline weights are a local folder, and a cable is faster and needs no network (§2).
- **Energy.** An iPhone app can read battery level (5 % steps since iOS 17, per Apple), charge state, thermal state and Low Power Mode, nothing in watts. Per-token joules need a battery-bypass rig and an external meter, so the phone reports **battery % per hour** over 60-minute runs with a same-day no-model control, and records thermal state with every sample (§4).
- **Kills are results.** Before every step the app writes a sentinel and, once a second, a heartbeat with footprint and available memory. A sentinel found at the next launch becomes that step's result (`oom_killed` with memory evidence, `killed` without), and the run resumes from Results (§3.6).
- **Built and verified within the rules:** core package with 30 unit tests against a stub generator, the SwiftUI app, simulator and generic-device builds, and a Simulator run to an exported file (§9 has the commands and results). **Not verified:** anything that needs the phone, including whether MLX loads this folder at all (§10).

---

## 1. Runtime

### 1.1 Packages and versions

| Package | Version | Why |
|---|---|---|
| [mlx-swift-lm](https://github.com/ml-explore/mlx-swift-lm) | exact 3.32.3, `traits: []` | Has Gemma 4 (`Libraries/MLXLLM/Models/Gemma4.swift`, `Gemma4Text.swift`), registered as `gemma4` and `gemma4_text` in `LLMModelFactory.swift:63-65`; the Gemma 4 tool parser (`Tool/Parsers/GemmaFunctionParser.swift`); the MTP drafter code. Traits off drops the FoundationModels adapter and vendored xgrammar, as Harbor's app does. |
| [mlx-swift](https://github.com/ml-explore/mlx-swift) | exact 0.32.3 | What 3.32.3 resolves; MLX core 0.32.2. |
| [swift-transformers](https://github.com/huggingface/swift-transformers) | from 1.3.5 (resolved 1.3.5) | Tokenizer and Jinja chat template; reads `chat_template.jinja` from the folder (Hub.swift:224, 277). |

`ios-bench/Packages/EdgeBenchMLX/Package.resolved` is a copy of HarborLing's, so both apps build the same dependency set. **3.33.0** (released 2026-11-17, needs mlx-swift 0.33.0) is source-breaking and adds nothing iOS-specific; revisit after the first phone numbers.

### 1.2 Loading the oMLX folder

- **Factory.** `LLMModelFactory.shared.load(from: dir, using: tokenizerLoader)` (`ModelFactory.swift:197-214`) loads from a local directory with no network. `Gemma4Configuration` reads the nested `text_config`; `Gemma4Model.sanitize` drops `vision_tower`, `audio_tower`, `embed_audio`, `embed_vision`; `Gemma4TextModel.sanitize` drops the k/v projections of the 18 KV-shared layers (`Gemma4Text.swift:875-941`).
- **Mixed quantisation.** `loadWeights` quantises a module when `<path>.scales` exists, using `perLayerQuantization` from `config.json` (`Load.swift:368-405`). The folder has default 4-bit/group 64 with 93 layers at 5 bits and 14 at 6 bits, keyed by the same module paths.
- **Lazy reads.** The safetensors loader reads headers only; bytes are read when an array is evaluated (`Load.swift:9-18`), and the model is realised before `load` returns (`materializeModelForInference`, `Load.swift:404-407`). So unused vision/audio tensors are never read, and the measured load time includes reading the weights.
- **The one blocker, fixed in the app.** The folder's `model.safetensors.index.json` lists the 50 `language_model.mtp.*` tensors, and `model.update(parameters:, verify: [.all])` should refuse keys the text model has no module for (inferred from the code, not run). `EdgeBenchMLX.TextOnlyFolder` builds `<folder>/.edgebench-textonly/` at load time: symlinks to every file except `model-mtp.safetensors` and the index, plus an index without the MTP entries (`ios-bench/Packages/EdgeBenchMLX/Sources/EdgeBenchMLX/MLXBenchEngine.swift`). No weights are copied.
- **Chat template and thinking.** The template is rendered by swift-jinja 2.5.2 with `enable_thinking` passed through `additionalContext`. Every construct it uses exists in swift-jinja; **runtime parity with Python's `apply_chat_template` is unverified**, so the first device run must compare prompt token counts with the Mac (§12, step 7). Reasoning arrives as `<|channel>thought…<channel|>`; the core classifies tokens as reasoning, tool call or answer from the raw token strings (`GemmaTokenClassifier`) and splits the final text with `GemmaOutput.parse` (unit-tested).
- **Tool calls.** The template declares tools as `<|tool>declaration:name{…}<tool|>` and the model answers `<|tool_call>call:name{key:<|"|>text<|"|>,n:3}<tool_call|>`. The app parses this in the core (`Quality.swift`), converting Gemma's argument syntax to JSON and keeping the raw string when it does not parse, so malformed arguments count against the model, as spec 02 §4.1 asks.
- **Generation loop.** The engine drives mlx-swift-lm's `TokenIterator` itself (`Evaluate.swift:712-1000`) instead of the stream API: prefill happens in the iterator's init, the first `next()` returns the first token, and the loop can ignore end-of-sequence for the fixed 256-token perf cells. Prefill runs in 512-token steps (`GenerateParameters.prefill.stepSize`); the MLX buffer cache is capped at 64 MB (the value that avoided jetsam for Gemma 4 E2B in [mlx-swift-lm #343](https://github.com/ml-explore/mlx-swift-lm/pull/343)) and cleared after each generation.

### 1.3 The multi-token-prediction head, and comparability with the Mac

- **What 3.32.3 needs for MTP** (`Gemma4Assistant.swift`, `MTPSpeculativeTokenIterator.swift`): the target loaded with **VLMModelFactory**, because only the VLM `Gemma4` class emits the drafter's hidden state and shared KV (`MLXVLM/Models/Gemma4.swift:2150`); a drafter folder of type `gemma4_assistant` with `text_config.mtp_assistant_config` from the model's config.json as its config, and `model-mtp.safetensors` rewritten without the `language_model.mtp.` prefix (152 MB, data unchanged). The ordered-embedding head this model uses has no upstream test beyond an env-gated E4B pair.
- **Costs on a phone:** the vision tower stays resident (+~320 MiB) and the drafter adds ~152 MiB, about 0.47 GiB more against a ~6 GB limit that is already tight. Only aggregate acceptance statistics exist (`acceptedDraftTokens`, `proposedDraftTokens`).
- **Decision for v1: speculation off.** The engine reports `speculation: "none"` and the record says why. **Comparability:** Harbor ships Gemma with MTP on (`service/setup/catalog.py:56`, adaptive depth 4), so the Mac's as-shipped decode number is not comparable with the phone's. The Mac harness must run its spec-off column (spec 04 §3.5 already asks for `decode_tps_spec_off`); compare the phone against that. TTFT and prefill are unaffected by MTP and compare directly. Greedy outputs differ across Metal GPUs anyway (spec 02 §4.2), so quality parity is judged on aggregate pass rates, not identical text.
- **v2 path (not built):** a "Make MTP drafter" step in the app that writes the drafter folder with `MLX.loadArrays` / `save(arrays:)`, a VLM-path engine, and a spec-on column. Worth doing only if the spec-off run shows enough memory headroom.

### 1.4 FoundationModels with an MLX backend vs MLX Swift directly

`MLXFoundationModels` adapts Apple's `FoundationModels.LanguageModel` protocol and needs **iOS 27** (`MLXLanguageModel.swift:12-69`). It exposes usage counts only: no time to first token, no per-token timestamps, no prefill/decode split, no MTP. The phone is likely on iOS 26 or 27 (open question 1). For a benchmark the direct API is the only one that shows what is measured; an "API-level" row through FoundationModels could be added later as a separate entry, never on the headline.

---

## 2. Getting models onto the phone

| Route | Practical? | Notes |
|---|---|---|
| **Finder over USB** (chosen) | Yes | The app sets `UIFileSharingEnabled` and `LSSupportsOpeningDocumentsInPlace` (`ios-bench/App/Info.plist`). Finder → the iPhone → **Files** → drag the model folder onto **EdgeBench**. It lands in the app's Documents, where the Models screen finds it. Multi-GB reliability is not documented; if Finder stalls, use devicectl. |
| `xcrun devicectl device copy to` | Yes, scriptable | `--domain-type appDataContainer --domain-identifier <bundle id>`, one file at a time (directory copies reportedly transfer nothing) ([Apple](https://developer.apple.com/documentation/xcode/interacting-with-devices-using-the-command-line.md)). |
| Files app import (in-app "Import a model folder…") | Yes, slow | Copies from iCloud Drive or a USB drive into Documents/Models; needs twice the space while copying. |
| In-app download from a hub | Not in v1 | The baseline folder is a local copy under its pre-release folder name; a hub download adds network to a run that should be in airplane mode. Easy to add for public variants later (URLSession background download). |
| Bundle in the app | No | A 5.4 GB app bundle makes every reinstall (weekly, with a free team) copy 5 GB. |

**Storage:** 5.43 GB for the folder as it is on the Mac (`ls -la` of the `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` folder), about 5.1 GiB. Keep 2 GB free beyond that for the OS and results. The app excludes `Documents/Models` from iCloud backup. To save 0.9 GB on the phone you may leave out nothing: the shards mix text and vision/audio tensors, so a smaller folder would need re-sharding on the Mac (possible later, recorded as a different file set).

**Integrity:** the app hashes every file on request (Models → model → Verify SHA-256) and compares with Harbor's pinned manifest for these weights (`service/setup/catalog.py` `GEMMA_HARBOR_FILES`, copied to `ios-bench/Resources/known-models.json`). The hashes then travel in every record (`model.files[].sha256`).

---

## 3. Memory

### 3.1 The per-app limit on a 12 GB iPhone

- Apple publishes no table. The increased limit is "only available on some device models"; apps should call `os_proc_available_memory()` ([Apple](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.kernel.increased-memory-limit.md)).
- Reported: **~6,144 MB** with `increased-memory-limit` on both an 8 GB iPhone 16 Pro Max and a 12 GB iPhone 17 Pro Max; adding `extended-virtual-addressing` changed nothing; `increased-debugging-memory-limit` gave ~6,656 MB in debug only ([forum 834805](https://developer.apple.com/forums/thread/834805), one developer, no Apple reply). A 0.6B Core AI model was killed at ~6 GB on an iPhone 17 Pro ([forum 855499](https://developer.apple.com/forums/thread/855499)).
- Without the entitlement: **~3.3 GB** (iPhone 15 Pro Max, iOS 27.0) ([SideStore #1616](https://github.com/SideStore/SideStore/issues/1616)).
- Metal on the iPhone Air: `recommendedMaxWorkingSetSize` = 8,192 MiB ([forum 805161](https://origin-devforums.apple.com/forums/thread/805161)); jetsam's ~6 GB limit binds first.
- One third-party write-up measured **Gemma 4 E4B peaking at 4,651 MB** with the entitlement on an iPhone 17 Pro, and a crash with neither entitlement ([zenn.dev](https://zenn.dev/mtfum/articles/ios_memory_entitlements?locale=en); unverified, context length unknown).
- **The app measures the real budget** at the start of every run as `os_proc_available_memory() + phys_footprint` and records it in `device.measured_app_budget_MiB` with its source. That is the number every "fits" verdict should use (spec 04 §2.4).

### 3.2 The entitlement and a free team

- `ios-bench/App/EdgeBench.entitlements` asks for `com.apple.developer.kernel.increased-memory-limit` only. Extended virtual addressing is left out: it made no measurable difference in thread 834805, and whether a free team may use it is unconfirmed.
- Community reports (not Apple) say Xcode with a free Personal Team keeps this entitlement and the app gets ~6 GB, while sideloading tools such as SideStore drop it ([SideStore #1616](https://github.com/SideStore/SideStore/issues/1616), [NX2Apple](https://raw.githubusercontent.com/LNPANDY/NX2Apple/master/ENTITLEMENTS.md)).
- If the team is not allowed it, Xcode fails at signing ("…do not support the … capability" or "Provisioning profile doesn't include …"); it is not silently ignored under Xcode. Then add `EDGEBENCH_ENTITLEMENTS = App/EdgeBench-no-entitlements.entitlements` to `Local.xcconfig`; the app installs with the default limit and records that.
- The app reads what the provisioning profile granted from `embedded.mobileprovision` and records it in `device.entitlements` (`Provisioning.entitlements()` in `ios-bench/App/DeviceProbes.swift`), beside the measured budget.

### 3.3 Reading memory inside the app

| Number | Source | Use |
|---|---|---|
| `phys_footprint` | `task_info(TASK_VM_INFO)` | Headline (jetsam judges by it; spec 04 §2.1). Sampled every 50 ms during each step. |
| `ledger_phys_footprint_peak` | same struct | Kernel high-water mark. If it rises during a step, that value is the step's peak, so short prefill spikes between samples are not missed. Used by WebKit on iOS; Apple does not document it for apps (unverified that it behaves the same on the phone). |
| `os_proc_available_memory()` | `<os/proc.h>`, iOS 13+ | Bytes left before the limit; recorded with every sample and at load. |
| MLX active/cache/peak | `Memory.snapshot()` | Not the headline (spec 04 §2.1: an engine's own peak excludes its pool); logged in runtime notes. |
| Memory warnings | `UIApplication.didReceiveMemoryWarningNotification` | Counted per step; a step with a warning is reported `ok_memory_warning`. |

### 3.4 Will the baseline load, and up to which level?

Prediction, from the folder's own tensor sizes (summed from the safetensors headers) and the KV layout in its config:

| Part | Size | Basis |
|---|---|---|
| Text weights | 4,373 MiB | `language_model.model.*`: layers 2,305 + per-layer embeddings 1,512 + embeddings 440 + projections 15 |
| Runtime, tokenizer (262k vocab), Metal, app | ~250–450 MiB | unmeasured guess |
| KV cache | ~16 MiB per 1k tokens | 4 own-KV full-attention layers (5, 11, 17, 23; the other 3 are KV-shared) × 2 KV heads × 512 dims × K+V × bf16; the 20 own sliding layers hold a fixed 512-token window (~21 MB) |
| Prefill transients | unknown, bounded by the 512-token step | MLX's fused attention may avoid materialising scores; unverified on device |

| Level | Predicted peak | vs ~6,144 MB |
|---|---|---|
| 512–2,048 | ~4.7–4.9 GB | fits |
| 10,240 | ~4.9–5.1 GB | fits |
| 32,768 | ~5.2–5.6 GB | likely fits |
| 65,536 | ~5.7–6.3 GB | **likely killed** |

All of this is a prediction. The runner records whatever happens: a killed level is `oom_killed` with the last heartbeat attached, and with the default `skipLargerLevelsAfterOOM` larger levels are recorded as `skipped_after_oom` instead of costing another kill each.

### 3.5 If it does not load

1. Check the entitlement made it into the profile (Results → run → Device shows the measured budget; ~3.3 GB means it did not).
2. If the budget is ~6 GB and the load is still killed: make a **smaller quantisation of the same weights** on the Mac with oMLX's quantiser (for example 3-bit with the same recipe, `oQ3e`, or 4-bit without the 5/6-bit overrides), copy it over, and run it as a **separate variant**. Its folder name, file hashes and quantisation are in every record, so it can never be mistaken for the baseline. Harbor measured tool selection getting much worse below 4 bits (`service/setup/catalog.py` TESTED_NOTE), so report its quality-parity subset beside its speed.
3. A Gemma 4 **E2B** build is the next fallback; it is a different model, recorded as such.

### 3.6 What happens at the limit, and how kills are recorded

- iOS ends the app with SIGKILL; nothing runs afterwards. So before each step the runner writes `sentinel.json` (step, cell, footprint, available memory, conditions), and about once a second `heartbeat.json` (footprint, available, phase, thermal, warnings so far). Results go to an append-only, fsync'd `units.jsonl`; a torn last line is ignored.
- At the next launch, before anything else, `RunStore.recoverInterruptedRuns` turns a leftover sentinel into that step's result: **`oom_killed`** if the last heartbeat showed under 600 MiB available or a memory warning, otherwise **`killed`** (force-quit, crash or reboot), with the heartbeat attached as evidence. The run is marked interrupted and Results offers **Resume**, which reloads the model (recorded as a resume load) and continues with the next step.
- iOS gives no kill reason to the app; MetricKit only reports daily counts ([forum 812269](https://developer.apple.com/forums/thread/812269)). The phone's own `JetsamEvent-*.ips` logs (Settings → Privacy & Security → Analytics Data) confirm a jetsam kill if needed.
- Leaving the app pauses the run: iPhone apps get no GPU in the background ([forum 802789](https://developer.apple.com/forums/thread/802789)).

---

## 4. Energy and heat

### 4.1 What the app can read by itself

| Reading | API | Resolution |
|---|---|---|
| Battery level | `UIDevice.batteryLevel` | **5 % steps** since iOS 17, "expected and intended" per an Apple engineer ([forum 788911](https://developer.apple.com/forums/thread/788911)); iOS 26/27 not confirmed. The app records the step it actually observes. |
| Charging | `UIDevice.batteryState` | unplugged / charging / full |
| Thermal state | `ProcessInfo.thermalState` + notification | nominal / fair / serious / critical |
| Low Power Mode | `ProcessInfo.isLowPowerModeEnabled` | on / off |
| Screen brightness | `UIScreen.brightness` | 0–1 |

No public API gives current, voltage, power or temperature. All of these are recorded at the start and end of every step, and in the heartbeat.

### 4.2 What Xcode and Instruments add with the phone connected

- **Power Profiler** (Instruments, iOS 26+): per-subsystem *relative* power impact (CPU, GPU, display, networking) and system power as a fraction of battery per hour. Reads 0 while charging, so pair over Wi-Fi; values are not comparable across devices ([Apple](https://developer.apple.com/documentation/xcode/measuring-your-app-s-power-use-with-power-profiler.md)). The on-device **Performance Trace** (Settings → Developer → Performance Trace, Power Profiler mode) records untethered for up to 10 hours. Useful as a diagnostic (does the GPU dominate? does the display?), never as joules.
- Thermal State and os_signpost lanes in Instruments line up with the app's per-step timestamps.

### 4.3 What needs an external meter

Joules per prefill or decode token: a battery-bypass rig and a Monsoon or Joulescope, as MELT did (Monsoon at 5 kHz, battery bypassed; iPhone 14 Pro reached 47.9 °C, [arXiv 2403.12844](https://arxiv.org/html/2403.12844)). Out of scope for v1 (spec 04 §8.3). Records for speed cells say `energy.scope: "none"` with the reason.

### 4.4 The phone's energy numbers, defined honestly

- **`battery_pct_per_h`** over the 60-minute **Battery hour (S2)** plan: one 2,048-token request every 3 minutes, screen on, on battery from ~90 %. `(start % − end %) ÷ hours`, with **`observed_step_pct`** (the smallest change seen, e.g. 5) as the error bound in percentage points. With 5 % steps a 60-minute run must drain ≥ 25 % for ±20 %; plan two back-to-back hours, or report the bound honestly.
- **`battery_control`**: the same hour with no model loaded and the same screen state, run the same day. The model's share is the difference.
- Run state is recorded so a run that was charging is marked `invalid_not_on_battery`.
- No mAh or Wh: iOS gives no charge counter.

### 4.5 Heat

- Every step records thermal state at start and end; the level cells report `start`, `end`, `worst` and the total cooldown wait.
- **Sustained 20 min (S1)**: back-to-back 2,048-token requests for 20 minutes (spec 04 §6.1 phone duration): initial decode (median of the first 2 minutes), sustained (median of the last 5), `stability_ratio`, `time_to_throttle_s` (first minute below 90 % of initial) and the thermal trace.

---

## 5. Timing

All durations use one monotonic clock (`ContinuousClock`, keeps counting through sleep) in the core, never the wall clock. Definitions are spec 04 §3.2:

| Metric | How the phone measures it |
|---|---|
| `t_load_warm_s` | Unload, then `load()` until mlx-swift-lm returns with the weights realised. |
| `t_load_cold_s` | The same for the first load after a reboot (iOS cannot purge the file cache; spec 04 §4.2). The operator ticks "I rebooted the phone before this run"; the **Cold load** plan is 1 cold + 4 warm loads. One cold sample per reboot. `getrusage` block-input counts are recorded as cold-load evidence (whether iOS counts page-ins there is unverified). |
| `t_first_token_possible_s` | Load start → first token of a ~15-token prompt ("Say OK.") with one output token. |
| TTFT | Send → first token of any kind (reasoning included). TTFA (first answer token) is recorded too. |
| Prefill tok/s | Prompt tokens ÷ the engine's prompt time (mlx-swift-lm `promptPrefillTime` + first step); the client fallback, prompt tokens ÷ TTFT, is kept beside it. |
| Decode tok/s | `(n − 1) ÷ (t_last − t_first)` on the client clock, n = 256, end-of-sequence ignored. |

- **Levels:** 512, 1,024, 2,048, 5,120, 10,240, 32,768, 65,536 prompt tokens counted after the model's own template. The prompt builder binary-searches a corpus prefix to land within 2 tokens below the target, then reuses that size for every rep; each rep gets a fresh 8-character nonce at the top of the system message so nothing could be served from a prefix cache.
- **Repetitions:** 10 per level up to 10k and 5 at 32k and 64k, after one discarded warm-up per level (spec 04 §3.4). Median, IQR, min, max and a bootstrap 95 % CI of the median (fixed seed); **p95 only from n ≥ 20**, nearest-rank as Harbor's `scripts/release_performance.py:231-250`. Sustained runs (S1) have far more than 20 requests, so they report p95.
- **Thermal drift:** levels run round-robin, order reversed every other round, so heating does not pile onto the last level; a cooldown gate before each round waits (up to 5 minutes, polling every 5 s) until thermal state is nominal and records the wait.
- **Limits:** 600 s per step; a level beyond the model's window (131,072) is `exceeds_native_window`, not run.
- **Corpus:** `ios-bench/Resources/corpus-agent-v0.txt`, a deterministic agent-shaped stand-in (tool schemas, tool results, dialogue, email; invented data) from `ios-bench/tools/make_corpus.py`, until the agentic spec's corpus exists. Its sha256 is in every record. The Mac harness must size its prompts from the same file for the numbers to compare.

---

## 6. The portable quality subset

- **Goal:** confirm the same weights behave the same on the phone, not repeat the suite.
- **Bundle:** `ios-bench/Resources/portable-cases.json` (schema `edgebench.portable-cases/0.1`, 35 cases), generated by `ios-bench/tools/export_portable_cases.py` from Toolbench (`scripts/toolbench/cases/*.yaml`) plus authored cases:
  - 24 **select**: single-step tool choice with argument matchers, no history, no world facts;
  - 4 **abstain**: the right answer calls nothing;
  - 4 **extract**: reply with a JSON object, fields matched;
  - 3 **summary**: at most 40 words, key facts present, a planted wrong fact absent, 60-word cap.
- **Fixed prompt and menu:** one neutral system prompt with the run day's date ("Today is Wednesday 2026-11-18.") and one fixed 19-tool menu (`core`, terse schemas, about 1,600 tokens), identical on both sides. Scores are comparable phone-vs-Mac, **not** with full Toolbench numbers.
- **Scoring** is deterministic and the same on both sides: Swift (`ios-bench/Sources/EdgeBenchCore/Quality.swift`) and Python (`ios-bench/tools/run_portable_cases_mac.py`) implement Toolbench's matcher semantics (`scripts/toolbench/matchers.py`), tested with the same fixed dates.
- **Mac side:** `run_portable_cases_mac.py --endpoint <oMLX URL> --model <id> --out mac.json` runs the bundle at temperature 0, seed 1234, thinking on, and writes `edgebench.quality-parity/0.1` in the same shape the phone exports. It was not run.
- **Sets:** `smoke` (6 cases, inside the Smoke plan) and `parity` (all 35, the Quality plan).
- **Parity rule (proposal):** same pass/fail on ≥ 90 % of cases and no family more than one case apart; disagreements are listed case by case for review, since greedy decoding differs across GPUs (spec 02 §4.2).

---

## 7. Vision and audio on the phone (hooks only)

- The folder already carries Gemma 4's vision tower (~320 MiB) and audio tower (~580 MiB); the text-only load leaves them on flash.
- **Hook:** `GenerationRequest` gets an `attachments` field (image file URL or audio PCM) in v2; the MLX engine switches to `VLMModelFactory` (`MLXVLM/Models/Gemma4.swift`) when a plan contains vision or audio cases. That is the same class the MTP path needs, so the two can share one engine variant.
- **Cases:** spec 05 (`05-vision-audio-spec.md`) defines them; the bundle format gains an `attachments` list of files shipped in the app's Resources, scored with the same `json` / `text` expectations.
- **Memory:** VLM path ≈ +0.3–0.9 GiB resident, so it must run as its own variant with its own budget check.
- Nothing is built for this yet.

---

## 8. What was built

### 8.1 Layout (`ios-bench/`, all new; `ios/` and the Mac app are untouched)

| Path | What |
|---|---|
| `Package.swift`, `Sources/EdgeBenchCore/` | Measurement core, no MLX: `BenchClock` (monotonic, manual for tests), `Stats` (nearest-rank, bootstrap CI, fit), `Probes` (phys_footprint, kernel peak, available memory, device identity), `Measurement` (50 ms memory sampler, token timer, timing definitions), `PromptBuilder` (exact-length prompts + nonce), `Plan` (built-in plans, round-robin schedule), `Runner` (the run loop: cooldown gate, battery floor, window and timeout checks, kill skip rules, quality cases), `RunStore` (manifest, append-only results, sentinel, heartbeat, recovery), `Records` (export), `Quality` (case bundle, matchers, Gemma 4 output parser), `StubGenerator`. |
| `Tests/EdgeBenchCoreTests/` | 30 tests, stub generator on a manual clock. |
| `Packages/EdgeBenchMLX/` | MLX engine: text-only folder view, tokenizer bridge, token loop with per-token callback. |
| `App/` | SwiftUI app: Models (scan, import, facts, SHA-256 verify), Run (plan picker, pre-flight checks, live progress), Results (cells, quality, events, resume, export via share sheet). |
| `Resources/` | Corpus, portable cases, known model hashes. |
| `scripts/generate_project.py` | Writes `EdgeBench.xcodeproj` (stdlib only, as `ios/scripts/generate_project.py`). |
| `scripts/with-host-lock.sh` | Runs a build under a shared heavy-build lock, so one Swift build runs at a time. |
| `tools/` | Case exporter, Mac-side quality runner and its tests, corpus generator. |

### 8.2 Built-in plans

| Plan | Steps | Estimate on the Air (guess) |
|---|---|---|
| Smoke check | 2 loads; 512 and 2,048 × 3 reps; 6 quality cases | ~6 min |
| Cold load (after a reboot) | 1 cold + 4 warm loads | ~3 min |
| Context ladder to 10k | 3 loads; 512–10,240 × 10 reps | ~40 min |
| Full context ladder (to 64k) | 3 loads; all seven levels (5 reps at 32k/64k) | ~2 h |
| Quality parity subset | 35 cases | ~15 min |
| Sustained 20 min (S1) | back-to-back 2,048 for 20 min | ~22 min |
| Battery hour (S2) and control | 60 min, one request / 3 min | 61 min each |

The estimates assume roughly 300–600 prefill tok/s and 12–25 decode tok/s for E4B on an A19 Pro. That is extrapolated from E2B at ~29 tok/s on an iPhone 17 Pro Max ([#343](https://github.com/ml-explore/mlx-swift-lm/pull/343)) and has not been measured.

### 8.3 The exported file

One JSON file per run, `edgebench-<device>-<plan>-<run id>.json`, schema `edgebench.run/0.1`:

- `records[]`: one **`edgebench.perf/0.1`** record per cell in the shape of spec 04 §11: `model`, `runtime`, `device`, `conditions` and `cell` with `speed` (`ttft_s`, `ttfa_s`, `prefill_tps`, `prefill_tps_client`, `decode_tps`, `e2e_s`, each `{n, median, iqr, min, max, ci95, p95?, mean, stdev}`), `memory` (`metric: phys_footprint`, `loaded_MiB`, `peak_MiB`, `steady_MiB`, `min_available_MiB`, `kv_MiB_per_1k_fit`, `overhead_MiB`), `energy`, `thermal` and `status`. Load, sustained and battery cells use `test: "load" | "sustained" | "battery" | "battery_control"` with the spec's metric names.
- `quality`: `edgebench.quality-parity/0.1`, the same shape `run_portable_cases_mac.py` writes.
- `raw.units[]`: every step's full result; `raw.memory_series[]`: the 50 ms series per step; `events[]`: kills, memory warnings, pauses, backgrounding.

Deviations from the §11 sketch, all additive:

- statuses `killed`, `skipped_after_oom`, `ok_memory_warning` and `invalid_not_on_battery`;
- `params_*` are `null` (counted on the Mac from the unquantised index);
- `neural_MiB` and `swap_delta_MiB` are `null` (iOS has no swap and the Neural Engine is not used);
- `fits` is empty until a balloon calibration exists (spec 04 §2.4).

---

## 9. Verification done within the rules

(Filled in after the builds; see the end of the build section below.)

---

## 10. Unverified without the phone, and risks

**Unverified (each is answered by the first Smoke run):**

- that mlx-swift-lm 3.32.3 loads this folder through the text-only view (the `verify: [.all]` behaviour and every per-layer quantisation override);
- that swift-jinja renders Gemma 4's template exactly as Python does (compare prompt token counts with the Mac);
- whether a free team keeps the increased-memory-limit entitlement;
- the real per-app budget on the Air;
- footprint after load and the KV slope;
- whether `ledger_phys_footprint_peak` and `ru_inblock` behave as assumed on iOS;
- the battery step size on the phone's iOS;
- whether the Gemma tool-call and thinking markup arrive as single tokens (the classifier assumes `<|channel>`, `<|tool_call>` and the rest are special tokens);
- every time estimate.

**Risks:**

1. **Memory (highest).** About 1.4 GB of headroom over the weights is all there is. If runtime overhead is larger than guessed, even short levels fail and the plan falls back to §3.5. 64k is likely to be killed.
2. **The baseline's size on disk.** 5.4 GB plus the copy over Finder; a stalled copy wastes an hour.
3. **Free-team churn.** The app expires after 7 days and must be rebuilt and reinstalled; the 3-app limit may require removing another sideloaded app. Results stay in the app's Documents, but deleting the app deletes them, so export after every run.
4. **Spec-off only.** Phone decode numbers are not comparable with the Mac's as-shipped MTP numbers.
5. **Thermals.** A phone in a case, on a soft surface or charging will throttle and change the numbers; the cooldown gate only waits for "nominal", which is coarse.
6. **Corpus.** The stand-in corpus differs from the agentic spec's final corpus; numbers should be re-run when it lands.

---

## 11. Open questions

1. **Which iOS version is the phone on?** Settings → General → About. FoundationModels needs 27; everything built here needs 17+.
2. **Free storage on the phone?** About 7.5 GB is needed (folder + headroom).
3. **May the phone run in airplane mode with Wi-Fi off** for the speed runs (spec 04 §7.3)? It needs no network.
4. If the baseline does not fit: **which smaller variant should be made**, 3-bit oQ3e of the same weights or E2B?
5. Should the board show the phone's **spec-off** decode number beside the Mac's spec-off column (proposed), or wait for the MTP path (§1.3 v2)?
6. **Battery test length:** with 5 % steps, one hour gives ±20 %. Run two hours, or accept the bound?
7. Is the **parity rule** in §6 (≥ 90 % same pass/fail, no family more than one case apart) acceptable?
8. Should the Mac harness adopt `corpus-agent-v0.txt` until the agentic corpus exists, so phone and Mac prompts match?

---

## 12. Device-run plan (for the operator)

Nothing here has been done; it needs explicit approval before it runs. Allow about 1.5 hours for the first session, mostly copying and the Smoke and ladder runs.

**1. Set the free team (Mac, 2 minutes).** Create `ios-bench/Config/Local.xcconfig` in the repository (it is git-ignored):

```
DEVELOPMENT_TEAM = <your Personal Team ID, from Xcode → Settings → Accounts → your Apple ID → Personal Team>
EDGEBENCH_BUNDLE_ID = com.<yourname>.edgebench
```

**2. Connect and trust the phone (5 minutes).**

- Plug the iPhone into the Mac, unlock it, and tap **Trust**.
- In Xcode, open Window → Devices and Simulators (Device Hub in Xcode 27) and wait for it to finish preparing the phone.
- On the phone, turn on Settings → Privacy & Security → **Developer Mode**. It restarts; confirm after the restart.
- Check you have a free slot: a free team allows 3 sideloaded apps per device.

**3. Build and install (10–30 minutes the first time; MLX compiles from source).** Open the project:

```bash
open ios-bench/EdgeBench.xcodeproj
```

- Pick the iPhone as the destination and press Run. The scheme builds Release.
- If signing fails with "…does not support the Increased Memory Limit capability", add `EDGEBENCH_ENTITLEMENTS = App/EdgeBench-no-entitlements.entitlements` to `Local.xcconfig`, build again, and expect only small models to load.
- The first launch fails until you trust the developer: on the phone, Settings → General → **VPN & Device Management** → your Apple ID → Trust.
- Do not build while another heavy Swift build runs on the same machine.

**4. Copy the baseline onto the phone (15–40 minutes for 5.4 GB over USB).**

- In Finder, select the iPhone in the sidebar, open **Files**, and expand **EdgeBench**.
- Drag the local `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` folder onto it. Keep the phone unlocked and plugged in.
- In the app, open **Models** and pull to refresh. The folder should show about 5.4 GB, `gemma4_text`, 4-bit, MTP head.
- Tap it, then **Verify SHA-256** (a minute or two). It should say all 10 pinned files match Harbor's manifest.

**5. Prepare the conditions.**

- Charge to about 90 %, then **unplug**: the app warns while charging, and charging heats the phone.
- Turn Low Power Mode off, take the case off, and lay the phone on a hard surface in a 20–26 °C room.
- Close other apps, set minimum brightness, and switch on airplane mode.

**6. First run: Smoke check (about 6 minutes; the phone gets warm, not hot).**

- In **Run**, choose the model and **Smoke check**, enter the room temperature, then **Start**. The screen stays on.
- Watch the live panel: step, phase, tok/s, footprint and available memory, thermal state, battery.
- If the app disappears during the load or a level, that is the memory limit. Reopen it: Results shows the step as `oom_killed` with the last memory reading. Send that file; it is the most important result.

**7. Check the run, then send it back.**

- Open Results → the run. Check the budget at start: ~6,000 MiB means the entitlement worked, ~3,300 means it did not.
- Check the load cell, both levels, and the 6 quality cases.
- Tap **Export JSON**, then **Share**. AirDrop it to the Mac, or save it to Files.
- Every export also stays in Finder → iPhone → Files → EdgeBench → Exports.
- Put the file in `results/iphone/`. The Mac side then compares prompt token counts at 512 and 2,048 (template parity) and quality outcomes with `run_portable_cases_mac.py` on the same bundle.

**8. If Smoke passes, in this order (one per sitting, export after each):**

1. **Cold load**: reboot the phone, wait 5 minutes, open the app, tick "I rebooted", run it. 3 minutes.
2. **Context ladder to 10k**: about 40 minutes. The phone will get warm and may reach "fair"; the cooldown gate waits between rounds, so it can take longer.
3. **Quality parity subset**: about 15 minutes.
4. **Full context ladder (to 64k)**: about 2 hours. Expect 64k, and maybe 32k, to be killed; reopen the app and Resume each time.
5. **Sustained 20 min**: warm to hot. Expect "serious" thermal state and a stability ratio well below 1.
6. **Battery hour**, then **Battery hour, control** on the same day: 61 minutes each, from about 90 %, unplugged, same brightness.

The free-team build expires after 7 days. Rebuild and reinstall from Xcode as in step 3; runs and model files stay in the app's container as long as the app is not deleted.
