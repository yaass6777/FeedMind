"""Metadata, mapping and PostgreSQL DDL-compilation tests for the 12 ORM models.

None of these tests connect to a database or execute DDL: they inspect
SQLAlchemy metadata and compile SQL strings with the PostgreSQL dialect only.
"""

import os
import subprocess
import sys
from datetime import UTC, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    inspect,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import RelationshipDirection, configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from app import models
from app.core.datetime_utils import utc_now
from app.db.base import Base
from app.models import (
    Article,
    ArticleCategory,
    ArticleSource,
    Bookmark,
    Category,
    Feedback,
    FeedbackType,
    FetchStatus,
    ReadingHistory,
    Source,
    SourceFetchLog,
    User,
    UserPreference,
    UserSource,
)

PG = postgresql.dialect()
BACKEND_DIR = Path(__file__).resolve().parents[1]
MAX_IDENTIFIER_LENGTH = 63

TABLE_TO_MODEL = {
    "users": User,
    "sources": Source,
    "articles": Article,
    "categories": Category,
    "user_sources": UserSource,
    "article_sources": ArticleSource,
    "article_categories": ArticleCategory,
    "user_preferences": UserPreference,
    "bookmarks": Bookmark,
    "reading_history": ReadingHistory,
    "feedback": Feedback,
    "source_fetch_logs": SourceFetchLog,
}

# Shorthand column specs: (type, size, nullable, server_default SQL or None).
# size = String length, Numeric (precision, scale), Enum length, or None.
ID = (BigInteger, None, False, None)
FK = (BigInteger, None, False, None)
TS_NOW = (DateTime, None, False, "now()")
TS_NULL = (DateTime, None, True, None)
TEXT_REQ = (Text, None, False, None)
TEXT_NULL = (Text, None, True, None)

EXPECTED_COLUMNS: dict[str, dict[str, tuple]] = {
    "users": {
        "id": ID,
        "name": (String, 100, False, None),
        "email": (String, 255, False, None),
        "password_hash": (String, 255, False, None),
        "is_active": (Boolean, None, False, "true"),
        "created_at": TS_NOW,
        "updated_at": TS_NOW,
    },
    "sources": {
        "id": ID,
        "name": (String, 150, False, None),
        "feed_url": TEXT_REQ,
        "website_url": TEXT_NULL,
        "description": TEXT_NULL,
        "category_id": FK,
        "is_active": (Boolean, None, False, "true"),
        "last_attempted_at": TS_NULL,
        "last_successful_at": TS_NULL,
        "etag": TEXT_NULL,
        "last_modified": TEXT_NULL,
        "created_at": TS_NOW,
    },
    "articles": {
        "id": ID,
        "canonical_url": TEXT_REQ,
        "original_url": TEXT_REQ,
        "title": TEXT_REQ,
        "description": TEXT_NULL,
        "author": (String, 255, True, None),
        "image_url": TEXT_NULL,
        "published_at": TS_NULL,
        "ingested_at": TS_NOW,
        "created_at": TS_NOW,
        "updated_at": TS_NOW,
    },
    "categories": {
        "id": ID,
        "name": (String, 100, False, None),
        "slug": (String, 100, False, None),
        "description": TEXT_NULL,
        "created_at": TS_NOW,
    },
    "user_sources": {
        "id": ID,
        "user_id": FK,
        "source_id": FK,
        "followed_at": TS_NOW,
    },
    "article_sources": {
        "id": ID,
        "article_id": FK,
        "source_id": FK,
        "external_guid": TEXT_NULL,
        "first_seen_at": TS_NOW,
    },
    "article_categories": {
        "article_id": FK,
        "category_id": FK,
        "confidence_score": (Numeric, (5, 4), True, None),
    },
    "user_preferences": {
        "id": ID,
        "user_id": FK,
        "category_id": FK,
        "created_at": TS_NOW,
    },
    "bookmarks": {
        "id": ID,
        "user_id": FK,
        "article_id": FK,
        "created_at": TS_NOW,
    },
    "reading_history": {
        "id": ID,
        "user_id": FK,
        "article_id": FK,
        "first_opened_at": TS_NULL,
        "last_opened_at": TS_NULL,
        "is_read": (Boolean, None, False, "false"),
        "read_at": TS_NULL,
    },
    "feedback": {
        "id": ID,
        "user_id": FK,
        "article_id": FK,
        "feedback_type": (Enum, 20, False, None),
        "created_at": TS_NOW,
        "updated_at": TS_NOW,
    },
    "source_fetch_logs": {
        "id": ID,
        "source_id": FK,
        "status": (Enum, 20, False, None),
        "http_status_code": (Integer, None, True, None),
        "articles_found": (Integer, None, False, "0"),
        "articles_added": (Integer, None, False, "0"),
        "error_message": TEXT_NULL,
        "started_at": TS_NOW,
        "completed_at": TS_NULL,
    },
}

