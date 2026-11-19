> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 06 · Website UI design

Status: design only, first draft, 2026-11-18.
Mock-up: `site/index.html` (multi-file, the source). A one-file bundle can be built from `site/` for sharing; the bundle and its build script are not included in this repository.
Every number in the mock-up is **sample data** made by a seeded generator in `site/data.js`. Model names are invented ("Model D 4B", "Baseline 4B (tuned)"). The two devices and their runtimes are the planned first ones: MacBook Pro 24 GB with oMLX, and iPhone Air 12 GB with MLX Swift. No results exist for them yet, and the page says so. The site's name, **Edge agent bench**, is a working title only.

Revision 2 (same day) folds in three project decisions; see section 12.

## 1. Direction in one paragraph

The site answers one question first: *which model is the best agent that fits my device, and what does it cost me in memory, speed and battery?*

- **Device first.** The device is a global setting in the top bar and in the URL. Every page, table and chart is scoped to it.
- **Answer first.** The home page opens with a sentence that rewrites itself as the filters change, for example "Model D 4B is the best agent that fits an 8 GB phone." Beside it sit six figures (agent score, unsafe calls, readability, memory, decode speed, energy per task), with the tied models named.
- **Tables for lookup, charts for shape.** Every chart has a "Show the numbers as a table" disclosure.
- **Honest uncertainty.** Intervals sit beside every score, ranks are ranges, and differences are only called out when they are larger than the noise.
- **Missing results are results.** "Out of memory", "Beyond window", "Run failed", "Not run yet" and "Not applicable" each have their own icon and words.

## 2. Information architecture

| Page | Route in mock-up | What it answers |
|---|---|---|
| Leaderboard (home) | `#/?d=iph12&b=8&g=agentic&vs=tb` | Which model is the best agent that fits my device? What are the trade-offs against memory, energy and time? |
| Model | `#/model/d?d=iph12&q=q4&rt=mlxs` | What is this model? How does it score, scale with context, spend energy and behave on each device, quantisation and language? What do its answers look like? |
| Compare | `#/compare?d=mac24&m=h,g,tb&vs=rb` | Of these 2 to 4 models on one device, where is one *really* better? |
| Test explorer | `#/tests`, `#/tests/<tier>`, `#/case/<tier>/<case>?m=…&t=…` | What does each tier test? Which cases does a model fail, and how exactly (step-by-step tool calls, results and grader checks)? |
| In-assistant captures | `#/tests/asst-ask`, `#/capture/asst-ask?m=…` | Inside the assistant, what did the model actually answer, brief or plan? How does that compare with the answer key it was scored against? |
| Profiles | `#/profiles` | Who are the three synthetic people, and what is planted in their data? |
| Devices | `#/devices`, `#/device/<id>` | Which devices are covered? What fits on this one and how does it run there, including sustained load? |
| Readability vote | `#/vote` | Blind side-by-side: which answer is easier to read? This feeds the readability score. |
| Methodology | `#/methodology` | How each number is measured; uncertainty and ties; fairness rules; how missing results are shown; versions; reproducing and submitting a run. |

Structure decisions, and why:
- **The device page and the device filter are one concept.** The leaderboard's Device and Memory trays choose a reference device. The device page is a deeper view of the same device ("What fits", "How they run here", "Under sustained load"). There is no separate "fits in 8 GB" page.
- **Each test case gets its own page**, not a modal. A reader can then link to the exact trajectory (case, model, device and trial). This is where a reader checks that a score means what it says.
- **The readability vote is its own page.** It fits because human preference is planned as an input to readability (here 40% once a model has 300 votes, which is a proposal). It is also linked from every model's sample outputs.
- **Submissions** sit inside Methodology for now. Once community runs exist they deserve their own page (pattern 3 in the sites review: raw rows behind every number).

## 3. Main user journeys

