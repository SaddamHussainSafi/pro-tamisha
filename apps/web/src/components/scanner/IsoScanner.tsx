import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import type { Scene } from "../../lib/scanProgress";
import "./scanner.css";

/* Isometric scanner, drawn in SVG from a small 3D model so the faces, pages and beam line up.
   Axes: x runs to the lower right (the paper path: hopper → glass → output tray), y to the lower
   left, z up. The picture is a function of the props alone; the props come from the server. */

const C = 0.866;
const S = 0.5;
const O: [number, number] = [262, 196];
type V3 = [number, number, number];

function P(x: number, y: number, z: number): [number, number] {
  return [O[0] + (x - y) * C, O[1] + (x + y) * S - z];
}
const r1 = (n: number) => Math.round(n * 10) / 10;
const pts = (...ps: V3[]) => ps.map((p) => P(...p).map(r1).join(",")).join(" ");
/** Horizontal rectangle at height z. */
const flatRect = (x0: number, y0: number, x1: number, y1: number, z: number) => pts([x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]);
/** Wall facing the lower left (y = const). */
const wallY = (x0: number, x1: number, y: number, z0: number, z1: number) => pts([x0, y, z1], [x1, y, z1], [x1, y, z0], [x0, y, z0]);
/** Wall facing the lower right (x = const). */
const wallX = (x: number, y0: number, y1: number, z0: number, z1: number) => pts([x, y0, z1], [x, y1, z1], [x, y1, z0], [x, y0, z0]);
/** Local 2D (u → +x, v → +y) lying flat at height z, origin at (x, y). */
const flat = (x: number, y: number, z: number) => {
  const [e, f] = P(x, y, z);
  return `matrix(${C} ${S} ${-C} ${S} ${r1(e)} ${r1(f)})`;
};
/** Local 2D on the lower-left wall (y = const): u → +x, down = −z. */
const onWallY = (x: number, y: number, z: number) => {
  const [e, f] = P(x, y, z);
  return `matrix(${C} ${S} 0 1 ${r1(e)} ${r1(f)})`;
};
/** Local 2D on a vertical plane x = const: u → +y, v → +z. */
const onWallX = (x: number, y: number, z: number) => {
  const [e, f] = P(x, y, z);
  return `matrix(${-C} ${S} 0 -1 ${r1(e)} ${r1(f)})`;
};
const shift = (a: V3, b: V3) => {
  const [ax, ay] = P(...a);
  const [bx, by] = P(...b);
  return { "--sc-fx": `${r1(ax - bx)}px`, "--sc-fy": `${r1(ay - by)}px` } as CSSProperties;
};

// Body and parts (model units).
const W = 220;
const D = 160;
const H = 48;
const PW = 104; // page length along the paper path
const PH = 74; // page width
const BED: V3 = [58, 43, H + 1.4];
const hop = (i: number): V3 => [-104, 43, 75 + i * 2.4];
const out = (i: number): V3 => [214, 43, 17 + i * 2.4];
const STACK = 5;
// text lines on a page: [u, v-start, length, bold]
const LINES: [number, number, number, boolean][] = [
  [9, 9, 30, true],
  [9, 46, 18, false],
  [20, 9, 54, false],
  [29, 9, 46, false],
  [38, 9, 56, false],
  [47, 9, 40, false],
  [56, 9, 52, false],
  [65, 9, 36, false],
  [74, 9, 50, false],
  [83, 9, 28, false],
];

export type ScannerProps = {
  scene: Scene;
  /** Pages in the document (0 = not known yet). */
  total: number;
  /** Pages the reader has finished. */
  read: number;
  /** Page on the glass (being read, failed, or picked in the page strip). */
  current: number | null;
  uploadPct: number | null;
  matched: { lines: number; toCheck: number } | null;
  duplicate: boolean;
  retrying: boolean;
  /** Accessible description of what the picture shows. */
  label: string;
  /** Page picked in the page strip, shown on the glass once reading is over. */
  selected?: number | null;
  /** Pure illustration (no state to tell): hidden from assistive tech. */
  decorative?: boolean;
};

