"""Enumerations stored as VARCHAR(20) with named CHECK constraints (no native enums)."""

from enum import StrEnum

from sqlalchemy import Enum


class FeedbackType(StrEnum):
    LIKE = "LIKE"
    DISLIKE = "DISLIKE"
    DISMISS = "DISMISS"


class FetchStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


def string_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Map a StrEnum to VARCHAR(20) plus a CHECK constraint named via ``name``.

    The metadata naming convention renders the constraint as
    ``ck_<table>_<name>``. Values (not member names) are persisted.
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )
