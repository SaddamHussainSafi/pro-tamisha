# Module 11-focus-migration: Focus V7.0129 import and reconciliation

**Priority:** P2 after foundation

## Business outcome
Read-only authorized backup investigation, report exports, XML invoice export, customer/vendor/product/stock/transaction mapping; historical invoice read-only.

## Non-negotiable business rules
Not Focus 9. Never mutate Focus. Full backup structure unknown; migration must not proceed without sample and reconciliation.

## Design and integration
Import staging tables, provenance, checksums, id mapping, duplicate detection, dry-run and variance reports, legacy invoice namespace.

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
100 sample customers/products and 10 invoices field-level match; opening stock and AR zero unexplained variance.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.
