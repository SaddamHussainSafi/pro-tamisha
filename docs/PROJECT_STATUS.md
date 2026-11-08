# Project status: Pro Trading ERP (audit)

Audit date: 2026-11-08. Audited commit: **`5ab6265`** (`main`, pushed). This audit changed no application code; only this file was added.

**Sources.** Read-only inspection of:
- source code (`apps/api`, `apps/web`, `apps/bridge`) and migrations `0001`–`0013`;
- tests;
- `CLAUDE.md`, `docs/modules/*.md`, `tests/ACCEPTANCE_MATRIX.md`, `docs/02-eight-week-plan.md` (the "60-day roadmap": 8 weeks plus gates G0–G5) and `docs/decisions/OPEN_DECISIONS.md`;
- git status.

Checks were run where stated below.

> **Rule used.** "Done" means the backend invariant is implemented **and** an automated test exercises it. A screen existing is not enough. Anything not run in this audit is marked *recorded* (with its source) or *not run*.

---

## 1. Automated checks (this audit)

Run on a clean export of `5ab6265` (`git archive`), with its own PostgreSQL database `tami_audit`. This kept another session's uncommitted work out of the results and avoided touching its test database.

| Check | Command | Result |
|---|---|---|
| API lint | `ruff check src tests migrations scripts ../bridge` | **pass** |
| API format | `ruff format --check src tests migrations scripts` | **pass** |
| API types | `mypy src` (49 files) | **pass** |
| API tests | `pytest -q` against `tami_audit` | **223 passed, 0 failed, 0 skipped** (58.8 s). Tesseract tests ran because `tesseract` + `pdftoppm` exist on this Mac. |
| Web lint | `eslint src` | **pass** |
| Web types | `tsc -b --noEmit` | **pass** |
| Web unit | `vitest run` | **117 passed** (19 files) |
| Browser e2e | `playwright test` | **not run in this audit.** It needs the dev servers, which serve the working tree, and that tree holds another session's uncommitted migration `0014`. *Recorded:* 40 passed on `3a37c38` (BUILD_LOG). `5ab6265` changed only API OCR code. |
| CI | `.github/workflows/ci.yml` | **Not verified.** There is no `gh` CLI, so CI status on GitHub was not read. Two notes: CI has no `tesseract`, so the Tesseract tests are **skipped** there (`skipif`); and CI runs only the Playwright *smoke* suite. |

**Working tree (`git status`).** It is not clean, but none of the changes are from this audit. Another session is part-way through "scan document types": `models.py`, `schemas.py`, `web/src/api.ts`, `web/src/lib/scans.ts`, and a new `migrations/versions/0014_scan_document_types.py`. That work is not committed, not reviewed and not counted below. Untracked `dump.rdb` (Redis) is git-ignored.

---

## 2. Module status

Legend: ✅ implemented + tested · 🟡 partial · ❌ not implemented · ⛔ blocked (external access / hardware / documents / decision)

