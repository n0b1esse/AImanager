"""Access gate: who may use paid features, in what state.

Rules:
- trial: user.trial_ends_at in the future -> allowed (state='trial')
- paid + status=active: allowed until current_period_end passes
- paid + period ended: 7-day grace -> allowed with warning (state='grace')
- grace older than 7 days / status=read_only / cancelled -> blocked (402)
- no subscription -> blocked (402)
Read access is never gated; the gate applies to actions that cost us money/ops
(e.g. creating shops, AI generation, publishing).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.auth.deps import CurrentUser, SessionDep
from wb_web.db.models import Subscription, User

GRACE_DAYS = 7


@dataclass(frozen=True)
class AccessContext:
    allowed: bool
    state: str  # trial | active | grace | read_only
    plan: str
    status: str | None
    reason: str | None
    trial_ends_at: datetime | None
    current_period_end: datetime | None

    @property
    def is_grace(self) -> bool:
        return self.state == "grace"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def compute_access(
    user: User,
    subscription: Subscription | None,
    now: datetime | None = None,
) -> AccessContext:
    now = now or datetime.now(UTC)
    trial_end = _aware(user.trial_ends_at)

    if subscription is None:
        return AccessContext(False, "read_only", "none", None, "no_subscription", trial_end, None)

    period_end = _aware(subscription.current_period_end)
    status = subscription.status
    plan = subscription.plan

    if plan == "trial":
        if trial_end is not None and trial_end > now:
            return AccessContext(True, "trial", plan, status, None, trial_end, period_end)
        return AccessContext(False, "read_only", plan, status, "trial_expired", trial_end, period_end)

    if status == "cancelled":
        return AccessContext(False, "read_only", plan, status, "cancelled", trial_end, period_end)
    if status == "read_only":
        return AccessContext(False, "read_only", plan, status, "read_only", trial_end, period_end)

    grace_deadline = period_end + timedelta(days=GRACE_DAYS) if period_end else None

    if status == "grace":
        if grace_deadline is None or grace_deadline >= now:
            return AccessContext(True, "grace", plan, status, "payment_failed", trial_end, period_end)
        return AccessContext(False, "read_only", plan, status, "grace_expired", trial_end, period_end)

    # status == active
    if period_end is not None and period_end <= now:
        if grace_deadline is not None and grace_deadline >= now:
            return AccessContext(True, "grace", plan, status, "period_ended_grace", trial_end, period_end)
        return AccessContext(False, "read_only", plan, status, "period_expired", trial_end, period_end)
    return AccessContext(True, "active", plan, status, None, trial_end, period_end)


async def resolve_access(user: User, session: AsyncSession) -> AccessContext:
    subscription = (
        await session.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one_or_none()
    return compute_access(user, subscription)


async def get_access_context(user: CurrentUser, session: SessionDep) -> AccessContext:
    return await resolve_access(user, session)


AccessDep = Annotated[AccessContext, Depends(get_access_context)]


async def require_active_access(ctx: AccessDep) -> AccessContext:
    """Gate for actions that require a live subscription (trial or paid). Reads stay open."""
    if not ctx.allowed:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "subscription_required",
                "state": ctx.state,
                "reason": ctx.reason,
                "trial_ends_at": ctx.trial_ends_at.isoformat() if ctx.trial_ends_at else None,
                "current_period_end": ctx.current_period_end.isoformat() if ctx.current_period_end else None,
            },
        )
    return ctx


ActiveAccess = Annotated[AccessContext, Depends(require_active_access)]
