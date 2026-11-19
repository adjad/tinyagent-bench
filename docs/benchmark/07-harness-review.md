> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 07 · Toolbench harness review: comparing new model types fairly

Reviewed tree: Harbor `stage-0.1.7` at `01f86514` (branch `claude/bench-harness-fairness`).
Scope: `scripts/run_toolbench.py`, `scripts/toolbench/*`, plus the service code a Toolbench turn runs through, plus `scripts/eval_planner.py`, `scripts/bench_ondevice_perf.py` and `scripts/run_routing_stress_suite.py`.
Method: static reading only. No model, engine, build or Harbor process was run. Line numbers refer to `01f86514`.

## Summary

Toolbench is a careful harness for one question: "how does a model behave inside Harbor's real model-led Ask turn on oMLX?" It is not yet a fair harness for comparing different kinds of models. Three problems decide most of the outcome:

1. **Only oMLX models can run.** Any other engine is refused by Harbor's transport, and every refused trial is silently excluded rather than failed.
2. **Context window, sampling and thinking are not under the harness's control.** Each depends on per-engine and per-model state that the harness neither sets nor verifies.
3. **The report cannot tell a real difference from noise.** It has no intervals and no paired test.

Below these, a set of Gemma-shaped assumptions in the scorer, the cases and the host each move a comparison by a few points. Most can be neutralised bench-side. The ones that live in the app itself are listed as recommendations, because the app's behaviour must not change.

Findings are ranked by how much each distorts a comparison. "Fixed" means implemented on `claude/bench-harness-fairness`; see that branch's commits.

---

## Findings, ranked

### 1. Critical: no engine other than oMLX can be the model under test (fixed)

- `runner.bench_client_class` subclasses `OMLXClient` (`scripts/toolbench/runner.py:383-387`). The endpoint is pinned to the managed `local` endpoint with the `local_omlx` credential (`runner.py:102-112`). That is required, because production runs a turn model-led only when the agent target is the managed `local` endpoint and the client is managed (`service/main.py:2212-2228`, `2249-2274`). `prepare_turn` raises otherwise (`runner.py:1070-1076`).
- A managed client's transport is `CredentialTransport(managed=True)`, whose `CheckedBackend` verifies that the listener is a signed oMLX `omlx-server` child of `app.omlx` (`service/inference/attributed_transport.py:1244-1247`, `462-511`, `564-580`). A llama.cpp, Ollama, LM Studio or `mlx_lm.server` listener is refused with "Harbor couldn't verify the local AI engine (oMLX)" (`attributed_transport.py:44`).
- That message matches the scorer's `ENGINE_ERROR` (`scripts/toolbench/scorer.py:74-77`). Every trial is therefore **excluded** (`scorer.py:256-261`), and the model shows up with zero scored trials instead of failing loudly.
- Residency uses oMLX-only calls: `loaded_models`/`status` (`/v1/models/status`), `load` and `unload` (`service/inference/omlx_client.py:983-1012`). These are used by `make_resident` (`runner.py:421-430`), `_recover` (`run_toolbench.py:255-281`), `--exclusive` (`run_toolbench.py:415-423`) and `engine_settings` (`runner.py:837-875`, admin API and `~/.omlx/model_settings.json`).
- Managed requests also carry oMLX's GPU power-cap field (`omlx_client.py:479-490`, `_power_fields`), which another engine does not understand.
- The network guard allows exactly one loopback port (`runner.py:115-123`). That part is right and should stay.

**Fix (done).** Toolbench gets an engine abstraction, `--engine omlx|openai --port N`.
- `openai` is any OpenAI-compatible server on `127.0.0.1:N`. The turn still goes through the managed `local` endpoint, so production's model-led path is unchanged.
- The bench client replaces only the transport with a plain loopback HTTP client. It never loads, unloads or evicts anything; residency means "the server lists the model".
- The power-cap field is not sent to a non-oMLX engine.
- The network guard allows exactly the configured port. Harbor's own ports (8765, 8791) are refused as engine ports.
- Engine identity and version are read best-effort (`/props` for llama.cpp, `/api/version` and `/api/show` for Ollama, `/api/v0/models` for LM Studio, and the `/v1/models` row) and recorded.
- Nothing under `service/` changes.

