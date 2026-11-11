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

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.user import User


class UserPreference(Base):
    """A binary category selection: the row exists or it does not (no weights)."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "category_id", name="uq_user_preferences_user_id_category_id"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id", name="fk_user_preferences_user_id_users", ondelete="CASCADE"
        ),
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "categories.id",
            name="fk_user_preferences_category_id_categories",
            ondelete="RESTRICT",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="user_preferences")
    category: Mapped["Category"] = relationship(back_populates="user_preferences")
