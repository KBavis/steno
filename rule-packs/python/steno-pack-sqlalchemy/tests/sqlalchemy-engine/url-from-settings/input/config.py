from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ASYNC_DB_URL: str = "postgresql+asyncpg://app:secret@pg.local:5432/billing"


settings = Settings()
