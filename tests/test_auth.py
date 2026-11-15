import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.auth.service import InvalidTokenError, verify_magic_link
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import MagicLinkToken, Subscription, User
from wb_web.main import create_app


def test_magic_link_flow_via_api() -> None:
    client = TestClient(create_app())

    response = client.post("/auth/request", json={"email": "Seller@Example.com"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    token = body["dev_token"]  # APP_ENV=test -> token exposed for convenience

    response = client.post("/auth/verify", json={"token": token})
    assert response.status_code == 200
    assert response.json()["email"] == "seller@example.com"

    # One-time use
    response = client.post("/auth/verify", json={"token": token})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_signup_creates_trial_subscription() -> None:
    client = TestClient(create_app())
    client.post("/auth/request", json={"email": "trial@example.com"})

    session_maker = get_sessionmaker()
    async with session_maker() as session:
        user = (
            await session.execute(select(User).where(User.email == "trial@example.com"))
        ).scalar_one()
        assert user.trial_ends_at is not None
        subscription = (
            await session.execute(select(Subscription).where(Subscription.user_id == user.id))
        ).scalar_one()
        assert subscription.plan == "trial"
        assert subscription.status == "active"


@pytest.mark.asyncio
async def test_expired_token_rejected() -> None:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": "expired@example.com"}).json()["dev_token"]

    from datetime import UTC, datetime

    session_maker = get_sessionmaker()
    async with session_maker() as session:
        row = (
            await session.execute(
                select(MagicLinkToken).order_by(MagicLinkToken.id.desc()).limit(1)
            )
        ).scalar_one()
        row.expires_at = datetime.now(UTC).replace(year=2000)
        await session.commit()

    with pytest.raises(InvalidTokenError):
        async with session_maker() as session:
            await verify_magic_link(session, token)


def test_verify_rejects_garbage() -> None:
    client = TestClient(create_app())
    response = client.post("/auth/verify", json={"token": "not-a-real-token"})
    assert response.status_code == 400
