"""Reviews routes (JSON API; HTMX UI wraps these endpoints)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from wb_web.ai.generate import AIRequestError, DailyLimitExceeded, generate_for_review
from wb_web.auth.deps import CurrentShop, SessionDep
from wb_web.billing.access import ActiveAccess
from wb_web.db.models import Review, Shop
from wb_web.queue import publish_review
from wb_web.wb.adapter import WBReviewsAdapter
from wb_web.wb.client import WildberriesClient
from wb_web.wb.errors import (
    WildberriesAuthenticationError,
    WildberriesError,
    WildberriesRateLimitError,
)
from wb_web.wb.sync import build_client_for_shop

router = APIRouter(prefix="/shops/{shop_id}/reviews", tags=["reviews"])


def _public(review: Review) -> dict[str, Any]:
    return {
        "id": review.id,
        "wb_review_id": review.wb_review_id,
        "state": review.state,
        "rating": review.rating,
        "text": review.text,
        "product_name": review.product_name,
        "review_meta": review.review_meta,
        "generated_text": review.generated_text,
        "ai_model": review.ai_model,
        "ai_tokens_total": review.ai_tokens_total,
        "generated_at": review.generated_at.isoformat() if review.generated_at else None,
        "published_at": review.published_at.isoformat() if review.published_at else None,
        "error": review.error,
        "created_at": review.created_at.isoformat(),
    }


async def _get_review(session: SessionDep, shop: Shop, review_id: int) -> Review:
    review = (
        await session.execute(
            select(Review).where(Review.id == review_id, Review.shop_id == shop.id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@router.get("")
async def list_reviews(
    shop: CurrentShop,
    session: SessionDep,
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    stmt = select(Review).where(Review.shop_id == shop.id)
    if state:
        stmt = stmt.where(Review.state == state)
    result = await session.execute(stmt.order_by(Review.id.desc()).limit(limit).offset(offset))
    items = list(result.scalars())
    return {"reviews": [_public(review) for review in items], "count": len(items)}


@router.post("/{review_id}/generate")
async def generate_reply(
    shop: CurrentShop,
    review_id: int,
    session: SessionDep,
    _access: ActiveAccess,
) -> dict:
    review = await _get_review(session, shop, review_id)
    if review.state not in ("new", "generated", "failed"):
        raise HTTPException(status_code=409, detail=f"review cannot be generated in state '{review.state}'")
    try:
        result = await generate_for_review(session, shop, review)
    except DailyLimitExceeded as exc:
        raise HTTPException(status_code=429, detail={"error": "ai_daily_limit", "message": str(exc)}) from exc
    except AIRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # provider/transport/quality-gate — message is token-free
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ok": True,
        "state": review.state,
        "generated_text": review.generated_text,
        "ai_model": result.model,
        "ai_tokens_total": result.usage.total_tokens,
    }


@router.post("/{review_id}/approve")
async def approve_reply(shop: CurrentShop, review_id: int, session: SessionDep, _access: ActiveAccess) -> dict:
    review = await _get_review(session, shop, review_id)
    if review.state != "generated":
        raise HTTPException(status_code=409, detail=f"approve requires state 'generated', got '{review.state}'")
    if not (review.generated_text or "").strip():
        raise HTTPException(status_code=409, detail="nothing to approve: generated text is empty")
    review.state = "approved"
    review.error = None
    await session.commit()
    return {"ok": True, "state": review.state}


@router.post("/{review_id}/skip")
async def skip_reply(shop: CurrentShop, review_id: int, session: SessionDep) -> dict:
    review = await _get_review(session, shop, review_id)
    if review.state not in ("new", "generated", "approved", "failed"):
        raise HTTPException(status_code=409, detail=f"cannot skip review in state '{review.state}'")
    review.state = "skipped"
    await session.commit()
    return {"ok": True, "state": review.state}


@router.post("/{review_id}/publish")
async def publish_reply(shop: CurrentShop, review_id: int, session: SessionDep, _access: ActiveAccess) -> dict:
    review = await _get_review(session, shop, review_id)
    if review.state != "approved":
        raise HTTPException(status_code=409, detail=f"publish requires state 'approved', got '{review.state}'")
    client: WildberriesClient = build_client_for_shop(shop)
    try:
        result = await publish_review(session, review, WBReviewsAdapter(client))
    except WildberriesAuthenticationError as exc:
        raise HTTPException(status_code=400, detail="WB token is invalid or expired") from exc
    except WildberriesRateLimitError as exc:
        raise HTTPException(status_code=429, detail={"error": "wb_rate_limit", "retry_after": getattr(exc, "retry_after", None)}) from exc
    except WildberriesError as exc:
        raise HTTPException(status_code=502, detail="Wildberries API request failed") from exc
    finally:
        await client.aclose()
    return {"ok": True, "outcome": result.outcome, "state": review.state}
