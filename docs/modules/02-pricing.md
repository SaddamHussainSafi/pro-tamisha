# Module 02-pricing: Price management and versioning

**Priority:** P0 foundation/core

## Business outcome
Alphabetical/filterable price grid, price per box/lb/kg/piece, customer/group overrides, effective timing, CSV template download/preview/diff/approve; prior lists kept immutable.

## Non-negotiable business rules
Customer price > group price > standard; approval changes new drafts only; drafts underway prompt to refresh.

## Design and integration
Persist price_book_versions + entries and invoice snapshot rate; CSV import validates SKU/unit/currency/conflicts, reports row-level errors.

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
Two price versions differ; old invoice rate unchanged; duplicate CSV upload not duplicate published version; invalid unit rejected.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

## Implementation notes (Module 02 build)

**Tables:** migration `apps/api/migrations/versions/0003_pricing.py` adds:
- `customer_groups`: code unique per company
- `customers`: ref unique per company, optional group, optimistic `version`
- `price_book_versions`: status DRAFT/ACTIVE/ARCHIVED, `effective_at`, `source_hash`, `based_on_id`, `approved_by`; unique (company, effective_at) among ACTIVE
- `price_entries`: (version, product, unit, scope, customer/group) unique; `rate` NUMERIC(14,4) CAD

**Immutability:** trigger `price_reject_published_change` blocks any UPDATE/DELETE of entries on non-DRAFT versions and any change to an ACTIVE version except cancelling one still scheduled in the future. Activation requires an approver. Old rates therefore never change (INV-005); a later invoice module stores the `PriceQuoteOut` snapshot on each line.

**States shown to users:** DRAFT, SCHEDULED (active, future), CURRENT (active, latest effective ≤ now), SUPERSEDED (older active), ARCHIVED. A draft is flagged `stale` when a version was activated after the one it was based on — the UI shows a "refresh" banner.

**Resolution:** `GET /prices/resolve` picks the ACTIVE version with the latest `effective_at ≤ at`, then CUSTOMER > GROUP (customer's group) > STANDARD. No match → 404 `NO_PRICE` (never a guessed price). Units: LB/KG always; BOX/PIECE only when the product has an active unit profile of that kind.

**CSV:** columns `sku,unit,rate,customer_ref,group_code`. Row-level errors (line, column, code): INVALID_UNIT, UNKNOWN_SKU, UNKNOWN_CUSTOMER, UNKNOWN_GROUP, INVALID_RATE, DUPLICATE_ROW, EXTRA_CELLS, and "no active <unit> unit profile". The canonical content hash ignores row order, case, number formatting and BOM, so re-uploading the same list (even reordered) returns 409 `DUPLICATE_UPLOAD` with the existing version; activating content identical to the current version returns 422 `NO_CHANGES`. Exports are formula-injection safe (`'` prefix) and re-import to the same hash.

**Authorization:** `price.read` for SALES_OPERATOR, FINANCE, MANAGER, OWNER. `price.draft` / `price.approve` need a company-wide MANAGER/OWNER grant plus verified MFA. Customer/group creation needs `catalog.manage`.

**Concurrency:** activation locks the company row; two activations of the same draft → one 200, one 409 INVALID_STATE; two drafts at the same effective time → one 409 DUPLICATE. Draft edits carry the draft `version`.

**UI:** `/pricing` (version list, state filter, price check, CSV import with preview/diff/row errors, template download) and `/pricing/:id` (filterable grid, set/remove price on drafts, activate/cancel/discard with confirmation dialogs, 409 warnings).

## Failure recovery

| Failure | Behaviour | Operator action |
|---|---|---|
| Network drop / client retry | Same `Idempotency-Key` replays stored response; no second draft or activation | None — retry is safe |
| Crash mid-import (audit/DB) | Whole transaction rolls back; retry with same key succeeds once (failure-injection test) | Retry |
| Same CSV uploaded twice | 409 DUPLICATE_UPLOAD linking the existing version | Open the existing version |
| Bad rows in CSV | 422 CSV_INVALID listing line/column/code; nothing saved | Fix file, check again |
| Wrong price activated | Cannot edit (trigger). If still scheduled: cancel. If current: import/draft a corrected version and activate it now | Manager publishes correction |
| Two managers activate at once | One wins; other gets 409 and reloads | Review the winner |
| Draft based on an old version | "Stale" banner; activation still allowed but diff shown against current | Start a fresh draft if needed |
| No price for customer/unit | 404 NO_PRICE; sale cannot silently use 0 | Add a price and activate |
