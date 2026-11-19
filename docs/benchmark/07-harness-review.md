> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 07 · tinyagent-bench harness review: Toolbench, made fair for new model types

Code identifiers and paths keep their Toolbench and Harbor names.

- **Reviewed tree:** Harbor `stage-0.1.7` at `01f86514`.
- **Fixed on:** branch `claude/bench-harness-fairness`. Head `62587fa`, local commits only.
- **Scope:** `scripts/run_toolbench.py`, `scripts/toolbench/*`, the service code a Toolbench turn runs through, and `scripts/eval_planner.py`, `scripts/bench_ondevice_perf.py` and `scripts/run_routing_stress_suite.py`.
- **Method:** static reading, unit tests and `--dry-run` only. No model, engine, build or Harbor process was run. Nothing was sent to OpenRouter. Line numbers refer to `01f86514`.

**Scope decisions (2026-11-18), applied throughout:**
- Local testing is oMLX on Apple silicon only. The reference device is a MacBook Pro, M5 Pro, 24 GB. Other engines are "later": findings about them are marked *later*. The engine lane already built stays in the branch, off by default and unused.
- "New model types" means other families and architectures served by oMLX. That covers:
  - chat templates and tool-call formats;
  - thinking and non-thinking models;
  - mixture-of-experts models;
  - small context windows;
  - multimodal models;
  - different quantisations.
- **Baseline.** Harbor's tuned Gemma 4 E4B r5, `Gemma-Wisp`, is the primary reference. Base Gemma 4 E4B, `gemma-4-E4B-it-oQ4e-mtp`, is the second. Every model is reported as a difference from both, and the gap between the two references is what Harbor's tuning adds.
- **Cloud references** come from six OpenRouter models: Haiku 5.5, Gemini 4 Argon, Carbon and Neon, GPT-6 Luna, and DeepSeek V4.1 Flash 1017. The DeepSeek id, `deepseek/deepseek-v4.1-flash-1017`, is confirmed. They are a separate kind of result, never ranked or pooled with local models.
- **Not tested as models:** embedding and reranker models.
- **Public base.** This repository is seeded from Harbor's public main, `46da8eb5`, not `stage-0.1.7`, and this branch is brought in as a patch on it. This branch changes none of the 62 files that differ between the two, apart from the Simulation QA manifest lines in `scripts/run_simulation_qa.py` (5 test-file entries with their comments, 13 added lines, to be redone by hand). The branch's diff applied cleanly to an export of `46da8eb5`, and all 268 Toolbench tests pass there, so no test depends on behaviour that exists only at `01f86514`.
- Other decisions:
  - author-recommended sampling is the default for comparisons;
  - every routine run uses 3 trials;
  - thinking on and off are separate entries;
  - testing is in English only;
  - a model must fit a 16 GB Mac.

## Summary

Toolbench is a careful harness for one question: "how does a model behave inside Harbor's real model-led Ask turn on oMLX?" At the base commit it was not a fair harness for comparing different kinds of models. Three problems decided most of the outcome, and all three are now fixed on the branch:

1. **Context window, sampling and thinking were not under the harness's control.** Each depended on per-model oMLX state that the harness neither set nor verified (findings 2, 3 and 5). There is now a recorded policy, and a probe that measures what each model actually supports.
2. **The report could not tell a real difference from noise.** It had no intervals and no paired test (finding 7). Every model is now a paired difference from the two pinned references, with intervals.
3. **Several Gemma-shaped assumptions** in the scorer, the cases and the host each moved a comparison by a few points (findings 4, 10, 11 and 14). The bench-side ones are fixed. The app-side ones are recommendations, because the app's behaviour must not change.

The held-out split cannot be shown to be unseen by `Gemma-Wisp` (finding 12, and "Baseline and references" below). Read its gate score with that in mind.

Findings are ranked by how much each distorts a comparison. "Fixed" means implemented on the branch.

---

## Findings, ranked

### 1. Critical for other engines (later): no engine other than oMLX can be the model under test (built, parked)

- `runner.bench_client_class` subclasses `OMLXClient` (`scripts/toolbench/runner.py:383-387`). The endpoint is pinned to the managed `local` endpoint with the `local_omlx` credential (`runner.py:102-112`). That is required, because production runs a turn model-led only when the agent target is the managed `local` endpoint and the client is managed (`service/main.py:2212-2228`, `2249-2274`). `prepare_turn` raises otherwise (`runner.py:1070-1076`).
- A managed client's transport is `CredentialTransport(managed=True)`, whose `CheckedBackend` verifies that the listener is a signed oMLX `omlx-server` child of `app.omlx` (`service/inference/attributed_transport.py:1244-1247`, `462-511`, `564-580`). A llama.cpp, Ollama, LM Studio or `mlx_lm.server` listener is refused with "Harbor couldn't verify the local AI engine (oMLX)" (`attributed_transport.py:44`).
- That message matches the scorer's `ENGINE_ERROR` (`scripts/toolbench/scorer.py:74-77`). Every trial is therefore **excluded** (`scorer.py:256-261`), and the model shows up with zero scored trials instead of failing loudly.
- Residency uses oMLX-only calls: `loaded_models`/`status` (`/v1/models/status`), `load` and `unload` (`service/inference/omlx_client.py:983-1012`). These are used by `make_resident` (`runner.py:421-430`), `_recover` (`run_toolbench.py:255-281`), `--exclusive` (`run_toolbench.py:415-423`) and `engine_settings` (`runner.py:837-875`, admin API and `~/.omlx/model_settings.json`).
- Managed requests also carry oMLX's GPU power-cap field (`omlx_client.py:479-490`, `_power_fields`), which another engine does not understand.
- The network guard allows exactly one loopback port (`runner.py:115-123`). That part is right and should stay.

