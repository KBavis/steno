"""identity registry and claim store

The identity registry (D81–D84): every application's permanent Steno ID, its names, its
lifecycle, admin links, and names no application matched. The claim store (D109): what
PLATFORM, DECLARED, and ADMIN witnesses say. And the identify and reconcile stages.

Revision ID: 7c4555b65880
Revises: e7a1c3d5f9b2
Create Date: 2026-10-14 00:01:53.115002

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7c4555b65880"
down_revision: str | Sequence[str] | None = "e7a1c3d5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGES = ("clone", "deps", "parse", "extract", "assemble", "flows", "write", "coverage", "cards")
NEW_STAGES = ("identify", "reconcile")
STAGE_CHECK = "ck_ingestion_stage_stage_name"


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _allow_stages(stages: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(STAGE_CHECK), "ingestion_stage", type_="check")
    allowed = ", ".join(f"'{s}'" for s in stages)
    op.create_check_constraint(op.f(STAGE_CHECK), "ingestion_stage", f"stage IN ({allowed})")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "application_identity",
        sa.Column("steno_id", sa.String(length=32), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=True),
        sa.Column("module_path", sa.Text(), nullable=True),
        sa.Column(
            "state", _enum("identity_state", "active", "removed", "deprecated"), nullable=False
        ),
        sa.Column("conflict_with", sa.String(length=32), nullable=True),
        sa.Column("first_seen_job", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_job", sa.BigInteger(), nullable=True),
        sa.Column("removed_job", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repository.id"],
            name=op.f("fk_application_identity_repository_id_repository"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("steno_id", name=op.f("pk_application_identity")),
    )
    op.create_index("ix_application_identity_canonical", "application_identity", ["canonical_name"])
    op.create_index(
        "ix_application_identity_location", "application_identity", ["repository_id", "module_path"]
    )
    op.create_table(
        "application_alias",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("steno_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slot", sa.String(length=32), nullable=False),
        sa.Column("witness_type", sa.String(length=16), nullable=False),
        sa.Column("source", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_past_name", sa.Boolean(), nullable=False),
        sa.Column("first_seen_job", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_job", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["steno_id"],
            ["application_identity.steno_id"],
            name=op.f("fk_application_alias_steno_id_application_identity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_alias")),
        sa.UniqueConstraint("steno_id", "name", "slot", name=op.f("uq_application_alias_steno_id")),
    )
    op.create_index("ix_application_alias_name", "application_alias", ["name"])
    op.create_table(
        "application_link",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("from_steno_id", sa.String(length=32), nullable=True),
        sa.Column("to_steno_id", sa.String(length=32), nullable=True),
        sa.Column("pending_name", sa.Text(), nullable=True),
        sa.Column("kind", _enum("link_kind", "rename", "same"), nullable=False),
        sa.Column("by", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("applied_job", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_link")),
    )
    op.create_table(
        "unmatched_name",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("status", _enum("unmatched_status", "open", "mapped", "ignored"), nullable=False),
        sa.Column("mapped_to", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unmatched_name")),
        sa.UniqueConstraint("name", name=op.f("uq_unmatched_name_name")),
    )
    op.create_table(
        "claim",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("facet", sa.String(length=32), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("subjects", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("witness_type", sa.String(length=16), nullable=False),
        sa.Column("witness", sa.String(length=200), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=True),
        sa.Column("commit", sa.String(length=64), nullable=True),
        sa.Column("file", sa.Text(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("generated", sa.Boolean(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("first_seen_job", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_job", sa.BigInteger(), nullable=True),
        sa.Column("removed_job", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repository.id"],
            name=op.f("fk_claim_repository_id_repository"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim")),
        sa.UniqueConstraint("repository_id", "witness", "key", name=op.f("uq_claim_repository_id")),
    )
    op.create_index(op.f("ix_claim_repository_id"), "claim", ["repository_id"])
    op.create_index("ix_claim_subjects", "claim", ["subjects"], postgresql_using="gin")
    _allow_stages((*STAGES, *NEW_STAGES))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM ingestion_stage WHERE stage IN ('identify', 'reconcile')")
    _allow_stages(STAGES)
    op.drop_index("ix_claim_subjects", table_name="claim", postgresql_using="gin")
    op.drop_index(op.f("ix_claim_repository_id"), table_name="claim")
    op.drop_table("claim")
    op.drop_table("unmatched_name")
    op.drop_table("application_link")
    op.drop_index("ix_application_alias_name", table_name="application_alias")
    op.drop_table("application_alias")
    op.drop_index("ix_application_identity_location", table_name="application_identity")
    op.drop_index("ix_application_identity_canonical", table_name="application_identity")
    op.drop_table("application_identity")
