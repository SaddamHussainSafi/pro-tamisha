"""Sync code-owned permission catalog and per-company default roles (idempotent)."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Permission, Role, RolePermission
from .permissions import DEFAULT_ROLES, PERMISSIONS


def sync_permissions(db: Session) -> None:
    for code, desc in PERMISSIONS.items():
        db.execute(
            insert(Permission)
            .values(code=code, description=desc)
            .on_conflict_do_update(index_elements=["code"], set_={"description": desc})
        )


def ensure_default_roles(db: Session, company_id: uuid.UUID) -> dict[str, Role]:
    sync_permissions(db)
    roles: dict[str, Role] = {}
    for code, (name, _description, perms) in DEFAULT_ROLES.items():
        role = db.scalar(select(Role).where(Role.company_id == company_id, Role.code == code))
        if role is None:
            role = Role(company_id=company_id, code=code, name=name)
            db.add(role)
            db.flush()
        for p in perms:
            db.execute(
                insert(RolePermission)
                .values(role_id=role.id, permission_code=p)
                .on_conflict_do_nothing()
            )
        roles[code] = role
    db.flush()
    return roles
