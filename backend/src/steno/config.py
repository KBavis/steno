"""Runtime settings, read from the environment (prefix `STENO_`) or a `.env` file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STENO_", env_file=".env", extra="ignore")

    # Postgres: what Steno is told and what it did (DESIGN_DOC D33)
    database_url: str = "postgresql+psycopg://steno:steno@localhost:5432/steno"

    # Neo4j: what Steno knows
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "steno-dev-password"
    neo4j_database: str = "neo4j"

    # POC declarations (connectors, spaces, repositories), loaded into Postgres
    poc_config_path: Path = Path("../config/steno.yaml")

    # Where ingestion workers clone repositories; deleted after each run
    workspace_dir: Path = Path("/tmp/steno-workspaces")

    # Worker queue polling
    worker_poll_seconds: float = 2.0

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
