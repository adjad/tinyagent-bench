/* Devices: the list of measured devices (#/devices) and one device in depth (#/device/<id>): what fits its fit line,
 * how every entry runs there, memory over context, the simulated day and sustained load. Everything is read from the
 * board files through EDAT.store; a device the export does not list gets a designed empty state. */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store, C = EDAT.charts, CFG = EDAT.config;
  const { esc, fmt, I } = U;
  const HEAD = "agentic_score";
  /* the performance curve the memory chart reads (results-record §4.1) */
  const MEM_CURVE = "footprint_peak_bytes";
  /* proposed curve for sustained load: points [{minute, value, status}] (not in the export yet) */
  const SUSTAIN_CURVE = "decode_tps_sustained";
  const PERF_COLS = ["decode_2k", "prefill_2k", "ttft_2k", "mem_10k", "max_ctx", "load_s"];
  const SHAPES = ["circle", "square", "triangle", "diamond"];

  const val = (e, id) => { const c = ST.value(e, id); return c.status === "ok" ? c.value : null; };
  const nameOf = (e) => e.name + (e.thinkingText ? ", " + e.thinkingText : "");
  const localEntries = (b) => b.entries.filter((e) => !e.cloud);
  const verdictOf = (e) => (e.run.fit || {}).verdict || "not_measured";
  const budgetOf = (b) => localEntries(b).map((e) => (e.run.fit || {}).budget_bytes).find(Boolean) || null;
  const weightsKey = (e) => [e.model.id, e.variant.quant, e.variant.runtime].join("§");
  const isPhone = (b) => (b.device || {}).kind === "phone";
  const byScore = (list) => list.slice().sort((a, b) => (val(b, HEAD) ?? -1) - (val(a, HEAD) ?? -1));
  const statusText = (key) => (R.statuses[key] || { text: String(key).replace(/_/g, " ") }).text;
  const modelLink = (ctx, e, devId) => ctx.link("/model/" + U.seg(e.model.id), { v: e.key, d: devId });
  const deviceLink = (ctx, id) => ctx.link("/device/" + U.seg(id), { d: id });

  function counts(b) {
    const c = { fits: 0, small: 0, nofit: 0, nm: 0, total: 0 };
    localEntries(b).forEach((e) => {
      const v = verdictOf(e); c.total++;
      if (v === "fits") c.fits++; else if (v === "fits_small_context") c.small++; else if (v === "does_not_fit") c.nofit++; else c.nm++;
    });
    return c;
  }
  function runtimes(b) {
    const m = new Map();
    localEntries(b).forEach((e) => { const r = e.variant.runtime; if (!r) return; const s = m.get(r) || new Set(); if (e.variant.runtime_version) s.add(e.variant.runtime_version); m.set(r, s); });
    return [...m].map(([r, vs]) => ({ id: r, name: CFG.runtimes[r] || r, versions: [...vs] }));
  }
  const runtimeText = (b) => runtimes(b).map((r) => `<span style="white-space:nowrap">${esc(r.name)}</span>` + (r.versions.length ? `<span class="muted xsmall mono" style="display:block;white-space:nowrap">${esc(r.versions.join(", "))}</span>` : "")).join("<br>") || U.status("no_value", { text: "No runtime recorded" });
  /* the leading entry that fits: highest agentic score among ranked entries */
  const leaderOf = (b) => byScore(localEntries(b).filter((e) => e.group === "ranked")).find((e) => val(e, HEAD) != null) || null;

  /* energy is Mac only in version 1; the iPhone gets battery-gauge estimates */
  const note = (t) => `<span class="small muted" style="display:block;white-space:normal;max-width:30ch;margin-top:2px">${t}</span>`;
  function energyText(b) {
    if (isPhone(b)) {
      return `${U.status(R.metric.j_task.macOnly ? "not_applicable_iphone" : "not_in_board", { text: "Joules per task: not in version 1" })}${note("Decode energy and battery share are estimates from the battery gauge.")}`;
    }
    const any = localEntries(b).some((e) => ST.value(e, "j_task").status === "ok");
    return any ? `<span class="cell-status">${I.bolt}Measured: joules per task</span>${note("From the on-board power counters.")}` : `${U.status("not_in_board")}${note("Measured from the on-board power counters once published.")}`;
  }

  function fitCell(e) {
    const v = verdictOf(e);
    if (v === "fits") return `<span class="fitbadge" title="Peak memory at 10k context is within the fit line.">${I.check}Fits</span>`;
    if (v === "fits_small_context") return U.fitBadge(Object.assign({}, e, { group: "ranked" }));
    if (v === "does_not_fit") return U.status("does_not_fit");
    return U.status("not_measured");
  }
  const fitWords = (e) => ({ fits: "Fits", fits_small_context: "Small context only", does_not_fit: "Does not fit", not_measured: "Fit not measured" }[verdictOf(e)] || statusText(verdictOf(e)));

  /* ======================================================================
   * #/devices
   * ==================================================================== */
  async function renderList(ctx) {
    const bi = ctx.boardInfo;
    if (!bi || !bi.devices.length) {
      return { title: "Devices", html: `<div class="container"><div class="page-head"><div><h1>Devices</h1></div></div>${U.empty("No devices in this board yet", "The results store has not published a board with any device. Devices appear here as soon as the export lists them.")}</div>` };
    }
    const boards = await ST.loadAllDevices(bi.version);
    const sample = boards.some((b) => b.sample) || bi.sample;
    const rows = boards.map((b) => {
      const d = b.device, c = counts(b), budget = budgetOf(b), top = leaderOf(b);
      const gb = d.ram_bytes ? fmt(U.GiB(d.ram_bytes), 0) + " GB" : null;
      const fitLines = [
        `<span class="cell-status">${I.check}${c.fits} fit</span>`,
        c.small ? `<span class="cell-status">${I.mem}${c.small} small context only</span>` : "",
        c.nofit ? `<span class="cell-status bad">${I.mem}${c.nofit} do${c.nofit === 1 ? "es" : ""} not fit</span>` : "",
        c.nm ? `<span class="cell-status">${I.clock}${c.nm} not measured</span>` : ""
      ].filter(Boolean).join("<br>");
      const best = top ? `<a href="${modelLink(ctx, top, d.id)}">${esc(nameOf(top))}</a><span class="vline num">${fmt(val(top, HEAD), 1)}${ST.value(top, HEAD).half > 0 ? " ±" + fmt(ST.value(top, HEAD).half, 1) : ""} agentic score</span>` : U.status("no_value", { text: "Nothing ranked yet" });
      return `<tr>
        <th scope="row" class="sticky"><span class="mname"><a href="${deviceLink(ctx, d.id)}">${esc(d.name)}</a></span><span class="vline">${esc([d.soc, d.os].filter(Boolean).join(", ")) || "Chip and system not recorded"}</span></th>
        <td>${d.kind === "phone" ? "Phone" : "Laptop"}</td>
        <td class="num">${gb ? `<span class="num">${esc(gb)}</span>` : U.status("no_value", { text: "Not recorded" })}</td>
        <td>${runtimeText(b)}</td>
        <td class="num">${budget ? `<span class="num">${fmt(U.GiB(budget), 1)} GB</span><span class="vline">for ${esc(d.fitLabel)}</span>` : U.status("no_value", { text: "No fit line yet" })}</td>
        <td>${fitLines}<span class="vline">of ${c.total} entries</span></td>
        <td>${best}</td>
        <td>${energyText(b)}</td>
      </tr>`;
    }).join("");
    const html = `<div class="container">
      <div class="page-head"><div><h1>Devices</h1><p>Every number on this site is measured on a real device, and each device has its own fit line. This board covers ${boards.length} device${boards.length === 1 ? "" : "s"}${sample ? ", with sample data" : ""}. New devices are added to this list when they are measured.</p></div></div>
      <div class="table-wrap" role="region" aria-label="Devices" tabindex="0"><table class="data">
        <caption class="visually-hidden">Devices in board ${esc(bi.version)}</caption>
        <thead><tr><th scope="col" class="sticky">Device</th><th scope="col">Class</th><th scope="col" class="num">Memory</th><th scope="col">Runtime</th><th scope="col" class="num">Fit line</th><th scope="col">Entries by fit</th><th scope="col">Best agent that fits</th><th scope="col">Energy</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <p class="small muted" style="margin-top:8px">An entry fits when its peak memory with 10k tokens of context stays within the device's fit line. One that fits only at 2k is ranked with a "small context only" badge; the rest are listed and never ranked. GB means 2<sup>30</sup> bytes. <a class="link" href="${ctx.link("/methodology", { at: "fit" })}">How the fit gate works</a>.</p>
      <p class="small muted" style="margin-top:4px">Cloud references run on a provider's servers, so they have no device, memory or energy here. They appear on the leaderboard in their own band, never ranked.</p>
    </div>`;
    return { title: "Devices", html };
  }

  /* ======================================================================
   * #/device/<id>
   * ==================================================================== */
  function notFound(ctx, id) {
    return {
      title: "Device not found",
      html: `<div class="container"><p class="crumbs"><a href="${ctx.link("/devices")}">Devices</a></p>${U.empty("This device is not in the board", `No device with the id <span class="mono">${esc(id || "")}</span> is published in board ${esc(ctx.boardInfo ? ctx.boardInfo.version : "")}. The list of devices shows every one that has results.`, `<a class="key key--primary" href="${ctx.link("/devices")}">See every device</a>`)}</div>`
    };
  }

  /* one row per set of weights: thinking on and off share the model's memory */
  function memRows(ctx, b, devId, leader) {
    const seen = new Map();
    localEntries(b).forEach((e) => { const k = weightsKey(e); const cur = seen.get(k); if (!cur || (cur.thinking === "on" && e.thinking !== "on")) seen.set(k, e); });
    const quantsOf = new Map();
    seen.forEach((e) => { const s = quantsOf.get(e.model.id) || new Set(); s.add(e.variant.quant); quantsOf.set(e.model.id, s); });
    const label = (e) => e.name + ((quantsOf.get(e.model.id) || new Set()).size > 1 ? ", " + (CFG.quants[e.variant.quant] || e.variant.quant) : "");
    const measured = [], missing = [];
    seen.forEach((e) => {
      const f = e.run.fit || {};
      if (f.peak_bytes_at_10240 != null) measured.push({ e, label: label(e), v: U.GiB(f.peak_bytes_at_10240), v2: U.GiB(f.peak_bytes_at_2048), href: modelLink(ctx, e, devId), hl: !!leader && weightsKey(leader) === weightsKey(e) });
      else missing.push({ e, label: label(e) });
    });
    measured.sort((a, b2) => a.v - b2.v);
    return { measured, missing, label };
  }

  async function renderDevice(ctx, id) {
    const bi = ctx.boardInfo;
    if (!bi) return notFound(ctx, id);
    const info = bi.devices.find((d) => d.id === id);
    if (!info) return notFound(ctx, id);
    const boards = await ST.loadAllDevices(bi.version);
    const b = boards.find((x) => x.device && x.device.id === id) || await ST.loadDevice(bi.version, id);
    if (!b || !b.device || b.device.id !== id) return notFound(ctx, id);
    const d = b.device, phone = isPhone(b), q = ctx.q;
    const all = localEntries(b), c = counts(b), leader = leaderOf(b);
    const budget = budgetOf(b), budgetGB = U.GiB(budget);
    const gb = d.ram_bytes ? fmt(U.GiB(d.ram_bytes), 0) + " GB" : null;
    const lb = ctx.link("/", { d: id });

    /* ---------- head ---------- */
    const lead = leader ? ` The best agent that fits is <a class="link" href="${modelLink(ctx, leader, id)}">${esc(nameOf(leader))}</a>.` : " Nothing is ranked on this device yet.";
    const head = `<p class="crumbs"><a href="${ctx.link("/devices", { d: id })}">Devices</a> / ${esc(d.name)}</p>
      <div class="page-head"><div><h1>${esc(d.name)}</h1><p>${c.fits + c.small} of ${c.total} entries fit ${esc(d.fitLabel)}${c.small ? `, ${c.small} of them only at small context` : ""}.${lead}${b.sample ? " Sample data." : ""}</p></div><a class="key key--primary" href="${lb}">Leaderboard for this device</a></div>
      ${U.facts([
        ["Memory", gb ? `<span class="num">${esc(gb)}</span>` : U.status("no_value", { text: "Not recorded" })],
        ["Chip", d.soc ? esc(d.soc) : U.status("no_value", { text: "Not recorded" })],
        ["Operating system", d.os ? esc(d.os) : U.status("no_value", { text: "Not recorded" })],
        ["Runtime", runtimeText(b)],
        ["Fit line", budget ? `<span class="num">${fmt(budgetGB, 1)} GB</span>` : U.status("no_value", { text: "No fit line yet" })],
        ["Class", phone ? "Phone" : "Laptop"],
        ["Energy", energyText(b)],
        ["Board", `${esc(b.version)}<span class="vline">results dated ${esc(U.date(b.generatedAt))}</span>`]
      ])}`;

    /* ---------- what fits ---------- */
    const M = memRows(ctx, b, id, leader);
    let fitsHtml;
    if (M.measured.length && budgetGB) {
      fitsHtml = `<figure class="chart-frame" style="margin:0">${C.budgetBars({ rows: M.measured, budget: budgetGB, budgetLabel: "Fit line", atLabel: "at 10k context", aria: `Peak memory at 10k context against the fit line for ${d.fitLabel}` })}
        <figcaption class="legend"><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><rect class="bar" x="1" y="2" width="16" height="8" rx="2"/></svg> Leading entry</span><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><rect class="bar-mute" x="1" y="2" width="16" height="8" rx="2"/></svg> Within the fit line</span><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><rect class="bar-over" x="1" y="2" width="16" height="8" rx="2"/></svg> Over the line: too big, hatched in the chart</span><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><path class="budget" d="M9 0V12"/></svg> Fit line</span></figcaption>
        ${U.howTo([
          `Each bar is one model's peak memory with 10k tokens of context on ${esc(d.name)}, at each quantisation measured. Shorter is better.`,
          `The dashed line is the fit line, ${fmt(budgetGB, 1)} GB. A bar past it is hatched and labelled "too big".`,
          "An entry that is over the line at 10k but within it at 2k is still ranked, with a \"small context only\" badge. The verdict comes from the store's fit gate; the table under the chart gives it for every entry.",
          "Thinking on and off share a model's weights and memory, so they share one bar.",
          "GB means 2<sup>30</sup> bytes. Memory is the process's physical footprint, runtime included."
        ])}
        ${U.asTable(["Entry", "Peak at 2k, GB", "Peak at 10k, GB", "Fit line, GB", "Verdict"], M.measured.map((r) => [esc(r.label), r.v2 != null ? fmt(r.v2, 1) : statusText("no_value"), fmt(r.v, 1), fmt(budgetGB, 1), esc(fitWords(r.e))]), "What fits " + d.name)}</figure>`;
    } else {
      fitsHtml = `<div class="chart-frame">${U.empty(budget ? "No memory measured yet" : "No fit line for this device yet", "The fit gate has no peak-memory measurements to draw for this device in this board. Nothing is ranked until it does.")}${U.howTo(["Each bar will be one model's peak memory at 10k context, against the device's fit line.", "Bars past the line are hatched and labelled \"too big\"."])}</div>`;
    }
    const missingHtml = M.missing.length ? `<ul class="checks" style="margin-top:var(--space-sm)">${M.missing.map((r) => `<li>${U.status("not_measured")} <span><a class="link" href="${modelLink(ctx, r.e, id)}">${esc(r.label)}</a> <span class="muted small">has no memory measurement here, so it is listed and not ranked.</span></span></li>`).join("")}</ul>` : "";

    /* ---------- how they run here ---------- */
    const GROUP_ORDER = { ranked: 0, invalid: 1, not_measured: 2, does_not_fit: 3 };
    const sortId = PERF_COLS.includes(q.get("s")) ? q.get("s") : null;
    const order = sortId ? (q.get("o") || (R.metric[sortId].better === "low" ? "asc" : "desc")) : null;
    const rows = all.slice().sort((a, b2) => {
      if (sortId) {
        const va = val(a, sortId), vb = val(b2, sortId);
        if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1;
        return order === "asc" ? va - vb : vb - va;
      }
      return ((GROUP_ORDER[a.group] ?? 4) - (GROUP_ORDER[b2.group] ?? 4)) || ((val(b2, HEAD) ?? -1) - (val(a, HEAD) ?? -1));
    });
    const thSort = (mid) => {
      const m = R.metric[mid], on = sortId === mid;
      const unit = m.format === "ctx" ? "tokens" : m.unit;
      return `<th scope="col" class="num"${on ? ` aria-sort="${order === "asc" ? "ascending" : "descending"}"` : ""}><button type="button" data-sort="${mid}" title="${esc(m.name + (m.desc ? ". " + m.desc : ""))}">${esc(m.short)}${on ? (order === "asc" ? " ↑" : " ↓") : ""}</button><span class="vline">${esc(unit + (m.better === "low" ? ", lower is better" : m.better === "high" ? ", higher is better" : ""))}</span></th>`;
    };
    const runTable = all.length ? `<div class="table-wrap" role="region" aria-label="How every entry runs on ${esc(d.name)}" tabindex="0"><table class="data">
        <caption class="visually-hidden">Speed and memory of every entry on ${esc(d.name)}. Column headers sort the table.</caption>
        <thead><tr><th scope="col" class="sticky">Entry</th><th scope="col">Fit</th>${PERF_COLS.map(thSort).join("")}</tr></thead>
        <tbody>${rows.map((e) => `<tr class="${e === leader ? "is-top " : ""}${e.group === "ranked" ? "" : "row-dim"}"><th scope="row" class="sticky"><span class="mname"><a href="${modelLink(ctx, e, id)}">${esc(e.name)}</a></span><span class="vline">${esc(U.entryLine(e))} ${U.refBadge(e)} ${e.group === "invalid" ? U.fitBadge(e) : ""}</span></th><td>${fitCell(e)}</td>${PERF_COLS.map((mid) => `<td class="num">${mid === "max_ctx" && val(e, mid) === 0 ? U.status("does_not_fit", { text: "None" }) : U.cell(e, mid)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table></div>
      <p class="small muted" style="margin-top:8px">Speed and memory belong to the weights, so thinking-on and thinking-off entries of one model show the same numbers. Entries that do not fit are measured here too, for reference, and are never ranked. ${sortId ? `<a class="link" href="${ctx.linkHere({ s: null, o: null })}">Back to the default order</a>.` : "Ranked entries come first, by agentic score."}</p>`
      : U.empty("No entries on this device", "Nothing has been published for this device in this board.");

    /* ---------- context scaling ---------- */
    const reps = new Map();
    all.forEach((e) => { const k = weightsKey(e); const cur = reps.get(k); if (!cur || (cur.thinking === "on" && e.thinking !== "on") || (cur.group !== "ranked" && e.group === "ranked")) reps.set(k, e); });
    const withCurve = [...reps.values()].filter((e) => ST.curveFor(e, MEM_CURVE));
    const bestOf = (e) => Math.max(-1, ...all.filter((x) => weightsKey(x) === weightsKey(e)).map((x) => val(x, HEAD) ?? -1));
    const rankedReps = withCurve.filter((e) => all.some((x) => weightsKey(x) === weightsKey(e) && x.group === "ranked")).sort((a, b2) => bestOf(b2) - bestOf(a));
    const otherReps = withCurve.filter((e) => !rankedReps.includes(e)).sort((a, b2) => a.name.localeCompare(b2.name));
    const repByKey = new Map(withCurve.map((e) => [e.key, e]));
    let chosen = (q.get("cs") || "").split(",").map((k) => repByKey.get(k)).filter(Boolean);
    chosen = chosen.filter((e, i) => chosen.indexOf(e) === i).slice(0, 4);
    if (!q.get("cs")) chosen = rankedReps.slice(0, 4);
    const leadRep = leader ? reps.get(weightsKey(leader)) : null;
    let n = 0;
    const series = chosen.map((e) => {
      const cls = e === leadRep ? "A" : String(++n > 4 ? 4 : n);
      const cv = ST.curveFor(e, MEM_CURVE);
      const points = CFG.contextLevels.map((lvl) => {
        const p = cv.points.find((x) => x.level === lvl);
        if (!p) return { status: "not_run", label: statusText("not_run") };
        if (p.status === "ok" && p.value != null) return { v: U.GiB(p.value) };
        return { status: p.status, need: U.GiB(p.need_bytes), label: statusText(p.status) };
      });
      return { e, name: M.label(e), short: M.label(e), cls, points };
    });
    series.forEach((s, i) => { s.shape = SHAPES[i % SHAPES.length]; });
    const slotOpts = (cur) => `<option value="">None</option>${rankedReps.length ? `<optgroup label="Ranked">${rankedReps.map((e) => `<option value="${esc(e.key)}"${cur === e.key ? " selected" : ""}>${esc(M.label(e))}</option>`).join("")}</optgroup>` : ""}${otherReps.length ? `<optgroup label="Listed, not ranked">${otherReps.map((e) => `<option value="${esc(e.key)}"${cur === e.key ? " selected" : ""}>${esc(M.label(e))}, ${esc(e.group === "does_not_fit" ? "does not fit" : e.group === "invalid" ? "invalid run" : e.group === "not_measured" ? "fit not measured" : statusText(e.run.status))}</option>`).join("")}</optgroup>` : ""}`;
    const slots = withCurve.length ? `<div class="toolbar" role="group" aria-label="Lines on the context chart">${[0, 1, 2, 3].map((i) => `<label class="field"><span>Line ${i + 1}</span><select class="select" data-cs="${i}">${slotOpts(chosen[i] ? chosen[i].key : "")}</select></label>`).join("")}</div>` : "";
    const failures = [];
    series.forEach((s) => s.points.forEach((p, i) => { if (p.status && p.status !== "not_run") failures.push(`${s.name}: ${statusText(p.status).toLowerCase()} at ${CFG.contextLabels[i]}`); }));
    const ctxHtml = !withCurve.length
      ? `<div class="chart-frame">${U.empty("Not in this board yet", "No memory-over-context curves are published for this device.")}${U.howTo(["Each line will be one model's peak memory at seven context sizes, against the fit line."])}</div>`
      : !series.length
        ? `${slots}<div class="chart-frame">${U.empty("No lines chosen", "Pick up to four entries above to draw their memory over context.")}</div>`
        : `${slots}<figure class="chart-frame" style="margin:var(--space-sm) 0 0">${C.contextChart({ series, levels: CFG.contextLevels, labels: CFG.contextLabels, budget: budgetGB || null, budgetLabel: "Fit line", unit: "GB", d: 1, labelRight: true, aria: `Peak memory over context on ${d.name}` })}
          <figcaption class="legend">${series.map((s) => `<span>${C.shapeSwatch(s.shape, "f" + s.cls)} ${esc(s.name)}${s.cls === "A" ? " (leading entry)" : ""}</span>`).join("")}${budgetGB ? '<span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><path class="budget" d="M1 6H17"/></svg> Fit line</span>' : ""}</figcaption>
          ${failures.length ? `<p class="small muted" style="margin-top:4px">${failures.map(esc).join(". ")}.</p>` : ""}
          ${U.howTo([
            `Each line is one model's peak memory at seven context sizes, from ${esc(CFG.contextLabels[0])} to ${esc(CFG.contextLabels[CFG.contextLabels.length - 1])} tokens, on ${esc(d.name)}. Lower is better.`,
            budgetGB ? `The dashed line is the fit line, ${fmt(budgetGB, 1)} GB. The fit gate checks it at 10k context.` : "No fit line is published for this device yet.",
            "A cross marked \"out of memory\" is where the device ended the run; the line stops there because there is nothing to draw, not because the result is missing.",
            "A hatched band marked \"beyond window\" is longer than the model's context window.",
            "Each line has its own shape and colour, named in the legend. The cobalt line is the leading entry.",
            "Up to four lines at a time. The menus above choose them, including entries that are listed but not ranked."
          ])}
          ${U.asTable(["Entry"].concat(CFG.contextLabels.map((l) => l + ", GB")), series.map((s) => [esc(s.name)].concat(s.points.map((p) => (p.v != null ? fmt(p.v, 1) : U.status(p.status))))), "Peak memory over context")}</figure>`;

    /* ---------- simulated day ---------- */
    const dayIds = R.metrics.filter((m) => m.group === "day").map((m) => m.id);
    const ranked = byScore(all.filter((e) => e.group === "ranked"));
    const batt = R.metric.day_battery;
    const battRows = ranked.map((e) => {
      const cc = batt ? ST.value(e, batt.id) : { status: "not_in_board" };
      return { label: nameOf(e), v: cc.status === "ok" ? cc.value : null, ci: cc.interval || null, est: phone, status: statusText(cc.status), st: cc.status, href: modelLink(ctx, e, id), hl: e === leader };
    });
    const anyBatt = battRows.some((r) => r.v != null);
    const allIphone = battRows.length && battRows.every((r) => r.st === "not_applicable_iphone");
    const dayHow = U.howTo([
      `Each bar is the share of a full battery one simulated day used with that entry kept loaded on ${esc(d.name)}: forty scripted tasks across a working day, with the idle time between them.`,
      "Shorter is better. The scale runs from 0 to 100% of one full charge.",
      phone ? "On the iPhone the share comes from the battery gauge, so every bar is hatched and marked \"est.\" for estimate." : "On the Mac the energy comes from the on-board power counters.",
      "Whiskers, where shown, are 95% intervals."
    ]);
    const dayTable = U.asTable(["Entry", "Battery used over the day"], battRows.map((r) => [esc(r.label), r.v != null ? fmt(r.v, 0) + "%" + (r.est ? " (estimate)" : "") : U.status(r.st)]), "Battery used over a simulated day");
    const battChart = battRows.length ? C.hbars({ rows: battRows, labelW: 220, domain: [0, 100], unit: "%", dp: 0, tickStep: 25, aria: `Battery used over a simulated day on ${d.name}` }) : "";
    const dayChart = anyBatt
      ? `<figure class="chart-frame" style="margin:0">${battChart}${phone ? '<figcaption class="legend"><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><rect class="bar-mute" x="1" y="2" width="16" height="8" rx="2" stroke="currentColor" stroke-opacity=".35"/></svg> Hatched: estimate from the battery gauge</span></figcaption>' : ""}${dayHow}${dayTable}</figure>`
      : `<figure class="chart-frame" style="margin:0">${U.empty(allIphone ? statusText("not_applicable_iphone") : statusText("not_in_board"), allIphone ? "This measure is Mac only in version 1." : `No simulated day has been published for ${esc(d.name)} in this board. When it is, each ranked entry gets a bar here${phone ? ", hatched as an estimate from the battery gauge" : ""}.`)}${battChart ? `<div aria-hidden="true" style="opacity:.55;margin-top:var(--space-sm)">${battChart}</div>` : ""}${dayHow}${dayTable}</figure>`;
    const dayAny = ranked.some((e) => dayIds.some((mid) => ST.value(e, mid).status === "ok"));
    const otherDay = dayIds.filter((mid) => mid !== "day_battery");
    const fitLoad = R.metric.day_fit_load || null;
    const dayFigures = dayAny
      ? `<div class="table-wrap" role="region" aria-label="Simulated day measures" tabindex="0" style="margin-top:var(--space-md)"><table class="data"><thead><tr><th scope="col" class="sticky">Entry</th>${otherDay.concat(fitLoad ? [fitLoad.id] : []).map((mid) => `<th scope="col" class="num" title="${esc(R.metric[mid].desc || R.metric[mid].name)}">${esc(R.metric[mid].name)}<span class="vline">${esc(R.metric[mid].unit || "")}</span></th>`).join("")}</tr></thead><tbody>${ranked.map((e) => `<tr><th scope="row" class="sticky"><span class="mname"><a href="${modelLink(ctx, e, id)}">${esc(e.name)}</a></span><span class="vline">${esc(U.entryLine(e))}</span></th>${otherDay.concat(fitLoad ? [fitLoad.id] : []).map((mid) => `<td class="num">${U.cell(e, mid)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
      : (() => {
        const probe = ranked[0] || all[0];
        const st = (mid) => (probe ? U.status(ST.value(probe, mid).status) : U.status("not_in_board"));
        const items = otherDay.map((mid) => [R.metric[mid].name, st(mid)]);
        items.push(["Fit under a background load", fitLoad ? st(fitLoad.id) : U.status("not_in_board")]);
        return `<div style="margin-top:var(--space-md)">${U.facts(items)}</div>`;
      })();

    /* ---------- sustained load ---------- */
    const susCurves = ranked.map((e) => ({ e, c: b.curve(e.key, SUSTAIN_CURVE) })).filter((x) => x.c && (x.c.points || []).some((p) => p.value != null)).slice(0, 3);
    const susHow = U.howTo([
      "Decode speed over 30 minutes of back-to-back tasks, one line per entry, for the best entries that fit.",
      "Where a line falls and then flattens, the device is slowing down under heat; the flat part is the speed in long use.",
      "Each line has its own shape and colour, named in the legend and at the line's end."
    ]);
    const susHtml = susCurves.length
      ? (() => {
        const ss = susCurves.map(({ e, c: cv }, i) => ({ name: nameOf(e), short: e.name, cls: e === leader ? "A" : String(i + 1), shape: SHAPES[i], points: cv.points.filter((p) => p.value != null).map((p) => ({ t: p.minute != null ? p.minute : p.t, v: p.value })) }));
        return `<figure class="chart-frame" style="margin:0">${C.sustain({ series: ss, aria: `Sustained decode speed on ${d.name}` })}<figcaption class="legend">${ss.map((s) => `<span>${C.shapeSwatch(s.shape, "f" + s.cls)} ${esc(s.name)}</span>`).join("")}</figcaption>${susHow}${U.asTable(["Entry", "Minute", "Decode, tok/s"], ss.flatMap((s) => s.points.map((p) => [esc(s.name), fmt(p.t, 0), fmt(p.v, 1)])), "Sustained decode speed")}</figure>`;
      })()
      : `<div class="chart-frame">${U.empty(statusText("not_in_board"), `No sustained-load run has been published for ${esc(d.name)} in this board. When it is, this chart shows how decode speed holds over 30 minutes of back-to-back work.`)}${susHow}</div>`;

    const html = `<div class="container" data-device-page="${esc(id)}">
      ${head}
      ${U.secHead("What fits", `Peak memory at 10k context against the fit line for ${esc(d.fitLabel)}. Bars past the line are hatched and labelled "too big".`, "", "fits")}
      ${fitsHtml}
      ${missingHtml}
      ${U.secHead("How they run here", `Every entry on ${esc(d.name)}: fit verdict, speed at 2k context, memory at 10k, the longest context that fits, and load time.`, "", "run")}
      ${runTable}
      ${U.secHead("Context scaling here", "Peak memory as the context grows, for up to four entries. Failures are drawn where they happen.", "", "context")}
      ${ctxHtml}
      ${U.secHead("Simulated day", `Battery used over one scripted working day with the model kept loaded${phone ? ", as an estimate from the battery gauge" : ""}, and what else the day measures.`, "", "day")}
      ${dayChart}
      ${dayFigures}
      ${U.secHead("Under sustained load", "Decode speed over 30 minutes of back-to-back tasks.", "", "sustain")}
      ${susHtml}
      <p class="small muted" style="margin-top:var(--space-lg)">${esc(d.name)}, board ${esc(b.version)}, results dated ${esc(U.date(b.generatedAt))}. <a class="link" href="${ctx.link("/methodology", { d: id, at: "perf" })}">How speed, memory and energy are measured</a>.</p>
    </div>`;
    return { title: d.name, html };
  }

  /* On a device page the top bar's device menu moves to the chosen device's page. It is wired once, at the document,
   * in the capture phase, so it runs before the shell's own handler. */
  let navWired = false;
  function wireNav() {
    if (navWired) return; navWired = true;
    document.addEventListener("change", (ev) => {
      const t = ev.target;
      if (!t || t.id !== "nav-device") return;
      if (document.documentElement.getAttribute("data-page") !== "devices" || !document.querySelector("[data-device-page]")) return;
      ev.stopPropagation();
      EDAT.app.go(EDAT.app.link("/device/" + U.seg(t.value), { d: t.value }));
    }, true);
  }

  async function render(ctx) {
    if (ctx.parts[0] === "device") return renderDevice(ctx, ctx.parts[1]);
    return renderList(ctx);
  }

  function after(root, ctx) {
    if (ctx.parts[0] !== "device") return;
    wireNav();
    const id = ctx.parts[1];
    const sel = document.getElementById("nav-device");
    if (sel && [...sel.options].some((o) => o.value === id)) sel.value = id;
    root.querySelectorAll("select[data-cs]").forEach((s) => s.addEventListener("change", () => {
      const keys = [...root.querySelectorAll("select[data-cs]")].map((x) => x.value).filter(Boolean);
      const uniq = keys.filter((k, i) => keys.indexOf(k) === i);
      ctx.setQ({ cs: uniq.length ? uniq.join(",") : "none" });
    }));
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.devices = { render, after };
})();
