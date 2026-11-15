
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from wb_web.core.events import track
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import ProductEvent, User
from wb_web.main import create_app
from wb_web.stats import admin_sales, client_stats


def _login(email: str) -> TestClient:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": email}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    return client


@pytest.mark.asyncio
async def test_signup_and_login_events_tracked() -> None:
    _login("events@example.com")
    sm = get_sessionmaker()
    async with sm() as session:
        rows = (
            await session.execute(
                select(ProductEvent.event, func.count(ProductEvent.id)).group_by(ProductEvent.event)
            )
        ).all()
    counts = dict(rows)
    assert counts.get("user.signup") == 1
    assert counts.get("user.login") == 1


@pytest.mark.asyncio
async def test_client_stats_and_admin_sales() -> None:
    _login("stats-customer@example.com")

    sm = get_sessionmaker()
    async with sm() as session:
        customer_id = (
            await session.execute(select(User.id).where(User.email == "stats-customer@example.com"))
        ).scalar_one()

    # Seed a month of activity
    async with sm() as session:
        for event in ["review.generated"] * 8 + ["review.approved"] * 6 + ["review.published"] * 4:
            await track(session, event=event, user_id=customer_id, shop_id=1)

    async with sm() as session:
        stats = await client_stats(session, customer_id)
        assert stats["generated_30d"] == 8
        assert stats["approved_30d"] == 6
        assert stats["published_30d"] == 4
        assert stats["hours_saved_30d"] == round(4 * 5 / 60, 1)

    # Convert the customer to paid
    async with sm() as session:
        from wb_web.billing.activate import activate_subscription

        await activate_subscription(session, actor_id=1, target_user_id=customer_id, plan="pro", months=1)

    async with sm() as session:
        sales = await admin_sales(session)
        assert sales["paid_users"] >= 1
        assert sales["activations_30d"] == 1


def test_stats_page_renders() -> None:
    client = _login("stats-page@example.com")
    response = client.get("/app/stats")
    assert response.status_code == 200
    assert "Статистика" in response.text
