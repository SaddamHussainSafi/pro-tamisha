# Build log

No application code has been generated or executed by this planning pack. This log should be appended by Claude Code after each completed task.

## Template entry
- Date/build commit:
- Task/module:
- Files/migrations created:
- Decisions needed:
- Tests executed and exact results:
- Manual verification evidence:
- Risks/known issues:
- Next task:

---

## 2026-11-07 — Task 00 Foundation
- **Date/build commit:** 2026-11-07. The project is not a git repository yet, so there is no commit hash.
- **Task/module:** `claude/tasks/00-foundation.md` (Module 00).
- **Files/migrations created:**
  - `apps/api`: FastAPI package `pro_trading`:
    - core modules: config, db, models, permissions, security, deps, audit, outbox, idempotency, errors
    - routes: health, auth, warehouses, admin
    - entry points: seed, worker
  - Alembic migration `0001_foundation`.
  - API tests: `apps/api/tests` (37 tests).
  - API scripts: `scripts/e2e_fixture.py`, `scripts/export_openapi.py`.
  - `apps/web`: React 19 + Vite 8 + Tailwind 4 shell:
    - auth, MFA enrolment, warehouses
    - token test, Playwright smoke
  - `apps/worker/README.md`
  - `.github/workflows/ci.yml`
  - `contracts/openapi.implemented.json` (generated; drift test)
  - `docs/decisions/ADR-0001-foundation.md`
  - `examples/.env.example` (Module 00 keys added)
- **Decisions needed:**
  - ADR-0001 sign-off.
  - Default role matrix: MANAGER currently lacks `warehouse.manage`.
  - Official logo/colour verification (`design/BRAND_VERIFICATION.md`).
  - No D-01..D-20 decision was made or assumed.
- **Tests executed and exact results:**
  - API checks (`apps/api`, Postgres 16 `tami_test` + Redis local):
    - `uv run pytest` → `37 passed in 3.12s`
    - `uv run ruff check src tests migrations scripts` → `All checks passed!`
    - `uv run ruff format --check src tests migrations` → `30 files already formatted`
    - `uv run mypy` → `Success: no issues found in 21 source files`
  - Web checks (`apps/web`):
    - `npm run lint` → clean
    - `npm run typecheck` → clean
    - `npm test` → `Tests 14 passed (14)`
    - `npm run build` → OK (~292 kB JS)
    - `npm run e2e` (Playwright, real API + DB) → `4 passed`
- **Manual verification evidence:**
  - `apps/web/screenshots/shell-1440.png` was inspected: branded sidebar, warehouse switcher, sign-out.
  - `/health/ready` → `{"status":"ok","checks":{"database":{"ok":true,"migration":"0001"},"redis":{"ok":true}}}`.
- **Risks/known issues:**
  1. On macOS, uv can create a *hidden* editable `.pth` that Python skips. The workarounds are already applied:
     - `chflags nohidden .venv/lib/python3.12/site-packages/*.pth`
     - pytest `pythonpath`
     - `uvicorn --app-dir src`
  2. CI has not run yet (no remote repository).
  3. Visual checks at 1366/1024/390 are deferred to task 00b.
  4. shadcn/ui is not installed yet.
  5. The logo is text-only until authorized assets exist.
- **Next task:** `00b-design-system`, then Module 01 catalog, pending approval.

## 2026-11-07 — Task 00b Design system (preview)
- **Scope:** UI tokens plus components, and four mock screens on synthetic data only (`/preview/*`, labelled "Design preview — sample data").
  - No backend or table changes.
  - No invoice PDF layout (D-12).
- **Built (apps/web):**
  - `lib/decimal.ts`: BigInt decimal math for UI money (half-up).
  - Components:
    - `ui/Icon.tsx`
    - `ui/primitives.tsx`: StatusChip, KpiCard, FilterBar, PageHeader, Empty/Error/Loading states, ConfirmDialog
    - `ui/DataGrid.tsx`: sortable, sticky header, arrow/Enter keyboard
    - `ui/NumericInput.tsx`: decimal-only, unit label, OCR-confidence flag
  - `components/Shell.tsx` rewritten: collapsible icon sidebar, mobile drawer, skip link, topbar with search "/", warehouse, scanner status, user, sign-out.
  - Screens:
    - Overview
    - Orders (data/loading/empty/error states)
    - Invoice editor: finalize disabled pending D-01/D-03; tax pending D-03
    - Vendor receiving split review: approve blocked while low-confidence lines exist; freight pending D-04
- **Brand/accessibility finding:**
  - White text on #198C43 is 4.3:1, which fails WCAG AA.
  - Text-bearing green surfaces use `brand.primaryHover` #116C33. #198C43 is kept for bars, icons and borders.
  - Warning-coloured text is replaced by ink text plus a warning icon or border.
  - Needs brand-owner acknowledgement (`design/BRAND_VERIFICATION.md`).
