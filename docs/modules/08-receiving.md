# Module 08-receiving: Purchases (was: vendor shipments, multi-document packages and GRN)

**Priority:** P1 integration

## Business outcome
Scan/upload AWB, supplier invoice, packing list or combined document; group documents by reference; parse declared goods/cost/currency; compare physical counted accepted/damaged/rejected.

## Non-negotiable business rules
Vendor docs retained unaltered. Stock moves only through the ledger, once per posting key. Posted rows are never edited: a later fix is a new movement with a reason. Negative stock is refused (D-05).

> **Owner decision, 2026-11-12 (D-48 … D-51):** the original rule "declared quantities are not automatically on-hand" (count first, then a manager approves) is replaced. A purchase is in stock the moment it is saved. See "As built (2026-11-13): Purchases" below.

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

## As built (2026-11-13): Purchases

The screens say **Purchases**; the record is still a `shipments` row, so every existing link, report, search result and supplier ledger keeps working. `/receiving` and `/receiving/{id}` redirect to `/purchases`.

### One purchase flow (D-48)
There are no purchase types. Airport imports, local suppliers and other wholesalers are all suppliers and use the same record:
supplier · invoice number and date · lines (product, quantity, unit, cost per unit).

It is filled in three ways, which can be mixed:
1. **By hand** in the keyboard grid (Product → Qty → Cost → next line, paste from Excel). `POST /purchases`.
2. **By scanning or uploading the seller's invoice.** The reading is only a proposal: a person confirms or corrects every line on the review screen, then presses **Save & add to stock** (`POST /ocr/extractions/{id}/to-purchase`). Unreviewed lines refuse the request.
3. **Both:** lines can be added by hand on the review screen, or afterwards on the purchase ("Add a line missed on this invoice", `POST /purchases/{id}/lines`).

The original document is stored byte-for-byte and is never changed; more documents can be attached at any time, also after the purchase is in stock.

### Stock on save (D-49)
"Save & add to stock" does everything in **one transaction**: the purchase row (status `POSTED`, with its `GRN-…` number), one invoice line per line (`shipment_declared_lines`: what the supplier bills), one stock line + one batch per line (`grn_lines`, `inventory_lots`), and one ledger row per line with posting key `grn:{line}:accepted`. There is no count and no approval step. `grn.record` is enough.

- A retry with the same idempotency key replays the first answer; four people pressing at once add the stock once.
- The same supplier + invoice number entered again is refused (`INVOICE_ALREADY_ENTERED`, with the purchase it already is) unless the person confirms. Two people entering the same invoice at the same moment: one succeeds, the others get the refusal (the supplier row is locked for the check).
- A kg line for a product kept in lb is saved in lb (D-38); its cost per kg becomes the cost per lb.
- A product that tracks expiry needs its expiry date for goods going into stock now (D-53).

### Later fixes are corrections
Short, damaged, returned to the supplier, or more than invoiced: **Correct…** on a stock line adds a `purchase_corrections` row with a reason (`POST /purchases/{id}/corrections`). Approving it posts new `GRN_CORRECTION` ledger rows (posting keys `grnfix:{id}:out|dmg|in`); damaged goods move to the batch's damaged companion. Nothing that was saved is edited.

- Someone holding `grn.approve` posts a correction at once. Anyone else's correction waits as **Needs manager's OK**; the manager approves or refuses (with a reason).
- **Stock is never forced below zero (D-05).** When part of the batch was already invoiced or is reserved, the correction takes what is still free, records the rest as `uncovered_qty`, marks it **To review**, lists the invoices that took the batch, and alerts the owner (critical notification). Invoicing is never blocked. The owner clears the flag with a note on what was done (D-50).
- Corrections do not change what the supplier is owed (still an open question, see `suppliers.py`); the supplier page shows them as not accepted / damaged.

### Airport shipments
The same purchase with **Airport shipment, verify pieces on arrival** ticked, plus airway bill number, expected arrival and (optional) pieces on the airway bill. CFIA documents are a document type; freight, duty and other landed costs can be recorded for airport shipments only (never spread over product costs, never part of the supplier balance).

