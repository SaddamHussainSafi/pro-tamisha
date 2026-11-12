// Thin fetch wrapper: cookie session + CSRF header + idempotency keys on mutations.

export type Grant = { permission: string; warehouse_id: string | null };
export type Session = {
  employee: { id: string; email: string; display_name: string; company_id: string };
  /** This sign-in still needs its two-step code (or, when the company requires it, setup). */
  mfa_required: boolean;
  mfa_enrolled: boolean;
  mfa_setup_required?: boolean;
  roles?: string[];
  permissions: string[];
  grants: Grant[];
  expires_at: string;
};
export type Warehouse = { id: string; code: string; name: string; active: boolean; version: number };

export type UnitCode = "LB" | "KG" | "BOX" | "PIECE";
export type Category = { id: string; code: string; name: string; active: boolean };
export type UnitProfile = {
  id: string;
  code: string;
  unit_code: UnitCode;
  pieces_per_unit: string | null;
  weight_lb_per_unit: string | null;
  is_default: boolean;
  active: boolean;
};
/** A unit profile listed across products (Products › Case sizes). */
export type CaseSize = UnitProfile & { product_id: string; sku: string; product_name: string; product_active: boolean };
export type Alias = { id: string; alias: string; source_label: string };
export type ProductSummary = {
  id: string;
  sku: string;
  name: string;
  category_id: string | null;
  stock_unit: UnitCode;
  tracks_expiry: boolean;
  active: boolean;
  version: number;
};
export type Product = ProductSummary & {
  created_at: string;
  updated_at: string;
  unit_profiles: UnitProfile[];
  aliases: Alias[];
};
export type UnitProfileIn = {
  code: string;
  unit_code: UnitCode;
  pieces_per_unit?: string | null;
  weight_lb_per_unit?: string | null;
  is_default?: boolean;
};
export type ProductIn = {
  sku: string;
  name: string;
  category_id: string | null;
  stock_unit: UnitCode;
  tracks_expiry: boolean;
  unit_profiles: UnitProfileIn[];
  aliases: { alias: string; source_label?: string }[];
};
export type WeightGroupIn = { profile_id: string; count: string; case_weight_lb?: string | null };
export type WeightCalc = {
  groups: {
    profile_id: string;
    count: string;
    unit_code: UnitCode;
    weight_lb_per_unit: string;
    weight_lb: string;
    weight_source: "ENTERED" | "PROFILE_DEFAULT" | "BASE_UNIT";
  }[];
  units: Record<string, string>;
  total_lb: string;
  total_kg: string;
  estimated: boolean;
};

export type PriceScope = "STANDARD" | "GROUP" | "CUSTOMER";
export type PriceState = "DRAFT" | "SCHEDULED" | "CURRENT" | "SUPERSEDED" | "ARCHIVED";
export type PriceVersion = {
  id: string;
  version_code: string;
  status: "DRAFT" | "ACTIVE" | "ARCHIVED";
  state: PriceState;
  notes: string;
  effective_at: string | null;
  source_hash: string | null;
  based_on_id: string | null;
  stale: boolean;
  current_version_id: string | null;
  approved_at: string | null;
  entry_count: number;
  version: number;
  created_at: string;
};
export type PriceEntry = {
  id: string;
  product_id: string;
  sku: string;
  product_name: string;
  unit_code: UnitCode;
  scope: PriceScope;
  customer_id: string | null;
  group_id: string | null;
  target: string | null;
  rate: string;
  currency: string;
};
export type DiffRow = {
  product_id: string;
  sku: string;
  product_name: string;
  unit_code: UnitCode;
  scope: PriceScope;
  target: string | null;
  old_rate: string | null;
  new_rate: string | null;
  change: "ADDED" | "CHANGED" | "REMOVED";
};
export type PriceDiff = {
  against_version_id: string | null;
  added: number;
  changed: number;
  removed: number;
  unchanged: number;
  rows: DiffRow[];
  truncated: boolean;
};
export type RowError = { row: number; field: string | null; code: string; message: string };
export type PricePreview = {
  valid: boolean;
  entry_count: number;
  source_hash: string | null;
  duplicate_of: string | null;
  errors: RowError[];
  diff: PriceDiff | null;
};
export type PriceQuote = {
  rate: string;
  currency: string;
  scope: PriceScope;
  version_code: string;
  effective_at: string;
  unit_code: UnitCode;
};
export type CustomerGroup = { id: string; code: string; name: string; active: boolean };
/** Printed on invoices: bill-to / ship-to (blank ship-to = same as bill-to), fax, vendor no. */
export type CustomerInvoiceFields = {
  fax: string | null;
  bill_to_address: string | null;
  ship_to_name: string | null;
  ship_to_address: string | null;
  vendor_no: string | null;
};
export type CustomerIn = Partial<CustomerInvoiceFields> & {
  external_ref?: string | null;
  name: string;
  email?: string | null;
  phone?: string | null;
  group_id?: string | null;
  payment_terms_days: number;
};
/** Omitted = unchanged; "" text (email, phone, fax, address, vendor no.) = clear; clear_group removes the group. */
export type CustomerPatch = {
  version: number;
  name?: string;
  email?: string;
  phone?: string;
  fax?: string;
  bill_to_address?: string;
  ship_to_name?: string;
  ship_to_address?: string;
  vendor_no?: string;
  group_id?: string;
  clear_group?: boolean;
  payment_terms_days?: number;
  active?: boolean;
};
export type Customer = CustomerInvoiceFields & {
  id: string;
  external_ref: string | null;
  name: string;
  email: string | null;
  phone: string | null;
  group_id: string | null;
  payment_terms_days: number;
  active: boolean;
  version: number;
};
export type PriceEntryIn = {
  product_id: string;
  unit_code: UnitCode;
  scope: PriceScope;
  customer_id?: string | null;
  group_id?: string | null;
  rate: string;
  version: number;
};

export type LotStatus = "SALEABLE" | "DAMAGED" | "QUARANTINE" | "EXPIRED";
export type MovementType =
  | "OPENING_BALANCE"
  | "GRN_RECEIPT"
  | "INVOICE_DISPATCH"
  | "ADJUSTMENT"
  | "COUNT_ADJUSTMENT"
  | "DAMAGE"
  | "RETURN"
  | "REVERSAL";
export type AdjustReason = "FOUND" | "LOST" | "SPOILAGE" | "DATA_ENTRY" | "OTHER";
export type Balance = {
  lot_id: string;
  lot_code: string;
  lot_status: LotStatus;
  expired: boolean;
  batch_ref: string | null;
  received_at: string | null;
  expires_at: string | null;
  product_id: string;
  sku: string;
  product_name: string;
  warehouse_id: string;
  warehouse_code: string;
  unit_code: UnitCode;
  qty: string;
  reserved_qty: string;
  available_qty: string;
  version: number;
};
export type Movement = {
  id: string;
  lot_id: string;
  lot_code: string;
  product_id: string;
  sku: string;
  warehouse_id: string;
  warehouse_code: string;
  unit_code: UnitCode;
  delta_qty: string;
  qty_after: string;
  movement_type: MovementType;
  reason_code: string | null;
  note: string;
  source_type: string;
  source_id: string;
  reverses_movement_id: string | null;
  reversed_by_id: string | null;
  actor_name: string | null;
  created_at: string;
};
export type Posting = { source_type: string; source_id: string; lot_id: string | null; movements: Movement[] };
export type Lot = {
  id: string;
  lot_code: string;
  status: LotStatus;
  expired: boolean;
  product_id: string;
  sku: string;
  product_name: string;
  warehouse_id: string;
  warehouse_code: string;
  batch_ref: string | null;
  received_at: string | null;
  expires_at: string | null;
  parent_lot_id: string | null;
  damaged_lot_id: string | null;
  landed_unit_cost: string | null;
  cost_currency: string | null;
  version: number;
  balances: Balance[];
};
export type CountLine = {
  lot_id: string;
  lot_code: string;
  sku: string;
  unit_code: UnitCode;
  expected_qty: string;
  counted_qty: string;
  variance: string;
  current_qty: string | null;
  stale: boolean;
  movement_id: string | null;
};
export type StockCount = {
  id: string;
  count_no: string;
  warehouse_id: string;
  warehouse_code: string;
  status: "SUBMITTED" | "POSTED" | "REJECTED";
  note: string;
  counted_by_name: string | null;
  decided_by_name: string | null;
  created_at: string;
  decided_at: string | null;
  version: number;
  lines: CountLine[];
};
export type Discrepancy = {
  lot_id: string;
  lot_code: string;
  unit_code: UnitCode;
  cached_qty: string | null;
  ledger_qty: string;
  cached_reserved: string | null;
  active_reserved: string;
};
export type Reconciliation = { checked: number; discrepancies: Discrepancy[]; ledger_rows: number; rebuilt: boolean };
export type Page<T> = { items: T[]; next_cursor: string | null; total: number };