type Tone = "cyan" | "ok" | "warn" | "error" | "idle";
const LED: Record<Tone, string> = { cyan: "var(--sc-beam)", ok: "var(--sc-ok)", warn: "var(--sc-warn)", error: "var(--sc-error)", idle: "var(--sc-idle)" };

function display(p: ScannerProps): string {
  switch (p.scene) {
    case "idle":
      return "READY";
    case "connecting":
      return "· · ·";
    case "retrieving":
      return "SCAN";
    case "uploading":
      return p.uploadPct === null ? "SEND" : `${p.uploadPct}%`;
    case "queued":
      return p.retrying ? "RETRY" : "WAIT";
    case "reading":
      return p.total ? `${p.current ?? 1}/${p.total}` : "READ";
    case "matching":
      return "MATCH";
    case "ready":
      return p.selected ? `P${p.selected}` : "READY";
    case "done":
      return "DONE";
    case "failed":
      return "ERROR";
    case "timeout":
      return "TIME";
    case "missing":
      return "EMPTY";
    case "unread":
      return "IDLE";
    case "cancelled":
      return "—";
  }
}

function ledTone(p: ScannerProps): Tone {
  if (p.scene === "failed" || p.scene === "missing") return "error";
  if (p.scene === "timeout" || p.retrying) return "warn";
  if (p.scene === "ready" || p.scene === "done" || p.scene === "idle") return "ok";
  if (p.scene === "unread" || p.scene === "cancelled" || p.scene === "connecting") return "idle";
  return "cyan";
}

/** What sits where: sheets in the hopper, the page on the glass, sheets on the output tray. */
function layout(p: ScannerProps) {
  const total = Math.max(p.total, 1);
  const read = Math.min(p.read, total);
  switch (p.scene) {
    case "cancelled":
      return { hopper: 0, bed: null as number | null, out: 0 };
    case "connecting": // the file is with the server; its first answer is on the way
      return { hopper: 0, bed: 1, out: 0 };
    case "retrieving":
    case "idle":
      return { hopper: 3, bed: null, out: 0 };
    case "missing":
      return { hopper: 0, bed: null, out: 0 };
    case "uploading":
      return { hopper: 0, bed: 1, out: 0 };
    case "matching":
      return { hopper: 0, bed: null, out: total };
    case "ready":
    case "done":
      return { hopper: 0, bed: p.selected ?? null, out: total };
    default: {
      const bed = p.current ?? Math.min(read + 1, total);
      return { hopper: Math.max(total - read - 1, 0), bed, out: read };
    }
  }
}

function Page({ uid, lit, tint, dashed }: { uid: string; lit: "none" | "beam" | "all"; tint?: string; dashed?: boolean }) {
  return (
    <g>
      <rect x={1.5} y={1.5} width={PW} height={PH} rx={2} fill="var(--sc-shadow)" opacity={0.1} />
      <rect width={PW} height={PH} rx={2} fill={dashed ? "none" : "var(--sc-paper)"} stroke={dashed ? "var(--sc-line-strong)" : "var(--sc-paper-edge)"} strokeWidth={0.8} strokeDasharray={dashed ? "4 3" : undefined} />
      {!dashed && (
        <g>
          {LINES.map(([u, v, len, bold]) => (
            <rect key={`${u}-${v}`} x={u} y={v} width={bold ? 4 : 2.6} height={len * 0.82} rx={1.2} fill={bold ? "var(--sc-line-strong)" : "var(--sc-line)"} />
          ))}
          {lit !== "none" && (
            <g clipPath={lit === "beam" ? `url(#${uid}-reveal)` : undefined}>
              {LINES.map(([u, v, len, bold]) => (
                <rect key={`${u}-${v}`} x={u} y={v} width={bold ? 4 : 2.6} height={len * 0.82} rx={1.2} fill={lit === "all" ? "var(--sc-ok)" : "var(--sc-beam)"} opacity={lit === "all" ? 0.75 : 0.9} />
              ))}
            </g>
          )}
          {tint && <rect width={PW} height={PH} rx={2} fill={tint} opacity={0.14} />}
        </g>
      )}
    </g>
  );
}

