from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Review, Shop, User
from wb_web.main import create_app
from wb_web.ozon.adapter import OzonReviewsAdapter
from wb_web.ozon.client import OzonClient, OzonTransientError
from wb_web.ozon.sync import sync_ozon_reviews, verify_ozon_connection


def _client(handler):
    transport = httpx.MockTransport(handler)
    return OzonClient("12345", "test-api-key-123456", http_client=httpx.AsyncClient(transport=transport), max_attempts=2)


def _review(rid, *, rating=4, text="хороший товар", status="NEW", comments=0, sku=148591503):
    return {
        "id": rid,
        "sku": sku,
        "text": text,
        "published_at": "2026-11-10T10:00:00.970Z",
        "rating": rating,
        "status": status,
        "comments_amount": comments,
        "photos_amount": 0,
        "videos_amount": 0,
        "order_status": "DELIVERED",
        "is_rating_participant": True,
    }


@pytest.mark.asyncio
async def test_list_reviews_parses_page() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Client-Id"] == "12345"
        assert req.headers["Api-Key"] == "test-api-key-123456"
        body = req.read()
        assert b"NEW" in body and b"limit" in body
        return httpx.Response(200, json={"reviews": [_review("r1"), _review("r2", rating=1)], "has_next": True, "last_id": "r2"})

    client = _client(handler)
    page = await client.list_reviews(status="NEW", limit=100)
    assert len(page.reviews) == 2
    assert page.reviews[0].sku == 148591503
    assert page.reviews[1].rating == 1
    assert page.has_next is True
    assert page.last_id == "r2"
    await client.aclose()


@pytest.mark.asyncio
async def test_list_reviews_429_retries_then_ok() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"message": "rate limit"})
        return httpx.Response(200, json={"reviews": [], "has_next": False, "last_id": ""})

    client = _client(handler)
    page = await client.list_reviews()
    assert calls["n"] == 2
    assert page.reviews == ()
    await client.aclose()


@pytest.mark.asyncio
async def test_create_comment_not_retried_on_failure() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"message": "boom"})

    client = _client(handler)
    with pytest.raises(OzonTransientError):
        await client.create_comment("r1", "Спасибо за отзыв!")
    assert calls["n"] == 1  # NO retry for mutations
    await client.aclose()


@pytest.mark.asyncio
async def test_adapter_recheck_detects_own_comment() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/review/comment/list":
            return httpx.Response(200, json={"comments": [{"id": "c1", "is_owner": True, "text": "уже отвечали"}]})
        raise AssertionError("unexpected call")

    adapter = OzonReviewsAdapter(_client(handler))
    answer = await adapter.get_review("r1")
    assert answer["answer"]["text"] == "уже отвечали"


@pytest.mark.asyncio
async def test_sync_upserts_new_reviews() -> None:
    client = _client(
        lambda req: httpx.Response(
            200,
            json={"reviews": [_review("oz1"), _review("oz2", rating=2, text="качество плохое")], "has_next": False, "last_id": "oz2"},
        )
    )
    sm = get_sessionmaker()
    async with sm() as session:
        user = User(email="ozon-sync@example.com", trial_ends_at=datetime.now(UTC) + timedelta(days=7))
        session.add(user)
        await session.flush()
        shop = Shop(
            owner_id=user.id, name="ozon-shop", marketplace="ozon",
            wb_token_encrypted="", ozon_client_id="12345", ozon_api_key_encrypted="enc",
        )
        session.add(shop)
        await session.commit()
        shop = (await session.execute(select(Shop).where(Shop.name == "ozon-shop"))).scalar_one()
        result = await sync_ozon_reviews(session, shop, client=client)
    assert (result.fetched, result.created, result.updated) == (2, 2, 0)

    async with sm() as session:
        rows = (await session.execute(select(Review).order_by(Review.wb_review_id))).scalars().all()
        assert [r.state for r in rows] == ["new", "new"]
        assert rows[0].review_meta["marketplace"] == "ozon"
        assert rows[0].review_meta["ozon_sku"] == 148591503
    await client.aclose()


@pytest.mark.asyncio
async def test_verify_connection_smoke() -> None:
    client = _client(
        lambda req: httpx.Response(200, json={"reviews": [_review("oz1")], "has_next": False, "last_id": ""})
    )
    shop = Shop(name="x", marketplace="ozon", wb_token_encrypted="", ozon_client_id="12345", ozon_api_key_encrypted="enc")
    result = await verify_ozon_connection(shop, client=client)
    assert result["feedback_access"] is True
    await client.aclose()


def test_shop_creation_ozon_via_api() -> None:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": "ozon-owner@example.com"}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})

    missing = client.post("/shops", json={"name": "oz", "marketplace": "ozon"})
    assert missing.status_code == 400

    created = client.post(
        "/shops",
        json={"name": "oz", "marketplace": "ozon", "ozon_client_id": "12345", "ozon_api_key": "ozon-key-1234567890"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["marketplace"] == "ozon"
    assert body["ozon_api_key_masked"].startswith("••••")
    assert "ozon-key" not in created.text


def test_shop_creation_wb_still_works() -> None:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": "wb-keep@example.com"}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    created = client.post(
        "/shops", json={"name": "wb", "wb_token": "wb-token-1234567890"}
    )
    assert created.status_code == 201
    assert created.json()["marketplace"] == "wb"
