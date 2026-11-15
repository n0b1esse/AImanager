from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Shop
from wb_web.main import create_app


def _login(email: str) -> TestClient:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": email}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    return client


def test_create_shop_encrypts_token_and_masks_it() -> None:
    client = _login("owner@example.com")
    raw = "super-secret-wb-token-1234567890"

    response = client.post(
        "/shops",
        json={"name": "Мой магазин", "wb_token": raw, "niche_ids": ["base"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["wb_token_masked"].startswith("••••")
    assert raw not in response.text

    import asyncio

    async def _check() -> None:
        session_maker = get_sessionmaker()
        async with session_maker() as session:
            shop = (
                await session.execute(select(Shop).where(Shop.name == "Мой магазин"))
            ).scalar_one()
            assert shop.wb_token_encrypted != raw

        from wb_web.core.security import SecretEncryptor

        assert SecretEncryptor.from_settings().decrypt(shop.wb_token_encrypted) == raw

    asyncio.run(_check())


def test_tenant_isolation_between_users() -> None:
    alice = _login("alice@example.com")
    bob = _login("bob@example.com")

    shop_id = alice.post(
        "/shops", json={"name": "Alice shop", "wb_token": "alice-token-abcdef123456"}
    ).json()["id"]

    listing = bob.get("/shops").json()["shops"]
    assert listing == []

    assert bob.get(f"/shops/{shop_id}").status_code == 404
    assert alice.get(f"/shops/{shop_id}").status_code == 200


def test_shops_require_auth() -> None:
    client = TestClient(create_app())
    assert client.get("/shops").status_code == 401
    assert (
        client.post("/shops", json={"name": "x", "wb_token": "12345678"}).status_code == 401
    )


def test_unknown_niche_rejected() -> None:
    client = _login("niches@example.com")
    response = client.post(
        "/shops",
        json={"name": "n", "wb_token": "niche-token-abcdef12", "niche_ids": ["nonexistent"]},
    )
    assert response.status_code == 422
    assert "nonexistent" in response.text


def test_me_and_logout_flow() -> None:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": "me@example.com"}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})

    me = client.get("/auth/me").json()
    assert me["email"] == "me@example.com"
    assert me["subscription"]["plan"] == "trial"
    assert me["trial_ends_at"] is not None

    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401
