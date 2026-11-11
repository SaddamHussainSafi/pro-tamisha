import { lazy, Suspense, useEffect, type ComponentType } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { Shell } from "./components/Shell";
import { LoginPage } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";
import { PageSkeleton } from "./ui/primitives";

// Every screen except Overview and Login is its own chunk, so the first load stays small.
// Once the app is idle the other chunks are fetched too, so moving between screens never waits.
const chunks = {
  authLinks: () => import("./pages/AuthLinks"),
  caseSizes: () => import("./pages/CaseSizes"),
  customers: () => import("./pages/Customers"),
  documents: () => import("./pages/Documents"),
  gmail: () => import("./pages/Gmail"),
  inventory: () => import("./pages/Inventory"),
  invoices: () => import("./pages/Invoices"),
  mfa: () => import("./pages/Mfa"),
  newInvoice: () => import("./pages/NewInvoice"),
  activity: () => import("./pages/Activity"),
  ocrReview: () => import("./pages/OcrReview"),
  orderConnect: () => import("./pages/OrderConnect"),
  orderConnectBuilder: () => import("./pages/OrderConnectBuilder"),
  orders: () => import("./pages/Orders"),
  payments: () => import("./pages/Payments"),
  picking: () => import("./pages/Picking"),
  pricing: () => import("./pages/Pricing"),
  products: () => import("./pages/Products"),
  receiving: () => import("./pages/Receiving"),
  reports: () => import("./pages/Reports"),
  scans: () => import("./pages/Scans"),
  settings: () => import("./pages/Settings"),
  suppliers: () => import("./pages/Suppliers"),
  trash: () => import("./pages/Trash"),
};
function page<M>(load: () => Promise<M>, pick: (m: M) => ComponentType) {
  return lazy(() => load().then((m) => ({ default: pick(m) })));
}
const ForgotPasswordPage = page(chunks.authLinks, (m) => m.ForgotPasswordPage);
const ResetPasswordPage = page(chunks.authLinks, (m) => m.ResetPasswordPage);
const AcceptInvitePage = page(chunks.authLinks, (m) => m.AcceptInvitePage);
const CaseSizesPage = page(chunks.caseSizes, (m) => m.CaseSizesPage);
const CustomersPage = page(chunks.customers, (m) => m.CustomersPage);
const CustomerDetailPage = page(chunks.customers, (m) => m.CustomerDetailPage);
const DocumentsPage = page(chunks.documents, (m) => m.DocumentsPage);
const GmailInboxPage = page(chunks.gmail, (m) => m.GmailInboxPage);
const GmailCallbackPage = page(chunks.gmail, (m) => m.GmailCallbackPage);
const InventoryPage = page(chunks.inventory, (m) => m.InventoryPage);
const LotPage = page(chunks.inventory, (m) => m.LotPage);
const InvoicesPage = page(chunks.invoices, (m) => m.InvoicesPage);
const InvoicePage = page(chunks.invoices, (m) => m.InvoicePage);
const IssueInvoicePage = page(chunks.invoices, (m) => m.IssueInvoicePage);
const MfaPage = page(chunks.mfa, (m) => m.MfaPage);
const NewInvoicePage = page(chunks.newInvoice, (m) => m.NewInvoicePage);
const ActivityPage = page(chunks.activity, (m) => m.ActivityPage);
const OcrReviewPage = page(chunks.ocrReview, (m) => m.OcrReviewPage);
const OcOverviewPage = page(chunks.orderConnect, (m) => m.OcOverviewPage);
const OcSubmissionsPage = page(chunks.orderConnect, (m) => m.OcSubmissionsPage);
const OcSubmissionPage = page(chunks.orderConnect, (m) => m.OcSubmissionPage);
const OcEmbedPage = page(chunks.orderConnect, (m) => m.OcEmbedPage);
const OcSettingsPage = page(chunks.orderConnect, (m) => m.OcSettingsPage);
const OcFormsPage = page(chunks.orderConnectBuilder, (m) => m.OcFormsPage);
const OcBuilderPage = page(chunks.orderConnectBuilder, (m) => m.OcBuilderPage);
const OrdersPage = page(chunks.orders, (m) => m.OrdersPage);
const OrderPage = page(chunks.orders, (m) => m.OrderPage);
const NewOrderPage = page(chunks.orders, (m) => m.NewOrderPage);
const PaymentsPage = page(chunks.payments, (m) => m.PaymentsPage);
const PaymentDetailPage = page(chunks.payments, (m) => m.PaymentDetailPage);
const ReceivablesPage = page(chunks.payments, (m) => m.ReceivablesPage);
const PickingPage = page(chunks.picking, (m) => m.PickingPage);
const PickSheetPage = page(chunks.picking, (m) => m.PickSheetPage);
const PricingPage = page(chunks.pricing, (m) => m.PricingPage);
const PriceVersionPage = page(chunks.pricing, (m) => m.PriceVersionPage);
const ProductsPage = page(chunks.products, (m) => m.ProductsPage);
const ProductDetailPage = page(chunks.products, (m) => m.ProductDetailPage);
const ReceivingPage = page(chunks.receiving, (m) => m.ReceivingPage);
const ShipmentPage = page(chunks.receiving, (m) => m.ShipmentPage);
const SuppliersPage = page(chunks.receiving, (m) => m.SuppliersPage);
const ReportsPage = page(chunks.reports, (m) => m.ReportsPage);
const ScansPage = page(chunks.scans, (m) => m.ScansPage);
const ScanJobPage = page(chunks.scans, (m) => m.ScanJobPage);
const SettingsPage = page(chunks.settings, (m) => m.SettingsPage);
const SupplierDetailPage = page(chunks.suppliers, (m) => m.SupplierDetailPage);
const SupplierPaymentsPage = page(chunks.suppliers, (m) => m.SupplierPaymentsPage);
const TrashPage = page(chunks.trash, (m) => m.TrashPage);

