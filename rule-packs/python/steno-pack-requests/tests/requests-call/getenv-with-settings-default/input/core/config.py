from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    OLLAMA_LOCAL_HOST_URL: str = "http://localhost:11434"


settings = Settings()
