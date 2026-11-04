"""organization settings: what the organization chooses for its deployment (the LLM that
writes summaries, D143); set in the Settings page now, by onboarding later

Revision ID: 5e1a7c3d9b20
Revises: 9d1e6b3c5a20
Create Date: 2026-11-03 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5e1a7c3d9b20"
down_revision: str | Sequence[str] | None = "9d1e6b3c5a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organization", "settings")
