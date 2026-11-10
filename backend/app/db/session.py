"""SQLAlchemy 2.x engine, session factory and FastAPI session dependency.

Nothing here connects to PostgreSQL or reads configuration at import time:
- ``get_engine()`` / ``get_session_factory()`` are built lazily on first call and
  cached, mirroring ``get_settings()``.
- ``create_engine()`` itself never opens a connection; the pool connects on the
  first query.
"""

from collections.abc import Iterator
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def create_db_engine(settings: Settings) -> Engine:
    """Build an Engine for ``settings.database_url`` without connecting."""
    return create_engine(
        make_url(settings.database_url.get_secret_value()),
        # Validate pooled connections before use, so connections dropped by a
        # PostgreSQL restart are replaced transparently.
        pool_pre_ping=True,
        # SQL echo stays off even in DEBUG to keep parameters out of logs.
        echo=False,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build the Session factory used for every request/unit of work."""
    return sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
    )


@cache
def get_engine() -> Engine:
    """Return the application's single, lazily created Engine."""
    return create_db_engine(get_settings())


@cache
def get_session_factory() -> sessionmaker[Session]:
    """Return the application's single, lazily created Session factory."""
    return create_session_factory(get_engine())


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield a Session and always close it afterwards.

    Closing returns the connection to the pool and rolls back any transaction
    that was not committed, including when the request raised an exception.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
