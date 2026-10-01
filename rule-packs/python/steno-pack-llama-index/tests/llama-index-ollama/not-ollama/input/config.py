from dataclasses import dataclass


@dataclass
class Choice:
    model: str


default = Choice(model="gpt-oss:latest")    # model= but not an Ollama client: excluded by `where`
