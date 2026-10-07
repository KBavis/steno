"""Postgres schema: what Steno is told and what it did (docs/ingestion.md §8).

One Steno deployment per organization (D34), so no table carries an organization id.
Neo4j must stay rebuildable from these tables plus git (D36).
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _enum(cls: type[StrEnum], name: str) -> Enum:
    # VARCHAR + CHECK instead of a native Postgres enum, so adding a value is a simple migration.
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(), primary_key=True)


_now = func.now()
Cost = Numeric(12, 6)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConnectorKind(StrEnum):
    BITBUCKET = "bitbucket"
    GITHUB = "github"
    GITLAB = "gitlab"


class RepositorySelection(StrEnum):
    INCLUDED = "included"
    DISCOVERED = "discovered"
    EXCLUDED = "excluded"


class RulePackSource(StrEnum):
    CORE = "core"
    ORG = "org"


class RulePackReason(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class JobTrigger(StrEnum):
    INITIAL = "initial"
    NIGHTLY = "nightly"  # D59: the nightly run, for a repository whose default branch moved
    MANUAL = "manual"


class JobMode(StrEnum):
    # Every run analyzes the whole repository and writes only what changed (D59)
    FULL = "full"
    DRY_RUN = "dry_run"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StageName(StrEnum):
    CLONE = "clone"
    DEPS = "deps"
    PARSE = "parse"
    EXTRACT = "extract"
    ASSEMBLE = "assemble"
    FLOWS = "flows"
    WRITE = "write"
    COVERAGE = "coverage"
    CARDS = "cards"


class StageStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class CoverageKind(StrEnum):
    """The coverage report's signals (docs/ingestion.md §4)."""

    EXTERNAL_CALL = "external_call"  # a call leaving first-party code that no rule explains
    LIBRARY = "library"  # a declared or imported library no rule pack covers
    UNREACHABLE = "unreachable"  # code (or an effect) no entry point reaches
    UNMET_JOIN = "unmet_join"  # a blank an assembler couldn't fill, an unresolved call
    INCONSISTENCY = "inconsistency"  # produced but never consumed, a call to a missing endpoint
    CONFIG = "config"  # a config key that looks like a host, URL, topic, or queue, unread
    UNPARSED = "unparsed"  # a file Steno couldn't read at all


class CoverageStatus(StrEnum):
    """Triage state, kept across runs (D62)."""

    UNEXPLAINED = "unexplained"
    IGNORED = "ignored"  # looked at: not I/O
    EXPLAINED = "explained"  # a rule now covers it


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


# ---------------------------------------------------------------------------
# Configuration: what Steno is told
# ---------------------------------------------------------------------------


class Organization(Base):
    """The organization this deployment serves: exactly one row (D34). Root of the space tree."""

    __tablename__ = "organization"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # Set when the admin finishes the onboarding flow; until then the UI shows it
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Connector(Base):
    """A source system plus a scope, e.g. a Bitbucket workspace (D30)."""

    __tablename__ = "connector"

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[ConnectorKind] = mapped_column(_enum(ConnectorKind, "connector_kind"))
    base_url: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # A reference into a secret manager. Secrets are never stored here.
    credentials_ref: Mapped[str | None] = mapped_column(Text)


class Space(Base):
    """An admin-declared space (D27). Can nest. Projected into Neo4j (D33)."""

    __tablename__ = "space"
    __table_args__ = (UniqueConstraint("parent_id", "name"),)

    id: Mapped[int] = _pk()
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)


class Repository(Base):
    __tablename__ = "repository"
    __table_args__ = (UniqueConstraint("connector_id", "name"),)

    id: Mapped[int] = _pk()
    connector_id: Mapped[int] = mapped_column(ForeignKey("connector.id", ondelete="CASCADE"))
    space_id: Mapped[int | None] = mapped_column(ForeignKey("space.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    clone_url: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    selection: Mapped[RepositorySelection] = mapped_column(
        _enum(RepositorySelection, "repository_selection"), default=RepositorySelection.INCLUDED
    )
    last_ingested_sha: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))


class GlossaryTerm(Base):
    """Human-declared vocabulary (D28)."""

    __tablename__ = "glossary_term"
    __table_args__ = (UniqueConstraint("space_id", "term"),)

    id: Mapped[int] = _pk()
    space_id: Mapped[int] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    term: Mapped[str] = mapped_column(String(200))
    target_node_id: Mapped[str | None] = mapped_column(Text)
    definition: Mapped[str | None] = mapped_column(Text)


class RulePack(Base):
    __tablename__ = "rule_pack"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(64))
    source: Mapped[RulePackSource] = mapped_column(_enum(RulePackSource, "rule_pack_source"))


