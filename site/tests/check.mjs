// Checks for the static site. Run from anywhere: `node tests/check.mjs` (Node 24, standard library only).
// Exit 0 when everything passes, 1 otherwise.
// Part 1 (static): loads the site's real config/registry/store in a vm, then checks
//   b. export schema (index, boards, trajectories: fields, keys, vocabularies, percentage ranges)
//   c. registry coverage (every non-planned metric has data; duplicate ids, groups, tierLinks)
//   d. store.value() / store.diff() on every entry for every metric (never throws, typed statuses)
//   e. the benchmark name appears only in js/config.js; no local paths or dev ports anywhere (public repo)
//   f. held-out guard (no heldout trajectories)
// Part 2 (headless): serves the site on 127.0.0.1 (ephemeral port) and dumps every route's DOM in headless Chrome
//   (CHROME env or /Applications/Google Chrome.app); a page passes with data-render="ok" and no data-error="1".
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import vm from "node:vm";
import http from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCHEMA_FILE = path.resolve(SITE, "..", "edat", "results", "schema", "site-board.schema.json");
let fails = 0, passes = 0;
const fail = (m) => { fails++; console.log("  FAIL " + m); };
const ok = (m) => { passes++; if (process.env.VERBOSE) console.log("  ok   " + m); };
const check = (c, m) => (c ? ok(m) : fail(m));
const section = (t) => console.log("\n" + t);
const readJSON = (p) => JSON.parse(fs.readFileSync(p, "utf8"));
const uniq = (a) => [...new Set(a)];

// ---------------------------------------------------------------- Part 1
section("Part 1: static checks");
const sandbox = { window: {}, console };
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
for (const f of ["config", "registry", "store"]) vm.runInContext(fs.readFileSync(path.join(SITE, "js", f + ".js"), "utf8"), sandbox, { filename: f + ".js" });
const EDAT = sandbox.window.EDAT, R = EDAT.registry, ST = EDAT.store;

const DATA = path.join(SITE, "data");
const index = readJSON(path.join(DATA, "index.json"));
check(index.schema === "edat.site-index/1.0", "index.json schema is edat.site-index/1.0");

let enums = {};
try {
  const sch = readJSON(SCHEMA_FILE);
  const walk = (o, p) => { if (o && typeof o === "object") for (const [k, v] of Object.entries(o)) { if (k === "enum") enums[p] = v; else walk(v, p + "/" + k); } };
  walk(sch, "");
} catch (e) { fail("could not read the board schema: " + e.message); }
const en = (suffix, dflt) => enums[Object.keys(enums).find((k) => k.endsWith(suffix)) || ""] || dflt;
const RUN_STATUS = en("runs/items/properties/status", ["ok", "does_not_fit", "not_run", "failed", "invalid"]);
const VERDICT = en("verdict", ["fits", "fits_small_context", "does_not_fit", "not_measured", "not_applicable"]);
const GROUPS = en("rank_group", ["ranked", "invalid", "does_not_fit", "not_measured", "cloud"]);
const CURVE_ST = en("points/items/properties/status", ["ok", "out_of_memory", "beyond_window", "not_run"]);
const RES_ST = [null, "ok", ...Object.keys(R.statuses)];
const TOP = ["schema", "sample", "board_version", "generated_at", "device", "references", "models", "variants", "runs", "results", "safety", "curves", "ranking"];

// load every board
const boards = []; // {id, file, raw, board, isCloud, dev}
const trajFiles = [];
for (const bi of index.boards || []) {
  const items = (bi.devices || []).map((d) => ({ id: d.id, rel: d.path, dev: ST.deviceInfo(d), isCloud: false, traj: d.trajectories }));
  if (bi.cloud) items.push({ id: "cloud", rel: bi.cloud, dev: null, isCloud: true });
  for (const it of items) {
    const file = path.join(DATA, it.rel);
    if (!fs.existsSync(file)) { fail(`${it.rel} listed in index.json but missing`); continue; }
    const raw = readJSON(file);
    let board = null;
    try { board = new ST.Board(raw, it.dev, it.isCloud); } catch (e) { fail(`${it.rel}: Board constructor threw: ${e.message}`); }
    boards.push({ ...it, raw, board, version: bi.board_version });
    if (it.traj) {
      const tf = path.join(DATA, it.traj);
      if (!fs.existsSync(tf)) fail(`${it.traj} listed but missing`); else trajFiles.push({ rel: it.traj, raw: readJSON(tf), id: it.id });
    }
  }
  for (const [dev, refs] of Object.entries(bi.references || {})) {
    const b = boards.find((x) => x.id === dev && x.version === bi.board_version);
    for (const role of ["tuned", "base"]) if (b) check((b.raw.variants || []).some((v) => v.key === refs[role]), `index reference ${dev}.${role} exists in its board`);
  }
}

