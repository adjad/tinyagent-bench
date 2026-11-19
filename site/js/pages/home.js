/* Leaderboard (home): the fit gate first, a verdict that names the best agent that fits the chosen device,
 * the pinned references, ranked rows with intervals and rank ranges, listed-not-ranked rows, cloud references
 * in their own band, the variants that do not fit behind a toggle, and quality against cost. */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store, C = EDAT.charts, CFG = EDAT.config;
  const { esc, fmt, I } = U;
  const HEAD = "agentic_score";

  const val = (e, id) => { const c = ST.value(e, id); return c.status === "ok" ? c.value : null; };
  const nameOf = (e) => e.name + (e.thinkingText ? ", " + e.thinkingText : "");

  function filtersFromQ(q) { return { th: q.get("th") || "", arch: q.get("arch") || "" }; }
  const passes = (e, f) => !!e.refRole || ((!f.th || (f.th === "ship" ? e.variant.headline_entry !== false : e.thinking === f.th)) && (!f.arch || (f.arch === "moe") === !!e.model.moe));

  /* split one device board into the bands the table shows */
  function bands(board, f, every) {
    const all = board.entries.filter((e) => !e.cloud && passes(e, f));
    const rankedAll = all.filter((e) => e.group === "ranked");
    const ranked = every ? rankedAll : ST.bestPerModel(rankedAll, HEAD);
    const listed = all.filter((e) => e.group === "invalid" || e.group === "not_measured" || ["not_run", "failed"].includes(e.run.status) && e.group !== "does_not_fit" && e.group !== "ranked");
    const nofit = all.filter((e) => e.group === "does_not_fit");
    return { all, rankedAll, ranked, listed, nofit };
  }

  /* the two agentic loops, explained where the columns are read */
  const LOOPS_HOW = `<details class="howto"><summary>${I.chev}How to read the two agentic columns</summary><ul>
      <li>The agentic score comes from the benchmark's own minimal loop: it sends the cases to the model and takes its tool calls as they come, with no second tries. It is the headline because it measures the model alone.</li>
      <li>"Assistant's loop" runs the same assistant-lane cases through the assistant's own loop, which re-prompts the model and repairs malformed or failed calls. "Repairs add" is that score minus the minimal loop's score on the same assistant-lane cases (not minus the headline, which also counts the neutral lane), with its paired 95% interval: what the assistant's repairs add for this model.</li>
      <li>Both are empty for the neutral lane (it has no assistant loop), for cloud references (they run the minimal loop only) and on the iPhone (its parity subset runs the minimal loop only). Hover an empty cell for the reason.</li></ul></details>`;

  function render(ctx) {
    const q = ctx.q, board = ctx.device, cloud = ctx.cloud, d = board.device, phone = d.kind === "phone";
    const f = filtersFromQ(q), every = q.get("rows") === "all", showNofit = q.get("nf") === "1", showCloud = q.get("cl") !== "0";
    const vs = ["tuned", "base"].includes(q.get("vs")) ? q.get("vs") : "";
    let groups = (q.get("g") || "agentic").split(",").filter((g) => R.group[g] && !R.group[g].fixed);
    if (!groups.length) groups = ["agentic"];

    const B = bands(board, f, every);
    const fullSet = B.ranked.length === board.entries.filter((e) => e.group === "ranked").length;
    const ranks = fullSet ? null : ST.rankRanges(B.ranked, HEAD);
    const rankOf = (e) => (ranks ? ranks.get(e.key) : ST.storeRank(e, HEAD)) || ST.rankRanges(B.ranked, HEAD).get(e.key) || null;
    const rankLabel = (e) => { const r = rankOf(e); return r ? (r[0] === r[1] ? String(r[0]) : r[0] + "–" + r[1]) : ""; };

    const sortId = R.metric[q.get("s")] ? q.get("s") : HEAD;
    const order = q.get("o") || (R.metric[sortId].better === "low" ? "asc" : "desc");
    const sv = (e) => { const v = val(e, sortId); return v == null ? (order === "asc" ? Infinity : -Infinity) : v; };
    const byScore = B.ranked.slice().sort((a, b) => (val(b, HEAD) ?? -1) - (val(a, HEAD) ?? -1));
    const sorted = B.ranked.slice().sort((a, b) => (order === "asc" ? sv(a) - sv(b) : sv(b) - sv(a)) || (val(b, HEAD) ?? -1) - (val(a, HEAD) ?? -1));
    const top = byScore.find((e) => val(e, HEAD) != null) || null;
    const tied = top ? byScore.filter((e) => e !== top && (rankOf(e) || [9])[0] === 1) : [];
    const budget = (board.entries.map((e) => (e.run.fit || {}).budget_bytes).find(Boolean)) || null;
    const budgetGB = U.GiB(budget);
    const mlink = (e) => ctx.link("/model/" + U.seg(e.model.id), { v: e.key });
    const clouds = cloud ? cloud.entries.slice().sort((a, b) => (val(b, HEAD) ?? -1) - (val(a, HEAD) ?? -1)) : [];

    /* ---------- verdict ---------- */
    let verdict;
    if (top) {
      const c = ST.value(top, HEAD);
      const tiedTxt = tied.length
        ? `Statistically tied with ${tied.slice(0, 2).map((e) => `<a class="link" href="${mlink(e)}">${esc(nameOf(e))}</a>`).join(" and ")}${tied.length > 2 ? ` and ${tied.length - 2} more` : ""}: the gap is inside the margin of error.`
        : "Ahead of every other entry by more than the margin of error.";
      const bestCloud = clouds.find((e) => val(e, HEAD) != null);
      const cloudTxt = bestCloud && showCloud ? ` The best cloud reference, ${esc(nameOf(bestCloud))}, scores ${fmt(val(bestCloud, HEAD), 1)}: ${fmt(Math.abs(val(bestCloud, HEAD) - c.value), 1)} points ${val(bestCloud, HEAD) >= c.value ? "higher" : "lower"}, and it is never ranked.` : "";
      const m10 = U.GiB((top.run.fit || {}).peak_bytes_at_10240);
      const fitTxt = m10 != null && budgetGB ? ` It runs as ${esc(top.line)} and peaks at ${fmt(m10, 1)} GB with 10k tokens of context, inside the ${fmt(budgetGB, 1)} GB fit line.` : ` It runs as ${esc(top.line)}.`;
      const small = (top.run.fit || {}).verdict === "fits_small_context" ? " It fits only at small context (2k), so check the context chart before relying on long prompts." : "";
      const fig = (label, id, unit, bad) => { const cc = ST.value(top, id); return `<div><dt>${esc(label)}</dt><dd class="${bad && cc.status === "ok" && cc.value > 0 ? "bad" : ""}">${cc.status === "ok" ? fmt(cc.value, R.metric[id].dp) + `<small>${cc.half ? "±" + fmt(cc.half, 1) : esc(unit)}</small>` : `<span class="small">${U.status(cc.status)}</span>`}</dd></div>`; };
      const compareKeys = [top].concat(tied.length ? tied : byScore.filter((e) => e !== top).slice(0, 2)).slice(0, 3).map((e) => e.key);
      const refKey = board.references.tuned;
      if (refKey && !compareKeys.includes(refKey)) compareKeys.push(refKey);
      verdict = `<section class="verdict" aria-labelledby="verdict-h">
        <div>
          <h1 id="verdict-h"><a href="${mlink(top)}">${esc(nameOf(top))}</a> is the best agent that fits ${esc(d.fitLabel)}.</h1>
          <p class="verdict-sub">${tiedTxt}${cloudTxt}${fitTxt}${small}</p>
          <div class="verdict-actions">
            <a class="key key--primary" href="${mlink(top)}">Open ${esc(top.name)}</a>
            <a class="key" href="${ctx.link("/compare", { m: compareKeys.slice(0, 4).join(",") })}">Compare with ${tied.length ? "the tied entries" : "the next two"}</a>
          </div>
        </div>
        <dl class="figures">
          ${fig("Agentic score", HEAD, "of 100")}
          ${fig("Unsafe actions", "unsafe", "per 100 trials", true)}
          ${fig("Privacy leaks", "leaks", "per 100 trials", true)}
          ${fig("Assistant score", "assistant_score", "of 100")}
          ${fig("Decode speed at 2k", "decode_2k", "tok/s")}
          ${phone ? fig("Peak memory at 10k", "mem_10k", "GB") : ST.value(top, "j_task").status === "ok" ? fig("Energy per task", "j_task", "J") : fig("Peak memory at 10k", "mem_10k", "GB")}
        </dl>
      </section>`;
    } else {
      verdict = `<section class="verdict"><div><h1>No entry fits ${esc(d.fitLabel)} with these filters.</h1><p class="verdict-sub">${B.all.length ? "Clear a filter. The variants that do not fit are listed below with the memory each one needs." : "Nothing has been published for this device yet."}</p><div class="verdict-actions"><a class="key key--primary" href="${ctx.link("/", {})}">Clear filters</a></div></div></section>`;
    }

    /* ---------- best in each size ---------- */
    const SIZE = [["under 1B", 0, 1], ["1B to 4B", 1, 4.05], ["4B to 8B", 4.05, 8.05], ["8B and up", 8.05, 1e9]];
    const bn = (e) => (e.model.params_total || 0) / 1e9;
    const sizeStrip = top ? `<section class="size-strip" aria-label="Best agent in each size">${SIZE.map(([label, lo, hi]) => {
      const best = byScore.find((e) => bn(e) >= lo && bn(e) < hi && val(e, HEAD) != null);
      const any = B.all.some((e) => bn(e) >= lo && bn(e) < hi);
      return `<div><p class="xsmall muted">Best ${label}</p>${best ? `<p><a class="mname" href="${mlink(best)}">${esc(nameOf(best))}</a></p><p class="small muted num">${fmt(val(best, HEAD), 1)} ±${fmt(ST.value(best, HEAD).half, 1)}, ${fmt(val(best, "unsafe"), 1)} unsafe per 100</p>` : `<p class="small">${any ? U.status("does_not_fit", { text: "None fits" }) : U.status("no_value", { text: "None tested" })}</p>`}</div>`;
    }).join("")}</section>` : "";

    /* ---------- filters ---------- */
    const MORE = ["th", "arch"];
    const nOn = MORE.filter((k) => q.get(k)).length;
    const fitNote = budgetGB ? `Ranked rows fit ${esc(d.fitLabel)}: peak memory at 10k context within <strong class="num">${fmt(budgetGB, 1)} GB</strong>${phone ? ", the test app's measured budget less a margin" : ", what a 16 GB Mac leaves for one model, less a margin"}. Rows that fit only at 2k are ranked with a badge.` : "No fit line is published for this device yet, so nothing is ranked.";
    const filters = `<section class="filters" aria-label="Filters">
      <div class="filter-row">
        ${U.tray("d", "Device", ctx.boardInfo.devices.map((x) => ({ v: x.id, l: x.short })), ctx.devId)}
        ${U.tray("rows", "Rows", [{ v: "", l: "Best variant per model" }, { v: "all", l: "Every variant" }], every ? "all" : "")}
        <p class="small muted fit-note">${fitNote}</p>
      </div>
      <details class="more-filters"${nOn ? " open" : ""}>
        <summary>${I.chev}More filters${nOn ? ` (${nOn} on)` : ""}</summary>
        <div class="filter-row">
          ${U.selectField("th", "Thinking", [{ v: "", l: "Both modes" }, { v: "ship", l: "Shipped mode only" }, { v: "off", l: "Thinking off" }, { v: "on", l: "Thinking on" }], f.th)}
          ${U.selectField("arch", "Architecture", [{ v: "", l: "Any" }, { v: "dense", l: "Dense" }, { v: "moe", l: "Mixture of experts" }], f.arch)}
        </div>
      </details>
    </section>`;

    /* ---------- columns ---------- */
    const gm = [];
    groups.forEach((g) => R.metrics.filter((m) => m.group === g && !R.fixed.includes(m.id)).forEach((m, i) => gm.push({ m, first: i === 0 })));
    const thSort = (id, label, sub, extra) => {
      const m = R.metric[id], on = sortId === id;
      return `<th scope="col" class="num ${extra || ""}"${on ? ` aria-sort="${order === "asc" ? "ascending" : "descending"}"` : ""}><button type="button" data-sort="${id}" title="${esc(m.name + (m.desc ? ". " + m.desc : ""))}">${esc(label)}${on ? (order === "asc" ? " ↑" : " ↓") : ""}</button>${sub ? `<span class="vline">${esc(sub)}</span>` : ""}</th>`;
    };
    const unitTxt = (m) => (m.unit === "%" ? "%" : m.unit || "") + (m.better === "low" ? (m.unit ? ", " : "") + "lower is better" : "");
    const nCols = 8 + gm.length;
    const head = `<thead><tr><th scope="col" class="sticky">Rank and entry</th>${thSort(HEAD, "Agentic score", "minimal loop, 0 to 100, ±95%")}${thSort("assistant_loop_score", "Assistant's loop", "assistant lane, repairs on", "col-loop")}${thSort("repair_gain", "Repairs add", "points, same cases", "col-loop")}<th scope="col" class="num col-delta">Against tuned<span class="vline">difference</span></th><th scope="col" class="num col-delta">Against base<span class="vline">difference</span></th>${thSort("unsafe", "Unsafe actions", "per 100 trials", "col-unsafe")}${thSort("leaks", "Privacy leaks", "per 100 trials", "col-unsafe")}${gm.map(({ m, first }) => thSort(m.id, m.short, (vs ? "difference, " : "") + unitTxt(m), first ? "group-start" : "")).join("")}</tr></thead>`;

    const refBoardFor = (e) => (e.cloud ? board : null);
    const cellFor = (e, id) => (vs && e.refRole !== vs ? U.deltaCell(e, id, vs, refBoardFor(e)) : U.cell(e, id));
    const nameCell = (e, rank) => `<th scope="row" class="sticky"><div style="display:flex;gap:12px;align-items:baseline"><span class="rank" style="min-width:2.6em">${rank}</span><span><span class="mname"><a href="${mlink(e)}">${esc(e.name)}</a></span><span class="vline">${esc(U.entryLine(e))} ${U.refBadge(e)} ${U.fitBadge(e)}</span></span></div></th>`;
    const rowHtml = (e, cls) => `<tr class="${e === top ? "is-top " : ""}${cls || ""}">${nameCell(e, e.cloud ? "" : rankLabel(e))}<td class="num">${U.scoreCell(e, HEAD)}<span class="unsafe-inline">${U.cell(e, "unsafe")} ${U.cell(e, "leaks")}</span></td><td class="num col-loop">${U.cell(e, "assistant_loop_score")}</td><td class="num col-loop">${U.cell(e, "repair_gain")}</td><td class="num col-delta">${U.deltaCell(e, HEAD, "tuned", refBoardFor(e))}</td><td class="num col-delta">${U.deltaCell(e, HEAD, "base", refBoardFor(e))}</td><td class="num col-unsafe">${U.cell(e, "unsafe")}</td><td class="num col-unsafe">${U.cell(e, "leaks")}</td>${gm.map(({ m, first }) => `<td class="num ${first ? "group-start" : ""}">${cellFor(e, m.id)}</td>`).join("")}</tr>`;
    const divider = (title, sub, id) => `<tr class="divider-row"${id ? ` id="${id}"` : ""}><td colspan="${nCols}">${title}${sub ? `<span>${sub}</span>` : ""}</td></tr>`;
    const listedRow = (e) => {
      const why = e.group === "invalid"
        ? U.status("invalid", { text: `Invalid run: ${fmt(e.run.excluded_rate, 1)}% of trials excluded` }) + ` <span class="small muted">Over the 2% limit, so it is listed and not ranked. Engine and transport faults are excluded, never counted as failures.</span>`
        : e.group === "not_measured" ? U.status("not_measured") + ` <span class="small muted">Its memory has not been measured on this device, so it is listed and not ranked until the fit gate has a verdict.</span>`
        : U.status(e.run.status === "failed" ? "failed" : "not_run");
      return `<tr class="row-dim">${nameCell(e, "")}<td colspan="${nCols - 1}"><div class="row-note">${why}</div></td></tr>`;
    };
    const nofitRow = (e) => {
      const fit = e.run.fit || {};
      return `<tr class="row-dim">${nameCell(e, "")}<td colspan="${nCols - 1}"><div class="row-note">${U.status("does_not_fit")} <span class="small muted">Needs <span class="num">${fmt(U.GiB(fit.peak_bytes_at_2048), 1)} GB</span> at 2k and <span class="num">${fmt(U.GiB(fit.peak_bytes_at_10240), 1)} GB</span> at 10k; the fit line is ${fmt(U.GiB(fit.budget_bytes || budget), 1)} GB. Never ranked or scored here.</span></div></td></tr>`;
    };

    const pinned = ["tuned", "base"].map((role) => board.refEntry(role)).filter(Boolean);
    const pinnedRanked = pinned.filter((e) => e.group === "ranked");
    const rest = sorted.filter((e) => !pinned.includes(e));
    const pinnedHtml = pinned.length ? divider("Pinned references", `Always shown, and ranked with everyone else. The two difference columns read every row against each of them; a mark means the gap is larger than the margin of error.${vs ? " The other columns show the difference from the " + (vs === "tuned" ? "tuned" : "base") + " reference." : ""}`) + pinned.map((e) => (e.group === "ranked" ? rowHtml(e, "row-ref") : listedRow(e))).join("") : "";
    const rankedHtml = divider(every ? `Every variant that fits (${rest.length})` : `Every entry that fits (${rest.length})`, ranks ? "Ranks are among the rows shown." : "") + rest.map((e) => rowHtml(e)).join("");
    const listedHtml = B.listed.length ? divider(`Listed, not ranked (${B.listed.length})`, "Shown so a missing rank reads as a finding, not an omission") + B.listed.map(listedRow).join("") : "";
    const cloudHtml = clouds.length && showCloud ? divider(`Cloud references, not ranked (${clouds.length})`, "Hosted models run on the same cases, for scale. No device, memory, energy or fit verdict; each shows its run date because hosted models change.", "cloud-band") + clouds.map((e) => rowHtml(e, "row-cloud")).join("") : "";
    const nofitHtml = B.nofit.length && showNofit ? divider(`Does not fit ${esc(d.fitLabel)} (${B.nofit.length})`, "Never ranked. Memory is measured on the real device, so the shortfall is exact.", "nofit-band") + B.nofit.slice().sort((a, b) => ((a.run.fit || {}).peak_bytes_at_2048 || 0) - ((b.run.fit || {}).peak_bytes_at_2048 || 0)).map(nofitRow).join("") : "";

    const toggles = `<div class="nofit-toggle toolbar">${clouds.length ? `<button type="button" class="key" data-set="cl" data-val="${showCloud ? "0" : ""}" aria-expanded="${showCloud}">${showCloud ? "Hide" : "Show"} the ${clouds.length} cloud references</button>` : ""}${B.nofit.length ? `<button type="button" class="key" data-set="nf" data-val="${showNofit ? "" : "1"}" aria-expanded="${showNofit}">${showNofit ? "Hide" : "Show"} the ${B.nofit.length} variant${B.nofit.length > 1 ? "s" : ""} that do${B.nofit.length > 1 ? "" : "es"} not fit ${esc(d.fitLabel)}</button>` : ""}</div>`;
    const table = B.all.length || clouds.length
      ? `<div class="table-wrap" role="region" aria-label="Leaderboard" tabindex="0"><table class="data" id="board"><caption class="visually-hidden">Leaderboard for ${esc(d.name)}. Column headers sort the table.</caption>${head}<tbody>${pinnedHtml}${rankedHtml}${listedHtml}${cloudHtml}${nofitHtml}</tbody></table></div>${toggles}`
      : U.empty("No entries match", "Every entry was filtered out. Clear a filter to see results.", `<a class="key" href="${ctx.link("/", {})}">Clear filters</a>`);

    /* ---------- group and view controls ---------- */
    const hasData = (g) => R.metrics.some((m) => m.group === g.id && !m.planned);
    const groupBtn = (g) => `<button type="button" data-gt="${g.id}" aria-pressed="${groups.includes(g.id)}">${esc(g.name)}</button>`;
    const shownGroups = R.groups.filter((g) => !g.fixed && (g.main || groups.includes(g.id)));
    const moreGroups = R.groups.filter((g) => !g.fixed && !g.main && !groups.includes(g.id));
    const controls = `<div class="toolbar">
        <div class="field"><span id="lbl-g">Columns: add or remove groups</span><div class="tray-scroll"><div class="tray" role="group" aria-labelledby="lbl-g">${shownGroups.map(groupBtn).join("")}</div></div></div>
        ${moreGroups.length ? `<label class="field"><span>More groups</span><select class="select" data-gadd="1"><option value="">Add a group</option>${moreGroups.map((g) => `<option value="${g.id}">${esc(g.name)}${hasData(g) ? "" : " (not in this board yet)"}</option>`).join("")}</select></label>` : ""}
        ${U.tray("vs", "Show", [{ v: "", l: "Values" }, { v: "tuned", l: "Difference from tuned reference" }, { v: "base", l: "Difference from base reference" }], vs)}
      </div>`;
    const plannedGroups = groups.filter((g) => !hasData(R.group[g]));
    const conditions = `<p class="conditions"><strong>${esc(d.name)}</strong>, ${every ? "every variant" : "best fitting variant per model and thinking mode"}, English cases, speed at 2k context, ${B.ranked.length} ranked, board ${esc(board.version)}, results dated ${esc(U.date(board.generatedAt))}. Thinking on and off are separate entries. Ranks are ranges: entries share a rank when their intervals overlap.${board.sample ? " Sample data." : ""}</p>`;

    /* ---------- quality against cost ---------- */
    const rankedVariants = bands(board, f, true).rankedAll;
    const jOk = rankedVariants.some((e) => ST.value(e, "j_task").status === "ok");
    const xOpts = [
      { v: "mem", l: "Memory" },
      { v: "time", l: "Seconds per case" },
      { v: "energy", l: "Joules per task", disabled: !jOk, title: phone ? "Not in version 1 on the iPhone" : "Not in this board yet" }
    ];
    let xKey = q.get("x") || "mem"; if (xKey === "energy" && !jOk) xKey = "mem";
    const X = { mem: { id: "mem_10k", label: "Peak memory at 10k, GB", dp: 1, word: "memory" }, time: { id: "wall_s", label: "Seconds per case", dp: 1, word: "time" }, energy: { id: "j_task", label: "Joules per completed task", dp: 0, word: "energy" } }[xKey];
    const shown = new Set(B.ranked.map((e) => e.key));
    const pts = rankedVariants.map((e) => ({ e, x: val(e, X.id), c: ST.value(e, HEAD) })).filter((p) => p.x != null && p.c.status === "ok").map(({ e, x, c }) => ({
      x, y: c.value, ci: c.half || 0, group: e.model.id + "§" + e.thinking,
      label: nameOf(e) + (shown.has(e.key) ? "" : ", " + (CFG.quants[e.variant.quant] || e.variant.quant)), minor: !shown.has(e.key),
      shape: e.model.moe ? "diamond" : e.thinking === "on" ? "triangle" : "circle", hl: !!top && e.key === top.key, pin: !!e.refRole && shown.has(e.key), href: mlink(e),
      tip: `${nameOf(e)}, ${e.line}\nAgentic ${fmt(c.value, 1)} ±${fmt(c.half, 1)}\n${X.label}: ${fmt(x, X.dp)}\nUnsafe actions: ${fmt(val(e, "unsafe"), 1)} per 100 trials`
    }));
    const cloudLines = showCloud ? clouds.filter((e) => val(e, HEAD) != null).map((e) => ({ y: val(e, HEAD), label: nameOf(e) + ", cloud" })) : [];
    const scatterHow = U.howTo([
      `Each point is one variant that fits ${esc(d.fitLabel)}: a model at one quantisation and thinking mode. Higher is a better agent; further left costs less ${X.word}.`,
      "A thin line joins the quantisations of one model, so you can see what a smaller file costs in score.",
      "The dashed step line is the trade-off edge: no other variant is both better and cheaper than a point on it.",
      "Whiskers are 95% intervals. When two whiskers overlap, the two variants may be equally good.",
      "Circles are dense models, diamonds are mixture-of-experts models and triangles are thinking-on entries. The filled cobalt point is the leader.",
      "Dotted horizontal lines are cloud references. They carry only a score: a hosted model has no memory, energy or on-device time, so it has no place on the cost axis."
    ]);
    const scatter = pts.length ? `<figure class="scatter-fig" style="margin:0">${C.scatter({ points: pts, refLines: cloudLines, xLabel: X.label, yLabel: "Agentic score", aria: "Agentic score against " + X.label })}
        <figcaption class="legend"><span>${C.shapeSwatch("circle", "dot")} Dense</span><span>${C.shapeSwatch("diamond", "dot")} Mixture of experts</span><span>${C.shapeSwatch("triangle", "dot")} Thinking on</span><span>${C.shapeSwatch("circle", "dot hl")} Leader</span><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><path class="variant-join" d="M1 6H17"/></svg> Quantisations of one model</span><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><path class="frontier" d="M1 6H17"/></svg> Trade-off edge</span>${cloudLines.length ? '<span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><path class="cloudline" d="M1 6H17"/></svg> Cloud reference score, no position on the cost axis</span>' : ""}</figcaption>
        ${scatterHow}
        ${U.asTable(["Entry", "Variant", "Agentic score", "± 95%", X.label], pts.map((p) => [esc(p.label), esc(p.tip.split("\n")[0].split(", ").slice(1).join(", ")), fmt(p.y, 1), fmt(p.ci, 1), fmt(p.x, X.dp)]), "Quality against cost")}</figure>`
      : U.empty("Nothing to plot", "No ranked variant has both a score and a cost on this axis with these filters.");

    const html = `<div class="container">
      ${verdict}
      ${sizeStrip}
      ${filters}
      <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:end;gap:12px;margin-top:var(--space-lg)">${controls}${conditions}</div>
      ${plannedGroups.length ? `<p class="note-planned">${I.info}<span>${plannedGroups.map((g) => esc(R.group[g].name)).join(", ")}: no results in this board yet. The columns are shown so you can see what will be measured; every cell says so rather than showing a blank or a zero.</span></p>` : ""}
      ${table}
      <p class="small muted" style="margin-top:8px">Unsafe actions and privacy leaks are counted on their own and never blended into any score. The agentic score is the mean of seven families, each half assistant lane and half neutral lane, three trials per case${phone ? "; the iPhone runs a smaller parity subset, so its intervals are wider" : ""}. Differences marked ≈ are unpaired estimates; the rest are paired over the same cases. <a class="link" href="${ctx.link("/methodology")}">How scores are built</a>.</p>
      ${LOOPS_HOW}
      ${U.secHead("Quality against cost", "Up and to the left is better.", U.tray("x", "Cost axis", xOpts, xKey), "cost")}
      ${scatter}
    </div>`;
    return { title: "Leaderboard", html };
  }

  function after(root, ctx) {
    root.querySelectorAll("button[data-gt]").forEach((b) => b.addEventListener("click", () => {
      const cur = (ctx.q.get("g") || "agentic").split(",").filter((g) => R.group[g]);
      const id = b.dataset.gt, i = cur.indexOf(id);
      if (i > -1) cur.splice(i, 1); else cur.push(id);
      ctx.setQ({ g: cur.length ? cur.join(",") : "agentic" });
    }));
    const add = root.querySelector("select[data-gadd]");
    if (add) add.addEventListener("change", () => { if (!add.value) return; const cur = (ctx.q.get("g") || "agentic").split(","); cur.push(add.value); ctx.setQ({ g: cur.join(",") }); });
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.home = { render, after, bands };
})();
