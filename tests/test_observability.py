import io
import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from wb_web.admin.audit import record_admin_action, sanitize
from wb_web.core.logging import JsonFormatter
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import AdminAuditLog
from wb_web.main import create_app


def test_json_formatter_allow_list_and_redaction() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.json.formatter")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("checkout started", extra={"user_id": 7, "wb_token": "abc123", "random_field": "x"})

    payload = json.loads(stream.getvalue().strip())
    assert payload["msg"] == "checkout started"
    assert payload["user_id"] == 7
    assert payload["wb_token"] == "[redacted]"
    assert "random_field" not in payload


def test_sanitize_nested_secrets() -> None:
    raw = {
        "shop_name": "ok",
        "wb_token": "secret",
        "nested": {"api_key": "k", "safe": "v"},
        "items": [{"password": "p", "fine": 1}],
    }
    clean = sanitize(raw)
    assert clean["shop_name"] == "ok"
    assert clean["wb_token"] == "[redacted]"
    assert clean["nested"] == {"api_key": "[redacted]", "safe": "v"}
    assert clean["items"] == [{"password": "[redacted]", "fine": 1}]


def test_readyz_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_request_id_header_present() -> None:
    client = TestClient(create_app())
    response = client.get("/readyz")
    assert response.headers.get("X-Request-ID")


@pytest.mark.asyncio
async def test_record_admin_action_persists_redacted() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        entry = await record_admin_action(
            session,
            actor_id=1,
            action="subscription.activate",
            target="user:42",
            detail={"plan": "pro", "wb_token": "raw-secret-value"},
            ip="127.0.0.1",
        )
        assert entry.id is not None

    async with sessionmaker() as session:
        stored = (
            await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.id == entry.id)
            )
        ).scalar_one()
        assert stored.actor_admin_id == 1
        assert stored.detail["plan"] == "pro"
        assert stored.detail["wb_token"] == "[redacted]"
