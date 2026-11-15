"""Auth routes: request magic link, verify (sets session cookie), logout, me."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from wb_web.auth.deps import CurrentUser, SessionDep
from wb_web.auth.service import InvalidTokenError, request_magic_link, verify_magic_link
from wb_web.billing.access import resolve_access
from wb_web.core.config import get_settings
from wb_web.core.session import SESSION_COOKIE, SESSION_TTL_SECONDS, sign_session
from wb_web.db.models import Subscription

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: int) -> None:
    settings = get_settings()
    token = sign_session({"uid": user_id}, settings.session_secret, SESSION_TTL_SECONDS)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


class RequestLinkBody(BaseModel):
    email: EmailStr


class VerifyBody(BaseModel):
    token: str


@router.post("/request")
async def request_link(body: RequestLinkBody, session: SessionDep) -> dict:
    raw_token = await request_magic_link(session, body.email)
    # Anti-enumeration: same response either way. Dev convenience: expose token outside production.
    response = {"ok": True}
    if get_settings().app_env != "production":
        response["dev_token"] = raw_token
    return response


@router.post("/verify")
async def verify_link(body: VerifyBody, session: SessionDep) -> Response:
    try:
        user = await verify_magic_link(session, body.token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse({"ok": True, "user_id": user.id, "email": user.email})
    _set_session_cookie(response, user.id)
    return response


@router.post("/logout")
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/me")
async def me(user: CurrentUser, session: SessionDep) -> dict:
    access = await resolve_access(user, session)
    subscription = (
        await session.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one_or_none()
    return {
        "id": user.id,
        "email": user.email,
        "trial_ends_at": user.trial_ends_at.isoformat() if user.trial_ends_at else None,
        "access": {"state": access.state, "reason": access.reason, "allowed": access.allowed},
        "subscription": None
        if subscription is None
        else {
            "plan": subscription.plan,
            "status": subscription.status,
            "current_period_end": subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else None,
        },
    }
