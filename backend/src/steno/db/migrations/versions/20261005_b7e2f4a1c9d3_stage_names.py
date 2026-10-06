"""stage names: resolve becomes assemble; parse is split into parse and extract

Revision ID: b7e2f4a1c9d3
Revises: da9a7b9abcce
Create Date: 2026-10-05 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2f4a1c9d3"
down_revision: str | Sequence[str] | None = "da9a7b9abcce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("clone", "deps", "parse", "resolve", "flows", "write", "cards")
NEW = ("clone", "deps", "parse", "extract", "assemble", "flows", "write", "cards")
CHECK = "ck_ingestion_stage_stage_name"


def _allow(stages: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{s}'" for s in stages)
    op.create_check_constraint(op.f(CHECK), "ingestion_stage", f"stage IN ({allowed})")


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(CHECK), "ingestion_stage", type_="check")
    op.execute("UPDATE ingestion_stage SET stage = 'assemble' WHERE stage = 'resolve'")
    _allow(NEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f(CHECK), "ingestion_stage", type_="check")
    op.execute("DELETE FROM ingestion_stage WHERE stage = 'extract'")
    op.execute("UPDATE ingestion_stage SET stage = 'resolve' WHERE stage = 'assemble'")
    _allow(OLD)
