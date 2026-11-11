# Module 07-ocr: Document AI extraction and review

**Priority:** P1 integration

## Business outcome
Printed fixed customer order sheets: align form, detect template version/QR and read handwritten quantity cells; vendor text + blocks; operator sees original and extracted cells side-by-side.

## Non-negotiable business rules
OCR never creates issued invoice or stock movement; unknown/unclear quantities need manual correction; keep source image.

## Design and integration
Google Document AI Enterprise OCR server-side, bounding boxes and confidence data, product alias mapping, user corrections and extraction versions.

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
50 real handwriting samples measured exact field accuracy; target 95%; below target do not claim automation; manual path always functional.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

---

## As built (2026-11-08)

### Flow
1. A `SALES_ORDER` or `PICK_SHEET` scan is received (bridge or upload) → `ocr.auto_queue` creates an extraction (`QUEUED`) and an outbox event `ocr.extract`. (Picking sheets were kept unread until 2026-11-10; the office fills one paper form for both, see below.) Any other received scan can be read on request (`POST /ocr/extractions`, **Read this scan**).
2. The Celery worker reads the original with the configured engine (`OCR_PROVIDER=fake|document_ai|disabled`), maps it to the PT-OS-1 template, and stores one field per line with confidence and status:
   - `OK` — product and quantity read at or above `OCR_CONFIDENCE_THRESHOLD` (0.900).
   - `LOW_CONFIDENCE` / `UNREADABLE` — the quantity must be entered by a person; it cannot be confirmed as read.
   - `UNMAPPED` — written product not matched by SKU, alias or name.
3. A reviewer (`scan.review`) opens **Scan Center → To review → Review scan**: original on the left, lines on the right. Each line is Confirmed, Corrected or Removed; missed lines can be added. Overriding a clearly read line needs a reason. Every change is an append-only correction with actor and time.
4. **Create draft order** (needs `sales_order.manage`, all lines decided, customer chosen, "I checked every line" ticked) → a **DRAFT** sales order with source `SCAN` linked to the original document. No reservation, pick, stock movement or invoice is created; confirming the order is the normal order workflow.

### Printable sheet
`GET /ocr/order-sheet` produces the PT-OS-1 form for a customer + location (one row per active product, a "Ref:" token with the customer code, and write-in rows). Sheets filled on this form read best.

### Office paper order sheet (TT-PAPER-1, 2026-11-10)
The office's own pre-printed sheet ("TAMISHA TRADING INC  CUSTOMER NAME", three tables VEGETABLES+PEPPERS / ROOTS+HERBS+OTHERS / FRUITS with columns ITEM | Q | PKG | WEIGHT | NOTES) is not the PT-OS-1 form and has no SKUs, so it is recognised by its section bars and PKG headers (`paper_sheet.detect`). Reading (`paper_sheet.read`): a printed product name (matched to the catalog by name or the sheet's own spelling kept as an alias by `scripts/load_catalog.py`; spacing ignored; one or two OCR slips on long names allowed but sent for review) plus a mark in **that table's Q box** on the same row = one line in the product's stock unit. PKG / WEIGHT / NOTES marks are packing notes, never quantities; an empty Q box means not ordered; a Q mark whose name can't be matched becomes an "unknown product" line with the printed text. The handwritten customer name is matched only if the engine reads handwriting (Document AI).

**Tesseract on forms** (`ocr_grid.py`, Pillow only): table rulings are found as long ink runs and painted out before reading (otherwise names come back glued to `|`/`[`); each orientation is scored by confidently read *level* words, because OSD is often unsure on these sheets and Tesseract also reads sideways text with the layout lost; narrow boxes holding ink but no printed word are read once more, digits only. Tesseract has no handwriting model: on the two office scans (SCN-20261110-0001/0003) it found the product rows with a Q mark (22 and 9 lines, including some scribbles/signatures over the boxes) but read almost none of the handwritten numbers correctly, so such reads are capped below the confidence threshold, and a read under 0.5 is shown only as a hint ("10?") with the quantity left for the reviewer. Expect to type the quantities with Tesseract; Document AI (handwriting) is the path to reading them.

