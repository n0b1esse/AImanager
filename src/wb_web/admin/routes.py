"""Admin v1 (owner-only, JSON API; HTML console comes with the frontend)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from wb_web.admin.audit import record_admin_action
from wb_web.admin.deps import require_owner
from wb_web.auth.deps import SessionDep
from wb_web.billing.access import compute_access
from wb_web.billing.activate import activate_subscription
from wb_web.db.models import Shop, Subscription, User

router = APIRouter(prefix="/admin", tags=["admin"])

Owner = Annotated[User, Depends(require_owner)]


@router.get("/summary")
async def summary(_owner: Owner, session: SessionDep) -> dict:
    users_total = (await session.execute(select(func.count(User.id)))).scalar_one()
    shops_total = (await session.execute(select(func.count(Shop.id)))).scalar_one()
    plan_counts = dict(
        (
            await session.execute(
                select(Subscription.plan, func.count(Subscription.id)).group_by(Subscription.plan)
            )
        ).all()
    )
    status_counts = dict(
        (
            await session.execute(
                select(Subscription.status, func.count(Subscription.id)).group_by(Subscription.status)
            )
        ).all()
    )
    return {
        "users_total": users_total,
        "shops_total": shops_total,
        "plans": plan_counts,
        "statuses": status_counts,
    }


@router.get("/users")
async def list_users(_owner: Owner, session: SessionDep, limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    result = await session.execute(select(User).order_by(User.id).limit(limit).offset(offset))
    users = result.scalars().all()
    subs = {
        row.user_id: row
        for row in (
            await session.execute(select(Subscription).where(Subscription.user_id.in_([u.id for u in users])))
        ).scalars()
    }
    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "is_active": u.is_active,
                "trial_ends_at": u.trial_ends_at.isoformat() if u.trial_ends_at else None,
                "subscription": (
                    None
                    if (s := subs.get(u.id)) is None
                    else {
                        "plan": s.plan,
                        "status": s.status,
                        "current_period_end": s.current_period_end.isoformat()
                        if s.current_period_end
                        else None,
                        "access": compute_access(u, s).state,
                    }
                ),
            }
            for u in users
        ]
    }


class ActivateBody(BaseModel):
    plan: Literal["starter", "pro", "business"]
    months: int = Field(default=1, ge=1, le=24)
    note: str | None = Field(default=None, max_length=500)


@router.post("/users/{user_id}/activate")
async def activate(
    user_id: int, body: ActivateBody, owner: Owner, session: SessionDep, request: Request
) -> dict:
    try:
        subscription = await activate_subscription(
            session,
            actor_id=owner.id,
            target_user_id=user_id,
            plan=body.plan,
            months=body.months,
            ip=request.client.host if request.client else None,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "user_id": user_id,
        "plan": subscription.plan,
        "status": subscription.status,
        "current_period_end": subscription.current_period_end.isoformat()
        if subscription.current_period_end
        else None,
    }


class KillSwitchBody(BaseModel):
    paused: bool


@router.post("/shops/{shop_id}/kill-switch")
async def kill_switch(
    shop_id: int, body: KillSwitchBody, owner: Owner, session: SessionDep, request: Request
) -> dict:
    shop = await session.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    shop.automation_paused = body.paused
    await record_admin_action(
        session,
        actor_id=owner.id,
        action="shop.kill_switch",
        target=f"shop:{shop.id}",
        detail={"automation_paused": body.paused},
        ip=request.client.host if request.client else None,
    )
    await session.refresh(shop)
    return {"ok": True, "shop_id": shop.id, "automation_paused": shop.automation_paused}


class UserActiveBody(BaseModel):
    active: bool


@router.post("/users/{user_id}/active")
async def set_user_active(
    user_id: int, body: UserActiveBody, owner: Owner, session: SessionDep, request: Request
) -> dict:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    from wb_web.admin.deps import owner_emails

    if target.email.lower() in owner_emails():
        raise HTTPException(status_code=400, detail="cannot deactivate an owner account")
    target.is_active = body.active
    await record_admin_action(
        session,
        actor_id=owner.id,
        action="user.reactivate" if body.active else "user.deactivate",
        target=f"user:{target.id}",
        detail={"is_active": body.active},
        ip=request.client.host if request.client else None,
    )
    return {"ok": True, "user_id": target.id, "is_active": target.is_active}
