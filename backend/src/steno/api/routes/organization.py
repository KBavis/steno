"""The organization this deployment serves (one row, D34) and its onboarding state."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from steno.api.deps import SessionDep, sync_graph
from steno.api.schemas import OrganizationIn, OrganizationOut
from steno.db.models import Organization

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("", response_model=OrganizationOut)
def get_organization(session: SessionDep) -> Organization:
    org = session.get(Organization, 1)
    if org is None:
        raise HTTPException(404, "no organization yet: start onboarding")
    return org


@router.put("", response_model=OrganizationOut)
def put_organization(body: OrganizationIn, session: SessionDep) -> Organization:
    """Create the organization (first onboarding step) or update it."""
    org = session.get(Organization, 1)
    if org is None:
        org = Organization(id=1)
        session.add(org)
    org.name = body.name
    org.description = body.description
    sync_graph(session)
    return org


@router.post("/onboarding/complete", response_model=OrganizationOut)
def complete_onboarding(session: SessionDep) -> Organization:
    org = get_organization(session)
    org.onboarded_at = datetime.now(UTC)
    session.flush()
    return org
