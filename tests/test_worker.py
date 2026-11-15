from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from wb_web.ai.models import GenerationResult, TokenUsage
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Review, Shop, User
from wb_web.worker import process_generation_queue


class StubProvider:
    name = "stub"
    model = "stub-1"

    async def generate(self, context, profile):
        return GenerationResult(
            text="Спасибо за отзыв! Нам важно ваше мнение.",
            provider=self.name,
            model=self.model,
            usage=TokenUsage(input_tokens=3, output_tokens=4, total_tokens=7),
        )


async def _seed(email: str, *, mode: str, automation_paused: bool = False, review_state: str = "new"):
    sm = get_sessionmaker()
    async with sm() as session:
        user = User(email=email, trial_ends_at=datetime.now(UTC) + timedelta(days=7))
        session.add(user)
        await session.flush()
        shop = Shop(
            owner_id=user.id,
            name=f"w-{email}",
            wb_token_encrypted="enc",
            mode=mode,
            automation_paused=automation_paused,
            ai_provider="ollama",
            ai_model="stub-1",
        )
        session.add(shop)
        await session.flush()
        review = Review(
            shop_id=shop.id, wb_review_id=f"wb-{email}", state=review_state,
            text="Всё хорошо", rating=5,
        )
        session.add(review)
        await session.commit()
        return shop.id, review.id


@pytest.mark.asyncio
async def test_semi_auto_shop_generates(monkeypatch) -> None:
    monkeypatch.setattr(
        "wb_web.ai.generate.build_provider_for_shop",
        lambda shop, **kwargs: StubProvider(),
    )
    _, review_id = await _seed("semi-a@example.com", mode="semi")

    generated = await process_generation_queue(max_items=5)
    assert generated == 1

    sm = get_sessionmaker()
    async with sm() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        assert review.state == "generated"
        assert review.generated_text
        assert review.ai_tokens_total == 7


@pytest.mark.asyncio
async def test_manual_shop_not_processed(monkeypatch) -> None:
    monkeypatch.setattr(
        "wb_web.ai.generate.build_provider_for_shop",
        lambda shop, **kwargs: StubProvider(),
    )
    _, review_id = await _seed("manual-a@example.com", mode="manual")
    assert await process_generation_queue(max_items=5) == 0

    sm = get_sessionmaker()
    async with sm() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        assert review.state == "new"


@pytest.mark.asyncio
async def test_paused_shop_not_processed(monkeypatch) -> None:
    monkeypatch.setattr(
        "wb_web.ai.generate.build_provider_for_shop",
        lambda shop, **kwargs: StubProvider(),
    )
    _, review_id = await _seed("paused-a@example.com", mode="auto", automation_paused=True)
    assert await process_generation_queue(max_items=5) == 0