for (const b of boards) {
  const r = b.raw, tag = b.rel;
  const missing = TOP.filter((k) => !(k in r));
  check(!missing.length, `${tag}: top-level fields present${missing.length ? " (missing " + missing.join(",") + ")" : ""}`);
  check(r.schema === "edat.site-board/1.0", `${tag}: schema id`);
  const keys = new Set((r.variants || []).map((v) => v.key));
  const bad = [];
  const known = (k, where) => { if (!keys.has(k)) bad.push(`${where} names unknown variant ${k}`); };
  (r.runs || []).forEach((x) => {
    known(x.variant, "run");
    if (!RUN_STATUS.includes(x.status)) bad.push(`run status ${x.status}`);
    if (!x.fit || !VERDICT.includes(x.fit.verdict)) bad.push(`fit verdict ${x.fit && x.fit.verdict}`);
    if (!GROUPS.includes(x.rank_group)) bad.push(`rank_group ${x.rank_group}`);
    if (b.isCloud ? x.rank_group !== "cloud" || x.ranked : x.rank_group === "cloud") bad.push(`cloud/local rank_group mismatch for ${x.variant}`);
  });
  keys.forEach((k) => { if (!(r.runs || []).some((x) => x.variant === k)) bad.push(`variant ${k} has no run`); });
  (r.results || []).forEach((x) => {
    known(x.variant, "result");
    if (!RES_ST.includes(x.status)) bad.push(`result status ${x.status}`);
    if (x.unit === "%" && x.value != null && !(x.value >= 0 && x.value <= 100)) bad.push(`% value out of 0-100: ${x.metric}=${x.value}`);
    if (x.unit === "%" && Array.isArray(x.interval) && x.interval.some((n) => n != null && !(n >= 0 && n <= 100))) bad.push(`% interval out of 0-100: ${x.metric}`);
  });
  (r.safety || []).forEach((x) => {
    known(x.variant, "safety");
    for (const f of ["unsafe_rate", "leak_rate"]) if (x[f] != null && !(x[f] >= 0 && x[f] <= 100)) bad.push(`safety ${f} out of 0-100: ${x[f]}`);
  });
  (r.curves || []).forEach((x) => { known(x.variant, "curve"); (x.points || []).forEach((p) => { if (!CURVE_ST.includes(p.status)) bad.push(`curve point status ${p.status}`); }); });
  (r.ranking || []).forEach((x) => known(x.variant, "ranking"));
  (r.models || []).forEach((m) => { if (!["local", "cloud_reference"].includes(m.kind)) bad.push(`model kind ${m.kind}`); });
  (r.variants || []).forEach((v) => { if (!(r.models || []).some((m) => m.id === v.model_id)) bad.push(`variant ${v.key} has unknown model ${v.model_id}`); });
  for (const role of ["tuned", "base"]) { const k = (r.references || {})[role]; if (k != null && !keys.has(k)) bad.push(`references.${role} ${k} not in variants`); }
  uniq(bad).slice(0, 8).forEach((m) => fail(`${tag}: ${m}`));
  if (!bad.length) ok(`${tag}: variants, vocabularies, ranges`);
}

