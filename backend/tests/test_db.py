import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, ClassVar

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core import config
from app.core.config import Settings
from app.db import session as db_session

TEST_DB_URL = (
    "postgresql+psycopg://feedmind_user:S3cretTestPw@db.test:5433/feedmind_test"
)
BACKEND_DIR = Path(__file__).resolve().parents[1]
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
    """Isolate from the real environment, root .env and cached db objects."""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    _clear_caches()
    yield monkeypatch
    _clear_caches()


# Captured at import, so clearing still works while a test has monkeypatched
# one of these module attributes.
_CACHED_FUNCTIONS = (
    config.get_settings,
    db_session.get_engine,
    db_session.get_session_factory,
)


def _clear_caches() -> None:
    for function in _CACHED_FUNCTIONS:
        function.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        secret_key="d" * 40,
        database_url=TEST_DB_URL,
        cors_origins="http://localhost:5173",
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = db_session.create_db_engine(settings)
    yield engine
    engine.dispose()


class TrackingSession(Session):
    """Session that records close() calls, for asserting dependency cleanup."""

    instances: ClassVar[list["TrackingSession"]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.close_calls = 0
        TrackingSession.instances.append(self)

    def close(self) -> None:
        self.close_calls += 1
        super().close()


@pytest.fixture
def tracking_factory(
    engine: Engine, isolated_env: pytest.MonkeyPatch
) -> sessionmaker[Session]:
    """Make get_db() use a factory of TrackingSessions (no DB connection needed)."""
    TrackingSession.instances = []
    factory = sessionmaker(
        bind=engine, class_=TrackingSession, autocommit=False, autoflush=False
    )
    isolated_env.setattr(db_session, "get_session_factory", lambda: factory)
    return factory


# --- Engine -----------------------------------------------------------------


def test_engine_uses_database_url_with_psycopg_driver(engine: Engine) -> None:
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    assert engine.url.host == "db.test"
    assert engine.url.port == 5433
    assert engine.url.database == "feedmind_test"
    assert engine.url.username == "feedmind_user"


def test_engine_enables_pool_pre_ping_and_disables_echo(engine: Engine) -> None:
    assert engine.pool._pre_ping is True
    assert engine.echo is False


def test_engine_password_is_masked_in_representations(engine: Engine) -> None:
    rendered = repr(engine) + str(engine.url) + repr(engine.url)

    assert "S3cretTestPw" not in rendered
    assert "***" in rendered


def test_creating_engine_does_not_connect(settings: Settings) -> None:
    # db.test does not resolve; creating the engine must still succeed.
    engine = db_session.create_db_engine(settings)

    assert engine.pool.checkedout() == 0
    engine.dispose()


def test_get_engine_is_lazy_and_cached(isolated_env: pytest.MonkeyPatch) -> None:
    isolated_env.setenv("SECRET_KEY", "d" * 40)
    isolated_env.setenv("DATABASE_URL", TEST_DB_URL)
    isolated_env.setenv("CORS_ORIGINS", "http://localhost:5173")

    first = db_session.get_engine()

    assert first is db_session.get_engine()
    assert first.url.database == "feedmind_test"
    first.dispose()


def test_importing_db_module_has_no_config_or_connection_side_effects() -> None:
    """Importing must work with no configuration and no reachable database."""
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    code = (
        "import app.db.session as s;"
        "assert s.get_engine.cache_info().currsize == 0;"
        "assert s.get_session_factory.cache_info().currsize == 0"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


# --- Session factory --------------------------------------------------------


def test_session_factory_configuration(engine: Engine) -> None:
    factory = db_session.create_session_factory(engine)

    assert factory.kw["bind"] is engine
    assert factory.kw["autocommit"] is False
    assert factory.kw["autoflush"] is False
    assert issubclass(factory.class_, Session)


def test_session_factory_creates_unconnected_sessions(engine: Engine) -> None:
    factory = db_session.create_session_factory(engine)

    with factory() as session:
        assert isinstance(session, Session)
        assert session.get_bind() is engine
        assert session.autoflush is False
        assert not session.in_transaction()


def test_get_session_factory_is_cached_and_bound_to_get_engine(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    isolated_env.setenv("SECRET_KEY", "d" * 40)
    isolated_env.setenv("DATABASE_URL", TEST_DB_URL)
    isolated_env.setenv("CORS_ORIGINS", "http://localhost:5173")

    factory = db_session.get_session_factory()

    assert factory is db_session.get_session_factory()
    assert factory.kw["bind"] is db_session.get_engine()
    db_session.get_engine().dispose()


# --- get_db() dependency ----------------------------------------------------


def test_get_db_yields_session(tracking_factory: sessionmaker[Session]) -> None:
    dependency = db_session.get_db()

    session = next(dependency)

    assert isinstance(session, TrackingSession)
    assert session.close_calls == 0
    dependency.close()


def test_get_db_closes_session_on_completion(
    tracking_factory: sessionmaker[Session],
) -> None:
    dependency = db_session.get_db()
    session = next(dependency)

    with pytest.raises(StopIteration):
        next(dependency)

    assert session.close_calls == 1


def test_get_db_closes_session_when_exception_raised(
    tracking_factory: sessionmaker[Session],
) -> None:
    dependency = db_session.get_db()
    session = next(dependency)

    with pytest.raises(RuntimeError, match="boom"):
        dependency.throw(RuntimeError("boom"))

    assert session.close_calls == 1


DbSession = Annotated[Session, Depends(db_session.get_db)]


def _app_with_db_routes() -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    def ok(db: DbSession) -> dict[str, bool]:
        return {"session": isinstance(db, Session)}

    @app.get("/http-error")
    def http_error(db: DbSession) -> None:
        raise HTTPException(status_code=409, detail="conflict")

    @app.get("/crash")
    def crash(db: DbSession) -> None:
        raise RuntimeError("unexpected")

    return app


def test_fastapi_dependency_closes_session_after_request(
    tracking_factory: sessionmaker[Session],
) -> None:
    with TestClient(_app_with_db_routes()) as client:
        response = client.get("/ok")

    assert response.json() == {"session": True}
    assert [s.close_calls for s in TrackingSession.instances] == [1]


def test_fastapi_dependency_closes_session_on_http_exception(
    tracking_factory: sessionmaker[Session],
) -> None:
    with TestClient(_app_with_db_routes()) as client:
        response = client.get("/http-error")

    assert response.status_code == 409
    assert [s.close_calls for s in TrackingSession.instances] == [1]


def test_fastapi_dependency_closes_session_on_unhandled_exception(
    tracking_factory: sessionmaker[Session],
) -> None:
    with TestClient(_app_with_db_routes(), raise_server_exceptions=False) as client:
        response = client.get("/crash")

    assert response.status_code == 500
    assert [s.close_calls for s in TrackingSession.instances] == [1]


# --- Integration (opt-in) ---------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("FEEDMIND_RUN_INTEGRATION") != "1",
    reason="set FEEDMIND_RUN_INTEGRATION=1 with PostgreSQL running to enable",
)
def test_integration_select_one_against_real_postgres(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """Connects to the real DATABASE_URL (root .env) and runs SELECT 1."""
    isolated_env.setitem(Settings.model_config, "env_file", config.ROOT_ENV_FILE)
    _clear_caches()

    dependency = db_session.get_db()
    session = next(dependency)
    try:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        dependency.close()
        db_session.get_engine().dispose()
