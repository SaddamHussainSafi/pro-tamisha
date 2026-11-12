# Module 16 — Email (Lark Mail) and the user replacement

Owner directive 2026-11-12 ("MASTER IMPLEMENTATION DIRECTIVE — User Management Replacement & Native Lark Mail Integration").

This is the company's **communication** email: the @tamishatrading.com mailboxes hosted on Lark Mail. It is separate from **Gmail Inbox / Scan Center** (module 15). That module is the scanning mailbox tamishatrading@gmail.com for scanner PDFs and OCR. Nothing in this module reads, writes or changes the Gmail tables, routes, workers or settings.

## What a person sees

**Email** in the sidebar (permission `mail.use`: Super Admin and Admin roles):
- One mailbox at a time, never a combined inbox.
  - Saddam: *Saddam Mail* and *Info Mail*.
  - Asha: *Asha Mail* and *Info Mail*.
  - Everyone else: their own mailbox.
- Folders: Inbox, Starred, Sent, Drafts, Archive, Junk, All Mail (only those Lark really has). There is **no Trash and no delete, move or archive action anywhere**.
- Reading:
  - The email HTML is cleaned by an allow-list on the server and shown in a sandboxed frame.
  - Remote pictures load only after **Load images**. Pictures sent inside the email show straight away.
  - Attachments: PDF and image files have a preview; everything else is download only. Program and script files (.exe, .js, .bat …) are never served.
  - Also available: the conversation, Print, Reply / Reply all / Forward.
- Writing:
  - To / Cc / Bcc, rich text, attachments (25 MB in total) and the mailbox's signature.
  - Drafts autosave in Pro Trading.
  - Sending goes through a queue, which shows Queued / Sent / Failed / Uncertain.
- Search:
  - Within the selected mailbox: sender, recipient, Cc, subject, text, attachment file name, invoice / order numbers and names, date range, folder, has attachments.
  - The ⌘K universal search also shows email results, but only from the mailboxes given to that person.
- ERP links: an email can be linked to a customer, supplier, order, invoice, receiving or payment. Only by a person, audited, and the record itself is not changed.

**Settings › Integrations › Email Services › Lark Mail** (permission `mail.manage`: **Super Admin only**):
- Per mailbox:
  - address, assigned users, status and health
  - IMAP / SMTP status
  - last sync and last attempt, errors, message and unread counts
  - whether the history import is complete
- Actions: Add mailbox, Test connection, Save password, Sync now, Pause / Resume, Reconnect, Sync log, Security log, Assign users.

## Rules this module keeps (and how they are enforced)

| Rule | Enforcement |
|---|---|
| Never delete, move or change mail on Lark | `larkmail/imap.py` checks **every** IMAP command before it is sent. Only CAPABILITY, LOGIN, LIST, EXAMINE, STATUS, NOOP, LOGOUT, NAMESPACE, UID SEARCH and UID FETCH are allowed. FETCH must use `BODY.PEEK`. Anything else (SELECT, STORE, COPY, MOVE, EXPUNGE, DELETE, APPEND, CREATE, RENAME, non-UID FETCH) raises `ForbiddenImapCommand` before a byte leaves the server. Test: `test_guard_refuses_every_mailbox_changing_command`. The test server also records every command, and each sync test asserts that none was a mailbox-changing one. |
| Read / star state | Kept in Pro Trading, **per person** (`mail_user_state`). Saddam reading an info@ email does not mark it read for Asha, and Lark's flags are never written. |
| Never delete email here | No endpoint or job deletes mail rows. Mail removed in Lark itself is marked `server_removed_at` and stays. A changed UIDVALIDITY re-keys the existing rows by content hash instead of importing duplicates. A draft "discard" only hides a Pro Trading draft (`discarded_at`). |
| Only Super Admins configure | `/mail/admin/*` requires `mail.manage`. `permissions.py` asserts that no other role holds it. |
| No blanket access | A mailbox opens only with an explicit `mail_access` grant for that person, checked in `larkmail/access.py` on every request and every query. Being Super Admin grants no mailbox. Refusals answer 404 and write a `mail.access_denied` audit row. |
| One connection for info@ | `mail_accounts` is unique per company + address. info@ is one `SHARED` row with two grants. A `PERSONAL` mailbox can have only one person. |
| Passwords never leave the server | The Lark app password is Fernet-encrypted with `LARK_MAIL_KEY`. No endpoint returns it (only `has_password`, `password_set_at`), and it is not in audit rows or logs. A test checks responses, the stored ciphertext and the audit rows. |
| Only report "connected" after real authentication | A password is saved only if Lark accepted the IMAP login with it. The status becomes CONNECTED only then (or after a successful sync). |
| No duplicate sends | Each Send has an Idempotency-Key, unique per mailbox, so a double click or a retried request returns the same queue row. If the connection breaks after the message data was handed to Lark, the send is **UNCERTAIN**: it is never retried automatically, and sending again needs an explicit "it may arrive twice" confirmation. |
| Sender identity | Only the person's own granted mailboxes with `can_send`, and only after Lark accepted that mailbox's SMTP login. |