### 2. High: the context window is neither the model's nor verified

- The window comes from `config.model_context_window(model)`, read from oMLX's `model_settings.json`, with a default of **8,000** for any model oMLX does not list (`service/config/__init__.py:786-803`). Every budget derives from it:
  - the history budget is `max(1500, window - 11500)`, so 1,500 at 8k (`service/main.py:892-898`);
  - the step fit, which drops schemas or raises "Target context is too small for the requested tools" (`omlx_client.py:1681-1727`);
  - the output budget, which is the window minus the prompt, up to the loop's `max_tokens=3000` (`service/agent/loop.py:1859`, `4518-4531`).
- With `--profile-as` (the default in every multi-model run, `run_toolbench.py:363-368`), the candidate is given the **profile model's window** through the alias (`runner.py:761`, `785-793`). A model whose engine holds 4k is then sent 16k-sized steps. oMLX answers 400. Ollama silently drops the start of the prompt, which is where Harbor's instructions and tool schemas live (see `service/inference/qualify.py:6-15`).
- A context error raises `EndpointConfigurationError`. That is scored as the model's `error`, a fail (`scorer.py:277-281`), with no hint that the cause was the window.
- No window is ever measured. `qualify.measure_context` (`service/inference/qualify.py:168-211`) exists for exactly this and is unused by the bench.

**Fix (done).**
- A pre-run probe measures the window the engine actually holds from reported `usage.prompt_tokens`. It reuses qualify's filler, verification and tolerance rules.
- A window policy, `--context-window` (default 16,384, Harbor's recommended size), gives every model `min(policy, verified)`.
- A model whose verified window is below Harbor's 8,192 tool minimum is stopped before scoring, unless `--allow-unqualified` is passed.
- A context-overflow error gets its own label, `context_overflow`. It is still a fail, because the model cannot serve Harbor's prompt on-device, but it is reported apart from model errors.

### 3. High: sampling is decided per engine and per model, and is not recorded as a policy

- No temperature, top-p or top-k is sent (`runner.py:51-52`, `398-416`; `omlx_client.py:1736-1745`). Each model samples with its oMLX profile.
- On another engine the server's own defaults apply, and they differ. For example, llama.cpp applies top_k 40 and min_p 0.05 unless told otherwise. Two models are then compared under different decoders.
- The parity check compares a candidate only with the profile model and only on oMLX keys (`runner.py:878-891`, `run_toolbench.py:321-347`). With two different families, the check aborts on what is normal (different tuned temperatures), or the user overrides it with `--allow-engine-mismatch`.
- **One seed per trial is sent on every request of the trial** (`runner.py:398-409`, `run_toolbench.py:444`). The loop's empty-response retry re-sends the same messages, so with a seeded engine it reproduces the same output (see the loop's own note on this, `loop.py:4555-4568`). A model that sometimes answers empty therefore loses its retry in the bench, though not in production, where no seed is sent.
- `max_tokens` is the loop's, which is fine and engine-independent. But no per-model cap exists, and the window that sizes it is finding 2.

**Fix (done).** An explicit, recorded sampling policy (`scripts/toolbench/policy.py`, `--sampling`):

| Policy | What is sent | Default for |
|---|---|---|
| `engine-default` | Nothing; the engine or profile decides (the old behaviour) | Single-model oMLX runs (production fidelity) |
| `fixed` | `temperature` 0.6, `top_p` 0.95, `top_k` 0, `min_p` 0, `repeat_penalty` 1.0, sent identically to every model and engine | Every multi-model or non-oMLX run |
| `file:<path>` | Each model's published recommended settings, from a JSON map | Explicit opt-in |

- Seeds are now per request: `trial seed × 1000 + request index`. A retry therefore re-samples, as in production.
- `--max-output-tokens` is an optional cap, off by default.
- The full policy, and a fingerprint of it, is written to `meta.json`. Under `fixed`, the engine parity check covers only what the policy does not set (window, thinking, MTP).

