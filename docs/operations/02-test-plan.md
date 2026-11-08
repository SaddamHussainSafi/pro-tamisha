# Test strategy and operational acceptance

## Units
Money as Decimal, line tax/rounding, quantity normalization, 7-box/205-lb catch weight, customer rate precedence, version effective timestamps, ambiguous OCR suggestions, backorder `NO` behavior.

## DB integration (real PostgreSQL, never SQLite for transaction tests)
- `issue_invoice` with same idempotency key 100x concurrent -> 1 issued document, exact set of stock moves, no row duplication.
- Two different invoices competing for one lot's remaining 10 boxes -> never sell >10 unless owner-approved adjustment workflow.
- 15 original -> 10 first final invoice -> pending 5 -> second 5 -> total 15; repeat issue of first pick returns original invoice.
- Post GRN 23 accepted from 25 declared -> 23 stock, two damaged segregated; retry 20x -> still +23.
- Rollback exception after movement insert -> no invoice/movement/partial state persisted.
- Manual payment 700 of 1250 -> 550 AR; verify role denial and replay protections.
- Pricing v2 change does not modify historical invoice snapshot/PDF.
- Cached stock balance computed from ledger matches all lots after 100 randomized stock events.

## UI/E2E
- Sales operator keyboard enters multiple variable-weight lines with case-size suggestions but can edit actuals.
- Warehouse sees only pick controls; writes NO/zero and shortage reason; cannot issue invoice.
- Scanner offline -> manual PDF upload/edit quantities path without broken workflow.
- OCR confidence warnings + source preview; operator corrections audited.
- Google Drive offline -> invoice PDF still available locally/private object store and sync retried.
- Customer email includes valid invoice PDF/amount/due date, but reminders stop when paid.
- Reference print PDF diff tests: 1, 15, 40, 100 lines, tested on Toshiba after sample received.
- No exposed staff IDs or sensitive pricing in public routes.

## Security
OWASP ASVS-inspired: password session/MFA, CSRF, IDOR, RBAC, injection, file upload content limits, local bridge origin/auth/pairing, log redaction, backup secrets and permitted Drive scopes. Test negative / failed actions create audit event.

## Pilot gates
- 30 real end-to-end orders (or agreed production-like set), zero unexplained stock posting discrepancies and no duplicated invoice IDs.
- At least five simultaneous users in controlled staging exercise.
- OCR evaluated on at least 50 real handwritten sheets; target 95% exact field-level quantity matches. If below target, keep manual as primary; field review always required.
- 100 repeated idempotent requests -> one financial/inventory result.
- Focus 100-row import plus balances/stock compared field-by-field and totals reconciled.
- Three consecutive successful automated backup restore exercises, and documented manual recovery.
- 30-day parallel run is a **separate post-development gate**; not feasible entirely within Week 8. Keep Focus authoritative until passed.

## Evidence format
For each gate: `Test ID`, environment/build hash, command, seeded dataset, expected, actual, date, owner, pass/fail, link to redacted log/screenshot, defects and waiver authorization.
