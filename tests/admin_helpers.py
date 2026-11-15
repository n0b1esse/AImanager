"""Shared 2FA helpers for admin-gated tests."""

import asyncio

import pyotp
from sqlalchemy import select

from wb_web.admin.deps import owner_emails, support_emails
from wb_web.core.security import SecretEncryptor
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import AdminUser

ADMIN_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def _role(email: str) -> str | None:
    email = email.lower()
    if email in owner_emails():
        return "owner"
    if email in support_emails():
        return "support"
    return None


async def set_2fa(email: str, secret: str = ADMIN_SECRET, role: str | None = None) -> bool:
    email = email.lower()
    resolved = role or _role(email)
    if resolved is None:
        return False
    sm = get_sessionmaker()
    async with sm() as session:
        row = (
            await session.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        encrypted = SecretEncryptor.from_settings().encrypt(secret)
        if row is None:
            row = AdminUser(
                email=email,
                role=resolved,
                totp_secret_encrypted=encrypted,
                totp_enabled=True,
            )
            session.add(row)
        else:
            row.role = resolved
            row.totp_secret_encrypted = encrypted
            row.totp_enabled = True
        await session.commit()
    return True


def admin_code(secret: str = ADMIN_SECRET) -> str:
    return pyotp.TOTP(secret).now()


def setup_admin(client, email: str, secret: str = ADMIN_SECRET, role: str | None = None) -> bool:
    """Give the admin user a working 2FA setup and pass the gate on this client."""
    ok = asyncio.run(set_2fa(email, secret, role))
    if ok:
        client.post("/app/admin/2fa", data={"code": admin_code(secret)})
    return ok
