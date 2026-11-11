"""Real PostgreSQL constraint, deletion and default behaviour (opt-in).

Runs against feedmind_test only (see conftest.py safeguards). Every test runs in
a transaction that is rolled back, and every expected failure runs inside a
SAVEPOINT, so no rows are left behind. Assertions check PostgreSQL's reported
constraint name, proving the intended constraint (not some other) fired.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import Connection, delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError

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

pytestmark = pytest.mark.integration

_sequence = count(1)
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def unique_token() -> str:
    return f"t{next(_sequence)}"


def add(connection: Connection, model: type, **values) -> dict:
    """Insert one row with Core (so the database applies defaults) and return it."""
    table = model.__table__
    return dict(
        connection.execute(insert(table).values(**values).returning(table))
        .mappings()
        .one()
    )


def assert_violation(connection: Connection, statement, constraint: str) -> None:
    """The statement must fail with exactly the named constraint."""
    with pytest.raises(IntegrityError) as exc_info, connection.begin_nested():
        connection.execute(statement)
    assert exc_info.value.orig.diag.constraint_name == constraint


def row_count(connection: Connection, model: type, **filters) -> int:
    statement = select(func.count()).select_from(model)
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    return connection.execute(statement).scalar_one()


# --- Factories ----------------------------------------------------------------


def make_category(connection: Connection) -> dict:
    token = unique_token()
    return add(connection, Category, name=f"Category {token}", slug=f"category-{token}")


def make_user(connection: Connection) -> dict:
    token = unique_token()
    return add(
        connection,
        User,
        name="Test User",
        email=f"{token}@example.test",
        password_hash="not-a-real-hash",
    )


def make_source(connection: Connection, category_id: int) -> dict:
    token = unique_token()
    return add(
        connection,
        Source,
        name=f"Source {token}",
        feed_url=f"https://{token}.example.test/rss",
        category_id=category_id,
    )


def make_article(connection: Connection) -> dict:
    token = unique_token()
    return add(
        connection,
        Article,
        canonical_url=f"https://example.test/{token}",
        original_url=f"https://example.test/{token}?ref=rss",
        title=f"Article {token}",
    )


@pytest.fixture
def world(test_db: Connection) -> dict:
    """One category, user, source and article to hang test rows on."""
    category = make_category(test_db)
    return {
        "category": category,
        "user": make_user(test_db),
        "source": make_source(test_db, category["id"]),
        "article": make_article(test_db),
    }


# --- Unique constraints ---------------------------------------------------------


def test_duplicate_user_email_rejected(test_db: Connection, world: dict) -> None:
    statement = insert(User).values(
        name="Other", email=world["user"]["email"], password_hash="x"
    )
    assert_violation(test_db, statement, "uq_users_email")


def test_duplicate_source_feed_url_rejected(test_db: Connection, world: dict) -> None:
    statement = insert(Source).values(
        name="Other",
        feed_url=world["source"]["feed_url"],
        category_id=world["category"]["id"],
    )
    assert_violation(test_db, statement, "uq_sources_feed_url")


def test_duplicate_canonical_url_rejected(test_db: Connection, world: dict) -> None:
    statement = insert(Article).values(
        canonical_url=world["article"]["canonical_url"],
        original_url="https://example.test/other",
        title="Other",
    )
    assert_violation(test_db, statement, "uq_articles_canonical_url")


def test_duplicate_user_source_rejected(test_db: Connection, world: dict) -> None:
    values = {"user_id": world["user"]["id"], "source_id": world["source"]["id"]}
    add(test_db, UserSource, **values)
    assert_violation(
        test_db,
        insert(UserSource).values(**values),
        "uq_user_sources_user_id_source_id",
    )


def test_duplicate_article_source_rejected(test_db: Connection, world: dict) -> None:
    values = {"article_id": world["article"]["id"], "source_id": world["source"]["id"]}
    add(test_db, ArticleSource, **values)
    assert_violation(
        test_db,
        insert(ArticleSource).values(**values),
        "uq_article_sources_article_id_source_id",
    )


def test_duplicate_external_guid_per_source_rejected(
    test_db: Connection, world: dict
) -> None:
    source_id = world["source"]["id"]
    add(
        test_db,
        ArticleSource,
        article_id=world["article"]["id"],
        source_id=source_id,
        external_guid="guid-1",
    )
    statement = insert(ArticleSource).values(
        article_id=make_article(test_db)["id"],
        source_id=source_id,
        external_guid="guid-1",
    )
    assert_violation(test_db, statement, "uq_article_sources_source_id_external_guid")


def test_null_external_guids_are_not_unique_constrained(
    test_db: Connection, world: dict
) -> None:
    source_id = world["source"]["id"]
    for _ in range(3):
        add(
            test_db,
            ArticleSource,
            article_id=make_article(test_db)["id"],
            source_id=source_id,
            external_guid=None,
        )
    assert row_count(test_db, ArticleSource, source_id=source_id) == 3


def test_same_guid_allowed_in_different_sources(
    test_db: Connection, world: dict
) -> None:
    other_source = make_source(test_db, world["category"]["id"])
    for source in (world["source"], other_source):
        add(
            test_db,
            ArticleSource,
            article_id=world["article"]["id"],
            source_id=source["id"],
            external_guid="shared-guid",
        )


def test_duplicate_article_category_rejected(test_db: Connection, world: dict) -> None:
    values = {
        "article_id": world["article"]["id"],
        "category_id": world["category"]["id"],
    }
    add(test_db, ArticleCategory, **values)
    assert_violation(
        test_db, insert(ArticleCategory).values(**values), "pk_article_categories"
    )


def test_duplicate_user_preference_rejected(test_db: Connection, world: dict) -> None:
    values = {"user_id": world["user"]["id"], "category_id": world["category"]["id"]}
    add(test_db, UserPreference, **values)
    assert_violation(
        test_db,
        insert(UserPreference).values(**values),
        "uq_user_preferences_user_id_category_id",
    )


def test_duplicate_bookmark_rejected(test_db: Connection, world: dict) -> None:
    values = {"user_id": world["user"]["id"], "article_id": world["article"]["id"]}
    add(test_db, Bookmark, **values)
    assert_violation(
        test_db, insert(Bookmark).values(**values), "uq_bookmarks_user_id_article_id"
    )


def test_duplicate_reading_history_rejected(test_db: Connection, world: dict) -> None:
    values = {"user_id": world["user"]["id"], "article_id": world["article"]["id"]}
    add(test_db, ReadingHistory, **values)
    assert_violation(
        test_db,
        insert(ReadingHistory).values(**values),
        "uq_reading_history_user_id_article_id",
    )


def test_duplicate_feedback_rejected(test_db: Connection, world: dict) -> None:
    values = {"user_id": world["user"]["id"], "article_id": world["article"]["id"]}
    add(test_db, Feedback, feedback_type=FeedbackType.LIKE, **values)
    statement = insert(Feedback).values(feedback_type=FeedbackType.DISLIKE, **values)
    assert_violation(test_db, statement, "uq_feedback_user_id_article_id")


# --- CHECK constraints ----------------------------------------------------------


@pytest.mark.parametrize("score", [Decimal("-0.0001"), Decimal("1.0001")])
def test_confidence_score_out_of_range_rejected(
    test_db: Connection, world: dict, score: Decimal
) -> None:
    statement = insert(ArticleCategory).values(
        article_id=world["article"]["id"],
        category_id=world["category"]["id"],
        confidence_score=score,
    )
    assert_violation(test_db, statement, "ck_article_categories_confidence_score_range")


@pytest.mark.parametrize("score", [Decimal(0), Decimal(1), Decimal("0.5"), None])
def test_confidence_score_boundaries_and_null_accepted(
    test_db: Connection, world: dict, score: Decimal | None
) -> None:
    row = add(
        test_db,
        ArticleCategory,
        article_id=world["article"]["id"],
        category_id=world["category"]["id"],
        confidence_score=score,
    )
    assert row["confidence_score"] == score


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("articles_found", "ck_source_fetch_logs_articles_found_non_negative"),
        ("articles_added", "ck_source_fetch_logs_articles_added_non_negative"),
    ],
)
def test_negative_fetch_counts_rejected(
    test_db: Connection, world: dict, column: str, constraint: str
) -> None:
    statement = insert(SourceFetchLog).values(
        source_id=world["source"]["id"], status=FetchStatus.SUCCESS, **{column: -1}
    )
    assert_violation(test_db, statement, constraint)


def test_invalid_feedback_type_rejected_by_database(
    test_db: Connection, world: dict
) -> None:
    # Raw SQL bypasses SQLAlchemy's Python-side enum validation.
    statement = text(
        "INSERT INTO feedback (user_id, article_id, feedback_type) "
        "VALUES (:user_id, :article_id, 'LOVE')"
    ).bindparams(user_id=world["user"]["id"], article_id=world["article"]["id"])
    assert_violation(test_db, statement, "ck_feedback_feedback_type")


def test_lowercase_feedback_type_rejected_by_database(
    test_db: Connection, world: dict
) -> None:
    statement = text(
        "INSERT INTO feedback (user_id, article_id, feedback_type) "
        "VALUES (:user_id, :article_id, 'like')"
    ).bindparams(user_id=world["user"]["id"], article_id=world["article"]["id"])
    assert_violation(test_db, statement, "ck_feedback_feedback_type")


def test_invalid_fetch_status_rejected_by_database(
    test_db: Connection, world: dict
) -> None:
    statement = text(
        "INSERT INTO source_fetch_logs (source_id, status) VALUES (:source_id, 'DONE')"
    ).bindparams(source_id=world["source"]["id"])
    assert_violation(test_db, statement, "ck_source_fetch_logs_status")


@pytest.mark.parametrize("value", list(FeedbackType))
def test_every_feedback_type_accepted(
    test_db: Connection, world: dict, value: FeedbackType
) -> None:
    row = add(
        test_db,
        Feedback,
        user_id=world["user"]["id"],
        article_id=world["article"]["id"],
        feedback_type=value,
    )
    assert row["feedback_type"] is value


@pytest.mark.parametrize("value", list(FetchStatus))
def test_every_fetch_status_accepted(
    test_db: Connection, world: dict, value: FetchStatus
) -> None:
    row = add(test_db, SourceFetchLog, source_id=world["source"]["id"], status=value)
    assert row["status"] is value


@pytest.mark.parametrize(
    ("is_read", "read_at"),
    [(True, None), (False, T0)],
    ids=["read_without_read_at", "unread_with_read_at"],
)
def test_inconsistent_read_state_rejected(
    test_db: Connection, world: dict, is_read: bool, read_at: datetime | None
) -> None:
    statement = insert(ReadingHistory).values(
        user_id=world["user"]["id"],
        article_id=world["article"]["id"],
        is_read=is_read,
        read_at=read_at,
    )
    assert_violation(test_db, statement, "ck_reading_history_read_at_matches_is_read")


@pytest.mark.parametrize(
    ("first", "last"),
    [(T0, None), (None, T0)],
    ids=["first_without_last", "last_without_first"],
)
def test_unpaired_open_timestamps_rejected(
    test_db: Connection, world: dict, first: datetime | None, last: datetime | None
) -> None:
    statement = insert(ReadingHistory).values(
        user_id=world["user"]["id"],
        article_id=world["article"]["id"],
        first_opened_at=first,
        last_opened_at=last,
    )
    assert_violation(test_db, statement, "ck_reading_history_opened_timestamps_paired")


def test_last_opened_before_first_opened_rejected(
    test_db: Connection, world: dict
) -> None:
    statement = insert(ReadingHistory).values(
        user_id=world["user"]["id"],
        article_id=world["article"]["id"],
        first_opened_at=T0,
        last_opened_at=T0 - timedelta(seconds=1),
    )
    assert_violation(
        test_db, statement, "ck_reading_history_last_opened_not_before_first"
    )


@pytest.mark.parametrize(
    "values",
    [
        {},  # created before any open (both open timestamps NULL)
        {"is_read": True, "read_at": T0},  # read without ever being opened
        {"first_opened_at": T0, "last_opened_at": T0},
        {
            "first_opened_at": T0,
            "last_opened_at": T0 + timedelta(hours=1),
            "is_read": True,
            "read_at": T0 + timedelta(minutes=5),
        },
    ],
    ids=["never_opened", "read_unopened", "opened_once", "opened_and_read"],
)
def test_valid_reading_history_states_accepted(
    test_db: Connection, world: dict, values: dict
) -> None:
    add(
        test_db,
        ReadingHistory,
        user_id=world["user"]["id"],
        article_id=world["article"]["id"],
        **values,
    )


def test_completed_before_started_rejected(test_db: Connection, world: dict) -> None:
    statement = insert(SourceFetchLog).values(
        source_id=world["source"]["id"],
        status=FetchStatus.FAILED,
        started_at=T0,
        completed_at=T0 - timedelta(seconds=1),
    )
    assert_violation(
        test_db, statement, "ck_source_fetch_logs_completed_not_before_started"
    )


# --- Foreign keys and deletion behaviour ---------------------------------------

USER_OWNED = (UserSource, UserPreference, Bookmark, ReadingHistory, Feedback)
ARTICLE_DEPENDENTS = (
    ArticleSource,
    ArticleCategory,
    Bookmark,
    ReadingHistory,
    Feedback,
)


def give_user_all_five_collections(connection: Connection, user: dict, world: dict):
    user_id = user["id"]
    add(connection, UserSource, user_id=user_id, source_id=world["source"]["id"])
    add(
        connection,
        UserPreference,
        user_id=user_id,
        category_id=world["category"]["id"],
    )
    add(connection, Bookmark, user_id=user_id, article_id=world["article"]["id"])
    add(connection, ReadingHistory, user_id=user_id, article_id=world["article"]["id"])
    add(
        connection,
        Feedback,
        user_id=user_id,
        article_id=world["article"]["id"],
        feedback_type=FeedbackType.LIKE,
    )


def test_deleting_user_cascades_to_all_five_collections(
    test_db: Connection, world: dict
) -> None:
    doomed, other = world["user"], make_user(test_db)
    give_user_all_five_collections(test_db, doomed, world)
    give_user_all_five_collections(test_db, other, world)

    test_db.execute(delete(User).where(User.id == doomed["id"]))

    for model in USER_OWNED:
        assert row_count(test_db, model, user_id=doomed["id"]) == 0, model.__name__
        assert row_count(test_db, model, user_id=other["id"]) == 1, model.__name__
    # Shared catalogue rows are untouched.
    assert row_count(test_db, Source, id=world["source"]["id"]) == 1
    assert row_count(test_db, Article, id=world["article"]["id"]) == 1


def test_deleting_article_cascades_to_mappings_and_interactions(
    test_db: Connection, world: dict
) -> None:
    article_id = world["article"]["id"]
    kept = make_article(test_db)
    for target in (world["article"], kept):
        add(
            test_db,
            ArticleSource,
            article_id=target["id"],
            source_id=world["source"]["id"],
        )
        add(
            test_db,
            ArticleCategory,
            article_id=target["id"],
            category_id=world["category"]["id"],
        )
        add(test_db, Bookmark, user_id=world["user"]["id"], article_id=target["id"])
        add(
            test_db,
            ReadingHistory,
            user_id=world["user"]["id"],
            article_id=target["id"],
        )
        add(
            test_db,
            Feedback,
            user_id=world["user"]["id"],
            article_id=target["id"],
            feedback_type=FeedbackType.DISMISS,
        )

    test_db.execute(delete(Article).where(Article.id == article_id))

    for model in ARTICLE_DEPENDENTS:
        assert row_count(test_db, model, article_id=article_id) == 0, model.__name__
        assert row_count(test_db, model, article_id=kept["id"]) == 1, model.__name__
    assert row_count(test_db, User, id=world["user"]["id"]) == 1
    assert row_count(test_db, Source, id=world["source"]["id"]) == 1
    assert row_count(test_db, Category, id=world["category"]["id"]) == 1


def test_deleting_source_removes_subscriptions_when_unrestricted(
    test_db: Connection, world: dict
) -> None:
    source_id = world["source"]["id"]
    add(test_db, UserSource, user_id=world["user"]["id"], source_id=source_id)
    add(test_db, UserSource, user_id=make_user(test_db)["id"], source_id=source_id)

    test_db.execute(delete(Source).where(Source.id == source_id))

    assert row_count(test_db, Source, id=source_id) == 0
    assert row_count(test_db, UserSource, source_id=source_id) == 0
    assert row_count(test_db, User, id=world["user"]["id"]) == 1


def test_deleting_source_with_article_mappings_rejected(
    test_db: Connection, world: dict
) -> None:
    source_id = world["source"]["id"]
    add(test_db, UserSource, user_id=world["user"]["id"], source_id=source_id)
    add(test_db, ArticleSource, article_id=world["article"]["id"], source_id=source_id)

    assert_violation(
        test_db,
        delete(Source).where(Source.id == source_id),
        "fk_article_sources_source_id_sources",
    )
    # The failed delete removed nothing, including the cascading subscriptions.
    assert row_count(test_db, Source, id=source_id) == 1
    assert row_count(test_db, UserSource, source_id=source_id) == 1


def test_deleting_source_with_fetch_logs_rejected(
    test_db: Connection, world: dict
) -> None:
    source_id = world["source"]["id"]
    add(test_db, SourceFetchLog, source_id=source_id, status=FetchStatus.SUCCESS)

    assert_violation(
        test_db,
        delete(Source).where(Source.id == source_id),
        "fk_source_fetch_logs_source_id_sources",
    )
    assert row_count(test_db, SourceFetchLog, source_id=source_id) == 1


@pytest.mark.parametrize(
    ("reference", "constraint"),
    [
        ("source", "fk_sources_category_id_categories"),
        ("article_category", "fk_article_categories_category_id_categories"),
        ("user_preference", "fk_user_preferences_category_id_categories"),
    ],
)
def test_deleting_referenced_category_rejected(
    test_db: Connection, world: dict, reference: str, constraint: str
) -> None:
    category_id = make_category(test_db)["id"]
    if reference == "source":
        make_source(test_db, category_id)
    elif reference == "article_category":
        add(
            test_db,
            ArticleCategory,
            article_id=world["article"]["id"],
            category_id=category_id,
        )
    else:
        add(
            test_db,
            UserPreference,
            user_id=world["user"]["id"],
            category_id=category_id,
        )

    assert_violation(
        test_db, delete(Category).where(Category.id == category_id), constraint
    )
    assert row_count(test_db, Category, id=category_id) == 1


def test_deleting_unreferenced_category_allowed(test_db: Connection) -> None:
    category_id = make_category(test_db)["id"]

    test_db.execute(delete(Category).where(Category.id == category_id))

    assert row_count(test_db, Category, id=category_id) == 0


def test_foreign_keys_reject_missing_parents(test_db: Connection, world: dict) -> None:
    statement = insert(Bookmark).values(user_id=-1, article_id=world["article"]["id"])
    assert_violation(test_db, statement, "fk_bookmarks_user_id_users")


# --- Defaults, identity and timestamps ------------------------------------------


def assert_aware_utc(value: datetime) -> None:
    assert isinstance(value, datetime)
    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(0)


def test_database_defaults_and_aware_timestamps(
    test_db: Connection, world: dict
) -> None:
    user, source, article, category = (
        world["user"],
        world["source"],
        world["article"],
        world["category"],
    )
    subscription = add(test_db, UserSource, user_id=user["id"], source_id=source["id"])
    mapping = add(
        test_db, ArticleSource, article_id=article["id"], source_id=source["id"]
    )
    preference = add(
        test_db, UserPreference, user_id=user["id"], category_id=category["id"]
    )
    bookmark = add(test_db, Bookmark, user_id=user["id"], article_id=article["id"])
    history = add(test_db, ReadingHistory, user_id=user["id"], article_id=article["id"])
    feedback = add(
        test_db,
        Feedback,
        user_id=user["id"],
        article_id=article["id"],
        feedback_type=FeedbackType.LIKE,
    )
    log = add(
        test_db, SourceFetchLog, source_id=source["id"], status=FetchStatus.RUNNING
    )

    # Boolean and count defaults.
    assert user["is_active"] is True
    assert source["is_active"] is True
    assert history["is_read"] is False
    assert (log["articles_found"], log["articles_added"]) == (0, 0)

    # now() defaults: present, timezone-aware, UTC.
    for value in (
        user["created_at"],
        user["updated_at"],
        source["created_at"],
        article["ingested_at"],
        article["created_at"],
        article["updated_at"],
        category["created_at"],
        subscription["followed_at"],
        mapping["first_seen_at"],
        preference["created_at"],
        bookmark["created_at"],
        feedback["created_at"],
        feedback["updated_at"],
        log["started_at"],
    ):
        assert_aware_utc(value)

    # Nullable timestamps stay NULL when not supplied.
    assert history["first_opened_at"] is None
    assert history["read_at"] is None
    assert log["completed_at"] is None
    assert article["published_at"] is None


def test_identity_ids_are_generated_and_increasing(test_db: Connection) -> None:
    first, second = make_user(test_db), make_user(test_db)

    assert isinstance(first["id"], int) and first["id"] > 0
    assert second["id"] > first["id"]


def test_explicit_category_ids_allowed_by_default_identity(
    test_db: Connection,
) -> None:
    """GENERATED BY DEFAULT permits explicit IDs (needed for seeding 1-8)."""
    row = add(test_db, Category, id=999_001, name="Explicit", slug="explicit-id")

    assert row["id"] == 999_001


def test_aware_timestamps_round_trip_as_utc(test_db: Connection, world: dict) -> None:
    supplied = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
    row = add(
        test_db,
        ReadingHistory,
        user_id=world["user"]["id"],
        article_id=world["article"]["id"],
        first_opened_at=supplied,
        last_opened_at=supplied,
    )

    assert row["first_opened_at"] == supplied
    assert_aware_utc(row["first_opened_at"])
