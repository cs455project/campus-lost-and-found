"""The agent's language model: any OpenAI-compatible chat API.

Two providers, tried in order on every call:
  1. Groq   (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL)            fast, free tier
  2. Gemini (LLM_FALLBACK_BASE_URL, key and model from GEMINI_*) fallback

If both fail (no key, rate limit, network error, daily limit reached),
LLMUnavailable is raised and the caller uses the rule-based matching
instead, so the app keeps working without any AI provider.

The OpenAI Python SDK is used only as a client: Groq and Gemini both offer
the same "chat completions" API, including tool (function) calling.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AgentRun

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30


class LLMUnavailable(Exception):
    """No provider could answer (or the daily limit was reached)."""


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


def configured_providers() -> list[Provider]:
    settings = get_settings()
    if not settings.ai_enabled:
        return []
    providers = []
    if settings.llm_api_key and settings.llm_model:
        providers.append(Provider("groq", settings.llm_base_url, settings.llm_api_key, settings.llm_model))
    fallback_key = settings.llm_fallback_api_key or settings.gemini_api_key
    fallback_model = settings.llm_fallback_model or settings.gemini_model
    if fallback_key and fallback_model:
        providers.append(Provider("gemini", settings.llm_fallback_base_url, fallback_key, fallback_model))
    return providers


class LLMClient:
    """chat() returns (assistant message as a dict, provider name, model)."""

    def __init__(self, providers: list[Provider]):
        from openai import OpenAI  # imported here: only needed when an LLM is configured

        self.providers = providers
        self._clients = {p.name: OpenAI(api_key=p.api_key, base_url=p.base_url, timeout=TIMEOUT_SECONDS,
                                        max_retries=1) for p in providers}

    def chat(self, messages: list[dict], tools: list[dict]) -> tuple[dict, str, str]:
        errors = []
        for provider in self.providers:
            try:
                response = self._clients[provider.name].chat.completions.create(
                    model=provider.model, messages=messages, tools=tools, tool_choice="auto",
                    temperature=0.2,
                )
                return _as_dict(response.choices[0].message), provider.name, provider.model
            except Exception as error:  # rate limit, timeout, bad key, server error...
                log.warning("LLM provider %s failed: %s", provider.name, error)
                errors.append(f"{provider.name}: {type(error).__name__}: {str(error)[:150]}")
        raise LLMUnavailable("; ".join(errors) or "No LLM provider configured")


def _as_dict(message) -> dict:
    """The SDK's message object -> a plain dict we can send back next turn."""
    out = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        out["tool_calls"] = [
            {"id": call.id, "type": "function",
             "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"}}
            for call in message.tool_calls
        ]
    return out


def calls_today(db: Session) -> int:
    """LLM calls made since midnight campus time (counted from agent_runs)."""
    zone = ZoneInfo(get_settings().timezone)
    midnight = datetime.combine(datetime.now(zone).date(), time.min, tzinfo=zone)
    return db.scalar(select(func.coalesce(func.sum(AgentRun.llm_calls), 0))
                     .where(AgentRun.started_at >= midnight)) or 0


_client: LLMClient | None = None
_override = False


def get_llm() -> LLMClient | None:
    """The configured client, or None when no provider has a key."""
    global _client
    if _override:
        return _client
    if _client is None:
        providers = configured_providers()
        _client = LLMClient(providers) if providers else None
    return _client


def set_llm(client) -> None:
    """Tests: plug in a fake client (or None for "no LLM"). Call reset_llm() after."""
    global _client, _override
    _client, _override = client, True


def reset_llm() -> None:
    global _client, _override
    _client, _override = None, False