EXPECTED_PRIMARY_KEYS = {
    table: ("pk_" + table, ("id",)) for table in EXPECTED_COLUMNS
} | {"article_categories": ("pk_article_categories", ("article_id", "category_id"))}

# (table, column) -> (constraint name, referenced "table.column", ON DELETE)
EXPECTED_FOREIGN_KEYS = {
    ("sources", "category_id"): (
        "fk_sources_category_id_categories",
        "categories.id",
        "RESTRICT",
    ),
    ("user_sources", "user_id"): (
        "fk_user_sources_user_id_users",
        "users.id",
        "CASCADE",
    ),
    ("user_sources", "source_id"): (
        "fk_user_sources_source_id_sources",
        "sources.id",
        "CASCADE",
    ),
    ("article_sources", "article_id"): (
        "fk_article_sources_article_id_articles",
        "articles.id",
        "CASCADE",
    ),
    ("article_sources", "source_id"): (
        "fk_article_sources_source_id_sources",
        "sources.id",
        "RESTRICT",
    ),
    ("article_categories", "article_id"): (
        "fk_article_categories_article_id_articles",
        "articles.id",
        "CASCADE",
    ),
    ("article_categories", "category_id"): (
        "fk_article_categories_category_id_categories",
        "categories.id",
        "RESTRICT",
    ),
    ("user_preferences", "user_id"): (
        "fk_user_preferences_user_id_users",
        "users.id",
        "CASCADE",
    ),
    ("user_preferences", "category_id"): (
        "fk_user_preferences_category_id_categories",
        "categories.id",
        "RESTRICT",
    ),
    ("bookmarks", "user_id"): ("fk_bookmarks_user_id_users", "users.id", "CASCADE"),
    ("bookmarks", "article_id"): (
        "fk_bookmarks_article_id_articles",
        "articles.id",
        "CASCADE",
    ),
    ("reading_history", "user_id"): (
        "fk_reading_history_user_id_users",
        "users.id",
        "CASCADE",
    ),
    ("reading_history", "article_id"): (
        "fk_reading_history_article_id_articles",
        "articles.id",
        "CASCADE",
    ),
    ("feedback", "user_id"): ("fk_feedback_user_id_users", "users.id", "CASCADE"),
    ("feedback", "article_id"): (
        "fk_feedback_article_id_articles",
        "articles.id",
        "CASCADE",
    ),
    ("source_fetch_logs", "source_id"): (
        "fk_source_fetch_logs_source_id_sources",
        "sources.id",
        "RESTRICT",
    ),
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "users": {"uq_users_email": ("email",)},
    "sources": {"uq_sources_feed_url": ("feed_url",)},
    "articles": {"uq_articles_canonical_url": ("canonical_url",)},
    "categories": {"uq_categories_name": ("name",), "uq_categories_slug": ("slug",)},
    "user_sources": {"uq_user_sources_user_id_source_id": ("user_id", "source_id")},
    "article_sources": {
        "uq_article_sources_article_id_source_id": ("article_id", "source_id")
    },
    "article_categories": {},
    "user_preferences": {
        "uq_user_preferences_user_id_category_id": ("user_id", "category_id")
    },
    "bookmarks": {"uq_bookmarks_user_id_article_id": ("user_id", "article_id")},
    "reading_history": {
        "uq_reading_history_user_id_article_id": ("user_id", "article_id")
    },
    "feedback": {"uq_feedback_user_id_article_id": ("user_id", "article_id")},
    "source_fetch_logs": {},
}

