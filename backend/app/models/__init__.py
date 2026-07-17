"""SQLAlchemy models — DB shape only, no business logic (CODING.md §2).

Importing this package registers every table on Base.metadata; Alembic's
env.py relies on that for autogenerate diffs.
"""

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.citation import Citation
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript, ManuscriptArchive
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag, ReadinessReport

__all__ = [
    "AuditLog",
    "Base",
    "Citation",
    "CheckResult",
    "CheckRun",
    "Criterion",
    "Flag",
    "Instructor",
    "Manuscript",
    "ManuscriptArchive",
    "ReadinessReport",
    "Rubric",
]
