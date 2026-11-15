"""Sync NEW Ozon reviews into the local queue (idempotent upsert by review id)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.core.events import track
from wb_web.db.models import Review, Shop
from wb_web.ozon.client import OzonClient


@dataclass(frozen=True)
class OzonSyncResult:
    fetched: int
    created: int
    updated: int


async def verify_ozon_connection(shop: Shop, *, client: OzonClient | None = None) -> dict:
    """Smoke-check: fetch first page of NEW reviews (small). Caller persists nothing."""
    own_client = client is None
    client = client or OzonClient.from_shop(shop)
    try:
        page = await client.list_reviews(status="NEW", limit=20)
        return {
            "seller_name": None,
            "display_name": "Ozon Seller API",
            "feedback_access": True,
            "unanswered_preview": len(page.reviews),
        }
    finally:
        if own_client:
            await client.aclose()


async def sync_ozon_reviews(
    session: AsyncSession,
    shop: Shop,
    *,
    client: OzonClient | None = None,
    max_items: int = 1000,
) -> OzonSyncResult:
    own_client = client is None
    client = client or OzonClient.from_shop(shop)
    fetched = created = updated = 0
    try:
        async for review in client.iter_new_reviews(max_pages=20):
            fetched += 1
            if fetched > max_items:
                break
            existing = (
                await session.execute(
                    select(Review).where(
                        Review.shop_id == shop.id,
                        Review.wb_review_id == review.review_id,
                    )
                )
            ).scalar_one_or_none()
            fields = {
                "text": review.text,
                "rating": review.rating,
                "created_at_wb": review.published_at,
                "review_meta": {
                    "marketplace": "ozon",
                    "ozon_sku": review.sku,
                    "ozon_status": review.status,
                    "ozon_comments_amount": review.comments_amount,
                },
            }
            if existing is not None:
                for key, value in fields.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                session.add(
                    Review(
                        shop_id=shop.id,
                        wb_review_id=review.review_id,
                        state="new",
                        **fields,
                    )
                )
                created += 1
        await session.commit()
        await track(
            session,
            event="sync.completed",
            user_id=shop.owner_id,
            shop_id=shop.id,
            meta={"fetched": fetched, "created": created, "updated": updated, "marketplace": "ozon"},
        )
        return OzonSyncResult(fetched=fetched, created=created, updated=updated)
    finally:
        if own_client:
            await client.aclose()