section("Registry coverage");
const metrics = R.metrics;
const dup = metrics.map((m) => m.id).filter((id, i, a) => a.indexOf(id) !== i);
check(!dup.length, "metric ids unique" + (dup.length ? ": " + uniq(dup).join(",") : ""));
metrics.forEach((m) => { if (!R.group[m.group]) fail(`metric ${m.id}: group "${m.group}" is not in registry.groups`); if (m.tierLink && !R.tier[m.tierLink]) fail(`metric ${m.id}: tierLink "${m.tierLink}" is not in tiers`); });
function hasData(m) {
  const s = m.src;
  return boards.some(({ raw }) => {
    if (s.r) return (raw.results || []).some((x) => x.tier === s.r && x.metric === s.m && (x.facet || null) === (s.f || null));
    if (s.s) return (raw.safety || []).some((x) => x.tier === s.s && x[s.k] != null);
    if (s.c) return (raw.curves || []).some((x) => x.metric === s.c && (x.points || []).some((p) => p.level === s.level));
    if (s.fit) return (raw.runs || []).some((x) => x.fit && x.fit[s.fit] != null);
    return false;
  });
}
const planned = [];
for (const m of metrics) {
  const has = hasData(m);
  if (m.planned) { if (!has) planned.push(m.id); else ok(`planned metric ${m.id} now has data`); } else check(has, `metric ${m.id} has rows in the sample boards`);
}
console.log(`  planned, not in this board yet (${planned.length}): ${planned.join(", ")}`);

section("Store exercise");
let cells = 0, storeBad = [];
for (const b of boards) {
  if (!b.board) continue;
  const devBoard = b.isCloud ? (boards.find((x) => !x.isCloud && x.id === "mac-ref") || boards.find((x) => !x.isCloud)).board : b.board;
  for (const e of b.board.entries) {
    if (e.group === "does_not_fit" && e.ranked) storeBad.push(`${e.key}: does_not_fit but ranked`);
    for (const m of metrics) {
      try {
        const v = ST.value(e, m.id); cells++;
        if (!(v && typeof v === "object")) { storeBad.push(`value ${e.key} ${m.id}: not an object`); continue; }
        if (v.status === "ok" ? !Number.isFinite(v.value) : !R.statuses[v.status]) storeBad.push(`value ${e.key} ${m.id}: bad ${v.status}/${v.value}`);
        for (const role of ["tuned", "base"]) {
          const d = ST.diff(e, m.id, role, devBoard);
          if (!(d && typeof d === "object")) storeBad.push(`diff ${e.key} ${m.id} ${role}: not an object`);
          else if (d.status === "ok" ? !Number.isFinite(d.delta) : !(R.statuses[d.status] || ["self", "refused", "none"].includes(d.status))) storeBad.push(`diff ${e.key} ${m.id} ${role}: bad ${d.status}`);
        }
      } catch (err) { storeBad.push(`threw for ${e.key} ${m.id}: ${err.message}`); }
    }
  }
}
uniq(storeBad).slice(0, 12).forEach((m) => fail(m));
if (storeBad.length > 12) fail(`... and ${storeBad.length - 12} more store problems`);
if (!storeBad.length) ok("store");
console.log(`  ${cells} cells, ${storeBad.length} problems`);

