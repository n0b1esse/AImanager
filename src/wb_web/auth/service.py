"""Magic-link authentication: token issuing and consumption.

Dev mode: the link is logged (and returned in the API response when app_env != 'production').
Production: the link must be delivered by an email integration (SMTP/API) — TODO Phase 1 cont.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.billing.plans import normalize_plan
from wb_web.core.config import get_settings
from wb_web.core.events import track
from wb_web.core.security import generate_token, hash_token
from wb_web.db.models import MagicLinkToken, Subscription, User

logger = logging.getLogger(__name__)


class InvalidTokenError(Exception):
    pass


async def ensure_user(session: AsyncSession, email: str, plan: str | None = None) -> User:
    """Create (or reuse) a user and apply the chosen plan to a fresh trial."""
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("invalid email")
    selected_plan = normalize_plan(plan)

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        now = datetime.now(UTC)
        user = User(email=email, trial_ends_at=now + timedelta(days=7), is_active=True)
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                plan=selected_plan or "trial",
                status="active",
                provider="manual",
                current_period_end=user.trial_ends_at,
            )
        )
        await track(session, event="user.signup", user_id=user.id)
    elif selected_plan:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.user_id == user.id))
        ).scalar_one_or_none()
        # Only override the trial placeholder — never touch a paid/cancelled plan.
        if subscription is not None and subscription.plan == "trial":
            subscription.plan = selected_plan
    return user


async def login_direct(session: AsyncSession, email: str, plan: str | None = None) -> User:
    """TEMPORARY: create/reuse the user and sign in without sending an email."""
    user = await ensure_user(session, email, plan)
    await session.commit()
    await track(session, event="user.login", user_id=user.id)
    return user


async def request_magic_link(session: AsyncSession, email: str, plan: str | None = None) -> str:
    """Create (or reuse) a user, issue a one-time token, return the raw token.

    A selected plan travels with the magic link so the choice survives the
    email round-trip and is applied when the link is consumed.
    """
    settings = get_settings()
    user = await ensure_user(session, email, plan)
    selected_plan = normalize_plan(plan)

    raw_token = generate_token()
    ttl = timedelta(minutes=settings.magic_link_ttl_minutes)
    session.add(
        MagicLinkToken(user_id=user.id, token_hash=hash_token(raw_token), expires_at=datetime.now(UTC) + ttl)
    )
    await session.commit()

    base = settings.public_base_url.rstrip("/") if settings.public_base_url else ""
    suffix = f"&plan={selected_plan}" if selected_plan else ""
    link = (
        f"{base}/auth/link?token={raw_token}{suffix}"
        if base
        else f"/auth/link?token={raw_token}{suffix}"
    )
    if settings.smtp_host or base:
        from wb_web.core.mailer import send_magic_link

        try:
            await send_magic_link(to=email, link=link, ttl_minutes=settings.magic_link_ttl_minutes)
        except Exception:  # noqa: BLE001 — the request must not leak mail infra state
            logger.error("failed to deliver magic link", extra={"action": "mail_send"})
    if settings.app_env != "production":
        # Without a public URL/SMTP, dev links must be visible in the console.
        logger.warning("DEV magic link for %s: %s", email, link)
    return raw_token


async def verify_magic_link(session: AsyncSession, raw_token: str, plan: str | None = None) -> User:
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
    selected_plan = normalize_plan(plan)
    if selected_plan:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.user_id == user.id))
        ).scalar_one_or_none()
        # Only override the trial placeholder — never touch a paid/cancelled plan.
        if subscription is not None and subscription.plan == "trial":
            subscription.plan = selected_plan
    await session.commit()
    await track(session, event="user.login", user_id=user.id)
    return user