- **Tests executed and exact results (`apps/web`):**
  - `npm run lint` → clean
  - `npm run typecheck` → clean
  - `npm test` → `Tests 20 passed (20)`
  - `npm run build` → OK (328.59 kB JS)
  - `npx playwright test` → `11 passed`. Covers:
    - 4 screens × 1440/1366/1024/390: no horizontal overflow, screenshots in `apps/web/screenshots/`
    - axe WCAG 2.2 AA on all screens
    - invoice math 41.35 × 1.15 → CAD 47.55
    - remove-line confirm and Esc
    - orders filter and keyboard open
- **Fixes found by tests:**
  - Absolutely positioned sr-only labels escaped table scroll containers; wrappers are now `relative`.
  - Bar chart heights collapsed to 0.
  - `<dl>` contained a non-dt/dd child.
- **Not done / blockers:**
  1. Logo download/verification needs permission. A text wordmark is used.
  2. No visual sign-off is claimed; screenshots await owner review.
  3. At 1024px the topbar warehouse select is cramped.
- **Next:** Module 01 (catalog), pending explicit approval.

## 2026-11-07 — Module 01 Catalog, units and aliases
- **Scope:**
  - Products, categories, aliases and unit profiles (variable case weights).
  - Weight calculator.
  - Products UI.
- **Actors and invariants:**
  - Owner/manager manages; any role with `catalog.read` views.
  - SKU and stock unit are immutable. Conversions are immutable (add new, deactivate old).
  - Unknown piece/box weight is never guessed. Decimal only.
- **Built (apps/api):**
  - Migration `0002_catalog`: 4 tables and the immutability trigger.
  - `units.py`: Decimal conversion engine.
  - `routes/catalog.py`: 11 endpoints, each with idempotency, authz + MFA, audit and optimistic version.
  - Synthetic Cassava/Plantain/Rice seed.
  - No outbox: the catalog has no external IO.
- **Built (apps/web):**
  - `/products` list: search, status and category filters, cursor paging.
  - Create form.
  - Detail page: edit with 409 conflict state, profiles, aliases.
  - Weight calculator with an "Estimated — confirm on scale" badge and a CAD amount.
- **Contract:**
  - `contracts/openapi.yaml` updated with the catalog paths and schemas.
  - `contracts/openapi.implemented.json` regenerated; the drift test passes.
- **Acceptance:**
  - 7 boxes (2×20 + 2×30 + 2×40 + 1×25 lb) → **205.0000 lb / 92.9864 kg**, × 1.50 → **CAD 307.50**. Verified in unit, integration and E2E tests.
  - A PIECE with no weight → 422 `UNKNOWN_CONVERSION` with the group index.
  - Note: 205 cannot be reached with 20/30/40 lb boxes alone (every sum is a multiple of 10), so the INV-006 25 lb case is used.
- **Tests executed and exact results:**
  - `apps/api`:
    - `ruff check` clean, `ruff format` clean, `mypy src` clean (23 files).
    - `pytest` → **78 passed**: 11 units, 30 catalog integration (authz, MFA, cross-company isolation, idempotency replay/reuse, concurrent patch [200, 409], trigger, audit, failure injection), plus the earlier suites.
  - `apps/web`:
    - `lint` clean, `typecheck` clean.
    - `vitest` 23 passed.
    - `build` OK.
    - `playwright` **13 passed**, including the new `e2e/catalog.spec.ts`: owner MFA → create Cassava → 205 lb / CAD 307.50 → unknown-piece rejection; warehouse user read-only; axe WCAG 2.2 AA clean.
- **Fixes found by tests:**
  - The trigger referenced `NEW.product_id` on `products` ("record new has no field product_id"); fixed by splitting it per table.
  - LIKE wildcards in search are now escaped.
  - The loading skeleton had `aria-label` without a role (axe `aria-prohibited-attr`); `role="status"` added.
- **Failure recovery:** documented in `docs/modules/01-catalog.md`. Failure injection: an audit crash mid-create rolls everything back, and a retry with the same key succeeds once.
- **Screenshots:** `apps/web/screenshots/catalog-weight-1440.png`, `catalog-unknown-1440.png`.
- **Deferred:** lots, stock per warehouse and expiry dates go to Module 03. Bulk operations are not built yet.
- **Next:** Module 02 (pricing), pending approval.

