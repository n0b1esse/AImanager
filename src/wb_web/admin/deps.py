"""Admin authorization: env bootstrap (owner/support) + mandatory TOTP 2FA.

Owner emails (OWNER_EMAILS) get role 'owner'. Extra emails (ADMIN_SUPPORT_EMAILS)
get role 'support' (read-only). Both require TOTP after first login — without it
/app/admin redirects to setup (web) or returns 403 2fa_required (JSON API).
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.auth.deps import CurrentUser, SessionDep
from wb_web.core.config import get_settings
from wb_web.core.session import verify_session
from wb_web.db.models import AdminUser

ADMIN_2FA_COOKIE = "aim_admin2fa"
ADMIN_2FA_TTL_SECONDS = 8 * 3600


def owner_emails() -> set[str]:
    return {e.strip().lower() for e in get_settings().owner_emails.split(",") if e.strip()}


def support_emails() -> set[str]:
    return {e.strip().lower() for e in get_settings().admin_support_emails.split(",") if e.strip()}


def role_for_email(email: str) -> str | None:
    email = email.lower()
    if email in owner_emails():
        return "owner"
    if email in support_emails():
        return "support"
    return None


async def get_or_create_admin_user(session: AsyncSession, email: str) -> AdminUser | None:
    email = email.lower()
    role = role_for_email(email)
    if role is None:
        return None
    row = (
        await session.execute(select(AdminUser).where(AdminUser.email == email))
    ).scalar_one_or_none()
    if row is None:
        row = AdminUser(email=email, role=role)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    elif row.role != role:
        # Env list is the source of truth for roles
        row.role = role
        await session.commit()
    return row


def admin_2fa_cookie_valid(request: Request, admin: AdminUser) -> bool:
    raw = request.cookies.get(ADMIN_2FA_COOKIE)
    if not raw:
        return False
    payload = verify_session(raw, get_settings().session_secret)
    return bool(payload and payload.get("purpose") == "admin2fa" and payload.get("adm") == admin.id)


async def require_owner(user: CurrentUser, session: SessionDep, request: Request) -> CurrentUser:
    """JSON API gate: owner email + 2FA done. setup > login cookie required."""
    if role_for_email(user.email) != "owner":
        raise HTTPException(status_code=403, detail="admin access required")
    admin = await get_or_create_admin_user(session, user.email)
    assert admin is not None
    if not admin.totp_enabled:
        raise HTTPException(status_code=403, detail={"error": "2fa_setup_required"})
    if not admin_2fa_cookie_valid(request, admin):
        raise HTTPException(status_code=403, detail={"error": "2fa_required"})
    return user


async def require_support_or_owner(user: CurrentUser, session: SessionDep, request: Request) -> AdminUser:
    if role_for_email(user.email) is None:
        raise HTTPException(status_code=403, detail="admin access required")
    admin = await get_or_create_admin_user(session, user.email)
    assert admin is not None
    if not admin.totp_enabled:
        raise HTTPException(status_code=403, detail={"error": "2fa_setup_required"})
    if not admin_2fa_cookie_valid(request, admin):
        raise HTTPException(status_code=403, detail={"error": "2fa_required"})
    return admin
