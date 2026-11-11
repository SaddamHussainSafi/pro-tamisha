import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { gmail } from "../../lib/gmail";
import { Icon } from "../../ui/Icon";

/** A plain modal on the native <dialog> (focus trap + Esc), sized for documents and pickers. */
export function Modal({ open, title, onClose, children, footer, wide = false }: { open: boolean; title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby={titleId}
      className={`m-auto flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden rounded-card border-0 bg-card p-0 text-ink shadow-[0_8px_24px_rgb(24_24_27/0.12)] backdrop:bg-ink/40 [&:not([open])]:hidden ${wide ? "h-[calc(100dvh-2rem)] w-[min(78rem,calc(100vw-2rem))]" : "w-[min(40rem,calc(100vw-2rem))]"}`}
    >
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <h2 id={titleId} className="min-w-0 truncate text-lg font-semibold tracking-tight">
          {title}
        </h2>
        <button type="button" className="icon-btn size-9" aria-label="Close" onClick={onClose}>
          <Icon name="x" size={16} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">{open && children}</div>
      {footer && <div className="flex flex-wrap items-center justify-end gap-2.5 border-t border-line px-5 py-3.5">{footer}</div>}
    </dialog>
  );
}

/**
 * Page-by-page view of an email attachment. The server draws each page as a picture, so the
 * file itself never runs in the browser. Page navigation, zoom, rotate and full screen.
 */
export function PagePreview({ attachmentId, title, className = "", fullScreen = true }: { attachmentId: string; title: string; className?: string; fullScreen?: boolean }) {
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [turn, setTurn] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  const [ratio, setRatio] = useState(1); // page width / height, to fit a quarter-turned page
  useEffect(() => {
    let live = true;
    setPage(1);
    setPages(null);
    setProblem(null);
    setZoom(1);
    setTurn(0);
    fetch(gmail.pageUrl(attachmentId, 1), { credentials: "same-origin" }).then(
      async (r) => {
        if (!live) return;
        if (r.ok) return setPages(Number(r.headers.get("X-Page-Count") ?? "1"));
        const data = (await r.json().catch(() => ({}))) as { error?: { message?: string } };
        setProblem(data.error?.message ?? "This file can't be shown.");
      },
      () => live && setProblem("This file can't be shown right now."),
    );
    return () => {
      live = false;
    };
  }, [attachmentId]);

  const btn = "grid size-9 shrink-0 cursor-pointer place-items-center rounded-ctl border border-line bg-card text-sm hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-40";
  const body = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-2" role="toolbar" aria-label="Document view">
        <button type="button" className={btn} aria-label="Previous page" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          <Icon name="chevronLeft" size={16} />
        </button>
        <span className="num text-sm" aria-live="polite">
          Page {page} of {pages ?? "…"}
        </span>
        <button type="button" className={btn} aria-label="Next page" disabled={!pages || page >= pages} onClick={() => setPage((p) => p + 1)}>
          <Icon name="chevron" size={16} />
        </button>
        <span className="mx-1 h-5 w-px bg-line" aria-hidden />
        <button type="button" className={btn} aria-label="Zoom out" disabled={zoom <= 0.5} onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}>
          −
        </button>
        <span className="num w-12 text-center text-sm">{Math.round(zoom * 100)}%</span>
        <button type="button" className={btn} aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom((z) => Math.min(3, z + 0.25))}>
          +
        </button>
        <button type="button" className={btn} aria-label="Rotate" onClick={() => setTurn((t) => (t + 90) % 360)}>
          <Icon name="refresh" size={16} />
        </button>
        {!full && fullScreen && (
          <button type="button" className="ml-auto rounded-ctl border border-line bg-card px-3 py-1.5 text-sm hover:bg-subtle" onClick={() => setFull(true)}>
            Full screen
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-subtle p-3" tabIndex={0} role="region" aria-label={`${title}, page ${page}`}>
        {problem ? (
          <p className="p-6 text-sm text-ink-muted" role="alert">
            {problem}
          </p>
        ) : pages === null ? (
          <div className="skeleton mx-auto aspect-[8.5/11] w-full max-w-xl" role="status" aria-label="Loading page" />
        ) : (
          <div className="mx-auto transition-transform duration-200" style={{ width: `${zoom * 100}%`, transform: `rotate(${turn}deg) scale(${turn % 180 ? Math.min(ratio, 1 / ratio) : 1})` }}>
            <img
              alt={`${title}, page ${page}`}
              src={gmail.pageUrl(attachmentId, page)}
              onLoad={(e) => setRatio(e.currentTarget.naturalHeight ? e.currentTarget.naturalWidth / e.currentTarget.naturalHeight : 1)}
              className="block w-full rounded-ctl border border-line bg-card"
            />
          </div>
        )}
      </div>
    </div>
  );
  return (
    <>
      <div className={`overflow-hidden rounded-[var(--radius-ctl)] border border-line ${className}`}>{!full && body}</div>
      <Modal open={full} title={title} onClose={() => setFull(false)} wide>
        {body}
      </Modal>
    </>
  );
}
