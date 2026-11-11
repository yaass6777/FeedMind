from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.category import Category


class ArticleCategory(Base):
    """Classifies an article into a category, with an optional confidence score."""

    __tablename__ = "article_categories"
    __table_args__ = (
        PrimaryKeyConstraint("article_id", "category_id", name="pk_article_categories"),
        # NULL scores pass the check (CHECK treats NULL as not violated).
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="confidence_score_range",
        ),
        Index(
            "ix_article_categories_category_id_article_id", "category_id", "article_id"
        ),
    )

    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "articles.id",
            name="fk_article_categories_article_id_articles",
            ondelete="CASCADE",
        ),
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "categories.id",
            name="fk_article_categories_category_id_categories",
            ondelete="RESTRICT",
        ),
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    article: Mapped["Article"] = relationship(back_populates="article_categories")
    category: Mapped["Category"] = relationship(back_populates="article_categories")
