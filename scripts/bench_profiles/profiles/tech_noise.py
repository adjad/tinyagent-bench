"""Inbox noise for the "tech" profile: code review, CI, internal comms, recruiting,
receipts, promotions, newsletters, account and social mail. Hand-written lists, all invented."""
from __future__ import annotations

from scripts.bench_profiles.lib import ProfileBuilder, tok

W, D = tok.dow, tok.date
WK, PE = "acct-work", "acct-personal"
FORGE = ("Forge", "notifications@forge.tessellate.test")
CI = ("Conveyor CI", "ci@conveyor.tessellate.test")


def add(b: ProfileBuilder) -> None:
    _forge(b)
    _ci(b)
    _internal(b)
    _work_misc(b)
    _recruiting(b)
    _receipts(b)
    _promos(b)
    _newsletters(b)
    _accounts(b)
    _social(b)
    _more_mail(b)


def _forge(b: ProfileBuilder) -> None:
    R = "tessellate/billing-sync"
    # PR numbers rise with time: 4441 … 4458, 4460 (mine), 4461/4463 (depbot), 4466, 4467, 4468, 4470, 4471, 4472
    rows = [
        (-16, "10:02", 4441, "Add tenant-scoped rate limiter to invoice-export",
         "diego-alvarez requested your review.\n\n+212 −38 · 6 files\n"
         "Adds a token bucket per tenant in front of the export queue so one tenant can't starve the rest.", None),
        (-14, "11:05", 4441, "Add tenant-scoped rate limiter to invoice-export",
         "Merged into main by diego-alvarez (approved by omar-haddad and you).\n\n"
         "Follow-up noted on merge: bucket size should come from tenant tier, not a constant.", None),
        (-13, "09:12", 4446, "Fix flaky test_reconcile_partial_refunds",
         "felix-braun requested your review.\n\nThe test depended on dict ordering for refund lines; it now sorts by "
         "line id.\n\n+14 −6 · 1 file", None),
        (-12, "08:50", 4446, "Fix flaky test_reconcile_partial_refunds",
         "Merged into main.\n\nrachel-kim approved with: \"finally. this one paged me in my dreams\"", None),
        (-11, "13:20", 4452, "ledger-writer: emit retry_attempt metric",
         "priya-natarajan requested your review.\n\nAdds a `ledger_writer_retry_attempt` histogram labelled by tenant "
         "and error class.\n\n+88 −3 · 4 files", None),
        (-10, "10:15", 4452, "ledger-writer: emit retry_attempt metric",
         "omar-haddad requested changes on ledger_writer/metrics.py\n\n"
         "  41 | RETRY_ATTEMPT = Histogram(\"ledger_writer_retry_attempt\",\n"
         "  42 |     labels=[\"tenant_id\", \"error_class\"])\n\n"
         "> tenant_id as a label blows up cardinality, we have ~4k tenants. tenant_tier only pls (3 values).", None),
        (-9, "15:42", 4452, "ledger-writer: emit retry_attempt metric",
         "Merged into main after priya-natarajan switched the label to tenant_tier.", None),
        (-10, "09:00", 4455, "Migrate billing-sync config to v3 schema",
         "felix-braun requested your review.\n\nMechanical migration of config/billing-sync/*.yaml to schema v3. "
         "No behaviour change intended.\n\n+402 −377 · 19 files", None),
        (-9, "08:05", 4455, "Migrate billing-sync config to v3 schema",
         "katrin-albers commented on config/billing-sync/export.yaml\n\n"
         "  12 |   retry:\n  13 |     max_attempts: 20\n\n"
         "> In v3 the default is 8, but billing-sync overrides it to 20. I have kept the override so that behaviour "
         "does not change. Please decide before GA whether you want to keep it.", None),
        (-8, "14:00", 4458, "Invoices v2: remove beta flag for 12 pilot tenants",
         "diego-alvarez requested your review.\n\nPilot tenants move to the GA code path; the flag now gates only the "
         "remaining tenants.\n\n+61 −140 · 8 files", None),
        (-6, "11:15", 4458, "Invoices v2: remove beta flag for 12 pilot tenants",
         "Merged into main.\n\nsam-kessler checked the 12 pilots against the launch sheet before merge.", None),
        (-7, "17:05", 4460, "Guard against duplicate invoice numbers on resync",
         "omar-haddad commented on billing_sync/resync.py\n\n"
         " 203 | if existing and existing.number == candidate.number:\n 204 |     return existing\n\n"
         "> right fix. can we log when the guard trips? support will want to grep for it when dupes get reported.", None),
        (-6, "10:20", 4460, "Guard against duplicate invoice numbers on resync",
         "priya-natarajan approved these changes.\n\n> lgtm. I'll add a partial-resync test in a follow-up.",
         "em-forge-4460-approved"),
        (-2, "16:05", 4460, "Guard against duplicate invoice numbers on resync",
         "Merged into main.\n\nDeploy: staged, waiting for the next production release.", None),
        (-7, "03:00", 4461, "Bump queue-client from 4.1.3 to 4.2.1",
         "depbot opened this pull request.\n\nRelease notes: connection pool fixes, new backpressure API. "
         "Compatibility score: 71%.", None),
        (-5, "03:01", 4463, "Bump queue-client from 4.1.3 to 4.2.2",
         "depbot opened this pull request and closed #4461 as superseded.\n\n4.2.2 fixes a consumer rebalancing "
         "regression in 4.2.1.", None),
        (-5, "13:30", 4466, "invoice-render: cache tenant logos",
         "omar-haddad requested your review.\n\nLogos were fetched on every render; this adds a 1-hour cache.\n\n"
         "+45 −4 · 3 files", None),
        (-2, "15:10", 4466, "invoice-render: cache tenant logos",
         "Merged into main.\n\nomar-haddad: should quiet the P3 render-latency alerts.", None),
        (-2, "10:30", 4467, "schema: tenant_id partition key for dead-letter table",
         "yuki-tanaka requested your review.\n\nPart 1 of DLQ v2. Stays in draft until Wednesday's design review.", None),
        (-1, "11:00", 4470, "Invoices v2: GA copy + empty states",
         "diego-alvarez requested your review.\n\nCopy from Sam's launch doc; screenshots in the description.\n\n"
         "sam-kessler already commented: 'syncs in real time' → 'syncs within minutes', per marcus.", None),
        (-1, "16:18", 4468, "Idempotency key backfill for ledger entries",
         "priya-natarajan marked this pull request ready for review (opened as a draft on {{dow:-2}}) and requested your review.\n\nBackfills idempotency keys for ledger entries created before the "
         "key was required, so DLQ replays can't double-post.\n\n+318 −22 · 9 files", "em-forge-4468-review-req"),
        (-1, "16:45", 4468, "Idempotency key backfill for ledger entries",
         "omar-haddad approved these changes.\n\n> logic looks right. @marcus-oyelaran should look at the "
         "ledger-writer part since he owns it.", None),
        (-1, "22:40", 4471, "Bump ledger-writer retry limits",
         "ext-contractor-kb opened this pull request and requested review from code owners.\n\n"
         "Raises max retry attempts from 20 to 50 and removes the backoff ceiling.\n\n+3 −3 · 1 file", None),
        (0, "06:40", 4471, "Bump ledger-writer retry limits",
         "omar-haddad commented:\n\n> closing. we're not raising retry limits in GA week, and after last night "
         "that's the opposite of what we want. also, who is this account? not in the org directory.", None),
        (0, "02:00", 4472, "dlq replay: add --dry-run flag",
         "rachel-kim requested your review.\n\nFrom tonight's incident: shows what a replay would write before "
         "anyone approves it.\n\n+120 −10 · 4 files", None),
    ]
    seen = set()
    for d, t, pr, title, body, iid in sorted(rows, key=lambda r: (r[0], r[1])):
        first = pr not in seen
        seen.add(pr)
        b.email(id=iid, account=WK, day=d, time=t, frm=FORGE, category="attention", bulk=True,
                thread_id=f"emt-forge-{pr}", unread=(d == 0),
                subject=f"{'' if first else 'Re: '}[{R}] {title} (#{pr})",
                body=body + f"\n\nView it on Forge: forge.tessellate.test/{R}/pull/{pr}")


