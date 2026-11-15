from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Review, Shop, User
from wb_web.queue import (
    WBClientError,
    WBTransientError,
    claim_next_task,
    publication_marker_for,
    publish_review,
)


class FakeWBReviewsClient:
    def __init__(self, mode: str = "ok") -> None:
        self.remote: dict[str, dict[str, Any] | None] = {}
        self.post_calls = 0
        self.mode = mode

    async def get_review(self, wb_review_id: str) -> dict[str, Any]:
        return {"id": wb_review_id, "answer": self.remote.get(wb_review_id)}

    async def post_answer(self, wb_review_id: str, text: str) -> None:
        self.post_calls += 1
        if self.mode == "timeout_but_published":
            # Server processed it; response lost.
            self.remote[wb_review_id] = {"text": text, "state": "published"}
            raise WBTransientError("read timeout")
        if self.mode == "timeout_no_post":
            raise WBTransientError("read timeout")
        if self.mode == "client_error":
            raise WBClientError("409 answer already exists")
        self.remote[wb_review_id] = {"text": text, "state": "published"}


async def _make_review(
    *,
    state: str = "approved",
    wb_review_id: str = "wb-1",
    generated_text: str = "Спасибо за отзыв! Будем стараться лучше.",
    email: str = "queue@example.com",
) -> int:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        user = User(email=email, trial_ends_at=datetime.now(UTC) + timedelta(days=7))
        session.add(user)
        await session.flush()
        shop = Shop(owner_id=user.id, name="shop", wb_token_encrypted="enc", mode="semi")
        session.add(shop)
        await session.flush()
        review = Review(
            shop_id=shop.id,
            wb_review_id=wb_review_id,
            state=state,
            rating=5,
            text="Всё отлично",
            generated_text=generated_text,
        )
        session.add(review)
        await session.commit()
        return review.id


async def _get_review(review_id: int) -> Review:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        session.expunge(review)
        return review


@pytest.mark.asyncio
async def test_claim_single_winner_and_lease_set() -> None:
    review_id = await _make_review()
    session_maker = get_sessionmaker()

    async with session_maker() as s1:
        claimed = await claim_next_task(s1, lease_seconds=120)
        assert claimed is not None
        assert claimed.id == review_id
        assert claimed.claimed_at is not None
        assert claimed.claim_expires_at is not None
        assert claimed.claim_token

    async with session_maker() as s2:
        assert await claim_next_task(s2, lease_seconds=120) is None


@pytest.mark.asyncio
async def test_claim_reclaims_expired_lease() -> None:
    review_id = await _make_review()
    session_maker = get_sessionmaker()

    async with session_maker() as s1:
        claimed = await claim_next_task(s1, lease_seconds=120)
    assert claimed is not None

    async with session_maker() as s:
        row = (
            await s.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        row.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()

    async with session_maker() as s2:
        reclaimed = await claim_next_task(s2, lease_seconds=120)
        assert reclaimed is not None
        assert reclaimed.id == review_id


@pytest.mark.asyncio
async def test_claim_ignores_unapproved_states() -> None:
    await _make_review(state="new")
    session_maker = get_sessionmaker()
    async with session_maker() as s:
        assert await claim_next_task(s) is None


@pytest.mark.asyncio
async def test_publish_success() -> None:
    review_id = await _make_review()
    wb = FakeWBReviewsClient()
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        result = await publish_review(session, review, wb)

    assert result.outcome == "published"
    assert wb.post_calls == 1
    review = await _get_review(review_id)
    assert review.state == "published"
    assert review.published_at is not None
    assert review.publication_marker == publication_marker_for(review)


@pytest.mark.asyncio
async def test_publish_remote_already_answered_no_post() -> None:
    review_id = await _make_review()
    wb = FakeWBReviewsClient()
    wb.remote["wb-1"] = {"text": "existing answer", "state": "published"}
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        result = await publish_review(session, review, wb)

    assert result.outcome == "remote_already_answered"
    assert wb.post_calls == 0


@pytest.mark.asyncio
async def test_ambiguous_timeout_then_recheck_never_duplicates() -> None:
    review_id = await _make_review()
    wb = FakeWBReviewsClient(mode="timeout_but_published")
    session_maker = get_sessionmaker()

    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        first = await publish_review(session, review, wb)
    assert first.outcome == "ambiguous"
    assert wb.post_calls == 1
    review = await _get_review(review_id)
    assert review.state == "publishing"

    # Next run: network is healthy again, but the server already has the answer.
    wb.mode = "ok"
    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        second = await publish_review(session, review, wb)
    assert second.outcome == "remote_already_answered"
    assert wb.post_calls == 1  # NO duplicate publication
    review = await _get_review(review_id)
    assert review.state == "published"


@pytest.mark.asyncio
async def test_client_error_marks_failed_without_retry() -> None:
    review_id = await _make_review()
    wb = FakeWBReviewsClient(mode="client_error")
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        result = await publish_review(session, review, wb)

    assert result.outcome == "failed_client"
    assert wb.post_calls == 1
    assert (await _get_review(review_id)).state == "failed"


@pytest.mark.asyncio
async def test_safety_gate_blocks_short_text() -> None:
    review_id = await _make_review(generated_text="a")
    wb = FakeWBReviewsClient()
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one()
        result = await publish_review(session, review, wb)

    assert result.outcome == "safety_rejected"
    assert wb.post_calls == 0
    assert (await _get_review(review_id)).state == "failed"
