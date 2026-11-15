"""Auth routes (JSON API; HTML/HTMX UI comes with the web frontend)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.auth.service import InvalidTokenError, request_magic_link, verify_magic_link
from wb_web.core.config import get_settings
from wb_web.db.base import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
async def verify_link(body: VerifyBody, session: SessionDep) -> dict:
    try:
        user = await verify_magic_link(session, body.token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "user_id": user.id, "email": user.email}
