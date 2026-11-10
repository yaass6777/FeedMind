"""Declarative base whose metadata Alembic uses as its migration target.

ORM models subclass ``Base`` and are imported in ``app.models`` so that their
tables register on ``Base.metadata`` before Alembic autogenerate runs.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint/index names, so migrations can reliably reference
# (and later drop or alter) them instead of relying on database-generated names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
