/* Test explorer, one test area, and one case's trajectory.
 *   #/tests                                  every test area: what it measures, whether this board has it, cases published
 *   #/tests/<tier>?m=<entry key>             one area's scores for one entry, safety beside them, and its published cases
 *   #/case/<tier>/<case id>?m=<key>&t=<n>    one trial step by step, beside the checks it was graded against
 * Trajectories are the development split only; held-out cases never appear, only their aggregates in the scores.
 * The export has no case catalogue yet (prompt, tools offered, expected calls, check descriptions), so the case page
 * shows what the trajectory carries and says plainly what is missing. */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store, C = EDAT.charts;
  const { esc, fmt, I } = U;

  /* ---------- small lookups ---------- */
  const HEADLINE = { agentic: "agentic_score", in_assistant: "assistant_score" };
  const TOTALS = ["agentic_score", "assistant_score", "pass1", "pass_all_trials", "score_mean", "format_ok", "steps", "wall_s", "fabricated_atoms", "visible_tokens", "restraint_violation", "over_refusal"];
  const TOTAL_NAMES = { pass1: "Pass rate", pass_all_trials: "Passed every trial", score_mean: "Mean score", agentic_score: "Agentic score", assistant_score: "Assistant score" };
  const PERF_CURVES = ["decode_tps", "prefill_tps", "ttft_s", "footprint_peak_bytes", "e2e_s"];
  const nameOf = (e) => e.name + (e.thinkingText ? ", " + e.thinkingText : "");

  const regId = (tier, metric, facet) => {
    const m = R.metrics.find((x) => x.src && x.src.r === tier && x.src.m === metric && (x.src.f || null) === (facet || null));
    return m ? m.id : null;
  };
  const headMetric = (board, tier) => [HEADLINE[tier], "pass1", "score_mean"].filter(Boolean).find((m) => board.has(tier, m)) || null;

  /* one result cell: the registry's reading when the measure has an id, otherwise the same rules on the store's row */
  function read(entry, tier, metric, facet) {
    if (!entry.cloud && !entry.scored) return { status: entry.group === "does_not_fit" ? "does_not_fit" : "not_run", value: null };
    const id = regId(tier, metric, facet);
    if (id) { const c = ST.value(entry, id); const m = R.metric[id]; return Object.assign({ unit: m.unit, dp: m.dp, name: m.name }, c); }
    const b = entry.board, rec = b.result(entry.key, tier, metric, facet);
    if (!rec) return { status: b.has(tier, metric, facet) ? "not_run" : "not_in_board", value: null };
    if (rec.status && rec.status !== "ok") return { status: rec.status, value: null, rec };
    if (rec.value == null) return { status: "no_value", value: null, rec };
    const iv = rec.interval && rec.interval_kind !== "none" && rec.interval.length === 2 ? rec.interval : null;
    return { status: "ok", value: rec.value, interval: iv, half: iv ? (iv[1] - iv[0]) / 2 : null, n: rec.n_cases, trials: rec.n_trials, unit: rec.unit || "", dp: 1, rec };
  }
  const metricName = (tier, metric) => { const id = regId(tier, metric, null); return id ? R.metric[id].name : TOTAL_NAMES[metric] || metric.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()); };
  function valHtml(c) {
    if (c.status !== "ok") return U.statusFor(c);
    const dp = c.dp != null ? c.dp : 1;
    const unit = c.unit === "%" ? "%" : c.unit ? " " + c.unit : "";
    return `<span class="num">${fmt(c.value, dp)}${esc(unit)}</span>${c.half != null && c.half > 0 ? `<span class="muted xsmall"> ±${fmt(c.half, dp)}</span>` : ""}`;
  }
  /* safety counts for any tier, in the shape U.safetyCell reads; never part of a score */
  function safetyRead(entry, tier, key) {
    if (!entry.cloud && !entry.scored) return { status: entry.group === "does_not_fit" ? "does_not_fit" : "not_run" };
    const b = entry.board, rec = b.safety(entry.key, tier);
    if (!rec) return { status: b.has("safety§" + tier) ? "not_run" : "not_in_board" };
    if (rec[key] == null) return { status: "not_applicable" };
    const leak = key.startsWith("leak");
    return { status: "ok", value: rec[key], trials: rec.trials, count: leak ? rec.leak_trials : rec.unsafe_trials, rateInterval: leak ? rec.leak_interval : rec.unsafe_interval };
  }
  const SAFETY = [["unsafe_per_100", "Unsafe actions"], ["leaks_per_100", "Privacy leaks"]];

  /* entries for the picker: ranked by the area's headline first, then listed, then the ones that do not fit */
  function orderedEntries(board, tier) {
    const hm = headMetric(board, tier);
    const v = (e) => { if (!hm) return -1; const c = read(e, tier, hm, null); return c.status === "ok" ? c.value : -1; };
    const band = (e) => (e.group === "ranked" ? 0 : e.group === "does_not_fit" ? 2 : 1);
    return board.entries.filter((e) => !e.cloud).slice().sort((a, b) => band(a) - band(b) || v(b) - v(a) || a.key.localeCompare(b.key));
  }
  const optLabel = (e) => nameOf(e) + (e.group === "does_not_fit" ? " (does not fit)" : e.group === "invalid" ? " (invalid run)" : e.group === "not_measured" ? " (fit not measured)" : "");
  function pickEntry(ctx, list, prefer) {
    const want = ctx.q.get("m");
    return list.find((e) => e.key === want && (!prefer || prefer(e))) || list.find((e) => e.group === "ranked" && (!prefer || prefer(e))) || list.find((e) => !prefer || prefer(e)) || list[0] || null;
  }
  const median = (xs) => { const s = xs.filter((x) => x != null).sort((a, b) => a - b); if (!s.length) return null; const k = Math.floor(s.length / 2); return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2; };

  /* case ids look like lane-family-nn; only read them that way when both words are known */
  function parseCase(id) {
    const p = String(id).split("-");
    if (p.length < 3 || !/^\d+$/.test(p[p.length - 1])) return null;
    const lane = p[0], fam = p.slice(1, -1).join("-"), F = R.facetNames;
    if (!(F.lane && F.lane[lane] && F.family && F.family[fam])) return null;
    return { lane, family: fam, laneLabel: F.lane[lane], famLabel: F.family[fam] };
  }

  /* ---------- outcomes, never colour alone ---------- */
  const OUT = {
    pass: { pip: "pass", pf: "pass", icon: "check", text: "Pass" },
    fail: { pip: "fail", pf: "fail", icon: "cross", text: "Fail" },
    unsafe: { pip: "fail", pf: "unsafe-call", icon: "warn", text: "Unsafe call" }
  };
  const outOf = (o) => OUT[o] || { pip: "", pf: "", icon: "dash", text: o ? String(o).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "Not published" };
  const badge = (o) => { const x = outOf(o); return x.pf ? `<span class="pf ${x.pf}">${I[x.icon]}${esc(x.text)}</span>` : U.status("no_value", { text: x.text }); };
  const legend = `<p class="legend" aria-label="Trial marks">${badge("pass")}${badge("fail")}${badge("unsafe")}<span class="cell-status">${I.dash}Not published</span></p>`;
  function pipLinks(trials, byTrial, hrefFor) {
    return `<span class="pips" role="group" aria-label="Trials">${trials.map((n) => {
      const t = byTrial.get(n);
      if (!t) return `<span title="Trial ${n}: not published" aria-label="Trial ${n}: not published" role="img">${I.dash}</span>`;
      const x = outOf(t.outcome), lbl = `Trial ${n}: ${x.text.toLowerCase()}`;
      return `<a href="${hrefFor(n)}" aria-label="${esc(lbl + ". Open the trajectory")}" title="${esc(lbl)}"><span class="${x.pip}">${I[x.icon]}</span></a>`;
    }).join("")}</span>`;
  }

  /* check words: the store writes short codes; unknown codes are tidied, never hidden */
  const CHECK_WORDS = {
    wrong_answer: "Wrong answer", correct_answer: "Correct answer", unsafe_call: "Unsafe call", privacy_leak: "Privacy leak",
    wrong_tool: "Wrong tool", missing_call: "Missing call", extra_call: "Needless call", needless_call: "Needless call",
    bad_args: "Bad arguments", wrong_args: "Wrong arguments", missing_args: "Missing arguments", invalid_format: "Invalid call format",
    format_error: "Invalid call format", no_verify: "Did not check the end state", gave_up: "Gave up after an error",
    skipped_step: "Skipped a step", serial_calls: "Independent calls made one at a time", fabricated: "Fabricated detail",
    over_refusal: "Refused a safe request", unconfirmed_action: "Acted without confirming", wrong_recipient: "Wrong recipient"
  };
  const checkText = (t) => { const s = String(t == null ? "" : t); return CHECK_WORDS[s] || (/^[a-z0-9_]+$/.test(s) ? s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : s) || "Unnamed check"; };
  const WHO = { model: "Model", user: "User", tool: "Tool", system: "System", assistant: "Model" };
  const toText = (v) => (v == null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2));
  const fmtArgs = (o) => {
    if (o == null) return "()";
    if (typeof o !== "object" || Array.isArray(o)) return "(" + esc(JSON.stringify(o)) + ")";
    const ks = Object.keys(o);
    if (!ks.length) return "()";
    return "(\n" + ks.map((k) => `  <span class="key-n">${esc(k)}</span>: <span class="str">${esc(JSON.stringify(o[k]))}</span>`).join(",\n") + "\n)";
  };

  const crumbs = (ctx, parts) => `<p class="crumbs"><a href="${ctx.link("/tests")}">Test explorer</a>${parts.map((p) => " / " + p).join("")}</p>`;
  const tierLink = (ctx, tier, params) => ctx.link("/tests/" + U.seg(tier), params || {});
  const caseLink = (ctx, tier, id, params) => ctx.link("/case/" + U.seg(tier) + "/" + U.seg(id), params || {});
  const tierName = (tier) => (R.tier[tier] ? R.tier[tier].name : String(tier).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()));
  const heldOutNote = `<p class="note-planned">${I.lock}<span>Held-out cases never appear on this site, only their aggregates inside the scores. Every trajectory shown is from the development split.</span></p>`;
  const perfHas = (board) => board.has("perf") || PERF_CURVES.some((m) => board.has("curve§" + m));

  async function trajectories(ctx) {
    try { return await ST.loadTrajectories(ctx.boardInfo.version, ctx.devId); }
    catch (e) { return { failed: e }; }
  }
  const trajError = (err) => U.empty("The trajectories did not load", `${esc(err.message || "The trajectories file could not be read.")} Scores above are unaffected.`);

  /* =========================================================
   * #/tests
   * ========================================================= */
  async function pageExplorer(ctx) {
    const board = ctx.device, d = board.device;
    const tr = await trajectories(ctx), list = Array.isArray(tr) ? tr : [];
    const casesBy = new Map();
    list.forEach((t) => { (casesBy.get(t.tier) || casesBy.set(t.tier, new Set()).get(t.tier)).add(t.case_id); });
    const known = R.tiers.map((t) => t.id);
    const extra = board.tiers().filter((t) => !known.includes(t));
    const rows = known.concat(extra).map((id) => {
      const t = R.tier[id];
      const isPerf = id === "perf", has = isPerf ? perfHas(board) : board.has(id);
      const hm = has && !isPerf ? headMetric(board, id) : null;
      const nEntries = isPerf ? board.entries.filter((e) => PERF_CURVES.some((m) => board.curve(e.key, m))).length : hm ? board.entries.filter((e) => board.result(e.key, id, hm, null)).length : 0;
      const nCases = (casesBy.get(id) || new Set()).size;
      const inBoard = has ? `<span class="cell-status">${I.check}${nEntries} ${nEntries === 1 ? "entry" : "entries"}</span>` : U.status("not_in_board");
      const cases = !has ? U.status("not_in_board") : isPerf ? `<span class="cell-status">${I.dash}Measured, not graded per case</span>` : !Array.isArray(tr) ? U.status("failed", { text: "Did not load" }) : nCases ? `<span class="num">${nCases}</span>` : U.status("no_value", { text: "None published yet" });
      return `<tr${has ? "" : ' class="row-dim"'}><th scope="row"><span class="mname"><a href="${tierLink(ctx, id)}">${esc(tierName(id))}</a></span><span class="vline">${t ? "Spec " + esc(t.spec) : "Not described yet"}</span></th><td class="small" style="min-width:24ch;max-width:52ch;color:var(--color-ink-2)">${t ? esc(t.what) : "This test area is in the board but the site has no description for it yet."}</td><td>${inBoard}</td><td class="num">${cases}</td></tr>`;
    }).join("");
    const html = `<div class="container">
      <div class="page-head"><div><h1>Test explorer</h1><p>Every score comes from cases you can read. Open a test area to see its scores for one entry and the cases behind them, then a case to follow one trial step by step beside the checks that graded it. English cases only.</p></div>
        ${U.tray("d", "Device", ctx.boardInfo.devices.map((x) => ({ v: x.id, l: x.short })), ctx.devId)}</div>
      ${heldOutNote}
      <div class="table-wrap" role="region" aria-label="Test areas" tabindex="0"><table class="data"><caption class="visually-hidden">Test areas on ${esc(d.name)}, board ${esc(board.version)}</caption>
        <thead><tr><th scope="col">Test area</th><th scope="col">What it measures</th><th scope="col">In this board</th><th scope="col" class="num">Cases with trajectories</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${!Array.isArray(tr) ? `<div style="margin-top:var(--space-md)">${trajError(tr.failed)}</div>` : ""}
      <p class="small muted" style="margin-top:8px">Results on ${esc(d.name)}, board ${esc(board.version)}. A test area marked "Not in this board yet" is planned and has no published results here. Trajectories are published for a few development cases per area; the counts above are those, not the size of the test set. <a class="link" href="${ctx.link("/methodology")}">How cases are built and graded</a>.</p>
    </div>`;
    return { title: "Test explorer", html };
  }

  /* =========================================================
   * #/tests/<tier>
   * ========================================================= */
  function scoreFacts(entry, tier) {
    const board = entry.board;
    const metrics = TOTALS.concat(R.metrics.filter((m) => m.src && m.src.r === tier && !m.src.f && !m.planned).map((m) => m.src.m)).filter((m, i, a) => a.indexOf(m) === i && board.has(tier, m));
    const pairs = metrics.map((m) => [metricName(tier, m), valHtml(read(entry, tier, m, null))]);
    SAFETY.forEach(([k, n]) => { const c = safetyRead(entry, tier, k); if (c.status !== "not_in_board" || board.has("safety§" + tier)) pairs.push([n + " (on their own)", c.status === "ok" ? U.safetyCell(c, { name: n }) : U.statusFor(c)]); });
    return pairs.length ? U.facts(pairs) : "";
  }

  function facetChart(entry, tier, kind, title) {
    const board = entry.board, facets = board.facets(tier, kind).filter((f) => board.has(tier, "pass1", f));
    if (!facets.length) return "";
    const ranked = board.entries.filter((e) => e.group === "ranked");
    const rows = facets.map((f) => {
      const c = read(entry, tier, "pass1", f), label = R.facetLabel(f);
      const ref = median(ranked.map((e) => { const x = read(e, tier, "pass1", f); return x.status === "ok" ? x.value : null; }));
      return { f, c, label, v: c.status === "ok" ? c.value : null, ci: c.half || 0, ref, missing: (R.statuses[c.status] || {}).text || "No value",
        tip: c.status === "ok" ? `${label}: ${fmt(c.value, 1)}% ±${fmt(c.half || 0, 1)}${c.n ? ", " + c.n + " cases" : ""}${ref != null ? "\nMedian of ranked entries " + fmt(ref, 1) + "%" : ""}` : label };
    });
    const chart = C.dotRows({ rows, domain: [0, 100], step: 20, d: 1, aria: `${title} for ${nameOf(entry)}`, labelW: 180 });
    return `<figure style="margin:0"><p class="chart-title">${esc(title)}</p><p class="chart-sub">Pass rate, %, with 95% intervals</p>${chart}
      ${U.asTable([kind === "profile" ? "Person" : "Breakdown", "Pass rate %", "± 95%", "Cases", "Median of ranked entries"], rows.map((r) => [esc(r.label), r.c.status === "ok" ? fmt(r.c.value, 1) : U.statusFor(r.c), r.c.status === "ok" && r.c.half != null ? fmt(r.c.half, 1) : "", r.c.n != null ? fmt(r.c.n, 0) : "", r.ref != null ? fmt(r.ref, 1) : ""]), title)}</figure>`;
  }
  const facetHow = (entry) => U.howTo([
    `Each row is one part of this test area for ${esc(nameOf(entry))}. The dot is its pass rate; further right is better.`,
    "The whisker is the 95% interval. When two whiskers overlap, the two parts may be equally hard for this entry.",
    "The short upright tick is the median of the ranked entries on this device, so you can see where this entry sits among its peers.",
    "Unsafe actions and privacy leaks are counted on their own beside the score above. They are never part of a pass rate."
  ]);

  /* surface × person: the export has each surface and each person separately, not each pair */
  function surfaceProfileTable(ctx, entry) {
    const board = entry.board, T = "in_assistant";
    const surf = board.facets(T, "surface"), prof = board.facets(T, "profile");
    if (!surf.length && !prof.length) return "";
    const split = U.status("no_value", { text: "Not split yet", tip: "The export has each surface and each person separately, not each pair." });
    const cell = (f) => valHtml(read(entry, T, "pass1", f));
    const head = `<tr><th scope="col">Surface</th>${prof.map((p) => `<th scope="col" class="num">${esc(R.facetLabel(p))}<span class="vline">Synthetic person</span></th>`).join("")}<th scope="col" class="num">All people</th></tr>`;
    const body = surf.map((s) => `<tr><th scope="row">${esc(R.facetLabel(s))}</th>${prof.map(() => `<td class="num">${split}</td>`).join("")}<td class="num">${cell(s)}</td></tr>`).join("")
      + `<tr><th scope="row">All surfaces</th>${prof.map((p) => `<td class="num">${cell(p)}</td>`).join("")}<td class="num">${board.has(T, "pass1") ? cell(null) : split}</td></tr>`;
    return `<div class="table-wrap" role="region" aria-label="Pass rate by surface and person" tabindex="0"><table class="data"><caption class="visually-hidden">Pass rate by surface and synthetic person for ${esc(nameOf(entry))}</caption><thead>${head}</thead><tbody>${body}</tbody></table></div>
      <p class="small muted" style="margin-top:8px">Pass rates in %, ±95% interval. The export gives each surface across all people, and each person across all surfaces; the cells for one surface and one person arrive when the store writes them. <a class="link" href="${ctx.link("/profiles", { m: entry.key })}">Who the synthetic people are</a>.</p>`;
  }

  function caseTable(ctx, tier, entry, list) {
    const byCase = new Map();
    list.forEach((t) => { (byCase.get(t.case_id) || byCase.set(t.case_id, []).get(t.case_id)).push(t); });
    const ids = [...byCase.keys()].sort();
    const parsed = new Map(ids.map((id) => [id, parseCase(id)]));
    const anyParsed = [...parsed.values()].some(Boolean);
    let passedAll = 0, withMine = 0, unsafeTrials = 0;
    const rows = ids.map((id) => {
      const all = byCase.get(id);
      const trials = [...new Set(all.map((t) => t.trial))].sort((a, b) => a - b);
      const mine = new Map(all.filter((t) => t.variant === entry.key).map((t) => [t.trial, t]));
      if (mine.size) withMine++;
      const allPass = mine.size === trials.length && [...mine.values()].every((t) => t.outcome === "pass");
      if (allPass) passedAll++;
      unsafeTrials += [...mine.values()].filter((t) => t.outcome === "unsafe").length;
      const keys = [...new Set(all.map((t) => t.variant))];
      const entriesAll = keys.filter((k) => { const ts = all.filter((t) => t.variant === k); return ts.length === trials.length && ts.every((t) => t.outcome === "pass"); }).length;
      const p = parsed.get(id);
      const pips = mine.size ? pipLinks(trials, mine, (n) => caseLink(ctx, tier, id, { m: entry.key, t: n })) : U.status("no_value", { text: "Not published for this entry" });
      return `<tr><th scope="row"><a class="mono" href="${caseLink(ctx, tier, id, { m: entry.key })}">${esc(id)}</a></th>${anyParsed ? `<td>${p ? esc(p.laneLabel) : '<span class="muted">Not in the id</span>'}</td><td>${p ? esc(p.famLabel) : '<span class="muted">Not in the id</span>'}</td>` : ""}<td>${pips}</td><td class="num">${entriesAll} of ${keys.length}<span class="vline">entries passed every trial</span></td></tr>`;
    }).join("");
    const nCols = anyParsed ? 5 : 3;
    const foot = `<tr><th scope="row">All ${ids.length} published</th><td colspan="${nCols - 1}">${withMine ? `<b class="num">${passedAll}</b> of ${withMine} cases passed on every trial by ${esc(nameOf(entry))}${unsafeTrials ? `; <span class="unsafe">${I.warn}<span class="num">${unsafeTrials}</span>&nbsp;unsafe ${unsafeTrials === 1 ? "trial" : "trials"}</span>, counted on their own` : "; no unsafe trial"}.` : `No published trial for ${esc(nameOf(entry))}.`}</td></tr>`;
    return `<div class="table-wrap" role="region" aria-label="Published cases" tabindex="0"><table class="data"><caption class="visually-hidden">Published development cases for ${esc(tierName(tier))}, with ${esc(nameOf(entry))}'s trials</caption>
      <thead><tr><th scope="col">Case</th>${anyParsed ? '<th scope="col">Lane</th><th scope="col">Family</th>' : ""}<th scope="col">${esc(nameOf(entry))}, by trial</th><th scope="col" class="num">Every entry</th></tr></thead><tbody>${rows}</tbody><tfoot>${foot}</tfoot></table></div>${legend}`;
  }

  async function pageTier(ctx, tier) {
    const board = ctx.device, d = board.device, t = R.tier[tier];
    const isPerf = tier === "perf";
    const has = isPerf ? perfHas(board) : board.has(tier);
    if (!t && !has) throw Object.assign(new Error("not found"), { notFound: true });
    const name = tierName(tier);
    const intro = t ? `${esc(t.what)} Spec ${esc(t.spec)}.` : "This test area is in the board but the site has no description for it yet.";

    if (!has) {
      const html = `<div class="container">${crumbs(ctx, [esc(name)])}
        <div class="page-head"><div><h1>${esc(name)}</h1><p>${intro}</p></div>${U.tray("d", "Device", ctx.boardInfo.devices.map((x) => ({ v: x.id, l: x.short })), ctx.devId)}</div>
        ${U.empty("Not in this board yet", `${esc(d.name)} has no published results for this test area in board ${esc(board.version)}. It is planned: when the store writes its results, its scores and published cases appear here. Nothing is shown as a zero in the meantime.`, `<a class="key" href="${ctx.link("/tests")}">Back to the test explorer</a> <a class="key key--ghost" href="${ctx.link("/methodology")}">Read the methodology</a>`)}
      </div>`;
      return { title: name, html };
    }

    const entries = orderedEntries(board, tier);
    const entry = pickEntry(ctx, entries);
    const picker = U.selectField("m", "Entry", entries.map((e) => ({ v: e.key, l: optLabel(e) })), entry ? entry.key : "");
    const head = `${crumbs(ctx, [esc(name)])}
      <div class="page-head"><div><h1>${esc(name)}</h1><p>${intro}</p></div><div class="toolbar">${picker}${U.tray("d", "Device", ctx.boardInfo.devices.map((x) => ({ v: x.id, l: x.short })), ctx.devId)}</div></div>`;
    if (!entry) return { title: name, html: `<div class="container">${head}${U.empty("No entries on this device", "Nothing has been published for this device yet.")}</div>` };
    const who = `<p class="small" style="margin-bottom:var(--space-sm)"><span class="mname">${esc(nameOf(entry))}</span> <span class="muted">${esc(U.entryLine(entry))}</span> ${U.refBadge(entry)} ${U.fitBadge(entry)}</p>${tier === "in_assistant" || tier === "in_assistant_findings" ? `<p style="margin-bottom:var(--space-sm)"><a class="key" href="${ctx.link("/found", { m: entry.key })}">What each model found</a></p>` : ""}`;

    /* performance: measured, not graded per case */
    if (isPerf) {
      const ids = ["decode_2k", "prefill_2k", "ttft_2k", "mem_10k", "max_ctx"].filter((id) => R.metric[id]);
      const rows = board.entries.filter((e) => !e.cloud).map((e) => `<tr><th scope="row"><span class="mname"><a href="${ctx.link("/model/" + U.seg(e.model.id), { v: e.key })}">${esc(nameOf(e))}</a></span><span class="vline">${esc(U.entryLine(e))} ${U.fitBadge(e)}</span></th>${ids.map((id) => `<td class="num">${U.cell(e, id)}</td>`).join("")}</tr>`).join("");
      const html = `<div class="container">${crumbs(ctx, [esc(name)])}
        <div class="page-head"><div><h1>${esc(name)}</h1><p>${intro}</p></div>${U.tray("d", "Device", ctx.boardInfo.devices.map((x) => ({ v: x.id, l: x.short })), ctx.devId)}</div>
        <p class="note-planned">${I.info}<span>Performance is measured, not graded per case, so there are no trajectories here. Each model page draws its speed and memory over seven context sizes; the <a class="link" href="${ctx.link("/device/" + U.seg(ctx.devId))}">device page</a> shows what fits.</span></p>
        <div class="table-wrap" role="region" aria-label="Performance at a glance" tabindex="0"><table class="data"><caption class="visually-hidden">Performance on ${esc(d.name)}</caption><thead><tr><th scope="col">Entry</th>${ids.map((id) => `<th scope="col" class="num">${esc(R.metric[id].short)}<span class="vline">${esc(R.metric[id].unit || "")}</span></th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>
      </div>`;
      return { title: name, html };
    }

    /* scores and the breakdowns */
    let scores;
    if (!entry.scored && !entry.cloud) {
      scores = U.empty(entry.group === "does_not_fit" ? "Does not fit, so never scored" : "Not scored", entry.group === "does_not_fit" ? `${esc(nameOf(entry))} is over the fit line for ${esc(d.fitLabel)}. Entries that do not fit are listed with the memory they need and never scored or ranked. Its published trials, if any, are below for reference.` : "This entry has no scored run on this device.");
    } else {
      const kinds = (t ? t.facets : []).concat(board.facets(tier).map((f) => f.split("=")[0])).filter((k, i, a) => a.indexOf(k) === i);
      const charts = kinds.map((k) => facetChart(entry, tier, k, "By " + (k === "profile" ? "person" : k))).filter(Boolean);
      scores = `${scoreFacts(entry, tier)}
        <p class="small muted" style="margin-top:8px">Scores are 0 to 100 with a 95% interval. Unsafe actions and privacy leaks are counts per 100 trials, shown beside the score and never blended into it.</p>
        ${charts.length ? `<div class="${charts.length > 1 ? "grid-2" : ""}" style="margin-top:var(--space-lg)">${charts.join("")}</div>${facetHow(entry)}` : ""}
        ${tier === "in_assistant" ? U.secHead("Surface and person", "Ask, Brief and Day for each synthetic person.", "") + surfaceProfileTable(ctx, entry) : ""}`;
    }

    /* the published cases */
    const tr = await trajectories(ctx);
    let cases;
    if (!Array.isArray(tr)) cases = trajError(tr.failed);
    else {
      const list = tr.filter((x) => x.tier === tier);
      if (!list.length) {
        cases = tier === "in_assistant"
          ? U.empty("No captured outputs in this export", `Trajectories are published for the development split only, and this board has none for ${esc(name.toLowerCase())}. The capture page, which puts what the model produced beside the answer key it was scored against, fills in when the store exports captures. Until then the scores above by surface and by person are what this board carries.`)
          : U.empty("No published trajectories for this test area", `Trajectories are published for the development split only, and this board has none for ${esc(name.toLowerCase())} yet. Held-out cases never appear; they count only inside the scores above.`);
      } else cases = caseTable(ctx, tier, entry, list);
    }

    const html = `<div class="container">${head}${who}
      ${U.secHead("Scores", `${esc(nameOf(entry))} on ${esc(d.name)}.`, "")}
      ${scores}
      ${U.secHead("Published cases", "Development cases with trajectories. Pick a trial to read it step by step.", "", "cases")}
      ${cases}
      <div style="margin-top:var(--space-md)">${heldOutNote}</div>
    </div>`;
    return { title: name, html };
  }

  /* =========================================================
   * #/case/<tier>/<case id>
   * ========================================================= */
  function stepsHtml(tr) {
    let n = 0;
    const steps = (tr.steps || []).map((s) => {
      const isCall = s.call != null && s.call !== "";
      const err = s.error != null && s.error !== "" && s.error !== false;
      const bad = err || s.unsafe === true;
      if (isCall) n++;
      const body = isCall
        ? `<div class="callbox" role="region" aria-label="${esc("Call to " + s.call)}" tabindex="0"><span class="fn">${esc(s.call)}</span>${fmtArgs(s.args)}</div>${s.result != null ? `<div class="resultbox"><span class="visually-hidden">Tool result: </span>${esc(toText(s.result))}</div>` : ""}`
        : `<div class="msg">${esc(toText(s.text != null ? s.text : s.content != null ? s.content : s.msg != null ? s.msg : s.result))}</div>`;
      const errBox = err ? `<div class="resultbox err"><b>Error:</b> ${esc(toText(s.error === true ? "the call failed" : s.error))}</div>` : "";
      const mark = s.unsafe === true ? `<span class="pf unsafe-call">${I.warn}Unsafe call</span>` : err ? `<span class="pf fail">${I.cross}Error</span>` : "";
      return `<li class="${bad ? "is-bad" : ""}"><div class="who"><b>${esc(WHO[s.who] || toText(s.who) || "Step")}</b>${isCall ? "Step " + n + ", tool call" : ""}</div><div>${body}${errBox}</div><div>${mark}</div></li>`;
    });
    const final = `<li class="${tr.outcome === "unsafe" ? "is-bad" : ""}"><div class="who"><b>Final answer</b>Model</div><div><div class="msg">${tr.final != null && tr.final !== "" ? esc(toText(tr.final)) : '<span class="muted">No final answer recorded.</span>'}</div></div><div>${badge(tr.outcome)}</div></li>`;
    return `<ol class="traj" aria-label="Steps, in order">${steps.join("")}${final}</ol>${steps.length ? "" : '<p class="small muted" style="margin-top:8px">This trial recorded no tool calls before the answer.</p>'}`;
  }
  function checksPanel(tr, tierLabel, p, d, trials) {
    const checks = tr.checks || [];
    const list = checks.length
      ? `<ul class="checks">${checks.map((c) => { const ok = !!c.ok; return `<li class="${ok ? "ok" : "no"}">${ok ? I.check : I.cross}<span>${esc(checkText(c.text != null ? c.text : c.id))} <span class="muted xsmall">${ok ? "met" : "not met"}</span></span></li>`; }).join("")}</ul>`
      : '<p class="small muted">No grader checks were recorded for this trial.</p>';
    const unsafe = tr.outcome === "unsafe" ? `<p style="margin-top:14px"><span class="pf unsafe-call">${I.warn}Unsafe call</span></p><p class="small" style="margin-top:6px">This trial made an unsafe or unconfirmed action. It is counted in unsafe actions per 100 trials, on its own, and never blended into the score.</p>` : "";
    return `<aside class="cut panel" aria-label="Grader checks"><h3 style="margin-bottom:10px">Grader checks</h3>${list}${unsafe}
      <p class="small muted" style="margin-top:14px">The case passes only if every check passes. What each check tests is not in this export yet; the words above are the check names the grader recorded.</p>
      <h3 style="margin:20px 0 8px">Case facts</h3>${U.facts([["Test area", esc(tierLabel)], p ? ["Lane", esc(p.laneLabel)] : null, p ? ["Family", esc(p.famLabel)] : null, ["Trials published", `<span class="num">${trials}</span>`], ["Device", esc(d.short || d.name)], ["Split", "Development"]])}</aside>`;
  }

  async function pageCase(ctx, tier, caseId) {
    const board = ctx.device, d = board.device, name = tierName(tier);
    const p = parseCase(caseId);
    const tr = await trajectories(ctx);
    const head0 = crumbs(ctx, [`<a href="${tierLink(ctx, tier)}">${esc(name)}</a>`, `<span class="mono">${esc(caseId)}</span>`]);
    if (!Array.isArray(tr)) return { title: "Case " + caseId, html: `<div class="container">${head0}${trajError(tr.failed)}</div>` };
    const all = tr.filter((x) => x.tier === tier && x.case_id === caseId);

    if (!all.length) {
      /* is it published on another device? */
      const others = [];
      for (const x of ctx.boardInfo.devices) {
        if (x.id === ctx.devId) continue;
        try { const o = await ST.loadTrajectories(ctx.boardInfo.version, x.id); if (o.some((t) => t.tier === tier && t.case_id === caseId)) others.push(x); } catch (e) { /* another device's file is optional here */ }
      }
      const action = (others.length ? others.map((x) => `<a class="key key--primary" href="${caseLink(ctx, tier, caseId, { d: x.id })}">Read it on ${esc(x.short || x.name)}</a>`).join(" ") + " " : "") + `<a class="key" href="${tierLink(ctx, tier)}">Published cases for ${esc(name.toLowerCase())}</a>`;
      const html = `<div class="container">${head0}
        <div class="page-head"><div><h1 class="mono">${esc(caseId)}</h1><p>${esc(name)} on ${esc(d.name)}.</p></div></div>
        ${U.empty("No published trajectory for this case", `${esc(d.name)} has no trajectory for this case in board ${esc(board.version)}${others.length ? ", but another device does" : ""}. Trajectories are published for the development split only; held-out cases never appear, only their aggregates in the scores. The address may also name a case that does not exist.`, action)}
      </div>`;
      return { title: "Case " + caseId, html };
    }

    const keys = [...new Set(all.map((t) => t.variant))];
    const trials = [...new Set(all.map((t) => t.trial))].sort((a, b) => a - b);
    const entries = orderedEntries(board, tier);
    const withTraj = (e) => keys.includes(e.key);
    const entry = pickEntry(ctx, entries, withTraj);
    const strays = keys.filter((k) => !board.entry(k));
    const mineList = all.filter((t) => entry && t.variant === entry.key);
    const mine = new Map(mineList.map((t) => [t.trial, t]));
    const tq = parseInt(ctx.q.get("t"), 10);
    const trialN = mine.has(tq) ? tq : (mineList.map((t) => t.trial).sort((a, b) => a - b)[0]);
    const cur = mine.get(trialN);

    const opts = entries.map((e) => ({ v: e.key, l: optLabel(e) + (withTraj(e) ? "" : " (no trajectory)"), disabled: !withTraj(e) }));
    const trialPips = `<div class="field"><span id="lbl-trial">Trial</span><span class="pips" role="group" aria-labelledby="lbl-trial">${trials.map((n) => {
      const t = mine.get(n), x = t ? outOf(t.outcome) : null;
      return t ? `<button type="button" data-set="t" data-val="${n}" aria-pressed="${n === trialN}" class="${x.pip}" aria-label="${esc("Trial " + n + ": " + x.text.toLowerCase())}" title="${esc("Trial " + n + ": " + x.text.toLowerCase())}">${I[x.icon]}</button>`
        : `<button type="button" disabled aria-label="Trial ${n}: not published" title="Trial ${n}: not published">${I.dash}</button>`;
    }).join("")}</span></div>`;
    const title = p ? `${p.laneLabel}, ${p.famLabel.toLowerCase()} family` : "Case " + caseId;
    const head = `${head0}
      <div class="page-head"><div><h1>${esc(title)}</h1><p><span class="mono">${esc(caseId)}</span>, ${esc(name.toLowerCase())}. ${entry ? `${esc(nameOf(entry))} on ${esc(d.name)}, ${esc(U.entryLine(entry))}.` : ""} Each case runs ${trials.length} ${trials.length === 1 ? "time" : "times"}; pick a trial to read it.</p></div>
        <div class="toolbar">${U.selectField("m", "Entry", opts, entry ? entry.key : "")}${trialPips}${cur ? `<span style="align-self:center">${badge(cur.outcome)}</span>` : ""}</div></div>
      ${entry ? `<p class="small" style="margin-bottom:var(--space-sm)">${U.refBadge(entry)} ${U.fitBadge(entry)}${entry.group === "does_not_fit" ? ` <span class="muted">This variant does not fit ${esc(d.fitLabel)}; its trials are shown for reference and are never scored or ranked.</span>` : ""}</p>` : ""}
      <p class="note-planned">${I.info}<span>The case's prompt, tools offered and expected calls are not in this export yet. Shown here is what the trajectory carries: the calls the model made, what each tool returned, its final answer and the grader's checks.</span></p>`;

    let body;
    if (!cur) {
      body = U.empty("No trajectory for this entry", `${entry ? esc(nameOf(entry)) : "This entry"} has no published trial for this case on ${esc(d.name)}. Pick another entry above.`);
    } else if (tier === "in_assistant") {
      body = `<div class="vote-grid">
          <div class="cut"><div class="output-head"><b style="font-weight:500">What the model produced</b><span class="muted">Trial ${trialN}</span></div><div class="output">${cur.final != null && cur.final !== "" ? esc(toText(cur.final)) : '<span class="muted">No output recorded.</span>'}</div></div>
          <div class="cut"><div class="output-head"><b style="font-weight:500">Answer key</b><span class="muted">Never shown to the model</span></div><div class="output" style="white-space:normal"><p class="muted">The answer key (the reference answer, the evidence items, and the items likely to mislead) is not in this export yet.</p></div></div>
        </div>
        <div class="grid-case" style="margin-top:var(--space-md)"><div>${stepsHtml(cur)}</div>${checksPanel(cur, name, p, d, trials.length)}</div>`;
    } else {
      body = `<div class="grid-case" style="margin-top:var(--space-md)"><div>${stepsHtml(cur)}</div>${checksPanel(cur, name, p, d, trials.length)}</div>`;
    }

    /* every entry's outcomes on this case */
    const rowEntries = entries.filter(withTraj).concat(entries.filter((e) => !withTraj(e)));
    const rowFor = (key, label, sub) => {
      const ts = new Map(all.filter((t) => t.variant === key).map((t) => [t.trial, t]));
      const allPass = ts.size === trials.length && [...ts.values()].every((t) => t.outcome === "pass");
      const isCur = entry && key === entry.key;
      const cells = trials.map((n) => { const t = ts.get(n); return `<td>${t ? `<a href="${caseLink(ctx, tier, caseId, { m: key, t: n })}" aria-label="${esc(label + ", trial " + n + ": " + outOf(t.outcome).text.toLowerCase())}"${isCur && n === trialN ? ' aria-current="true"' : ""}>${badge(t.outcome)}</a>` : U.status("no_value", { text: "Not published" })}</td>`; }).join("");
      return `<tr class="${isCur ? "is-top" : ts.size ? "" : "row-dim"}"><th scope="row"><span class="mname">${ts.size ? `<a href="${caseLink(ctx, tier, caseId, { m: key })}">${esc(label)}</a>` : esc(label)}</span>${sub ? `<span class="vline">${sub}</span>` : ""}</th>${cells}<td>${ts.size ? (allPass ? `<span class="cell-status">${I.check}Yes</span>` : `<span class="cell-status">${I.cross}No</span>`) : ""}</td></tr>`;
    };
    const table = `<div class="table-wrap" role="region" aria-label="Every entry on this case" tabindex="0"><table class="data"><caption class="visually-hidden">Outcomes of every entry on case ${esc(caseId)}</caption>
      <thead><tr><th scope="col">Entry</th>${trials.map((n) => `<th scope="col">Trial ${n}</th>`).join("")}<th scope="col">Passed every trial</th></tr></thead>
      <tbody>${rowEntries.map((e) => rowFor(e.key, nameOf(e), esc(U.entryLine(e)) + (e.group === "does_not_fit" ? ", does not fit" : ""))).join("")}${strays.map((k) => rowFor(k, k, "Not on this board")).join("")}</tbody></table></div>
      <p class="small muted" style="margin-top:8px">An unsafe call fails the trial and is also counted on its own in unsafe actions per 100 trials. It is never blended into a score.</p>`;

    const html = `<div class="container">${head}${body}
      ${U.secHead("Every entry on this case", `${keys.length} ${keys.length === 1 ? "entry has" : "entries have"} published trials for this case on ${esc(d.name)}.`, "", "entries")}
      ${table}
      <div style="margin-top:var(--space-md)">${heldOutNote}</div>
    </div>`;
    return { title: "Case " + caseId, html };
  }

  /* ---------- routing ---------- */
  async function render(ctx) {
    if (!ctx.device) return { title: "Test explorer", html: `<div class="container">${U.empty("No results yet", "No results board is published yet.")}</div>` };
    const [head, tier, ...rest] = ctx.parts;
    if (head === "case") {
      if (!tier || !rest.length) return pageExplorer(ctx);
      return pageCase(ctx, tier, rest.join("/"));
    }
    if (tier) return pageTier(ctx, tier);
    return pageExplorer(ctx);
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.tests = { render, parseCase, checkText };
})();
