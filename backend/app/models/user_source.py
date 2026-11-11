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
    from app.models.source import Source
    from app.models.user import User


class UserSource(Base):
    """A user's subscription to (follow of) a source."""

    __tablename__ = "user_sources"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_id", name="uq_user_sources_user_id_source_id"
        ),
        Index("ix_user_sources_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id", name="fk_user_sources_user_id_users", ondelete="CASCADE"
        ),
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "sources.id", name="fk_user_sources_source_id_sources", ondelete="CASCADE"
        ),
    )
    followed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="user_sources")
    source: Mapped["Source"] = relationship(back_populates="user_sources")
