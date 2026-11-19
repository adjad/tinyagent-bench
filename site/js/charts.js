/* Small SVG chart kit. Colours come from CSS classes bound to tokens, so light and dark both work.
 * Identity never rests on colour alone: shapes, direct labels, hatching and words carry it.
 * Every mark carries data-tip for the shared hover layer (keyboard focus opens it too); every chart is paired with
 * a "Show the numbers as a table" view and a "How to read this chart" expander by the caller. */
(function () {
  "use strict";
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (v, d) => (v == null ? "" : Number(v).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const niceMax = (v) => { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p; };
  const ticks = (max, n) => { const step = niceMax(max / n); const out = []; for (let t = 0; t <= max + 1e-9; t += step) out.push(+t.toFixed(6)); return out; };
  let hatchSeq = 0;
  /* each chart gets its own pattern id, so several charts on one page never share one */
  const hatch = () => { const id = "hatch-" + (++hatchSeq); return { id, defs: `<defs><pattern id="${id}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="hatch-line"/></pattern></defs>`, fill: `fill="url(#${id})"` }; };
  /* store statuses to the chart's two failure kinds */
  const failKind = (st) => (st === "out_of_memory" || st === "oom" ? "oom" : st === "beyond_window" || st === "window" ? "window" : st && st !== "ok" ? "other" : null);

  /* push end labels apart so they never overlap */
  function dodge(items, gap) { items.sort((a, b) => a.y - b.y); for (let i = 1; i < items.length; i++) if (items[i].y - items[i - 1].y < gap) items[i].y = items[i - 1].y + gap; return items; }

  /* marker shapes, so identity never rests on colour alone */
  function marker(shape, x, y, r, cls, tip, href) {
    let d;
    if (shape === "diamond") d = `<path class="${cls}" d="M${x} ${y - r * 1.25}L${x + r * 1.25} ${y}L${x} ${y + r * 1.25}L${x - r * 1.25} ${y}Z"/>`;
    else if (shape === "square") d = `<rect class="${cls}" x="${x - r}" y="${y - r}" width="${r * 2}" height="${r * 2}" rx="1.5"/>`;
    else if (shape === "triangle") d = `<path class="${cls}" d="M${x} ${y - r * 1.3}L${x + r * 1.2} ${y + r * 0.9}L${x - r * 1.2} ${y + r * 0.9}Z"/>`;
    else d = `<circle class="${cls}" cx="${x}" cy="${y}" r="${r}"/>`;
    const hit = `<circle class="hit" cx="${x}" cy="${y}" r="${Math.max(r + 8, 12)}" data-tip="${esc(tip || "")}"${href ? ' tabindex="0" role="link" aria-label="' + esc((tip || "").split("\n")[0]) + '"' : ""}/>`;
    return href ? `<a href="${href}">${d}${hit}</a>` : d + hit;
  }
  const shapeSwatch = (shape, cls) => `<svg viewBox="0 0 18 12" aria-hidden="true" class="chart">${marker(shape, 9, 6, 4, cls + " mk", "")}</svg>`;

  /* Quality against cost scatter with CI whiskers and a Pareto frontier */
  function scatter(o) {
    const W = o.W || 980, H = o.H || 400, m = { l: 48, r: 24, t: 22, b: 44 };
    const pts = o.points.filter((p) => p.x != null && p.y != null);
    const xMax = niceMax(Math.max(...pts.map((p) => p.x)) * 1.08);
    const refs = o.refLines || [];
    const yMin = Math.max(0, Math.floor((Math.min(...pts.map((p) => p.y - (p.ci || 0))) - 5) / 10) * 10), yMax = Math.min(100, Math.ceil((Math.max(...pts.map((p) => p.y + (p.ci || 0)), ...refs.map((r) => r.y)) + 3) / 10) * 10);
    const X = (v) => m.l + (v / xMax) * (W - m.l - m.r), Y = (v) => H - m.b - ((v - yMin) / (yMax - yMin)) * (H - m.t - m.b);
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">`;
    for (let t = yMin; t <= yMax; t += 10) s += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 8}" y="${Y(t) + 4}" text-anchor="end">${t}</text>`;
    ticks(xMax, 5).forEach((t) => { s += `<line class="axis" x1="${X(t)}" x2="${X(t)}" y1="${H - m.b}" y2="${H - m.b + 4}"/><text x="${X(t)}" y="${H - m.b + 18}" text-anchor="middle">${fmt(t, t < 10 && t % 1 ? 1 : 0)}</text>`; });
    s += `<line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>`;
    s += `<text class="lbl" x="${W - m.r}" y="${H - 6}" text-anchor="end">${esc(o.xLabel)}, lower is cheaper</text>`;
    s += `<text class="lbl" x="${m.l - 40}" y="${m.t - 10}" text-anchor="start">${esc(o.yLabel)}</text>`;
    // frontier: best y for any x at or below
    const sorted = pts.slice().sort((a, b) => a.x - b.x || b.y - a.y); const front = []; let best = -1;
    sorted.forEach((p) => { if (p.y > best) { front.push(p); best = p.y; p.frontier = true; } });
    if (front.length > 1) { let d = ""; front.forEach((p, i) => { d += (i ? `H${X(p.x)}V${Y(p.y)}` : `M${X(p.x)} ${Y(p.y)}`); }); s += `<path class="frontier" d="${d}"/>`; }
    // score-only reference lines (cloud references): no position on the cost axis, labels at the right edge
    if (refs.length) {
      refs.forEach((r) => { s += `<line class="cloudline" x1="${m.l}" x2="${W - m.r}" y1="${Y(r.y)}" y2="${Y(r.y)}"><title>${esc(r.label)}: ${fmt(r.y, 1)}</title></line>`; });
      dodge(refs.map((r) => ({ y: Y(r.y) - 3, label: r.label + " " + fmt(r.y, 1) })), 12).forEach((it) => { s += `<text class="lbl cloud-lbl" x="${W - m.r - 2}" y="${it.y}" text-anchor="end">${esc(it.label)}</text>`; });
    }
    // quantisation variants of one model, joined in order of cost
    const groups = {}; pts.forEach((p) => { if (p.group) (groups[p.group] = groups[p.group] || []).push(p); });
    Object.values(groups).forEach((g) => { if (g.length < 2) return; g.sort((a, b) => a.x - b.x); s += `<path class="variant-join" d="${g.map((p, i) => (i ? "L" : "M") + X(p.x) + " " + Y(p.y)).join("")}"/>`; });
    pts.forEach((p) => { const g = p.hl ? "hl" : ""; s += `<g class="${g}"><line class="ci" x1="${X(p.x)}" x2="${X(p.x)}" y1="${Y(p.y - p.ci)}" y2="${Y(p.y + p.ci)}"/></g>`; });
    pts.slice().sort((a, b) => (a.hl ? 1 : 0) - (b.hl ? 1 : 0)).forEach((p) => { s += `<g class="${p.hl ? "hl" : ""}">${marker(p.shape, X(p.x), Y(p.y), p.hl ? 6 : 5, "dot", p.tip, p.href)}</g>`; });
    // direct labels: the leader, the references and the trade-off edge; a label that cannot be placed clear is left to the tooltip and table
    const boxes = [];
    const cand = pts.filter((p) => p.hl || p.pin || (p.frontier && !p.minor)).sort((a, b) => (b.hl ? 2 : b.pin ? 1 : 0) - (a.hl ? 2 : a.pin ? 1 : 0));
    cand.forEach((p) => {
      const w = p.label.length * 6.6 + 4, px = X(p.x), py = Y(p.y);
      const x0 = px + 10 + w < W - 8 ? px + 10 : px - 10 - w;
      let y = null;
      for (const dy of [-8, 16, -22, 30, -36, 44]) {
        const yy = py + dy; if (yy < m.t + 4 || yy > H - m.b - 4) continue;
        if (!boxes.some((q) => x0 < q.x1 && x0 + w > q.x0 && Math.abs(q.y - yy) < 13)) { y = yy; break; }
      }
      if (y == null) return;
      boxes.push({ x0, x1: x0 + w, y });
      s += `<text class="${p.hl ? "lbl-strong" : "lbl"}" x="${x0}" y="${y}">${esc(p.label)}</text>`;
    });
    return s + "</svg>";
  }

  /* Horizontal dot-and-whisker rows (tiers, languages, compare) */
  function dotRows(o) {
    const rowH = o.rowH || 30, W = 640, lw = o.labelW || 170, m = { r: 56, t: 22, b: 10 };
    const H = m.t + o.rows.length * rowH + m.b, lo = o.domain[0], hi = o.domain[1];
    const X = (v) => lw + ((v - lo) / (hi - lo)) * (W - lw - m.r);
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">`;
    for (let t = lo; t <= hi; t += o.step || 20) s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${m.t - 6}" y2="${H - m.b}"/><text x="${X(t)}" y="${m.t - 10}" text-anchor="middle">${t}</text>`;
    o.rows.forEach((r, i) => {
      const y = m.t + i * rowH + rowH / 2;
      const label = r.href ? `<a href="${r.href}"><text class="lbl" x="${lw - 12}" y="${y + 4}" text-anchor="end">${esc(r.label)}</text></a>` : `<text class="lbl" x="${lw - 12}" y="${y + 4}" text-anchor="end">${esc(r.label)}</text>`;
      s += label;
      if (r.v == null) { s += `<text x="${lw}" y="${y + 4}">${esc(r.missing || "Not measured")}</text>`; return; }
      (r.series || [{ v: r.v, ci: r.ci, cls: "hl", shape: "circle", tip: r.tip }]).forEach((p, j, arr) => {
        const dy = arr.length > 1 ? (j - (arr.length - 1) / 2) * 7 : 0;
        if (p.v == null) return;
        const c = p.cls || "hl";
        s += `<line class="ci ${c === "hl" ? "hl" : ""} ${c !== "hl" ? c.replace("f", "s") : ""}" x1="${X(Math.max(lo, p.v - (p.ci || 0)))}" x2="${X(Math.min(hi, p.v + (p.ci || 0)))}" y1="${y + dy}" y2="${y + dy}"/>`;
        s += marker(p.shape || "circle", X(p.v), y + dy, 4.5, c === "hl" ? "dot hl" : c + " mk", p.tip || `${r.label}: ${fmt(p.v, 0)} ± ${fmt(p.ci, 1)}`);
      });
      if (r.ref != null) s += `<line class="ref" x1="${X(r.ref)}" x2="${X(r.ref)}" y1="${y - 9}" y2="${y + 9}"><title>Median of models that fit</title></line>`;
      if (!r.series) s += `<text class="lbl" x="${W - m.r + 8}" y="${y + 4}">${fmt(r.v, o.d || 0)}</text>`;
    });
    return s + "</svg>";
  }

  /* Context-scaling chart: one metric over the seven context levels.
   * series: [{name, cls ('A' or '1'..'4'), shape, points:[{c,v}|{c,status}]}]; budget optional (memory) */
  function contextChart(o) {
    const W = o.labelRight ? 520 : 360, H = o.labelRight ? 260 : 220, m = { l: 44, r: o.labelRight ? 92 : 14, t: 14, b: 34 };
    const levels = o.levels, labels = o.labels;
    const allV = []; o.series.forEach((s) => s.points.forEach((p) => p.v != null && allV.push(p.v)));
    if (o.budget) allV.push(o.budget);
    const yMax = niceMax(Math.max(...allV, 0.001) * 1.12);
    const step = (W - m.l - m.r) / (levels.length - 1);
    const X = (i) => m.l + i * step, Y = (v) => H - m.b - (v / yMax) * (H - m.t - m.b);
    const hx = hatch();
    o.series.forEach((sr) => sr.points.forEach((p) => { p.kind = failKind(p.status); }));
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">${hx.defs}`;
    ticks(yMax, 4).forEach((t) => { s += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 6}" y="${Y(t) + 4}" text-anchor="end">${fmt(t, t < 1 && t > 0 ? 2 : t < 10 && t % 1 ? 1 : 0)}</text>`; });
    // beyond-window band: shared when every series is beyond window at that level
    const firstWin = levels.findIndex((c, i) => o.series.every((sr) => sr.points[i] && sr.points[i].kind === "window"));
    if (firstWin > -1) { const x0 = X(firstWin) - step / 2; s += `<rect ${hx.fill} x="${x0}" y="${m.t}" width="${W - m.r - x0}" height="${H - m.t - m.b}"/><text x="${(x0 + W - m.r) / 2}" y="${m.t + 14}" text-anchor="middle" class="lbl">Beyond window</text>`; }
    labels.forEach((l, i) => { s += `<text x="${X(i)}" y="${H - m.b + 16}" text-anchor="middle">${l}</text>`; });
    s += `<line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>`;
    s += `<text x="${W - m.r}" y="${H - 4}" text-anchor="end">Context, tokens</text>`;
    if (o.budget) s += `<line class="budget" x1="${m.l}" x2="${W - m.r}" y1="${Y(o.budget)}" y2="${Y(o.budget)}"/><text class="budget-lbl" x="${m.l + 4}" y="${Y(o.budget) - 5}">${o.budgetLabel || "Fit line"} ${fmt(o.budget, 1)} GB</text>`;
    const ends = [];
    o.series.forEach((sr) => {
      const cls = sr.cls || "A"; let d = ""; let last = null, lastI = -1;
      sr.points.forEach((p, i) => { if (p.v == null) return; d += (d ? "L" : "M") + X(i) + " " + Y(p.v); last = p; lastI = i; });
      if (d) s += `<path class="line s${cls}" d="${d}"/>`;
      sr.points.forEach((p, i) => {
        if (p.v != null) s += marker(sr.shape || "circle", X(i), Y(p.v), 3.6, `f${cls} mk`, `${sr.name}\n${labels[i]} context: ${fmt(p.v, o.d)} ${o.unit}`);
        else if (p.kind === "oom" && (i === 0 || sr.points[i - 1].kind !== "oom")) {
          const y = m.t + 10 + (o.series.indexOf(sr) * 14); const x = X(i);
          s += `<path class="oom" d="M${x - 4} ${y - 4}L${x + 4} ${y + 4}M${x + 4} ${y - 4}L${x - 4} ${y + 4}"/><text class="oom-lbl" x="${x > W * 0.55 ? x - 8 : x + 8}" y="${y + 4}" text-anchor="${x > W * 0.55 ? "end" : "start"}">${o.series.length > 1 ? esc(sr.short || sr.name) + " out of memory" : "Out of memory"}</text><circle class="hit" cx="${x}" cy="${y}" r="12" tabindex="0" data-tip="${esc(sr.name + "\n" + labels[i] + " context: out of memory" + (p.need ? ", needs " + fmt(p.need, 1) + " GB" : ""))}"/>`;
        } else if (p.kind === "window" && firstWin === -1 && (i === 0 || sr.points[i - 1].kind !== "window")) {
          const x = X(i), y = H - m.b - 8;
          s += `<rect ${hx.fill} x="${x - step / 2}" y="${m.t}" width="${step}" height="${H - m.t - m.b}" opacity=".7"/><text class="lbl" x="${x}" y="${y}" text-anchor="middle">${o.series.length > 1 ? esc(sr.short || sr.name) + ": beyond window" : "Beyond window"}</text>`;
        } else if (p.kind === "other" && (i === 0 || sr.points[i - 1].kind !== "other")) {
          const x = X(i), y = m.t + 10 + (o.series.indexOf(sr) * 14);
          s += `<circle class="hit" cx="${x}" cy="${y}" r="10" tabindex="0" data-tip="${esc(sr.name + "\n" + labels[i] + " context: " + (p.label || String(p.status).replace(/_/g, " ")))}"/><text class="lbl" x="${x}" y="${y + 4}" text-anchor="middle">${esc(p.label || String(p.status).replace(/_/g, " "))}</text>`;
        }
      });
      if (o.labelRight && last) ends.push({ x: X(lastI) + 8, y: Y(last.v) + 4, t: sr.short || sr.name });
    });
    dodge(ends, 13).forEach((e) => { s += `<text class="lbl" x="${e.x}" y="${e.y}">${esc(e.t)}</text>`; });
    return s + "</svg>";
  }

  /* One stacked bar: where a task's joules go */
  function stackBar(o) {
    const W = 640, H = 64, total = o.segments.reduce((t, x) => t + x.v, 0);
    let x = 0, s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">`;
    o.segments.forEach((seg, i) => {
      const w = (seg.v / total) * W; const gap = i < o.segments.length - 1 ? 2 : 0;
      s += `<rect class="${seg.cls}" x="${x}" y="4" width="${Math.max(w - gap, 1)}" height="22" rx="3" data-tip="${esc(seg.label + ": " + fmt(seg.v, 0) + " J, " + Math.round(seg.v / total * 100) + "%")}"/>`;
      if (w > 90) s += `<text class="lbl" x="${x}" y="46">${esc(seg.label)}</text><text x="${x}" y="60">${fmt(seg.v, 0)} J, ${Math.round(seg.v / total * 100)}%</text>`;
      x += w;
    });
    return s + "</svg>";
  }

  /* Horizontal memory bars against a device budget */
  function budgetBars(o) {
    const rowH = 28, lw = 150, W = 640, m = { r: 70, t: 24, b: 24 };
    const H = m.t + o.rows.length * rowH + m.b;
    const xMax = niceMax(Math.max(o.budget * 1.15, ...o.rows.map((r) => r.v)));
    const X = (v) => lw + (v / xMax) * (W - lw - m.r);
    const hx = hatch();
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">${hx.defs}`;
    ticks(xMax, 5).forEach((t) => { s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${m.t - 4}" y2="${H - m.b}"/><text x="${X(t)}" y="${m.t - 10}" text-anchor="middle">${t} GB</text>`; });
    o.rows.forEach((r, i) => {
      const y = m.t + i * rowH + 6, over = r.v > o.budget;
      s += `<a href="${r.href}"><text class="lbl" x="${lw - 10}" y="${y + 12}" text-anchor="end">${esc(r.label)}</text></a>`;
      s += `<rect class="${over ? "bar-over" : r.hl ? "bar" : "bar-mute"}" ${over ? hx.fill : ""} x="${lw}" y="${y}" width="${Math.max(X(r.v) - lw, 2)}" height="16" rx="3" tabindex="0" data-tip="${esc(r.label + "\n" + fmt(r.v, 1) + " GB " + (o.atLabel || "at 2k context") + (over ? "\nDoes not fit" : ""))}"/>`;
      s += `<text class="lbl" x="${X(r.v) + 6}" y="${y + 12}">${fmt(r.v, 1)}${over ? ", too big" : ""}</text>`;
    });
    s += `<line class="budget" x1="${X(o.budget)}" x2="${X(o.budget)}" y1="${m.t - 4}" y2="${H - m.b}"/><text class="budget-lbl" x="${X(o.budget) + 4}" y="${H - 6}">${o.budgetLabel || "Fit line"} ${fmt(o.budget, 1)} GB</text>`;
    return s + "</svg>";
  }

  /* Decode speed over minutes of sustained load */
  function sustain(o) {
    const W = 640, H = 230, m = { l: 44, r: 110, t: 14, b: 34 };
    const yMax = niceMax(Math.max(...o.series.flatMap((s) => s.points.map((p) => p.v))) * 1.1);
    const X = (t) => m.l + (t / 30) * (W - m.l - m.r), Y = (v) => H - m.b - (v / yMax) * (H - m.t - m.b);
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">`;
    ticks(yMax, 4).forEach((t) => { s += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 6}" y="${Y(t) + 4}" text-anchor="end">${t}</text>`; });
    [0, 10, 20, 30].forEach((t) => { s += `<text x="${X(t)}" y="${H - m.b + 16}" text-anchor="middle">${t} min</text>`; });
    s += `<line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/><text x="${m.l}" y="${m.t - 2}">Decode tok/s</text>`;
    const ends = [];
    o.series.forEach((sr) => {
      s += `<path class="line s${sr.cls}" d="${sr.points.map((p, i) => (i ? "L" : "M") + X(p.t) + " " + Y(p.v)).join("")}"/>`;
      sr.points.forEach((p, i) => { if (i % 2 === 0) s += marker(sr.shape, X(p.t), Y(p.v), 3.4, `f${sr.cls} mk`, `${sr.name}\n${p.t} min: ${fmt(p.v, 1)} tok/s`); });
      const lp = sr.points[sr.points.length - 1]; ends.push({ x: X(lp.t) + 8, y: Y(lp.v) + 4, t: sr.short });
    });
    dodge(ends, 13).forEach((e) => { s += `<text class="lbl" x="${e.x}" y="${e.y}">${esc(e.t)}</text>`; });
    return s + "</svg>";
  }

  /* Labelled horizontal bars on a fixed domain (battery share over a simulated day, shares of tasks in time).
   * rows: [{label, v, ci?:[lo,hi], href?, hl?, est?: true (hatched, "estimate"), status?: text shown in place of a bar}]
   * o: {domain:[lo,hi], unit, dp, threshold?: {v, label}, aria, tickStep} */
  function hbars(o) {
    const rowH = 30, lw = o.labelW || 170, W = 640, m = { r: 90, t: 24, b: 12 };
    const H = m.t + o.rows.length * rowH + m.b, lo = o.domain[0], hi = o.domain[1];
    const X = (v) => lw + ((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * (W - lw - m.r);
    const hx = hatch();
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">${hx.defs}`;
    const step = o.tickStep || niceMax((hi - lo) / 4);
    for (let t = lo; t <= hi + 1e-9; t += step) s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${m.t - 4}" y2="${H - m.b}"/><text x="${X(t)}" y="${m.t - 10}" text-anchor="middle">${fmt(t, 0)}${esc(o.unit === "%" ? "%" : "")}</text>`;
    o.rows.forEach((r, i) => {
      const y = m.t + i * rowH + 7;
      const lab = `<text class="${r.hl ? "lbl-strong" : "lbl"}" x="${lw - 10}" y="${y + 12}" text-anchor="end">${esc(r.label)}</text>`;
      s += r.href ? `<a href="${r.href}">${lab}</a>` : lab;
      if (r.v == null) { s += `<text x="${lw}" y="${y + 12}">${esc(r.status || "Not measured")}</text>`; return; }
      const tip = `${r.label}\n${fmt(r.v, o.dp || 0)}${o.unit === "%" ? "%" : " " + (o.unit || "")}${r.ci ? ", 95% interval " + fmt(r.ci[0], o.dp || 0) + " to " + fmt(r.ci[1], o.dp || 0) : ""}${r.est ? "\nEstimate" : ""}`;
      s += `<rect class="${r.hl ? "bar" : "bar-mute"}"${r.est ? ` ${hx.fill} stroke="currentColor" stroke-opacity=".35"` : ""} x="${lw}" y="${y}" width="${Math.max(X(r.v) - lw, 2)}" height="16" rx="3" tabindex="0" data-tip="${esc(tip)}"/>`;
      if (r.ci) s += `<line class="ci${r.hl ? " hl" : ""}" x1="${X(r.ci[0])}" x2="${X(r.ci[1])}" y1="${y + 8}" y2="${y + 8}"/>`;
      s += `<text class="lbl" x="${Math.max(X(r.v), r.ci ? X(r.ci[1]) : 0) + 6}" y="${y + 12}">${fmt(r.v, o.dp || 0)}${o.unit === "%" ? "%" : ""}${r.est ? " est." : ""}</text>`;
    });
    if (o.threshold) s += `<line class="budget" x1="${X(o.threshold.v)}" x2="${X(o.threshold.v)}" y1="${m.t - 4}" y2="${H - m.b}"/><text class="budget-lbl" x="${X(o.threshold.v) + 4}" y="${H - 2}">${esc(o.threshold.label)}</text>`;
    return s + "</svg>";
  }

  /* Two values per row on one 0 to 100 axis, joined: a hollow ring for the first, a filled diamond for the second.
   * rows: [{label, href?, a:{v, ci?, tip}, b:{v, ci?, tip} | null, note?, noteCls?, missing?}]
   * o: {aria, labelW?, domain?:[lo,hi], step?} */
  function dumbbell(o) {
    const rowH = 34, lw = o.labelW || 170, W = 640, m = { r: 150, t: 24, b: 10 };
    const H = m.t + o.rows.length * rowH + m.b, lo = (o.domain || [0, 100])[0], hi = (o.domain || [0, 100])[1];
    const X = (v) => lw + ((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * (W - lw - m.r);
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">`;
    for (let t = lo; t <= hi + 1e-9; t += o.step || 20) s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${m.t - 6}" y2="${H - m.b}"/><text x="${X(t)}" y="${m.t - 10}" text-anchor="middle">${t}</text>`;
    o.rows.forEach((r, i) => {
      const y = m.t + i * rowH + rowH / 2;
      const lab = `<text class="${r.hl ? "lbl-strong" : "lbl"}" x="${lw - 12}" y="${y + 4}" text-anchor="end">${esc(r.label)}</text>`;
      s += r.href ? `<a href="${r.href}">${lab}</a>` : lab;
      if (!r.a || r.a.v == null) { s += `<text x="${lw}" y="${y + 4}">${esc(r.missing || "Not measured")}</text>`; return; }
      const ci = (p, dy) => (p.ci ? `<line class="ci" x1="${X(p.ci[0])}" x2="${X(p.ci[1])}" y1="${y + dy}" y2="${y + dy}"/>` : "");
      if (r.b && r.b.v != null) s += `<line class="join" x1="${X(r.a.v)}" x2="${X(r.b.v)}" y1="${y}" y2="${y}"/>`;
      s += ci(r.a, -5) + (r.b && r.b.v != null ? ci(r.b, 5) : "");
      s += marker("circle", X(r.a.v), y, 4.5, "ring", r.a.tip);
      if (r.b && r.b.v != null) s += marker("diamond", X(r.b.v), y, 4.5, "dot hl", r.b.tip);
      else if (r.b) s += `<text class="lbl" x="${X(r.a.v) + 12}" y="${y + 4}">${esc(r.b.missing || "")}</text>`;
      if (r.note) s += `<text class="${r.noteCls || "lbl"}" x="${W - m.r + 12}" y="${y + 4}">${esc(r.note)}</text>`;
    });
    return s + "</svg>";
  }

  /* Small scatter on two percentage axes, both from zero, with an optional y = x line (a rate that cannot exceed the other).
   * points: [{x, y, shape, hl?, label?, tip, href?}]; o: {xLabel, yLabel, aria, diagonal?, diagLabel?} */
  function xy(o) {
    const W = 360, H = 260, m = { l: 40, r: 14, t: 16, b: 38 };
    const X = (v) => m.l + (v / 100) * (W - m.l - m.r), Y = (v) => H - m.b - (v / 100) * (H - m.t - m.b);
    let s = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria)}">`;
    [0, 25, 50, 75, 100].forEach((t) => { s += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 6}" y="${Y(t) + 4}" text-anchor="end">${t}</text><text x="${X(t)}" y="${H - m.b + 15}" text-anchor="middle">${t}</text>`; });
    s += `<line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>`;
    if (o.diagonal) s += `<line class="frontier" x1="${X(0)}" y1="${Y(0)}" x2="${X(100)}" y2="${Y(100)}"/><text class="lbl" x="${X(62)}" y="${Y(62) - 8}" text-anchor="end" transform="rotate(-${Math.atan2(H - m.t - m.b, W - m.l - m.r) * 180 / Math.PI} ${X(62)} ${Y(62) - 8})">${esc(o.diagLabel || "")}</text>`;
    s += `<text x="${W - m.r}" y="${H - 4}" text-anchor="end">${esc(o.xLabel)}</text><text x="${m.l - 34}" y="${m.t - 6}">${esc(o.yLabel)}</text>`;
    o.points.slice().sort((a, b) => (a.hl ? 1 : 0) - (b.hl ? 1 : 0)).forEach((p) => { s += `<g class="${p.hl ? "hl" : ""}">${marker(p.shape || "circle", X(p.x), Y(p.y), p.hl ? 5.5 : 4.5, "dot", p.tip, p.href)}</g>`; });
    o.points.filter((p) => p.label).forEach((p) => { const right = X(p.x) < W * 0.62; s += `<text class="${p.hl ? "lbl-strong" : "lbl"}" x="${X(p.x) + (right ? 9 : -9)}" y="${Y(p.y) + 4}" text-anchor="${right ? "start" : "end"}">${esc(p.label)}</text>`; });
    return s + "</svg>";
  }

  /* shared hover layer */
  function bindTips(root) {
    let tip = document.querySelector(".tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "tip"; tip.setAttribute("role", "status"); document.body.appendChild(tip); }
    const show = (el, x, y) => { const t = el.getAttribute("data-tip"); if (!t) return; const [h, ...rest] = t.split("\n"); tip.innerHTML = `<b>${esc(h)}</b>${rest.length ? "\n" + esc(rest.join("\n")) : ""}`; tip.classList.add("on"); const r = tip.getBoundingClientRect(); tip.style.left = Math.min(window.innerWidth - r.width - 8, x + 14) + "px"; tip.style.top = Math.max(8, y - r.height - 10) + "px"; };
    root.addEventListener("pointermove", (e) => { const el = e.target.closest("[data-tip]"); if (el) show(el, e.clientX, e.clientY); else tip.classList.remove("on"); });
    root.addEventListener("pointerleave", () => tip.classList.remove("on"));
    root.addEventListener("focusin", (e) => { const el = e.target.closest("[data-tip]"); if (el) { const b = el.getBoundingClientRect(); show(el, b.right, b.top); } });
    root.addEventListener("focusout", () => tip.classList.remove("on"));
  }

  const api = { hbars, scatter, dumbbell, xy, dotRows, contextChart, stackBar, budgetBars, sustain, bindTips, shapeSwatch, marker, fmt, esc };
  window.EDAT = window.EDAT || {};
  window.EDAT.charts = api;
  window.CHARTS = api;
})();
