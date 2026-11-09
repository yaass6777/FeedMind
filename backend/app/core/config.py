"""Application configuration loaded from environment variables.

Precedence (highest first):
1. Real environment variables (e.g. supplied by Docker Compose).
2. The repository-root ``.env`` file, if present (local development only).
3. Field defaults defined below.

Secrets have no defaults and are stored as ``SecretStr`` so they are masked in
``repr()``, ``str()`` and logs. ``hide_input_in_errors`` keeps raw input values
out of validation error messages, and custom validator messages never echo the
supplied value. Note: ``ValidationError.errors()`` and ``.json()`` still include
raw input unless called with ``include_input=False``; never log them otherwise.
"""

import ipaddress
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# backend/app/core/config.py -> parents[3] is the repository root.
# Inside a container this path usually does not exist; a missing env file is
# silently ignored, so configuration comes purely from environment variables.
_PARENTS = Path(__file__).resolve().parents
ROOT_ENV_FILE: Path | None = _PARENTS[3] / ".env" if len(_PARENTS) > 3 else None

_DB_DRIVERNAME = "postgresql+psycopg"
_CORS_SCHEMES = ("http", "https")
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
# Characters that can never appear in a bare origin (scheme://host[:port]).
# Square brackets are allowed for IPv6 literals and validated by urlsplit.
_CORS_FORBIDDEN_CHARS = frozenset("*;\"'@?# \t\\")


def _is_valid_host(host: str) -> bool:
    """Return True for a valid DNS hostname or IPv4/IPv6 address."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    host = host.lower()
    if len(host) > 253:
        return False
    labels = host.split(".")
    if all(label.isdigit() for label in labels):
        return False  # numeric-only but not a valid IPv4 address
    return all(_HOST_LABEL.match(label) for label in labels)


def _is_valid_port(port: int | None) -> bool:
    return port is None or 1 <= port <= 65535


def _normalize_origin(raw: str, position: int) -> str:
    """Validate a single CORS origin and return it as ``scheme://host[:port]``.

    Error messages identify the entry by position only, never by value.
    """

    def invalid(reason: str) -> ValueError:
        return ValueError(f"CORS_ORIGINS entry #{position} is invalid: {reason}")

    if any(char in _CORS_FORBIDDEN_CHARS for char in raw):
        raise invalid(
            "contains characters not allowed in an origin "
            "(wildcards, credentials, query, fragment, ';' or JSON syntax)"
        )
    if "://" not in raw:
        raise invalid("missing scheme (expected http:// or https://)")

    try:
        parts = urlsplit(raw)
    except ValueError:
        raise invalid("malformed origin") from None
    if parts.scheme not in _CORS_SCHEMES:
        raise invalid("scheme must be http or https")
    if parts.path not in ("", "/"):
        raise invalid("paths are not allowed")
    if parts.username is not None or parts.password is not None:
        raise invalid("credentials are not allowed")
    host = parts.hostname
    if not host or not _is_valid_host(host):
        raise invalid("missing or invalid hostname")
    try:
        port = parts.port
    except ValueError:
        raise invalid("invalid port") from None
    if parts.netloc.endswith(":") or not _is_valid_port(port):
        raise invalid("invalid port")

    host_part = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host_part}" + (f":{port}" if port is not None else "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "FeedMind"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False

    secret_key: SecretStr = Field(min_length=32)
    database_url: SecretStr

    # NoDecode: read the raw string so we can accept a comma-separated list
    # instead of requiring JSON.
    cors_origins: Annotated[list[str], NoDecode]

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            port = url.port
        except (ArgumentError, ValueError):
            raise ValueError("DATABASE_URL is not a valid database URL") from None
        if url.drivername != _DB_DRIVERNAME:
            raise ValueError(f"DATABASE_URL must use the {_DB_DRIVERNAME}:// scheme")
        if not url.host or not _is_valid_host(url.host):
            raise ValueError("DATABASE_URL must include a valid hostname")
        if not _is_valid_port(port):
            raise ValueError("DATABASE_URL has an invalid port")
        if not url.database:
            raise ValueError("DATABASE_URL must include a database name")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            entries = value.split(",")
        elif isinstance(value, list):
            entries = [str(entry) for entry in value]
        else:
            return value  # let Pydantic's list[str] type check reject it

        origins = []
        for position, entry in enumerate(entries, start=1):
            entry = entry.strip()
            if entry:
                origins.append(_normalize_origin(entry, position))
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        return origins


@lru_cache
def get_settings() -> Settings:
    """Return the single, cached Settings instance for the application."""
    return Settings()
