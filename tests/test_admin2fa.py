import asyncio

import pyotp
from admin_helpers import admin_code, set_2fa
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.core.security import SecretEncryptor
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import AdminUser
from wb_web.main import create_app


def _login(email: str) -> TestClient:
    client = TestClient(create_app(), follow_redirects=False)
    token = client.post("/auth/request", json={"email": email}).json()["dev_token"]
    client.post("/auth/verify", json={"token": token})
    return client


def test_fresh_owner_redirects_to_2fa_setup() -> None:
    client = _login("setup-owner@example.com")  # not owner email -> plain user
    response = client.get("/app/admin")
    assert response.status_code == 303
    assert "/app" in response.headers["location"]  # no rights at all


def test_owner_setup_flow_enables_2fa() -> None:
    # First-time setup for the configured owner email
    client = _login("owner@example.com")
    response = client.get("/app/admin")
    assert response.status_code == 303
    assert response.headers["location"] == "/app/admin/setup"

    page = client.get("/app/admin/setup")
    assert page.status_code == 200
    assert "admin_setup" in page.text or "QR" in page.text or "Код" in page.text

    # Wrong code → back with error
    bad = client.post("/app/admin/setup", data={"code": "000000"})
    assert bad.status_code == 303

    # Read the pending secret from the DB (as the authenticator app would see it)
    async def _secret() -> str:
        sm = get_sessionmaker()
        async with sm() as session:
            row = (
                await session.execute(select(AdminUser).where(AdminUser.email == "owner@example.com"))
            ).scalar_one()
            return SecretEncryptor.from_settings().decrypt(row.totp_secret_encrypted)

    secret = asyncio.run(_secret())
    good = client.post("/app/admin/setup", data={"code": pyotp.TOTP(secret).now()})
    assert good.status_code == 303
    assert good.headers["location"].startswith("/app/admin")

    # 2FA session cookie set: admin reachable
    assert client.get("/app/admin").status_code == 200


def test_json_api_requires_2fa_after_enable() -> None:
    client = _login("owner@example.com")
    asyncio.run(set_2fa("owner@example.com"))

    # No 2FA cookie yet -> 403 2fa_required
    response = client.get("/admin/summary")
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "2fa_required"

    # Verify with the app code -> cookie issued
    client.post("/app/admin/2fa", data={"code": admin_code()})
    assert client.get("/admin/summary").status_code == 200


def test_support_role_is_read_only() -> None:
    client = _login("support@example.com")
    asyncio.run(set_2fa("support@example.com", role="support"))
    client.post("/app/admin/2fa", data={"code": admin_code()})

    # Can view
    page = client.get("/app/admin")
    assert page.status_code == 200
    assert "read-only" in page.text

    # Cannot activate
    response = client.post("/app/admin/users/1/activate", data={"plan": "pro", "months": "1"})
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]

    # JSON API for support is closed entirely (owner-only)
    assert client.get("/admin/summary").status_code == 403


def test_owner_rejected_code_and_garbage() -> None:
    client = _login("owner@example.com")
    asyncio.run(set_2fa("owner@example.com"))
    response = client.post("/app/admin/2fa", data={"code": "abcdef"})
    assert response.status_code == 303
    assert "err" in response.headers["location"]
    assert client.get("/admin/summary").status_code == 403
