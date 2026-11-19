/* The metric registry. Every column, compare row, sort key and "not applicable" rule reads from here.
 * Adding a metric is one entry in `metrics`; adding a column group is one entry in `groups`;
 * adding a test area is one entry in `tiers`.
 *
 * A metric's `src` says where its value lives in the results store's export (site/data/README.md):
 *   { r: tier, m: metric, f: facet|null }    a row of results[] (value, interval, vs)
 *   { s: tier, k: field }                    a row of safety[] (unsafe and leak counts, never in a score)
 *   { c: curveMetric, level: tokens }        one point of curves[] (performance over context)
 *   { fit: field }                           runs[].fit (the 16 GB fit gate)
 * `scale` converts the stored unit to the shown unit (bytes to GB, for example).
 * `on`: "local" (on-device only), "cloud" (hosted references only) or "both".
 * `{ loop: tier, k: "gap" }` reads the board's loops[] row for that tier (the gap between the two loops).
 * `planned: true` marks a metric whose tier has no rows in today's export; its cells read
 * "Not in this board yet" until the store writes it, and the check script lists it. */
(function () {
  "use strict";
  const EDAT = (window.EDAT = window.EDAT || {});
  const GiB = 1 / 1073741824;

  const groups = [
    { id: "safety", name: "Safety", fixed: true, desc: "Unsafe actions and privacy leaks: always shown beside the score, never in it." },
    { id: "agentic", name: "Agentic", main: true, desc: "Tool use across seven families, each half assistant lane and half neutral lane, three trials per case." },
    { id: "families", name: "Agentic families", main: true, desc: "Pass rate in each of the seven families that make up the agentic score." },
    { id: "assistant", name: "In the assistant", main: true, desc: "Ask, Brief and Day inside the assistant, across three synthetic people." },
    { id: "quality", name: "Output quality", main: true, desc: "Readability, text understanding and writing." },
    { id: "perf", name: "Performance", main: true, desc: "Speed and memory on this device, at fixed context sizes." },
    { id: "preferences", name: "Preferences", desc: "Instruction following: standing preferences kept, overridden and replaced correctly." },
    { id: "longsession", name: "Long sessions", desc: "Whether what was set early in a 40-turn conversation still holds late." },
    { id: "memory", name: "Memory", desc: "Remembering facts across sessions." },
    { id: "vision", name: "Vision", desc: "Reading screenshots, documents and photos." },
    { id: "audio", name: "Audio input", desc: "Understanding spoken requests. There is no speech output." },
    { id: "screen", name: "Screen and terminal", desc: "Controlling apps on screen, and light coding and terminal work." },
    { id: "energy", name: "Energy", desc: "Joules per task and per token (Mac only in version 1)." },
    { id: "day", name: "Simulated day", desc: "Forty scripted tasks with the model kept loaded." },
    { id: "cloudcost", name: "Cloud cost and latency", cloud: true, desc: "Hosted references only: cost, tokens and network time. Never compared with on-device numbers." }
  ];

  const P = (id, group, name, short, unit, better, dp, src, extra) => Object.assign({ id, group, name, short, unit, better, dp, src, on: "both" }, extra || {});

  const metrics = [
    /* headline and fixed columns */
    P("agentic_score", "agentic", "Agentic score", "Agentic score", "%", "high", 1, { r: "agentic", m: "agentic_score" }, { headline: true, tierLink: "agentic", desc: "From the benchmark's own minimal loop, which never repairs a model's mistakes. Mean of the seven family pass rates; each family is the mean of its two lanes." }),
    P("assistant_loop_score", "agentic", "Score in the assistant's own loop", "Assistant loop", "%", "high", 1, { r: "agentic_harbor_loop", m: "agentic_score_harbor_loop" }, { tierLink: "agentic_harbor_loop", desc: "The assistant-lane cases run through the assistant's own loop, which re-prompts and repairs mistakes. Empty, with the store's reason, where that loop was not run." }),
    P("repair_gain", "agentic", "What the assistant's repairs add", "Repairs add", "points", null, 1, { loop: "agentic", k: "gap" }, { format: "signed", desc: "The assistant's loop score minus the minimal loop's assistant-lane score on the same cases (the store's loop_gap), with its paired 95% interval." }),
    P("unsafe", "safety", "Unsafe actions", "Unsafe", "per 100 trials", "low", 1, { s: "agentic", k: "unsafe_per_100" }, { safety: true, rateKey: "unsafe_rate", desc: "Forbidden or unconfirmed actions per 100 agentic trials. Counted on its own, never in a score." }),
    P("leaks", "safety", "Privacy leaks", "Leaks", "per 100 trials", "low", 1, { s: "agentic", k: "leaks_per_100" }, { safety: true, rateKey: "leak_rate", desc: "Wrong recipients or leaked private details per 100 agentic trials. Counted on its own, never in a score." }),

    /* agentic (spec 02) */
    P("lane_assistant", "agentic", "Assistant lane", "Assistant lane", "%", "high", 1, { r: "agentic", m: "pass1", f: "lane=assistant" }, { desc: "Pass rate with the assistant's own tool set." }),
    P("lane_neutral", "agentic", "Neutral lane", "Neutral lane", "%", "high", 1, { r: "agentic", m: "pass1", f: "lane=neutral" }, { desc: "Pass rate with a neutral, model-agnostic tool set." }),
    P("pass_all", "agentic", "Reliability (pass every trial)", "Reliability", "%", "high", 1, { r: "agentic", m: "pass_all_trials" }, { desc: "Share of cases passed on all three trials (pass^3)." }),
    P("progress", "agentic", "Progress", "Progress", "%", "high", 1, { r: "agentic", m: "score_mean" }, { desc: "Partial credit: how far through each case the model got." }),
    P("format_ok", "agentic", "Format validity", "Valid calls", "%", "high", 1, { r: "agentic", m: "format_ok" }, { desc: "Share of tool calls that parse and match the schema." }),
    P("restraint", "agentic", "Restraint violations", "Restraint", "%", "low", 1, { r: "agentic", m: "restraint_violation" }, { planned: true, desc: "Share of restraint cases where the model acted when it should have asked or stopped." }),
    P("over_refusal", "agentic", "Over-refusal", "Over-refusal", "%", "low", 1, { r: "agentic", m: "over_refusal" }, { planned: true, desc: "Share of safe requests the model refused." }),
    P("steps", "agentic", "Steps per case", "Steps", "steps", "low", 2, { r: "agentic", m: "steps" }, { desc: "Mean model turns per case." }),
    P("wall_s", "agentic", "Seconds per case", "Time", "s", "low", 1, { r: "agentic", m: "wall_s" }, { on: "local", desc: "Mean wall-clock time per case on this device." }),

    /* the seven families */
    ...[["select", "Select"], ["parallel", "Parallel"], ["sequential", "Sequential"], ["mixed", "Mixed"], ["pev", "Plan, execute, verify"], ["recovery", "Recovery"], ["abstain", "Abstain"]].map(([f, n]) =>
      P("fam_" + f, "families", n, n, "%", "high", 1, { r: "agentic", m: "pass1", f: "family=" + f }, { family: f, tierLink: "agentic", desc: n + " family pass rate, both lanes." })),

    /* in the assistant (spec 08) */
    P("assistant_score", "assistant", "Assistant score", "Assistant", "%", "high", 1, { r: "in_assistant", m: "assistant_score" }, { tierLink: "in_assistant", desc: "Mean of Ask, Brief and Day across the three synthetic people." }),
    P("ask", "assistant", "Ask", "Ask", "%", "high", 1, { r: "in_assistant", m: "pass1", f: "surface=ask" }, { desc: "Questions about the person's day, answered from their data." }),
    P("brief", "assistant", "Brief", "Brief", "%", "high", 1, { r: "in_assistant", m: "pass1", f: "surface=brief" }, { desc: "Surfaces what needs attention, without false alarms." }),
    P("day", "assistant", "Day", "Day", "%", "high", 1, { r: "in_assistant", m: "pass1", f: "surface=day" }, { desc: "A valid plan that covers what is due." }),
    P("findings_found", "assistant", "Found in Brief, inbox and to-dos", "Found", "%", "high", 1, { r: "in_assistant_findings", m: "found" }, { tierLink: "in_assistant_findings", desc: "Share of answer-key items the assistant surfaced. See \"What it found\" for each item." }),
    P("asst_unsafe", "assistant", "Unsafe or unconfirmed actions", "Unsafe", "per 100 trials", "low", 1, { s: "in_assistant", k: "unsafe_per_100" }, { safety: true, desc: "Inside the assistant. Counted on its own, never in the assistant score." }),

    /* output quality (spec 03) */
    P("quality_pass", "quality", "Quality pass rate", "Pass rate", "%", "high", 1, { r: "quality", m: "pass1" }, { tierLink: "quality", desc: "Share of answers that pass every check." }),
    P("quality_score", "quality", "Quality score", "Score", "%", "high", 1, { r: "quality", m: "score_mean" }, { desc: "Mean per-answer score." }),
    P("readability", "quality", "Readability", "Readability", "%", "high", 1, { r: "quality", m: "readability" }, { showLabel: true, desc: "Hygiene plus a style-controlled judge. The cell says which: \"judge\", or \"structure only\" until the judge is validated against votes." }),
    P("text_understanding", "quality", "Text understanding", "Understanding", "%", "high", 1, { r: "quality", m: "text_understanding" }, { showLabel: true, desc: "Summaries, key points, extraction and numbers: are the facts that matter kept, and nothing invented?" }),
    P("writing", "quality", "Writing", "Writing", "%", "high", 1, { r: "quality", m: "writing" }, { showLabel: true, desc: "Rewrites that keep the facts and follow the constraints. The cell says whether a judge or only the checks scored it." }),
    P("extraction", "quality", "Extraction", "Extraction", "%", "high", 1, { r: "quality", m: "pass1", f: "family=extraction" }, { desc: "Fields pulled correctly from documents." }),
    P("summary", "quality", "Summaries", "Summaries", "%", "high", 1, { r: "quality", m: "pass1", f: "family=summary" }, { desc: "Summaries that keep the facts that matter." }),
    P("fabricated", "quality", "Fabricated details per answer", "Fabricated", "per answer", "low", 2, { r: "quality", m: "fabricated_atoms" }, { desc: "Invented facts per answer. Shown beside the score, not in it." }),
    P("visible_tokens", "quality", "Visible tokens per answer", "Length", "tokens", null, 0, { r: "quality", m: "visible_tokens" }, { desc: "Answer length. Neither longer nor shorter is better on its own." }),

    /* preferences, long sessions, memory (spec 02 §3.14 to 3.16, spec 14) */
    P("instruction_following", "preferences", "Instruction following", "Following", "%", "high", 1, { r: "preferences", m: "preferences_score" }, { tierLink: "preferences", desc: "Mean of five dimensions: still followed later, one-off override, newer replaces older, not over-stretched, never waives a confirmation." }),
    P("prefs_loop", "preferences", "Instruction following in the assistant's loop", "In assistant loop", "%", "high", 1, { r: "preferences_harbor_loop", m: "preferences_score_harbor_loop" }, { desc: "The same cases through the assistant's own loop." }),
    P("late_retention", "longsession", "Late-turn retention", "Late turns", "%", "high", 1, { r: "long_session", m: "late_retention" }, { tierLink: "long_session", desc: "How much of what was set early in a 40-turn conversation still holds in the late turns." }),
    P("long_pass", "longsession", "Long-session pass rate", "Pass rate", "%", "high", 1, { r: "long_session", m: "pass1" }),
    P("late_loop", "longsession", "Late-turn retention in the assistant's loop", "In assistant loop", "%", "high", 1, { r: "long_session_harbor_loop", m: "late_retention_harbor_loop" }),
    P("memory_pass", "memory", "Memory score", "Memory", "%", "high", 1, { r: "memory", m: "memory_score" }, { tierLink: "memory", desc: "Facts remembered and recalled correctly across sessions." }),
    P("forget", "memory", "Forgets when asked", "Forgets", "%", "high", 1, { r: "memory", m: "forget_compliance" }, { desc: "Share of forget requests honoured: the fact is gone from later answers." }),
    P("memory_loop", "memory", "Memory in the assistant's loop", "In assistant loop", "%", "high", 1, { r: "memory_harbor_loop", m: "memory_score_harbor_loop" }),

    /* vision and audio (spec 05) */
    P("vision_pass", "vision", "Vision pass rate", "Vision", "%", "high", 1, { r: "vision", m: "pass1" }, { planned: true, modality: "vision", tierLink: "vision" }),
    P("audio_pass", "audio", "Audio input pass rate", "Audio", "%", "high", 1, { r: "audio", m: "pass1" }, { planned: true, modality: "audio", tierLink: "audio" }),

    /* screen control (spec 11) and sidekick coding (spec 12) */
    P("screen_pass", "screen", "On-screen app control", "Screen", "%", "high", 1, { r: "app_control", m: "screen_score" }, { tierLink: "app_control", desc: "Operating app screens through the accessibility tree." }),
    P("screen_shot", "screen", "App control from screenshots", "From screenshots", "%", "high", 1, { r: "app_control", m: "screen_score_screenshot" }, { desc: "The same tasks with a screenshot in place of the accessibility tree." }),
    P("sidekick_pass", "screen", "Coding and terminal", "Sidekick", "%", "high", 1, { r: "sidekick", m: "sidekick_score" }, { tierLink: "sidekick" }),
    P("sidekick_tools", "screen", "Coding with tools", "With tools", "%", "high", 1, { r: "sidekick", m: "sidekick_tool_mode" }, { desc: "Sidekick tasks done through tool calls rather than a written answer." }),

    /* performance (spec 04): read from the context curves */
    P("decode_2k", "perf", "Decode speed at 2k", "Decode", "tok/s", "high", 0, { c: "decode_tps", level: 2048 }, { on: "local", desc: "Tokens generated per second with 2k tokens of context." }),
    P("prefill_2k", "perf", "Prefill speed at 2k", "Prefill", "tok/s", "high", 0, { c: "prefill_tps", level: 2048 }, { on: "local", desc: "Prompt tokens read per second with 2k tokens of context." }),
    P("ttft_2k", "perf", "Time to first token at 2k", "First token", "s", "low", 2, { c: "ttft_s", level: 2048 }, { on: "local" }),
    P("mem_10k", "perf", "Peak memory at 10k", "Memory 10k", "GB", "low", 1, { fit: "peak_bytes_at_10240" }, { on: "local", scale: GiB, desc: "Peak memory with 10k tokens of context: the fit gate's measure." }),
    P("max_ctx", "perf", "Longest context that fits", "Longest fit", "tokens", "high", 0, { fit: "max_level_fits" }, { on: "local", format: "ctx" }),
    P("load_s", "perf", "Load time", "Load", "s", "low", 1, { r: "perf", m: "load_s" }, { on: "local", planned: true }),

    /* energy (spec 04): Mac only in version 1 */
    P("j_task", "energy", "Energy per completed task", "J per task", "J", "low", 0, { r: "perf", m: "energy_task_J" }, { on: "local", planned: true, macOnly: true }),
    P("mj_prefill", "energy", "Energy per prefill token at 2k", "mJ prefill", "mJ/token", "low", 2, { r: "perf", m: "energy_prefill_mJ_per_token" }, { on: "local", planned: true, macOnly: true }),
    P("j_task_prefill", "energy", "Energy per task: prefill", "J prefill", "J", "low", 0, { r: "perf", m: "energy_task_prefill_J" }, { on: "local", planned: true, macOnly: true, split: true }),
    P("j_task_decode", "energy", "Energy per task: decode", "J decode", "J", "low", 0, { r: "perf", m: "energy_task_decode_J" }, { on: "local", planned: true, macOnly: true, split: true }),
    P("j_task_tools", "energy", "Energy per task: tool waits", "J tools", "J", "low", 0, { r: "perf", m: "energy_task_tools_J" }, { on: "local", planned: true, macOnly: true, split: true }),
    P("mj_decode", "energy", "Energy per decode token at 2k", "mJ decode", "mJ/token", "low", 1, { r: "perf", m: "energy_decode_mJ_per_token" }, { on: "local", planned: true }),

    /* simulated day (spec 04 §5) */
    P("day_battery", "day", "Battery used over the day", "Battery", "%", "low", 0, { r: "day", m: "battery_share" }, { on: "local", planned: true }),
    P("day_in_time", "day", "Tasks finished in time", "In time", "%", "high", 0, { r: "day", m: "in_budget" }, { on: "local", planned: true }),
    P("day_idle_w", "day", "Idle power, model loaded", "Idle", "W", "low", 2, { r: "day", m: "idle_power_W" }, { on: "local", planned: true, macOnly: true }),
    P("day_after_unload", "day", "Answer time after an idle unload", "After unload", "s", "low", 1, { r: "day", m: "after_unload_s" }, { on: "local", planned: true }),

    /* cloud references only */
    P("cost_task", "cloudcost", "Cost per completed task", "Cost", "US$", "low", 4, { r: "agentic", m: "cost_usd" }, { on: "cloud", planned: true }),
    P("tokens_task", "cloudcost", "Tokens per task", "Tokens", "tokens", null, 0, { r: "agentic", m: "completion_tokens" }, { on: "cloud", planned: true }),
    P("net_ttft", "cloudcost", "Network time to first token", "Net first token", "s", "low", 2, { r: "perf", m: "net_ttft_s" }, { on: "cloud", planned: true }),
    P("net_total", "cloudcost", "Network seconds per task", "Net per task", "s", "low", 1, { r: "perf", m: "net_total_s" }, { on: "cloud", planned: true })
  ];

  /* Test areas, for the test explorer and the methodology. `tier` is the store's tier name. */
  const tiers = [
    { id: "agentic", name: "Agentic tool use", group: "agentic", spec: "02", facets: ["family", "lane"], what: "Can the model pick the right tools, fill their arguments, chain and parallelise calls, recover from errors, and hold back when it should ask or stop?" },
    { id: "in_assistant", name: "In the assistant", group: "assistant", spec: "08", facets: ["surface", "profile"], what: "Inside the assistant itself, against three synthetic people: answer questions about the day (Ask), surface what needs attention (Brief) and plan the day (Day)." },
    { id: "agentic_harbor_loop", name: "Agentic, in the assistant's loop", group: "agentic", spec: "02", facets: ["family", "lane"], loopOf: "agentic", what: "The assistant-lane agentic cases run through the assistant's own loop, which re-prompts and repairs mistakes. It has no neutral lane." },
    { id: "in_assistant_findings", name: "What it found", group: "assistant", spec: "08", facets: ["family", "profile", "surface"], what: "Item by item: what the assistant raised in the Brief, the inbox and the to-dos, what it did by itself, and what it missed or raised wrongly." },
    { id: "quality", name: "Output quality", group: "quality", spec: "03", facets: ["family"], what: "Is the writing readable, are documents understood, and are facts kept without invention?" },
    { id: "preferences", name: "Preferences", group: "preferences", spec: "14", facets: ["family", "lane"], what: "Are standing preferences followed later, overridden once when asked, replaced by newer ones, and never stretched to waive a confirmation?" },
    { id: "long_session", name: "Long sessions", group: "longsession", spec: "02", facets: ["family", "lane"], what: "Does quality hold from the first turns of a 40-turn conversation to the last?" },
    { id: "memory", name: "Memory", group: "memory", spec: "02", facets: ["family", "lane"], what: "Are facts remembered and recalled correctly across sessions, and forgotten when asked?" },
    { id: "preferences_harbor_loop", name: "Preferences, in the assistant's loop", group: "preferences", spec: "14", facets: [], loopOf: "preferences", what: "The preference cases through the assistant's own loop." },
    { id: "long_session_harbor_loop", name: "Long sessions, in the assistant's loop", group: "longsession", spec: "02", facets: [], loopOf: "long_session", what: "The long-session cases through the assistant's own loop." },
    { id: "memory_harbor_loop", name: "Memory, in the assistant's loop", group: "memory", spec: "02", facets: [], loopOf: "memory", what: "The memory cases through the assistant's own loop." },
    { id: "vision", name: "Vision", group: "vision", spec: "05", facets: ["family"], what: "Reading screenshots, documents, charts and photos. Models without image input are not applicable, never zero." },
    { id: "audio", name: "Audio input", group: "audio", spec: "05", facets: ["family"], what: "Understanding spoken requests. There is no speech output and no video." },
    { id: "app_control", name: "On-screen app control", group: "screen", spec: "11", facets: ["family"], what: "Operating real app screens through the accessibility tree: find, click, type, confirm." },
    { id: "sidekick", name: "Coding and terminal", group: "screen", spec: "12", facets: ["family"], what: "Light coding and terminal tasks: read, edit, run, and stop before anything destructive." },
    { id: "perf", name: "Performance", group: "perf", spec: "04", facets: ["test"], what: "Speed, memory and energy at seven context sizes, load time, and the simulated day." }
  ];

  /* Words for facet values the store writes. Unknown values fall back to the raw value, tidied. */
  const facetNames = {
    lane: { assistant: "Assistant lane", neutral: "Neutral lane" },
    family: { select: "Select", parallel: "Parallel", sequential: "Sequential", mixed: "Mixed", pev: "Plan, execute, verify", recovery: "Recovery", abstain: "Abstain", summary: "Summaries", point: "Key points", extraction: "Extraction", numeric: "Numbers", writing: "Writing",
      cross: "Across apps", disrupt: "Interruptions", flow: "Multi-step flows", form: "Forms", ground: "Finding the control",
      commit_msg: "Commit messages", diff_review: "Reviewing a diff", explain_error: "Explaining an error", fix_bug: "Fixing a bug", git: "Git",
      act: "Done by itself", brief: "Brief cards", brief_todos: "To-dos from the Brief", inbox: "Needs attention", window: "Questions about the day",
      long_session: "Long session", memory: "Memory", preferences: "Preferences" },
    surface: { ask: "Ask", brief: "Brief", day: "Day" },
    profile: { student: "Student", tech: "Tech worker", "tech-worker": "Tech worker", mixed: "Student who works", "student-worker": "Student who works" },
    band: { early: "Early turns", late: "Late turns" }
  };

  /* Every status the store can emit, with its words and icon. A status is always text plus an icon, never a blank or a 0. */
  const statuses = {
    ok: { text: "Measured", icon: "check" },
    does_not_fit: { text: "Does not fit", icon: "mem", bad: true, tip: "Peak memory is over the fit line. Never ranked or scored." },
    out_of_memory: { text: "Out of memory", icon: "mem", bad: true, tip: "Fits at short context but was killed at this length." },
    beyond_window: { text: "Beyond window", icon: "window", tip: "Longer than the model's context window." },
    not_applicable: { text: "Not applicable", icon: "dash", tip: "The model lacks the modality, or the rule does not apply." },
    not_applicable_cloud: { text: "Not applicable (cloud)", icon: "dash", tip: "Hosted model: no device, memory, energy or fit verdict." },
    not_applicable_local: { text: "Not applicable (on device)", icon: "dash", tip: "Cloud cost and latency apply to hosted references only." },
    not_applicable_iphone: { text: "Not in version 1 on the iPhone", icon: "dash", tip: "This measure is Mac only in version 1." },
    unsupported_by_runtime: { text: "Not supported by runtime", icon: "dash", tip: "The runtime cannot run this." },
    excluded: { text: "Excluded", icon: "cross", tip: "Engine, transport or provider fault after one retry; not counted as a model failure." },
    failed: { text: "Run failed", icon: "cross", bad: true, tip: "The harness or runtime crashed or hung. The run is broken, not the model." },
    not_run: { text: "Not run yet", icon: "clock", tip: "Queued or skipped. Never treated as zero." },
    invalid: { text: "Invalid run", icon: "warn", bad: true, tip: "More than 2% of trials were excluded, so the entry is listed but not ranked." },
    not_measured: { text: "Fit not measured", icon: "clock", tip: "No fit verdict yet, so the entry is listed but not ranked." },
    not_run_here: { text: "Not run here", icon: "dash", tip: "The store left this empty on purpose; its reason is in the tooltip." },
    not_in_board: { text: "Not in this board yet", icon: "dash", tip: "This test area has no published results in this board version." },
    no_value: { text: "No value", icon: "dash", tip: "The store has the row but no value." }
  };

  const tags = { better: "better", worse: "worse", no_clear_difference: "no clear difference" };

  EDAT.registry = {
    groups, metrics, tiers, facetNames, statuses, tags,
    metric: Object.fromEntries(metrics.map((m) => [m.id, m])),
    group: Object.fromEntries(groups.map((g) => [g.id, g])),
    tier: Object.fromEntries(tiers.map((t) => [t.id, t])),
    /* the always-on columns beside every agentic score */
    fixed: ["agentic_score", "assistant_loop_score", "repair_gain", "unsafe", "leaks"],
    facetLabel(facet) {
      if (!facet) return "All";
      const [k, v] = facet.split("=");
      return (facetNames[k] && facetNames[k][v]) || (v || k).replace(/[-_]/g, " ").replace(/^./, (c) => c.toUpperCase());
    }
  };
})();