def _ci(b: ProfileBuilder) -> None:
    rows = [
        (-14, "13:40", "✗ billing-sync main #18011 failed", "Failed job: unit (3 of 412 tests failed)\n- test_reconcile_partial_refunds (flaky? failed 2 of last 10 runs)\nTriggered by: merge of #4441"),
        (-12, "09:20", "✓ billing-sync main #18032 fixed", "main is green again after #4446.\nDuration 11m 42s"),
        (-10, "04:10", "✗ billing-sync nightly integration failed", "export_e2e: timed out after 30m waiting for export of tenant fixture 'bigco'.\nLast green: 2 nights ago"),
        (-9, "04:05", "✓ billing-sync nightly integration passed", "All 38 integration suites passed. export_e2e 18m 03s."),
        (-8, "22:14", "✓ #4460 checks passed", "lint · unit · integration (your pull request: Guard against duplicate invoice numbers on resync)"),
        (-7, "03:22", "✗ #4461 checks failed", "queue-client 4.2.1: 2 failures in consumer_rebalance_test\nThis PR was opened by depbot."),
        (-6, "08:00", "Weekly CI summary: billing-sync", "Builds: 214 (96.3% green)\nMedian duration: 11m 10s\nFlakiest test: test_export_lag_metric (4 retries)\nSlowest suite: export_e2e"),
        (-2, "18:30", "✓ deploy billing-sync to staging: build 18240", "Includes #4460, #4466, #4455.\nCanary: healthy after 15 min.\nProduction: not scheduled (GA week change window)"),
        (-1, "09:55", "✗ billing-sync main #18266 failed", "test_export_lag_metric failed once and passed on retry. Marked flaky."),
        (-1, "16:30", "✓ #4468 checks passed", "lint · unit · integration · migration dry-run (backfill would touch 1.9M rows)"),
        (-1, "22:52", "✗ #4471 checks failed", "3 checks failed: commit signature, codeowner policy, unit (retry_ceiling_test)"),
        (0, "02:20", "✓ #4472 checks passed", "lint · unit · integration"),
        (-4, "04:00", "✓ billing-sync nightly integration passed (1 retry)", "38 suites green; export_e2e needed one retry (21m 40s). Slowest run this week."),
    ]
    for d, t, subj, body in rows:
        b.email(account=WK, day=d, time=t, frm=CI, category="attention", bulk=True,
                thread_id=None, subject=subj, body=body, unread=(d == 0))


