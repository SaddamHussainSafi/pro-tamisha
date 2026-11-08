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
}

ALL: Final = frozenset(PERMISSIONS)

# role_code -> (display name, privileged => MFA required, permissions)
DEFAULT_ROLES: Final[dict[str, tuple[str, bool, frozenset[str]]]] = {
    "OWNER_ADMIN": ("Owner / Admin", True, ALL),
    "MANAGER": (
        "Manager",
        True,
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
            }
        ),
    ),
    "SALES_OPERATOR": (
        "Sales / Operator",
        False,
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
        False,
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
        "Finance",
        True,
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
            }
        ),
    ),
}

assert all(p <= ALL for _, _, p in DEFAULT_ROLES.values()), "unknown permission in role"
