import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { announce, pageLabel, viewOf, type Local, type ProgressPage, type ScanProgress, type StepState, type View } from "../../lib/scanProgress";
import { scanMessage } from "../../lib/scans";
import { Icon } from "../../ui/Icon";
import { IsoScanner } from "./IsoScanner";
import { useScanProgress } from "./useScanProgress";
import "./scanner.css";

const STATE_TEXT: Record<StepState, string> = { done: "done", active: "in progress", failed: "failed", waiting: "not started", skipped: "not needed" };

/** The five steps, left to right (stacked on narrow screens). */
export function StepsRail({ steps }: { steps: View["steps"] }) {
  const doneCount = steps.filter((s) => s.state === "done").length;
  return (
    <ol className="relative mx-auto grid max-w-3xl gap-3 sm:grid-cols-5 sm:gap-0" aria-label="Progress">
      <span aria-hidden className="absolute left-[10%] right-[10%] top-[15px] hidden h-0.5 rounded-full bg-line sm:block" />
      <span
        aria-hidden
        className="sc-rail-fill absolute left-[10%] top-[15px] hidden h-0.5 w-[80%] rounded-full bg-brand-accent sm:block"
        style={{ transform: `scaleX(${Math.min(doneCount, steps.length - 1) / (steps.length - 1)})` }}
      />
      {steps.map((s) => (
        <li key={s.key} className="relative flex items-center gap-3 sm:flex-col sm:gap-2 sm:text-center" aria-current={s.state === "active" ? "step" : undefined} data-step={s.key} data-state={s.state}>
          <span
            className={`sc-step-dot grid size-8 shrink-0 place-items-center rounded-full text-xs font-semibold ${
              s.state === "done"
                ? "bg-brand-accent text-white"
                : s.state === "active"
                  ? "sc-step-active bg-card text-brand-primary ring-2 ring-brand-accent"
                  : s.state === "failed"
                    ? "bg-danger text-white"
                    : "bg-card text-ink-muted ring-1 ring-line"
            }`}
          >
            {s.state === "done" ? <Icon name="check" size={15} /> : s.state === "failed" ? <Icon name="x" size={15} /> : s.state === "active" ? <span className="size-2.5 rounded-full bg-brand-accent motion-safe:animate-pulse" /> : s.state === "skipped" ? "–" : null}
          </span>
          <span className={`text-sm ${s.state === "active" || s.state === "failed" ? "font-semibold text-ink" : s.state === "done" ? "text-ink" : "text-ink-muted"}`}>
            {s.label}
            <span className="sr-only">: {STATE_TEXT[s.state]}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

/** One button per page; picking one shows what was read on it. */
export function PageStrip({ pages, selected, onSelect }: { pages: ProgressPage[]; selected: number | null; onSelect: (n: number | null) => void }) {
  if (pages.length < 2) return null;
  return (
    <div className="flex flex-wrap justify-center gap-1.5" role="group" aria-label="Pages">
      {pages.map((p) => {
        const on = selected === p.page;
        const tone =
          p.state === "READ"
            ? p.to_check
              ? "border-warning/40 bg-warning/10 text-ink"
              : "border-brand-accent/40 bg-brand-accent/10 text-brand-primary-hover"
            : p.state === "READING"
              ? "sc-page-reading border-brand-accent bg-card text-brand-primary"
              : p.state === "FAILED"
                ? "border-danger/40 bg-danger/8 text-danger"
                : "border-line bg-card text-ink-muted";
        return (
          <button
            key={p.page}
            type="button"
            className={`sc-page num relative grid h-9 min-w-9 place-items-center rounded-ctl border px-2 text-sm font-semibold ${tone} ${on ? "outline-2 outline-offset-2 outline-brand-primary" : ""}`}
            aria-pressed={on}
            aria-label={pageLabel(p)}
            title={pageLabel(p)}
            onClick={() => onSelect(on ? null : p.page)}
            data-testid={`page-${p.page}`}
            data-state={p.state}
          >
            {p.page}
            {p.state === "READING" && <span aria-hidden className="sc-page-dot absolute -right-1 -top-1 size-2.5 rounded-full bg-brand-accent" />}
            {p.state === "READ" && !!p.to_check && <span aria-hidden className="absolute -right-1 -top-1 size-2.5 rounded-full bg-warning" />}
          </button>
        );
      })}
    </div>
  );
}

function PageDetail({ p }: { p: ProgressPage }) {
  const bits: string[] = [];
  if (p.words !== null) bits.push(`${p.words} word${p.words === 1 ? "" : "s"} read`);
  if (p.confidence !== null) bits.push(`${Math.round(Number(p.confidence) * 100)}% sure`);
  if (p.lines !== null) bits.push(`${p.lines} line${p.lines === 1 ? "" : "s"}`);
  if (p.to_check) bits.push(`${p.to_check} to check`);
  return (
    <p className="text-sm text-ink-muted" data-testid="page-detail">
      <span className="font-semibold text-ink">Page {p.page}</span>
      {bits.length ? ` · ${bits.join(" · ")}` : p.state === "WAITING" ? " · not read yet" : ""}
      {p.sample && <span className="mt-1 block truncate text-ink">“{p.sample}”</span>}
    </p>
  );
}

const TONE_RING: Record<View["tone"], string> = {
  working: "bg-brand-accent/12 text-brand-primary",
  ok: "bg-brand-accent/12 text-brand-primary",
  warn: "bg-warning/12 text-warning",
  error: "bg-danger/10 text-danger",
  neutral: "bg-subtle text-ink-muted",
};

export type ScanProcessingProps = {
  /** null while the browser is still sending the file. */
  jobId: string | null;
  local?: Local | null;
  /** The upload answered "this exact file is already on file". */
  duplicate?: boolean;
  /** Smaller picture, no page details (several files at once). */
  compact?: boolean;
  /** Bridge scan failed / upload failed: offer to start over. */
  onRestart?: () => void;
  restartLabel?: string;
  /** Instead of linking to the review page (e.g. the review opens on the same page). */
  onReview?: (extractionId: string) => void;
  /** Hide "Open scan" (the page already links to the scan). */
  hideOpenLink?: boolean;
  /** Hide "Review now" (the page shows its own review button). */
  hideReview?: boolean;
  /** Called whenever the server's answer changes. */
  onProgress?: (p: ScanProgress) => void;
  heading?: ReactNode;
};

/** Animated scanner following one scan through the five steps, from the server's own events. */
export function ScanProcessing({ jobId, local = null, duplicate = false, compact = false, onRestart, restartLabel = "Scan again", onReview, hideOpenLink = false, hideReview = false, onProgress, heading }: ScanProcessingProps) {
  const { snap, missing, live, restart } = useScanProgress(jobId);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const view = viewOf(jobId ? snap : null, local, { duplicate, missing });
  const cb = useRef(onProgress);
  cb.current = onProgress;
  useEffect(() => {
    if (snap) cb.current?.(snap);
  }, [snap]);

  const pages = snap?.pages ?? [];
  const picked = selected !== null ? (pages.find((p) => p.page === selected) ?? null) : null;
  const finished = view.scene === "ready" || view.scene === "done";
  const onGlass = picked && finished ? picked.page : view.currentPage;

  async function readNow() {
    if (!snap) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.readScan(snap.job_id);
      setSelected(null);
      restart();
    } catch (e) {
      setActionError(scanMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const exId = snap?.reading?.id ?? null;
  const reviewTo = exId ? `/scan/review/${exId}${selected ? `?page=${selected}` : ""}` : null;
  const label = `Scanner: ${view.title}${view.detail ? `. ${view.detail}` : ""}`;
  const canRestart = !!onRestart && (view.scene === "failed" || view.scene === "timeout" || view.scene === "missing" || view.scene === "cancelled") && !snap?.can_retry;

  return (
    <section className="card overflow-hidden" aria-label={heading ? undefined : "Scan progress"} data-testid="scan-processing" data-scene={view.scene} data-outcome={snap?.outcome ?? ""}>
      <div className={`sc-stage relative ${compact ? "px-3 pt-3" : "px-6 pt-5"}`}>
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="inline-flex min-w-0 items-center gap-2 font-medium text-ink-muted">
            {heading ?? (snap ? <span className="num truncate">{snap.job_no}</span> : local ? <span className="truncate">{local.fileName}</span> : null)}
          </span>
          {jobId && !compact && (
            <span className={`inline-flex items-center gap-1.5 text-xs ${live ? "text-brand-primary" : "text-ink-muted"}`}>
              <span aria-hidden className={`size-2 rounded-full ${live ? "bg-brand-accent" : "bg-brand-gray"}`} />
              {live ? "Live" : "Updated"}
            </span>
          )}
        </div>
        <div className={`mx-auto ${compact ? "max-w-[17rem]" : "max-w-[34rem]"}`}>
          <IsoScanner
            scene={view.scene}
            total={view.pageTotal}
            read={view.pagesRead}
            current={onGlass}
            selected={finished ? (picked?.page ?? null) : null}
            uploadPct={view.uploadPct}
            matched={view.matched}
            duplicate={view.duplicate}
            retrying={view.retrying}
            label={label}
          />
        </div>
      </div>

      <div className={`space-y-5 text-center ${compact ? "px-4 pb-4 pt-1" : "px-6 pb-7 pt-2"}`}>
        <div key={`${view.scene}-${view.title}`} className="sc-swap space-y-1">
          <h2 className={`flex items-center justify-center gap-2 font-semibold tracking-tight ${compact ? "text-lg" : "text-[26px] leading-tight"}`} data-testid="scan-title">
            <span aria-hidden className={`grid size-8 place-items-center rounded-full ${TONE_RING[view.tone]}`}>
              <Icon name={view.tone === "ok" ? "check" : view.tone === "error" ? "alert" : view.tone === "warn" ? "clock" : view.scene === "uploading" ? "upload" : "scan"} size={16} />
            </span>
            {view.title}
          </h2>
          {view.detail && <p className={`text-ink-muted ${compact ? "text-sm" : "text-base"}`} data-testid="scan-detail">{view.detail}</p>}
        </div>
        <p className="sr-only" aria-live="polite">
          {announce(view)}
        </p>

        {!compact && <StepsRail steps={view.steps} />}

        {!compact && pages.length > 1 && (
          <div className="space-y-2">
            <PageStrip pages={pages} selected={selected} onSelect={setSelected} />
            {picked && <PageDetail p={picked} />}
          </div>
        )}
        {!compact && pages.length === 1 && finished && pages[0] && pages[0].words !== null && <PageDetail p={pages[0]} />}

        {actionError && (
          <p role="alert" className="alert-danger mx-auto max-w-xl text-left">
            {actionError}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-center gap-3">
          {view.scene === "ready" && snap?.can_review && exId && !hideReview && (onReview ? (
            <button type="button" className="btn-accent px-6 py-3 text-base" onClick={() => onReview(exId)}>
              <Icon name="check" />Review now
            </button>
          ) : (
            reviewTo && (
              <Link className="btn-accent px-6 py-3 text-base" to={reviewTo}>
                <Icon name="check" />Review now
              </Link>
            )
          ))}
          {view.scene === "done" && reviewTo && (
            <Link className="btn-ghost px-5 py-3" to={reviewTo}>
              <Icon name="orders" />Open reading
            </Link>
          )}
          {snap?.can_retry && (
            <button type="button" className="btn-accent px-6 py-3 text-base" disabled={busy} onClick={() => void readNow()}>
              <Icon name="refresh" />
              {busy ? "Starting…" : "Read again"}
            </button>
          )}
          {snap?.can_read && (
            <button type="button" className="btn-accent px-6 py-3 text-base" disabled={busy} onClick={() => void readNow()}>
              <Icon name="scan" />
              {busy ? "Starting…" : "Read this scan"}
            </button>
          )}
          {(canRestart || local?.kind === "upload-failed") && onRestart && (
            <button type="button" className="btn-ghost px-5 py-3" onClick={onRestart}>
              <Icon name="refresh" />
              {restartLabel}
            </button>
          )}
          {jobId && !hideOpenLink && !compact && (
            <Link className="btn-ghost px-5 py-3" to={`/scan/${jobId}`}>
              <Icon name="file" />Open scan
            </Link>
          )}
        </div>
      </div>
    </section>
  );
}
