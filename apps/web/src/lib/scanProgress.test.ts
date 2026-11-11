import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { IsoScanner, type ScannerProps } from "../components/scanner/IsoScanner";
import { PageStrip, StepsRail } from "../components/scanner/ScanProcessing";
import { announce, pageLabel, viewOf, type ProgressPage, type ScanProgress, type StepKey, type StepState } from "./scanProgress";

const KEYS: StepKey[] = ["RETRIEVING", "UPLOADING", "EXTRACTING", "MATCHING", "REVIEW"];
const steps = (...s: StepState[]) => KEYS.map((key, i) => ({ key, state: s[i] ?? "waiting" }));
const page = (n: number, state: ProgressPage["state"], extra: Partial<ProgressPage> = {}): ProgressPage => ({ page: n, state, words: null, confidence: null, sample: null, lines: null, to_check: null, ...extra });

function snap(over: Partial<ScanProgress> = {}): ScanProgress {
  return {
    job_id: "j1",
    job_no: "SCN-20261110-0007",
    job_type: "SALES_ORDER",
    channel: "MANUAL",
    status: "UPLOADED",
    duplicate: false,
    error_code: null,
    error_detail: null,
    document_id: "d1",
    document_mime: "application/pdf",
    document_filename: "order.pdf",
    page_count: 10,
    outcome: "WORKING",
    step: "EXTRACTING",
    steps: steps("done", "done", "active"),
    pages: Array.from({ length: 10 }, (_, i) => page(i + 1, "WAITING")),
    pages_read: 0,
    attempt: null,
    retrying: null,
    reading: null,
    events: [],
    seq: 0,
    can_read: false,
    can_retry: false,
    can_review: false,
    version: "1.1.0",
    ...over,
  };
}

