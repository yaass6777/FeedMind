from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.article_category import ArticleCategory
    from app.models.article_source import ArticleSource
    from app.models.bookmark import Bookmark
    from app.models.feedback import Feedback
    from app.models.reading_history import ReadingHistory


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("canonical_url", name="uq_articles_canonical_url"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    canonical_url: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Dependent rows are removed by database ON DELETE CASCADE only when an
    # article is deliberately deleted; the ORM never deletes them itself.
    article_sources: Mapped[list["ArticleSource"]] = relationship(
        back_populates="article", passive_deletes="all"
    )
    article_categories: Mapped[list["ArticleCategory"]] = relationship(
        back_populates="article", passive_deletes="all"
    )
    bookmarks: Mapped[list["Bookmark"]] = relationship(
        back_populates="article", passive_deletes="all"
    )
    reading_history: Mapped[list["ReadingHistory"]] = relationship(
        back_populates="article", passive_deletes="all"
    )
    feedback: Mapped[list["Feedback"]] = relationship(
        back_populates="article", passive_deletes="all"
    )


# Chronological feed (master spec 10.5); undated articles sort last.
Index("ix_articles_published_at", Article.published_at.desc().nulls_last())