## 2026-11-07 — Module 02 Pricing and versioning
- **Built (apps/api):** migration `0003_pricing.py` (customer_groups, customers, price_book_versions, price_entries, trigger `price_reject_published_change`); `pricing.py` (CSV parse/validate, canonical hash, diff, resolve with CUSTOMER > GROUP > STANDARD); routes `customers.py`, `pricing.py` (versions, import/preview, entries, activate/cancel/discard, diff, template/export CSV, `/prices/resolve`); seed `seed_pricing` (PB-2026-01, synthetic customers C-1001..3).
- **Built (apps/web):** `/pricing` list + price check + CSV import (preview, diff, row errors, duplicate warning); `/pricing/:id` grid with filters, set/remove on drafts, activate/cancel/discard dialogs, stale banner, 409 states; `alert-success` utility.
- **Contract:** `contracts/openapi.yaml` adds customers, customer-groups, price-book and resolve paths; `openapi.implemented.json` regenerated.
- **Acceptance:**
  - Two versions differ: PB-2026-01 CAS-FZ LB 1.50 → new version 1.60; resolve at old time = 1.50, now = 1.60 (API + E2E).
  - Old invoice rate unchanged: published entries cannot be updated/deleted (trigger tested directly); superseded version still shows 1.50.
  - Duplicate CSV (reordered, reformatted) → 409 DUPLICATE_UPLOAD, no new version; identical content → NO_CHANGES.
  - Invalid unit (TON) → INVALID_UNIT; unit without profile (RICE-JAS PIECE) → row error.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean (27 files), pytest **94 passed** (12 pricing integration incl. permissions, cross-tenant, concurrent activation ×2, idempotent replay, failure injection; 3 pricing unit).
  - `apps/web`: lint, typecheck clean; vitest **28 passed**; build OK; Playwright **15 passed** incl. `e2e/pricing.spec.ts` (owner import → diff → activate → price check; sales read-only) with axe WCAG 2.2 AA clean.
- **Fixes found by tests:** link contrast (text-brand-primary → -hover), seed needed an approver for activation trigger, catalog E2E alias collided with new seed data (renamed).
- **Screenshots:** `apps/web/screenshots/pricing-import-1440.png`, `pricing-version-1440.png`.
- **Deferred:** invoice snapshot storage (Module 05); bulk percentage changes.
- **Next:** Module 03 inventory (autonomous per user instruction).

## 2026-11-07 — Module 03 Transactional inventory engine
- **Built (apps/api):** migration `0004_inventory.py` (lots, balances, append-only stock_movements, reservations, counts, count lines; trigger `inventory_reject_change`); service `inventory.py` (single `post()` path with sorted row locks, unique posting keys, no-negative rule, damaged companion lots, reservations, reconcile/rebuild); routes `routes/inventory.py` (stock, availability, movements, lots, opening balances, adjustments, damage, returns, reverse, lot status, reservations, counts submit/post/reject, reconciliation/rebuild, SSE `/inventory/events`); permissions inventory.count/reserve/reverse/reconcile; `seed_inventory` (4 synthetic lots in MAIN).
- **Built (apps/web):** `/inventory` (stock grid, ledger, counts, reconciliation tabs; opening balance + count panels; live SSE badge) and `/inventory/lots/:id` (balances, adjust/damage/status, ledger with reverse dialog); `lib/inventory.ts` helpers + tests.
- **Contract:** `contracts/openapi.yaml` adds 20 inventory paths; `openapi.implemented.json` regenerated.
- **Acceptance:**
  - Concurrent oversell rejected: 10 parallel −6 on stock 8 → exactly 1 success, 9× 409, final 2 (INV-004); concurrent reservations likewise.
  - 100 repeated requests with one key → identical response, one movement.
  - Rebuild after corrupting cached balances → equals ledger; reconciliation then shows no mismatches.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **109 passed** (15 inventory integration: units/whole-qty, replay ×100, concurrency ×3, damage/return/reverse, refused reversal, count + stale count, rebuild, append-only triggers, lot status/expiry, permissions/warehouse scope/cross-tenant, failure injection, SSE ready).
  - `apps/web`: lint, typecheck clean; vitest **31 passed**; build OK; Playwright **18 passed** incl. `e2e/inventory.spec.ts` (warehouse count read-only; owner adjust → oversell refused → damage → reverse → post count → reconciliation all match; sales read-only), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** trigger referenced `NEW.lot_code` on balances (PL/pgSQL evaluates both AND operands) → nested IF per table; error envelope assertions nested under `error`.
- **Screenshots:** `apps/web/screenshots/inventory-stock-1440.png`, `inventory-lot-1440.png`.
- **Deferred / open:** D-04 costing method (landed cost column only); SSE thread-per-stream (documented); GRN and invoice postings come in Modules 04/05.
- **Next:** Module 04 (autonomous).