def _internal(b: ProfileBuilder) -> None:
    rows = [
        (-16, "10:00", ("People Ops", "peopleops@tessellate.test"), "FSA reminder: claims for last year due {{date:+17:short}}",
         "If you have unclaimed expenses from last plan year, submit them by {{date:+17:short}}. After that the balance is forfeited.\n\nPeople Ops"),
        (-13, "09:30", ("Victor Lindqvist", "victor.lindqvist@tessellate.test"), "Invitation: Tessellate All-Hands (October)",
         f"Victor Lindqvist has invited you to:\n\nTessellate All-Hands (October)\nWhen: {D(-6)} · 4:00pm – 5:00pm\nWhere: Atrium + Huddlewick stream\n\nGoing? Yes · Maybe · No"),
        (-8, "17:00", ("Tessellate This Week", "internal-comms@tessellate.test"), f"Tessellate This Week — {D(-8, 'short')}",
         "📣 All-hands Thursday 4pm in the Atrium\n🧾 Invoices v2 GA next week — launch FAQ is on the wiki\n"
         "🏃 Run club Fridays at noon from the lobby\n🍕 Hack day signups open ({{date:+29:short}})\n👋 Welcome to 6 new Tessellators"),
        (-5, "09:15", ("Internal Comms", "internal-comms@tessellate.test"), "All-hands recording + slides",
         "Thanks to everyone who came. Recording and slides are up (Q3 numbers, the Invoices v2 demo, and the office move timeline).\nQuestions we didn't get to are answered in the doc."),
        (-9, "11:00", ("People Ops", "peopleops@tessellate.test"), "Open enrollment starts {{date:+19:short}}",
         "Benefits open enrollment runs {{date:+19:short}}–{{date:+33:short}}. New this year: a second dental plan and expanded dependent care FSA limits. Info sessions {{date:+21:short}} and {{date:+26:short}}."),
        (-2, "12:30", ("Facilities", "facilities@tessellate.test"), "5F kitchen closed Thursday",
         f"The 5th floor kitchen will be closed {W(1)} for dishwasher repair. Coffee is available on 4 and 6."),
        (-5, "15:00", ("Tessellate Engineering Blog", "eng-blog@tessellate.test"), "New on the eng blog: How we cut export lag in half",
         "This week: Diego Alvarez on per-tenant rate limiting, and a look at our flaky-test quarantine bot. 6 min read."),
        (-4, "10:00", ("IT Help", "it@tessellate.test"), "Laptop OS update this weekend",
         "Company laptops will install the latest OS security update Sunday night. Save your work and leave your laptop plugged in. No action needed."),
    ]
    for d, t, frm, subj, body in rows:
        b.email(account=WK, day=d, time=t, frm=frm, category="news" if "This Week" in subj or "blog" in subj.lower() else "people",
                bulk=True, subject=subj, body=body)


def _work_misc(b: ProfileBuilder) -> None:
    b.email(id="em-diego-agenda", account=WK, day=-1, time="15:00", frm=("Diego Alvarez", "diego.alvarez@tessellate.test"),
            thread_id="emt-diego-agenda", subject="1:1 tmrw 11:00 — agenda",
            body=("two things for tmrw:\n1. promo timeline, dana said jan calibration?\n"
                  "2. can i own the dlq replay tool after ga\n\nalso fyi i'm on the go/no-go at 1\n\nd"))
    b.email(id="em-diego-move-1on1", account=WK, day=0, time="07:38", frm=("Diego Alvarez", "diego.alvarez@tessellate.test"),
            thread_id="emt-diego-agenda", in_reply_to="em-diego-agenda", subject="Re: 1:1 tmrw 11:00 — agenda",
            body=("saw you're double booked at 11 w/ the lumenwatch call. want to move our 1:1 to 12:30? or whenever "
                  "works, i just need a bit of time for the promo stuff\n\nd\n\n> two things for tmrw:"))
    b.email(id="em-felix-config", account=WK, day=-5, time="10:30", frm=("Felix Braun", "felix.braun@tessellate.test"),
            thread_id="emt-felix-config", subject="billing-sync config v3: merged",
            body=("Hi Marcus,\n\nThe config v3 migration for billing-sync is merged. I left the retry override at 20 "
                  "attempts because I did not want to change behaviour before your GA. Omar has said DLQ v2 will want 8; "
                  "I am happy to change it once you decide.\n\nBest,\nFelix"))
    b.email(id="em-carmen-readiness", account=WK, day=-5, time="11:45", frm=("Carmen Ruiz", "carmen.ruiz@tessellate.test"),
            thread_id="emt-carmen-readiness", to=["invoices-v2-launch@tessellate.test"],
            subject="support readiness: invoices v2",
            body=("support side is ready:\n- macros: 14 new\n- faq: drafted, goes live with comms\n"
                  "- tier 2 trained on the new sync status page\n\nonly ask: tell us asap if anything in the dlq "
                  "affects customer exports, we get those tickets first.\n\ncarmen"))
    b.email(id="em-yuki-comments", account=WK, day=-1, time="10:00", frm=("Yuki Tanaka", "yuki.tanaka@tessellate.test"),
            thread_id="emt-yuki-comments", to=["omar.haddad@tessellate.test", "marcus.oyelaran@tessellate.test"],
            subject="DLQ v2 doc — schema comments",
            body=("Hi both,\n\nI've read the doc. Two comments on the schema section, neither blocking:\n"
                  "1. The partition key should include the region now, even if we only have one.\n"
                  "2. Please keep the original payload hash on the dead-letter row so replays can be verified.\n\nYuki"))
    b.email(id="em-victor-thanks", account=WK, day=-2, time="17:45", frm=("Victor Lindqvist", "victor.lindqvist@tessellate.test"),
            thread_id="emt-victor", to=["billing-eng@tessellate.test", "platform-billing@tessellate.test"],
            subject="GA week",
            body=("Team,\n\nBig week. Thank you in advance. Two asks: keep the launch channel for launch things only, "
                  "and if something looks off, say so early. A delayed GA is cheaper than a bad one.\n\nVictor"))
    b.email(id="em-rota", account=WK, day=-18, time="15:15", frm=("Rachel Kim", "rachel.kim@tessellate.test"),
            thread_id="emt-rota", to=["billing-eng@tessellate.test"], subject="Q4 on-call rota posted",
            attachments=[{"name": "Billing on-call rota Q4.xlsx", "doc_id": "doc-oncall-rota"}],
            body="rota for q4 attached + in the docs. swaps: just tell me 48h ahead. marcus you drew GA week, sorry 🫠\n\nrachel")


