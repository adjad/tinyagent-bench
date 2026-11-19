/* Edge agent bench, clickable mock-up. Hash routes keep every view's state in the URL. */
(function () {
  "use strict";
  const B = window.BENCH, C = window.CHARTS, esc = C.esc, fmt = C.fmt;
  const $ = (s, r = document) => r.querySelector(s);
  /* outputs are shown as a chat app would render them: bold is rendered, everything else stays as written */
  const md = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const M = Object.fromEntries(B.metrics.map((m) => [m.id, m]));
  const modelById = Object.fromEntries(B.models.map((m) => [m.id, m]));
  const devById = Object.fromEntries(B.devices.map((d) => [d.id, d]));
  const rtById = Object.fromEntries(B.runtimes.map((r) => [r.id, r]));
  const qById = Object.fromEntries(B.quants.map((q) => [q.id, q]));
  const DEFAULT_DEVICE = "mac24";

  /* ---------- icons (inline SVG, never emoji) ---------- */
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
    info: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5"/><path d="M8 7.2v4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="5" r=".9" fill="currentColor"/></svg>'
  };

  /* ---------- URL state ---------- */
  /* Routes live in the hash. Where the host forbids path-like hashes (the published Artifact), they live in memory instead. */
  const MEM = !!window.EAB_MEMORY_ROUTES; let memRoute = "#/";
  const currentHash = () => (MEM ? memRoute : location.hash);
  function go(h) { if (MEM) { memRoute = h; onRoute(); } else location.hash = h; }
  function parse() { const h = currentHash().slice(1) || "/"; const [path, qs] = h.split("?"); return { parts: path.split("/").filter(Boolean), q: new URLSearchParams(qs || "") }; }
  let S = parse();
  const dev = () => devById[S.q.get("d")] || devById[DEFAULT_DEVICE];
  /* memory budget: a smaller device class simulated on the chosen device by capping model memory */
  const budgetClass = () => { const b = +S.q.get("b"), opts = B.BUDGETS[dev().cls]; return opts[b] ? b : dev().ram; };
  const budgetGB = (devId) => (!devId || devId === dev().id ? B.BUDGETS[dev().cls][budgetClass()] : devById[devId].budget);
  function link(path, params, keepFilters) {
    const q = new URLSearchParams();
    q.set("d", (params && params.d) || dev().id);
    if (S.q.get("b") && (!params || !params.d || params.d === dev().id)) q.set("b", S.q.get("b"));
    if (S.q.get("vs")) q.set("vs", S.q.get("vs"));
    if (keepFilters) ["g", "mod", "rt", "qn", "lic", "arch", "x", "s", "o"].forEach((k) => S.q.get(k) && q.set(k, S.q.get(k)));
    Object.entries(params || {}).forEach(([k, v]) => { if (v == null || v === "") q.delete(k); else q.set(k, v); });
    return "#" + path + "?" + q.toString();
  }
  const setQ = (params) => { go(link("/" + S.parts.join("/"), params, true)); };

  /* ---------- results ---------- */
  const cache = {};
  function runOf(variant, devId, budget) { budget = budget || budgetGB(devId); const k = variant.key + "@" + devId + "@" + budget; return cache[k] || (cache[k] = B.run(variant, devId, budget)); }
  function runsFor(model, devId, budget) { return B.variantsFor(model, devById[devId]).map((v) => runOf(v, devId, budget)); }
  function bestRun(model, devId, f, budget) {
    const runs = runsFor(model, devId, budget).filter((r) => (!f || !f.rt || r.variant.runtime === f.rt) && (!f || !f.qn || r.variant.quant === f.qn));
    if (!runs.length) return { variant: null, status: "no_variant", values: {}, ci: {}, note: "No variant for this runtime and quantisation" };
    const ok = runs.filter((r) => r.status === "ok");
    if (ok.length) return ok.sort((a, b) => b.values.agent - a.values.agent || a.values.mem2k - b.values.mem2k)[0];
    return runs.find((r) => r.status === "failed") || runs.find((r) => r.status === "not_run") || runs.sort((a, b) => a.values.mem2k - b.values.mem2k)[0];
  }
  function rankRanges(rows) {
    rows.forEach((r) => {
      const lo = r.values.agent - r.ci.agent, hi = r.values.agent + r.ci.agent;
      const others = rows.filter((o) => o !== r);
      r.rankBest = 1 + others.filter((o) => o.values.agent - o.ci.agent > hi).length;
      r.rankWorst = 1 + others.filter((o) => o.values.agent + o.ci.agent > lo).length;
    });
  }
  const rankLabel = (r) => (r.rankBest === r.rankWorst ? String(r.rankBest) : r.rankBest + "–" + r.rankWorst);
  const variantLine = (r) => r.variant ? `${qById[r.variant.quant].name}, ${rtById[r.variant.runtime].name}` : "";
  const ctxLabel = (c) => (c ? B.CONTEXT_LABELS[B.CONTEXTS.indexOf(c)] : "none");
  const fitsPhrase = (d) => (budgetClass() === d.ram ? `the ${d.name}` : `${budgetClass() === 8 ? "an" : "a"} ${budgetClass()} GB ${d.cls}`);

  function cellValue(run, mid) {
    const m = M[mid], model = modelById[run.variant ? run.variant.model : ""] || {};
    const v = run.values[mid];
    if (v == null) {
      if (m.needs && !(model.mod || []).includes(m.needs)) return status("Not applicable", "dash", false, "This model has no " + m.needs + " input");
      if (m.needsAny && (model.mod || []).length === 1) return status("Not applicable", "dash", false, "Text-only model");
      if (mid === "mem10k" && run.missing10k) return run.missing10k === "oom" ? status("Out of memory", "mem", true) : status("Beyond window", "window");
      if (mid === "mem32k" && run.missing32k) return run.missing32k === "oom" ? status("Out of memory", "mem", true) : status("Beyond window", "window");
      return status("Not measured", "dash");
    }
    if (m.fmt === "ctx") return `<span class="num">${ctxLabel(v)}</span>`;
    if (mid === "unsafe") return unsafeCell(v);
    if (mid === "asstUnsafe") return unsafeCell(v, 90);
    const ci = run.ci[mid];
    return `<span class="num">${fmt(v, m.fmt)}</span>${m.ci && ci ? `<span class="muted xsmall"> ±${fmt(ci, m.fmt === 0 ? 0 : 1)}</span>` : ""}`;
  }
  const status = (text, icon, bad, tip) => `<span class="cell-status${bad ? " bad" : ""}"${tip ? ` title="${esc(tip)}"` : ""}>${I[icon]}${esc(text)}</span>`;
  const unsafeCell = (v, of) => `<span class="unsafe${v === 0 ? " zero" : ""}" title="${v} ${of ? "unsafe or unconfirmed actions in " + of + " assistant sessions" : "unsafe calls in 120 restraint cases"}">${v === 0 ? I.shield : I.warn}${v}<small>&nbsp;of ${of || 120}</small></span>`;
  /* difference from a pinned reference, with its margin; marked only when larger than the margin */
  const REFS = B.models.filter((m) => m.ref);
  /* compare like with like: the reference at the same quantisation and runtime when it runs, else its best variant */
  function refRunFor(refId, run, devId) {
    const same = run && run.variant ? runsFor(modelById[refId], devId).find((r) => r.status === "ok" && r.variant.quant === run.variant.quant && r.variant.runtime === run.variant.runtime) : null;
    return same || bestRun(modelById[refId], devId);
  }
  const refLabel = (id) => (id === "tb" ? "tuned baseline" : "base reference");
  function deltaParts(run, refRun, mid) {
    const m = M[mid]; const a = run && run.status === "ok" ? run.values[mid] : null; const b = refRun && refRun.status === "ok" ? refRun.values[mid] : null;
    if (a == null || b == null) return null;
    const d = a - b; let ci;
    if (mid === "unsafe" || mid === "asstUnsafe") ci = 1.96 * Math.sqrt(Math.max(a + b, 1));
    else if (m.exact || m.fmt === "ctx") ci = 0;
    else if (mid === "tokens") ci = 0.12 * Math.max(a, b);
    else ci = Math.sqrt((run.ci[mid] || 0) ** 2 + (refRun.ci[mid] || 0) ** 2);
    const sig = Math.abs(d) > ci && d !== 0; const good = m.better === "high" ? d > 0 : d < 0;
    return { d, ci, sig, good };
  }
  function deltaCell(run, refRun, mid) {
    const m = M[mid]; const p = deltaParts(run, refRun, mid);
    if (!p) return run && run.status === "ok" && run.values[mid] == null ? cellValue(run, mid) : status("No reference value", "dash");
    if (m.fmt === "ctx") return p.d === 0 ? '<span class="delta flat">Same</span>' : `<span class="delta ${p.good ? "up" : "down"}">${p.good ? I.check : I.warn}${p.d > 0 ? "Longer" : "Shorter"}</span>`;
    const dg = typeof m.fmt === "number" ? m.fmt : 0;
    const sign = p.d > 0 ? "+" : p.d < 0 ? "−" : "";
    const word = p.sig ? (p.good ? "better" : "worse") : "within noise";
    return `<span class="delta ${p.sig ? (p.good ? "up" : "down") : "flat"}" title="${esc(M[mid].name)}: ${sign}${fmt(Math.abs(p.d), dg)} ±${fmt(p.ci, dg)}, ${word}">${p.sig ? (p.good ? I.check : I.warn) : ""}<span class="num">${sign}${fmt(Math.abs(p.d), dg)}</span><span class="muted xsmall"> ±${fmt(p.ci, dg)}</span></span>`;
  }
  const scoreCell = (r) => { const v = r.values.agent, ci = r.ci.agent; return `<span class="score"><span><b class="num">${fmt(v, 1)}</b><span class="pm"> ±${fmt(ci, 1)}</span></span><span class="ci-bar" aria-hidden="true"><i style="left:${v - ci}%;width:${ci * 2}%"></i><b style="left:calc(${v}% - 1px)"></b></span></span>`; };
  function runStatusText(r) {
    if (r.status === "oom") return { icon: "mem", text: "Does not fit", note: r.note, bad: true };
    if (r.status === "not_run") return { icon: "clock", text: "Not run yet", note: r.note };
    if (r.status === "failed") return { icon: "cross", text: "Run failed", note: r.note, bad: true };
    return { icon: "dash", text: "No variant", note: r.note };
  }

  /* ---------- small builders ---------- */
  const tray = (name, label, opts, cur, extraCls) => `<div class="field"><span id="lbl-${name}">${esc(label)}</span><div class="tray-scroll"><div class="tray ${extraCls || ""}" role="group" aria-labelledby="lbl-${name}">${opts.map((o) => `<button type="button" data-set="${name}" data-val="${esc(o.v)}" aria-pressed="${String(o.v) === String(cur)}"${o.disabled ? ` disabled title="${esc(o.title || "")}"` : ""}>${esc(o.l)}</button>`).join("")}</div></div></div>`;
  const selectField = (name, label, opts, cur) => `<label class="field"><span>${esc(label)}</span><select class="select" data-set="${name}">${opts.map((o) => `<option value="${esc(o.v)}"${String(o.v) === String(cur) ? " selected" : ""}>${esc(o.l)}</option>`).join("")}</select></label>`;
  const secHead = (title, sub, right, id) => `<div class="sec-head"${id ? ` id="${id}"` : ""}><div><h2 class="sec">${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>${right || ""}</div>`;
  const asTable = (head, rows) => `<details class="as-table"><summary>Show the numbers as a table</summary><div class="table-wrap"><table class="data"><thead><tr>${head.map((h, i) => `<th${i ? ' class="num"' : ""}>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;

  /* =========================================================
   * Leaderboard (home)
   * ========================================================= */
  const DEV_BY_CLASS = Object.fromEntries(B.devices.map((d) => [d.cls, d.id]));
  function filtersFromQ() { return { mod: S.q.get("mod") || "", rt: S.q.get("rt") || "", qn: S.q.get("qn") || "", lic: S.q.get("lic") || "", arch: S.q.get("arch") || "" }; }
  function leaderboardRows(devId, f) {
    const models = B.models.filter((m) => m.ref || ((!f.mod || m.mod.includes(f.mod)) && (!f.lic || m.licence.startsWith(f.lic)) && (!f.arch || (f.arch === "moe") === m.moe)));
    const all = models.map((m) => Object.assign({ model: m }, bestRun(m, devId, f)));
    const ok = all.filter((r) => r.status === "ok"), rest = all.filter((r) => r.status !== "ok");
    rankRanges(ok);
    return { ok, rest };
  }

  function pageHome() {
    const d = dev(), f = filtersFromQ(), group = S.q.get("g") || "agentic";
    let { ok, rest } = leaderboardRows(d.id, f);
    const sortId = S.q.get("s") || "agent", order = S.q.get("o") || (M[sortId].better === "low" ? "asc" : "desc");
    const sv = (r) => { const v = r.values[sortId]; return v == null ? (order === "asc" ? Infinity : -Infinity) : v; };
    ok.sort((a, b) => (order === "asc" ? sv(a) - sv(b) : sv(b) - sv(a)) || b.values.agent - a.values.agent);
    const byAgent = ok.slice().sort((a, b) => b.values.agent - a.values.agent);
    const top = byAgent[0];
    const tied = top ? byAgent.filter((r) => r !== top && r.rankBest === 1) : [];

    /* verdict */
    let verdict;
    if (top) {
      const tiedTxt = tied.length ? `Statistically tied with ${tied.map((r) => `<a class="link" href="${link("/model/" + r.model.id)}">${esc(r.model.name)}</a>`).join(" and ")}: the gap is inside the margin of error.` : `Ahead of the next model by more than the margin of error.`;
      verdict = `<section class="verdict" aria-labelledby="verdict-h">
        <div>
          <h1 id="verdict-h"><a href="${link("/model/" + top.model.id, { q: top.variant.quant, rt: top.variant.runtime })}">${esc(top.model.name)}</a> is the best agent that fits ${esc(fitsPhrase(d))}.</h1>
          <p class="verdict-sub">${tiedTxt} It runs as ${esc(variantLine(top))} and leaves ${fmt(budgetGB() - top.values.mem2k, 1)} GB of the memory budget free.</p>
          <div class="verdict-actions">
            <a class="key key--primary" href="${link("/model/" + top.model.id, { q: top.variant.quant, rt: top.variant.runtime })}">Open ${esc(top.model.name)}</a>
            <a class="key" href="${link("/compare", { m: [top].concat(tied.length ? tied : byAgent.slice(1, 3)).slice(0, 4).map((r) => r.model.id).join(",") })}">Compare with ${tied.length ? "the tied models" : "the next two"}</a>
          </div>
        </div>
        <dl class="figures">
          <div><dt>Agent score</dt><dd>${fmt(top.values.agent, 1)}<small>±${fmt(top.ci.agent, 1)}</small></dd></div>
          <div><dt>Unsafe calls</dt><dd class="${top.values.unsafe > 0 ? "bad" : ""}">${top.values.unsafe}<small>of 120</small></dd></div>
          <div><dt>Readability</dt><dd>${top.values.readab}<small>of 100</small></dd></div>
          <div><dt>Memory at 2k</dt><dd>${fmt(top.values.mem2k, 1)}<small>GB</small></dd></div>
          <div><dt>Decode speed</dt><dd>${fmt(top.values.decode2k, 0)}<small>tok/s</small></dd></div>
          <div><dt>Energy per task</dt><dd>${fmt(top.values.jTask, 0)}<small>J</small></dd></div>
        </dl>
      </section>`;
    } else {
      verdict = `<section class="verdict"><div><h1>No tested model fits ${esc(fitsPhrase(d))} with these filters.</h1><p class="verdict-sub">Clear a filter or pick a device with more memory. The models below show why each one was left out.</p><div class="verdict-actions"><a class="key key--primary" href="${link("/", {})}">Clear filters</a></div></div></section>`;
    }

    /* best per size tier (pattern from the sites review: top model per size tier) */
    const SIZE = [["Under 1B", 0, 1], ["1B to 4B", 1, 4.05], ["4B to 8B", 4.05, 8.05], ["8B to 12B", 8.05, 12.5]];
    const sizeStrip = `<section class="size-strip" aria-label="Best agent in each size">${SIZE.map(([label, lo, hi]) => { const best = byAgent.find((r) => r.model.total >= lo && r.model.total < hi); const any = B.models.some((m) => m.total >= lo && m.total < hi); return `<div><p class="xsmall muted">Best ${label.replace("Under", "under")}</p>${best ? `<p><a class="mname" href="${link("/model/" + best.model.id, { q: best.variant.quant, rt: best.variant.runtime })}">${esc(best.model.name)}</a></p><p class="small muted num">${fmt(best.values.agent, 1)} ±${fmt(best.ci.agent, 1)}, ${best.values.unsafe} unsafe</p>` : `<p class="small">${any ? status("None fits", "mem") : status("None tested", "dash")}</p>`}</div>`; }).join("")}</section>`;

    /* filters */
    const cls = d.cls, bc = budgetClass(), bGB = budgetGB();
    const fitOpts = Object.keys(B.BUDGETS[cls]).map(Number).map((g) => ({ v: g, l: g === d.ram ? `Whole ${cls}, ${g} GB` : `Fits in ${g} GB` }));
    const filters = `<section class="filters" aria-label="Filters">
      <div class="filter-row">
        ${tray("cls", "Device", B.devices.map((x) => ({ v: x.cls, l: x.name })), cls)}
        ${tray("b", "Memory budget", fitOpts, bc)}
        <p class="small muted fit-note">${bc === d.ram ? `A model fits when its peak memory at 2k context stays under <strong class="num">${fmt(bGB, 1)} GB</strong>, what the ${esc(d.name)} leaves after the operating system.` : `A ${bc} GB ${cls} is simulated on the ${esc(d.name)} by capping the model at <strong class="num">${fmt(bGB, 1)} GB</strong>, what a ${bc} GB ${cls} leaves after its operating system.`}</p>
      </div>
      <details class="more-filters"${["mod", "rt", "qn", "lic", "arch"].some((k) => S.q.get(k)) ? " open" : ""}>
        <summary>${I.chev}More filters${["mod", "rt", "qn", "lic", "arch"].filter((k) => S.q.get(k)).length ? ` (${["mod", "rt", "qn", "lic", "arch"].filter((k) => S.q.get(k)).length} on)` : ""}</summary>
        <div class="filter-row">
          ${selectField("mod", "Modality", [{ v: "", l: "Any" }, { v: "vision", l: "Reads images" }, { v: "audio", l: "Hears audio" }], f.mod)}
          ${selectField("qn", "Quantisation", [{ v: "", l: "Best that fits" }].concat(B.quants.map((q) => ({ v: q.id, l: q.name }))), f.qn)}
          ${selectField("lic", "Licence", [{ v: "", l: "Any" }, { v: "Open weights, permissive", l: "Permissive" }, { v: "Open weights", l: "Any open weights" }, { v: "Research", l: "Research only" }], f.lic)}
          ${selectField("arch", "Architecture", [{ v: "", l: "Any" }, { v: "dense", l: "Dense" }, { v: "moe", l: "Mixture of experts" }], f.arch)}
        </div>
      </details>
    </section>`;

    /* table */
    const gm = B.metrics.filter((m) => m.group === group && m.id !== "agent" && m.id !== "unsafe");
    const thSort = (mid, label, unit, extra) => { const on = sortId === mid; return `<th class="num ${extra || ""}"${on ? ` aria-sort="${order === "asc" ? "ascending" : "descending"}"` : ""}><button type="button" data-sort="${mid}" title="${esc(M[mid].name)}${M[mid].desc ? ". " + esc(M[mid].desc) : ""}">${esc(label)}${on ? (order === "asc" ? " ↑" : " ↓") : ""}</button>${unit ? `<span class="vline">${esc(unit)}${M[mid].better === "low" ? ", lower is better" : ""}</span>` : ""}</th>`; };
    const head = `<thead><tr><th class="sticky">Rank and model</th>${thSort("agent", "Agent score", "0 to 100, ±95% CI")}${thSort("unsafe", "Unsafe calls", "of 120", "col-unsafe")}${gm.map((m, i) => thSort(m.id, m.short, m.unit === "%" ? "%" : m.unit, i === 0 ? "group-start" : "")).join("")}</tr></thead>`;
    const vs = REFS.some((m) => m.id === S.q.get("vs")) ? S.q.get("vs") : "";
    const refRun = vs ? ok.concat(rest).find((r) => r.model.id === vs) : null;
    const cellFor = (r, mid) => (vs && r.model.id !== vs ? deltaCell(r, refRunFor(vs, r, d.id), mid) : cellValue(r, mid));
    const rowHtml = (r) => `<tr class="${r === top ? "is-top" : ""}"><td class="sticky"><div style="display:flex;gap:12px;align-items:baseline"><span class="rank" style="min-width:2.6em">${rankLabel(r)}</span><span><span class="mname"><a href="${link("/model/" + r.model.id, { q: r.variant.quant, rt: r.variant.runtime })}">${esc(r.model.name)}</a></span><span class="vline">${esc(variantLine(r))}${r.model.moe ? ", MoE" : ""}${r.model.id === vs ? ", the reference for differences" : ""}</span></span></div></td><td class="num">${vs && r.model.id !== vs ? deltaCell(r, refRunFor(vs, r, d.id), "agent") : scoreCell(r)}<span class="unsafe-inline">${cellFor(r, "unsafe")}</span></td><td class="num col-unsafe">${cellFor(r, "unsafe")}</td>${gm.map((m, i) => `<td class="num ${i === 0 ? "group-start" : ""}">${cellFor(r, m.id)}</td>`).join("")}</tr>`;
    const statusRow = (r) => { const st = runStatusText(r); return `<tr class="row-dim"><td class="sticky"><div style="display:flex;gap:12px;align-items:baseline"><span class="rank" style="min-width:2.6em"></span><span><span class="mname"><a href="${link("/model/" + r.model.id)}">${esc(r.model.name)}</a></span><span class="vline">${r.variant ? esc(variantLine(r)) : ""}</span></span></div></td><td colspan="${2 + gm.length}">${status(st.text, st.icon, st.bad)} <span class="small muted">${esc(st.note || "")}</span></td></tr>`; };
    const pinned = REFS.map((m) => ok.concat(rest).find((r) => r.model.id === m.id)).filter(Boolean);
    const pinnedRows = `<tr class="divider-row"><td colspan="${3 + gm.length}">Pinned references<span>Always shown and ranked with everyone else. ${vs ? "Other rows show the difference from the " + refLabel(vs) + " at the same quantisation; a mark means the gap is larger than the margin of error." : "Choose Show to read every row as a difference from one of them."}</span></td></tr>` + pinned.map((r) => (r.status === "ok" ? rowHtml(r) : statusRow(r))).join("") + `<tr class="divider-row"><td colspan="${3 + gm.length}">All models</td></tr>`;
    rest = rest.filter((r) => !r.model.ref);
    const restRows = rest.length ? `<tr class="divider-row"><td colspan="${3 + gm.length}">Left out on this device (${rest.length})<span>Shown so a missing model reads as a result, not an omission</span></td></tr>` + rest.map((r) => { const st = runStatusText(r); return `<tr class="row-dim"><td class="sticky"><div style="display:flex;gap:12px;align-items:baseline"><span class="rank" style="min-width:2.6em">${I.dash ? "" : ""}</span><span><span class="mname"><a href="${link("/model/" + r.model.id)}">${esc(r.model.name)}</a></span><span class="vline">${r.variant ? esc(variantLine(r)) : ""}</span></span></div></td><td colspan="${2 + gm.length}">${status(st.text, st.icon, st.bad)} <span class="small muted">${esc(st.note || "")}</span></td></tr>`; }).join("") : "";
    const table = ok.length || rest.length ? `<div class="table-wrap"><table class="data" id="board">${head}<tbody>${pinnedRows}${ok.filter((r) => !r.model.ref).map(rowHtml).join("")}${restRows}</tbody></table></div>` : `<div class="empty"><h3>No models match</h3><p>Every model was filtered out. Clear a filter to see results.</p></div>`;

    /* scatter */
    const xKey = S.q.get("x") || "mem2k";
    const xOpts = [{ v: "mem2k", l: "Memory" }, { v: "jTask", l: "Joules per task" }, { v: "secTask", l: "Seconds per task" }];
    const xLabels = { mem2k: "Peak memory at 2k, GB", jTask: "Joules per completed task", secTask: "Seconds per completed task" };
    const pts = ok.map((r) => ({ x: r.values[xKey], y: r.values.agent, ci: r.ci.agent, label: r.model.name, shape: r.model.moe ? "diamond" : "circle", hl: r === top, pin: !!r.model.ref, href: link("/model/" + r.model.id), tip: `${r.model.name}\nAgent ${fmt(r.values.agent, 1)} ±${fmt(r.ci.agent, 1)}\n${xLabels[xKey]}: ${fmt(r.values[xKey], xKey === "mem2k" ? 1 : 0)}\nUnsafe calls: ${r.values.unsafe} of 120` }));

    return `<div class="container">
      ${verdict}
      ${top ? sizeStrip : ""}
      ${filters}
      <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:end;gap:12px;margin-top:var(--space-lg)">
        <div class="toolbar">${tray("g", "Columns", B.groups.map((g) => ({ v: g.id, l: g.name })), group)}${tray("vs", "Show", [{ v: "", l: "Values" }, { v: "tb", l: "Difference from tuned baseline" }, { v: "rb", l: "Difference from base reference" }], S.q.get("vs") || "")}</div>
        <p class="conditions"><strong>${esc(d.name)}</strong>${bc !== d.ram ? `, capped as ${bc === 8 ? "an" : "a"} ${bc} GB ${cls}` : ""}, best fitting variant per model, speed and memory at 2k context, ${ok.length} of ${ok.length + rest.length} models run, results dated ${B.generated}. Ranks are ranges: two models share a rank when their intervals overlap. Sample data.</p>
      </div>
      ${table}
      <p class="small muted" style="margin-top:8px">The agent score is the mean of six agentic tiers with three trials per case. Unsafe calls are counted on their own and never blended in. <a class="link" href="${link("/methodology")}#scores">How scores are built</a>.</p>
      ${secHead("Quality against cost", "Up and to the left is better. The dashed line joins the models nothing else beats on both axes. Whiskers are 95% intervals; diamonds are mixture-of-experts models.", tray("x", "Cost axis", xOpts, xKey))}
      ${pts.length ? `<figure class="scatter-fig">${C.scatter({ points: pts, xLabel: xLabels[xKey], yLabel: "Agent score", aria: "Agent score against " + xLabels[xKey] })}
        <div class="legend"><span>${C.shapeSwatch("circle", "dot")} Dense</span><span>${C.shapeSwatch("diamond", "dot")} Mixture of experts</span><span>${C.shapeSwatch("circle", "dot hl")} Best that fits</span><span><svg viewBox="0 0 18 12" class="chart" aria-hidden="true"><path class="frontier" d="M1 6H17"/></svg> Best trade-off line</span></div>
        ${asTable(["Model", "Agent score", "± 95%", xLabels[xKey]], ok.map((r) => [esc(r.model.name), fmt(r.values.agent, 1), fmt(r.ci.agent, 1), fmt(r.values[xKey], xKey === "mem2k" ? 1 : 0)]))}</figure>` : `<div class="empty"><p>Nothing to plot: no model runs on this device with these filters.</p></div>`}
    </div>`;
  }

  /* =========================================================
   * Model page
   * ========================================================= */
  function pageModel(id) {
    const model = modelById[id]; if (!model) return notFound();
    const d = dev();
    const runs = runsFor(model, d.id);
    let run = runs.find((r) => r.variant.quant === S.q.get("q") && r.variant.runtime === S.q.get("rt"));
    if (!run) run = bestRun(model, d.id);
    const v = run.values, ci = run.ci;
    const fitDevices = B.devices.filter((x) => bestRun(model, x.id).status === "ok");

    const facts = `<dl class="facts">
      <div><dt>Parameters</dt><dd class="num">${fmt(model.total, 1)}B total${model.moe ? `, ${fmt(model.active, 1)}B active` : ""}</dd></div>
      <div><dt>Architecture</dt><dd>${model.moe ? "Mixture of experts" : "Dense"}</dd></div>
      <div><dt>Modalities</dt><dd>${model.mod.map((x) => x[0].toUpperCase() + x.slice(1)).join(", ")}</dd></div>
      <div><dt>Context window</dt><dd class="num">${fmt(model.ctx / 1024, 0)}k tokens</dd></div>
      <div><dt>Licence</dt><dd>${esc(model.licence)}</dd></div>
      <div><dt>Released</dt><dd class="num">${model.released}</dd></div>
      <div><dt>File size, this variant</dt><dd class="num">${v.file != null ? fmt(v.file, 1) + " GB" : "Not measured"}</dd></div>
      <div><dt>Runs on</dt><dd>${fitDevices.length} of ${B.devices.length} devices</dd></div>
    </dl>`;

    const qOpts = B.quants.map((q) => { const has = runs.some((r) => r.variant.quant === q.id && (!run.variant || r.variant.runtime === run.variant.runtime)); return { v: q.id, l: q.name, disabled: !has, title: "Not offered on this runtime" }; });
    const rtOpts = B.runtimes.filter((r) => r.on.includes(d.cls)).map((r) => ({ v: r.id, l: r.name, disabled: !runs.some((x) => x.variant.runtime === r.id), title: "Not available for this model on this device" }));
    const variantBar = `<div class="toolbar" style="margin:var(--space-lg) 0 var(--space-sm)">
      ${selectField("d", "Device", B.devices.map((x) => ({ v: x.id, l: x.name })), d.id)}
      ${run.variant ? tray("q", "Quantisation", qOpts, run.variant.quant) : ""}
      ${run.variant && rtOpts.length > 1 ? tray("rt", "Runtime", rtOpts, run.variant.runtime) : run.variant ? `<div class="field"><span>Runtime</span><p style="min-height:40px;display:flex;align-items:center">${esc(rtById[run.variant.runtime].name)}</p></div>` : ""}
    </div>`;

    let body;
    if (run.status !== "ok") {
      const st = runStatusText(run);
      body = `<div class="empty" style="margin-top:var(--space-md)"><h3>${status(st.text, st.icon, st.bad)} on ${esc(d.name)}</h3><p>${esc(st.note || "")}${run.status === "oom" ? ". Try a smaller quantisation or a device with more memory." : ""}</p>
        <div class="verdict-actions">${fitDevices.slice(0, 3).map((x) => `<a class="key" href="${link("/model/" + id, { d: x.id })}">See it on ${esc(x.name)}</a>`).join("")}</div></div>`;
      return modelShell(model, facts, variantBar, body);
    }

    // rank on this device
    const { ok } = leaderboardRows(d.id, {});
    const me = ok.find((r) => r.model.id === id);
    const medianOf = (mid) => { const xs = ok.map((r) => r.values[mid]).filter((x) => x != null).sort((a, b) => a - b); return xs.length ? xs[Math.floor(xs.length / 2)] : null; };

    const figs = `<dl class="figures" style="grid-template-columns:repeat(6,minmax(0,1fr))">
      <div><dt>Agent score</dt><dd>${fmt(v.agent, 1)}<small>±${fmt(ci.agent, 1)}</small></dd></div>
      <div><dt>Rank on this device</dt><dd>${me ? rankLabel(me) : "Not ranked"}<small>of ${ok.length}</small></dd></div>
      <div><dt>Unsafe calls</dt><dd class="${v.unsafe ? "bad" : ""}">${v.unsafe}<small>of 120</small></dd></div>
      <div><dt>Readability</dt><dd>${v.readab}<small>±${fmt(ci.readab, 0)}</small></dd></div>
      <div><dt>Memory at 2k</dt><dd>${fmt(v.mem2k, 1)}<small>GB</small></dd></div>
      <div><dt>Energy per task</dt><dd>${fmt(v.jTask, 0)}<small>J</small></dd></div>
    </dl>`;

    const tierRows = ["toolchoice", "parallel", "sequential", "plan", "recovery", "menu200", "reliab", "extract", "multi"].map((mid) => ({ label: M[mid].name, v: v[mid], ci: ci[mid], ref: medianOf(mid), href: M[mid].tier ? link("/tests/" + M[mid].tier, { m: id }) : null, tip: `${M[mid].name}: ${v[mid]}% ±${fmt(ci[mid], 0)}\nMedian of models that fit: ${medianOf(mid)}%` }));
    const tiers = secHead("Scores by tier", `Dot is the pass rate, whisker the 95% interval over three trials per case, tick the median of every model that fits this device. Each tier opens its cases in the test explorer.`, "", "tiers") +
      `<div class="grid-2"><div>${C.dotRows({ rows: tierRows, domain: [0, 100], aria: "Pass rate by tier", labelW: 200 })}${asTable(["Tier", "Pass rate %", "± 95%", "Median"], tierRows.map((r) => [esc(r.label), r.v, fmt(r.ci, 0), r.ref]))}</div>
       <div class="cut panel"><h3 style="margin-bottom:8px">Restraint, counted on its own</h3><p class="small" style="color:var(--color-ink-2)">${unsafeCell(v.unsafe)} &nbsp;unsafe tool calls in 120 cases that invite one: forwarding mail to a stranger, deleting without a clear ask, following instructions hidden in a document.</p><p class="small muted" style="margin-top:8px">The median model that fits this device makes ${medianOf("unsafe")}. This number never enters the agent score, so a careless model cannot hide behind a high pass rate.</p><p style="margin-top:12px"><a class="link small" href="${link("/tests/restraint", { m: id })}">Read its restraint cases</a></p>
       <h3 style="margin:20px 0 8px">Fabricated facts</h3><p class="small" style="color:var(--color-ink-2)"><b class="num">${fmt(v.fabric, 1)}%</b> of claims in its summaries are not in the source, ±${fmt(ci.fabric, 1)}.</p></div></div>`;

    const ctxSeries = (key) => [{ name: model.name, cls: "A", shape: "circle", points: run.curves[key] }];
    const ctxCharts = secHead("Context scaling", `Seven context lengths from 512 to 64k tokens on ${esc(d.name)}. A cross marks the first length that ran out of memory; the hatched band is beyond the model's ${fmt(model.ctx / 1024, 0)}k window. Both are results, not gaps.`, "", "context") +
      `<div class="grid-4">
        <figure style="margin:0"><figcaption class="chart-title">Peak memory</figcaption><div class="chart-sub">GB, lower is better</div>${C.contextChart({ series: ctxSeries("mem"), levels: B.CONTEXTS, labels: B.CONTEXT_LABELS, unit: "GB", d: 2, budget: budgetGB(), aria: "Peak memory by context" })}</figure>
        <figure style="margin:0"><figcaption class="chart-title">Prefill speed</figcaption><div class="chart-sub">tok/s, higher is better</div>${C.contextChart({ series: ctxSeries("prefill"), levels: B.CONTEXTS, labels: B.CONTEXT_LABELS, unit: "tok/s", d: 0, aria: "Prefill speed by context" })}</figure>
        <figure style="margin:0"><figcaption class="chart-title">Decode speed</figcaption><div class="chart-sub">tok/s, higher is better</div>${C.contextChart({ series: ctxSeries("decode"), levels: B.CONTEXTS, labels: B.CONTEXT_LABELS, unit: "tok/s", d: 1, aria: "Decode speed by context" })}</figure>
        <figure style="margin:0"><figcaption class="chart-title">Time to first token</figcaption><div class="chart-sub">seconds, lower is better</div>${C.contextChart({ series: ctxSeries("ttft"), levels: B.CONTEXTS, labels: B.CONTEXT_LABELS, unit: "s", d: 2, aria: "Time to first token by context" })}</figure>
      </div>` + asTable(["Context", "Memory GB", "Prefill tok/s", "Decode tok/s", "TTFT s"], B.CONTEXTS.map((c, i) => { const cell = (k, dd) => { const p = run.curves[k][i]; return p.v != null ? fmt(p.v, dd) : p.status === "oom" ? "Out of memory" : "Beyond window"; }; return [B.CONTEXT_LABELS[i], cell("mem", 2), cell("prefill", 0), cell("decode", 1), cell("ttft", 2)]; })) +
      `<p class="small muted" style="margin-top:8px">Load from SSD: <span class="num">${fmt(v.loadCold, 1)} s</span> cold, <span class="num">${fmt(v.loadWarm, 2)} s</span> warm.</p>`;

    const es = run.energySplit;
    const energy = secHead("Energy", `Measured at the battery or wall, idle draw subtracted. ${esc(d.power_method)}.`, "", "energy") +
      `<div class="grid-2"><div><dl class="figures"><div><dt>Per prefill token</dt><dd>${fmt(v.jPre, 3)}<small>J</small></dd></div><div><dt>Per decode token</dt><dd>${fmt(v.jDec, 2)}<small>J</small></dd></div><div><dt>Per completed task</dt><dd>${fmt(v.jTask, 0)}<small>J</small></dd></div><div><dt>Speed kept at 20 min</dt><dd>${v.sustain}<small>%</small></dd></div><div><dt>Battery per hour</dt><dd>${v.battery}<small>%</small></dd></div><div><dt>Seconds per task</dt><dd>${fmt(v.secTask, 1)}<small>s</small></dd></div></dl>
        <p class="chart-title" style="margin-top:var(--space-lg)">Where one task's energy goes</p>${C.stackBar({ segments: [{ label: "Prefill", v: es.prefill, cls: "seg1" }, { label: "Decode", v: es.decode, cls: "seg2" }, { label: "Tool waits", v: es.idle, cls: "seg3" }], aria: "Energy split for one task" })}</div>
        <figure style="margin:0"><figcaption class="chart-title">Sustained decode speed</figcaption><div class="chart-sub">30 minutes of back-to-back tasks, room at 22 °C</div>${C.sustain({ series: [{ name: model.name, short: model.name, cls: "A", shape: "circle", points: run.sustainCurve }], aria: "Sustained decode speed" })}</figure></div>`;

    const perDevice = secHead("On every device", "Best fitting variant on each device. Click a row to switch the page to it.", "", "devices") +
      `<div class="table-wrap"><table class="data"><thead><tr><th>Device</th><th>Variant</th><th class="num">Agent</th><th class="num">Unsafe</th><th class="num">Decode tok/s</th><th class="num">Memory GB</th><th class="num">J per task</th></tr></thead><tbody>${B.devices.map((x) => { const r = bestRun(model, x.id); const here = x.id === d.id; if (r.status !== "ok") { const st = runStatusText(r); return `<tr class="row-dim"><td><a href="${link("/model/" + id, { d: x.id })}">${esc(x.name)}</a></td><td colspan="6">${status(st.text, st.icon, st.bad)} <span class="small muted">${esc(st.note || "")}</span></td></tr>`; } return `<tr class="${here ? "is-top" : ""}"><td><a href="${link("/model/" + id, { d: x.id, q: r.variant.quant, rt: r.variant.runtime })}">${esc(x.name)}</a>${here ? ' <span class="muted xsmall">showing</span>' : ""}</td><td>${esc(variantLine(r))}</td><td class="num">${fmt(r.values.agent, 1)} <span class="muted xsmall">±${fmt(r.ci.agent, 1)}</span></td><td class="num">${unsafeCell(r.values.unsafe)}</td><td class="num">${fmt(r.values.decode2k, 1)}</td><td class="num">${fmt(r.values.mem2k, 1)}</td><td class="num">${fmt(r.values.jTask, 0)}</td></tr>`; }).join("")}</tbody></table></div>`;

    const perQuant = secHead("By quantisation", `${esc(d.name)}. A difference is marked only when it is larger than the measurement's margin of error.`, "", "quant") +
      `<div class="table-wrap"><table class="data"><thead><tr><th>Variant</th><th class="num">Agent</th><th class="num">Change from best</th><th class="num">Unsafe</th><th class="num">Memory GB</th><th class="num">Decode tok/s</th><th class="num">J per task</th></tr></thead><tbody>${(() => { const okRuns = runs.filter((r) => r.status === "ok"); const best = okRuns.slice().sort((a, b) => b.values.agent - a.values.agent)[0]; return runs.map((r) => { if (r.status !== "ok") { const st = runStatusText(r); return `<tr class="row-dim"><td>${esc(variantLine(r))}</td><td colspan="6">${status(st.text, st.icon, st.bad)} <span class="small muted">${esc(st.note || "")}</span></td></tr>`; } const diff = r.values.agent - best.values.agent; const sig = Math.abs(diff) > Math.sqrt(r.ci.agent ** 2 + best.ci.agent ** 2); return `<tr class="${r === run ? "is-top" : ""}"><td><a href="${link("/model/" + id, { q: r.variant.quant, rt: r.variant.runtime })}">${esc(variantLine(r))}</a></td><td class="num">${fmt(r.values.agent, 1)} <span class="muted xsmall">±${fmt(r.ci.agent, 1)}</span></td><td class="num">${r === best ? '<span class="muted xsmall">best</span>' : sig ? `<span class="sig" style="color:var(--color-bad)">${I.warn}${fmt(diff, 1)}, real loss</span>` : `<span class="sig same">${fmt(diff, 1)}, within noise</span>`}</td><td class="num">${unsafeCell(r.values.unsafe)}</td><td class="num">${fmt(r.values.mem2k, 1)}</td><td class="num">${fmt(r.values.decode2k, 1)}</td><td class="num">${fmt(r.values.jTask, 0)}</td></tr>`; }).join(""); })()}</tbody></table></div>`;

    const langRows = B.languages.map((L) => ({ label: L, v: run.languages[L], ci: run.languagesCi[L], tip: `${L}: ${run.languages[L]}% ±${fmt(run.languagesCi[L], 0)}` }));
    const perLang = secHead("By language", "Agentic and extraction cases translated by native speakers, 60 cases per language.", "", "languages") +
      `<div class="grid-2"><div>${C.dotRows({ rows: langRows, domain: [0, 100], aria: "Pass rate by language", labelW: 120 })}${asTable(["Language", "Pass rate %", "± 95%"], langRows.map((r) => [r.label, r.v, fmt(r.ci, 0)]))}</div><div class="small" style="color:var(--color-ink-2);align-self:center"><p>English is the reference. ${(() => { const worst = langRows.slice().sort((a, b) => a.v - b.v)[0]; return `The weakest language is ${worst.label} at ${worst.v}%, ${run.languages.English - worst.v} points below English.`; })()}</p></div></div>`;

    let va = "";
    if (model.mod.length > 1) {
      va = secHead("Vision and audio", "Measured with the encoders loaded. Memory with and without them is shown so the cost of each modality is clear.", "", "va") +
        `<dl class="figures" style="grid-template-columns:repeat(5,minmax(0,1fr))">
          <div><dt>Image extraction</dt><dd>${v.imgAcc != null ? v.imgAcc + "<small>±" + fmt(ci.imgAcc, 0) + "</small>" : '<small>Not applicable</small>'}</dd></div>
          <div><dt>Image latency</dt><dd>${v.imgLat != null ? fmt(v.imgLat, 1) + "<small>s</small>" : '<small>Not applicable</small>'}</dd></div>
          <div><dt>Audio word errors</dt><dd>${v.wer != null ? fmt(v.wer, 1) + "<small>%</small>" : '<small>Not applicable</small>'}</dd></div>
          <div><dt>Audio latency</dt><dd>${v.audLat != null ? fmt(v.audLat, 1) + "<small>s</small>" : '<small>Not applicable</small>'}</dd></div>
          <div><dt>Memory, text only and with encoders</dt><dd>${fmt(v.mem2k, 1)}<small>to ${fmt(v.mem2k + v.memVA, 1)} GB</small></dd></div>
        </dl>`;
    }

    const tierKey = model.read > 0.78 ? "good" : model.read > 0.62 ? "mid" : "poor";
    const samples = secHead("Sample outputs", "Read the answers, not only the score. Same prompts for every model, shown exactly as generated.", `<a class="key" href="${link("/vote")}">Vote on readability</a>`, "samples") +
      `<div class="grid-2">${B.samplePrompts.map((p, i) => `<div class="cut" style="overflow:hidden"><div class="output-head"><span><b style="font-weight:500">${esc(p.ask)}</b><span class="vline">${esc(p.source)}</span></span><span class="num muted">${Math.round(B.sampleOutputs[tierKey][i].split(/\s+/).length * 1.3)} tokens</span></div><div class="output">${md(B.sampleOutputs[tierKey][i])}</div></div>`).join("")}</div>`;

    /* every score read against the two pinned references */
    const refRuns = Object.fromEntries(REFS.map((m) => [m.id, refRunFor(m.id, run, d.id)]));
    const KEY = ["agent", "unsafe", "readab", "fabric", "asstScore", "asstUnsafe", "decode2k", "mem2k", "jTask"];
    const unitTag = (mid) => (M[mid].unit ? ` <span class="muted xsmall">${esc(M[mid].unit)}</span>` : "");
    const refTable = secHead("Against the references", `Differences from the two pinned 4B references on ${esc(d.name)}, each with its margin of error. A mark appears only when the gap is larger than the margin.`, "", "refs") +
      `<div class="table-wrap"><table class="data"><thead><tr><th class="sticky">Measure</th><th class="num">This model</th>${REFS.map((m) => `<th class="num">Difference from ${esc(m.name)}</th>`).join("")}</tr></thead><tbody>${KEY.map((mid) => `<tr><td class="sticky">${esc(M[mid].name)}${unitTag(mid)}</td><td class="num">${cellValue(run, mid)}</td>${REFS.map((m) => `<td class="num">${m.id === id ? '<span class="muted xsmall">This model</span>' : deltaCell(run, refRuns[m.id], mid)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    let tuning = "";
    if (model.ref) {
      const TUNE = ["agent", "unsafe", "toolchoice", "sequential", "recovery", "readab", "prose", "tokens", "fabric", "asstScore", "asstAsk", "asstBrief", "asstFalse", "asstDayValid", "asstDayCover", "asstUnsafe", "decode2k", "mem2k"];
      tuning = secHead("Effect of tuning", `The tuned baseline minus the base reference on ${esc(d.name)}, both at ${esc(variantLine(refRuns.tb))}. Speed and memory should not move; if they do, tuning changed more than behaviour.`, "", "tuning") +
        `<div class="table-wrap"><table class="data"><thead><tr><th class="sticky">Measure</th><th class="num">${esc(modelById.rb.name)}</th><th class="num">${esc(modelById.tb.name)}</th><th class="num">Effect of tuning</th></tr></thead><tbody>${TUNE.map((mid) => `<tr><td class="sticky">${esc(M[mid].name)}${unitTag(mid)}</td><td class="num">${cellValue(refRuns.rb, mid)}</td><td class="num">${cellValue(refRuns.tb, mid)}</td><td class="num">${deltaCell(refRuns.tb, refRuns.rb, mid)}</td></tr>`).join("")}</tbody></table></div>`;
    }
    const A = run.assistant, AC = run.assistantCi;
    const ASURF = [["ask", "Ask: correct answers", "asstAsk", 0], ["brief", "Brief: right items surfaced", "asstBrief", 0], ["briefFalse", "Brief: false alarms per brief", "asstFalse", 1], ["dayValid", "Day: valid plans", "asstDayValid", 0], ["dayCover", "Day: due items covered", "asstDayCover", 0]];
    const assistant = secHead("In the assistant", `Run inside the assistant itself against three <a class="link" href="${link("/profiles")}">synthetic profiles</a>, scored against each profile's answer key. Percentages unless marked.`, `<div class="toolbar">${["asst-ask", "asst-brief", "asst-day"].map((t) => `<a class="key" href="${link("/capture/" + t, { m: id })}">Read a captured ${t === "asst-ask" ? "answer" : t === "asst-brief" ? "brief" : "day plan"}</a>`).join("")}</div>`, "assistant") +
      `<div class="table-wrap"><table class="data"><thead><tr><th class="sticky">Surface</th>${B.profiles.map((p) => `<th class="num">${esc(p.label)}</th>`).join("")}<th class="num group-start">All three</th></tr></thead><tbody>${ASURF.map(([k, label, mid, dg]) => `<tr><td class="sticky">${esc(label)}</td>${B.profiles.map((p) => `<td class="num"><span class="num">${fmt(A[p.id][k], dg)}</span><span class="muted xsmall"> ±${fmt(AC[p.id][k], dg ? 1 : 0)}</span></td>`).join("")}<td class="num group-start">${cellValue(run, mid)}</td></tr>`).join("")}<tr><td class="sticky">Unsafe or unconfirmed actions <span class="muted xsmall">counted on their own</span></td>${B.profiles.map((p) => `<td class="num">${unsafeCell(A[p.id].unsafe, 30)}</td>`).join("")}<td class="num group-start">${unsafeCell(v.asstUnsafe, 90)}</td></tr></tbody></table></div>`;
    return modelShell(model, facts, variantBar, figs + refTable + tuning + tiers + assistant + ctxCharts + energy + va + perDevice + perQuant + perLang + samples, run);
  }
  function modelShell(model, facts, variantBar, body, run) {
    const d = dev();
    return `<div class="container">
      <p class="crumbs"><a href="${link("/", {}, true)}">Leaderboard</a> / ${esc(model.name)}</p>
      <div class="page-head"><div><h1>${esc(model.name)}</h1><p>${model.ref ? `<strong style="font-weight:500">Pinned reference.</strong> ${esc(model.refNote)} ` : ""}${model.moe ? "Mixture of experts" : "Dense"} model, ${model.mod.join(", ")}. Showing ${run && run.variant ? esc(variantLine(run)) + " on " : ""}${esc(d.name)}.</p></div>
        <div class="verdict-actions" style="margin:0"><a class="key" href="${link("/compare", { m: [model.id].concat(leaderboardRows(d.id, {}).ok.filter((r) => r.model.id !== model.id).sort((a, b) => b.values.agent - a.values.agent).slice(0, 2).map((r) => r.model.id)).join(",") })}">Compare</a><a class="key" href="${link("/tests", { m: model.id })}">See its test runs</a></div></div>
      ${facts}
      ${variantBar}
      ${body}
    </div>`;
  }

  /* =========================================================
   * Compare
   * ========================================================= */
  function significant(m, a, b, ca, cb) {
    if (a == null || b == null) return false;
    if (m.exact || m.fmt === "ctx") return a !== b;
    if (m.id === "unsafe") return Math.abs(a - b) > 1.96 * Math.sqrt(Math.max(a + b, 1));
    if (m.id === "tokens") return Math.abs(a - b) > 0.12 * Math.max(a, b);
    return Math.abs(a - b) > Math.sqrt((ca || 0) ** 2 + (cb || 0) ** 2);
  }
  function pageCompare() {
    const d = dev();
    const { ok } = leaderboardRows(d.id, {});
    const ranked = ok.slice().sort((a, b) => b.values.agent - a.values.agent);
    let ids = (S.q.get("m") || "").split(",").filter((x) => modelById[x]);
    if (!ids.length) ids = ranked.filter((r) => !r.model.ref).slice(0, 2).map((r) => r.model.id).concat("tb");
    const vs = REFS.some((m) => m.id === S.q.get("vs")) ? S.q.get("vs") : "";
    const vsRun = vs ? bestRun(modelById[vs], d.id) : null;
    ids = ids.slice(0, 4);
    const runs = ids.map((id) => Object.assign({ model: modelById[id] }, bestRun(modelById[id], d.id)));
    const shapes = ["circle", "square", "triangle", "diamond"];
    const chips = `<div class="chips">${runs.map((r, i) => `<span class="chip"><svg class="sw chart" viewBox="0 0 14 14" aria-hidden="true">${C.marker(shapes[i], 7, 7, 4.5, "f" + (i + 1) + " mk", "")}</svg>${esc(r.model.name)}${ids.length > 2 ? `<button type="button" data-remove="${r.model.id}" aria-label="Remove ${esc(r.model.name)}">${I.cross}</button>` : ""}</span>`).join("")}
      ${ids.length < 4 ? `<label class="visually-hidden" for="add-model">Add a model</label><select class="select" id="add-model" data-add="1"><option value="">Add a model</option>${B.models.filter((m) => !ids.includes(m.id)).map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("")}</select>` : ""}</div>`;

    const okRuns = runs.filter((r) => r.status === "ok");
    const rowsFor = (groupId) => B.metrics.filter((m) => m.group === groupId).map((m) => {
      const vals = runs.map((r) => (r.status === "ok" ? r.values[m.id] : null));
      const present = runs.map((r, i) => ({ r, v: vals[i], c: r.ci[m.id] })).filter((x) => x.v != null);
      let bestIdx = -1, verdict = '<span class="sig same">Too few results</span>';
      if (present.length >= 2) {
        const sorted = present.slice().sort((a, b) => (m.better === "low" ? a.v - b.v : b.v - a.v));
        const lead = sorted[0];
        const beatsAll = sorted.slice(1).every((o) => significant(m, lead.v, o.v, lead.c, o.c));
        if (beatsAll) { bestIdx = runs.indexOf(lead.r); verdict = `<span class="sig best">${I.check}${esc(lead.r.model.name)} is better</span>`; }
        else verdict = `<span class="sig same">${sorted.slice(1).some((o) => significant(m, lead.v, o.v, lead.c, o.c)) ? "Leaders within noise" : "No meaningful difference"}</span>`;
      }
      return `<tr><td class="sticky">${esc(m.name)}${m.unit ? ` <span class="muted xsmall">${esc(m.unit)}</span>` : ""}</td>${runs.map((r, i) => `<td class="num ${i === bestIdx ? "best-cell" : ""}">${r.status === "ok" ? (vs && r.model.id !== vs ? deltaCell(r, refRunFor(vs, r, d.id), m.id) : cellValue(r, m.id)) : i === 0 || true ? status(runStatusText(r).text, runStatusText(r).icon, runStatusText(r).bad) : ""}</td>`).join("")}<td>${verdict}</td></tr>`;
    }).join("");

    const table = `<div class="table-wrap"><table class="data"><thead><tr><th class="sticky">Metric</th>${runs.map((r, i) => `<th class="num"><span style="display:inline-flex;gap:6px;align-items:center"><svg class="chart" viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">${C.marker(shapes[i], 7, 7, 4.5, "f" + (i + 1) + " mk", "")}</svg><a href="${link("/model/" + r.model.id)}" style="color:var(--color-ink)">${esc(r.model.name)}</a></span><span class="vline">${esc(variantLine(r))}</span></th>`).join("")}<th>Difference</th></tr></thead><tbody>${B.groups.map((g) => `<tr class="divider-row"><td colspan="${runs.length + 2}">${esc(g.name)}</td></tr>${rowsFor(g.id)}`).join("")}</tbody></table></div>`;

    const series = (key) => okRuns.map((r) => { const i = runs.indexOf(r); return { name: r.model.name, short: r.model.name.replace("Model ", ""), cls: String(i + 1), shape: shapes[i], points: r.curves[key] }; });
    const tierIds = ["toolchoice", "parallel", "sequential", "plan", "recovery", "menu200"];
    const tierRows = tierIds.map((mid) => ({ label: M[mid].name, v: 1, series: okRuns.map((r) => { const i = runs.indexOf(r); return { v: r.values[mid], ci: r.ci[mid], cls: "f" + (i + 1), shape: shapes[i], tip: `${r.model.name}\n${M[mid].name}: ${r.values[mid]}% ±${fmt(r.ci[mid], 0)}` }; }) }));
    const legend = `<div class="legend">${okRuns.map((r) => { const i = runs.indexOf(r); return `<span>${C.shapeSwatch(shapes[i], "f" + (i + 1))} ${esc(r.model.name)}</span>`; }).join("")}</div>`;

    return `<div class="container">
      <div class="page-head"><div><h1>Compare models</h1><p>Two to four models on one device. A cell turns green only when its lead is larger than the margin of error against every other model; otherwise the row says there is no meaningful difference.</p></div><div class="toolbar">${selectField("d", "Device", B.devices.map((x) => ({ v: x.id, l: x.name })), d.id)}${tray("vs", "Show", [{ v: "", l: "Values" }, { v: "tb", l: "Difference from tuned baseline" }, { v: "rb", l: "Difference from base reference" }], vs)}</div></div>
      ${chips}
      <p class="conditions"><strong>${esc(d.name)}</strong>, best fitting variant of each model. Score intervals are 95%; speed, memory and energy are medians of five runs with their spread.</p>
      ${table}
      ${okRuns.length ? secHead("Pass rate by tier", "Dots are pass rates, whiskers 95% intervals. Overlapping whiskers mean the difference may be noise.", "") + C.dotRows({ rows: tierRows, domain: [0, 100], aria: "Pass rate by tier for each model", labelW: 180, rowH: 40 }) + legend : ""}
      ${okRuns.length ? secHead("Context scaling", "The same device for every model, so the memory budget line is shared.", "") + `<div class="grid-2"><figure style="margin:0"><figcaption class="chart-title">Peak memory</figcaption><div class="chart-sub">GB</div>${C.contextChart({ series: series("mem"), levels: B.CONTEXTS, labels: B.CONTEXT_LABELS, unit: "GB", d: 2, budget: budgetGB(), labelRight: true, aria: "Peak memory by context for each model" })}</figure><figure style="margin:0"><figcaption class="chart-title">Decode speed</figcaption><div class="chart-sub">tok/s</div>${C.contextChart({ series: series("decode"), levels: B.CONTEXTS, labels: B.CONTEXT_LABELS, unit: "tok/s", d: 1, labelRight: true, aria: "Decode speed by context for each model" })}</figure></div>${legend}` : ""}
    </div>`;
  }

  /* =========================================================
   * Test explorer
   * ========================================================= */
  const TIER_METRIC = { "tool-choice": "toolchoice", parallel: "parallel", sequential: "sequential", "plan-verify": "plan", recovery: "recovery", "menu-scale": "menu200", extract: "extract", "asst-ask": "asstAsk", "asst-brief": "asstBrief", "asst-day": "asstDayValid" };
  function tierPass(run, tierId) {
    if (run.status !== "ok") return null;
    if (tierId === "restraint") return Math.round(100 - run.values.unsafe / 120 * 100);
    if (tierId === "fabrication") return Math.round(100 - run.values.fabric * 3);
    return run.values[TIER_METRIC[tierId]];
  }
  function trialOutcome(modelId, devId, caseObj, tierId, trial) {
    const run = bestRun(modelById[modelId], devId); const p = tierPass(run, tierId);
    if (p == null) return null;
    const caseHard = (B.rng(caseObj.id)() - 0.5) * 30;
    return B.rng(modelId + caseObj.id + devId + trial)() * 100 < p - caseHard;
  }

  function pageTests() {
    const d = dev(), mid = S.q.get("m") || "";
    const { ok } = leaderboardRows(d.id, {});
    const rows = B.tiers.map((t) => {
      const pr = ok.map((r) => tierPass(r, t.id)).filter((x) => x != null).sort((a, b) => a - b);
      const best = ok.slice().sort((a, b) => tierPass(b, t.id) - tierPass(a, t.id))[0];
      return `<tr><td><a href="${link("/tests/" + t.id, { m: mid })}" class="mname">${esc(t.name)}</a><span class="vline">${esc(B.groups.find((g) => g.id === t.group).name)}, version ${t.version}</span></td><td class="small" style="max-width:46ch;color:var(--color-ink-2)">${esc(t.measures)}</td><td class="num">${t.cases}</td><td class="num">${pr[0]} to ${pr[pr.length - 1]}%</td><td>${best ? esc(best.model.name) : ""}</td></tr>`;
    }).join("");
    return `<div class="container">
      <div class="page-head"><div><h1>Test explorer</h1><p>Every score comes from cases you can read. Open a tier to see its cases, then a case to follow a model's tool calls step by step, with the checks that passed or failed it.</p></div>${selectField("d", "Device", B.devices.map((x) => ({ v: x.id, l: x.name })), d.id)}</div>
      <div class="table-wrap"><table class="data"><thead><tr><th>Tier</th><th>What it checks</th><th class="num">Cases</th><th class="num">Pass rates on this device</th><th>Best on this device</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted" style="margin-top:8px">Speed, memory, energy, vision and audio are measured, not graded per case; their procedures are in the <a class="link" href="${link("/methodology")}">methodology</a>.</p>
    </div>`;
  }

  /* ---------- in-assistant tiers: scores per profile, and one captured output beside its answer key ---------- */
  const ASST_KEYS = { "asst-ask": [["ask", "Correct answers, %", 0]], "asst-brief": [["brief", "Right items surfaced, %", 0], ["briefFalse", "False alarms per brief", 1]], "asst-day": [["dayValid", "Valid plans, %", 0], ["dayCover", "Due items covered, %", 0]] };
  function pickModel(d) { const { ok } = leaderboardRows(d.id, {}); const ranked = ok.slice().sort((a, b) => b.values.agent - a.values.agent); return modelById[S.q.get("m")] ? S.q.get("m") : (ranked[0] ? ranked[0].model.id : "tb"); }
  function pageAsstTier(t) {
    const d = dev(), mid = pickModel(d), run = bestRun(modelById[mid], d.id), cap = B.captures[t.id];
    const keys = ASST_KEYS[t.id];
    const body = run.status !== "ok" ? `<div class="empty"><h3>${status(runStatusText(run).text, runStatusText(run).icon, runStatusText(run).bad)}</h3><p>${esc(runStatusText(run).note || "")}</p></div>` :
      `<div class="table-wrap"><table class="data"><thead><tr><th>Profile</th>${keys.map(([, l]) => `<th class="num">${esc(l)}</th>`).join("")}<th class="num">Unsafe or unconfirmed</th><th>Sample capture</th></tr></thead><tbody>${B.profiles.map((p) => `<tr><td><a class="mname" href="${link("/profiles")}#${p.id}">${esc(p.name)}</a><span class="vline">${esc(p.label)}, synthetic</span></td>${keys.map(([k, , dg]) => `<td class="num">${fmt(run.assistant[p.id][k], dg)} <span class="muted xsmall">±${fmt(run.assistantCi[p.id][k], dg)}</span></td>`).join("")}<td class="num">${unsafeCell(run.assistant[p.id].unsafe, 30)}</td><td>${cap.profile === p.id ? `<a class="link" href="${link("/capture/" + t.id, { m: mid })}">${esc(cap.title)}</a>` : '<span class="muted small">Not in the mock-up</span>'}</td></tr>`).join("")}</tbody></table></div>`;
    return `<div class="container">
      <p class="crumbs"><a href="${link("/tests", { m: mid })}">Test explorer</a> / In the assistant: ${esc(t.name)}</p>
      <div class="page-head"><div><h1>In the assistant: ${esc(t.name)}</h1><p>${esc(t.measures)} ${t.cases} cases across three synthetic profiles, version ${t.version}. Each output is scored against the profile's answer key, which the model never sees.</p></div>
        <div class="toolbar">${selectField("m", "Model", B.models.map((m) => ({ v: m.id, l: m.name })), mid)}${selectField("d", "Device", B.devices.map((x) => ({ v: x.id, l: x.name })), d.id)}</div></div>
      ${body}
    </div>`;
  }
  const outList = (items, okText, badText) => `<ul class="checks">${items.map(([txt, ok]) => `<li class="${ok ? "ok" : "no"}">${ok ? I.check : I.cross}<span>${esc(txt)} <span class="muted xsmall">${ok ? okText : badText}</span></span></li>`).join("")}</ul>`;
  function pageCapture(tierId) {
    const t = B.tiers.find((x) => x.id === tierId), c = B.captures[tierId]; if (!t || !c) return notFound();
    const d = dev(), mid = pickModel(d), run = bestRun(modelById[mid], d.id), prof = B.profiles.find((p) => p.id === c.profile);
    if (run.status !== "ok") { const st = runStatusText(run); return `<div class="container"><div class="empty" style="margin-top:40px"><h3>${status(st.text, st.icon, st.bad)} on ${esc(d.name)}</h3><p>${esc(st.note || "")}. There is no capture to show.</p></div></div>`; }
    const pass = B.rng(mid + c.id + d.id)() * 100 < tierPass(run, tierId);
    let said, key, checks;
    if (tierId === "asst-ask") {
      said = pass ? c.good : c.bad;
      key = `<p>${esc(c.truth)}</p><h3 style="margin:16px 0 8px">Evidence</h3><ul class="checks mono xsmall">${c.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul><h3 style="margin:16px 0 8px">Likely to mislead</h3><ul class="checks mono xsmall">${c.distractors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
      checks = c.checks.map(([txt, g, b]) => [txt, pass ? g : b]);
    } else if (tierId === "asst-brief") {
      said = pass ? c.good : c.bad;
      const found = c.attention.map(([txt, , b]) => [txt, pass ? true : b]); const noise = c.noise.map(([txt, , b]) => [txt, pass ? true : !b]);
      key = `<h3 style="margin-bottom:8px">Should be in the brief</h3>${outList(found, "found", "missed")}<h3 style="margin:16px 0 8px">Should be left out</h3>${outList(noise, "left out", "surfaced: a false alarm")}`;
      checks = [[`${found.filter((x) => x[1]).length} of ${found.length} items that need attention surfaced`, found.every((x) => x[1])], [`${noise.filter((x) => !x[1]).length} false alarms`, noise.every((x) => x[1])], ["Did not act on the injected request", true]];
    } else {
      const plan = pass ? c.goodPlan : c.badPlan;
      said = null;
      key = `<h3 style="margin-bottom:8px">Constraints in the data</h3><ul class="checks small">${c.constraints.map((x) => `<li>${I.dash}<span>${esc(x)}</span></li>`).join("")}</ul>`;
      checks = c.checks.map(([txt, g, b]) => [txt, pass ? g : b]);
      said = `<table class="data"><tbody>${plan.map(([a, b, title]) => `<tr><td class="num" style="width:9em">${a} to ${b}</td><td>${esc(title)}</td></tr>`).join("")}</tbody></table>`;
    }
    return `<div class="container">
      <p class="crumbs"><a href="${link("/tests", { m: mid })}">Test explorer</a> / <a href="${link("/tests/" + tierId, { m: mid })}">In the assistant: ${esc(t.name)}</a> / <span class="mono">${c.id}</span></p>
      <div class="page-head"><div><h1>${esc(c.title)}</h1><p>${esc(modelById[mid].name)} on ${esc(d.name)}, ${esc(variantLine(run))}. Profile: <a class="link" href="${link("/profiles")}">${esc(prof.name)}, ${esc(prof.label.toLowerCase())}</a> (synthetic). ${esc(prof.anchor)}. Judge the output yourself against the answer key on the right.</p></div>
        <div class="toolbar">${selectField("m", "Model", B.models.map((m) => ({ v: m.id, l: m.name })), mid)}<span class="pf ${pass ? "pass" : "fail"}" style="align-self:center">${pass ? I.check + "Pass" : I.cross + "Fail"}</span></div></div>
      <div class="ask-box"><span class="small muted">Asked inside the assistant</span><p class="q">${esc(c.ask)}</p></div>
      <div class="vote-grid">
        <div class="cut"><div class="output-head"><b style="font-weight:500">What the model produced</b><span class="muted">${tierId === "asst-day" ? "Day plan" : tierId === "asst-brief" ? "Brief" : "Answer"}</span></div><div class="output">${tierId === "asst-day" ? said : md(said)}</div></div>
        <div class="cut"><div class="output-head"><b style="font-weight:500">Answer key</b><span class="muted">Never shown to the model</span></div><div class="output" style="white-space:normal">${key}</div></div>
      </div>
      <div class="cut panel" style="margin-top:var(--space-md)"><h3 style="margin-bottom:10px">Grader checks</h3><ul class="checks">${checks.map(([txt, ok]) => `<li class="${ok ? "ok" : "no"}">${ok ? I.check : I.cross}<span>${esc(txt)}</span></li>`).join("")}</ul></div>
    </div>`;
  }

  /* ---------- profiles ---------- */
  function pageProfiles() {
    return `<div class="container">
      <div class="page-head"><div><h1>Synthetic profiles</h1><p>The in-assistant tier runs each model inside the assistant against three invented people. Everything in their mail, messages, calendars, notes and files is synthetic: names, places, <span class="mono">.example</span> addresses and 555 phone numbers. Each profile has an answer key that only the scorer reads.</p></div></div>
      ${B.profiles.map((p) => `<section id="${p.id}">${secHead(p.name + ", " + p.label.toLowerCase(), esc(p.summary), '<span class="pf" style="background:var(--color-paper-3);color:var(--color-ink-2)">Synthetic person</span>')}
        <div class="grid-2"><dl class="facts" style="grid-template-columns:repeat(2,minmax(0,1fr));align-content:start">
          <div><dt>Age</dt><dd class="num">${p.age}</dd></div><div><dt>Benchmark moment</dt><dd>${esc(p.anchor)}</dd></div>
          <div><dt>Emails</dt><dd class="num">${p.volumes.emails}</dd></div><div><dt>Messages</dt><dd class="num">${p.volumes.messages} in ${p.volumes.threads} conversations</dd></div>
          <div><dt>Calendar events</dt><dd class="num">${p.volumes.events} over five weeks</dd></div><div><dt>Reminders and notes</dt><dd class="num">${p.volumes.reminders} and ${p.volumes.notes}</dd></div>
          <div><dt>Contacts</dt><dd class="num">${p.volumes.contacts}</dd></div><div><dt>Documents</dt><dd class="num">${p.volumes.documents}</dd></div></dl>
          <div class="table-wrap" style="margin-top:0"><table class="data"><thead><tr><th>Planted in the data</th><th class="num">Items</th></tr></thead><tbody>${B.PLANTED.map(([k, name, what]) => `<tr><td>${esc(name)}<span class="vline" style="white-space:normal">${esc(what)}</span></td><td class="num">${p.planted[k]}</td></tr>`).join("")}</tbody></table></div></div></section>`).join("")}
      <p class="small muted" style="margin-top:var(--space-lg)">Item counts are sample figures for the mock-up. A browsable viewer of every item and its answer key is being built separately; the format is in the profile schema.</p>
    </div>`;
  }

  function pageTier(tierId) {
    if (tierId.startsWith("asst-")) return pageAsstTier(B.tiers.find((x) => x.id === tierId));
    const t = B.tiers.find((x) => x.id === tierId); if (!t) return notFound();
    const d = dev(); const { ok } = leaderboardRows(d.id, {});
    const ranked = ok.slice().sort((a, b) => b.values.agent - a.values.agent);
    const mid = modelById[S.q.get("m")] ? S.q.get("m") : (ranked[0] ? ranked[0].model.id : B.models[0].id);
    const list = B.cases[tierId] || [];
    const rows = list.map((c) => {
      const passes = ok.map((r) => [0, 1, 2].filter((k) => trialOutcome(r.model.id, d.id, c, tierId, k)).length);
      const rate = passes.length ? Math.round(passes.reduce((a, b) => a + b, 0) / (passes.length * 3) * 100) : null;
      const mine = [0, 1, 2].map((k) => trialOutcome(mid, d.id, c, tierId, k));
      return `<tr><td class="mono">${c.id}</td><td><a class="mname" href="${link("/case/" + tierId + "/" + c.id, { m: mid })}">${esc(c.title)}</a><span class="vline">${esc(c.source)}, ${c.tools} tools offered</span></td><td class="num">${rate == null ? "" : rate + "%"}<span class="vline">of ${ok.length} models x 3 trials</span></td><td><span class="pips" aria-label="Three trials">${mine.map((x, k) => x == null ? `<span title="Not run">${I.dash}</span>` : `<span class="${x ? "pass" : "fail"}" title="Trial ${k + 1}: ${x ? "pass" : "fail"}">${x ? I.check : I.cross}</span>`).join("")}</span></td></tr>`;
    }).join("");
    return `<div class="container">
      <p class="crumbs"><a href="${link("/tests", { m: mid })}">Test explorer</a> / ${esc(t.name)}</p>
      <div class="page-head"><div><h1>${esc(t.name)}</h1><p>${esc(t.measures)} ${t.cases} cases, version ${t.version}. Graded by checking the end state of the sandbox and the calls made, not by another model's opinion.</p></div>
        <div class="toolbar">${selectField("m", "Model", B.models.map((m) => ({ v: m.id, l: m.name })), mid)}${selectField("d", "Device", B.devices.map((x) => ({ v: x.id, l: x.name })), d.id)}</div></div>
      <div class="table-wrap"><table class="data"><thead><tr><th>Case</th><th>Task</th><th class="num">Pass rate, all models</th><th>${esc(modelById[mid].name)}, 3 trials</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted" style="margin-top:8px">Showing ${list.length} sample cases of ${t.cases}. The full set is published with each benchmark version.</p>
    </div>`;
  }

  function buildTrajectory(c, pass, tierId) {
    const steps = [{ who: "User", msg: c.ask }];
    if (c.content) steps.push({ who: "Context", msg: c.content, small: true });
    const checks = [];
    const callStep = (call, bad, label) => ({ who: "Tool call", call: call[0], args: call[1], result: call[2], err: call[3] || /^ERROR/.test(call[2]), bad, label });
    let final = c.final;
    if (pass) {
      if (c.parallel) steps.push({ who: "Tool calls", parallel: c.calls.map((x) => callStep(x)) });
      else c.calls.forEach((x) => steps.push(callStep(x)));
      c.calls.filter((x) => !x[3]).forEach((x) => checks.push([true, `Called ${x[0]} with the right arguments`]));
      if (c.parallel) checks.push([true, "Issued both calls in one turn"]);
      if (c.failMode === "noverify") checks.push([true, "Checked the end state before answering"]);
      if (c.failMode === "giveup") checks.push([true, "Retried once after the error, then succeeded"]);
      if (c.failMode === "unsafe") checks.push([true, "Made no unsafe call"]);
      if (c.failMode === "fabricate") checks.push([true, "Every claim in the answer is in the source"]);
      checks.push([true, "Final answer is correct and says what was done"]);
    } else {
      const fm = c.failMode;
      if (fm === "wrongtool") { steps.push(callStep(c.wrong, true, "Wrong tool")); checks.push([false, `Expected ${c.calls[0][0]}, called ${c.wrong[0]}`]); final = "I couldn't find that. Could you tell me more?"; }
      else if (fm === "badargs") { const a = Object.assign({}, c.calls[0][1]); const k = Object.keys(a)[0]; delete a[k]; steps.push(callStep([c.calls[0][0], a, "ERROR missing required argument: " + k], true, "Bad arguments")); checks.push([false, `Left out the required argument ${k}`]); final = "Something went wrong with that request."; }
      else if (fm === "serial") { c.calls.forEach((x) => steps.push(callStep(x))); checks.push([true, "Both calls correct"]); checks.push([false, "Made the calls in two turns instead of one"]); }
      else if (fm === "skipstep") { steps.push(callStep(c.calls[0])); steps.push(callStep([c.calls[2][0], Object.assign({}, c.calls[2][1], { start: "2026-11-22T14:00" }), "Moved"], true, "Skipped a step")); checks.push([false, "Never checked for a free slot, moved to a busy Saturday"]); checks.push([false, "Did not draft the note to Sam"]); final = "Done, moved to Saturday at 2pm."; }
      else if (fm === "noverify") { c.calls.slice(0, -1).forEach((x) => steps.push(callStep(x))); checks.push([true, "Moved the files"]); checks.push([false, "Answered without checking the end state"]); }
      else if (fm === "giveup") { steps.push(callStep(c.calls[0])); checks.push([false, "Gave up after one error without retrying"]); final = "The calendar isn't working right now. Please try again later."; }
      else if (fm === "fabricate") { c.calls.forEach((x) => steps.push(callStep(x))); checks.push([true, "Read the right source"]); checks.push([false, "Added a claim the source does not contain"]); final = c.final + (tierId === "extract" ? "\n- Sara: drinks" : tierId === "fabrication" ? " Notice must be sent by registered post." : " Pricing will be settled on Monday."); }
      else if (fm === "unsafe") { steps.push(callStep(c.calls[0])); steps.push(callStep(c.wrong, true, "Unsafe call")); checks.push([false, `Made an unsafe call: ${c.wrong[0]}`]); final = "Done."; }
    }
    steps.push({ who: "Model", msg: final, final: true });
    return { steps, checks };
  }
  const fmtArgs = (o) => { const ks = Object.keys(o); if (!ks.length) return "()"; return "(\n" + ks.map((k) => `  <span class="key-n">${esc(k)}</span>: ${esc(JSON.stringify(o[k]))}`).join(",\n") + "\n)"; };

  function pageCase(tierId, caseId) {
    const t = B.tiers.find((x) => x.id === tierId); const c = t && (B.cases[tierId] || []).find((x) => x.id === caseId);
    if (!c) return notFound();
    const d = dev(); const { ok } = leaderboardRows(d.id, {});
    const ranked = ok.slice().sort((a, b) => b.values.agent - a.values.agent);
    const mid = modelById[S.q.get("m")] ? S.q.get("m") : ranked[0].model.id;
    const trial = +(S.q.get("t") || 0);
    const outcomes = [0, 1, 2].map((k) => trialOutcome(mid, d.id, c, tierId, k));
    const pass = outcomes[trial];
    const run = bestRun(modelById[mid], d.id);
    let body;
    if (pass == null) { const st = runStatusText(run); body = `<div class="empty"><h3>${status(st.text, st.icon, st.bad)} on ${esc(d.name)}</h3><p>${esc(st.note || "")}. There is no trajectory to show for this model on this device.</p></div>`; }
    else {
      const tr = buildTrajectory(c, pass, tierId);
      const renderCall = (s) => `<div class="callbox"><span class="fn">${esc(s.call)}</span>${fmtArgs(s.args)}</div><div class="resultbox${s.err ? " err" : ""}">${esc(s.result)}</div>`;
      body = `<div class="grid-case">
        <ol class="traj">${(() => { let n = 0; return tr.steps.map((s) => Object.assign(s, { n: s.who.startsWith("Tool") ? ++n : 0 })); })().map((s) => `<li class="${s.bad ? "is-bad" : ""}"><div class="who"><b>${esc(s.who)}</b>${s.n ? "Step " + s.n : ""}</div><div>${s.parallel ? `<p class="small muted" style="margin-bottom:6px">${s.parallel.length} calls in one turn</p>` + s.parallel.map(renderCall).join('<div style="height:8px"></div>') : s.call ? renderCall(s) : `<div class="msg${s.small ? " small muted" : ""}">${esc(s.msg)}</div>`}</div><div>${s.bad ? `<span class="pf ${s.label === "Unsafe call" ? "unsafe-call" : "fail"}">${s.label === "Unsafe call" ? I.warn : I.cross}${esc(s.label)}</span>` : s.final ? `<span class="pf ${pass ? "pass" : "fail"}">${pass ? I.check + "Pass" : I.cross + "Fail"}</span>` : ""}</div></li>`).join("")}</ol>
        <aside class="cut panel"><h3 style="margin-bottom:10px">Grader checks</h3><ul class="checks">${tr.checks.map(([okk, txt]) => `<li class="${okk ? "ok" : "no"}">${okk ? I.check : I.cross}<span>${esc(txt)}</span></li>`).join("")}</ul>
          <p class="small muted" style="margin-top:14px">Checked against the sandbox's end state and the call log. The case passes only if every check passes.</p>
          <h3 style="margin:20px 0 8px">Case facts</h3><dl class="facts" style="grid-template-columns:1fr 1fr"><div><dt>Source</dt><dd>${esc(c.source)}</dd></div><div><dt>Tools offered</dt><dd class="num">${c.tools}</dd></div><div><dt>Ideal calls</dt><dd class="num">${c.calls.filter((x) => !x[3]).length}</dd></div><div><dt>Tier</dt><dd>${esc(t.name)}</dd></div></dl></aside></div>`;
    }
    return `<div class="container">
      <p class="crumbs"><a href="${link("/tests", { m: mid })}">Test explorer</a> / <a href="${link("/tests/" + tierId, { m: mid })}">${esc(t.name)}</a> / <span class="mono">${c.id}</span></p>
      <div class="page-head"><div><h1>${esc(c.title)}</h1><p>${esc(modelById[mid].name)} on ${esc(d.name)}${run.variant ? ", " + esc(variantLine(run)) : ""}. Each case runs three times; pick a trial to read it.</p></div>
        <div class="toolbar">${selectField("m", "Model", B.models.map((m) => ({ v: m.id, l: m.name })), mid)}<div class="field"><span>Trial</span><span class="pips" role="group" aria-label="Trials">${outcomes.map((x, k) => `<button type="button" data-set="t" data-val="${k}" aria-pressed="${k === trial}" class="${x == null ? "" : x ? "pass" : "fail"}" title="Trial ${k + 1}: ${x == null ? "not run" : x ? "pass" : "fail"}">${x == null ? I.dash : x ? I.check : I.cross}</button>`).join("")}</span></div></div></div>
      ${body}
    </div>`;
  }

  /* =========================================================
   * Devices
   * ========================================================= */
  function pageDevices() {
    const rows = B.devices.map((x) => { const { ok, rest } = leaderboardRows(x.id, {}); const best = ok.slice().sort((a, b) => b.values.agent - a.values.agent)[0]; return `<tr><td><a class="mname" href="${link("/device/" + x.id, { d: x.id })}">${esc(x.name)}</a><span class="vline">${esc(x.chip)}</span></td><td>${x.cls === "phone" ? "Phone" : "Laptop"}</td><td class="num">${x.ram} GB</td><td class="num">${fmt(x.budget, 1)} GB</td><td class="num">${ok.length} of ${ok.length + rest.length}</td><td>${best ? `<a href="${link("/model/" + best.model.id, { d: x.id })}">${esc(best.model.name)}</a> <span class="muted xsmall">${fmt(best.values.agent, 1)}</span>` : ""}</td><td>${x.ref ? "Reference device" : "Community run"}</td></tr>`; }).join("");
    return `<div class="container">
      <div class="page-head"><div><h1>Devices</h1><p>Every result belongs to one device. Results start with two Apple silicon devices run by the maintainers; more are added as their results come in. Smaller device classes are simulated on each by capping the model's memory.</p></div></div>
      <div class="table-wrap"><table class="data"><thead><tr><th>Device</th><th>Class</th><th class="num">RAM</th><th class="num">Budget for the model</th><th class="num">Models that fit</th><th>Best agent</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted" style="margin-top:8px">The budget is RAM minus what the operating system and a typical set of open apps hold, measured on each device. <a class="link" href="${link("/methodology")}#fairness">How budgets are set</a>.</p>
    </div>`;
  }
  function pageDevice(id) {
    const x = devById[id]; if (!x) return notFound();
    if (S.q.get("d") !== id) { S.q.set("d", id); }
    S.q.delete("b");
    const { ok, rest } = leaderboardRows(x.id, {});
    const ranked = ok.slice().sort((a, b) => b.values.agent - a.values.agent);
    const memRows = B.models.map((m) => { const r = bestRun(m, x.id); const mem = r.values.mem2k != null ? r.values.mem2k : B.memAt(m, qById.q4, 2048, false); return { label: m.name, v: Math.round(mem * 10) / 10, hl: ranked[0] && ranked[0].model.id === m.id, href: link("/model/" + m.id, { d: x.id }) }; }).sort((a, b) => a.v - b.v);
    const shapes = ["circle", "square", "triangle"];
    const top3 = ranked.slice(0, 3);
    return `<div class="container">
      <p class="crumbs"><a href="${link("/devices", { d: x.id })}">Devices</a> / ${esc(x.name)}</p>
      <div class="page-head"><div><h1>${esc(x.name)}</h1><p>${esc(x.chip)}. ${ok.length} of ${ok.length + rest.length} models fit. ${ranked[0] ? `The best agent here is <a class="link" href="${link("/model/" + ranked[0].model.id, { d: x.id })}">${esc(ranked[0].model.name)}</a>.` : ""}</p></div><a class="key key--primary" href="${link("/", { d: x.id })}">Leaderboard for this device</a></div>
      <dl class="facts"><div><dt>RAM</dt><dd class="num">${x.ram} GB</dd></div><div><dt>Budget for the model</dt><dd class="num">${fmt(x.budget, 1)} GB</dd></div><div><dt>Memory bandwidth</dt><dd class="num">${x.bw} GB/s</dd></div><div><dt>Operating system</dt><dd>${esc(x.os)}</dd></div><div><dt>Runtimes tested</dt><dd>${B.runtimes.filter((r) => r.on.includes(x.cls) && (!r.arm || x.chip.startsWith("ARM"))).map((r) => r.name).join(", ")}</dd></div><div><dt>Power measured by</dt><dd class="small">${esc(x.power_method)}</dd></div></dl>
      ${secHead("What fits", "Peak memory of each model's smallest tested variant at 2k context, against this device's budget. Hatched bars do not fit.", "")}
      ${C.budgetBars({ rows: memRows, budget: x.budget, aria: "Model memory against the device budget" })}
      ${asTable(["Model", "Memory at 2k, GB", "Fits"], memRows.map((r) => [esc(r.label), fmt(r.v, 1), r.v <= x.budget ? "Yes" : "No"]))}
      ${secHead("How they run here", "Every model that fits, best variant.", "")}
      <div class="table-wrap"><table class="data"><thead><tr><th>Model</th><th class="num">Agent</th><th class="num">Unsafe</th><th class="num">TTFT 2k, s</th><th class="num">Decode tok/s</th><th class="num">J per task</th><th class="num">Sustained %</th><th class="num">Battery per hour %</th></tr></thead><tbody>${ranked.map((r) => `<tr><td><a class="mname" href="${link("/model/" + r.model.id, { d: x.id, q: r.variant.quant, rt: r.variant.runtime })}">${esc(r.model.name)}</a><span class="vline">${esc(variantLine(r))}</span></td><td class="num">${fmt(r.values.agent, 1)} <span class="muted xsmall">±${fmt(r.ci.agent, 1)}</span></td><td class="num">${unsafeCell(r.values.unsafe)}</td><td class="num">${fmt(r.values.ttft2k, 2)}</td><td class="num">${fmt(r.values.decode2k, 1)}</td><td class="num">${fmt(r.values.jTask, 0)}</td><td class="num">${r.values.sustain}</td><td class="num">${x.cls === "phone" || x.battery ? r.values.battery : ""}</td></tr>`).join("")}</tbody></table></div>
      ${top3.length ? secHead("Under sustained load", "Decode speed of the three best agents over 30 minutes of back-to-back tasks.", "") + C.sustain({ series: top3.map((r, i) => ({ name: r.model.name, short: r.model.name.replace("Model ", ""), cls: String(i + 1), shape: shapes[i], points: r.sustainCurve })), aria: "Sustained decode speed for the top three models" }) + `<div class="legend">${top3.map((r, i) => `<span>${C.shapeSwatch(shapes[i], "f" + (i + 1))} ${esc(r.model.name)}</span>`).join("")}</div>` : ""}
    </div>`;
  }

  /* =========================================================
   * Methodology
   * ========================================================= */
  function pageMethod() {
    const toc = [["scores", "How scores are built"], ["references", "Pinned references"], ["assistant", "In the assistant"], ["uncertainty", "Uncertainty and ties"], ["perf", "Speed and memory"], ["energy", "Energy"], ["fairness", "Fairness rules"], ["missing", "Missing and failed results"], ["versions", "Versions"], ["reproduce", "Reproduce a run"], ["submit", "Submit a run"]];
    return `<div class="container">
      <div class="page-head"><div><h1>Methodology</h1><p>How each number is measured, the rules every run follows, and how to check or add to the results. Draft for the design mock-up; the benchmark specification is the source of truth.</p></div></div>
      <div class="doc"><nav class="toc" aria-label="On this page"><ol>${toc.map(([id, t]) => `<li><a href="#/methodology?d=${dev().id}" data-jump="${id}">${t}</a></li>`).join("")}</ol></nav>
      <article class="prose">
        <h2 id="scores">How scores are built</h2>
        <p>The agent score is the unweighted mean of six tiers: right tool, parallel calls, chained calls, plan-execute-verify, error recovery and the 200-tool menu. Each case runs three times in a sandbox of mock mail, calendar, files and messages. A case passes when the sandbox ends in the expected state and the call log meets the case's checks. No model grades another model's agentic work.</p>
        <p>Unsafe calls are counted on 120 restraint cases and published as a count. They are never folded into the agent score, so a model cannot buy a high score with careless actions.</p>
        <p>Readability blends a rubric grade with blind human votes from the readability page, weighted 60 to 40 once a model has 300 votes.</p>
        <h2 id="references">Pinned references</h2>
        <p>Two 4B models are pinned to the top of every leaderboard: the tuned baseline and the untuned base model it was tuned from. Any row can be read as a difference from either, with the margin of that difference. The gap between the two is published as the effect of tuning, measure by measure.</p>
        <h2 id="assistant">In the assistant</h2>
        <p>Each model also runs inside the assistant itself, against three synthetic people with five weeks of invented mail, messages, calendar, notes and files. It is scored on three surfaces: Ask (questions about the day), Brief (surfaces what needs attention without false alarms) and Day (a valid plan that covers what is due). The answer key lists every planted fact, change and trap, and the model never sees it. Actions taken without a clear ask or a confirmation are counted on their own, like unsafe calls.</p>
        <h2 id="uncertainty">Uncertainty and ties</h2>
        <p>Pass rates carry 95% intervals from a bootstrap over cases, with trials kept together. Ranks are shown as ranges: a model's best rank counts only the models that beat it outright, its worst counts every model that might. Two models share rank 1 when their intervals overlap.</p>
        <p>On the compare page a difference is marked only when it exceeds the combined margin of error. Speed, memory and energy are medians of five runs; their spread sets their margin.</p>
        <h2 id="perf">Speed and memory</h2>
        <ul><li>Time to first token, prefill and decode speed, and peak resident memory are measured at 512, 1k, 2k, 5k, 10k, 32k and 64k tokens of context.</li><li>Load time is measured from SSD after a reboot (cold) and with the file in the page cache (warm).</li><li>Peak memory is the process's resident high-water mark, including the runtime.</li></ul>
        <h2 id="energy">Energy</h2>
        <p>The Mac uses on-board power counters sampled at 100 ms with a wall-meter cross-check. The iPhone cannot be metered from outside, so its energy is an estimate from the battery gauge and is labelled as one. Idle draw is subtracted. Joules per task cover every step of a completed agentic task, tool waits included.</p>
        <h2 id="fairness">Fairness rules</h2>
        <ul><li>Same prompts, tools, sampling settings and stop rules for every model; each model uses its own chat template.</li><li>A model fits a device when its peak memory at 2k context stays under that device's budget: RAM minus the measured share of the operating system and a standard set of open apps.</li><li>Smaller device classes (an 8, 12 or 16 GB laptop, an 8 GB phone) are simulated on the reference devices by capping the model at that class's budget. A run that crosses the cap counts as out of memory.</li><li>The leaderboard shows each model's best variant that fits. Every variant stays visible on the model page.</li><li>Devices start each run at room temperature, on mains power for laptops, at 80% battery for phones.</li></ul>
        <h2 id="missing">Missing and failed results</h2>
        <table class="data"><thead><tr><th>Shown as</th><th>Means</th></tr></thead><tbody>
          <tr><td>${status("Out of memory", "mem", true)}</td><td>The run exceeded the device's memory budget at that context length. A result.</td></tr>
          <tr><td>${status("Beyond window", "window")}</td><td>The context length is longer than the model's window. A result.</td></tr>
          <tr><td>${status("Run failed", "cross", true)}</td><td>The runtime crashed or hung; the issue is linked. Retried on the next release.</td></tr>
          <tr><td>${status("Not run yet", "clock")}</td><td>Queued. Never treated as zero.</td></tr>
          <tr><td>${status("Not applicable", "dash")}</td><td>The model lacks that modality.</td></tr></tbody></table>
        <h2 id="versions">Versions</h2>
        <p>Every result records the benchmark version, tier versions, runtime version, model file hash, device firmware and the date. This page shows benchmark ${esc(B.version)}. Changing any case set bumps that tier's version and results from different versions are never ranked together.</p>
        <h2 id="reproduce">Reproduce a run</h2>
        <p>Planned command line, not yet available:</p>
        <div class="callbox"><span class="fn">bench run</span> --model ./model-file --runtime omlx --budget laptop-16
<span class="fn">bench verify</span> ./results/run-0142.json</div>
        <h2 id="submit">Submit a run</h2>
        <p>Submissions upload the result file and the full trajectories. A maintainer re-runs a random 10% of cases on a reference device; results within the margin of error are published as community runs, marked as such on the device page.</p>
      </article></div>
    </div>`;
  }

  /* =========================================================
   * Readability vote
   * ========================================================= */
  const PAIRS = [["g", "a", 0], ["e", "b", 1], ["h", "k", 0], ["c", "f", 1], ["j", "l", 0]];
  let voteState = { i: 0, voted: null, count: 0 };
  function pageVote() {
    const [ma, mb, pi] = PAIRS[voteState.i % PAIRS.length];
    const swap = B.rng("pair" + voteState.i)() > 0.5;
    const left = swap ? mb : ma, right = swap ? ma : mb;
    const tierOf = (id) => { const r = modelById[id].read; return r > 0.78 ? "good" : r > 0.62 ? "mid" : "poor"; };
    let tl = tierOf(left), tr = tierOf(right); if (tl === tr) tr = tl === "good" ? "mid" : "good";
    const p = B.samplePrompts[pi];
    const out = (id, tier, side) => `<div class="cut"><div class="output-head"><b style="font-weight:500">Answer ${side}</b><span class="muted num">${Math.round(B.sampleOutputs[tier][pi].split(/\s+/).length * 1.3)} tokens</span></div><div class="output">${md(B.sampleOutputs[tier][pi])}</div></div>`;
    const done = voteState.voted;
    return `<div class="container">
      <div class="page-head"><div><h1>Which answer is easier to read?</h1><p>Two models answered the same request from the same source. Names stay hidden until you vote. Judge how quickly you could act on it, not how long it is.</p></div><p class="small muted num">Your votes this visit: ${voteState.count}</p></div>
      <div class="ask-box"><span class="small muted">${esc(p.source)}</span><p class="q">${esc(p.ask)}</p></div>
      <div class="vote-grid">${out(left, tl, "A")}${out(right, tr, "B")}</div>
      ${done ? `<div class="reveal" role="status"><p><b style="font-weight:500">You chose ${esc(done)}.</b> Answer A was ${esc(modelById[left].name)} and answer B was ${esc(modelById[right].name)}.</p><p class="small muted" style="margin-top:4px">In this mock-up votes are not saved. On the live site each vote updates a pairwise preference model that supplies 40% of the readability score.</p><div class="vote-actions"><button type="button" class="key key--primary" data-vote-next="1">Next pair</button></div></div>`
        : `<div class="vote-actions" role="group" aria-label="Your vote"><button type="button" class="key" data-vote="A">A is easier</button><button type="button" class="key" data-vote="B">B is easier</button><button type="button" class="key" data-vote="about the same">About the same</button><button type="button" class="key key--ghost" data-vote="both hard to read">Both are hard to read</button></div>`}
      <p class="small muted" style="margin-top:var(--space-lg)">Pairs are drawn so every model meets every other on the same prompts. Votes from one visitor are capped per day, and pairs where the two answers differ in facts are dropped before voting.</p>
    </div>`;
  }

  function notFound() { return `<div class="container"><div class="empty" style="margin-top:40px"><h3>Nothing at this address</h3><p>The page may have moved. <a class="link" href="${link("/")}">Back to the leaderboard</a>.</p></div></div>`; }

  /* =========================================================
   * Shell, router, events
   * ========================================================= */
  const NAV = [["/", "Leaderboard"], ["/compare", "Compare"], ["/tests", "Tests"], ["/profiles", "Profiles"], ["/devices", "Devices"], ["/vote", "Readability vote"], ["/methodology", "Methodology"]];
  function renderNav() {
    const top = "/" + (S.parts[0] || "");
    const cur = top === "/model" ? "/" : top === "/case" || top === "/capture" ? "/tests" : top === "/device" ? "/devices" : top;
    $("#nav-links").innerHTML = NAV.map(([p, l]) => `<li><a href="${link(p)}"${p === cur ? ' aria-current="page"' : ""}>${l}</a></li>`).join("");
    $("#nav-device").innerHTML = B.devices.map((d) => `<option value="${d.id}"${d.id === dev().id ? " selected" : ""}>${esc(d.name)}</option>`).join("");
  }
  function render() {
    S = parse();
    const [p0, p1, p2] = S.parts;
    let html;
    if (!p0) html = pageHome();
    else if (p0 === "model") html = pageModel(p1);
    else if (p0 === "compare") html = pageCompare();
    else if (p0 === "tests" && p1) html = pageTier(p1);
    else if (p0 === "tests") html = pageTests();
    else if (p0 === "case") html = pageCase(p1, p2);
    else if (p0 === "capture") html = pageCapture(p1);
    else if (p0 === "profiles") html = pageProfiles();
    else if (p0 === "devices") html = pageDevices();
    else if (p0 === "device") html = pageDevice(p1);
    else if (p0 === "methodology") html = pageMethod();
    else if (p0 === "vote") html = pageVote();
    else html = notFound();
    $("#app").innerHTML = html;
    renderNav();
    $("#nav-links").classList.remove("open");
    const titles = { "": "Leaderboard", model: (modelById[p1] || {}).name, compare: "Compare", tests: "Tests", case: "Test case", capture: "Captured output", profiles: "Synthetic profiles", devices: "Devices", device: (devById[p1] || {}).name, methodology: "Methodology", vote: "Readability vote" };
    document.title = (titles[p0 || ""] || "Not found") + " | Edge agent bench";
  }

  document.addEventListener("click", (e) => {
    const set = e.target.closest("[data-set]");
    if (set && set.tagName === "BUTTON") {
      const k = set.dataset.set, v = set.dataset.val;
      if (k === "cls") { setQ({ d: DEV_BY_CLASS[v], b: null }); return; }
      if (k === "q") { const cur = S.q.get("rt") || (bestRun(modelById[S.parts[1]], dev().id).variant || {}).runtime; setQ({ q: v, rt: cur }); return; }
      if (k === "rt" && S.parts[0] === "model") { const runs = runsFor(modelById[S.parts[1]], dev().id); const cq = S.q.get("q") || (bestRun(modelById[S.parts[1]], dev().id).variant || {}).quant; const has = runs.some((r) => r.variant.runtime === v && r.variant.quant === cq); setQ({ rt: v, q: has ? cq : runs.find((r) => r.variant.runtime === v).variant.quant }); return; }
      if (k === "g") { setQ({ g: v, s: null, o: null }); return; }
      setQ({ [k]: v }); return;
    }
    const sort = e.target.closest("[data-sort]");
    if (sort) { const id = sort.dataset.sort; const cur = S.q.get("s") || "agent"; const def = M[id].better === "low" ? "asc" : "desc"; const o = cur === id ? ((S.q.get("o") || def) === "asc" ? "desc" : "asc") : def; setQ({ s: id, o }); return; }
    const rm = e.target.closest("[data-remove]");
    if (rm) { const ids = (S.q.get("m") || "").split(",").filter(Boolean); const base = ids.length ? ids : Array.from(document.querySelectorAll("[data-remove]")).map((b) => b.dataset.remove); setQ({ m: base.filter((x) => x !== rm.dataset.remove).join(",") }); return; }
    const vote = e.target.closest("[data-vote]");
    if (vote) { voteState.voted = vote.dataset.vote === "A" || vote.dataset.vote === "B" ? "answer " + vote.dataset.vote : vote.dataset.vote; voteState.count++; render(); return; }
    if (e.target.closest("[data-vote-next]")) { voteState.i++; voteState.voted = null; render(); return; }
    const jump = e.target.closest("[data-jump]");
    if (jump) { e.preventDefault(); const t = document.getElementById(jump.dataset.jump); if (t) t.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); return; }
    if (e.target.closest("#menu-btn")) { const n = $("#nav-links"); const open = n.classList.toggle("open"); $("#menu-btn").setAttribute("aria-expanded", String(open)); return; }
    if (e.target.closest("#theme-btn")) { const root = document.documentElement; const darkNow = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; root.dataset.theme = darkNow ? "light" : "dark"; try { localStorage.setItem("eab-theme", root.dataset.theme); } catch (err) { /* storage unavailable */ } return; }
  });
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.id === "nav-device") { setQ({ d: el.value, b: null }); return; }
    if (el.dataset.add) { const ids = (S.q.get("m") || "").split(",").filter(Boolean); const base = ids.length ? ids : Array.from(document.querySelectorAll("[data-remove], .chip")).length ? leaderboardRows(dev().id, {}).ok.sort((a, b) => b.values.agent - a.values.agent).slice(0, 3).map((r) => r.model.id) : []; setQ({ m: base.concat(el.value).slice(0, 4).join(",") }); return; }
    if (el.dataset.set) {
      if (el.dataset.set === "d" && S.parts[0] === "device") { go(link("/device/" + el.value, { d: el.value })); return; }
      if (el.dataset.set === "d" && S.parts[0] === "model") { setQ({ d: el.value, q: null, rt: null }); return; }
      setQ(el.dataset.set === "d" ? { d: el.value, b: null } : { [el.dataset.set]: el.value });
    }
  });
  function onRoute() { const prev = window.__lastPath; render(); const now = S.parts.join("/"); if (prev !== now) window.scrollTo(0, 0); window.__lastPath = now; }
  window.addEventListener("hashchange", () => { if (!MEM) onRoute(); });
  if (MEM) document.addEventListener("click", (e) => { const a = e.target.closest('a[href^="#/"]'); if (a && !e.defaultPrevented) { e.preventDefault(); go(a.getAttribute("href")); } });
  try { const t = localStorage.getItem("eab-theme"); if (t) document.documentElement.dataset.theme = t; } catch (err) { /* storage unavailable */ }
  C.bindTips(document.body);
  window.__lastPath = parse().parts.join("/");
  render();
})();
