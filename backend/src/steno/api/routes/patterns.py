"""The organization's patterns: what no rule explains, grouped by what it is (D121–D123).

What rule curation works from (docs/how-it-works/ingestion/rule-curation.md). Read here;
triage ignores or reopens whole patterns; proposals and review come with the curation job.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from steno.api.deps import SessionDep
from steno.coverage.patterns import TRIAGE, frameworks, triage
from steno.db.models import CoveragePattern, PatternState

router = APIRouter(prefix="/patterns", tags=["patterns"])


class Triage(BaseModel):
    fingerprints: list[str]
    state: PatternState  # ignored or open
    note: str | None = None


@router.get("")
def patterns(
    session: SessionDep, state: PatternState = PatternState.OPEN, limit: int = 50
) -> dict[str, Any]:
    """Patterns in a state, as frameworks (patterns sharing an owner: a library, a package
    root, a config prefix) ranked by their summed score; a pattern no library owns stands
    alone. `limit`: how many frameworks or lone patterns."""
    rows = list(session.scalars(select(CoveragePattern).where(CoveragePattern.state == state)))
    groups = frameworks(rows)
    return {
        "state": state.value,
        "patterns": len(rows),
        "frameworks": [
            {
                "owner": g["owner"],
                "score": round(g["score"], 2),
                "patterns": [_pattern(p) for p in g["patterns"]],
            }
            for g in groups[:limit]
        ],
        "more": max(len(groups) - limit, 0),
    }


@router.put("/triage")
def set_state(body: Triage, session: SessionDep) -> dict[str, int]:
    """Ignore or reopen patterns, in bulk, with a note saying why (who decided, and on what
    grounds). Applies everywhere the pattern occurs, and later runs keep it."""
    if body.state not in TRIAGE:
        raise HTTPException(422, f"state is one of {sorted(s.value for s in TRIAGE)}")
    changed = triage(session, body.fingerprints, body.state, body.note)
    if changed < len(set(body.fingerprints)):
        raise HTTPException(404, f"{len(set(body.fingerprints)) - changed} fingerprints not found")
    return {"updated": changed}


def _pattern(p: CoveragePattern) -> dict[str, Any]:
    return {
        "fingerprint": p.fingerprint,
        "signal": p.signal,
        "label": p.label,
        "repositories": p.repositories,
        "applications": p.applications,
        "occurrences": p.occurrences,
        "score": p.score,
        "state": p.state.value,
        **({"evidence": p.evidence} if p.evidence else {}),
        **({"note": p.note} if p.note else {}),
    }