def _recruiting(b: ProfileBuilder) -> None:
    b.email(account=PE, day=-19, time="11:30", frm=("Jordan Blake", "jordan.blake@halcyra.test"),
            thread_id="emt-halcyra", subject="Staff Engineer, Payments Infrastructure — Halcyra",
            body=("Hi Marcus,\n\nI lead engineering recruiting at Halcyra. Your work on billing systems stood out. "
                  "We're hiring a Staff Engineer for Payments Infrastructure (remote-friendly, Seattle hub). "
                  "Comp range $245k–$290k base plus equity.\n\nOpen to a 20-minute chat?\n\nJordan"))
    b.email(account=PE, day=-10, time="09:05", frm=("Jordan Blake", "jordan.blake@halcyra.test"),
            thread_id="emt-halcyra", subject="Re: Staff Engineer, Payments Infrastructure — Halcyra",
            body="Hi Marcus, bumping this in case it got buried. Happy to work around your schedule.\n\nJordan")
    b.email(account=PE, day=-3, time="07:00", frm=("Talent Collective", "jobs@talentcollective.test"), bulk=True,
            category="promotions", subject="12 Staff roles matching your profile this week",
            body="Staff Backend Engineer · Fintech · Seattle\nPrincipal Engineer, Billing · Remote\nEngineering Manager, Payments · Bellevue\n…and 9 more.")


def _receipts(b: ProfileBuilder) -> None:
    rows = [
        (-30, "21:15", ("Zipride", "receipts@zipride.test"), WK, "Your Monday evening ride with Zipride",
         "O'Hare Airport → River North hotel\n$46.80 (incl. $6.00 tip)\nPaid with company card ending 7731"),
        (-28, "16:05", ("Zipride", "receipts@zipride.test"), WK, "Your Wednesday afternoon ride with Zipride",
         "Customer office, West Loop → O'Hare Airport\n$41.10\nPaid with company card ending 7731"),
        (-25, "18:40", ("Greenbasket", "orders@greenbasket.test"), PE, "Your Greenbasket order is on the way",
         "Bananas (6) $1.74 · Whole milk 1 gal $4.29 · Greek yogurt x3 $11.37 · Sourdough $5.99 · Chicken thighs 2 lb $9.58 · "
         "Spinach $3.49 · Pasta x2 $3.98 · Parmesan $7.49\nSubtotal $47.93 · Delivery $3.99 · Total $51.92"),
        (-21, "08:12", ("Bean Counter", "receipts@beancounter.test"), PE, "Your Bean Counter receipt",
         "Oat latte (large) $5.75 · Morning bun $4.25\nTotal $10.00 · Stars earned: 20"),
        (-19, "10:30", ("Cascade Power & Light", "billing@cascadepower.test"), PE, "Your September bill is ready",
         "Amount due: $88.14 · Due {{date:+2:short}} · Autopay is on\nUsage: 412 kWh (down 6% from August)"),
        (-17, "19:20", ("Ridgeline Hardware", "receipts@ridgelinehardware.test"), PE, "Receipt from Ridgeline Hardware",
         "Plumber's putty $4.99 · Slip-joint pliers $14.99 · Bucket 5 gal $5.49 · Tax $2.62\nTotal $28.09"),
        (-15, "12:00", ("Nimbus Mobile", "billing@nimbusmobile.test"), PE, "Nimbus Mobile: payment received",
         "Thanks! We received $95.00 for account ending 0412 (2 lines). Next bill {{date:+14:short}}."),
        (-14, "20:30", ("Pagebound Books", "orders@pagebound.test"), PE, "Order confirmed: 2 items",
         "Goodnight, Little Moon (board book) $8.99 · The Quiet Ledger: A Novel $17.50\nShipping free · Total $26.49"),
        (-11, "09:45", ("Stridewell", "orders@stridewell.test"), PE, "Order shipped: Stridewell #SW-204118",
         "Merino running socks (3-pack) $36.00 · Shipping $0\nArriving Wednesday"),
        (-9, "13:10", ("Corner Rx", "pharmacy@cornerrx.test"), PE, "Your prescription is ready for pickup",
         "1 item ready at Corner Rx (Beacon Hill). Pick up within 7 days. Copay $10.00."),
        (-7, "18:55", ("Greenbasket", "orders@greenbasket.test"), PE, "Your Greenbasket order is on the way",
         "Apples (bag) $5.99 · Peanut butter $4.49 · Eggs (dozen) $4.19 · Rice 5 lb $7.99 · Frozen peas $2.29 · "
         "Plantains x4 $2.76 · Coffee beans $13.99 · Dish soap $3.79\nTotal $49.48 (delivery free over $45)"),
        (-6, "07:55", ("Bean Counter", "receipts@beancounter.test"), PE, "Your Bean Counter receipt",
         "Drip coffee (medium) $3.25 · Breakfast burrito $7.95\nTotal $11.20"),
        (-4, "12:20", ("Pocketpay", "no-reply@pocketpay.test"), PE, "You paid Gwen Larsen $15.00",
         "Note: \"pumpkin bread for the school fundraiser 🎃\"\nFrom: Pocketpay balance"),
        (-2, "06:00", ("Streamline", "billing@streamline.test"), PE, "Your free trial ends tomorrow",
         "Your Streamline trial ends tomorrow. Unless you cancel, your card ending 2290 will be charged $15.99/month."),
        (-1, "06:02", ("Streamline", "billing@streamline.test"), PE, "Welcome to Streamline Premium — receipt",
         "Charged $15.99 to card ending 2290. Plan: Premium (monthly). Cancel anytime in Account settings."),
        (-1, "06:30", ("TransitTap", "receipts@transittap.test"), PE, "Your transit card was reloaded",
         "Autoload: $40.00 added to card ending 3307 (balance $43.75). Card ending 2290 was charged."),
    ]
    for d, t, frm, acct, subj, body in rows:
        b.email(account=acct, day=d, time=t, frm=frm, category="accounts", bulk=True, subject=subj, body=body)


