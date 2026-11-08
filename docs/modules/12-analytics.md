# Module 12-analytics: Operational analytics and management dashboards

**Priority:** P2 after foundation

## Business outcome
Revenue, boxes/pieces/lb sold, top/least performers, per-customer rate/volume, stock flow, GRN, outstanding, vendor performance; interactive date/customer/product/vendor/origin/warehouse filters.

## Non-negotiable business rules
No predictions V1; profit shown only with approved cost basis and reconciled landed-cost data; no mixing box with lb.

## Design and integration
SQL aggregate views/materialized cache where needed; server-side filters; CSV export, drill-down invoice/movement, SSE invalidation.

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
Revenue totals reconcile to issued less credits, stock summary to movements, filters yield reproducible totals.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.