## 2026-11-07 — Module 04 Sales order drafts and picking
- **Built (apps/api):** migration `0005_sales_orders.py` (orders, lines, append-only amendments, picking attempts/lines; guard triggers; one-open-sheet partial unique index); service `orders.py` (line quantities, derived status, validation); routes `routes/orders.py` (list/get/create/update draft, confirm with frozen prices, cancel, cancel line qty, close with shortage, open/list/get sheets, record, submit, verify with dispositions, return, abort); `seed_orders`.
- **Built (apps/web):** `/orders`, `/orders/new`, `/orders/:id`, `/picking`, `/picking/:id` (QR code, print, record/NO/shortage reasons, verify BACKORDER/CANCEL); `lib/orders.ts` + tests.
- **Contract:** `contracts/openapi.yaml` sales-order and pick-attempt paths (14); `openapi.implemented.json` regenerated.
- **Acceptance:**
  - 15 requested, 10 supplied → 5 pending, flagged backorder; next sheet requests 5 (API + E2E).
  - NO (0 packed) stays pending, never auto-cancelled; cancel needs close permission + reason.
  - Warehouse cannot change price, confirm, close or verify; confirmed rate change blocked by trigger.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **120 passed** (11 order tests: acceptance, frozen price/triggers, role limits, validation/version conflict, pick rules, shortage cancel/close, abort, replay, concurrent pick open + numbering, scope/tenancy, failure injection).
  - `apps/web`: lint, typecheck clean; vitest **35 passed**; build OK; Playwright **21 passed** incl. `e2e/orders.spec.ts` (sales create→confirm→sheet; warehouse packs 10/15 with reason→submit; owner verifies→5 backorder→cancel qty), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** verify sent dispositions keyed by pick line instead of order line (422 INVALID_LINE); failed save wiped picker's typed quantities (now reload only on 409); `<dl>` without dt/dd (axe).
- **Screenshots:** `apps/web/screenshots/orders-detail-1440.png`, `orders-list-1440.png`, `picking-sheet-1440.png`.
- **Deferred:** scanned order source (Module 06/07), lot/weight capture on sheet UI (API accepts `lot_id`, `weight_lb`).
- **Next:** Module 05 invoicing (autonomous).

## 2026-11-07 — Module 05 Final invoices, partial dispatch and PDF
- **Built (apps/api):** migration `0006_invoicing.py` (sequences, invoices, lines, stored documents, email messages; immutability triggers); `invoicing.py` (compute/allocate/issue, gapless numbering, catch weight, email outbox + dev sink); `invoice_pdf.py` (deterministic provisional PDF); routes `routes/invoices.py` (preview, issue, list, detail, PDF with ETag, resend email); permission `invoice.read`; settings `invoice_tax_verified`, `invoice_prefix`; worker handler `invoice.email`; seed verified sheet for C-1002 (7 boxes, bill by weight).
- **Built (apps/web):** `/invoices`, `/invoices/:id`, `/picking/:id/invoice`; pick sheet Prepare/View invoice; order page invoices list; `lib/invoices.ts` + tests.
- **Contract:** `contracts/openapi.yaml` invoice paths (preview, issue, list, detail, pdf, email); `openapi.implemented.json` regenerated.
- **Acceptance:** INV-001 (10 of 15 → invoice A, stock −10, 5 remaining); INV-002 (next 5 → invoice B, total −15); INV-003 (10 concurrent retries → one invoice, one movement per line); INV-004 (stock 8, two picks of 6 → one issues, other 409, no number consumed); INV-005 (price change → amounts + PDF bytes identical); INV-006 (205 lb × 1.50 = CAD 307.50, API + E2E).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **132 passed** (12 invoicing: INV-001…006, weight rules, gates, tax unverified, permissions/scope/tenancy, failure injection with gapless retry, email once/resend/no email).
  - `apps/web`: lint, typecheck clean; vitest **39 passed**; build OK; Playwright **23 passed** ×3 consecutive runs incl. `e2e/invoices.spec.ts` (sales preview read-only; owner weights → 307.50 → issue → PDF served → sheet links invoice → list), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** success chip text failed contrast on canvas (now ink text + green icon); pick sheet double initial load could wipe a typed quantity (stale response now ignored) — was the source of an intermittent orders E2E failure.
- **Screenshots:** `apps/web/screenshots/invoices-issue-1440.png`, `invoices-detail-1440.png`, `invoices-list-1440.png`.
- **Deferred / open:** D-02 credit/void, D-03 tax rules, D-12 final PDF layout, D-15 email provider.
- **Next:** Module 06 (autonomous).