export type DocFileKind = "INVOICE_PDF" | "POD" | "VENDOR_DOC" | "SCAN";
export type DocSyncStatus = "PENDING" | "SYNCED" | "FAILED" | "LOCAL_ONLY";
export type DocumentFile = {
  id: string;
  warehouse_id: string;
  kind: DocFileKind;
  folder: string;
  filename: string;
  path: string;
  mime_type: string;
  sha256: string;
  byte_size: number;
  sync_status: DocSyncStatus;
  drive_file_id: string | null;
  sync_attempts: number;
  last_error: string | null;
  synced_at: string | null;
  note: string | null;
  invoice_id: string | null;
  shipment_id: string | null;
  scan_job_id: string | null;
  invoice_no: string | null;
  shipment_no: string | null;
  scan_job_no: string | null;
  customer_name: string | null;
  vendor_name: string | null;
  created_at: string;
  version: number;
};
export type DocumentPage = Page<DocumentFile> & { counts: Partial<Record<DocSyncStatus, number>>; drive_provider: string };

const qs = (q: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  return p.toString();
};

export type OrderStatus =
  | "DRAFT"
  | "CONFIRMED"
  | "PICKING"
  | "PARTIALLY_FULFILLED"
  | "FULFILLED"
  | "CLOSED_WITH_SHORTAGE"
  | "CANCELLED";
export type PickStatus = "CREATED" | "IN_PROGRESS" | "SUBMITTED" | "NEEDS_CORRECTION" | "VERIFIED" | "INVOICED" | "ABORTED";
export type ShortageReason = "OUT_OF_STOCK" | "DAMAGED" | "NOT_FOUND" | "OTHER";
export type Disposition = "BACKORDER" | "CANCEL";
export type OrderLine = {
  id: string;
  line_no: number;
  product_id: string;
  sku: string;
  product_name: string;
  unit_code: UnitCode;
  ordered_qty: string;
  cancelled_qty: string;
  dispatched_qty: string;
  awaiting_invoice_qty: string;
  remaining_qty: string;
  pending_qty: string;
  backorder: boolean;
  source_line_text: string;
  rate: string | null;
  price_scope: PriceScope | null;
  price_version_code: string | null;
  note: string;
  /** Pounds the whole line weighs (boxes), e.g. the sheet's WEIGHT. */
  weight_lb?: string | null;
  lb_rate?: string | null;
};
export type PickSummary = {
  id: string;
  order_id: string;
  order_no: string;
  customer_name: string;
  warehouse_code: string;
  attempt_no: number;
  pick_code: string;
  status: PickStatus;
  line_count: number;
  created_at: string;
  verified_at: string | null;
};
export type Amendment = {
  id: string;
  line_no: number | null;
  kind: string;
  qty: string | null;
  reason: string;
  actor_name: string | null;
  created_at: string;
};
export type Order = {
  id: string;
  order_no: string;
  customer_id: string;
  customer_name: string;
  customer_ref: string | null;
  warehouse_id: string;
  warehouse_code: string;
  status: OrderStatus;
  source: "MANUAL" | "SCAN" | "WEB";
  source_text: string;
  source_snapshot: Record<string, string>[];
  requested_for: string | null;
  note: string;
  created_by_name: string | null;
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  close_reason: string | null;
  has_backorder: boolean;
  open_attempt_id: string | null;
  version: number;
  created_at: string;
  lines: OrderLine[];
  attempts: PickSummary[];
  amendments: Amendment[];
};
export type OrderSummary = {
  id: string;
  order_no: string;
  customer_name: string;
  warehouse_code: string;
  status: OrderStatus;
  line_count: number;
  requested_for: string | null;
  created_at: string;
};
export type OrderLineIn = { product_id: string; unit_code: UnitCode; qty: string; source_line_text?: string; note?: string; weight_lb?: string | null };
export type OrderIn = {
  customer_id: string;
  warehouse_id: string;
  source_text?: string;
  requested_for?: string | null;
  note?: string;
  lines: OrderLineIn[];
};
export type PickLine = {
  id: string;
  order_line_id: string;
  line_no: number;
  sku: string;
  product_name: string;
  unit_code: UnitCode;
  requested_qty: string;
  picked_qty: string | null;
  outcome: "PICKED" | "SHORT" | "NO" | null;
  shortage_reason: ShortageReason | null;
  disposition: Disposition | null;
  lot_id: string | null;
  lot_code: string | null;
  weight_lb: string | null;
  note: string;
  /** Packing sheet UNITS column: CASE-40, PIECE, LB… (the invoice's case-size rule). */
  units: string;
  /** Packing sheet REMARKS: the order line's note, then the picker's. */
  remarks: string;
};
export type Pick = Omit<PickSummary, "line_count" | "verified_at"> & {
  /** The order's date (YYYY-MM-DD), printed in the sheet's Date box. */
  order_date: string;
  warehouse_id: string;
  status_note: string;
  created_by_name: string | null;
  picked_by_name: string | null;
  verified_by_name: string | null;
  submitted_at: string | null;
  verified_at: string | null;
  version: number;
  lines: PickLine[];
};
export type PickLineIn = { line_id: string; picked_qty: string; shortage_reason?: ShortageReason | null; note?: string };

export type InvoiceState = "ISSUED" | "PARTIALLY_PAID" | "PAID" | "VOIDED" | "CREDITED";
export type EmailStatus = "NO_EMAIL" | "QUEUED" | "SENT" | "FAILED";
export type WeightRow = { cases: number; weight_lb: string };
export type InvoiceDraftLine = {
  pick_line_id: string;
  order_line_id: string;
  product_id: string;
  sku: string;
  description: string;
  unit_code: UnitCode;
  cases: string | null;
  case_weight_lb: string | null;
  weight_source: "ENTERED" | null;
  billed_qty: string;
  unit_rate: string;
  line_amount: string;
  tax_code: string;
  tax_rate: string;
  tax_amount: string;
  price_version_code: string | null;
  price_scope: string | null;
  /** lb per unit printed on the invoice (case size); absent before the D-12 template. */
  pack_lb?: string | null;
};
export type StockCheck = { pick_line_id: string; sku: string; unit_code: UnitCode; deduct_qty: string; available_qty: string; ok: boolean };
export type InvoicePreview = {
  pick_id: string;
  pick_code: string;
  pick_version: number;
  order_id: string;
  order_no: string;
  customer_name: string;
  email_to: string | null;
  payment_terms_days: number;
  currency: "CAD";
  lines: InvoiceDraftLine[];
  stock: StockCheck[];
  weighable_pick_line_ids: string[];
  subtotal: string;
  tax_amount: string;
  total: string;
  tax_note: string;
  can_issue: boolean;
};
export type InvoiceSummary = {
  id: string;
  invoice_no: string;
  state: InvoiceState;
  customer_id: string;
  customer_name: string;
  customer_ref: string | null;
  sales_order_id: string;
  order_no: string;
  pick_code: string;
  warehouse_code: string;
  issued_at: string;
  due_at: string;
  total: string;
  paid_amount: string;
  balance: string;
  currency: "CAD";
  email_status: EmailStatus;
};
export type Invoice = InvoiceSummary & {
  warehouse_id: string;
  picking_attempt_id: string;
  subtotal: string;
  tax_amount: string;
  tax_note: string;
  issued_by_name: string | null;
  pdf_sha256: string;
  template_version: string;
  email_to: string | null;
  email_sent_at: string | null;
  email_error: string | null;
  email_sends: number;
  version: number;
  lines: (InvoiceDraftLine & { id: string; line_no: number })[];
  movements: { id: string; lot_id: string; lot_code: string; sku: string; unit_code: UnitCode; delta_qty: string; qty_after: string }[];
};

