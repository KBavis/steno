"""rule proposal question

Revision ID: 8b3e5d1f0a7c
Revises: 1669cfea554e
Create Date: 2026-11-03 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b3e5d1f0a7c"
down_revision: str | Sequence[str] | None = "1669cfea554e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("rule_proposal", sa.Column("question", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("rule_proposal", "question")
