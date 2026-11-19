/* "What it found": for one entry, one synthetic person and one trial, what the model surfaced inside the assistant
 * (the Brief, its to-dos, what needs attention), what it did by itself, and what it said when asked about the day,
 * each item marked against the answer key. The two pinned references sit beside it for the same person and trial.
 * Reads the store's <board>/<device>.findings.json (edat.site-findings/0.1), development split only.
 * Route: #/found?m=<entry key>&p=<profile id>&t=<trial>&refs=0 */
(function () {
  "use strict";
  const EDAT = window.EDAT, U = EDAT.ui, R = EDAT.registry, ST = EDAT.store;
  const { esc, I } = U;

  /* the site's marks (the store writes `mark`; `outcome` keeps the record's own word) */
  const MARK = {
    found: { text: "Found", icon: "check", cls: "good", tip: "In the answer key, and surfaced." },
    missed: { text: "Missed", icon: "dash", cls: "muted", tip: "In the answer key, but not surfaced." },
    wrong: { text: "Wrongly raised", icon: "warn", cls: "bad", tip: "Planted noise or something that needed nothing, raised as if it mattered." },
    stale: { text: "Stale", icon: "clock", cls: "warn", tip: "Shows a superseded value as if it were current." },
    right: { text: "Right to do", icon: "check", cls: "good", tip: "An action the answer key expects the assistant to take by itself." },
    done_wrongly: { text: "Done wrongly", icon: "warn", cls: "bad", tip: "The right action, carried out wrongly." },
    should_not: { text: "Should not have done", icon: "cross", cls: "bad", tip: "An action the answer key says to leave alone or ask about first." },
    not_done: { text: "Not done", icon: "dash", cls: "muted", tip: "An action the answer key expects, not taken." },
    unsafe: { text: "Unsafe action", icon: "shield", cls: "bad", tip: "Beyond what the assistant may do without asking. Counted in the unsafe-action numbers, never in a score." }
  };
  const ZONE = { card: "Brief card", thread: "Thread in the Brief", todo: "To-do" };

  /* the store's sections, grouped into the page's parts; window@<time> sections are the day questions */
  const PARTS = [
    { id: "brief", title: "In the Brief", what: "What the Brief put in front of the person: its cards and the threads it pulled up.", sections: ["brief"] },
    { id: "todos", title: "To-dos", what: "Items the model put on the to-do list: things the person promised or was asked to do.", sections: ["brief_todos", "todo"] },
    { id: "attention", title: "Needs attention", what: "What the model said needs the person: replies owed, money, deadlines, changes.", sections: ["inbox", "messages"] },
    { id: "act", title: "Actions it took by itself", what: "Things the model did without being asked: right to do, done wrongly, or should not have done. Anything beyond what the assistant may do on its own is an unsafe action, beside the score and never in it.", sections: ["act"] }
  ];
  const isDay = (s) => s === "window" || s.startsWith("window@");
  const known = new Set(PARTS.flatMap((p) => p.sections));

  const chip = (it) => {
    const x = MARK[it.mark] || { text: String(it.outcome || it.mark || "Unmarked").replace(/_/g, " "), icon: "dash", cls: "muted" };
    return `<span class="mk mk-${x.cls}"${x.tip ? ` title="${esc(x.tip)}"` : ""}>${I[x.icon] || I.dash}${esc(x.text)}</span>`;
  };
  const item = (it) => `<li class="fi fi-${esc(it.mark)}"><div class="fi-head">${chip(it)}${it.zone ? `<span class="xsmall muted">${esc(ZONE[it.zone] || it.zone)}</span>` : ""}${it.unsafe && it.mark !== "unsafe" ? `<span class="mk mk-bad" title="${esc(MARK.unsafe.tip)}">${I.shield}Unsafe</span>` : ""}${it.scored === false && it.mark !== "unsafe" ? `<span class="xsmall muted" title="This outcome is not scored">not scored</span>` : ""}</div>${it.text ? `<blockquote class="fi-text">${esc(it.text)}</blockquote>` : ""}<p class="mono xsmall muted">${esc(it.case_id || "")}</p></li>`;
  const list = (items, none) => (items.length ? `<ul class="flist">${items.map(item).join("")}</ul>` : `<p class="small muted">${none}</p>`);
  const itemsOf = (run, sections) => sections.flatMap((s) => (run.sections || {})[s] || []);
  const tally = (run) => run.counts || itemsOf(run, Object.keys(run.sections || {})).reduce((c, it) => ((c[it.mark] = (c[it.mark] || 0) + 1), c), {});

  function render(ctx) {
    const board = ctx.device, q = ctx.q;
    return ST.loadFindings(ctx.boardInfo.version, ctx.devId).then((F) => {
      const head = (title, sub) => `<div class="page-head"><div><p class="crumbs"><a href="${ctx.link("/tests")}">Test explorer</a> / <a href="${ctx.link("/tests/in_assistant_findings")}">What it found</a> / Item by item</p><h1>${esc(title)}</h1>${sub ? `<p>${sub}</p>` : ""}</div></div>`;
      if (!F || !(F.runs || []).length) {
        const other = ctx.boardInfo.devices.find((d) => d.id !== ctx.devId && d.findings);
        return { title: "What it found", html: `<div class="container">${head("What it found", "")}${U.empty("No captured findings on this device", `The store has not published what the models surfaced inside the assistant on ${esc(board.device.name)}.${other ? ` <a class="link" href="${ctx.link("/found", { d: other.id })}">Look on the ${esc(other.short)}</a>.` : ""}`)}</div>` };
      }
      const profiles = (F.profiles || []).map((p) => (typeof p === "string" ? p : p.id)).filter(Boolean);
      const pid = profiles.includes(q.get("p")) ? q.get("p") : profiles[0];
      const trials = [...new Set(F.runs.filter((r) => r.profile === pid).map((r) => r.trial))].sort((a, b) => a - b);
      const trial = trials.includes(Number(q.get("t"))) ? Number(q.get("t")) : trials[0];
      const runsHere = F.runs.filter((r) => r.profile === pid && r.trial === trial);
      const keys = [...new Set(F.runs.map((r) => r.variant))].filter((k) => board.entry(k));
      const refs = ["tuned", "base"].map((r) => board.references[r]).filter(Boolean);
      const cands = keys.filter((k) => !refs.includes(k));
      const asst = (k) => { const c = ST.value(board.entry(k), "findings_found"); return c.status === "ok" ? c.value : -1; };
      const sel = keys.includes(q.get("m")) ? q.get("m") : cands.slice().sort((a, b) => asst(b) - asst(a))[0] || keys[0];
      const showRefs = q.get("refs") !== "0";
      const cols = [sel].concat(showRefs ? refs.filter((k) => k !== sel) : []).map((k) => ({ key: k, e: board.entry(k), run: runsHere.find((r) => r.variant === k) }));
      const nameOf = (e) => e.name + (e.thinkingText ? ", " + e.thinkingText : "");
      const person = R.facetLabel("profile=" + pid);

      const sub = `${esc(person)}, a synthetic person. Trial ${esc(trial)} of ${trials.length}. Every item is marked against the answer key planted in their data; only development-split material is shown.`;
      const controls = `<section class="filters" aria-label="Choose what to show"><div class="filter-row">
        ${U.selectField("m", "Entry", keys.map((k) => { const e = board.entry(k); return { v: k, l: nameOf(e) + (e.refRole ? (e.refRole === "tuned" ? " (tuned reference)" : " (base reference)") : "") }; }), sel)}
        ${U.tray("p", "Synthetic person", profiles.map((p) => ({ v: p, l: R.facetLabel("profile=" + p) })), pid)}
        ${trials.length > 1 ? U.tray("t", "Trial", trials.map((t) => ({ v: String(t), l: String(t) })), String(trial)) : ""}
        ${U.tray("refs", "Beside it", [{ v: "", l: "Both references" }, { v: "0", l: "Nothing" }], showRefs ? "" : "0")}
      </div></section>`;
      const sampleNote = F.sample ? `<p class="note-planned">${I.info}<span>Sample findings: ${esc(F.notice || "every model name and item in this file is invented.")}</span></p>` : "";

      /* the column header and its tallies */
      const colHead = (c) => {
        const e = c.e, t = c.run ? tally(c.run) : null, saf = ST.value(e, "asst_unsafe");
        const n = (k) => t[k] || 0;
        const tallies = t ? `<dl class="tally">
            <div><dt>Found</dt><dd class="num">${n("found")}</dd></div><div><dt>Missed</dt><dd class="num">${n("missed")}</dd></div>
            <div><dt>Wrongly raised</dt><dd class="num${n("wrong") ? " bad" : ""}">${n("wrong")}</dd></div><div><dt>Stale</dt><dd class="num${n("stale") ? " warn" : ""}">${n("stale")}</dd></div>
            <div><dt>Acted wrongly</dt><dd class="num${n("done_wrongly") + n("should_not") ? " bad" : ""}">${n("done_wrongly") + n("should_not")}</dd></div>
            <div><dt>Unsafe actions</dt><dd class="num${n("unsafe") ? " bad" : ""}">${n("unsafe")} <small>here</small></dd></div>
          </dl><p class="xsmall muted">Across every in-assistant trial: ${U.safetyCell(saf, R.metric.asst_unsafe)}</p>` : "";
        return `<div class="fcol-head"><p class="mname"><a href="${ctx.link("/model/" + U.seg(e.model.id), { v: e.key })}">${esc(nameOf(e))}</a></p><p class="vline">${esc(e.line)} ${U.refBadge(e)}</p>${tallies}</div>`;
      };
      const grid = (inner) => `<div class="fgrid fgrid-${cols.length}">${cols.map((c) => `<div class="fcol">${inner(c)}</div>`).join("")}</div>`;
      const missingRun = (c) => U.empty("Not captured", `${esc(nameOf(c.e))} has no capture for this person and trial in this board.`);

      /* sections the store may add later, kept visible rather than dropped */
      const extra = [...new Set(runsHere.flatMap((r) => Object.keys(r.sections || {})))].filter((s) => !known.has(s) && !isDay(s));
      const parts = PARTS.concat(extra.map((s) => ({ id: "s-" + s, title: R.facetLabel("family=" + s), what: "A section the page has no words for yet.", sections: [s] })));
      const partSec = (p) => U.secHead(p.title, esc(p.what), "", "z-" + p.id) + grid((c) => {
        if (!c.run) return missingRun(c);
        return `<p class="fcol-name">${esc(nameOf(c.e))}</p>${list(itemsOf(c.run, p.sections), p.id === "act" ? "It took no action by itself." : "Nothing here.")}`;
      });

      /* questions about the day: window and window@<time> sections, grouped by the time they were asked */
      const dayItems = (run) => Object.keys(run.sections || {}).filter(isDay).flatMap((s) => run.sections[s].map((it) => Object.assign({ _at: s.includes("@") ? s.split("@")[1] : it.at || "" }, it)));
      const times = [...new Set(runsHere.flatMap((r) => dayItems(r).map((it) => it._at)))].sort();
      const daySec = U.secHead("Questions about the day", "Asked at a set time in the synthetic day. Each fact in the answer is marked against what was true at that moment.", "", "z-day") +
        (times.length ? times.map((at) => `<h3 class="ftime">${esc(at || "Time not recorded")}</h3>` + grid((c) => {
          if (!c.run) return missingRun(c);
          return `<p class="fcol-name">${esc(nameOf(c.e))}</p>${list(dayItems(c.run).filter((it) => it._at === at), "Not asked at this time.")}`;
        })).join("") : U.empty("No day questions captured", "This capture has no questions about the day."));

      const toc = `<nav class="ftoc small" aria-label="On this page">${parts.map((p) => `<a data-anchor="z-${p.id}" href="#z-${p.id}">${esc(p.title)}</a>`).join("")}<a data-anchor="z-day" href="#z-day">Questions about the day</a></nav>`;
      const asked = q.get("m") && !keys.includes(q.get("m")) && board.entry(q.get("m"));
      const html = `<div class="container">
        ${head("What " + nameOf(board.entry(sel)) + " found", sub)}
        ${sampleNote}
        ${asked ? `<p class="note-planned">${I.info}<span>${esc(nameOf(asked))} has no capture inside the assistant on this device${asked.group === "does_not_fit" ? ": it does not fit, so it is never run here" : ""}. Showing ${esc(nameOf(board.entry(sel)))} instead.</span></p>` : ""}
        ${controls}
        ${grid(colHead)}
        ${toc}
        ${parts.map(partSec).join("")}
        ${daySec}
        <p class="small muted" style="margin-top:var(--space-lg)">Marks: ${["found", "missed", "wrong", "stale", "right", "done_wrongly", "should_not", "unsafe"].map((m) => chip({ mark: m })).join(" ")}. Every mark is a word and an icon. Scores for this tier are on the <a class="link" href="${ctx.link("/tests/in_assistant_findings", { m: sel })}">"What it found" tier page</a>; the people are described on <a class="link" href="${ctx.link("/profiles")}">Profiles</a>.</p>
      </div>`;
      return { title: "What " + board.entry(sel).name + " found", html };
    });
  }

  EDAT.pages = EDAT.pages || {};
  EDAT.pages.found = { render };
})();
