/* Shared builders for every page: icons, number formats, status cells, score and difference cells, trays,
 * section heads, "Show the numbers as a table", "How to read this chart", and the loading, empty and error states.
 * Everything returns HTML strings; values from the data are always escaped. */
(function () {
  "use strict";
  const EDAT = (window.EDAT = window.EDAT || {});
  const R = EDAT.registry, CFG = EDAT.config;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d) => (v == null || Number.isNaN(v) ? "" : Number(v).toLocaleString("en-GB", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }));
  const signed = (v, d) => (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v), d);
  const GiB = (b) => (b == null ? null : b / 1073741824);
  const ctxLabel = (tokens) => { const i = CFG.contextLevels.indexOf(tokens); return i > -1 ? CFG.contextLabels[i] : tokens >= 1024 ? Math.round(tokens / 1024) + "k" : String(tokens); };
  const seg = (id) => encodeURIComponent(id);
  const date = (iso) => (iso ? String(iso).slice(0, 10) : "");

  /* inline SVG icons, never emoji */
  const I = {
    logo: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="12" width="4" height="9" rx="1" fill="currentColor"/><rect x="10" y="7" width="4" height="14" rx="1" fill="currentColor" opacity=".7"/><rect x="17" y="3" width="4" height="18" rx="1" fill="currentColor" opacity=".45"/></svg>',
    check: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    cross: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    warn: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 2l6.5 11.5h-13L8 2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6.5v3.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.6" r=".9" fill="currentColor"/></svg>',
    shield: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.8l5 2v4c0 3-2.2 5.2-5 6.4C5.2 13 3 10.8 3 7.8v-4l5-2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M5.8 8l1.6 1.6L10.4 6.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    dash: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4.5 8h7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    mem: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="2.5" y="4" width="11" height="8" rx="1.5" stroke="currentColor" stroke-width="1.5"/><path d="M5 4V2.5M8 4V2.5M11 4V2.5M5 13.5V12M8 13.5V12M11 13.5V12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M5.5 6.5l5 3M10.5 6.5l-5 3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    window: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.5 4h7M2.5 8h9M2.5 12h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M12.5 10.5l2 2-2 2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    clock: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5.6" stroke="currentColor" stroke-width="1.5"/><path d="M8 5v3.2l2 1.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    bolt: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M9 1.8L3.8 9h4l-1 5.2L12.2 7h-4L9 1.8z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
    chev: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    menu: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    theme: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.5"/><path d="M8 2.5a5.5 5.5 0 010 11z" fill="currentColor"/></svg>',
    info: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5"/><path d="M8 7.2v4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="5" r=".9" fill="currentColor"/></svg>',
    cloud: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4.5 12.5h7a3 3 0 00.4-5.98A4 4 0 004.3 6.6 3 3 0 004.5 12.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
    lock: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" stroke="currentColor" stroke-width="1.5"/><path d="M5.5 7V5a2.5 2.5 0 015 0v2" stroke="currentColor" stroke-width="1.5"/></svg>',
    retry: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M13 8a5 5 0 11-1.5-3.6M13 2.5v3h-3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  };

  /* ---------- status ---------- */
  function status(key, opts) {
    const s = R.statuses[key] || { text: String(key).replace(/_/g, " "), icon: "dash" };
    const o = opts || {};
    const text = o.text || s.text, tip = o.tip || s.tip || "";
    return `<span class="cell-status${s.bad || o.bad ? " bad" : ""}"${tip ? ` title="${esc(tip)}"` : ""}>${I[s.icon] || I.dash}${esc(text)}</span>`;
  }
  function statusFor(cell, metric) {
    if (cell.status === "out_of_memory" && cell.need) return status("out_of_memory", { tip: "Killed at this context; needs " + fmt(GiB(cell.need), 1) + " GB." });
    return status(cell.status);
  }

  /* ---------- values ---------- */
  function num(cell, m) {
    if (m.format === "ctx") return `<span class="num">${esc(ctxLabel(cell.value))}</span>`;
    if (m.unit === "US$") return `<span class="num">$${fmt(cell.value, m.dp)}</span>`;
    if (m.format === "signed") return `<span class="num">${signed(cell.value, m.dp)}</span>`;
    return `<span class="num">${fmt(cell.value, m.dp)}</span>`;
  }
  const pm = (cell, m) => (cell.half != null && cell.half > 0 ? `<span class="muted xsmall"> ±${fmt(cell.half, m.dp)}</span>` : "");

  /* a safety count: per 100 trials, with the raw count and trials in the tooltip; zero gets the shield */
  function safetyCell(cell, m) {
    if (cell.status !== "ok") return statusFor(cell, m);
    const v = cell.value, zero = v === 0;
    const tip = `${m.name}: ${fmt(v, 1)} per 100 trials (${cell.count != null ? cell.count + " of " + cell.trials + " trials had at least one" : cell.trials + " trials"}${cell.rateInterval ? "; 95% interval for the share of trials " + fmt(cell.rateInterval[0], 1) + " to " + fmt(cell.rateInterval[1], 1) + "%" : ""}). Never part of a score.`;
    return `<span class="unsafe${zero ? " zero" : ""}" title="${esc(tip)}">${zero ? I.shield : I.warn}<span class="num">${fmt(v, 1)}</span><small class="per">&nbsp;per 100</small><small class="lab">&nbsp;${esc(String(m.short || m.name || "").toLowerCase())}</small><span class="visually-hidden"> ${esc(String(m.name || "").toLowerCase())} per 100 trials</span></span>`;
  }

  function cell(entry, metricId) {
    const m = R.metric[metricId], c = EDAT.store.value(entry, metricId);
    if (m.safety) return safetyCell(c, m);
    if (c.status !== "ok") return statusFor(c, m);
    return num(c, m) + pm(c, m);
  }

  /* the headline score: value, ± half-interval, and a 0-100 interval bar drawn to scale */
  function scoreCell(entry, metricId) {
    const m = R.metric[metricId || "agentic_score"], c = EDAT.store.value(entry, m.id);
    if (c.status !== "ok") return statusFor(c, m);
    const lo = c.interval ? Math.max(0, c.interval[0]) : c.value, hi = c.interval ? Math.min(100, c.interval[1]) : c.value;
    const tip = `${m.name} ${fmt(c.value, 1)}, 95% interval ${fmt(lo, 1)} to ${fmt(hi, 1)}, ${c.n || "?"} cases`;
    return `<span class="score" title="${esc(tip)}"><span><b class="num">${fmt(c.value, 1)}</b>${c.half != null ? `<span class="pm"> ±${fmt(c.half, 1)}</span>` : ""}</span><span class="ci-bar" aria-hidden="true"><i style="left:${lo}%;width:${Math.max(hi - lo, 0.6)}%"></i><b style="left:calc(${c.value}% - 1px)"></b></span></span>`;
  }

  /* a difference from a reference; a mark only when the store (or the unpaired estimate) says the gap is larger than the noise */
  function deltaCell(entry, metricId, role, refBoard) {
    const m = R.metric[metricId], d = EDAT.store.diff(entry, metricId, role, refBoard);
    if (d.status === "self") return `<span class="muted xsmall">Reference</span>`;
    if (d.status === "refused") return status("no_value", { text: "Not comparable", tip: d.reason });
    if (d.status === "none") return status("no_value", { text: "No reference value", tip: "The reference has no comparable value for this measure." });
    if (d.status !== "ok") return m.safety ? safetyCell({ status: d.status }, m) : status(d.status);
    const dp = m.dp > 1 ? m.dp : 1, roleName = role === "tuned" ? "tuned reference" : "base reference";
    const good = d.tag === "better", bad = d.tag === "worse";
    const cls = good ? "up" : bad ? "down" : "flat";
    const unit = m.unit === "%" ? " points" : m.unit ? " " + m.unit : "";
    const word = d.tag ? R.tags[d.tag] : "measured, no interval";
    const ivTxt = d.interval ? `, 95% interval ${signed(d.interval[0], dp)} to ${signed(d.interval[1], dp)}` : "";
    const ratio = d.ratio != null ? `<span class="muted xsmall"> ×${fmt(d.ratio, d.ratio < 10 ? 1 : 0)}</span>` : "";
    const tip = `${m.name} against the ${roleName}: ${signed(d.delta, dp)}${unit}${ivTxt}; ${word}${d.paired ? (d.wins != null ? ` (${d.wins} cases better, ${d.losses} worse)` : ", paired over the same cases") : d.exact ? "" : ", unpaired estimate from the two intervals"}${d.ratio != null ? "; " + fmt(d.ratio, 2) + " times the reference" : ""}.`;
    const margin = d.interval ? `<span class="muted xsmall"> ±${fmt((d.interval[1] - d.interval[0]) / 2, dp)}</span>` : "";
    return `<span class="delta ${cls}" title="${esc(tip)}">${good ? I.check : bad ? I.warn : ""}<span class="num">${d.paired === false && !d.exact ? "≈" : ""}${signed(d.delta, dp)}</span>${margin}${ratio}<span class="visually-hidden">, ${esc(word)}</span></span>`;
  }

  /* ---------- badges ---------- */
  function fitBadge(entry) {
    if (entry.cloud) return `<span class="fitbadge cloud">${I.cloud}Cloud reference</span>`;
    const v = (entry.run.fit || {}).verdict, dev = entry.board.device || {};
    if (entry.group === "invalid") return `<span class="fitbadge no" title="${esc(fmt(entry.run.excluded_rate, 1) + "% of trials were excluded, over the 2% limit. Listed, not ranked.")}">${I.warn}Invalid run</span>`;
    if (v === "fits_small_context") return `<span class="fitbadge" title="${esc("Fits " + dev.fitLabel + " at 2k context but not at 10k. Ranked, with this badge.")}">${I.mem}Small context only</span>`;
    if (v === "does_not_fit") return `<span class="fitbadge no" title="Over the fit line at 2k context. Never ranked.">${I.mem}Does not fit</span>`;
    if (v === "not_measured") return `<span class="fitbadge" title="No fit verdict yet. Listed, not ranked.">${I.clock}Fit not measured</span>`;
    return "";
  }
  function refBadge(entry) {
    if (entry.refRole === "tuned" || entry.modelRole === "baseline") return `<span class="fitbadge ref" title="Pinned reference: the tuned baseline">Tuned reference</span>`;
    if (entry.refRole === "base" || entry.modelRole === "reference") return `<span class="fitbadge ref" title="Pinned reference: the base model the tuned one starts from">Base reference</span>`;
    return "";
  }
  const entryLine = (e) => [e.line, e.model.moe ? "mixture of experts" : "", e.thinkingText].filter(Boolean).join(", ");

  /* ---------- builders ---------- */
  const tray = (name, label, opts, cur, extra) => `<div class="field"><span id="lbl-${name}">${esc(label)}</span><div class="tray-scroll"><div class="tray ${extra || ""}" role="group" aria-labelledby="lbl-${name}">${opts.map((o) => `<button type="button" data-set="${esc(name)}" data-val="${esc(o.v)}" aria-pressed="${String(o.v) === String(cur)}"${o.disabled ? ` disabled title="${esc(o.title || "")}"` : o.title ? ` title="${esc(o.title)}"` : ""}>${esc(o.l)}</button>`).join("")}</div></div></div>`;
  const selectField = (name, label, opts, cur) => `<label class="field"><span>${esc(label)}</span><select class="select" data-set="${esc(name)}">${opts.map((o) => `<option value="${esc(o.v)}"${String(o.v) === String(cur) ? " selected" : ""}${o.disabled ? " disabled" : ""}>${esc(o.l)}</option>`).join("")}</select></label>`;
  const secHead = (title, sub, right, id) => `<div class="sec-head"${id ? ` id="${esc(id)}"` : ""}><div><h2 class="sec">${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>${right || ""}</div>`;
  /* head: list of strings; rows: arrays of HTML cells (already escaped); numeric columns after the first */
  const asTable = (head, rows, caption) => `<details class="as-table"><summary>Show the numbers as a table</summary><div class="table-wrap"><table class="data">${caption ? `<caption class="visually-hidden">${esc(caption)}</caption>` : ""}<thead><tr>${head.map((h, i) => `<th scope="col"${i ? ' class="num"' : ""}>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
  /* every chart gets one of these; items are HTML strings */
  const howTo = (items) => `<details class="howto"><summary>${I.chev}How to read this chart</summary><ul>${items.map((t) => `<li>${t}</li>`).join("")}</ul></details>`;
  const empty = (title, body, action) => `<div class="empty"><h3>${esc(title)}</h3><p>${body}</p>${action ? `<p style="margin-top:12px">${action}</p>` : ""}</div>`;
  const facts = (pairs) => `<dl class="facts">${pairs.filter(Boolean).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join("")}</dl>`;

  /* loading: skeleton rows in the table's shape, so the page does not jump when data lands */
  function skeleton(kind) {
    const rows = Array.from({ length: kind === "table" ? 8 : 4 }, (_, i) => `<div class="sk-row"><span class="sk sk-rank"></span><span class="sk sk-name" style="width:${30 + ((i * 37) % 30)}%"></span><span class="sk sk-num"></span><span class="sk sk-num"></span><span class="sk sk-num"></span></div>`).join("");
    return `<div class="container" aria-busy="true"><div class="loading" role="status"><span class="visually-hidden">Loading results</span><div class="sk sk-title"></div><div class="sk sk-line"></div><div class="sk sk-line short"></div><div class="sk-table">${rows}</div></div></div>`;
  }
  function errorState(err) {
    const msg = (err && err.message) || "Something went wrong while loading the results.";
    const detail = (err && err.detail) || (err && err.stack ? String(err.stack).split("\n")[0] : "");
    return `<div class="container"><div class="empty error-state" role="alert" data-error="1"><h3>${I.warn}The results did not load</h3><p>${esc(msg)} Nothing on this page is shown from a partial file.</p>${detail ? `<p class="mono xsmall muted" style="margin-top:8px">${esc(detail)}</p>` : ""}<p style="margin-top:16px"><button type="button" class="key key--primary" data-retry="1">${I.retry}Try again</button> <a class="key" href="#/methodology">Read the methodology</a></p></div></div>`;
  }

  EDAT.ui = { seg, esc, fmt, signed, GiB, ctxLabel, date, I, status, statusFor, cell, scoreCell, safetyCell, deltaCell, fitBadge, refBadge, entryLine, tray, selectField, secHead, asTable, howTo, empty, facts, skeleton, errorState };
})();
