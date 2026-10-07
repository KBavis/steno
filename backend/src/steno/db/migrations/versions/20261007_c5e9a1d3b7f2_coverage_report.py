"""coverage report: a coverage stage, item signal and label, unparsed files

Revision ID: c5e9a1d3b7f2
Revises: a4d8c2e6f1b3
Create Date: 2026-10-07 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e9a1d3b7f2"
down_revision: str | Sequence[str] | None = "a4d8c2e6f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGES = ("clone", "deps", "parse", "extract", "assemble", "flows", "write", "cards")
KINDS = ("external_call", "library", "unreachable", "unmet_join", "inconsistency", "config")


def _allow(table: str, column: str, check: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(check), table, type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(op.f(check), table, f"{column} IN ({allowed})")


def upgrade() -> None:
    """Upgrade schema."""
    _allow("ingestion_stage", "stage", "ck_ingestion_stage_stage_name", (*STAGES, "coverage"))
    _allow("coverage_item", "kind", "ck_coverage_item_coverage_kind", (*KINDS, "unparsed"))
    op.add_column(
        "coverage_item",
        sa.Column("signal", sa.String(length=32), server_default="", nullable=False),
    )
    op.add_column("coverage_item", sa.Column("label", sa.Text(), server_default="", nullable=False))
    op.create_index(
        "ix_coverage_item_key", "coverage_item", ["repository_id", "kind", "target_symbol"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_coverage_item_key", table_name="coverage_item")
    op.drop_column("coverage_item", "label")
    op.drop_column("coverage_item", "signal")
    op.execute("DELETE FROM coverage_item WHERE kind = 'unparsed'")
    op.execute("DELETE FROM ingestion_stage WHERE stage = 'coverage'")
    _allow("coverage_item", "kind", "ck_coverage_item_coverage_kind", KINDS)
    _allow("ingestion_stage", "stage", "ck_ingestion_stage_stage_name", STAGES)