| # | Module (roadmap week) | State | Working and tested (examples) | Partial / missing | Blocked |
|---|---|---|---|---|---|
| 00 | Foundation: auth, MFA, RBAC, audit (W1, W7; G0/G5) | 🟡 | Login/logout/revocation, httpOnly cookie, MFA for privileged roles, permission denials audited (SEC-001), cross-warehouse write blocked (`test_auth`, `test_mfa`, `test_rbac`, `smoke.spec`) | **No generated TS types**: `web/src/api.ts` is hand-written. `contracts/openapi.yaml` lists 135 paths, the implemented spec 148: auth, warehouses, feature flags, audit events and health are missing from the YAML, and `/events` (SSE) is in the YAML but not built. No bulk operations or undo. Header search box does nothing. Feature-flag and audit-log APIs have no screens. A **new tab is signed out on its first save** (CSRF token per tab). | G0 not met: D-02, D-03, D-04 open |
| 01 | Catalog, units, aliases (W2) | ✅ | 205 lb for 7 mixed cases (INV-006); unknown conversion rejected; case sizes list (`test_units`, `test_catalog`, `test_case_sizes`, `catalog.spec`, `products.spec`) | Case-size weight is immutable by design: a change means adding a new size | D-09 product-specific conversions |
| 02 | Pricing, versions, CSV (W3) | ✅ | Versions differ and the old rate is unchanged (INV-005); duplicate upload; invalid unit; change from the product page via a draft (`test_pricing`, `pricing.spec`, `products.spec`) | none found | D-07 price basis per product |
| 03 | Inventory ledger (W2; G1) | ✅ | Concurrent oversell refused; 100 repeats return one result; rebuild = ledger (`test_inventory`) | Reconciliation is on demand only: **no scheduled reconciliation job**. The SSE stock stream test only checks the "ready" message. | D-05, D-10 |
| 04 | Sales orders + picking (W3; G2) | 🟡 | 15 ordered → 10 supplied → 5 pending; NO is not a cancellation; warehouse can't change price (`test_orders`, `orders.spec`) | G2 "returns/corrections don't rewrite originals" cannot be shown: no correction flow, returns not linked to invoices | D-06, D-10 |
| 05 | Invoicing, partial dispatch, PDF (W3; G1) | 🟡 | INV-001…006 all tested (`test_invoicing`); direct invoices (`test_direct_invoice`, `new-invoice.spec`) | **No void/credit-note flow** (`invoice.correct` permission has no handler). Email goes to a dev sink. Direct invoices skip the warehouse check by owner choice. | D-01, D-02, D-03, D-11; email provider |
| 06 | Scanner bridge (W1 spike, W4) | 🟡 | SEC-002, unplugged fallback, OCR-002 upload dedupe, scanner discovery round trip, all with the **fake driver** (`test_scans`) | `apps/bridge` has no tests of its own (driven from `test_scans`). Uncommitted `0014` by another session not assessed. | ⛔ D-14: real Toshiba/NAPS2 unverified |
| 07 | OCR read + review (W4; G3) | 🟡 | OCR-001 (draft only, never invoices), re-read versions, manual path; Tesseract engine (`test_ocr`, `test_ocr_tesseract`, `ocr.spec`) | **G3 accuracy on 50 real forms not measured** (`/ocr/accuracy` only does arithmetic). No QR/template-version detection (fixed `PT-OS-1`). Supplier documents are not read. | ⛔ D-18, real sample forms; Document AI unconfigured |
| 08 | Receiving / GRN (W2, W5) | 🟡 | GRN-001 (25 declared → 23 saleable + 2 damaged); concurrent posts add once; supplier ledger + payments (`test_receiving`, `test_supplier_ledger`, `receiving.spec`, `suppliers.spec`) | **GRN-002 tested with 1 replay + 4 concurrent, not 100.** Declared lines typed by hand (no supplier-document OCR). | D-04 costs; supplier-credit questions |
| 09 | Documents, Drive, POD (W5) | 🟡 | Drive timeout keeps the invoice and retries; same bytes on reprint; 50-line pagination (`test_documents`) | DOC-001 (real template) missing; POD not attached to emails; no golden-PDF test | ⛔ D-12 template, D-15 Google Drive |
| 10 | Payments, receivables, reminders (W7) | ✅ with placeholders | 700 paid on 1,250 → 550 open; duplicate confirmation blocked; no bank secrets; reminders once (`test_payments`, `payments.spec`) | Celery beat reminder job never verified live; reminder days/roles are placeholders | D-16; email provider |
| 11 | Focus migration (W6; G4) | ❌ | — | Not started (deferred by owner 2026-11-08). MIG-001 has no code and no test. | ⛔ D-13, D-17: no Focus sample |
| 12 | Analytics / reports (W7) | 🟡 | Revenue = issued invoices; stock flow = ledger = balances; reproducible filters; `report.read` scoping (`test_reports`, `reports.spec`) | "Less credits" can't be tested (no credits). **No SSE invalidation**, no materialized cache, CSV built in the browser (≤50 rows), no invoice/movement drill-down, no origin filter, supplier performance = quantities only. | D-04 (no profit), revenue definition |
| — | Owner extras (2026-11-08) | ✅ | Customers UI, Settings users/My account, supplier ledger/payments, direct invoices, Overview panels, card grid (`test_customers`, `test_staff`, `test_supplier_ledger`, `test_direct_invoice`, `customers/settings/suppliers/new-invoice/grid-view.spec`) | Inactive customers are not blocked on new orders. Overview panels have no dedicated e2e test (covered only by the page loading in smoke/axe runs). | owner questions in BLOCKERS |