# Hand-written CHECK constraints: name -> exact SQL expression.
EXPECTED_CHECKS = {
    "article_categories": {
        "ck_article_categories_confidence_score_range": (
            "confidence_score >= 0 AND confidence_score <= 1"
        ),
    },
    "reading_history": {
        "ck_reading_history_read_at_matches_is_read": (
            "(is_read AND read_at IS NOT NULL) OR (NOT is_read AND read_at IS NULL)"
        ),
        "ck_reading_history_last_opened_not_before_first": (
            "first_opened_at IS NULL OR last_opened_at IS NULL"
            " OR last_opened_at >= first_opened_at"
        ),
        "ck_reading_history_opened_timestamps_paired": (
            "(first_opened_at IS NULL) = (last_opened_at IS NULL)"
        ),
    },
    "source_fetch_logs": {
        "ck_source_fetch_logs_articles_found_non_negative": "articles_found >= 0",
        "ck_source_fetch_logs_articles_added_non_negative": "articles_added >= 0",
        "ck_source_fetch_logs_completed_not_before_started": (
            "completed_at IS NULL OR completed_at >= started_at"
        ),
    },
}

# Enum-generated CHECK constraints: name -> compiled PostgreSQL DDL fragment.
EXPECTED_ENUM_CHECKS = {
    "feedback": {
        "ck_feedback_feedback_type": (
            "CONSTRAINT ck_feedback_feedback_type CHECK "
            "(feedback_type IN ('LIKE', 'DISLIKE', 'DISMISS'))"
        ),
    },
    "source_fetch_logs": {
        "ck_source_fetch_logs_status": (
            "CONSTRAINT ck_source_fetch_logs_status CHECK "
            "(status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED'))"
        ),
    },
}

# Every index in the schema: name -> exact compiled PostgreSQL CREATE INDEX.
EXPECTED_INDEXES = {
    "sources": {
        "ix_sources_active": (
            "CREATE INDEX ix_sources_active ON sources (id) WHERE is_active = true"
        ),
    },
    "articles": {
        "ix_articles_published_at": (
            "CREATE INDEX ix_articles_published_at "
            "ON articles (published_at DESC NULLS LAST)"
        ),
    },
    "user_sources": {
        "ix_user_sources_source_id": (
            "CREATE INDEX ix_user_sources_source_id ON user_sources (source_id)"
        ),
    },
    "article_sources": {
        "ix_article_sources_source_id_article_id": (
            "CREATE INDEX ix_article_sources_source_id_article_id "
            "ON article_sources (source_id, article_id)"
        ),
        "uq_article_sources_source_id_external_guid": (
            "CREATE UNIQUE INDEX uq_article_sources_source_id_external_guid "
            "ON article_sources (source_id, external_guid) "
            "WHERE external_guid IS NOT NULL"
        ),
    },
    "article_categories": {
        "ix_article_categories_category_id_article_id": (
            "CREATE INDEX ix_article_categories_category_id_article_id "
            "ON article_categories (category_id, article_id)"
        ),
    },
    "bookmarks": {
        "ix_bookmarks_user_id_created_at": (
            "CREATE INDEX ix_bookmarks_user_id_created_at "
            "ON bookmarks (user_id, created_at DESC)"
        ),
    },
    "reading_history": {
        "ix_reading_history_user_id_last_opened_at": (
            "CREATE INDEX ix_reading_history_user_id_last_opened_at "
            "ON reading_history (user_id, last_opened_at DESC NULLS LAST)"
        ),
    },
    "source_fetch_logs": {
        "ix_source_fetch_logs_source_id_started_at": (
            "CREATE INDEX ix_source_fetch_logs_source_id_started_at "
            "ON source_fetch_logs (source_id, started_at DESC)"
        ),
    },
}

