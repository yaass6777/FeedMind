import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.core import config
from app.core.config import Settings

ALLOWED_ORIGIN = "http://localhost:5173"
OTHER_ALLOWED_ORIGIN = "https://app.example.com"
UNCONFIGURED_ORIGIN = "http://evil.example.com"
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
    """Isolate from the real process environment and root .env file."""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    config.get_settings.cache_clear()
    main._default_app.cache_clear()
    yield monkeypatch
    config.get_settings.cache_clear()
    main._default_app.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_name="FeedMind Test API",
        app_env="test",
        debug=False,
        secret_key="t" * 40,
        database_url="postgresql+psycopg://user:pass@localhost:5432/feedmind_test",
        cors_origins=f"{ALLOWED_ORIGIN},{OTHER_ALLOWED_ORIGIN}",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(main.create_app(settings)) as test_client:
        yield test_client


def preflight(client: TestClient, origin: str, method: str = "GET"):
    return client.options(
        "/api/v1/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )


# --- Application creation ---------------------------------------------------


def test_create_app_returns_fastapi_instance(settings: Settings) -> None:
    app = main.create_app(settings)

    assert isinstance(app, FastAPI)
    assert app.state.settings is settings


def test_app_metadata_uses_configured_settings(settings: Settings) -> None:
    app = main.create_app(settings)

    assert app.title == "FeedMind Test API"
    assert app.version == main.API_VERSION
    assert app.debug is False
    assert app.openapi()["info"]["title"] == "FeedMind Test API"


def test_health_route_is_registered_under_api_v1(settings: Settings) -> None:
    paths = main.create_app(settings).openapi()["paths"]

    assert "/api/v1/health" in paths
    assert "/health" not in paths


def test_importing_main_has_no_config_side_effects() -> None:
    """Importing app.main must work with no configuration at all."""
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_module_level_app_is_built_lazily_from_get_settings(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    isolated_env.setenv("APP_NAME", "FeedMind From Env")
    isolated_env.setenv("SECRET_KEY", "e" * 40)
    isolated_env.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/feedmind")
    isolated_env.setenv("CORS_ORIGINS", ALLOWED_ORIGIN)

    app = main.app

    assert isinstance(app, FastAPI)
    assert app.title == "FeedMind From Env"
    assert main.app is app  # built once
    assert app.state.settings is config.get_settings()


def test_unknown_module_attribute_raises() -> None:
    with pytest.raises(AttributeError):
        _ = main.does_not_exist


# --- Health endpoint --------------------------------------------------------


def test_health_returns_200(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200


def test_health_returns_expected_json(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


# --- CORS -------------------------------------------------------------------


@pytest.mark.parametrize("origin", [ALLOWED_ORIGIN, OTHER_ALLOWED_ORIGIN])
def test_cors_simple_request_allows_configured_origin(
    client: TestClient, origin: str
) -> None:
    response = client.get("/api/v1/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("method", main.CORS_ALLOWED_METHODS)
def test_cors_preflight_allows_configured_origin(
    client: TestClient, method: str
) -> None:
    response = preflight(client, ALLOWED_ORIGIN, method)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert method in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers
    assert "access-control-allow-credentials" not in response.headers


def test_cors_simple_request_rejects_unconfigured_origin(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": UNCONFIGURED_ORIGIN})

    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_rejects_unconfigured_origin(client: TestClient) -> None:
    response = preflight(client, UNCONFIGURED_ORIGIN)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_rejects_unlisted_header(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Custom-Header",
        },
    )

    assert response.status_code == 400


def test_cors_never_allows_wildcard(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.headers["access-control-allow-origin"] != "*"
