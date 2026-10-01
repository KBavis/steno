import os

import requests

from core.config import settings


def list_models():
    response = requests.get(os.getenv("OLLAMA_BASE_URL", settings.OLLAMA_LOCAL_HOST_URL) + "/api/tags")
    return response.json()
