# Module 10-payments: Manual payments, AR and notices

**Priority:** P1 integration

## Business outcome
Record cash, cheque, EFT, Interac Autodeposit; customer email invoice/amount/due date; payment status unpaid/reported/partial/paid; reminders; no direct bank integration.

## Non-negotiable business rules
Customer says sent != confirmed; only authorized employee checks Scotiabank then applies verified payment.

## Design and integration
Append-only payment allocations, evidence refs and audit; due reminders via worker; balance computed from invoice and verified receipts.

## Interfaces / UI
- Desktop-first, keyboard-friendly, dense responsive tables, clear status badges (not color alone), search, multi-filters, bulk operations with confirmation and undo policy.
- Every data mutation is a typed backend API with strong server authorization, idempotency key, audit event and optimistic version/conflict response where relevant.
- Show original source document and change history when decisions depend on scanned text or financial details.

## API/data checklist
- Define request/response schemas and errors in `contracts/openapi.yaml` before building route handlers; generate TS types.
- Add normalized tables, indexes and foreign keys to versioned Alembic migrations based on `db/schema.sql`.
- Implement `GET` list/detail filters with pagination; `POST` create; commands for approve/issue/post; no generic direct PATCH of immutable posted documents.
- Changes that cross invoice/stock/payment boundaries are one DB transaction; external IO via outbox.

## Acceptance tests
700 payment on 1250 -> 550 remaining; duplicate confirmation blocked; no bank login/API secret.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.
