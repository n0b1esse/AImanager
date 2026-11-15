"""Manual subscription activation (operator/admin action, audited)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.admin.audit import record_admin_action
from wb_web.db.models import Subscription, User

VALID_PLANS = frozenset({"starter", "pro", "business"})


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


async def activate_subscription(
    session: AsyncSession,
    *,
    actor_id: int,
    target_user_id: int,
    plan: str,
    months: int,
    ip: str | None = None,
    note: str | None = None,
) -> Subscription:
    if plan not in VALID_PLANS:
        raise ValueError(f"plan must be one of {sorted(VALID_PLANS)}")
    if not 1 <= months <= 24:
        raise ValueError("months must be between 1 and 24")

    user = (
        await session.execute(select(User).where(User.id == target_user_id))
    ).scalar_one_or_none()
    if user is None:
        raise ValueError("user not found")

    subscription = (
        await session.execute(select(Subscription).where(Subscription.user_id == target_user_id))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    base = _aware(subscription.current_period_end) if subscription else None
    if base is None or base < now:
        base = now

    if subscription is None:
        subscription = Subscription(user_id=user.id, provider="manual")
        session.add(subscription)
    subscription.plan = plan
    subscription.status = "active"
    subscription.provider = "manual"
    subscription.current_period_end = base + timedelta(days=30 * months)

    detail: dict[str, Any] = {"plan": plan, "months": months}
    if note:
        detail["note"] = note
    await record_admin_action(
        session,
        actor_id=actor_id,
        action="subscription.activate",
        target=f"user:{user.id}",
        detail=detail,
        ip=ip,
    )
    await session.refresh(subscription)
    return subscription
