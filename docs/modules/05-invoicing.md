# Module 05-invoicing: Final invoices, partial dispatch and PDF

**Priority:** P0 foundation/core

## Business outcome
Invoice only for verified quantities supplied; later deliveries create new numbered invoices; same product multiple weighted rows; invoice email, due date and preserved issued PDF.

## Non-negotiable business rules
One original order to many distinct invoices; atomic stock posting; authorized correction/credit only, pending accountant rules.

## Design and integration
Invoice numbering lock, price/tax snapshots, transactionally link pick -> invoice -> movement, PDF/email outbox.

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
10+5 gives separate 2 invoices and 15 total deduction; old invoice PDF hash unchanged after price update.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

## Implementation notes (Module 05 build)
- **Migration** `0006_invoicing.py`: `invoice_sequences` (company, fiscal_year → last_no), `invoices` (unique `picking_attempt_id`, snapshot JSONB, `pdf_sha256`, email state), `invoice_lines`, `invoice_documents` (stored PDF bytes), `email_messages` (unique `dedupe_key`). Triggers: `invoice_guard` (no DELETE; content columns immutable, only state/email/version may change) and `invoice_child_frozen` on lines/documents.
- **Service** `invoicing.py`: `compute()` builds draft lines from verified picked quantities only; `allocate()` posts `INVOICE_DISPATCH` movements FEFO under sorted row locks with posting key `invoice:{pick_line}:{lot}:{unit}`; `issue()` gates → posts stock → takes the next number → snapshots prices/tax → renders and stores the PDF → marks the sheet INVOICED and updates order dispatched quantities → queues email, all in one transaction.
- **Numbering (D-11):** `{INVOICE_PREFIX}{year}-{n:06d}`, e.g. `TT2026-000001` (prefix `TT` since 2026-11-09; before that `INV-2026-000001`), per company and fiscal year in company time zone; sequence row locked `FOR UPDATE` and taken after stock posting, so a rollback never leaves a gap. The prefix change kept the year's sequence, so `(fiscal_year, seq_no)` stays unique and issued numbers never change.
- **Catch weight (D-07/D-08):** BOX lines may be billed per LB when case weights are entered at issue; rows with identical weights are grouped; LB rate resolved at the order's confirmation time. Example: 20,30,40,40,25,25,25 → 205 lb × 1.50 = CAD 307.50.
- **Tax (D-03):** `UNASSESSED`, 0 %. Production-like environments refuse to issue (`TAX_RULES_UNVERIFIED`) until `INVOICE_TAX_VERIFIED=true` after accountant sign-off.
- **PDF (D-12, settled 2026-11-09):** deterministic hand-written PDF (`invoice_pdf.py`, template `sales-invoice-1`, the owner's design); bytes stored once and always served from storage with ETag = sha256. Invoices issued earlier keep their `provisional-1` bytes.
  - Black and white only: greyscale JPEG logo (`assets/invoice_logo.jpg`, DCTDecode, DeviceGray), grey fills, no colour operators (tested). US Letter, Helvetica (measured with the standard AFM widths).
  - Header: logo, letterhead (company `address`, `phone`, `fax`, `email`), SALES INVOICE banner, Invoice No. / Date (DD-MM-YYYY) / Bus # (company `business_no`) / Vendor # (customer `vendor_no`), Page n of N.
  - Bill To (customer name, `bill_to_address`, phone, fax) and Ship To (`ship_to_name` / `ship_to_address`, blank = bill-to); Salesperson (request, else company `default_salesperson`), Customer PO No., Terms (Net N Days / Due on receipt).
  - Lines: Qty, Units, Description, LBS, KG, Unit Price, Amount; 25 ruled rows a page, blank rows fill the page. Boxes print `CASE-40` from the product's default case size (`pack_lb` on the snapshot line), LBS = cases × lb, KG = LBS × 0.45359237 (Decimal). Weighed boxes print their cases and entered weight with a per-lb price ("1.50/lb"). Total row: quantity of cases/pieces and total weight.
  - Notes (no claims after 24 hours; verify on receipt; thank you), the invoice note, Gross / Tax / Net Amount, and one signature line: "Received By (Name & Signature)".
  - Everything printed comes from the snapshot taken at issue (letterhead, customer addresses, vendor no., salesperson, PO, lines).
- **Email (D-15):** outbox event `invoice.email` → dev sink table; dedupe key prevents double sends; resend creates a new key.
- **Corrections (D-02):** void/credit deliberately not built; `invoice.correct` unused until accountant rules exist.
- **UI:** `/invoices`, `/invoices/:id` (PDF open/download, email, stock deducted), `/picking/:id/invoice` (weights, live preview, stock check, dispatch confirmation, issue confirm). Pick sheet shows *Prepare invoice* / *View invoice*; order page lists its invoices.
- **Create invoice › Direct (2026-11-09):** starts filled in: today's date, salesperson (company default, editable), Customer PO No., the customer's bill-to / ship-to / terms / vendor # (from the customer record), and the products of the customer's last invoice with empty quantities, so staff only type quantities; products left empty are not billed. `GET /invoice-settings` serves the letterhead and defaults.

## Failure recovery
| Failure | What the system does | Operator action |
|---|---|---|
| Network drop / double click on Issue | Same `Idempotency-Key` returns the original invoice; sheet already INVOICED → 409 `ALREADY_INVOICED` with `invoice_id` | Open the existing invoice |
| Two managers issue against the same stock | Row locks; second gets 409 `INSUFFICIENT_STOCK` (available, requested); no number consumed | Count/adjust stock, then issue again |
| Crash mid-issue (DB, PDF, audit error) | Whole transaction rolls back: no movement, no number, sheet stays VERIFIED | Retry issue |
| Price list changes after issue | Issued lines keep their snapshot; PDF bytes and hash unchanged | None |
| Weights don't add up to packed cases | 422 `WEIGHTS_MISMATCH`; nothing issued | Fix weight rows |
| Email provider down | Outbox retries; after max attempts status FAILED with error shown | *Email again* |
| No customer email | Status NO_EMAIL; invoice still issued | Add email on customer, then *Email invoice* |
| Tax rules not signed off in production | 409 `TAX_RULES_UNVERIFIED` | Accountant sign-off → set `INVOICE_TAX_VERIFIED` |
| Stale sheet version | 409 `VERSION_CONFLICT`; page reloads preview | Review and issue again |
