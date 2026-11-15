import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.billing.provider import ManualProvider, get_provider
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import AdminAuditLog
from wb_web.main import create_app


def _login(email: str) -> TestClient:
    client = TestClient(create_app())
    token = client.post("/auth/request", json={"email": email}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    return client


def test_admin_requires_owner_and_auth() -> None:
    anon = TestClient(create_app())
    assert anon.get("/admin/summary").status_code == 401

    plain = _login("not-owner@example.com")
    assert plain.get("/admin/summary").status_code == 403


def test_owner_summary() -> None:
    owner = _login("owner@example.com")
    client = _login("customer@example.com")
    client.post("/shops", json={"name": "c", "wb_token": "customer-token-1234"})

    response = owner.get("/admin/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["users_total"] >= 2
    assert body["shops_total"] >= 1
    assert "trial" in body["plans"]


def test_manual_activation_flow_ungates_user() -> None:
    owner = _login("owner@example.com")
    customer = _login("payme@example.com")

    # Expire the trial so paid actions are blocked
    import asyncio
    from datetime import UTC, datetime

    async def _expire() -> None:
        sm = get_sessionmaker()
        from wb_web.db.models import Subscription, User

        async with sm() as session:
            uid = (
                await session.execute(select(User.id).where(User.email == "payme@example.com"))
            ).scalar_one()
            user = (await session.execute(select(User).where(User.id == uid))).scalar_one()
            user.trial_ends_at = datetime.now(UTC).replace(year=2000)
            sub = (
                await session.execute(select(Subscription).where(Subscription.user_id == uid))
            ).scalar_one()
            sub.current_period_end = datetime.now(UTC).replace(year=2000)
            await session.commit()

    asyncio.run(_expire())

    blocked = customer.post("/shops", json={"name": "x", "wb_token": "blocked-token-12345"})
    assert blocked.status_code == 402

    payme_id = _user_id("payme@example.com")
    response = owner.post(
        f"/admin/users/{payme_id}/activate",
        json={"plan": "pro", "months": 1, "note": "оплата наличным переводом"},
    )
    assert response.status_code == 200
    assert response.json()["plan"] == "pro"

    allowed = customer.post("/shops", json={"name": "x", "wb_token": "allowed-token-12345"})
    assert allowed.status_code == 201

    me = customer.get("/auth/me").json()
    assert me["subscription"]["plan"] == "pro"


def _user_id(email: str) -> int:
    import asyncio

    async def _get() -> int:
        sm = get_sessionmaker()
        from wb_web.db.models import User

        async with sm() as session:
            return (
                await session.execute(select(User.id).where(User.email == email))
            ).scalar_one()

    return asyncio.run(_get())


def test_activation_is_audited() -> None:
    owner = _login("owner@example.com")
    _login("audited@example.com")
    user_id = _user_id("audited@example.com")

    response = owner.post(
        f"/admin/users/{user_id}/activate", json={"plan": "business", "months": 2}
    )
    assert response.status_code == 200

    async def _check_audit() -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                await session.execute(
                    select(AdminAuditLog).where(AdminAuditLog.target == f"user:{user_id}")
                )
            ).scalars().all()
            assert any(r.action == "subscription.activate" for r in rows)
            entry = next(r for r in rows if r.action == "subscription.activate")
            assert entry.detail["plan"] == "business"
            assert entry.detail["months"] == 2

    import asyncio

    asyncio.run(_check_audit())


def test_kill_switch_toggles_and_audits() -> None:
    owner = _login("owner@example.com")
    customer = _login("kill@example.com")
    shop_id = customer.post(
        "/shops", json={"name": "ks", "wb_token": "kill-switch-token-12"}
    ).json()["id"]

    assert owner.post(f"/admin/shops/{shop_id}/kill-switch", json={"paused": True}).json()["automation_paused"] is True
    assert owner.post(f"/admin/shops/{shop_id}/kill-switch", json={"paused": False}).json()["automation_paused"] is False


def test_owner_cannot_deactivate_owner_account() -> None:
    owner = _login("owner@example.com")
    owner_id = _user_id("owner@example.com")
    response = owner.post(f"/admin/users/{owner_id}/active", json={"active": False})
    assert response.status_code == 400


def test_deactivate_and_reactivate_user() -> None:
    owner = _login("owner@example.com")
    target_client = _login("toggle@example.com")
    target_id = _user_id("toggle@example.com")

    assert owner.post(f"/admin/users/{target_id}/active", json={"active": False}).json()["is_active"] is False
    # Session of the deactivated user is dead even with valid signature
    assert target_client.get("/auth/me").status_code == 401
    assert owner.post(f"/admin/users/{target_id}/active", json={"active": True}).json()["is_active"] is True


def test_manual_provider_checkout_instructions() -> None:
    import asyncio

    result = asyncio.run(
        ManualProvider().create_checkout(user_id=1, plan="pro", months=3)
    )
    assert "pro" in (result.instructions or "")
    assert "3" in (result.instructions or "")
    assert get_provider("manual").name == "manual"
    with pytest.raises(KeyError):
        get_provider("mbank-unconfigured")
