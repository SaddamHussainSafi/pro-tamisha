# Pro Trading — Tamisha Trading | Engineering Build Pack

**Status:** implementation-ready specification and example artifacts, **not a completed application**.  **Target:** 8-week development / controlled pilot; production cutover requires acceptance gates and longer parallel verification. **Last requirements consolidation:** November 2026.

## Start here (for the developer and Claude Code)
1. Read `AGENTS.md`, `CLAUDE.md`, `docs/00-product-brief.md`, `docs/01-source-of-truth.md`, `docs/02-eight-week-plan.md` and `docs/architecture/01-system.md`.
2. Read the module relevant to the next task. Read `docs/decisions/OPEN_DECISIONS.md` before selecting any business rule.
3. Start with `claude/tasks/00-foundation.md` then advance **one gated module at a time**. Do not build all modules at once.
4. Use `db/schema.sql` as **schema-design reference**; implement actual database changes through versioned Alembic migrations. `contracts/openapi.yaml` is a starting contract, not a verified production API.
5. Run automated tests, inspect generated SQL/migrations, report evidence and blockers after each module.

## The exact core flow
Original handwritten customer order -> scan/import -> human-confirmed **sales order draft** -> paper warehouse picking -> scan/manual warehouse confirmation -> **first real final invoice** for quantities supplied -> stock posting once -> optionally later **new final invoice** for pending goods under the same original order. The morning draft is **not an invoice and has no invoice number**. Preserve the original scanned order and every finalized invoice.

## Scope
- V1: products and variable case/weight/piece units; price versions and customer overrides; customers/vendors; receiving and lot inventory; sales orders; picking; separate partial invoices; live inventory; manual payment ledger; PDF template printing, email, Google Drive; scanner + human-reviewed OCR; Focus V7 discovery/migration subject to feasibility; verified sales/stock analytics.
- Later: predictive AI and WordPress form builder. Card payment gateway/Interac API integration is not in V1; record external payments manually.

## Hardware and legacy evidence
- Office printer/scanner in photos is **Toshiba e-STUDIO 409CS** (label says FC-4090CS), not 409S. Physical cable presence does **not** prove NAPS2 control works. Verify on real office Windows PC.
- Screenshots show **Focus V7.0129 (International)**, not Focus 9. Legacy UI contains sales orders, invoices, purchasing, stock ledgers, CASE-XX / PIECE-XX / LBS and batch numbers, XML export and report Excel/PDF export. Backup format not yet verified.
- No original invoice PDF print-template has been uploaded. **Exact print layout is blocked pending reference PDF.** No visual guesses.

## Architecture choices
React/TypeScript/Vite; Tailwind and shadcn/ui; FastAPI/Python; PostgreSQL; SQLAlchemy/Alembic; Redis/Celery workers; SSE for one-way data notifications; NAPS2 + privileged Windows bridge; Google Document AI Enterprise OCR; Google Drive API; SMTP/API transactional email; Docker on Ubuntu; Pytest/Playwright, structured logging and monitoring.

## Security / operational constraints
No bank passwords in the app. Backend RBAC on every mutation; strong sessions, MFA for privileged roles, audit trail, no public DB, secrets in environment/secret manager, tested encrypted offsite backups. Browser/local scanner integration must pass explicit origin, session authorization, CSRF/DNS-rebinding defenses and local pairing. OCR never posts stock or invoices directly. Never modify production Focus during migration work.

## Where everything lives
- `docs/00-product-brief.md` — all discussions condensed
- `docs/01-source-of-truth.md` — decided vs unverified
- `docs/02-eight-week-plan.md` — weekly deliverables and gates
- `docs/modules/` — module-by-module behavior and acceptance criteria
- `docs/architecture/` — data, state machines, security, printing and failure handling
- `docs/operations/` — legacy migration, deployment, testing and release
- `docs/decisions/OPEN_DECISIONS.md` — points requiring business approval
- `db/schema.sql`, `contracts/openapi.yaml`, `examples/` — reference implementation resources
- `claude/tasks/` — ordered Claude Code task prompts

## Definition of Done
Working code, database migration, server-side auth, deterministic financial calculations, concurrency/idempotency tests, integration tests, audit entry, observability, rollback/recovery plan, approved business rules and documented hands-on acceptance evidence. A demo alone is not done.

## Product identity and design
**Name: Pro Trading.** Company: **Tamisha Trading Inc.** Read `design/UI_DESIGN_REFERENCE.md` and `design/tokens.json`. Inspiration screenshots are included in `design/references/`; official website logos are recorded as external source URLs and must be verified / downloaded with permission.
