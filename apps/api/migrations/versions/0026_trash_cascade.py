"""Trash cascade: what depends on a record goes to the Trash with it.

The Super Admin's delete now takes the rows that only exist for a record into the same Trash
item (a scan's OCR reads, a customer's uninvoiced orders) and clears loose links that are put
back on restore (customers of a deleted group). `trash.py` does this only while
`pro_trading.trash = on` for the transaction; the guard triggers below let those statements
through in that mode and nowhere else:

- DELETE in Trash mode: OCR reads, lines and corrections; sales order lines (any order
  status) and order changes; prices in published price books; sales orders in any status;
  converted website entries. Foreign keys from invoices, payments and the stock ledger still
  refuse an order or line that was invoiced.
- INSERT in Trash mode: prices back into a published price book (restore).
- UPDATE in Trash mode, only when nothing but the named link column changes: the order an OCR
  read created; the product an OCR line read; the picking sheet an order change came from;
  the product on a website order item; the price book a draft was based on.

Invoices, invoice lines/PDFs, payments, allocations, supplier payments, stock movements, lots,
balances, receiving, shipments and the audit log are not touched.

Rollback: `alembic downgrade 0025` puts the previous triggers back.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRASH_ON = "coalesce(current_setting('pro_trading.trash', true), '') = 'on'"


def _only(*cols: str) -> str:
    """Nothing but these columns changed."""
    arr = "ARRAY[" + ",".join(f"'{c}'" for c in cols) + "]"
    return f"(to_jsonb(NEW) - {arr}::text[]) = (to_jsonb(OLD) - {arr}::text[])"


def _bypass(cond: str) -> str:
    return f"WHEN (NOT ({TRASH_ON} AND {cond})) "


# (table, function, old trigger(s) to drop, new triggers: (name, events, WHEN clause or ""))
CHANGES: list[tuple[str, str, list[str], list[tuple[str, str, str]]]] = [
    (
        "ocr_extractions",
        "ocr_extraction_guard",
        ["ocr_extractions_guard"],
        [
            ("ocr_extractions_guard", "UPDATE", _bypass(_only("sales_order_id"))),
            ("ocr_extractions_guard_delete", "DELETE", _bypass("true")),
        ],
    ),
    (
        "ocr_fields",
        "ocr_field_guard",
        ["ocr_fields_guard"],
        [
            ("ocr_fields_guard", "UPDATE", _bypass(_only("read_product_id", "final_product_id"))),
            ("ocr_fields_guard_delete", "DELETE", _bypass("true")),
        ],
    ),
    (
        "ocr_corrections",
        "ocr_correction_frozen",
        ["ocr_corrections_frozen"],
        [
            ("ocr_corrections_frozen", "UPDATE", ""),
            ("ocr_corrections_frozen_delete", "DELETE", _bypass("true")),
        ],
    ),
    (
        "sales_order_lines",
        "sales_order_line_guard",
        ["sales_order_lines_guard"],
        [
            ("sales_order_lines_guard", "UPDATE", ""),
            ("sales_order_lines_guard_delete", "DELETE", _bypass("true")),
        ],
    ),
    (
        "sales_order_amendments",
        "sales_order_amendment_guard",
        ["sales_order_amendments_guard"],
        [
            ("sales_order_amendments_guard", "UPDATE", _bypass(_only("pick_attempt_id"))),
            ("sales_order_amendments_guard_delete", "DELETE", _bypass("true")),
        ],
    ),
    (
        "price_entries",
        "price_reject_published_change",
        ["price_entries_immutable"],
        [
            ("price_entries_immutable", "UPDATE", ""),
            ("price_entries_immutable_insert", "INSERT", _bypass("true")),
            ("price_entries_immutable_delete", "DELETE", _bypass("true")),
        ],
    ),
    (
        "price_book_versions",
        "price_reject_published_change",
        ["price_book_versions_immutable"],
        [
            ("price_book_versions_immutable", "UPDATE", _bypass(_only("based_on_id"))),
            ("price_book_versions_immutable_delete", "DELETE", ""),
        ],
    ),
    (
        "order_form_submission_items",
        "oc_append_only",
        ["order_form_submission_items_guard"],
        [("order_form_submission_items_guard", "UPDATE", _bypass(_only("product_id")))],
    ),
    (
        "order_form_submissions",
        "oc_submission_guard",
        ["order_form_submissions_guard_delete"],
        [("order_form_submissions_guard_delete", "DELETE", _bypass("true"))],
    ),
    (
        "sales_orders",
        "sales_order_guard",
        ["sales_orders_guard_delete"],
        [("sales_orders_guard_delete", "DELETE", _bypass("true"))],
    ),
]

# What 0020 and earlier had, for the downgrade.
BEFORE: list[tuple[str, str, list[str], list[tuple[str, str, str]]]] = [
    (
        "ocr_extractions",
        "ocr_extraction_guard",
        ["ocr_extractions_guard", "ocr_extractions_guard_delete"],
        [("ocr_extractions_guard", "UPDATE OR DELETE", "")],
    ),
    (
        "ocr_fields",
        "ocr_field_guard",
        ["ocr_fields_guard", "ocr_fields_guard_delete"],
        [("ocr_fields_guard", "UPDATE OR DELETE", "")],
    ),
    (
        "ocr_corrections",
        "ocr_correction_frozen",
        ["ocr_corrections_frozen", "ocr_corrections_frozen_delete"],
        [("ocr_corrections_frozen", "UPDATE OR DELETE", "")],
    ),
    (
        "sales_order_lines",
        "sales_order_line_guard",
        ["sales_order_lines_guard", "sales_order_lines_guard_delete"],
        [("sales_order_lines_guard", "UPDATE OR DELETE", "")],
    ),
    (
        "sales_order_amendments",
        "sales_order_amendment_guard",
        ["sales_order_amendments_guard", "sales_order_amendments_guard_delete"],
        [("sales_order_amendments_guard", "UPDATE OR DELETE", "")],
    ),
    (
        "price_entries",
        "price_reject_published_change",
        [
            "price_entries_immutable",
            "price_entries_immutable_insert",
            "price_entries_immutable_delete",
        ],
        [("price_entries_immutable", "INSERT OR UPDATE OR DELETE", "")],
    ),
    (
        "price_book_versions",
        "price_reject_published_change",
        ["price_book_versions_immutable", "price_book_versions_immutable_delete"],
        [("price_book_versions_immutable", "UPDATE OR DELETE", "")],
    ),
    (
        "order_form_submission_items",
        "oc_append_only",
        ["order_form_submission_items_guard"],
        [("order_form_submission_items_guard", "UPDATE", "")],
    ),
    (
        "order_form_submissions",
        "oc_submission_guard",
        ["order_form_submissions_guard_delete"],
        [
            (
                "order_form_submissions_guard_delete",
                "DELETE",
                _bypass("OLD.status <> 'CONVERTED'"),
            )
        ],
    ),
    (
        "sales_orders",
        "sales_order_guard",
        ["sales_orders_guard_delete"],
        [("sales_orders_guard_delete", "DELETE", _bypass("OLD.status = 'DRAFT'"))],
    ),
]


def _apply(changes: list[tuple[str, str, list[str], list[tuple[str, str, str]]]]) -> None:
    for table, fn, drop, create in changes:
        for trg in drop:
            op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {table}")
        for trg, events, when in create:
            op.execute(
                f"CREATE TRIGGER {trg} BEFORE {events} ON {table} FOR EACH ROW "
                f"{when}EXECUTE FUNCTION {fn}()"
            )


def upgrade() -> None:
    _apply(CHANGES)


def downgrade() -> None:
    _apply(BEFORE)
