"""Graph views for the Visualize tab (D53): one layer of the graph at a time."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from steno.api.deps import SessionDep
from steno.db.models import Repository
from steno.graph.driver import database, get_driver
from steno.graph.views import GraphViews

router = APIRouter(prefix="/graph", tags=["graph"])


def _views() -> GraphViews:
    return GraphViews(get_driver(), database())


def _or_404(fn: Any, *args: Any) -> dict[str, Any]:
    try:
        return fn(*args)
    except KeyError as exc:
        raise HTTPException(404, f"no node {exc.args[0]!r} in the graph") from exc


@router.get("/overview")
def overview() -> dict[str, Any]:
    return _views().overview()


@router.get("/space")
def space(id: str) -> dict[str, Any]:
    return _or_404(_views().level, id)


@router.get("/application")
def application(
    id: str, layer: str = Query("architecture", pattern="^(architecture|code)$")
) -> dict[str, Any]:
    v = _views()
    return _or_404(v.application_code if layer == "code" else v.application, id)


@router.get("/flow")
def flow(id: str, significant_only: bool = True) -> dict[str, Any]:
    return _or_404(_views().flow, id, significant_only)


@router.get("/node")
def node(id: str, session: SessionDep) -> dict[str, Any]:
    detail = _or_404(_views().node, id)
    prov = detail["provenance"]
    detail["source_url"] = _source_url(
        session,
        prov.get("repo"),
        prov.get("commit"),
        prov.get("source_file"),
        prov.get("source_line"),
    )
    return detail


@router.get("/search")
def search(q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
    return _views().search(q)


def _source_url(
    session: Any, repo: str | None, commit: str | None, path: str | None, line: int | None
) -> str | None:
    """A link to the exact line at the ingested commit (GitHub and GitLab URL shapes)."""
    if not (repo and commit and path):
        return None
    row = session.scalar(select(Repository).where(Repository.name == repo))
    if row is None or not row.clone_url.startswith("https://"):
        return None
    base = row.clone_url.removesuffix(".git")
    anchor = f"#L{line}" if line else ""
    if "gitlab" in base:
        return f"{base}/-/blob/{commit}/{path}{anchor}"
    return f"{base}/blob/{commit}/{path}{anchor}"
