/* Compare: two to four entries on one device, local or cloud. Chips with remove buttons and an "Add an entry"
 * select; the Show tray (values, difference from tuned, difference from base); one table with the measure column
 * pinned, grouped by the registry's groups, with a "best" mark only where the leader's lead over every other entry is
 * larger than the margin of error; family dots with one shape and colour per entry; and overlaid context curves. */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store, C = EDAT.charts, CFG = EDAT.config;
  const { esc, fmt, I } = U;
  const HEAD = "agentic_score";
  const SHAPES = ["circle", "square", "triangle", "diamond"];
  const LEVELS = CFG.contextLevels, LABELS = CFG.contextLabels;

  const ok = (c) => !!c && c.status === "ok";
  const val = (e, id) => { const c = ST.value(e, id); return ok(c) ? c.value : null; };
  const nameOf = (e) => e.name + (e.thinkingText ? ", " + e.thinkingText : "");
  const statusText = (key) => (R.statuses[key] || { text: String(key).replace(/_/g, " ") }).text;
  const swatch = (i) => C.shapeSwatch(SHAPES[i], "f" + (i + 1));

  /* listed-not-ranked entries keep their speed and memory but show why in place of a score, as on the leaderboard */
  function withheld(e) {
    if (e.cloud) return null;
    if (e.group === "does_not_fit" || e.run.status === "does_not_fit") return "does_not_fit";
    if (e.group === "invalid" || e.run.status === "invalid") return "invalid";
    if (e.run.status === "failed") return "failed";
    if (e.run.status === "not_run") return "not_run";
    if (e.group === "not_measured") return "not_measured";
    if (e.group !== "ranked") return e.group || "not_run";
    return null;
  }
  const DEVICE_GROUPS = ["perf", "energy", "day"];
  const shown = (e, m) => !withheld(e) || DEVICE_GROUPS.includes(m.group);
  const cellValue = (e, m) => (shown(e, m) ? ST.value(e, m.id) : { status: withheld(e) });

  /* the margin a lead must beat: the two half-intervals; rare events use the interval of the share of trials */
  function measure(e, m) {
    const c = cellValue(e, m);
    if (!ok(c)) return null;
    if (m.safety) {
      const iv = c.rateInterval;
      return c.rate != null && iv ? { v: c.value, t: c.rate, h: (iv[1] - iv[0]) / 2 } : { v: c.value, t: c.value, h: null };
    }
    return { v: c.value, t: c.value, h: c.half != null ? c.half : null };
  }
  function verdict(entries, m) {
    const xs = entries.map((e, i) => ({ e, i, x: measure(e, m) })).filter((o) => o.x);
    if (xs.length < 2) return { best: -1, html: `<span class="sig same">${xs.length ? "Only one entry has a value" : "No values"}</span>` };
    if (!m.better) return { best: -1, html: `<span class="sig same">Neither higher nor lower is better</span>` };
    if (xs.some((o) => o.x.h == null)) return { best: -1, html: `<span class="sig same">Measured without an interval, no mark</span>` };
    const sorted = xs.slice().sort((a, b) => (m.better === "low" ? a.x.t - b.x.t : b.x.t - a.x.t));
    const lead = sorted[0];
    const beats = (o) => Math.abs(lead.x.t - o.x.t) > Math.sqrt(lead.x.h ** 2 + o.x.h ** 2);
    if (sorted.slice(1).every(beats)) return { best: lead.i, html: `<span class="sig best">${I.check}${esc(nameOf(lead.e))} is better</span>` };
    return { best: -1, html: `<span class="sig same">No meaningful difference</span>` };
  }

  function defaults(board) {
    const top = ST.bestPerModel(board.entries.filter((e) => !e.cloud && e.group === "ranked" && !withheld(e)), HEAD)
      .sort((a, b) => (val(b, HEAD) ?? -1) - (val(a, HEAD) ?? -1)).slice(0, 3).map((e) => e.key);
    const t = board.references.tuned;
    if (t && board.entry(t) && !top.includes(t)) top.push(t);
    return top.slice(0, 4);
  }

  function render(ctx) {
    const board = ctx.device, cloud = ctx.cloud, d = board.device, q = ctx.q;
    const find = (k) => board.entry(k) || (cloud && cloud.entry(k)) || null;
    let keys = [...new Set((q.get("m") || "").split(",").map((k) => k.trim()).filter(Boolean))].filter(find).slice(0, 4);
    const asked = (q.get("m") || "").split(",").filter(Boolean).length;
    const fellBack = !keys.length;
    if (fellBack) keys = defaults(board);
    const entries = keys.map(find);
    const vs = ["tuned", "base"].includes(q.get("vs")) ? q.get("vs") : "";
    const refBoardFor = (e) => (e.cloud ? board : null);

    /* ---------- chips and the add select ---------- */
    const chips = `<div class="chips" role="group" aria-label="Chosen entries">${entries.map((e, i) => `<span class="chip" data-key="${esc(e.key)}"><svg class="sw chart" viewBox="0 0 14 14" aria-hidden="true">${C.marker(SHAPES[i], 7, 7, 4.5, "f" + (i + 1) + " mk", "")}</svg><a href="${esc(ctx.link("/model/" + U.seg(e.model.id), { v: e.key }))}" style="color:inherit">${esc(nameOf(e))}</a>${entries.length > 2 ? `<button type="button" data-remove="${esc(e.key)}" aria-label="Remove ${esc(nameOf(e))}">${I.cross}</button>` : ""}</span>`).join("")}
      ${entries.length < 4 ? (() => {
        const opt = (e) => `<option value="${esc(e.key)}">${esc(nameOf(e) + ", " + e.line)}</option>`;
        const localOpts = board.entries.filter((e) => !keys.includes(e.key)).map(opt).join("");
        const cloudOpts = cloud ? cloud.entries.filter((e) => !keys.includes(e.key)).map(opt).join("") : "";
        return `<label class="visually-hidden" for="cmp-add">Add an entry</label><select class="select" id="cmp-add" data-add="1" style="max-width:100%"><option value="">Add an entry</option>${localOpts ? `<optgroup label="${esc("On " + d.name)}">${localOpts}</optgroup>` : ""}${cloudOpts ? `<optgroup label="Cloud references, never ranked">${cloudOpts}</optgroup>` : ""}</select>`;
      })() : `<span class="small muted">Four entries at most. Remove one to add another.</span>`}</div>`;

    const vsTray = U.tray("vs", "Show", [{ v: "", l: "Values" }, { v: "tuned", l: "Difference from tuned reference" }, { v: "base", l: "Difference from base reference" }], vs);
    const devSel = U.selectField("d", "Device", ctx.boardInfo.devices.map((x) => ({ v: x.id, l: x.name })), ctx.devId);
    const headHtml = `<div class="page-head"><div><h1>Compare entries</h1><p>Two to four entries on one device. A cell is marked best only when its lead is larger than the margin of error against every other entry; otherwise the row says there is no meaningful difference.</p></div><div class="toolbar">${devSel}${vsTray}</div></div>`;
    const fellNote = fellBack && asked ? `<p class="note-planned" style="margin-bottom:var(--space-sm)">${I.info}<span>The entries in the link are not on ${esc(d.name)}, so the leading entries and the tuned reference are shown instead.</span></p>` : "";

    if (entries.length < 2) {
      return { title: "Compare", html: `<div class="container">${headHtml}${fellNote}${chips}${U.empty("Add one more entry", "Choose at least two entries to compare. The select above lists every entry on this device and the cloud references.")}</div>` };
    }

    /* ---------- the table ---------- */
    const order = [...new Set(["agentic", "safety"].concat(R.groups.map((g) => g.id)))];
    const gname = (g) => (R.group[g] ? R.group[g].name : "Restraint and privacy");
    const planned = [], empty = [];
    const sections = order.map((g) => {
      const ms = R.metrics.filter((m) => m.group === g);
      const rows = ms.filter((m) => {
        if (entries.some((e) => ok(cellValue(e, m)))) return true;
        (m.planned ? planned : empty).push(m);
        return false;
      }).map((m) => {
        const v = verdict(entries, m);
        const cells = entries.map((e, i) => {
          let html;
          if (!shown(e, m)) html = U.status(withheld(e), withheld(e) === "invalid" ? { text: "Invalid run" } : null);
          else if (vs && e.refRole !== vs) html = U.deltaCell(e, m.id, vs, refBoardFor(e));
          else html = U.cell(e, m.id) + (vs && e.refRole === vs ? `<span class="vline">reference</span>` : "");
          return `<td class="num${i === v.best ? " best-cell" : ""}">${html}${i === v.best ? `<span class="visually-hidden">, best</span>` : ""}</td>`;
        }).join("");
        const unit = m.unit ? ` <span class="muted xsmall">${esc(m.unit)}${m.better === "low" ? ", lower is better" : ""}</span>` : "";
        return `<tr><th scope="row" class="sticky">${esc(m.name)}${unit}</th>${cells}<td>${v.html}</td></tr>`;
      });
      return rows.length ? `<tr class="divider-row"><td colspan="${entries.length + 2}">${esc(gname(g))}</td></tr>${rows.join("")}` : "";
    }).join("");
    const thead = `<thead><tr><th scope="col" class="sticky">Measure</th>${entries.map((e, i) => `<th scope="col" class="num"><span style="display:inline-flex;gap:6px;align-items:center"><svg class="chart" viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">${C.marker(SHAPES[i], 7, 7, 4.5, "f" + (i + 1) + " mk", "")}</svg><a href="${esc(ctx.link("/model/" + U.seg(e.model.id), { v: e.key }))}">${esc(nameOf(e))}</a></span><span class="vline">${esc(e.line)}</span><span class="vline">${U.refBadge(e)} ${U.fitBadge(e)}</span></th>`).join("")}<th scope="col">Difference</th></tr></thead>`;
    const table = `<div class="table-wrap" role="region" aria-label="Comparison table" tabindex="0"><table class="data"><caption class="visually-hidden">${esc(entries.map(nameOf).join(", "))} on ${esc(d.name)}${vs ? ", shown as the difference from the " + vs + " reference" : ""}</caption>${thead}<tbody>${sections}</tbody></table></div>`;
    const missingNote = planned.length || empty.length ? `<p class="note-planned" style="margin-top:var(--space-sm)">${I.info}<span>${planned.length ? `Not in this board yet, so not shown: ${esc(planned.map((m) => m.name).join(", "))}.` : ""}${empty.length ? ` No chosen entry has a value for: ${esc(empty.map((m) => m.name).join(", "))}.` : ""}</span></p>` : "";
    const withheldNote = entries.some(withheld) ? `<p class="small muted" style="margin-top:var(--space-xs)">Entries that do not fit, are invalid or have no fit measurement are listed, never ranked: their scores read as the reason, and only their speed and memory are shown.</p>` : "";

    /* ---------- family dots ---------- */
    const legend = `<div class="legend" aria-label="Legend">${entries.map((e, i) => `<span>${swatch(i)} ${esc(nameOf(e))}</span>`).join("")}</div>`;
    const famIds = R.metrics.filter((m) => m.group === "families" && !m.planned).map((m) => m.id).concat(["lane_assistant", "lane_neutral"].filter((id) => R.metric[id]));
    const famRows = famIds.map((id) => {
      const m = R.metric[id];
      const series = entries.map((e, i) => { const c = cellValue(e, m); return { v: ok(c) ? c.value : null, ci: ok(c) ? c.half || 0 : 0, cls: "f" + (i + 1), shape: SHAPES[i], tip: `${nameOf(e)}\n${m.name}: ${ok(c) ? fmt(c.value, 1) + (c.half != null ? " ±" + fmt(c.half, 1) : "") : statusText(c.status)}` }; });
      const any = series.some((s) => s.v != null);
      return { label: m.name, v: any ? 1 : null, missing: "No chosen entry has a value", series, href: ctx.link("/tests/agentic"), cells: entries.map((e) => cellValue(e, m)) };
    }).filter((r) => r.v != null);
    const famHtml = famRows.length ? U.secHead("Agentic families", "Pass rate per family, each the mean of the assistant lane and the neutral lane, then the two lanes. One shape and colour per entry.", "", "families") +
      `<figure style="margin:0;max-width:760px">${C.dotRows({ rows: famRows, domain: [0, 100], step: 20, aria: "Pass rate by family for each chosen entry", labelW: 190, rowH: 16 + entries.length * 8 })}${legend}
        ${U.howTo(["One row per family; one marker per entry, in the shapes and colours of the legend, so identity never rests on colour alone.", "The marker is the pass rate on a 0 to 100 scale; the whisker is its 95% interval. Where two whiskers overlap, the difference may be noise.", "Entries that are listed but not ranked have no markers here. Each family name opens the agentic cases."])}
        ${U.asTable(["Family"].concat(entries.map(nameOf)), famRows.map((r) => [`<a href="${esc(r.href)}">${esc(r.label)}</a>`].concat(r.cells.map((c) => (ok(c) ? fmt(c.value, 1) + (c.half != null ? ` <span class="muted xsmall">±${fmt(c.half, 1)}</span>` : "") : U.statusFor(c))))), "Pass rate by family")}</figure>` : "";

    /* ---------- context curves, local entries only ---------- */
    const local = entries.map((e, i) => ({ e, i })).filter((o) => !o.e.cloud);
    const budgetB = board.entries.map((e) => (e.run.fit || {}).budget_bytes).find(Boolean);
    const budget = budgetB ? U.GiB(budgetB) : null;
    const pts = (e, metric, scale) => {
      const c = ST.curveFor(e, metric);
      if (!c) return null;
      return LEVELS.map((lv) => { const p = c.points.find((x) => x.level === lv); if (!p) return { v: null, status: "not_run", label: statusText("not_run") }; if (p.status === "ok" && p.value != null) return { v: p.value * scale, status: "ok" }; return { v: null, status: p.status, need: p.need_bytes != null ? U.GiB(p.need_bytes) : null, needBytes: p.need_bytes, label: statusText(p.status) }; });
    };
    const CURVES = [
      { m: "footprint_peak_bytes", title: "Peak memory", sub: "GB, lower is better", unit: "GB", d: 1, scale: 1 / 1073741824, mem: true },
      { m: "decode_tps", title: "Decode speed", sub: "tok/s, higher is better", unit: "tok/s", d: 1, scale: 1 }
    ];
    let ctxHtml = "";
    if (local.length) {
      const figs = CURVES.map((cv) => {
        const series = local.map(({ e, i }) => ({ e, i, points: pts(e, cv.m, cv.scale) })).filter((s) => s.points).map((s) => ({ name: nameOf(s.e), short: s.e.name.length > 14 ? s.e.name.slice(0, 13) + "…" : s.e.name, cls: String(s.i + 1), shape: SHAPES[s.i], points: s.points, e: s.e }));
        const how = U.howTo(cv.mem
          ? [`One line per entry, in the shapes and colours of the legend.${budget ? ` The dashed line is the fit line for ${esc(d.fitLabel)}, ${fmt(budget, 1)} GB; read each line at 10k, where under the fit line means it fits.` : ""}`, "A cross marks where an entry ran out of the device's real memory; hatching is beyond its context window. Cloud references have no curve: they run on no device."]
          : ["Writing speed at each context length, after the prompt is read. Higher is better; all fall as context grows.", "A failed length is marked where it happened rather than leaving the line to end silently."]);
        if (!series.length) return `<figure class="chart-frame" style="margin:0"><figcaption class="chart-title">${esc(cv.title)}</figcaption><div class="chart-sub">${esc(cv.sub)}</div>${U.empty("Not measured", "None of the chosen local entries has this curve in this board.")}${how}</figure>`;
        const table = U.asTable(["Context, tokens"].concat(series.map((s) => s.name)), LEVELS.map((lv, k) => [esc(LABELS[k])].concat(series.map((s) => { const p = s.points[k]; return p.v != null ? fmt(p.v, cv.d) : U.statusFor({ status: p.status, need: p.needBytes }); }))), cv.title + " by context");
        return `<figure style="margin:0"><figcaption class="chart-title">${esc(cv.title)}</figcaption><div class="chart-sub">${esc(cv.sub)}</div>${C.contextChart({ series: series.map(({ e, ...s }) => s), levels: LEVELS, labels: LABELS, unit: cv.unit, d: cv.d, budget: cv.mem ? budget : null, budgetLabel: "Fit line", labelRight: true, aria: cv.title + " by context length for each chosen entry" })}${how}${table}</figure>`;
      }).join("");
      const sharedNote = local.some(({ e }) => !e.board.curve(e.key, "decode_tps") && ST.curveFor(e, "decode_tps")) ? `<p class="small muted" style="margin-top:var(--space-xs)">An entry without its own curves uses those of its other thinking mode with the same weights: speed and memory belong to the weights.</p>` : "";
      ctxHtml = U.secHead("Context scaling", `The same device for every entry, so the fit line is shared.${entries.some((e) => e.cloud) ? " Cloud references are left out: they have no device." : ""}`, "", "context") +
        `<div class="grid-2">${figs}</div><div class="legend" aria-label="Legend">${local.map(({ e, i }) => `<span>${swatch(i)} ${esc(nameOf(e))}</span>`).join("")}</div>${sharedNote}`;
    }

    const conditions = `<p class="conditions" style="margin-top:var(--space-sm)"><strong>${esc(d.name)}</strong>, ${entries.length} entries${entries.some((e) => e.cloud) ? ", cloud references never ranked" : ""}. Score intervals are 95%; differences marked ≈ are unpaired estimates; speed and memory carry no interval, so they never get a mark. Board ${esc(board.version)}, results dated ${esc(U.date(board.generatedAt))}.${board.sample ? " Sample data." : ""}</p>`;
    const html = `<div class="container">${headHtml}${fellNote}${chips}${conditions}${table}${missingNote}${withheldNote}${famHtml}${ctxHtml}</div>`;
    return { title: "Compare", html };
  }

  function after(root, ctx) {
    const chosen = () => [...root.querySelectorAll(".chip[data-key]")].map((c) => c.dataset.key);
    root.querySelectorAll("button[data-remove]").forEach((b) => b.addEventListener("click", () => {
      ctx.setQ({ m: chosen().filter((k) => k !== b.dataset.remove).join(",") });
    }));
    const add = root.querySelector("select[data-add]");
    if (add) add.addEventListener("change", () => {
      if (!add.value) return;
      const keys = chosen();
      if (!keys.includes(add.value)) keys.push(add.value);
      ctx.setQ({ m: keys.slice(0, 4).join(",") });
    });
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.compare = { render, after };
})();
