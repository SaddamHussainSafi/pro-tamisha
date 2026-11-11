# Module 06-scanner: Toshiba one-click scanning and intake

**Priority:** P1 integration

## Business outcome
Toshiba 409CS on Windows PC -> NAPS2 compatible device -> hardened localhost bridge -> API scan job -> raw PDF/image. Manual upload fallback.

## Non-negotiable business rules
Device integration is unverified until office PoC; Chrome cannot access scanner unaided.

## Design and integration
Pairing, signed expiring capabilities, single-flight worker, output file sandbox, upload hash/dedupe, timeouts; do not bind 0.0.0.0.

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
No website can invoke scan unauthenticated; unplugged scanner yields manual-upload fallback; repeat upload no duplicate source.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

## Implementation notes (Module 06 build)
- **Migration** `0007_scanner.py`: `scan_devices` (status PENDING → ACTIVE → REVOKED, hashed pairing code and device token, fingerprint, heartbeat fields), `source_documents` (original bytes, sha256 unique per company, mime, size), `scan_jobs` (REQUESTED/SCANNING/UPLOADED/FAILED/CANCELLED/EXPIRED, channel BRIDGE|MANUAL, capability nonce/expiry, error code). Partial unique index `uq_scan_one_active_per_device` (one SCANNING job per device = single flight). Triggers keep documents append-only and jobs undeletable.
- **Service** `scanning.py`: file validation by magic bytes (PDF, PNG, JPEG, TIFF; 20 MB; `SCAN_MAX_BYTES`), HMAC-signed single-use capabilities (300 s, `SCAN_CAPABILITY_KEY`; production refuses the dev key with 503 `SCANNER_NOT_CONFIGURED`), device bearer auth with security audit events, job numbering `SCN-YYYYMMDD-NNNN`, sweep of stale REQUESTED/SCANNING jobs to EXPIRED (180 s scan timeout).
- **Routes** `routes/scans.py`: devices (list, pairing code, approve with fingerprint, revoke), jobs (list/detail/create/cancel), manual upload (raw body, dedupe by sha256), document content (inline, `CSP: sandbox`, nosniff, ETag), bridge endpoints (pair, heartbeat, claim, upload, fail).
- **Bridge** `apps/bridge/pro_trading_bridge` (stdlib only, see `apps/bridge/README.md`): 127.0.0.1-only HTTP server with Host and Origin allow-lists, NAPS2 driver via argument list in a private temp dir, FAKE driver for tests, heartbeat every 30 s.
- **Permissions:** `scan.create` (start/upload, warehouse-scoped), `scan.review` (read), `scan.device.manage` (pair/approve/revoke). Defaults: Owner/Manager all three; Sales create+review.
- **UI:** `/scan` Scan Center — *New scan* (location, document type, scanner, note; **Scan now** via bridge, **Upload a file** fallback), job list with status views (In progress / Received / Failed / Cancelled-expired) auto-refreshing while scans are open; *Scanners* tab (managers) to add a PC, show the pair command, approve by confirmation code, revoke with reason; `/scan/:id` detail with the original PDF/image inline, SHA-256 and failure guidance.
- **Not built here:** OCR extraction and review (Module 07). Scans never change stock, orders or invoices.
- **Which scans are read (2026-11-10):** sales orders, picking sheets (the office uses the same paper order sheet for both) and supplier documents are read automatically; "Other" is kept unread until someone with `scan.review` presses **Read this scan**. Single-scan answers (upload, duplicate, detail) carry `page_count` from the stored document (pdfinfo / TIFF frames), so the result card shows pages before any reading; lists leave it empty.

## Failure recovery
| Failure | What the system does | Operator action |
|---|---|---|
| Bridge not running / PC off | Browser gets no answer → message "Can't reach the scanner PC"; the requested job is cancelled | Start the bridge, or **Upload a file** |
| Scanner unplugged / driver missing / jam timeout / empty feeder | Bridge reports `SCANNER_NOT_FOUND`, `DRIVER_MISSING`, `SCAN_TIMEOUT`, `NO_PAGES`; job FAILED with plain-language hint | Fix the scanner and scan again, or upload |
| Browser closes mid-scan | Job stays SCANNING until upload or the 180 s timeout → EXPIRED | Start a new scan |
| Capability replay / expired / other device | 403 `CAPABILITY_USED` / `CAPABILITY_EXPIRED` / `CAPABILITY_INVALID`; audited | None (start a new scan) |
| Foreign website calls the bridge | 403 `ORIGIN_REJECTED` / `HOST_REJECTED` before anything runs | None |
| Same file uploaded twice | Same sha256 → existing job returned (`duplicate: true`, its own document type), one source document. The result card shows that scan's reading (review, or **Open reading**); if it was never read (e.g. a picking sheet uploaded before 2026-11-10) it offers **Read this scan** | Read it, or open it |
| Wrong or oversized file | 415 `UNSUPPORTED_FILE` / `FILE_TYPE_MISMATCH`, 413 `FILE_TOO_LARGE`, 422 `EMPTY_FILE` | Convert to PDF/JPEG or split |
| Lost or stolen scanner PC | Revoke: token rejected at once (401 `DEVICE_REVOKED`), open jobs FAILED | Pair a replacement PC |
| Second scan while busy | Bridge 409 `BUSY`; DB index guarantees one SCANNING job per device | Wait, then scan |
| Storage/DB error during upload | Transaction rolls back; no partial document; job not UPLOADED | Retry |
