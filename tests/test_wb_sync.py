from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Review, Shop, User
from wb_web.main import create_app
from wb_web.wb.client import WildberriesClient
from wb_web.wb.sync import sync_shop_reviews


def _page_payload(feedbacks):
    return {
        "error": False,
        "data": {"feedbacks": feedbacks, "countUnanswered": len(feedbacks), "countArchive": 0},
    }


def _fb(fid: str, *, rating=5, text="супер") -> dict:
    return {
        "id": fid,
        "text": text,
        "pros": "хорошо",
        "cons": "",
        "productValuation": rating,
        "createdDate": "2026-11-10T10:00:00Z",
        "productDetails": {"nmId": 42, "productName": "Кроссовки", "supplierArticle": "SKU-1"},
    }


def _list_client(feedbacks):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "isAnswered=false" in str(request.url)
        return httpx.Response(200, json=_page_payload(feedbacks))

    transport = httpx.MockTransport(handler)
    return WildberriesClient("test-token", http_client=httpx.AsyncClient(transport=transport))


async def _make_shop() -> tuple[User, Shop]:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        user = User(email="sync@example.com", trial_ends_at=datetime.now(UTC) + timedelta(days=7))
        session.add(user)
        await session.flush()
        shop = Shop(owner_id=user.id, name="sync-shop", wb_token_encrypted="enc", mode="manual")
        session.add(shop)
        await session.commit()
        return user, shop


@pytest.mark.asyncio
async def test_sync_creates_reviews_then_updates_without_duplicates() -> None:
    _, shop = await _make_shop()
    client = _list_client([_fb("fb1"), _fb("fb2", rating=2, text="плохо")])
    session_maker = get_sessionmaker()

    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.name == "sync-shop"))).scalar_one()
        first = await sync_shop_reviews(session, shop, client=client)
    assert (first.fetched, first.created, first.updated) == (2, 2, 0)

    # Second sync: no duplicates; text refreshed
    client2 = _list_client([_fb("fb1", text="исправленный текст"), _fb("fb2", rating=2, text="плохо")])
    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.name == "sync-shop"))).scalar_one()
        second = await sync_shop_reviews(session, shop, client=client2)
    assert (second.fetched, second.created, second.updated) == (2, 0, 2)

    async with session_maker() as session:
        rows = (
            await session.execute(select(Review).order_by(Review.wb_review_id))
        ).scalars().all()
        assert [r.state for r in rows] == ["new", "new"]
        assert rows[0].review_meta["nm_id"] == 42
        assert rows[0].text == "исправленный текст"


@pytest.mark.asyncio
async def test_sync_never_resurrects_workflow_state() -> None:
    _, shop = await _make_shop()
    client = _list_client([_fb("fb1")])
    session_maker = get_sessionmaker()

    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.name == "sync-shop"))).scalar_one()
        await sync_shop_reviews(session, shop, client=client)

    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.wb_review_id == "fb1"))
        ).scalar_one()
        review.state = "published"
        await session.commit()

    client2 = _list_client([_fb("fb1", text="новый текст")])
    async with session_maker() as session:
        shop = (await session.execute(select(Shop).where(Shop.name == "sync-shop"))).scalar_one()
        await sync_shop_reviews(session, shop, client=client2)

    async with session_maker() as session:
        review = (
            await session.execute(select(Review).where(Review.wb_review_id == "fb1"))
        ).scalar_one()
        assert review.state == "published"
        assert review.text == "новый текст"


def test_sync_endpoint_gated_and_mapped(monkeypatch) -> None:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": "wb@example.com"}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    shop_id = client.post(
        "/shops", json={"name": "wb", "wb_token": "wb-endpoint-token-123"}
    ).json()["id"]


    class FakeResult:
        fetched, created, updated = 7, 3, 4

    async def fake_sync(session, shop):
        return FakeResult()

    async def fake_verify(shop):
        return {"seller_name": "ИП Тестов", "display_name": "ИП Тестов", "feedback_access": True}

    monkeypatch.setattr("wb_web.shops.routes.sync_shop_reviews", fake_sync)
    monkeypatch.setattr("wb_web.shops.routes.verify_shop_connection", fake_verify)

    assert client.post(f"/shops/{shop_id}/sync").json() == {"fetched": 7, "created": 3, "updated": 4}
    assert client.post(f"/shops/{shop_id}/verify").json()["seller_name"] == "ИП Тестов"


def test_sync_endpoint_requires_active_subscription() -> None:
    import asyncio

    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": "expired-wb@example.com"}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    shop_id = client.post(
        "/shops", json={"name": "wb2", "wb_token": "wb-expired-token-1234"}
    ).json()["id"]

    async def _expire() -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            uid = (
                await session.execute(
                    select(User.id).where(User.email == "expired-wb@example.com")
                )
            ).scalar_one()
            user = (await session.execute(select(User).where(User.id == uid))).scalar_one()
            user.trial_ends_at = datetime.now(UTC).replace(year=2000)
            await session.commit()

    asyncio.run(_expire())
    assert client.post(f"/shops/{shop_id}/sync").status_code == 402
    assert client.post(f"/shops/{shop_id}/verify").status_code == 402
