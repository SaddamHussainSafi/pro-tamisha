import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type OcrExtraction, type ScanJob, type ScanJobType } from "../api";
import { AUTO_READ, checkFile, scanMessage, stageOf, STAGES, type Stage } from "../lib/scans";
import { Icon } from "../ui/Icon";

const POLL_MS = 2000;

/** Stepper for one scan. The active step animates; motion is off for reduced-motion users. */
export function StageSteps({ stage, progress, error }: { stage: Stage; progress?: number; error?: string | null }) {
  const at = stage === "done" ? STAGES.length : stage === "failed" ? 1 : STAGES.findIndex((s) => s.key === stage);
  return (
    <ol className="space-y-3" aria-label="Processing steps">
      {STAGES.map((s, i) => {
        const done = i < at || stage === "done" || (stage === "review" && i === 2);
        const active = i === at && stage !== "review" && stage !== "done";
        const failed = stage === "failed" && i === 1;
        return (
          <li key={s.key} className="flex items-start gap-3" aria-current={active ? "step" : undefined}>
            <span
              className={`grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold transition-colors duration-500 ${
                failed ? "bg-danger text-white" : done ? "bg-brand-accent text-white" : active ? "bg-brand-accent/20 text-brand-primary" : "bg-subtle text-ink-muted"
              }`}
            >
              {failed ? <Icon name="alert" size={14} /> : done ? <Icon name="check" size={14} /> : active ? <span className="size-2.5 rounded-full bg-brand-accent motion-safe:animate-ping" /> : i + 1}
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <p className={`text-sm ${active || failed ? "font-semibold" : done ? "" : "text-ink-muted"}`}>
                {failed ? "Reading failed" : s.label}
                {s.key === "upload" && active && progress !== undefined && <span className="num text-ink-muted"> · {Math.round(progress * 100)}%</span>}
              </p>
              {active && (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-subtle" role="progressbar" aria-label={s.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={s.key === "upload" && progress !== undefined ? Math.round(progress * 100) : undefined}>
                  {s.key === "upload" && progress !== undefined ? (
                    <div className="h-full rounded-full bg-brand-accent transition-[width] duration-300 ease-out" style={{ width: `${Math.max(3, progress * 100)}%` }} />
                  ) : (
                    <div className="pt-indeterminate h-full w-1/3 rounded-full bg-brand-accent" />
                  )}
                </div>
              )}
              {failed && error && <p className="mt-1 text-sm text-danger">{error}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

type Item = { key: string; file: File; progress: number; job: ScanJob | null; ex: OcrExtraction | null; error: string | null };

/** Several files at once: uploaded one after another (real byte progress), each its own scan. */
export function UploadQueue({ files, warehouseId, jobType, onDone }: { files: File[]; warehouseId: string; jobType: ScanJobType; onDone: () => void }) {
  const [items, setItems] = useState<Item[]>(() => files.map((file, i) => ({ key: `${i}-${file.name}`, file, progress: 0, job: null, ex: null, error: checkFile(file) })));
  const set = (key: string, patch: Partial<Item>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));

  useEffect(() => {
    let live = true;
    void (async () => {
      for (const it of items) {
        if (!live || it.error) continue;
        try {
          const job = await api.uploadScanProgress(it.file, { warehouse_id: warehouseId, job_type: jobType }, (f) => live && set(it.key, { progress: f }));
          if (live) set(it.key, { job, progress: 1 });
        } catch (e) {
          if (live) set(it.key, { error: scanMessage(e) });
        }
      }
      if (live) onDone();
    })();
    return () => {
      live = false;
    };
    // run once for the files given
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reading = items.filter((x) => x.job && AUTO_READ.includes(jobType) && (!x.ex || x.ex.status === "QUEUED"));
  useEffect(() => {
    if (!reading.length) return;
    const t = setInterval(() => {
      for (const it of reading) {
        if (!it.job) continue;
        api.ocrExtractions({ scan_job_id: it.job.id, status: "QUEUED,NEEDS_REVIEW,FAILED,ACCEPTED,REJECTED" }).then((r) => set(it.key, { ex: r.items[0] ?? null }), () => undefined);
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [reading.length]); // eslint-disable-line react-hooks/exhaustive-deps

  async function retry(it: Item) {
    if (!it.job) return;
    try {
      await api.readScan(it.job.id);
      set(it.key, { ex: null });
    } catch (e) {
      set(it.key, { error: scanMessage(e) });
    }
  }

  return (
    <section className="card space-y-4 p-6" aria-labelledby="uq-h">
      <h2 id="uq-h" className="text-xl font-semibold tracking-tight">
        {items.length} files
      </h2>
      <ul className="grid gap-4 md:grid-cols-2">
        {items.map((it) => {
          const stage: Stage = it.error && !it.job ? "failed" : stageOf(!!it.job, it.ex?.status ?? null);
          return (
            <li key={it.key} className="rounded-ctl border border-line p-4" data-testid="upload-item">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <Icon name="file" size={16} className="text-ink-muted" />
                <span className="min-w-0 flex-1 truncate">{it.file.name}</span>
                {it.ex?.page_count ? <span className="text-ink-muted">{it.ex.page_count} p.</span> : null}
              </div>
              {AUTO_READ.includes(jobType) || !it.job ? (
                <StageSteps stage={stage} progress={it.job ? undefined : it.progress} error={it.error ?? it.ex?.error_detail ?? null} />
              ) : (
                <p className="text-sm">Saved. This document type isn't read automatically.</p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                {it.job && (
                  <Link className="btn-ghost px-3 py-1.5 text-xs" to={`/scan/${it.job.id}`}>
                    {it.job.duplicate ? `Already uploaded: ${it.job.job_no}` : `Open ${it.job.job_no}`}
                  </Link>
                )}
                {it.ex?.status === "NEEDS_REVIEW" && (
                  <Link className="btn-primary px-3 py-1.5 text-xs" to={`/scan/review/${it.ex.id}`}>
                    Review
                  </Link>
                )}
                {it.ex?.status === "FAILED" && (
                  <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => void retry(it)}>
                    Read again
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