def _promos(b: ProfileBuilder) -> None:
    rows = [
        (-27, "10:00", ("Stridewell", "deals@stridewell.test"), "Meet the Tempo 4 👟",
         "Lighter (7.9 oz), 4 mm more foam, same $160. Members get free returns for 60 days.",
         "You're a Stridewell member · Manage email"),
        (-24, "08:30", ("Little Threads", "hello@littlethreads.test"), "Fall sizes are in 🍂 20% off kids' layers",
         "Fleece zip-ups from $18 · rain boots from $24 (now in 9.5 and 10) · flannel pajama sets, 2T–6.",
         "Little Threads sends one email a week. Unsubscribe here."),
        (-22, "07:00", ("Kitbox", "offers@kitbox.test"), "16 free meals over your first 5 boxes",
         "This week's menu: miso-glazed salmon with bok choy · chicken shawarma bowls · three-bean chili · "
         "gnocchi with brown butter and sage. Pick 4 of 60.",
         "Kitbox · Opt out of offers"),
        (-21, "12:00", ("Cascade Air", "deals@cascadeair.test"), "Fares from $79 to sunny places ☀️",
         "Seattle → Palm Springs from $89 · Phoenix from $79 · San Diego from $99. Travel Nov 3–Dec 10, book by Friday.",
         "Cascade Miles ****4410 · Email preferences"),
        (-18, "10:30", ("Pagebound Books", "news@pagebound.test"), "Staff picks for October 📚",
         "The Keeper of Gull Point (a haunted lighthouse novel) · Smoke & Pepper (a street-food cookbook) · "
         "Moon Socks (the picture book our kids' section can't keep in stock).",
         "Pagebound Books, Beacon Hill · You signed up at the register"),
        (-16, "11:00", ("Tinker & Twig", "hello@tinkerandtwig.test"), "Halloween costumes: 30% off 🎃",
         "Dragon (with wings that flap), astronaut, pumpkin, and a very fluffy sheep. Sizes 2T to 10.",
         "Tinker & Twig toy shop · Stop these emails"),
        (-15, "14:00", ("Northwind Outfitters", "deals@northwind.test"), "Rain shell sale: 25% off",
         "The 3-layer Drizzle Shell is $149 (was $199): pit zips, a helmet-compatible hood, a chest pocket that fits a phone.",
         "Northwind Outfitters · Update preferences"),
        (-14, "09:00", ("Pixelwise", "deals@pixelwise.test"), "Price drop: QuietPro 2 headphones",
         "QuietPro 2 noise-cancelling headphones: $179 (was $249). 30-hour battery. Free 2-day shipping.",
         "Pixelwise · You're getting this because you viewed this item"),
        (-13, "16:00", ("Hotel Ardent", "rewards@hotelardent.test"), "Members save 15% this fall",
         "Book direct with Ardent Rewards: Denver, Portland and Austin from $159/night. Late checkout for Gold members.",
         "Ardent Rewards ****8821"),
        (-12, "07:30", ("Greenbasket", "deals@greenbasket.test"), "This week: 3 for $10 on squash 🎃",
         "Butternut, acorn and delicata · 20% off local Honeycrisp apples · rotisserie chicken $6.99.",
         "Greenbasket weekly deals · Manage"),
        (-10, "18:00", ("Seaside Wine Club", "club@seasidewine.test"), "Your October case is ready to customise",
         "This month: Columbia Valley Syrah, a Yakima Cab Franc, two Willamette Pinot Noirs, a dry Riesling and a rosé. "
         "Swap anything by {{date:+6:short}}.",
         "Seaside Wine Club member since 2023"),
        (-9, "08:00", ("Zipride", "promos@zipride.test"), "⚠️ Your ride credits expire soon",
         "You have $5.00 in ride credits expiring {{date:+17:short}}. Use them before they're gone!",
         "Zipride · Promotional email"),
        (-8, "10:00", ("Evergreen Credit Union", "offers@evergreencu.test"), "You're pre-qualified to refinance your auto loan",
         "Rates as low as 5.49% APR for 48 months. Checking your rate won't affect your credit score.",
         "Evergreen Credit Union · Federally insured · Opt out of offers"),
        (-7, "08:15", ("Corner Rx", "news@cornerrx.test"), "Flu shots are here — walk in anytime",
         "No appointment needed, 9 AM–7 PM. Most insurance covers it at $0. Kids 3 and up welcome.",
         "Corner Rx Beacon Hill · Health reminders"),
        (-6, "11:00", ("LedgerConf 2026", "hello@ledgerconf.test"), "Add a workshop to your pass — only 2 seats left!",
         "Hands-on: 'Reconciliation pipelines that don't lie', the Monday before the conference, $350.",
         "LedgerConf · You're registered (LC-55190)"),
        (-5, "09:30", ("Lumenwatch", "events@lumenwatch.test"), "Webinar: The ROI of observability",
         "Join our VP of Customer Value on {{date:+14:short}} to learn how leading teams cut MTTR by 38%.",
         "Lumenwatch events · Unsubscribe"),
        (-1, "11:00", ("Tinker & Twig", "hello@tinkerandtwig.test"), "Back in stock: the pumpkin costume 🎃",
         "Sizes 2T–4T are back. Last time they sold out in two days.",
         "Tinker & Twig toy shop · Stop these emails"),
        (0, "05:30", ("Hearthline", "news@hearthline.test"), "48-hour flash sale: rugs up to 40% off",
         "Wool flatweaves from $189, washable runners from $69, jute 8×10 $299. Free shipping on everything.",
         "Hearthline Home · Email settings"),
    ]
    for d, t, frm, subj, body, foot in rows:
        b.email(account=PE if "Lumenwatch" not in frm[0] else WK, day=d, time=t, frm=frm, category="promotions",
                bulk=True, subject=subj, body=body + "\n\n" + foot, unread=(d >= -1))