## 2026-11-08 — Module 06 Scanner bridge and intake
- **Built (apps/api):** migration `0007_scanner.py` (scan devices, source documents, scan jobs; single-flight partial unique index; append-only triggers); service `scanning.py` (file validation, signed single-use capabilities, device auth, numbering, sweep); routes `routes/scans.py` (devices, jobs, uploads, document content, bridge pair/heartbeat/claim/upload/fail); settings `scan_max_bytes`, `scan_capability_key`, TTLs. `seed.py` now re-syncs default role grants for an existing company (fixed live 403s on orders/invoices after new permissions were added).
- **Built (apps/bridge):** stdlib localhost bridge (`pair`, `run`), Host/Origin gate, NAPS2 + fake drivers, heartbeat; `README.md`.
- **Built (apps/web):** `/scan` Scan Center (Scan now via bridge, upload fallback, status views, Scanners tab with pair/approve/revoke), `/scan/:id` detail with inline original; nav enabled; `api.ts` raw-body upload; `lib/scans.ts` + tests.
- **Contract:** `contracts/openapi.yaml` scan-device, scan job, upload, document and bridge paths (15); `openapi.implemented.json` regenerated.
- **Acceptance:** no website can scan unauthenticated (SEC-002: foreign origin/host rejected, capability required, single use, device-bound); unplugged scanner → FAILED with hint and manual upload works (API + E2E); repeat upload → one source document.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean (incl. bridge), pytest **145 passed** (13 scanner: pairing + fingerprint, end-to-end bridge scan + capability single use, SEC-002, expiry + sweep, single flight, unplugged fallback, dedupe, file validation, permissions/scope/tenancy, revoke, cancel, failure injection, append-only).
  - `apps/web`: lint, typecheck clean; vitest **49 passed**; build OK; Playwright **26 passed** incl. `e2e/scans.spec.ts` (owner pairs the real bridge process and approves by confirmation code, wrong code refused; sales scans through the bridge → received → inline PDF; upload, duplicate, rejected .txt; unplugged scanner → Failed view), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** claim checked nonce before expiry/used (wrong error code); audit query used non-existent `detail` column; bridge exception renamed `BridgeApiError` (N818); E2E sign-in race before navigation; screenshot review: PDF preview blank because Chrome will not run its PDF viewer under CSP `sandbox` (PDFs now get `default-src 'none'; frame-ancestors 'self'` without sandbox, images/TIFF stay sandboxed — tested); header "Scanner: not connected" was hard-coded (now from device heartbeats, no localhost probing on every page).
- **Screenshots:** `apps/web/screenshots/scanners-1440.png`, `scans-1440.png`, `scan-detail-1440.png`.
- **Deferred / open:** office PoC with the Toshiba 409CS + NAPS2 profile; Windows service installer for the bridge; OCR (Module 07).
- **Next:** Module 07 OCR (autonomous).

## 2026-11-08 — Module 07 OCR reading and review
- **Built (apps/api):** migration `0008_ocr.py` (extractions, fields, corrections; append-only / immutable-read / final-state triggers); `ocr.py` (fake + Document AI providers, PT-OS-1 template mapping via SKU/alias/name, confidence threshold, outbox-driven reading with transient retry, printable order-sheet PDF); routes `routes/ocr.py` (list/detail, read again, resolve CONFIRM/CORRECT/REMOVE, add missed line, accept → DRAFT order, reject, accuracy, order sheet); `insert_order` extracted from order creation; worker handler `ocr.extract`; scans auto-queue reading for sales-order scans; settings `ocr_provider`, `ocr_confidence_threshold`, Document AI endpoint/credentials, timeout. `scripts/e2e_fixture.py --sheet` writes a synthetic hand-filled sheet.
- **Built (apps/web):** Scan Center **To review** tab (accuracy KPI, lines checked, printable order sheet download, status views); `/scan/review/:id` side-by-side review (original + lines, confirm/correct/remove/add, customer, "checked every line", create draft / reject / read again); reading panel on scan detail; `lib/ocr.ts` + tests.
- **Contract:** `contracts/openapi.yaml` `/ocr/*` (8 paths, replaces placeholder `/scans/jobs/{jobId}/review`); `openapi.implemented.json` regenerated.
- **Acceptance:** OCR never creates an issued invoice, reservation or stock movement (counts asserted unchanged; accept → DRAFT only); unclear/unknown lines cannot be confirmed and need manual correction; original kept and shown beside the lines; accuracy measured as exact-field and only claims the 95% target with ≥50 samples (real-handwriting measurement pending office PoC — D-18 full review stays).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **157 passed** (12 OCR: read→review→accept draft-only, DB immutability, photo/no-text + manual line, accuracy maths, reject + re-read supersedes, provider outage retries → FAILED → re-read, engine disabled, vendor docs not auto-read, permissions/scope, worker registration, order-sheet round trip).
  - `apps/web`: lint, typecheck clean; vitest **55 passed**; build OK; Playwright **27 passed** incl. `e2e/ocr.spec.ts` (sales uploads synthetic sheet → worker reads → queue → confirm clear line, correct unclear line, remove unknown write-in → draft order with 2 lines), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** review queue loaded before the worker finished and never refreshed (now polls); quantities displayed as `12.0000` (trimmed).
- **Screenshots:** `apps/web/screenshots/ocr-queue-1440.png`, `ocr-review-1440.png`, `ocr-draft-order-1440.png` (headless Chromium does not render the PDF in the iframe; verified in real Chrome in Module 06).
- **Deferred / open:** 50-sample real handwriting accuracy run; Document AI provider live verification (needs credentials); QR/template alignment for skewed scans.
- **Next:** Module 08 (autonomous).

