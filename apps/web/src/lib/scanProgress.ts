import { apiRequest } from "../api";
import { failHint } from "./scans";

/** GET /scans/jobs/{id}/progress (and each `progress` event of its SSE stream). Built by the API
 * from committed rows only: the scan's status, the reading's status and the OCR worker's page
 * events. The scanner animation is a picture of this object — nothing here is timed or guessed. */
export type StepKey = "RETRIEVING" | "UPLOADING" | "EXTRACTING" | "MATCHING" | "REVIEW";
export type StepState = "waiting" | "active" | "done" | "failed" | "skipped";
export type Outcome = "WORKING" | "READY" | "DONE" | "FAILED" | "TIMEOUT" | "MISSING" | "CANCELLED" | "UNREAD";
export type PageState = "WAITING" | "READING" | "READ" | "FAILED" | "SKIPPED";

export type ProgressPage = {
  page: number;
  state: PageState;
  words: number | null;
  confidence: string | null;
  sample: string | null;
  lines: number | null;
  to_check: number | null;
};
export type ProgressEvent = { seq: number; kind: string; attempt: number | null; page: number | null; page_count: number | null; at: string; detail: Record<string, unknown> };
export type ProgressReading = {
  id: string;
  run_no: number;
  status: "QUEUED" | "NEEDS_REVIEW" | "FAILED" | "ACCEPTED" | "REJECTED" | "SUPERSEDED";
  provider: string;
  page_count: number | null;
  error_code: string | null;
  error_detail: string | null;
  lines: number | null;
  to_check: number | null;
  unknown_products: number | null;
  unclear_quantities: number | null;
  warnings: string[];
  created_at: string;
  completed_at: string | null;
};
export type ScanProgress = {
  job_id: string;
  job_no: string;
  job_type: string;
  channel: "BRIDGE" | "MANUAL";
  status: "REQUESTED" | "SCANNING" | "UPLOADED" | "FAILED" | "CANCELLED" | "EXPIRED";
  duplicate: boolean;
  error_code: string | null;
  error_detail: string | null;
  document_id: string | null;
  document_mime: string | null;
  document_filename: string | null;
  page_count: number | null;
  outcome: Outcome;
  step: StepKey;
  steps: { key: StepKey; state: StepState }[];
  pages: ProgressPage[];
  pages_read: number;
  attempt: number | null;
  retrying: { code: string | null; attempt: number; max_attempts: number | null } | null;
  reading: ProgressReading | null;
  events: ProgressEvent[];
  seq: number;
  can_read: boolean;
  can_retry: boolean;
  can_review: boolean;
  version: string;
};

export const scanProgressApi = {
  get: (jobId: string) => apiRequest<ScanProgress>("GET", `/scans/jobs/${jobId}/progress`),
  streamUrl: (jobId: string) => `/api/v1/scans/jobs/${jobId}/progress/stream`,
};

export const STEP_ORDER: StepKey[] = ["RETRIEVING", "UPLOADING", "EXTRACTING", "MATCHING", "REVIEW"];
export const STEP_LABEL: Record<StepKey, string> = {
  RETRIEVING: "Retrieving PDF",
  UPLOADING: "Uploading",
  EXTRACTING: "Extracting text",
  MATCHING: "Matching products",
  REVIEW: "Review ready",
};

/** What the browser itself knows before the server has the scan: the file it is sending. */
export type Local = { kind: "uploading"; fileName: string; fraction: number | null } | { kind: "upload-failed"; fileName: string; message: string };

/** The picture the scanner draws. One per resting or working state. */
export type Scene =
  | "idle"
  | "connecting"
  | "retrieving"
  | "uploading"
  | "queued"
  | "reading"
  | "matching"
  | "ready"
  | "done"
  | "failed"
  | "timeout"
  | "missing"
  | "unread"
  | "cancelled";

export type Tone = "working" | "ok" | "warn" | "error" | "neutral";

