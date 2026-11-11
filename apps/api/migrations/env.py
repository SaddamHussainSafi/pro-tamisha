"""Alembic env. DB URL comes from Settings (DATABASE_URL), never from alembic.ini."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from pro_trading import models, notifications  # noqa: F401  (register tables)
from pro_trading.config import get_settings
from pro_trading.db import Base
from pro_trading.gmail import models as _gmail  # noqa: F401
from pro_trading.order_connect import models as _oc  # noqa: F401

config = context.config
url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
