from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

import steno
from steno.db.session import get_engine
from steno.graph.driver import database, get_driver

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, Any]:
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {type(exc).__name__}"
    try:
        get_driver().verify_connectivity(database=database())
        checks["neo4j"] = "ok"
    except Exception as exc:
        checks["neo4j"] = f"error: {type(exc).__name__}"
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "version": steno.__version__, "checks": checks}
