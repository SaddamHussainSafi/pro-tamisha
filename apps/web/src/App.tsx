import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Shell } from "./components/Shell";
import { DocumentsPage } from "./pages/Documents";
import { InventoryPage, LotPage } from "./pages/Inventory";
import { InvoicePage, InvoicesPage, IssueInvoicePage } from "./pages/Invoices";
import { LoginPage } from "./pages/Login";
import { MfaPage } from "./pages/Mfa";
import { NewOrderPage, OrderPage, OrdersPage } from "./pages/Orders";
import { OverviewPage } from "./pages/Overview";
import { PaymentDetailPage, PaymentsPage, ReceivablesPage } from "./pages/Payments";
import { PickSheetPage, PickingPage } from "./pages/Picking";
import { PriceVersionPage, PricingPage } from "./pages/Pricing";
import { ReportsPage } from "./pages/Reports";
import { OcrReviewPage } from "./pages/OcrReview";
import { ScanJobPage, ScansPage } from "./pages/Scans";
import { ProductDetailPage, ProductsPage } from "./pages/Products";
import { ReceivingPage, ShipmentPage, VendorsPage } from "./pages/Receiving";
import { WarehousesPage } from "./pages/Warehouses";
import { InvoicePreview } from "./pages/preview/InvoicePreview";
import { OrdersPreview } from "./pages/preview/OrdersPreview";
import { OverviewPreview } from "./pages/preview/OverviewPreview";
import { ReceivingPreview } from "./pages/preview/ReceivingPreview";

function Protected() {
  const { status, session } = useAuth();
  if (status === "loading") return <p className="p-6 text-sm text-ink-muted">Loading…</p>;
  if (!session) return <Navigate to="/login" replace />;
  if (session.mfa_required) return <Navigate to="/mfa" replace />;
  return <Shell />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/mfa" element={<MfaPage />} />
      <Route element={<Protected />}>
        <Route index element={<OverviewPage />} />
        <Route path="products" element={<ProductsPage />} />
        <Route path="products/:id" element={<ProductDetailPage />} />
        <Route path="pricing" element={<PricingPage />} />
        <Route path="pricing/:id" element={<PriceVersionPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="inventory/lots/:id" element={<LotPage />} />
        <Route path="orders" element={<OrdersPage />} />
        <Route path="orders/new" element={<NewOrderPage />} />
        <Route path="orders/:id" element={<OrderPage />} />
        <Route path="picking" element={<PickingPage />} />
        <Route path="picking/:id" element={<PickSheetPage />} />
        <Route path="picking/:id/invoice" element={<IssueInvoicePage />} />
        <Route path="invoices" element={<InvoicesPage />} />
        <Route path="invoices/:id" element={<InvoicePage />} />
        <Route path="payments" element={<PaymentsPage />} />
        <Route path="payments/:id" element={<PaymentDetailPage />} />
        <Route path="receivables" element={<ReceivablesPage />} />
        <Route path="receiving" element={<ReceivingPage />} />
        <Route path="receiving/:id" element={<ShipmentPage />} />
        <Route path="vendors" element={<VendorsPage />} />
        <Route path="scan" element={<ScansPage />} />
        <Route path="scan/review/:id" element={<OcrReviewPage />} />
        <Route path="scan/:id" element={<ScanJobPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings/warehouses" element={<WarehousesPage />} />
        <Route path="preview/overview" element={<OverviewPreview />} />
        <Route path="preview/orders" element={<OrdersPreview />} />
        <Route path="preview/invoice" element={<InvoicePreview />} />
        <Route path="preview/receiving" element={<ReceivingPreview />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
