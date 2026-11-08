"""More scan document types: purchase order, delivery note, packing list, receipt.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = (
    "'SALES_ORDER','PICK_SHEET','VENDOR_DOCUMENT','PURCHASE_ORDER','DELIVERY_NOTE',"
    "'PACKING_LIST','RECEIPT','OTHER'"
)
OLD = "'SALES_ORDER','PICK_SHEET','VENDOR_DOCUMENT','OTHER'"


def upgrade() -> None:
    op.drop_constraint("job_type_valid", "scan_jobs", type_="check")
    op.create_check_constraint("job_type_valid", "scan_jobs", f"job_type IN ({NEW})")


def downgrade() -> None:
    op.drop_constraint("job_type_valid", "scan_jobs", type_="check")
    op.create_check_constraint("job_type_valid", "scan_jobs", f"job_type IN ({OLD})")
