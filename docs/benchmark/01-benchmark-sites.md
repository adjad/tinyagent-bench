> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 01 — What other benchmark sites do, and what our edge-agent benchmark should take from them

Written 2026-11-18 · design only, no code

How this was researched: docs, papers and GitHub READMEs were read with a page fetcher. The JavaScript-heavy pages were then opened in a real browser: Pipette, Artificial Analysis (mobile, laptops, methodology), BFCL, LiveBench, Geekbench AI, MTEB, ML.ENERGY and LMArena style control. Every claim below links to a page that was actually opened. The fetcher returns a summary of each page, not the raw text, so the few figures that come only from it are marked *(fetch summary)*. Anything I could not open is listed in [Could not verify](#could-not-verify).

---

## Summary

**1. Someone already does half of this, and we must build on it rather than copy it.**
- **What exists.** On 24 August 2026 Artificial Analysis (AA) and Liquid AI launched joint benchmarking of small models on phones ([AA article](https://artificialanalysis.ai/articles/mobile-phone-intelligence-inference)).
  - **Pipette** (Liquid AI) measures prefill speed, decode speed, end-to-end latency and peak memory. Each result is tied to one exact model + quantisation + runtime + flags + device, and runs on a lab fleet with a thermal "readiness gate" before every repetition ([Pipette docs](https://pipette.liquid.ai/docs/introduction)).
  - **AA** adds a five-eval intelligence score for the same quantised builds (BFCL tool-calling subset, IFBench, AA-Omniscience, GPQA Diamond, MATH-500).
  - **AA-AgentPerf-Local** times how fast a machine replays recorded agent conversations at 64K context ([GitHub](https://github.com/ArtificialAnalysis/aa-agentperf-local)).
- **Pipette is the "Pipette" that Harbor's perf script names as its comparison target.** The script's report section links `https://pipette.liquid.ai/leaderboard`.

**2. Nobody publishes what would make our benchmark distinct.** Each item below is checked against the pages listed:
- **Agentic plan → act → verify tasks run on the device itself.** AA scores tool calling with BFCL's single-shot and multi-turn categories. It says frontier agent evals "return near-zero scores" for small models.
- **Context up to 64K.** Pipette's public ladder stops at 8,192 tokens on Macs and 4,096 on phones ([coverage page](https://pipette.liquid.ai/docs/methodology/coverage-and-selection)).
- **Energy per token on phones and laptops.** Pipette has an energy survey but no published energy numbers. ML.ENERGY and the HF AI Energy Score cover datacentre GPUs only.
- **Model load time from SSD.** Every site we read excludes it.
- **Sustained, throttled performance.** Pipette deliberately measures cool devices only.
- **Memory saved by removing vision or audio components.** No site measures it.

**3. The biggest gaps in the draft list** (detail in [Missing from our list](#1-missing-from-our-list)):
- **Repeated-trial reliability:** pass^k, not just pass@1.
- **A fixed identity per result:** quantisation, runtime and version, flags, weight-file hash.
- **Device-state control:** a thermal gate, a power and cooling disclosure, and runs that swapped excluded.
- **A memory-fit gate**, with "did not fit" shown as a result.
- **Abstention cases:** irrelevance, "no tool fits", and a hallucinated-call rate.
- **Context-budget overruns and a time-budgeted score.**
- **Energy and time per completed task**, not only per token.
- **Confidence intervals on every number.**
- **Contamination control and versioned methods**, with raw data published.

**4. The methodology rules worth adopting first** (detail in [Methodology rules to copy](#2-methodology-rules-to-copy)):
- Exact token counts.
- Measured repetitions after a discarded warm-up, reported as mean ± sample SD.
- A per-platform readiness gate that records an error instead of a throttled number.
- Peak memory from one fresh-process run.
- Native function-calling only, with no rescue of malformed tool calls.
- State-based checking of multi-turn tool tasks.
- Reasoning and non-reasoning modes listed as separate variants.

**5. The UI patterns that work best** (detail in [UI patterns to copy](#3-ui-patterns-to-copy)):
- A device selector that scopes the whole page.
- A quality-vs-time scatter with a Pareto halo and threshold inputs.
- A context-scaling line chart.
- A chart where each model's quantisation variants are joined by lines.
- Top-model-per-size-tier cards.
- "Did not fit" cells drawn as results.
- A drill-down from every number to its raw rows.

---

## Comparison table across all sites

"Current" is the most recent date visible on the site when opened on 2026-11-18. In the edge column, **✓✓** means directly about on-device small-model inference, **✓** means a useful method or layout, and **–** means not edge-relevant.

| Site | Category | Current (date seen) | What it measures | Who runs it | Repetitions / stats | Edge |
|---|---|---|---|---|---|---|
| [Pipette](https://pipette.liquid.ai/leaderboard) (Liquid AI) | On-device perf | Data to 2026-11-05; 4,760 records since 2026-10-05 | Prefill tok/s, TTFT (prefill only), decode tok/s, E2E latency, peak RAM, joined to quality | Liquid AI lab fleet; community submissions planned | 5 measured reps, mean ± sample SD; readiness gate per rep | ✓✓ |
| [AA mobile phones](https://artificialanalysis.ai/hardware-inference-stack/mobile-phones) | On-device intel + perf | Launched 2026-08-24; live page | 5-eval average + E2E (1,024 in/256 out), output speed, peak memory, context overruns | AA (evals) + Liquid (inference) | 5 repeats/question pass@1 | ✓✓ |
| [AA laptops & AgentPerf-Local](https://artificialanalysis.ai/hardware-inference-stack/laptops-workstations) | Local agent serving speed | Prices "October 2026" | Completion time of 168-turn, 172-tool-call replay | AA lab + open tool for users | n/a on page | ✓✓ |
| [AA API performance](https://artificialanalysis.ai/methodology/performance-benchmarking) | API speed | Method v3.0.0, 2026-11-03 | Output speed, TTFT, time to first answer token, E2E, error rate | AA | Median per run, mean of runs over 5 days | ✓ (definitions) |
| [AA Intelligence Index](https://artificialanalysis.ai/methodology/intelligence-benchmarking) | Quality index | v5.0.1, Oct 2026 *(fetch summary)* | 9 evals, 4 categories × 25% | AA | 1–5 repeats per eval | – |
| [LMArena / arena.ai](https://arena.ai/leaderboard/text) | Human preference | 2026-11-18 | Bradley-Terry score from pairwise votes | Public votes | Bootstrapped CIs | ✓ (style control) |
| [LiveBench 2.0](https://livebench.ai/) | Contamination-free tasks | Release 2026-11-06, updated 2026-11-11 | 7 suites + cost per successful task | Abacus AI | n/a | ✓ (UI) |
| [HF Open LLM Leaderboard](https://huggingface.co/posts/burtenshaw/596060753193040) | Static evals | **Retired** 2025-03-14 | 6 evals, normalised | HF | – | ✓ (lessons) |
| [Scale Labs leaderboards](https://labs.scale.com/leaderboard) | Private held-out evals | Live, no dates | 13 boards, scores ± CI | Scale | CIs | – |
| [Epoch AI Benchmarking Hub](https://epoch.ai/benchmarks) | Eval hub | "Nov 19, 2026" (sic) | 99 benchmarks, ECI | Epoch + external | 8–16 runs, ±1 SE | ✓ (data access) |
| [llm-stats.com](https://llm-stats.com/) | Aggregator | "Continuously" | Mixed self-reported + verified | Aggregated | Mixed | – |
| [HELM](https://github.com/stanford-crfm/helm) | Holistic evals | **Maintenance mode** since 2026-06-01 | 7 metrics × scenarios, raw prompts | Stanford CRFM | – | ✓ (transparency) |
| [BFCL V4](https://gorilla.cs.berkeley.edu/leaderboard.html) | Function calling | **Last updated 2026-04-12** | AST/state-checked calls, irrelevance, multi-turn, format sensitivity, cost, latency | Berkeley | Single run; format SD | ✓✓ (tasks) |
| [τ-bench / τ²](https://github.com/sierra-research/tau2-bench) | Tool-agent-user | taubench.com live; 2.0 | pass^k, DB-state reward | Self-submitted; "Verified" tier | ≥3–4 trials, pass^1..4 | ✓✓ (reliability) |
| [Terminal-Bench](https://www.tbench.ai/leaderboard) | CLI agents | v4.1 board | Resolution rate ± 95% CI; model and agent columns | Self-run via harness | ≥5 runs per pair (2.0 paper) | ✓ |
| [HAL](https://hal.cs.princeton.edu) | Agent cost + reliability | New models **paused** | Accuracy vs cost Pareto; consistency (2p̂−1)² | Princeton + submitters | 21,730 rollouts (paper) | ✓ |
| [AndroidWorld](https://github.com/google-research/android_world) | Mobile GUI agent | Docker support 2025-06 | Task success via adb state checks | Self-run; Google Sheet board | Seeds; ±3 pts variance | ✓ |
| [OSWorld-Verified](http://osworld-v1.xlang.ai/) | Desktop GUI agent | 2.0 announced 2026-06-26 | Execution-script success | Maintainers re-run "verified" | n/a | ✓ |
| [Apple ToolSandbox](https://github.com/apple/ToolSandbox) | Stateful tool use | v2 Apr 2025 | Milestone-DAG similarity | Self-run, no board | – | ✓✓ (scoring) |
| [When2Call](https://arxiv.org/html/2504.18851v1) | Call / ask / refuse | NAACL 2025 | Decision F1, tool-hallucination rate | Self-run | – | ✓✓ (small models) |
| [Geekbench AI](https://browser.geekbench.com/ai/v1) | Device AI score | Uploads 2026-11-18 | 10 vision/NLP workloads × 3 precisions; **no LLM** | Crowd uploads | ≥5 iterations ≥1 s | ✓ (pitfalls) |
| [MLPerf Client](https://mlcommons.org/benchmarks/client/) | PC/Mac LLM | v2.0.1 (24 Sep) | TTFT, TPS (excl. first token), accuracy gate | Users/vendors run binary | 1 warm-up + 3 runs, mean | ✓✓ (defs) |
| [MLPerf Mobile](https://mlcommons.org/2026/06/mlperf-mobile-v6/) | Phone app | v6.0, June 2026 | Adds Llama 3.2 1B/3B, 3.1 8B | Users/vendors | p90 latency | ✓ |
| [MLPerf Inference Edge + Power](https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc) | Audited inference | Round v6.1 | p90 latency, offline throughput, wall energy | Vendors; audited | ≥600 s runs | ✓ (rigour) |
| [AI Benchmark (ETH)](https://ai-benchmark.com/ranking.html) | Phone NN score | "Updated 11.26" | 78 tests; no LLM | Site team | Formula unpublished | – |
| [LocalScore](https://www.localscore.ai) | Local LLM speed | Live | pp tok/s, tg tok/s, TTFT → one score | Self-submitted CLI | Reps configurable | ✓ |
| [Qualcomm AI Hub](https://aihub.qualcomm.com/models) | Per-device model perf | 272 models | Latency, TTFT, memory, compute unit | Qualcomm on real devices | n/a | ✓ |
| [UL Procyon AI Text Gen](https://benchmarks.ul.com/en/procyon/ai-text-generation-benchmark) | Commercial PC LLM | v1.1.231.0, 2026-11-17 | √(TTFT score × tok/s score) | Licensed users | Means over 7 prompts | ✓ |
| [PocketPal leaderboard](https://pocketpal.dev/leaderboard) | Crowd phone perf | "Nov 19, 2026" (sic) | pp512 / tg128, Glicko-2 device rating | Crowd, unverified | Spread + submission count | ✓✓ (crowd) |
| [Google LiteRT-LM cards](https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm) | Vendor edge numbers | Blog 2026-05-19 | Prefill, decode, TTFT, memory per device/backend | Google self-report | Not stated | ✓ |
| [Liquid LFM2.5 cards](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct) | Vendor edge numbers | 2026 | 1K prefill / 100 decode tok/s, memory | Vendor self-report | Not stated | ✓ |
| [llama.cpp #4167 / llama-bench](https://github.com/ggml-org/llama.cpp/discussions/4167) | Community Apple Silicon | Updated 2026-08-25 | pp512 / tg128 by chip | Crowd thread | `-r 5` mean ± sd | ✓✓ (tool) |
| [mlx_lm.benchmark](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/benchmark.py) | MLX tool | Live repo | prompt tps, gen tps, peak memory | Self-run | 5 trials, 1 warm-up, mean | ✓✓ (tool) |
| [Anubis OSS](https://uncsoft.github.io/anubis-oss/analysis.html) | Crowd Apple Silicon | 2026-08-22 | tok/s, TTFT, W/token | Crowd | 2–20 reps, 95% CI | ✓ |
| [ML.ENERGY](https://ml.energy/leaderboard/) | GPU energy | v3.0, 2026-02-04 | J/token, J/response vs inter-token latency | Maintainers | No repeats (paper) | ✓ (UI) |
| [HF AI Energy Score](https://huggingface.github.io/AIEnergyScore/) | GPU energy label | Org update 2025-12-04 | GPU Wh / 1,000 queries, 1–5 stars | Portal submissions on H100 | 10 runs averaged | ✓ (label) |
| [Open ASR Leaderboard](https://github.com/huggingface/open_asr_leaderboard) | Speech-to-text | README 2026-07-24 | WER, RTFx | PRs, re-run by maintainers | None | ✓✓ (audio) |
| [AA speech-to-text](https://artificialanalysis.ai/methodology/speech-to-text) | ASR APIs | AA-WER v2.2 | WER index, speed factor, streaming latency | AA | Median over 7 days | ✓ |
| [Open VLM Leaderboard](https://huggingface.co/spaces/opencompass/open_vlm_leaderboard) | Vision-language | VLMEvalKit news 2026-11-10 | Avg over VLM benchmarks | OpenCompass | None | ✓ |
| [MTEB](https://mteb-leaderboard.hf.space/) | Embeddings | Live | Per-task means, Borda rank, zero-shot % | PRs | – | ✓ (UI) |
| [Vectara hallucination](https://github.com/vectara/hallucination-leaderboard) | Summary faithfulness | 2026-10-22 | Hallucination rate, answer rate, summary length | Vectara | Not stated | ✓✓ (summaries) |
| [SEA-HELM](https://leaderboard.sea-lion.ai/) | Multilingual | 2026-11-18 | 7 SEA languages, 95% CI | AI Singapore | 2,000 bootstraps | ✓ (UI) |
| [RULER](https://github.com/NVIDIA/RULER) / [NoLiMa](https://github.com/adobe-research/NoLiMa) | Long context | 2025 | Effective context length | Self-run | 500 examples/length (RULER) | ✓✓ (context) |
| [EQ-Bench Creative Writing v3](https://eqbench.com/about.html) | Prose quality | News 2026-10-25 | Rubric + pairwise Elo, slop score | Maintainer | 3 runs per prompt | ✓ (prose) |
| [AlpacaEval 2 LC](https://github.com/tatsu-lab/alpaca_eval) | Length-controlled judge | LC default since 2024-03-12 | LC win rate | PRs; top 5 verified | – | ✓ (verbosity) |

---

## Per-site notes

Each block covers: **M** what it measures · **D** exact definitions · **C** test conditions · **E** how models get in and how results are verified · **UI** how results are shown · **X** criticisms and failure modes.

### A. On-device small-model benchmarks (the closest neighbours)

#### Pipette (Liquid AI) — https://pipette.liquid.ai/leaderboard
- **M:** It benchmarks "complete deployment configurations: model, quantization, runtime, device, and workload". It covers speed, memory and accuracy in one view ([intro](https://pipette.liquid.ai/docs/introduction)).
  - The leaderboard lists about 65 GGUF models: LFM2.5, LFM3, Qwen3.5/3.6, gemma-4, granite, Nemotron, MiniCPM5, Ministral 3, ternary Bonsai and others.
  - Devices: Galaxy S26 Ultra, Mac Studio, MacBook Pro, Ryzen AI Max+ 395 and iPhone 17 Pro.
  - The Mac Studio view states "4,760 lab-condition metric records collected by Liquid AI since October 5, 2026, with the latest data as of November 5, 2026" ([leaderboard](https://pipette.liquid.ai/leaderboard)).
  - Quality evals run separately on an AMD MI325X system and are joined to device results by model + quantisation ([intro](https://pipette.liquid.ai/docs/introduction)). The evals are IFBench, IFStruct, GPQA Diamond and MATH-500.
- **D** ([performance methodology](https://pipette.liquid.ai/docs/methodology/performance-methodology)):
  - **Prefill throughput:** input tokens divided by prefill time.
  - **TTFT:** the dashboard label is prefill time *only*. It "does not include tokenization, request handling, or delivery of the first generated token".
  - **Decode throughput:** output-token rate after the first output token.
  - **E2E latency:** submit to last token, including runtime overhead.
  - **Benchmark IDs:** `prefill_throughput_<P>`, `decode_throughput_<P>_100` (100 output tokens), `end_to_end_latency_<P>_256` (256 output tokens) and `max_memory_usage_<P>` (1 output token).
  - **Peak memory:** the high-water mark of host and GPU counters, never summed on unified memory. Apple Silicon host memory already includes GPU allocations, and Android host memory includes sampled swap ([peak memory](https://pipette.liquid.ai/docs/benchmarks/peak-memory-usage-latest)).
- **C:**
  - **Prompt catalogue:** 100, 256, 512, 1,024, 2,048, 4,096 and 8,192 tokens. Public charts show 256–8,192 on macOS and Windows and 256–4,096 on Android and iOS ([coverage](https://pipette.liquid.ai/docs/methodology/coverage-and-selection)).
  - **Prompts and decoding:** prompts are synthesised to exact token counts, with greedy decoding for E2E.
  - **Repetitions:** 5 measured reps, reported as mean and Bessel-corrected SD. Warm-up is excluded.
  - **Peak memory:** one measured run after a discarded warm-up in a separate process, with no SD.
  - **Runtimes:** stock llama.cpp b11988. Android is CPU-only with flash attention off. iOS runs in-app on Metal. macOS and Windows have flash attention on ([limitations](https://pipette.liquid.ai/docs/methodology/limitations-and-future-directions)).
  - **Fixed identity fields:** threads, GPU layers, mmap and context sizing are part of the configuration identity.
  - **Readiness gate before each measured rep** ([device conditions](https://pipette.liquid.ai/docs/methodology/device-conditions)). If any check fails before the deadline, the whole cell is stored as an error.

    | Platform | Deadline | Thermal check | Load check |
    |---|---|---|---|
    | macOS | 7 min | Thermal pressure nominal and SoC die < 50 °C | < 1 busy core over 1 s |
    | Android | 5 min | CPU cluster < 34 °C | CPU < 30 % busy |
    | iOS | 5 min | Die < 36 °C (private-API build) or a motion-sensor estimate < 38 °C (App Store build) | — |
    | Windows | 5 min | — | CPU ≤ 40 %, GPU < 5 % |

  - **Rig:** phones sit on a charger with one active cooler clamped to the back. Each row records `device_battery_level`, `device_power_state` and `device_power_save_mode`.
- **E:**
  - **Who runs it:** only Liquid AI devices for now. Third-party submissions are "planned" and would be tagged as not lab-condition ([device conditions](https://pipette.liquid.ai/docs/methodology/device-conditions)).
  - **Swap:** a run that used swap is ineligible. The collector retries up to 5 times ([publication](https://pipette.liquid.ai/docs/methodology/result-selection)).
  - **Eval versions:** only one canonical version of each eval is approved, and versions are never mixed. "Average Intelligence" is the mean of the selected scores.
  - **Harness:** open source ([pipette-clients](https://github.com/Liquid4All/pipette-clients/tree/704fae756db8a247d9294736b149567ea7c3023c)).
  - **Validation:** AA says it independently validated the method ([AA mobile methodology](https://artificialanalysis.ai/methodology/mobile-device-benchmark-set)).
- **UI** ([leaderboard](https://pipette.liquid.ai/leaderboard)): a global device selector, a filter chip row (quant, runtime such as `llama.cpp:macos-arm64`, runtime version, input tokens), and seven charts. Each chart has a "How to read this chart" expander:
  1. Quality vs E2E Pareto. A halo marks frontier points, dashed lines join a model's quant variants, and threshold inputs cover min decode, min prefill, max TTFT, max E2E, max peak RAM and min intelligence.
  2. "Per-model performance across the board". Thin rank bars sit in one lane per metric, and "longer is always better".
  3. Score vs size tier.
  4. A ranked bar / list toggle.
  5. "How performance scales with context length", with one line per model + quant.
  6. "Memory cost of throughput", with prefill and decode dots joined vertically against log peak RAM.
  7. "Accuracy hit from quantization".

  Every row links to the raw Submissions view with a full query string. The site also has separate Leaderboard / Results / Submissions views, a data changelog and data exports on request.
- **X** (self-stated):
  - Lab conditions, not consumer use.
  - Compare only within one device, since cross-device results are "not controlled hardware-only comparisons".
  - Long reps can still heat past the gate: the S26 Ultra hits its 105 °C trip point in 2–4 s under full load even with a cooler.
  - App Store iOS builds can't reproduce the published thermal gate.
  - Memory counters differ by platform.
  - No NPU results, no context beyond 8K, and no energy.

#### Artificial Analysis — mobile phones and the Mobile Device Benchmark Set
- **M:** Intelligence on the exact quantised build a phone loads, plus Pipette inference numbers ([article, 2026-08-24](https://artificialanalysis.ai/articles/mobile-phone-intelligence-inference); [live page](https://artificialanalysis.ai/hardware-inference-stack/mobile-phones)). Devices: iPhone 17 Pro 12 GB and Galaxy S26 Ultra 12 GB.
- **D** ([methodology](https://artificialanalysis.ai/methodology/mobile-device-benchmark-set)):
  - **Eligibility:** the model must fit "inside 8 GB of memory after quantization, including the KV cache at 8K context".
  - **Score:** a simple average of five evals:
    - BFCL v4, 640-task subset: Multiple 200, Multi-turn base 200 and Irrelevance 240, each a third of the BFCL score. It uses the non-live split and native function calling only, with llama.cpp template parsing. They do "not attempt to rescue tool calls from the text content".
    - IFBench: 294 prompts, prompt-level, loose mode.
    - AA-Omniscience: accuracy plus non-hallucination, weighted 50/50.
    - GPQA Diamond: 198 questions.
    - MATH-500.
  - **E2E:** the seconds to process a 1,024-token prompt and generate 256 tokens.
  - **Peak memory:** quoted at 4K context.
- **C:**
  - Q4_K_M or smaller GGUF on llama.cpp.
  - 16K context, no compaction. Running out of context before the answer counts as wrong. AA notes real phone limits are "often <4K".
  - Also scored at 64K and under a 1-minute budget (output speed × 60).
  - 5 repeats per question at pass@1; AA-Omniscience runs once over 6,000 questions.
  - Reasoning and non-reasoning modes are listed separately.
- **E:** AA runs the evals and Liquid runs inference. There is no self-submission.
- **UI:**
  - Intelligence vs E2E scatter with a "Most attractive quadrant" and a Pareto line.
  - Tabs that swap the x-axis to peak memory or output tokens.
  - A score toggle: 16K / 64K / 1-min.
  - "Context budget overruns", the percentage of generations stopped at 16K.
  - Per-eval bars.
  - Models that qualified but failed on a device shown in a different colour or pattern, with the note "Performance measurements omitted as the model did not fit on this device or exceeded the time limit".
  - A header with hardware tiers by power: phones 2–20 W, laptops 15 W–2 kW, datacentre 700 W–600 kW.
- **X:**
  - The 1-minute budget flips the ranking: Nanbeige4.2-3B falls from 63 to 18 and LFM2.5-8B-A1B leads.
  - Qwen3.5 9B (Reasoning) used 74.5M output tokens across one pass versus 5.2M for Gemma 4 E4B and hit the 16K cap on 29% of generations ([article](https://artificialanalysis.ai/articles/mobile-phone-intelligence-inference)).
  - AA itself says frontier agent evals floor out for small models, phone runtimes have template and parser bugs ("parsers that drop parallel tool calls"), and "one model name covers many builds".
  - Next steps it lists: compare by memory footprint instead of one quantisation, and add more frameworks.

#### Artificial Analysis — laptops/workstations and AA-AgentPerf-Local
- **M:** "Local agent inference speeds" on AA's own hardware: the time to complete the default replay of 168 turns and 172 tool calls ([page](https://artificialanalysis.ai/hardware-inference-stack/laptops-workstations)).
  - Systems: MacBook Pro M5 Pro 64 GB, Ryzen AI Halo, DGX Spark, RTX Spark N1X and RTX 5090.
  - Example: Qwen3.6 35B A3B takes 11.9 min on the M5 Pro.
- **D/C** ([README](https://github.com/ArtificialAnalysis/aa-agentperf-local)):
  - It replays recorded agent conversations, with "each request carries the full conversation so far". It measures speed, not quality.
  - The "exact" policy sends `ignore_eos` so every turn generates its recorded token count.
  - It needs 65,536 context at batch 1. Smaller runs are flagged `reduced: true` and are "not comparable".
  - `managed-run` pins the model and framework version and checks SHA-256 for every file.
  - Tool time is selectable: skipped, fixed delay, or live in Docker.
- **E:** Official runs are AA's. Users can submit optionally, and `doctor` prints hardware facts "without identifiers".
- **UI:**
  - A system card per machine with memory, bandwidth, memory type, chip, launch date and MSRP.
  - A model × system heat table with "Does not fit in 64 GB" cells.
  - Editable system prices that drive a completion-time vs price Pareto chart.
  - A "Serving configurations" library.
- **X:** Ollama can't honour `ignore_eos`, so its results are "not directly comparable".

#### PocketPal AI leaderboard — https://pocketpal.dev/leaderboard
- **M:** Crowd-sourced phone prefill and decode speed from the PocketPal app ([results](https://pocketpal.dev/leaderboard/results)).
- **D:** Defaults are pp = 512 and tg = 128 ([announcement](https://github.com/a-ghorbani/pocketpal-ai/discussions/155)). Device ranking uses Glicko-2 from pairwise matchups on the same model, weighting TG 60% and PP 40%. The sort key is rating − 2 × RD. A device needs ≥ 3 models and ≥ 5 matches, and rankings are recomputed every 6 h *(fetch summary)*.
- **C:** Each row records GPU layers, threads, flash attention, and K/V cache type.
- **E:** Optional sharing from the app with no verification step.
- **UI:**
  - Tabs: Rankings / Results / Compare / Trends / Engines.
  - Rankings columns: Rating, TG Rating, PP Rating, Uncertainty (RD), Matches.
  - Results table: Spread, submission count, group-by, column chooser, CSV export. The site reports 12,667 submissions condensed into 8,665 rows.
- **X:** Unverified mixed settings, and the ranking page shows no chip or RAM. The Glicko design is a response to crowd noise.

#### Vendor edge numbers: Google LiteRT-LM, Liquid AI, Nexa
- **Google LiteRT-LM** ([Gemma 4 E2B card](https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm)):
  - **Shape:** 1,024 prefill and 256 decode tokens at a 2,048 context.
  - **TTFT:** "excludes load time. Caches were enabled and initialized".
  - **Example rows:** S26 Ultra GPU 3,808 prefill / 52.1 decode tok/s, TTFT 0.3 s, 676 MB; S26 Ultra CPU 557 / 46.9 tok/s, TTFT 1.8 s, 1,733 MB *(fetch summary)*.
  - **Gaps:** run count is not stated, and the NPU row uses a different model file and a 4,096 context.
- **Liquid LFM2.5 cards** ([LFM2.5-1.2B](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct)) say "calculated using 1K prefill and 100 decode tokens", with no repetitions or thermal state given. Pipette is effectively Liquid's fix for this.
- **Nexa:** `nexa.ai/blog` now redirects to Qualcomm AI Hub, and the NexaSDK README references Qualcomm GenieX. Status is unclear (see [Could not verify](#could-not-verify)).

#### Community tools: llama-bench, mlx_lm.benchmark, Anubis, others
- **llama-bench** ([README](https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md)):
  - Test types: `pp`, `tg`, `pg`.
  - `-r 5` repetitions by default, reported as mean ± sd. Warm-up is on.
  - `-d <n>` pre-fills the KV cache to depth n. This is the right tool for "decode at 32K/64K depth".
  - "Measurements exclude tokenization and sampling time."
  - [Discussion #4167](https://github.com/ggml-org/llama.cpp/discussions/4167) collects pp512/tg128 results by Apple chip, with bandwidth and GPU cores. Mixed builds are called "incomparable".
- **mlx_lm.benchmark** ([source](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/benchmark.py)): random prompt token IDs with EOS cleared, 5 trials and 1 warm-up. It prints prompt_tps, generation_tps and peak_memory, with no stddev.
- **Anubis OSS** ([analysis](https://uncsoft.github.io/anubis-oss/analysis.html)): a crowd-sourced Apple Silicon board with W/token. Each aggregate shows "± X · N reps". It admits some top results came from cloud-routed models, a 550B model that cannot fit 128 GB.
- **Also seen:** DeviceMark ([HF dataset](https://huggingface.co/datasets/devicemark/results), where the power field is "reserved for future use"), the ExecuTorch perf dashboard ([README](https://github.com/pytorch/executorch/blob/main/extension/benchmark/README.md), which colours regressions over 5%), and nyunai's Raspberry Pi board.

### B. Edge hardware benchmarks (industry standard)

#### MLPerf Client — https://mlcommons.org/benchmarks/client/
- **M/D:** "TTFT: the wait time in seconds before the system produces the first token". "TPS: the average rate to produce all of the rest of the tokens … excluding the first one."
- **C:**
  - Models: Llama 3.1 8B and Phi 4 Mini (mandatory), Phi 4 Reasoning 14B (extended), Qwen 3 8B (experimental).
  - Categories in input/output tokens: 128/256, 512/512, 830/256, 1,856/128 and ~3,906/128, plus an extended ~7,631/128 that doesn't count toward the score.
  - "Each test runs once as warm-up, then three times … average of the three."
  - An accuracy gate on MMLU / IFEval, e.g. Llama 3.1 8B must reach 62 / 39.
  - Runs on macOS through MLX and llama.cpp Metal, and in an iPad app ([repo](https://github.com/mlcommons/mlperf_client)).
- **E:** Users and vendors run the binary. Results are valid only with the "configuration tested" notice. No public board was found.
- **X:** None sourced. No context beyond ~7.6K.

#### MLPerf Mobile — https://mlcommons.org/2026/06/mlperf-mobile-v6/
- v6.0 adds Llama 3.2 1B / 3B and 3.1 8B on TinyMMLU and IFEval. They run on CPU by default; an NPU path exists only for 8B on Snapdragon 8 Elite Gen 5.
- The result spec has `latency90`, `isMinDurationMet` and `isResultValid` ([result-spec](https://github.com/mlcommons/mobile_app_open)). The LLM metric definitions could not be verified.

#### MLPerf Inference Edge + Power — [rules](https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc), [power](https://github.com/mlcommons/inference_policies/blob/master/power_measurement.adoc)
- **Edge Llama 3.1 8B:** CNN/DailyMail, inputs ≤ 2,048 tokens, outputs ≤ 128. It must reach 99% of the FP32 ROUGE score.
- **Scenarios:** single-stream, reported at 90th-percentile latency with early stopping, and offline.
- **Duration:** every scenario runs at least 600 s.
- **Power:**
  - Measured at the wall with SPEC PTDaemon.
  - Power and performance must come from the same run ("cherry-picking … prohibited").
- **Audits:** up to 2 per round.
- **Lesson:** this is the rigour ceiling. Laptops and phones have a battery in the power path, so wall-only power doesn't transfer.

#### Geekbench AI — https://www.geekbench.com/ai/
- **M:** "Ten AI workloads, each with three different data types" (single precision, half precision, quantised) on CPU, GPU or NPU, using a framework the user picks. There is **no LLM workload**.
- **D:** Each workload score is "adjusted using a factor based on the accuracy". The overall is a geometric mean ([workloads PDF](https://www.geekbench.com/doc/geekbench-ai-workloads.pdf)).
- **E/UI:** The [results browser](https://browser.geekbench.com/ai/v1) is crowd uploads with columns Device, Framework, and Single / Half / Quantized. The same laptop appears about 15 times on one day without being collapsed.
  - The same iPhone 17 Pro Max scored 7,282 quantised on Core ML CPU vs 52,509 on the Neural Engine. The backend changes the score by about 7×.
- **X:** Signal65 found an NPU subtest at 22% accuracy that still scored 2,771 ([analysis](https://signal65.com/research/ai/new-geekbench-ai-1-0-benchmark-analysis-and-early-results/)). Multiplying by accuracy hides broken outputs.

#### LocalScore — https://www.localscore.ai
- **D:** score = 10 × ∛(prompt tok/s × gen tok/s × 1000 / TTFT ms) ([llamafile README](https://github.com/Mozilla-Ocho/llamafile/tree/main/localscore)).
- **C:** 9 prompt/generation shapes from 16/1,536 to 4,096/256 tokens. No memory or energy.
- **E:** Self-submitted through the CLI with non-PII hardware info. No accuracy gate.
- **UI:**
  - Leaderboards split into Tiny (1B) / Small (8B) / Medium (14B).
  - An accelerator page with a model × metric grid and a "Compare Models" chart ([example](https://www.localscore.ai/accelerator/1)).

#### Qualcomm AI Hub, UL Procyon, AI Benchmark (ETH)
- **Qualcomm AI Hub** ([models](https://aihub.qualcomm.com/models)): TTFT "corresponds to the prompt processor's latency". A filter sidebar covers chipset, device, precision, runtime and tags (LLM / VLM / MoE). Per-device tables could not be read.
- **UL Procyon** ([score doc](https://support.benchmarks.ul.com/support/solutions/articles/44002570744-how-is-the-procyon-ai-text-generation-benchmark-score-calculated-)):
  - Per-model score = √((C1 / TTFT) × (C2 × tok/s)), with the constants published.
  - It uses 7 prompts mixing RAG and non-RAG.
  - Engines include MLX on macOS.
- **AI Benchmark** ([ranking](https://ai-benchmark.com/ranking.html)) has a "Lib" column (NNAPI, QNN, Core ML…). It has no LLM tests and an unpublished score formula.

### C. Tool use and agent benchmarks

#### Berkeley Function-Calling Leaderboard V4 — https://gorilla.cs.berkeley.edu/leaderboard.html
- **M:**
  - Agentic: web search and memory.
  - Multi-turn.
  - Non-live and live AST.
  - Hallucination measurement: relevance and irrelevance.
  - Format sensitivity.
  - Cost ($) and mean latency (s).
- **D:**
  - The page says "Overall Accuracy is the unweighted average of all the sub-categories". The [V4 blog](https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html) gives explicit weights instead: Agentic 40 / Multi-turn 30 / Live 10 / Non-live 10 / Hallucination 10 *(fetch summary)*. **The two conflict.**
  - AST matching is all-or-nothing for parallel and multiple calls ([V1 blog](https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html)).
  - A multi-turn task is correct only if both a state check and a response check pass in every turn, and a task is force-stopped after more than 20 steps ([V3 blog](https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html)).
- **C:** "Models are evaluated using commit f7cf735". FC rows (native tool calling) and Prompt rows are listed separately. Format-sensitivity tests 26 prompt variants ([blog](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html)).
- **E:** A PR adds a model handler. All model responses are published.
- **UI:** Grouped two-row headers, sortable columns, search, and a "Wagon Wheel" radar.
- **X:**
  - **Stale:** last updated 2026-04-12, seven months before this writing.
  - Small models collapse when the tool-call format changes (format blog).
  - Small rows do exist, e.g. Gemma-3-1b-it (Prompt) overall 7.17 and xLAM-2-3b 41.22.

#### τ-bench / τ²-bench / τ³ (Sierra) — https://github.com/sierra-research/tau2-bench
- **D:** pass^k = E_task[C(c,k) / C(n,k)], the chance that all k independent trials succeed ([paper](https://arxiv.org/html/2406.12045)). The reward is a database-state match.
- **C:** The original used an LLM user simulator at temperature 1.0, the agent at temperature 0, a cap of 30 actions, and ≥ 3 trials. Submissions report pass^1 … pass^4.
- **E:**
  - Self-submitted with trajectories ([submission guide](https://github.com/sierra-research/tau2-bench/blob/main/docs/leaderboard-submission.md)).
  - The "Verified" label needs unmodified prompts.
  - Infrastructure errors are excluded from the denominator but reported as `error_trials`.
- **X:**
  - Simulator noise.
  - Reward is "necessary not sufficient".
  - The authors didn't test 7B/13B models because the benchmark was too hard.

#### Terminal-Bench, HAL, AndroidWorld, OSWorld
- **Terminal-Bench** ([board](https://www.tbench.ai/leaderboard)):
  - Resolution rate ± 95% CI, with separate Model and Agent columns.
  - TABLE / PARETO / WAFFLE views.
  - The 2.0 paper runs each model-agent pair at least 5 times and uses a neutral scaffold to compare models ([paper](https://arxiv.org/html/2601.11868v1)).
  - Its harness is called **Harbor** (see [Open questions](#open-questions)).
- **HAL** ([site](https://hal.cs.princeton.edu); [reliability](https://hal.cs.princeton.edu/reliability)):
  - Accuracy vs cost Pareto plots.
  - A reliability dashboard with outcome consistency (2p̂−1)², calibration and robustness.
  - Traces published encrypted.
  - Its log analysis found agents searching for the benchmark answers ([paper](https://arxiv.org/abs/2510.11977)).
  - New models are paused.
- **AndroidWorld** ([repo](https://github.com/google-research/android_world)):
  - 116 parameterised tasks, success checked through adb state, and a step budget of about 2× human steps.
  - Seed variance is about ±3 points ([paper](https://arxiv.org/html/2405.14573v5)).
- **OSWorld-Verified** ([site](http://osworld-v1.xlang.ai/)): 369 tasks with execution-script checkers. For a "verified" listing the maintainers run the agent themselves.

#### Small-model tool benchmarks: ToolSandbox, When2Call, NESTFUL, MCPMark
- **ToolSandbox (Apple)** ([repo](https://github.com/apple/ToolSandbox)): stateful tools plus a user simulator. Score = similarity to a milestone DAG. Each milestone is the geometric mean of state and trace similarities, so any zero zeroes the milestone.
- **When2Call** ([paper](https://arxiv.org/html/2504.18851v1)): measures whether to call, ask, refuse or answer, over 3,652 questions. Small models hallucinate tool calls when none are given: Llama 3.2 1B 43%, Llama 3.2 3B 52%, Qwen 2.5 0.5B 20%.
- **NESTFUL** ([paper](https://arxiv.org/html/2409.03797v3)): nested call sequences, scored at partial and full sequence level.
- **MCPMark** ([paper](https://arxiv.org/abs/2509.24002)): 127 tasks with programmatic verifiers. The best model scored 52.56% pass@1 vs 33.86% pass^4, which shows how much repeated-trial reliability drops.

### D. General leaderboards

#### Artificial Analysis — API performance and index ([methodology v3.0.0, 2026-11-03](https://artificialanalysis.ai/methodology/performance-benchmarking))
- **D:**
  - **TTFT:** request to the first token, including the first reasoning token.
  - **Time to First Answer Token:** includes reasoning time.
  - **Output speed:** "output tokens, including reasoning tokens … over a request's full duration, … so it includes time to first token". This is a v3 change.
  - **Token counts:** native counts from the provider's API (previously tiktoken o200k_base).
  - **Failures:** endpoint-side failures count as speed 0.
- **C:**
  - Workloads: 1k, 10k and 100k input tokens plus multi-turn agentic, all under concurrent load, every 6 h.
  - "Prompt diversity matters … speculative decoding means that output speeds vary with the type of output."
  - Per-run median, then the mean of run medians over 5 days.
  - Temperature 0 for non-reasoning models and 0.6 for reasoning models.
- **E:** AA runs everything. Its integrity terms ban detecting benchmark traffic or serving a different quantisation, and it cross-checks with anonymous accounts.
- **UI:** Column groups Intelligence / Price / Speed / Latency / Features, "Expand columns", top-5 highlight panels, and model pages ([leaderboard](https://artificialanalysis.ai/leaderboards/models)).
- **X:** Single-region latency bias and incomplete quantisation disclosure (self-stated). Index versions are not comparable across releases.

#### LMArena (arena.ai), LiveBench 2.0, HF Open LLM Leaderboard, HELM, Scale, Epoch, llm-stats
- **LMArena** ([text board](https://arena.ai/leaderboard/text)):
  - Bootstrapped CIs and cost per task.
  - **Style control** ([blog](https://lmsys.org/blog/2024-08-28-style-control/)) adds answer-length difference and counts of markdown headers, bold and list items as regression features. Rankings moved substantially.
  - Criticism: private variant testing and selective disclosure ([Leaderboard Illusion](https://arxiv.org/abs/2504.20879)).
- **LiveBench 2.0** ([site](https://livebench.ai/)):
  - "Contamination-free long-horizon" tasks across 7 suites. Overall = a weighted mean (long agentic ×2, design ×1.5, coding ×1.5).
  - "Cost per successful task = (Σ cost ÷ Σ tasks ÷ score) × 100".
  - Top-5 shading per column, an open-weights toggle and a compare picker.
  - A quality-vs-cost chart where clicking a model greys out its "kill zone".
  - Its smallest model is Gemma 4 31B.
- **HF Open LLM Leaderboard:**
  - Retired on 2025-03-14 ([post](https://huggingface.co/posts/burtenshaw/596060753193040)).
  - It normalised scores by subtracting the random baseline and clipping at 0 ([doc](https://huggingface.co/docs/leaderboards/open_llm_leaderboard/normalization)).
  - The same model scored 0.637 vs 0.488 on MMLU under different harnesses ([blog](https://huggingface.co/blog/open-llm-leaderboard-mmlu)).
- **HELM** entered maintenance mode on 2026-06-01 and released every raw prompt and completion ([repo](https://github.com/stanford-crfm/helm)).
- **Scale Labs** ([board](https://labs.scale.com/leaderboard)): private datasets and a "first encounter only" rule.
- **Epoch** ([hub](https://epoch.ai/benchmarks)): 16 runs and ±1 SE; Epoch's own runs are separated from external results; CSV and Python client; CC BY.
- **llm-stats** ([site](https://llm-stats.com/methodology)): mixes self-reported and verified results, with visible internal inconsistencies.

### E. Energy

#### ML.ENERGY Leaderboard v3.0 — https://ml.energy/leaderboard/
- **M/D:** GPU-only energy on H100 and B200. Energy/Token (J), Energy/Response (J), Median ITL (ms), Perf/Watt. The paper's metric is steady-state J/token × output tokens per response ([paper](https://arxiv.org/html/2601.22076v2) *(fetch summary)*). Prefill and decode are not separated.
- **C:**
  - Batch size and GPU count are swept, with latency targets on inter-token latency.
  - Their blog found measurement windows under about 2 s noisy, and 5 s windows with 5 s cooldown stable ([blog](https://ml.energy/blog/energy/measurement/thermally-stable-profiling-for-accurate-gpu-energy-measurement/)).
  - The v1/v2 paper did no repeat runs.
- **UI:**
  - An energy-per-token vs inter-token-latency scatter with a P50 / P90 / P95 / P99 toggle.
  - A "Median ITL deadline" slider and a "Per token energy budget".
  - GPU checkboxes and "Energy-optimal points for each model".
  - Table text "12 models satisfy the given constraints", with Advanced columns and Compare.

#### HF AI Energy Score — https://huggingface.github.io/AIEnergyScore/
- GPU Wh per 1,000 queries on one H100 at batch 1, with prefill + decode summed.
- 1–5 stars are quintile bands *within size classes*, recalibrated each release.
- A shareable label image shows model, score, hardware and date ([repo](https://github.com/huggingface/AIEnergyScore)).

#### On-device energy practice (no leaderboard publishes it)
- **Apple Silicon:**
  - Zeus reads IOReport counters (CPU, GPU, ANE, DRAM in mJ, no sudo) ([docs](https://ml.energy/zeus/reference/device/soc/apple/)). These are Apple's *model-based estimates*, not sensor readings.
  - The `powermetrics` man page says values are "not strictly accurate" and "generally not comparable across machine models" ([man page, older text](https://www.unix.com/man_page/osx/1/powermetrics/)).
  - macmon notes that macOS 27 restricted some IOReport counters ([repo](https://github.com/vladkens/macmon)).
- **Android:**
  - Pixel 6+ on-device power rails through Perfetto ([doc](https://perfetto.dev/docs/data-sources/battery-counters)). They are measured downstream of the battery, so charging doesn't corrupt them.
  - A Monsoon meter with the battery bypassed at 5 kHz is the gold standard ([CORE paper](https://arxiv.org/html/2507.02135v1)).
  - The software fallback samples BatteryManager at 100 ms with idle subtracted, 30 runs and 200 s cooldown ([paper](https://arxiv.org/html/2603.26603)).
- **iPhone:** "iOS exposes no per-component power draw to third-party apps". Sustained decode fell 41.5% within about 3 iterations on an iPhone 16 Pro ([paper](https://arxiv.org/html/2603.23640)).
- **Prefill/decode split:**
  - TokenPowerBench tags each power sample with the active stage ([paper](https://arxiv.org/html/2512.03024)).
  - The CORE paper uses P × TTFT / N_prompt for prefill and P × TPOT for decode ([paper](https://arxiv.org/html/2507.02135v1)).
- **Pipette** says phones on a charger still drew from the battery during reps ([device conditions](https://pipette.liquid.ai/docs/methodology/device-conditions)). Its energy folder is a survey with no results *(fetch summary)*.

### F. Modalities and quality

- **Open ASR Leaderboard** ([repo](https://github.com/huggingface/open_asr_leaderboard), [paper](https://arxiv.org/html/2510.06961)):
  - RTFx = audio duration ÷ transcription time.
  - WER uses a Whisper-style normaliser, and raw predictions are stored so results can be rescored.
  - Tracks: short-form, long-form and multilingual.
  - Hardware drifted: the paper says A100, the README says H200.
- **AA speech-to-text** ([method](https://artificialanalysis.ai/methodology/speech-to-text)):
  - A WER index weighted 50 / 25 / 25 over three datasets, one held out.
  - Speed factor = audio duration ÷ response time, median over 7 days.
  - Streaming: time to first partial and time to final transcript, measured from end of speech.
- **AA speech-to-speech** ([page](https://artificialanalysis.ai/speech-to-speech)): Time to First Audio, and Big Bench Audio with 1,000 questions.
- **Open VLM Leaderboard** ([VLMEvalKit](https://github.com/open-compass/VLMEvalKit)): exact match first with an LLM extractor as fallback. Filters: size buckets, model type, and dimension checkboxes that choose the visible columns ([app.py](https://huggingface.co/spaces/opencompass/open_vlm_leaderboard/raw/main/app.py)).
- **MTEB** ([home](https://mteb-leaderboard.hf.space/), [English board](https://mteb-leaderboard.hf.space/benchmark/MTEB(eng%2C%20v2))):
  - Benchmark cards list the **top model per size group: < 500M, 500M–1B, 1B–5B, > 5B**.
  - Benchmark pages have tabs: Summary / Performance per model size / Over time / Per task / Task info, plus Download CSV.
  - Columns include Parameters, Openness, a **Zero-shot %** (with "⚠️ NA" when training data is undisclosed), Mean (Task) and Mean (TaskType). A **PARETO** badge appears inline in the table.
  - Every column header has a "?" tooltip.
- **Vectara hallucination** ([repo](https://github.com/vectara/hallucination-leaderboard)):
  - Hallucination rate, factual-consistency rate, **answer rate** and **average summary length (words)**.
  - Summaries are capped at 20% of the source length.
  - Only documents that every model summarised are scored.
  - The dataset is private.
  - The authors admit pure copying would score 100%.
- **Long context:**
  - RULER ([repo](https://github.com/NVIDIA/RULER)): effective length = the longest length scoring above 85.6%. Tables show claimed length, effective length and score per length.
  - NoLiMa ([repo](https://github.com/adobe-research/NoLiMa)): the threshold is 85% of the model's *own* short-context score, and cells below it are shaded.
  - HELMET found needle tests don't predict real tasks ([paper](https://arxiv.org/abs/2410.02694)).
- **Verbosity:**
  - AlpacaEval's length-controlled win rate predicts the result with the length difference set to 0. It raised correlation with Arena from 0.94 to 0.98 ([paper](https://arxiv.org/abs/2404.04475)).
  - EQ-Bench truncates outputs at 4,000 characters and has a slop score ([about](https://eqbench.com/about.html)).
- **Multilingual:**
  - SEA-HELM ([board](https://leaderboard.sea-lion.ai/)) has bootstrapped 95% CIs, a "MoE or dense" column, a quantisation tag, a size filter (< 10B) and a score-vs-size Pareto.
  - MMLU-ProX ([site](https://mmluprox.github.io/)) covers 29 languages and shows gaps up to 24.3%.
  - Global-MMLU ([paper](https://arxiv.org/abs/2412.03304)) splits culturally sensitive from culturally agnostic questions.
- **Documents and spreadsheets:**
  - OmniDocBench ([repo](https://github.com/opendatalab/OmniDocBench)) scores edit distance, TEDS and CDM.
  - SpreadsheetBench ([site](https://spreadsheetbench.github.io/)) labels Verified results separately and requires unchanged cells to stay unchanged.
  - **No email / message / calendar extraction leaderboard was found.**

---

## Synthesis

### 1. Missing from our list

Each item is something these sites measure or control for that the draft list lacks. Priority: **must** means the benchmark isn't credible without it, **should** means high value, and **nice** means later.

| # | Missing item | What the sites actually do | Why we need it | Priority |
|---|---|---|---|---|
| 1 | **Reliability across repeated trials (pass^k)** | τ-bench pass^k ([paper](https://arxiv.org/html/2406.12045)); MCPMark 52.56% pass@1 vs 33.86% pass^4 ([paper](https://arxiv.org/abs/2509.24002)); AA 5 repeats per question ([method](https://artificialanalysis.ai/methodology/mobile-device-benchmark-set)); HAL consistency (2p̂−1)² ([reliability](https://hal.cs.princeton.edu/reliability)) | An on-device agent that succeeds 1 time in 3 is not usable; pass@1 hides this. | must |
| 2 | **Deployment identity on every result**: quantisation, weight file and hash, runtime and version, flags (threads, GPU layers, flash attention, mmap, context size, KV cache type), chat template | Pipette: "Two results are from the same benchmark configuration only when the model, quantization, runtime, flags, and exact token counts all match" ([perf method](https://pipette.liquid.ai/docs/methodology/performance-methodology)); AgentPerf checks SHA-256 per file ([README](https://github.com/ArtificialAnalysis/aa-agentperf-local)); AA: "one model name covers many builds" | Without it, numbers can't be reproduced or compared. | must |
| 3 | **Device-state control**: thermal and load readiness gate, power state, cooling disclosure | Pipette's per-platform gate, recorded power fields and "lab-condition results" label ([device conditions](https://pipette.liquid.ai/docs/methodology/device-conditions)) | Phones reach their thermal trip point in seconds, so ungated runs measure heat, not the model. | must |
| 4 | **Sustained (throttled) performance**: minute 1 vs minute 10 | None of the leaderboards do it. Papers: iPhone 16 Pro decode −41.5% within ~3 iterations ([paper](https://arxiv.org/html/2603.23640)). Pipette admits long reps throttle | Real agent sessions are long; cool-device numbers overstate phones. | should |
| 5 | **Battery drain** (% or mWh per 1,000 tokens or per task) | No leaderboard. Phone papers integrate battery-gauge V × I ([paper](https://arxiv.org/html/2603.26603)). iPhone gauge reports in coarse steps | It is the user-facing cost on phones and laptops. | should |
| 6 | **Energy and time per *completed task***, not only per token | Cost per task: HAL ([site](https://hal.cs.princeton.edu)); LiveBench cost per successful task ([site](https://livebench.ai/)); AA cost/time per task; AgentPerf completion time | A verbose or retry-heavy model can win per token but lose per task. | must |
| 7 | **Time-budgeted score** (score when each answer is capped at N seconds on the device) | AA 1-minute budget flips the ranking, 63 → 18 for Nanbeige4.2-3B ([article](https://artificialanalysis.ai/articles/mobile-phone-intelligence-inference)) | Users won't wait minutes on a phone. | must |
| 8 | **Fit gate plus "did not fit / failed / timed out" as visible results** | AA "fits inside 8 GB … including the KV cache at 8K"; failures greyed. AA laptops "Does not fit in 64 GB". Pipette stores failed cells as errors | Under 16 GB, *not running* is the most important result for many models. | must |
| 9 | **Swap and memory-pressure exclusion** | Pipette: runs that swapped are ineligible, retried up to 5 times ([publication](https://pipette.liquid.ai/docs/methodology/result-selection)) | Memory at 32K/64K on 8–16 GB devices will swap; a swapped number is a different measurement. | must |
| 10 | **Context-budget overruns and output-token totals per eval; reasoning vs non-reasoning as separate variants** | AA "Context budget overruns" chart; Qwen3.5 9B used 74.5M tokens vs 5.2M ([article](https://artificialanalysis.ai/articles/mobile-phone-intelligence-inference)) | "Tokens per answer" alone doesn't show truncation failures or thinking cost. | must |
| 11 | **Abstention: irrelevance, "no tool fits", ask-for-missing-info, hallucinated-call rate** | BFCL Irrelevance (240 of AA's 640 tasks); When2Call (small models hallucinate tool calls 20–52% of the time) ([paper](https://arxiv.org/html/2504.18851v1)); AA-Omniscience non-hallucination half | On-device agents act on real data; a wrong call is worse than no call. | must |
| 12 | **Native function-calling mode, with runtime-template parsing and no "rescue"** | AA uses BFCL FC mode only, llama.cpp parsing, failed parses = failure ([method](https://artificialanalysis.ai/methodology/mobile-device-benchmark-set)); BFCL lists FC and Prompt rows separately | Template and parser bugs are a real on-device failure mode; rescuing hides them. | must |
| 13 | **Format sensitivity** (same task, varied tool-schema and prompt formats) | BFCL V4: 26 variants, Max Delta and SD columns ([blog](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html)) | Small models collapse under format changes. | should |
| 14 | **Quantisation ladder and the accuracy lost to quantisation** | Pipette "Accuracy hit from quantization" chart; sweeps Q4_0 / Q4_K_M / Q5_K_M (Q8_0 on laptops) *(fetch summary)*; AA plans to compare by memory footprint | Which quantisation to run is the user's real decision on a 16 GB device. | should |
| 15 | **Hardware matrix with spec metadata** (chip, RAM, memory bandwidth, OS, accelerator, power class) | AA system cards (bandwidth, memory type, MSRP); AA power tiers 2–20 W vs 15 W–2 kW; llama.cpp #4167 bandwidth column | Decode speed tracks memory bandwidth, and readers need to map results to their own device. | must |
| 16 | **Backend / accelerator as a dimension** (CPU / GPU / NPU / ANE) | Geekbench: same phone ~7× apart by backend ([browser](https://browser.geekbench.com/ai/v1)); Pipette Android = CPU only; AI Benchmark "Lib" column | Backend choice can swamp model differences. | should |
| 17 | **Quality at each context length (effective context)**, not only speed and memory | RULER / NoLiMa effective length ([NoLiMa](https://github.com/adobe-research/NoLiMa)); HELMET: needles don't predict real tasks | A model that runs at 64K but answers wrongly there doesn't support 64K. | should |
| 18 | **Faithfulness and answer rate for summaries** | Vectara: hallucination rate + answer rate + summary length, scored only on documents every model answered ([repo](https://github.com/vectara/hallucination-leaderboard)) | The user's summarising item needs a correctness metric, not just readability. | must |
| 19 | **Length/style-controlled quality** | LMArena style control ([blog](https://lmsys.org/blog/2024-08-28-style-control/)); AlpacaEval LC ([paper](https://arxiv.org/abs/2404.04475)); EQ-Bench truncation | Judges prefer longer, list-heavy answers; readability scores would reward verbosity. | should |
| 20 | **Confidence intervals / SD on every number** | Pipette mean ± SD; Epoch ±1 SE; Arena and SEA-HELM bootstraps; Terminal-Bench ± CI | Small models' scores sit close together; without intervals the ranks are noise. | must |
| 21 | **Contamination control** (held-out or refreshed items, canary string, encrypted traces, training-data disclosure) | LiveBench refresh; Scale private sets and "first encounter only"; HAL encrypted traces; MTEB Zero-shot % | Small models are often distilled on public benchmarks. | should |
| 22 | **Versioned methodology, changelog, raw data export** | AA version history; Pipette data changelog and one canonical eval version; Epoch CSV / CC BY; HELM raw prompts | Results must stay interpretable across releases. | must |
| 23 | **Verified vs self-submitted tiers** | τ² "Verified"; SpreadsheetBench Verified tab; Epoch internal vs external; Pipette planned community tag | Needed if the benchmark accepts community runs. | should |
| 24 | **Agent prefix-cache behaviour** (multi-turn reuse of the KV cache) | AA reports cache hit rate; AgentPerf sends the full conversation every turn; Pipette disables prefix caching for microbenchmarks | Agent latency depends on cache reuse across turns; microbenchmarks miss it. | should |
| 25 | **Tool execution time accounting** | AgentPerf: skipped / fixed delay / live tools ([README](https://github.com/ArtificialAnalysis/aa-agentperf-local)); AA excludes tool time from speed | Separates model speed from the speed of the tools it calls. | nice |
| 26 | **Device price or class context** (performance per dollar, per watt-class) | AA editable prices → completion time vs price Pareto | Helps buyers; not core. | nice |
| 27 | **Integrity terms for submitters** | AA bans traffic detection and serving a different quantisation | Only matters once vendors submit. | nice |

### 2. Methodology rules to copy

**Speed and latency**

1. **Synthesise prompts to exact token counts using each model's own tokenizer, and fix output length by suppressing end-of-sequence.**
   - Source: Pipette ([perf](https://pipette.liquid.ai/docs/methodology/performance-methodology)); EOS suppression also in mlx_lm ([source](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/benchmark.py)) and AgentPerf `ignore_eos`.
   - Name results with IDs like `decode_throughput_<P>_100`.
   - For comparability, reuse Pipette's shapes: prefill at P, decode 100 tokens at P, E2E 1,024 in / 256 out.
   - Extend P to 512, 1k, 2k, 5k, 10k, 32k and 64k. 5k and 10k are not Pipette shapes; consider 4,096 / 8,192 to overlap.
2. **Define four timings separately and print the definitions on the page:**
   - (a) prefill time = TTFT as Pipette defines it, prefill only;
   - (b) TTFT from submit to first visible token, including tokenisation;
   - (c) time to first *answer* token for reasoning models (AA);
   - (d) decode tok/s excluding the first token (MLPerf Client: "excluding the first one").
   - Sources: [Pipette](https://pipette.liquid.ai/docs/methodology/performance-methodology), [AA](https://artificialanalysis.ai/methodology/performance-benchmarking), [MLPerf Client](https://mlcommons.org/benchmarks/client/).
3. **Measure decode at depth.** Pre-fill the KV cache to depth d, then time decode at that depth (llama-bench `-d` ([README](https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md))). This produces our "decode speed at 32K/64K" without generating 64K tokens.
4. **Discard one warm-up, then take 5 measured reps and report mean ± sample SD (n−1), with no trimming.**
   - Source: Pipette; also llama-bench `-r 5` and mlx_lm 5 trials. MLPerf Client uses 1 + 3.
   - Show SD next to the mean, because "a large value means the run was noisy or thermally unstable".
5. **Use a readiness gate before every measured rep with fixed per-platform thresholds. On timeout, store an error, never a throttled number.**
   - Reuse Pipette's published thresholds ([device conditions](https://pipette.liquid.ai/docs/methodology/device-conditions)).
   - Record `device_battery_level`, `device_power_state` and `device_power_save_mode`, and state the cooling rig.
6. **Measure peak memory in one run in a fresh process, after a discarded warm-up process.**
   - Use the platform's lifetime high-water counter. Pipette uses `phys_footprint` on macOS and iOS and `VmHWM` + swap on Android *(fetch summary of the pipette-clients docs)*.
   - Never add host and GPU memory together on unified-memory devices.
   - Exclude runs that swapped and retry up to 5 times.
   - Source: [Pipette peak memory](https://pipette.liquid.ai/docs/benchmarks/peak-memory-usage-latest), [publication](https://pipette.liquid.ai/docs/methodology/result-selection).
7. **Turn off prefix/KV caching for microbenchmarks, but run a separate agent-replay workload with caching on.** Replay the full conversation per turn and report the cache hit rate. Sources: Pipette; AgentPerf; AA cache hit rate.
8. **Load time** (our addition; every site excludes it, see the [Google card](https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm) and Pipette):
   - Report cold load (OS file cache flushed, measured from file open to ready) separately from warm load.
   - Never fold either into TTFT.

**Identity and comparability**

9. **Make the configuration tuple the primary key of every result:** model, weight source and hash, quantisation, runtime and version, flags, device, OS, and token shape. Compare only rows whose keys match. Sources: Pipette; AgentPerf SHA-256.
10. **Use a fit gate tied to device class.** AA's rule is "fits in 8 GB including KV at 8K". Define ours per RAM tier, e.g. 8 / 12 / 16 GB devices with KV at a stated context. Draw "qualifies but failed" as a result ([AA](https://artificialanalysis.ai/methodology/mobile-device-benchmark-set)).
11. **Score the quantised build that actually runs on the device, not the full-precision weights.** Join quality to speed only by model + quantisation, and say so (Pipette; AA).

**Quality and agent scoring**

12. **Use native function-calling only, parse through the runtime's chat template, and count a failed parse as a failure** ([AA BFCL rules](https://artificialanalysis.ai/methodology/mobile-device-benchmark-set)).
13. **Check outcomes against state, not text.** Compare the backend state after each turn, and pass a task only if every turn passes. Cap steps: BFCL 20, τ 30, AndroidWorld about 2× human ([BFCL V3](https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html), [AndroidWorld](https://arxiv.org/html/2405.14573v5)).
14. **Run ≥ 5 trials per task and report pass@1 and pass^k** ([τ-bench](https://arxiv.org/html/2406.12045); AA 5 repeats). Report infrastructure failures as `error_trials` and keep them out of the denominator ([τ² guide](https://github.com/sierra-research/tau2-bench/blob/main/docs/leaderboard-submission.md)).
15. **Give partial credit for multi-step plans with milestones**, scoring each milestone as a geometric mean so any zeroed component zeroes it ([ToolSandbox](https://github.com/apple/ToolSandbox)).
16. **Cap context and count overruns as wrong.** List reasoning and non-reasoning modes as separate variants, publish a time-budget variant, and publish an output-token total per eval ([AA](https://artificialanalysis.ai/methodology/mobile-device-benchmark-set)).
17. **Combine with a simple equal-weight average and publish the weights.** Subtract the random baseline for multiple-choice evals so near-chance small models score about 0 ([HF normalisation](https://huggingface.co/docs/leaderboards/open_llm_leaderboard/normalization)). Avoid opaque roll-ups such as Geekbench's accuracy multiplier ([Signal65](https://signal65.com/research/ai/new-geekbench-ai-1-0-benchmark-analysis-and-early-results/)).
18. **For summaries,** report faithfulness, answer rate and average length; cap length relative to the source; and score only items every model answered ([Vectara](https://github.com/vectara/hallucination-leaderboard)).
19. **For long context,** report effective length as the longest context keeping ≥ 85% of the model's own short-context score ([NoLiMa](https://github.com/adobe-research/NoLiMa)).
20. **For ASR,** report WER with a published normaliser, keep raw transcripts for rescoring, and report RTFx = audio seconds ÷ processing seconds ([Open ASR](https://github.com/huggingface/open_asr_leaderboard)). For streaming, report time to first partial from end of speech ([AA STT](https://artificialanalysis.ai/methodology/speech-to-text)).

**Energy**

21. **Report both gross and idle-subtracted net energy, and show the measurement scope and instrument on every row:**
    - IOReport/Zeus estimate (Mac), Pixel power rails, Monsoon meter, battery gauge, or wall meter.
    - Use windows of at least about 5 s with cooldown between them.
    - Take power and performance from the same run.
    - Sources: [Zeus Apple](https://ml.energy/zeus/reference/device/soc/apple/), [ML.ENERGY windows](https://ml.energy/blog/energy/measurement/thermally-stable-profiling-for-accurate-gpu-energy-measurement/), [MLPerf power rules](https://github.com/mlcommons/inference_policies/blob/master/power_measurement.adoc), [Perfetto rails](https://perfetto.dev/docs/data-sources/battery-counters).
22. **Split prefill and decode energy by stage timestamps,** or with a prefill-only probe at the same prompt length ([TokenPowerBench](https://arxiv.org/html/2512.03024), [CORE](https://arxiv.org/html/2507.02135v1)). Label iPhone energy as an estimate, since no per-component API exists ([paper](https://arxiv.org/html/2603.23640)).

**Process and governance**

23. **Version the methodology and the eval set, keep a dated changelog, and never mix eval versions** (AA version history; Pipette "only one canonical version").
24. **Publish raw rows, per-run logs and CSV/JSON under an open licence**, and link every displayed number to its raw rows (Pipette Submissions; Epoch CSV CC BY; HELM raw prompts).
25. **Open-source the harness.** Let users run it and optionally submit, collect hardware facts "without identifiers", and tag community rows separately from lab rows ([AgentPerf](https://github.com/ArtificialAnalysis/aa-agentperf-local); Pipette planned tagging).
26. **Use realistic text prompts for speed runs, not only random token IDs.** "Speculative decoding means that output speeds vary with the type of output" ([AA](https://artificialanalysis.ai/methodology/performance-benchmarking)). This matters for multi-token-prediction models such as LiteRT's MTP.

### 3. UI patterns to copy

These are for the website design. Each pattern links to a live example I opened. They are ordered by how much they help when comparing many models across many metrics and devices.

**Page scaffolding**

1. **One global device / scope selector that every chart obeys.** Every chart caption restates the active filters in a sentence, e.g. "Filtered to q4_k_m on llama.cpp:macos-arm64 … at 1,024 input tokens". Example: [Pipette leaderboard](https://pipette.liquid.ai/leaderboard).
2. **Hardware tiers named by power envelope as top-level navigation.** AA uses "2–20 W Mobile phones", "15 W–2 kW Laptops & workstations". Use a device class with its RAM, e.g. "Phones 8–12 GB" and "Laptops 16 GB". Example: [AA mobile phones](https://artificialanalysis.ai/hardware-inference-stack/mobile-phones).
3. **Three views of the same data:** Leaderboard (charts), Results (a configuration-level table), and Submissions (raw rows). Every number in the first two links to its raw rows through a query-string URL, which also makes views shareable. Example: [Pipette Submissions links](https://pipette.liquid.ai/submissions).
4. **A "How to read this chart" expander under every chart, plus a "?" tooltip on every column header.** Examples: [Pipette](https://pipette.liquid.ai/leaderboard), [MTEB English](https://mteb-leaderboard.hf.space/benchmark/MTEB(eng%2C%20v2)).
5. **A methodology page with anchored sections** (overview, workloads, definitions, known limitations, version history) and a dated changelog. Example: [AA methodology](https://artificialanalysis.ai/methodology/performance-benchmarking); [Pipette docs](https://pipette.liquid.ai/docs/introduction).

**Headline charts**

6. **A quality-vs-time scatter with a Pareto halo, a shaded "most attractive quadrant" and threshold inputs.** Thresholds: min decode, min prefill, max TTFT, max E2E, max peak RAM, min score. Add a preset such as "Responsive & Accurate". Use agent-task success on the y-axis and time or energy per task on the x-axis. Examples: [Pipette](https://pipette.liquid.ai/leaderboard), [AA mobile](https://artificialanalysis.ai/hardware-inference-stack/mobile-phones).
7. **Dashed lines joining a model's quantisation variants on the scatter** (colour = family, shade = precision). This shows the quantisation trade-off without a separate chart. Example: [Pipette](https://pipette.liquid.ai/leaderboard).
8. **A context-scaling line chart:** one line per model + quantisation, with the x-axis on a log scale from 512 to 64K. Use separate panels for prefill tok/s, decode tok/s, TTFT and peak RAM. Our addition: mark the point where a model fails or swaps with an ✕, and draw a horizontal line at the device's RAM. Example: Pipette's "How performance scales with context length" on the [leaderboard](https://pipette.liquid.ai/leaderboard).
9. **The "memory cost of throughput" dumbbell:** prefill and decode dots for each model joined vertically, plotted against peak RAM on a log scale. Example: [Pipette](https://pipette.liquid.ai/leaderboard).
10. **Score toggles for alternative budgets** (16K / 64K / 1-minute) on the same chart. For us: score at native context / at 8K cap / within a 30 s or 60 s budget. Add a "context budget overruns" bar and a score-vs-output-tokens tab. Example: [AA mobile](https://artificialanalysis.ai/hardware-inference-stack/mobile-phones).
11. **An energy-vs-latency scatter with a deadline slider and an energy budget.** The table filters to "N models satisfy the given constraints", and a P50/P90/P95/P99 toggle sits on the latency axis. Example: [ML.ENERGY](https://ml.energy/leaderboard/).
12. **"Accuracy hit from quantization":** bars per quantisation level with lines joining a model's levels, so a steep drop is visible at a glance. Example: [Pipette](https://pipette.liquid.ai/leaderboard).

**Tables**

13. **Top model per size tier** as cards on the home page. MTEB uses < 500M / 500M–1B / 1B–5B / > 5B; ours could be < 1B / 1–4B / 4–8B / 8–12B, plus an MoE flag. Example: [MTEB home](https://mteb-leaderboard.hf.space/).
14. **Column groups with "Expand columns"** (AA: Intelligence / Price / Speed / Latency / Features). For us: Agent / Tools / Speed / Memory / Energy / Modalities. Missing values show as "—", not 0. Examples: [AA leaderboard](https://artificialanalysis.ai/leaderboards/models); BFCL's two-row grouped header ([BFCL](https://gorilla.cs.berkeley.edu/leaderboard.html)).
15. **Normalised rank lanes:** one thin bar per metric where "the best result fills the lane" and "longer is always better", even for latency and memory. This lets many metrics share one row. Example: Pipette's "Per-model performance across the board" ([leaderboard](https://pipette.liquid.ai/leaderboard)).
16. **Inline badges in the table:** PARETO, MoE/dense, quantisation, reasoning mode, and a contamination or "⚠️ NA" disclosure column (MTEB Zero-shot %). Examples: [MTEB English](https://mteb-leaderboard.hf.space/benchmark/MTEB(eng%2C%20v2)); SEA-HELM's MoE and quantisation columns ([SEA-HELM](https://leaderboard.sea-lion.ai/)).
17. **"Did not fit / failed / over budget" drawn as a result:** patterned cells in a model × device heat table, e.g. "Does not fit in 64 GB". Example: [AA laptops](https://artificialanalysis.ai/hardware-inference-stack/laptops-workstations).
18. **Uncertainty on every number:** "± SD · N reps" under aggregates ([Anubis](https://uncsoft.github.io/anubis-oss/analysis.html)). Rank by a conservative bound, such as PocketPal's rating − 2 × RD, so noisy entries don't top the table ([PocketPal](https://pocketpal.dev/leaderboard)). Show ± CI columns ([Terminal-Bench](https://www.tbench.ai/leaderboard)).
19. **Top-5 shading per column and a click-to-grey "kill zone"** on the cost chart: clicking a model greys every model that scores lower and costs more. Example: [LiveBench](https://livebench.ai/).
20. **A score-by-context-length table:** claimed vs effective length, with below-threshold cells shaded. Use it for quality at 512 → 64K. Examples: [RULER](https://github.com/NVIDIA/RULER), [NoLiMa](https://github.com/adobe-research/NoLiMa).
21. **A Results explorer** with group-by (model / device / platform), a column chooser, Spread, submission count and CSV export. Example: [PocketPal results](https://pocketpal.dev/leaderboard/results).

**Detail pages**

22. **Device spec cards:** chip, RAM, memory bandwidth, memory type, launch date and price, with editable price inputs that drive a perf-per-dollar chart. Example: [AA laptops](https://artificialanalysis.ai/hardware-inference-stack/laptops-workstations).
23. **Side-by-side compare of 2–3 models across suites.** LiveBench and BFCL use radar charts. Small multiples of bars may read better, which is our recommendation, not a site's. Examples: [LiveBench](https://livebench.ai/), [BFCL](https://gorilla.cs.berkeley.edu/leaderboard.html).
24. **Run-to-run regression colouring** (green/red above 5% vs the previous version) for model or runtime updates. Example: [ExecuTorch dashboard README](https://github.com/pytorch/executorch/blob/main/extension/benchmark/README.md).
25. **A shareable energy label** (model, score, hardware, date, stars within size class). Example: [AI Energy Score](https://huggingface.github.io/AIEnergyScore/).

**Patterns to avoid** (seen failing):
- A single opaque score: AI Benchmark and Geekbench roll-ups.
- Crowd uploads that aren't de-duplicated: the same laptop about 15 times in one day on [Geekbench](https://browser.geekbench.com/ai/v1).
- A leaderboard with no visible freshness date: BFCL is 7 months stale, and its date is only in small text.
- Weights stated differently on the page and in the blog: BFCL.

---

## Could not verify

- **Pipette:**
  - The client-repo pages for iOS and Android memory counters, the energy survey contents, and community-submission status. These come only from the research agent's fetch summary of `Liquid4All/pipette-clients` docs; the live `pipette.liquid.ai` site rate-limited the fetcher, so I read only the live docs pages listed above.
  - The eval host conflicts: the live intro says AMD MI325X, a secondary article says H100. I used the live page.
  - Whether it supports any context above 8,192: no evidence found.
- **AA:** the API "concurrent load" level; Intelligence Index CIs; the AA-AgentPerf methodology page (404).
- **BFCL:** overall weighting, since the page ("unweighted") and the V4 blog (40/30/10/10/10) conflict; temperature; latency hardware for V4.
- **τ-bench 2.0:** default trial and step counts; leaderboard update date.
- **Terminal-Bench 4.1:** task count, timeouts and submission process. **OSWorld 2.0:** page not fetched.
- **ACEBench, AndroidLab, Mobile-Bench:** not opened.
- **MLPerf Mobile:** LLM metric definitions and lengths. **MLPerf Client:** public results page and overall formula.
- **Qualcomm AI Hub:** per-device tables (TTFT range, memory range, compute-unit split).
- **Nexa AI:** current status (its blog redirects to Qualcomm AI Hub, and its SDK references Qualcomm GenieX). No Nexa-owned benchmark page found.
- **PocketPal:** submission verification; chip and RAM fields.
- **Energy tools:**
  - The current Apple Silicon `powermetrics` man page (only older text opened) and its minimum useful sampling interval.
  - Apple Instruments Power Profiler and MetricKit pages.
  - ODPM sampling rate.
  - The AI Energy Score leaderboard columns.
  - Whether MELT's iPhone energy used a hardware meter.
- **Modalities:** the Open VLM Avg Score formula; the MTEB submission process; Fiction.LiveBench; EQ-Bench Longform; the EQ-Bench judge model (the site and README disagree); the Arena multilingual category and style-control default on the current site.
- **Missing benchmark:** no 2026 benchmark or leaderboard found for extracting data from **emails, messages or calendars**. This looks like an open niche, but the search was not exhaustive.
- **Dates:** Epoch and PocketPal show "updated Nov 19, 2026", one day after today, probably a timezone effect.
- **Not researched:** Microsoft Foundry Local, Ollama and LM Studio performance notes; Apple Foundation Models benchmarks beyond Pipette's note that its runtime "cannot report prefill throughput or peak memory".

## Open questions

1. **Build on Pipette or alongside it?** Pipette's harness is public and AA has validated it. We could:
   - (a) reuse its token shapes and readiness thresholds so our speed numbers line up with theirs;
   - (b) run Pipette's own client for the speed and memory columns and spend our effort on agent tasks, 64K context, energy, load time and sustained runs; or
   - (c) keep Harbor's own perf script.

   Harbor's script currently uses 256 / 1,024 / 4,096-token prompts, 5 reps, 256 output tokens, and polled process RSS for memory. Pipette uses `phys_footprint` high-water on macOS. These would need checking against each other.
2. **Lab-only or community submissions?** Pipette and AA publish lab results only. PocketPal and LocalScore accept crowd runs without verification. A two-tier design is possible: lab rows plus tagged community rows.
3. **Which devices form the lab matrix, and how is "< 16 GB" enforced?** If the reference Mac has more than 16 GB, we need an enforced memory budget per device class, such as a fit gate plus a swap check. Using a smaller physical device is the alternative.
4. **Where does 64K apply?** AA says a 64K window "does not fit in mobile phone memory". Should 32K/64K rows be laptop-only, with phones capped at 8K or 16K?
5. **Energy hardware and labelling:** buy a wall meter for Macs and a Monsoon-class meter for one Android phone, or accept IOReport and battery-gauge estimates labelled as such? iPhone energy can only be an estimate.
6. **Naming:** Terminal-Bench's agent harness is called "Harbor" ([paper](https://arxiv.org/html/2601.11868v1); `harbor-framework/terminal-bench`). Is that a problem for the benchmark's public name or for describing it as built on Harbor?
7. **Thinking modes:** should reasoning and non-reasoning modes be separate rows, as AA does, and which should be the headline?
