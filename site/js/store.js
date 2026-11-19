/* The data layer. It loads the results store's export (site/data/README.md) and answers every question a page asks:
 * which entries exist on a device, what one cell holds (a value with its interval, or a typed status), how an entry
 * differs from each pinned reference, and where it ranks. Pages never read the raw JSON themselves. */
(function () {
  "use strict";
  const EDAT = (window.EDAT = window.EDAT || {});
  const CFG = EDAT.config, R = EDAT.registry;

  const SCHEMAS = { index: "edat.site-index/1.0", board: "edat.site-board/1.0", traj: "edat.site-trajectories/1.0" };
  const cache = new Map();

  class DataError extends Error {
    constructor(message, detail) { super(message); this.detail = detail || ""; }
  }

  async function getJSON(path) {
    if (cache.has(path)) return cache.get(path);
    const p = (async () => {
      let res;
      try { res = await fetch(CFG.dataRoot + path, { cache: "no-cache" }); }
      catch (e) { throw new DataError("The results could not be reached.", CFG.dataRoot + path + ": " + e.message); }
      if (!res.ok) throw new DataError("A results file is missing.", CFG.dataRoot + path + " answered " + res.status);
      try { return await res.json(); }
      catch (e) { throw new DataError("A results file is not valid JSON.", CFG.dataRoot + path); }
    })();
    cache.set(path, p);
    p.catch(() => cache.delete(path));
    return p;
  }

  /* ---------- the index ---------- */
  let index = null;
  async function loadIndex() {
    if (index) return index;
    const raw = await getJSON("index.json");
    if (raw.schema !== SCHEMAS.index) throw new DataError("The results index has an unknown format.", "schema " + raw.schema);
    const boards = (raw.boards || []).map((b) => ({
      version: b.board_version, sample: !!b.sample, generatedAt: b.generated_at, cloud: b.cloud || null, references: b.references || {},
      devices: (b.devices || []).map((d) => deviceInfo(d))
    }));
    index = { raw, sample: !!raw.sample, notice: raw.notice || "", generatedAt: raw.generated_at, boards };
    return index;
  }

  /* display facts for a device: the export's own fields first, then the site's labels for its class */
  function deviceInfo(d) {
    const c = CFG.devices[d.class] || CFG.devices[d.id] || {};
    const gb = d.ram_bytes ? Math.round(d.ram_bytes / 1073741824) : null;
    return {
      id: d.id, class: d.class || d.id, path: d.path, trajectories: d.trajectories,
      name: d.name || c.name || [d.soc, gb ? gb + " GB" : null].filter(Boolean).join(", ") || d.id,
      short: d.short || c.short || d.id, kind: d.kind || c.kind || (/(phone|iphone)/i.test(d.class || d.id) ? "phone" : "laptop"),
      fitLabel: d.fit_label || c.fitLabel || "this device", runtimeNote: c.runtimeNote || "",
      ram_bytes: d.ram_bytes || null, soc: d.soc || null, os: d.os || null
    };
  }

  /* ---------- one board file (a device, or the cloud references) ---------- */
  class Board {
    constructor(raw, devInfo, isCloud) {
      if (raw.schema !== SCHEMAS.board) throw new DataError("A results board has an unknown format.", "schema " + raw.schema);
      this.raw = raw; this.cloud = !!isCloud; this.sample = !!raw.sample; this.notice = raw.notice || "";
      this.version = raw.board_version; this.generatedAt = raw.generated_at;
      this.device = isCloud ? null : Object.assign({}, devInfo || {}, deviceInfo(Object.assign({}, devInfo || {}, raw.device || {})));
      this.references = raw.references || { tuned: null, base: null };
      this.models = new Map((raw.models || []).map((m) => [m.id, m]));
      this.variants = new Map((raw.variants || []).map((v) => [v.key, v]));
      this.runs = new Map((raw.runs || []).map((r) => [r.variant, r]));
      this.res = new Map(); this.present = new Set(); this.facetsByTier = new Map();
      (raw.results || []).forEach((r) => {
        this.res.set(r.variant + "§" + r.tier + "§" + r.metric + "§" + (r.facet || ""), r);
        this.present.add(r.tier + "§" + r.metric + "§" + (r.facet || ""));
        this.present.add(r.tier);
        if (r.facet) { const s = this.facetsByTier.get(r.tier) || new Set(); s.add(r.facet); this.facetsByTier.set(r.tier, s); }
      });
      this.saf = new Map((raw.safety || []).map((s) => [s.variant + "§" + s.tier, s]));
      (raw.safety || []).forEach((s) => this.present.add("safety§" + s.tier));
      this.cur = new Map((raw.curves || []).map((c) => [c.variant + "§" + c.metric, c]));
      (raw.curves || []).forEach((c) => this.present.add("curve§" + c.metric));
      this.rnk = new Map((raw.ranking || []).map((r) => [r.variant + "§" + r.tier + "§" + r.metric, r.rank_range]));
      this.entries = (raw.variants || []).map((v) => makeEntry(this, v));
      this.byKey = new Map(this.entries.map((e) => [e.key, e]));
    }
    entry(key) { return this.byKey.get(key) || null; }
    result(key, tier, metric, facet) { return this.res.get(key + "§" + tier + "§" + metric + "§" + (facet || "")) || null; }
    safety(key, tier) { return this.saf.get(key + "§" + tier) || null; }
    curve(key, metric) { return this.cur.get(key + "§" + metric) || null; }
    rank(key, tier, metric) { return this.rnk.get(key + "§" + tier + "§" + metric) || null; }
    has(tier, metric, facet) { return metric ? this.present.has(tier + "§" + metric + "§" + (facet || "")) : this.present.has(tier); }
    facets(tier, kind) { return [...(this.facetsByTier.get(tier) || [])].filter((f) => !kind || f.startsWith(kind + "=")).sort(); }
    tiers() { return [...new Set((this.raw.results || []).map((r) => r.tier))]; }
    refEntry(role) { return this.references[role] ? this.entry(this.references[role]) : null; }
  }

  const THINK = { on: "thinking on", off: "thinking off", minimal: "minimal thinking" };
  function makeEntry(board, v) {
    const model = board.models.get(v.model_id) || { id: v.model_id, kind: "local" };
    const run = board.runs.get(v.key) || { variant: v.key, status: "not_run", fit: { verdict: "not_measured" }, ranked: false, rank_group: "not_measured" };
    const cloud = model.kind === "cloud_reference" || run.rank_group === "cloud";
    const role = board.references.tuned === v.key ? "tuned" : board.references.base === v.key ? "base" : null;
    const siblings = (board.raw.variants || []).filter((x) => x.model_id === v.model_id && x.thinking !== v.thinking);
    return {
      key: v.key, board, model, variant: v, run, cloud, refRole: role,
      modelRole: model.role || (cloud ? "cloud_reference" : "candidate"),
      name: model.id, thinking: v.thinking,
      thinkingText: v.thinking === "on" || siblings.length ? THINK[v.thinking] : "",
      line: variantLine(v, model, run, cloud),
      group: run.rank_group, ranked: !!run.ranked,
      scored: ["ranked", "invalid", "cloud", "not_measured"].includes(run.rank_group) && run.status !== "does_not_fit"
    };
  }
  function variantLine(v, model, run, cloud) {
    if (cloud) {
      const d = (model.run_date && (model.run_date.last || model.run_date.first)) || run.date;
      return "Hosted through " + (CFG.runtimes[v.runtime] || v.runtime || "a provider") + (d ? ", run " + String(d).slice(0, 10) : "");
    }
    return [CFG.quants[v.quant] || v.quant, CFG.runtimes[v.runtime] || v.runtime].filter(Boolean).join(", ");
  }

  /* ---------- one cell ---------- */
  const S = (status, extra) => Object.assign({ status, value: null }, extra || {});
  const half = (iv) => (iv && iv.length === 2 && iv[1] != null && iv[0] != null ? (iv[1] - iv[0]) / 2 : null);

  function value(entry, metricId) {
    const m = R.metric[metricId];
    if (!m) return S("no_value");
    const b = entry.board, src = m.src, sc = m.scale || 1;
    if (entry.cloud && m.on === "local") return S("not_applicable_cloud");
    if (!entry.cloud && m.on === "cloud") return S("not_applicable_local");
    if (m.macOnly && b.device && b.device.kind === "phone") return S("not_applicable_iphone");
    if (src.r) {
      const rec = b.result(entry.key, src.r, src.m, src.f);
      if (!rec) return S(b.has(src.r, src.m, src.f) ? "not_run" : "not_in_board");
      if (rec.status && rec.status !== "ok") return S(rec.status, { rec });
      if (rec.value == null) return S("no_value", { rec });
      const iv = rec.interval && rec.interval_kind !== "none" ? [rec.interval[0] * sc, rec.interval[1] * sc] : null;
      return { status: "ok", value: rec.value * sc, interval: iv, half: half(iv), n: rec.n_cases, trials: rec.n_trials, kind: rec.interval_kind, vs: rec.vs || null, rec };
    }
    if (src.s) {
      const rec = b.safety(entry.key, src.s);
      if (!rec) return S(b.has("safety§" + src.s) ? "not_run" : "not_in_board");
      if (rec[src.k] == null) return S(src.s === "in_assistant" || src.s === "quality" ? "not_applicable" : "no_value", { rec });
      const isLeak = src.k.startsWith("leak");
      return {
        status: "ok", value: rec[src.k], interval: null, half: null, n: rec.trials, trials: rec.trials, rec,
        count: isLeak ? rec.leak_trials : rec.unsafe_trials, rate: rec[m.rateKey || (isLeak ? "leak_rate" : "unsafe_rate")],
        rateInterval: rec[isLeak ? "leak_interval" : "unsafe_interval"], vs: (rec.vs || {})[m.rateKey || (isLeak ? "leak_rate" : "unsafe_rate")] || null
      };
    }
    if (src.c) {
      const own = b.curve(entry.key, src.c), c = own || sharedCurve(entry, src.c);
      if (!c) return S(b.has("curve§" + src.c) ? "not_run" : "not_in_board");
      const p = c.points.find((x) => x.level === src.level);
      if (!p) return S("not_run");
      if (p.status !== "ok") return S(p.status, { need: p.need_bytes });
      if (p.value == null) return S("no_value");
      return { status: "ok", value: p.value * sc, interval: null, half: null, n: p.n, exact: true, shared: !own };
    }
    if (src.fit) {
      const f = entry.run.fit || {};
      if (entry.cloud || f.verdict === "not_applicable") return S("not_applicable_cloud");
      if (f.verdict === "not_measured") return S("not_measured");
      if (f[src.fit] == null) return S("no_value");
      return { status: "ok", value: f[src.fit] * sc, interval: null, half: null, exact: true };
    }
    return S("no_value");
  }

  /* Speed and memory belong to the weights, like the fit verdict: when an entry has no curve of its own, the curve of
   * the same model, quantisation and runtime in its other thinking mode is used, and the cell says so (`shared`). */
  function sharedCurve(entry, metric) {
    const v = entry.variant;
    for (const o of entry.board.entries) {
      if (o.key === entry.key || o.model.id !== v.model_id || o.variant.quant !== v.quant || o.variant.runtime !== v.runtime) continue;
      const c = entry.board.curve(o.key, metric);
      if (c) return c;
    }
    return null;
  }
  function curveFor(entry, metric) { return entry.board.curve(entry.key, metric) || sharedCurve(entry, metric); }

  /* ---------- difference from a pinned reference ----------
   * The store's paired difference (`vs`) is used whenever it exists. Where it does not, an unpaired estimate is made:
   * the difference of the two values with a margin of sqrt(a² + b²) of the two half-intervals, flagged `paired: false`.
   * Exact measures (speed, memory) get a plain difference with no significance tag. */
  function diff(entry, metricId, role, refBoard) {
    const m = R.metric[metricId];
    const cell = value(entry, metricId);
    if (entry.refRole === role) return { status: "self" };
    if (cell.status !== "ok") return { status: cell.status };
    const vs = cell.vs ? cell.vs[role] : undefined;
    if (vs && vs.self) return { status: "self" };
    if (vs && vs.refused) return { status: "refused", reason: vs.refused };
    if (vs && vs.delta != null) {
      const sc = m.scale || 1;
      return { status: "ok", paired: true, delta: vs.delta * sc, interval: vs.interval ? [vs.interval[0] * sc, vs.interval[1] * sc] : null, tag: vs.tag || null, wins: vs.wins, losses: vs.losses, ratio: vs.ratio != null ? vs.ratio : null, n: vs.n_cases };
    }
    if (vs === null && cell.vs) return { status: "none" };
    const rb = refBoard || entry.board;
    const ref = rb.refEntry(role);
    if (!ref) return { status: "none" };
    if (ref.key === entry.key) return { status: "self" };
    const rc = value(ref, metricId);
    if (rc.status !== "ok") return { status: "none" };
    const d = cell.value - rc.value;
    if (cell.half == null || rc.half == null) {
      return { status: "ok", paired: false, exact: true, delta: d, interval: null, tag: null, ratio: m.safety && rc.value > 0 ? cell.value / rc.value : null };
    }
    const margin = Math.sqrt(cell.half ** 2 + rc.half ** 2);
    const tag = !m.better || Math.abs(d) <= margin ? "no_clear_difference" : (m.better === "high") === d > 0 ? "better" : "worse";
    return { status: "ok", paired: false, delta: d, interval: [d - margin, d + margin], tag, ratio: m.safety && rc.value > 0 ? cell.value / rc.value : null };
  }

  /* ---------- ranking: the store's rank ranges, or the same rule over a filtered set ---------- */
  function rankRanges(entries, metricId) {
    const m = R.metric[metricId];
    const iv = new Map();
    entries.forEach((e) => { const c = value(e, metricId); if (c.status === "ok") iv.set(e.key, c.interval || [c.value, c.value]); });
    const out = new Map();
    const hib = m.better !== "low";
    iv.forEach(([lo, hi], k) => {
      if (!hib) [lo, hi] = [-hi, -lo];
      let ahead = 0, notBehind = 0;
      iv.forEach(([olo, ohi], o) => { if (o === k) return; if (!hib) [olo, ohi] = [-ohi, -olo]; if (olo > hi) ahead++; if (ohi >= lo) notBehind++; });
      out.set(k, [1 + ahead, 1 + notBehind]);
    });
    return out;
  }
  function storeRank(entry, metricId) {
    const src = R.metric[metricId].src;
    return src.r ? entry.board.rank(entry.key, src.r, src.m) : null;
  }

  /* best variant per model and thinking mode: a full fit leads unless a small-context variant beats it by more than the margin */
  function bestPerModel(entries, metricId) {
    const by = new Map();
    entries.forEach((e) => { const k = e.model.id + "§" + e.thinking; (by.get(k) || by.set(k, []).get(k)).push(e); });
    const out = [];
    by.forEach((list) => {
      const scoredList = list.map((e) => ({ e, c: value(e, metricId) })).filter((x) => x.c.status === "ok").sort((a, b) => b.c.value - a.c.value);
      if (!scoredList.length) { out.push(list[0]); return; }
      const top = scoredList[0], full = scoredList.find((x) => (x.e.run.fit || {}).verdict === "fits");
      if ((top.e.run.fit || {}).verdict !== "fits" && full && top.c.value - full.c.value <= Math.sqrt((top.c.half || 0) ** 2 + (full.c.half || 0) ** 2)) out.push(full.e);
      else out.push(top.e);
    });
    return out;
  }

  /* ---------- loaders the pages call ---------- */
  function board(version) {
    if (!index) return null;
    return index.boards.find((b) => b.version === version) || index.boards[0] || null;
  }
  async function loadDevice(version, deviceId) {
    const bi = board(version);
    if (!bi) throw new DataError("No results board is published yet.");
    const dev = bi.devices.find((d) => d.id === deviceId) || bi.devices[0];
    if (!dev) throw new DataError("This board lists no devices.");
    const k = "board:" + dev.path;
    if (!cache.has(k)) cache.set(k, getJSON(dev.path).then((raw) => new Board(raw, dev, false)));
    return cache.get(k);
  }
  async function loadCloud(version) {
    const bi = board(version);
    if (!bi || !bi.cloud) return null;
    const k = "board:" + bi.cloud;
    if (!cache.has(k)) cache.set(k, getJSON(bi.cloud).then((raw) => new Board(raw, null, true)).catch((e) => { cache.delete(k); throw e; }));
    return cache.get(k);
  }
  async function loadAllDevices(version) {
    const bi = board(version);
    return Promise.all((bi ? bi.devices : []).map((d) => loadDevice(version, d.id)));
  }
  /* trajectories: development split only. Any record marked held out is dropped here as a second guard. */
  async function loadTrajectories(version, deviceId) {
    const bi = board(version);
    const dev = bi && bi.devices.find((d) => d.id === deviceId);
    if (!dev || !dev.trajectories) return [];
    const raw = await getJSON(dev.trajectories);
    if (raw.schema !== SCHEMAS.traj) throw new DataError("The trajectories file has an unknown format.", "schema " + raw.schema);
    return (raw.trajectories || []).filter((t) => t.split !== "heldout" && t.split !== "held_out");
  }

  /* "What it found" (in-assistant findings). Read from the export when it exists. Until the store writes it, a sample
   * board falls back to the site's own labelled sample in fixtures/ (see fixtures/make_findings_sample.py); a real
   * board never does. Records marked held out are dropped. */
  async function loadFindings(version, deviceId) {
    const bi = board(version);
    if (!bi) return null;
    const k = "findings:" + version + "/" + deviceId;
    if (!cache.has(k)) cache.set(k, (async () => {
      const tryGet = async (url) => {
        let res; try { res = await fetch(url, { cache: "no-cache" }); } catch (e) { return null; }
        if (res.status === 404) return null;
        if (!res.ok) throw new DataError("The findings file did not load.", url + " answered " + res.status);
        try { return await res.json(); } catch (e) { throw new DataError("The findings file is not valid JSON.", url); }
      };
      let raw = await tryGet(CFG.dataRoot + version + "/" + deviceId + ".findings.json"), fromFixture = false;
      if (!raw && bi.sample) { raw = await tryGet("fixtures/" + version + "/" + deviceId + ".findings.json"); fromFixture = !!raw; }
      if (!raw) return null;
      if (!/^edat\.site-findings\//.test(raw.schema || "")) throw new DataError("The findings file has an unknown format.", "schema " + raw.schema);
      raw.runs = (raw.runs || []).filter((r) => r.split !== "heldout" && r.split !== "held_out");
      raw.fromFixture = fromFixture;
      return raw;
    })());
    return cache.get(k);
  }

  EDAT.store = { loadFindings, curveFor, loadIndex, loadDevice, loadCloud, loadAllDevices, loadTrajectories, board, value, diff, rankRanges, storeRank, bestPerModel, deviceInfo, DataError, Board, _clear: () => { cache.clear(); index = null; } };
})();
