"""seed_initial_categories

Revision ID: 61c116438233
Revises: 1c74d9d04571
Create Date: 2026-11-11 16:29:48.751347+00:00

Data migration: inserts the eight initial categories from the master
specification (Section 10.3) with explicit IDs 1-8 and NULL descriptions, then
advances the categories identity sequence so the next generated ID is 9.

- Conflicts are never ignored: any existing row sharing a seed ID, name or slug
  aborts the upgrade with a clear error (and the plain INSERT, which has no
  ON CONFLICT clause, would also fail on the unique/primary-key constraints).
- Downgrade deletes only rows matching a seed's exact (id, name, slug), and
  refuses to run while any seeded category is still referenced (the foreign
  keys are ON DELETE RESTRICT; nothing is ever cascade-deleted).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "61c116438233"
down_revision: str | Sequence[str] | None = "1c74d9d04571"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (id, name, slug) exactly as in master specification Section 10.3.
SEED_CATEGORIES: tuple[tuple[int, str, str], ...] = (
    (1, "Technology", "technology"),
    (2, "Programming", "programming"),
    (3, "Artificial Intelligence", "artificial-intelligence"),
    (4, "Finance", "finance"),
    (5, "Science", "science"),
    (6, "Business", "business"),
    (7, "General News", "general-news"),
    (8, "Sports", "sports"),
)

# Lightweight table definitions: migrations must not depend on the current
# application models, which may change after this revision.
categories = sa.table(
    "categories",
    sa.column("id", sa.BigInteger),
    sa.column("name", sa.String),
    sa.column("slug", sa.String),
    sa.column("description", sa.Text),
)
CATEGORY_REFERENCES = (
    ("sources", "category_id"),
    ("article_categories", "category_id"),
    ("user_preferences", "category_id"),
)

SEED_IDS = [category_id for category_id, _, _ in SEED_CATEGORIES]
SEED_NAMES = [name for _, name, _ in SEED_CATEGORIES]
SEED_SLUGS = [slug for _, _, slug in SEED_CATEGORIES]


def _offline() -> bool:
    """True for `--sql` runs, where there is no connection to inspect data."""
    return op.get_context().as_sql


def _exact_seed_match() -> sa.ColumnElement[bool]:
    return sa.or_(
        *(
            sa.and_(
                categories.c.id == category_id,
                categories.c.name == name,
                categories.c.slug == slug,
            )
            for category_id, name, slug in SEED_CATEGORIES
        )
    )


def upgrade() -> None:
    """Insert the eight seed categories and advance the identity sequence."""
    if not _offline():
        conflicts = (
            op.get_bind()
            .execute(
                sa.select(categories.c.id, categories.c.name, categories.c.slug)
                .where(
                    sa.or_(
                        categories.c.id.in_(SEED_IDS),
                        categories.c.name.in_(SEED_NAMES),
                        categories.c.slug.in_(SEED_SLUGS),
                    )
                )
                .order_by(categories.c.id)
            )
            .all()
        )
        if conflicts:
            details = "; ".join(
                f"id={row.id} name={row.name!r} slug={row.slug!r}" for row in conflicts
            )
            raise RuntimeError(
                f"Cannot seed categories: {len(conflicts)} existing row(s) conflict "
                f"with a seed ID, name or slug ({details}). Resolve them manually; "
                "this migration never overwrites or skips existing categories."
            )

    op.bulk_insert(
        categories,
        [
            {"id": category_id, "name": name, "slug": slug, "description": None}
            for category_id, name, slug in SEED_CATEGORIES
        ],
    )

    # Explicit IDs do not advance an identity sequence. Point it at the highest
    # existing ID so the next generated ID is MAX(id) + 1 (9 for a fresh table)
    # and can never collide with an existing row.
    op.execute(
        sa.text(
            "SELECT setval(pg_get_serial_sequence('categories', 'id'), "
            "(SELECT MAX(id) FROM categories))"
        )
    )


def downgrade() -> None:
    """Delete only the exact seeded rows; refuse while any is still referenced."""
    if not _offline():
        bind = op.get_bind()
        references = []
        for table_name, column_name in CATEGORY_REFERENCES:
            referencing = sa.table(table_name, sa.column(column_name, sa.BigInteger))
            count = bind.execute(
                sa.select(sa.func.count()).where(
                    referencing.c[column_name].in_(SEED_IDS)
                )
            ).scalar_one()
            if count:
                references.append(f"{table_name}: {count}")
        if references:
            raise RuntimeError(
                "Cannot remove seed categories while they are referenced "
                f"({', '.join(references)}). Remove or reassign those rows first; "
                "this migration never cascade-deletes dependent data."
            )

    op.execute(categories.delete().where(_exact_seed_match()))
