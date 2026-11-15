from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from wb_web.ai.generate import (
    AIRequestError,
    DailyLimitExceeded,
    build_provider_for_shop,
    generate_for_review,
)
from wb_web.ai.models import GenerationResult, TokenUsage
from wb_web.ai.providers import AIProviderError
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Review, Shop, User


class StubProvider:
    name = "stub"
    model = "stub-1"
    calls = 0

    def __init__(self, reply="Спасибо за отзыв! Нам важно ваше мнение."):
        self.reply = reply

    async def generate(self, context, profile):
        StubProvider.calls += 1
        return GenerationResult(
            text=self.reply, provider=self.name, model=self.model,
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


async def _make_shop(*, niche_ids=None, ai_daily_limit=None, mode="semi") -> tuple[User, Shop]:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        user = User(email=f"gen-{datetime.now(UTC).timestamp()}@example.com",
                    trial_ends_at=datetime.now(UTC) + timedelta(days=7))
        session.add(user)
        await session.flush()
        shop = Shop(
            owner_id=user.id,
            name="gen-shop",
            wb_token_encrypted="enc",
            niche_ids=niche_ids or [],
            mode=mode,
            ai_provider="ollama",
            ai_model="stub-model",
        )
        if ai_daily_limit is not None:
            shop.ai_daily_limit = ai_daily_limit
        session.add(shop)
        await session.commit()
        return user, shop


async def _make_review(shop_id: int, *, wb_id="wb-gen-1", text="Всё хорошо", rating=5) -> Review:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        review = Review(shop_id=shop_id, wb_review_id=wb_id, state="new", text=text, rating=rating)
        session.add(review)
        await session.commit()
        await session.refresh(review)
        return review


@pytest.mark.asyncio
async def test_generation_stores_text_and_usage() -> None:
    _, shop = await _make_shop()
    review = await _make_review(shop.id)

    session_maker = get_sessionmaker()
    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.id == shop.id))).scalar_one()
        review = (await session.execute(select(Review).where(Review.id == review.id))).scalar_one()
        result = await generate_for_review(session, shop, review, provider=StubProvider())

    assert result.usage.total_tokens == 15
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        stored = (
            await session.execute(select(Review).where(Review.id == review.id))
        ).scalar_one()
        assert stored.state == "generated"
        assert stored.generated_text == "Спасибо за отзыв! Нам важно ваше мнение."
        assert stored.ai_tokens_total == 15
        assert stored.ai_model == "stub-1"
        assert stored.generated_at is not None


@pytest.mark.asyncio
async def test_daily_limit_enforced() -> None:
    _, shop = await _make_shop(ai_daily_limit=1)
    first = await _make_review(shop.id, wb_id="wb-limit-1")
    second = await _make_review(shop.id, wb_id="wb-limit-2")

    session_maker = get_sessionmaker()
    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.id == shop.id))).scalar_one()
        first = (await session.execute(select(Review).where(Review.id == first.id))).scalar_one()
        await generate_for_review(session, shop, first, provider=StubProvider())

    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.id == shop.id))).scalar_one()
        second = (await session.execute(select(Review).where(Review.id == second.id))).scalar_one()
        with pytest.raises(DailyLimitExceeded):
            await generate_for_review(session, shop, second, provider=StubProvider())


@pytest.mark.asyncio
async def test_zero_daily_limit_disables_generation() -> None:
    _, shop = await _make_shop(ai_daily_limit=0)
    review = await _make_review(shop.id, wb_id="wb-off-1")
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.id == shop.id))).scalar_one()
        review = (await session.execute(select(Review).where(Review.id == review.id))).scalar_one()
        with pytest.raises(DailyLimitExceeded):
            await generate_for_review(session, shop, review, provider=StubProvider())


@pytest.mark.asyncio
async def test_provider_error_marks_review_failed() -> None:
    class FailingStub(StubProvider):
        async def generate(self, context, profile):
            raise AIProviderError("provider exploded")

    _, shop = await _make_shop()
    review = await _make_review(shop.id, wb_id="wb-fail-1")
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.id == shop.id))).scalar_one()
        review = (await session.execute(select(Review).where(Review.id == review.id))).scalar_one()
        with pytest.raises(AIProviderError):
            await generate_for_review(session, shop, review, provider=FailingStub())

    async with session_maker() as session:
        stored = (
            await session.execute(select(Review).where(Review.wb_review_id == "wb-fail-1"))
        ).scalar_one()
        assert stored.state == "new"
        assert "provider exploded" in (stored.error or "")


def test_unconfigured_openai_raises() -> None:
    shop = Shop(name="naked", wb_token_encrypted="enc", ai_provider="openai", ai_api_key_encrypted="")
    with pytest.raises(AIRequestError):
        build_provider_for_shop(shop)
