from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.article_source import ArticleSource
    from app.models.category import Category
    from app.models.source_fetch_log import SourceFetchLog
    from app.models.user_source import UserSource


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("feed_url", name="uq_sources_feed_url"),)

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(150))
    feed_url: Mapped[str] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Primary category; deleting a referenced category is restricted.
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "categories.id",
            name="fk_sources_category_id_categories",
            ondelete="RESTRICT",
        ),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    category: Mapped["Category"] = relationship(back_populates="sources")
    # Subscriptions are removed by the database (ON DELETE CASCADE) if a source
    # is deliberately deleted. Article mappings and fetch logs RESTRICT deletion.
    # passive_deletes="all": the ORM never nulls or deletes children itself.
    user_sources: Mapped[list["UserSource"]] = relationship(
        back_populates="source", passive_deletes="all"
    )
    article_sources: Mapped[list["ArticleSource"]] = relationship(
        back_populates="source", passive_deletes="all"
    )
    fetch_logs: Mapped[list["SourceFetchLog"]] = relationship(
        back_populates="source", passive_deletes="all"
    )


# Worker selection of active sources (master spec 10.5).
Index("ix_sources_active", Source.id, postgresql_where=Source.is_active == true())
