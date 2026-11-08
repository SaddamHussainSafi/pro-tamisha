# Owner/developer handoff checklist

- [ ] Confirm product name, domain and branding.
- [ ] Upload reference actual invoice PDF for pixel-matched output (redact identifying data as appropriate).
- [ ] Install NAPS2 on authorized Windows office workstation, inspect TWAIN/WIA driver and perform real Toshiba e-STUDIO 409CS scan; save redacted test sample.
- [ ] Obtain 50+ actual handwritten order sheets and 10+ vendor document sets for confidential local OCR evaluation.
- [ ] Obtain authorized Focus V7.0129 backup file and documentation, plus sample CSV/XML exports. Do not expose raw production DB to cloud AI chats.
- [ ] Export product master + default units/case profiles, customer/vendor master, stock ledger by batch, legacy sales and purchases, unpaid balances.
- [ ] Approve tax, invoice correction, landed-cost basis and unit conversion business rules in `OPEN_DECISIONS.md`.
- [ ] Create company-owned Google Cloud project/document processor and company-controlled Google Drive archive; allocate test/prod credentials separately.
- [ ] Confirm payment instructions and verified email sending domain; no bank credentials.
- [ ] Confirm hosting, backup owner, monitoring/alert contact, staff roles, MFA, data retention and incident process.
- [ ] Begin Claude Code with `claude/PROMPT_START_HERE.md` and Module 00 only.
