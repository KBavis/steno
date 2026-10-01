from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class GitCommit(Base):
    __tablename__ = "git_commit"

    sha: Mapped[str] = mapped_column(primary_key=True)
