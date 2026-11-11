# Module 04-sales-orders: Sales order drafts and picking

**Priority:** P0 foundation/core

## Business outcome
Capture owner handwritten customer order from OCR/manual, preserve source, create order draft; print picking sheet; actual packed quantity or NO, missing reason and customer backorder.

## Non-negotiable business rules
No morning invoice number. NO = 0 packed, NOT cancelled. Original request preserved.

## Design and integration
Sales order/order lines; picking attempt and its lines; warehouse queue; QR order ID; operator review path.

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
15 requested, first 10 supplied -> 5 pending; worker cannot change original order price or issue invoice.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

## Implementation notes (Module 04 build)
- **Data:** migration `0005_sales_orders.py` — `sales_orders`, `sales_order_lines`, `sales_order_amendments` (append-only), `picking_attempts`, `picking_lines`. Triggers `sales_order_guard`, `sales_order_line_guard`, `sales_order_amendment_guard`: no deletes, `source_text`/`source_snapshot` immutable, rate/price snapshot frozen once confirmed, amendments never change.
- **One open sheet per order:** partial unique index `uq_pick_one_open_per_order` on CREATED/IN_PROGRESS/SUBMITTED/NEEDS_CORRECTION. A VERIFIED sheet (awaiting invoice) does not block a new one because a new sheet requests only `pending = remaining − awaiting_invoice`.
- **Quantities per line:** ordered, cancelled, dispatched (set by invoicing in Module 05), awaiting_invoice (picked on VERIFIED sheets), remaining = ordered − cancelled − dispatched, pending = remaining − awaiting. Status is derived in `orders.refresh_status` after every command.
- **NO ≠ cancel:** picking 0 records outcome NO; at verification the default disposition is BACKORDER, so the quantity stays pending. Cancelling a shortfall requires `sales_order.close` plus a reason and writes an amendment.
- **Prices** are resolved once at confirm (`pricing.resolve`, CUSTOMER > GROUP > STANDARD) and stored with the price snapshot. Warehouse roles have no price, confirm or close permission.
- **Numbering:** `SO-YYYYMMDD-NNNN` under a company row lock. Sheet code is `{order_no}-P{n}`, rendered as a QR code (qrcode.react) for scan lookup in `/picking`.
- **Web:** `/orders` (Open/Drafts/Backorders/Closed/All), `/orders/new`, `/orders/:id` (lines with pending + backorder badge, original request, sheets, changes, confirm/print/cancel/close/cancel-qty dialogs), `/picking` queue with code lookup, `/picking/:id` sheet (record packed qty, NO button, shortage reason, submit, verify with per-line BACKORDER/CANCEL, send back, void, print).
- **Seed:** C-1001 confirmed order (CAS-FZ BOX 15, PLN-GR PIECE 24), C-1002 draft (RICE-JAS BOX 4).
- **Stock** is not moved by picking; dispatch posts `INVOICE_DISPATCH` when the invoice is issued (Module 05).

## Drafts are checked on the entry form; boxes sold per lb (2026-11-11)
- A DRAFT opens as the same form as New sales order and Create invoice: Customer, Wanted for, Warehouse, payment terms, Note, then the grid S/N · Item · Qty · Unit · **LB** · Rate · Gross with the Subtotal / Tax / Net bar, and **Save draft** / **Confirm order** at the bottom (no confirm dialog). Picking columns, the original request and history show once the order is confirmed. **Cancel draft** asks no reason (recorded as "Draft cancelled").
- **Create sales order screen (2026-11-11):** the page is titled "Create sales order"; Save draft (and, on a draft, Confirm order) are also at the top of the page; each chosen product shows its photo as a small square thumbnail (about 40 px, `ui/ProductThumb.tsx`, the files in `public/products/<SKU>.webp`, a neutral placeholder when there is none); 20 rows are open, more appear as rows fill, and "Add 10 more rows" adds them on request (up to 200); on narrow screens the grid scrolls inside its own frame so the product stays readable. Empty rows are never saved. Order rules, prices, tax and totals are unchanged (still the server's).
- `sales_order_lines.weight_lb` (migration `0025`): what the whole line weighs, boxes only. A scanned sheet's WEIGHT box fills it; staff can type it in LB (empty shows the case size's weight greyed). Confirm (and direct invoices) price a weighed box as the per-lb rate × weight ÷ boxes; a per-lb price beats a box price for a weighed line. With no weight and no case size: `WEIGHT_REQUIRED`. The original capture (`source_snapshot`) keeps the weight as written.

## Failure recovery
| Failure | What happens | Recovery |
|---|---|---|
| Two clerks open a sheet at once | Partial unique index → 409 `OPEN_PICK_EXISTS` with `attempt_id` | UI links to the existing sheet |
| Stale screen (someone else changed order/sheet) | 409 `VERSION_CONFLICT` with `current_version` | Page reloads; user re-applies |
| Network drop / double click | Idempotency key replays the stored response | Retry is safe |
| Audit/DB failure mid-command | Whole transaction rolls back (tested by failure injection) | Retry with same key |
| Validation error on record | 422; typed quantities kept on screen (only 409 reloads) | Fix the line and save |
| Picker made a mistake | Supervisor "Send back" → NEEDS_CORRECTION with note | Picker corrects and resubmits |
| Wrong sheet printed | Void sheet (reason) | Print a new sheet from the order |
| Close requested while picked goods await invoice | 409 `AWAITING_INVOICE` | Invoice first (Module 05) or cancel per line |
| No price for a line at confirm | 422 `NO_PRICE` | Publish price in Pricing, confirm again |
| Box with no known weight at confirm | 422 `WEIGHT_REQUIRED` (boxes are sold per lb) | Type the line's weight in LB on the draft, confirm again |