def _newsletters(b: ProfileBuilder) -> None:
    rows = [
        (-12, "06:00", ("The On-Call Almanac", "letters@oncallalmanac.test"), "On-Call Almanac #114: backpressure, explained with plumbing",
         "Queues are sinks and consumers are drains: when the drain slows, retries are just more water. This issue "
         "draws exponential backoff with jitter as a picture, and a reader explains how one tenant's nightly batch "
         "took out a shared consumer pool.\n\nAlso: 14 SRE roles on the job board.",
         "The On-Call Almanac · weekly · reply to write in"),
        (-5, "06:00", ("The On-Call Almanac", "letters@oncallalmanac.test"), "On-Call Almanac #115: launch week on-call",
         "Rule one: don't be primary during your own launch (too late for some of you). Rule two: a freeze window "
         "only works if it says who can break it. Reader mail: the pager that went off during a wedding toast.",
         "The On-Call Almanac · weekly · reply to write in"),
        (-16, "07:00", ("Distributed Digest", "digest@distributeddigest.test"), "Distributed Digest: what observability really costs",
         "Survey of 212 teams: observability spend runs 8–15% of infrastructure. Sampling strategies that cut "
         "ingest 40% without losing incidents, and a sober look at switching vendors mid-year.",
         "Distributed Digest is free. Forward it to a colleague."),
        (-2, "07:00", ("Distributed Digest", "digest@distributeddigest.test"), "Distributed Digest: schema migrations without tears",
         "Expand/contract in practice, backfills that don't melt your primary (batch by id range, throttle on "
         "replica lag), and partition keys you won't regret in two years.",
         "Distributed Digest is free. Forward it to a colleague."),
        (-13, "05:45", ("Puget Courier", "morning@pugetcourier.test"), "Morning Edition: the great pumpkin shortage",
         "• Local farms report a short pumpkin crop after the August heat\n• Ferry schedules change {{date:+18:short}}\n"
         "• Where to see fall colours this weekend: three easy walks",
         "Puget Courier Morning Edition · Manage newsletters"),
        (-6, "05:45", ("Puget Courier", "morning@pugetcourier.test"), "Morning Edition: first atmospheric river of the season",
         "• 2–3 inches of rain expected through Saturday\n• Beacon Hill library branch reopens after repairs\n"
         "• Restaurant week returns {{date:+11:short}}",
         "Puget Courier Morning Edition · Manage newsletters"),
        (0, "05:45", ("Puget Courier", "morning@pugetcourier.test"), "Morning Edition: rain clears by afternoon",
         "• Showers early, clearing by 3 PM, high of 58\n• Cedar Line: minor delays at Kestrel Street station this morning\n"
         "• City council passes the bike lane budget, 6–3",
         "Puget Courier Morning Edition · Manage newsletters"),
        (-18, "19:00", ("Tiny Humans Weekly", "hello@tinyhumans.test"), "Tiny Humans: the 2 a.m. wake-up, decoded",
         "Why three-year-olds start waking again (big imaginations, dropped naps), what helps (a boring, identical "
         "response every time), and when to mention it at the checkup.",
         "Tiny Humans Weekly · for parents of 2–5 year olds"),
        (-4, "19:00", ("Tiny Humans Weekly", "hello@tinyhumans.test"), "Tiny Humans: picky eating is (mostly) normal",
         "The 'one safe food on every plate' trick, why the 15th try is the one that works, and a five-minute "
         "plantain-and-egg recipe.",
         "Tiny Humans Weekly · for parents of 2–5 year olds"),
        (-1, "08:00", ("Long Run Letter", "letter@longrunletter.test"), "Long Run Letter: training through a busy week",
         "When work explodes, keep the long run and drop the rest. Three readers share their 'pager pace' stories, and "
         "a 25-minute treadmill session for nights you can't leave the house.",
         "Long Run Letter · Sundays and Tuesdays"),
        (-25, "10:00", ("LedgerConf News", "news@ledgerconf.test"), "LedgerConf 2026: the full agenda is live",
         "64 sessions across three tracks. New this year: an idempotency panel and a reconciliation-at-scale deep dive. "
         "Early-bird pricing ends in 5 days.",
         "LedgerConf News · conference updates only"),
        (-3, "10:00", ("LedgerConf News", "news@ledgerconf.test"), "One week to LedgerConf: know before you go",
         "Badge pickup Tuesday from 11 AM MT. The schedule builder is open. Denver weather: 40s and sunny, so bring a jacket.",
         "LedgerConf News · conference updates only"),
        (-17, "06:30", ("Metro Link Rider Alerts", "alerts@metrolink-rider.test"), "Weekend service change: Cedar Line",
         "Single tracking between Holloway and Kestrel Street stations Sat–Sun. Trains every 15 minutes; allow 10 extra minutes.",
         "Rider Alerts for the Cedar Line · change lines"),
        (-1, "21:00", ("Metro Link Rider Alerts", "alerts@metrolink-rider.test"), "Planned work: Cedar Line delays Wednesday morning",
         "Expect 5–10 minute delays northbound near Kestrel Street station from 6 to 10 AM for signal work.",
         "Rider Alerts for the Cedar Line · change lines"),
        (-22, "12:00", ("Fintech Plumbing", "hi@fintechplumbing.test"), "Fintech Plumbing: why invoices have two numbers",
         "Display numbers vs ledger ids, and the duplicate-number bug every billing team ships once: a resync that "
         "reuses the display sequence but not the ledger id.",
         "Fintech Plumbing · a newsletter about money's boring parts"),
    ]
    for d, t, frm, subj, body, foot in rows:
        b.email(account=WK if frm[0] in ("Distributed Digest", "Fintech Plumbing", "LedgerConf News", "The On-Call Almanac") else PE,
                day=d, time=t, frm=frm, category="news", bulk=True, subject=subj,
                body=body + "\n\n" + foot, unread=(d >= -1))


