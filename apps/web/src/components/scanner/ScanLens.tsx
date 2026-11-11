import { useEffect, useRef, useState } from "react";
import { autoZoom, LENS_H as H, LENS_W as W, LENS_ZOOMS, lensDpi, lensLayout, type LensBox, type LensZoom } from "../../lib/scanLens";

const KEY = "pt.scan-lens-zoom";

function savedZoom(): LensZoom {
  try {
    const v = Number(localStorage.getItem(KEY));
    return (LENS_ZOOMS as readonly number[]).includes(v) ? (v as LensZoom) : 0;
  } catch {
    return 0;
  }
}

/**
 * Magnifier for the scan review: while a line is hovered (or focused), a small floating window
 * beside it shows that line's row on the original scan (item, Q, PKG, weight, notes), zoomed:
 * Auto, 2x, 3x or 4x; the mouse
 * wheel over the window changes the zoom too, and the choice is remembered on this computer.
 * Read-only: it only displays the original.
 */
export function ScanLens({
  documentId,
  box,
  anchor,
  label,
  onKeep,
  onLeave,
}: {
  documentId: string;
  box: LensBox;
  /** The hovered row, to place the window beside it. */
  anchor: DOMRect;
  label: string;
  /** The pointer is over the window: keep it open. */
  onKeep: () => void;
  onLeave: () => void;
}) {
  const [zoomPick, setZoomPick] = useState<LensZoom>(savedZoom);
  const [ratio, setRatio] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const zoom = zoomPick === 0 ? autoZoom(box) : zoomPick;
  const dpi = lensDpi(zoom);
  const src = `/api/v1/scans/documents/${documentId}/pages/${box.page}?dpi=${dpi}`;
  useEffect(() => setFailed(false), [src]);
  // Fetch both sharp copies of the page once, so changing the zoom never waits.
  useEffect(() => {
    for (const d of [220, 300]) {
      const im = new Image();
      im.src = `/api/v1/scans/documents/${documentId}/pages/${box.page}?dpi=${d}`;
    }
  }, [documentId, box.page]);
  // The wheel over the window zooms (a native listener: React's is passive and can't stop the
  // page from scrolling underneath).
  const frame = useRef<HTMLDivElement>(null);
  const wheel = useRef<(e: WheelEvent) => void>(() => undefined);
  wheel.current = (e: WheelEvent) => {
    e.preventDefault();
    // from Auto, step from the nearest fixed zoom
    const now = zoomPick === 0 ? Math.min(4, Math.max(2, Math.round(zoom))) : zoomPick;
    const i = LENS_ZOOMS.indexOf(now as LensZoom);
    pick(LENS_ZOOMS[Math.min(LENS_ZOOMS.length - 1, Math.max(1, i + (e.deltaY < 0 ? 1 : -1)))] as LensZoom);
  };
  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const on = (e: WheelEvent) => wheel.current(e);
    el.addEventListener("wheel", on, { passive: false });
    return () => el.removeEventListener("wheel", on);
  }, []);
  const pick = (z: LensZoom) => {
    setZoomPick(z);
    try {
      localStorage.setItem(KEY, String(z));
    } catch {
      /* storage blocked */
    }
  };

  // Beside the row: below it when there is room, else above; never off the window.
  const vw = typeof window === "undefined" ? 1440 : window.innerWidth;
  const vh = typeof window === "undefined" ? 900 : window.innerHeight;
  const total = H + 40;
  const left = Math.max(8, Math.min(anchor.left + Math.min(220, anchor.width * 0.18), vw - W - 16));
  const top = anchor.bottom + 6 + total <= vh - 8 ? anchor.bottom + 6 : Math.max(8, anchor.top - total - 6);
  const lay = ratio ? lensLayout(box, zoom, ratio) : null;

  return (
    <div
      role="dialog"
      aria-label={`Original scan, ${label}`}
      data-testid="scan-lens"
      data-zoom={Math.round(zoom * 10) / 10}
      className="fixed z-40 overflow-hidden rounded-[var(--radius-ctl)] border border-line bg-card shadow-[0_12px_32px_rgb(24_24_27/0.18)]"
      style={{ left, top, width: W }}
      ref={frame}
      onMouseEnter={onKeep}
      onMouseLeave={onLeave}
    >
      <div className="relative overflow-hidden bg-subtle" style={{ width: W, height: H }}>
        {failed ? (
          <p className="p-4 text-sm text-ink-muted">The original can't be shown here.</p>
        ) : (
          <>
            {/* Same element across zoom changes: the browser keeps the current picture on screen
                until the sharper one has arrived. */}
            <img
              src={src}
              alt={`Original scan, ${label}, zoomed ${Math.round(zoom * 10) / 10} times`}
              draggable={false}
              onLoad={(e) => setRatio(e.currentTarget.naturalWidth ? e.currentTarget.naturalHeight / e.currentTarget.naturalWidth : 1.29)}
              onError={() => setFailed(true)}
              className="absolute max-w-none select-none"
              style={lay ? { width: lay.width, height: lay.height, left: lay.left, top: lay.top } : { opacity: 0 }}
            />
            {lay && (
              <div
                aria-hidden
                data-testid="scan-lens-spot"
                className="pointer-events-none absolute rounded-md ring-[3px] ring-brand-accent"
                // Spotlight: everything but the line's row is dimmed, the row stays bright.
                style={{ left: lay.box.left, top: lay.box.top - 3, width: lay.box.width, height: lay.box.height + 6, boxShadow: "0 0 0 9999px rgb(24 24 27 / 0.55)" }}
              />
            )}
            {!lay && <div className="skeleton absolute inset-3" role="status" aria-label="Loading the original" />}
          </>
        )}
      </div>
      <div className="flex items-center gap-1.5 border-t border-line px-2.5 py-1.5 text-xs">
        <span className="mr-auto truncate text-ink-muted">Original · page {box.page}</span>
        {LENS_ZOOMS.map((z) => (
          <button
            key={z}
            type="button"
            aria-pressed={zoomPick === z}
            onClick={() => pick(z)}
            className={`cursor-pointer rounded-full px-2.5 py-0.5 font-medium transition-colors ${zoomPick === z ? "bg-brand-secondary text-white" : "text-ink hover:bg-subtle"}`}
          >
            {z === 0 ? "Auto" : `${z}×`}
          </button>
        ))}
      </div>
    </div>
  );
}
