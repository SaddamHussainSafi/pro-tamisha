import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { has } from "../api";
import { useAuth } from "../auth";
import { GmailBrowser } from "../components/gmail/GmailBrowser";
import { Modal } from "../components/gmail/PagePreview";
import { ago, connection, gmail, gmailMessage, whenFull, type Filters, type GmailStatus } from "../lib/gmail";
import { useGmailLive } from "../lib/useGmailLive";
import { Icon } from "../ui/Icon";
import { ConfirmDialog, EmptyState, ErrorState, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const SHOW = new Set(["attachments", "pdf", "processed", "not_processed"]);

/** Warehouse › Gmail Inbox: the company mailbox as stored here, with its files ready to be
 * sent to OCR. Reading mail never starts OCR or creates anything by itself. */
export function GmailInboxPage() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(() => {
    gmail.status().then(
      (s) => {
        setStatus(s);
        setFailed(null);
      },
      (err: unknown) => setFailed(gmailMessage(err)),
    );
  }, []);
  useEffect(load, [load]);
  useGmailLive(load);

  if (!session) return null;
  if (!has(session, "gmail.read"))
    return (
      <div className="space-y-5">
        <PageHeader title="Gmail Inbox" />
        <section className="card">
          <EmptyState icon="mail" title="Your account can't open the Gmail Inbox" hint="Ask an administrator if you need to see scanned documents that arrive by email." />
        </section>
      </div>
    );
  if (failed && !status) return <ErrorState title="Couldn't load Gmail Inbox" detail={failed} onRetry={load} />;
  if (!status) return <PageSkeleton />;

  const conn = connection(status);
  const acc = status.account;
  const show = params.get("show") ?? "all";

  async function refresh() {
    setRefreshing(true);
    setNotice(null);
    try {
      await gmail.sync();
      setNotice("Checking the mailbox… new mail appears in a few seconds.");
      setTimeout(() => {
        setReloadKey((k) => k + 1);
        load();
      }, 6000);
    } catch (err) {
      setNotice(gmailMessage(err));
    } finally {
      setReloadKey((k) => k + 1);
      setRefreshing(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Gmail Inbox"
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            {acc && <span className="font-medium text-ink">{acc.email_address}</span>}
            <StatusChip tone={conn.tone}>{conn.label}</StatusChip>
            {acc && (
              <span className="text-sm" title={acc.last_sync_at ? whenFull(acc.last_sync_at) : undefined}>
                Last synchronized {ago(acc.last_sync_at)}
              </span>
            )}
          </span>
        }
        actions={
          <>
            {acc && acc.status !== "REAUTH_REQUIRED" && (
              <button type="button" className="btn-ghost" onClick={() => void refresh()} disabled={refreshing}>
                <Icon name="refresh" size={16} />
                Refresh
              </button>
            )}
            {status.can_manage && (
              <button type="button" className="btn-ghost" onClick={() => setSettings(true)}>
                <Icon name="settings" size={16} />
                Settings
              </button>
            )}
          </>
        }
      />
      {(conn.tone === "danger" || conn.tone === "warning") && (
        <p role="alert" className={conn.tone === "danger" ? "alert-danger" : "alert-warning"}>
          {conn.detail}
          {status.can_manage && conn.tone === "danger" && (
            <>
              {" "}
              <button type="button" className="cursor-pointer font-medium underline" onClick={() => setSettings(true)}>
                Open settings
              </button>
            </>
          )}
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm text-ink-muted">
          {notice}
        </p>
      )}
      {!acc && status.message_count === 0 ? (
        <section className="card">
          <EmptyState
            icon="mail"
            title={status.configured ? "No mailbox is connected" : "Gmail is not set up yet"}
            hint={
              status.configured
                ? "Connect the company's scanning mailbox to see its emails and documents here."
                : "This server has no Google connection configured, so no email can arrive here yet. An administrator has to add the Google Cloud details first."
            }
            action={
              status.can_manage ? (
                <button type="button" className="btn-primary" onClick={() => setSettings(true)}>
                  {status.configured ? "Connect Gmail" : "See what is needed"}
                </button>
              ) : undefined
            }
          />
        </section>
      ) : (
        <GmailBrowser
          status={status}
          mode="page"
          reloadKey={reloadKey}
          initialMessage={params.get("message")}
          initialShow={(SHOW.has(show) ? show : "all") as Filters["show"]}
          onProcessed={(out) => navigate(out.review_path)}
        />
      )}
      <ConnectionSettings open={settings} status={status} onClose={() => setSettings(false)} onChanged={load} />
    </div>
  );
}

/** Connection settings for administrators: what is connected, how it syncs, connect/disconnect. */
function ConnectionSettings({ open, status, onClose, onChanged }: { open: boolean; status: GmailStatus; onClose: () => void; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const acc = status.account;
  const conn = connection(status);

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      const { authorization_url } = await gmail.start();
      window.location.assign(authorization_url); // Google's own sign-in and consent page
    } catch (err) {
      setError(gmailMessage(err));
      setBusy(false);
    }
  }
  async function disconnect() {
    setConfirm(false);
    if (!acc) return;
    setBusy(true);
    setError(null);
    try {
      await gmail.disconnect(acc.version);
      onChanged();
    } catch (err) {
      setError(gmailMessage(err));
    } finally {
      setBusy(false);
    }
  }
  const row = "flex flex-wrap justify-between gap-x-6 gap-y-0.5 py-2";
  return (
    <>
      <Modal open={open && !confirm} title="Gmail connection" onClose={onClose}>
        <div className="space-y-4 p-5 text-sm">
          <dl className="divide-y divide-line">
            <div className={row}>
              <dt className="text-ink-muted">Mailbox</dt>
              <dd className="font-medium">{acc?.email_address ?? "None"}</dd>
            </div>
            <div className={row}>
              <dt className="text-ink-muted">Status</dt>
              <dd>
                <StatusChip tone={conn.tone}>{conn.label}</StatusChip>
              </dd>
            </div>
            <div className={row}>
              <dt className="text-ink-muted">Access</dt>
              <dd>Read-only. Pro Trading cannot send, delete or change mail.</dd>
            </div>
            {acc && (
              <>
                <div className={row}>
                  <dt className="text-ink-muted">Connected</dt>
                  <dd>
                    {whenFull(acc.connected_at)}
                    {acc.connected_by_name ? ` by ${acc.connected_by_name}` : ""}
                  </dd>
                </div>
                <div className={row}>
                  <dt className="text-ink-muted">Last synchronized</dt>
                  <dd>{acc.last_sync_at ? whenFull(acc.last_sync_at) : "Not yet"}</dd>
                </div>
                <div className={row}>
                  <dt className="text-ink-muted">New mail</dt>
                  <dd>{status.push_configured && acc.watch_expires_at ? `Live (Google push), renewed daily; also checked every ${status.poll_seconds} s` : `Checked every ${status.poll_seconds} s (Google push is not set up)`}</dd>
                </div>
                <div className={row}>
                  <dt className="text-ink-muted">Older mail</dt>
                  <dd>{acc.backfill_done ? "Imported" : "Still being imported"}</dd>
                </div>
                {acc.last_error && (
                  <div className={row}>
                    <dt className="text-ink-muted">Last problem</dt>
                    <dd className="text-danger">
                      {acc.last_error}
                      {acc.last_error_at ? ` (${whenFull(acc.last_error_at)})` : ""}
                    </dd>
                  </div>
                )}
              </>
            )}
          </dl>
          {!status.configured ? (
            <div className="alert-warning space-y-1.5">
              <p className="font-medium">Gmail is not set up on this server.</p>
              <p>
                Nothing can be connected until the Google Cloud details are added to the server's settings (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_TOKEN_KEY). The steps are in the project's <span className="num">docs/modules/15-gmail-inbox.md</span>. Redirect address to register with Google:
              </p>
              <p className="num break-all">{status.redirect_uri}</p>
            </div>
          ) : (
            <p className="text-ink-muted">Connecting opens Google's sign-in page. Sign in as the scanning mailbox and allow read-only access. Emails then arrive here; nothing is read by OCR until someone chooses a document.</p>
          )}
          {error && (
            <p role="alert" className="alert-danger">
              {error}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2.5">
            {acc && (
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => setConfirm(true)}>
                Disconnect
              </button>
            )}
            {status.configured && (!acc || acc.status !== "CONNECTED") && (
              <button type="button" className="btn-primary" disabled={busy} onClick={() => void connect()}>
                {acc ? "Connect again" : "Connect Gmail"}
              </button>
            )}
          </div>
        </div>
      </Modal>
      <ConfirmDialog open={confirm} title="Disconnect this mailbox?" confirmLabel="Disconnect" danger onConfirm={() => void disconnect()} onClose={() => setConfirm(false)}>
        New mail stops arriving and the saved Google sign-in is removed. Emails already here, processed documents and drafts stay.
      </ConfirmDialog>
    </>
  );
}

/** Where Google sends the administrator back after consent (?code=…&state=…). The one-time
 * code is handed to the server and taken out of the address bar at once. */
export function GmailCallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const sent = useRef(false);
  useEffect(() => {
    if (sent.current) return; // StrictMode runs effects twice; the code works once
    sent.current = true;
    const code = params.get("code");
    const state = params.get("state");
    const denied = params.get("error");
    window.history.replaceState(null, "", "/gmail/oauth/callback");
    if (denied || !code || !state) return setError(denied === "access_denied" ? "Access was not allowed, so nothing was connected." : "Google did not complete the connection. Start again from Gmail Inbox.");
    gmail.complete(code, state).then(
      () => navigate("/gmail", { replace: true }),
      (err: unknown) => setError(gmailMessage(err)),
    );
  }, [params, navigate]);
  return (
    <div className="space-y-5">
      <PageHeader title="Gmail Inbox" />
      <section className="card">
        {error ? (
          <EmptyState
            icon="alert"
            title="The mailbox was not connected"
            hint={error}
            action={
              <Link className="btn-primary" to="/gmail">
                Back to Gmail Inbox
              </Link>
            }
          />
        ) : (
          <EmptyState icon="mail" title="Connecting the mailbox…" hint="Finishing the secure sign-in with Google." />
        )}
      </section>
    </div>
  );
}
