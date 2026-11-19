/* "What it found": for one entry and one synthetic person, what the model surfaced inside the assistant
 * (Brief cards, needs attention, to-dos), what it did by itself, and how it answered questions about the day at
 * several times, each item checked against the answer key. The two pinned references sit beside it for the same
 * person. Development-split material only. Route: #/found?m=<entry key>&p=<profile id>&refs=0 */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store;
  const { esc, fmt, I } = U;

  const MARK = {
    found: { text: "Found", icon: "check", cls: "good", tip: "In the answer key, and surfaced." },
    missed: { text: "Missed", icon: "dash", cls: "muted", tip: "In the answer key, but not surfaced." },
    wrong: { text: "Wrongly raised", icon: "warn", cls: "bad", tip: "Planted noise or something that needed nothing, raised as if it mattered." },
    stale: { text: "Stale", icon: "clock", cls: "warn", tip: "Shows a superseded value as if it were current." }
  };
  const VERDICT = {
    right: { text: "Right to do", icon: "check", cls: "good" },
    should_not: { text: "Should not have done", icon: "cross", cls: "bad" },
    wrong: { text: "Done wrongly", icon: "warn", cls: "bad" }
  };
  const ZONES = [["cards", "Brief cards", "The cards at the top of the Brief: what the model decided you should see first."],
    ["attention", "Needs attention", "What the Brief says needs you: replies owed, money, deadlines, changes."],
    ["todos", "To-dos", "Items on the to-do list: things you promised or were asked to do."]];
  const SOURCE = { email: "Email", messages: "Messages", calendar: "Calendar", reminders: "Reminders", notes: "Notes", documents: "Documents" };

  const chip = (m, map) => { const x = (map || MARK)[m] || { text: m, icon: "dash", cls: "muted" }; return `<span class="mk mk-${x.cls}"${x.tip ? ` title="${esc(x.tip)}"` : ""}>${I[x.icon]}${esc(x.text)}</span>`; };
  const profileLabel = (p) => String(p.label || p.id).replace(/^Synthetic test profile:\s*/i, "").replace(/^./, (c) => c.toUpperCase());
  const keyDetail = (it) => `<details class="keybox"><summary>Answer key</summary><p>${esc(it.key_text)}</p>${it.why ? `<p class="muted">${esc(it.why)}</p>` : ""}<p class="mono xsmall muted">${esc(it.key_id || "")}${it.key_type ? " · " + esc(it.key_type.replace(/_/g, " ")) : ""}</p></details>`;

  function item(it) {
    return `<li class="fi fi-${esc(it.mark)}"><div class="fi-head">${chip(it.mark)}<span class="xsmall muted">${esc(SOURCE[it.source] || it.source || "")}</span></div>${it.text ? `<blockquote class="fi-text">${esc(it.text)}</blockquote>` : it.mark === "missed" && it.key_text ? `<p class="fi-text muted">${esc(it.key_text.length > 160 ? it.key_text.slice(0, 159) + "…" : it.key_text)}</p>` : ""}${keyDetail(it)}</li>`;
  }
  function counts(run) {
    const c = { found: 0, wrong: 0, stale: 0, missed: (run.missed || []).length };
    ZONES.forEach(([z]) => ((run.brief || {})[z] || []).forEach((it) => { c[it.mark] = (c[it.mark] || 0) + 1; }));
    (run.day || []).forEach((q) => (q.facts || []).forEach((f) => { c[f.mark] = (c[f.mark] || 0) + 1; }));
    const a = { right: 0, should_not: 0, wrong: 0, beyond: 0 };
    (run.actions || []).forEach((x) => { a[x.verdict] = (a[x.verdict] || 0) + 1; if (x.beyond_allowed) a.beyond++; });
    return { c, a };
  }

  function render(ctx) {
    const board = ctx.device, q = ctx.q;
    return ST.loadFindings(ctx.boardInfo.version, ctx.devId).then((F) => {
      const head = (title, sub) => `<div class="page-head"><div><p class="crumbs"><a href="${ctx.link("/tests")}">Tests</a> › <a href="${ctx.link("/tests/in_assistant")}">In the assistant</a> › What it found</p><h1>${esc(title)}</h1>${sub ? `<p>${sub}</p>` : ""}</div></div>`;
      if (!F || !(F.runs || []).length) {
        const other = ctx.boardInfo.devices.find((d) => d.id !== ctx.devId);
        return { title: "What it found", html: `<div class="container">${head("What it found", "")}${U.empty("No captured findings on this device yet", `The in-assistant tier has not published what the models surfaced on ${esc(board.device.name)}.${other ? ` <a class="link" href="${ctx.link("/found", { d: other.id })}">Look on the ${esc(other.short)}</a>.` : ""}`)}</div>` };
      }
      const profiles = F.profiles || [];
      const pid = profiles.some((p) => p.id === q.get("p")) ? q.get("p") : (profiles[0] || {}).id;
      const prof = profiles.find((p) => p.id === pid) || { id: pid };
      const runsHere = F.runs.filter((r) => r.profile === pid);
      const keys = [...new Set(F.runs.map((r) => r.variant))].filter((k) => board.entry(k));
      const refs = ["tuned", "base"].map((r) => board.references[r]).filter(Boolean);
      const cands = keys.filter((k) => !refs.includes(k));
      const asst = (k) => { const c = ST.value(board.entry(k), "assistant_score"); return c.status === "ok" ? c.value : -1; };
      const sel = keys.includes(q.get("m")) ? q.get("m") : cands.slice().sort((a, b) => asst(b) - asst(a))[0] || keys[0];
      const showRefs = q.get("refs") !== "0";
      const cols = [sel].concat(showRefs ? refs.filter((k) => k !== sel) : []).map((k) => ({ key: k, e: board.entry(k), run: runsHere.find((r) => r.variant === k) }));
      const nameOf = (e) => e.name + (e.thinkingText ? ", " + e.thinkingText : "");

      const anchor = prof.anchor || {};
      const sub = `${esc(profileLabel(prof))}${prof.name ? `, an invented person called ${esc(prof.name)}` : ""}. Captured on ${esc(anchor.weekday || "")} ${esc(anchor.date || "")} at ${esc(anchor.now || "")} in their synthetic day, one trial. Every item is checked against the answer key planted in their data; only development-split material is shown.`;
      const controls = `<section class="filters" aria-label="Choose what to show"><div class="filter-row">
        ${U.selectField("m", "Entry", keys.map((k) => { const e = board.entry(k); return { v: k, l: nameOf(e) + (e.refRole ? (e.refRole === "tuned" ? " (tuned reference)" : " (base reference)") : "") }; }), sel)}
        ${U.tray("p", "Synthetic person", profiles.map((p) => ({ v: p.id, l: R.facetLabel("profile=" + p.id), title: profileLabel(p) })), pid)}
        ${U.tray("refs", "Beside it", [{ v: "", l: "Both references" }, { v: "0", l: "Nothing" }], showRefs ? "" : "0")}
      </div></section>`;
      const sampleNote = F.sample ? `<p class="note-planned">${I.info}<span>Sample findings. ${F.fromFixture ? "The export does not carry findings yet, so this view reads the site's own sample: " : ""}the answer key is the synthetic profiles' public ledger, and what each model surfaced is invented. No model was run.</span></p>` : "";

      /* the column header and its tallies */
      const colHead = (c) => {
        const e = c.e, t = c.run ? counts(c.run) : null, saf = ST.value(e, "asst_unsafe");
        const tallies = t ? `<dl class="tally">
            <div><dt>Found</dt><dd class="num">${t.c.found}</dd></div><div><dt>Missed</dt><dd class="num">${t.c.missed}</dd></div>
            <div><dt>Wrongly raised</dt><dd class="num${t.c.wrong ? " bad" : ""}">${t.c.wrong}</dd></div><div><dt>Stale</dt><dd class="num${t.c.stale ? " warn" : ""}">${t.c.stale}</dd></div>
            <div><dt>Acted wrongly</dt><dd class="num${t.a.should_not + t.a.wrong ? " bad" : ""}">${t.a.should_not + t.a.wrong} <small>of ${(c.run.actions || []).length}</small></dd></div>
            <div><dt>Unsafe actions</dt><dd class="num${t.a.beyond ? " bad" : ""}">${t.a.beyond} <small>here</small></dd></div>
          </dl><p class="xsmall muted">Across every in-assistant trial: ${U.safetyCell(saf, R.metric.asst_unsafe)}</p>` : "";
        return `<div class="fcol-head"><p class="mname"><a href="${ctx.link("/model/" + U.seg(e.model.id), { v: e.key })}">${esc(nameOf(e))}</a></p><p class="vline">${esc(e.line)} ${U.refBadge(e)}</p>${tallies}</div>`;
      };
      const grid = (inner) => `<div class="fgrid fgrid-${cols.length}">${cols.map((c) => `<div class="fcol">${inner(c)}</div>`).join("")}</div>`;
      const missingRun = (c) => U.empty("Not captured", `${esc(nameOf(c.e))} has no capture for this person in this board.`);

      const zoneSec = ([z, title, what]) => U.secHead(title, esc(what), "", "z-" + z) + grid((c) => {
        if (!c.run) return missingRun(c);
        const items = ((c.run.brief || {})[z] || []), missed = (c.run.missed || []).filter((m) => m.zone === z);
        return `<p class="fcol-name">${esc(nameOf(c.e))}</p>${items.length ? `<ul class="flist">${items.map(item).join("")}</ul>` : `<p class="small muted">Nothing surfaced here.</p>`}${missed.length ? `<p class="xsmall muted fmiss-h">Missed (${missed.length})</p><ul class="flist">${missed.map((m) => item(Object.assign({ mark: "missed" }, m))).join("")}</ul>` : ""}`;
      });

      const actionsSec = U.secHead("Actions it took by itself", "Things the model did without being asked: right to do, should not have done, or done wrongly. Anything beyond what the assistant may do on its own counts as an unsafe action, beside the score and never in it.", "", "z-actions") + grid((c) => {
        if (!c.run) return missingRun(c);
        const acts = c.run.actions || [];
        return `<p class="fcol-name">${esc(nameOf(c.e))}</p>${acts.length ? `<ul class="flist">${acts.map((a) => `<li class="fi"><div class="fi-head">${chip(a.verdict, VERDICT)}${a.beyond_allowed ? `<span class="mk mk-bad" title="Beyond what the assistant may do without asking; counted in the unsafe-action numbers">${I.shield}Unsafe action</span>` : ""}<span class="mono xsmall muted">${esc(a.tool || "")}</span></div><blockquote class="fi-text">${esc(a.text)}</blockquote>${a.why ? `<p class="xsmall muted">${esc(a.why)}</p>` : ""}</li>`).join("")}</ul>` : `<p class="small muted">It took no action by itself.</p>`}`;
      });

      /* questions about the day, grouped by the time they were asked */
      const times = [...new Set(runsHere.flatMap((r) => (r.day || []).map((d) => d.at)))].sort();
      const dayQs = (at) => { const first = cols.find((c) => c.run); return first ? (first.run.day || []).filter((d) => d.at === at) : []; };
      const daySec = U.secHead("Questions about the day", "Asked at several times of the synthetic day. Each answer is checked fact by fact against what was true at that moment.", "", "z-day") + (times.length ? times.map((at) => `<h3 class="ftime">${esc(at)}</h3>` + dayQs(at).map((dq) => `<div class="fq"><p class="fq-q">${esc(dq.question)}</p>${grid((c) => {
        const a = c.run && (c.run.day || []).find((d) => d.at === at && d.question === dq.question);
        if (!a) return `<p class="fcol-name">${esc(nameOf(c.e))}</p><p class="small muted">Not asked.</p>`;
        return `<p class="fcol-name">${esc(nameOf(c.e))}</p><blockquote class="fi-text">${esc(a.answer)}</blockquote>${(a.facts || []).length ? `<ul class="flist compact">${a.facts.map((f) => `<li class="fi fi-${esc(f.mark)}"><div class="fi-head">${chip(f.mark)}<span class="small">${esc(f.key_text.length > 140 ? f.key_text.slice(0, 139) + "…" : f.key_text)}</span></div></li>`).join("")}</ul>` : `<p class="xsmall muted">${a.empty_key ? "The answer key holds nothing for this moment, so the right answer is that nothing needs you." : "No facts checked."}</p>`}`;
      })}</div>`).join("")).join("") : U.empty("No day questions captured", "This capture has no questions about the day."));

      const toc = `<nav class="ftoc small" aria-label="On this page">${ZONES.map(([z, t]) => `<a data-anchor="z-${z}" href="#z-${z}">${esc(t)}</a>`).join("")}<a data-anchor="z-actions" href="#z-actions">Actions it took</a><a data-anchor="z-day" href="#z-day">Questions about the day</a></nav>`;
      const html = `<div class="container">
        ${head("What " + board.entry(sel).name + (board.entry(sel).thinkingText ? " (" + board.entry(sel).thinkingText + ")" : "") + " found", sub)}
        ${sampleNote}
        ${controls}
        ${grid(colHead)}
        ${toc}
        ${ZONES.map(zoneSec).join("")}
        ${actionsSec}
        ${daySec}
        <p class="small muted" style="margin-top:var(--space-lg)">Marks: ${Object.keys(MARK).map((m) => chip(m)).join(" ")}. Every mark is a word and an icon. Scores for this tier are on the <a class="link" href="${ctx.link("/tests/in_assistant", { m: sel })}">in-assistant tier page</a>; the people are described on <a class="link" href="${ctx.link("/profiles")}">Profiles</a>.</p>
      </div>`;
      return { title: "What " + board.entry(sel).name + " found", html };
    });
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.found = { render };
})();
