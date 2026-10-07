"""coverage items by application

Revision ID: d6f0b2e8c4a9
Revises: c5e9a1d3b7f2
Create Date: 2026-10-07 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d6f0b2e8c4a9"
down_revision: str | Sequence[str] | None = "c5e9a1d3b7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "coverage_item",
        sa.Column(
            "applications",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("coverage_item", "applications")
