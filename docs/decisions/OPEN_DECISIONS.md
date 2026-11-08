# Owner sign-off decision log (do not guess these)

| ID | Decision required | Default until approved |
|---|---|---|
| D-01 | What triggers legal invoice issuance? confirmed packing vs physical dispatch; same-day process? | treat warehouse-verified *dispatch confirmation* as finalization gate |
| D-02 | Finalized invoice correction process: credit note, void/reissue, time windows, HST compliance | no direct edit, manager-only corrective documents |
| D-03 | Tax rules by goods and jurisdiction; exempt/zero-rated cases | block production issue until verified by accountant |
| D-04 | Stock costing: FIFO/batch actual/weighted average, freight/duties allocation/currency conversion | record costs, do not show unsupported profit |
| D-05 | Negative stock, oversell and warehouse reservation | refuse negative stock |
| D-06 | How should `NO` be handled: backorder until closed, or ask manager to cancel? | pending backorder, never auto-cancel |
| D-07 | Per-product invoice price basis: fixed per case, per lb, per kg or per piece; split-case pricing | explicit product configuration |
| D-08 | Actual case weights: scale recorded per box, weighted total, or default estimated | estimated is clearly labeled; cannot book actual-weight price as verified |
| D-09 | Product units case-to-piece or case-to-weight, storage measurement policy | conversions require product-specific validated rule |
| D-10 | Physical inventory/dispatch and invoice timing, returned goods, rejection and spoils | recorded separately; require warehouse signoff |
| D-11 | Invoice numbering, number prefix, historical Focus numbers, fiscal periods | new sequence separate from Focus; preserve external legacy reference |
| D-12 | Real customer invoice PDF template, paper size, fonts, signature/multicolumn totals | blocked pending upload; no invented layout |
| D-13 | Actual Focus V7 backup format and export permissions | investigative only |
| D-14 | Current Toshiba driver/network and whether click-to-scan works on Windows office PC | manual upload fallback |
| D-15 | Google Workspace account vs personal Drive, storage residency and employee access | company-controlled Shared Drive preferred |
| D-16 | Payment instructions/reminder dates and authorized verification roles | simple manual ledger, no API |
| D-17 | Historic transaction migration vs only opening balances and 12-24 months reports | proceed only with reconciliation evidence |
| D-18 | OCR accuracy target and operator review turnaround | 95% quantity-field target in pilot, full review regardless |
| D-19 | Data retention, audit log retention and Canadian privacy obligations | must approve compliance policy |
| D-20 | Client's preferred actual application name (Tami vs Tamisha One) | working name **Pro Trading** only |

For each decision record approver, date, selected option, affected schema/APIs and test updates in an ADR. Never silently infer legal/financial answers.
