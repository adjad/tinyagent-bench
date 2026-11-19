/* Methodology (#/methodology): how every number is made, in plain words, with a sticky table of contents.
 * Facts that change with the data (board version, dates, schema ids, runtime versions, harness commits, fit lines,
 * reference names, sampling sources) are read from the export; the status table is read from the registry. */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store, CFG = EDAT.config;
  const { esc, fmt, I } = U;

  const SAMPLING = {
    author_recommended: "the model author's recommended settings",
    fixed_sensitivity: "a fixed setting, for a sensitivity run",
    engine_default: "the engine's defaults",
    provider_default: "the provider's defaults"
  };
  const uniq = (xs) => [...new Set(xs.filter((x) => x != null && x !== ""))];
  const list = (xs) => (xs.length <= 1 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]);
  const code = (s) => `<span class="mono">${esc(s)}</span>`;

  const TOC = [
    ["answers", "What the benchmark answers"],
    ["devices", "Devices and runtimes"],
    ["fit", "The fit gate"],
    ["measures", "How each number is measured"],
    ["safety", "Unsafe actions and privacy leaks"],
    ["uncertainty", "Uncertainty and ties"],
    ["fairness", "Fairness rules"],
    ["references", "The two pinned references"],
    ["cloud", "Cloud references"],
    ["missing", "How missing results are shown"],
    ["versions", "Versions"],
    ["harness", "Where the harness comes from"],
    ["reproduce", "How to reproduce a run"],
    ["left-out", "What is left out"]
  ];
  const h2 = (id, text) => `<h2 id="${id}" tabindex="-1">${esc(text)}</h2>`;
  const h3 = (id, text) => `<h3 id="${id}" tabindex="-1">${esc(text)}</h3>`;

  async function render(ctx) {
    const bi = ctx.boardInfo, cloud = ctx.cloud || null;
    const boards = bi ? await ST.loadAllDevices(bi.version) : [];
    const sample = !!(ctx.index && ctx.index.sample) || !!(bi && bi.sample) || boards.some((b) => b.sample);
    const N = esc(CFG.name);
    const anchor = (id, text) => `<a href="${ctx.linkHere({ at: id })}" data-anchor="${id}">${esc(text)}</a>`;
    const page = (path, text, params) => `<a href="${ctx.link(path, params)}">${esc(text)}</a>`;
    const levels = list(CFG.contextLabels.map(esc));

    /* ---------- facts read from the export ---------- */
    const devFacts = boards.map((b) => {
      const local = b.entries.filter((e) => !e.cloud);
      const budget = local.map((e) => (e.run.fit || {}).budget_bytes).find(Boolean) || null;
      const rts = uniq(local.map((e) => e.variant.runtime)).map((r) => {
        const vs = uniq(local.filter((e) => e.variant.runtime === r).map((e) => e.variant.runtime_version));
        return (CFG.runtimes[r] || r) + (vs.length ? " " + vs.join(", ") : "");
      });
      return { b, d: b.device, budget, rts, commits: uniq(b.entries.map((e) => e.run.harness_commit)), sampling: uniq(b.entries.map((e) => e.variant.sampling_source)) };
    });
    const allSampling = uniq(devFacts.flatMap((f) => f.sampling).concat(cloud ? cloud.entries.map((e) => e.variant.sampling_source) : []));
    const refNames = (role) => uniq(boards.map((b) => (b.refEntry(role) || {}).name));
    const tunedNames = refNames("tuned"), baseNames = refNames("base");
    const cloudModels = cloud ? [...cloud.models.values()] : [];
    const cloudDates = cloud ? uniq(cloudModels.flatMap((m) => (m.run_date ? [m.run_date.first, m.run_date.last] : [])).concat(cloud.entries.map((e) => e.run.date)).map((d) => U.date(d))).sort() : [];
    const cloudCommits = cloud ? uniq(cloud.entries.map((e) => e.run.harness_commit)) : [];
    const cloudRts = cloud ? uniq(cloud.entries.map((e) => CFG.runtimes[e.variant.runtime] || e.variant.runtime)) : [];
    const schemas = uniq([ctx.index && ctx.index.raw && ctx.index.raw.schema].concat(boards.map((b) => b.raw.schema), cloud ? [cloud.raw.schema] : []));

    /* ---------- sections ---------- */
    const sampleNote = sample ? `<div class="note-planned" role="note" style="margin-bottom:var(--space-lg)">${I.warn}<span><b style="font-weight:500">This board is sample data.</b> Every model name and number on the site is invented to exercise the pages; nothing has been measured yet. The method below is the one real boards will follow.</span></div>` : "";

    const answers = `${h2("answers", "What the benchmark answers")}
      <p>${N} (${esc(CFG.longName)}) measures small language models, from about 0.2B to 12B parameters, used as agents on your own device. It answers one question first: which model is the best agent that fits my device, and what does it cost in memory, speed and battery?</p>
      <p>Every model runs locally on a real device. The same cases, tools and rules apply to every entry. Scores carry 95% intervals, ranks are ranges, and a missing result is shown as a result, with its reason.</p>`;

    const devicesSec = `${h2("devices", "Devices and runtimes")}
      <p>Version 1 has two reference devices: a MacBook Pro with an M5 Pro and 24 GB, running models with oMLX, and an iPhone Air with 12 GB, running them with MLX Swift. Local models run only on Apple silicon in version 1. New devices are added to the ${page("/devices", "devices list")} as they are measured.</p>
      ${devFacts.length ? `<p>In this board:</p><ul>${devFacts.map((f) => `<li><a href="${ctx.link("/device/" + U.seg(f.d.id), { d: f.d.id })}">${esc(f.d.name)}</a>${f.d.soc || f.d.os ? ", " + esc([f.d.soc, f.d.os].filter(Boolean).join(", ")) : ""}: ${esc(list(f.rts) || "no runtime recorded")}.</li>`).join("")}</ul>` : ""}`;

    const fitSec = `${h2("fit", "The fit gate")}
      <p>Only variants that fit are ranked. A variant is one model at one quantisation, on one runtime, with thinking on or off.</p>
      <ul>
        <li><b style="font-weight:500">Fits:</b> peak memory with 10k tokens of context stays within the device's fit line. Ranked.</li>
        <li><b style="font-weight:500">Small context only:</b> within the line at 2k tokens but not at 10k. Ranked, with a badge.</li>
        <li><b style="font-weight:500">Does not fit:</b> over the line even at 2k. Listed with the memory it needs, never scored or ranked.</li>
        <li><b style="font-weight:500">Fit not measured:</b> no verdict yet. Listed, not ranked.</li>
      </ul>
      <p>On the Mac the fit line is for a 16 GB Mac: what such a Mac leaves for one model, less a safety margin, checked on the 24 GB machine with memory held back. The iPhone has its own line: the most the test app held before the system ended it, less the same margin. Fit belongs to the weights, so thinking on and off share one verdict. GB means 2<sup>30</sup> bytes.</p>
      ${devFacts.some((f) => f.budget) ? `<p>Fit lines in this board: ${devFacts.filter((f) => f.budget).map((f) => `${esc(f.d.name)}, <span class="num">${fmt(U.GiB(f.budget), 1)} GB</span>`).join("; ")}.</p>` : ""}`;

    const measures = `${h2("measures", "How each number is measured")}
      <p>Each test area is its own tier with its own score. Only the agentic score leads the leaderboard; the rest are column groups and pages of their own.</p>
      ${h3("m-agentic", "Agentic tool use")}
      <p>Seven families: select, parallel, sequential, mixed, plan-execute-verify, recovery and abstain. Each case runs in two lanes, the assistant lane with the assistant's own tools and a neutral lane with plain tools no model has seen, and three trials. Each family is the mean of its two lanes; the agentic score is the mean of the seven families. A trial passes when the sandbox ends in the expected state and the call log meets its checks. No model grades another model's agentic work.</p>
      <p>Agentic scores come from two loops. The headline score uses the benchmark's own minimal loop, which sends the case to the model and takes its tool calls as they come, with no second tries, so it measures the model alone. Beside it, the same assistant-lane cases also run through the assistant's own loop, which re-prompts the model and repairs malformed or failed calls; the gap between the two is what the assistant's repairs add for that model. The second score does not exist for the neutral lane, the cloud references or the iPhone's parity subset, which run the minimal loop only.</p>
      <ul>
        <li><b style="font-weight:500">Reliability</b> (pass^3): the share of cases passed on all three trials.</li>
        <li><b style="font-weight:500">Progress:</b> partial credit for how far through a case the model got.</li>
        <li><b style="font-weight:500">Format validity:</b> the share of tool calls that parse and match their schema.</li>
        <li><b style="font-weight:500">Restraint:</b> the share of restraint cases where the model acted when it should have asked or stopped.</li>
        <li><b style="font-weight:500">Over-refusal:</b> the share of plain, safe requests the model declined.</li>
      </ul>
      <p>The iPhone runs a smaller parity subset of the agentic cases, so its intervals are wider.</p>
      ${h3("m-preferences", "Preferences")}
      <p>Instruction following is the mean of five dimensions, each about a preference the person stated earlier: still followed later, a one-off override respected once, a newer preference replacing an older one, not stretched to cases it does not cover, and never used to skip a confirmation.</p>
      ${h3("m-long", "Long sessions")}
      <p>The same kinds of task are asked early (turns 1 to 5) and late (turns 30 to 40) in one 40-turn conversation. Both are reported, with the change between them.</p>
      ${h3("m-memory", "Memory")}
      <p>Facts given in one session are asked for in a later one. The score is the share recalled correctly, without invented details.</p>
      ${h3("m-assistant", "In the assistant")}
      <p>Each entry also runs inside the assistant itself, against three synthetic people (a student, a tech worker and a student who works) with weeks of invented mail, messages, calendar, notes and files. It is scored on three surfaces: Ask (questions about the day), Brief (what needs attention, without false alarms) and Day (a valid plan that covers what is due). The assistant score is the mean across the three surfaces and three people. Actions taken without a clear ask or a confirmation are counted on their own. ${page("/profiles", "Meet the three people")}.</p>
      ${h3("m-quality", "Output quality")}
      <ul>
        <li><b style="font-weight:500">Readability</b> is 35% automatic hygiene checks (renders, fits the panel, answer first, right length, nothing leaked) and 65% a style-controlled preference from an open-weights judge. The judge counts only after it agrees with the author's blind ${page("/vote", "readability votes")}; until then readability shows the structure checks alone, labelled "structure only".</li>
        <li><b style="font-weight:500">Text understanding</b> weights core fact recall 30%, extraction 20%, faithfulness to the source 25%, resistance to traps 15% and schema validity 10%.</li>
        <li><b style="font-weight:500">Writing</b> covers drafting, rewriting and editing for the person: the points made, faithfulness, the limits kept, edit precision, and how it reads.</li>
        <li><b style="font-weight:500">Fabricated details</b> per answer and <b style="font-weight:500">visible tokens</b> per answer are shown beside the scores, never inside them. Reasoning tokens of thinking-on entries are reported separately and do not count as visible tokens.</li>
      </ul>
      ${h3("m-vision", "Vision")}
      <p>Reading screenshots, documents, receipts, tables, charts and photos, and acting on what they show. A model without image input reads "Not applicable", never zero.</p>
      ${h3("m-audio", "Audio input")}
      <p>Understanding spoken requests in English: accuracy on names, numbers and times, spoken commands, and silence or noise that should produce no words. There is no speech output and no video. A model without audio input reads "Not applicable".</p>
      ${h3("m-screen", "On-screen app control")}
      <p>The model operates invented apps through a text list of the screen's elements, as an accessibility tree would give it, with eight actions such as click, type and finish. A screenshot track runs for models with vision. Tasks are graded on the apps' final state, with no judge model. The screen score is the mean over six capability families; guard cases (confirmations, injections, secrets) count toward restraint instead.</p>
      ${h3("m-sidekick", "Coding and terminal")}
      <p>Nine everyday helper tasks: explain an error, write or fix a small function, a shell one-liner, a git command, a regular expression, a unit test, a commit message, and reading a short diff. Code and commands run only inside a throwaway sandbox with no network, and are graded on their result. A destructive or unsafe proposal fails the trial and counts toward restraint.</p>
      ${h3("m-perf", "Performance")}
      <ul>
        <li>Time to first token, prefill speed, decode speed and peak memory at seven context sizes: ${levels} tokens. The leaderboard shows speed at 2k and memory at 10k.</li>
        <li>Peak memory is the process's physical footprint, runtime included.</li>
        <li>Load time, from storage after a restart and with the file cached.</li>
        <li>Energy is joules per completed task, Mac only in version 1, from the on-board power counters, with prefill and decode separated by paired requests. The iPhone cannot measure energy per phase, so its decode energy and battery share are estimates from the battery gauge and are labelled so.</li>
      </ul>
      ${h3("m-day", "The simulated day")}
      <p>Forty scripted tasks across a working day, with the model kept loaded. It reports the share of a full battery the day used, the share of tasks finished within a time budget, idle power with the model loaded (Mac only), the answer time after the runtime unloaded an idle model, and whether the model still fits with a standard set of background apps holding memory.</p>`;

    const safety = `${h2("safety", "Unsafe actions and privacy leaks")}
      <p>Unsafe actions are forbidden calls (counted even when blocked), effects without a confirmation, skipped confirmations and followed injections. Privacy leaks are private details sent somewhere the task did not need them, or to the wrong person. Both are counted per 100 trials, with a 95% interval for the share of trials that had one.</p>
      <p>They sit beside every agentic and assistant score and are never blended into any score, so a careless model cannot buy a high one.</p>`;

    const uncertainty = `${h2("uncertainty", "Uncertainty and ties")}
      <ul>
        <li><b style="font-weight:500">Pass rates</b> carry a Wilson 95% interval. A case-clustered bootstrap interval is kept beside it.</li>
        <li><b style="font-weight:500">Means</b> (progress, scores per answer) carry a 95% interval from a bootstrap over cases.</li>
        <li><b style="font-weight:500">Differences from each reference</b> come from a paired bootstrap over the cases both entries ran. A difference is tagged "better" or "worse" only when its interval leaves out zero; otherwise it reads "no clear difference". Rare events, such as unsafe actions, also show the ratio.</li>
        <li><b style="font-weight:500">Ranks are ranges.</b> An entry's best rank counts only the entries whose intervals sit wholly above its own; its worst counts every entry that might be ahead. Entries with overlapping intervals share ranks.</li>
        <li>Where the store has no paired difference, the site estimates one from the two intervals (the margin is the square root of the sum of their squared half-widths) and marks it ≈. Speed and memory are exact measures and get a plain difference with no tag.</li>
      </ul>`;

    const fairness = `${h2("fairness", "Fairness rules")}
      <ul>
        <li>Thinking on and thinking off are separate entries, each ranked on its own. The mode the assistant would ship is marked.</li>
        <li>Every case is in English.</li>
        <li>Every entry gets the same cases, prompts, tools and stop rules, each with its own chat template.</li>
        <li>Sampling settings are recorded with their source.${allSampling.length ? ` In this board: ${esc(list(allSampling.map((s) => SAMPLING[s] || s.replace(/_/g, " "))))}.` : ""}</li>
        <li>Results are never merged or compared across board versions, harness commits or sampling classes.</li>
        <li>Engine, transport and provider faults are retried once and then excluded, not counted as model failures. An entry with more than 2% of its trials excluded is listed as an invalid run and not ranked.</li>
        <li>Held-out cases are never published, only their aggregates. Trajectories on the site come from the development split.</li>
      </ul>`;

    const references = `${h2("references", "The two pinned references")}
      <p>Two 4B entries are pinned to every leaderboard: a tuned reference and the base model it was tuned from. Both run with thinking off and are ranked like everyone else. Every score can be read as a difference from each of them.${tunedNames.length || baseNames.length ? ` In this board the tuned reference is ${esc(list(tunedNames)) || "not set"} and the base reference is ${esc(list(baseNames)) || "not set"}.` : ""}</p>
      <p><b style="font-weight:500">Disclosure.</b> The tuned reference was fine-tuned on data from real-life testing of the assistant, not on the benchmark's questions. An overlap check between that training data and the held-out questions is part of the method.</p>`;

    const cloudSec = `${h2("cloud", "Cloud references")}
      <p>Six hosted models run the same cases through a model router${cloudRts.length ? ` (${esc(list(cloudRts))})` : ""}, to show how far a local entry is from a hosted one.${cloud ? ` This board has ${cloudModels.length}.` : " This board has none."}</p>
      <ul>
        <li>They are references, not contestants: never ranked, and shown apart from the on-device entries.</li>
        <li>They have no device, memory, load time, energy or fit verdict. In their place they report cost per completed task, tokens per task and network latency, which includes the network and the provider's queue.</li>
        <li>Their cost is never put on one axis with on-device cost.</li>
        <li>Hosted models change without notice, so every cloud result carries its run date.${cloudDates.length ? ` Runs in this board are dated ${esc(cloudDates[0])}${cloudDates.length > 1 ? " to " + esc(cloudDates[cloudDates.length - 1]) : ""}.` : ""}</li>
      </ul>`;

    const statusRows = Object.entries(R.statuses).map(([k, s]) => `<tr><td>${U.status(k)}</td><td>${esc(s.tip || (k === "ok" ? "The value was measured and is shown with its interval." : ""))}</td><td>${code(k)}</td></tr>`).join("");
    const missing = `${h2("missing", "How missing results are shown")}
      <p>A missing result is never a blank or a zero. Each reason has its own words and icon.</p>
      <div class="table-wrap"><table class="data"><caption class="visually-hidden">Every status a cell can show</caption><thead><tr><th scope="col">Shown as</th><th scope="col">What it means</th><th scope="col">Store word</th></tr></thead><tbody>${statusRows}</tbody></table></div>
      <p>Entries also carry badges: <span class="fitbadge">${I.mem}Small context only</span>, <span class="fitbadge no">${I.mem}Does not fit</span>, <span class="fitbadge no">${I.warn}Invalid run</span> and <span class="fitbadge cloud">${I.cloud}Cloud reference</span>.</p>`;

    const versionRows = devFacts.map((f) => `<tr><td>${esc(f.d.name)}</td><td>${esc(f.b.version)}</td><td>${esc(U.date(f.b.generatedAt))}</td><td>${esc(list(f.rts)) || U.status("no_value")}</td><td>${f.commits.length ? f.commits.map(code).join(", ") : U.status("no_value")}</td></tr>`).join("")
      + (cloud ? `<tr><td>Cloud references</td><td>${esc(cloud.version)}</td><td>${esc(U.date(cloud.generatedAt))}</td><td>${esc(list(cloudRts)) || U.status("no_value")}</td><td>${cloudCommits.length ? cloudCommits.map(code).join(", ") : U.status("no_value")}</td></tr>` : "");
    const versions = `${h2("versions", "Versions")}
      <p>Every result records the board version, the harness commit, the runtime and its version, the model's weights hash, the device and the date.${bi ? ` This page describes board ${code(bi.version)}, generated ${esc(U.date(bi.generatedAt))}.` : " No board is published yet."}${sample ? " It is sample data." : ""}</p>
      ${versionRows ? `<div class="table-wrap"><table class="data"><caption class="visually-hidden">Versions in this board</caption><thead><tr><th scope="col">Results</th><th scope="col">Board</th><th scope="col">Generated</th><th scope="col">Runtime</th><th scope="col">Harness commit</th></tr></thead><tbody>${versionRows}</tbody></table></div>` : ""}
      ${schemas.length ? `<p>File formats: ${schemas.map(code).join(", ")}.</p>` : ""}`;

    const harness = `${h2("harness", "Where the harness comes from")}
      <p>The test harness was imported from the repository of ${esc(CFG.harnessOrigin)}, an on-device assistant. The assistant lane's tools and the in-assistant surfaces come from it. ${N} runs them unchanged for every entry, and adds the neutral lane so the assistant's own tools give no model a home advantage.</p>`;

    const reproduce = `${h2("reproduce", "How to reproduce a run")}
      <p>The code is at <a href="${esc(CFG.repository)}">${esc(CFG.repository.replace(/^https?:\/\//, ""))}</a>. Results go through one store:</p>
      <ol>
        <li>Write records with the writer, ${code("edat.results.writer.RunWriter")}, or convert existing harness output with ${code("python -m edat.results convert")}.</li>
        <li>Check the finished run: ${code("python -m edat.results validate <run dir>")}.</li>
        <li>Export it to the website files: ${code("python -m edat.results export <run dir> --tuned <model id> --base <model id> --site-dir site/data")}.</li>
        <li>Serve the ${code("site/")} folder with any static file server, for example ${code("python3 -m http.server")} from inside it. Opening the page straight from disk does not work, because browsers refuse to load the data files from there.</li>
      </ol>
      <p>Every command exits with 0 when it worked, 1 for invalid or damaged input, 2 when it refuses to combine results that are not comparable, and 3 for a usage or conversion error.</p>`;

    const leftOut = `${h2("left-out", "What is left out")}
      <ul>
        <li>Video.</li>
        <li>Speech output. Audio input is tested; spoken replies are not.</li>
        <li>Embedding and reranker models. They are not tested as models; when one is loaded beside the model under test, its memory and energy are recorded so they are not blamed on that model.</li>
        <li>Languages other than English.</li>
      </ul>`;

    const html = `<div class="container">
      <div class="page-head"><div><h1>Methodology</h1><p>How each number on ${N} is measured, and the rules every run follows.</p></div></div>
      ${sampleNote}
      <div class="doc">
        <nav class="toc" aria-label="On this page"><ol>${TOC.map(([id, t]) => `<li>${anchor(id, t)}</li>`).join("")}</ol></nav>
        <article class="prose">${answers}${devicesSec}${fitSec}${measures}${safety}${uncertainty}${fairness}${references}${cloudSec}${missing}${versions}${harness}${reproduce}${leftOut}</article>
      </div>
    </div>`;
    return { title: "Methodology", html };
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.methodology = { render };
})();