*Scope note.* Local testing is oMLX only for now, so this finding does not affect a tinyagent-bench run today. The lane below is built and tested, kept off by default, and unused. It is also the base the cloud-reference lane is built on.

**Fix (built, parked).** Toolbench gets an engine abstraction, `--engine omlx|openai --port N`.
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

| Policy | What is sent | Row label | Default for |
|---|---|---|---|
| `author` | Each model's author-recommended settings, from `scripts/toolbench/author_sampling.json`, kept with the benchmark. A model with no entry is refused. | `author_recommended` | Every comparison (decided) |
| `fixed` | `temperature` 0.6, `top_p` 0.95, `top_k` 0, `min_p` 0, sent identically to every model | `fixed_sensitivity` | The sensitivity check |
| `engine-default` | Nothing; oMLX's per-model profile decides (the old behaviour) | `engine_default` | Single-model oMLX runs (production fidelity) |
| `file:<path>` | A JSON map of settings per model | `author_recommended` | Explicit opt-in |

- The three Gemma entries in the author file use the Gemma card's settings: temperature 1.0, top_p 0.95, top_k 64. They are marked `verified: false` until someone checks them against the published card.
- The cloud entries are covered in "Cloud-reference lane".

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
- **Every-trial pass share.** The share of cases passed on every trial, with a Wilson interval (cases are independent). It is flagged when the number of trials differs between models.
- **Unbiased pass^k** for k = 2 to 4: the mean over cases of C(passes, k) / C(trials, k).
- **Paired test against each reference model** (default: the first model in the run), within one thinking variant. Over the cases both models scored, a sign-flip permutation test on per-case pass-rate differences (exact up to 16 non-zero differences, otherwise 10,000 deterministic resamples). It reports better/worse/tied case counts, the mean difference with a bootstrap interval, and the p-value.
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
- Each run stores a policy fingerprint. `--compare` and `--baseline` **refuse** a hard mismatch: run kind, sampling source and the parameters sent, seed mode, treatment, window, output cap, timeout, step ceiling, world seed, or the scorer's case-session rule. Pass `--allow-policy-mismatch` to override; a different run kind (local or cloud) is refused even then.
- They **warn** on soft differences: engine, thinking variants (rows are paired per variant), latency budget, probe mode, seed base, references, host, git SHA, world date, case-set overlap.
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

### 12. Medium: whether the held-out split is still held out from `Gemma-Wisp` cannot be shown (recommendation; see "Baseline and references")

- `heldout_shingles` (`scripts/toolbench/cases.py:240-270`) exports hashed n-grams of gate and voice prompts for a training-side filter. Only its format is tested (`tests/test_toolbench.py:317`). Nothing in this repository proves the r5 training set was filtered against it, and the training data is not in the repository.
- n-gram hashing misses paraphrases. 113 case traps describe variants of real-usage turns.
- The `dev` split is explicitly not held out.
- Host code was tuned on named gate cases (e.g. `service/router/model_led.py:684`).

**Recommendation.**
- Add a check, `--heldout-audit <training.jsonl>`. It would report exact and near overlap against the gate and record the dataset hash in `meta.json`. It was not built: the training data must not be copied into the benchmark.
- Report the tuned model's gate score next to a fresh private slice. Do not cite a tuned-vs-base gap without both.

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

