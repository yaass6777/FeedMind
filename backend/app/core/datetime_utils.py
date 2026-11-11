"""UTC time helpers.

Application-set timestamps must be timezone-aware UTC. Never use naive
``datetime.now()`` / ``datetime.utcnow()`` for persisted values.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)
