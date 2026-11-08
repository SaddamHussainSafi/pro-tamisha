# Claude Code instructions — Pro Trading

You are acting as principal software architect + senior backend engineer + frontend engineer + QA lead. Implement with restraint and measurable acceptance criteria; do not prioritize visible UI over financial correctness.

**Read first:** `AGENTS.md`, `README.md`, `docs/00-product-brief.md`, `docs/01-source-of-truth.md`, `docs/architecture/02-state-machines.md`, `docs/architecture/03-data-and-transactions.md`, `docs/decisions/OPEN_DECISIONS.md`.

## Mandatory task loop
1. Choose the next `claude/tasks/NN-*.md` not completed, or wait for explicit instruction.
2. Present 5–10 lines of scope, API/table changes, tests and risks. Identify decisions not resolved.
3. Implement smallest vertical slice with versioned migrations, typed API requests, server permissions and tests.
4. Run formatters/typecheck/tests. For concurrent financial operations add deterministic retry/concurrency tests.
5. Record exact commands/results and remaining blockers in `docs/operations/BUILD_LOG.md`.
6. Do not mark phase complete unless gate evidence exists. Never claim to have tested actual Toshiba or Focus data without real access.

## First turn requested from Claude Code
Inspect repository; do NOT write full ERP in one shot. Propose monorepo layout, application bootstrap, database strategy, local dev environment and Module 00 acceptance tests. Then implement task `claude/tasks/00-foundation.md` only.

## Do not do
- No generic CRUD-only stock balance design.
- No `float` for money or case weights.
- No silent `stock -= qty` without locking/idempotency/inventory-ledger posting.
- No invoice numbers on draft sales orders.
- No automatic stock movements on scans.
- No direct client access to Google OCR/Drive credentials.
- No unlocked edit/delete of issued invoices.
- No importing legacy Focus data until formats and reconciliation are proven.
- No unreviewed print layout changes.

## Required Pro Trading branding
Read `design/UI_DESIGN_REFERENCE.md`, `design/tokens.json`, `design/BRAND_VERIFICATION.md` and the five screenshots in `design/references/` before writing any UI. Use brand assets from https://tamishatrading.com/ after obtaining authorized local copies. Pro Trading is product name; Tamisha Trading Inc. remains legal company name. Colors currently provisional until CSS verification.
