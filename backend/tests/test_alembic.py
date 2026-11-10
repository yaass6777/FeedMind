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
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from app.core import config
from app.core.config import Settings
from app.db import session as db_session
from app.db.base import NAMING_CONVENTION, Base

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


def test_no_migrations_exist_yet() -> None:
    script = ScriptDirectory.from_config(make_config())

    assert script.get_heads() == []
    assert list(script.walk_revisions()) == []


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


def test_base_metadata_has_naming_convention_and_no_tables_yet() -> None:
    for key, template in NAMING_CONVENTION.items():
        assert Base.metadata.naming_convention[key] == template
    assert Base.metadata.tables == {}


def test_models_registered_on_base_are_visible_to_autogenerate() -> None:
    """A model subclassing Base appears in target_metadata with convention names."""
    from sqlalchemy import Integer, String
    from sqlalchemy.orm import Mapped, mapped_column

    class _ProbeModel(Base):
        __tablename__ = "alembic_probe"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        code: Mapped[str] = mapped_column(String(10), unique=True)

    try:
        table = Base.metadata.tables["alembic_probe"]
        assert table.primary_key.name == "pk_alembic_probe"
        unique_names = {c.name for c in table.constraints if c is not table.primary_key}
        assert "uq_alembic_probe_code" in unique_names
    finally:
        Base.metadata.remove(Base.metadata.tables["alembic_probe"])
        Base.registry.dispose()
        Base.registry.configure()

    assert "alembic_probe" not in Base.metadata.tables


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

    assert diffs == []  # no models yet, so nothing to migrate
    assert tables_after == tables_before
    if "alembic_version" not in tables_before:
        assert current is None