## 2026-11-08 — Module 08 Vendor shipments and GRN
- **Built (apps/api):** migration `0009_receiving.py` (vendors, shipments, shipment documents, declared lines, GRN lines, landed costs; append-only / final-state / withdraw-only triggers); `receiving.py` (numbering, declared-vs-counted comparison incl. document conflicts, exactly-once GRN posting into SALEABLE + DAMAGED lots); routes `routes/receiving.py` (vendors, shipments, documents attach/upload, declared lines with vendor alias memory, count lines, costs, submit/reopen/post/cancel); vendor documents readable by receiving roles; seed vendors V-ACC / V-KIN / V-LIM (synthetic).
- **Built (apps/web):** `/receiving` list, `/receiving/:id` shipment workspace (comparison, original documents, declared, count, costs, approve dialog with acknowledgement), `/vendors`; nav enabled; `lib/receiving.ts` + tests. Shared `ConfirmDialog` now uses a unique title id (two dialogs on one page shared `confirm-title`, so the accessible name was wrong).
- **Contract:** `contracts/openapi.yaml` `/vendors*` + `/shipments*` (17 paths, replaces `/grns/{grnId}/post` placeholder); `openapi.implemented.json` regenerated.
- **Acceptance:** supplier declares 25, warehouse accepts 23 + damaged 2 → only 23 saleable posted (damaged in separate lot); retry/concurrent posts cannot double-add (API + E2E).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **166 passed** (9 receiving).
  - `apps/web`: lint, typecheck clean; vitest **61 passed**; build OK; Playwright **30 passed** incl. `e2e/receiving.spec.ts`, axe WCAG 2.2 AA clean.
- **Fixes found by tests:** horizontally scrolling tables were not keyboard-focusable (axe); comparison table cramped in side column (moved full width after screenshot review); name clash `submitCount` (inventory) vs receiving.
- **Screenshots:** `apps/web/screenshots/receiving-count-1440.png`, `receiving-list-1440.png`, `receiving-posted-1440.png`, `vendors-1440.png`.
- **Deferred / open:** landed-cost allocation rule (decision needed); OCR of vendor documents into declared lines (manual entry for now); currency conversion of vendor costs.
- **Next:** Module 09 (autonomous).

## 2026-11-08 — Module 09 Documents, Drive archive, POD
- **Built (apps/api):** migration `0010_documents.py` (`document_files` archive index + `document_file_guard` trigger); `documents.py` (filing for invoice PDFs / scans / vendor docs / POD, folder layout, local/google/disabled Drive providers with fault injection, outbox handler `document.drive_sync`, retry); hooks in invoice issue, scan uploads (manual + bridge) and shipment documents; routes `routes/documents.py` (list/detail/content/retry-sync, `POST /invoices/{id}/pod`); permissions `document.read` / `document.manage` (manager, sales, finance; owner); settings `DRIVE_PROVIDER`, `DRIVE_LOCAL_ROOT`, `DRIVE_ROOT_FOLDER_ID`, `DRIVE_CREDENTIALS_FILE`, `DRIVE_TIMEOUT_SECONDS`, `DRIVE_FAULT`; worker registers the Drive handler.
- **Built (apps/web):** `/documents` archive page (sync KPIs, filters, Retry), invoice-page Archive panel with POD upload; nav enabled; `lib/documents.ts` + tests.
- **Contract:** `contracts/openapi.yaml` 5 paths; `openapi.implemented.json` regenerated.
- **Acceptance:** 50-line invoice paginates (2 pages, totals once); Drive timeout leaves the invoice committed with Pending sync → retries → Sync failed → Retry → In Drive; reprint returns the same issued bytes (invoice + archive endpoints, ETag).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **180 passed** (13 documents + 1 receiving filing: pagination, sync once + stable id, timeout failure injection + retry/replay/version conflict, denied → immediate fail + audit, disabled → local only, DB guards, POD append-only, scan filing, permissions/scope/tenancy).
  - `apps/web`: lint, typecheck clean; vitest **65 passed**; build OK; Playwright **32 passed** incl. `e2e/documents.spec.ts` (owner issues → archive panel → real worker copies → In Drive; POD upload; archive page; warehouse denied), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** test helpers (fixture import), folder assertion included "(synthetic)" suffix; size column wrapped.
- **Screenshots:** `apps/web/screenshots/documents-1440.png`.
- **Deferred / open:** live Google Drive verification (credentials + folder decision); real invoice template (D-12); POD in emails; kiosk printing.
- **Next:** Module 10 Payments (autonomous).

## Demo data loader (user request: "prefill everything with demo data")
- `apps/api/scripts/demo_data.py` fills the synthetic demo company through the real API (in-process client, signed in as the seeded demo employees; sessions revoked at end). Refuses production-like envs; idempotent (stops if C-1004 exists).
- Loads: 12 products, 6 customers, price book PB-2026-02 active + PB-2026-03 draft, 5 vendor shipments (3 posted incl. EAST, 1 counted awaiting approval, 1 open), opening balances, a damage write-off, a pending cycle count, 14 sales orders across every stage (7 invoiced, 1 with a shortage), 3 PODs, 3 scans (2 OCR order sheets).
- Verified on dev DB `tami`: demo company now 8 invoices / 16 orders / 5 shipments / 18 archived documents / 8 scans; all outbox events PROCESSED. Payments demo is added with Module 10.

