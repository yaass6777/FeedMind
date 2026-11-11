from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import FetchStatus, string_enum

if TYPE_CHECKING:
    from app.models.source import Source


class SourceFetchLog(Base):
    """One ingestion attempt for a source. Retained; restricts source deletion."""

    __tablename__ = "source_fetch_logs"
    __table_args__ = (
        CheckConstraint("articles_found >= 0", name="articles_found_non_negative"),
        CheckConstraint("articles_added >= 0", name="articles_added_non_negative"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completed_not_before_started",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "sources.id",
            name="fk_source_fetch_logs_source_id_sources",
            ondelete="RESTRICT",
        ),
    )
    # VARCHAR(20) + CHECK ck_source_fetch_logs_status.
    status: Mapped[FetchStatus] = mapped_column(string_enum(FetchStatus, "status"))
    http_status_code: Mapped[int | None] = mapped_column(Integer)
    articles_found: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    articles_added: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped["Source"] = relationship(back_populates="fetch_logs")


# Fetch monitoring, newest attempt first (master spec 10.5).
Index(
    "ix_source_fetch_logs_source_id_started_at",
    SourceFetchLog.source_id,
    SourceFetchLog.started_at.desc(),
)