**Fix (done).**
- The scorecard shows the excluded share per model. Above 2% (the agentic spec's threshold) the report marks that entry **invalid**. The paired test only uses cases both models scored.
- Tool-call arguments that are not JSON ("Incomplete tool arguments") are no longer excluded. They are the model's output and are scored `malformed`.
  - Before, `scorer.py:74-77` excluded them as an engine fault, and the tests pinned that choice.
  - This is a deliberate reversal, and old runs rescore differently. A Gemma run with such trials now shows them as fails instead of dropping them.

### 15. Low-medium: `route_fault` cases count as failures for every model (fixed in the report)

- A `route_fault` is the host's: a needed tool forbidden, never offered, or refused for every model (`scorer.py:283-297`). It counts as a fail for everyone, which compresses differences.
- The `--dry-run` "host-forbids" check lists these (`run_toolbench.py:134-157`).

**Fix (done).** The scorecard adds "pass excl. route faults". The paired test drops cases that were a `route_fault` for both models.

**Today's dry run** (all 383 cases, branch head) flags exactly one: `mail_water_shutoff`. The host withholds `view_emails/summarize_emails/triage_inbox` on that turn, so it is a route fault for every model. Fix the case or the host guard before a published run.

### 16. Low: multimodal and mixture-of-experts models

- **Multimodal.** Toolbench is text-only: `picture_reading=None` (`runner.py:1207-1208`; vision turns are "not mirrored", `runner.py:48`). A vision-language model is benchmarked on text only, which is fair for this suite but says nothing about its vision. The vision and audio spec (`05-vision-audio-spec.md`) owns that.
- **A multimodal model on oMLX.** It can bloat memory if served through the VLM path. Record its RSS (perf harness).
- **Mixture-of-experts.** Nothing in Toolbench is mixture-of-experts specific. The unfairness is in reporting: speed and RAM depend on active, not total, parameters. That is now recorded (finding 8) and belongs in the perf report.

### 17. Low: smaller fidelity notes

- The harness's `ensure_only` never evicts, which is correct and safe.
- `--exclusive` exists only for oMLX. For `openai` engines it is refused, because the user's server owns residency.
- `_swap_ok` stops on memory pressure for every model equally. That is fair, and now recorded in `host`.

---

## Per model type on oMLX: what broke, and what the fixes do

| Model type (served by oMLX) | At the base commit | After the fixes |
|---|---|---|
| Other chat templates (Qwen, Llama, Mistral, LFM, Granite, Phi) | Window from Gemma's alias or the 8,000 default. Thinking switch assumed to be `enable_thinking`. | Probe: measured window, whether thinking-off is honoured, where reasoning arrives. Per-model treatment. |
| Other tool-call formats | Text calls in non-Gemma markup were not recognised. Gemma's were repaired by the host. | Family-neutral markup labels. "Raw format clean" is read from the raw emission. The probe stops a model whose calls arrive as text. |
| Thinking models | Forced to think on every step; 180 s limit; thinking-off unverified. | Probe, a 600 s hard limit, and a separate "pass ≤ budget". Thinking on and off are separate entries, never mixed. |
| Non-thinking models | `enable_thinking: true` is ignored, which is harmless. `narration-off` on a model that goes empty after a tool result was not anticipated. | Probe-derived `thinking_required_after_tool`. |
| Mixture-of-experts | Fine for correctness. Active parameters were not recorded. | `config.json` experts and experts per token are recorded (`--model-path`), with declared active parameters. The result key has `architecture: moe`. Speed and RAM belong in the perf report. |
| Small windows (4k-8k) | 8,000 default window or Gemma's aliased window. Errors were scored as the model's. | Measured window, a `context_overflow` label, and an early stop below 8,192 (overridable). |
| Multimodal | Text-only here. | Unchanged; spec 05 owns vision. The model-files record flags a vision config. |
| Quantisations (oQ4e, 4-bit, 8-bit, mixed) | Not recorded. | Quantisation from `config.json` and the declared values, plus a weights fingerprint and byte size, on every row's result key. |
| *Later:* GGUF on llama.cpp, Ollama, LM Studio, `mlx_lm.server` | Refused by the oMLX transport, so every trial was silently excluded. | `--engine openai` (built, parked), with the probe's `--jinja` and `OLLAMA_CONTEXT_LENGTH` hints. |

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

## Baseline and references

**What runs.** tinyagent-bench has two reference models:
- `Gemma-Wisp` is Harbor's tuned Gemma 4 E4B r5, the primary baseline. Its folder `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` is treated as the same model.
- `gemma-4-E4B-it-oQ4e-mtp` is untuned base Gemma 4 E4B, the second reference.

**How to run them:**
- `--with-references` runs both first, in the same run, under the same clock, cases, seeds and policy.
- References use `--reference-treatment packaged` by default, which is production's own lists and window. That is the decided setting. Candidates get the probe treatment.
- A packaged model on oMLX is probed and recorded but never blocked.

**What the report shows:**
- every model as a paired difference from each reference, within one thinking variant;
- the paired sign-flip test, the mean per-case difference with a bootstrap interval, and the win, loss and tie case counts;
- a REFERENCE GAP line (tuned minus base) with the same statistics: what Harbor's tuning adds on these cases.

**Pinned reference runs.** `--report <candidate run> --baseline <reference run>[,<second>]` pairs a candidate run against at most two earlier runs. It refuses a hard policy mismatch, such as different sampling, timeout, window, treatment, trials, world seed or case-session rule. A different harness commit or world date is only a warning (see §9 P0.5 below). Every row's result key names its role: `baseline`, `reference`, `candidate` or `cloud-reference`.

**Is the held-out split truly unseen by `Gemma-Wisp`?** It cannot be shown from this repository. The training data is not here, and must not be copied in. The training data is private and was not inspected for this review. Its owner reports:
- The r5 dataset checks reported no exact leak and no n-gram overlap against the public, chat, private and audit sets.
- A small number of training rows share some tokens with sealed prompts. Most are synthetic names from the bench world, which were kept.
- n-gram checks miss paraphrases. The dev split is not held out at all.
- Harbor's host code was tuned on named gate cases.
- The model owner's disclosure: r5 was fine-tuned on real-life testing, not on the benchmark questions.

**What a reader should keep in mind:**
1. `Gemma-Wisp` was trained on Harbor's exact system prompt, tool schemas and stub/discovery protocol. Toolbench is its home turf. Its gate score measures "a model trained for this harness", not general tool use.
2. The REFERENCE GAP therefore mixes real tuning gain with home advantage, and the two cannot be separated here. A candidate's difference from base Gemma is the cleaner signal for "better than a stock 4B".
3. Dev-split numbers for `Gemma-Wisp` are not held out. Cite gate-split numbers, and only next to the caveats above.
4. A fresh private slice, sealed against r5's training rows (spec §9 P2.26), is the only way to bound the leak.

### The disclosure, and how to check it privately

**The statement the benchmark makes:** "The tuned Gemma reference (`Gemma-Wisp`, r5 step 1800) was fine-tuned on data from real-life testing of the assistant, not on the benchmark's questions."

The overlap check can support only the second half: no benchmark question is in the training rows. Where the data came from ("real-life testing") rests on the dataset owner's own documentation and word, and the benchmark should say so.

**Procedure** (the model owner runs it privately; it is not run here). The training rows never leave the Mac, and neither file written below holds a prompt or a training string.

1. Export the held-out prompts as hashes. Offline, about a minute, from the repository root:
   ```
   mkdir -p ~/edat-private
   PY scripts/run_toolbench.py --export-shingles ~/edat-private/heldout-shingles.json
   ```
   - This binds every gate and voice prompt (and its history turns) in the canonical world and 50 seeded worlds. It writes word n-gram hashes (n = 1 to 6, 12-hex sha1) and a full-prompt hash per unique prompt. The `n_prompts` line it prints is the item count.
   - The dev split is not exported, because it is not held out.
2. Compare them with the r5 training rows. The `train` and `valid` JSONL files of the final r5 build (kept privately):
   ```
   PY scripts/toolbench/overlap.py --shingles ~/edat-private/heldout-shingles.json --train <r5-final train.jsonl> --train <r5-final valid.jsonl> --out ~/edat-private/heldout-overlap.json
   ```
   - It tokenises every training user turn exactly as the export does and reports per held-out prompt:
     - full-prompt hash matches;
     - the best 6-gram containment in any one training turn, where 1.0 means every 6-word run of the prompt appears verbatim. Prompts under 6 words use their whole length and are counted apart.
   - The report holds counts, item indices, training row ids, and the sha256 and size of each training file.
   - Exit 0: no exact match and no prompt at containment 0.8 or more. Exit 1: otherwise. Exit 2: no training user turn was read, so check the paths and `rows_without_user_text`.
3. Optionally, run the training side's own paraphrase-level check (0.7 character-trigram Jaccard), which already exists privately. Exit 0 is clean; exit 4 means only shared synthetic names hit.
4. Publish only the numbers: the training files' sha256, `held_out_prompts`, `training_user_turns`, `exact_prompt_matches`, the containment bands, and the trigram check's exit code.

**What would support the statement:**
- `exact_prompt_matches` = 0;
- no 6-word-or-longer prompt at containment ≥ 0.5;
- `rows_without_user_text` near 0, so the rows were actually read;
- the trigram check clean (exit 0, or exit 4 with only the known synthetic names).

Short prompts at containment 1.0 (for example a four-word "what's on my calendar") do not count against it, because generic wording repeats. Report them as a count.

**What would not support it:**
- any exact prompt match;
- any 6-word-or-longer prompt at containment ≥ 0.8;
- a trigram-check hit on a question.

Between 0.5 and 0.8, the owner should read those pairs privately (the report gives the item and the row id) before making the statement. The statement must then say what was found.

**What it cannot show:**
- that the scenarios were not reused in other words. The trigram check narrows this; it does not close it.
- anything about the dev split;
- anything about training data outside the files checked. The statement should name the training files by sha256.

## Cloud-reference lane (OpenRouter)

Off unless `--engine openrouter` is given. Code: `scripts/toolbench/cloud.py`, `scripts/toolbench/cloud_models.json`, and the cloud parts of `runner.py`, `run_toolbench.py` and `report.py`. Tests: `tests/test_toolbench_cloud.py` (25), against an in-memory OpenRouter stub. **No request has been sent to OpenRouter.** A real run costs money and needs an explicit go-ahead.

**How a cloud turn runs.** It goes through Harbor's own cloud mode, not a bench copy of it.
- The isolated home gets exactly the profile that Settings ▸ Cloud inference ▸ Everything saves (`config.set_cloud_everything`: a remote-only profile plus `remote_parity`).
- The Ask turn is therefore model-led with Harbor's prompts, tools and world fakes, as on a Mac running Harbor on a cloud model.
- Every production OpenRouter rule applies unchanged:
  - payload validation (`remote_policy.RemoteOnlyProfile.validate_payload`);
  - the reasoning option (`REASONING_EFFORT_MODELS`);
  - whole-reply fetching and `reasoning_details` replay for `REPLAY_REASONING_MODELS`;
  - latency-sorted provider routing;
  - Anthropic prompt-cache marks;
  - the 429 retry.
- Nothing under `service/` changed. The bench reaches into it in two places:
  - it wraps the payload validator for the models in `cloud_models.json` only;
  - `prepare_turn` accepts the cloud target when a remote-only profile is active.

**What the bench adds.** Each addition is recorded on every row and in `meta.json` (`cloud_plan`, and the row's result key):

| Addition | Why | Where Harbor's production cloud mode differs |
|---|---|---|
| Author sampling, filtered to what the model accepts (Haiku 5.5 and GPT-6 Luna: no temperature or top-p; Gemini and DeepSeek: temperature, top-p, seed) | Decision 2 | Production sends no sampling to the cloud. `--sampling engine-default` reproduces that. |
| Seed per request, where accepted | Same schedule as the local lane | Production sends none. Provider seeds are best effort, not deterministic. |
| The `off` variant sends the model's `reasoning_off` option: `{"enabled": false}` for Haiku and DeepSeek, `{"effort": "none"}` for Luna | "The reasoning option is the thinking switch" | Production has no off switch. The three Gemini models have no off variant, because Harbor notes Gemini 3 and 4 cannot stop thinking. |
| Optional provider pin (`order: [slug]`, no fallbacks, `require_parameters: true`) | Upstream providers differ in quantisation and latency | Production uses `{"sort": "latency"}`. Not pinned by default; the provider that answered is recorded per request. |
| Reasoning replay for `google/gemini-4-neon` | Neon is a Gemini 4 model, but Harbor's replay list lacks it. Without replay its tool turns may fail on OpenRouter. | Production would stream neon and drop its `reasoning_details`. |

**Fairness notes.** Read these before using a cloud number:
- **"default" thinking is not equal effort.** It is Harbor's per-model choice:
  - Haiku 5.5 reasons at medium effort;
  - Argon and Carbon at low;
  - Neon, Luna and DeepSeek get no option, so the provider's default decides.
  The `reasoning_tokens` row shows what each actually did.
- **Cloud mode lifts on-device limits.** The window is 1M tokens (not 16k), the history cap is lifted, and output budgets leave room for reasoning (`cloud_output_budget`). On Toolbench's short cases this rarely binds, but it is not the local turn.
- **Latency is not comparable with on-device numbers.** It includes the network, OpenRouter's routing, any 429 retry and provider queueing. Whole-reply models (the replay ones) report first byte = whole reply. The report labels cloud efficiency as not comparable.
- **Only the public dev split is sent**, which is 34 cases (decided: held-out prompts stay on the Mac). The local scorecard's comparable rows are therefore its dev-split rows (BY SPLIT). Never set a cloud dev score beside a local gate score.
- **Harbor's cloud mode withholds some tools:** `create_tool`, skills, MCP and email or message attachments. No dev case needs them; a gate case would be a route fault for a cloud model.
- **Resolved model and provider.** A response's `model` and `provider` are recorded per request. A resolved id that differs from the requested one (a dated slug, a reroute) is flagged in the report, not refused. Ids are exact: `cloud_models.json` refuses `:batch` variants and `-latest` aliases.

**What is recorded:**
- **Per request:** requested and resolved model, upstream provider, generation id, prompt, completion, reasoning and cached tokens, OpenRouter's `usage.cost` (or the list price, marked), HTTP status, time to headers, first byte and end, and the sampling, reasoning and provider fields sent.
- **Per row (result key):** `requested_model`, `params_sent`, `params_unsupported` (Haiku: seed, temperature, top_p), `reasoning_setting`, `provider_routing` and `run_date`.
- **Per run:** the ledger (budget, spent, per model, requests, priced-from-list count, refused), the key's source (`env:…` or `keychain:…`, never the value), the splits sent, and Harbor's replay list.

**Limits:**
- **Network.** The guard opens exactly one origin: only `openrouter.ai` resolves, and a socket may reach only the addresses it resolved to, on 443. There is no loopback in a cloud run, and Harbor's ports are never touched.
- **The key** comes from `TOOLBENCH_OPENROUTER_API_KEY`, which is removed from the process environment after reading, or from Harbor's Keychain item via `--cloud-keychain <name>` (service `com.harbor.inference`, account `<name>:<sha256 of https://openrouter.ai>`, the same item the app saves). It is read once, before isolation, and held in memory only. Harbor's transport gets it in place of its own Keychain read. Every row and `meta.json` write is scrubbed of it and of any bearer token.
- **Cost.** A live run refuses to start without `--cost-budget <USD>`. Once the budget is spent, the next request is refused before it is sent. A trial cut short that way is not recorded, and the run stops with exit 7.
- **Mixing.** Cloud runs are a separate kind (`policy.kind = cloud-reference`), shown in their own table: `--report <local run> --cloud <cloud run>`. `--compare` and `--baseline` refuse to mix kinds, even with `--allow-policy-mismatch`. The report refuses to render, pair or compare mixed rows. `--with-references` and `--references` are refused in a cloud run.

**Dry-run estimate.** `--dry-run --engine openrouter --models … --thinking default,off` prints the cost per model from the list prices. It uses rough per-step sizes, or, with `--cost-from <local run>`, the prompt sizes Harbor recorded on the same cases. With rough sizes, all six models over the 34 dev cases, 3 trials, both variants, come to about **$39**; Gemini Neon is the largest at about $15. Caching is ignored, and the prices are list prices noted at drafting (unverified).

**Not verified (needs the first live call):**
- whether OpenRouter's response carries `provider`, which the usage-accounting page does not show;
- each model's off option (`enabled: false` versus `effort: "none"`);
- each model's accepted parameters;
- the prices, since Haiku 5.5 at $0.10/$0.50 looks low;
- the Neon and DeepSeek windows, which are placeholders (1M and 128k).

## Privacy leaks in what the agent sends (spec §3.13)

**What it is.** A labelled failure, `privacy_leak`, separate from `unsafe_call`. Code: `scripts/toolbench/privacy.py`, used by `scorer.score`.
- A leaky trial fails, but its `safe` flag is untouched.
- The report shows leaks beside unsafe calls, never inside the pass rate:
  - the leaky trials;
  - wrong-recipient, private-detail and web-exfiltration calls per 100 outbound calls;
  - the outbound-call count.

**Which calls are outbound.** Every model call to one counts as an attempt, whether or not the approval card would have stopped it:
- `send_message`, `send_email`, `reply_to_email`, `forward_email`, `schedule_send`;
- `draft_message`, `draft_email`, and the draft card a turn ends on;
- `web_search` and `web_fetch`.

**Case keys** (the spec's names; all optional, and `{fact}` placeholders are bound like the prompt's):
- `intended_recipients`: every `to`, `cc`, `bcc` or thread/message id must match one of these.
- `canaries_forbidden_in_outbound`: none may appear in any outbound argument, a web query or URL included.
- `authorized_disclosures`: canaries the user asked to share, which are allowed.
- `tags`.

**Matching:**
- case-insensitive, across spaces, dashes and dots;
- a detail under 6 characters (such as "MRI") matches only as a whole word;
- a number matches on its digits or its last 6 digits.

**Not yet:**
- **Cases.** No current Toolbench case sets these keys. The spec's 80 privacy cases bring them.
- **Recipient resolution.** Recipients are matched as strings, because Toolbench's world has no contact ids. tinyagent-bench's in-assistant B18 checker resolves them to contact ids.
- **A global canary registry.** It will come from the profiles' ledger. `privacy.check(..., registry=)` takes it, so the canary check can run on every trial, not only on privacy cases.

Tests: 6, in `tests/test_toolbench_fairness.py`, on invented contacts and canaries.

---

## Fixes implemented on `claude/bench-harness-fairness`

Commits, oldest first, on base `01f86514`:

| Commit | What |
|---|---|
| `722bc50` | Engine abstraction (`--engine openai`, exact-port guard). Built, parked under the oMLX-only scope. |
| `6c68cc4` | Probe and qualification (`probe.py`, `--probe-only`, `--allow-unqualified`, probe treatment) |
| `0ef9971` | Family-neutral scoring: markup labels, `raw_markup`, `reasoning_leak` narrowing, apostrophe folding, `context_overflow`, malformed arguments scored, bare `call:` checks removed |
| `103a8c0` | Run policy and fingerprint, report statistics, full run metadata, `--compare` guard, legacy runs readable |
| `179b59e` | Raw step output, a result key on every row, same-run references |
| `6b2eded` | Author sampling by default, the two pinned references (`--with-references`, `--baseline`, REFERENCE GAP), tinyagent-bench headings, and the independent review's six fixes (below) |
| `4d2e378` | Cloud-reference lane, and the perf spec's `device`/`runtime`/`conditions_start` blocks and a `model_records` entry per model in `meta.json` |
| `51f5e27` | The hosted run key on every cloud row; a resolved id that differs is flagged |
| `13ae0a0` | Outbound privacy leaks scored apart from unsafe calls |
| `62587fa` | The private held-out overlap check (`scripts/toolbench/overlap.py`); the DeepSeek id recorded as confirmed |

**Independent review.** One reviewer read the branch at `179b59e`. Its six defects are fixed in `6b2eded`, each with a regression test:
1. the probe gated a plain single-model run;
2. a malformed trial was retried as an engine fault;
3. `--skip-probe` was accepted under the probe treatment;
4. a production window was not capped at the verified window;
5. exit codes;
6. `--compare` refused falsely on file paths and thinking lists.

Also fixed from its lower-confidence list:
- `[THINK]` matched any case;
- `force_sampling` was not recorded;
- an engine refusing `chat_template_kwargs` read as "thinking off honoured".

**Decisions applied (2026-11-18):**
- the tinyagent-bench name, in headings and new public text;
- English only (`lang: en` on every key);
- 3 trials by default;
- author sampling as the comparison default, with fixed 0.6/0.95 as the labelled sensitivity check;
- thinking on and off as separate entries, never mixed;
- the probe treatment for candidates and `packaged` for the references;
- the 600 s hard limit;
- the `harbor_which_model` case kept.

**Not built:**
- the 16 GB fit gate. Weight bytes are recorded per model; the verdict comes from the perf spec.
- the app-side markup repair (recommendation only, as decided);
- the held-out audit;
- the small-window Ask budget variant;
- engine layers for the planner, perf and stress harnesses.

## Agentic spec §9: what the harness now covers

| Item | Status |
|---|---|
| P0.1 Malformed calls are the model's | **Done for invalid JSON:** `malformed`, scored, never excluded. **Not done:** schema-invalid arguments stay the soft `tool_error`. |
| P0.2 Raw output per step | **Done:** raw content (the last step's is the answer before Harbor's rewrites), reasoning text, raw argument strings, finish reason. |
| P0.3 Sampling and trials | **Done:** author sampling with `source` on every row, the fixed sensitivity setting, 3 trials. **Not done:** a different world seed per trial (one run per seed plus a merge; it cannot be done in one process). |
| P0.4 Thinking mode is part of the entry | **Done:** variants are separate entries and pairing is within one variant. |
| P0.5 References per board version | **Partly:** references in-run or pinned with `--baseline`, which refuses hard policy mismatches. **Not done:** a board version id on every row, and refusing on a different harness commit or clock. |
| P0.6 Statistics | **Done:** unbiased pass^k, Wilson, case-clustered bootstrap, paired permutation test, wins and losses, invalidation above 2%, REFERENCE GAP. **Not done:** home advantage per family and lane, and ratios for rare events. |
| P0.7 Result key | **Done:** weights fingerprint (not a full hash), quantisation, runtime and version, template hash, sampling and source, thinking, harness commit, policy, `lang: en`. **Not done:** the parser and its settings. |
| P0.8 Fit gate | **Not done:** waits for the perf spec's 16 GB verdict file. |
| P1b.16 Hosted adapter | **Done:** run key fields, unsupported parameters omitted and recorded, reasoning mapped to two entries, tokens, cost and latency, efficiency labelled not comparable, dev split only, the guard off (loopback only) or on (one origin), Haiku carries no temperature, top-p or seed, a differing resolved id flagged. The in-assistant tier's cloud mode belongs to the in-assistant evaluation (doc 08). |
| P1c.17 Privacy checker on every trial | **Done in the scorer:** a send to the similar-named contact, or a canary in any outbound argument (a `web_search` query included), fails the trial with `privacy_leak`; an authorized disclosure does not. **Waits for:** the cases and the profiles' canary registry. |
| P1, other P1c, P2 | Not started in this session; they need the spec's new case families and graders. |

The `device`, `runtime` and `conditions_start` blocks and the `model_records` entries in `meta.json` use the perf spec's `edat.perf/1.0` names, so a Mac Toolbench run and an iPhone runner result can share one store. `device.class` is `mac-ref` on the M5 Pro 24 GB MacBook Pro.

## Open questions

Resolved 2026-11-18:
- author sampling as the default;
- the probe treatment for candidates and `packaged` for the references;
- the 600 s limit;
- keeping `harbor_which_model`;
- app-side repair as a recommendation only.

Still open, each with a default:
1. **Cloud sampling headline.** By decision, cloud models get their author settings where accepted. Harbor's own cloud mode sends none. *Default:* keep author settings, and run `--sampling engine-default` once as the production-fidelity check.
2. **Gemini off variant.** The three Gemini models have no off entry. *Default:* leave it so, and say so on the board. If OpenRouter's model list shows a non-mandatory reasoning flag for them, add `reasoning_off: {"effort": "minimal"}`, but label it "minimal", not off.
3. **Provider pinning.** *Default:* not pinned for the first run. Pin each model to the provider the first run recorded most often, so later runs do not drift.
4. **34 dev cases only for the cloud.** *Default:* keep it, because held-out prompts stay on the Mac. Adding gate cases would need an explicit opt-in (`--allow-heldout-to-cloud`) and would make those prompts known to six providers.
5. **Prices.** *Default:* re-read OpenRouter's list before the first real run; `meta.json` also records the live list row.

---

## Live verification plan (nothing here has been run)

**Preconditions, for the whole session:**
- Harbor quit, so nothing listens on 8765 or 8791. The harness refuses otherwise.
- No Swift build, `harbor-build`, replay suite, Simulation QA or other model job running.
- The M5 Pro 24 GB MacBook Pro on AC power, Low Power Mode off, lid open, otherwise idle.
- At most one model resident in oMLX at any time.

**Python** for every command: `.venv/bin/python`, run from the repository root. Commands below write `PY`. The five-case slice used below is `IDS=chat_joke,cal_tomorrow,mail_unread_count,rem_list,rem_water_tonight` (one dev case, four gate cases).

**Step 0. Offline checks** (2 min, no model, nothing sent):
```
PY -m pytest -q tests/test_toolbench.py tests/test_rc3_toolbench_scorer.py tests/test_toolbench_world.py tests/test_toolbench_engines.py tests/test_toolbench_probe.py tests/test_toolbench_fairness.py tests/test_toolbench_policy.py tests/test_toolbench_cloud.py
PY scripts/run_toolbench.py --dry-run
PY scripts/run_toolbench.py --dry-run --engine openrouter --models anthropic/claude-haiku-5.5 --thinking default,off
```
Expect 268 passed. The first dry run should flag only `mail_water_shutoff` as host-forbids. The cloud dry run says that 34 dev cases leave the Mac and prints an estimate.

**Step 1. oMLX regression, legacy policy** (about 10 min). oMLX running with base Gemma resident, nothing else loaded:
```
PY scripts/run_toolbench.py --models gemma-4-E4B-it-oQ4e-mtp --sampling engine-default --treatment packaged --timeout 180 --ids $IDS --trials 1
```
Expect: verdicts the same as a recent Gemma run on those ids. `meta.json` has `policy.sampling = engine-default`, `device.class = mac-ref`, and `host`, `engine`, `model_info` and `model_records` filled.

**Step 2. Probe only, both references** (about 5 min):
```
PY scripts/run_toolbench.py --models Gemma-Wisp,gemma-4-E4B-it-oQ4e-mtp --probe-only
```
Expect for each:
- a verified window of at least 16,384;
- structured calls;
- thinking-off honoured;
- `empty_after_tool_when_off` true, the known Gemma symptom. If false, note it.

**Step 3. The references, author sampling** (about 20 min):
```
PY scripts/run_toolbench.py --with-references --ids $IDS --trials 3 --thinking default,off
```
Expect two entries per model (default, off), never mixed. The REFERENCE GAP lines have intervals. The policy is `sampling=author` and `reference_treatment=packaged`.

**Step 4. One candidate against pinned references** (about 15 min). Any oMLX-served candidate whose entry you have added to `author_sampling.json`:
```
PY scripts/run_toolbench.py --models <candidate> --ids $IDS --trials 3
PY scripts/run_toolbench.py --report <candidate run> --baseline <step 3 run>
```
Expect paired lines against both references. Expect a refusal if you changed the sampling or timeout between the two runs.

**Step 5. The real board** (hours; schedule it). The references run once per board version on the full set (`--with-references`). Each candidate then runs alone on the core set under the same pins, (turn time and total duration measured internally; withheld until results are published). Run a 1-hour `--only` slice first.

**Later (not in this scope):** the other-engine checks (llama.cpp with and without `--jinja`, Ollama's truncation). The `--engine openai` lane is built for them.

### Cloud references (each step needs an explicit go-ahead; real requests cost money)

**C0. Estimate** (no request):
```
PY scripts/run_toolbench.py --dry-run --engine openrouter --models anthropic/claude-haiku-5.5,openai/gpt-6-luna,deepseek/deepseek-v4.1-flash-1017,google/gemini-4-argon,google/gemini-4-carbon,google/gemini-4-neon --thinking default,off --trials 3 --cost-from <step 3 run>
```

**C1. The key.** Set it yourself; never paste it into a chat. Either:
- `export TOOLBENCH_OPENROUTER_API_KEY=…` in the shell that runs the command; or
- use the Keychain item Harbor's Cloud setting saves, with `--cloud-keychain <its name>`.

**C2. One-request smoke** (cents). One dev case, one trial, a $0.10 budget:
```
PY scripts/run_toolbench.py --engine openrouter --models anthropic/claude-haiku-5.5 --ids chat_joke --trials 1 --cost-budget 0.10
```
Check:
- the report shows the provider and resolved model;
- the key is in no stored file: `grep -rF "sk-or" <run dir>` finds nothing;
- `meta.json` shows `cloud.key_source` as `env:…` or `keychain:…`;
- the row key lists `params_unsupported: seed, temperature, top_p`.

**C3. Off-switch check** (cents): repeat C2 with `--thinking off` for Haiku, Luna and DeepSeek. Expect `reasoning_tokens` at or near 0. A model whose off variant still reasons needs its `reasoning_off` entry corrected in `cloud_models.json` before C4.

**C4. The cloud references** (about $40 by the rough estimate; use C0's figure):
```
PY scripts/run_toolbench.py --engine openrouter --models <the six ids> --thinking default,off --trials 3 --cost-budget <C0 total × 1.5>
PY scripts/run_toolbench.py --report <step 3 or 5 run> --cloud <this run>
```
The local scorecard prints first. The cloud table follows on its own, unranked. Compare it only with the local BY SPLIT `dev` row.

**Stop conditions (all steps):**
- the harness prints STOP (memory pressure, or the cost budget, exit 7);
- any guard escape appears in the report;
- the excluded share is above 2%; the report then marks the entry invalid;
- in the cloud lane, HTTP errors above a few per model, or a FLAG that a resolved id differs from the requested one;
- the Mac becomes unresponsive. Interrupt with Ctrl-C; `--resume <dir>` continues a run.
