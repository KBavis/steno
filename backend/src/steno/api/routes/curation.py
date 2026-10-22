"""Rule curation (D120): start runs, read proposals, approve or reject them.

docs/how-it-works/ingestion/rule-curation.md. A run is queued here and executed by a worker:
drafting is LLM work and never runs in the API process.
"""

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from steno.api.deps import SessionDep
from steno.config import get_settings
from steno.curation.approve import ApprovalError, approve, pack_meta, reject
from steno.curation.run import queue_run
from steno.db.models import CurationRun, ProposalStatus, RuleProposal
from steno.rule_packs.packs import load_all_packs

router = APIRouter(tags=["curation"])


class RunIn(BaseModel):
    limit: int = 3
    fingerprints: list[str] = []
    budget_usd: float | None = None


class ApproveIn(BaseModel):
    by: str = "ui"
    commit: bool = False


class RejectIn(BaseModel):
    why: str
    by: str = "ui"


@router.get("/curation")
def settings_and_runs(session: SessionDep) -> dict[str, Any]:
    """The drafter in use and its caps, and the latest runs."""
    s = get_settings()
    runs = session.scalars(select(CurationRun).order_by(CurationRun.id.desc()).limit(20))
    return {
        "drafter": s.rule_drafter,
        "model": s.rule_drafter_model,
        "effort": s.rule_drafter_effort,
        "max_turns": s.rule_drafter_max_turns,
        "max_usd_per_pattern": s.rule_drafter_max_usd,
        "budget_usd": s.curation_budget_usd,
        "runs": [_run(r) for r in runs],
    }


@router.post("/curation/runs", status_code=201)
def start(body: RunIn, session: SessionDep) -> dict[str, Any]:
    """Queue a run over the top open patterns (or the ones named); a worker executes it."""
    if body.limit < 1 or body.limit > 50:
        raise HTTPException(422, "limit is 1 to 50")
    run = queue_run(session, body.limit, body.fingerprints, body.budget_usd, "manual")
    return _run(run)


@router.get("/proposals")
def proposals(session: SessionDep, status: ProposalStatus | None = None) -> list[dict[str, Any]]:
    q = select(RuleProposal).order_by(RuleProposal.id.desc()).limit(200)
    if status is not None:
        q = q.where(RuleProposal.status == status)
    return [_summary(p) for p in session.scalars(q)]


@router.get("/proposals/{proposal_id}")
def proposal(proposal_id: int, session: SessionDep) -> dict[str, Any]:
    p = _get(session, proposal_id)
    rule_id = (yaml.safe_load(p.rule) or {}).get("id") if p.rule else None
    pack = pack_meta(p) if p.rule else None
    return {
        "rule_id": rule_id,
        "pack_exists": bool(pack and pack["name"] in {k.name for k in load_all_packs()}),
        **_summary(p),
        "rule": p.rule,
        "tests": p.tests,
        "proof": p.proof,
        "samples": p.samples,
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
        out = approve(session, p, body.by, commit=body.commit)
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
