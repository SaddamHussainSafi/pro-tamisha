# Task 00b — Pro Trading Design System (after foundation, before page implementation)

Read: `CLAUDE.md`, `design/UI_DESIGN_REFERENCE.md`, `design/tokens.json`, `design/BRAND_VERIFICATION.md`, and all five files under `design/references/`.

1. Verify official logo and website colors from https://tamishatrading.com/ using browser devtools or accessible CSS. Record exact verified values before updating tokens. Obtain permission and download approved official assets locally to `apps/web/public/brand/`; never hotlink.
2. Implement global CSS custom properties for colors, spacing, radius, typography and semantic statuses; integrate with Tailwind/shadcn/ui.
3. Build reusable components: AppShell, Sidebar, Topbar, KPI Card, FilterBar, DataGrid, StatusChip, NumericInput, SplitDocumentReview, ConfirmDialog and ErrorState.
4. Implement mock-only screens: Overview, Order List, Invoice Editor and Vendor Receiving. Match the *structural qualities* of the supplied screenshots, not their orange/purple colors or logos.
5. Include responsiveness, keyboard navigation, accessibility and realistic error/empty/loading states.
6. Add screenshot/Playwright visual checks at 1440x900, 1366x768, 1024x768, 390x844.
7. Do not implement PDF invoices using the dashboard look; wait for real user-supplied print template.

Done when design tokens, navigation, four reference screens, tests, and screenshots are reviewed. Do not claim visual sign-off without review.
