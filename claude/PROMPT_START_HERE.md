# Paste this as first Claude Code prompt

You are building **Pro Trading**, a business-critical wholesale operations system for Tamisha Trading. First inspect `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/00-product-brief.md`, `docs/01-source-of-truth.md`, `docs/02-eight-week-plan.md`, `docs/architecture/`, `docs/decisions/OPEN_DECISIONS.md` and `tests/ACCEPTANCE_MATRIX.md`.

**Your assignment on the first run is ONLY Module 00, foundation. Do not write the entire app at once.**

1. Summarize the architecture and 10 highest-priority invariants, including order draft != invoice, warehouse-confirmed partial fulfillment, idempotent invoice/stock, protected issued invoices, versioned customer prices, verified GRN, OCR human approval and manual fallback.
2. Identify any contradictions, legal/accounting ambiguities and gaps in reference `db/schema.sql`/`contracts/openapi.yaml`. Present needed ADRs. Ask only blocker questions, and use safe defaults for non-blocking framework details.
3. Scaffold monorepo React/TypeScript/Vite frontend, FastAPI backend, SQLAlchemy 2/Alembic migrations, Postgres, Redis/Celery, typed contracts, Ruff/mypy/pytest, ESLint/TypeScript/Playwright, Docker dev services, CI workflow, auth and audit foundations.
4. Do NOT run raw `db/schema.sql` blindly. Treat it as a reference; construct reviewed Alembic migrations with constraints and a test database. Fix design gaps rather than propagating them.
5. Prove `/health/live`, `/health/ready`, DB migration up/down, session auth/authorization tests and basic end-to-end boot. Use synthetic data; no real Focus exports or customer details.
6. Update `docs/operations/BUILD_LOG.md` with exact commands, tests, observed results, files added, issues and next module.
7. Stop and ask approval to begin Module 01/03 business engine.

Standards: test-first, contract-first, transaction-first for money/stock; every mutation idempotent and authorized server-side; SQL parameterized; Decimal not float; no secrets in Git; no production cutover; no invented invoice PDF layout. Follow every instruction in `AGENTS.md`.

Before implementing the UI: follow `design/UI_DESIGN_REFERENCE.md`, copy only structural concepts from `design/references/`, verify https://tamishatrading.com/ logo/color assets, and implement the semantic tokens in `design/tokens.json`. Never substitute orange or purple inspiration accents.