# model -> relationship -> (target model, direction, back_populates, passive_deletes,
#                           ORM delete cascade?)
O2M = RelationshipDirection.ONETOMANY
M2O = RelationshipDirection.MANYTOONE
EXPECTED_RELATIONSHIPS = {
    User: {
        "user_sources": (UserSource, O2M, "user", True, True),
        "user_preferences": (UserPreference, O2M, "user", True, True),
        "bookmarks": (Bookmark, O2M, "user", True, True),
        "reading_history": (ReadingHistory, O2M, "user", True, True),
        "feedback": (Feedback, O2M, "user", True, True),
    },
    Category: {
        "sources": (Source, O2M, "category", "all", False),
        "article_categories": (ArticleCategory, O2M, "category", "all", False),
        "user_preferences": (UserPreference, O2M, "category", "all", False),
    },
    Source: {
        "category": (Category, M2O, "sources", False, False),
        "user_sources": (UserSource, O2M, "source", "all", False),
        "article_sources": (ArticleSource, O2M, "source", "all", False),
        "fetch_logs": (SourceFetchLog, O2M, "source", "all", False),
    },
    Article: {
        "article_sources": (ArticleSource, O2M, "article", "all", False),
        "article_categories": (ArticleCategory, O2M, "article", "all", False),
        "bookmarks": (Bookmark, O2M, "article", "all", False),
        "reading_history": (ReadingHistory, O2M, "article", "all", False),
        "feedback": (Feedback, O2M, "article", "all", False),
    },
    UserSource: {
        "user": (User, M2O, "user_sources", False, False),
        "source": (Source, M2O, "user_sources", False, False),
    },
    ArticleSource: {
        "article": (Article, M2O, "article_sources", False, False),
        "source": (Source, M2O, "article_sources", False, False),
    },
    ArticleCategory: {
        "article": (Article, M2O, "article_categories", False, False),
        "category": (Category, M2O, "article_categories", False, False),
    },
    UserPreference: {
        "user": (User, M2O, "user_preferences", False, False),
        "category": (Category, M2O, "user_preferences", False, False),
    },
    Bookmark: {
        "user": (User, M2O, "bookmarks", False, False),
        "article": (Article, M2O, "bookmarks", False, False),
    },
    ReadingHistory: {
        "user": (User, M2O, "reading_history", False, False),
        "article": (Article, M2O, "reading_history", False, False),
    },
    Feedback: {
        "user": (User, M2O, "feedback", False, False),
        "article": (Article, M2O, "feedback", False, False),
    },
    SourceFetchLog: {
        "source": (Source, M2O, "fetch_logs", False, False),
    },
}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def compile_pg(element: object) -> str:
    return str(element.compile(dialect=PG)).strip()


def server_default_sql(column) -> str | None:
    """Compiled server default; identity columns are checked separately."""
    if column.server_default is None or isinstance(column.server_default, Identity):
        return None
    return compile_pg(column.server_default.arg)


def all_table_items() -> list[tuple[str, Table]]:
    return sorted(Base.metadata.tables.items())


# --- Registration and import safety ------------------------------------------


def test_exactly_the_12_specified_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == set(TABLE_TO_MODEL)
    assert len(Base.metadata.tables) == 12


@pytest.mark.parametrize(("table_name", "model"), sorted(TABLE_TO_MODEL.items()))
def test_each_table_is_mapped_by_its_model(table_name: str, model: type) -> None:
    assert model.__table__ is table(table_name)
    assert model.__tablename__ == table_name


def test_package_exports_all_models_and_enums() -> None:
    exported = {getattr(models, name) for name in models.__all__}

    assert set(TABLE_TO_MODEL.values()) <= exported
    assert {FeedbackType, FetchStatus} <= exported
    assert len(models.__all__) == 14


