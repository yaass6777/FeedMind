"""Unit tests for the category seed data migration (no database needed).

The migration module is loaded directly, and upgrade()/downgrade() are compiled
to SQL with an offline (as_sql) migration context: nothing connects anywhere.
"""

import importlib.util
import io
import re
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

BACKEND_DIR = Path(__file__).resolve().parents[1]
(MIGRATION_PATH,) = (BACKEND_DIR / "migrations" / "versions").glob(
    "*61c116438233_seed_initial_categories.py"
)

# Master specification, Section 10.3 "Seed categories".
SPEC_CATEGORIES = [
    (1, "Technology", "technology"),
    (2, "Programming", "programming"),
    (3, "Artificial Intelligence", "artificial-intelligence"),
    (4, "Finance", "finance"),
    (5, "Science", "science"),
    (6, "Business", "business"),
    (7, "General News", "general-news"),
    (8, "Sports", "sports"),
]


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offline_sql(function_name: str) -> list[str]:
    """Compile upgrade()/downgrade() to PostgreSQL statements without connecting."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer, "literal_binds": True},
    )
    module = load_migration()
    with Operations.context(context):
        getattr(module, function_name)()
    return [s.strip() for s in buffer.getvalue().split(";") if s.strip()]


def test_revision_follows_initial_schema() -> None:
    module = load_migration()

    assert module.revision == "61c116438233"
    assert module.down_revision == "1c74d9d04571"


def test_seed_data_matches_master_specification_exactly() -> None:
    assert list(load_migration().SEED_CATEGORIES) == SPEC_CATEGORIES


def test_seed_ids_names_and_slugs_are_unique() -> None:
    seeds = load_migration().SEED_CATEGORIES

    for position in range(3):
        values = [seed[position] for seed in seeds]
        assert len(values) == len(set(values)) == 8


def test_seed_values_fit_column_limits_and_slug_format() -> None:
    for _, name, slug in load_migration().SEED_CATEGORIES:
        assert 0 < len(name) <= 100
        assert 0 < len(slug) <= 100
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", slug), slug


def test_upgrade_inserts_eight_explicit_rows_with_null_descriptions() -> None:
    inserts = [s for s in offline_sql("upgrade") if s.startswith("INSERT")]

    assert inserts == [
        "INSERT INTO categories (id, name, slug, description) "
        f"VALUES ({category_id}, '{name}', '{slug}', NULL)"
        for category_id, name, slug in SPEC_CATEGORIES
    ]


def test_upgrade_never_ignores_conflicts() -> None:
    sql = " ".join(offline_sql("upgrade")).upper()

    assert "ON CONFLICT" not in sql
    assert "DO NOTHING" not in sql
    assert "UPDATE" not in sql


def test_upgrade_advances_identity_sequence_to_max_id() -> None:
    statements = offline_sql("upgrade")

    assert statements[-1] == (
        "SELECT setval(pg_get_serial_sequence('categories', 'id'), "
        "(SELECT MAX(id) FROM categories))"
    )
    assert len(statements) == 9  # 8 inserts + setval; nothing else


def test_downgrade_deletes_only_exact_seed_rows() -> None:
    (statement,) = offline_sql("downgrade")

    assert statement.startswith("DELETE FROM categories WHERE ")
    expected_conditions = [
        f"categories.id = {category_id} AND categories.name = '{name}' "
        f"AND categories.slug = '{slug}'"
        for category_id, name, slug in SPEC_CATEGORIES
    ]
    where = statement.removeprefix("DELETE FROM categories WHERE ")
    assert where.split(" OR ") == expected_conditions
    assert "CASCADE" not in statement.upper()
    assert "TRUNCATE" not in statement.upper()


@pytest.mark.parametrize("function_name", ["upgrade", "downgrade"])
def test_offline_mode_skips_data_inspection(function_name: str) -> None:
    """In --sql mode there is no connection, so no SELECT checks are emitted."""
    statements = offline_sql(function_name)

    assert not any(s.startswith("SELECT ") and "setval" not in s for s in statements)
