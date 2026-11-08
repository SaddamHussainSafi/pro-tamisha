# Failure modes, recovery and alerting

| Failure | UX | Backend guarantee | Recovery / verification |
|---|---|---|---|
| Scanner disconnected/driver hung | explicit error; upload/manual entry | no phantom scan/order created | bridge timeout, queue release, operator retry |
| Google OCR down/429 | status queued/unavailable; manual review | no stock/invoice side effects | Celery retry with exponential backoff and DLQ |
| OCR reads `NO` as `10` | mark uncertain quantity; original side-by-side | unapproved field cannot post | human correction + audit |
| Duplicated scan/upload | existing scan job reference | content hash + job key guards | return previous result |
| Drive 429/network failure | `Pending Sync` banner | invoice already committed and immutable | retry outbox, alert on aged failed jobs |
| Email API timeout | email pending | no invoice rollback after issue | idempotent job, reconcile provider send state |
| VPS crashes during invoice DB transaction | "status unknown; refresh" | committed exactly once or rolled back fully | request idempotency key lookup, logs |
| Two staff post same pick | conflict / one confirmed outcome | serializable/locked and UNIQUE keys | concurrent transaction test |
| Oversold stock | refuse finalization | no negative unless explicit exception policy | manager investigation, adjustment ledger |
| Bad vendor doc duplicates AWB | manual reconcile | receipt not auto-posted | duplicate-warning, vendor/source record link |
| Wrong inventory/count after receipt | discrepancy screen | original GRN intact | adjustment transaction, audit reason |
| Focus import mapping inconsistent | hold cutover | staging import never impacts Focus | discrepancy spreadsheet, iterative dry-run |
| Backup restore fails | urgent ops alert | preserve last known successful copy | offsite restore drill and incident process |
| Compromised scanner bridge PC | revoke device | restrict scope and token lifetime | stop bridge, rotate credentials, investigate |
| SSE disconnect | stale indicator/refetch | authoritative API unaffected | reconnect with event IDs + refresh |

Use structured JSON logs with request_id, actor_id, entity_id, job_id; mask personal data, secrets, prices in general logs. Monitoring: API/worker errors, job lag, DB availability, Drive/Google failures, backups, scan bridge heartbeat. Health endpoints: `/health/live`, `/health/ready` with appropriate access; internal `/metrics`. Test every high-impact failure path.
