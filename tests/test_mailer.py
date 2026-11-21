import logging

import pytest
from fastapi.testclient import TestClient

from wb_web.core.config import get_settings
from wb_web.core.mailer import build_magic_link_email, send_email
from wb_web.main import create_app


@pytest.mark.asyncio
async def test_console_backend_logs_without_smtp() -> None:
    settings = get_settings()
    assert settings.smtp_host == ""  # test env: console fallback

    message = build_magic_link_email(to="user@example.com", link="https://x/auth/link?token=abc", ttl_minutes=15)
    captured = []

    handler = logging.Handler()
    handler.emit = captured.append  # type: ignore[method-assign]
    logging.getLogger("wb_web.mailer").addHandler(handler)
    try:
        backend = await send_email(message)
    finally:
        logging.getLogger("wb_web.mailer").removeHandler(handler)

    assert backend == "console"
    assert any("user@example.com" in r.getMessage() for r in captured)


@pytest.mark.asyncio
async def test_smtp_backend_calls_aiosmtplib(monkeypatch) -> None:
    sent = {}

    class FakeAioSMTP:  # namespace to monkeypatch attribute
        pass

    async def fake_send(message, **kwargs):
        sent["message"] = message
        sent["kwargs"] = kwargs

    monkeypatch.setattr("wb_web.core.mailer.aiosmtplib.send", fake_send)
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.kg")
    monkeypatch.setattr(get_settings(), "smtp_user", "bot")
    monkeypatch.setattr(get_settings(), "smtp_password", "pass")

    message = build_magic_link_email(to="kgd@example.com", link="https://x/auth/link?token=t123", ttl_minutes=15)
    backend = await send_email(message)

    assert backend == "smtp"
    assert sent["message"]["To"] == "kgd@example.com"
    assert sent["kwargs"]["hostname"] == "smtp.example.kg"
    assert sent["kwargs"]["username"] == "bot"
    assert sent["kwargs"]["start_tls"] is True
    assert "/auth/link?token=t123" in sent["message"].get_content()


def test_magic_link_uses_public_base_url_and_sends(monkeypatch) -> None:
    sent = {}

    async def fake_send_magic_link(*, to, link, ttl_minutes):
        sent.update(to=to, link=link, ttl=ttl_minutes)
        return "smtp"

    monkeypatch.setattr("wb_web.core.mailer.send_magic_link", fake_send_magic_link)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://avto-otvetchik.com")

    client = TestClient(create_app())
    response = client.post("/auth/request", json={"email": "abs@example.com"})
    assert response.status_code == 200
    assert sent["link"].startswith("https://avto-otvetchik.com/auth/link?token=")
    assert sent["to"] == "abs@example.com"


def test_production_response_hides_dev_token(monkeypatch) -> None:
    client = TestClient(create_app())
    monkeypatch.setattr("wb_web.auth.routes.get_settings", lambda: type(
        "S", (), {"app_env": "production"})())
    response = client.post("/auth/request", json={"email": "prod@example.com"})
    assert response.status_code == 200
    assert "dev_token" not in response.json()
