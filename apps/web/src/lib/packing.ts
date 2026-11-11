import { packingSheetPdfUrl, type PickLine } from "../api";
import { dec, toFixed } from "./decimal";
import { qtyText } from "./inventory";

/** Copies in every print of a packing sheet (owner, 2026-11-11: "2 prints by default"). */
export const PACKING_COPIES = 2;

/** Whole units add up in the Total row; lb/kg lines are listed beside it ("+ 12.5 LB by weight"),
 *  the same rule as the printed PDF (apps/api/src/pro_trading/packing_pdf.py). */
const COUNTED = new Set(["BOX", "PIECE"]);

export function packingTotal(lines: Pick<PickLine, "unit_code" | "requested_qty">[]): { qty: string; byWeight: string } {
  let count = 0n;
  const weights = new Map<string, bigint>();
  for (const l of lines) {
    const q = dec(l.requested_qty);
    if (COUNTED.has(l.unit_code)) count += q;
    else weights.set(l.unit_code, (weights.get(l.unit_code) ?? 0n) + q);
  }
  const extra = [...weights.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([u, v]) => `${qtyText(toFixed(v, 4))} ${u}`)
    .join(" + ");
  return { qty: qtyText(toFixed(count, 4)), byWeight: extra ? `+ ${extra} by weight` : "" };
}

/** 2026-11-09 -> 09-11-2026, the date format of the office's paper forms and invoices. */
export function sheetDate(iso: string): string {
  const [y, m, d] = (iso ?? "").split("-");
  return y && m && d ? `${d}-${m}-${y}` : iso;
}

/**
 * Print the packing sheet PDF: every page, PACKING_COPIES copies, without the app around it.
 * Chrome prints a same-origin PDF from a hidden frame; where a browser can't (or the frame
 * throws), the PDF opens in a new tab to print from there. A web page can't set the print
 * dialog's copy count, so the PDF itself holds the copies.
 */
export async function printPackingSheet(pickId: string): Promise<"printed" | "opened"> {
  const url = packingSheetPdfUrl(pickId, PACKING_COPIES);
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok || !(res.headers.get("content-type") ?? "").includes("application/pdf")) throw new Error(`PDF ${res.status}`);
  const blob = URL.createObjectURL(await res.blob());
  const old = document.getElementById("packing-print-frame") as HTMLIFrameElement | null;
  if (old?.dataset.blob) URL.revokeObjectURL(old.dataset.blob);
  old?.remove();
  const frame = document.createElement("iframe");
  frame.id = "packing-print-frame";
  frame.title = "Packing sheet to print";
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  Object.assign(frame.style, { position: "fixed", right: "0", bottom: "0", width: "0", height: "0", border: "0" });
  frame.dataset.src = url;
  frame.dataset.blob = blob;
  return new Promise((resolve) => {
    // A browser that downloads the PDF instead of showing it never fires load: don't hang.
    const giveUp = window.setTimeout(() => resolve("opened"), 15_000);
    frame.onload = () => {
      window.clearTimeout(giveUp);
      try {
        frame.contentWindow?.focus();
        frame.contentWindow?.print();
        resolve("printed");
      } catch {
        window.open(blob, "_blank", "noopener");
        resolve("opened");
      }
    };
    frame.src = blob;
    document.body.appendChild(frame);
  });
}
