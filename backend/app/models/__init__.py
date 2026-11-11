"""ORM models package.

Importing ``app.models`` registers all 12 tables on ``Base.metadata``; Alembic's
``migrations/env.py`` imports this package before autogenerate. When adding a
model, import it here and add it to ``__all__``.
"""

from app.models.article import Article
from app.models.article_category import ArticleCategory
from app.models.article_source import ArticleSource
from app.models.bookmark import Bookmark
from app.models.category import Category
from app.models.enums import FeedbackType, FetchStatus
from app.models.feedback import Feedback
from app.models.reading_history import ReadingHistory
from app.models.source import Source
from app.models.source_fetch_log import SourceFetchLog
from app.models.user import User
from app.models.user_preference import UserPreference
from app.models.user_source import UserSource

__all__ = [
    "Article",
    "ArticleCategory",
    "ArticleSource",
    "Bookmark",
    "Category",
    "Feedback",
    "FeedbackType",
    "FetchStatus",
    "ReadingHistory",
    "Source",
    "SourceFetchLog",
    "User",
    "UserPreference",
    "UserSource",
]