def test_configure_mappers_succeeds() -> None:
    configure_mappers()


def test_models_import_without_configuration_or_database() -> None:
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    code = (
        "import app.models\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "from app.db.base import Base\n"
        "configure_mappers()\n"
        "assert len(Base.metadata.tables) == 12\n"
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


# --- Columns ------------------------------------------------------------------


@pytest.mark.parametrize("table_name", sorted(EXPECTED_COLUMNS))
def test_columns_match_specification_exactly(table_name: str) -> None:
    assert list(table(table_name).columns.keys()) == list(EXPECTED_COLUMNS[table_name])


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [(t, c) for t, cols in sorted(EXPECTED_COLUMNS.items()) for c in cols],
)
def test_column_type_size_nullability_and_default(
    table_name: str, column_name: str
) -> None:
    type_class, size, nullable, default_sql = EXPECTED_COLUMNS[table_name][column_name]
    column = table(table_name).columns[column_name]

    assert type(column.type) is type_class
    assert column.nullable is nullable
    assert server_default_sql(column) == default_sql
    if type_class is String or type_class is Enum:
        assert column.type.length == size
    if type_class is Numeric:
        assert (column.type.precision, column.type.scale) == size
        assert column.type.asdecimal is True
    if type_class is DateTime:
        assert column.type.timezone is True


def test_every_datetime_column_is_timezone_aware() -> None:
    datetime_columns = [
        column
        for _, tbl in all_table_items()
        for column in tbl.columns
        if isinstance(column.type, DateTime)
    ]

    expected_count = sum(
        spec[0] is DateTime
        for columns in EXPECTED_COLUMNS.values()
        for spec in columns.values()
    )
    assert expected_count == 21
    assert len(datetime_columns) == expected_count
    assert all(column.type.timezone for column in datetime_columns)


def test_identity_primary_keys_are_bigint_generated_by_default() -> None:
    for table_name, tbl in all_table_items():
        if table_name == "article_categories":
            assert all(column.identity is None for column in tbl.columns)
            continue
        column = tbl.columns["id"]
        assert column.primary_key
        assert isinstance(column.type, BigInteger)
        assert column.identity is not None
        assert column.identity.always is False


def test_updated_at_columns_refresh_on_update() -> None:
    for table_name in ("users", "articles", "feedback"):
        column = table(table_name).columns["updated_at"]
        assert column.onupdate is not None
        assert compile_pg(column.onupdate.arg) == "now()"

    columns_with_onupdate = {
        (tbl.name, column.name)
        for _, tbl in all_table_items()
        for column in tbl.columns
        if column.onupdate is not None
    }
    assert columns_with_onupdate == {
        ("users", "updated_at"),
        ("articles", "updated_at"),
        ("feedback", "updated_at"),
    }


def test_user_preferences_are_binary_with_no_weight_column() -> None:
    columns = set(table("user_preferences").columns.keys())

    assert columns == {"id", "user_id", "category_id", "created_at"}
    assert not any(
        word in name for name in columns for word in ("weight", "score", "rank")
    )


# --- Primary keys, unique constraints, foreign keys ----------------------------


@pytest.mark.parametrize("table_name", sorted(EXPECTED_PRIMARY_KEYS))
def test_primary_keys(table_name: str) -> None:
    name, columns = EXPECTED_PRIMARY_KEYS[table_name]
    primary_key = table(table_name).primary_key

    assert primary_key.name == name
    assert tuple(primary_key.columns.keys()) == columns


@pytest.mark.parametrize("table_name", sorted(EXPECTED_UNIQUE_CONSTRAINTS))
def test_unique_constraints(table_name: str) -> None:
    actual = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in table(table_name).constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert actual == EXPECTED_UNIQUE_CONSTRAINTS[table_name]


