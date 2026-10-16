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

    # Ingestion jobs running at once, across every worker process and machine. Each job holds
    # a clone and, for Java, a JVM: this is what bounds memory and disk (docs: storage.md)
    max_parallel_jobs: int = 4
    # Worker processes `steno worker` starts (each runs one job at a time)
    worker_processes: int = 2

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