## 2026-11-08 — UI restyle to the reference dashboards (owner request)
- **Request:** "make it look like this, everything" with the three reference images (Invowise, dosage, Finexy), using the ui-ux-pro-max skill. This overrides the Tamisha green palette (note added at top of `design/UI_DESIGN_REFERENCE.md`).
- **Tokens:** `design/tokens.json` schemaVersion 2 — orange primary `#D2441A` (4.5:1 on white), bright accent `#F26430` (decoration and large text only), near-black `#18181B` for active pills/primary buttons, grey canvas `#EFEFF1`, new `surface.subtle` and `state.info`. `theme.css` and `tokens.test.ts` updated to match.
- **Shell:** header with logo pill, top pill navigation (Overview, Orders, Picking, Invoices, Inventory, Receiving), search, warehouse switcher, bell and avatar; icon rail on the left for the other sections, design previews and sign-out; full-label drawer below 1280px. Inter loaded from Google Fonts.
- **Components:** borderless 20px cards, pill buttons/chips/inputs, soft status chips, grey table headers, new `CardTitle`, `Trend`, `Stat`, `Avatar`, `BarChart` (striped bars, one orange highlighted bar, dashed average, accessible table), accent `KpiCard`.
- **Overview (real):** greeting, figures from the API only (invoiced this month by `issued_at`, open orders, shipments to receive), 6-month invoiced chart, open orders, recent invoices table; cards hide when the role can't read that data. Nothing claims paid/overdue until Module 10.
- **Tests executed (apps/web):** typecheck, lint clean; vitest **66 passed**; build OK; Playwright **32 passed** incl. axe WCAG 2.2 AA and no horizontal overflow at 1440/1366/1024/390.
- **Fixes found by tests:** header too wide at 1366 (name hidden until 2xl); single-column grid overflow on phones; screen-reader text escaping table scroll boxes; rail label colliding with the OCR "Customer" field lookup; colour transitions caught mid-fade by axe (state changes on nav pills, rail and filter chips are now instant).
- **Flaky (pre-existing, not changed):** `scans.spec.ts` upload-fallback can see two `role=status` elements while the list reloads; passed on rerun.
- **Follow-up (same day, owner: "too text heavy, bigger text, more whitespace, bigger icons, proper dashboard"):** type scale up (`--text-sm` 15px, `--text-base` 17px) and spacing up 10% (`--spacing` 0.275rem) app-wide; 24px card radius; bigger nav pills, 48px rail icons, design previews moved into one rail popover. Overview rebuilt as a dashboard: big KPI cards (56px icon tiles, 40px figures, accent gradient card), large striped bar chart, new `DonutChart` (orders by stage; chart series tokens validated with the dataviz CVD/contrast checker) and `Progress` bars on striped tracks; fewer words. Playwright **32 passed**, axe clean; screenshots `apps/web/screenshots/dashboard-1440.png`, `dashboard-390.png`, `overview-live-1440.png`.
- **Follow-up (owner: "remove main warehouse or option to choose warehouse completely"):** removed the header/drawer warehouse picker and its saved choice (`pt.warehouse`); removed "Main warehouse" from preview screens. Scanner hint uses the first visible warehouse. Inventory page warehouse fields left in place (stock counts and opening balances need a warehouse). Playwright **32 passed**.
- **Follow-up (owner: "remove them too"):** Inventory page has no warehouse fields any more — removed the stock and ledger warehouse filters (lists show all warehouses the user can see), the Warehouse column, warehouse codes in count and lot headers, and the warehouse selects in Record count / Opening balance (they now use the first warehouse the user is allowed to count / adjust in). Playwright **32 passed** incl. inventory counts and opening balance.
- **Follow-up (owner: "remove all"):** warehouse/location codes, columns and pickers removed from Orders (list column, subtitle, New order field), Picking, Receiving (column, subtitle, "Receiving at" field, posting text), Scan Center (Location columns, New scan and Add scanner fields, job subtitle), OCR review (column, order-sheet location, subtitle) and invoice details. New records use the first warehouse the user is allowed to act in. Settings › Warehouses (admin list) kept. `e2e/scans.spec.ts` updated: Add-scanner has no Location field; the upload test waits for the list to finish loading (fixes the two-`role=status` flake). Playwright **32 passed** twice in a row; a third run had one timing flake in `documents.spec.ts` (warehouse role access), which passed 3/3 when rerun alone.

