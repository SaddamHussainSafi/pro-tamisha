"""Official sales invoice template (D-12): letterhead, bill-to / ship-to and vendor numbers.

- `companies`: letterhead printed on every invoice (address, phone, fax, email), the business
  number ("Bus #") and the salesperson the Create invoice form starts with. Existing companies
  get Tamisha Trading Inc.'s letterhead from the owner's paper invoice (single-company system).
- `customers`: bill-to address, ship-to name and address (blank = same as bill-to), fax and
  the vendor number the customer gave Tamisha ("Vendor #", e.g. No Frills 1040118).

Issued invoices are unchanged: they print from their stored snapshot and stored PDF bytes.

Rollback: `alembic downgrade 0020` drops the new columns.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMPANY = (
    ("address", sa.Text()),
    ("phone", sa.String(32)),
    ("fax", sa.String(32)),
    ("email", sa.Text()),
    ("business_no", sa.String(32)),
    ("default_salesperson", sa.String(100)),
)
CUSTOMER = (
    ("bill_to_address", sa.Text()),
    ("ship_to_name", sa.Text()),
    ("ship_to_address", sa.Text()),
    ("fax", sa.String(32)),
    ("vendor_no", sa.String(32)),
)


def upgrade() -> None:
    for name, type_ in COMPANY:
        op.add_column(
            "companies", sa.Column(name, type_, nullable=False, server_default=sa.text("''"))
        )
    for name, type_ in CUSTOMER:
        op.add_column("customers", sa.Column(name, type_, nullable=True))
    op.execute(
        sa.text(
            "UPDATE companies SET address = :address, phone = :phone, fax = :fax, "
            "email = :email, business_no = :bn, default_salesperson = :sp"
        ).bindparams(
            address="140 Milner Ave., Units 34-36\nScarborough, ON  M1S 3R3",
            phone="416-332-8853",
            fax="416-332-1406",
            email="tamishatradinginc@hotmail.com",
            bn="803444827",
            sp="Andy Narine",
        )
    )


def downgrade() -> None:
    for name, _ in CUSTOMER:
        op.drop_column("customers", name)
    for name, _ in COMPANY:
        op.drop_column("companies", name)