export type ScanJobType = "SALES_ORDER" | "PICK_SHEET" | "VENDOR_DOCUMENT" | "PURCHASE_ORDER" | "DELIVERY_NOTE" | "PACKING_LIST" | "RECEIPT" | "OTHER";
export type ScanJobStatus = "REQUESTED" | "SCANNING" | "UPLOADED" | "FAILED" | "CANCELLED" | "EXPIRED";
export type ScanDeviceStatus = "PENDING" | "ACTIVE" | "REVOKED";
export type ScannerState = "READY" | "NOT_FOUND" | "DRIVER_MISSING" | "BUSY" | "ERROR" | "UNKNOWN";
export type ScanDevice = {
  id: string;
  warehouse_id: string;
  warehouse_code: string;
  name: string;
  status: ScanDeviceStatus;
  paired: boolean;
  pairing_expires_at: string | null;
  online: boolean;
  last_seen_at: string | null;
  scanner_state: ScannerState;
  scanner_detail: string | null;
  bridge_version: string | null;
  discover_requested_at: string | null;
  detected_scanners: { name: string; driver: string }[] | null;
  detected_at: string | null;
  approved_by_name: string | null;
  approved_at: string | null;
  revoked_reason: string | null;
  version: number;
};
export type ScanJob = {
  id: string;
  job_no: string;
  warehouse_id: string;
  warehouse_code: string;
  job_type: ScanJobType;
  channel: "BRIDGE" | "MANUAL";
  status: ScanJobStatus;
  device_id: string | null;
  device_name: string | null;
  note: string | null;
  requested_by_name: string;
  created_at: string;
  claimed_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_detail: string | null;
  document_id: string | null;
  document_sha256: string | null;
  document_mime: string | null;
  document_size: number | null;
  document_filename: string | null;
  duplicate: boolean;
  /** Only on single-scan answers (upload, detail); lists leave it out. */
  page_count?: number | null;
  version: number;
};
export type OcrStatus = "QUEUED" | "NEEDS_REVIEW" | "FAILED" | "ACCEPTED" | "REJECTED" | "SUPERSEDED";
export type OcrFieldStatus = "OK" | "LOW_CONFIDENCE" | "UNREADABLE" | "UNMAPPED";
export type OcrResolution = "NONE" | "CONFIRMED" | "CORRECTED" | "REMOVED";
export type OcrCorrection = {
  id: string;
  action: "CONFIRM" | "CORRECT" | "REMOVE" | "ADD";
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  reason: string | null;
  actor_name: string;
  created_at: string;
};
export type OcrField = {
  read_unit_price?: string | null;
  read_amount?: string | null;
  id: string;
  seq: number;
  kind: "LINE" | "EXTRA" | "MANUAL";
  label: string;
  raw_text: string;
  read_product_id: string | null;
  read_sku: string | null;
  read_unit_code: UnitCode | null;
  read_qty: string | null;
  confidence: string | null;
  status: OcrFieldStatus;
  bbox: Record<string, unknown> | null;
  /** Kept apart from the quantity by the handwriting reader (package, weight, notes, NO, ...). */
  detail?: OcrLineDetail | null;
  resolution: OcrResolution;
  final_product_id: string | null;
  final_sku: string | null;
  final_product_name: string | null;
  final_unit_code: UnitCode | null;
  final_qty: string | null;
  resolved_by_name: string | null;
  resolved_at: string | null;
  corrections: OcrCorrection[];
};
export type OcrLineDetail = {
  pkg?: string | null;
  weight?: string | null;
  case_size?: string | null;
  notes?: string | null;
  crossed_out?: string | null;
  no_mark?: boolean;
  checkmark?: boolean;
  supplied?: string | null;
  unavailable?: string | null;
  flags?: string[];
  engine?: string;
  page?: number;
};
/** Which engine really read the scan and what it cost. Never holds a key. */
export type OcrEngine = {
  provider?: string;
  model?: string;
  requests?: number;
  total_tokens?: number;
  seconds?: number;
  fallback?: string | null;
  doc_type?: string | null;
  mode?: string;
  pages?: { page: number; engine: string; seconds: number; error: string | null; doc_type: string | null; rows: number; raw?: unknown }[];
};
export type OcrEngineStatus = {
  provider: string;
  fallback_provider: string;
  model: string;
  key_set: boolean;
  data_consent: boolean;
  target_seconds: number;
  requests_per_minute: number;
  daily_request_cap: number;
  used_today: number;
  remaining_today: number;
  used_this_minute: number;
  breaker_open_seconds: number;
  quota_hit_today: boolean;
  recent_calls: { at: string; model: string; stage: string; page: number | null; outcome: string; http_status: number | null; total_tokens: number; seconds: string | null }[];
  accuracy: { engine: string; samples: number; correct: number; accuracy: string | null }[];
};
export type OcrEngineReading = {
  extraction_id: string;
  status: OcrStatus;
  provider: string;
  page_count: number | null;
  warnings: string[];
  engine: OcrEngine;
  lines: number;
  uncertain: number;
  no_marks: number;
  crossed_out: number;
  corrections: number;
  validation_errors: string[];
  tesseract_lines: { page: number | null; label: string; status: string; qty: string | null; raw: string }[];
};
export type OcrExtraction = {
  engine?: OcrEngine;
  header?: Record<string, OcrHeaderValue>;
  vendor_id?: string | null;
  shipment_id?: string | null;
  id: string;
  scan_job_id: string;
  scan_job_no: string;
  document_id: string;
  document_mime: string;
  warehouse_id: string;
  warehouse_code: string;
  run_no: number;
  status: OcrStatus;
  provider: string;
  template: string | null;
  customer_id: string | null;
  customer_name: string | null;
  warnings: string[];
  page_count: number | null;
  error_code: string | null;
  error_detail: string | null;
  sales_order_id: string | null;
  sales_order_no: string | null;
  requested_by_name: string | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
  completed_at: string | null;
  field_count: number;
  open_count: number;
  version: number;
};
export type OcrExtractionDetail = OcrExtraction & { fields: OcrField[]; text_blocks: { text: string }[] };
export type OcrAccuracy = {
  samples: number;
  correct: number;
  accuracy: string | null;
  target: string;
  minimum_samples: number;
  meets_target: boolean;
  by_status: Record<string, number>;
};
export type OcrResolveIn = {
  version: number;
  action: "CONFIRM" | "CORRECT" | "REMOVE";
  product_id?: string;
  unit_code?: UnitCode;
  qty?: string;
  reason?: string;
};
export type ScanJobCreated = ScanJob & { capability: string; capability_expires_at: string };

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra: Record<string, unknown> = {},
    /** HTTP method of the failed request: a failed write says whether anything was saved. */
    public method?: string,
  ) {
    super(message);
  }
  /** Server request id: the reference support uses to find the log line. */
  get requestId(): string | null {
    return typeof this.extra.request_id === "string" ? this.extra.request_id : null;
  }
}

/** The latest failed request (for error screens that weren't handed the error itself). */
let lastFailure: { err: ApiError; at: number } | null = null;
export function recentFailure(withinMs = 15_000): ApiError | null {
  return lastFailure && Date.now() - lastFailure.at <= withinMs ? lastFailure.err : null;
}
function failed(err: ApiError): ApiError {
  lastFailure = { err, at: Date.now() };
  return err;
}

/** No answer within this time: the request is given up (the screen offers to try again). */
const TIMEOUT_MS = 120_000;

/** fetch() rejected (no answer at all): offline, dropped connection, timeout or stopped. */
function networkError(e: unknown, method: string): ApiError {
  const name = (e as { name?: string } | null)?.name;
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  const code = offline ? "OFFLINE" : name === "TimeoutError" ? "TIMEOUT" : name === "AbortError" ? "ABORTED" : "NETWORK";
  return failed(new ApiError(0, code, "No answer from Pro Trading", {}, method));
}

const CSRF_KEY = "pt.csrf";
const storage = {
  get: () => sessionStorage.getItem(CSRF_KEY),
  set: (v: string | null) => (v ? sessionStorage.setItem(CSRF_KEY, v) : sessionStorage.removeItem(CSRF_KEY)),
};

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => {
  onUnauthorized = fn;
};

/** Raw (non-JSON) body, e.g. a scanned file: `{ raw, type }`. */
type RawBody = { raw: Blob; type: string };
const isRaw = (b: unknown): b is RawBody => typeof b === "object" && b !== null && "raw" in b && b.raw instanceof Blob;

/**
 * The CSRF token lives in this tab's sessionStorage, but the session cookie is shared by every
 * tab: signing in again elsewhere (another tab, the dev auto sign-in) starts a new session and
 * leaves this tab with a stale token, or none in a new tab. Then the server gives the current
 * session a fresh token (same-origin only) and the request is sent once more. One renewal at a
 * time; null when the session itself is gone.
 */
let renewing: Promise<string | null> | null = null;
export function renewCsrf(): Promise<string | null> {
  renewing ??= fetch("/api/v1/auth/csrf", { method: "POST", headers: { Accept: "application/json" }, credentials: "same-origin" })
    .then(async (res) => {
      const token = res.ok ? ((await res.json()) as { csrf_token?: string }).csrf_token ?? null : null;
      if (token) storage.set(token);
      return token;
    })
    .catch(() => null)
    .finally(() => {
      renewing = null;
    });
  return renewing;
}

async function request<T>(method: string, path: string, body?: unknown, idempotent = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = isRaw(body) ? body.type : "application/json";
  if (method !== "GET" && idempotent) headers["Idempotency-Key"] = crypto.randomUUID();
  const send = () => {
    if (method !== "GET") {
      const csrf = storage.get();
      if (csrf) headers["X-CSRF-Token"] = csrf;
      else delete headers["X-CSRF-Token"];
    }
    return fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : isRaw(body) ? body.raw : JSON.stringify(body),
      signal: typeof AbortSignal !== "undefined" && "timeout" in AbortSignal ? AbortSignal.timeout(TIMEOUT_MS) : undefined,
    }).catch((e: unknown) => {
      throw networkError(e, method);
    });
  };
  let res = await send();
  // Refused before anything ran, so sending again (same idempotency key) is safe.
  if (res.status === 403 && method !== "GET" && (await csrfRefused(res)) && (await renewCsrf())) res = await send();
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw toApiError(res.status, res.statusText, data, method, res.headers.get("X-Request-Id"));
  return data as T;
}

/** An error answer as an ApiError. No JSON envelope with a 5xx means the API itself didn't
 * answer (the dev proxy or a load balancer did): the server is down or restarting. */
function toApiError(status: number, statusText: string, data: unknown, method: string, headerRid: string | null): ApiError {
  const env = (data as { error?: Record<string, unknown> } | null)?.error;
  if (!env && status >= 500) return failed(new ApiError(status, "SERVER_UNREACHABLE", statusText, headerRid ? { request_id: headerRid } : {}, method));
  const { code = "HTTP_ERROR", message = statusText, ...extra } = (env ?? {}) as { code?: string; message?: string } & Record<string, unknown>;
  if (!extra.request_id && headerRid) extra.request_id = headerRid;
  if (status === 401 || code === "CSRF_FAILED") onUnauthorized();
  return failed(new ApiError(status, code, message, extra, method));
}

