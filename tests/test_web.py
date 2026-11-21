import asyncio
import html
import re
from datetime import UTC, datetime

from admin_helpers import setup_admin
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.db.base import get_sessionmaker
from wb_web.db.models import AdminAuditLog, Review, Shop, Subscription, User
from wb_web.main import create_app


def _web_login(email: str) -> TestClient:
    client = TestClient(create_app(), follow_redirects=False)
    response = client.post("/login", data={"email": email})
    assert response.status_code == 200
    match = re.search(r'href="(/auth/link\?token=[^"]+)"', response.text)
    assert match, "dev magic link must appear in test/dev HTML"
    linked = client.get(match.group(1))
    assert linked.status_code == 303
    assert linked.headers["location"] == "/app"
    setup_admin(client, email)  # no-op for non-admin emails
    assert client.get("/app").status_code == 200
    return client


def _shop_id(name: str) -> int:
    async def _get() -> int:
        sm = get_sessionmaker()
        async with sm() as session:
            return (
                await session.execute(select(Shop.id).where(Shop.name == name))
            ).scalar_one()

    return asyncio.run(_get())


def _seed_review(shop_name: str, wb_id: str = "wb-web-1", *, state="new", text="Всё отлично", rating=5):
    async def _make() -> int:
        sm = get_sessionmaker()
        async with sm() as session:
            shop = (
                await session.execute(select(Shop).where(Shop.name == shop_name))
            ).scalar_one()
            review = Review(shop_id=shop.id, wb_review_id=wb_id, state=state, text=text, rating=rating)
            session.add(review)
            await session.commit()
            return review.id

    return asyncio.run(_make())


def test_login_page_renders() -> None:
    client = TestClient(create_app())
    response = client.get("/login")
    assert response.status_code == 200
    assert "Авто-ответчик" in response.text


def test_login_with_selected_plan_persists_it() -> None:
    client = TestClient(create_app(), follow_redirects=False)
    email = "plan-flow@example.com"

    page = client.get("/login?plan=pro")
    assert page.status_code == 200
    assert "Рост" in page.text
    assert 'name="plan" value="pro"' in page.text

    sent = client.post("/login", data={"email": email, "plan": "pro"})
    assert sent.status_code == 200
    match = re.search(r'href="(/auth/link\?token=[^"]+)"', sent.text)
    assert match and "plan=pro" in match.group(1), "selected plan must travel with the magic link"
    assert client.get(html.unescape(match.group(1))).status_code == 303

    async def _plan() -> str:
        sm = get_sessionmaker()
        async with sm() as session:
            user = (await session.execute(select(User).where(User.email == email))).scalar_one()
            sub = (await session.execute(select(Subscription).where(Subscription.user_id == user.id))).scalar_one()
            return sub.plan

    assert asyncio.run(_plan()) == "pro"


def test_root_redirects_to_login_or_app() -> None:
    client = TestClient(create_app(), follow_redirects=False)
    assert client.get("/").headers["location"] == "/login"


def test_app_requires_auth() -> None:
    client = TestClient(create_app(), follow_redirects=False)
    assert client.get("/app").headers["location"] == "/login"
    assert client.get("/app/reviews").headers["location"] == "/login"


def test_web_flow_shop_create_generate_approve(monkeypatch) -> None:
    client = _web_login("webflow@example.com")

    response = client.post(
        "/app/shops",
        data={"name": "webflow-shop", "wb_token": "webflow-token-123456", "niche_ids": ["base"]},
    )
    assert response.status_code == 303
    shop_id = _shop_id("webflow-shop")

    page = client.get("/app/shops")
    assert page.status_code == 200
    assert "webflow-shop" in page.text
    assert "webflow-token" not in page.text  # token never printed (masked)

    review_id = _seed_review("webflow-shop")
    page = client.get("/app/reviews")
    assert "Всё отлично" in page.text

    async def fake_generate(session, shop, review):
        review.generated_text = "Спасибо за отзыв! Будем лучше."
        review.state = "generated"
        review.generated_at = datetime.now(UTC)
        await session.commit()

    monkeypatch.setattr("wb_web.web.routes.generate_for_review", fake_generate)
    assert client.post(f"/app/shops/{shop_id}/reviews/{review_id}/generate").status_code == 303

    page = client.get("/app/reviews?state=generated")
    assert "Будем лучше" in page.text

    # Edit the reply text, then approve it
    assert (
        client.post(
            f"/app/shops/{shop_id}/reviews/{review_id}/edit",
            data={"text": "Отредактированный ответ. Спасибо за отзыв!"},
        ).status_code
        == 303
    )
    page = client.get("/app/reviews?state=generated")
    assert "Отредактированный ответ" in page.text

    assert client.post(f"/app/shops/{shop_id}/reviews/{review_id}/approve").status_code == 303
    page = client.get("/app/reviews?state=approved")
    assert "Одобрено" in page.text or "одобрено" in page.text


def test_paid_action_redirects_to_billing_when_blocked() -> None:
    client = _web_login("paywall@example.com")
    response = client.post(
        "/app/shops", data={"name": "paywall-shop", "wb_token": "paywall-token-123456"}
    )
    assert response.status_code == 303

    async def _expire() -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            uid = (
                await session.execute(select(User.id).where(User.email == "paywall@example.com"))
            ).scalar_one()
            user = (await session.execute(select(User).where(User.id == uid))).scalar_one()
            user.trial_ends_at = datetime.now(UTC).replace(year=2000)
            await session.commit()

    asyncio.run(_expire())

    # Reads stay open
    page = client.get("/app/reviews")
    assert page.status_code == 200
    assert "Пробный период" in page.text

    # Mutations redirect to billing
    response = client.post(
        "/app/shops", data={"name": "paywall-2", "wb_token": "paywall-token-654321"}
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/billing")


def test_admin_page_for_owner_and_forbidden_for_others() -> None:
    owner = _web_login("owner@example.com")  # conftest: OWNER_EMAILS=owner@example.com
    page = owner.get("/app/admin")
    assert page.status_code == 200
    assert "Пользователи" in page.text

    plain = _web_login("plain-admin@example.com")
    response = plain.get("/app/admin")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/app")


def test_owner_activates_user_via_form() -> None:
    owner = _web_login("owner@example.com")
    _web_login("activate-me@example.com")

    user_id = asyncio.run(
        _uid_of("activate-me@example.com")
    )
    response = owner.post(f"/app/admin/users/{user_id}/activate", data={"plan": "pro", "months": "2"})
    assert response.status_code == 303

    async def _audit() -> bool:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                await session.execute(
                    select(AdminAuditLog).where(AdminAuditLog.target == f"user:{user_id}")
                )
            ).scalars().all()
            return any(r.action == "subscription.activate" for r in rows)

    assert asyncio.run(_audit()) is True


async def _uid_of(email: str) -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        return (
            await session.execute(select(User.id).where(User.email == email))
        ).scalar_one()


def test_static_css_served() -> None:
    client = TestClient(create_app())
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "--brand" in response.text