export type View = {
  scene: Scene;
  tone: Tone;
  title: string;
  detail: string;
  steps: { key: StepKey; label: string; state: StepState }[];
  pageTotal: number;
  pagesRead: number;
  /** The page on the glass: being read now, or the last one read. */
  currentPage: number | null;
  uploadPct: number | null;
  /** After matching: lines found and how many of them need a person. */
  matched: { lines: number; toCheck: number } | null;
  duplicate: boolean;
  retrying: boolean;
};

const OCR_FAIL: Record<string, string> = {
  UNREADABLE: "The file couldn't be opened. Save it again as PDF and upload it.",
  TOO_LARGE: "A page is too large to read. Scan at a lower resolution.",
  UNSUPPORTED: "This file type can't be read.",
  OCR_UNAVAILABLE: "The text reader couldn't be reached.",
  OCR_REJECTED: "The text reader refused this file.",
  OCR_NOT_CONFIGURED: "The text reader isn't set up on this server.",
  OCR_DISABLED: "Text reading is switched off. Enter the lines by hand.",
  TIMEOUT: "The text reader took too long.",
};

export function readFailHint(code: string | null): string {
  return (code && OCR_FAIL[code]) || "The text reader reported an error. Read it again, or enter the lines by hand.";
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function stepsOf(states: Partial<Record<StepKey, StepState>>): View["steps"] {
  return STEP_ORDER.map((key) => ({ key, label: STEP_LABEL[key], state: states[key] ?? "waiting" }));
}

function base(partial: Partial<View> & Pick<View, "scene" | "tone" | "title" | "steps">): View {
  return { detail: "", pageTotal: 0, pagesRead: 0, currentPage: null, uploadPct: null, matched: null, duplicate: false, retrying: false, ...partial };
}

/** From what the server (and, during an upload, the browser) says to what the scanner shows. */
export function viewOf(snap: ScanProgress | null, local: Local | null = null, opts: { duplicate?: boolean; missing?: boolean } = {}): View {
  if (opts.missing) {
    return base({ scene: "missing", tone: "error", title: "Scan not found", detail: "It may have been deleted. Upload the file again.", steps: stepsOf({ RETRIEVING: "failed" }) });
  }
  if (!snap) {
    if (local?.kind === "uploading") {
      const pct = local.fraction === null ? null : Math.round(local.fraction * 100);
      return base({
        scene: "uploading",
        tone: "working",
        title: "Uploading",
        detail: pct === null ? local.fileName : `${pct}% · ${local.fileName}`,
        steps: stepsOf({ RETRIEVING: "done", UPLOADING: "active" }),
        uploadPct: pct,
      });
    }
    if (local?.kind === "upload-failed") {
      return base({ scene: "failed", tone: "error", title: "Upload failed", detail: local.message, steps: stepsOf({ RETRIEVING: "done", UPLOADING: "failed" }) });
    }
    return base({ scene: "connecting", tone: "neutral", title: "Connecting…", steps: stepsOf({}) });
  }

  const steps = snap.steps.map((s) => ({ ...s, label: STEP_LABEL[s.key] }));
  const pageTotal = snap.page_count ?? snap.pages.length;
  const reading = snap.pages.find((p) => p.state === "READING")?.page ?? null;
  const lastRead = [...snap.pages].reverse().find((p) => p.state === "READ")?.page ?? null;
  const matchedEv = [...snap.events].reverse().find((e) => e.kind === "MATCHED");
  const r = snap.reading;
  const matched =
    r && r.lines !== null
      ? { lines: r.lines, toCheck: r.to_check ?? 0 }
      : matchedEv
        ? { lines: Number(matchedEv.detail.lines ?? 0), toCheck: Number(matchedEv.detail.to_check ?? 0) }
        : null;
  const common = {
    steps,
    pageTotal,
    pagesRead: snap.pages_read,
    currentPage: reading ?? lastRead ?? (pageTotal ? 1 : null),
    matched,
    duplicate: !!opts.duplicate || snap.duplicate,
  };
  const pages = pageTotal ? plural(pageTotal, "page") : "";

  switch (snap.outcome) {
    case "WORKING": {
      if (snap.status === "REQUESTED") return base({ ...common, scene: "retrieving", tone: "working", title: "Waiting for the scanner", detail: "It picks up the request in a moment." });
      if (snap.status === "SCANNING") return base({ ...common, scene: "retrieving", tone: "working", title: "Retrieving PDF", detail: "The scanner is feeding the pages." });
      if (snap.retrying) {
        const { attempt, max_attempts } = snap.retrying;
        return base({
          ...common,
          scene: "queued",
          tone: "warn",
          retrying: true,
          title: "Trying again",
          detail: `${readFailHint(snap.retrying.code)} Attempt ${attempt + 1}${max_attempts ? ` of ${max_attempts}` : ""} starts shortly.`,
        });
      }
      if (snap.step === "MATCHING") return base({ ...common, scene: "matching", tone: "working", title: "Matching products", detail: `${plural(snap.pages_read, "page")} read` });
      if (reading !== null) return base({ ...common, scene: "reading", tone: "working", title: "Extracting text", detail: `Page ${reading} of ${pageTotal}` });
      return base({ ...common, scene: "queued", tone: "working", title: "Waiting for the reader", detail: pages ? `${pages} received` : "Received" });
    }
    case "READY": {
      const lines = matched?.lines ?? 0;
      const check = matched?.toCheck ?? 0;
      const empty = lines === 0;
      return base({
        ...common,
        scene: "ready",
        tone: "ok",
        title: common.duplicate ? "Already scanned" : "Review ready",
        detail: empty
          ? "No lines were found. Add them by hand from the original."
          : `${plural(lines, "line")}${check ? ` · ${check} to check` : " · all clear"}`,
      });
    }
    case "DONE":
      return base({
        ...common,
        scene: "done",
        tone: "neutral",
        title: r?.status === "REJECTED" ? "Rejected" : "Reviewed",
        detail: r?.status === "REJECTED" ? "No order was created from this scan." : "A draft order was created from this scan.",
      });
    case "FAILED":
      return snap.status === "FAILED"
        ? base({ ...common, scene: "failed", tone: "error", title: "Scan failed", detail: failHint(snap.error_code) })
        : base({ ...common, scene: "failed", tone: "error", title: "Couldn't read the scan", detail: readFailHint(r?.error_code ?? null) });
    case "TIMEOUT":
      return base({
        ...common,
        scene: "timeout",
        tone: "warn",
        title: "Timed out",
        detail:
          snap.status === "EXPIRED"
            ? "The scanner didn't pick up the request. Scan again, or upload the file."
            : snap.status === "FAILED"
              ? "The scanner didn't send the pages in time. Check for a paper jam, then scan again."
              : "No reader picked this scan up in time. Read it again.",
      });
    case "MISSING":
      return base({ ...common, scene: "missing", tone: "error", title: "No document received", detail: failHint(snap.error_code) });
    case "CANCELLED":
      return base({ ...common, scene: "cancelled", tone: "neutral", title: "Cancelled", detail: "Nothing was scanned." });
    case "UNREAD":
      return base({
        ...common,
        scene: "unread",
        tone: "neutral",
        title: common.duplicate ? "Already scanned" : "Not read yet",
        detail: common.duplicate ? `${snap.job_no} hasn't been read yet.` : "This document type is read on request.",
      });
  }
}

/** One sentence for screen readers when the step changes. */
export const announce = (v: View) => (v.detail ? `${v.title}. ${v.detail}` : v.title);

/** Accessible name for one page in the page strip. */
export function pageLabel(p: ProgressPage): string {
  const state = { WAITING: "waiting", READING: "being read", READ: "read", FAILED: "could not be read", SKIPPED: "not read (over the page limit)" }[p.state];
  const bits = [`Page ${p.page}: ${state}`];
  if (p.lines !== null) bits.push(plural(p.lines, "line"));
  if (p.to_check) bits.push(`${p.to_check} to check`);
  return bits.join(", ");
}
