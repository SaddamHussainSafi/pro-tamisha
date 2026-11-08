# Module 08-receiving: Vendor shipments, multi-document packages and GRN

**Priority:** P1 integration

## Business outcome
Scan/upload AWB, supplier invoice, packing list or combined document; group documents by reference; parse declared goods/cost/currency; compare physical counted accepted/damaged/rejected.

## Non-negotiable business rules
Declared quantities are not automatically on-hand. Vendor docs retained unaltered. Multiple docs may conflict.

## Design and integration
Shipment + docs + vendor aliases + GRN/lines, discrepancy review, posting key, landed-cost capture without speculative allocation.

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
Supplier declares 25, warehouse accepts 23 + damaged 2 -> only 23 saleable posted; retry cannot double-add.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

## As built (2026-11-08)

### Flow
1. **Warehouse (grn.record)** creates a shipment (vendor, warehouse, optional invoice no., airway bill, expected date). Status **OPEN**.
2. Vendor documents are uploaded or attached from Scan Center (AWB, invoice, packing list, combined, other). Each becomes a `VENDOR_DOCUMENT` scan job and is stored byte-for-byte; `shipment_documents` is append-only.
3. **Declared lines** record what each document says (printed label, product, unit, qty, unit cost). Labels resolve through vendor memory (`vendor:CODE` aliases), then SKU/name; "remember" stores a new vendor alias. Declared lines are never posted; they can only be withdrawn with a reason.
4. **Count lines** record what physically arrived: accepted / damaged / refused, batch, expiry (required for expiry-tracked products), whole numbers for BOX/PIECE.
5. The comparison shows per product+unit: MATCH, SHORT, OVER, DAMAGED, NOT_DECLARED, NOT_RECEIVED, DOC_CONFLICT (two documents disagree).
6. **Send count for approval** → COUNTED (count frozen; reopen with reason returns to OPEN).
7. **Manager (grn.approve)** approves → POSTED with `GRN-YYYYMMDD-NNNN`: accepted qty → new SALEABLE lot (`{SKU}-{date-seq}-{line}`), damaged → `-DMG` DAMAGED companion lot, refused not posted. Any difference requires an acknowledgement and a note (`DISCREPANCY_ACK_REQUIRED`). Cancel (reason) never touches stock.
8. **Landed costs** (freight, duty, brokerage, insurance, other) are recorded against the shipment, also after posting, and withdrawable with reason. They are **not** allocated to lots — `lots.landed_unit_cost` stays NULL until the allocation rule is decided (no speculative allocation).

### Exactly-once posting
Posting keys `grn:{line_id}:accepted|damaged` + idempotency key + row lock on the shipment. Same-key retry replays the stored response; a new key gets 409 INVALID_STATE; 4 concurrent posts → one success, one set of movements.

### Failure / recovery
| Situation | What happens | What to do |
|---|---|---|
| Network drops during "Approve & post" | Retry with the same key returns the original result | Press again; stock is added once |
| Someone else changed the shipment | 409 VERSION_CONFLICT, page reloads | Re-check and repeat |
| Documents disagree | DOC_CONFLICT row lists each document's qty | Withdraw the wrong line or acknowledge with a note |
| Label not recognised | 422 UNMAPPED_LABEL | Pick the product; tick "remember" |
| Scanner/OCR down | Manual upload of the vendor document | Upload a photo/PDF from the shipment page |
| Wrong count after submit | Reopen count (reason) | Edit, send again |

### Database guards
Triggers: shipment documents append-only; shipments cannot be deleted, identity is immutable, POSTED/CANCELLED final; GRN lines immutable once a lot is linked; declared lines and costs only accept the `removed_*` fields.

### Evidence
- Migration `apps/api/migrations/versions/0009_receiving.py`; contract `contracts/openapi.yaml` (`/vendors*`, `/shipments*`, 17 paths).
- `apps/api/tests/test_receiving.py` (9 tests incl. acceptance 25 → 23 + 2, replay, concurrency, permissions, DB guards).
- `apps/web/e2e/receiving.spec.ts` (warehouse counts → manager approves with note → saleable 23 + damaged lot), axe clean.
- Screenshots: `apps/web/screenshots/receiving-count-1440.png`, `receiving-list-1440.png`, `receiving-posted-1440.png`, `vendors-1440.png`.
