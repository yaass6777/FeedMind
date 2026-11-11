"""Category seed data on real PostgreSQL (feedmind_test only; opt-in).

State tests read the committed seed rows. Migration tests run Alembic on the
test's own connection inside a transaction that is always rolled back: Alembic
treats it as an external transaction and never commits. Sequence positions are
not transactional, so the round-trip test restores the categories sequence to
MAX(id) itself (exactly what the migration does) before finishing.
"""

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import BACKEND_DIR
from sqlalchemy import Connection, text

pytestmark = pytest.mark.integration

INITIAL_REVISION = "1c74d9d04571"
SEED_REVISION = "61c116438233"
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
OTHER_APPLICATION_TABLES = (
    "users",
    "sources",
    "articles",
    "user_sources",
    "article_sources",
    "article_categories",
    "user_preferences",
    "bookmarks",
    "reading_history",
    "feedback",
    "source_fetch_logs",
)


@pytest.fixture
def alembic_on(test_db: Connection) -> Config:
    """Alembic config that runs migrations on the rolled-back test connection."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = test_db
    config.attributes["configure_logger"] = False
    return config


def categories(connection: Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            text("SELECT id, name, slug FROM categories ORDER BY id")
        )
    ]


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def next_category_id(connection: Connection) -> int:
    """The ID the identity sequence will generate next, without consuming it."""
    sequence = connection.execute(
        text("SELECT pg_get_serial_sequence('categories', 'id')")
    ).scalar_one()
    last_value, is_called = connection.execute(
        text(f"SELECT last_value, is_called FROM {sequence}")
    ).one()
    return last_value + 1 if is_called else last_value


def realign_category_sequence(connection: Connection) -> None:
    connection.execute(
        text(
            "SELECT setval(pg_get_serial_sequence('categories', 'id'), "
            "(SELECT MAX(id) FROM categories))"
        )
    )


# --- Seeded state ---------------------------------------------------------------


def test_database_is_at_seed_revision(test_db: Connection) -> None:
    assert test_db.execute(text("SELECT current_database()")).scalar() == (
        "feedmind_test"
    )
    assert current_revision(test_db) == SEED_REVISION


def test_exactly_the_eight_specified_categories(test_db: Connection) -> None:
    assert categories(test_db) == SPEC_CATEGORIES


def test_seeded_descriptions_are_null_and_created_at_is_set(
    test_db: Connection,
) -> None:
    rows = test_db.execute(
        text("SELECT description, created_at FROM categories ORDER BY id")
    ).all()

    assert len(rows) == 8
    for description, created_at in rows:
        assert description is None
        assert created_at is not None
        assert created_at.tzinfo is not None


def test_seeded_ids_names_and_slugs_are_unique(test_db: Connection) -> None:
    counts = test_db.execute(
        text(
            "SELECT count(*), count(DISTINCT id), count(DISTINCT name), "
            "count(DISTINCT slug) FROM categories"
        )
    ).one()

    assert tuple(counts) == (8, 8, 8, 8)


def test_identity_sequence_is_never_behind_existing_ids(test_db: Connection) -> None:
    max_id = test_db.execute(text("SELECT MAX(id) FROM categories")).scalar_one()

    assert next_category_id(test_db) > max_id


def test_no_other_application_data_exists(test_db: Connection) -> None:
    for table_name in OTHER_APPLICATION_TABLES:
        count = test_db.execute(text(f"SELECT count(*) FROM {table_name}")).scalar()
        assert count == 0, table_name


# --- Migration behaviour (rolled back) ---------------------------------------


@pytest.mark.parametrize(
    ("conflict_id", "conflict_name", "conflict_slug"),
    [
        (1, "Something Else", "something-else"),  # conflicting ID
        (900, "Technology", "tech-elsewhere"),  # conflicting name
        (900, "Tech Elsewhere", "technology"),  # conflicting slug
    ],
    ids=["id", "name", "slug"],
)
def test_upgrade_fails_clearly_on_conflicting_category(
    test_db: Connection,
    alembic_on: Config,
    conflict_id: int,
    conflict_name: str,
    conflict_slug: str,
) -> None:
    command.downgrade(alembic_on, INITIAL_REVISION)
    test_db.execute(
        text("INSERT INTO categories (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": conflict_id, "name": conflict_name, "slug": conflict_slug},
    )

    with (
        pytest.raises(RuntimeError, match=r"Cannot seed categories: 1 existing row"),
        test_db.begin_nested(),
    ):
        command.upgrade(alembic_on, SEED_REVISION)

    # Nothing was inserted or skipped, and the revision did not advance.
    assert categories(test_db) == [(conflict_id, conflict_name, conflict_slug)]
    assert current_revision(test_db) == INITIAL_REVISION


@pytest.mark.parametrize(
    "reference",
    ["source", "user_preference", "article_category"],
)
def test_downgrade_refuses_while_seed_category_is_referenced(
    test_db: Connection, alembic_on: Config, reference: str
) -> None:
    if reference == "source":
        test_db.execute(
            text(
                "INSERT INTO sources (name, feed_url, category_id) "
                "VALUES ('S', 'https://seed-ref.example.test/rss', 3)"
            )
        )
    elif reference == "user_preference":
        user_id = test_db.execute(
            text(
                "INSERT INTO users (name, email, password_hash) "
                "VALUES ('U', 'seed-ref@example.test', 'x') RETURNING id"
            )
        ).scalar_one()
        test_db.execute(
            text("INSERT INTO user_preferences (user_id, category_id) VALUES (:u, 3)"),
            {"u": user_id},
        )
    else:
        article_id = test_db.execute(
            text(
                "INSERT INTO articles (canonical_url, original_url, title) "
                "VALUES ('https://seed-ref.example.test/a', "
                "'https://seed-ref.example.test/a', 'A') RETURNING id"
            )
        ).scalar_one()
        test_db.execute(
            text(
                "INSERT INTO article_categories (article_id, category_id) "
                "VALUES (:a, 3)"
            ),
            {"a": article_id},
        )

    with (
        pytest.raises(RuntimeError, match="Cannot remove seed categories"),
        test_db.begin_nested(),
    ):
        command.downgrade(alembic_on, INITIAL_REVISION)

    assert categories(test_db) == SPEC_CATEGORIES
    assert current_revision(test_db) == SEED_REVISION


def test_downgrade_deletes_only_exact_seed_rows(
    test_db: Connection, alembic_on: Config
) -> None:
    test_db.execute(text("UPDATE categories SET name = 'Sports & Games' WHERE id = 8"))
    test_db.execute(
        text("INSERT INTO categories (id, name, slug) VALUES (900, 'Extra', 'extra')")
    )

    command.downgrade(alembic_on, INITIAL_REVISION)

    # The modified seed row and the unrelated row are left untouched.
    assert categories(test_db) == [
        (8, "Sports & Games", "sports"),
        (900, "Extra", "extra"),
    ]
    assert current_revision(test_db) == INITIAL_REVISION


def test_downgrade_and_reupgrade_round_trip(
    test_db: Connection, alembic_on: Config
) -> None:
    command.downgrade(alembic_on, INITIAL_REVISION)

    assert categories(test_db) == []
    assert current_revision(test_db) == INITIAL_REVISION

    command.upgrade(alembic_on, "head")

    assert categories(test_db) == SPEC_CATEGORIES
    assert current_revision(test_db) == SEED_REVISION
    assert next_category_id(test_db) == 9

    # A real generated ID is 9 (inserted in a savepoint and rolled back).
    with test_db.begin_nested() as savepoint:
        generated = test_db.execute(
            text(
                "INSERT INTO categories (name, slug) "
                "VALUES ('Generated', 'generated') RETURNING id"
            )
        ).scalar_one()
        savepoint.rollback()
    assert generated == 9

    # nextval() is not transactional: realign the sequence to MAX(id) (= 8),
    # leaving it exactly where the migration put it.
    realign_category_sequence(test_db)
    assert next_category_id(test_db) == 9