**Acceptance matrix** (`tests/ACCEPTANCE_MATRIX.md`):
- Covered by tests: INV-001…006, GRN-001, OCR-001, OCR-002, SEC-001, SEC-002.
- Partly covered: GRN-002 (not 100 replays).
- **No test and no code:** MIG-001, DOC-001, **OPS-001** (backup/restore drill).

**Gates** (`docs/02-eight-week-plan.md`):

| Gate | Met? | Evidence |
|---|---|---|
| G0 | No | Business decisions D-02, D-03, D-04 are still open |
| G1 | Yes | Concurrency, retry and negative-stock tests pass |
| G2 | Partial | There is no correction flow |
| G3 | No | No 50-form accuracy measurement |
| G4 | No | Focus migration is deferred |
| G5 | No | No backup/restore scripts or drill; no monitoring set up |

`CLAUDE.md` "Do not do" list: no violations found.
- `float` appears only for PDF coordinates and OCR confidence.
- Stock changes go only through the ledger.
- Issued invoices are locked by a database trigger.
- OCR creates drafts only.
- No Focus import exists.

---

## 3. Blocked by external access, hardware, documents or decisions

| Blocker | Decision / need | What it blocks today |
|---|---|---|
| Toshiba scanner + NAPS2 on the office PC | D-14, office PC access | Only the fake bridge driver is tested. Real click-to-scan and **scanner discovery** are unverified. Manual upload works. |
| Real handwriting OCR accuracy | D-18, 50 real order sheets | G3 (≥95 % exact quantities on 50 real forms) has not been measured. Tesseract runs locally; Document AI is not configured. Every line is human-reviewed. |
| Focus V7 backup + migration scope | D-13, D-17, a sample backup | Module 11 has not started (deferred by the owner). MIG-001 and G4 cannot be done. |
| Real invoice PDF template | D-12, the template file | The PDF layout is a placeholder. DOC-001 is open. |
| Tax rules | D-03, accountant | Production-like environments refuse to issue invoices (`TAX_RULES_UNVERIFIED`). |
| Costing method | D-04 | No cost, margin or profit figures. Supplier balances are per currency, with no conversion. |
| Invoice correction (credit note / void) | D-02 | No correction flow exists, so "revenue = issued − credits" can't be tested with real credits. |
| Google Drive / Workspace | D-15, account | `DRIVE_PROVIDER=local` only. |
| Email provider | provider + credentials | A dev sink only; reminders and invoice emails are not delivered. |
| Payment rules | D-16 | Reminder days, wording and verifier roles are placeholders. |
| Retention / privacy | D-19 | No retention jobs. |
| Owner questions logged 2026-11-08 | see `docs/BLOCKERS.md` | Supplier credits for damaged/rejected goods, supplier currencies, direct-invoice controls, inactive customers, revenue definition, "origin" filter. |

---

## 4. Critical bugs and security risks

This is a read-only review of the code at `5ab6265`. Every finding names a file and line.
- Re-checked in code during this audit: findings 1 (no MFA attempt limit), 2 (no rate limiting anywhere), 3 (`cookie_secure` defaults to false, `app_env` defaults to development) and 4 (random idempotency key per request).
- No Critical issue was found.