def test_foreign_keys_targets_names_and_on_delete() -> None:
    actual = {}
    for table_name, tbl in all_table_items():
        for constraint in tbl.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                (column_name,) = constraint.column_keys
                (element,) = constraint.elements
                actual[(table_name, column_name)] = (
                    constraint.name,
                    element.target_fullname,
                    constraint.ondelete,
                )

    assert actual == EXPECTED_FOREIGN_KEYS


def test_every_foreign_key_column_is_not_null() -> None:
    for table_name, column_name in EXPECTED_FOREIGN_KEYS:
        assert table(table_name).columns[column_name].nullable is False


# --- CHECK constraints and enums ----------------------------------------------


def _check_constraints(table_name: str) -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in table(table_name).constraints
        if isinstance(constraint, CheckConstraint)
    }


@pytest.mark.parametrize("table_name", sorted(set(TABLE_TO_MODEL)))
def test_check_constraint_names_are_exactly_as_specified(table_name: str) -> None:
    expected = set(EXPECTED_CHECKS.get(table_name, {})) | set(
        EXPECTED_ENUM_CHECKS.get(table_name, {})
    )

    assert set(_check_constraints(table_name)) == expected


@pytest.mark.parametrize(
    ("table_name", "name"),
    [(t, n) for t, checks in sorted(EXPECTED_CHECKS.items()) for n in checks],
)
def test_check_constraint_sql(table_name: str, name: str) -> None:
    constraint = _check_constraints(table_name)[name]

    assert str(constraint.sqltext) == EXPECTED_CHECKS[table_name][name]


@pytest.mark.parametrize(
    ("table_name", "name"),
    [(t, n) for t, checks in sorted(EXPECTED_ENUM_CHECKS.items()) for n in checks],
)
def test_enum_check_constraints_compile_for_postgresql(
    table_name: str, name: str
) -> None:
    ddl = compile_pg(CreateTable(table(table_name)))

    assert EXPECTED_ENUM_CHECKS[table_name][name] in ddl


def test_enum_values() -> None:
    assert [member.value for member in FeedbackType] == ["LIKE", "DISLIKE", "DISMISS"]
    assert [member.value for member in FetchStatus] == [
        "RUNNING",
        "SUCCESS",
        "PARTIAL",
        "FAILED",
    ]


@pytest.mark.parametrize(
    ("table_name", "column_name", "enum_class"),
    [
        ("feedback", "feedback_type", FeedbackType),
        ("source_fetch_logs", "status", FetchStatus),
    ],
)
def test_enum_columns_are_non_native_varchar_with_check(
    table_name: str, column_name: str, enum_class: type
) -> None:
    column_type = table(table_name).columns[column_name].type

    assert column_type.enum_class is enum_class
    assert column_type.native_enum is False
    assert column_type.create_constraint is True
    assert column_type.length == 20
    assert column_type.enums == [member.value for member in enum_class]
    assert compile_pg(column_type) == "VARCHAR(20)"


def test_no_native_postgresql_enum_types_are_created() -> None:
    for _, tbl in all_table_items():
        assert "CREATE TYPE" not in compile_pg(CreateTable(tbl))


# --- Indexes ------------------------------------------------------------------


@pytest.mark.parametrize("table_name", sorted(set(TABLE_TO_MODEL)))
def test_indexes_are_exactly_as_specified(table_name: str) -> None:
    actual = {
        index.name: compile_pg(CreateIndex(index))
        for index in table(table_name).indexes
    }

    assert actual == EXPECTED_INDEXES.get(table_name, {})


def test_no_index_duplicates_a_primary_key_or_unique_constraint() -> None:
    for _, tbl in all_table_items():
        constraint_column_sets = {
            tuple(constraint.columns.keys())
            for constraint in tbl.constraints
            if isinstance(constraint, UniqueConstraint | PrimaryKeyConstraint)
        }
        for index in tbl.indexes:
            # A partial index covers a subset of rows, so it never duplicates a
            # full constraint index (e.g. ix_sources_active on id WHERE is_active).
            if index.dialect_options["postgresql"]["where"] is not None:
                continue
            index_columns = tuple(column.name for column in index.columns)
            assert index_columns not in constraint_column_sets, index.name


