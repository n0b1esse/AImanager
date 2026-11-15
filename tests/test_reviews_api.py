import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.db.base import get_sessionmaker
from wb_web.db.models import Review, Shop
from wb_web.main import create_app


def _login(email: str) -> TestClient:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": email}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    return client


def _make_review_orm(shop_name: str, wb_id: str = "wb-api-1", *, text="Всё отлично", rating=5, state="new"):
    async def _make() -> int:
        sm = get_sessionmaker()
        async with sm() as session:
            shop = (
                await session.execute(select(Shop).where(Shop.name == shop_name))
            ).scalar_one()
            review = Review(
                shop_id=shop.id, wb_review_id=wb_id, state=state, text=text, rating=rating,
            )
            session.add(review)
            await session.commit()
            return review.id

    return asyncio.run(_make())


def test_full_loop_list_generate_approve_publish(monkeypatch) -> None:
    client = _login("loop@example.com")
    shop_id = client.post(
        "/shops", json={"name": "loop-shop", "wb_token": "loop-token-1234567"}
    ).json()["id"]
    review_id = _make_review_orm("loop-shop")

    # BYOK config: ollama needs no key
    response = client.patch(f"/shops/{shop_id}/ai", json={"provider": "ollama", "model": "gemma3:4b"})
    assert response.status_code == 200
    assert response.json()["provider"] == "ollama"

    listed = client.get(f"/shops/{shop_id}/reviews").json()
    assert listed["count"] == 1
    assert listed["reviews"][0]["state"] == "new"

    async def fake_generate(session, shop, review):
        review.generated_text = "Спасибо за отзыв! Нам важно ваше мнение."
        review.ai_model = "gemma3:4b"
        review.ai_tokens_total = 12
        review.state = "generated"
        review.generated_at = datetime.now(UTC)
        await session.commit()
        from wb_web.ai.models import GenerationResult, TokenUsage

        return GenerationResult(text=review.generated_text, provider="ollama",
                                model="gemma3:4b", usage=TokenUsage(5, 7, 12))

    monkeypatch.setattr("wb_web.reviews.routes.generate_for_review", fake_generate)
    generated = client.post(f"/shops/{shop_id}/reviews/{review_id}/generate")
    assert generated.status_code == 200
    assert generated.json()["state"] == "generated"

    approved = client.post(f"/shops/{shop_id}/reviews/{review_id}/approve")
    assert approved.status_code == 200

    async def fake_publish(session, review, wb):
        from wb_web.queue import PublishResult

        review.state = "published"
        review.published_at = datetime.now(UTC)
        await session.commit()
        return PublishResult(outcome="published", review_id=review.id)

    monkeypatch.setattr("wb_web.reviews.routes.publish_review", fake_publish)

    class _DummyWBClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("wb_web.reviews.routes.build_client_for_shop", lambda shop: _DummyWBClient())
    published = client.post(f"/shops/{shop_id}/reviews/{review_id}/publish")
    assert published.status_code == 200
    assert published.json()["outcome"] == "published"

    # Review is gone from 'new'/'generated' listing, present as published
    final = client.get(f"/shops/{shop_id}/reviews?state=published").json()
    assert final["count"] == 1


def test_publish_before_approve_rejected() -> None:
    client = _login("early@example.com")
    shop_id = client.post(
        "/shops", json={"name": "early-shop", "wb_token": "early-token-123456"}
    ).json()["id"]
    review_id = _make_review_orm("early-shop", wb_id="wb-early-1")
    response = client.post(f"/shops/{shop_id}/reviews/{review_id}/publish")
    assert response.status_code == 409


def test_reviews_tenant_isolation() -> None:
    alice = _login("alice-r@example.com")
    bob = _login("bob-r@example.com")
    shop_id = alice.post(
        "/shops", json={"name": "alice-shop", "wb_token": "alice-r-token-1234"}
    ).json()["id"]
    review_id = _make_review_orm("alice-shop", wb_id="wb-alice-1")

    assert bob.get(f"/shops/{shop_id}/reviews").status_code == 404
    assert bob.post(f"/shops/{shop_id}/reviews/{review_id}/skip").status_code == 404


def test_skip_review() -> None:
    client = _login("skip@example.com")
    shop_id = client.post(
        "/shops", json={"name": "skip-shop", "wb_token": "skip-token-1234567"}
    ).json()["id"]
    review_id = _make_review_orm("skip-shop", wb_id="wb-skip-1")
    response = client.post(f"/shops/{shop_id}/reviews/{review_id}/skip")
    assert response.json()["state"] == "skipped"


def test_ai_config_write_only_key_and_masked() -> None:
    client = _login("ai-config@example.com")
    shop_id = client.post(
        "/shops", json={"name": "ai-shop", "wb_token": "ai-token-123456789"}
    ).json()["id"]

    response = client.patch(
        f"/shops/{shop_id}/ai",
        json={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "api_key": "sk-secret-1234567890abcdef",
            "instructions": "наш бренд «ТестПром», отвечаем на вы",
            "temperature": 0.4,
            "daily_limit": 50,
        },
    )
    body = response.json()
    assert "sk-secret" not in response.text  # raw key never echoed
    assert body["api_key_configured"] is True
    assert body["api_key_masked"].endswith("cdef")
    assert body["temperature"] == 0.4
    assert body["daily_limit"] == 50

    fetched = client.get(f"/shops/{shop_id}/ai").json()
    assert fetched["api_key_masked"].endswith("cdef")
    assert "sk-secret" not in client.get(f"/shops/{shop_id}/ai").text


def test_clear_api_key() -> None:
    client = _login("ai-clear@example.com")
    shop_id = client.post(
        "/shops", json={"name": "ai-clear-shop", "wb_token": "ai-clear-token-1234"}
    ).json()["id"]
    client.patch(f"/shops/{shop_id}/ai", json={"provider": "openai", "api_key": "sk-temp-1234567890"})
    cleared = client.patch(f"/shops/{shop_id}/ai", json={"api_key": ""}).json()
    assert cleared["api_key_configured"] is False
    assert cleared["api_key_masked"] is None
