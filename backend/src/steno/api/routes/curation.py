"""Rule curation (D120): start runs, read proposals, approve or reject them.

docs/how-it-works/ingestion/rule-curation.md. A run is queued here and executed by a worker:
drafting is LLM work and never runs in the API process.
"""

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from steno.api.deps import SessionDep
from steno.api.links import source_url
from steno.config import get_settings
from steno.curation.approve import (
    ApprovalError,
    approve,
    ignore_scope,
    pack_meta,
    reject,
    target,
)
from steno.curation.publish import BRANCH, destination
from steno.curation.run import estimate, pick, queue_run
from steno.db.models import CurationRun, ProposalStatus, Repository, RuleProposal
from steno.rule_packs.packs import load_all_packs

router = APIRouter(tags=["curation"])


class RunIn(BaseModel):
    limit: int | None = None  # every open pattern, within the budget
    fingerprints: list[str] = []
    budget_usd: float | None = None
    model: str | None = None  # one of the allowed models; default: the setting


class ApproveIn(BaseModel):
    by: str = "ui"


class RejectIn(BaseModel):
    why: str
    by: str = "ui"


@router.get("/curation")
def settings_and_runs(session: SessionDep) -> dict[str, Any]:
    """The drafter in use and its caps, the open patterns a run would work on, and the latest
    runs."""
    s = get_settings()
    runs = session.scalars(select(CurationRun).order_by(CurationRun.id.desc()).limit(20))
    waiting = len(pick(session, None))
    total = session.scalar(select(func.coalesce(func.sum(CurationRun.spent_usd), 0.0)))
    count = session.scalar(select(func.count()).select_from(CurationRun))
    return {
        "drafter": s.rule_drafter,
        "model": s.rule_drafter_model,
        "models": [{"id": k, "name": v} for k, v in s.rule_drafter_models.items()],
        "effort": s.rule_drafter_effort,
        "max_turns": s.rule_drafter_max_turns,
        "max_usd_per_pattern": s.rule_drafter_max_usd,
        "budget_usd": s.curation_budget_usd,
        "open_patterns": waiting,
        "spent_total_usd": round(total or 0.0, 4),  # every search so far
        "searches": count,
        "runs": [_run(r) for r in runs],
    }


@router.get("/curation/estimate")
def cost_estimate(session: SessionDep, model: str | None = None) -> dict[str, Any]:
    """What a search over every open pattern would cost with `model`: worst case, and a
    projection from past drafts (§9)."""
    return estimate(session, model)


@router.post("/curation/runs", status_code=201)
def start(body: RunIn, session: SessionDep) -> dict[str, Any]:
    """Queue a run over the open patterns (or the ones named); a worker executes it."""
    if body.limit is not None and body.limit < 1:
        raise HTTPException(422, "limit is at least 1")
    models = get_settings().rule_drafter_models
    if body.model is not None and body.model not in models:
        raise HTTPException(422, f"model is one of {', '.join(models)}")
    run = queue_run(
        session, body.limit, body.fingerprints, body.budget_usd, "manual", model=body.model
    )
    return _run(run)


@router.get("/proposals")
def proposals(
    session: SessionDep, status: ProposalStatus | None = None, run_id: int | None = None
) -> list[dict[str, Any]]:
    """Suggestions, newest first: all, in one status, or from one search."""
    q = select(RuleProposal).order_by(RuleProposal.id.desc()).limit(200)
    if status is not None:
        q = q.where(RuleProposal.status == status)
    if run_id is not None:
        q = q.where(RuleProposal.run_id == run_id)
    return [_summary(p) for p in session.scalars(q)]


@router.get("/proposals/{proposal_id}")
def proposal(proposal_id: int, session: SessionDep) -> dict[str, Any]:
    p = _get(session, proposal_id)
    rule_id = (yaml.safe_load(p.rule) or {}).get("id") if p.rule else None
    pack = pack_meta(p) if p.rule else None
    return {
        "publish": p.published or (_preview(p) if p.rule else None),
        # For an ignore of unread files: the only file names it ignores
        "ignore_names": sorted({n for ns in ignore_scope(p).values() for n in ns}),
        "rule_id": rule_id,
        "pack_exists": bool(pack and pack["name"] in {k.name for k in load_all_packs()}),
        **_summary(p),
        "rule": p.rule,
        "tests": p.tests,
        "proof": p.proof,
        "samples": _linked(session, p.samples or []),
        "pack": pack,
        "flags": p.flags,
        "comment": p.comment,
        "reviewed_by": p.reviewed_by,
        "reviewed_at": p.reviewed_at,
        "commit_sha": p.commit_sha,
    }


