// SAMPLE DATA for design-preview screens only (task 00b). Synthetic, explicitly labelled,
// never sent to the API. Replace with real API bindings in Modules 01+.
import type { Tone } from "../ui/primitives";

export type OrderStatus = "NEW" | "PICKING" | "PARTIAL" | "DISPATCHED" | "INVOICED" | "CANCELLED";
export const ORDER_STATUS: Record<OrderStatus, { label: string; tone: Tone }> = {
  NEW: { label: "New", tone: "info" },
  PICKING: { label: "Picking", tone: "warning" },
  PARTIAL: { label: "Backorder", tone: "danger" },
  DISPATCHED: { label: "Dispatched", tone: "success" },
  INVOICED: { label: "Invoiced", tone: "neutral" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

export type SampleOrder = {
  id: string;
  customer: string;
  date: string;
  ordered: string;
  picked: string;
  dispatched: string;
  backordered: string;
  status: OrderStatus;
  source: "Scan" | "Manual" | "Email";
  lines: { product: string; qty: string; unit: string }[];
};

const C = ["Sample Grocer North", "Demo Market Co.", "Example Foods Ltd.", "Test Halal Butcher", "Placeholder Supermart", "Fictional Fresh Inc."];
const P = ["Yam (Ghana) 50 lb", "Plantain green", "Scotch bonnet pepper", "Cassava root", "Taro (eddoe)", "Okra", "Ginger root", "Thyme bunch"];
const STATUSES: OrderStatus[] = ["NEW", "PICKING", "PARTIAL", "DISPATCHED", "INVOICED", "DISPATCHED", "NEW", "PICKING", "INVOICED", "CANCELLED"];

export const SAMPLE_ORDERS: SampleOrder[] = Array.from({ length: 24 }, (_, i) => {
  const status = STATUSES[i % STATUSES.length] ?? "NEW";
  const ordered = 20 + ((i * 7) % 45);
  const picked = status === "NEW" ? 0 : status === "PICKING" ? Math.floor(ordered / 2) : status === "PARTIAL" ? ordered - 4 : status === "CANCELLED" ? 0 : ordered;
  const dispatched = status === "DISPATCHED" || status === "INVOICED" ? picked : status === "PARTIAL" ? picked : 0;
  const back = status === "PARTIAL" ? 4 : 0;
  return {
    id: `SO-DEMO-${String(1040 - i).padStart(4, "0")}`,
    customer: C[i % C.length] ?? "Sample customer",
    date: `2026-11-${String(7 - Math.floor(i / 4)).padStart(2, "0")}`,
    ordered: String(ordered),
    picked: String(picked),
    dispatched: String(dispatched),
    backordered: String(back),
    status,
    source: (["Scan", "Manual", "Email"] as const)[i % 3] ?? "Manual",
    lines: [0, 1, 2].map((j) => ({ product: P[(i + j * 3) % P.length] ?? "Sample product", qty: String(4 + ((i + j) % 9)), unit: j === 1 ? "case" : "box" })),
  };
});

export type InvoiceLine = { id: string; product: string; unit: "box" | "case" | "bunch"; boxes: string; weight: string; weightUnit: "lb" | "kg"; rate: string; rateBasis: "per lb" | "per kg" | "per unit"; taxCode: "ZR" | "GST" | "HST" };

// Same product repeated with different box weights — required by the invoice blueprint.
export const SAMPLE_INVOICE_LINES: InvoiceLine[] = [
  { id: "l1", product: "Yam (Ghana) 50 lb", unit: "box", boxes: "1", weight: "48.20", weightUnit: "lb", rate: "1.15", rateBasis: "per lb", taxCode: "ZR" },
  { id: "l2", product: "Yam (Ghana) 50 lb", unit: "box", boxes: "1", weight: "51.40", weightUnit: "lb", rate: "1.15", rateBasis: "per lb", taxCode: "ZR" },
  { id: "l3", product: "Yam (Ghana) 50 lb", unit: "box", boxes: "1", weight: "49.75", weightUnit: "lb", rate: "1.15", rateBasis: "per lb", taxCode: "ZR" },
  { id: "l4", product: "Plantain green", unit: "case", boxes: "6", weight: "", weightUnit: "lb", rate: "24.50", rateBasis: "per unit", taxCode: "ZR" },
  { id: "l5", product: "Scotch bonnet pepper", unit: "box", boxes: "2", weight: "11.30", weightUnit: "kg", rate: "6.80", rateBasis: "per kg", taxCode: "ZR" },
];

export type ReceiptLine = { id: string; product: string; batch: string; declared: string; received: string; damaged: string; unit: string; cost: string; confidence: number };

export const SAMPLE_RECEIPT = {
  vendor: "Sample Exporters Ltd. (synthetic)",
  shipment: "SHP-DEMO-0192",
  billNo: "BL-DEMO-55021",
  waybill: "AWB-000-DEMO-7781",
  date: "2026-11-06",
  currency: "CAD",
  documents: ["Bill of lading (page 1/2)", "Bill of lading (page 2/2)", "Packing list", "Commercial invoice"],
  lines: [
    { id: "r1", product: "Yam (Ghana) 50 lb", batch: "GH-2611-A", declared: "120", received: "118", damaged: "2", unit: "box", cost: "38.40", confidence: 97 },
    { id: "r2", product: "Cassava root", batch: "GH-2611-B", declared: "80", received: "80", damaged: "0", unit: "box", cost: "22.10", confidence: 91 },
    { id: "r3", product: "Taro (eddoe)", batch: "GH-2611-C", declared: "60", received: "57", damaged: "1", unit: "box", cost: "27.75", confidence: 64 },
    { id: "r4", product: "Ginger root", batch: "GH-2611-D", declared: "40", received: "40", damaged: "0", unit: "box", cost: "31.00", confidence: 88 },
  ] satisfies ReceiptLine[],
};

export const SAMPLE_ACTIVITY = [
  { at: "09:42", text: "SO-DEMO-1040 scanned from order sheet", tone: "info" as Tone },
  { at: "09:15", text: "GRN for SHP-DEMO-0192 awaiting approval", tone: "warning" as Tone },
  { at: "08:58", text: "Taro (eddoe) below reorder level", tone: "danger" as Tone },
  { at: "08:31", text: "INV-DEMO-2210 emailed to Demo Market Co.", tone: "success" as Tone },
];

// Weekly net sales (sample) for the compact chart, in CAD.
export const SAMPLE_WEEK = [
  { d: "Mon", v: "18420.50" },
  { d: "Tue", v: "21310.00" },
  { d: "Wed", v: "16980.25" },
  { d: "Thu", v: "24105.75" },
  { d: "Fri", v: "27640.00" },
  { d: "Sat", v: "12210.40" },
  { d: "Sun", v: "4390.00" },
];