def test_no_column_level_index_or_unique_flags_create_unnamed_objects() -> None:
    for _, tbl in all_table_items():
        for column in tbl.columns:
            assert not column.index, f"{tbl.name}.{column.name}"
            assert not column.unique, f"{tbl.name}.{column.name}"


def test_all_identifiers_fit_postgresql_limit() -> None:
    names = []
    for table_name, tbl in all_table_items():
        names.append(table_name)
        names.extend(constraint.name for constraint in tbl.constraints)
        names.extend(index.name for index in tbl.indexes)

    assert all(names)  # every constraint and index is named
    assert max(len(name) for name in names) <= MAX_IDENTIFIER_LENGTH
    assert len(names) == len(set(names))  # schema-wide unique


# --- Relationships ------------------------------------------------------------


@pytest.mark.parametrize(
    "model", list(EXPECTED_RELATIONSHIPS), ids=lambda m: m.__name__
)
def test_relationships(model: type) -> None:
    relationships = inspect(model).relationships
    expected = EXPECTED_RELATIONSHIPS[model]

    assert set(relationships.keys()) == set(expected)
    for name, (target, direction, back, passive, deletes) in expected.items():
        relationship = relationships[name]
        assert relationship.mapper.class_ is target, name
        assert relationship.direction is direction, name
        assert relationship.back_populates == back, name
        assert relationship.passive_deletes == passive, name
        assert relationship.cascade.delete is deletes, name
        assert relationship.cascade.delete_orphan is deletes, name


def test_back_populates_are_symmetric() -> None:
    for model, relationships in EXPECTED_RELATIONSHIPS.items():
        for name, (target, *_rest) in relationships.items():
            back_name = inspect(model).relationships[name].back_populates
            back = inspect(target).relationships[back_name]
            assert back.back_populates == name
            assert back.mapper.class_ is model


def test_no_many_to_many_shortcuts_bypass_association_objects() -> None:
    for mapper in Base.registry.mappers:
        for relationship in mapper.relationships:
            assert relationship.secondary is None, relationship
            assert relationship.direction is not RelationshipDirection.MANYTOMANY


def test_association_objects_link_both_sides_in_memory() -> None:
    user = User(name="Ada", email="ada@example.com", password_hash="x")
    category = Category(name="Science", slug="science")
    source = Source(name="Feed", feed_url="https://example.com/rss", category=category)
    article = Article(
        canonical_url="https://example.com/a",
        original_url="https://example.com/a?ref=rss",
        title="A",
    )

    subscription = UserSource(user=user, source=source)
    mapping = ArticleSource(article=article, source=source, external_guid="g-1")
    classification = ArticleCategory(
        article=article, category=category, confidence_score=Decimal("0.8750")
    )
    preference = UserPreference(user=user, category=category)
    bookmark = Bookmark(user=user, article=article)
    history = ReadingHistory(user=user, article=article)
    feedback = Feedback(user=user, article=article, feedback_type=FeedbackType.LIKE)
    log = SourceFetchLog(source=source, status=FetchStatus.RUNNING)

    assert source in category.sources
    assert user.user_sources == [subscription] and source.user_sources == [subscription]
    assert source.article_sources == [mapping] == article.article_sources
    assert article.article_categories == [classification]
    assert category.article_categories == [classification]
    assert user.user_preferences == [preference] == category.user_preferences
    assert user.bookmarks == [bookmark] == article.bookmarks
    assert user.reading_history == [history] == article.reading_history
    assert user.feedback == [feedback] == article.feedback
    assert source.fetch_logs == [log]


def _foreign_key_for(relationship) -> ForeignKey:
    """The child-side ForeignKey that a one-to-many relationship is joined on."""
    ((parent_column, child_column),) = relationship.synchronize_pairs
    (foreign_key,) = (
        fk for fk in child_column.foreign_keys if fk.column is parent_column
    )
    return foreign_key


