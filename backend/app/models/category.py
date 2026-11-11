from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Identity,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.article_category import ArticleCategory
    from app.models.source import Source
    from app.models.user_preference import UserPreference


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("name", name="uq_categories_name"),
        UniqueConstraint("slug", name="uq_categories_slug"),
    )

    # BY DEFAULT identity allows the seed data to insert explicit IDs 1-8.
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Category deletion is RESTRICTed while referenced; passive_deletes="all"
    # stops the ORM from nulling foreign keys so the database enforces it.
    sources: Mapped[list["Source"]] = relationship(
        back_populates="category", passive_deletes="all"
    )
    article_categories: Mapped[list["ArticleCategory"]] = relationship(
        back_populates="category", passive_deletes="all"
    )
    user_preferences: Mapped[list["UserPreference"]] = relationship(
        back_populates="category", passive_deletes="all"
    )
