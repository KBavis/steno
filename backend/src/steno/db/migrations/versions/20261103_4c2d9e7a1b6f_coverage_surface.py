"""coverage on the surface: quiet items and patterns, the expected state

Revision ID: 4c2d9e7a1b6f
Revises: 8b3e5d1f0a7c
Create Date: 2026-11-03 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c2d9e7a1b6f"
down_revision: str | Sequence[str] | None = "8b3e5d1f0a7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = ("unexplained", "ignored", "explained")
STATE = (
    "open", "queued", "drafting", "proposed", "approved", "covered", "rejected", "ignored",
    "blocked_vocabulary", "needs_person",
)  # fmt: skip


def _allow(table: str, name: str, column: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(op.f(name), table, f"{column} IN ({allowed})")


def upgrade() -> None:
    op.add_column(
        "coverage_item", sa.Column("quiet", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "coverage_pattern",
        sa.Column("quiet", sa.Boolean(), server_default="false", nullable=False),
    )
    _allow("coverage_item", "ck_coverage_item_coverage_status", "status", (*STATUS, "expected"))
    _allow("coverage_pattern", "ck_coverage_pattern_pattern_state", "state", (*STATE, "expected"))


def downgrade() -> None:
    op.execute("UPDATE coverage_item SET status = 'ignored' WHERE status = 'expected'")
    op.execute("UPDATE coverage_pattern SET state = 'ignored' WHERE state = 'expected'")
    _allow("coverage_item", "ck_coverage_item_coverage_status", "status", STATUS)
    _allow("coverage_pattern", "ck_coverage_pattern_pattern_state", "state", STATE)
    op.drop_column("coverage_pattern", "quiet")
    op.drop_column("coverage_item", "quiet")
