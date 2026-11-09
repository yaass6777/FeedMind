from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.config import Settings

VALID_SECRET = "x" * 40
VALID_DB_URL = "postgresql+psycopg://user:pass@localhost:5432/feedmind_test"
VALID_CORS = "http://localhost:5173"
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
    """Isolate every test from the real process environment and root .env file."""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Default env file off, so even get_settings() never reads the real .env.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    config.get_settings.cache_clear()
    yield monkeypatch
    config.get_settings.cache_clear()


def set_required_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Supply settings as OS environment variables, as Docker Compose will."""
    values = {
        "SECRET_KEY": VALID_SECRET,
        "DATABASE_URL": VALID_DB_URL,
        "CORS_ORIGINS": VALID_CORS,
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def load_from_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    set_required_env(monkeypatch, **overrides)
    return Settings()


def write_env_file(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "SECRET_KEY": VALID_SECRET,
        "DATABASE_URL": VALID_DB_URL,
        "CORS_ORIGINS": VALID_CORS,
    }
    values.update(overrides)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    return env_file


# --- Loading ---------------------------------------------------------------


def test_loads_from_env_file(tmp_path: Path) -> None:
    env_file = write_env_file(
        tmp_path,
        APP_NAME="FeedMind Test",
        APP_ENV="test",
        DEBUG="true",
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "FeedMind Test"
    assert settings.app_env == "test"
    assert settings.debug is True
    assert settings.secret_key.get_secret_value() == VALID_SECRET
    assert settings.database_url.get_secret_value() == VALID_DB_URL
    assert settings.cors_origins == [VALID_CORS]


def test_defaults_apply_for_optional_settings(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    settings = load_from_env(isolated_env)

    assert settings.app_name == "FeedMind"
    assert settings.app_env == "development"
    assert settings.debug is False


def test_environment_variables_override_env_file(
    tmp_path: Path, isolated_env: pytest.MonkeyPatch
) -> None:
    env_file = write_env_file(
        tmp_path,
        APP_ENV="development",
        DEBUG="false",
        DATABASE_URL="postgresql+psycopg://file:file@localhost:5432/from_file",
        CORS_ORIGINS="http://from-file.test",
    )
    docker_url = "postgresql+psycopg://feedmind:secret@db:5432/feedmind"
    isolated_env.setenv("APP_ENV", "production")
    isolated_env.setenv("DEBUG", "true")
    isolated_env.setenv("DATABASE_URL", docker_url)
    isolated_env.setenv("CORS_ORIGINS", "http://frontend:5173")

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "production"
    assert settings.debug is True
    assert settings.database_url.get_secret_value() == docker_url
    assert settings.cors_origins == ["http://frontend:5173"]


def test_works_from_environment_only_without_env_file(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    settings = load_from_env(isolated_env)

    assert settings.database_url.get_secret_value() == VALID_DB_URL
    assert settings.cors_origins == [VALID_CORS]


@pytest.mark.parametrize("missing", ["SECRET_KEY", "DATABASE_URL", "CORS_ORIGINS"])
def test_missing_required_setting_raises(
    isolated_env: pytest.MonkeyPatch, missing: str
) -> None:
    set_required_env(isolated_env)
    isolated_env.delenv(missing)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert missing.lower() in str(exc_info.value)


def test_invalid_app_env_is_rejected(isolated_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        load_from_env(isolated_env, APP_ENV="staging")


# --- DEBUG -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("no", False),
    ],
)
def test_debug_boolean_parsing(
    isolated_env: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    assert load_from_env(isolated_env, DEBUG=raw).debug is expected


def test_invalid_debug_value_is_rejected(isolated_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        load_from_env(isolated_env, DEBUG="maybe")


# --- CORS_ORIGINS ----------------------------------------------------------

MULTIPLE_ORIGINS = "http://localhost:5173,https://app.example.com,http://127.0.0.1:8080"
EXPECTED_ORIGINS = [
    "http://localhost:5173",
    "https://app.example.com",
    "http://127.0.0.1:8080",
]


def test_cors_multiple_origins_from_env_file(tmp_path: Path) -> None:
    env_file = write_env_file(tmp_path, CORS_ORIGINS=MULTIPLE_ORIGINS)

    assert Settings(_env_file=env_file).cors_origins == EXPECTED_ORIGINS


def test_cors_quoted_value_in_env_file(tmp_path: Path) -> None:
    env_file = write_env_file(tmp_path, CORS_ORIGINS=f'"{MULTIPLE_ORIGINS}"')

    assert Settings(_env_file=env_file).cors_origins == EXPECTED_ORIGINS


def test_cors_multiple_origins_from_os_environment(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    settings = load_from_env(isolated_env, CORS_ORIGINS=MULTIPLE_ORIGINS)

    assert settings.cors_origins == EXPECTED_ORIGINS


def test_cors_whitespace_and_empty_entries_are_trimmed(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    raw = "  http://localhost:5173 ,, \t https://app.example.com  , "

    settings = load_from_env(isolated_env, CORS_ORIGINS=raw)

    assert settings.cors_origins == ["http://localhost:5173", "https://app.example.com"]


def test_cors_trailing_slash_is_normalized(isolated_env: pytest.MonkeyPatch) -> None:
    raw = "http://localhost:5173/,https://app.example.com/"

    settings = load_from_env(isolated_env, CORS_ORIGINS=raw)

    assert settings.cors_origins == ["http://localhost:5173", "https://app.example.com"]


def test_cors_scheme_and_host_are_lowercased(isolated_env: pytest.MonkeyPatch) -> None:
    settings = load_from_env(isolated_env, CORS_ORIGINS="HTTPS://App.Example.COM")

    assert settings.cors_origins == ["https://app.example.com"]


@pytest.mark.parametrize(
    "origin",
    ["http://[::1]:5173", "http://192.168.1.10", "https://localhost:65535"],
)
def test_cors_accepts_ip_and_port_variants(
    isolated_env: pytest.MonkeyPatch, origin: str
) -> None:
    assert load_from_env(isolated_env, CORS_ORIGINS=origin).cors_origins == [origin]


@pytest.mark.parametrize(
    ("raw", "case"),
    [
        ("localhost:5173", "missing scheme"),
        ("//localhost:5173", "missing scheme"),
        ("ftp://localhost", "unsupported scheme"),
        ("ws://localhost:5173", "unsupported scheme"),
        ("http://", "missing hostname"),
        ("http://:5173", "missing hostname"),
        ("http://localhost:abc", "non-numeric port"),
        ("http://localhost:0", "port zero"),
        ("http://localhost:70000", "port out of range"),
        ("http://localhost:", "empty port"),
        ("http://localhost:5173/app", "path"),
        ("http://localhost:5173//", "double slash path"),
        ("http://localhost:5173?debug=1", "query"),
        ("http://localhost:5173?", "empty query"),
        ("http://localhost:5173#top", "fragment"),
        ("http://user:pass@localhost:5173", "credentials"),
        ("http://user@localhost:5173", "username"),
        ("*", "wildcard"),
        ("http://*", "wildcard host"),
        ("https://*.example.com", "wildcard subdomain"),
        ("http://a.test;http://b.test", "semicolon separated"),
        ('["http://localhost:5173"]', "JSON array"),
        ("[http://localhost:5173]", "bracketed list"),
        ("not a url", "free text"),
        ("http://-bad-.example.com", "invalid hostname label"),
        ("http://999.1.1.1", "invalid IPv4"),
        ("http://[::1", "unbalanced IPv6 bracket"),
        ("http://localhost:5173,ftp://bad", "one bad entry among good ones"),
    ],
)
def test_cors_invalid_origin_is_rejected(
    isolated_env: pytest.MonkeyPatch, raw: str, case: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        load_from_env(isolated_env, CORS_ORIGINS=raw)

    assert "cors_origins" in str(exc_info.value), case


@pytest.mark.parametrize("raw", ["", "   ", " , , "])
def test_cors_empty_configuration_is_rejected(
    isolated_env: pytest.MonkeyPatch, raw: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        load_from_env(isolated_env, CORS_ORIGINS=raw)

    assert "at least one origin" in str(exc_info.value)


def test_cors_error_does_not_echo_input(isolated_env: pytest.MonkeyPatch) -> None:
    raw = "http://good.test,https://sensitive-internal-host.test/private-path"

    with pytest.raises(ValidationError) as exc_info:
        load_from_env(isolated_env, CORS_ORIGINS=raw)

    message = str(exc_info.value)
    assert "entry #2" in message
    assert "sensitive-internal-host" not in message
    assert "private-path" not in message


# --- DATABASE_URL ----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:pass@localhost:5432/feedmind",
        "postgresql+psycopg://user:pass@db/feedmind",
        "postgresql+psycopg://user@127.0.0.1:5432/feedmind",
    ],
)
def test_valid_database_url_is_accepted(
    isolated_env: pytest.MonkeyPatch, url: str
) -> None:
    settings = load_from_env(isolated_env, DATABASE_URL=url)

    assert settings.database_url.get_secret_value() == url


@pytest.mark.parametrize(
    ("url", "case"),
    [
        ("postgresql+psycopg://user:pass@/feedmind", "missing hostname"),
        ("postgresql+psycopg:///feedmind", "missing hostname"),
        ("postgresql+psycopg://user:pass@localhost:5432", "missing database name"),
        ("postgresql+psycopg://user:pass@localhost:5432/", "missing database name"),
        ("postgresql://user:pass@localhost:5432/feedmind", "driver not specified"),
        ("postgresql+psycopg2://user:pass@localhost/feedmind", "unsupported driver"),
        ("postgresql+asyncpg://user:pass@localhost/feedmind", "unsupported driver"),
        ("postgres://user:pass@localhost/feedmind", "legacy scheme"),
        ("mysql+pymysql://user:pass@localhost/feedmind", "unsupported database"),
        ("sqlite:///feedmind.db", "unsupported database"),
        ("not a url", "malformed"),
        ("", "empty"),
        ("postgresql+psycopg://:::not a url", "malformed"),
        ("postgresql+psycopg://user:pass@localhost:abc/feedmind", "bad port"),
        ("postgresql+psycopg://user:pass@localhost:99999/feedmind", "port range"),
    ],
)
def test_invalid_database_url_is_rejected(
    isolated_env: pytest.MonkeyPatch, url: str, case: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        load_from_env(isolated_env, DATABASE_URL=url)

    assert "database_url" in str(exc_info.value), case


# --- Secret handling -------------------------------------------------------


def test_short_secret_key_is_rejected(isolated_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        load_from_env(isolated_env, SECRET_KEY="too-short")


def test_invalid_secret_key_not_in_error_text(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    leaked_candidate = "RealButTooShortSecret123"

    with pytest.raises(ValidationError) as exc_info:
        load_from_env(isolated_env, SECRET_KEY=leaked_candidate)

    error = exc_info.value
    assert leaked_candidate not in str(error)
    assert leaked_candidate not in repr(error)
    # errors()/json() always carry raw input unless explicitly excluded, so any
    # code that serializes these errors must pass include_input=False.
    assert leaked_candidate not in repr(error.errors(include_input=False))
    assert leaked_candidate not in error.json(include_input=False)


@pytest.mark.parametrize(
    "url",
    [
        "mysql://dbadmin:Hunter2Password@db.internal/feedmind",
        "postgresql+psycopg://dbadmin:Hunter2Password@/feedmind",
        "postgresql+psycopg://dbadmin:Hunter2Password@localhost:abc/feedmind",
    ],
)
def test_database_credentials_not_in_error_text(
    isolated_env: pytest.MonkeyPatch, url: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        load_from_env(isolated_env, DATABASE_URL=url)

    rendered = str(exc_info.value) + repr(exc_info.value)
    assert "dbadmin" not in rendered
    assert "Hunter2Password" not in rendered


def test_secrets_are_masked_in_representations(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    settings = load_from_env(isolated_env)

    rendered = (
        repr(settings)
        + str(settings)
        + repr(settings.model_dump())
        + settings.model_dump_json()
    )

    assert VALID_SECRET not in rendered
    assert "user:pass" not in rendered


# --- get_settings() cache --------------------------------------------------


def test_get_settings_returns_same_cached_object(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    set_required_env(isolated_env)

    assert config.get_settings() is config.get_settings()


def test_get_settings_picks_up_env_changes_after_cache_clear(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    set_required_env(isolated_env, APP_NAME="First")
    first = config.get_settings()

    isolated_env.setenv("APP_NAME", "Second")
    assert config.get_settings().app_name == "First"  # still cached

    config.get_settings.cache_clear()
    second = config.get_settings()

    assert second is not first
    assert second.app_name == "Second"


def test_default_env_file_points_to_repository_root() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    assert config.ROOT_ENV_FILE == repo_root / ".env"