1. **"What should I run on my phone?"** Home → Device: Phone → Memory: 8 GB → the verdict sentence names the model, and its figures show the costs → "Open Model D 4B" → the model page's context-scaling and energy sections → "On every device" to see whether the laptop is better.
2. **"Is the winner really better or just lucky?"** The verdict says "Statistically tied with Model E 4B" → "Compare with the tied models" → the compare table shows "No meaningful difference" on most rows and a green cell only where a lead beats the margin of error.
3. **"Does 71% mean what I think?"** Model page → "Scores by tier" → click "Chained calls" → the tier's case list, with three trial pips for this model → a failed case → the trajectory, where the bad step is shaded and labelled ("Skipped a step") and the grader checks say why it failed.
4. **"Is it safe to give it tools?"** The unsafe-call count is beside every agent score: in leaderboard rows, verdict figures, model figures, device tables and compare. On a phone it moves under the score instead of into a scrolled column. Model page → "Read its restraint cases" → an unsafe trajectory flagged "Unsafe call".
5. **"What does long context cost me?"** Model page → Context scaling → memory against the device's budget line, with the first out-of-memory level marked by a cross and the label "Out of memory", and the area beyond the model's window hatched.
6. **"Which quantisation should I download?"** Model page → "By quantisation": each variant on this device, with "-1.4, within noise" or "-4.2, real loss" against the best variant. Variants that do not fit are shown as rows with the memory they need.
7. **"Help rate readability."** Vote → read two blind answers → vote → the names are revealed → Next pair.

## 4. Data model the pages assume

```
Model        id, name, family, params_total, params_active, moe (bool), modalities[], context_window,
             licence, release_date, file hash per variant
Variant      model × quantisation × runtime  (key "d|q4|rtC"); file_size
Device       id, name, class (laptop|phone), ram_gb, memory_budget_gb (measured: RAM minus OS and typical
             apps), chip, os, bandwidth, power_method, source (reference | community)
Runtime      id, name, version, platforms
Quantisation id, name, bits per weight
Tier         id, group (agentic|quality|…), name, what it measures, case count, version
Case         id, tier, title, source kind, tools offered, ideal calls, checks
Run          variant × device × benchmark version → status (ok | oom | not_run | failed | no_variant), note,
             date, harness version, firmware
Result       run × metric → value, interval (95%), n; or a typed status
Curve        run × metric × context level (512…64k) → value | out_of_memory(need_gb) | beyond_window
Trajectory   run × case × trial → steps[{who, call, args, result, error}], final answer, checks[{ok, text}],
             outcome (pass | fail | unsafe)
Vote         pair(model a, model b, prompt) → choice (a | b | same | both poor), voter bucket, date
```

**The metric registry drives the pages.** Each metric is one record in `BENCH.metrics` (`site/data.js`) with id, group, name, short label, unit, better (high|low), decimals, interval kind (`ci` for pass rates, `rel` for timed medians, `exact`), an optional tier link, and an optional modality it needs. The leaderboard's column groups, the compare rows, the sort keys and the not-applicable logic all read the registry. Adding "Battery drain at 50% brightness", or a whole new group, is one line plus the data. The six groups (Agentic, Quality, Speed, Memory, Energy, Vision and audio) are themselves a list.

**The leaderboard's default row** is each model's best variant that fits the device: the highest agent score among the variants whose memory at 2k is under the budget, with ties going to less memory. The Runtime and Quantisation filters narrow the candidates. Every other variant stays visible on the model page.

## 5. Chart for each comparison, and why

