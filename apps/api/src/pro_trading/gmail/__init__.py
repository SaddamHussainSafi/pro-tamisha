"""Gmail Inbox: one dedicated Google mailbox, read-only, kept in step with the database.

- The connection is server-side OAuth (scope `gmail.readonly` only). The refresh token is stored
  encrypted (Fernet, GMAIL_TOKEN_KEY); no token, client secret or password ever reaches the
  browser, the logs or the audit trail.
- New mail is found with Gmail's History API from a saved cursor. A Pub/Sub push (POST
  /gmail/push) is only a signal to look; the worker also looks on a timer, so a missed or late
  push costs seconds, not mail.
- Receiving an email stores its header, body text and the *names* of its attachments. It never
  downloads a file, never starts OCR, never creates an order or an invoice and never moves stock.
- "Use for OCR" is a person's action: the chosen attachment is fetched, checked and handed to the
  existing scan -> OCR -> review -> DRAFT sales order pipeline (docs/modules/07-ocr.md).
- Email text, attachment names and OCR output are untrusted data: they are stored and shown,
  never interpreted as instructions.

Design: docs/modules/15-gmail-inbox.md."""
