"""Which LLM writes the organization's text: its own choice, made on the Settings page
(`organization.settings["llm"]`), else the deployment's defaults (STENO_LLM_PROVIDER,
STENO_LLM_MODEL, STENO_LLM_BASE_URL, STENO_LLM_API_KEY_REF). Everything that calls an LLM goes
through `provider_for`. A key is a reference (`env:NAME`), never the key itself."""

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.db.models import Organization
from steno.llm.providers import Provider, get_provider

# What the Settings page offers: providers that are built, and their models. `models` empty
# means the organization names its own model, and `connection` that it may give an address and
# a key reference
PROVIDERS: dict[str, dict[str, Any]] = {
    "litellm": {
        "label": "Your organization's LLM (gateway, Azure, Bedrock, a vendor's API)",
        "hint": "Reached through LiteLLM. For a gateway that speaks the OpenAI API, give its "
        "address and name the model openai/<model>. Others use LiteLLM's name, such as "
        "azure/<deployment> or bedrock/<model>, and their usual environment variables.",
        "models": {},
        "connection": True,
    },
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


def llm_choice(session: Session) -> dict[str, Any]:
    """{"provider", "model", "base_url", "api_key_ref", "source"}: the organization's choice,
    else the defaults."""
    org = session.get(Organization, 1)
    chosen = ((org.settings if org else None) or {}).get("llm") or {}
    if chosen.get("provider") and chosen.get("model"):
        return {
            "provider": chosen["provider"],
            "model": chosen["model"],
            "base_url": chosen.get("base_url"),
            "api_key_ref": chosen.get("api_key_ref"),
            "source": "organization",
        }
    settings = get_settings()
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "api_key_ref": settings.llm_api_key_ref,
        "source": "default",
    }


def provider_for(
    session: Session, provider: str | None = None, model: str | None = None
) -> Provider:
    """The LLM to use: the organization's choice. `provider` or `model` override it for one
    run (the command line); a different provider doesn't get this one's address or key."""
    choice = llm_choice(session)
    same = provider in (None, choice["provider"])
    return get_provider(
        provider or choice["provider"],
        model or choice["model"],
        choice["base_url"] if same else None,
        choice["api_key_ref"] if same else None,
    )


def check(
    provider: str, model: str, base_url: str | None = None, api_key_ref: str | None = None
) -> None:
    """Raise ValueError unless the Settings page can offer this: a known provider and model, an
    address that is one, and a key given only as a reference."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}: {', '.join(PROVIDERS)}")
    offered = PROVIDERS[provider]
    if not model.strip():
        raise ValueError("name a model")
    if offered["models"] and model not in offered["models"]:
        raise ValueError(f"{provider} has no model {model!r}")
    if (base_url or api_key_ref) and not offered.get("connection"):
        raise ValueError(f"{provider} doesn't take an address or a key")
    if base_url and not (
        urlsplit(base_url).scheme in ("http", "https") and urlsplit(base_url).netloc
    ):
        raise ValueError(f"{base_url!r} isn't an address (https://...)")
    if api_key_ref and not (
        api_key_ref.startswith("env:") and api_key_ref[4:].replace("_", "").isalnum()
    ):
        raise ValueError(
            "give the key as a reference, env:NAME, and set NAME where the worker runs; "
            "Steno never stores the key itself"
        )