| Comparison | Chart | Why |
|---|---|---|
| Many models × many metrics (exact lookup) | **Table** with switchable column groups and sortable heads; rank and model pinned on the left | Readers look values up. Grouping keeps any one view to 4 to 8 metric columns, and agent score plus unsafe calls are always on. |
| Score with uncertainty in a table row | Number ± interval plus a tiny **interval bar** (0 to 100) | Shows the overlap between neighbours at a glance without a separate chart (the sites review's main complaint about Arena). |
| Rank | **Rank range** "1–3" | Two models share a rank when their intervals overlap, which is honest about ties. |
| Quality against cost | **Scatter** (agent score against memory, J per task or s per task, chosen by a tray), 95% whiskers, a dashed **step frontier**, the winner in cobalt and the rest grey, diamonds for MoE | Shows the trade-off curve. Direct labels go only on frontier points and the winner, to stay readable. |
| Pass rate by tier, by language | **Dot-and-whisker rows** with a median tick for the models that fit | Intervals and position against peers in one line each. No radar charts (hard to read precisely). |
| Same for 2 to 4 models | Dot-and-whisker rows, vertically offset, one **shape + colour** per model | Shapes keep identity readable without colour. |
| Metric against context length (memory, prefill, decode, TTFT) | **Small multiples of line charts** over 7 categorical levels; a budget line on memory; a cross plus "Out of memory" at the first failing level; a hatched band for "Beyond window" | Shape over context is the point. Failures are drawn where they happen instead of leaving the line to end silently. Four separate charts, never a dual axis. |
| Energy for one task | One **100% stacked bar**: prefill, decode, tool waits | Part-to-whole for one task. The exact joules are in the figures above it. |
| Sustained performance | **Line** of decode tok/s over 30 minutes, end-labelled | Throttling is a shape over time. |
| What fits a device | **Horizontal bars** of memory at 2k against a red dashed budget line; bars over budget are hatched and labelled "too big" | Answers "fits or not" by position, pattern and words, not by colour alone. |
| Quantisation trade-off | **Table** with "change from best", marked "within noise" or "real loss" | Few rows. A chart would add nothing. The sites review's "variants joined by dashed lines on the scatter" is a good next step (see section 10). |
| Trajectory | **Ordered list** of steps with code panels, results, and labelled pass/fail/unsafe marks; grader checks beside it | It is a document, not a chart. |

Chart rules, from the dataviz method:
- **Series colours.** Light: `#2a78d6 #eb6834 #1baf7a #a8740a`. Dark: `#3987e5 #d95926 #199e70 #c98500`. They are validated with the dataviz validator: all checks pass in both modes. Aqua is under 3:1 in light, so it always has a shape, a direct label and a table view.
- **Never colour alone.** Shapes (circle, square, triangle, diamond), text labels and hatching carry identity and status.
- **Status colours** (good, bad, warning) are separate from the series colours and always come with an icon and words.
- **Text uses ink tokens, never series colours.** Every mark has a hover tooltip (keyboard focus works too), and every chart has a table view.

## 6. Missing and failed results

| Shown as | Icon | Meaning | Where |
|---|---|---|---|
| Does not fit | memory chip, red | Peak memory at 2k is over the device budget; the note gives the GB needed | Leaderboard's "Left out on this device" group, model per-device and per-quant tables, device bars (hatched) |
| Out of memory | memory chip, red; a cross on charts | Fits at short context but not at this length | Memory columns at 10k and 32k, context charts |
| Beyond window | arrow past lines | The context level is longer than the model's window | Memory columns, a hatched chart band |
| Run failed | cross, red | Runtime crashed or hung; the issue is linked | Rows, model page empty state |
| Not run yet | clock | Queued; never treated as zero | Rows, case pages |
| Not applicable | dash | The model lacks the modality | Vision and audio columns, compare |

Models that do not fit stay in the leaderboard, in a separate group under a divider ("Left out on this device (5)"), so absence reads as a finding. Each status is text plus an icon, never a blank or a 0. A page whose whole subject failed (for example a model page on a device it does not fit) shows an empty state that says what happened and links to the devices where it does run. In the filtered leaderboard, if no model fits, the verdict says so and offers "Clear filters".

The planned site also needs a **loading** state (skeleton rows in the table's shape) and an **error** state (data did not load, with a retry). The mock-up renders synchronously, so it does not draw them.

## 7. Visual direction and tokens

The direction is **Cobalt**, dealt by the Hallmark design skill (deal 2 of 3) and used in its app-screen scope. It is an exact, technical register:
- cool near-white paper ruled with hairlines;
- Familjen Grotesk heads and figures over IBM Plex Sans text;
- JetBrains Mono only for tool calls and case ids;
- keycap buttons and pressed-key toggle trays;
- graphite panels for code;
- one electric-cobalt signal, used only for the primary action, the current selection and the winning model.

It reads like an instrument, not a dashboard template. No gradients, no card grids, no icon tiles.

**Type** (fixed scale, ratio 1.2; numbers use tabular figures)

| Role | Face | Size |
|---|---|---|
| Verdict headline | Familjen Grotesk 600, −0.028em | clamp(1.9rem → 2.9rem) |
| Page title | Familjen Grotesk 600 | 2.074rem |
| Section | Familjen Grotesk 600 | 1.44rem |
| Figures | Familjen Grotesk 600 | 1.728rem (1.44 on phone) |
| Body | IBM Plex Sans 400/500 | 1rem |
| Table and labels | IBM Plex Sans / Familjen 500 | 0.875 / 0.75rem, sentence case, no tracking |
| Code | JetBrains Mono 400 | 13px |

**Colour tokens** (`site/tokens.css` for light; dark overrides in `site/site.css`, under both `prefers-color-scheme` guarded by `:root:not([data-theme="light"])` and `:root[data-theme="dark"]`)

| Token | Light | Dark |
|---|---|---|
| `--color-paper` | oklch(98.6% 0.005 250) | oklch(17.5% 0.02 262) |
| `--color-paper-2` / `-3` | 96.2% / 93% | 21% / 25% |
| `--color-ink` | oklch(21% 0.028 262) | oklch(94% 0.01 250) |
| `--color-ink-2` / `--color-muted` | 36% / 50% | 83% / 70% |
| `--color-rule` / `-2` | 90% / 83% | 29% / 37% |
| `--color-accent` (cobalt) | oklch(52% 0.19 258) | oklch(70% 0.15 256) |
| `--color-accent-wash` | oklch(95% 0.028 256) | oklch(27% 0.06 260) |
| `--color-graphite` (code) | oklch(21% 0.024 262) | oklch(13% 0.02 262) |
| `--color-good` / `--color-bad` / `--color-warn` | 50% 0.13 150 / 52% 0.19 27 / 58% 0.14 70 | 74% / 70% / 80% |
| `--series-1…4` | see section 5 | see section 5 |

**Spacing:** a 4px scale (`--space-3xs` 2 → `--space-4xl` 144). App density: controls are 34 to 40px with a 44px hit target on phones, table cells 12px, sections 64px apart. **Radius:** 8px panels (cut-corner bevel where `corner-shape` is supported), 6px inputs. **Motion:** state only, 120 to 220ms, with no load animation and none under reduced motion.

## 8. Responsive behaviour down to phone width (375px checked)

- **Top bar.** Below 68rem the six destinations fold into a menu button. The device picker and theme toggle stay. Below 40rem the wordmark collapses to its mark.
- **Verdict.** The two columns stack: headline, sentence, two full-width actions, then the six figures in a 2 × 3 grid. The headline, sentence and primary action fit the first screen at 375 × 812.
- **Size strip.** 4 columns become 2 × 2.
- **Filters.** The Device and Memory trays wrap. "More filters" stays a disclosure. Column-group trays scroll sideways and never wrap.
- **Leaderboard table.** The rank-and-model column is pinned while the metric columns scroll inside the table, never the page. The unsafe count moves under the agent score, so it stays visible beside the score without scrolling.
- **Compare.** The metric column is pinned and the model columns scroll.
- **Charts.** They render at their natural size and shrink to the column. Small multiples go from 4 across to 2 to 1. Tooltips also open on focus.
- **Trajectory.** The step label sits above the step, and the grader-checks panel drops below the steps.
- **Methodology.** The table of contents moves above the text.
- Checked in the browser at 1440, 1280 and 375 widths, in light and dark. The page body never scrolls sideways (`scrollWidth` = 375).

## 9. Mock-up files

```
site/index.html        shell: sample-data band, top bar, footer (open via a local server, see below)
site/tokens.css        Cobalt tokens + shared scale (scaffolded)
site/base.css          base rules (scaffolded)
site/site.css         the site's components, dark tokens, chart classes
site/data.js           ALL sample data: facts, metric registry, tiers, cases, seeded result generator
site/charts.js         SVG chart kit (scatter, dot rows, context lines, stacked bar, budget bars, sustain)
site/app.js            router (state in the URL hash) and the seven page types
```

To view locally, serve the folder and open the page: `python3 -m http.server 8731` in `site/`, then go to `http://127.0.0.1:8731/`. Opening the file directly also works in most browsers. Sloplint (Hallmark's linter, app scope) reports 0 FAIL. Its one remaining WARN, F4, takes the pressed tray buttons and trial pips for filled primary buttons; they are toggles, so I dismissed it.

## 10. Patterns adopted from the sites review (`01-benchmark-sites.md` §3)

**Adopted:**
- a global device scope that every chart obeys, with the filters restated in a conditions line;
- column groups;
- "did not fit" drawn as a result;
- a context-scaling chart with a cross at failure and the RAM line;
- intervals and rank ranges in the row, not hidden in a secondary chart;
- a top model per size tier (the size strip under the verdict);
- a visible results date;
- lab and community runs labelled on the device page;
- methodology with anchored sections;
- drill-down from a tier score to its cases and trajectories.

**Next, not yet in the mock-up:**
- a "How to read this chart" expander under each chart;
- threshold inputs on the scatter (minimum decode speed, maximum memory, maximum seconds per task) with "N models meet these limits";
- quantisation variants joined by dashed lines on the scatter;
- a submissions view of raw rows behind every number;
- score toggles for budgets (native context, 8k cap, 30 s per task).

**Rejected:**
- normalised "rank lanes" as the main table, which hide units;
- radar charts;
- a single opaque composite. The agent score is a plain mean of named tiers, and the formula is shown under the table.

## 11. Open questions for the user

1. **The name.** "Edge agent bench" is a placeholder. The sites review notes that Terminal-Bench's harness is also called "Harbor", so "Harbor bench" would clash.
2. **The headline metric.** The verdict ranks by the agent score alone and shows unsafe calls beside it. Should a model with many unsafe calls be barred from the verdict, for example above 10 of 120 or above the device median?
3. **The default device on first visit.** The mock-up defaults to Laptop 16 GB. Options: a phone class, the visitor's own device guessed from the browser, or a forced first-run picker.
4. **The fit rule.** Peak memory at 2k context under a measured budget. Should the rule use a longer context, such as 8k, because agent prompts with tool schemas are long?
5. **Readability in the vote.** Should outputs be rendered as a chat app would (bold rendered, as in the mock-up) or shown raw? Rendering is closer to what users see, but it hides markdown noise that small models produce.
6. **Thinking modes.** If a model has reasoning on and off, are they separate rows, as Artificial Analysis does it?
7. **Community runs.** Should they appear in the main leaderboard, or only on device pages until verified?

## 12. Revision 2: in-assistant tier, two devices, two pinned references

These are project decisions of 2026-11-18, folded into the mock-up and this file.

### 12.1 In the assistant (a new tier)

Each model runs inside the assistant itself against three synthetic profiles:
- a student;
- a tech worker;
- a student who works.

It is scored on three surfaces: **Ask** (questions about the day), **Brief** (surfaces what needs attention, without false alarms) and **Day** (a valid plan that covers what is due). Study is excluded.

- **Leaderboard.** A seventh column group, "In the assistant", shows:
  - the assistant score (the mean of Ask, Brief, Day valid and Day covered);
  - Ask;
  - Brief right items;
  - Brief false alarms per brief;
  - Day valid plans;
  - Day items covered;
  - unsafe or unconfirmed actions (of 90).

  The last is a count on its own, like unsafe calls, and never part of the score.
- **Model page.** An "In the assistant" table shows each surface (rows) by each profile (columns), plus "All three", each with its margin. It links to the three captures and to the Profiles page.
- **Test explorer.** The Ask, Brief and Day tiers list per-profile scores for the chosen model. The capture page puts **what the model produced** beside the **answer key**, then the grader checks:
  - Ask: the reference answer, the evidence item ids, and the items likely to mislead.
  - Brief: the items that should be in the brief (found or missed) and the noise that should be left out (left out, or surfaced as a false alarm), including an injection.
  - Day: the plan as a time table beside the constraints in the data (fixed events, travel time, the work a deadline needs, a changed dinner time, quiet hours).
- **Profiles page.** It introduces the three invented people, each labelled "Synthetic person", with:
  - age and the benchmark moment;
  - data volumes (emails, messages, events, reminders, notes, contacts, documents);
  - a table of what is planted, using the ledger types from `09-profile-schema.md` §5 in plain words, for example "Calendar is wrong" and "Instructions aimed at the assistant".

  Persona names in the mock-up are invented. The profile viewer was empty and the fixtures were not available when this draft was written, so the site should take names, counts and planted items from the real `index.json`, and link "Browse every item" to the viewer once it exists.
- **Data model additions.**
  - `Profile`: id, label, invented name, age, summary, anchor, volumes, planted counts by ledger type.
  - `AssistantResult`: run × profile × surface → value and interval, plus an unsafe-or-unconfirmed count.
  - `Capture`: tier × profile → prompt, output, answer key (truth, evidence ids, distractors, or expected and noise items, or constraints) and checks.

### 12.2 Two devices first, more later

- The device list is data: two entries now, and new devices are appended to `devices` in `site/data.js`. There are no empty Android or Windows sections.
- The **Device** tray shows the two devices by name. The **Memory budget** tray simulates smaller classes on each device by capping the model's memory:
  - Mac: 8, 12, 16 GB or the whole 24 GB;
  - iPhone: 8 GB or the whole 12 GB.

  The verdict reads "fits a 16 GB laptop" when capped and "fits the MacBook Pro, 24 GB" uncapped. The note under the tray says how the cap works. Budgets (sample values): laptop 4.8 / 7.5 / 10.5 / 16 GB; phone 3.6 / 6.0 GB.
- Each device has one runtime, so the runtime filter is gone and the model page shows the runtime as text. A runtime tray comes back automatically when a device has two.
- iPhone energy is labelled as a battery-gauge estimate, on the device page and in Methodology.

### 12.3 Two pinned references

- **"Baseline 4B (tuned)" and "Reference 4B (base)"** sit in a pinned group at the top of the leaderboard table. They ignore filters, and they are still ranked with everyone else.
- **Show: Values / Difference from tuned baseline / Difference from base reference.** This is on the leaderboard and on Compare, and it is kept in the URL as `vs`. In difference mode every cell becomes "+3.2 ±4.1". The margin is √(a² + b²) of the two intervals, a Poisson margin for counts, and 12% for answer length.
- **A mark appears only when the gap is larger than the margin.** A tick in the good colour means better; a warning sign in the bad colour means worse. Within noise there is no mark and the text stays in plain ink, so the marks never rely on colour alone.
- **Like-for-like.** A row is compared with the reference at the same quantisation and runtime when that variant runs; otherwise with the reference's best variant.
- **Model page.**
  - "Against the references" lists nine key measures (value, difference from tuned, difference from base).
  - On either reference's page, "Effect of tuning" shows tuned minus base for 18 measures at the same variant. Speed and memory rows act as a check that tuning changed only behaviour.

### 12.4 New open questions

1. Should the verdict be allowed to name a reference model as "the best agent"? Today it can, because the references are ranked like everyone else.
2. Should the default view be "Difference from tuned baseline" instead of values, since the baseline is the project's own model?
3. The memory budget for simulated classes (for example a 16 GB laptop = 10.5 GB for the model) needs measured numbers from real 8, 12 and 16 GB machines.
4. The leaderboard has no assistant score in its default columns. Should the assistant score sit beside the agent score permanently, like unsafe calls?
5. Device names are real (MacBook Pro, iPhone Air) while every number is sample data. The band at the top says so. Is that acceptable for a shared mock-up, or should devices be generic until results exist?