def test_database_deletion_policy_matches_orm_configuration() -> None:
    """Each parent collection's ORM settings agree with its FK's actual ON DELETE.

    Inspects the mapped relationships and the real foreign keys in the metadata:
    - ORM ``delete`` cascade is only allowed where the database also CASCADEs,
      and must be paired with ``passive_deletes=True`` (unloaded children are
      left to the database).
    - Without an ORM delete cascade, ``passive_deletes`` must be ``"all"`` so the
      ORM never NULLs the (NOT NULL) foreign key; the database's CASCADE or
      RESTRICT decides instead.
    - Every foreign key in the schema is covered by exactly one such collection.
    """
    covered_foreign_keys = set()

    for mapper in Base.registry.mappers:
        for relationship in mapper.relationships:
            if relationship.direction is not RelationshipDirection.ONETOMANY:
                continue
            foreign_key = _foreign_key_for(relationship)
            ondelete = foreign_key.constraint.ondelete
            label = f"{mapper.class_.__name__}.{relationship.key}"

            assert foreign_key.parent.nullable is False, label
            assert ondelete in {"CASCADE", "RESTRICT"}, label
            if relationship.cascade.delete:
                assert ondelete == "CASCADE", label
                assert relationship.cascade.delete_orphan, label
                assert relationship.passive_deletes is True, label
            else:
                assert relationship.passive_deletes == "all", label
            covered_foreign_keys.add(foreign_key)

    all_foreign_keys = {
        fk for tbl in Base.metadata.tables.values() for fk in tbl.foreign_keys
    }
    assert covered_foreign_keys == all_foreign_keys

    # The approved ORM delete cascade is exactly User's five owned collections,
    # each backed by an ON DELETE CASCADE foreign key to users.id.
    orm_delete_cascades = {
        (mapper.class_, relationship.key)
        for mapper in Base.registry.mappers
        for relationship in mapper.relationships
        if relationship.cascade.delete
    }
    assert orm_delete_cascades == {
        (User, "user_sources"),
        (User, "user_preferences"),
        (User, "bookmarks"),
        (User, "reading_history"),
        (User, "feedback"),
    }


# --- PostgreSQL DDL compilation (compile only, never executed) ---------------


@pytest.mark.parametrize("table_name", sorted(set(TABLE_TO_MODEL)))
def test_create_table_ddl_compiles_for_postgresql(table_name: str) -> None:
    ddl = compile_pg(CreateTable(table(table_name)))

    assert ddl.startswith(f"CREATE TABLE {table_name} (")
    for (fk_table, _column), (name, target, ondelete) in EXPECTED_FOREIGN_KEYS.items():
        if fk_table != table_name:
            continue
        ref_table, ref_column = target.split(".")
        assert f"CONSTRAINT {name} FOREIGN KEY" in ddl
        assert f"REFERENCES {ref_table} ({ref_column}) ON DELETE {ondelete}" in ddl


def test_ddl_identity_timestamp_and_default_rendering() -> None:
    users = compile_pg(CreateTable(table("users")))
    logs = compile_pg(CreateTable(table("source_fetch_logs")))
    categories = compile_pg(CreateTable(table("article_categories")))
    history = compile_pg(CreateTable(table("reading_history")))

    assert "id BIGINT GENERATED BY DEFAULT AS IDENTITY" in users
    assert "GENERATED ALWAYS" not in users
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in users
    assert "is_active BOOLEAN DEFAULT true NOT NULL" in users
    assert "articles_found INTEGER DEFAULT 0 NOT NULL" in logs
    assert "started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in logs
    assert "confidence_score NUMERIC(5, 4)," in categories
    assert "CONSTRAINT pk_article_categories PRIMARY KEY (article_id, category_id)" in (
        categories
    )
    assert "is_read BOOLEAN DEFAULT false NOT NULL" in history


# --- UTC helper ---------------------------------------------------------------


def test_utc_now_is_timezone_aware_utc() -> None:
    now = utc_now()

    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)
