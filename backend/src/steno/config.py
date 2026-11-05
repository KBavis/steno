"""Runtime settings, read from the environment (prefix `STENO_`) or a `.env` file."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STENO_", env_file=".env", extra="ignore")

    # Postgres: what Steno is told and what it did (D33)
    database_url: str = "postgresql+psycopg://steno:steno@localhost:5434/steno"

    # Neo4j: what Steno knows
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "steno-dev-password"
    neo4j_database: str = "neo4j"

    # Where ingestion workers clone repositories; deleted after each run
    workspace_dir: Path = Path("/tmp/steno-workspaces")

    # How a repository's graph is written to Neo4j (D135): in many small transactions, so Neo4j's
    # memory depends on the size of one, not of the repository. A transaction holds at most this
    # many MB of properties and this many rows; one that still runs out of Neo4j's memory is
    # halved and tried again.
    graph_write_chunk_mb: int = 32
    graph_write_node_rows: int = 2000
    graph_write_edge_rows: int = 5000

    # How MCP serves a flow (D140): `trace` (its steps in order, from the stored trace) or
    # `reach` (its trigger, effects, and the functions it reaches, without order or nesting),
    # for the stage A ablation that decides whether flows keep their ordered trace
    flow_view: Literal["trace", "reach"] = "trace"

    @property
    def index_dir(self) -> Path:
        """Each Java resolver's on-disk index (D127), removed when it's done; one a stopped
        worker left is removed by `steno job recover`."""
        return self.workspace_dir / "indexes"

    # Rule packs (docs/how-it-works/ingestion/rules-and-packs.md): the core packs here, plus
    # any folders in `rule_pack_dirs` (an organization's packs, kept in its own repository).
    # STENO_RULE_PACK_DIRS: folders separated by commas
    rule_packs_dir: Path = Path(__file__).resolve().parents[3] / "rule-packs"
    rule_pack_dirs: Annotated[list[Path], NoDecode] = []

    @field_validator("rule_pack_dirs", mode="before")
    @classmethod
    def _split_dirs(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(p.strip()).expanduser() for p in value.split(",") if p.strip()]
        return value

    # Packs Steno acts as if it didn't have, everywhere: for measuring rule curation against a
    # pack we wrote (rule-curation §8).
    # STENO_DISABLED_PACKS: pack names separated by commas
    disabled_packs: Annotated[list[str], NoDecode] = []

    # The organization's own code, for the coverage report's library gaps (D146): Java packages
    # (`com.acme`), coordinates (`com.acme:*`), or Python packages (`acme_`). A library under
    # one is internal; the repository's own package roots count too, so it can stay empty.
    # STENO_INTERNAL_PREFIXES: prefixes separated by commas. Onboarding takes it over in v1.
    internal_prefixes: Annotated[list[str], NoDecode] = []

    @field_validator("disabled_packs", "internal_prefixes", mode="before")
    @classmethod
    def _split_names(cls, value: object) -> object:
        if isinstance(value, str):
            return [n.strip() for n in value.split(",") if n.strip()]
        return value

    # The JVM helper for Java symbol resolution (D69): `make resolver` builds it here
    resolver_jvm_dir: Path = (
        Path(__file__).resolve().parents[3]
        / "resolver-jvm"
        / "build"
        / "install"
        / "steno-resolver"
    )
    java_bin: str = "java"
    # The JVM helper's heap ceiling per job: what a Java repository can cost in memory
    resolver_jvm_max_heap: str = "2g"
    # Seconds the helper may take: at least this, plus `resolver_jvm_timeout_per_file` for each
    # Java file, so a very large monorepo isn't cut off for being large
    resolver_jvm_timeout: int = 1800
    resolver_jvm_timeout_per_file: float = 0.25
    # Resolve method bodies this many files at a time against a declaration index of the whole
    # repository, so memory stays bounded (D127); 0 resolves everything in one pass
    resolver_jvm_batch_files: int = 0
    # A repository with more Java files than this resolves in batches from its first run: one
    # pass ran a 5,500-file repository out of memory at a 2 GB heap (D127)
    resolver_jvm_batch_above_files: int = 3000

    # A ceiling on ingestion jobs running at once across every machine. Within it, each
    # machine takes only what fits its memory and CPUs (D128; docs: storage.md)
    max_parallel_jobs: int = 16
    # Worker processes `steno worker` starts (each runs one job at a time); 0: one per CPU
    # this machine may use, up to 8
    worker_processes: int = 0
    # The share of this machine's memory (a container's limit when there is one) that jobs may
    # reserve; the rest is left to the system and the API
    worker_memory_fraction: float = 0.8
    # What a job reserves for a repository never measured before (MB): its first run
    job_memory_default_mb: int = 3072
    # Memory (MB) kept free beyond what a job takes: other things on the machine grow while it
    # runs (Neo4j, Postgres, the system). A job starts only if it fits the free memory minus
    # this, even when it's the only one (D134)
    memory_headroom_mb: int = 768
    # How long (minutes) a job's Java step waits for free memory before failing (D134)
    memory_wait_minutes: int = 30
    # This machine's name for counting its jobs (default: the host name)
    worker_host: str = ""

    # Worker queue polling
    worker_poll_seconds: float = 2.0

    # Nightly ingestion (D59): each night at this time (UTC, HH:MM), queue a run for every
    # repository whose default branch moved since it was last ingested
    nightly_enabled: bool = True
    nightly_at: str = "02:00"

    # Generated text (D143): summaries for applications, spaces, and the organization. The
    # provider: claude_agent_sdk (this machine's Claude Code login, or ANTHROPIC_API_KEY; for
    # development), or fake (tests). An organization's gateway (Azure) comes later. The model
    # is still Open, decided from a dry run's token counts
    llm_provider: str = "claude_agent_sdk"
    llm_model: str = "claude-sonnet-5-5"
    # Generate summaries in the job's cards stage (off: only `steno summaries` writes them)
    summaries_enabled: bool = True
    # Who makes routing's decisions at query time (D142): stand_in (ranks by search matches,
    # uncalibrated), jev, or disabled, which switches every decision off
    decision_backend: str = "stand_in"
    # Jev, directly from TypeSafe by default, or through an organization's gateway: its address
    # (Steno adds /systemone), and how it wants the key sent. The key is a reference (env:NAME)
    jev_base_url: str = "https://api.typesafe.ai/v1"
    jev_api_key_ref: str | None = "env:JEV_API_KEY"
    jev_auth_header: str = "Authorization"
    jev_auth_scheme: str = "Bearer"  # empty for a header that takes the key alone (api-key)
    jev_model: str = "jev-1.14.0"  # pinned: scores can shift between versions
    jev_timeout_seconds: float = 10.0
    # Turn on once its probabilities are checked on this organization's questions (D142): only
    # then do answers carry bands
    jev_calibrated: bool = False

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
