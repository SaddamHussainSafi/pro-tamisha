# Module 03-inventory: Transactional inventory engine

**Priority:** P0 foundation/core

## Business outcome
Append-only lot/warehouse stock movements, cached balances, availability/reservation, stock count, damage, returns and controlled reversal.

## Non-negotiable business rules
Customer order draft does not deduct; final invoice deducts exactly once; approved GRN adds exactly once.

## Design and integration
Postgres row lock/atomic guarded updates, posting key unique, ledger reconciliation jobs, SSE after commit.

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
Concurrent oversell rejected; repeat 100 requests returns prior result; rebuild cached balance equals ledger.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

## Implementation notes (Module 03 build)

**Tables** (migration `apps/api/migrations/versions/0004_inventory.py`):
- `inventory_lots`: lot_code unique per company, expiry, status SALEABLE/QUARANTINE/DAMAGED/EXPIRED, `parent_lot_id` for the single damaged companion lot, landed cost (nullable, D-04 open), optimistic `version`.
- `inventory_balances`: PK (lot, unit) — cached; checks `qty >= 0`, `reserved_qty <= qty`; `version` bumped on each change.
- `stock_movements`: append-only ledger (`delta_qty`, `qty_after`, type, reason, source_type/id/line, **unique `posting_key`**, `reverses_movement_id` unique → a movement is reversed at most once).
- `stock_reservations`, `stock_counts`, `stock_count_lines`.
- Trigger `inventory_reject_change`: movements reject UPDATE/DELETE; lots/balances reject DELETE and identity changes.

**Single write path:** `pro_trading/inventory.py::post()` is the only code that changes stock. It locks the affected balance rows `FOR UPDATE` in sorted (lot, unit) order (no deadlocks), refuses any posting that would take `qty` below `reserved_qty` (409 `INSUFFICIENT_STOCK` with `available`/`requested`; negative stock is never allowed, D-05), writes the movement and updates the cache in one transaction. A repeated `posting_key` returns the existing movement (exactly-once); the same key with different content → 409 `POSTING_KEY_CONFLICT`. Modules 04/05 (GRN, invoice) will call `post()` with keys `grn:…`/`invoice:…`.

**Units:** BOX, PIECE, LB, KG are separate stock and never converted. BOX/PIECE quantities must be whole (422 `UNIT_NOT_DIVISIBLE`); units must be the product's stock unit, LB, or an active profile unit (422 `INVALID_UNIT`).

**Operations:** opening balance, adjustment (reason + note), damage (two legs to `<lot>-DMG`), return (SALEABLE or DAMAGED, source reference required, D-10), reservation/release, lot status with reason, count (warehouse submits → manager posts or rejects; stale if the balance version changed since counting), reversal (compensating entries for the whole source group, reason required; GRN/invoice postings must be reversed through their documents), reconciliation and owner-only rebuild.

**Authorization:** `inventory.read` warehouse-scoped; `inventory.count` WAREHOUSE/MANAGER; `inventory.adjust` MANAGER/OWNER; `inventory.reserve` SALES_OPERATOR/MANAGER; `inventory.reverse` MANAGER/OWNER; `inventory.reconcile` OWNER only (company-wide). Privileged grants need MFA.

**Live updates:** `GET /inventory/events` (SSE) polls a committed-state cursor every 1.5 s, so events are only ever sent after commit. Each open stream holds one worker thread — acceptable for MVP; move to LISTEN/NOTIFY + async if many clients.

**UI:** `/inventory` tabs Stock on hand (search, status, warehouse filters, paging), Stock ledger, Counts (expand lines, post/reject), Reconciliation (owner); Opening balance and Record count panels; `/inventory/lots/:id` balances, adjust/damage/status forms, ledger with reverse dialog, live badge.

## Failure recovery

| Failure | Behaviour | Operator action |
|---|---|---|
| Client retries (network drop) | Same Idempotency-Key replays the stored result; 100 repeats → one movement (tested) | None |
| Two people take the last stock | Row lock serializes; one succeeds, the other gets 409 INSUFFICIENT_STOCK with what is available (10 concurrent −6 on 8 → 1 success) | Re-pick from another lot |
| Crash mid-posting | Whole transaction rolls back (failure-injection test); retry succeeds once | Retry |
| Wrong adjustment posted | Cannot edit/delete (trigger). Post a reversal with reason; original stays for audit | Manager reverses |
| Reversal would make stock negative | Refused (409 INSUFFICIENT_STOCK) | Investigate, count the lot |
| Count submitted, stock moved before posting | 409 STALE_COUNT; nothing posted | Reject and recount |
| Cached balance drifts from ledger | Reconciliation lists mismatches; owner rebuilds from ledger (audited) | Owner runs rebuild |
| SSE disconnected | UI shows "Reconnecting…"; browser reconnects; data still loads on navigation | None |
| Expired/quarantined lot | Cannot be reserved (409 LOT_NOT_SALEABLE) | Change status with reason or move to damage |
