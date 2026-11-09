# Module 14 — Notification Center

In-app notifications for real business events: a bell in the top bar, a floating panel, a full
history page (`/notifications`) and per-person preferences (Settings › Notifications).

## Guarantees

| Requirement | How |
|---|---|
| Only real events | Notifications are written by `notification_rules.py` from audit rows (every committed state change already writes one) or by the worker where a job fails. No sample data, no invented amounts: titles and figures come from the stored records. |
| Only after commit | The notification rows join the business transaction; a rollback removes them. The live wake-up is published from SQLAlchemy's `after_commit` hook, and the stream reads committed rows only. |
| Durable, offline-safe | Rows persist per recipient. The SSE `id:` is the recipient's `seq`; a reconnect (browser `Last-Event-ID`, or `?after=`) replays what was missed, up to 50, then asks the tab to reload (`resync`). |
| No duplicates | `UNIQUE (company_id, event_id)` on notifications (semantic ids such as `invoice.issued:<id>`), `UNIQUE (notification_id, employee_id)` on recipients, `UNIQUE (recipient_seq, channel)` on delivery attempts; inserts use `ON CONFLICT DO NOTHING`. Idempotent replays of a command never re-notify. |
| No leaks | Recipients are chosen by permission and warehouse scope (same rule as `Principal.can`). On every read the caller must own the row and still hold the permission in this session, so revoked access or a pending two-step sign-in hides old rows too. Company id is checked on both tables. |
| Never breaks business | Each rule runs in a SAVEPOINT; a failure is logged and the business change commits. |

## Data (migration `0018_notifications`)

- `notifications`: event_id, kind, category, severity, title, body, source_module, link (in-app path only, DB check), target, required_permission, any_scope, warehouse_id, actor_id.
- `notification_recipients`: per-employee inbox row; `seq` (identity) is the stream cursor; read_at, archived_at. Partial index for unread counts.
- `notification_preferences`: one row per employee; in_app, desktop, email, sound, grouped, quiet hours (company time zone), per-category channel switches (JSONB), version.
- `notification_delivery_attempts`: email channel through the outbox (`notification.email`), dev mailbox = `email_messages`.

## Events wired today

| Category | Event | Who |
|---|---|---|
| Website orders | `order_connect.submission` (Order Connect module calls `notify()`) | company-wide `order_connect.read` (Order Connect passes `any_scope=False`: warehouse-scoped staff can't open submissions) |
| Sales & warehouse | order confirmed; pick sheet submitted; pick verified | `pick.record` / `pick.verify` / `invoice.issue`, order's warehouse |
| Invoices | invoice issued | `payment.verify`, invoice's warehouse |
| Payments & balances | payment reported; confirmed / rejected (to the person who recorded it); invoice overdue (once per reminder step) | `payment.verify`; recorder |
| Stock & shortages | pick packed short (back-ordered / cancelled); product out of stock after an invoice | `sales_order.manage` |
| Vendor receiving | delivery counted; stock received (GRN posted, with discrepancy count) | `grn.approve`; `sales_order.manage` |
| Scans & OCR | scan ready to review; OCR failed; scan job failed (to the requester) | `scan.review`; requester |
| Drive & email | Drive copy failed; invoice email failed | `document.manage`; `invoice.issue` |
| Account & security | account locked (sign-in, password page or re-confirm prompt), two-step sign-in paused / turned on / turned off, recovery code used or regenerated, password changed, password reset by link or by an admin, 2FA reset by an admin, sessions ended by an admin or "sign out other devices", invitation accepted, scanner pairing code mismatch | the person; admins (`employee.manage`) for lockouts and accepted invitations; `scan.device.manage` |
| Jobs & system | any background job that gave up after 5 attempts | `company.manage` |

People are not notified about their own clicks (except their own security events). Deliberately
not mapped: password-reset *requests* and link throttling (often anonymous, and an email notice
already goes out), invitations sent and accounts created (the admin's own click), the
bootstrap Super Admin.

**Not wired (no data to base it on):** "low stock" against a reorder level — products have no
reorder threshold yet. Out-of-stock after an invoice is wired. Adding thresholds is an owner
decision (catalog change).

## API (`/api/v1/notifications`, self-service: the caller's own inbox only)

`GET ""` (status unread/all/archived, category[], severity[], q, before, limit → keyset pages),
`GET /summary`, `POST /{id}/read|unread|archive|unarchive`, `POST /read-all`, `POST /archive`
(ids), `GET|PUT /preferences` (optimistic `version`, Idempotency-Key, audited as
`notification.preferences_update`), `GET /stream` (SSE; max 4 per employee). Writes are limited
to 240 per minute per employee. Muting the security category needs `employee.manage`
(`SECURITY_ALERTS_LOCKED`); critical alerts reach the inbox whatever the settings.

## Web

- `components/notifications/`: `NotificationProvider` (one SSE connection per browser: a Web
  Lock picks the leader tab, a BroadcastChannel relays events), `NotificationBell`,
  `NotificationPopover`, `NotificationCard`, `NotificationGroup` (Today / Yesterday / Earlier,
  stacks of 3+ from one module), `NotificationFilters`, `NotificationPreferences`, `useInbox`.
- `pages/Notifications.tsx`: `NotificationCenterPage` (filters in the URL, infinite scroll).
- Desktop notifications use the Web Notifications API only after the person presses "Allow…"
  in Settings and only when the app is in the background; sound is a WebAudio chime (no asset).
  Quiet hours hold back banners, desktop alerts, sounds and emails, never the inbox.

## Adding an event

Prefer an audit-driven rule: `@rule("your.action")` in `notification_rules.py`, look the record
up by `ev.target_id`, call `notify_safely(...)` with a semantic `event_id`, a category, a
permission (+ warehouse) or `to=[...]`, and an in-app `link`. Add a test in
`tests/test_notifications.py`.