async function csrfRefused(res: Response): Promise<boolean> {
  const data = (await res.clone().json().catch(() => ({}))) as { error?: { code?: string } };
  return data.error?.code === "CSRF_FAILED";
}
/** The same fetch wrapper for feature modules that keep their own endpoints (e.g. lib/notificationsApi.ts). */
export const apiRequest = request;


/* ---------- Module 08: receiving ---------- */
export type Vendor = {
  id: string;
  code: string;
  name: string;
  country_code: string | null;
  email: string | null;
  currency: string;
  active: boolean;
  version: number;
};
export type ShipmentStatus = "OPEN" | "COUNTED" | "POSTED" | "CANCELLED";
export type ShipmentDocKind = "AWB" | "INVOICE" | "PACKING_LIST" | "COMBINED" | "OTHER";
export type LandedCostKind = "FREIGHT" | "DUTY" | "BROKERAGE" | "INSURANCE" | "OTHER";
export type CompareStatus = "MATCH" | "SHORT" | "OVER" | "DAMAGED" | "NOT_DECLARED" | "NOT_RECEIVED" | "DOC_CONFLICT";
export type Shipment = {
  id: string;
  shipment_no: string;
  status: ShipmentStatus;
  warehouse_id: string;
  warehouse_code: string;
  vendor_id: string;
  vendor_code: string;
  vendor_name: string;
  vendor_invoice_no: string | null;
  awb_no: string | null;
  currency: string;
  expected_on: string | null;
  note: string | null;
  document_count: number;
  discrepancy_count: number;
  grn_no: string | null;
  created_by_name: string;
  counted_by_name: string | null;
  counted_at: string | null;
  posted_by_name: string | null;
  posted_at: string | null;
  discrepancy_note: string | null;
  cancel_reason: string | null;
  version: number;
  created_at: string;
};
export type ShipmentDoc = {
  id: string;
  document_id: string;
  scan_job_id: string | null;
  scan_job_no: string | null;
  kind: ShipmentDocKind;
  doc_ref: string | null;
  filename: string;
  mime_type: string;
  byte_size: number;
  sha256: string;
  added_by_name: string;
  created_at: string;
};
export type DeclaredLine = {
  id: string;
  shipment_document_id: string | null;
  raw_label: string;
  product_id: string;
  sku: string;
  product_name: string;
  unit_code: UnitCode;
  declared_qty: string;
  unit_cost: string | null;
  created_by_name: string;
  created_at: string;
  removed_at: string | null;
  removed_reason: string | null;
};
export type GrnLine = {
  id: string;
  seq: number;
  product_id: string;
  sku: string;
  product_name: string;
  unit_code: UnitCode;
  accepted_qty: string;
  damaged_qty: string;
  rejected_qty: string;
  batch_ref: string | null;
  expires_at: string | null;
  note: string | null;
  unit_cost: string | null;
  lot_id: string | null;
  lot_code: string | null;
  counted_by_name: string;
  version: number;
};
export type LandedCost = {
  id: string;
  kind: LandedCostKind;
  amount: string;
  currency: string;
  shipment_document_id: string | null;
  note: string | null;
  created_by_name: string;
  created_at: string;
  removed_at: string | null;
  removed_reason: string | null;
};
export type CompareRow = {
  product_id: string;
  sku: string;
  product_name: string;
  unit_code: UnitCode;
  declared_qty: string | null;
  declared_by_document: Record<string, string>;
  accepted_qty: string | null;
  damaged_qty: string | null;
  rejected_qty: string | null;
  difference: string | null;
  status: CompareStatus;
};
export type ShipmentDetail = Shipment & {
  documents: ShipmentDoc[];
  declared: DeclaredLine[];
  counted: GrnLine[];
  costs: LandedCost[];
  comparison: CompareRow[];
  cost_totals: Record<string, string>;
  /** What an empty Batch will most likely be named when the line is saved (D-37). */
  next_batch_ref: string;
};
export type CountIn = {
  product_id: string;
  unit_code: UnitCode;
  accepted_qty: string;
  damaged_qty: string;
  rejected_qty: string;
  batch_ref?: string | null;
  expires_at?: string | null;
  note?: string | null;
};

let devLoginInFlight: Promise<Session> | null = null;