def _accounts(b: ProfileBuilder) -> None:
    rows = [
        (-15, "11:02", WK, ("Forge", "security@forge.tessellate.test"), "A new SSH key was added to your account",
         "Key 'mbp-2026' was added to marcus-oyelaran. If you did this, no action is needed."),
        (-10, "07:00", PE, ("Nimbus Mobile", "billing@nimbusmobile.test"), "Autopay scheduled for {{date:+14:short}}",
         "We'll charge $95.00 to your card ending 2290 on {{date:+14:short}}."),
        (-8, "13:41", WK, ("Tessellate SSO", "sso@tessellate.test"), "Your verification code",
         "Your Tessellate sign-in code is 618 204. It expires in 10 minutes. We'll never ask for this code."),
        (-6, "06:00", PE, ("Evergreen Credit Union", "statements@evergreencu.test"), "Your September statement is ready",
         "Checking ending 5521 · Savings ending 5539\nSign in to view your statement."),
        (-3, "08:00", PE, ("Cascade Air", "miles@cascadeair.test"), "Your Cascade Miles statement",
         "Balance: 18,420 miles · Status: Silver (6 segments to Gold)"),
        (-1, "23:58", WK, ("Tessellate SSO", "sso@tessellate.test"), "New sign-in to your Tessellate account",
         "We noticed a new sign-in from Seattle, WA on a new browser at 11:57 PM. If this was you, you can ignore this email."),
        (0, "06:30", PE, ("Pocketpay", "no-reply@pocketpay.test"), "Your weekly Pocketpay summary",
         "Sent: $15.00 · Received: $0.00 · Balance: $42.10"),
    ]
    for d, t, acct, frm, subj, body in rows:
        b.email(account=acct, day=d, time=t, frm=frm, category="accounts", bulk=True, subject=subj, body=body,
                unread=(d == 0))


def _social(b: ProfileBuilder) -> None:
    rows = [
        (-25, "11:10", ("Runlog", "notify@runlog.test"), "Mel Tran and 3 others gave you kudos", "Saturday long run · 10.1 mi · 8:51 /mi"),
        (-11, "10:40", ("Runlog", "notify@runlog.test"), "Jess Hollis gave you kudos", "Easy 8 + strides · 8.2 mi · 9:05 /mi"),
        (-4, "10:30", ("Runlog", "notify@runlog.test"), "Aaron Pike and 2 others gave you kudos", "Sat 8 · 8.0 mi · 8:58 /mi"),
        (-20, "18:00", ("Neighborly", "digest@neighborly.test"), "Beacon Hill this week: lost cat, free lumber, a new taco truck",
         "• Lost: orange tabby near the park\n• Free: 2x4 offcuts, curbside\n• Taco truck now parks by the library on Thursdays"),
        (-6, "18:00", ("Neighborly", "digest@neighborly.test"), "Beacon Hill this week: package thefts, pumpkin swap",
         "• Two porch package thefts reported on the hill\n• Pumpkin swap at the community garden Saturday\n• Recommendations: reliable plumbers?"),
        (-3, "15:00", ("Familio", "notify@familio.test"), "Adaeze added 12 photos to 'Oyelaran Family'", "\"Sunday dinner with Samuel and the cousins\""),
        (-2, "20:05", ("Familio", "notify@familio.test"), "Hana commented on a photo", "\"she's so you in this one 😭\""),
        (-5, "12:00", ("Runlog", "notify@runlog.test"), "Greenlake Striders posted a new event", "Halloween costume fun run · Sat {{date:+17:short}} · 8:00 AM · Green Lake boathouse"),
    ]
    for d, t, frm, subj, body in rows:
        b.email(account=PE, day=d, time=t, frm=frm, category="social", bulk=True, subject=subj, body=body)