### Accuracy (acceptance)
`GET /ocr/accuracy` measures exact-field accuracy over accepted readings: a line counts as correct only if it was Confirmed as read; corrections and removals of read lines are errors; lines added by hand count as misses. The UI shows "— / N of 50 lines needed" until 50 samples exist and only says "Meets 95% target" when it does. Per D-18 every reading is still fully reviewed regardless of accuracy. The 50-sample measurement on real office handwriting is **pending** (requires the office PoC and Document AI credentials).

### Failure / recovery
| Situation | What happens | What to do |
|---|---|---|
| OCR engine down / timeout | Outbox retries (transient errors) up to 5 attempts, then `FAILED` with the error code | **Read again** from the review page or scan detail, or **Reject** and type the order by hand |
| Scan is a photo / no text | `NEEDS_REVIEW` with warning `NO_TEXT`, no lines | Add the lines by hand from the original |
| Customer ref not found | Warning `CUSTOMER_NOT_FOUND` | Choose the customer before creating the draft |
| Engine disabled (`OCR_PROVIDER=disabled`) | Extraction opens directly for manual entry (`OCR_DISABLED`) | Manual path always works |
| Two people edit at once | `409 VERSION_CONFLICT`; page reloads current state | Re-apply the change |
| Wrong reading accepted | Draft order only | Cancel the draft order; the reading and scan stay on file |

### Data protection
Document AI credentials stay server-side (`OCR_DOCAI_CREDENTIALS_FILE`); the browser only receives extracted text and the original via the authenticated document endpoint. Extraction identity, read values, corrections and final states are protected by DB triggers (migration `0008_ocr.py`).

### Evidence
Migration `0008_ocr.py`; `src/pro_trading/ocr.py`, `ocr_grid.py`, `paper_sheet.py`, `routes/ocr.py`; `tests/test_ocr.py` (12), `tests/test_paper_sheet.py` (6); `e2e/paper-sheet.spec.ts` (2); web `pages/OcrReview.tsx`, `lib/ocr.ts` (+tests); `e2e/ocr.spec.ts`; screenshots `ocr-queue-1440.png`, `ocr-review-1440.png`, `ocr-draft-order-1440.png`; contract `/ocr/*` paths.

### Live progress while a scan is read (migration 0022, 2026-11-11)
- `scan_events` (append-only telemetry, goes with its scan via ON DELETE CASCADE): `OCR_STARTED` (attempt, document pages), `PAGE_READ` (page n of m, words, median engine confidence, first words), `MATCHING`, `MATCHED` (lines, to check, unknown products), `OCR_RETRY` (transient engine error, attempt n of 5), `OCR_FAILED`, `OCR_TIMEOUT`. The worker writes each in its **own short transaction** (`scan_progress.py`, lock timeout 0.5 s, dropped and logged rather than ever slowing the reading), so page 3 of 10 shows while the reading's own transaction is still open. `extraction_id` has no foreign key on purpose: the worker holds the reading's row lock. The reading's status stays the truth.
- Tesseract reports each page as it finishes (`page_read` in its page loop); engines that answer for the whole document at once (Document AI, fake) are reported page by page right after they answer (`pages_done`).
- **Stall check (`OCR_STALL_SECONDS`, default 180):** a reading still QUEUED that no worker holds (its row and its outbox event are not locked) and with no event for that long is set FAILED / `OCR_TIMEOUT` with an audit row; a late worker then finds it closed and does nothing. "Read again" starts a fresh run.
- Review screen: every line still to check is outlined on the original (amber: unclear quantity, red: unknown product / unreadable) and clicking an outline brings its line into view; in each line the uncertain part (product name or quantity) is marked; `?page=n` (from the scanner's page strip) opens the original on that page. The approval steps are unchanged (every line decided, customer chosen, "I checked every line", draft order only).
