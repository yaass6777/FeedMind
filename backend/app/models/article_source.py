from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.source import Source


class ArticleSource(Base):
    """Links one canonical article to each RSS source it appeared in."""

    __tablename__ = "article_sources"
    __table_args__ = (
        UniqueConstraint(
            "article_id", "source_id", name="uq_article_sources_article_id_source_id"
        ),
        Index("ix_article_sources_source_id_article_id", "source_id", "article_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "articles.id",
            name="fk_article_sources_article_id_articles",
            ondelete="CASCADE",
        ),
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "sources.id",
            name="fk_article_sources_source_id_sources",
            ondelete="RESTRICT",
        ),
    )
    external_guid: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["Article"] = relationship(back_populates="article_sources")
    source: Mapped["Source"] = relationship(back_populates="article_sources")


# A source's GUIDs are unique; entries without a GUID are unconstrained.
Index(
    "uq_article_sources_source_id_external_guid",
    ArticleSource.source_id,
    ArticleSource.external_guid,
    unique=True,
    postgresql_where=ArticleSource.external_guid.is_not(None),
)
