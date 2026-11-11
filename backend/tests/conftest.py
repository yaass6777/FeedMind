"""Shared fixtures for PostgreSQL integration tests against ``feedmind_test`` ONLY.

Safety rules enforced here:
- Tests are skipped unless ``FEEDMIND_RUN_INTEGRATION=1``.
- The URL is ``FEEDMIND_TEST_DATABASE_URL`` if set; otherwise the development
  URL's credentials with the database name *explicitly replaced* by
  ``feedmind_test``. There is never a fallback to the development database.
- Both the URL and the live ``current_database()`` must be exactly
  ``feedmind_test``, or the test session fails before touching anything.
- Each test runs inside a transaction that is always rolled back.
Credentials are never printed: only the database name appears in messages.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import URL, make_url

TEST_DATABASE_NAME = "feedmind_test"
BACKEND_DIR = Path(__file__).resolve().parents[1]


def migration_head() -> str:
    """The single Alembic head revision; feedmind_test must be migrated to it."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    (head,) = ScriptDirectory.from_config(
        Config(str(BACKEND_DIR / "alembic.ini"))
    ).get_heads()
    return head


EXPECTED_REVISION = migration_head()


def _resolve_test_database_url() -> URL:
    from app.core.config import ROOT_ENV_FILE, Settings

    explicit = os.environ.get("FEEDMIND_TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
    else:
        development = Settings(_env_file=ROOT_ENV_FILE).database_url.get_secret_value()
        url = make_url(development).set(database=TEST_DATABASE_NAME)
    if url.database != TEST_DATABASE_NAME:
        pytest.fail(
            f"Refusing to run: integration database is {url.database!r}, "
            f"not {TEST_DATABASE_NAME!r}."
        )
    return url


@pytest.fixture(scope="session")
def test_db_engine() -> Iterator[Engine]:
    """Engine for feedmind_test, built with the application's engine factory."""
    if os.environ.get("FEEDMIND_RUN_INTEGRATION") != "1":
        pytest.skip("set FEEDMIND_RUN_INTEGRATION=1 with PostgreSQL running to enable")

    from app.core.config import ROOT_ENV_FILE, Settings
    from app.db.session import create_db_engine

    url = _resolve_test_database_url()
    settings = Settings(
        _env_file=ROOT_ENV_FILE,
        database_url=url.render_as_string(hide_password=False),
    )
    engine = create_db_engine(settings)
    try:
        with engine.connect() as connection:
            current = connection.execute(text("SELECT current_database()")).scalar()
            if current != TEST_DATABASE_NAME:
                pytest.fail(f"Refusing to run: connected to {current!r}.")
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
            if revision != EXPECTED_REVISION:
                pytest.fail(
                    f"{TEST_DATABASE_NAME} is at revision {revision!r}; "
                    f"apply {EXPECTED_REVISION} before running integration tests."
                )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def test_db(test_db_engine: Engine) -> Iterator[Connection]:
    """A connection whose transaction is always rolled back (no leftover rows)."""
    with test_db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
