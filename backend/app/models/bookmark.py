from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.user import User


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "article_id", name="uq_bookmarks_user_id_article_id"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_bookmarks_user_id_users", ondelete="CASCADE"),
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "articles.id", name="fk_bookmarks_article_id_articles", ondelete="CASCADE"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="bookmarks")
    article: Mapped["Article"] = relationship(back_populates="bookmarks")


# Saved articles, newest first (master spec 10.5).
Index("ix_bookmarks_user_id_created_at", Bookmark.user_id, Bookmark.created_at.desc())