@router.post("/proposals/{proposal_id}/approve")
def approve_one(proposal_id: int, body: ApproveIn, session: SessionDep) -> dict[str, Any]:
    p = _get(session, proposal_id)
    try:
        out = approve(session, p, body.by)
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"proposal": _summary(p), **{k: str(v) for k, v in out.items()}}


@router.post("/proposals/{proposal_id}/reject")
def reject_one(proposal_id: int, body: RejectIn, session: SessionDep) -> dict[str, Any]:
    p = _get(session, proposal_id)
    if not body.why.strip():
        raise HTTPException(422, "say why")
    try:
        reject(session, p, body.by, body.why)
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _summary(p)


class ManyIn(BaseModel):
    ids: list[int]
    why: str = ""  # for reject
    by: str = "ui"


@router.post("/proposals/approve")
def approve_many(body: ManyIn, session: SessionDep) -> list[dict[str, Any]]:
    """Approve several suggestions (a framework's, reviewed as one unit), one after another;
    each result says whether it went through, so one failure doesn't hide the rest."""
    out = []
    for pid in body.ids:
        p = _get(session, pid)
        try:
            approve(session, p, body.by)
            out.append({"id": pid, "ok": True})
        except ApprovalError as exc:
            out.append({"id": pid, "ok": False, "error": str(exc)})
    return out


@router.post("/proposals/reject")
def reject_many(body: ManyIn, session: SessionDep) -> list[dict[str, Any]]:
    if not body.why.strip():
        raise HTTPException(422, "say why")
    out = []
    for pid in body.ids:
        p = _get(session, pid)
        try:
            reject(session, p, body.by, body.why)
            out.append({"id": pid, "ok": True})
        except ApprovalError as exc:
            out.append({"id": pid, "ok": False, "error": str(exc)})
    return out


def _preview(p: RuleProposal) -> dict[str, Any] | None:
    """Where approving would publish the rule (D131), before anything is written."""
    try:
        pack_dir, source, created = target(p)
        dest = destination(source, pack_dir)
    except Exception as exc:  # noqa: BLE001 - shown instead of a preview
        return {"error": str(exc)}
    return {
        "destination": dest.name,
        "repository": _remote_name(dest.repo),
        "mode": dest.mode,
        "branch": BRANCH if dest.mode == "pr" else dest.base,
        "base": dest.base,
        "created_pack": created,
        # The pack's folder in that repository, e.g. rule-packs/platform/steno-pack-compose
        "path": str(pack_dir.resolve().relative_to(dest.repo.resolve())),
    }


def _linked(session: Any, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each sample with a link to its line on the git host, at the commit Steno ingested."""
    repos = {r.name: r for r in session.scalars(select(Repository))}
    out = []
    for s in samples:
        r = repos.get(s.get("repository"))
        url = r and source_url(r.clone_url, r.last_ingested_sha, s.get("file"), s.get("line"))
        out.append({**s, "url": url or None})
    return out


def _remote_name(repo: Any) -> str:
    import subprocess

    url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()  # fmt: skip
    return url.rsplit(":", 1)[-1].removesuffix(".git") if url else repo.name


def _get(session: Any, proposal_id: int) -> RuleProposal:
    p = session.get(RuleProposal, proposal_id)
    if p is None:
        raise HTTPException(404, f"no proposal #{proposal_id}")
    return p


def _summary(p: RuleProposal) -> dict[str, Any]:
    cases = (p.proof or {}).get("cases") or []
    return {
        "id": p.id,
        "run_id": p.run_id,
        "kind": p.kind.value,
        "status": p.status.value,
        "title": p.title,
        "name": p.name,
        "summary": p.summary,
        "fingerprints": p.fingerprints,
        "owner": p.owner,
        "facet": p.facet,
        "target_pack": p.target_pack,
        "cases": len(p.tests or {}),
        "cases_passed": sum(1 for c in cases if c.get("passed")),
        "drafter": p.drafter,
        "cost_usd": round(p.cost_usd or 0.0, 4),
        "turns": p.turns,
        "created_at": p.created_at,
        "reviewed_at": p.reviewed_at,
        # Where an approved rule went: its pull request, or a link that opens one
        "link": (p.published or {}).get("pr_url") or (p.published or {}).get("compare_url"),
    }


def _run(r: CurationRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "status": r.status,
        "trigger": r.trigger,
        "drafter": r.drafter,
        "model": r.model,
        "budget_usd": r.budget_usd,
        "spent_usd": round(r.spent_usd or 0.0, 4),
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "stats": r.stats,
        "error": r.error,
    }
