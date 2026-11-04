"""The organization's patterns: what no rule explains, grouped by what it is (D121–D123).

The rule author's worklist (D147; docs/how-it-works/ingestion/rule-curation.md): the
`rule-pack-author` skill reads open patterns here in the report's order, and sets their state
when it's done: ignored (harmless), expected (outside the organization), proposed (a rule's
pull request is open), or open again.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from steno.api.deps import SessionDep
from steno.coverage.joins import one_sided
from steno.coverage.patterns import TRIAGE, WEIGHT, frameworks, occurrences, triage
from steno.db.models import CoverageKind, CoveragePattern, PatternState
from steno.graph.driver import database, get_driver

log = logging.getLogger(__name__)

router = APIRouter(prefix="/patterns", tags=["patterns"])


class Triage(BaseModel):
    fingerprints: list[str]
    state: PatternState  # ignored, expected, proposed, or open
    note: str | None = None


@router.get("")
def patterns(
    session: SessionDep,
    state: PatternState = PatternState.OPEN,
    limit: int = 50,
    quiet: bool = False,
) -> dict[str, Any]:
    """Patterns in a state, as frameworks (patterns sharing an owner: a library, a package
    root, a config prefix) ranked by their summed score; a pattern no library owns stands
    alone. `limit`: how many frameworks or lone patterns. `quiet`: include what the report
    leaves out (unread files and settings, library versions: D146). Open one-sided joins
    are read from the graph."""
    query = select(CoveragePattern).where(CoveragePattern.state == state)
    if not quiet:
        query = query.where(CoveragePattern.quiet.is_(False))
    rows = list(session.scalars(query))
    if state == PatternState.OPEN:
        rows += _one_sided(session)
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


@router.get("/occurrences")
def pattern_occurrences(session: SessionDep, fingerprint: str, limit: int = 50) -> dict[str, Any]:
    """Where a pattern occurs now, with links to the code at the ingested commit, and its
    samples: what the rule author reads before writing a rule (D147)."""
    try:
        return occurrences(session, fingerprint, limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


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


def _one_sided(session: Any) -> list[CoveragePattern]:
    """Open one-sided joins, as unsaved patterns: those triaged already aren't open."""
    try:
        found = one_sided(get_driver(), database())
    except Exception as exc:  # noqa: BLE001 - the graph being down mustn't break the list
        log.warning("one-sided joins not read: %s", exc)
        return []
    decided = set(
        session.scalars(
            select(CoveragePattern.fingerprint).where(
                CoveragePattern.fingerprint.in_([f["fingerprint"] for f in found]),
                CoveragePattern.state != PatternState.OPEN,
            )
        )
    ) if found else set()  # fmt: skip
    return [
        CoveragePattern(
            fingerprint=f["fingerprint"],
            kind=CoverageKind.INCONSISTENCY,
            signal="one_sided",
            label=f["label"],
            owner=None,
            state=PatternState.OPEN,
            repositories=0,
            applications=len(f["apps"]),
            occurrences=len(f["apps"]),
            score=WEIGHT["one_sided"] * (10 * len(f["apps"]) + 1),
            evidence=[],
            note=f["detail"],
        )
        for f in found
        if f["fingerprint"] not in decided
    ]


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