### 4. High: the host repairs Gemma's tool-call text and the scorer only recognises Gemma's markup (partly fixed; app part is a recommendation)

- Production strips and nudges only Gemma's `<|tool_call>` text calls (`service/model_only.py:321-335`; `loop.py:4756-4791`, `4821`). The scorer judges the repaired `final`.
  - A Gemma leak is mostly cleaned before scoring.
  - A Qwen `<tool_call>`, Llama `<|python_tag|>`, Mistral `[TOOL_CALLS]`, LFM `<|tool_call_start|>` or Granite `<|tool_call|>` leak is not.
- The scorer's own patterns are Gemma- and Hermes-shaped: `_TEXT_CALL` (`scorer.py:143`), `_MARKUP` (`scorer.py:155`) and `_THINK_MARKUP` (`scorer.py:80`). Other families' leaks therefore show up as `ungrounded_answer` or not at all.

**Fix (bench side, done).**
- One family-neutral markup table covers the text-call, markup-leak and think-leak labels.
- A new soft label, `raw_markup`, and a scorecard row, "raw format clean", are computed from each step's **raw** emission, before the host's repair. The host's Gemma-only help becomes visible without changing the as-shipped pass rate.

**Recommendation (app).** Generalise `text_tool_call`/`strip_text_tool_calls` and the nudge to every family's markup. Until then, read "raw format clean" next to "pass".

### 5. High: thinking is forced on, unbudgeted and unverified across engines

- Under `model_only`, every step states `enable_thinking: true` for every model (`loop.py:913-916`). Narration thinking is on globally (`service/config/models.yaml:204`).
  - A long-reasoning model (Qwen3, DeepSeek-R1 distills) spends the whole output budget or the 180-second turn limit thinking. It then scores `timeout` or `empty_response`.
  - A family whose template uses another switch never thinks. Granite uses `thinking`; Phi has none.
- `--thinking off` relies on `chat_template_kwargs.enable_thinking=false` (`runner.py:407-416`). oMLX and llama.cpp with `--jinja` honour it. Ollama's OpenAI endpoint ignores it. Nobody checks.
- `thinking_required_after_tool` lists only Gemma ids (`models.yaml:140-143`). An unlisted model that writes nothing after a tool result with thinking off is scored as empty under `narration-off`.

**Fix (done).**
- The probe measures four things:
  - whether the model thinks by default;
  - whether thinking-off is honoured (no reasoning field and no think markup);
  - whether the answer after a tool result is empty with thinking off but not on, which is the `thinking_required_after_tool` symptom;
  - where reasoning arrives (`reasoning_content`, `reasoning`, or inline).
- A run asking for `--thinking off` on a model that does not honour it is stopped for that variant.
- Thinking characters are already reported per model. The report now adds the time-limit view (finding 6) so long thinkers are not judged only by timeouts.

### 6. Medium-high: the 180-second turn limit mixes speed into correctness (fixed)

