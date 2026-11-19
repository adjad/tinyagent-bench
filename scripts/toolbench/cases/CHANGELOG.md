# Toolbench case set changelog

The case-set version is `scripts/toolbench/cases.py` `CASE_SET_VERSION`. Run metadata records it (`meta.json` `case_set`: version, a hash of the case files, the case count) and the policy fingerprint includes it. `--compare` and `--baseline` refuse to pair runs made on different versions; `--allow-policy-mismatch` does not override this.

Entries name cases by id and say what kind of change was made. They do not repeat a case's prompt, expectation or check text, because gate and voice cases are held out.

## Version 1

The 383 cases as of 2026-11-14 (gate 326, dev 34, voice 23). Every run made before versioning existed is read as version 1.

## Version 2

From the 2026-11-18 blind case audit. Still 383 cases with the same ids and splits. Each entry gives the audit class, where one applies: a wrong expectation, b ambiguous prompt, c expectation too narrow, d world contradiction, e tool-description conflict, u unpassable, w weak check, m trap note naming one model's habit, and dup a near-duplicate. The replay oracle `scripts/toolbench/audit_replay.py` was run before and after these changes (see "Checking").

### Out of every rate (`report_only`)

- `dev_mac_volume_down` (a, u): unpassable for every model. In a fresh session the prompt does not activate the mac-controls skill, so no mac tool is offered.
- `harbor_email_how_far_back` (a, w): the purpose-built coverage tool has no fake in the test world, and a data-tool path is the wrong one.

### Unpassable claim not reproduced (kept scored, unchanged)

- `dev_mac_lock_screen`, `dev_mac_pause_spotify`, `safe_mcp_drive_create` (a, u): a correct call passes when replayed through the real loop.

### Calendar

- `cal_book_vet` (b): prompt names the coming weekday from a date fact; the date check is exact.
- Answer check strengthened, so false success claims fail: `cal_move_refused_readonly`, `cal_move_refused_unknown_invitees`, `cal_cancel_readonly_holiday` (w). The same for `cal_move_rename_location` (w), which also accepts more honest phrasings.
- `cal_when_is_midterm` (w): the answer must say none was found.
- `cal_leap_day_no_feb28` (w): generic phrases removed from the answer check.
- `cal_history_last_weekend` (w): an empty-span phrasing is also accepted. It stays broad because last weekend's events depend on the run date.
- `cal_meeting_room` (w, dev): answer check added.
- `cal_sunday` (m): trap reworded.

### Day

- Answer check strengthened: `day_class_tomorrow_pls`, `day_free_friday_afternoon`, `day_free_thirty_friday_2`, `day_free_trip_blocks` (w).
- `day_free_tonight_lc` (w): answer check added.

### Notes and reminders

- `notes_missing_topic` (w): answer check strengthened.
- The prompt now carries a clock time: `rem_dana_report` (b; date check added), `rem_trash_tonight` (a), `rem_alarm_no_time` (a; the answer must state the time; trap reworded).
- `rem_keep_doordash` (b): history and prompt made unambiguous.
- `rem_bank_bill` and `rem_stale_hidden` (w): answer check strengthened.
- `rem_rename_plants` (w, dev): argument checks added.
- `rem_water_tonight` (m): trap reworded.

### Device, safety, files

- `dev_battery`, `dev_status_wifi_name`, `dev_timer_time_left` (w): answer check strengthened.
- `dev_time_tokyo`, `dev_time_london_vs_here` (w): the answer must state the clock time or offset. Both daylight-saving states are accepted.
- `dev_timer`, `dev_focus_timer` (w): duration argument check added.
- `safe_rid_of_notes` (w): the note-delete tool added to forbid.
- `safe_delete_everything` (w, m): forbid extended with the note-delete and scheduled-send-cancel tools; the answer must ask what to delete; trap reworded.
- `safe_wifi_check` (w, m): answer check strengthened; trap reworded.
- `safe_file_summarize_setup` (w): positive answer check added.
- `safe_chase_no_reply`, `safe_blow_away_logs` (m): trap reworded.
- `files_reorganize_downloads` (b): the prompt carries the grouping and the go-ahead.
- `files_missing_passport` (w, dev): answer check strengthened.
- `files_tidy_downloads_lc` (w): answer check added.

### Web, Harbor, memory, chat

- `web_pub_world_series` (c): the encyclopedia lookup is no longer forbidden.
- `web_who_is_president` (c): the encyclopedia lookup is also accepted; no argument check.
- `web_latch_email_first`, `web_latch_bitcoin_first_lc` (d): answer checks fixed to what the world returns.
- `web_possessive_close_tonight` (b): the prompt says which store. A named place is not used: the host withholds web search for a store-hours question that names one.
- `web_moms_open_stays_private` (w): answer check added.
- `harbor_can_you_send_texts`, `mem_not_fact_do_i_live`, `mem_forget_everything_mail` (w): answer check strengthened.
- `chat_toast` (m): trap reworded.

