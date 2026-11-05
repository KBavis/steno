"""The organization this deployment serves (one row, D34), its onboarding state, and its
settings (the LLM that writes summaries, D143)."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from steno.api.deps import SessionDep, sync_graph
from steno.api.schemas import (
    LlmCheckOut,
    LlmModelOption,
    LlmProviderOption,
    OrganizationIn,
    OrganizationOut,
    OrganizationSettingsIn,
    OrganizationSettingsOut,
)
from steno.db.models import Organization
from steno.llm import LlmUnavailable
from steno.llm.choice import PROVIDERS, check, llm_choice, provider_for

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


@router.get("/settings", response_model=OrganizationSettingsOut)
def get_settings_(session: SessionDep) -> OrganizationSettingsOut:
    return OrganizationSettingsOut(
        llm=llm_choice(session),
        llm_options=[
            LlmProviderOption(
                id=pid,
                label=p["label"],
                hint=p["hint"],
                models=[LlmModelOption(id=m, label=label) for m, label in p["models"].items()],
                connection=bool(p.get("connection")),
            )
            for pid, p in PROVIDERS.items()
        ],
    )


@router.put("/settings", response_model=OrganizationSettingsOut)
def put_settings(body: OrganizationSettingsIn, session: SessionDep) -> OrganizationSettingsOut:
    """Choose the LLM the organization uses (D149). Summaries already written stay until their
    inputs change."""
    org = get_organization(session)
    try:
        check(body.llm.provider, body.llm.model, body.llm.base_url, body.llm.api_key_ref)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    org.settings = {**(org.settings or {}), "llm": body.llm.model_dump()}
    session.flush()
    return get_settings_(session)


@router.post("/settings/llm/check", response_model=LlmCheckOut)
def check_llm(session: SessionDep) -> LlmCheckOut:
    """Send one short prompt to the chosen LLM, to see that it answers."""
    try:
        done = provider_for(session).complete("Answer in one word.", "Say ok.")
    except LlmUnavailable as exc:
        return LlmCheckOut(ok=False, detail=str(exc))
    return LlmCheckOut(ok=True, detail=f"{done.model} answered in {done.latency_ms} ms")
