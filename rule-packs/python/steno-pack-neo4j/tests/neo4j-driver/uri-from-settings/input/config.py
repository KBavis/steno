from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    graph_uri: str = "bolt://graph.local:7687"


settings = Settings()
