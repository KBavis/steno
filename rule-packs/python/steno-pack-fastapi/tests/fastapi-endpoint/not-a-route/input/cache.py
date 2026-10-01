from functools import lru_cache
from fastapi import FastAPI

app = FastAPI()
cache = {}


@app.on_event("startup")          # a FastAPI hook, but not an HTTP method
async def warm():
    pass


class Registry:
    def get(self, name):
        return lambda fn: fn


registry = Registry()


@registry.get("handler")          # .get(...) on something that isn't a router: excluded by `where`
def handler():
    pass


@lru_cache
def settings():
    return cache.get("settings")
