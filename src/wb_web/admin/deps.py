"""Admin authorization (v1): owner-only via OWNER_EMAILS env list.

Phase 3 upgrades this to DB-backed roles (owner/admin/support/content) + TOTP 2FA.
"""

from __future__ import annotations

from fastapi import HTTPException

from wb_web.auth.deps import CurrentUser
from wb_web.core.config import get_settings


def owner_emails() -> set[str]:
    raw = get_settings().owner_emails
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


async def require_owner(user: CurrentUser) -> CurrentUser:
    if user.email.lower() not in owner_emails():
        raise HTTPException(status_code=403, detail="admin access required")
    return user
