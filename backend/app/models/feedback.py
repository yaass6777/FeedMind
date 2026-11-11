from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import FeedbackType, string_enum

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.user import User


class Feedback(Base):
    """A user's single current feedback on an article (updated in place)."""

    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "article_id", name="uq_feedback_user_id_article_id"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_feedback_user_id_users", ondelete="CASCADE"),
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "articles.id", name="fk_feedback_article_id_articles", ondelete="CASCADE"
        ),
    )
    # VARCHAR(20) + CHECK ck_feedback_feedback_type.
    feedback_type: Mapped[FeedbackType] = mapped_column(
        string_enum(FeedbackType, "feedback_type")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="feedback")
    article: Mapped["Article"] = relationship(back_populates="feedback")
