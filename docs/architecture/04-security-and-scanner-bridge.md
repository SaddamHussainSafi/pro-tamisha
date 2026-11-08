# Security + local scanner bridge trust boundary

## Identity / roles (initial RBAC)
- Owner/Admin: company settings, roles, all reports and sensitive corrections.
- Manager: approve price versions, GRN, invoice corrections, inventory adjustments (configurable).
- Sales/Operator: create orders, review scans, prepare invoices, record reported payments; may issue invoices only if delegated.
- Warehouse: view picking sheets, enter actual counts and damage; cannot change prices, issue invoice, apply payments.
- Finance: accounts receivable, manually verify payments, credit workflow, audited finance exports.

Do server-side `require_permission('invoice.issue', warehouse_id=...)`, not frontend button permission. Apply least-privilege access to customer and vendor files and invoice PDFs. Strong password hashing, MFA for privileged roles, login throttling, secure HttpOnly SameSite cookies, CSRF token for mutations, short session TTL / session revocation, TLS always.

## Scanner bridge minimal safe protocol
A browser **cannot directly invoke any arbitrary scanner**. Office Windows component controls NAPS2/TWAIN driver. Recommended first PoC: manual scanned file upload (safe fallback), then one-click paired bridge.
- Bridge listens on **127.0.0.1 only**; OS service under restricted Windows account; firewall deny inbound; no general file-read/execute endpoint.
- Restrict origins; reject missing/invalid Origin including `null`; validate Host to block DNS rebinding; CSRF protection. CORS is NOT authentication.
- Secure **pairing handshake** initiated by logged-in office operator with on-device confirmation. Use temporary per-device public-key challenge / signed short-lived scan capability, scoped to scan job ID + authorized action, no permanent secret in browser JavaScript.
- Require scan job initiated by server, single-flight scanning lock/queue, explicit output directory, filename randomization, MIME+size limits, no arbitrary paths, no shell interpolation, subprocess timeout/kill and antivirus-safe handling.
- Bridge uploads file using time-limited server-issued upload token, only to designated scan job; backend validates hash/content, MIME and idempotent job key. Token rotation and revocation; persist security events.
- Failure UI: scanner disconnected/driver missing/sleep/timeouts -> allow upload PDF/image or type directly. Never compromise ability to invoice manually.
- Limit data exposure: original PDFs private; Google OCR called from server-only secrets; retention schedule set by owner/legal counsel.

## Security tests
Unauthorized POST invoice, price, stock, GRN -> 403; cross-role object access -> 403/404. Replayed scan tokens rejected; malicious website localhost request rejected; duplicate scan job doesn't create duplicate order. Test file type spoofing, oversized uploads, PDF processing isolation, SQL injection via product/search, IDOR, SSRF, logs redaction, auth lockout, audit denials.

## Threat model
Assets: customer addresses/transactions, confidential customer-specific prices, vendor costs, warehouse stock and bank reference records. Threat actors: external website targeting localhost, compromised office PC, employee abusing access, stolen tokens, supply-chain dependency, leaked Google Drive permissions. Mitigations: local pairing, no public listener, RBAC, audit, isolated workers, encrypted backups, rotation, scoped Drive permissions, updates and incident response checklist.
