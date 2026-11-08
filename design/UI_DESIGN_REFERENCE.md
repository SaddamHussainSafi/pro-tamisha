# Pro Trading — UI & Design Reference (authoritative)

**Product name:** Pro Trading. **Company / legal entity:** Tamisha Trading Inc. Never rename company records to Pro Trading.  **Brand authority:** [tamishatrading.com](https://tamishatrading.com/).  **User-supplied inspiration images:** `design/references/`.

> **2026-11-08 — owner direction supersedes the palette rules below.**
> - **Layout** follows the reference dashboards (`references/01-invoice-dashboard-orange.jpg`, `02-sidebar-purple.jpg`, `03-finance-dashboard-orange.jpg`): light grey canvas, white 24px-radius cards without borders, top pill navigation with a black active pill, icon rail on the left, large KPI cards, striped bar charts with one highlighted bar, donut and progress bars.
> - **Colour** is the Tamisha Trading main green **#28A745** given by the owner, with its "apple" ramp (50 `#C8FECF` … 400 `#28A745` … 600 `#1B7A30` … 950 `#011504`), recorded in `tokens.json` schemaVersion 3. Use 400 for fills, chart highlights, icons and large text; 600 for text, links, focus rings and buttons with white text (4.5:1); 700 for hover. The provisional palette table below (#198C43 etc.) is replaced by this ramp. The orange of the references is not used except as one chart series colour.
> - Everything else in this file (data density, accessibility, numeric formatting, no fabricated figures, print template rules) still applies.

## Brand source of truth and assets
- Reuse company-owned official branding from website for the **Pro Trading** internal platform: retain the recognizable Tamisha globe/produce company mark when permission is available. Brand logo resources observed on the site:
  - Full-color emblem (transparent): https://tamishatrading.com/wp-content/uploads/2025/04/Untitled-1.png
  - White horizontal wordmark for dark surfaces: https://tamishatrading.com/wp-content/uploads/2025/05/Untitled-1-1.webp
- These are **source asset URLs, not bundled files**. Before production, download authorized copies into `/public/brand/`, verify resolution and transparency, optimize locally, and avoid hotlinking from the production app.
- The logo visibly features **green, deep blue, golden yellow, and gray**. The following theme tokens are *implementation approximations based on the logo*, not claimed exact CSS values of the public site. When possible inspect the live website styles using browser devtools and verify exact hex values; record any refinements in an ADR. No other brand palette (orange/purple from the inspirations) should override Tamisha branding.
- The public site promotes tropical produce, categories Vegetables, Roots, Herbs, Peppers, Fruits, Others, and the message “Delivering Nature’s Best – Daily.” Use this context selectively. It is an **internal ERP**, not an ecommerce/marketing site.

## Palette hierarchy (provisional; verify with site and logo source)
| Token | Color | Use |
|---|---|---|
| `brand.primary` | `#198C43` | Navigation active, primary action, links |
| `brand.primaryHover` | `#116C33` | Hover |
| `brand.secondary` | `#121B82` | Header/nav detail, analytics accents |
| `brand.accent` | `#F4C400` | Small highlights, not text on white |
| `brand.gray` | `#8D9694` | Subordinate neutrals |
| `surface.canvas` | `#F6F8F6` | Main page background |
| `surface.card` | `#FFFFFF` | Cards and tables |
| `text.primary` | `#17251E` | Content |
| `text.muted` | `#65756D` | Metadata |
| `border.default` | `#E0E8E2` | Dividers |
| `state.success` | `#167C46` | Paid, approved, in-stock |
| `state.warning` | `#B66B13` | Due, unverified, low stock |
| `state.danger` | `#B63838` | Error, rejected, shortage |

Do not use accent colors as the only way to convey status. Test WCAG 2.2 AA contrast and adapt tokens accordingly.

## Design references — what to borrow / what not to borrow
1. `references/01-invoice-dashboard-orange.jpg`: **Borrow** clean rounded dashboard cards, tidy invoice table, dense compact KPI cards, chip filters and top search. **Do not copy** orange palette or AI invoice marketing widget.
2. `references/02-sidebar-purple.jpg`: **Borrow** narrow collapsible left navigation, strong active state, balanced content zoning. **Do not copy** purple palette, oversized fitness visuals, game-like card layout.
3. `references/03-finance-dashboard-orange.jpg`: **Borrow** hierarchy, consistent card sizes, compact activity table, period filter and financial analytics. **Do not copy** payment-wallet design or fake account balances.
4. `references/04-analytics-charts.png`: **Borrow** thoughtful line/bar/donut composition, interaction and multi-metric comparison. **Do not copy** noisy gauge widgets or unrelated dashboard indicators.
5. `references/05-soft-dashboard.png`: **Borrow** generous whitespace, grouped navigational cards, tooltips and readable graph annotations. **Do not copy** course-learning interface semantics.
6. [Khata](https://khata.alpha.com.np/): Functional design benchmark to inspect if accessible. **Do not reuse** its proprietary graphics, HTML, source code, or unique trade dress. Translate useful information architecture patterns only.
7. Focus V7.0129 screenshots shared separately: **Functional mapping** for quantity, units, batch no, pounds/kg, rate, tax, customer, warehouse, due date and the order-to-invoice links. **Never emulate** its visual style.

## Design principles
- Desktop-first **1366–1440px**, responsive at 1024px and tablet/mobile. This is a data-dense wholesale warehouse UI: prioritize speed, numeric accuracy, scanning status and readability over visual flourishes.
- Component shape: border radius 10–14px for cards, 6–8px for data cells and buttons, subtle border and near-zero shadows. No glassmorphism, distracting gradients, over-saturated illustrations or unreadably pale charts.
- Typography: modern neutral system stack (`Inter` if licensed / installed from CDN or a system sans fallback). Do not bundle proprietary typefaces. Numeric values use `font-variant-numeric: tabular-nums`.
- Semantic green for success, yellow/amber for warning, red for errors, blue for informational events; distinguish by icon and label.
- App shell: logo + **Pro Trading** text at upper left; authenticated role/account, quick search, scan health, notifications and warehouse switcher in top bar. Sidebar: Overview, Orders, Picking, Invoices, Products, Pricing, Inventory, Receiving, Vendors, Customers, Scan Center, Documents, Payments, Reports, Settings.
- Light theme first; design tokens must support future dark theme (not required in v1).

## Page-level blueprint
**Overview:** 4–6 KPIs (net sales, paid/outstanding, profit if verified costs, available inventory, active backorders, awaiting GRN approvals), compact graph, action queue, stockout alerts and recent operational events.
**Orders:** searchable/filterable dense data grid; order ID, customer, order date, ordered/picked/dispatched/backordered quantities, status, source scan. Open immutable original scan side panel.
**Picking:** large high-contrast numeric inputs, shortage “NO”, packed qty, reason, picker, QR reference. Keyboard-first, minimal scrolling.
**Invoices:** sortable invoices, separate draft/final views, repeated product with different box weights, unit, qty, pounds/kg, rate, tax and total. Finalized invoice is immutable and matches supplied print-PDF template once provided.
**Pricing:** A–Z product list, customer overrides, CSV upload preview, comparison to previous rate, versions and approvals; edits must show clear diff.
**Receiving:** vendor/shipment/multi-document review, bill/waybill/date, declared vs physically received vs damaged, batch, cost, currency, signed approval before stock posting.
**Scan Center:** front-and-center scanner health plus `Scan Order` / `Scan Vendor Documents`, manual upload fallback, asynchronous OCR progress and side-by-side original + editable recognized values with confidence badges.
**Inventory:** lot-level boxes/pieces/lbs, reserved vs available, movement history, real-time updates; explicitly label estimated vs verified weight.
**Analytics:** global date/product/customer/vendor/warehouse filters, trusted KPI definitions, drilldowns to invoices and movements; never fabricate profit.
**Documents:** folder-like browsing plus relational filters, download/print, scan original and finalized PDF distinction, Drive sync indicator.

## Interaction contract
- Loading, empty, error, offline scanner, 409 concurrency conflict, OCR uncertainty and email failure states are first-class designs.
- Never make the user re-enter a full document because one OCR field is unclear. Always allow line-level correction and fast manual entry.
- Destructive or financial posting actions require explicit review/confirmation and server-side authorization; never rely only on hidden UI buttons.
- Keyboard workflows: `/` search where safe; tabular arrow navigation; focus-visible; multi-row edit and paste with validation.
- Tables must handle 100s–1000s of rows by server pagination / virtualization, with sticky headers, column visibility and accessible filters.
- Print UI must follow the **existing customer-provided PDF template**, not the web dashboard aesthetics. This template is currently pending upload and is a gate before final PDF printing implementation.

## Implementation instructions for Claude Code
1. Read this file and `design/tokens.json` **before building frontend components**.
2. Create typed design-token exports and Tailwind/shadcn semantic variants; no untracked hex literals in React files.
3. Build the app shell and 4 reference screens first: Overview, Orders, Invoice Editor, Receiving. Get layout sign-off before 10+ more pages.
4. Use representative **explicitly labelled fake sample data** for UI demos; data must be swapped to real API bindings in production.
5. Document actual live website colors/asset findings in `design/BRAND_VERIFICATION.md` once browser access is available. Do not claim provisional palette is exact.
6. Include visual regression screenshots at 1440x900, 1366x768, 1024x768 and 390x844, plus accessibility tests. Respect reduced motion.
7. Verify all company assets' usage rights. Do not fetch remote artwork at build/runtime without permission; store approved copies locally.

## Acceptance criteria
- Every page uses the same semantic brand tokens, logo treatment, typography, tables and navigation behavior.
- There is no misleading card transaction or bank-link UI unless that capability is implemented and authenticated.
- Numeric input / invoice totals are unambiguous and always formatted with CAD units, and whole vs partial boxes and lb/kg labels are explicit.
- Focus functional fields survive migration/mapping; the legacy appearance does not.
- Invoice PDFs visually match the supplied template after it is uploaded and approved; before then only a placeholder print adapter is permitted.