class RepositoryRulePack(Base):
    __tablename__ = "repository_rule_pack"

    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), primary_key=True
    )
    rule_pack_id: Mapped[int] = mapped_column(
        ForeignKey("rule_pack.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[RulePackReason] = mapped_column(_enum(RulePackReason, "rule_pack_reason"))


# ---------------------------------------------------------------------------
# Operations: what Steno did
# ---------------------------------------------------------------------------


class IngestionJob(Base):
    """Every ingestion job. Also the work queue: workers claim with SKIP LOCKED (D35)."""

    __tablename__ = "ingestion_job"
    __table_args__ = (Index("ix_ingestion_job_queue", "status", "queued_at"),)

    id: Mapped[int] = _pk()
    repository_id: Mapped[int] = mapped_column(ForeignKey("repository.id", ondelete="CASCADE"))
    trigger: Mapped[JobTrigger] = mapped_column(_enum(JobTrigger, "job_trigger"))
    mode: Mapped[JobMode] = mapped_column(_enum(JobMode, "job_mode"))
    from_sha: Mapped[str | None] = mapped_column(String(64))
    to_sha: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.QUEUED
    )
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)


class IngestionStage(Base):
    """Timings, metrics, and cost per stage. Together they are the dry-run report."""

    __tablename__ = "ingestion_stage"

    id: Mapped[int] = _pk()
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_job.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[StageName] = mapped_column(_enum(StageName, "stage_name"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[StageStatus] = mapped_column(_enum(StageStatus, "stage_status"))
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    llm_cost: Mapped[Decimal] = mapped_column(Cost, default=Decimal(0))
    jev_cost: Mapped[Decimal] = mapped_column(Cost, default=Decimal(0))


class ScheduledRun(Base):
    """When each scheduled task last ran, so only one worker runs it per slot (D59)."""

    __tablename__ = "scheduled_run"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoverageItem(Base):
    """What no rule explained but looks like it matters."""

    __tablename__ = "coverage_item"

    id: Mapped[int] = _pk()
    job_id: Mapped[int] = mapped_column(ForeignKey("ingestion_job.id", ondelete="CASCADE"))
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[CoverageKind] = mapped_column(_enum(CoverageKind, "coverage_kind"))
    # Finer than kind (unknown_host, dropped_match, external_call, …), and how it reads
    signal: Mapped[str] = mapped_column(String(32), default="")
    label: Mapped[str] = mapped_column(Text, default="")
    # The key, with kind: the same gap in another run or repository is the same item (D62)
    target_symbol: Mapped[str] = mapped_column(Text)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    samples: Mapped[list[Any]] = mapped_column(default=list)
    # Where it occurs, by application (a repository can build several): name → occurrences
    applications: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[CoverageStatus] = mapped_column(
        _enum(CoverageStatus, "coverage_status"), default=CoverageStatus.UNEXPLAINED
    )


# ---------------------------------------------------------------------------
# Change history
# ---------------------------------------------------------------------------


class FactChange(Base):
    """One row per fact a job added, removed, or modified (D18)."""

    __tablename__ = "fact_change"

    id: Mapped[int] = _pk()
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_job.id", ondelete="CASCADE"), index=True
    )
    fact_id: Mapped[str] = mapped_column(Text, index=True)
    fact_type: Mapped[str] = mapped_column(String(64))
    change: Mapped[ChangeKind] = mapped_column(_enum(ChangeKind, "change_kind"))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    commit_sha: Mapped[str | None] = mapped_column(String(64))


class JobCommit(Base):
    __tablename__ = "job_commit"

    job_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_job.id", ondelete="CASCADE"), primary_key=True
    )
    commit_sha: Mapped[str] = mapped_column(String(64), primary_key=True)
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(Text)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Audit, cost, and caching
# ---------------------------------------------------------------------------


class LlmCall(Base):
    __tablename__ = "llm_call"

    id: Mapped[int] = _pk()
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_job.id", ondelete="SET NULL"), index=True
    )
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_stage.id", ondelete="SET NULL")
    )
    node_id: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[Decimal] = mapped_column(Cost, default=Decimal(0))
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class LlmOutput(Base):
    """Generated text, cached by input hash so unchanged inputs are never paid for twice (D36)."""

    __tablename__ = "llm_output"
    __table_args__ = (Index("ix_llm_output_lookup", "node_id", "kind", "input_hash"),)

    id: Mapped[int] = _pk()
    llm_call_id: Mapped[int] = mapped_column(ForeignKey("llm_call.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class JevDecision(Base):
    """Every decision's input, output, and confidence. Linked to a stage or a query request."""

    __tablename__ = "jev_decision"

    id: Mapped[int] = _pk()
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_stage.id", ondelete="SET NULL"), index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)
    confidence: Mapped[float | None]
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class ToolCall(Base):
    """Every MCP tool call, grouped by session, to measure round trips and latency."""

    __tablename__ = "tool_call"

    id: Mapped[int] = _pk()
    session_id: Mapped[str | None] = mapped_column(String(128), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    tool: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int | None] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)