## 2026-11-08 — Full health check, Modules 00–09 (owner: "check if all modules are working fine")
- **Environment:** local Postgres 16 (`tami` dev, `tami_test`), Redis, uvicorn :8000, Vite :5173, Celery worker. `/health/ready` → database ok (migration `0011`), redis ok.
- **apps/api:** `uv run pytest` → **180 passed in 45.87s**; `mypy` → no issues in 42 files.
  - `ruff check` initially **failed**: 1 × E501 in `models.py`, in the unfinished Module 10 payment constraint.
  - `ruff format --check` initially **failed**: 4 files (`config.py`, `models.py`, `receiving.py`, `tests/test_documents.py`).
  - Fixed both with a string split and `ruff format`. Both checks are now clean. No behaviour change.
- **apps/web:** lint and typecheck clean; vitest **70 passed**; build OK (chunk-size warning only).
  - Playwright **32 passed (26.4s)**.
  - The first run had 1 failure (`inventory.spec.ts` warehouse count; nav link not found) and 2 skipped. Cause: the API files were reformatted mid-run and the `--reload` dev server restarted. The spec passes in isolation, and the full rerun was clean.
- **Scanner bridge:** imports OK; covered by API scan tests with the `fake` driver. A real Toshiba/NAPS2 run has still **not been verified**.
- **Module 10 Payments:** only migration `0011_payments.py` and its models exist. There is no service, routes, tests or UI, so the module is **not working yet**.

## 2026-11-08 — Development auto sign-in (owner: "disable password and login for now")
- **API:** setting `DEV_AUTO_LOGIN_EMAIL` (default empty = off) and hidden route `POST /api/v1/auth/dev-login` (not in the OpenAPI contract) that opens a normal server-side session for that employee with MFA marked verified, audited as `auth.dev_login`. Returns 404 when unset or when `APP_ENV` is staging/production; `Settings` refuses to load if it is set in staging/production. Shared `_start_session` helper with `/auth/login`.
- **Web:** when there is no session, dev builds (`import.meta.env.DEV`) call `/auth/dev-login` before showing the login page; automated browsers (`navigator.webdriver`) skip it so Playwright still exercises the real login.
- **Local switch:** `apps/api/.env` (git-ignored) sets `DEV_AUTO_LOGIN_EMAIL=owner@example.test` (demo owner). Remove the line to bring the login back.
- **Tests:** API ruff/mypy clean, pytest all passed (7 new in `tests/test_dev_login.py`: off by default, signs in owner without password/MFA + audit, unknown email 404, refused when production-like, settings refuse staging/production, absent from contract). Web typecheck/lint clean; Playwright **32 passed** with auto sign-in enabled on the dev API.
- **Follow-up (owner: Tamisha main colour #28A745, "apple" shade ramp):** `tokens.json` schemaVersion 3 — accent apple-400 `#28A745` (fills, chart highlight, progress, icons), primary apple-600 `#1B7A30` (text, links, focus, white-text buttons; 5.4:1), hover apple-700 `#125E23`; full ramp exposed as `--color-apple-50…950` and checked by `tokens.test.ts`. Chart series re-validated (green, blue, orange, violet). Note in `UI_DESIGN_REFERENCE.md` updated. Vitest **81 passed**, Playwright **32 passed** incl. axe.

## 2026-11-08 — Sidebar, Scan Center flow and recognized lines (owner mock-ups)
- **Sidebar (all pages):** grouped, collapsible left sidebar replacing the top pill bar + icon rail — Overview; Sales (Orders, Invoices, Payments*); Warehouse (Picking, Inventory ▸ Stock on hand / Stock ledger / Counts, Receiving, Scan Center); Master data (Products, Customers*, Vendors, Pricing); Analytics (Reports*, Documents); Settings; Design previews; user card with sign-out. Collapse state remembered per browser (`pt.sidebar.collapsed`). Drawer below 1024px. (*module not built: shown as "Soon".)
- **Scan Center:** new-scan card in three states — start (illustration, document type, scanner/device with online dot, note, Scan now / Upload a file), in progress (step list and step-based progress; no fake page counts), completed (preview, document details, OCR summary, Review lines / Scan details / Download original / Upload another / New scan). Tabs "New scan · To review · Scanners".
- **Auto-open recognized lines:** after a sales-order scan or upload the card polls the reading; when it reaches Needs review the **Recognized lines** editor (the review page, embedded) appears under the card and scrolls into view, so OCR mistakes are fixed in place and the draft order is created from there.
- **Recognized lines look** (review page, embedded review, receiving preview, real receiving comparison): icon header, low-confidence / needs-fixing chips, product tiles, "OCR n% · check against original" flags, Matched/Discrepancy chips, green summary bar. Product photos are not in the catalog, so tiles show an icon.
- **Tests:** `e2e/scans.spec.ts` — the fault-mode scan now starts with "New scan" (the card shows the finished upload first). Vitest **81 passed**; Playwright **32 passed** incl. axe WCAG 2.2 AA.
- **Screenshots:** `apps/web/screenshots/scan-center-new-1440.png`, `scan-center-done-1440.png`, `scan-center-lines-1440.png`, `receiving-recognized-1440.png`, `sidebar-collapsed-1440.png`.
