import { ApiError, type ScanJob, type ScanJobStatus, type ScanJobType, type ScannerState } from "../api";
import type { Tone } from "../ui/primitives";

export const BRIDGE_URL: string = (import.meta.env?.VITE_SCAN_BRIDGE_URL as string | undefined) ?? "http://127.0.0.1:47615";

export const JOB_STATUS: Record<ScanJobStatus, { tone: Tone; label: string }> = {
  REQUESTED: { tone: "info", label: "Waiting for scanner" },
  SCANNING: { tone: "info", label: "Scanning" },
  UPLOADED: { tone: "success", label: "Received" },
  FAILED: { tone: "danger", label: "Failed" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
  EXPIRED: { tone: "warning", label: "Expired" },
};

export const JOB_TYPE: Record<ScanJobType, string> = {
  SALES_ORDER: "Sales order",
  PICK_SHEET: "Picking sheet",
  VENDOR_DOCUMENT: "Supplier invoice",
  PURCHASE_ORDER: "Purchase order",
  DELIVERY_NOTE: "Delivery note",
  PACKING_LIST: "Packing list",
  RECEIPT: "Receipt",
  OTHER: "Other",
};

export const SCANNER_STATE: Record<ScannerState, { tone: Tone; label: string }> = {
  READY: { tone: "success", label: "Scanner ready" },
  NOT_FOUND: { tone: "danger", label: "Scanner not found" },
  DRIVER_MISSING: { tone: "danger", label: "Scanner software missing" },
  BUSY: { tone: "warning", label: "Scanner busy" },
  ERROR: { tone: "danger", label: "Scanner error" },
  UNKNOWN: { tone: "neutral", label: "Scanner state unknown" },
};

/** Jobs that may still change on their own (the list polls while any are present). */
export const isOpen = (j: Pick<ScanJob, "status">) => j.status === "REQUESTED" || j.status === "SCANNING";

/** What the operator should do when a bridge scan fails, keyed by the bridge/driver error code. */
const FAIL_HINT: Record<string, string> = {
  SCANNER_NOT_FOUND: "The scanner is off or unplugged. Turn it on, check the USB cable, then scan again.",
  DRIVER_MISSING: "The scanning software (NAPS2) is not installed on the scanner PC. Ask your administrator.",
  SCAN_TIMEOUT: "The scanner took too long. Check for a paper jam, then scan again.",
  NO_PAGES: "No pages came through. Put the pages in the feeder, then scan again.",
  OUTPUT_INVALID: "The scan could not be read. Scan again, or upload the file instead.",
  ERROR: "The scanner reported an error. Scan again, or upload the file instead.",
};
const GENERIC_FAIL = "The scanner reported an error. Scan again, or upload the file instead.";
export const failHint = (code: string | null) => (code && FAIL_HINT[code]) || GENERIC_FAIL;

/** Bytes → "1.2 MB" (display only, one decimal). */
export function fileSize(n: number | null): string {
  if (n === null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"] as const;
  let div = 1024;
  let u = 0;
  while (n >= div * 1024 && u < units.length - 1) {
    div *= 1024;
    u++;
  }
  const tenths = Math.round((n * 10) / div);
  return `${Math.floor(tenths / 10)}.${tenths % 10} ${units[u]}`;
}

export const ACCEPT = "application/pdf,image/png,image/jpeg,image/tiff";
export const ACCEPTED_TYPES = new Set(ACCEPT.split(","));
export const MAX_BYTES = 20 * 1024 * 1024;

/** Client-side pre-check; the server re-checks the actual bytes. Returns an error message or null. */
export function checkFile(f: { name: string; size: number; type: string }): string | null {
  if (f.size === 0) return "That file is empty.";
  if (f.size > MAX_BYTES) return `That file is ${fileSize(f.size)}. The limit is ${fileSize(MAX_BYTES)}.`;
  if (f.type && !ACCEPTED_TYPES.has(f.type)) return "Only PDF, PNG, JPEG or TIFF files can be uploaded.";
  return null;
}

export function scanMessage(err: unknown): string {
  if (err instanceof BridgeError) {
    switch (err.code) {
      case "UNREACHABLE":
        return "Can't reach the scanner PC. Make sure Pro Trading Bridge is running on this computer, or upload the file instead.";
      case "NOT_PAIRED":
        return "The bridge on this computer is not paired. Pair it from the Scanners tab.";
      case "BUSY":
        return "The scanner is busy with another scan. Wait for it to finish.";
      case "ORIGIN_REJECTED":
      case "HOST_REJECTED":
        return "The bridge refused this page. Its PT_ALLOWED_ORIGINS setting must include this site's address.";
      case "CAPABILITY_EXPIRED":
        return "The scan request expired before the scanner picked it up. Start a new scan.";
      default:
        return err.message || "The scanner bridge reported an error.";
    }
  }
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  switch (err.code) {
    case "FILE_TOO_LARGE":
      return `That file is too large. The limit is ${fileSize(Number(err.extra.max_bytes ?? MAX_BYTES))}.`;
    case "EMPTY_FILE":
      return "That file is empty.";
    case "UNSUPPORTED_FILE":
      return "Only PDF, PNG, JPEG or TIFF files can be uploaded.";
    case "FILE_TYPE_MISMATCH":
      return "The file's contents don't match its type. Save it again as PDF or image and retry.";
    case "DUPLICATE_UPLOAD":
      return "This exact file was already uploaded for another location.";
    case "FINGERPRINT_MISMATCH":
      return "That code doesn't match the one shown on the scanner PC. Check it and try again.";
    case "NOT_PAIRED":
      return "The bridge hasn't used the pairing code yet. Run the pair command on the scanner PC first.";
    case "DEVICE_NOT_ACTIVE":
      return "This scanner is not approved yet.";
    case "WRONG_WAREHOUSE":
      return "This scanner belongs to another location.";
    case "VERSION_CONFLICT":
    case "INVALID_STATE":
      return "This changed since you opened it. It has been reloaded — check and try again.";
    case "FORBIDDEN":
    case "MFA_REQUIRED":
      return "You don't have permission to do that.";
    default:
      return err.message || "Something went wrong. Try again.";
  }
}

export class BridgeError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

type Fetch = typeof fetch;

/** Ask the localhost bridge to run a scan for a job the API just created. Resolves with the job
 * as the API saw it after the scan (UPLOADED or FAILED). Network failure → BridgeError UNREACHABLE. */
export async function bridgeScan(job: { id: string; capability: string }, f: Fetch = fetch, base = BRIDGE_URL): Promise<ScanJob> {
  let res: Response;
  try {
    res = await f(`${base}/v1/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job.id, capability: job.capability }),
    });
  } catch {
    throw new BridgeError(0, "UNREACHABLE", "Bridge not reachable");
  }
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new BridgeError(res.status, String(data.code ?? "BRIDGE_ERROR"), String(data.message ?? ""));
  return data as unknown as ScanJob;
}

export type BridgeStatus = { bridge_version: string; paired: boolean; device_id: string | null; scanner_state: ScannerState; scanner_detail: string | null };

export async function bridgeStatus(f: Fetch = fetch, base = BRIDGE_URL): Promise<BridgeStatus | null> {
  try {
    const res = await f(`${base}/v1/status`);
    return res.ok ? ((await res.json()) as BridgeStatus) : null;
  } catch {
    return null;
  }
}

/** Scans that are read automatically by OCR when they arrive. */
export const AUTO_READ: ScanJobType[] = ["SALES_ORDER", "VENDOR_DOCUMENT", "PURCHASE_ORDER", "DELIVERY_NOTE", "PACKING_LIST", "RECEIPT"];

export type Stage = "upload" | "read" | "review" | "done" | "failed";
export const STAGES: { key: Exclude<Stage, "failed" | "done">; label: string }[] = [
  { key: "upload", label: "Uploading" },
  { key: "read", label: "Reading text (OCR) and extracting" },
  { key: "review", label: "Ready for review" },
];

/** Where a scan is, from what the server says (never guessed). */
export function stageOf(uploaded: boolean, ocrStatus: string | null): Stage {
  if (!uploaded) return "upload";
  if (ocrStatus === "FAILED") return "failed";
  if (ocrStatus === "NEEDS_REVIEW") return "review";
  if (ocrStatus === "ACCEPTED" || ocrStatus === "REJECTED") return "done";
  return "read";
}
