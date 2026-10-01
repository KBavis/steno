"""One process serves the Admin API (/api) and the MCP server (/mcp).

Splitting MCP into its own process later only needs a new entrypoint that serves
`mcp.streamable_http_app()` on its own.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import steno
from steno.api.routes import admin, health
from steno.config import get_settings
from steno.mcp.server import mcp


def create_app() -> FastAPI:
    # Stateless (no MCP sessions), served at /mcp by the mounted sub-app.
    mcp_app = mcp.streamable_http_app(stateless_http=True, json_response=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # A mounted app's lifespan doesn't run, so start MCP's session manager here.
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Steno", version=steno.__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    # Mounted last so /api routes match first; the sub-app serves /mcp.
    app.mount("/", mcp_app)
    return app
