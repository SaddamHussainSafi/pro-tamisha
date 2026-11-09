"""Permission catalog and default role grants (docs/architecture/04-security-and-scanner-bridge.md).

The catalog is code-owned and synced into the `permissions` table by migration/seed.
Later modules register codes here before implementing their handlers.
"""

from typing import Final

PERMISSIONS: Final[dict[str, str]] = {
    # foundation
    "company.manage": "Edit company settings",
    "employee.manage": "Create/disable employees, grant roles, revoke sessions",
    "warehouse.read": "View warehouses",
    "warehouse.manage": "Create/edit warehouses",
    "feature_flag.manage": "Toggle feature flags",
    "audit.read": "Read audit log",
    # declared now so role matrix is reviewable; handlers arrive in later modules
    "catalog.read": "View products/customers/vendors",
    "catalog.manage": "Edit products/customers/vendors",
    "price.read": "View price books",
    "price.draft": "Draft price versions",
    "price.approve": "Approve/activate price versions",
    "sales_order.read": "View sales orders and picking attempts",
    "sales_order.manage": "Create/edit/confirm sales orders, issue picking sheets",
    "sales_order.close": "Cancel remaining quantities / close orders with shortage",
    "pick.record": "Enter picked quantities and shortages",
    "pick.verify": "Verify warehouse pick",
    "invoice.read": "View issued invoices and their PDFs",
    "invoice.prepare": "Prepare invoice drafts",
    "invoice.issue": "Issue final invoice (assigns number, posts stock)",
    "invoice.correct": "Corrective documents for issued invoices",
    "inventory.read": "View stock",
    "inventory.adjust": "Post inventory adjustments, opening balances, damage, returns, counts",
    "inventory.count": "Submit physical stock counts (posted by inventory.adjust)",
    "inventory.reserve": "Reserve/release stock for orders",
    "inventory.reverse": "Reverse a posted stock movement (compensating entry)",
    "inventory.reconcile": "Rebuild cached balances from the stock ledger",
    "grn.record": "Record receiving counts",
    "grn.approve": "Approve and post GRN",
    "scan.create": "Start scans / upload documents",
    "scan.review": "Review OCR extractions",
    "scan.device.manage": "Pair, approve and revoke office scanner bridges",
    "payment.record": "Record reported payments",
    "payment.verify": "Verify/apply payments",
    "report.read": "View analytics",
    "document.read": "View the document archive and its Drive sync status",
    "document.manage": "File signed delivery proofs and retry Drive sync",
    "supplier.ledger.read": "View supplier ledgers: billed, paid, returned",
    "supplier_payment.record": "Record and void payments made to suppliers",
}

ALL: Final = frozenset(PERMISSIONS)

# Who may manage users, roles, sessions and security settings: Super Admin only.
USER_ADMIN_PERMISSIONS: Final = frozenset(
    {"employee.manage", "feature_flag.manage", "company.manage"}
)

# role_code -> (display name, description, permissions). Codes are stable; names are the owner's
# six roles (2026-11-09, migration 0017). The matrix is fixed: access changes by assigning roles.
# Two-step sign-in is optional for every role (the `require_two_step` flag makes it mandatory).
DEFAULT_ROLES: Final[dict[str, tuple[str, str, frozenset[str]]]] = {
    "OWNER_ADMIN": (
        "Super Admin",
        "Full access. The only role that adds users, assigns roles, suspends or reactivates "
        "accounts, signs people out and manages security settings.",
        ALL,
    ),
    "MANAGER": (
        "Admin",
        "Runs day-to-day operations: products, prices, orders, invoices, stock, receiving, "
        "scans and reports. Cannot add users or manage roles.",
        frozenset(
            {
                "warehouse.read",
                "audit.read",
                "catalog.read",
                "catalog.manage",
                "price.read",
                "price.draft",
                "price.approve",
                "sales_order.read",
                "sales_order.manage",
                "sales_order.close",
                "pick.verify",
                "invoice.read",
                "invoice.prepare",
                "invoice.issue",
                "invoice.correct",
                "inventory.read",
                "inventory.adjust",
                "inventory.count",
                "inventory.reserve",
                "inventory.reverse",
                "grn.record",
                "grn.approve",
                "scan.create",
                "scan.review",
                "scan.device.manage",
                "payment.record",
                "report.read",
                "document.read",
                "document.manage",
                "supplier.ledger.read",
            }
        ),
    ),
    "SALES_OPERATOR": (
        "Sales",
        "Sales orders, invoice drafts and customer records; records payments customers report.",
        frozenset(
            {
                "warehouse.read",
                "catalog.read",
                "price.read",
                "sales_order.read",
                "sales_order.manage",
                "invoice.read",
                "invoice.prepare",
                "inventory.read",
                "inventory.reserve",
                "scan.create",
                "scan.review",
                "payment.record",
                "document.read",
                "document.manage",
            }
        ),
    ),
    "WAREHOUSE": (
        "Warehouse",
        "Picking, receiving counts and stock counts.",
        frozenset(
            {
                "warehouse.read",
                "catalog.read",
                "inventory.read",
                "inventory.count",
                "sales_order.read",
                "pick.record",
                "grn.record",
            }
        ),
    ),
    "FINANCE": (
        "Accounting",
        "Payments, receivables, supplier ledgers and financial reports.",
        frozenset(
            {
                "warehouse.read",
                "audit.read",
                "catalog.read",
                "price.read",
                "inventory.read",
                "sales_order.read",
                "invoice.read",
                "payment.record",
                "payment.verify",
                "report.read",
                "document.read",
                "document.manage",
                "supplier.ledger.read",
                "supplier_payment.record",
            }
        ),
    ),
    "VIEWER": (
        "Viewer",
        "Read-only: products, customers, orders and stock. Cannot change anything.",
        frozenset({"warehouse.read", "catalog.read", "sales_order.read", "inventory.read"}),
    ),
}

assert all(p <= ALL for _, _, p in DEFAULT_ROLES.values()), "unknown permission in role"
assert all(
    not (p & USER_ADMIN_PERMISSIONS) for c, (_, _, p) in DEFAULT_ROLES.items() if c != "OWNER_ADMIN"
), "only Super Admin may hold user-administration permissions"
SUPER_ADMIN: Final = "OWNER_ADMIN"
