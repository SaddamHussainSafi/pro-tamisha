"""Create the first Super Admin. There is no public sign-up: this command is the only way in.

Usage (on the server, after `alembic upgrade head`):

    PYTHONPATH=src uv run python -m pro_trading.create_super_admin \\
        --company "Tamisha Trading Inc." --email owner@company.example --name "Owner Name"

The password is typed twice at a hidden prompt (or read from stdin with --password-stdin, e.g.
from a secrets manager). It is never a command-line argument, never printed and never logged.
Refuses to run when the company already has an active Super Admin: from then on people are
invited from Settings › User management. Creates the company, its six roles and its first
warehouse if they don't exist yet.
"""

import argparse
import getpass
import re
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit
from .bootstrap import ensure_default_roles
from .db import get_sessionmaker
from .models import Company, Employee, EmployeeRole, Role, Warehouse
from .permissions import SUPER_ADMIN
from .schemas import EMAIL_RE
from .security import hash_password, password_problem


def _read_password(from_stdin: bool, email: str) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Password for the Super Admin: ")
        if getpass.getpass("Same password again: ") != pw:
            raise SystemExit("The two passwords don't match.")
    if len(pw) > 128:
        raise SystemExit("Use at most 128 characters.")
    if problem := password_problem(email, pw):
        raise SystemExit(problem + ".")
    return pw


def create(
    db: Session,
    *,
    company_name: str,
    email: str,
    name: str,
    password: str,
    warehouse_code: str = "MAIN",
    warehouse_name: str = "Main Warehouse",
) -> Employee:
    """Create (or reuse) the company and add its first Super Admin. Raises ValueError when it
    already has an active Super Admin or the email is taken."""
    if not re.match(EMAIL_RE, email) or len(email) > 320:
        raise ValueError("Enter a valid email")
    if not name.strip():
        raise ValueError("Enter a name")
    co = db.scalar(select(Company).where(Company.legal_name == company_name).with_for_update())
    if co is None:
        co = Company(legal_name=company_name)
        db.add(co)
        db.flush()
    roles = ensure_default_roles(db, co.id)
    existing = db.scalar(
        select(func.count())
        .select_from(Employee)
        .join(EmployeeRole, EmployeeRole.employee_id == Employee.id)
        .join(Role, Role.id == EmployeeRole.role_id)
        .where(Employee.company_id == co.id, Employee.active.is_(True), Role.code == SUPER_ADMIN)
    )
    if existing:
        raise ValueError(
            "This company already has a Super Admin. Invite people from Settings › User "
            "management instead."
        )
    if db.scalar(select(Employee.id).where(func.lower(Employee.email) == email.lower())):
        raise ValueError("Someone already signs in with this email")
    if db.scalar(select(Warehouse.id).where(Warehouse.company_id == co.id)) is None:
        db.add(Warehouse(company_id=co.id, code=warehouse_code, name=warehouse_name))
    emp = Employee(
        company_id=co.id,
        email=email,
        display_name=name.strip(),
        password_hash=hash_password(password),
    )
    db.add(emp)
    db.flush()
    db.add(EmployeeRole(employee_id=emp.id, role_id=roles[SUPER_ADMIN].id))
    audit.record(
        db,
        action="employee.bootstrap_super_admin",
        success=True,
        company_id=co.id,
        target_type="employee",
        target_id=str(emp.id),
        detail={"email": email},
    )
    db.flush()
    return emp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--company", required=True, help="Legal company name")
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True, help="The person's display name")
    ap.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")
    ap.add_argument("--warehouse-code", default="MAIN")
    ap.add_argument("--warehouse-name", default="Main Warehouse")
    a = ap.parse_args(argv)
    email = a.email.strip()
    password = _read_password(a.password_stdin, email)
    try:
        with get_sessionmaker()() as db, db.begin():
            create(
                db,
                company_name=a.company.strip(),
                email=email,
                name=a.name,
                password=password,
                warehouse_code=a.warehouse_code,
                warehouse_name=a.warehouse_name,
            )
    except ValueError as exc:
        print(f"Not created: {exc}", file=sys.stderr)
        return 1
    print(f"Super Admin {email} created. Sign in at the web app; turn on two-step sign-in there.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