section("Name, paths, held-out");
const NAME = EDAT.config.name;
const walkFiles = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => e.isDirectory() ? walkFiles(path.join(d, e.name)) : [path.join(d, e.name)]);
const all = walkFiles(SITE).filter((f) => !f.startsWith(DATA + path.sep) && !f.includes(path.sep + "tests" + path.sep));
const noName = all.filter((f) => /(^|\/)(index\.html|js\/app\.js|js\/ui\.js|js\/charts\.js|js\/store\.js|js\/registry\.js|js\/pages\/[^/]+\.js)$/.test(path.relative(SITE, f)));
for (const f of noName) if (fs.readFileSync(f, "utf8").includes(NAME)) fail(`${path.relative(SITE, f)} contains the literal name "${NAME}" (only js/config.js may)`);
for (const f of all) {
  if (/\.(png|jpe?g|gif|ico|woff2?)$/i.test(f)) continue;
  const t = fs.readFileSync(f, "utf8");
  for (const pat of ["/Users/", "8765", "8791", "localhost:8000"]) if (t.includes(pat)) fail(`${path.relative(SITE, f)} contains "${pat}"`);
}
ok("name and path scan done (" + all.length + " files)");
let held = 0, caseIds = [], trajAll = [];
for (const t of trajFiles) {
  if (t.raw.schema !== "edat.site-trajectories/1.0") fail(`${t.rel}: schema ${t.raw.schema}`);
  for (const r of t.raw.trajectories || []) { trajAll.push(r); if (["heldout", "held_out"].includes(r.split)) held++; }
}
check(held === 0, "no held-out trajectory records" + (held ? ` (${held} found)` : ""));
// the "What it found" files (export or site sample): development split only
for (const dir of [path.join(SITE, "data"), path.join(SITE, "fixtures")]) {
  if (!fs.existsSync(dir)) continue;
  for (const f of fs.readdirSync(dir, { recursive: true }).filter((x) => String(x).endsWith(".findings.json"))) {
    const doc = JSON.parse(fs.readFileSync(path.join(dir, String(f)), "utf8"));
    const bad = (doc.runs || []).filter((r) => ["heldout", "held_out"].includes(r.split)).length + (["heldout", "held_out"].includes(doc.split) ? 1 : 0);
    check(/^edat\.site-findings\//.test(doc.schema || "") && bad === 0, `findings ${path.basename(dir)}/${f}: known schema, development split only`);
  }
}
const mac = trajFiles.find((t) => t.id === "mac-ref") || trajFiles[0];
const caseList = [];
if (mac) for (const r of mac.raw.trajectories || []) if (r.tier === "agentic" && !caseList.some((c) => c.id === r.case_id)) caseList.push({ id: r.case_id, key: r.variant });
caseIds = caseList.slice(0, 3);

// ---------------------------------------------------------------- Part 2
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
async function headless() {
  section("Part 2: headless pages");
  if (!fs.existsSync(CHROME)) { console.log("  SKIP headless: no Chrome"); return; }
  const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml" };
  const server = http.createServer((req, res) => {
    let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (p.endsWith("/")) p += "index.html";
    const f = path.join(SITE, p);
    if (!f.startsWith(SITE + path.sep) || !fs.existsSync(f) || fs.statSync(f).isDirectory() || !TYPES[path.extname(f)]) { res.writeHead(404); return res.end("not found"); }
    res.writeHead(200, { "content-type": TYPES[path.extname(f)], "cache-control": "no-store" });
    res.end(fs.readFileSync(f));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}/index.html`;

  const mac = boards.find((b) => b.id === "mac-ref"), cloud = boards.find((b) => b.isCloud), iph = boards.find((b) => b.id === "iphone-ref");
  const enc = encodeURIComponent;
  const routes = [["#/"], ["#/?rows=all"], ["#/?g=agentic,families,assistant,quality,perf"], ["#/?vs=tuned&g=quality"], ["#/?nf=1&cl=0"], ["#/?th=on"], ["#/?d=iphone-ref"], ["#/?x=time"]].map((r) => ({ hash: r[0] }));
  const add = (hash, expect) => routes.push({ hash, expect });
  const modelIds = (b) => (b ? uniq((b.raw.models || []).map((m) => m.id)) : []);
  modelIds(mac).forEach((id) => add(`#/model/${enc(id)}?d=mac-ref`));
  modelIds(cloud).forEach((id) => add(`#/model/${enc(id)}?d=mac-ref`));
  modelIds(iph).forEach((id) => add(`#/model/${enc(id)}?d=iphone-ref`));
  add("#/compare");
  const lk = mac ? mac.raw.variants.map((v) => v.key) : [];
  const ck = cloud ? cloud.raw.variants.map((v) => v.key) : [];
  if (lk.length >= 2) add(`#/compare?m=${lk.slice(0, 2).map(enc).join(",")}`);
  if (lk.length >= 3 && ck.length) add(`#/compare?m=${[...lk.slice(0, 3), ck[0]].map(enc).join(",")}&vs=base`);
  add("#/tests");
  R.tiers.forEach((t) => add(`#/tests/${enc(t.id)}`));
  caseIds.forEach((c) => add(`#/case/agentic/${enc(c.id)}?m=${enc(c.key)}`));
  ["#/devices", "#/device/mac-ref", "#/device/iphone-ref", "#/profiles", "#/vote", "#/methodology"].forEach((h) => add(h));
  // "What it found": every profile in the findings sample, a reference as the chosen entry, no references beside it, the iPhone
  const fpath = path.join(SITE, "fixtures", "sample-0", "mac-ref.findings.json");
  const findings = fs.existsSync(fpath) ? JSON.parse(fs.readFileSync(fpath, "utf8")) : null;
  add("#/found");
  (findings ? findings.profiles : []).forEach((p) => add(`#/found?p=${enc(p.id)}`));
  if (mac && mac.raw.references.base) add(`#/found?m=${enc(mac.raw.references.base)}&refs=0`);
  add("#/found?d=iphone-ref");
  // case pages: an unsafe trial, a case id that does not exist; the test pages and profiles on the iPhone
  const macTraj = boards.find((b) => b.id === "mac-ref");
  const unsafe = trajAll.find((t) => t.outcome === "unsafe");
  if (unsafe) add(`#/case/${enc(unsafe.tier)}/${enc(unsafe.case_id)}?m=${enc(unsafe.variant)}&t=${unsafe.trial}`);
  add("#/case/agentic/no-such-case");
  ["#/tests?d=iphone-ref", "#/tests/agentic?d=iphone-ref", "#/tests/in_assistant?d=iphone-ref", "#/profiles?d=iphone-ref", "#/profiles?d=mac-ref"].forEach((h) => add(h));
  add("#/nope", "notfound");
  routes.push({ hash: "#/", width: 375, height: 812, label: "#/ at 375x812" });

  const run = (r) => new Promise((resolve) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "edat-chrome-"));
    const args = ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", `--user-data-dir=${dir}`, "--virtual-time-budget=6000", `--window-size=${r.width || 1280},${r.height || 900}`, "--dump-dom", base + r.hash];
    // Chrome prints the DOM but may not exit afterwards, so stop it once </html> arrives (60 s hard limit).
    const child = spawn(CHROME, args, { detached: true, stdio: ["ignore", "pipe", "ignore"] });
    let out = "", done = false;
    const stop = () => { try { process.kill(-child.pid, "SIGKILL"); } catch { /* already gone */ } };
    const timer = setTimeout(() => { stop(); finish("timeout after 60 s"); }, 60000);
    child.stdout.on("data", (d) => { out += d; if (out.includes("</html>")) { stop(); finish(null); } });
    child.on("exit", () => finish(null));
    function finish(err) {
      if (done) return; done = true; clearTimeout(timer);
      fs.rmSync(dir, { recursive: true, force: true });
      const dom = String(out || "");
      const want = r.expect || "ok";
      const m = dom.match(/<html[^>]*>/);
      const got = m && (m[0].match(/data-render="([^"]*)"/) || [])[1];
      const errored = /data-error="1"/.test(dom);
      let msg = "";
      if (err && !dom) msg = "chrome failed: " + err;
      else if (got !== want) msg = `data-render=${got} (wanted ${want})`;
      else if (errored) msg = "data-error=1 in DOM";
      if (msg) {
        const e = dom.match(/class="[^"]*\b(?:empty|error-state)\b[^"]*"[\s\S]*$/);
        msg += " | " + (e ? e[0].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() : dom.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()).slice(0, 300);
      }
      resolve({ r, msg });
    }
  });
  const queue = routes.slice(), results = [];
  await Promise.all([1, 2, 3].map(async () => { while (queue.length) results.push(await run(queue.shift())); }));
  server.close();
  for (const { r, msg } of results.sort((a, b) => routes.indexOf(a.r) - routes.indexOf(b.r))) msg ? fail(`${r.label || r.hash}: ${msg}`) : ok(r.label || r.hash);
  console.log(`  ${routes.length} routes, ${results.filter((x) => x.msg).length} failing`);
}
await headless();

console.log(`\n${fails ? "FAILED" : "PASSED"}: ${fails} failure(s), ${passes} passed checks`);
process.exit(fails ? 1 : 0);