describe("viewOf: what the scanner shows comes from the server", () => {
  it("shows real upload bytes before the server has the scan", () => {
    const v = viewOf(null, { kind: "uploading", fileName: "order.pdf", fraction: 0.42 });
    expect(v.scene).toBe("uploading");
    expect(v.uploadPct).toBe(42);
    expect(v.detail).toBe("42% · order.pdf");
    expect(v.steps.map((s) => s.state)).toEqual(["done", "active", "waiting", "waiting", "waiting"]);
    // no fraction known → no percentage invented
    expect(viewOf(null, { kind: "uploading", fileName: "a.pdf", fraction: null }).uploadPct).toBeNull();
  });

  it("waits for the reader until the worker reports it started", () => {
    const v = viewOf(snap());
    expect(v.scene).toBe("queued");
    expect(v.title).toBe("Waiting for the reader");
    expect(v.detail).toBe("10 pages received");
  });

  it("follows page n of m from page events", () => {
    const pages = [page(1, "READ"), page(2, "READ"), page(3, "READING"), ...Array.from({ length: 7 }, (_, i) => page(i + 4, "WAITING"))];
    const v = viewOf(snap({ pages, pages_read: 2, attempt: 1 }));
    expect(v.scene).toBe("reading");
    expect(v.detail).toBe("Page 3 of 10");
    expect(v.currentPage).toBe(3);
    expect(v.pagesRead).toBe(2);
  });

  it("shows matching, then review ready with the real counts", () => {
    const pages = Array.from({ length: 10 }, (_, i) => page(i + 1, "READ"));
    expect(viewOf(snap({ step: "MATCHING", pages, pages_read: 10, steps: steps("done", "done", "done", "active") })).scene).toBe("matching");
    const v = viewOf(
      snap({
        outcome: "READY",
        step: "REVIEW",
        pages,
        pages_read: 10,
        steps: steps("done", "done", "done", "done", "done"),
        reading: {
          id: "x",
          run_no: 1,
          status: "NEEDS_REVIEW",
          provider: "fake",
          page_count: 10,
          error_code: null,
          error_detail: null,
          lines: 4,
          to_check: 2,
          unknown_products: 1,
          unclear_quantities: 1,
          warnings: [],
          created_at: "",
          completed_at: "",
        },
        can_review: true,
      }),
    );
    expect(v.scene).toBe("ready");
    expect(v.title).toBe("Review ready");
    expect(v.detail).toBe("4 lines · 2 to check");
    expect(v.matched).toEqual({ lines: 4, toCheck: 2 });
  });

  it("says when the reader is trying again", () => {
    const v = viewOf(snap({ retrying: { code: "OCR_UNAVAILABLE", attempt: 1, max_attempts: 5 } }));
    expect(v.retrying).toBe(true);
    expect(v.tone).toBe("warn");
    expect(v.detail).toMatch(/Attempt 2 of 5/);
  });

  it("tells failures, timeouts, missing pages and duplicates apart", () => {
    const failed = viewOf(snap({ outcome: "FAILED", reading: { ...readingOf("FAILED"), error_code: "UNREADABLE" }, can_retry: true }));
    expect([failed.scene, failed.title]).toEqual(["failed", "Couldn't read the scan"]);
    expect(failed.detail).toMatch(/couldn't be opened/);
    const bridge = viewOf(snap({ outcome: "FAILED", status: "FAILED", error_code: "SCANNER_NOT_FOUND" }));
    expect([bridge.title, bridge.detail]).toEqual(["Scan failed", expect.stringMatching(/unplugged/)]);
    expect(viewOf(snap({ outcome: "TIMEOUT", reading: { ...readingOf("FAILED"), error_code: "OCR_TIMEOUT" } })).detail).toMatch(/No reader/);
    expect(viewOf(snap({ outcome: "TIMEOUT", status: "FAILED", error_code: "SCAN_TIMEOUT" })).detail).toMatch(/paper jam/);
    expect(viewOf(snap({ outcome: "TIMEOUT", status: "EXPIRED" })).detail).toMatch(/didn't pick up/);
    expect(viewOf(snap({ outcome: "MISSING", status: "FAILED", error_code: "NO_PAGES" })).scene).toBe("missing");
    expect(viewOf(null, null, { missing: true }).title).toBe("Scan not found");
    const dup = viewOf(snap({ outcome: "UNREAD", can_read: true }), null, { duplicate: true });
    expect([dup.scene, dup.title, dup.duplicate]).toEqual(["unread", "Already scanned", true]);
  });

  it("announces one sentence and labels pages for screen readers", () => {
    expect(announce(viewOf(snap()))).toBe("Waiting for the reader. 10 pages received");
    expect(pageLabel(page(3, "READ", { lines: 2, to_check: 1 }))).toBe("Page 3: read, 2 lines, 1 to check");
    expect(pageLabel(page(4, "READING"))).toBe("Page 4: being read");
  });
});

function readingOf(status: "FAILED" | "QUEUED") {
  return {
    id: "x",
    run_no: 1,
    status,
    provider: "fake",
    page_count: null,
    error_code: null,
    error_detail: null,
    lines: null,
    to_check: null,
    unknown_products: null,
    unclear_quantities: null,
    warnings: [],
    created_at: "",
    completed_at: null,
  };
}

const base: ScannerProps = { scene: "reading", total: 10, read: 2, current: 3, uploadPct: null, matched: null, duplicate: false, retrying: false, label: "Scanner: reading page 3 of 10" };
const html = (p: Partial<ScannerProps>) => renderToStaticMarkup(createElement(IsoScanner, { ...base, ...p }));

describe("IsoScanner", () => {
  it("is one labelled image whose beam runs only while a page is read", () => {
    const h = html({});
    expect(h).toContain('role="img"');
    expect(h).toContain('aria-label="Scanner: reading page 3 of 10"');
    expect(h).toContain("sc-reading");
    expect(h).toContain("sc-beam-line");
    expect(h).toContain(">3/10<");
    for (const scene of ["queued", "ready", "failed", "timeout", "uploading"] as const) expect(html({ scene })).not.toContain("sc-beam-line");
  });

  it("draws the page counts and badges the server reported", () => {
    expect(html({ scene: "uploading", uploadPct: 64 })).toContain(">64%<");
    expect(html({ scene: "ready", matched: { lines: 3, toCheck: 1 } })).toContain(">READY<");
    expect(html({ scene: "failed" })).toContain(">ERROR<");
    expect(html({ scene: "timeout" })).toContain(">TIME<");
    expect(html({ scene: "missing" })).toContain('stroke-dasharray="4 3"'); // empty feeder outline
  });

  it("gives every instance its own gradient ids", () => {
    const two = renderToStaticMarkup(createElement("div", null, createElement(IsoScanner, base), createElement(IsoScanner, base)));
    const ids = [...two.matchAll(/id="([^"]+)-top"/g)].map((m) => m[1]);
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
  });
});

describe("StepsRail and PageStrip", () => {
  it("marks the current step for assistive tech", () => {
    const h = renderToStaticMarkup(createElement(StepsRail, { steps: viewOf(snap()).steps }));
    expect(h).toContain('aria-current="step"');
    expect(h).toContain("Extracting text<span class=\"sr-only\">: in progress</span>");
    expect(h).toContain("Retrieving PDF<span class=\"sr-only\">: done</span>");
  });

  it("is a pressed-state button per page, hidden for one page", () => {
    const pages = [page(1, "READ", { lines: 1, to_check: 0 }), page(2, "READING")];
    const h = renderToStaticMarkup(createElement(PageStrip, { pages, selected: 1, onSelect: () => undefined }));
    expect(h.match(/<button/g)).toHaveLength(2);
    expect(h).toContain('aria-pressed="true"');
    expect(h).toContain('aria-label="Page 2: being read"');
    expect(renderToStaticMarkup(createElement(PageStrip, { pages: [page(1, "READ")], selected: null, onSelect: () => undefined }))).toBe("");
  });
});
