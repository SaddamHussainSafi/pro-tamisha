# Claude Code task 08: Vendor shipments, multi-document packages and GRN

Read `AGENTS.md`, `CLAUDE.md`, `docs/modules/08-receiving.md`, `docs/architecture/02-state-machines.md`, `docs/architecture/03-data-and-transactions.md`, `docs/decisions/OPEN_DECISIONS.md`, `contracts/openapi.yaml`, and `db/schema.sql` (reference only).

## Goal
Implement just this module as a complete, test-backed vertical slice. Do not implement later modules as side effects. For first execution, do foundation first.

## Work checklist
1. Summarize input data, authorized actors, transitions, invariants, concurrency risks and decision blockers.
2. Write the domain/data contracts, typed API schemas and failure responses; get approval if changing business rules.
3. Implement Alembic migration(s) and transactional DB layer; include indexes, uniqueness, check constraints, least privilege and audit hooks.
4. Implement backend service with idempotency, authorization and outbox for external side effects.
5. Implement accessible, keyboard-friendly web UI with clear error/409 conflict states.
6. Implement unit, PostgreSQL integration, permission, concurrent/retry and UI tests appropriate to this module.
7. Document failure-mode recovery and production setup. Run CI/local checks and record commands/results in BUILD_LOG.
8. Stop after reporting acceptance criteria with evidence; request next task approval.

**Acceptance:** Follow all tests from `docs/modules/08-receiving.md` and relevant IDs in `tests/ACCEPTANCE_MATRIX.md`. A green UI demo is insufficient without correct backend invariants.
