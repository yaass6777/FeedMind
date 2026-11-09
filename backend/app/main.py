"""FeedMind API entrypoint.

``create_app()`` builds the FastAPI application. The module-level ``app`` used
by ``uvicorn app.main:app`` is created lazily on first access, so importing this
module (e.g. in tests) does not read configuration or require environment
variables.
"""

from functools import cache

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings

API_V1_PREFIX = "/api/v1"
API_VERSION = "0.1.0"

# Methods used by the planned REST API (master spec, section 9).
CORS_ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FeedMind FastAPI application from the given (or global) settings."""
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=API_VERSION,
        description="Personalized RSS content aggregation and recommendation API.",
        debug=settings.debug,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=CORS_ALLOWED_HEADERS,
        # Bearer tokens are sent in the Authorization header, not cookies.
        allow_credentials=False,
    )

    app.include_router(api_router, prefix=API_V1_PREFIX)
    return app


@cache
def _default_app() -> FastAPI:
    return create_app()


def __getattr__(name: str) -> FastAPI:
    # PEP 562: build ``app`` on first access (``uvicorn app.main:app``).
    if name == "app":
        return _default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
