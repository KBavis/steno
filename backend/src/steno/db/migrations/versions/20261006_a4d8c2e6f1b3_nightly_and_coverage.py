"""nightly ingestion and coverage triage

D59: jobs are `initial`, `nightly`, or `manual`, and every run is `full` (or a dry run); a
`scheduled_run` row records when the nightly run last happened. D62: coverage items carry
the report's signal kinds and a triage state (unexplained / ignored / explained).

Revision ID: a4d8c2e6f1b3
Revises: e3c1a7d4b2f0
Create Date: 2026-10-06 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4d8c2e6f1b3"
down_revision: str | Sequence[str] | None = "e3c1a7d4b2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, constraint, old values, new values, old → new renames)
CHANGES = [
    (
        "ingestion_job",
        "trigger",
        "ck_ingestion_job_job_trigger",
        ("initial", "merge", "manual"),
        ("initial", "nightly", "manual"),
        {"merge": "nightly"},
    ),
    (
        "ingestion_job",
        "mode",
        "ck_ingestion_job_job_mode",
        ("full", "incremental", "dry_run"),
        ("full", "dry_run"),
        {"incremental": "full"},
    ),
    (
        "coverage_item",
        "kind",
        "ck_coverage_item_coverage_kind",
        ("call_site", "annotation"),
        ("external_call", "library", "unreachable", "unmet_join", "inconsistency", "config"),
        {"call_site": "external_call", "annotation": "unreachable"},
    ),
    (
        "coverage_item",
        "status",
        "ck_coverage_item_coverage_status",
        ("open", "ignored", "covered"),
        ("unexplained", "ignored", "explained"),
        {"open": "unexplained", "covered": "explained"},
    ),
]


def _migrate(forward: bool) -> None:
    for table, column, check, old, new, renames in CHANGES:
        allowed = new if forward else old
        # Going back, undo only renames to a value that didn't exist before (incremental →
        # full can't be undone: every full job would become incremental)
        mapping = renames if forward else {v: k for k, v in renames.items() if v not in old}
        op.drop_constraint(op.f(check), table, type_="check")
        for frm, to in mapping.items():
            op.execute(f"UPDATE {table} SET {column} = '{to}' WHERE {column} = '{frm}'")
        values = ", ".join(f"'{v}'" for v in allowed)
        op.create_check_constraint(op.f(check), table, f"{column} IN ({values})")


def upgrade() -> None:
    """Upgrade schema."""
    _migrate(forward=True)
    op.create_table(
        "scheduled_run",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_scheduled_run")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("scheduled_run")
    # Values with no old equivalent (library, unmet_join, …) fall back to the closest one
    op.execute(
        "UPDATE coverage_item SET kind = 'external_call' "
        "WHERE kind NOT IN ('external_call', 'unreachable')"
    )
    _migrate(forward=False)
