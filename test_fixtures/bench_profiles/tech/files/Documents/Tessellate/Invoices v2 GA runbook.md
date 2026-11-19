# Invoices v2 — GA runbook

Owner: Marcus Oyelaran (eng), Sam Kessler (product)
Last updated Oct 12 (GA time change)

## Timeline (Thursday, October 15)
- 07:30 PT Platform: billing-sync workers move to new queue cluster (Theo/Katrin)
- 08:00 PT Platform check with billing (Katrin) — TBC
- 12:00 PT Support coverage starts (Carmen's team)
- **13:00 PT GA: flag flip to 100% of tenants** (was 10:00)
- 13:30 PT Customer comms + status page
- 14:30 PT Bridge closes if green

## Go/no-go criteria
- Export lag p99 < 5 min for 24h
- ledger-writer 5xx < 0.5%
- DLQ empty or every item triaged
- No open P1/P2

## Rollback
1. Flip `invoices_v2_ga` flag off (takes ~2 min to propagate)
2. Pause invoice-export consumer
3. Re-enable v1 render path
4. Post in #invoices-v2-launch, page Sam

## Sign-off
Eng readiness sign-off goes in the sign-off form (not chat), due Wednesday 12:00 PT, approver Dana Whitfield.
