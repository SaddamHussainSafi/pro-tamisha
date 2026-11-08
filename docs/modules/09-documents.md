# Module 09-documents: Printing, Google Drive, document history

**Priority:** P1 integration

## Business outcome
Match existing invoice PDF exactly when provided; customer/vendor/year/month folder structure; PDF/signed POD original retention; stable Drive file IDs, email attachments and document sync status.

## Non-negotiable business rules
Never overwrite issued PDF or original signed scan; draft PDFs can be replaced; actual template not provided.

## Design and integration
PDF renderer golden templates, Drive OAuth/scoped service, outbox retries, print path from Chrome or local kiosk print service if approved.

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
50-line invoice paginates approved; Drive timeout leaves invoice committed with Pending Sync; reprint same issued bytes.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

## As built (2026-11-08)

### What is filed
`document_files` is the archive index. It points at bytes already stored (issued `invoice_documents`, scanned/uploaded `source_documents`); bytes are never copied, regenerated or overwritten.

| Kind | Filed when | Folder | File name |
|---|---|---|---|
| INVOICE_PDF | Invoice issued (same DB transaction) | `Customers/{ref} {name}/{YYYY}/{MM}` | `{invoice_no}.pdf` |
| POD | Proof of delivery uploaded on the invoice page | same customer folder | `{invoice_no} POD.ext`, then `… (2).ext` |
| VENDOR_DOC | Vendor document attached/uploaded on a shipment | `Vendors/{code} {name}/{YYYY}/{MM}` | `{shipment_no} {kind} {doc_ref}.ext` |
| SCAN | Scan received (bridge or upload) | `Scans/{YYYY}/{MM}` | `{job_no} {job_type}.ext` |

Year/month use the company time zone. Names are de-duplicated with ` (n)`; same bytes uploaded twice as POD return the existing file (200).

### Drive copy (outbox)
- Filing enqueues `document.drive_sync`; the worker copies the file. **A Drive outage never touches the invoice**: issue commits, the file shows **Pending sync**.
- Transient errors (timeout, IO) retry up to 5 outbox attempts, then **Sync failed**; permanent errors (access denied, name conflict) fail at once and write audit `document.sync_failed`. **Retry** (document.manage, version-checked, idempotent) moves FAILED → PENDING.
- Providers (`DRIVE_PROVIDER`): `local` (default; write-once under `DRIVE_LOCAL_ROOT/<company>/<folder>/<file>`, id `local-<row id>` so retries never create a second copy), `google` (Drive v3; finds an existing copy by `appProperties.pt_doc_id` before creating, folders created on demand — **not verified against a live Drive yet, needs a service account and folder decision**), `disabled` (files are **Stored here only**). `DRIVE_FAULT=timeout|denied` injects failures for drills. Credentials stay server-side.
- The Drive id is immutable once set (DB trigger).

### Printing / pagination
Invoice PDF renders 30 lines per page with "Page x of y"; totals only on the last page (50 lines → 2 pages, tested 1/30/31/50/61). Reprint = stored issued bytes (ETag = SHA-256). Exact match to the customer's existing template is still pending the template (D-12).

### Screens
- `/documents`: counts (Pending / Failed / In Drive), status + type filters, search, links to the source record, open original, Retry on failed copies. Warehouse role has no access.
- Invoice page **Archive** panel: filed files with sync status, folder, **Add proof of delivery** upload (append-only).

### Database guards
Trigger `document_file_guard`: no DELETE; only sync bookkeeping (`sync_status`, `drive_file_id`, `sync_attempts`, `last_error`, `synced_at`, `version`) may change; `drive_file_id` cannot change once set. Unique (company, folder, filename).

### Evidence
- Migration `apps/api/migrations/versions/0010_documents.py`; service `apps/api/src/pro_trading/documents.py`; routes `apps/api/src/pro_trading/routes/documents.py`; contract `/documents*`, `/invoices/{id}/pod` (5 paths).
- `apps/api/tests/test_documents.py` (13 tests) + vendor filing test in `test_receiving.py`.
- `apps/web/e2e/documents.spec.ts`; screenshot `apps/web/screenshots/documents-1440.png`.

### Open
Live Google Drive verification (credentials, shared-drive vs My Drive, scope `drive.file`); customer's real invoice template; email attachment of POD; kiosk printing (not approved).
