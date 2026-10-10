"""one queued or running job per repository

Revision ID: e7a1c3d5f9b2
Revises: d6f0b2e8c4a9
Create Date: 2026-10-10 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a1c3d5f9b2"
down_revision: str | Sequence[str] | None = "d6f0b2e8c4a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # A repository with several jobs waiting keeps only its oldest one
    op.execute(
        """
        UPDATE ingestion_job SET status = 'failed', error = 'superseded: another job was queued'
        WHERE status = 'queued' AND id NOT IN (
            SELECT min(id) FROM ingestion_job
            WHERE status IN ('queued', 'running') GROUP BY repository_id
        )
        """
    )
    op.create_index(
        "uq_ingestion_job_active_repository",
        "ingestion_job",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_ingestion_job_active_repository", table_name="ingestion_job")
