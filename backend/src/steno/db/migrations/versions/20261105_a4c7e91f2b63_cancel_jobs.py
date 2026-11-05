"""a job can be canceled

Revision ID: a4c7e91f2b63
Revises: 5e1a7c3d9b20
Create Date: 2026-11-05 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c7e91f2b63"
down_revision: str | Sequence[str] | None = "5e1a7c3d9b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CHECK = "ck_ingestion_job_job_status"
BEFORE = "status IN ('queued', 'running', 'succeeded', 'failed')"
AFTER = "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')"


def upgrade() -> None:
    op.drop_constraint(op.f(CHECK), "ingestion_job", type_="check")
    op.create_check_constraint(op.f(CHECK), "ingestion_job", AFTER)
    op.add_column(
        "ingestion_job", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.execute("UPDATE ingestion_job SET status = 'failed' WHERE status = 'canceled'")
    op.drop_column("ingestion_job", "cancel_requested_at")
    op.drop_constraint(op.f(CHECK), "ingestion_job", type_="check")
    op.create_check_constraint(op.f(CHECK), "ingestion_job", BEFORE)
