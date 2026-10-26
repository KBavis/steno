"""Runtime settings, read from the environment (prefix `STENO_`) or a `.env` file."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

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
    resolver_jvm_timeout: int = 1800  # seconds per repository
    # Resolve method bodies this many files at a time against a declaration index of the whole
    # repository, so memory stays bounded (D127); 0 resolves everything in one pass
    resolver_jvm_batch_files: int = 0

    # Rule curation (D120, D130): which provider drafts rules, and the caps on each pattern.
    # claude_agent_sdk uses this machine's Claude Code login, or ANTHROPIC_API_KEY when set
    rule_drafter: str = "claude_agent_sdk"  # claude_agent_sdk | openai | gemini | azure_openai
    rule_drafter_model: str = "claude-opus-5-5"  # the default; a search can pick another
    # The models a search may choose from, as model id → name shown in the UI
    rule_drafter_models: dict[str, str] = {
        "claude-opus-5-5": "Claude Opus 5.5",
        "claude-sonnet-5-5": "Claude Sonnet 5.5",
        "claude-haiku-5-5": "Claude Haiku 5.5",
    }
    rule_drafter_effort: str = "high"
    rule_drafter_max_turns: int = 30
    rule_drafter_max_usd: float = 2.0  # per pattern
    curation_budget_usd: float = 10.0  # per run
    # Patterns of one run drafted at once, across every worker (each worker process drafts one
    # at a time); a claim not finished within the timeout (a worker died) is drafted again
    curation_parallel: int = 4
    # The cost check (§9): a search whose projection passes its limit, or with no history to
    # project from, is confirmed first, and can start as a pilot of this many patterns
    curation_pilot: int = 10
    rule_drafter_timeout_min: int = 30
    # Times a draft goes back to the drafter with what Steno's checks found (recall, identity)
    rule_drafter_revisions: int = 1
    # Publishing approved rules (D131): `pr` collects them on one `steno/rules` branch with one
    # open pull request per destination; `push` commits straight to the default branch
    rules_publish_core: str = "pr"  # Steno's own rule-packs/ (rules for public libraries)
    rules_publish_org: str = "pr"  # the organization's rule-pack repository
    # Steno's own working files (the worktrees it publishes rules from)
    cache_dir: Path = Path.home() / ".cache" / "steno"

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
    # This machine's name for counting its jobs (default: the host name)
    worker_host: str = ""

    # Worker queue polling
    worker_poll_seconds: float = 2.0

    # Nightly ingestion (D59): each night at this time (UTC, HH:MM), queue a run for every
    # repository whose default branch moved since it was last ingested
    nightly_enabled: bool = True
    nightly_at: str = "02:00"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
