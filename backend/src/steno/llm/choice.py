"""Which provider and model write the organization's text: its own choice, made on the
Settings page (`organization.settings["llm"]`), else the deployment's defaults
(STENO_LLM_PROVIDER, STENO_LLM_MODEL). The model itself is still Open (D143)."""

from typing import Any

from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.db.models import Organization
from steno.llm.providers import Provider, get_provider

# What the Settings page offers: providers that are built, and their models
PROVIDERS: dict[str, dict[str, Any]] = {
    "claude_agent_sdk": {
        "label": "Claude Code (Claude Agent SDK)",
        "hint": "Uses the Claude Code login on the machine running the worker, or "
        "ANTHROPIC_API_KEY when set. For development and trials.",
        "models": {
            "claude-sonnet-5-5": "Claude Sonnet 5.5",
            "claude-opus-5-5": "Claude Opus 5.5",
            "claude-haiku-5-5": "Claude Haiku 5.5",
        },
    },
}


def llm_choice(session: Session) -> dict[str, str]:
    """{"provider", "model", "source"}: the organization's choice, else the defaults."""
    org = session.get(Organization, 1)
    chosen = ((org.settings if org else None) or {}).get("llm") or {}
    settings = get_settings()
    if chosen.get("provider") and chosen.get("model"):
        return {"provider": chosen["provider"], "model": chosen["model"], "source": "organization"}
    return {"provider": settings.llm_provider, "model": settings.llm_model, "source": "default"}


def provider_for(session: Session) -> Provider:
    choice = llm_choice(session)
    return get_provider(choice["provider"], choice["model"])


def check(provider: str, model: str) -> None:
    """Raise ValueError unless the Settings page offers this provider and model."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}: {', '.join(PROVIDERS)}")
    if model not in PROVIDERS[provider]["models"]:
        raise ValueError(f"{provider} has no model {model!r}")
