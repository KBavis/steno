from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base model for our tables. It has no __tablename__ of its own."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