function usePrefetchScreens(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const run = () => Object.values(chunks).forEach((load) => void load().catch(() => undefined));
    if ("requestIdleCallback" in window) {
      const id = window.requestIdleCallback(run, { timeout: 4000 });
      return () => window.cancelIdleCallback(id);
    }
    const t = setTimeout(run, 1500);
    return () => clearTimeout(t);
  }, [enabled]);
}

/** Same frame as the signed-in app (sidebar column + page skeleton), so nothing jumps after sign-in. */
function ShellSkeleton() {
  return (
    <div className="flex h-full">
      <div aria-hidden className="hidden w-[276px] shrink-0 border-r border-line bg-card lg:block" />
      <div className="min-w-0 flex-1 px-3 pt-20 sm:px-6 sm:pt-24">
        <PageSkeleton />
      </div>
    </div>
  );
}

function Protected() {
  const loc = useLocation();
  const { status, session } = useAuth();
  if (status === "loading") return <ShellSkeleton />;
  // Remember the page that was asked for, so sign-in returns there.
  const from = { from: loc.pathname + loc.search };
  if (!session) return <Navigate to="/login" replace state={from} />;
  if (session.mfa_required) return <Navigate to="/mfa" replace state={from} />;
  return <Shell />;
}

export function App() {
  const { session } = useAuth();
  usePrefetchScreens(!!session);
  return (
    <Suspense fallback={<ShellSkeleton />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/mfa" element={<MfaPage />} />
        {/* Signed-out account links (no public sign-up exists). */}
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
        <Route element={<Protected />}>
          <Route index element={<OverviewPage />} />
          <Route path="products" element={<ProductsPage />} />
          <Route path="products/:id" element={<ProductDetailPage />} />
          <Route path="customers" element={<CustomersPage />} />
          <Route path="customers/:id" element={<CustomerDetailPage />} />
          <Route path="products/case-sizes" element={<CaseSizesPage />} />
          <Route path="products/prices" element={<PricingPage />} />
          <Route path="products/prices/:id" element={<PriceVersionPage />} />
          <Route path="pricing" element={<Navigate to="/products/prices" replace />} />
          <Route path="pricing/:id" element={<OldPriceLink />} />
          <Route path="inventory" element={<InventoryPage />} />
          <Route path="inventory/lots/:id" element={<LotPage />} />
          <Route path="orders" element={<OrdersPage />} />
          <Route path="orders/new" element={<NewOrderPage />} />
          <Route path="orders/:id" element={<OrderPage />} />
          <Route path="online-orders" element={<OcOverviewPage />} />
          <Route path="online-orders/forms" element={<OcFormsPage />} />
          <Route path="online-orders/forms/:id" element={<OcBuilderPage />} />
          <Route path="online-orders/requests" element={<OcSubmissionsPage />} />
          <Route path="online-orders/requests/:id" element={<OcSubmissionPage />} />
          <Route path="online-orders/embed" element={<OcEmbedPage />} />
          <Route path="online-orders/settings" element={<OcSettingsPage />} />
          {/* Old "Order Connect" links (saved notifications, trash entries, bookmarks). */}
          <Route path="order-connect/*" element={<OldOnlineOrdersLink />} />
          <Route path="picking" element={<PickingPage />} />
          <Route path="picking/:id" element={<PickSheetPage />} />
          <Route path="picking/:id/invoice" element={<IssueInvoicePage />} />
          <Route path="invoices" element={<InvoicesPage />} />
          <Route path="invoices/new" element={<NewInvoicePage />} />
          <Route path="invoices/:id" element={<InvoicePage />} />
          <Route path="payments" element={<PaymentsPage />} />
          <Route path="payments/:id" element={<PaymentDetailPage />} />
          <Route path="receivables" element={<ReceivablesPage />} />
          <Route path="receiving" element={<ReceivingPage />} />
          <Route path="receiving/:id" element={<ShipmentPage />} />
          <Route path="suppliers" element={<SuppliersPage />} />
          <Route path="suppliers/payments" element={<SupplierPaymentsPage />} />
          <Route path="suppliers/:id" element={<SupplierDetailPage />} />
          <Route path="vendors" element={<Navigate to="/suppliers" replace />} />
          <Route path="scan" element={<ScansPage />} />
          <Route path="scan/review/:id" element={<OcrReviewPage />} />
          <Route path="scan/:id" element={<ScanJobPage />} />
          <Route path="gmail" element={<GmailInboxPage />} />
          <Route path="gmail/oauth/callback" element={<GmailCallbackPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="settings/users" element={<SettingsPage />} />
          <Route path="settings/roles" element={<SettingsPage />} />
          <Route path="settings/security" element={<SettingsPage />} />
          <Route path="settings/ocr" element={<SettingsPage />} />
          <Route path="settings/notifications" element={<SettingsPage />} />
          <Route path="activity" element={<ActivityPage />} />
          <Route path="notifications" element={<Navigate to="/activity?view=notifications" replace />} />
          <Route path="trash" element={<TrashPage />} />
          <Route path="settings/*" element={<Navigate to="/settings" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

/** "Order Connect" was renamed "Online Orders": /order-connect/submissions/x → /online-orders/requests/x. */
function OldOnlineOrdersLink() {
  const { pathname, search } = useLocation();
  const rest = pathname.replace(/^\/order-connect/, "").replace(/^\/submissions/, "/requests");
  return <Navigate to={`/online-orders${rest}${search}`} replace />;
}

/** Old /pricing/:id links keep working. */
function OldPriceLink() {
  const { id = "" } = useParams();
  return <Navigate to={`/products/prices/${id}`} replace />;
}