function Badge({ kind, uid }: { kind: "ok" | "error" | "warn" | "copy" | "missing" | "cancel"; uid: string }) {
  const [cx, cy] = P(110, 80, 182);
  const fill = { ok: "var(--sc-ok)", error: "var(--sc-error)", warn: "var(--sc-warn)", copy: "var(--sc-badge-copy)", missing: "var(--sc-badge-missing)", cancel: "var(--sc-idle)" }[kind];
  return (
    <g className="sc-pop" key={kind}>
      <circle cx={cx} cy={cy} r={27} fill={fill} filter={`url(#${uid}-soft)`} />
      <circle cx={cx} cy={cy} r={27} fill="none" stroke="var(--sc-paper)" strokeOpacity={0.55} strokeWidth={2} />
      <g transform={`translate(${r1(cx - 12)} ${r1(cy - 12)})`} fill="none" stroke="var(--sc-paper)" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
        {kind === "ok" && <path d="M5 12.5l4.5 4.5L19 7.5" />}
        {kind === "error" && <path d="M12 6v7.5M12 18.2h.01" />}
        {kind === "warn" && <path d="M12 6.5V12l3.5 2M12 22a10 10 0 100-20 10 10 0 000 20z" strokeWidth={2.4} />}
        {kind === "copy" && <path d="M9 9h10v10H9zM5 15V5h10" strokeWidth={2.4} />}
        {kind === "missing" && <path d="M9 9a3 3 0 115 2.2c-1 .8-2 1.3-2 2.8M12 18h.01" />}
        {kind === "cancel" && <path d="M7 7l10 10M17 7L7 17" />}
      </g>
    </g>
  );
}

function Chips({ matched, searching }: { matched: ScannerProps["matched"]; searching: boolean }) {
  const ok = matched ? Math.min(Math.max(matched.lines - matched.toCheck, 0), 4) : 0;
  const check = matched ? Math.min(matched.toCheck, 3) : 0;
  const chips: ("ok" | "check" | "search")[] = searching && !matched ? ["search", "search", "search"] : [...Array<"ok">(ok).fill("ok"), ...Array<"check">(check).fill("check")];
  if (!chips.length) return null;
  const [cx, cy] = P(110, 80, 124);
  const gap = 40;
  const x0 = cx - ((chips.length - 1) * gap) / 2;
  return (
    <g>
      {chips.map((c, i) => {
        const x = x0 + i * gap;
        const y = cy + Math.abs(i - (chips.length - 1) / 2) * 6;
        const fill = c === "ok" ? "var(--sc-ok)" : c === "check" ? "var(--sc-warn)" : "var(--sc-line-strong)";
        return (
          <g key={`${c}-${i}`} className={c === "search" ? "sc-search" : "sc-pop"} style={{ animationDelay: `${i * 110}ms` }}>
            <rect x={r1(x - 16)} y={r1(y - 11)} width={32} height={22} rx={11} fill={fill} />
            <g transform={`translate(${r1(x - 6)} ${r1(y - 6)})`} fill="none" stroke="var(--sc-paper)" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
              {c === "ok" && <path d="M2 6.5l2.6 2.6L10 3.5" />}
              {c === "check" && <path d="M4 4a2.2 2.2 0 114 1.2c-.8.6-2 1-2 2.3M6 11h.01" />}
              {c === "search" && <path d="M5 9a4 4 0 100-8 4 4 0 000 8zM8 8l3 3" />}
            </g>
          </g>
        );
      })}
    </g>
  );
}

