"""Read-only analytics queries for client and admin views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.core.config import get_settings
from wb_web.db.models import ProductEvent, Subscription, User


async def _count_events(session: AsyncSession, *, user_id: int | None, event: str, since: datetime | None = None) -> int:
    stmt = select(func.count(ProductEvent.id)).where(ProductEvent.event == event)
    if user_id is not None:
        stmt = stmt.where(ProductEvent.user_id == user_id)
    if since is not None:
        stmt = stmt.where(ProductEvent.created_at >= since)
    return int((await session.execute(stmt)).scalar_one())


async def client_stats(session: AsyncSession, user_id: int) -> dict:
    now = datetime.now(UTC)
    generated_30d = await _count_events(session, user_id=user_id, event="review.generated", since=now - timedelta(days=30))
    approved_30d = await _count_events(session, user_id=user_id, event="review.approved", since=now - timedelta(days=30))
    skipped_30d = await _count_events(session, user_id=user_id, event="review.skipped", since=now - timedelta(days=30))
    published_7d = await _count_events(session, user_id=user_id, event="review.published", since=now - timedelta(days=7))
    published_30d = await _count_events(session, user_id=user_id, event="review.published", since=now - timedelta(days=30))
    approval_rate = (approved_30d / generated_30d) if generated_30d else 0.0
    return {
        "generated_30d": generated_30d,
        "approved_30d": approved_30d,
        "skipped_30d": skipped_30d,
        "published_7d": published_7d,
        "published_30d": published_30d,
        "approval_rate": approval_rate,
        # 5 minutes of manager time per answer, saved
        "hours_saved_30d": round(published_30d * 5 / 60, 1),
    }


async def admin_sales(session: AsyncSession) -> dict:
    users_total = int((await session.execute(select(func.count(User.id)))).scalar_one())
    trial_users = int(
        (await session.execute(select(func.count(Subscription.id)).where(Subscription.plan == "trial"))).scalar_one()
    )
    paid_users = int(
        (
            await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.plan != "trial",
                    Subscription.status.in_(("active", "grace")),
                )
            )
        ).scalar_one()
    )
    grace_users = int(
        (await session.execute(select(func.count(Subscription.id)).where(Subscription.status == "grace"))).scalar_one()
    )
    read_only = int(
        (
            await session.execute(
                select(func.count(Subscription.id)).where(Subscription.status.in_(("read_only", "cancelled")))
            )
        ).scalar_one()
    )
    since_30d = datetime.now(UTC) - timedelta(days=30)
    activations_30d = await _count_events(session, user_id=None, event="subscription.activated", since=since_30d)
    plan_counts = dict(
        (
            await session.execute(
                select(Subscription.plan, func.count(Subscription.id))
                .where(Subscription.status.in_(("active", "grace")))
                .group_by(Subscription.plan)
            )
        ).all()
    )
    settings = get_settings()
    mrr = (
        plan_counts.get("starter", 0) * settings.price_starter
        + plan_counts.get("pro", 0) * settings.price_pro
        + plan_counts.get("business", 0) * settings.price_business
    )
    return {
        "users_total": users_total,
        "trial_users": trial_users,
        "paid_users": paid_users,
        "grace_users": grace_users,
        "read_only": read_only,
        "activations_30d": activations_30d,
        "conversion": (paid_users / users_total) if users_total else 0.0,
        "mrr": round(mrr, 2),
        "plan_counts": plan_counts,
        "prices_configured": (settings.price_starter or settings.price_pro or settings.price_business) != 0,
    }
