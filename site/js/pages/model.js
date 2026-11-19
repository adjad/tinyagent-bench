/* Model page: one entry (a model at one quantisation, runtime and thinking mode) on the chosen device, or one cloud
 * reference. Local: header and facts, the variant tray, headline figures, the two pinned references, scores by tier,
 * restraint and privacy, context scaling, energy, and the same model on every device. An entry that is not scored
 * (does not fit, invalid, fit not measured, not run, failed) gets an empty state that says why and where it does run.
 * Cloud: never ranked, its run date and provider, the references and the best local entry, families, and cost. */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store, C = EDAT.charts, CFG = EDAT.config;
  const { esc, fmt, I } = U;
  const HEAD = "agentic_score";
  const LEVELS = CFG.contextLevels, LABELS = CFG.contextLabels;

  const ok = (c) => !!c && c.status === "ok";
  const val = (e, id) => { const c = ST.value(e, id); return ok(c) ? c.value : null; };
  const nameOf = (e) => e.name + (e.thinkingText ? ", " + e.thinkingText : "");
  const median = (xs) => { const s = xs.filter((x) => x != null).sort((a, b) => a - b); if (!s.length) return null; const k = s.length >> 1; return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2; };
  const statusText = (key) => (R.statuses[key] || { text: String(key).replace(/_/g, " ") }).text;
  const tidy = (s) => String(s).replace(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase());
  const notFound = () => { throw Object.assign(new Error("not found"), { notFound: true }); };
  const unpublished = () => U.status("no_value", { text: "Not published" });
  const wrap = (label, inner) => `<div class="table-wrap" role="region" aria-label="${esc(label)}" tabindex="0">${inner}</div>`;
  const unitTxt = (m) => (m.unit ? ` <span class="muted xsmall">${esc(m.unit === "%" ? "%" : m.unit)}</span>` : "");
  const SAMPLING = { author_recommended: "The author's recommended settings", provider_default: "The provider's defaults", harness_default: "The harness defaults" };
  const KIND = { family: "family", lane: "lane", surface: "surface", profile: "person", band: "band", test: "test" };
  const TITLES = { "agentic|family": "Agentic families", "agentic|lane": "Agentic lanes", "in_assistant|surface": "In the assistant, by surface", "in_assistant|profile": "In the assistant, by synthetic person", "quality|family": "Output quality, by family" };

  /* why an entry has no scores on this page; null when it is scored */
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
  const ranked = (board) => board.entries.filter((x) => !x.cloud && x.group === "ranked" && !withheld(x));
  const byScore = (list) => list.slice().sort((a, b) => (val(b, HEAD) ?? -1) - (val(a, HEAD) ?? -1));
  const boardBudget = (b) => b.entries.map((x) => (x.run.fit || {}).budget_bytes).find(Boolean) || null;

  /* the entry's best ranked variant on a board, else its first */
  function pickEntry(list, key) {
    return list.find((x) => x.key === key) || byScore(list.filter((x) => x.group === "ranked" && !withheld(x)))[0] || list[0] || null;
  }

  function fitText(e, short) {
    const f = e.run.fit || {}, dev = e.board.device || {};
    if (e.cloud || f.verdict === "not_applicable") return U.status("not_applicable_cloud");
    if (f.verdict === "fits") return U.status("ok", { text: short ? "Yes, at 10k context" : "Fits " + dev.fitLabel + " at 10k" });
    if (f.verdict === "fits_small_context") return U.status("ok", { text: short ? "At 2k only" : "Small context only" });
    if (f.verdict === "does_not_fit") return U.status("does_not_fit");
    return U.status("not_measured");
  }
  const runIds = (e) => (e.run.run_ids && e.run.run_ids.length ? `<span class="mono xsmall" style="overflow-wrap:anywhere">${e.run.run_ids.map(esc).join(", ")}</span>` : unpublished());
  const mono = (s) => (s && s !== "-" ? `<span class="mono xsmall" style="overflow-wrap:anywhere">${esc(s)}</span>` : unpublished());
  const paramsTxt = (n) => (n == null ? null : fmt(n / 1e9, n % 1e9 === 0 ? 0 : 1) + "B");

  /* ---------- a facet's value (a breakdown row), through the registry when it names the row ---------- */
  function facetValue(e, tier, metric, facet) {
    const m = R.metrics.find((x) => x.src.r === tier && x.src.m === metric && (x.src.f || null) === (facet || null));
    if (m) return ST.value(e, m.id);
    const b = e.board, rec = b.result(e.key, tier, metric, facet);
    if (!rec) return { status: b.has(tier, metric, facet) ? "not_run" : "not_in_board" };
    if (rec.status && rec.status !== "ok") return { status: rec.status };
    if (rec.value == null) return { status: "no_value" };
    const iv = rec.interval && rec.interval_kind !== "none" ? rec.interval : null;
    return { status: "ok", value: rec.value, interval: iv, half: iv ? (iv[1] - iv[0]) / 2 : null, n: rec.n_cases };
  }
  const facetMetric = (b, tier, facet) => ["pass1", "score_mean"].find((m) => b.has(tier, m, facet)) || "pass1";

  /* ---------- scores by tier: dot-and-whisker rows ---------- */
  function tierCharts(ctx, e) {
    const b = e.board, dev = ctx.device, field = dev ? ranked(dev) : [];
    const devName = dev ? dev.device.fitLabel : "this device";
    const order = R.tiers.map((t) => t.id).concat(b.tiers().filter((t) => !R.tier[t]));
    const figs = [];
    order.filter((t) => b.has(t)).forEach((tier) => {
      const tdef = R.tier[tier] || { name: tidy(tier), facets: [] };
      const kinds = [...new Set((tdef.facets || []).concat(b.facets(tier).map((f) => f.split("=")[0])))].filter((k) => b.facets(tier, k).length);
      const total = R.metrics.find((m) => m.src.r === tier && !m.src.f && m.tierLink === tier && !m.planned);
      kinds.forEach((kind, ki) => {
        const href = ctx.link("/tests/" + U.seg(tier), { m: e.key });
        const rows = [];
        const mk = (label, cell, med, n) => ({
          label, v: ok(cell) ? cell.value : null, ci: ok(cell) ? cell.half || 0 : 0, ref: med, href,
          missing: ok(cell) ? "" : statusText(cell.status),
          tip: ok(cell) ? `${label}: ${fmt(cell.value, 1)}${cell.half != null ? " ±" + fmt(cell.half, 1) : ""}${cell.n ? ", " + cell.n + " cases" : ""}${med != null ? "\nMedian of ranked entries on " + devName + ": " + fmt(med, 1) : ""}` : label,
          cell, n
        });
        if (ki === 0 && total) {
          const med = dev && dev.has(tier, total.src.m) ? median(field.map((x) => val(x, total.id))) : null;
          rows.push(mk(total.name, ST.value(e, total.id), med));
        }
        b.facets(tier, kind).forEach((f) => {
          const metric = facetMetric(b, tier, f), label = R.facetLabel(f);
          const med = dev && dev.has(tier, metric, f) ? median(field.map((x) => { const c = facetValue(x, tier, metric, f); return ok(c) ? c.value : null; })) : null;
          rows.push(mk(label, facetValue(e, tier, metric, f), med));
        });
        const title = TITLES[tier + "|" + kind] || `${tdef.name}, by ${KIND[kind] || kind}`;
        const hasMed = rows.some((r) => r.ref != null);
        const how = [
          "The dot is the pass rate on a 0 to 100 scale; the whisker is its 95% interval. Where two whiskers would overlap, the difference may be noise.",
          hasMed ? `The short upright tick is the median of the ranked entries on ${esc(devName)}, so you can see where this entry sits against the field.` : "",
          kind === "lane" ? "The assistant lane uses the assistant's own tools; the neutral lane uses a generic tool set. A large gap between them suggests a home advantage." : "",
          kind === "family" && tier === "agentic" ? "The agentic score is the mean of the seven families; each family is the mean of its two lanes, three trials per case." : "",
          "Each name opens the test area's cases for this entry."
        ].filter(Boolean);
        figs.push(`<figure style="margin:0"><figcaption class="chart-title">${esc(title)}</figcaption><div class="chart-sub">Pass rate, %, higher is better</div>
          ${C.dotRows({ rows, domain: [0, 100], step: 20, aria: title + " for " + nameOf(e), labelW: 190 })}
          ${U.howTo(how)}
          ${U.asTable(["Measure", "Pass rate, %", "± 95%", "Cases", "Median of ranked entries"], rows.map((r) => [`<a href="${esc(r.href)}">${esc(r.label)}</a>`, ok(r.cell) ? fmt(r.cell.value, 1) : U.statusFor(r.cell), ok(r.cell) && r.cell.half != null ? fmt(r.cell.half, 1) : "", ok(r.cell) && r.cell.n ? String(r.cell.n) : "", r.ref != null ? fmt(r.ref, 1) : ""]), title)}
        </figure>`);
      });
    });
    if (!figs.length) return U.secHead("Scores by tier", "", "", "tiers") + U.empty("No breakdowns in this board", "This board publishes no per-family or per-surface results for this entry.");
    return U.secHead("Scores by tier", `Each test area broken down into its parts${dev && dev.device.kind === "phone" && !e.cloud ? "; the iPhone runs a smaller parity subset, so its intervals are wider" : ""}. Names open the cases.`, "", "tiers") + `<div class="grid-2">${figs.join("")}</div>`;
  }

  /* ---------- restraint and privacy: counted on their own, never in a score ---------- */
  const SAFE_M = { unsafe: { name: "Unsafe actions", short: "Unsafe" }, leak: { name: "Privacy leaks", short: "Leaks" } };
  function safetySection(ctx, e) {
    const b = e.board;
    const tiers = [...new Set(R.tiers.map((t) => t.id).concat(b.tiers()))].filter((t) => b.has("safety§" + t));
    if (!tiers.length) return U.secHead("Restraint and privacy", "", "", "safety") + U.empty("Not in this board yet", "This board has no unsafe-action or privacy-leak counts yet.");
    const cellOf = (rec, kind) => {
      if (!rec) return { status: "not_run" };
      const per = rec[kind === "unsafe" ? "unsafe_per_100" : "leaks_per_100"];
      if (per == null) return { status: "not_applicable" };
      return { status: "ok", value: per, count: rec[kind + "_trials"], trials: rec.trials, rateInterval: rec[kind + "_interval"] };
    };
    const ivTxt = (c) => (ok(c) && c.rateInterval ? `${fmt(c.rateInterval[0], 1)} to ${fmt(c.rateInterval[1], 1)}%` : "");
    const cntTxt = (c) => (ok(c) && c.count != null ? `${c.count} of ${c.trials}` : "");
    const rows = tiers.map((t) => {
      const rec = b.safety(e.key, t), u = cellOf(rec, "unsafe"), l = cellOf(rec, "leak");
      const name = (R.tier[t] || { name: tidy(t) }).name;
      return `<tr><th scope="row" class="sticky"><a href="${esc(ctx.link("/tests/" + U.seg(t), { m: e.key }))}">${esc(name)}</a></th><td class="num">${U.safetyCell(u, SAFE_M.unsafe)}</td><td class="num">${cntTxt(u)}</td><td class="num">${ivTxt(u)}</td><td class="num group-start">${U.safetyCell(l, SAFE_M.leak)}</td><td class="num">${cntTxt(l)}</td><td class="num">${ivTxt(l)}</td></tr>`;
    }).join("");
    const head = `<thead><tr><th scope="col" class="sticky">Test area</th><th scope="col" class="num">Unsafe actions<span class="vline">per 100 trials</span></th><th scope="col" class="num">Trials with one</th><th scope="col" class="num">95% interval<span class="vline">share of trials</span></th><th scope="col" class="num group-start">Privacy leaks<span class="vline">per 100 trials</span></th><th scope="col" class="num">Trials with one</th><th scope="col" class="num">95% interval<span class="vline">share of trials</span></th></tr></thead>`;
    const extra = ["restraint", "over_refusal", "format_ok"].filter((id) => R.metric[id]).map((id) => [R.metric[id].name, U.cell(e, id) + (ok(ST.value(e, id)) ? unitTxt(R.metric[id]) : "")]);
    return U.secHead("Restraint and privacy", "Unsafe actions are forbidden or unconfirmed actions; privacy leaks are private details sent where the task did not need them. Both are counted on their own in every test area and never blended into a score.", "", "safety") +
      wrap("Unsafe actions and privacy leaks by test area", `<table class="data"><caption class="visually-hidden">Unsafe actions and privacy leaks for ${esc(nameOf(e))}, by test area</caption>${head}<tbody>${rows}</tbody></table>`) +
      (extra.length ? U.facts(extra) : "") +
      `<p class="small" style="margin-top:var(--space-sm)"><a class="link" href="${esc(ctx.link("/tests/agentic", { m: e.key }))}">Read its agentic cases, unsafe ones included</a></p>`;
  }

  /* ---------- against the references, and the effect of tuning ---------- */
  const KEY = ["agentic_score", "assistant_loop_score", "repair_gain", "lane_assistant", "lane_neutral", "pass_all", "unsafe", "leaks", "assistant_score", "quality_pass", "decode_2k", "mem_10k"];
  function refsSection(ctx, e, refBoard) {
    const rb = refBoard || e.board, refs = ["tuned", "base"].map((role) => ({ role, e: rb.refEntry(role) }));
    const ids = KEY.filter((id) => R.metric[id] && !(e.cloud && R.metric[id].on === "local"));
    const head = `<thead><tr><th scope="col" class="sticky">Measure</th><th scope="col" class="num">This entry</th>${refs.map((r) => `<th scope="col" class="num">Difference from ${r.e ? esc(r.e.name) : r.role === "tuned" ? "the tuned reference" : "the base reference"}<span class="vline">${r.role === "tuned" ? "tuned reference" : "base reference"}</span></th>`).join("")}</tr></thead>`;
    const rows = ids.map((id) => { const m = R.metric[id]; return `<tr><th scope="row" class="sticky">${esc(m.name)}${unitTxt(m)}</th><td class="num">${U.cell(e, id)}</td>${refs.map((r) => `<td class="num">${U.deltaCell(e, id, r.role, e.cloud ? rb : null)}</td>`).join("")}</tr>`; }).join("");
    return U.secHead("Against the references", `Differences from the two pinned references on ${esc(rb.device ? rb.device.name : "this device")}, each with its margin of error. A mark appears only when the gap is larger than the margin; ≈ marks an unpaired estimate. Rare events also show the ratio.`, "", "refs") +
      wrap("Against the references", `<table class="data"><caption class="visually-hidden">${esc(nameOf(e))} against the two pinned references</caption>${head}<tbody>${rows}</tbody></table>`);
  }

  function tuningSection(ctx, e) {
    const b = e.board, t = b.refEntry("tuned"), base = b.refEntry("base");
    if (!t || !base) return "";
    const order = [...new Set(["agentic", "safety"].concat(R.groups.map((g) => g.id)))];
    const gname = (g) => (R.group[g] ? R.group[g].name : "Restraint and privacy");
    const used = R.metrics.filter((m) => m.on !== "cloud" && (ok(ST.value(t, m.id)) || ok(ST.value(base, m.id))));
    const body = order.map((g) => {
      const ms = used.filter((m) => m.group === g);
      if (!ms.length) return "";
      return `<tr class="divider-row"><td colspan="4">${esc(gname(g))}</td></tr>` + ms.map((m) => `<tr><th scope="row" class="sticky">${esc(m.name)}${unitTxt(m)}</th><td class="num">${U.cell(base, m.id)}</td><td class="num">${U.cell(t, m.id)}</td><td class="num">${U.deltaCell(t, m.id, "base")}</td></tr>`).join("");
    }).join("");
    return U.secHead("Effect of tuning", `The tuned reference minus the base reference on ${esc(b.device.name)}, ${t.line === base.line ? "both at " + esc(t.line) : "at " + esc(t.line) + " and " + esc(base.line)}. Every measure this board has for both. The speed and memory rows act as a check that tuning changed only behaviour: they should not move.`, "", "tuning") +
      wrap("Effect of tuning", `<table class="data"><caption class="visually-hidden">Tuned reference minus base reference</caption><thead><tr><th scope="col" class="sticky">Measure</th><th scope="col" class="num">${esc(base.name)}<span class="vline">base reference</span></th><th scope="col" class="num">${esc(t.name)}<span class="vline">tuned reference</span></th><th scope="col" class="num">Effect of tuning<span class="vline">tuned minus base</span></th></tr></thead><tbody>${body}</tbody></table>`);
  }

  /* ---------- context scaling ---------- */
  const CURVES = [
    { m: "footprint_peak_bytes", title: "Peak memory", sub: "GB, lower is better", unit: "GB", d: 1, scale: 1 / 1073741824, mem: true },
    { m: "prefill_tps", title: "Prefill speed", sub: "tok/s, higher is better", unit: "tok/s", d: 0 },
    { m: "decode_tps", title: "Decode speed", sub: "tok/s, higher is better", unit: "tok/s", d: 1 },
    { m: "ttft_s", title: "Time to first token", sub: "seconds, lower is better", unit: "s", d: 2 }
  ];
  function curvePoints(curve, scale) {
    return LEVELS.map((lv) => {
      const p = curve && curve.points.find((x) => x.level === lv);
      if (!p) return { v: null, status: "not_run", label: statusText("not_run") };
      if (p.status === "ok" && p.value != null) return { v: p.value * scale, status: "ok" };
      const st = p.status === "ok" ? "no_value" : p.status;
      return { v: null, status: st, need: p.need_bytes != null ? U.GiB(p.need_bytes) : null, needBytes: p.need_bytes, label: statusText(st) };
    });
  }
  function curveSource(e, metric) {
    if (e.board.curve(e.key, metric)) return e;
    const v = e.variant;
    return e.board.entries.find((o) => o.key !== e.key && o.model.id === v.model_id && o.variant.quant === v.quant && o.variant.runtime === v.runtime && o.board.curve(o.key, metric)) || null;
  }
  function contextSection(ctx, e) {
    const b = e.board, d = b.device, fit = e.run.fit || {};
    const budget = U.GiB(fit.budget_bytes || boardBudget(b));
    const shared = CURVES.map((c) => curveSource(e, c.m)).find((s) => s && s !== e) || null;
    const series = CURVES.map((c) => { const curve = ST.curveFor(e, c.m); return { c, curve, pts: curve ? curvePoints(curve, c.scale || 1) : null }; });
    if (!series.some((s) => s.curve)) {
      return U.secHead("Context scaling", "", "", "context") + `<div class="chart-frame">${U.empty("Not measured on this device", `No speed or memory curve is published for this entry on ${esc(d.name)}${b.has("curve§decode_tps") ? " yet" : ": the performance tier is not in this board yet"}. Nothing is drawn rather than a guess.`)}</div>`;
    }
    const hows = {
      footprint_peak_bytes: [`The line is peak memory at each context length, measured on the ${esc(d.name)} itself.`, budget ? `The dashed line is the fit line for ${esc(d.fitLabel)}, ${fmt(budget, 1)} GB. The verdict is read at 10k: under the line there means it fits; under it only at 2k means small context only.` : "No fit line is published for this device.", "A cross marks the first length that ran out of the device's real memory, with the memory it needed in the tooltip. A hatched band is beyond the model's context window."],
      prefill_tps: ["How fast the prompt is read, in tokens per second. It usually slows as context grows.", "A cross, a hatched band or a label marks a length that did not run, where it happened."],
      decode_tps: ["How fast the answer is written, after the prompt is read.", "It falls with context because each new token reads the whole cache."],
      ttft_s: ["The wait before anything appears, mostly prefill time.", "Lower is better. A failed length is marked where it happened rather than leaving the line to end silently."]
    };
    const figs = series.map(({ c, curve, pts }) => {
      const how = U.howTo(hows[c.m]);
      if (!curve) return `<figure class="chart-frame" style="margin:0"><figcaption class="chart-title">${esc(c.title)}</figcaption><div class="chart-sub">${esc(c.sub)}</div>${U.empty("Not measured", b.has("curve§" + c.m) ? "This entry has no curve for this measure yet." : "Not in this board yet.")}${how}</figure>`;
      return `<figure style="margin:0"><figcaption class="chart-title">${esc(c.title)}</figcaption><div class="chart-sub">${esc(c.sub)}</div>${C.contextChart({ series: [{ name: nameOf(e), cls: "A", shape: "circle", points: pts }], levels: LEVELS, labels: LABELS, unit: c.unit, d: c.d, budget: c.mem && budget ? budget : null, budgetLabel: "Fit line", aria: c.title + " by context length, " + nameOf(e) })}${how}</figure>`;
    }).join("");
    const cellTxt = (s, i) => {
      if (!s.pts) return U.status(b.has("curve§" + s.c.m) ? "not_run" : "not_in_board");
      const p = s.pts[i];
      if (p.v != null) return fmt(p.v, s.c.d);
      return U.statusFor({ status: p.status, need: p.needBytes });
    };
    const table = U.asTable(["Context, tokens"].concat(series.map((s) => s.c.title + ", " + s.c.unit)), LEVELS.map((lv, i) => [esc(LABELS[i])].concat(series.map((s) => cellTxt(s, i)))), "Context scaling");
    let fitLine;
    if (fit.verdict === "fits" || fit.verdict === "fits_small_context") fitLine = `Fits up to <strong class="num">${esc(U.ctxLabel(fit.max_level_fits))}</strong> tokens of context within the ${budget ? fmt(budget, 1) + " GB " : ""}fit line for ${esc(d.fitLabel)}.`;
    else if (fit.verdict === "does_not_fit") fitLine = `Over the fit line even at 2k: it needs <span class="num">${fmt(U.GiB(fit.peak_bytes_at_2048), 1)} GB</span> at 2k against a ${fmt(budget, 1)} GB fit line, so no context length fits ${esc(d.fitLabel)}.`;
    else fitLine = "The fit gate has not measured this entry, so no longest context is stated.";
    const sharedTxt = shared ? ` These curves are shared from the ${esc(shared.thinkingText || "other")} entry with the same weights (<a class="link" href="${esc(ctx.link("/model/" + U.seg(shared.model.id), { v: shared.key }))}">${esc(nameOf(shared))}</a>): speed and memory belong to the weights, like the fit verdict.` : "";
    return U.secHead("Context scaling", `Seven context lengths from ${esc(LABELS[0])} to ${esc(LABELS[LABELS.length - 1])} tokens on ${esc(d.name)}. Four separate charts, never a shared axis.`, "", "context") +
      `<div class="grid-4">${figs}</div>${table}<p class="small" style="margin-top:var(--space-xs);color:var(--color-ink-2)">${fitLine}${sharedTxt}</p>`;
  }

  /* ---------- energy: registry metrics only, so it lights up when the store writes them ---------- */
  const SPLIT = [["j_task_prefill", "Prefill", "seg1"], ["j_task_decode", "Decode", "seg2"], ["j_task_tools", "Tool waits", "seg3"]];
  function energySection(ctx, e) {
    const d = e.board.device, phone = d.kind === "phone";
    const ids = ["j_task", "mj_prefill", "mj_decode"].filter((id) => R.metric[id]);
    const facts = U.facts(ids.map((id) => [R.metric[id].name, U.cell(e, id) + (ok(ST.value(e, id)) ? unitTxt(R.metric[id]) : "")]));
    const segs = SPLIT.filter(([id]) => R.metric[id]).map(([id, label, cls]) => ({ id, label, cls, c: ST.value(e, id) }));
    const how = U.howTo(["One median completed task, split into reading prompts (prefill), writing answers (decode) and waiting on tools.", "Long tool results make prefill grow; long answers and thinking make decode grow. The exact joules are in the figures beside the bar.", phone ? "On the iPhone, energy per task and per prefill token are not in version 1: the phone gives no per-phase power." : "Idle draw is subtracted."]);
    let chart;
    if (segs.length === SPLIT.length && segs.every((s) => ok(s.c) && s.c.value >= 0) && segs.some((s) => s.c.value > 0)) {
      chart = C.stackBar({ segments: segs.map((s) => ({ label: s.label, v: s.c.value, cls: s.cls })), aria: "Where one task's energy goes, " + nameOf(e) }) + how +
        U.asTable(["Part of the task", "Joules"], segs.map((s) => [esc(s.label), fmt(s.c.value, 0)]), "Energy split for one task");
    } else {
      chart = U.empty(phone ? "Not in version 1 on the iPhone" : "Not in this board yet", phone ? "The iPhone gives no per-phase power, so there is no energy split and no energy per task in version 1." : "This board publishes no energy measurements yet. The split appears here once the store writes energy per task; nothing is drawn rather than a guess.") + how;
    }
    return U.secHead("Energy", `Measured on ${esc(d.name)}${phone ? "" : ", Mac only in version 1"}.`, "", "energy") +
      `<div class="grid-2"><div>${facts}</div><figure class="chart-frame" style="margin:0"><figcaption class="chart-title">Where one task's energy goes</figcaption><div class="chart-sub">Prefill, decode and tool waits</div>${chart}</figure></div>`;
  }

  /* ---------- the same model on every device ---------- */
  function everyDevice(ctx, e, boards) {
    const id = e.model.id;
    const rows = boards.map((b) => {
      const list = b.entries.filter((x) => x.model.id === id);
      const devLink = (x) => esc(ctx.link("/model/" + U.seg(id), { d: b.device.id, v: x ? x.key : null }));
      if (!list.length) return `<tr class="row-dim"><th scope="row" class="sticky"><a href="${devLink(null)}">${esc(b.device.name)}</a></th><td colspan="6">${U.status("not_run", { text: "Not run on this device" })}</td></tr>`;
      return list.map((x, i) => {
        const why = withheld(x), here = x.key === e.key && b === e.board;
        const score = why ? U.status(why, why === "invalid" ? { text: `Invalid run: ${fmt(x.run.excluded_rate, 1)}% excluded` } : null) : U.scoreCell(x, HEAD);
        return `<tr class="${here ? "is-top" : why ? "row-dim" : ""}"><th scope="row" class="sticky">${i === 0 ? `<a href="${devLink(x)}">${esc(b.device.name)}</a>` : `<span class="visually-hidden">${esc(b.device.name)}</span>`}${here ? ' <span class="muted xsmall">showing</span>' : ""}</th><td><a href="${devLink(x)}">${esc(U.entryLine(x))}</a></td><td>${fitText(x)}</td><td class="num">${score}</td><td class="num">${why ? "" : U.cell(x, "unsafe")}</td><td class="num">${U.cell(x, "decode_2k")}</td><td class="num">${U.cell(x, "mem_10k")}</td></tr>`;
      }).join("");
    }).join("");
    return U.secHead("On every device", "Every entry of this model in this board. Each device has its own fit line; entries that are not ranked show why instead of a score.", "", "devices") +
      wrap("On every device", `<table class="data"><caption class="visually-hidden">${esc(e.name)} on every device</caption><thead><tr><th scope="col" class="sticky">Device</th><th scope="col">Entry</th><th scope="col">Fit</th><th scope="col" class="num">Agentic score<span class="vline">±95% interval</span></th><th scope="col" class="num">Unsafe actions<span class="vline">per 100 trials</span></th><th scope="col" class="num">Decode at 2k<span class="vline">tok/s</span></th><th scope="col" class="num">Peak memory at 10k<span class="vline">GB</span></th></tr></thead><tbody>${rows}</tbody></table>`);
  }

  /* ---------- the variant tray: every entry of this model on this device ---------- */
  function variantBar(ctx, e, list) {
    const optLabel = (x) => [CFG.quants[x.variant.quant] || x.variant.quant, x.thinkingText || (x.variant.thinking ? "thinking " + x.variant.thinking : ""), (CFG.runtimes[x.variant.runtime] || x.variant.runtime)].filter(Boolean).join(", ") + (withheld(x) ? ", " + statusText(withheld(x)).toLowerCase() : "");
    const devOpts = ctx.boardInfo.devices.map((x) => ({ v: x.id, l: x.name }));
    const tray = list.length > 1 ? U.tray("v", "Variant on this device", list.map((x) => ({ v: x.key, l: optLabel(x), title: U.entryLine(x) })), e.key)
      : `<div class="field"><span>Variant on this device</span><p class="small" style="min-height:40px;display:flex;align-items:center">${esc(optLabel(e))}, the only one</p></div>`;
    const sib = list.filter((x) => x.key !== e.key && x.variant.quant === e.variant.quant && x.variant.runtime === e.variant.runtime && x.thinking !== e.thinking);
    const sibTxt = sib.length ? `<p class="small muted" style="margin-top:var(--space-xs)">${esc(e.thinkingText ? tidy(e.thinkingText) + "." : "")} The same model with ${sib.map((x) => `<a class="link" href="${esc(ctx.link("/model/" + U.seg(x.model.id), { v: x.key }))}">${esc(x.thinkingText || "the other thinking mode")}</a>`).join(" and ")} is a separate entry, scored and ranked on its own.</p>` : "";
    return `<div class="toolbar" style="margin:var(--space-lg) 0 var(--space-sm)">${e.cloud ? "" : U.selectField("d", "Device", devOpts, ctx.devId)}${tray}</div>${sibTxt}`;
  }

  function factsLocal(e) {
    const m = e.model, v = e.variant, d = e.board.device;
    return U.facts([
      ["Parameters", m.params_total != null ? `<span class="num">${esc(paramsTxt(m.params_total))} total${m.params_active != null && m.params_active !== m.params_total ? ", " + esc(paramsTxt(m.params_active)) + " active" : ""}</span>` : unpublished()],
      ["Architecture", m.moe ? "Mixture of experts" : m.architecture && m.architecture !== "unknown" ? esc(tidy(m.architecture)) : unpublished()],
      ["Quantisation", v.quant ? esc(CFG.quants[v.quant] || v.quant) : unpublished()],
      ["Runtime", esc(CFG.runtimes[v.runtime] || v.runtime || "") + (v.runtime_version ? ` <span class="mono xsmall">${esc(v.runtime_version)}</span>` : "")],
      ["Thinking", esc(v.thinking === "on" ? "On" : v.thinking === "off" ? "Off" : v.thinking ? tidy(v.thinking) : "No thinking mode") + (v.headline_entry ? ", the shipped mode" : "")],
      ["Sampling", v.sampling_source ? esc(SAMPLING[v.sampling_source] || tidy(v.sampling_source)) : unpublished()],
      ["Fits " + d.fitLabel, fitText(e, true)],
      ["Weights", mono(v.weights)],
      ["Harness commit", mono(e.run.harness_commit)],
      ["Runs", runIds(e)]
    ]);
  }

  function fig(e, label, id, small, bad) {
    const m = R.metric[id], c = ST.value(e, id);
    if (!m) return "";
    if (!ok(c)) return `<div><dt>${esc(label)}</dt><dd><span class="small">${U.statusFor(c)}</span></dd></div>`;
    return `<div><dt>${esc(label)}</dt><dd class="${bad && c.value > 0 ? "bad" : ""}">${fmt(c.value, m.dp)}<small>${c.half != null && c.half > 0 ? "±" + fmt(c.half, m.dp) : esc(small || "")}</small></dd></div>`;
  }

  /* ---------- profile across the tests: one bar per test area from zero, with its interval and, where the store
   * names one, the selection tier ("core, ±x points"). Areas not in this board are left out, never drawn as zero. ---------- */
  const PROFILE = [
    ["Agentic, minimal loop", "agentic_score", "/tests/agentic"], ["In the assistant", "assistant_score", "/tests/in_assistant"], ["What it found", "findings_found", "/found"],
    ["Understanding text", "text_understanding", "/tests/quality"], ["Readability", "readability", "/tests/quality"], ["Writing", "writing", "/tests/quality"],
    ["Following preferences", "instruction_following", "/tests/preferences"], ["Memory", "memory_pass", "/tests/memory"], ["Late turns, long sessions", "late_retention", "/tests/long_session"],
    ["Screen control", "screen_pass", "/tests/app_control"], ["Sidekick", "sidekick_pass", "/tests/sidekick"], ["Vision", "vision_pass", "/tests/vision"]
  ];
  const selText = (sel) => (sel && sel.tier ? sel.tier + (sel.ci95_half_width_pts != null ? ", ±" + fmt(sel.ci95_half_width_pts, 1) + " points" : "") : "");
  function profileSection(ctx, e) {
    const rows = PROFILE.map(([label, id, path]) => ({ label, id, path, c: R.metric[id] ? ST.value(e, id) : null })).filter((r) => r.c && r.c.status !== "not_in_board" && !(R.metric[r.id].planned && r.c.status !== "ok"));
    if (!rows.some((r) => ok(r.c))) return "";
    const href = (r) => esc(ctx.link(r.path, { m: e.key }));
    const chart = C.hbars({
      rows: rows.map((r) => {
        const sel = ok(r.c) ? selText(r.c.selection) : "";
        return { label: r.label + (sel ? " (" + sel + ")" : ""), v: ok(r.c) ? r.c.value : null, ci: ok(r.c) ? r.c.interval : null, href: href(r), hl: r.id === HEAD, status: ok(r.c) ? null : statusText(r.c.status) };
      }),
      labelW: 250, domain: [0, 100], unit: "%", dp: 0, tickStep: 25, aria: "Score in each test area for " + nameOf(e)
    });
    const table = U.asTable(["Test area", "Score", "95% interval", "Measured as", "Selection"], rows.map((r) => [
      `<a class="link" href="${href(r)}">${esc(r.label)}</a>`, ok(r.c) ? fmt(r.c.value, 1) : U.statusFor(r.c), ok(r.c) && r.c.interval ? fmt(r.c.interval[0], 1) + " to " + fmt(r.c.interval[1], 1) : "", ok(r.c) && r.c.label ? esc(r.c.label) : "", ok(r.c) ? esc(selText(r.c.selection)) : ""]), "Profile across the tests");
    const how = U.howTo(["One bar per test area, each scored 0 to 100 and drawn from zero. The whisker is the 95% interval.", "The areas measure different things on different cases, so read each bar against the same area for other models, not against the bar beside it.", "Where a score comes from a selection of the cases rather than all of them, the selection and its margin are printed beside the name.", "Areas this board has not measured are left out, never drawn as zero. Unsafe actions and privacy leaks are counted on their own, below."]);
    return U.secHead("Profile across the tests", `Every test area with a score for ${esc(nameOf(e))} on ${esc(e.board.device.name)}.`, "", "profile") + `<figure class="chart-frame" style="margin:0;max-width:760px"><div class="scroll-x">${chart}</div>${how}${table}</figure>`;
  }

  /* ---------- what it found in the assistant: tallies of the store's marks per section, over every person and trial ---------- */
  const FOUND_PARTS = [["In the Brief", ["brief"]], ["To-dos", ["brief_todos", "todo"]], ["Needs attention", ["inbox", "messages"]], ["Actions it took by itself", ["act"]], ["Questions about the day", ["window"]]];
  const MARKS = [["found", "found", "good"], ["right", "right to do", "good"], ["missed", "missed", "muted"], ["not_done", "not done", "muted"], ["stale", "stale", "warn"], ["wrong", "wrongly raised", "bad"], ["done_wrongly", "done wrongly", "bad"], ["should_not", "should not have done", "bad"], ["unsafe", "unsafe", "bad"]];
  function foundSection(ctx, e, fnd) {
    const runs = fnd && Array.isArray(fnd.runs) ? fnd.runs.filter((r) => r.variant === e.key) : [];
    if (!runs.length) return "";
    const tally = FOUND_PARTS.map(([title, secs]) => {
      const t = {};
      runs.forEach((r) => Object.entries(r.sections || {}).forEach(([sec, items]) => {
        const base = sec.split("@")[0];
        if (!secs.includes(base) || !Array.isArray(items)) return;
        items.forEach((it) => { const k = it.mark || "other"; t[k] = (t[k] || 0) + 1; });
      }));
      return { title, t, n: Object.values(t).reduce((a, b) => a + b, 0) };
    }).filter((x) => x.n);
    if (!tally.length) return "";
    const people = new Set(runs.map((r) => r.profile)).size, trials = new Set(runs.map((r) => r.trial)).size;
    const words = (t) => MARKS.filter(([k]) => t[k]).map(([k, w]) => `<span class="num">${t[k]}</span> ${esc(w)}`).concat(Object.keys(t).filter((k) => !MARKS.some(([m]) => m === k)).map((k) => `<span class="num">${t[k]}</span> ${esc(k.replace(/_/g, " "))}`)).join(" · ");
    const bar = (x) => `<div class="tally" role="img" aria-label="${esc(x.title + ": " + words(x.t).replace(/<[^>]+>/g, ""))}">${MARKS.filter(([k]) => x.t[k]).map(([k, w, cls]) => `<span class="tally-seg tally-${cls}" style="flex-grow:${x.t[k]}" title="${esc(x.t[k] + " " + w)}">${x.t[k] / x.n > 0.12 ? x.t[k] : ""}</span>`).join("")}</div>`;
    const rows = tally.map((x) => `<div class="tally-row"><p class="tally-name">${esc(x.title)}<span class="xsmall muted"> ${x.n} items</span></p>${bar(x)}<p class="xsmall tally-words">${words(x.t)}</p></div>`).join("");
    return U.secHead("What it found in the assistant", `Every item ${esc(nameOf(e))} surfaced, missed or did, marked against the answer key, over ${people} synthetic ${people === 1 ? "person" : "people"} and ${trials} ${trials === 1 ? "trial" : "trials"}. Development split only.`, `<a class="key" href="${esc(ctx.link("/found", { m: e.key }))}">Read the items</a>`, "found") +
      `<div class="chart-frame tally-block">${rows}<p class="legend"><span><span class="tally-key tally-good"></span> Found or right to do</span><span><span class="tally-key tally-muted"></span> Missed or not done</span><span><span class="tally-key tally-warn"></span> Stale</span><span><span class="tally-key tally-bad"></span> Wrong, should not have done, or unsafe</span></p><p class="xsmall muted">The counts are items, not a score; the score for this area is "What it found" in the profile above. Each row also says its counts in words, so nothing rests on colour.</p></div>`;
  }

  /* ---------- the page shell ---------- */
  function shell(ctx, e, sub, actions, body) {
    return `<div class="container">
      <p class="crumbs"><a href="${esc(ctx.link("/", {}, true))}">Leaderboard</a> / ${esc(e.name)}</p>
      <div class="page-head"><div><h1>${esc(e.name)} ${U.refBadge(e)} ${U.fitBadge(e)}</h1><p>${sub}</p></div><div class="verdict-actions" style="margin:0">${actions}</div></div>
      ${body}
    </div>`;
  }
  function compareKeys(ctx, e, board) {
    const r = byScore(ST.bestPerModel(ranked(board), HEAD)).filter((x) => x.key !== e.key && x.model.id !== e.model.id);
    const mine = ST.storeRank(e, HEAD);
    const tied = mine ? r.filter((x) => { const o = ST.storeRank(x, HEAD); return o && o[0] <= mine[1] && o[1] >= mine[0]; }) : [];
    let keys = [e.key].concat((tied.length ? tied : r).slice(0, 2).map((x) => x.key));
    const tk = board.references.tuned;
    if (tk && !keys.includes(tk)) keys.push(tk);
    return { keys: keys.slice(0, 4), tied: tied.length > 0 };
  }

  /* ---------- local entry ---------- */
  async function renderLocal(ctx, e, list) {
    const b = e.board, d = b.device, why = withheld(e), fit = e.run.fit || {};
    let boards = [b];
    try { boards = await ST.loadAllDevices(ctx.boardInfo.version); } catch (err) { boards = [b]; }
    if (!boards.includes(b)) boards = boards.map((x) => (x.device.id === b.device.id ? b : x));
    const cmp = compareKeys(ctx, e, b);
    const actions = `<a class="key" href="${esc(ctx.link("/compare", { m: cmp.keys.join(",") }))}">Compare with ${cmp.tied ? "the tied entries" : "the next entries"}</a><a class="key" href="${esc(ctx.link("/tests/agentic", { m: e.key }))}">Open its cases</a><a class="key" href="${esc(ctx.link("/found", { m: e.key }))}">What it found in the assistant</a>`;
    const refNote = e.refRole === "tuned" || e.modelRole === "baseline" ? "<strong>Pinned reference: the tuned baseline.</strong> Shown on every view and ranked with everyone else. " : e.refRole === "base" || e.modelRole === "reference" ? "<strong>Pinned reference: the base model the tuned one starts from.</strong> Shown on every view and ranked with everyone else. " : "";
    const sub = `${refNote}${e.model.moe ? "Mixture-of-experts" : "Dense"} model. Showing ${esc(U.entryLine(e))} on ${esc(d.name)}.${b.sample ? " Sample data." : ""}`;
    const head = factsLocal(e) + variantBar(ctx, e, list);

    if (why) {
      const runsOn = boards.filter((x) => x !== b).map((x) => ({ x, best: pickEntry(x.entries.filter((y) => y.model.id === e.model.id && y.group === "ranked" && !withheld(y))) })).filter((o) => o.best);
      const fitsHere = list.filter((x) => !withheld(x) && x.key !== e.key);
      let title, body;
      if (why === "does_not_fit") {
        title = `Does not fit ${d.fitLabel}`;
        body = `It needs <span class="num">${fmt(U.GiB(fit.peak_bytes_at_2048), 1)} GB</span> at 2k context and <span class="num">${fmt(U.GiB(fit.peak_bytes_at_10240), 1)} GB</span> at 10k; the fit line is <span class="num">${fmt(U.GiB(fit.budget_bytes || boardBudget(b)), 1)} GB</span>. Memory is measured on the real device, so the shortfall is exact. Entries that do not fit are never scored or ranked.`;
      } else if (why === "invalid") {
        title = "Invalid run, listed and not ranked";
        body = `<span class="num">${fmt(e.run.excluded_rate, 1)}%</span> of trials were excluded for engine, transport or provider faults, over the 2% limit. Exclusions are never counted as model failures, but with this many the scores are not reliable, so none are shown.`;
      } else if (why === "not_measured") {
        title = "Fit not measured, listed and not ranked";
        body = `Its memory has not been measured on ${esc(d.name)}, so the fit gate has no verdict. It stays listed and unscored until it has one.`;
      } else if (why === "failed") {
        title = "The run failed";
        body = "The harness or runtime crashed or hung. The run is broken, not the model, so nothing is scored.";
      } else {
        title = statusText(why);
        body = "This entry has no results on this device yet. It is never treated as zero.";
      }
      const where = runsOn.length ? runsOn.map((o) => `<a class="key" href="${esc(ctx.link("/model/" + U.seg(e.model.id), { d: o.x.device.id, v: o.best.key }))}">See it on ${esc(o.x.device.name)}</a>`).join("") : "";
      const alt = fitsHere.slice(0, 2).map((x) => `<a class="key" href="${esc(ctx.link("/model/" + U.seg(x.model.id), { v: x.key }))}">See ${esc(U.entryLine(x))}</a>`).join("");
      const none = !where && !alt ? `<p class="small muted" style="margin-top:8px">It is not ranked on any other device in this board.</p>` : "";
      const empty = `<div class="empty" role="status" style="margin-top:var(--space-md)"><h3>${U.status(why, { text: title })}</h3><p>${body}</p>${where || alt ? `<div class="verdict-actions">${alt}${where}</div>` : ""}${none}</div>`;
      return { title: e.name, html: shell(ctx, e, sub, actions, head + empty + contextSection(ctx, e) + everyDevice(ctx, e, boards)) };
    }

    const field = ranked(b);
    const rr = ST.storeRank(e, HEAD) || ST.rankRanges(field, HEAD).get(e.key) || null;
    const budget = U.GiB(fit.budget_bytes || boardBudget(b));
    const memC = ST.value(e, "mem_10k");
    const figs = `<dl class="figures wide" style="margin-top:var(--space-md)">
      ${fig(e, "Agentic score", HEAD, "of 100")}
      ${fig(e, "In the assistant's loop", "assistant_loop_score", "of 100")}
      ${fig(e, "Repairs add", "repair_gain", "points")}
      <div><dt>Rank on this device</dt><dd>${rr ? (rr[0] === rr[1] ? rr[0] : rr[0] + "–" + rr[1]) : `<span class="small">${U.status("no_value", { text: "Not ranked" })}</span>`}<small>of ${field.length}</small></dd></div>
      ${fig(e, "Unsafe actions", "unsafe", "per 100", true)}
      ${fig(e, "Privacy leaks", "leaks", "per 100", true)}
      ${fig(e, "Assistant score", "assistant_score", "of 100")}
      ${fig(e, "Quality pass rate", "quality_pass", "%")}
      ${fig(e, "Decode at 2k", "decode_2k", "tok/s")}
      ${ok(memC) ? `<div><dt>Peak memory at 10k</dt><dd>${fmt(memC.value, 1)}<small>${budget ? "GB of " + fmt(budget, 1) : "GB"}</small></dd></div>` : fig(e, "Peak memory at 10k", "mem_10k", "GB")}
    </dl>
    <p class="small muted" style="margin-top:8px">Ranks are ranges: entries share a rank when their intervals overlap. Unsafe actions and privacy leaks sit beside the score and are never blended into it.${(fit.verdict === "fits_small_context") ? " This entry fits only at small context (2k), so check the context charts before relying on long prompts." : ""}</p>`;
    const isRef = !!e.refRole;
    let fnd = null;
    try { fnd = await ST.loadFindings(ctx.boardInfo.version, d.id); } catch (err) { fnd = null; }
    const html = shell(ctx, e, sub, actions, head + figs + profileSection(ctx, e) + foundSection(ctx, e, fnd) + refsSection(ctx, e) + (isRef ? tuningSection(ctx, e) : "") + tierCharts(ctx, e) + safetySection(ctx, e) + contextSection(ctx, e) + energySection(ctx, e) + everyDevice(ctx, e, boards) +
      `<p class="small" style="margin-top:var(--space-lg)"><a class="link" href="${esc(ctx.link("/compare", { m: cmp.keys.join(",") }))}">Compare with ${cmp.tied ? "the entries it is tied with" : "the next entries"}</a> · <a class="link" href="${esc(ctx.link("/tests/agentic", { m: e.key }))}">Open its cases</a> · <a class="link" href="${esc(ctx.link("/found", { m: e.key }))}">What it found inside the assistant</a> · <a class="link" href="${esc(ctx.link("/methodology"))}">How scores are built</a></p>`);
    return { title: e.name, html };
  }

  /* ---------- cloud reference ---------- */
  function gapCell(a, b, id) {
    const m = R.metric[id], ca = ST.value(a, id), cb = ST.value(b, id);
    if (!ok(ca)) return U.statusFor(ca);
    if (!ok(cb)) return U.status("no_value", { text: "No local value" });
    const dlt = ca.value - cb.value, dp = m.dp > 1 ? m.dp : 1;
    const margin = ca.half != null && cb.half != null ? Math.sqrt(ca.half ** 2 + cb.half ** 2) : null;
    const tag = margin == null || !m.better ? null : Math.abs(dlt) <= margin ? "no_clear_difference" : (m.better === "high") === dlt > 0 ? "better" : "worse";
    const word = tag ? R.tags[tag] : !m.better ? "neither higher nor lower is better" : "measured, no interval";
    const cls = tag === "better" ? "up" : tag === "worse" ? "down" : "flat";
    const tip = `${m.name}, cloud reference minus ${nameOf(b)}: ${U.signed(dlt, dp)}${margin != null ? ", margin ±" + fmt(margin, dp) : ""}; ${word}${margin != null ? ", unpaired estimate from the two intervals" : ""}.`;
    return `<span class="delta ${cls}" title="${esc(tip)}">${tag === "better" ? I.check : tag === "worse" ? I.warn : ""}<span class="num">${margin != null ? "≈" : ""}${U.signed(dlt, dp)}</span>${margin != null ? `<span class="muted xsmall"> ±${fmt(margin, dp)}</span>` : ""}<span class="visually-hidden">, ${esc(word)}</span></span>`;
  }
  const CLOUD_KEY = ["agentic_score", "lane_assistant", "lane_neutral", "pass_all", "progress", "format_ok", "steps", "unsafe", "leaks", "restraint", "over_refusal", "instruction_following", "assistant_score", "quality_pass", "quality_score", "readability", "text_understanding", "writing", "summary", "extraction", "fabricated", "visible_tokens"];

  function renderCloud(ctx, e, list) {
    const dev = ctx.device, m = e.model;
    const rd = m.run_date || {}, first = U.date(rd.first || e.run.date), last = U.date(rd.last || rd.first || e.run.date);
    const dateTxt = first && last && first !== last ? `${first} to ${last}` : first || last || "";
    const best = dev ? byScore(ranked(dev)).find((x) => val(x, HEAD) != null) || null : null;
    const keys = [e.key].concat(best ? [best.key] : []).concat(dev && dev.references.tuned && (!best || best.key !== dev.references.tuned) ? [dev.references.tuned] : []).slice(0, 4);
    const actions = `<a class="key" href="${esc(ctx.link("/compare", { m: keys.join(",") }))}">Compare with local entries</a><a class="key" href="${esc(ctx.link("/tests/agentic", { m: e.key }))}">Open its cases</a>`;
    const sub = `A hosted model, shown for scale and never ranked. Hosted models change without notice, so every number here belongs to the run${dateTxt ? " of " + esc(dateTxt) : ""}.${e.board.sample ? " Sample data." : ""}`;
    const facts = U.facts([
      ["Kind", "Cloud reference, never ranked"],
      ["Run date", dateTxt ? `<span class="num">${esc(dateTxt)}</span>` : unpublished()],
      ["Model the provider served", m.resolved_model ? mono(m.resolved_model) : unpublished()],
      ["Upstream provider", m.upstream_provider ? esc(m.upstream_provider) : unpublished()],
      ["Reached through", esc(CFG.runtimes[e.variant.runtime] || e.variant.runtime || "") || unpublished()],
      ["Thinking", esc(e.variant.thinking === "on" ? "On" : e.variant.thinking === "off" ? "Off" : e.variant.thinking ? tidy(e.variant.thinking) : "No thinking mode")],
      ["Sampling", e.variant.sampling_source ? esc(SAMPLING[e.variant.sampling_source] || tidy(e.variant.sampling_source)) : unpublished()],
      ["Device, memory, energy, fit", U.status("not_applicable_cloud")],
      ["Harness commit", mono(e.run.harness_commit)],
      ["Runs", runIds(e)]
    ]);
    const optLabel = (x) => (x.variant.thinking === "on" ? "Thinking on" : x.variant.thinking === "off" ? "Thinking off" : tidy(x.variant.thinking || "Default"));
    const bar = list.length > 1 ? `<div class="toolbar" style="margin:var(--space-lg) 0 var(--space-sm)">${U.tray("v", "Thinking mode", list.map((x) => ({ v: x.key, l: optLabel(x) })), e.key)}</div>` : "";
    const figs = `<dl class="figures wide" style="margin-top:var(--space-md)">${fig(e, "Agentic score", HEAD, "of 100")}${fig(e, "Unsafe actions", "unsafe", "per 100", true)}${fig(e, "Privacy leaks", "leaks", "per 100", true)}${fig(e, "Assistant score", "assistant_score", "of 100")}${fig(e, "Quality pass rate", "quality_pass", "%")}</dl>`;

    /* against both references and the best local entry */
    const ids = CLOUD_KEY.filter((id) => R.metric[id] && (ok(ST.value(e, id)) || (best && ok(ST.value(best, id)))));
    const refs = dev ? ["tuned", "base"].map((role) => ({ role, r: dev.refEntry(role) })) : [];
    const thead = `<thead><tr><th scope="col" class="sticky">Measure</th><th scope="col" class="num">This cloud reference</th>${refs.map((x) => `<th scope="col" class="num">Difference from ${x.r ? esc(x.r.name) : "the " + x.role + " reference"}<span class="vline">${x.role} reference</span></th>`).join("")}<th scope="col" class="num">Gap to the best local entry<span class="vline">${best ? esc(nameOf(best)) : "none ranked"}</span></th></tr></thead>`;
    const tbody = ids.map((id) => { const mm = R.metric[id]; return `<tr><th scope="row" class="sticky">${esc(mm.name)}${unitTxt(mm)}</th><td class="num">${U.cell(e, id)}</td>${refs.map((x) => `<td class="num">${U.deltaCell(e, id, x.role, dev)}</td>`).join("")}<td class="num">${best ? gapCell(e, best, id) : U.status("no_value", { text: "No ranked local entry" })}</td></tr>`; }).join("");
    const table = U.secHead("Against the local entries", `The same cases as the local entries${dateTxt ? ", run on " + esc(dateTxt) : ""}. The differences read this reference against the two pinned references on ${esc(dev ? dev.device.name : "the chosen device")}; the last column is the gap to the best ranked local entry there: how far on-device is from hosted. A mark appears only when the gap is larger than the margin; ≈ marks an unpaired estimate.`, "", "refs") +
      wrap("Against the local entries", `<table class="data"><caption class="visually-hidden">${esc(nameOf(e))} against the references and the best local entry</caption>${thead}<tbody>${tbody}</tbody></table>`);

    /* cost and latency: hosted only, never on an axis with on-device numbers */
    const costIds = R.metrics.filter((x) => x.group === "cloudcost");
    const anyCost = costIds.some((x) => ok(ST.value(e, x.id)));
    const cost = `<div class="cut panel" style="margin-top:var(--space-lg)" id="cost"><h3 style="margin-bottom:8px">Cost and latency, not comparable with on-device numbers</h3>
      ${anyCost ? U.facts(costIds.map((x) => [x.name, U.cell(e, x.id) + (ok(ST.value(e, x.id)) && x.unit !== "US$" ? unitTxt(x) : "")])) : U.empty("Not in this board yet", "This board publishes no cost, token or network-time measurements for the cloud references yet.")}
      <p class="small muted" style="margin-top:8px">These include the network, the provider's queue and its prices${dateTxt ? " on " + esc(dateTxt) : ""}. They are never drawn on the same axis as on-device time, memory or energy.</p></div>`;
    const html = shell(ctx, e, sub, actions, facts + bar + figs + table + tierCharts(ctx, e) + safetySection(ctx, e) + cost);
    return { title: e.name, html };
  }

  /* ---------- the model is not on this device ---------- */
  async function renderElsewhere(ctx, id) {
    let boards = [];
    try { boards = await ST.loadAllDevices(ctx.boardInfo.version); } catch (err) { boards = []; }
    const hits = boards.map((b) => ({ b, list: b.entries.filter((x) => x.model.id === id) })).filter((o) => o.list.length);
    if (!hits.length) notFound();
    const d = ctx.device.device;
    const links = hits.map((o) => { const best = pickEntry(o.list); return `<a class="key" href="${esc(ctx.link("/model/" + U.seg(id), { d: o.b.device.id, v: best.key }))}">See it on ${esc(o.b.device.name)}</a>`; }).join("");
    return {
      title: id, html: `<div class="container"><p class="crumbs"><a href="${esc(ctx.link("/", {}, true))}">Leaderboard</a> / ${esc(id)}</p>
      <div class="page-head"><div><h1>${esc(id)}</h1><p>Not run on ${esc(d.name)} in this board.</p></div></div>
      <div class="empty" role="status"><h3>${U.status("not_run", { text: "Not run on " + d.name })}</h3><p>This board has no entry for this model on ${esc(d.name)}. It is never treated as zero. It does have results on the devices below.</p><div class="verdict-actions">${links}</div></div></div>`
    };
  }

  async function render(ctx) {
    const id = ctx.parts[1];
    if (!id) notFound();
    const key = ctx.q.get("v");
    const local = ctx.device ? ctx.device.entries.filter((x) => x.model.id === id) : [];
    if (local.length) {
      const e = pickEntry(local, key);
      return e.cloud ? renderCloud(ctx, e, local) : renderLocal(ctx, e, local);
    }
    const cl = ctx.cloud ? ctx.cloud.entries.filter((x) => x.model.id === id) : [];
    if (cl.length) {
      const e = cl.find((x) => x.key === key) || cl.find((x) => x.variant.thinking === "off") || cl[0];
      return renderCloud(ctx, e, cl);
    }
    if (ctx.device) return renderElsewhere(ctx, id);
    return notFound();
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.model = { render };
})();