- `--timeout` defaults to 180 seconds (`run_toolbench.py:486`). A timeout is a blocking fail (`scorer.py:277-278`). The value is **not recorded in `meta.json`** (`run_toolbench.py:400-409`).
- On a 16 GB laptop a 12B model or a thinker can be correct but slow. Production has no turn limit; the app waits.
- The step ceiling (8, production's) and the retry ladder are the same for every model, which is fair.

**Fix (done).**
- The default hard limit is now 600 seconds, a safety bound that still stops a hung engine.
- `--latency-budget` (default 180 s) is a separate reported metric, "pass ≤ budget": passed and finished within the budget.
- Correctness is "pass" and responsiveness is "pass ≤ budget". Both are in `meta.json` with the timeout and step ceiling.

### 7. Medium: no statistics, so differences of a few points are uninterpretable (fixed)

`report.render` prints raw pass rates and a count of cases passed on every trial (`scripts/toolbench/report.py:183-211`). `compare` prints mean per-case rates (`report.py:273-293`). There are no intervals and no significance test. With 383 cases × 3 trials, a 3-point gap is within noise for most pairs.

**Fix (done).**
- **95% interval on the pass rate.** A case-clustered bootstrap: cases are resampled, never trials, because trials of one case are correlated. It is deterministic (fixed RNG seed).
- **Every-trial pass share.** The share of cases passed on every trial, with its interval. It is flagged when the number of trials differs between models.
- **Paired test against the first model in a run.** Over the cases both models scored, a sign-flip permutation test on per-case pass-rate differences (exact when small, otherwise 20,000 deterministic resamples). It reports better/worse/tied case counts, the mean difference with a bootstrap interval, and the p-value.
- **`--compare` runs the same paired test across runs.** Before any of this, it **refuses** two runs made under different policies (finding 9). Pass `--allow-policy-mismatch` to override.

### 8. Medium: run metadata is too thin to reproduce or audit a result (fixed)

- `meta.json` records git, models, trials, cases, seeds, world, thinking and engine settings (`run_toolbench.py:400-409`).
- It does not record:
  - the model file, quantisation, parameter counts, active parameters for mixture-of-experts models, architecture;
  - the runtime and its version, the timeout, the sampling actually sent;
  - the hardware, OS, power source, Low Power Mode and thermal state;
  - Python and package versions;
  - whether the tree was dirty.

**Fix (done).** `meta.json` now has `host`, `engine` and `model_info` blocks plus `policy` and `probe`:
- `host`: chip, RAM, cores, macOS build, AC or battery and charge, Low Power Mode, thermal warning level, Python and package versions, git SHA and whether the tree is dirty.
- `engine`: kind, port, runtime and version where it reports them.
- `model_info`, per model, from three sources:
  - what the engine reports (llama.cpp model path and `n_ctx`; Ollama `parameter_size`, `quantization_level`, family; LM Studio `quantization`, `arch`, `max_context_length`);
  - for a local model directory given with `--model-path`, `config.json`: architecture, layers, hidden size, experts, experts per token, quantisation bits and group size, plus the total weight bytes;
  - user-declared values from `--model-info` (a JSON file: parameters, active parameters, licence, source URL).

### 9. Medium: `--compare` compares runs made under different conditions (fixed)

- `--compare` only warns when one run predates per-case session clearing (`run_toolbench.py:232-247`).
- Runs that differ in any of these silently mix: timeout, sampling, thinking, world seed, case set, engine, treatment or window.

**Fix (done).**
- Each run stores a policy fingerprint. `--compare` **refuses** a hard mismatch (sampling, thinking variants, world seed or date, step ceiling, timeout, window policy, treatment, or the scorer's case-session rule). Pass `--allow-policy-mismatch` to override.
- It **warns** on soft differences: engine, host, git SHA, case-set overlap.
- Old runs without a policy are read as `legacy` (engine-default sampling, 180 s, profile-as treatment). They still render and rescore; comparing them against a new policy needs the override.

### 10. Medium: `--profile-as` makes every candidate "be Gemma" (fixed)

- In a multi-model run `--profile-as` defaults to the packaged agent model (`run_toolbench.py:363-368`). Each candidate inherits Gemma's list membership and Gemma's window.
- The parity check then aborts when the candidate's tuned oMLX settings differ (`run_toolbench.py:321-347`). They always do across families.
- This is useful as a control ("same treatment as the incumbent"). It is not a fair default for new families.

**Fix (done).**
- `--treatment packaged|profile-as|probe`.
  - `probe` (the new default for multi-model runs and non-oMLX engines) gives each model the list membership its own probe supports, for the benchmark only, and the window from finding 2.
  - `profile-as` keeps the old control.
  - `packaged` is the old single-model behaviour.
- The treatment applied to each model is recorded.

What the lists do on this path:
- On the model-led Ask path the hand-kept lists matter less than they look. Only `thinking_required_after_tool` is read, and only under narration-off (`runner.py:699-749`; `loop.py:913-930`).
- `tool_capable` is true by definition for the agent role (`config/__init__.py:806-818`).
- So the probe mostly matters for the window and for the thinking variants.

### 11. Medium: scorer and case wording that penalise non-Gemma habits (mostly fixed)

- **`reasoning_leak` opener** (`scorer.py:147-150`).
  - It fails answers that open "Let me check…" or "I need to check…" even after the tool ran and the answer is correct. That is a normal opener for Llama, Mistral and Phi.
  - Fix (done): third-person or "we" planning voice ("we need to", "the user is asking") still always counts. First-person "let me…/I need to…" counts only when no tool ran.
- **Typographic apostrophes.** Answer checks compare ASCII only (`scorer.py:229-236`). A model writing "can’t" misses `contains_any: ["can't"]`, and slips past `not_contains: ["i've moved"]`. Production already folds these (`service/router/model_led.py:271`). Fix (done): fold ‘ ’ ʼ ´ to `'` and “ ” to `"` before matching.
- **`not_contains: ["call:"]` in 19 chat and harbor cases** (e.g. `cases/chat.yaml:52,59,101,107`, `cases/harbor.yaml:20,26,32`). A bare substring fails prose such as "missed call: Mom". The precise markup check already lives in the scorer. Fix (done): the bare `"call:"` entries are removed, and `<|tool_call` stays.
- **`harbor_which_model` expects "gemma"** (`cases/harbor.yaml:40-44`). That is because the world's `harbor_status` fake names `gemma-4-e4b-it-4bit` (`scripts/toolbench/world.py:2885-2887`). It is fair, since the model must read the tool result, but a candidate may "correct" the tool to its own name. Left as is and listed here; changing it would move every old run.

### 12. Medium: whether the held-out split is still held out from Harbor's tuned Gemma cannot be shown (recommendation)

- `heldout_shingles` (`scripts/toolbench/cases.py:240-270`) exports hashed n-grams of gate and voice prompts for a training-side filter. Only its format is tested (`tests/test_toolbench.py:317`). Nothing in the repository proves the tuned model's training set was filtered against it.
- n-gram hashing misses paraphrases. 113 case traps describe variants of real-usage turns.
- The `dev` split is explicitly not held out.
- Host code was tuned on named gate cases (e.g. `service/router/model_led.py:684`).

**Recommendation.**
- Add a check, `--heldout-audit <training.jsonl>`, which reports exact and near overlap against the gate and records the dataset hash in `meta.json`. It was not built here: the training data is not in the repository and must not be copied.
- Report the tuned model's gate score next to a fresh private slice.
- Do not cite tuned-vs-base gaps without both.

### 13. Medium: the Ask prompt budget and scaffold were sized on Gemma (recommendation)

- `ASK_PROMPT_BUDGET = 8000` and `ASK_HISTORY_RESERVE` were measured on Gemma 4 E4B with a 16k window (`service/model_only.py:105-117`). The estimate is character-based (`loop._est_tokens`), so tokenisers that split more finely, as many small models' do, hold more real tokens per step than the estimate says.
- The scaffold is Harbor's protocol, which a model trained on Harbor-shaped data has seen:
  - 6 real schemas;
  - name-only stubs;
  - discovery through `get_tool_schemas`, with at most 2 loads (`service/model_only.py:205-206`; `service/router/model_led.py:424-435`).
- These are part of "Harbor as shipped", so they stay. The probe's measured window makes a 4-8k model fail visibly (`context_overflow`) rather than as `wrong_tool`.

**Recommendation.** A separate `--ask-budget` research variant for small windows, recorded as a different policy. This needs an app-side switch, so it was not built.

### 14. Medium-low: engine-error exclusion can flatter an unstable model (fixed)

- Excluded trials leave the denominator (`report.py:62-64`).
- Some exclusions can be caused by what the model emits. For example, a server whose tool-call parser chokes may close the stream mid-way, and "Invalid inference stream data" is excluded (`scorer.py:74-77`).
- The CLI's transient regex also matches any error containing "choices" (`run_toolbench.py:71-73`). It retries those once.

**Fix (done).** The scorecard shows the excluded share per model. Above 5% the report marks the model's numbers as unreliable. The paired test only uses cases both models scored.

### 15. Low-medium: `route_fault` cases count as failures for every model (fixed in the report)

- A `route_fault` is the host's: a needed tool forbidden, never offered, or refused for every model (`scorer.py:283-297`). It counts as a fail for everyone, which compresses differences.
- The `--dry-run` "host-forbids" check lists these (`run_toolbench.py:134-157`).

**Fix (done).** The scorecard adds "pass excl. route faults". The paired test drops cases that were a `route_fault` for both models.

### 16. Low: multimodal and mixture-of-experts models

- **Multimodal.** Toolbench is text-only: `picture_reading=None` (`runner.py:1207-1208`; vision turns are "not mirrored", `runner.py:48`). A vision-language model is benchmarked on text only, which is fair for this suite but says nothing about its vision. The vision and audio spec (`05-vision-audio-spec.md`) owns that.
- **A multimodal model on oMLX.** It can bloat memory if served through the VLM path. Record its RSS (perf harness).
- **Mixture-of-experts.** Nothing in Toolbench is mixture-of-experts specific. The unfairness is in reporting: speed and RAM depend on active, not total, parameters. That is now recorded (finding 8) and belongs in the perf report.

### 17. Low: smaller fidelity notes

- The harness's `ensure_only` never evicts, which is correct and safe.
- `--exclusive` exists only for oMLX. For `openai` engines it is refused, because the user's server owns residency.
- `_swap_ok` stops on memory pressure for every model equally. That is fair, and now recorded in `host`.

---

## Per model type: what breaks today, and what the fixes do

| Model type | Today | After the fixes |
|---|---|---|
| GGUF on llama.cpp | Refused by the oMLX transport, so every trial is silently excluded. Without `--jinja` its tool calls arrive as text. | `--engine openai`. The probe stops the run if calls are not structured, with the hint "start with `--jinja`". |
| Ollama | Refused as above. It defaults to a 4k window and silently truncates. `enable_thinking` is ignored. | The probe measures truncation and stops below 8,192 tokens with the `OLLAMA_CONTEXT_LENGTH` hint. The thinking-off variant is stopped when not honoured. |
| LM Studio | Refused as above. The context length is set per load. | Same probe. Quantisation and architecture are recorded from `/api/v0/models`. |
| MLX server (`mlx_lm.server`) | Refused as above. | `--engine openai`. `--model-path` reads `config.json` (quantisation, experts). |
| Other chat templates and tool formats | Text calls in non-Gemma markup are not recognised. Gemma's are repaired by the host. | Family-neutral markup labels, and "raw format clean" from the raw emission. |
| Thinking models | Forced to think on every step; 180 s limit; thinking-off unverified. | Probe, a 600 s hard limit, and a separate pass ≤ budget metric. |
| Non-thinking models | `enable_thinking: true` is ignored, which is harmless. `narration-off` on a model that goes empty after a tool is not anticipated. | Probe-derived `thinking_required_after_tool`. |
| Mixture-of-experts | Fine for correctness. Active parameters are not recorded. | `config.json` experts and experts per token, plus declared active parameters. |
| 4k-8k windows | 8,000 default window or Gemma's aliased window; errors scored as the model's. | Measured window, `context_overflow` label, early stop below 8,192 (overridable). |
| Multimodal | Text-only here. | Unchanged (out of scope; see spec 05). |

---

## The other harnesses

### `scripts/eval_planner.py`: keep, as a planner diagnostic, not a headline score

- It runs the real planner loop (`planner=True`) over 59 cases from `test_fixtures/routing/quality_corpus.json`.
- It is oMLX-only:
  - residency through raw `/v1/models/status` and unload (`eval_planner.py:361-385`);
  - default `OMLXClient()` (`eval_planner.py:397`).
- It sends no sampling or seed. It writes several stub fields as zeros (`eval_planner.py:336-341`). It has no intervals, and resume keys on case id only (`eval_planner.py:392-395`).
- The planner path is off in production Ask (`planner=False`, `runner.py:1231`). For a model comparison it measures a protocol most models will never run.
- Needs (not built here):
  - Toolbench's engine layer (`--engine/--port`), with residency optional;
  - the sampling policy;
  - the metadata block;
  - removing the stub fields;
  - trials with intervals.

### `scripts/bench_ondevice_perf.py`: keep, but its engine layer needs a rewrite before cross-engine use

It measures TTFT, prefill and decode tokens per second, end-to-end latency and peak RSS over a context sweep.

What ties it to oMLX:
- `ensure_only(..., exclusive=True)` evicts the production model and restores it (`bench_ondevice_perf.py:495`, `515`). That is unsafe on the user's Mac while Harbor runs.
- It finds the oMLX process by a fixed app path (`bench_ondevice_perf.py:43`, `131-138`).
- It reads tokenizers with `mlx_lm` (`bench_ondevice_perf.py:36`), so it cannot even import on a machine without it.

What makes its numbers unreliable:
- Decode tokens are re-tokenised locally, not taken from `usage`.
- Thinking is uncontrolled.
- 5 repetitions, mean and stdev only.

Needs:
- plain `/v1/chat/completions` with `stream_options.include_usage`;
- `--engine/--port/--pid`;
- thinking pinned off and recorded;
- whole-system memory, not one process's RSS;
- median and p90 over at least 10 repetitions;
- the same metadata block as Toolbench.

The perf spec (`04-perf-energy-memory-spec.md`) owns its redesign. This branch only records what it needs.

### `scripts/run_routing_stress_suite.py`: keep separate; it tests Harbor's router more than the model

- Its audit hook hard-codes port 8000 (`run_routing_stress_suite.py:46-47`).
- It copies the user's `~/.moe/config.yaml` and skills into its isolated home (`run_routing_stress_suite.py:240-246`). That is a privacy and reproducibility problem for a public benchmark.
- It runs at concurrency 2 by default (`run_routing_stress_suite.py:509`), which distorts latency and RAM.
- It has no aggregate statistics.

Not part of the model benchmark. If kept, it needs `--port`, no copied user config, concurrency 1, and an aggregator.

---

## Fixes implemented on `claude/bench-harness-fairness`

In priority order:
1. Engine abstraction: `--engine omlx|openai`, `--port`, exact-port network guard, no residency side effects on `openai`.
2. Probe and qualification: `scripts/toolbench/probe.py`, `--probe-only`, `--allow-unqualified`, and the probe treatment.
3. Sampling and settings policy: `scripts/toolbench/policy.py`, `--sampling`, `--context-window`, `--max-output-tokens`, `--latency-budget`, a 600 s hard timeout, and per-request seeds.
4. Report statistics (bootstrap intervals, paired permutation test, every-trial share, excluded share, pass excluding route faults, pass ≤ budget) and full run metadata (`scripts/toolbench/runmeta.py`).
5. `--compare` policy guard. Legacy runs stay readable.
6. Bias fixes:
   - family-neutral markup labels;
   - the `raw_markup` diagnostic;
   - the `reasoning_leak` narrowing;
   - apostrophe folding;
   - the `context_overflow` label;
   - removing the bare `call:` case checks.

Not built (recommendations above): app-side generalisation of the text-call repair (4), the held-out audit (12), the small-window Ask budget variant (13), and engine layers for the planner, perf and stress harnesses.

## Open questions

1. **Default sampling for comparisons.** I chose `fixed` at temperature 0.6, top_p 0.95, with top_k and min_p disabled, for every multi-model or non-oMLX run.
   - Greedy decoding would make trials identical and hide variance. Model-card settings would favour models whose authors tuned them.
   - If you prefer model-card settings as the headline, run `--sampling file:<map>` and record both.
2. **Default treatment for multi-model runs.** I chose `probe`, so each model gets what its own probe supports. Gemma's own run under `probe` can differ slightly from production, for example if its probe does not reproduce the empty-after-tool symptom.
   - Use `--treatment packaged` for a production-fidelity incumbent baseline.
3. **The 600 s hard limit.** It doubles the worst-case run length for hung models. If runs get too long, lower `--timeout`; "pass ≤ budget" is unaffected.
4. **The `harbor_which_model` case** expects "gemma" from the tool result. Keep it (it tests reading the tool), or bind the reported model to the candidate's id? The second changes old runs.
5. **App-side markup repair** for all families (finding 4) is a product change, not a benchmark one. Should it be filed as a Harbor task once development resumes?

---

## Live verification plan (for your approval; nothing here has been run)

**Preconditions, for the whole session:**
- Harbor quit, so nothing listens on 8765 or 8791. The harness refuses otherwise.
- No Swift build, `harbor-build`, replay suite, Simulation QA or other model job running.
- On AC power, Low Power Mode off, lid open, the Mac otherwise idle.
- At most one inference engine with one model resident at any time.

**Python** for every command: `.venv/bin/python` (a virtual environment at the repository root), run from the repository root. Commands below write `PY`.

**Step 0. Offline checks** (2 min, no model):
```
PY -m pytest -q tests/test_toolbench.py tests/test_rc3_toolbench_scorer.py tests/test_toolbench_fairness.py
PY scripts/run_toolbench.py --dry-run
PY scripts/run_toolbench.py --dry-run --engine openai --port 8080 --models qwen3-1.7b
```

**Step 1. oMLX regression, legacy policy** (about 10 min). oMLX running with Gemma resident, nothing else loaded:
```
PY scripts/run_toolbench.py --engine omlx --models gemma-4-E4B-it-oQ4e-mtp --sampling engine-default --treatment packaged --timeout 180 --ids chat_joke,cal_today,mail_unread_count,web_weather_tomorrow,rem_add_basic --trials 1
```
Expect: verdicts the same as a recent Gemma run on those ids. The meta has `policy.sampling = engine-default`, and `host`, `engine` and `model_info` are filled. Substitute ids from `--list` if any are renamed.

**Step 2. Probe only, oMLX** (about 3 min):
```
PY scripts/run_toolbench.py --engine omlx --models gemma-4-E4B-it-oQ4e-mtp --probe-only
```
Expect:
- a verified window of at least 16,384 (Gemma's oMLX setting);
- structured calls;
- thinking-off honoured;
- `empty_after_tool_when_off` true. This is the known Gemma symptom, and the reason `thinking_required_after_tool` lists it. If false, note it: the probe treatment would then differ from production for Gemma.

**Step 3. A second engine** (about 15 min).
1. Unload Gemma in oMLX, or quit oMLX. You do this; the harness never does.
2. Start llama.cpp with a small GGUF: `llama-server -m <Qwen3-1.7B-Q4_K_M.gguf> --jinja -c 16384 --port 8080`.
3. Run:
```
PY scripts/run_toolbench.py --engine openai --port 8080 --models <the id /v1/models lists> --probe-only
PY scripts/run_toolbench.py --engine openai --port 8080 --models <id> --ids <same 5 ids> --trials 1
```
Expect: the probe passes; trials are scored, not excluded; `meta.engine.runtime` names llama.cpp; `model_info` has the GGUF path and `n_ctx`.

Then, as a negative check, restart llama.cpp without `--jinja` and run `--probe-only` again. Expect a stop: "tool calls arrive as text".

**Step 4. Ollama truncation check** (about 10 min, optional). Ollama with its default context and a small model, port 11434:
```
PY scripts/run_toolbench.py --engine openai --port 11434 --models <model> --probe-only
```
Expect a stop with the `OLLAMA_CONTEXT_LENGTH` hint.

**Step 5. A real comparison** (hours; schedule it):
- Gate split, 3 trials, one model per engine session:
```
PY scripts/run_toolbench.py --split gate --trials 3 --engine <e> --port <p> --models <m>
```
- At about 20 s a turn, a 4B model takes about 325 × 3 × 20 s ≈ 5.5 h. Use `--ids` or `--only` for a 1-hour slice first.
- Then:
```
PY scripts/run_toolbench.py --report <run B> --compare <run A>
```
Expect the paired table. Expect a refusal if the two runs used different sampling or timeouts.

**Stop conditions:**
- the harness prints STOP for memory pressure;
- any guard escape appears in the report;
- the excluded share is above 5%;
- the Mac becomes unresponsive. Interrupt with Ctrl-C; `--resume <dir>` continues a run.
