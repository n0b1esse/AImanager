"""Auth dependencies: current user and tenant scoping."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.core.config import get_settings
from wb_web.core.session import SESSION_COOKIE, verify_session
from wb_web.db.base import get_session
from wb_web.db.models import Shop, User

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


async def get_optional_user(request: Request, session: SessionDep) -> User | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    settings = get_settings()
    payload = verify_session(raw, settings.session_secret)
    if payload is None:
        return None
    uid = payload.get("uid")
    if not isinstance(uid, int):
        return None
    user = await session.get(User, uid)
    if user is None or not user.is_active:
        return None
    return user


async def get_current_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    if user is None:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_shop(shop_id: int, user: CurrentUser, session: SessionDep) -> Shop:
    """Tenant scope: a shop is visible/usable only by its owner. Else 404 (no existence leak)."""
    shop = await session.get(Shop, shop_id)
    if shop is None or shop.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


CurrentShop = Annotated[Shop, Depends(get_current_shop)]
