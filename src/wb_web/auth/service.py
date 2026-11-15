"""Magic-link authentication: token issuing and consumption.

Dev mode: the link is logged (and returned in the API response when app_env != 'production').
Production: the link must be delivered by an email integration (SMTP/API) — TODO Phase 1 cont.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.core.config import get_settings
from wb_web.core.security import generate_token, hash_token
from wb_web.db.models import MagicLinkToken, Subscription, User

logger = logging.getLogger(__name__)


class InvalidTokenError(Exception):
    pass


async def request_magic_link(session: AsyncSession, email: str) -> str:
    """Create (or reuse) a user, issue a one-time token, return the raw token."""
    settings = get_settings()
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("invalid email")

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        now = datetime.now(UTC)
        user = User(email=email, trial_ends_at=now + timedelta(days=7), is_active=True)
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                plan="trial",
                status="active",
                provider="manual",
                current_period_end=user.trial_ends_at,
            )
        )

    raw_token = generate_token()
    ttl = timedelta(minutes=settings.magic_link_ttl_minutes)
    session.add(
        MagicLinkToken(user_id=user.id, token_hash=hash_token(raw_token), expires_at=datetime.now(UTC) + ttl)
    )
    await session.commit()

    link = f"/auth/verify?token={raw_token}"
    if settings.app_env == "production":
        logger.info("magic link issued for user_id=%s", user.id)
    else:
        # Without an SMTP integration, dev links must be visible in the console.
        logger.warning("DEV magic link for %s: %s", email, link)
    return raw_token


async def verify_magic_link(session: AsyncSession, raw_token: str) -> User:
    now = datetime.now(UTC)
    token_hash = hash_token(raw_token)
    token = (
        await session.execute(select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if token is None or token.consumed_at is not None:
        raise InvalidTokenError("Link is invalid or already used")
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        raise InvalidTokenError("Link expired")
    token.consumed_at = now

    user = (await session.execute(select(User).where(User.id == token.user_id))).scalar_one()
    await session.commit()
    return user
