"""Read-only verification of the migrated feedmind_test schema (opt-in).

Expectations are derived from the SQLAlchemy metadata and compared with the
PostgreSQL system catalogs, so the migration and the models must agree exactly.
"""

import re

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import migration_head
from sqlalchemy import (
    CheckConstraint,
    Connection,
    DateTime,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

import app.models  # noqa: F401  (registers all models)
from app.db.base import Base

pytestmark = pytest.mark.integration

EXPECTED_REVISION_FOR_SCHEMA = migration_head()

ON_DELETE = {"c": "CASCADE", "r": "RESTRICT", "a": "NO ACTION", "n": "SET NULL"}
PG = postgresql.dialect()
TABLES = Base.metadata.tables


def rows(connection: Connection, sql: str) -> list[tuple]:
    return [tuple(row) for row in connection.execute(text(sql))]


def constraints_of_type(connection: Connection, contype: str) -> set[tuple]:
    return set(
        rows(
            connection,
            "SELECT rel.relname, con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = rel.relnamespace "
            f"WHERE n.nspname = 'public' AND con.contype = '{contype}' "
            "AND rel.relname <> 'alembic_version'",
        )
    )


def test_connected_to_feedmind_test_at_expected_revision(test_db: Connection) -> None:
    assert test_db.execute(text("SELECT current_database()")).scalar() == (
        "feedmind_test"
    )
    assert rows(test_db, "SELECT version_num FROM alembic_version") == [
        (EXPECTED_REVISION_FOR_SCHEMA,)
    ]


def test_exactly_the_12_application_tables_exist(test_db: Connection) -> None:
    tables = {
        name
        for (name,) in rows(
            test_db, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }

    assert tables - {"alembic_version"} == set(TABLES)
    assert len(tables - {"alembic_version"}) == 12


def test_primary_keys(test_db: Connection) -> None:
    expected = {(name, table.primary_key.name) for name, table in TABLES.items()}

    assert constraints_of_type(test_db, "p") == expected


def test_foreign_keys_and_on_delete_actions(test_db: Connection) -> None:
    actual = set(
        rows(
            test_db,
            "SELECT rel.relname, con.conname, ref.relname, con.confdeltype "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_class ref ON ref.oid = con.confrelid "
            "WHERE con.contype = 'f'",
        )
    )
    actual = {(t, name, ref, ON_DELETE[action]) for t, name, ref, action in actual}
    expected = {
        (table.name, fk.name, fk.referred_table.name, fk.ondelete)
        for table in TABLES.values()
        for fk in table.foreign_key_constraints
    }

    assert actual == expected
    assert len(actual) == 16


def test_unique_constraints(test_db: Connection) -> None:
    expected = {
        (table.name, constraint.name)
        for table in TABLES.values()
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert constraints_of_type(test_db, "u") == expected
    assert len(expected) == 11


def test_check_constraints(test_db: Connection) -> None:
    expected = {
        (table.name, constraint.name)
        for table in TABLES.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraints_of_type(test_db, "c") == expected
    assert len(expected) == 9


def test_enum_check_constraints_list_exactly_the_allowed_values(
    test_db: Connection,
) -> None:
    definitions = dict(
        rows(
            test_db,
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname IN ('ck_feedback_feedback_type', "
            "'ck_source_fetch_logs_status')",
        )
    )

    feedback = set(re.findall(r"'(\w+)'", definitions["ck_feedback_feedback_type"]))
    status = set(re.findall(r"'(\w+)'", definitions["ck_source_fetch_logs_status"]))
    assert feedback == {"LIKE", "DISLIKE", "DISMISS"}
    assert status == {"RUNNING", "SUCCESS", "PARTIAL", "FAILED"}


def _expected_index_definition(index) -> str:
    """Model CREATE INDEX rewritten into pg_get_indexdef()'s canonical form."""
    ddl = re.sub(r"\s+", " ", str(CreateIndex(index).compile(dialect=PG)))
    table = index.table.name
    ddl = ddl.replace(f" ON {table} (", f" ON public.{table} USING btree (")
    return re.sub(r" WHERE (.+)$", r" WHERE (\1)", ddl)


def test_indexes_match_models_exactly(test_db: Connection) -> None:
    actual = dict(
        rows(
            test_db,
            "SELECT i.indexname, i.indexdef FROM pg_indexes i "
            "WHERE i.schemaname = 'public' AND i.tablename <> 'alembic_version' "
            "AND NOT EXISTS (SELECT 1 FROM pg_constraint c "
            "WHERE c.conname = i.indexname)",
        )
    )
    expected = {
        index.name: _expected_index_definition(index)
        for table in TABLES.values()
        for index in table.indexes
    }

    assert actual == expected
    assert len(actual) == 9
    assert "DESC NULLS LAST" in actual["ix_articles_published_at"]
    assert "WHERE (is_active = true)" in actual["ix_sources_active"]
    assert actual["uq_article_sources_source_id_external_guid"].startswith(
        "CREATE UNIQUE INDEX"
    )


def test_columns_nullability_types_and_defaults(test_db: Connection) -> None:
    actual = {
        (table, column): (nullable == "YES", data_type, default, identity)
        for table, column, nullable, data_type, default, identity in rows(
            test_db,
            "SELECT table_name, column_name, is_nullable, data_type, "
            "column_default, identity_generation FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'",
        )
    }

    expected_keys = {(t.name, c.name) for t in TABLES.values() for c in t.columns}
    assert set(actual) == expected_keys

    for table in TABLES.values():
        for column in table.columns:
            nullable, data_type, default, identity = actual[(table.name, column.name)]
            label = f"{table.name}.{column.name}"
            assert nullable is column.nullable, label
            if isinstance(column.type, DateTime):
                assert data_type == "timestamp with time zone", label
            if column.identity is not None:
                assert identity == "BY DEFAULT", label
                assert default is None, label
            else:
                assert identity is None, label
                model_default = (
                    None
                    if column.server_default is None
                    else str(column.server_default.arg.compile(dialect=PG))
                )
                assert default == model_default, label


def test_identity_columns(test_db: Connection) -> None:
    identity_columns = set(
        rows(
            test_db,
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND is_identity = 'YES'",
        )
    )

    assert identity_columns == {
        (name, "id") for name in TABLES if name != "article_categories"
    }


def test_no_naive_timestamps_or_native_enum_types(test_db: Connection) -> None:
    naive = rows(
        test_db,
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND data_type = 'timestamp without time zone'",
    )
    enum_types = rows(
        test_db,
        "SELECT t.typname FROM pg_type t "
        "JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' AND t.typtype = 'e'",
    )

    assert naive == []
    assert enum_types == []


def test_alembic_compare_metadata_reports_no_differences(
    test_db: Connection,
) -> None:
    context = MigrationContext.configure(
        test_db,
        opts={
            "target_metadata": Base.metadata,
            "compare_type": True,
            "compare_server_default": True,
        },
    )

    assert compare_metadata(context, Base.metadata) == []