## Sync (background, no browser needed)

- Celery beat:
  - `mail-sync` every 20 s picks the mailboxes that are due. Each mailbox has its own interval (default 120 s, 60 s–24 h) and a lease, so it is never synced twice at once.
  - `mail-send` every 5 s sends the queue.
- Per folder:
  1. EXAMINE (read-only).
  2. New UIDs above `high_uid`.
  3. History, newest first, 200 messages per folder per run, in batches of 25. Each batch is its own transaction, so an interrupted import resumes where it stopped.
  4. Read / star flags of the latest 1,000 messages.
- Messages are keyed by (folder, UIDVALIDITY, UID), never by Message-ID alone.
- Errors:
  - A network or server error sets ERROR and retries with exponential backoff (up to 1 h).
  - A refused password sets AUTH_FAILED: no more tries until a Super Admin saves a new password or presses Reconnect.
- IDLE (push) is **not** used: the worker polls each mailbox on its interval. The settings page shows whether Lark advertises IDLE.
- Attachments are stored in `mail_attachment_blobs` (up to 25 MB each). The file type is checked from the bytes.
  - Virus scanning runs only if a ClamAV daemon is configured (`CLAMD_HOST`).
  - Without one, each file honestly shows **Not scanned**.
- Sent mail: Lark is expected to save its own copy in Sent. A queue row is marked "seen in Sent" when that copy is synced (D-42). Nothing is appended to Lark.

## Tables (migration 0030)

| Table | Holds |
|---|---|
| `mail_accounts` | Mailbox connections |
| `mail_access` | Grants (revoked rows are kept) |
| `mail_folders` | Folders and their sync cursors |
| `mail_messages` | Messages: `tsvector` GIN index and trigram index on the subject |
| `mail_attachments`, `mail_attachment_blobs` | Attachments and their stored bytes |
| `mail_user_state` | Per-person read / star state |
| `mail_drafts` | Drafts kept in Pro Trading |
| `mail_uploads` | Files attached in the compose window |
| `mail_outgoing` | The send queue |
| `mail_sync_runs` | Sync history |
| `mail_links` | Email ↔ ERP record links |

Migration 0030 also adds:
- `employees.archived_at` and `archived_reason`
- permissions `mail.use` (Super Admin, Admin) and `mail.manage` (Super Admin)

Audit actions:
- `mail.account_create` / `_update` / `_test`
- `mail.credentials_saved` / `_rejected`
- `mail.sync_now` / `_paused` / `_resumed`
- `mail.reconnect`
- `mail.access_granted` / `_revoked` / `_changed`
- `mail.access_denied`
- `mail.send_queued` / `_retry`
- `mail.link` / `unlink`
- `mail.attachment_download`

## User replacement (`python -m pro_trading.user_replacement`)

Staged and idempotent. Nothing is deleted, and no password is set or guessed.

1. `report --company-id …`: every account, its role, whether it can sign in, live sessions, and how many history rows point at it (all 76 foreign keys to `employees`).
2. `backup`: `pg_dump` to `apps/api/var/backups/`, then a test restore into a scratch database with row counts compared.
3. `users --company-id … --by <super admin email>`: upserts the seven people.
   - Roles: Super Admin = `OWNER_ADMIN`, Admin = `MANAGER`.
   - New accounts get the normal invitation link, and the person chooses their own password.
   - Two-step sign-in and password reset are unchanged.
   - Also creates the eight mailbox rows (NOT_CONFIGURED) with their grants.
4. `archive --company-id … --by …`: deactivates and archives every other account.
   - Sessions and open links are revoked. The rows and their history stay.
   - Archived accounts leave the user list (`GET /employees?archived=true` shows them).
   - **Refused** until Saddam or Asha has accepted the invitation, so there is always a Super Admin who can sign in.

History is **not** re-attributed. None of the old demo accounts is verifiably one of the seven people, so the old records keep their original creator.

## Setup / owner steps

1. `alembic upgrade head`, then restart the Celery worker (new beat entries).
2. Production: set `LARK_MAIL_KEY` to a new Fernet key. The server refuses to store passwords with the development placeholder.
3. In Lark, for each mailbox: Settings › Email › third-party clients › generate an **authorization code** (app password). This is not the employee's normal password.
4. In Pro Trading: Settings › Email Services › the mailbox › paste the code › **Save password**. The code is tested against Lark first and saved only if Lark accepts it. Then **Sync now**.
5. **Andy first** (pilot). Connect the other mailboxes the same way once Andy's works.
6. **info@**: check in the Lark admin console whether it is a user mailbox, a *public mailbox* or an *alias* (D-41) before connecting it. If it is an alias, its mail lands in another mailbox and it must not get its own connection.

## Rollback

`alembic downgrade 0029` drops the mail tables, the two permissions and the two employee columns. Only the imported copies of email are lost; nothing on Lark is affected. Take `pg_dump -t 'mail_*'` first if anything was imported.

To undo the user replacement:
- Reactivate the archived accounts in Settings › User management (this clears `archived_at`).
- Or restore the backup from step 2.
