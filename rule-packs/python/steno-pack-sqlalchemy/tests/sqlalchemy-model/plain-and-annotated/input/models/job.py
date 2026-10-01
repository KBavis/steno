from sqlalchemy import ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from .base import Base


class Job(Base):
    __tablename__: str = "job"

    id: Mapped["UUID"] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    project_id: Mapped["UUID"] = mapped_column(ForeignKey("project.id"), nullable=False)
