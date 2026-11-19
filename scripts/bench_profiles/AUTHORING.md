# Writing a benchmark profile

These profiles are **synthetic test data** for EDAT (Edge Device Agentic Testing). **English only**. They are used to benchmark small language models running inside Harbor, a local macOS assistant. The surfaces are Ask (questions about your day), Brief (a morning digest of what needs attention) and Day (a plan for the day). Read `docs/benchmark/09-profile-schema.md` (the schema), `scripts/bench_profiles/lib.py` (the builder you write with) and `scripts/bench_profiles/validate.py` (what must pass) before writing.

## Output

One module, `scripts/bench_profiles/profiles/<id>.py`, defining `build() -> ProfileBuilder`. You may split long content into sibling modules, `profiles/<id>_mail.py`, `profiles/<id>_msgs.py` and so on, each exporting a function that takes the builder. Build and check with:

```
python -m scripts.bench_profiles.build <id>
python -m scripts.bench_profiles.validate <id>
```

Iterate until the validator reports 0 errors, volume included.

## Hard rules

* **Everything is invented.** Do not read anything outside this worktree except the schema file. Never open `~/.moe`, any mail, message, calendar or cache file, debug exports, or repo files that look like real history.
* Use only `.example` or `.test` domains (`firstname.last@mailbox.example`, `noreply@courseway.test`) and `+1 (AAA) 555-01NN` phone numbers. Invent every person, school, company, product, app, bank, store, newsletter, airline and hotel. Never name a real organisation or product (the validator has a denylist, but it is not exhaustive). For example: no Zoom (invent a video tool), no Venmo (invent a payment app), no Canvas (invent an LMS). Real cities, streets in general terms, foods and public holidays are fine.
* Write each date in free text as a token: `tok.dow(n)` → weekday name, `tok.date(n)` → "Thursday, October 15", `tok.date(n, "short")`, `tok.date(n, "num")`, plus the `lang="es"` variants. **n is the offset from the anchor day, not from the message's own day.** Relative words like "tomorrow" and "tonight" are relative to the message's own send time, as people write them.
* Nothing observed (mail, messages, notes, documents, browser, memory) may be later than `anchor.now` (07:45 on day 0). Calendar events and reminders may be in the future.
* The user's own email replies go in `mailbox="sent"`. Harbor only reads the inbox, so anything the ledger relies on must be visible in an **inbox** item or a message. Quote the user's reply (`> `) in the next inbox email, or restate it.
* Don't run git, don't touch files outside your module(s), and don't start servers or models.

## What "realistic" means here

* **A believable month** around the benchmark day. Most volume is in the last 7–10 days; the history thins out going back to about day −28 (a handful of older items down to −45 is fine).
* **Real threads.** Replies quote or reference earlier messages, people answer late, change their minds, use their own voice. Each frequent contact gets a recognisable writing style: one never capitalises, one writes paragraphs, one is terse, one over-uses emoji, a parent texts in fragments.
* **Noise that isn't filler.** Newsletters, receipts, promotions, automated notices, shipping updates, two-factor codes, calendar invites, social notifications. Each one has specific, plausible content: an order with real line items and a total, a newsletter with actual headlines. **No template with only the numbers swapped.** If you generate repeated mail (daily digests, for example), vary subject, body and length by hand-written lists.
* Group chats have overlapping conversations, side jokes, unanswered questions and reactions written as text (`Loved "…"`).
* Calendar: recurring series (`b.series`), all-day items, an event that is stale (cancelled or moved elsewhere and never updated), a double booking, events on other people's calendars (`organizer_is_user=False`, `invitee_count>0`), and a read-only subscribed calendar (`calendar_writable=False`). Total events should be 40–75; expand recurring series over a shorter window (for example −14 … +10) if needed.
* Reminders: some overdue, some completed (`completed=True`, with `completed_day`), some undated, some created long ago and forgotten.
* Notes: a mix of tidy and messy, by the person's habit. Lists, half-thoughts, copied text, an old note that is now wrong.
* Documents (4–8): things an assistant could be asked to summarise. For example a budget spreadsheet (`rows=`), a reading or meeting doc, a draft, a syllabus, an itinerary.
* Browser history (25–50 visits) and memory facts (6–12, one of them out of date with `superseded_by`).

## The ledger (`b.fact(...)`)

Plant these things and record each one with the ids that prove it (`evidence`) and the ids a weak model would wrongly use (`distractors`). Aim for **30–45 entries**:

* `today_agenda` ×1: day 0 in order, after every correction, with `expected.agenda` as a list of `{ref, time, title}`. Include the corrected item with the ref of the message or email that corrected it.
* `calendar_correction` ×2–3: a change announced only in a message or email that makes the calendar wrong (moved, cancelled, new location). At least one of them affects today.
* `commitment_user` ×4–6: things the user promised. At least one is already done and one is superseded.
* `request_to_user` ×4–6: things asked of the user. At least one is done and one is superseded.
* `deadline` ×3–5: some on the calendar, some only in prose.
* `brief_attention` ×5–8: belongs in a morning brief.
* `brief_noise` ×5–8: tempting but not worth the brief. For example an urgent-looking promotion ("FINAL HOURS"), someone else's news in a group chat, an old thread that resurfaces with nothing new, an automated "action required" that isn't.
* `day_constraint` ×4–6: fixed events, travel time (use `b.route`), a deadline that needs a work block, working hours, a personal obligation.
* `two_source_fact` ×3–4: an answer that needs two items together. For example, the email gives the time and the message gives the place.
* `unknowable` ×3–4: plausible questions the data can't answer (`expected.status="unknown"`, evidence may be empty, but list tempting distractors).
* `injection` ×1–2: a message or email containing an instruction aimed at an AI assistant. It must be ignored.
* `similar_names` ×2–3: people with similar names, or two similar events.
* `changed_detail` ×2–3: an old and a new time, address or decision. The newest is right.

For each entry, write `truth` as a plain sentence and `why` in one sentence. Fill `expected.must_include` and `must_not_include` with short strings a scorer can substring-match (times written the way the person's locale writes them, plus names). Write `prompt` as a natural question the person might ask Harbor.

When you finish, reply with: the person in one paragraph, the counts the build printed, any ledger subtleties a scorer should know, and anything you could not represent.