function UploadRing({ pct }: { pct: number | null }) {
  const [cx, cy] = P(110, 80, 150);
  const r = 24;
  const len = 2 * Math.PI * r;
  return (
    <g className="sc-pop">
      <circle cx={cx} cy={cy} r={r + 7} fill="var(--sc-paper)" opacity={0.92} />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--sc-ring-track)" strokeWidth={5} />
      {pct !== null && (
        <circle className="sc-ring" cx={cx} cy={cy} r={r} fill="none" stroke="var(--sc-beam)" strokeWidth={5} strokeLinecap="round" strokeDasharray={r1(len)} strokeDashoffset={r1(len * (1 - pct / 100))} transform={`rotate(-90 ${r1(cx)} ${r1(cy)})`} />
      )}
      <path d={`M${r1(cx)} ${r1(cy + 9)}V${r1(cy - 9)}M${r1(cx - 7)} ${r1(cy - 2)}l7 -7 7 7`} fill="none" stroke="var(--sc-ink)" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" />
    </g>
  );
}

/** Pointer tilt, only where motion is welcome. */
function useTilt() {
  const ref = useRef<HTMLDivElement>(null);
  const [ok, setOk] = useState(false);
  useEffect(() => {
    const mq = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    const set = () => setOk(!mq?.matches);
    set();
    mq?.addEventListener?.("change", set);
    return () => mq?.removeEventListener?.("change", set);
  }, []);
  const onPointerMove = (e: React.PointerEvent) => {
    const el = ref.current;
    if (!ok || !el) return;
    const b = el.getBoundingClientRect();
    const dx = (e.clientX - b.left) / b.width - 0.5;
    const dy = (e.clientY - b.top) / b.height - 0.5;
    el.style.setProperty("--sc-ry", `${(dx * 8).toFixed(2)}deg`);
    el.style.setProperty("--sc-rx", `${(-dy * 6).toFixed(2)}deg`);
  };
  const onPointerLeave = () => {
    ref.current?.style.setProperty("--sc-ry", "0deg");
    ref.current?.style.setProperty("--sc-rx", "0deg");
  };
  return { ref, onPointerMove, onPointerLeave, tilt: ok };
}

