"""allow the extract stage

The previous revision was first applied before `extract` was added to it, so databases
migrated then don't allow it. This sets the allowed stage names explicitly (a no-op where
they're already right).

Revision ID: e3c1a7d4b2f0
Revises: b7e2f4a1c9d3
Create Date: 2026-10-06 04:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3c1a7d4b2f0"
down_revision: str | Sequence[str] | None = "b7e2f4a1c9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGES = ("clone", "deps", "parse", "extract", "assemble", "flows", "write", "cards")
CHECK = "ck_ingestion_stage_stage_name"


def _allow(stages: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(CHECK), "ingestion_stage", type_="check")
    allowed = ", ".join(f"'{s}'" for s in stages)
    op.create_check_constraint(op.f(CHECK), "ingestion_stage", f"stage IN ({allowed})")


def upgrade() -> None:
    """Upgrade schema."""
    _allow(STAGES)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM ingestion_stage WHERE stage = 'extract'")
    _allow(tuple(s for s in STAGES if s != "extract"))
