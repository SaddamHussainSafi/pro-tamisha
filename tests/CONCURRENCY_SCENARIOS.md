# Concurrency / idempotency harness design

Use real PostgreSQL `pytest` testcontainers or isolated DB; **do not** substitute SQLite. Spawn 10–50 concurrent API clients with synchronized start barrier. All use one test tenant and synthetic products. Assertions query both invoices and immutable stock movements, not just API response.

A. Same request/key concurrent 10: assert one issued invoice, one set of stock movements, 10 responses either same result or safely pending/409 with later replay resolving same result. Request hash mismatch under same key -> 409, never mutate.
B. Two distinct keys each ask for 6 boxes from 10 available: at most one succeeds, balance >=0, failed request leaves no issued invoice.
C. 100 network retries after server commits but before response: one posting key for each business event.
D. Two workers process same `outbox_event`: external email/Drive effects deduped or marked reconciliation-required on uncertain provider result; never resend invoice uncontrolled.
E. GRN double-post under retries: one +23 accepted; movement + balance consistency.
F. Crash injection between invoice-line creation and stock movement: rollback all; same idempotency key can retry after appropriate cleanup.
G. Backorders simultaneously fulfilled: `sum(invoice_line.billed_qty)` <= original line ordered - cancelled across correct normalized units.

Tests measure cache balance = sum movements per (lot,unit) and compare at each run end. Store seed and run metadata; use repeatable random seeds.