- Saving adds the stock immediately, even before pickup, so invoices can be printed when the goods arrive.
- The purchase shows **In stock · Airport · to verify**. **Verify pieces** (`POST /purchases/{id}/verify`) records the pieces counted. A match marks it **Verified**. If the count differs, the person says what arrived for the lines that differ (and what is damaged) and why; each difference becomes a correction as above.
- Statuses: In stock (to verify) → [needs manager's OK] → Verified. A refused difference sends it back to be counted again.

### Quick receive: goods on the shelf, no purchase entered
`POST /purchases/quick`: product, quantity, unit, the supplier if known (otherwise "Supplier invoice to follow") and a **reason** (required). The stock is available immediately; the action is audited (`shipment.quick_receive`) and managers are notified.

- Offered where missing stock blocks work: on a packing line and on both invoice screens when the system shows less than the line needs ("Add 5 BOX found in the warehouse"). It needs `grn.record`; others are told who can do it.
- Each quick receive is a purchase marked **Details to complete**. It stays on the "To complete" filter of the Purchases tab, and a reminder goes out once a day, until it is matched to the real supplier invoice.
- **Matching never posts its stock again.** Either open it and choose *Complete details* (supplier, invoice number, costs; `POST /purchases/{id}/complete`), or enter / scan the invoice as a normal purchase and answer "Same goods, on this invoice" for the quick receives it covers (`absorb_quick_ids`). Only a difference in quantity becomes a correction (with a reason); invoice lines that were never on the shelf go to stock as new goods.
- No cost, no bill: a quick receive adds nothing to the supplier balance until it is completed.

### Supplier bills
Unchanged: what a supplier billed = Σ invoice quantity × cost per unit of purchases in stock (lines without a cost are counted, not guessed). A quick receive matched into another purchase's invoice is billed there, once.

### One Orders page
`/orders` has the tabs **Sales orders** and **Purchases** (`/purchases`), with the same search and filter bar. Purchases shows supplier, invoice no., date, goods, total and status with the badges **Airport · to verify**, **Details to complete**, **N corrections to approve**, **To review**. **New** offers New sales order / New purchase / Quick receive, each only to people who may do it.

### Shipments from before the change
Migration `0033` is additive: existing rows get `entry = LEGACY` and are otherwise untouched; nothing posted is changed. A shipment that was still open or waiting for approval shows **Not in stock yet** with one button, **Add to stock now** (`POST /purchases/{id}/stock`): its count is posted as counted (or, with no count, what its documents say), with no approval step. The old endpoints (`/shipments/{id}/count`, `/submit-count`, `/post` …) still work for those rows.

### Permissions
| Action | Permission |
|---|---|
| See purchases | `grn.record` or `grn.approve` in the warehouse |
| New purchase, quick receive, add lines, complete details, verify pieces, record a correction, attach documents, add to stock (old shipment) | `grn.record` |
| Approve / refuse a correction, mark a flagged shortage reviewed, cancel an old shipment | `grn.approve` |
| Save a scanned invoice as a purchase | `grn.record` (and `scan.review` to review it) |

All checked on the server, per warehouse. Sales has neither (D-51).

### Failure / recovery
| Situation | What happens | What to do |
|---|---|---|
| Network drops during "Save & add to stock" | Same key → the first answer is replayed | Press again; stock is added once |
| The invoice was already entered | 409 `INVOICE_ALREADY_ENTERED` naming the purchase | Open it; or confirm "Enter it again" for a real second delivery |
| A line is wrong (unknown product, half a box) | Nothing is saved, the line is named | Fix the line, save again |
| Shortage found, but the stock was already invoiced | Correction posts what is free, the rest is flagged, owner alerted | Check the invoice and the shelf; "Mark reviewed" with a note |
| Invoice quantity differs from a quick receive | 422 `MATCH_REASON_REQUIRED` lists the differences | Give the reason; the difference becomes a correction |
| Quick receive already matched by someone else | 409 `QUICK_ALREADY_MATCHED` | Reload |
| OCR / scanner down | Nothing is lost | Enter the purchase by hand; attach the file later |

### Database guards
- `shipment_guard`: no delete; identity (company, number, warehouse, entry) never changes; `CANCELLED` is final; a `POSTED` purchase accepts only its follow-up columns (verification, completing a quick receive, version). Supplier, invoice number/date and currency can change only while details are to complete.
- `purchase_correction_guard`: never deleted; what it says never changes; a decided correction only accepts the review fields.
- `stock_movements` stays append-only; `grn_lines` stay frozen once linked to a batch; declared lines and costs are withdraw-only.

### Evidence
- Migration `apps/api/migrations/versions/0033_purchases.py` (rollback notes in its header). Dev database backed up to `checkpoints/pre-purchases-2026-11-12/` before it ran.
- API: `routes/purchases.py`, `purchases.py`, `/ocr/extractions/{id}/to-purchase`; contract `contracts/openapi.implemented.json`.
- `apps/api/tests/test_purchases.py` (23 tests: stock on save, atomic refusal, retry + concurrency, duplicate invoice, permissions, airport verify, manager's OK, shortage on invoiced stock, quick receive, matching, absorbing, corrections, documents/costs, old shipments, database guards).
- Playwright: `receiving.spec.ts` (purchase by hand, correction, old links), `purchases-scan.spec.ts`, `purchases-airport.spec.ts`, `purchases-quick.spec.ts`; `orders.spec.ts` and `packing-sheet.spec.ts` updated. All with axe checks.
- Screenshots (1440 and 390): `apps/web/screenshots/purchase-*.png`, `purchases-*.png`, `packing-short-line-*.png`, `quick-receive-dialog-*.png`, `invoice-short-line-1440.png`.

## Before 2026-11-12: count, then approve (kept for rows with entry LEGACY)

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
