"""Alembic migration environment for FeedMind.

Connection settings come from the application itself:
- The URL is the validated ``DATABASE_URL`` from ``app.core.config``; it is never
  written to ``alembic.ini`` or Alembic's config object, so it cannot leak via
  config dumps.
- Online migrations use the same ``create_db_engine()`` factory as the app, so
  there is a single engine configuration (psycopg driver, pool_pre_ping, no echo).
- ``target_metadata`` is ``Base.metadata``; importing ``app.models`` registers
  every ORM model on it for autogenerate.

Callers (e.g. tests) may pre-set ``config.attributes``:
- ``"connection"``: an existing SQLAlchemy Connection to run migrations on.
- ``"configure_logger"``: ``False`` to skip ``fileConfig`` logging setup.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.engine import URL, make_url

import app.models  # noqa: F401  (registers models on Base.metadata)
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_db_engine

config = context.config

if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

CONFIGURE_OPTIONS = {
    "target_metadata": target_metadata,
    # Detect column type and server default changes during autogenerate.
    "compare_type": True,
    "compare_server_default": True,
}


def get_database_url() -> URL:
    """Return the application's validated database URL (password kept inside URL)."""
    return make_url(get_settings().database_url.get_secret_value())


def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without connecting (``alembic ... --sql``)."""
    context.configure(
        url=get_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONFIGURE_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, **CONFIGURE_OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the database using the application's engine factory."""
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with_connection(connection)
        return

    engine = create_db_engine(get_settings())
    try:
        with engine.connect() as connection:
            _run_with_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
