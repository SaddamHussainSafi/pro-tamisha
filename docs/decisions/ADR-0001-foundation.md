# ADR-0001: Module 00 foundation choices and deviations from reference schema
Status: Proposed
Date: 2026-11-07
Owner/approver: pending (client / technical owner)

## Context
Task `claude/tasks/00-foundation.md` needs auth, RBAC, audit, idempotency, outbox, health and an app shell.
`db/schema.sql` is a reference only and must not be run raw. The foundation migration `0001` differs from it as listed below.
None of these choices touch accounting, tax or invoice policy (D-01..D-20 remain open).

## Options
- A: Copy `db/schema.sql` verbatim. Rejected: it has no sessions table, MFA fields, lockout or warehouse-scoped grants.
- B: Minimal Alembic migration with the security tables Module 00 needs (selected).

## Decision
1. **Employees**:
   - email is stored lowercased, and is unique per company.
   - Has failed-login counter and lock-until fields (5 failures → locked 15 min, HTTP 423).
2. **Sessions**:
   - Kept in the `auth_sessions` table, with only the sha256 of the cookie token stored.
   - Cookie `tami_session` is HttpOnly and SameSite=strict.
   - Idle timeout 60 min, absolute TTL 480 min. Revocable per employee.
3. **CSRF**:
   - The token is returned once at login and sent in the `X-CSRF-Token` header; the server stores only a hash.
   - Web keeps it in sessionStorage (`pt.csrf`). Mutations also check `Origin`.
4. **MFA**:
   - TOTP, with the secret Fernet-encrypted at rest. Replay is blocked by recording the last accepted step.
   - Privileged-role grants are **dormant** until the session is MFA-verified (403 `MFA_REQUIRED`).
5. **RBAC scope**:
   - `employee_roles.warehouse_id` NULL = company-wide; otherwise the grant applies to that warehouse only.
   - Company-level actions accept only company-wide grants.
   - List endpoints accept any scope and filter rows.
6. **Audit**:
   - `audit_events` gains `warehouse_id`, `request_id` and redacted detail.
   - A trigger rejects UPDATE/DELETE/TRUNCATE (append-only).
   - Permission denials are committed in an independent transaction.
7. **Idempotency**:
   - Every mutation needs `Idempotency-Key` (12–128 chars), stored per company+actor+route with a request hash.
   - A replay returns the stored response; a different payload with the same key gets 409.
8. **Outbox**: `outbox_events` is dispatched by the Celery beat task with SKIP LOCKED, and gives up after 5 attempts (FAILED).
9. **Worker** lives in the API package (`pro_trading.worker`) to share models; `apps/worker` documents how to run it.
10. **Web**:
    - Tailwind 4 tokens come from `design/tokens.json`, guarded by a test.
    - shadcn/ui is deferred to task 00b.
    - Text-only "Pro Trading" wordmark until official logo assets are authorized.
11. **Contract**: implemented endpoints are generated to `contracts/openapi.implemented.json`; a test fails on drift.

## Consequences
- **Schema**: `0001` migration is reversible (down/up covered by a test).
- **Security**: cookie + CSRF header + origin check; privileged actions require MFA.
- **UX**:
  - Owner/admin must enroll TOTP at first sign-in.
  - The default MANAGER role has no `warehouse.manage`. The role matrix needs client confirmation.
- **Rollback**: `alembic downgrade base` on an empty DB.

## Signoff
Pending.
