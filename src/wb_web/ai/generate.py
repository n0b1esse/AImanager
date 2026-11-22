"""BYOK generation service: wire a shop's AI config into the engine.

Flow: validate -> build ReviewContext/AIProfile (niche packs, recent answers)
-> AIEngine -> persist usage. Daily limit (shop.ai_daily_limit) is enforced
before spending anything. Failures leave the review in its previous state
with review.error populated; the endpoint maps them to status codes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.ai.engine import AIEngine, QualityGateError
from wb_web.ai.managed import build_managed_provider
from wb_web.ai.models import AIProfile, GenerationResult, ReviewContext
from wb_web.ai.providers import (
    AIProviderError,
    BaseAIProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)
from wb_web.core.events import track
from wb_web.core.security import SecretEncryptor
from wb_web.db.models import Review, Shop
from wb_web.niches import get_packs_for_shop

MODEL_DEFAULTS = {"openai": "gpt-4.1-mini", "ollama": "gemma3:4b"}  # openai_compatible: required from shop config


class AIRequestError(RuntimeError):
    pass


class AINotConfigured(AIRequestError):
    pass


class DailyLimitExceeded(AIRequestError):
    pass


def build_provider_for_shop(shop: Shop, *, client: Any = None) -> BaseAIProvider:
    provider = (shop.ai_provider or "openai").strip().lower()
    model = (shop.ai_model or "").strip() or MODEL_DEFAULTS.get(provider, "")
    key = SecretEncryptor.from_settings().decrypt(shop.ai_api_key_encrypted) if shop.ai_api_key_encrypted else None

    if provider == "openai":
        if not key:
            raise AINotConfigured("AI key is not configured for this shop (OpenAI)")
        return OpenAIProvider(
            api_key=key, model=model, base_url=(shop.ai_base_url or "https://api.openai.com/v1"), client=client
        )
    if provider == "openai_compatible":
        if not shop.ai_base_url:
            raise AINotConfigured("ai_base_url is required for an OpenAI-compatible provider")
        if not model:
            raise AINotConfigured("ai_model is required for an OpenAI-compatible provider")
        if not key:
            raise AINotConfigured("AI key is not configured for this shop")
        return OpenAICompatibleProvider(api_key=key, model=model, base_url=shop.ai_base_url, client=client)
    if provider == "ollama":
        if not model:
            raise AINotConfigured("ai_model is required for Ollama (e.g. gemma3:4b)")
        return OllamaProvider(model=model, base_url=shop.ai_base_url or "http://127.0.0.1:11434", client=client)
    if provider == "managed":
        raise AINotConfigured("managed LLM requires a session; call generate_for_review")
    raise AINotConfigured(f"unknown ai_provider: {provider}")


async def _generated_today(session: AsyncSession, shop_id: int) -> int:
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    count = (
        await session.execute(
            select(func.count(Review.id)).where(
                Review.shop_id == shop_id,
                Review.generated_at.is_not(None),
                Review.generated_at >= day_start,
            )
        )
    ).scalar_one()
    return int(count)


async def _recent_answers(session: AsyncSession, shop_id: int, limit: int = 6) -> tuple[str, ...]:
    rows = (
        await session.execute(
            select(Review.generated_text)
            .where(Review.shop_id == shop_id, Review.generated_text.is_not(None))
            .order_by(Review.generated_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return tuple(text for text in rows if text)


def build_context_and_profile(shop: Shop, review: Review, recent: tuple[str, ...]) -> tuple[ReviewContext, AIProfile]:
    meta = review.review_meta or {}
    rating = review.rating if isinstance(review.rating, int) and 1 <= review.rating <= 5 else 5
    context = ReviewContext(
        store_name=shop.wb_seller_name or shop.name,
        rating=rating,
        text=review.text or "",
        pros=meta.get("pros") or None,
        cons=meta.get("cons") or None,
        product_name=review.product_name,
        vendor_code=meta.get("supplier_article") or None,
    )
    profile = AIProfile(
        provider=shop.ai_provider,
        model=shop.ai_model or MODEL_DEFAULTS.get(shop.ai_provider or "openai", ""),
        style=shop.ai_style or "friendly",
        response_length=shop.ai_response_length or "short",
        max_characters=shop.ai_max_characters or 600,
        temperature=shop.ai_temperature,
        use_emoji=bool(shop.ai_use_emoji),
        negative_requires_approval=bool(shop.ai_negative_requires_approval),
        custom_instructions=(shop.ai_instructions or "").strip(),
        recent_answers=recent,
        packs=tuple(get_packs_for_shop(shop.niche_ids)),
    )
    return context, profile


async def generate_for_review(
    session: AsyncSession,
    shop: Shop,
    review: Review,
    *,
    provider: BaseAIProvider | None = None,
    client: Any = None,
) -> GenerationResult:
    limit = shop.ai_daily_limit if shop.ai_daily_limit is not None else 200
    if limit <= 0:
        raise DailyLimitExceeded("AI generation is disabled for this shop (ai_daily_limit=0)")
    used = await _generated_today(session, shop.id)
    if used >= limit:
        raise DailyLimitExceeded(f"daily AI limit reached ({used}/{limit})")

    recent = await _recent_answers(session, shop.id)
    context, profile = build_context_and_profile(shop, review, recent)
    if provider is None:
        if (shop.ai_provider or "").strip().lower() == "managed":
            provider = await build_managed_provider(session, client=client)
        else:
            provider = build_provider_for_shop(shop, client=client)
    engine = AIEngine(provider)

    try:
        result = await engine.generate(context, profile)
    except (AIProviderError, QualityGateError, ValueError) as exc:
        review.error = f"generation: {exc}"
        await session.commit()
        raise

    now = datetime.now(UTC)
    review.generated_text = result.text
    review.ai_model = result.model
    review.ai_tokens_total = result.usage.total_tokens
    review.generated_at = now
    review.state = "generated"
    review.error = None
    await session.commit()
    await track(
        session,
        event="review.generated",
        user_id=shop.owner_id,
        shop_id=shop.id,
        meta={"model": result.model, "tokens": result.usage.total_tokens},
    )
    return result
