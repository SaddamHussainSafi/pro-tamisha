"""User management and authentication: invitations, password resets, optional 2FA.

- `auth_tokens`: single-use invitation and password-reset links. Only the SHA-256 of the
  token is stored; the raw token exists in the emailed link alone.
- `mfa_recovery_codes`: one-time 2FA recovery codes, stored as SHA-256 (80-bit random codes).
- `employees.last_login_at`, `employees.password_changed_at`, `auth_sessions.ip_address`.
- Roles renamed to the owner's six (2026-11-09): Owner / Admin -> Super Admin, Manager -> Admin,
  Sales / Operator -> Sales, Finance -> Accounting; Warehouse unchanged; new read-only Viewer.
  Role codes and every existing grant are unchanged, so nobody gains or loses access.
- Two-step sign-in is optional for everyone now: `roles.privileged` no longer forces MFA
  (set false). Requiring it for everyone is the `require_two_step` feature flag (off).

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMES = {
    "OWNER_ADMIN": ("Super Admin", "Owner / Admin", True),
    "MANAGER": ("Admin", "Manager", True),
    "SALES_OPERATOR": ("Sales", "Sales / Operator", False),
    "WAREHOUSE": ("Warehouse", "Warehouse", False),
    "FINANCE": ("Accounting", "Finance", True),
}
VIEWER_PERMS = ("warehouse.read", "catalog.read", "sales_order.read", "inventory.read")


def _ts(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.add_column("employees", _ts("last_login_at"))
    op.add_column("employees", _ts("password_changed_at"))
    op.add_column("auth_sessions", sa.Column("ip_address", sa.Text(), nullable=True))

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.UUID(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _ts("expires_at", nullable=False),
        _ts("used_at"),
        _ts("revoked_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_sha256", name="uq_auth_tokens_token"),
        sa.CheckConstraint("purpose IN ('INVITE','PASSWORD_RESET')", name="purpose_valid"),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )
    op.create_index(
        "ix_auth_tokens_open",
        "auth_tokens",
        ["employee_id", "purpose"],
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "employee_id",
            sa.UUID(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _ts("used_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "code_sha256", name="uq_mfa_recovery_code"),
    )
    op.create_index("ix_mfa_recovery_codes_employee", "mfa_recovery_codes", ["employee_id"])

    # Sign-in history and security events per person.
    op.create_index("ix_audit_actor_created", "audit_events", ["actor_id", "created_at"])

    for code, (name, _, _) in NAMES.items():
        op.execute(
            sa.text("UPDATE roles SET name = :n, privileged = false WHERE code = :c").bindparams(
                n=name, c=code
            )
        )
    # Viewer for every existing company (new companies get it from bootstrap).
    for perm in VIEWER_PERMS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (code, description) VALUES (:c, :c) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(c=perm)
        )
    op.execute(
        "INSERT INTO roles (company_id, code, name, privileged) "
        "SELECT id, 'VIEWER', 'Viewer', false FROM companies "
        "ON CONFLICT (company_id, code) DO NOTHING"
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code) "
            "SELECT r.id, p FROM roles r, unnest(CAST(:perms AS text[])) AS p "
            "WHERE r.code = 'VIEWER' ON CONFLICT DO NOTHING"
        ).bindparams(perms=list(VIEWER_PERMS))
    )


def downgrade() -> None:
    # Viewer assignments are removed with the role (downgrade only).
    op.execute(
        "DELETE FROM employee_roles WHERE role_id IN (SELECT id FROM roles WHERE code = 'VIEWER')"
    )
    op.execute(
        "DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'VIEWER')"
    )
    op.execute("DELETE FROM roles WHERE code = 'VIEWER'")
    for code, (_, old, privileged) in NAMES.items():
        op.execute(
            sa.text("UPDATE roles SET name = :n, privileged = :p WHERE code = :c").bindparams(
                n=old, p=privileged, c=code
            )
        )
    op.drop_index("ix_audit_actor_created", table_name="audit_events")
    op.drop_index("ix_mfa_recovery_codes_employee", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
    op.drop_index("ix_auth_tokens_open", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_column("auth_sessions", "ip_address")
    op.drop_column("employees", "password_changed_at")
    op.drop_column("employees", "last_login_at")
