# Dev, staging, production & operations

## Environments
- Local: compose Postgres/Redis + API/worker/web, mock Document AI and Drive; synthetic documents.
- Staging: isolated DB, isolated Google test processor, test Drive folder, test email sandbox, no customer mail. Hardware PoC bridge connects only to staging during bench tests.
- Production: Ubuntu LTS VPS with TLS terminator, firewall SSH-key only, API behind HTTPS, DB/Redis private network, least-privilege service identities, centralized logs and backups.

## CI pipeline
`ruff check`, `ruff format --check`, `mypy`, `pytest`, Alembic migration-up on empty schema, constraint/unit tests, `npm ci`, `eslint`, `tsc --noEmit`, frontend unit tests, Playwright smoke on staging. Fail pipeline on schema drift and breaking API contract.

## Secret management
`.env.example` placeholders only; secrets from VPS secret store / protected environment, service account files outside repo, least scopes. Rotate signing keys/token credentials and revoke scanner devices on compromise.

## Monitoring
Structured JSON request logs with correlation IDs; exception tracker (Sentry or equivalent), `/health/live` + `/health/ready`, internal metrics for failed OCR, queue lag, Drive sync, invoice finalization errors, stock reconciliation drift, email bounce and backups. Alerts to authorized operators; no PII in generic telemetry.

## Backups
PostgreSQL daily encrypted dumps + WAL/PITR if operationally feasible, private object storage archive of original uploaded docs and PDF snapshots, 3-2-1 strategy, restricted access, documented RPO/RTO approved by owner. **Restore to isolated staging automatically and run consistency queries.** A positive row count alone does not prove correct backup; compare checksums, table counts, posting balances, foreign keys and latest snapshot timestamp.

## Go live
Go/no-go checklist: security, database migration, ledger reconciliation, recovery, scanner fallback, template approved, employee training, owner signoff. Perform operational parallel run with Focus; maintain rollback and handover runbook. No direct dual-booking in Focus and new ERP without explicit source-of-truth/bridge policy.