| # | Severity | Finding | Where | Fix |
|---|---|---|---|---|
| 1 | **High** | **The MFA code can be guessed without limit.** A wrong code is audited but never counted, and there is no throttle. Anyone who knows a privileged user's password can brute-force the 6 digits. | `routes/auth.py:190-207`, `security.py:72-84` | Count failures per employee and session; revoke after ~5; add a per-IP throttle |
| 2 | Medium | **No rate limiting at all.** Login lockout is per account only. A locked account answers 423, not 401, which reveals that the email exists. Unauthenticated bridge pairing and device-auth failures each write an audit row, so the audit table can be flooded. | `auth.py:48-87,69-74`; `scans.py:617-640`; `scanning.py:278-286` | Per-IP limits on `/auth/*` and `/scan-bridge/*`; return 401 for locked accounts |
| 3 | Medium | **Production settings fail open.** `app_env` defaults to `development` and `cookie_secure` to false, and staging/production don't force it on. A deploy that forgets `APP_ENV` gets API docs, placeholder keys and a cookie that can be sent over plain HTTP. | `config.py:11,16`; `auth.py:142` | Require `APP_ENV`; refuse to start in staging/production if `cookie_secure` is false or keys are placeholders |
| 4 | Medium | **Idempotency keys are random per request**, so a double submit or a network retry from the browser is never deduplicated. Ledger rules (posting keys, state checks, `ALREADY_INVOICED`) protect stock and invoices. **Supplier payments** have only an application check before the insert, with no lock and no unique index, and a payment with no reference has no check at all. | `web/src/api.ts:706`; `routes/suppliers.py:94-108`; `models.py:1604-1616` | One key per form submit, reused on retry; partial unique index `(company_id, vendor_id, lower(reference)) WHERE voided_at IS NULL` |
| 5 | Medium | **First MFA enrolment needs only the password.** This applies to seeded owners and to anyone right after an admin's "Reset two-step": someone with the password can enrol their own authenticator. | `auth.py:171-187`; `employees.py:236-251` | One-time enrolment code from an admin, or re-check the password at enrolment |
| 6 | Medium (usability) | **The CSRF token lives in per-tab `sessionStorage`.** A new tab's first save fails with `CSRF_FAILED` and signs the user out. Signing in again then kills the other tab. Not a security hole. | `api.ts:685-689`; `deps.py:96-99` | Session-wide token (`GET /auth/csrf`, HMAC of the session) |
| 7 | Low | The report timezone is inlined into SQL. It is guarded by a pattern at the route, but not inside the service. An unknown but well-formed zone causes a 500. | `reports.py:161`; `routes/reports.py:34,53-54`; `payments.py:54` | Validate inside the service; map unknown zones to a clear error |
| 8 | Low | OCR denial of service: `pdftoppm` has no page cap; Pillow's decompression-bomb error is not caught. | `ocr.py:272-288` | `-l` page cap; catch `DecompressionBombError` |
| 9 | Low | The pairing code and scan capability are stored in plain text inside idempotency replay records, although the code itself is stored hashed. | `scans.py:246-249,455`; `_common.py:58` | Leave secrets out of stored replay bodies |
| 10 | Low | Archive file downloads have no Content-Security-Policy (scans have one). Some non-Latin filenames cause a 500 in the download header. An upload is read before the permission check on receiving. | `documents.py:117-133`; `scanning.py:75`; `receiving.py:661/671` | Add the CSP; RFC 5987 `filename*`; authorize first |
| 11 | Low | The bridge URL check accepts `localhost.evil.com`. `chmod` on the bridge secret file does nothing on Windows. Missing keys fail on first use, not at startup. Feature flags are readable by any signed-in user. An unapproved bridge can update its status. `X-Request-Id` is echoed back untruncated. | `client.py:34,105-108`; `security.py:49-58`; `admin.py:16`; `scans.py:665`; `main.py:108` | Tighten each one |

**Checked and fine:**
- **Secrets:** none committed. `.env` and `dump.rdb` are git-ignored; no `checkpoints/` folder is tracked.
- **Dev auto sign-in:** refused in staging/production at both startup and the route. Locally it is on in `.env`; the server binds to 127.0.0.1.
- **Passwords and sessions:** argon2 passwords; httpOnly, SameSite=Strict cookie; idle and absolute session expiry; TOTP codes can't be replayed; privileged permissions need MFA.
- **CSRF:** every signed-in change is checked.
- **Authorization:** every handler has a permission or device check, and every lookup sampled filters by company.
- **Injection and files:** no other built SQL; subprocesses are called without a shell; archive paths are confined; uploads have a size cap and real type sniffing.
- **Web:** no `dangerouslySetInnerHTML`; CSV formula escaping on both sides.
- **Money and stock:** Decimal on the API, BigInt in the web; sorted row locks on stock and payments.
- **CORS:** a single origin, plus a server-side origin check.

**Correctness gaps (not security):**
- Inactive customers can still be ordered for, invoiced and paid.
- `invoice.correct` exists as a permission with no handler.
- The SSE stock-stream test does not prove that events are delivered.

---

## 5. Prioritized remaining work

Ordered by risk to real use, then by the roadmap gates. **Deps** = what must come first. ⛔ = cannot start without an outside input.

