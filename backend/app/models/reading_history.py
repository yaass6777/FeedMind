from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.user import User


class ReadingHistory(Base):
    """Per-user article open and read state.

    Opening and marking as read are separate actions. A row with both open
    timestamps NULL is valid (e.g. marked read before ever being opened).
    """

    __tablename__ = "reading_history"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "article_id", name="uq_reading_history_user_id_article_id"
        ),
        # 1. is_read is TRUE exactly when read_at is set.
        CheckConstraint(
            "(is_read AND read_at IS NOT NULL) OR (NOT is_read AND read_at IS NULL)",
            name="read_at_matches_is_read",
        ),
        # 2. The latest open cannot precede the first open.
        CheckConstraint(
            "first_opened_at IS NULL OR last_opened_at IS NULL"
            " OR last_opened_at >= first_opened_at",
            name="last_opened_not_before_first",
        ),
        # 3. Open timestamps are set together or not at all.
        CheckConstraint(
            "(first_opened_at IS NULL) = (last_opened_at IS NULL)",
            name="opened_timestamps_paired",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id", name="fk_reading_history_user_id_users", ondelete="CASCADE"
        ),
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "articles.id",
            name="fk_reading_history_article_id_articles",
            ondelete="CASCADE",
        ),
    )
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_read: Mapped[bool] = mapped_column(Boolean, server_default=false())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="reading_history")
    article: Mapped["Article"] = relationship(back_populates="reading_history")


# Reading history, most recently opened first; never-opened rows sort last.
Index(
    "ix_reading_history_user_id_last_opened_at",
    ReadingHistory.user_id,
    ReadingHistory.last_opened_at.desc().nulls_last(),
)
