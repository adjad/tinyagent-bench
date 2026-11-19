Ledger-writer retry queue (DLQ v2)
Author: Omar Haddad · Reviewers: Marcus Oyelaran, Yuki Tanaka, Priya Natarajan

Problem
Failed ledger writes go to a single shared dead-letter queue. Replays are manual, unordered and all-or-nothing. During retry storms one large tenant can fill the queue and delay everyone.

Proposal
1. Per-tenant DLQ partitions keyed by tenant_id.
2. Exponential backoff with jitter, max 8 attempts (currently 20, linear).
3. Replay tool with --dry-run and an approval step for > 500 jobs.
4. Idempotency keys required on every ledger entry before replay (depends on #4468 backfill).

Open questions
- Ordering for credit notes vs invoices on replay
- Schema change sign-off (Yuki)
- Owner for replay approvals after GA

Non-goals
- Cross-region replication
