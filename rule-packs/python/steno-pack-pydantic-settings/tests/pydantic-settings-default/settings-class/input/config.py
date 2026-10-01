from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Contextualized"
    VECTOR_DB_HOST: str = "localhost"
    VECTOR_DB_PORT: int = 8000
    SYNC_REL_DB_URL: str = ""
    AZURE_CONTEXT_WINDOW_OVERRIDES: dict[str, int] = {
        "gpt-4o": 128000,
    }

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_file=".env")