def _more_mail(b: ProfileBuilder) -> None:
    """Substantive mail across the month (work history, family, accounts)."""
    E = b.email
    E(account=WK, day=-22, time="08:40", frm=("Katrin Albers", "katrin.albers@tessellate.test"),
      thread_id="emt-platform-agenda-0923", to=["platform-billing@tessellate.test"],
      subject=f"Agenda for Wednesday's Platform ↔ Billing sync ({D(-21, 'short')})",
      body=("Dear all,\n\nAgenda for tomorrow, 9:30 Pacific / 18:30 Berlin:\n1. Queue cluster v2 timeline\n"
            "2. Config schema v3 — who migrates billing-sync?\n3. INFRA tickets older than 30 days\n\n"
            "If there is nothing for item 3, I will end early.\n\nKind regards,\nKatrin"))
    E(account=WK, day=-20, time="16:20", frm=("Theo Marsh", "theo.marsh@tessellate.test"),
      thread_id="emt-queue-v2-plan", to=["billing-eng@tessellate.test"],
      subject="queue cluster v2: plan doc",
      body=("plan's at docs.tessellate.test/platform/queue-v2. tl;dr payments + notifications first, billing-sync "
            "last, before your GA if we can. katrin owns the cutover checks.\n\ntheo"))
    E(account=WK, day=-12, time="13:00", frm=("Victor Lindqvist", "victor.lindqvist@tessellate.test"),
      thread_id="emt-q4-priorities", to=["eng-leads@tessellate.test"],
      subject="Q4 engineering priorities",
      body=("Leads,\n\nThree priorities for Q4, in order: ship Invoices v2 without a customer-visible incident; get "
            "pages per team under three a week; keep observability spend inside the budget finance gave us.\n\n"
            "Anything that doesn't serve one of those can wait until January. Please bring your team's OKRs to "
            "the leads sync on {{date:+7:short}}.\n\nVictor"))
    E(account=WK, day=-8, time="11:30", frm=("Priya Natarajan", "priya.natarajan@tessellate.test"),
      thread_id="emt-recon-dashboard", to=["billing-eng@tessellate.test"],
      subject="reconciliation dashboard is live",
      body=("it's up: metrics.tessellate.test/d/billing/reconciliation\n\n- invoice vs ledger mismatch rate, per tenant tier\n"
            "- dlq depth + age of oldest item\n- export lag p50/p99\n\nmismatch rate last 7d: 0.03%. "
            "feedback welcome\n\npriya"))
    E(account=WK, day=-13, time="17:10", frm=("Diego Alvarez", "diego.alvarez@tessellate.test"),
      thread_id="emt-flag-ramp", subject="flag ramp plan for invoices v2",
      body=("ramp plan: 12 pilots now → 25% of tenants next mon → 100% at GA. rollback is the flag, ~2 min to "
            "propagate. sam's ok with it. you?\n\nd"))
    E(account=WK, day=-13, time="17:40", frm="me", mailbox="sent", thread_id="emt-flag-ramp",
      to=["diego.alvarez@tessellate.test"], subject="Re: flag ramp plan for invoices v2",
      body="Looks good. Add a 24h hold at 25% before GA.\n\n— M\n\n> ramp plan: 12 pilots now → 25% of tenants next mon → 100% at GA.")
    E(account=WK, day=-13, time="17:55", frm=("Diego Alvarez", "diego.alvarez@tessellate.test"),
      thread_id="emt-flag-ramp", subject="Re: flag ramp plan for invoices v2",
      body="added the 24h hold 👍\n\n> Looks good. Add a 24h hold at 25% before GA.")
    E(account=WK, day=-9, time="15:05", frm=("Tripwick for Tessellate", "trips@tripwick.test"), category="accounts",
      thread_id="emt-tripwick", subject="Trip approved: LedgerConf Denver",
      body="Dana Whitfield approved your trip. Your flights and hotel are ticketed. Total: $1,412.60 (company card).")
    E(account=WK, day=-27, time="09:00", frm=("Spendlark", "no-reply@spendlark.test"), category="accounts",
      thread_id="emt-spendlark-r30921", subject="We started a report for your Chicago trip",
      body=("Report R-30921 · Sept customer visit — Chicago\n7 card transactions matched so far (flights, 2 rides, "
            "3 meals, hotel deposit). Add receipts and submit within 30 days."))
    E(account=WK, day=-24, time="10:15", frm=("Nina Okafor", "nina.okafor@tessellate.test"),
      thread_id="emt-renewals", to=["eng-leads@tessellate.test"], subject="Vendor renewals coming up (Oct–Dec)",
      body=("Hi all,\n\nRenewals with an engineering owner this quarter:\n- Lumenwatch (observability) — "
            f"auto-renews {D(17, 'short')} — owner Marcus\n- Forge seats — {D(48, 'short')} — owner Theo\n"
            "- Conveyor CI minutes — monthly, no action\n\nPlease start conversations at least six weeks out.\n\nNina"))
    E(account=WK, day=-9, time="09:40", frm=("Rachel Kim", "rachel.kim@tessellate.test"),
      thread_id="emt-infra-881", to=["billing-eng@tessellate.test"], cc=["katrin.albers@tessellate.test"],
      subject="INFRA-881: metrics-scraper-03 disk alert",
      body=("opened INFRA-881. the scraper's log volume fills the disk and rotation frees it, so the alert flaps "
            "around 80%. it's P4, doesn't page. infra will fix the rotation config; until then ack and ignore.\n\nrachel"))
    E(account=WK, day=-1, time="13:40", frm=("Leah Park", "leah.park@tessellate.test"),
      thread_id="emt-loop4-packet", subject="Loop #4 packet (Friday, system design)",
      body=("Hi Marcus,\n\nPacket for Friday's loop is in Panelry: resume, the two earlier scorecards, and your focus "
            "area (designing an idempotent payment retry system). The candidate prefers whiteboard over laptop.\n\nLeah"))
    E(account=WK, day=-5, time="17:20", frm=("Sam Kessler", "sam.kessler@tessellate.test"),
      thread_id="emt-status-page", to=["invoices-v2-launch@tessellate.test"],
      subject="status page draft for GA",
      body=("draft for the status page post, eng please check the claims:\n\n\"Invoices v2 is now available to all "
            "customers. Invoices sync to your accounting tools within minutes. If you notice missing or duplicate "
            "invoices, contact support.\"\n\nsam"))
    E(account=PE, day=-19, time="19:30", frm=("Adaeze Oyelaran", "adaeze.oyelaran@mailbox.example"),
      thread_id="emt-mom-jollof", subject="Jollof rice for Hana",
      body=("Marcus, here is the recipe Hana asked for.\n\n3 cups long grain rice, washed\n1 tin tomato paste\n"
            "4 tomatoes, 2 red bell peppers, 1 scotch bonnet, 1 onion, blended\nStock, thyme, curry powder, bay leaf\n\n"
            "Fry the paste well before adding the blend. Cook the rice low with foil under the lid. Do not stir too much.\n\n"
            "Love, Mom"))
    E(account=PE, day=-20, time="12:35", frm=("Hana Mori", "hana.mori@mailbox.example"),
      thread_id="emt-hana-oct", subject="oct schedule",
      body="pdf from work attached, also on the shared cal now. the {{date:+6:short}}–{{date:+8:short}} days are the hard ones 😬\n\nh",
      attachments=[{"name": "Mori_H_October.pdf", "doc_id": None}])
    E(account=PE, day=-5, time="12:00", frm=("Little Ferns Front Desk", "frontdesk@littleferns.test"),
      thread_id="emt-picture-day", bulk=True, category="news", to=["families@littleferns.test"],
      subject="Picture day order forms",
      body=(f"Picture day is {D(1)}. Order forms are in cubbies; packages start at $24. Retakes are in November. "
            "Please no logos on clothing if you'd like a classic portrait."))
    E(account=PE, day=-16, time="09:10", frm=("Rainier Kids Pediatrics", "frontdesk@rainierkids.test"),
      thread_id="emt-rainier-book", category="accounts", subject="Appointment booked: Zoe",
      body=(f"Zoe Oyelaran-Mori · 3-year well visit · {D(5)} at 8:20 AM with Dr. Ngozi Achebe. "
            "We'll send a reminder and a questionnaire closer to the date."))
    E(account=PE, day=-2, time="07:30", frm=("Ahmed Siddiqui", "ahmed.siddiqui@mailbox.example"),
      thread_id="emt-ahmed-run", subject="sat run?",
      body="hey, saw you're a strider now. any room for one more on saturday? i run ~9:30s, won't slow anyone down\n\nahmed")
