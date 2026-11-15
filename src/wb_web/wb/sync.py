"""Sync WB reviews into the local queue (idempotent upsert by wb_review_id).

Scope: unanswered reviews (the ones awaiting a reply). Answered reviews are
reconciled by the publish protocol's remote recheck, not by re-insert.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.core.security import SecretEncryptor
from wb_web.db.models import Review, Shop
from wb_web.wb.client import WildberriesClient
from wb_web.wb.types import Feedback

ClientFactory = Callable[[Shop], WildberriesClient]


@dataclass(frozen=True)
class SyncResult:
    fetched: int
    created: int
    updated: int


def build_client_for_shop(shop: Shop) -> WildberriesClient:
    token = SecretEncryptor.from_settings().decrypt(shop.wb_token_encrypted)
    return WildberriesClient(token, sandbox=shop.wb_sandbox)


def _mutable_fields(feedback: Feedback) -> dict:
    return {
        "text": feedback.text,
        "rating": feedback.rating,
        "product_name": feedback.product.product_name,
        "created_at_wb": feedback.created_at,
        "review_meta": {
            "pros": feedback.pros,
            "cons": feedback.cons,
            "supplier_article": feedback.product.supplier_article,
            "nm_id": feedback.product.nm_id,
        },
    }


async def verify_shop_connection(
    shop: Shop, *, client: WildberriesClient | None = None
) -> dict:
    """Ping WB: seller info + feedbacks access. Caller persists shop.wb_seller_name."""
    own_client = client is None
    client = client or build_client_for_shop(shop)
    try:
        validation = await client.validate_token()
        return {
            "seller_name": validation.seller.name,
            "display_name": validation.seller.display_name,
            "feedback_access": validation.feedback_access,
        }
    finally:
        if own_client:
            await client.aclose()


async def sync_shop_reviews(
    session: AsyncSession,
    shop: Shop,
    *,
    client: WildberriesClient | None = None,
    page_size: int = 100,
    max_items: int = 1000,
) -> SyncResult:
    own_client = client is None
    client = client or build_client_for_shop(shop)
    fetched = created = updated = 0
    try:
        async for feedback in client.iter_feedbacks(is_answered=False, page_size=page_size):
            fetched += 1
            if fetched > max_items:
                break
            existing = (
                await session.execute(
                    select(Review).where(
                        Review.shop_id == shop.id,
                        Review.wb_review_id == feedback.feedback_id,
                    )
                )
            ).scalar_one_or_none()
            fields = _mutable_fields(feedback)
            if existing is not None:
                # Never resurrect/mutate workflow state (published/skipped/failed...)
                for key, value in fields.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                session.add(
                    Review(
                        shop_id=shop.id,
                        wb_review_id=feedback.feedback_id,
                        state="new",
                        **fields,
                    )
                )
                created += 1
        await session.commit()
        return SyncResult(fetched=fetched, created=created, updated=updated)
    finally:
        if own_client:
            await client.aclose()