export const api = {
  async login(email: string, password: string): Promise<Session> {
    const r = await request<Session & { csrf_token: string }>("POST", "/auth/login", { email, password });
    storage.set(r.csrf_token);
    return r;
  },
  /** Development only: the API answers 404 unless DEV_AUTO_LOGIN_EMAIL is set (never in production).
   * Calls made at the same time share one sign-in (each sign-in is a new session and cookie, so
   * two racing ones could leave this tab with the other one's CSRF token). */
  devLogin(): Promise<Session> {
    devLoginInFlight ??= request<Session & { csrf_token: string }>("POST", "/auth/dev-login")
      .then((r) => {
        storage.set(r.csrf_token);
        return r as Session;
      })
      .finally(() => {
        devLoginInFlight = null;
      });
    return devLoginInFlight;
  },
  session: () => request<Session>("GET", "/auth/session"),
  async logout(): Promise<void> {
    try {
      await request<undefined>("POST", "/auth/logout");
    } finally {
      storage.set(null);
    }
  },
  /* Two-step sign-in (optional). From My account the password is asked again. */
  mfaEnroll: (password?: string) => request<{ otpauth_uri: string }>("POST", "/auth/mfa/enroll", password ? { password } : {}),
  mfaConfirm: (code: string) => request<{ session: Session; recovery_codes: string[] }>("POST", "/auth/mfa/enroll/confirm", { code }),
  mfaVerify: (b: SecondFactor) => request<Session>("POST", "/auth/mfa/verify", b),
  mfaDisable: (b: SecondFactor & { password: string }) => request<Session>("POST", "/auth/mfa/disable", b),
  mfaRecoveryCodes: (b: SecondFactor & { password: string }) => request<{ recovery_codes: string[] }>("POST", "/auth/mfa/recovery-codes", b),
  /* Account links (public): forgot / reset password, invitation */
  forgotPassword: (email: string) => request<{ message: string }>("POST", "/auth/password/forgot", { email }),
  inspectLink: (token: string, purpose: LinkPurpose) => request<LinkInfo>("POST", "/auth/token/inspect", { token, purpose }),
  resetPassword: (token: string, password: string) => request<undefined>("POST", "/auth/password/reset", { token, password }),
  acceptInvite: (token: string, password: string) => request<undefined>("POST", "/auth/invitation/accept", { token, password }),
  /* My sessions and sign-in activity */
  mySessions: () => request<SessionInfo[]>("GET", "/auth/sessions"),
  revokeMySession: (id: string) => request<undefined>("POST", `/auth/sessions/${id}/revoke`),
  revokeMyOtherSessions: () => request<undefined>("POST", "/auth/sessions/revoke-others"),
  myActivity: () => request<SecurityEvent[]>("GET", "/auth/activity"),
  warehouses: () => request<Warehouse[]>("GET", "/warehouses"),
  createWarehouse: (b: { code: string; name: string }) => request<Warehouse>("POST", "/warehouses", b, true),
  updateWarehouse: (id: string, b: { name: string; active: boolean; version: number }) =>
    request<Warehouse>("PATCH", `/warehouses/${id}`, b, true),
  categories: () => request<Category[]>("GET", "/product-categories"),
  products: (q: { search?: string; category_id?: string; active?: boolean; cursor?: string; limit?: number }) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
    return request<{ items: ProductSummary[]; next_cursor: string | null }>("GET", `/products?${p}`);
  },
  product: (id: string) => request<Product>("GET", `/products/${id}`),
  createProduct: (b: ProductIn) => request<Product>("POST", "/products", b, true),
  updateProduct: (
    id: string,
    b: { name: string; category_id: string | null; tracks_expiry: boolean; active: boolean; version: number },
  ) => request<Product>("PATCH", `/products/${id}`, b, true),
  addAlias: (id: string, b: { alias: string; source_label: string }) =>
    request<Alias>("POST", `/products/${id}/aliases`, b, true),
  removeAlias: (id: string, aliasId: string) => request<Alias>("DELETE", `/products/${id}/aliases/${aliasId}`, undefined, true),
  addProfile: (id: string, b: UnitProfileIn) => request<Product>("POST", `/products/${id}/unit-profiles`, b, true),
  caseSizes: (q: { q?: string; include_inactive?: boolean } = {}) => request<CaseSize[]>("GET", `/unit-profiles?${qs(q)}`),
  profileCommand: (id: string, pid: string, cmd: "deactivate" | "make-default", version: number) =>
    request<Product>("POST", `/products/${id}/unit-profiles/${pid}/${cmd}`, { version }, true),
  weightCalc: (id: string, groups: WeightGroupIn[]) =>
    request<WeightCalc>("POST", `/products/${id}/weight-calc`, { groups }),
  customerGroups: () => request<CustomerGroup[]>("GET", "/customer-groups"),
  customers: (q: { q?: string; group_id?: string; active?: boolean; limit?: number } = {}) => request<Customer[]>("GET", `/customers?${qs(q)}`),
  customer: (id: string) => request<Customer>("GET", `/customers/${id}`),
  createCustomer: (b: CustomerIn) => request<Customer>("POST", "/customers", b, true),
  updateCustomer: (id: string, b: CustomerPatch) => request<Customer>("PATCH", `/customers/${id}`, b, true),
  createCustomerGroup: (b: { code: string; name: string }) => request<CustomerGroup>("POST", "/customer-groups", b, true),
  priceVersions: (status?: string) => request<PriceVersion[]>("GET", `/price-books/versions?${qs({ status })}`),
  priceVersion: (id: string) => request<PriceVersion>("GET", `/price-books/versions/${id}`),
  createPriceVersion: (b: { version_code: string; notes?: string; based_on_id?: string | null }) =>
    request<PriceVersion>("POST", "/price-books/versions", b, true),
  previewPriceCsv: (csv: string, against_version_id?: string) =>
    request<PricePreview>("POST", "/price-books/imports/preview", { csv, against_version_id }, true),
  importPriceCsv: (b: { version_code: string; notes?: string; csv: string }) =>
    request<PriceVersion>("POST", "/price-books/versions/import", b, true),
  priceEntries: (
    id: string,
    q: { q?: string; unit_code?: string; scope?: string; product_id?: string; cursor?: string; limit?: number },
  ) => request<{ items: PriceEntry[]; next_cursor: string | null; total: number }>(
    "GET",
    `/price-books/versions/${id}/entries?${qs(q)}`,
  ),
  setPrice: (id: string, b: PriceEntryIn) => request<PriceVersion>("PUT", `/price-books/versions/${id}/entries`, b, true),
  removePrice: (id: string, entry_id: string, version: number) =>
    request<PriceVersion>("POST", `/price-books/versions/${id}/entries/remove`, { entry_id, version }, true),
  activatePriceVersion: (id: string, version: number, effective_at?: string) =>
    request<PriceVersion>("POST", `/price-books/versions/${id}/activate`, { version, effective_at }, true),
  cancelPriceVersion: (id: string, version: number) =>
    request<PriceVersion>("POST", `/price-books/versions/${id}/archive`, { version }, true),
  discardPriceVersion: (id: string, version: number) =>
    request<{ id: string }>("POST", `/price-books/versions/${id}/discard`, { version }, true),
  priceDiff: (id: string, against?: string) =>
    request<PriceDiff>("GET", `/price-books/versions/${id}/diff?${qs({ against_version_id: against })}`),
  resolvePrice: (q: { product_id: string; unit_code: UnitCode; customer_id?: string; at?: string }) =>
    request<PriceQuote>("GET", `/prices/resolve?${qs(q)}`),
  stock: (q: { warehouse_id?: string; product_id?: string; lot_status?: string; q?: string; cursor?: string; limit?: number }) =>
    request<Page<Balance>>("GET", `/inventory/stock?${qs(q)}`),
  movements: (q: { warehouse_id?: string; product_id?: string; lot_id?: string; movement_type?: string; cursor?: string; limit?: number }) =>
    request<Page<Movement>>("GET", `/inventory/movements?${qs(q)}`),
  lot: (id: string) => request<Lot>("GET", `/inventory/lots/${id}`),
  openingBalance: (b: {
    warehouse_id: string;
    product_id: string;
    lot_code?: string;
    batch_ref?: string;
    expires_at?: string;
    lines: { unit_code: UnitCode; qty: string }[];
    note?: string;
  }) => request<Posting>("POST", "/inventory/opening-balances", b, true),
  adjustStock: (b: { lot_id: string; unit_code: UnitCode; delta_qty: string; reason_code: AdjustReason; note: string }) =>
    request<Posting>("POST", "/inventory/adjustments", b, true),
  damageStock: (b: { lot_id: string; unit_code: UnitCode; qty: string; note: string }) =>
    request<Posting>("POST", "/inventory/damage", b, true),
  reverseMovement: (id: string, reason: string) =>
    request<Posting>("POST", `/inventory/movements/${id}/reverse`, { reason }, true),
  setLotStatus: (id: string, b: { status: "SALEABLE" | "QUARANTINE" | "EXPIRED"; version: number; reason: string }) =>
    request<Lot>("POST", `/inventory/lots/${id}/status`, b, true),
  counts: () => request<StockCount[]>("GET", "/inventory/counts"),
  submitCount: (b: { warehouse_id: string; note?: string; lines: { lot_id: string; unit_code: UnitCode; counted_qty: string }[] }) =>
    request<StockCount>("POST", "/inventory/counts", b, true),
  postCount: (id: string, version: number) => request<StockCount>("POST", `/inventory/counts/${id}/post`, { version }, true),
  rejectCount: (id: string, version: number, reason: string) =>
    request<StockCount>("POST", `/inventory/counts/${id}/reject`, { version, reason }, true),
  reconciliation: () => request<Reconciliation>("GET", "/inventory/reconciliation"),
  rebuildBalances: () => request<Reconciliation>("POST", "/inventory/reconciliation/rebuild", undefined, true),
  orders: (q: { status?: string; warehouse_id?: string; customer_id?: string; backorder?: boolean; q?: string; cursor?: string; limit?: number }) =>
    request<Page<OrderSummary>>("GET", `/sales-orders?${qs(q)}`),
  order: (id: string) => request<Order>("GET", `/sales-orders/${id}`),
  createOrder: (b: OrderIn) => request<Order>("POST", "/sales-orders", b, true),
  updateOrder: (id: string, b: OrderIn & { version: number }) => request<Order>("PUT", `/sales-orders/${id}`, b, true),
  confirmOrder: (id: string, version: number) => request<Order>("POST", `/sales-orders/${id}/confirm`, { version }, true),
  cancelOrder: (id: string, version: number, reason: string) =>
    request<Order>("POST", `/sales-orders/${id}/cancel`, { version, reason }, true),
  closeOrder: (id: string, version: number, reason: string) =>
    request<Order>("POST", `/sales-orders/${id}/close`, { version, reason }, true),
  cancelLineQty: (id: string, lineId: string, b: { version: number; qty: string; reason: string }) =>
    request<Order>("POST", `/sales-orders/${id}/lines/${lineId}/cancel`, b, true),
  openPick: (orderId: string, version: number) => request<Pick>("POST", `/sales-orders/${orderId}/pick-attempts`, { version }, true),
  picks: (q: { status?: string; warehouse_id?: string; code?: string }) => request<PickSummary[]>("GET", `/pick-attempts?${qs(q)}`),
  pick: (id: string) => request<Pick>("GET", `/pick-attempts/${id}`),
  recordPick: (id: string, version: number, lines: PickLineIn[]) =>
    request<Pick>("POST", `/pick-attempts/${id}/record`, { version, lines }, true),
  submitPick: (id: string, version: number) => request<Pick>("POST", `/pick-attempts/${id}/submit`, { version }, true),
  verifyPick: (id: string, b: { version: number; dispositions: Record<string, Disposition>; reason: string }) =>
    request<Pick>("POST", `/pick-attempts/${id}/verify`, b, true),
  returnPick: (id: string, version: number, reason: string) =>
    request<Pick>("POST", `/pick-attempts/${id}/return`, { version, reason }, true),
  abortPick: (id: string, version: number, reason: string) =>
    request<Pick>("POST", `/pick-attempts/${id}/abort`, { version, reason }, true),
  invoicePreview: (pickId: string, weights: Record<string, WeightRow[]>) =>
    request<InvoicePreview>("POST", `/pick-attempts/${pickId}/invoice-preview`, { weights }),
  issueInvoice: (pickId: string, b: InvoiceHeaderIn & { version: number; dispatch_confirmed: boolean; weights: Record<string, WeightRow[]>; note: string }) =>
    request<Invoice>("POST", `/pick-attempts/${pickId}/issue-invoice`, b, true),
  invoices: (q: { q?: string; customer_id?: string; sales_order_id?: string; picking_attempt_id?: string; email_status?: string; open?: boolean; cursor?: string; limit?: number }) =>
    request<Page<InvoiceSummary>>("GET", `/invoices?${qs(q)}`),
  invoice: (id: string) => request<Invoice>("GET", `/invoices/${id}`),
  resendInvoiceEmail: (id: string) => request<Invoice>("POST", `/invoices/${id}/email`, undefined, true),
  previewDirectInvoice: (b: DirectInvoiceIn) => request<DirectPreview>("POST", "/invoices/direct/preview", b),
  /** Entry grid: every active product with case sizes and aliases, in one call. */
  productLookup: () => request<Product[]>("GET", "/product-lookup"),
  /** Entry grid: live prices, pounds, batches and stock per line. Writes nothing. */
  previewLines: (b: LinePreviewIn) => request<LinePreview>("POST", "/sales-orders/preview", b),
  issueDirectInvoice: (b: DirectInvoiceIn & { dispatch_confirmed: boolean }) => request<Invoice>("POST", "/invoices/direct", b, true),
  invoiceSettings: () => request<InvoiceSettings>("GET", "/invoice-settings"),
  scanDevices: (q: { warehouse_id?: string; include_revoked?: boolean } = {}) =>
    request<ScanDevice[]>("GET", `/scan-devices?${qs(q)}`),
  pairScanDevice: (b: { warehouse_id: string; name: string }) =>
    request<{ device_id: string; pairing_code: string; expires_at: string }>("POST", "/scan-devices/pairings", b, true),
  discoverScanners: (id: string) => request<ScanDevice>("POST", `/scan-devices/${id}/discover`),
  approveScanDevice: (id: string, version: number, fingerprint: string) =>
    request<ScanDevice>("POST", `/scan-devices/${id}/approve`, { version, fingerprint }, true),
  revokeScanDevice: (id: string, version: number, reason: string) =>
    request<ScanDevice>("POST", `/scan-devices/${id}/revoke`, { version, reason }, true),
  scanJobs: (q: { status?: string; job_type?: string; channel?: string; warehouse_id?: string; q?: string; cursor?: string; limit?: number }) =>
    request<Page<ScanJob>>("GET", `/scans/jobs?${qs(q)}`),
  scanJob: (id: string) => request<ScanJob>("GET", `/scans/jobs/${id}`),
  requestScan: (b: { warehouse_id: string; job_type: ScanJobType; device_id: string; note?: string }) =>
    request<ScanJobCreated>("POST", "/scans/jobs", b, true),
  cancelScan: (id: string, version: number) => request<ScanJob>("POST", `/scans/jobs/${id}/cancel`, { version }, true),
  uploadScan: (file: File, q: { warehouse_id: string; job_type: ScanJobType; note?: string }) =>
    request<ScanJob>(
      "POST",
      `/scans/uploads?${qs({ ...q, filename: file.name })}`,
      { raw: file, type: file.type || "application/octet-stream" },
      true,
    ),
  /** Same as uploadScan, with real upload progress (0–1) from the browser. */
  uploadScanProgress: (file: File, q: { warehouse_id: string; job_type: ScanJobType }, onProgress: (f: number) => void) => {
    const key = crypto.randomUUID();
    const send = (retried: boolean): Promise<ScanJob> =>
      new Promise<ScanJob>((resolve, reject) => {
        const x = new XMLHttpRequest();
        x.open("POST", `/api/v1/scans/uploads?${qs({ ...q, filename: file.name })}`);
        x.setRequestHeader("Accept", "application/json");
        x.setRequestHeader("Content-Type", file.type || "application/octet-stream");
        const csrf = storage.get();
        if (csrf) x.setRequestHeader("X-CSRF-Token", csrf);
        x.setRequestHeader("Idempotency-Key", key);
        x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
        x.timeout = 10 * 60_000;
        x.onerror = () => reject(networkError(null, "POST"));
        x.ontimeout = () => reject(networkError({ name: "TimeoutError" }, "POST"));
        x.onload = () => {
          let data: { error?: { code?: string; message?: string } & Record<string, unknown> } & Record<string, unknown> = {};
          try {
            data = JSON.parse(x.responseText || "{}");
          } catch {
            /* not JSON */
          }
          if (x.status >= 200 && x.status < 300) return resolve(data as unknown as ScanJob);
          const code = data.error?.code ?? "HTTP_ERROR";
          // stale or missing CSRF token in this tab: get a fresh one and send the file once more
          if (x.status === 403 && code === "CSRF_FAILED" && !retried) {
            void renewCsrf().then((token) => {
              if (token) return send(true).then(resolve, reject);
              reject(toApiError(x.status, x.statusText, data, "POST", x.getResponseHeader("X-Request-Id")));
            });
            return;
          }
          reject(toApiError(x.status, x.statusText, data, "POST", x.getResponseHeader("X-Request-Id")));
        };
        x.send(file);
      });
    return send(false);
  },
  ocrExtractions: (q: { status?: string; warehouse_id?: string; scan_job_id?: string; q?: string; cursor?: string; limit?: number }) =>
    request<Page<OcrExtraction>>("GET", `/ocr/extractions?${qs(q)}`),
  ocrExtraction: (id: string) => request<OcrExtractionDetail>("GET", `/ocr/extractions/${id}`),
  ocrAccuracy: () => request<OcrAccuracy>("GET", "/ocr/accuracy"),
  ocrEngine: () => request<OcrEngineStatus>("GET", "/ocr/engine"),
  ocrEngineReading: (id: string) => request<OcrEngineReading>("GET", `/ocr/extractions/${id}/engine`),
  readScan: (scan_job_id: string) => request<OcrExtractionDetail>("POST", "/ocr/extractions", { scan_job_id }, true),
  resolveOcrField: (id: string, fieldId: string, b: OcrResolveIn) =>
    request<OcrExtractionDetail>("POST", `/ocr/extractions/${id}/fields/${fieldId}/resolve`, b, true),
  /** Confirm the ticked clear lines as read, all or none. */
  confirmOcrLines: (id: string, b: { version: number; field_ids: string[] }) =>
    request<OcrExtractionDetail>("POST", `/ocr/extractions/${id}/fields/confirm`, b, true),
  addOcrLine: (id: string, b: { version: number; product_id: string; unit_code: UnitCode; qty: string; reason: string }) =>
    request<OcrExtractionDetail>("POST", `/ocr/extractions/${id}/lines`, b, true),
  fixOcrHeader: (id: string, b: { version: number; field: string; value: string | null; reason: string }) =>
    request<OcrExtractionDetail>("POST", `/ocr/extractions/${id}/header`, b),
  ocrToReceiving: (id: string, b: { version: number; vendor_id: string; shipment_id?: string | null; document_no?: string | null; currency?: string | null; reviewed_all: true }) =>
    request<OcrExtractionDetail>("POST", `/ocr/extractions/${id}/to-receiving`, b, true),
  documentPageUrl: (documentId: string, page: number) => `/api/v1/scans/documents/${documentId}/pages/${page}`,
  acceptOcr: (id: string, b: { version: number; customer_id: string; requested_for?: string | null; note: string; reviewed_all: true }) =>
    request<OcrExtractionDetail>("POST", `/ocr/extractions/${id}/accept`, b, true),
  rejectOcr: (id: string, version: number, reason: string) =>
    request<OcrExtractionDetail>("POST", `/ocr/extractions/${id}/reject`, { version, reason }, true),
  vendors: (q: { active?: boolean; q?: string } = {}) => request<Vendor[]>("GET", `/vendors?${qs(q)}`),
  createVendor: (b: { code: string; name: string; country_code?: string | null; email?: string | null; currency: string }) =>
    request<Vendor>("POST", "/vendors", b, true),
  updateVendor: (id: string, b: { name: string; country_code: string | null; email: string | null; currency: string; active: boolean; version: number }) =>
    request<Vendor>("PATCH", `/vendors/${id}`, b, true),
  shipments: (q: { status?: string; warehouse_id?: string; vendor_id?: string; q?: string; cursor?: string; limit?: number }) =>
    request<Page<Shipment>>("GET", `/shipments?${qs(q)}`),
  shipment: (id: string) => request<ShipmentDetail>("GET", `/shipments/${id}`),
  createShipment: (b: { warehouse_id: string; vendor_id: string; vendor_invoice_no?: string | null; awb_no?: string | null; currency?: string | null; expected_on?: string | null; note?: string | null }) =>
    request<ShipmentDetail>("POST", "/shipments", b, true),
  attachShipmentScan: (id: string, b: { version: number; scan_job_id: string; kind: ShipmentDocKind; doc_ref?: string | null }) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/documents`, b, true),
  attachShipmentGmail: (id: string, b: { version: number; attachment_id: string; kind: ShipmentDocKind; doc_ref?: string | null }) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/documents/gmail`, b, true),
  uploadShipmentDoc: (id: string, file: File, q: { version: number; kind: ShipmentDocKind; doc_ref?: string }) =>
    request<ShipmentDetail>(
      "POST",
      `/shipments/${id}/documents/upload?${qs({ ...q, filename: file.name })}`,
      { raw: file, type: file.type || "application/octet-stream" },
      true,
    ),
  declareLine: (id: string, b: { version: number; shipment_document_id?: string | null; raw_label: string; product_id?: string | null; unit_code: UnitCode; declared_qty: string; unit_cost?: string | null; remember_alias?: boolean }) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/declared`, b, true),
  withdrawDeclared: (id: string, lineId: string, version: number, reason: string) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/declared/${lineId}/withdraw`, { version, reason }, true),
  addCount: (id: string, version: number, b: CountIn) => request<ShipmentDetail>("POST", `/shipments/${id}/count`, { version, ...b }, true),
  editCount: (id: string, lineId: string, version: number, line_version: number, b: CountIn) =>
    request<ShipmentDetail>("PUT", `/shipments/${id}/count/${lineId}`, { version, line_version, ...b }, true),
  removeCount: (id: string, lineId: string, version: number) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/count/${lineId}/remove`, { version }, true),
  addLandedCost: (id: string, b: { version: number; kind: LandedCostKind; amount: string; currency: string; shipment_document_id?: string | null; note?: string | null }) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/costs`, b, true),
  withdrawLandedCost: (id: string, costId: string, version: number, reason: string) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/costs/${costId}/withdraw`, { version, reason }, true),
  submitShipmentCount: (id: string, version: number) => request<ShipmentDetail>("POST", `/shipments/${id}/submit-count`, { version }, true),
  reopenShipment: (id: string, version: number, reason: string) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/reopen`, { version, reason }, true),
  postShipment: (id: string, b: { version: number; acknowledge_discrepancies: boolean; discrepancy_note?: string | null }) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/post`, b, true),
  cancelShipment: (id: string, version: number, reason: string) =>
    request<ShipmentDetail>("POST", `/shipments/${id}/cancel`, { version, reason }, true),
  documents: (q: { kind?: DocFileKind; sync_status?: DocSyncStatus; warehouse_id?: string; invoice_id?: string; shipment_id?: string; q?: string; cursor?: string; limit?: number }) =>
    request<DocumentPage>("GET", `/documents?${qs(q)}`),
  document: (id: string) => request<DocumentFile>("GET", `/documents/${id}`),
  retryDocumentSync: (id: string, version: number) => request<DocumentFile>("POST", `/documents/${id}/retry-sync`, { version }, true),
  uploadPod: (invoiceId: string, file: File, note?: string) =>
    request<DocumentFile>(
      "POST",
      `/invoices/${invoiceId}/pod?${qs({ filename: file.name, note: note || undefined })}`,
      { raw: file, type: file.type || "application/octet-stream" },
      true,
    ),

  /* Module 10: payments + receivables */
  payments: (q: { status?: PaymentStatus; customer_id?: string; method?: PaymentMethod; invoice_id?: string; q?: string; cursor?: string; limit?: number }) =>
    request<PaymentPage>("GET", `/payments?${qs(q)}`),
  payment: (id: string) => request<Payment>("GET", `/payments/${id}`),
  recordPayment: (b: PaymentRecordIn) => request<Payment>("POST", "/payments", b, true),
  verifyPayment: (id: string, b: { version: number; verification_ref: string; verification_note?: string | null; allocations: AllocationIn[] }) =>
    request<Payment>("POST", `/payments/${id}/verify`, b, true),
  allocatePayment: (id: string, b: { version: number; allocations: AllocationIn[] }) =>
    request<Payment>("POST", `/payments/${id}/allocations`, b, true),
  reverseAllocation: (id: string, allocationId: string, b: { version: number; reason: string }) =>
    request<Payment>("POST", `/payments/${id}/allocations/${allocationId}/reverse`, b, true),
  rejectPayment: (id: string, b: { version: number; reason: string }) => request<Payment>("POST", `/payments/${id}/reject`, b, true),
  receivables: () => request<Receivables>("GET", "/receivables"),

  /* Suppliers: ledger + payments we make */
  supplierBalances: () => request<SupplierBalance[]>("GET", "/suppliers/balances"),
  supplierLedger: (id: string) => request<SupplierLedger>("GET", `/suppliers/${id}/ledger`),
  supplierPayments: (q: { vendor_id?: string; include_voided?: boolean; limit?: number } = {}) => request<SupplierPayment[]>("GET", `/supplier-payments?${qs(q)}`),
  recordSupplierPayment: (b: SupplierPaymentIn) => request<SupplierPayment>("POST", "/supplier-payments", b, true),
  voidSupplierPayment: (id: string, reason: string) => request<SupplierPayment>("POST", `/supplier-payments/${id}/void`, { reason }, true),

  /* Settings: my account + staff users */
  updateMe: (b: { display_name: string }) => request<StaffMember>("PATCH", "/auth/me", b, true),
  changePassword: (b: { current_password: string; new_password: string }) => request<undefined>("POST", "/auth/password", b, true),
  staff: () => request<StaffMember[]>("GET", "/employees"),
  createStaff: (b: { email: string; display_name: string; role_code: RoleCode }) => request<StaffMember>("POST", "/employees", b, true),
  resendInvite: (id: string) => request<StaffMember>("POST", `/employees/${id}/invite`),
  revokeStaffSessions: (id: string) => request<undefined>("POST", `/auth/employees/${id}/revoke-sessions`),
  /* Roles & permissions, Security & sessions (Super Admin) */
  roleMatrix: () => request<RoleMatrix>("GET", "/roles"),
  securitySettings: () => request<SecuritySettings>("GET", "/security/settings"),
  securitySessions: () => request<SessionInfo[]>("GET", "/security/sessions"),
  revokeSecuritySession: (id: string) => request<undefined>("POST", `/security/sessions/${id}/revoke`),
  securityEvents: (q: { kind?: EventKind; failed_only?: boolean; before?: string; limit?: number }) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "" && v !== false) p.set(k, String(v));
    return request<SecurityEvent[]>("GET", `/security/events?${p}`);
  },
  updateStaff: (id: string, b: { display_name?: string; role_code?: RoleCode; active?: boolean }) => request<StaffMember>("PATCH", `/employees/${id}`, b, true),
  resetStaffPassword: (id: string, password: string) => request<StaffMember>("POST", `/employees/${id}/reset-password`, { password }, true),
  resetStaffMfa: (id: string) => request<StaffMember>("POST", `/employees/${id}/reset-mfa`, undefined, true),

  /* Module 12: reports (read-only; report.read) */
  reportSales: (q: ReportQuery & { bucket?: "day" | "month" }) => request<SalesReport>("GET", `/reports/sales?${qs(q)}`),
  reportByProduct: (q: ReportQuery & { sort?: ReportSort; limit?: number }) =>
    request<ProductSalesReport>("GET", `/reports/sales-by-product?${qs(q)}`),
  reportByCustomer: (q: ReportQuery & { sort?: ReportSort; limit?: number }) =>
    request<CustomerSalesReport>("GET", `/reports/sales-by-customer?${qs(q)}`),
  reportStockFlow: (q: Omit<ReportQuery, "customer_id">) => request<StockFlowReport>("GET", `/reports/stock-flow?${qs(q)}`),
  reportReceiving: (q: Omit<ReportQuery, "customer_id"> & { vendor_id?: string }) =>
    request<ReceivingReport>("GET", `/reports/receiving?${qs(q)}`),
};

export const scanDocumentUrl = (documentId: string) => `/api/v1/scans/documents/${documentId}/content`;

export const orderSheetUrl = (customerId: string, warehouseId: string) =>
  `/api/v1/ocr/order-sheet?customer_id=${customerId}&warehouse_id=${warehouseId}`;

export const documentContentUrl = (id: string) => `/api/v1/documents/${id}/content`;

export const invoicePdfUrl = (id: string) => `/api/v1/invoices/${id}/pdf`;
/** Packing sheet PDF to print: `copies` identical copies (default 2), each on its own pages. */
export const packingSheetPdfUrl = (pickId: string, copies = 2) => `/api/v1/pick-attempts/${pickId}/sheet.pdf?copies=${copies}`;

export const priceCsvUrl = (versionId?: string) =>
  versionId ? `/api/v1/price-books/versions/${versionId}/export.csv` : "/api/v1/price-books/template.csv";

export const hasCompanyWide = (s: Session, perm: string) =>
  s.grants.some((g) => g.permission === perm && g.warehouse_id === null);
export const canInWarehouse = (s: Session, perm: string, wh: string) =>
  s.grants.some((g) => g.permission === perm && (g.warehouse_id === null || g.warehouse_id === wh));

export const has = (s: Session, perm: string) => s.grants.some((g) => g.permission === perm);

/* ---------- Module 10: payments ---------- */
export type PaymentMethod = "INTERAC" | "EFT" | "CHEQUE" | "CASH";
export type PaymentStatus = "REPORTED" | "VERIFIED" | "APPLIED" | "REJECTED";
export type PaymentSummary = {
  id: string;
  payment_no: string;
  status: PaymentStatus;
  customer_id: string;
  customer_name: string;
  customer_ref: string | null;
  method: PaymentMethod;
  amount: string;
  allocated_amount: string;
  unallocated_amount: string;
  currency: "CAD";
  received_on: string;
  payer_ref: string | null;
  reported_invoice_id: string | null;
  reported_invoice_no: string | null;
  note: string | null;
  recorded_by_name: string | null;
  recorded_at: string;
  verification_ref: string | null;
  verification_note: string | null;
  verified_by_name: string | null;
  verified_at: string | null;
  rejected_reason: string | null;
  rejected_by_name: string | null;
  rejected_at: string | null;
  version: number;
};
export type PaymentAllocation = {
  id: string;
  invoice_id: string;
  invoice_no: string;
  invoice_state: InvoiceState;
  invoice_balance: string;
  amount: string;
  reverses_id: string | null;
  reversed: boolean;
  reason: string | null;
  created_by_name: string | null;
  created_at: string;
};
export type Payment = PaymentSummary & { allocations: PaymentAllocation[] };
export type PaymentPage = Page<PaymentSummary> & { counts: Record<PaymentStatus, number> };
export type PaymentRecordIn = {
  customer_id: string;
  method: PaymentMethod;
  amount: string;
  received_on: string;
  payer_ref?: string | null;
  invoice_id?: string | null;
  note?: string | null;
};
export type AllocationIn = { invoice_id: string; amount: string };
export type AgingBuckets = { current: string; d1_30: string; d31_60: string; d61_90: string; d90_plus: string; total: string };
export type AgingCustomer = AgingBuckets & {
  customer_id: string;
  customer_name: string;
  customer_ref: string | null;
  email: string | null;
  open_invoices: number;
  oldest_due: string | null;
  unapplied_credit: string;
  reported_pending: string;
};
export type Receivables = AgingBuckets & {
  as_of: string;
  currency: "CAD";
  unapplied_credit: string;
  reported_pending: string;
  customers: AgingCustomer[];
  payment_instructions: string;
  reminder_offsets: number[];
};

/* ---------- Module 12: reports ---------- */
export type ReportQuery = { date_from?: string; date_to?: string; customer_id?: string; product_id?: string };
export type ReportSort = "top" | "least";
export type UnitQty = { unit_code: UnitCode; qty: string };
export type ReportPeriod = { date_from: string; date_to: string; timezone: string; currency: "CAD" };
export type SalesReport = ReportPeriod & {
  bucket: "day" | "month";
  invoice_count: number;
  customer_count: number;
  net_sales: string;
  tax: string;
  gross: string;
  quantities: UnitQty[];
  series: { period: string; invoice_count: number; net_sales: string }[];
};
export type ProductSalesRow = {
  product_id: string;
  sku: string;
  name: string;
  unit_code: UnitCode;
  qty: string;
  net_sales: string;
  avg_rate: string;
  invoice_count: number;
  customer_count: number;
};
export type ProductSalesReport = ReportPeriod & { total_rows: number; net_sales: string; rows: ProductSalesRow[] };
export type CustomerSalesRow = {
  customer_id: string;
  name: string;
  external_ref: string | null;
  invoice_count: number;
  net_sales: string;
  gross: string;
  quantities: UnitQty[];
  last_invoice_at: string;
};
export type CustomerSalesReport = ReportPeriod & { total_rows: number; net_sales: string; rows: CustomerSalesRow[] };
export type StockFlowRow = {
  product_id: string;
  sku: string;
  name: string;
  unit_code: UnitCode;
  opening: string;
  received: string;
  dispatched: string;
  other: string;
  closing: string;
};
export type StockFlowReport = ReportPeriod & { rows: StockFlowRow[] };
export type VendorReceivingRow = {
  vendor_id: string;
  code: string;
  name: string;
  country_code: string | null;
  unit_code: UnitCode;
  shipments: number;
  declared: string;
  accepted: string;
  damaged: string;
  rejected: string;
};
export type ReceivingReport = ReportPeriod & { shipments: number; rows: VendorReceivingRow[] };

/* ---------- Settings: staff users ---------- */
export type RoleCode = "OWNER_ADMIN" | "MANAGER" | "SALES_OPERATOR" | "WAREHOUSE" | "FINANCE" | "VIEWER";
export type StaffStatus = "ACTIVE" | "INVITED" | "SUSPENDED";
export type StaffMember = {
  id: string;
  email: string;
  display_name: string;
  active: boolean;
  status: StaffStatus;
  roles: string[];
  mfa_enrolled: boolean;
  locked: boolean;
  last_seen_at: string | null;
  last_login_at: string | null;
  invite_expires_at: string | null;
  created_at: string;
};
export type SecondFactor = { code: string } | { recovery_code: string };
export type LinkPurpose = "INVITE" | "PASSWORD_RESET";
export type LinkInfo = { email: string; display_name: string; purpose: LinkPurpose; expires_at: string };
export type SessionInfo = {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string | null;
  ip_address: string | null;
  current: boolean;
  employee_id?: string | null;
  employee_name?: string | null;
  employee_email?: string | null;
};
export type SecurityEvent = {
  id: string;
  action: string;
  success: boolean;
  created_at: string;
  actor_id: string | null;
  actor_name: string | null;
  target_type: string | null;
  target_id: string | null;
  target_name: string | null;
  reason: string | null;
  ip_address: string | null;
  user_agent: string | null;
};
export type EventKind = "all" | "sign_in" | "account" | "denied";
export type RoleInfo = { code: RoleCode; name: string; description: string; permissions: string[]; members: number };
export type RoleMatrix = { roles: RoleInfo[]; permissions: { code: string; description: string }[] };
export type SecuritySettings = {
  two_step_required: boolean;
  session_ttl_minutes: number;
  session_idle_minutes: number;
  login_max_failures: number;
  login_lockout_minutes: number;
  login_ip_max_failures: number;
  login_ip_window_minutes: number;
  invite_ttl_hours: number;
  password_reset_ttl_minutes: number;
  password_min_length: number;
};

/* ---------- Suppliers: ledger + payments ---------- */
export type SupplierPayMethod = "WIRE" | "EFT" | "CHEQUE" | "CASH" | "OTHER";
export type CurrencyTotal = { currency: string; billed: string; paid: string; balance: string };
export type SupplierBalance = {
  vendor_id: string;
  code: string;
  name: string;
  country_code: string | null;
  currency: string;
  active: boolean;
  shipments: number;
  last_received_at: string | null;
  last_paid_on: string | null;
  totals: CurrencyTotal[];
};
export type SupplierPaymentIn = {
  vendor_id: string;
  shipment_id?: string | null;
  method: SupplierPayMethod;
  amount: string;
  currency?: string | null;
  paid_on: string;
  reference?: string | null;
  note?: string | null;
};
export type SupplierPayment = {
  id: string;
  payment_no: string;
  vendor_id: string;
  vendor_name: string;
  shipment_id: string | null;
  shipment_no: string | null;
  method: SupplierPayMethod;
  amount: string;
  currency: string;
  paid_on: string;
  reference: string | null;
  note: string | null;
  recorded_by_name: string | null;
  created_at: string;
  voided_at: string | null;
  voided_by_name: string | null;
  void_reason: string | null;
};
export type LedgerUnits = { unit_code: UnitCode; declared: string; accepted: string; damaged: string; rejected: string };
export type SupplierLedger = {
  vendor_id: string;
  code: string;
  name: string;
  country_code: string | null;
  email: string | null;
  currency: string;
  active: boolean;
  totals: CurrencyTotal[];
  returned: LedgerUnits[];
  shipments: {
    id: string;
    shipment_no: string;
    grn_no: string | null;
    vendor_invoice_no: string | null;
    posted_at: string;
    currency: string;
    billed: string;
    unpriced_lines: number;
    units: LedgerUnits[];
    landed_costs: { kind: string; amount: string; currency: string }[];
  }[];
  payments: SupplierPayment[];
  entries: { on: string; kind: "SHIPMENT" | "PAYMENT"; ref: string; id: string; currency: string; billed: string; paid: string; balance: string; voided: boolean }[];
};

/* ---------- Direct invoices (no picking sheet) ---------- */
/** Salesperson / Customer PO No. printed on the invoice; blank salesperson = company default. */
export type InvoiceHeaderIn = { salesperson?: string; customer_po?: string };
/** `lot_id`: the batch handed over; left out = oldest expiry first (FEFO). */
export type DirectInvoiceIn = InvoiceHeaderIn & { customer_id: string; warehouse_id: string; lines: { product_id: string; unit_code: UnitCode; qty: string; note?: string; lot_id?: string | null; weight_lb?: string }[]; note?: string };
/** Letterhead and the defaults the Create invoice form starts from. */
export type InvoiceSettings = {
  legal_name: string;
  address: string;
  phone: string;
  fax: string;
  email: string;
  business_no: string;
  default_salesperson: string;
  invoice_prefix: string;
};
export type DirectPreview = {
  lines: { product_id: string; sku: string; description: string; unit_code: UnitCode; billed_qty: string; unit_rate: string; line_amount: string; tax_amount: string; pack_lb: string | null }[];
  subtotal: string;
  tax_amount: string;
  total: string;
  /** `lots`: the batches issuing takes, in order. */
  stock: { sku: string; unit_code: UnitCode; deduct_qty: string; available_qty: string; ok: boolean; lots?: string[] }[];
  stock_ok: boolean;
};

/* ---------- Entry grid: live per-line preview (POST /sales-orders/preview) ---------- */
/** Provisional: confirming an order or issuing an invoice prices and checks stock again. */
export type LineLot = { lot_id: string; lot_code: string; batch_ref: string | null; expires_at: string | null; available_qty: string };
export type LinePreviewLine = {
  line: number;
  product_id: string;
  sku: string;
  unit_code: UnitCode;
  qty: string;
  rate: string | null;
  line_amount: string | null;
  basis: string | null;
  price_scope: string | null;
  price_version_code: string | null;
  pack_lb: string | null;
  weight_lb: string | null;
  /** Per-lb price a box rate was worked out from (boxes are sold per lb). */
  lb_rate?: string | null;
  available_qty: string;
  stock_ok: boolean;
  lots: LineLot[];
  problem: { code: string; message: string } | null;
};
export type LinePreview = { lines: LinePreviewLine[]; subtotal: string; tax_code: string; tax_amount: string; total: string; complete: boolean };
export type LinePreviewIn = { customer_id: string | null; warehouse_id: string; lines: { product_id: string; unit_code: UnitCode; qty: string; lot_id?: string | null; weight_lb?: string }[] };

export type OcrHeaderValue = { value: string | null; confidence: string | null; corrected?: string | null; matched?: boolean; ambiguous?: boolean; supplier_default?: string };
