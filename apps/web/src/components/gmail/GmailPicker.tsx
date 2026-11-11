import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { has } from "../../api";
import { useAuth } from "../../auth";
import { gmail, gmailMessage, type GmailStatus } from "../../lib/gmail";
import { CardTitle, EmptyState, ErrorState, LoadingRows } from "../../ui/primitives";
import { GmailBrowser } from "./GmailBrowser";
import { Modal } from "./PagePreview";

/** Create invoice › Pick from Gmail: choose a scanned document from any stored email (today's
 * or months old), have it read, check the reading, and get a draft. Nothing is issued here. */
export function GmailPicker() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const allowed = !!session && has(session, "gmail.read");

  useEffect(() => {
    if (!allowed) return;
    gmail.status().then(
      (s) => {
        setStatus(s);
        setOpen(s.message_count > 0); // the picker opens straight away when there is mail
      },
      (err: unknown) => setError(gmailMessage(err)),
    );
  }, [allowed]);

  return (
    <section className="card space-y-4 p-6" aria-labelledby="ni-gmail">
      <CardTitle id="ni-gmail" title="Pick from Gmail" />
      <p className="text-sm text-ink-muted">Choose a scanned packing sheet that arrived by email. It is read, you check every line, and the result is saved as a draft. Nothing is invoiced and no stock moves until the usual checks.</p>
      {!allowed ? (
        <EmptyState icon="mail" title="Your account can't open the Gmail Inbox" hint="Ask an administrator, or use a packing sheet or a direct invoice." />
      ) : error ? (
        <ErrorState title="Couldn't reach the Gmail Inbox" detail={error} />
      ) : !status ? (
        <LoadingRows rows={3} cols={3} />
      ) : status.message_count === 0 ? (
        <EmptyState
          icon="mail"
          title={status.account ? "No emails have arrived yet" : status.configured ? "No mailbox is connected" : "Gmail is not set up yet"}
          hint={status.account ? "Documents sent to the mailbox will show here." : "Until a mailbox is connected there is nothing to pick from. The other two ways of invoicing work as before."}
          action={
            <Link className="btn-ghost" to="/gmail">
              Open Gmail Inbox
            </Link>
          }
        />
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn-primary" onClick={() => setOpen(true)}>
            Choose a document
          </button>
          <span className="text-sm text-ink-muted">{`${status.message_count} ${status.message_count === 1 ? "email" : "emails"} on file`}</span>
        </div>
      )}
      {status && (
        <Modal open={open} title="Pick from Gmail" onClose={() => setOpen(false)} wide>
          <div className="p-5">
            <GmailBrowser status={status} mode="picker" defaultCategory="PICK_SHEET" initialShow="attachments" onProcessed={(out) => navigate(out.review_path)} />
          </div>
        </Modal>
      )}
    </section>
  );
}
