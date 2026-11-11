import io
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import ForeignKey, Integer, MetaData, String, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import app.models  # noqa: F401  (registers all models on Base.metadata)
from app.core import config
from app.core.config import Settings
from app.db import session as db_session
from app.db.base import NAMING_CONVENTION, Base

EXPECTED_TABLES = {
    "users",
    "sources",
    "articles",
    "categories",
    "user_sources",
    "article_sources",
    "article_categories",
    "user_preferences",
    "bookmarks",
    "reading_history",
    "feedback",
    "source_fetch_logs",
}

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
TEST_PASSWORD = "AlembicS3cretPw"
TEST_DB_URL = (
    f"postgresql+psycopg://alembic_user:{TEST_PASSWORD}@db.test:5432/feedmind_test"
)
CONFIG_ENV_VARS = (
    "APP_NAME",
    "APP_ENV",
    "DEBUG",
    "SECRET_KEY",
    "DATABASE_URL",
    "CORS_ORIGINS",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Isolated, unreachable test database settings; never the real root .env."""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SECRET_KEY", "a" * 40)
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    config.get_settings.cache_clear()
    yield monkeypatch
    config.get_settings.cache_clear()


def make_config(stdout: io.StringIO | None = None) -> Config:
    """Alembic config whose console and offline-SQL output go to ``stdout``."""
    buffer = stdout or io.StringIO()
    cfg = Config(str(ALEMBIC_INI), stdout=buffer, output_buffer=buffer)
    cfg.attributes["configure_logger"] = False  # keep pytest's logging intact
    return cfg


# --- Configuration loading and paths ------------------------------------------


def test_alembic_ini_loads() -> None:
    cfg = make_config()

    assert cfg.config_file_name == str(ALEMBIC_INI)
    assert cfg.get_main_option("script_location")


def test_script_location_resolves_to_backend_migrations() -> None:
    script = ScriptDirectory.from_config(make_config())

    assert Path(script.dir).resolve() == BACKEND_DIR / "migrations"
    assert (BACKEND_DIR / "migrations" / "env.py").is_file()
    assert (BACKEND_DIR / "migrations" / "script.py.mako").is_file()
    assert Path(script.versions).resolve() == BACKEND_DIR / "migrations" / "versions"


def test_paths_do_not_depend_on_working_directory(
    tmp_path: Path, isolated_env: pytest.MonkeyPatch
) -> None:
    isolated_env.chdir(tmp_path)

    script = ScriptDirectory.from_config(make_config())

    assert Path(script.dir).resolve() == BACKEND_DIR / "migrations"


INITIAL_REVISION = "1c74d9d04571"
SEED_CATEGORIES_REVISION = "61c116438233"


def test_migration_history_is_a_single_linear_chain() -> None:
    """Base: initial schema. Head: category seed data, directly on top of it."""
    script = ScriptDirectory.from_config(make_config())
    chain = [
        (revision.revision, revision.down_revision, revision.doc)
        for revision in script.walk_revisions()  # newest first
    ]

    assert script.get_bases() == [INITIAL_REVISION]
    assert script.get_heads() == [SEED_CATEGORIES_REVISION]
    assert chain == [
        (SEED_CATEGORIES_REVISION, INITIAL_REVISION, "seed_initial_categories"),
        (INITIAL_REVISION, None, "create_initial_database_schema"),
    ]


def test_alembic_ini_contains_no_database_url_or_credentials() -> None:
    cfg = make_config()
    raw = ALEMBIC_INI.read_text(encoding="utf-8")

    assert cfg.get_main_option("sqlalchemy.url") is None
    assert "postgresql" not in raw
    assert "@" not in raw


# --- env.py: database URL integration and safe execution ---------------------


def test_env_offline_mode_runs_without_database_or_leaks() -> None:
    """Offline (--sql) mode executes env.py fully without connecting."""
    stdout = io.StringIO()

    command.upgrade(make_config(stdout), "head", sql=True)

    output = stdout.getvalue()
    assert "BEGIN;" in output
    assert "COMMIT;" in output
    assert TEST_PASSWORD not in output


def run_env_capturing(capture) -> None:
    """Execute migrations/env.py in offline mode with a capturing migration fn."""
    cfg = make_config()
    script = ScriptDirectory.from_config(cfg)
    with EnvironmentContext(
        cfg, script, fn=capture, as_sql=True, destination_rev="head"
    ):
        script.run_env()


def test_env_uses_psycopg_dialect_from_settings_url() -> None:
    captured: dict[str, str] = {}

    def capture(rev: object, context: MigrationContext) -> list[object]:
        captured["dialect"] = context.dialect.name
        captured["driver"] = context.dialect.driver
        return []

    run_env_capturing(capture)

    assert captured == {"dialect": "postgresql", "driver": "psycopg"}


def test_env_reads_database_url_from_validated_settings(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """An invalid DATABASE_URL is rejected by app settings, without leaking it."""
    isolated_env.setenv("DATABASE_URL", f"mysql://alembic_user:{TEST_PASSWORD}@db/x")
    config.get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        command.upgrade(make_config(), "head", sql=True)

    assert "database_url" in str(exc_info.value)
    assert TEST_PASSWORD not in str(exc_info.value)


def test_env_error_does_not_leak_password(isolated_env: pytest.MonkeyPatch) -> None:
    """A failing online connection must not print the password."""
    stdout = io.StringIO()
    # Nothing listens on port 1; connect_timeout bounds the wait, since a dropped
    # (rather than refused) connection otherwise waits for the OS TCP timeout.
    isolated_env.setenv(
        "DATABASE_URL",
        f"postgresql+psycopg://alembic_user:{TEST_PASSWORD}@127.0.0.1:1/feedmind_test"
        "?connect_timeout=2",
    )
    config.get_settings.cache_clear()

    with pytest.raises(OperationalError) as exc_info:
        command.current(make_config(stdout))

    rendered = str(exc_info.value) + repr(exc_info.value) + stdout.getvalue()
    assert TEST_PASSWORD not in rendered


# --- Metadata compatibility ---------------------------------------------------


def test_target_metadata_is_declarative_base_metadata() -> None:
    captured: dict[str, object] = {}

    def capture(rev: object, context: MigrationContext) -> list[object]:
        captured.update(context.opts)
        return []

    run_env_capturing(capture)

    assert captured["target_metadata"] is Base.metadata
    assert captured["compare_type"] is True
    assert captured["compare_server_default"] is True


def test_base_metadata_has_naming_convention_and_all_model_tables() -> None:
    for key, template in NAMING_CONVENTION.items():
        assert Base.metadata.naming_convention[key] == template
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_naming_convention_applies_to_models_on_a_compatible_base() -> None:
    """A temporary model on a test-only base gets convention-generated names.

    Uses its own DeclarativeBase/registry so the application's real ``Base``
    (and its 12 mapped models) is never modified or disposed.
    """

    class _ProbeBase(DeclarativeBase):
        metadata = MetaData(naming_convention=NAMING_CONVENTION)

    try:

        class _ProbeModel(_ProbeBase):
            __tablename__ = "alembic_probe"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            code: Mapped[str] = mapped_column(String(10), unique=True)
            parent_id: Mapped[int] = mapped_column(ForeignKey("alembic_probe.id"))

        table = _ProbeBase.metadata.tables["alembic_probe"]
        constraint_names = {c.name for c in table.constraints}

        assert table.primary_key.name == "pk_alembic_probe"
        assert "uq_alembic_probe_code" in constraint_names
        assert "fk_alembic_probe_parent_id_alembic_probe" in constraint_names
        assert "alembic_probe" not in Base.metadata.tables
        assert set(Base.metadata.tables) == EXPECTED_TABLES
    finally:
        # Only the test-only registry is disposed; the real Base is untouched.
        _ProbeBase.registry.dispose()


# --- Integration (opt-in, read-only) ------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("FEEDMIND_RUN_INTEGRATION") != "1",
    reason="set FEEDMIND_RUN_INTEGRATION=1 with PostgreSQL running to enable",
)
def test_integration_autogenerate_compare_is_read_only(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """Connect to the real database, compare metadata, and change nothing.

    Uses MigrationContext + compare_metadata rather than ``alembic check``, which
    creates the alembic_version table as a side effect.
    """
    isolated_env.setitem(Settings.model_config, "env_file", config.ROOT_ENV_FILE)
    for name in ("SECRET_KEY", "DATABASE_URL", "CORS_ORIGINS"):
        isolated_env.delenv(name)
    config.get_settings.cache_clear()

    engine = db_session.create_db_engine(config.get_settings())
    try:
        with engine.connect() as connection:
            tables_before = sorted(inspect(connection).get_table_names())
            context = MigrationContext.configure(connection)

            current = context.get_current_revision()
            diffs = compare_metadata(context, Base.metadata)

            tables_after = sorted(inspect(connection).get_table_names())
            connection.rollback()
    finally:
        engine.dispose()

    # Reflection only: comparing must never change the database.
    assert tables_after == tables_before
    if "alembic_version" not in tables_before:
        assert current is None

    application_tables_present = EXPECTED_TABLES & set(tables_before)
    if not application_tables_present:
        # No migration applied yet: autogenerate would only add the 12 tables
        # (plus their indexes); nothing would be dropped or altered.
        added_tables = {diff[1].name for diff in diffs if diff[0] == "add_table"}
        assert added_tables == EXPECTED_TABLES
        assert {diff[0] for diff in diffs} <= {"add_table", "add_index"}
    else:
        # Once migrated, the database must match the models exactly.
        assert diffs == []