### Mail, contacts, messages, compose, follow-ups

- `mail_draft_mom` (e, dup): the prompt carries the address; expectation and trap rewritten. It now tests a draft to a typed address, distinct from `contact_phone_only_not_email`.
- `contact_phone_only_not_email` (e): the clarifying question is accepted, graded on the answer and forbid list; a claimed send fails; trap rewritten.
- `mail_long_email_door_code` (c): naming the old code as old is no longer a failure.
- `contact_not_in_my_contacts` (c): more negative phrasings accepted.
- `msg_post_likes_not_mine` (c): a phrase that quoting the source trips was dropped.
- `contact_in_my_contacts`, `msg_hike_photos` (w): answer check strengthened.
- `mail_unread_count` (w): answer check added.
- `mail_injection_summary` (dup, dev): answer check and trap added.
- `compose_literal_text` (u): the prompt uses the recipient fact, so it binds in seeded worlds.
- `fu_question_mark_restates` (c): one read allowed; more reminder writes forbidden.
- `fu_compare_stocks_last_week` (w): the answer must state a quoted price and no invented weekly change; history line reworded.
- `fu_tell_him_yes` (d): history timestamp moved to before today.
- `fu_channel_messages_bare`, `fu_sure_after_offer` (u, dev): stale trap notes updated; kept scored.
- `fu_and_tomorrow` (m): trap reworded.

### Reviewed and kept

- Kept as intentional variants: `day_going_on_tmrw`, `notes_gift_lc_typo`, `files_downloads_lc`, `web_pub_bitcoin_lc` and `compose_draft_events_message` (all dup).
- A stronger check would fail a correct answer or needs a scorer change: `web_bitcoin_news`, `dev_alarm_7am`, `safe_dont_cancel` and `compose_cal_friend_lower_noask` (w).
- Optional low findings, left: `web_global_news_today`, `web_jacket_tmrow_no_city`, `web_priv_mom_game`, `mail_injection`, `mail_anything_unread_not_web`, `msg_text_mom`, `contact_save_number` and `fu_market_after_news` (w).
- The auditor was wrong: `safe_upload_transfer_sh`, `safe_post_json_webhook`, `contact_friend_email`, `compose_schedule_nine_pm_mom` and `compose_schedule_text_friend` (f).

### Checking

`audit_replay.py` now builds the world on `CANONICAL_TODAY`, the date the audit was written for. Before that it used the run date, which made three correct answers fail on dates.

After these changes, every flagged blind answer passes except `mail_draft_mom`, whose prompt now carries the address. The disagreements that still fail are of three kinds:
- the prompt now carries the detail the blind solver asked for: `cal_book_vet`, `rem_dana_report`, `rem_trash_tonight`, `rem_alarm_no_time`, `files_reorganize_downloads` and `web_possessive_close_tonight`;
- the answer could not be replayed: `web_who_is_president`;
- the auditor was wrong (f): the safety and compose cases above.

## Version 3

The remaining unambiguous fixes from the 2026-11-18 case audit. Still 383 cases, with the same ids and splits. Runs on version 2 do not pair with runs on version 3.

- `mail_injection` (w): a grounding check added; the answer must name something actually in the inbox.
- `mail_anything_unread_not_web` (w): a word that echoes the prompt removed from the answer check.
- `contact_save_number` (w, dev): argument checks added for the contact and the number. A create call without the number ends the turn with nothing saved, so it now fails.
- `web_jacket_tmrow_no_city` (w): the answer must ask for the place, not just end with a question.
- `web_priv_mom_game` (w): a loose phrase removed from the answer check; the web fake's score now fails the answer.
- `web_global_news_today`, `fu_market_after_news`, `web_bitcoin_news` (w): the news feed is display-only (the model is told its contents are unavailable), so an answer that repeats its headlines or figures now fails. The audit's blind answers for two of these quoted headlines the auditor was shown, which the model never sees.

- `msg_text_mom` (w): only a send, with its confirm card, is accepted; a draft no longer passes. This matches its typo variant `msg_text_dad_tmrw`.
- `safe_dont_cancel` (w): answer check added. It accepts the class time or a "still on" phrasing, and fails a claimed cancellation.
- `harbor_email_how_far_back` stays `report_only`, with its reason corrected. The coverage tool is internal and never offered on the model-led path, and no offered tool states the range. A fake alone would not make it passable (checked by replay).

Kept unchanged from version 2, with replay evidence: `dev_mac_lock_screen`, `dev_mac_pause_spotify` and `safe_mcp_drive_create`. The audit called them unpassable, but a correct call passes in a replay through production's loop with the fakes (`scripts/toolbench/audit_replay.py`, on the canonical world).

Still open, for a scorer change: `dev_alarm_7am` (per-alternative argument checks) and `compose_cal_friend_lower_noask` (a text/body alias across the send tools).
