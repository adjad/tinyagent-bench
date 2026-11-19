/* Readability votes (#/vote): blind side-by-side pairs for one rater, the benchmark's author. The votes check the
 * readability judge before its preference counts. Pairs come from `<board>/vote-pairs.json` when the store publishes
 * it; votes stay in this browser until the rater exports them as a JSON file for the results store.
 *
 * Proposed file shape, `data/<board>/vote-pairs.json`:
 * {
 *   "schema": "edat.site-vote-pairs/1.0",
 *   "board_version": "<board>", "generated_at": "<ISO time>", "sample": false,
 *   "planned": <number of pairs in the full set>,
 *   "pairs": [{
 *     "id": "<pair id>",
 *     "kind": "candidate_vs_reference" | "candidate_vs_candidate",
 *     "recognition_check": false,             // true: after the vote, ask which answer the tuned reference wrote
 *     "source": "<question family, plain words>",
 *     "prompt": "<the user's message>",
 *     "a": { "text": "<answer>", "tokens": <visible tokens>, "entry": "<entry key>", "role": "tuned" | "base" | "candidate" },
 *     "b": { ... same ... }
 *   }]
 * }
 * Both answers already passed the fact check (pairs that differ in facts are dropped) and share one style.
 * `entry` and `role` are hidden until the vote is cast. Held-out cases never appear in this file. */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, ST = EDAT.store, CFG = EDAT.config;
  const { esc, fmt, I } = U;
  const PAIRS_SCHEMA = "edat.site-vote-pairs/1.0";
  const VOTES_SCHEMA = "edat.readability-votes/1.0";
  const CHOICES = [
    { key: "1", v: "a", label: "A is easier to read" },
    { key: "2", v: "b", label: "B is easier to read" },
    { key: "3", v: "same", label: "About the same" },
    { key: "4", v: "both_poor", label: "Both poor" }
  ];
  const choiceWords = { a: "A is easier to read", b: "B is easier to read", same: "about the same", both_poor: "both poor" };

  /* the only state this page keeps; votes live in localStorage, keyed by board version */
  const S = { version: null, file: null, problem: null, votes: {}, cur: null, revealed: false, names: new Map(), tuned: new Set() };
  const storeKey = (v) => "readability-votes:" + v;
  function loadVotes(v) { try { const raw = localStorage.getItem(storeKey(v)); const o = raw ? JSON.parse(raw) : null; return o && typeof o === "object" && o.votes ? o.votes : {}; } catch (e) { return {}; } }
  function saveVotes() { try { localStorage.setItem(storeKey(S.version), JSON.stringify({ votes: S.votes })); return true; } catch (e) { return false; } }

  /* render as a chat app would: escape everything first, then turn **bold** into bold and nothing else */
  const md = (t) => esc(t).replace(/\*\*([^*\n][^*\n]*?)\*\*/g, "<strong>$1</strong>");

  async function fetchPairs(version) {
    let res;
    try { res = await fetch(CFG.dataRoot + version + "/vote-pairs.json", { cache: "no-cache" }); }
    catch (e) { return { file: null, problem: "The pairs file could not be reached." }; }
    if (res.status === 404) return { file: null, problem: null };
    if (!res.ok) return { file: null, problem: "The pairs file answered " + res.status + "." };
    let raw;
    try { raw = await res.json(); } catch (e) { return { file: null, problem: "The pairs file is not valid JSON." }; }
    if (!raw || raw.schema !== PAIRS_SCHEMA || !Array.isArray(raw.pairs)) return { file: null, problem: "The pairs file has an unknown format." };
    const pairs = raw.pairs.filter((p) => p && p.id != null && p.prompt && p.a && p.b && typeof p.a.text === "string" && typeof p.b.text === "string" && p.split !== "heldout" && p.split !== "held_out");
    return { file: Object.assign({}, raw, { pairs }), problem: null };
  }

  /* names for revealed entries: every device board and the cloud board of this version */
  async function loadNames(version, cloud) {
    S.names = new Map(); S.tuned = new Set();
    try {
      const boards = await ST.loadAllDevices(version);
      boards.concat(cloud ? [cloud] : []).forEach((b) => {
        b.entries.forEach((e) => S.names.set(e.key, { name: e.name + (e.thinkingText ? ", " + e.thinkingText : ""), line: e.line }));
        if (b.references && b.references.tuned) S.tuned.add(b.references.tuned);
      });
    } catch (e) { /* names fall back to the key */ }
  }
  const nameFor = (side) => {
    const n = side.entry && S.names.get(side.entry);
    if (n) return n.name;
    if (side.entry) { const p = String(side.entry).split("|"); return p[1] || String(side.entry); }
    return "an entry the file does not name";
  };
  const roleWords = (side) => (side.role === "tuned" || S.tuned.has(side.entry) ? "the tuned reference" : side.role === "base" ? "the base reference" : side.role === "candidate" ? "a candidate" : "");
  const isTuned = (side) => side.role === "tuned" || S.tuned.has(side.entry);

  const pairs = () => (S.file ? S.file.pairs : []);
  const votedCount = () => pairs().filter((p) => S.votes[p.id]).length;
  const nextIndex = () => pairs().findIndex((p) => !S.votes[p.id]);

  /* one-sided binomial test: could the rater be spotting the tuned reference more often than chance? */
  function recognition() {
    let n = 0, k = 0;
    pairs().forEach((p) => { const v = S.votes[p.id]; if (!v || !v.recognition || v.recognition === "unsure") return; n++; if (isTuned(p[v.recognition])) k++; });
    let pv = 0; const c = (nn, kk) => { let r = 1; for (let i = 1; i <= kk; i++) r = (r * (nn - kk + i)) / i; return r; };
    for (let i = k; i <= n; i++) pv += c(n, i) * Math.pow(0.5, n);
    return { n, k, flagged: n >= 5 && pv < 0.05 };
  }

  /* ---------- pieces ---------- */
  function progressHtml() {
    if (!S.file) {
      return `<div style="min-width:220px;max-width:280px"><p class="small"><b style="font-weight:500">No pairs published for this board yet</b></p><span class="ci-bar" aria-hidden="true" style="width:220px;margin-top:6px"><i style="left:0;width:0"></i></span><p class="xsmall muted" style="margin-top:6px">0 voted. Progress appears here once the pairs file is published.</p></div>`;
    }
    const n = pairs().length, done = votedCount(), pct = n ? Math.round((done / n) * 100) : 0;
    const planned = S.file.planned && S.file.planned > n ? ` The full set is planned at ${fmt(S.file.planned, 0)} pairs.` : "";
    return `<div style="min-width:220px;max-width:280px"><p class="small num" id="vote-progress"><b style="font-weight:500">${done} of ${n}</b> pairs voted</p><span class="ci-bar" aria-hidden="true" style="width:220px;margin-top:6px"><i style="left:0;width:${pct}%"></i></span><p class="xsmall muted" style="margin-top:6px">Saved in this browser only.${planned}</p></div>`;
  }

  const answerBox = (side, label, text, tokens, disabled) => `<div class="cut" aria-label="Answer ${label}"><div class="output-head"><b style="font-weight:500">Answer ${label}</b>${tokens != null ? `<span class="muted num">${fmt(tokens, 0)} tokens</span>` : ""}</div><div class="output${disabled ? " muted" : ""}">${text}</div></div>`;

  function buttons(disabled) {
    return `<div class="vote-actions" role="group" aria-label="Your vote">${CHOICES.map((c) => `<button type="button" class="key${c.v === "both_poor" ? " key--ghost" : ""}" data-vote="${c.v}" aria-keyshortcuts="${c.key}"${disabled ? " disabled" : ""}>${esc(c.label)} <span class="kbd-hint" aria-hidden="true">${c.key}</span></button>`).join("")}</div><p class="kbd-hint" style="margin-top:6px">Keys 1 to 4 vote. After a vote, Enter or N moves to the next pair.</p>`;
  }

  function stageHtml() {
    if (!S.file) {
      return `<div class="ask-box" aria-disabled="true"><span class="small muted">Question family, and whether the pair is a candidate against the tuned reference or two candidates</span><p class="q muted">The user's message appears here.</p></div>
        <div class="vote-grid">${answerBox(null, "A", "The first answer appears here, rendered as a chat app would show it: <strong>bold</strong> text in bold, nothing else styled. You do not see which model wrote it.", null, true)}${answerBox(null, "B", "The second answer appears here in the same style, so formatting does not decide the vote.", null, true)}</div>
        ${buttons(true)}`;
    }
    const n = pairs().length;
    if (S.cur == null || S.cur < 0) {
      return `<div class="reveal" role="status"><p><b style="font-weight:500">Every pair is voted: ${n} of ${n}.</b></p><p class="small" style="margin-top:4px">Export your votes and give the file to the results store. Votes stay in this browser until you do.</p></div>`;
    }
    const p = pairs()[S.cur], v = S.votes[p.id];
    const kind = p.kind === "candidate_vs_candidate" ? "two candidates" : "a candidate against the tuned reference";
    let after = "";
    if (S.revealed && v) {
      const recog = p.recognition_check && !v.recognition
        ? `<div class="reveal" role="group" aria-label="Recognition check" style="margin-top:var(--space-sm)"><p><b style="font-weight:500">Recognition check.</b> Before the names: which answer do you think the tuned reference wrote?</p><div class="vote-actions"><button type="button" class="key" data-recog="a">A</button><button type="button" class="key" data-recog="b">B</button><button type="button" class="key key--ghost" data-recog="unsure">I cannot tell</button></div><p class="xsmall muted" style="margin-top:6px">If you can spot it more often than chance, the vote set is flagged as not blind.</p></div>`
        : "";
      const names = !recog ? `<p class="small" style="margin-top:4px">Answer A was <b style="font-weight:500">${esc(nameFor(p.a))}</b>${roleWords(p.a) ? " (" + esc(roleWords(p.a)) + ")" : ""}. Answer B was <b style="font-weight:500">${esc(nameFor(p.b))}</b>${roleWords(p.b) ? " (" + esc(roleWords(p.b)) + ")" : ""}.</p>` : "";
      after = `<div class="reveal" role="status"><p><b style="font-weight:500">You chose: ${esc(choiceWords[v.choice])}.</b>${S.saveFailed ? " This browser would not save it, so export before you leave the page." : " Saved in this browser."}</p>${names}${!recog ? `<div class="vote-actions"><button type="button" class="key key--primary" data-vote-next="1">Next pair</button></div>` : ""}</div>${recog}`;
    }
    return `<div class="ask-box"><span class="small muted">Pair ${S.cur + 1} of ${n}${p.source ? ", " + esc(p.source) : ""}, ${esc(kind)}</span><p class="q">${esc(p.prompt)}</p></div>
      <div class="vote-grid">${answerBox(p.a, "A", md(p.a.text), p.a.tokens)}${answerBox(p.b, "B", md(p.b.text), p.b.tokens)}</div>
      ${S.revealed ? after : buttons(false)}`;
  }

  function toolsHtml() {
    const n = Object.keys(S.votes).length, rc = recognition();
    const flag = rc.n ? `<p class="small" style="margin-top:8px">${rc.flagged ? `<span class="cell-status bad">${I.warn}Flagged: not blind</span> ` : ""}Recognition checks: you named the tuned reference in ${rc.k} of ${rc.n}.${rc.flagged ? " That is more often than chance, so the store will flag this vote set." : ""}</p>` : "";
    return `<div class="toolbar" style="margin-top:var(--space-lg)"><button type="button" class="key" data-export="1"${n ? "" : ' disabled title="No votes saved in this browser for this board"'}>Export my votes${n ? ` (${n})` : ""}</button><span class="small muted">A JSON file for the results store. Nothing is sent anywhere.</span></div>${flag}`;
  }

  /* ---------- page ---------- */
  async function render(ctx) {
    const bi = ctx.boardInfo;
    const version = bi ? bi.version : null;
    S.version = version; S.revealed = false; S.saveFailed = false;
    const got = version ? await fetchPairs(version) : { file: null, problem: "No results board is published yet." };
    S.file = got.file; S.problem = got.problem;
    S.votes = version ? loadVotes(version) : {};
    if (S.file) { await loadNames(version, ctx.cloud); S.cur = nextIndex(); }
    const sample = (S.file && S.file.sample) || (bi && bi.sample);

    const absent = !S.file ? `<div class="note-planned" role="note" style="margin-bottom:var(--space-md)">${I.info}<span>${S.problem ? `<b style="font-weight:500">The vote pairs did not load.</b> ${esc(S.problem)} ` : `<b style="font-weight:500">No pairs published for this board yet.</b> `}The results store has not written <span class="mono">${esc(CFG.dataRoot + (version || "<board>") + "/vote-pairs.json")}</span> for board ${esc(version || "")}. The frame below shows how a vote works; its buttons turn on when pairs are published.</span></div>` : "";

    const html = `<div class="container" id="vote-page">
      <div class="page-head"><div><h1>Which answer is easier to read?</h1><p>Blind side-by-side votes from one rater, the benchmark's author. They check the readability judge: until the judge agrees with these votes well enough, readability shows the structure checks only. Judge how quickly you could act on the answer, not its length.${sample ? " Sample data." : ""}</p></div>
        <div id="vote-progress-box">${progressHtml()}</div></div>
      ${absent}
      <section id="vote-stage" aria-live="polite">${stageHtml()}</section>
      <div id="vote-tools">${toolsHtml()}</div>
      ${U.secHead("How the pairs are made", "", "", "method")}
      <ul class="small" style="padding-left:1.2em;max-width:72ch;display:grid;gap:6px">
        <li>Most pairs put a candidate against the tuned reference. One pair in five puts two candidates together.</li>
        <li>Pairs whose answers differ in facts are dropped before voting, so the vote is about reading, not correctness.</li>
        <li>Both answers are shown in one style, rendered as a chat app would, so formatting does not decide.</li>
        <li>Which side each answer sits on is random. The names show only after the vote.</li>
        <li>Some pairs are recognition checks: before the names, you say which answer the tuned reference wrote. If you can tell more often than chance, the votes are not blind and the set is flagged.</li>
        <li>Your votes stay in this browser, under this board version, until you export them. Held-out cases are never shown here.</li>
      </ul>
      <details class="howto" style="margin-top:var(--space-md)"><summary>${I.chev}The file this page reads</summary>
        <p class="small">The results store writes <span class="mono">${esc(CFG.dataRoot)}&lt;board&gt;/vote-pairs.json</span>. Each pair has an id, its kind (<span class="mono">candidate_vs_reference</span> or <span class="mono">candidate_vs_candidate</span>), a question family, the prompt, and answers <span class="mono">a</span> and <span class="mono">b</span>, each with its text, visible tokens and the hidden entry key and role shown after the vote. A pair may be marked <span class="mono">recognition_check</span>.</p>
        <p class="small" style="margin-top:6px">The export is <span class="mono">${esc(VOTES_SCHEMA)}</span>: the board version, the time, and one row per vote with the pair id, the choice (<span class="mono">a</span>, <span class="mono">b</span>, <span class="mono">same</span> or <span class="mono">both_poor</span>), the recognition answer if asked, and when it was cast.</p>
      </details>
    </div>`;
    return { title: "Readability votes", html };
  }

  function refresh(root) {
    const st = root.querySelector("#vote-stage"), tools = root.querySelector("#vote-tools"), prog = root.querySelector("#vote-progress-box");
    if (st) st.innerHTML = stageHtml();
    if (tools) tools.innerHTML = toolsHtml();
    if (prog) prog.innerHTML = progressHtml();
  }
  function focusFirst(root, sel) { const el = root.querySelector(sel); if (el) el.focus(); }

  function cast(root, choice) {
    if (!S.file || S.cur == null || S.cur < 0 || S.revealed) return;
    const p = pairs()[S.cur];
    S.votes[p.id] = { choice, kind: p.kind || null, voted_at: new Date().toISOString(), recognition: null };
    S.saveFailed = !saveVotes();
    S.revealed = true;
    refresh(root);
    focusFirst(root, "[data-recog], [data-vote-next]");
  }
  function recog(root, side) {
    const p = S.file && pairs()[S.cur]; if (!p || !S.votes[p.id]) return;
    S.votes[p.id].recognition = side;
    S.saveFailed = !saveVotes();
    refresh(root);
    focusFirst(root, "[data-vote-next]");
  }
  function next(root) {
    if (!S.revealed) return;
    const p = S.file && pairs()[S.cur];
    if (p && p.recognition_check && S.votes[p.id] && !S.votes[p.id].recognition) return;
    S.revealed = false; S.cur = nextIndex();
    refresh(root);
    focusFirst(root, "[data-vote]");
  }
  function exportVotes() {
    const rows = Object.entries(S.votes).map(([id, v]) => ({ pair_id: id, choice: v.choice, kind: v.kind, recognition: v.recognition, voted_at: v.voted_at }));
    if (!rows.length) return;
    const out = { schema: VOTES_SCHEMA, board_version: S.version, rater: "author", exported_at: new Date().toISOString(), pairs_generated_at: S.file ? S.file.generated_at || null : null, votes: rows };
    const blob = new Blob([JSON.stringify(out, null, 2) + "\n"], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "readability-votes-" + String(S.version).replace(/[^a-z0-9._-]+/gi, "-") + "-" + new Date().toISOString().slice(0, 10) + ".json";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  /* keys 1 to 4 vote, Enter or N moves on; wired once and active only while this page is shown */
  let keysWired = false;
  function wireKeys() {
    if (keysWired) return; keysWired = true;
    document.addEventListener("keydown", (e) => {
      const root = document.getElementById("vote-page");
      if (!root || !S.file || e.altKey || e.ctrlKey || e.metaKey) return;
      const t = e.target; if (t && (t.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName))) return;
      if (!S.revealed) { const c = CHOICES.find((x) => x.key === e.key); if (c) { e.preventDefault(); cast(root, c.v); } return; }
      if (e.key === "n" || e.key === "N" || (e.key === "Enter" && !(t && t.tagName === "BUTTON"))) { e.preventDefault(); next(root); }
    });
  }

  function after(app) {
    wireKeys();
    const root = app.querySelector("#vote-page");
    if (!root) return;
    root.addEventListener("click", (e) => {
      const v = e.target.closest("button[data-vote]"); if (v && !v.disabled) { cast(root, v.dataset.vote); return; }
      const r = e.target.closest("button[data-recog]"); if (r) { recog(root, r.dataset.recog); return; }
      if (e.target.closest("button[data-vote-next]")) { next(root); return; }
      const x = e.target.closest("button[data-export]"); if (x && !x.disabled) exportVotes();
    });
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.vote = { render, after };
})();
