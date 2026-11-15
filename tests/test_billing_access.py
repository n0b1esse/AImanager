from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.billing.access import GRACE_DAYS, compute_access
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Subscription, User
from wb_web.main import create_app


def _login(email: str) -> TestClient:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": email}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    return client


async def _update_subscription(email: str, **fields) -> None:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        uid = (
            await session.execute(select(User.id).where(User.email == email))
        ).scalar_one()
        sub = (
            await session.execute(select(Subscription).where(Subscription.user_id == uid))
        ).scalar_one()
        for key, value in fields.items():
            setattr(sub, key, value)
        if "trial_ends_at" in fields:
            user = (await session.execute(select(User).where(User.id == uid))).scalar_one()
            user.trial_ends_at = fields["trial_ends_at"]
        await session.commit()


def _expiry(days_from_now: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days_from_now)


@pytest.mark.asyncio
async def test_expired_trial_blocks_paid_actions_but_reads_open() -> None:
    client = _login("expired@example.com")
    await _update_subscription("expired@example.com", trial_ends_at=_expiry(-1))

    blocked = client.post("/shops", json={"name": "x", "wb_token": "12345678901"})
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["reason"] == "trial_expired"

    assert client.get("/shops").status_code == 200  # read access stays open
    assert client.get("/auth/me").status_code == 200
    assert client.get("/auth/me").json()["access"]["state"] == "read_only"


@pytest.mark.asyncio
async def test_paid_active_allows() -> None:
    client = _login("active@example.com")
    await _update_subscription(
        "active@example.com", plan="pro", status="active", current_period_end=_expiry(20)
    )
    allowed = client.post("/shops", json={"name": "paid", "wb_token": "paid-token-123456"})
    assert allowed.status_code == 201
    assert client.get("/auth/me").json()["access"]["state"] == "active"


@pytest.mark.asyncio
async def test_period_ended_enters_grace() -> None:
    client = _login("grace@example.com")
    await _update_subscription(
        "grace@example.com", plan="pro", status="active", current_period_end=_expiry(-3)
    )
    allowed = client.post("/shops", json={"name": "grace", "wb_token": "grace-token-123456"})
    assert allowed.status_code == 201
    assert client.get("/auth/me").json()["access"]["state"] == "grace"


@pytest.mark.asyncio
async def test_grace_older_than_window_blocks() -> None:
    client = _login("graceexpired@example.com")
    await _update_subscription(
        "graceexpired@example.com",
        plan="pro",
        status="active",
        current_period_end=_expiry(-(GRACE_DAYS + 1)),
    )
    blocked = client.post("/shops", json={"name": "g", "wb_token": "grace-old-token-123"})
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["reason"] == "period_expired"


@pytest.mark.asyncio
async def test_grace_status_expires_too() -> None:
    client = _login("gracestatus@example.com")
    await _update_subscription(
        "gracestatus@example.com",
        plan="pro",
        status="grace",
        current_period_end=_expiry(-(GRACE_DAYS + 1)),
    )
    blocked = client.post("/shops", json={"name": "g2", "wb_token": "grace-status-tok-12"})
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["reason"] == "grace_expired"


@pytest.mark.asyncio
async def test_cancelled_blocks() -> None:
    client = _login("cancelled@example.com")
    await _update_subscription(
        "cancelled@example.com", plan="business", status="cancelled", current_period_end=_expiry(30)
    )
    blocked = client.post("/shops", json={"name": "c", "wb_token": "cancelled-token-12345"})
    assert blocked.status_code == 402


def test_missing_subscription_unit() -> None:
    now = datetime.now(UTC)
    user = User(email="none@example.com", trial_ends_at=now + timedelta(days=7))
    ctx = compute_access(user, None, now=now)
    assert ctx.allowed is False
    assert ctx.reason == "no_subscription"


def test_active_paid_in_future_unit() -> None:
    now = datetime.now(UTC)
    user = User(email="ok@example.com", trial_ends_at=now + timedelta(days=7))
    sub = Subscription(plan="business", status="active", current_period_end=now + timedelta(days=30))
    ctx = compute_access(user, sub, now=now)
    assert ctx.allowed is True
    assert ctx.state == "active"