### P0: before anyone outside the dev machine uses it
- [ ] **MFA attempt limit + rate limiting** on `/auth/*` and `/scan-bridge/*`; locked account returns 401. *Deps: none (Redis is already there for counters).* (Security 1, 2)
- [ ] **Fail-closed production config** (`APP_ENV` required, `cookie_secure`, no placeholder keys, keys checked at startup). *Deps: none.* (Security 3, 11)
- [ ] **Stable idempotency key per form submit** + supplier-payment unique reference index (new migration). *Deps: commit or abandon the other session's `0014` first, so migration numbers don't collide.* (Security 4)
- [ ] **Session-wide CSRF token** (new-tab sign-out). *Deps: none; a task card for it already exists.* (Security 6)
- [ ] **Safer first MFA enrolment** (admin code or password re-check). *Deps: owner choice of method.* (Security 5)

### P1: roadmap gate evidence
- [ ] **OPS-001 / G5: backup + restore drill**: scripts plus a test that restores into a scratch database and checks foreign-key, ledger, invoice and allocation invariants. *Deps: none.*
- [ ] **GRN-002 with 100 replays** (cheap test). *Deps: none.*
- [ ] **Scheduled ledger reconciliation job** + alert on drift (module 03), and **live check of the Celery beat jobs** (outbox, reminders). *Deps: none.*
- [ ] **Contract in sync**: add the 13+ missing paths to `contracts/openapi.yaml`, drop or build `/events`, generate TS types (module 00). *Deps: none.*
- [ ] ⛔ **Invoice correction (void / credit note)**, then report credits and G2. *Deps: D-02 (+ D-03 tax).*
- [ ] ⛔ **G3: OCR accuracy on 50 real order sheets** (Tesseract is now available locally). *Deps: D-18 + the real forms.*
- [ ] ⛔ **Real Toshiba scan + discovery on the office PC.** *Deps: D-14, office PC access.*

### P2: features still in the owner's queue or spec
- [ ] **Global search box** (owner queue #1). *Deps: none.*
- [ ] **Supplier-document OCR into declared lines** (owner queue #2, module 08). *Deps: the other session's scan document types (`0014`); D-18.*
- [ ] Report drill-down to invoices/movements, server-side CSV, SSE invalidation (module 12). *Deps: `/events` decision above.*
- [ ] Owner rules: inactive customers on new orders, direct-invoice controls, supplier credits for damaged goods, supplier currencies. *Deps: owner answers (BLOCKERS).*
- [ ] Low security items 7–11; filter-bar search box overflowing at phone width. *Deps: none.*
- [ ] ⛔ Real invoice PDF template (D-12), Google Drive (D-15), email provider, tax rules (D-03), costing/profit (D-04), Focus migration (D-13/D-17, owner-deferred), retention (D-19).

### Next five concrete implementation tasks
1. **Throttle MFA and login.**
   - Files: `routes/auth.py`, and a small limiter backed by Redis or Postgres.
   - Change: count failed MFA codes per employee and session; revoke the session after 5; per-IP limit on login, MFA and pairing; return 401 instead of 423.
   - Tests: 6th wrong code → session revoked; login flood → 429; audit rows written.
2. **Fail-closed settings.**
   - Files: `config.py` validators.
   - Change: in staging/production, refuse to start without `APP_ENV`, `cookie_secure=true` and real (non-placeholder) MFA, bridge and session keys; check those keys at startup.
   - Tests: added to `test_platform.py`.
3. **Stable idempotency keys + supplier payment uniqueness.**
   - `request()` in `web/src/api.ts` takes the key from the caller; forms keep one key until success.
   - New migration: partial unique index on supplier payment reference; map the violation to `DUPLICATE_REFERENCE`.
   - Tests: a double POST with the same key creates one payment; two concurrent posts with the same reference create one.
   - Wait for `0014` to land first.
4. **Session-wide CSRF token.**
   - Change: `GET /auth/csrf` (or the token in `/auth/session`) derived from the session; the web app fetches it when a tab starts.
   - Tests: pytest (another session's token is refused); Playwright with a second page, `context.newPage()`, saves and stays signed in.
5. **Backup/restore drill (OPS-001, G5).**
   - Scripts: `scripts/backup.sh` (`pg_dump -Fc`) and `scripts/restore_check.py` (restore into a scratch database, then SQL invariant checks: ledger sum = balances, invoice totals = lines, allocations ≤ totals, no orphan foreign keys).
   - Tests: a pytest that runs it on `tami_test`.
   - Docs: `docs/operations/RESTORE.md`.
