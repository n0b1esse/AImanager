"""Server-side LLM endpoints configured by owners (invisible to customers)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.ai.models import AIProfile, GenerationResult, ReviewContext
from wb_web.ai.providers import AIProviderError, BaseAIProvider, OpenAICompatibleProvider
from wb_web.core.security import SecretEncryptor
from wb_web.db.models import LLMProvider


class ManagedProvider(BaseAIProvider):
    """Try configured endpoints in order; fall through on provider errors."""

    name = "managed"

    def __init__(self, endpoints: list[dict[str, str]], *, client: Any = None) -> None:
        super().__init__(model=endpoints[0]["model"], client=client)
        self.endpoints = endpoints

    async def generate(self, context: ReviewContext, profile: AIProfile) -> GenerationResult:
        errors: list[str] = []
        for endpoint in self.endpoints:
            provider = OpenAICompatibleProvider(
                api_key=endpoint["key"] or "local",
                model=endpoint["model"],
                base_url=endpoint["base_url"],
                client=self.client,
            )
            try:
                return await provider.generate(context, profile)
            except AIProviderError as exc:
                errors.append(f"{endpoint['label']}/{endpoint['model']}: {exc}")
        raise AIProviderError("Все настроенные LLM недоступны: " + "; ".join(errors))


async def build_managed_provider(session: AsyncSession, *, client: Any = None) -> ManagedProvider:
    rows = (
        await session.execute(
            select(LLMProvider).where(LLMProvider.enabled).order_by(LLMProvider.priority, LLMProvider.id)
        )
    ).scalars().all()
    endpoints: list[dict[str, str]] = []
    encryptor = SecretEncryptor.from_settings()
    for row in rows:
        key = encryptor.decrypt(row.api_key_encrypted) if row.api_key_encrypted else ""
        for model in row.models:
            if model.strip():
                endpoints.append(
                    {
                        "label": row.label,
                        "base_url": row.base_url.rstrip("/"),
                        "key": key,
                        "model": model.strip(),
                    }
                )
    if not endpoints:
        raise AIProviderError("В админке не настроен ни один LLM-провайдер")
    return ManagedProvider(endpoints, client=client)
