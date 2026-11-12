"""remove Steno's rule drafting job: curation runs, rule proposals, their states

Rules are drafted locally by the rule-pack-author skill; Steno keeps patterns and their
triage states only. A pattern in a drafting state becomes open; an approved one, proposed.

Revision ID: 9d1e6b3c5a20
Revises: 4c2d9e7a1b6f
Create Date: 2026-11-03 20:00:00.000000

"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "9d1e6b3c5a20"
down_revision: str | Sequence[str] | None = "4c2d9e7a1b6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "ck_coverage_pattern_pattern_state"
NEW = ("open", "proposed", "covered", "ignored", "expected")
OLD = (
    "open", "queued", "drafting", "proposed", "approved", "covered", "rejected", "ignored",
    "blocked_vocabulary", "needs_person", "expected",
)  # fmt: skip
# The migrations that built the tables, replayed in order on downgrade
BUILT_BY = (
    "20261022_e844517b163d_curation_runs_and_rule_proposals.py",
    "20261022_401b7b128fb3_proposal_pack.py",
    "20261022_51c79084c962_proposal_published.py",
    "20261026_1cd65e0585ed_rule_proposal_name.py",
    "20261103_8b3e5d1f0a7c_rule_proposal_question.py",
)


def _allow(states: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(CHECK), "coverage_pattern", type_="check")
    allowed = ", ".join(f"'{s}'" for s in states)
    op.create_check_constraint(op.f(CHECK), "coverage_pattern", f"state IN ({allowed})")


def upgrade() -> None:
    op.execute("UPDATE coverage_pattern SET state = 'proposed' WHERE state = 'approved'")
    op.execute(
        "UPDATE coverage_pattern SET state = 'open' WHERE state IN "
        "('queued', 'drafting', 'rejected', 'blocked_vocabulary', 'needs_person')"
    )
    _allow(NEW)
    op.drop_table("rule_proposal")
    op.drop_table("curation_run")


def downgrade() -> None:
    _allow(OLD)
    here = Path(__file__).parent
    for name in BUILT_BY:
        spec = importlib.util.spec_from_file_location(name, here / name)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade()
