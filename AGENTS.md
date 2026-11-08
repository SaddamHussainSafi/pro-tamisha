# Engineering rules for all coding agents

This repo is a **contract-first, test-first implementation pack**. All implementation must match `docs/01-source-of-truth.md` and the module specification. Never claim completion without runnable tests or a manual verification note.

1. Work on **one module/task only** at a time. Start by reading the relevant documents. Summarize affected tables, APIs, roles, constraints, failure paths and tests BEFORE coding.
2. Use PostgreSQL transactions for document finalization and inventory posting. Lock affected stock-balance rows in a deterministic order or use validated atomic guarded updates. Hold a UNIQUE posting key and unique `(invoice_id, line_id, movement_type)` where appropriate. Race and retry tests are mandatory.
3. Stock movements are append-only; no deleting posted rows. Reversals are new linked movements. Cached balance can be rebuilt from the ledger. Handle dual units and lots without invented conversions.
4. Draft orders are not invoices. Invoice number is assigned **only at finalization**, in DB transaction. Never silently edit a finalized invoice. A later shipment means a new invoice linked to the original sales order.
5. OCR output is **untrusted proposed data**. Review/approve before creating authoritative records. Never create financial or stock postings from OCR alone.
6. All write APIs enforce permissions and object scope on server; client visibility is cosmetic. Use Pydantic validation, parameterized SQLAlchemy, typed contracts, database constraints and non-float decimal money.
7. External side effects (email, Drive, OCR) via jobs/outbox after DB commit; retries are idempotent. No network IO while holding inventory DB locks.
8. Scanner bridge is a privileged local service. Never publish it on `0.0.0.0`, embed a permanent secret in frontend, accept unauthenticated localhost requests, or assume CORS is authorization. Design explicit pairing, short-lived capabilities, and user confirmation.
9. Production migrations require backups, backwards compatibility review, and rollback instructions. Never fabricate or overwrite legacy Focus data. Do not place real customer data in tests/prompts.
10. Print/PDF design must match user-supplied reference once available. Do not substitute a made-up production layout. Use provisional generic PDF only for technical testing.
11. Maintain changelog, tests, documentation and task status. Report blockers honestly; do not silently decide tax, inventory costing, payment reconciliation, invoice legal correction or returned goods rules.
12. No credentials, access tokens, bank details, customer personal data or actual invoice scans committed to Git. `.env` ignored, sample files synthetic.

## Visual system enforcement
The design system is defined in `design/UI_DESIGN_REFERENCE.md` and `design/tokens.json`; no UI implementation may substitute the purple or orange hues of reference images for the Tamisha website branding.
