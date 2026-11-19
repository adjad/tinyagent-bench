/* Router and shell. Routes live in the URL hash so every view, filter and selection can be linked.
 * Each page module registers EDAT.pages[name] = { route, render(ctx) -> {title, html}, after?(root, ctx) }. */
(function () {
  "use strict";
  const EDAT = window.EDAT, CFG = EDAT.config, U = EDAT.ui, ST = EDAT.store, I = U.I, esc = U.esc;
  const pages = (EDAT.pages = EDAT.pages || {});
  const $ = (s, r) => (r || document).querySelector(s);

  /* Routes live in the hash. Where a host forbids path-like hashes, they can live in memory instead. */
  const MEM = !!window.EDAT_MEMORY_ROUTES; let memRoute = "#/";
  const currentHash = () => (MEM ? memRoute : location.hash);
  function go(h) { if (MEM) { memRoute = h; onRoute(); } else if (location.hash === h) onRoute(); else location.hash = h; }
  function parse() {
    const h = currentHash().slice(1) || "/";
    const [path, qs] = h.split("?");
    return { parts: path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch (e) { return p; } }), q: new URLSearchParams(qs || "") };
  }
  /* parameters that follow the reader from page to page */
  const STICKY = ["d", "b", "vs"];
  const LEADER = ["g", "th", "arch", "rows", "nf", "cl", "x", "s", "o"];
  let S = parse();
  function link(path, params, keepLeader) {
    const q = new URLSearchParams();
    STICKY.forEach((k) => S.q.get(k) && q.set(k, S.q.get(k)));
    if (keepLeader) LEADER.forEach((k) => S.q.get(k) && q.set(k, S.q.get(k)));
    Object.entries(params || {}).forEach(([k, v]) => { if (v == null || v === "") q.delete(k); else q.set(k, v); });
    const qs = q.toString();
    return "#" + path + (qs ? "?" + qs : "");
  }
  /* `path` segments must already be encoded: build them with EDAT.ui.seg(id), since ids may hold spaces or slashes */
  const herePath = () => "/" + S.parts.map(encodeURIComponent).join("/");
  const linkHere = (params) => link(herePath(), Object.assign(Object.fromEntries(S.q), params));
  const setQ = (params) => go(linkHere(params));

  const NAV = [["", "Leaderboard"], ["compare", "Compare"], ["tests", "Tests"], ["devices", "Devices"], ["profiles", "Profiles"], ["methodology", "Methodology"]];
  const ROUTES = {
    "": "home", model: "model", compare: "compare", tests: "tests", case: "tests", devices: "devices", device: "devices",
    profiles: "profiles", vote: "vote", methodology: "methodology"
  };

  /* ---------- shell ---------- */
  function renderShell(index) {
    $("#wordmark").innerHTML = `${I.logo}<span>${esc(CFG.name)}</span><span class="wordmark-sub">${esc(CFG.longName)}</span>`;
    $("#wordmark").setAttribute("aria-label", `${CFG.name}, ${CFG.longName}: leaderboard`);
    const here = S.parts[0] || "";
    const navKey = here === "model" ? "" : here === "case" ? "tests" : here === "device" ? "devices" : here;
    $("#nav-links").innerHTML = NAV.map(([p, l]) => `<li><a href="${link("/" + p)}"${navKey === p ? ' aria-current="page"' : ""}>${esc(l)}</a></li>`).join("");
    const repo = $("#foot-repo"); if (repo) repo.href = CFG.repository;
    if (!index) return;
    const bi = ST.board(S.q.get("b")), sel = $("#nav-device");
    if (bi) {
      const cur = currentDeviceId(bi);
      sel.innerHTML = bi.devices.map((d) => `<option value="${esc(d.id)}"${d.id === cur ? " selected" : ""}>${esc(d.name)}</option>`).join("");
      sel.setAttribute("aria-label", "Device for all results");
    }
    const band = $("#sample-band");
    if (index.sample || (bi && bi.sample)) {
      band.hidden = false;
      $("#sample-text").textContent = "Sample data. Every model name and number on this site is invented to exercise the pages; no model has been measured yet. The two devices are the planned first ones.";
    } else band.hidden = true;
    $("#foot-text").textContent = `${CFG.name}, ${CFG.longName}. ${bi ? "Board " + bi.version + ", results dated " + U.date(bi.generatedAt) + ". " : ""}English cases only.${index.sample ? " Sample data only." : ""}`;
  }
  function currentDeviceId(bi) {
    const d = S.q.get("d");
    if (d && bi.devices.some((x) => x.id === d)) return d;
    const byClass = bi.devices.find((x) => x.class === CFG.defaultDeviceClass);
    return (byClass || bi.devices[0] || {}).id;
  }

  /* ---------- render ---------- */
  let renderSeq = 0;
  async function render() {
    S = parse();
    const seq = ++renderSeq;
    const app = $("#app");
    const head = S.parts[0] || "";
    const pageName = ROUTES[head];
    renderShell(null);
    document.documentElement.removeAttribute("data-render");
    const slow = setTimeout(() => { if (seq === renderSeq) app.innerHTML = U.skeleton(pageName === "home" ? "table" : "page"); }, 120);
    try {
      const index = await ST.loadIndex();
      renderShell(index);
      const bi = ST.board(S.q.get("b"));
      const page = pages[pageName];
      if (!page) throw Object.assign(new Error("not found"), { notFound: true });
      const ctx = { parts: S.parts, q: S.q, link, linkHere, setQ, go, index, boardInfo: bi, devId: bi ? currentDeviceId(bi) : null };
      if (bi && bi.devices.length) {
        const [device, cloud] = await Promise.all([ST.loadDevice(bi.version, ctx.devId), ST.loadCloud(bi.version).catch(() => null)]);
        ctx.device = device; ctx.cloud = cloud;
      }
      const out = await page.render(ctx);
      if (seq !== renderSeq) return;
      clearTimeout(slow);
      document.title = (out.title ? out.title + " | " : "") + CFG.name;
      app.innerHTML = out.html;
      if (page.after) page.after(app, ctx);
      if (EDAT.charts && EDAT.charts.bindTips) EDAT.charts.bindTips(app);
      document.documentElement.setAttribute("data-render", "ok");
      document.documentElement.setAttribute("data-page", pageName);
    } catch (err) {
      if (seq !== renderSeq) return;
      clearTimeout(slow);
      if (err && err.notFound) {
        document.title = "Not found | " + CFG.name;
        app.innerHTML = `<div class="container">${U.empty("Nothing at this address", `The page may have moved. <a class="link" href="${link("/")}">Back to the leaderboard</a>.`)}</div>`;
        document.documentElement.setAttribute("data-render", "notfound");
      } else {
        document.title = "Results did not load | " + CFG.name;
        app.innerHTML = U.errorState(err);
        document.documentElement.setAttribute("data-render", "error");
        if (window.console) console.error(err);
      }
    }
  }
  function onRoute() {
    const prev = window.__edatPath; const now = (S = parse()).parts.join("/");
    render().then(() => {
      if (prev !== now) {
        const hashAnchor = S.q.get("at");
        const el = hashAnchor && document.getElementById(hashAnchor);
        if (el) el.scrollIntoView(); else window.scrollTo(0, 0);
      }
    });
    window.__edatPath = now;
    $("#nav-links").classList.remove("open"); $("#menu-btn").setAttribute("aria-expanded", "false");
  }

  /* ---------- events shared by every page ---------- */
  document.addEventListener("click", (e) => {
    if (MEM) {
      const a = e.target.closest('a[href^="#/"]');
      if (a && !e.metaKey && !e.ctrlKey) { e.preventDefault(); go(a.getAttribute("href")); return; }
    }
    const set = e.target.closest("button[data-set]");
    if (set && !set.disabled) { e.preventDefault(); setQ({ [set.dataset.set]: set.dataset.val }); return; }
    const sort = e.target.closest("button[data-sort]");
    if (sort) {
      const id = sort.dataset.sort, m = EDAT.registry.metric[id];
      const def = m && m.better === "low" ? "asc" : "desc";
      const cur = S.q.get("s") === id ? (S.q.get("o") || def) : null;
      setQ({ s: id, o: cur ? (cur === "asc" ? "desc" : "asc") : def });
      return;
    }
    if (e.target.closest("[data-retry]")) { ST._clear(); render(); return; }
    const anchor = e.target.closest("a[data-anchor]");
    if (anchor) { const el = document.getElementById(anchor.dataset.anchor); if (el) { e.preventDefault(); el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); el.focus({ preventScroll: true }); } }
  });
  document.addEventListener("change", (e) => {
    const sel = e.target.closest("select[data-set]");
    if (sel) setQ({ [sel.dataset.set]: sel.value });
  });
  $("#nav-device").addEventListener("change", (e) => setQ({ d: e.target.value }));
  $("#menu-btn").addEventListener("click", () => { const n = $("#nav-links"); const open = n.classList.toggle("open"); $("#menu-btn").setAttribute("aria-expanded", String(open)); });
  $("#theme-btn").addEventListener("click", () => {
    const root = document.documentElement;
    const isDark = root.getAttribute("data-theme") === "dark" || (!root.hasAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = isDark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("edat-theme", next); } catch (err) { /* private mode: the choice lasts this visit */ }
  });
  if (!MEM) window.addEventListener("hashchange", onRoute);
  EDAT.app = { go, link, render, parse: () => S };
  onRoute();
})();
