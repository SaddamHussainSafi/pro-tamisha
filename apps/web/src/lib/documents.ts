import { ApiError, type DocFileKind, type DocSyncStatus, type DocumentFile } from "../api";
import { errorText } from "./errors";
import type { Tone } from "../ui/primitives";
import { fileSize, MAX_BYTES } from "./scans";

export const SYNC_STATUS: Record<DocSyncStatus, { tone: Tone; label: string }> = {
  PENDING: { tone: "info", label: "Pending sync" },
  SYNCED: { tone: "success", label: "In Drive" },
  FAILED: { tone: "danger", label: "Sync failed" },
  LOCAL_ONLY: { tone: "neutral", label: "Stored here only" },
};

export const DOC_KIND: Record<DocFileKind, string> = {
  INVOICE_PDF: "Invoice",
  POD: "Proof of delivery",
  VENDOR_DOC: "Supplier document",
  SCAN: "Scan",
};

/** What the record belongs to, for the "Linked to" column. */
export function linkedTo(f: Pick<DocumentFile, "invoice_no" | "customer_name" | "shipment_no" | "vendor_name" | "scan_job_no">): string {
  if (f.invoice_no) return f.customer_name ? `${f.invoice_no} · ${f.customer_name}` : f.invoice_no;
  if (f.shipment_no) return f.vendor_name ? `${f.shipment_no} · ${f.vendor_name}` : f.shipment_no;
  if (f.scan_job_no) return f.scan_job_no;
  return "—";
}

/** In-app route for the linked record, if any. */
export function linkedHref(f: Pick<DocumentFile, "invoice_id" | "shipment_id" | "scan_job_id">): string | null {
  if (f.invoice_id) return `/invoices/${f.invoice_id}`;
  if (f.shipment_id) return `/purchases/${f.shipment_id}`;
  if (f.scan_job_id) return `/scan/${f.scan_job_id}`;
  return null;
}

/** Plain-language reason from a stored sync error such as "DRIVE_TIMEOUT: Drive did not answer in time". */
export function syncProblem(lastError: string | null): string | null {
  if (!lastError) return null;
  const code = lastError.split(":")[0]?.trim();
  switch (code) {
    case "DRIVE_TIMEOUT":
      return "Google Drive did not answer in time. It will be tried again automatically.";
    case "DRIVE_DENIED":
      return "Google Drive refused access to the archive folder. Ask your administrator to check the Drive sharing settings, then retry.";
    case "DRIVE_CONFLICT":
      return "A different file with the same name is already in the archive folder. Ask your administrator.";
    case "DRIVE_DISABLED":
      return "Copying to Google Drive is turned off for this installation.";
    default:
      return "The copy to Google Drive did not finish. Retry, or ask your administrator if it keeps failing.";
  }
}

export function documentMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  switch (err.code) {
    case "VERSION_CONFLICT":
      return "This document changed while you were looking at it. The list has been refreshed; try again.";
    case "INVALID_STATE":
      return "Only a failed copy can be retried. It may already be on its way to Drive.";
    case "DRIVE_DISABLED":
      return "Copying to Google Drive is turned off for this installation.";
    case "FILE_TOO_LARGE":
      return `That file is too large. The limit is ${fileSize(Number(err.extra.max_bytes ?? MAX_BYTES))}.`;
    case "EMPTY_FILE":
      return "That file is empty.";
    case "UNSUPPORTED_FILE":
    case "FILE_TYPE_MISMATCH":
      return "Only PDF, PNG, JPEG or TIFF files can be uploaded.";
    case "FORBIDDEN":
      return "You don't have permission to do that.";
    default:
      return errorText(err);
  }
}
