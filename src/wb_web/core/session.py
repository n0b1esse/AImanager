"""Stateless signed session cookies (base64url(json) + '.' + base64url(HMAC-SHA256)).

No server-side store: the cookie is the session. Revocation is by expiry
(default 14 days) or by rotating SESSION_SECRET (invalidates all sessions).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

SESSION_COOKIE = "aim_session"
SESSION_TTL_SECONDS = 14 * 24 * 3600


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _signature(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return _b64url_encode(digest)


def sign_session(payload: dict[str, Any], secret: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    payload_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload_b64}.{_signature(payload_b64, secret)}"


def verify_session(token: str, secret: str) -> dict[str, Any] | None:
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _signature(payload_b64, secret)
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        body = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    exp = body.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    return body