export function IsoScanner(p: ScannerProps) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const { ref, onPointerMove, onPointerLeave, tilt } = useTilt();
  const L = layout(p);
  const tone = ledTone(p);
  const hopperSheets = Math.min(L.hopper, STACK);
  const outSheets = Math.min(L.out, STACK);
  const bedPage = L.bed;
  const reading = p.scene === "reading";
  const lit: "none" | "beam" | "all" = reading ? "beam" : p.scene === "ready" || p.scene === "done" || p.scene === "matching" ? "all" : "none";
  const tint = p.scene === "failed" ? "var(--sc-error)" : p.scene === "timeout" ? "var(--sc-warn)" : undefined;
  const badge =
    p.scene === "ready" ? (p.duplicate ? "copy" : "ok")
    : p.scene === "done" ? "ok"
    : p.scene === "failed" ? "error"
    : p.scene === "timeout" ? "warn"
    : p.scene === "missing" ? "missing"
    : p.scene === "cancelled" ? "cancel"
    : p.duplicate && p.scene === "unread" ? "copy"
    : null;
  const [shadowX, shadowY] = P(W / 2 + 20, D / 2, 0);

  return (
    <div
      ref={ref}
      className={`sc-root ${tilt ? "sc-tilt" : ""} ${reading ? "sc-reading" : ""} ${p.scene === "retrieving" ? "sc-feeding" : ""}`}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      data-scene={p.scene}
      data-testid="iso-scanner"
    >
      <svg viewBox="0 40 540 400" {...(p.decorative ? { "aria-hidden": true, focusable: false } : { role: "img", "aria-label": p.label })} className="block h-auto w-full">
        <defs>
          <linearGradient id={`${uid}-top`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--sc-top-a)" />
            <stop offset="1" stopColor="var(--sc-top-b)" />
          </linearGradient>
          <linearGradient id={`${uid}-left`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="var(--sc-left-a)" />
            <stop offset="1" stopColor="var(--sc-left-b)" />
          </linearGradient>
          <linearGradient id={`${uid}-right`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="var(--sc-right-a)" />
            <stop offset="1" stopColor="var(--sc-right-b)" />
          </linearGradient>
          <linearGradient id={`${uid}-glass`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--sc-glass-a)" />
            <stop offset="0.55" stopColor="var(--sc-glass-b)" />
            <stop offset="1" stopColor="var(--sc-glass-c)" />
          </linearGradient>
          <linearGradient id={`${uid}-curtain`} x1="0" y1="1" x2="0" y2="0">
            <stop offset="0" stopColor="var(--sc-beam)" stopOpacity="0.55" />
            <stop offset="1" stopColor="var(--sc-beam)" stopOpacity="0" />
          </linearGradient>
          <radialGradient id={`${uid}-ground`}>
            <stop offset="0" stopColor="var(--sc-shadow)" stopOpacity="0.2" />
            <stop offset="1" stopColor="var(--sc-shadow)" stopOpacity="0" />
          </radialGradient>
          <filter id={`${uid}-soft`} x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="6" stdDeviation="6" floodColor="var(--sc-shadow)" floodOpacity="0.18" />
          </filter>
          <filter id={`${uid}-glow`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.4" />
          </filter>
          <clipPath id={`${uid}-reveal`}>
            <rect className="sc-reveal" width={PW} height={PH} />
          </clipPath>
        </defs>

        {/* ground shadow */}
        <ellipse cx={r1(shadowX)} cy={r1(shadowY + 6)} rx={250} ry={88} fill={`url(#${uid}-ground)`} />

        {/* hopper: riser, shelf, chute and waiting sheets */}
        <polygon points={wallY(-30, -10, 118, 0, 66)} fill="var(--sc-riser-left)" />
        <polygon points={wallX(-10, 44, 118, 0, 66)} fill="var(--sc-riser-right)" />
        <polygon points={flatRect(-112, 36, -12, 124, 74)} fill={`url(#${uid}-top)`} stroke="var(--sc-edge)" strokeWidth={0.8} />
        <polygon points={wallY(-112, -12, 124, 68, 74)} fill="var(--sc-shelf-left)" />
        <polygon points={wallX(-12, 36, 124, 68, 74)} fill="var(--sc-shelf-right)" />
        <polygon points={pts([-12, 36, 74], [4, 36, H], [4, 124, H], [-12, 124, 74])} fill="var(--sc-chute)" stroke="var(--sc-edge)" strokeWidth={0.8} />
        {Array.from({ length: hopperSheets }, (_, i) => {
          const top = i === hopperSheets - 1;
          return (
            <g key={top ? `hop-top-${L.hopper}` : `hop-${i}`} className={top ? "sc-hopper-top" : undefined}>
              <g transform={flat(...hop(i))}>
                <Page uid={uid} lit="none" />
              </g>
            </g>
          );
        })}
        {p.scene === "missing" && (
          <g transform={flat(...hop(0))}>
            <Page uid={uid} lit="none" dashed />
          </g>
        )}

        {/* body */}
        <polygon points={wallY(0, W, D, 0, H)} fill={`url(#${uid}-left)`} />
        <polygon points={wallX(W, 0, D, 0, H)} fill={`url(#${uid}-right)`} />
        <polygon points={wallY(0, W, D, 0, 6)} fill="var(--sc-band-left)" opacity={0.55} />
        <polygon points={wallX(W, 0, D, 0, 6)} fill="var(--sc-band-right)" opacity={0.55} />
        <polygon points={flatRect(0, 0, W, D, H)} fill={`url(#${uid}-top)`} />
        <polyline points={pts([0, D, H], [W, D, H], [W, 0, H])} fill="none" stroke="var(--sc-paper)" strokeOpacity={0.9} strokeWidth={1.6} strokeLinejoin="round" />
        {/* glass */}
        <polygon points={flatRect(22, 20, 198, 140, H + 0.2)} fill="var(--sc-bezel)" />
        <polygon points={flatRect(26, 24, 194, 136, H + 0.4)} fill={`url(#${uid}-glass)`} />
        <polygon points={flatRect(26, 24, 194, 52, H + 0.5)} fill="var(--sc-paper)" opacity={0.06} />
        {/* control panel on the front-left face */}
        <g transform={onWallY(18, D, 30)}>
          <ellipse className={`sc-led ${p.scene === "connecting" || p.scene === "retrieving" || p.scene === "queued" ? "sc-led-pulse" : ""}`} cx={6} cy={2} rx={4.4} ry={4.4} fill={LED[tone]} />
          <ellipse cx={6} cy={2} rx={8} ry={8} fill={LED[tone]} opacity={0.18} />
          <text x={18} y={6} fontSize={9.5} fontWeight={600} letterSpacing={1.6} fill="var(--sc-label)">
            PRO TRADING
          </text>
        </g>
        <polygon points={wallY(124, 204, D, 14, 38)} fill="var(--sc-panel)" />
        <polygon points={wallY(126, 202, D, 16, 36)} fill="var(--sc-screen)" />
        <g transform={onWallY(132, D, 21)}>
          <text className="sc-display" fontSize={12.5} fontWeight={600} letterSpacing={1} fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace" fill={tone === "error" ? "var(--sc-display-error)" : tone === "warn" ? "var(--sc-display-warn)" : tone === "ok" ? "var(--sc-display-ok)" : "var(--sc-display)"}>
            {display(p)}
          </text>
        </g>
        {/* output slot and tray on the front-right face */}
        <polygon points={wallX(W, 34, 126, 17, 23)} fill="var(--sc-panel)" />
        <polygon points={flatRect(W, 36, W + 102, 124, 16)} fill="var(--sc-tray)" stroke="var(--sc-edge)" strokeWidth={0.8} />
        <polygon points={wallY(W, W + 102, 124, 11, 16)} fill="var(--sc-left-b)" />
        <polygon points={wallX(W + 102, 36, 124, 11, 16)} fill="var(--sc-tray-right)" />
        {Array.from({ length: outSheets }, (_, i) => {
          const top = i === outSheets - 1;
          const from = shift(BED, out(i));
          return (
            <g key={top ? `out-top-${L.out}` : `out-${i}`} className={top && p.scene !== "ready" && p.scene !== "done" ? "sc-move" : undefined} style={top ? from : undefined}>
              <g transform={flat(...out(i))}>
                <Page uid={uid} lit={p.scene === "ready" || p.scene === "done" || p.scene === "matching" ? "all" : "none"} />
              </g>
            </g>
          );
        })}

        {/* the page on the glass */}
        {bedPage !== null && (
          <g key={`bed-${bedPage}-${p.scene === "uploading" ? "up" : "rd"}`} className={p.scene === "reading" || p.scene === "queued" ? "sc-move" : undefined} style={shift(hop(Math.max(hopperSheets, 0)), BED)}>
            <g transform={flat(...BED)}>
              <Page uid={uid} lit={lit} tint={tint} />
              {p.duplicate && <rect x={-5} y={-6} width={PW} height={PH} rx={2} fill="none" stroke="var(--sc-line-strong)" strokeWidth={0.9} strokeDasharray="3 2" />}
              {reading && (
                <g className="sc-beam-line">
                  <rect x={-3} y={-6} width={6} height={PH + 12} fill="var(--sc-beam-soft)" opacity={0.55} filter={`url(#${uid}-glow)`} />
                  <rect x={-1} y={-6} width={2} height={PH + 12} rx={1} fill="var(--sc-beam)" />
                </g>
              )}
            </g>
          </g>
        )}
        {reading && (
          <g className="sc-beam-curtain">
            <g transform={onWallX(...BED)}>
              <rect x={-6} y={0} width={PH + 12} height={64} fill={`url(#${uid}-curtain)`} />
            </g>
          </g>
        )}
        {p.scene === "queued" && !p.retrying && (
          <g transform={flat(...BED)}>
            <rect x={-1} y={-6} width={2} height={PH + 12} rx={1} fill="var(--sc-beam)" opacity={0.35} />
          </g>
        )}

        {p.scene === "uploading" && <UploadRing pct={p.uploadPct} />}
        {(p.scene === "matching" || p.scene === "ready") && <Chips matched={p.matched} searching={p.scene === "matching"} />}
        {badge && <Badge kind={badge} uid={uid} />}
        {p.retrying && <Badge kind="warn" uid={uid} />}
      </svg>
    </div>
  );
}
