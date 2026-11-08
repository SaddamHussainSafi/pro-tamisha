# Eight-week delivery plan / phase gates

The schedule assumes consistent full-time development, real operator access, and no guarantee of Focus cutover by day 60. Work flows by validated modules, not strictly calendar time.

| Week | Deliverables | Exit evidence |
|---|---|---|
| 1 | Monorepo, Docker local, auth architecture, schema/state machines, bench-test scanner, inspect Focus sample backup | ADRs; working health DB and Redis; Toshiba scan proof or blocker; Focus format report |
| 2 | Product/customer/vendor/unit/lot schema; immutable stock ledger, GRN transaction and concurrency tests | atomic stock posting + reversal; duplicate requests yield one posting |
| 3 | Sales-order draft, picking/partial backorders, finalized invoices, variable weights, price versions + CSV | original 15, first 10, next 5 -> two unique invoices, zero remaining; rate snapshot fixed |
| 4 | Scanner bridge and handwriting OCR with manual review and safe retry | 50 actual forms test set; ≥95% exact quantity field target, otherwise OCR stays assisted |
| 5 | Vendor docs OCR + GRN, Google Drive snapshots, invoice PDF printing integration, email/outbox | scanned/vendor document links; verified GRN adds exactly once; recovery test |
| 6 | Focus V7 mapping and selective import / reconciliation if feasible; otherwise explicit defer + export workflow | field-by-field 100-record sample, totals reconciled; no uncontrolled cutover |
| 7 | Filterable dashboards, customer balances, RBAC, MFA, monitoring, performance and restore drills | dashboards reconcile to ledger; unauthorized writes denied; restores verified |
| 8 | Office operational pilot, UAT, training, go/no-go checklist, staging-to-production review | real order/GRN rehearsal, 5-user load, documented issues and rollback |

## Non-negotiable gates
- G0: no architecture freeze until business rules on stock units, invoice tax/correction and cost basis are signed off; scanner and Focus feasibility evidenced or identified as blockers.
- G1: 10+ concurrent finalization attempts with same posting key -> one invoice and stock posting; 100 retries -> no duplication; negative stock refused or explicitly approved as exceptional policy.
- G2: two fulfillment invoices sum to actual delivered <= original ordered; `NO` not treated as cancellation; returns and invoice correction don't silently rewrite originals.
- G3: OCR field-level exact matches measured on 50 real documents; human approval required regardless of score. Failures fall back to manual input.
- G4: opening stock and receivable reconciliation zero **unexplained** variance, with documented historical adjustments; no forced cutover just to hit week 8.
- G5: restores succeed repeatedly, production access controlled, customer data exposure reviewed, logs and monitoring operational.

## Order of work
Architecture -> migrations -> immutable stock/GRN -> order/picking -> invoice -> pricing -> scanner/PDF -> Focus import -> analytics -> hardening. Scanner and Focus *feasibility spikes* happen week 1 in parallel; their full modules do not block core development if feasibility fails.
