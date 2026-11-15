from wb_web.core.session import sign_session, verify_session

SECRET = "test-secret-for-session"


def test_roundtrip() -> None:
    token = sign_session({"uid": 42}, SECRET, ttl_seconds=3600)
    payload = verify_session(token, SECRET)
    assert payload is not None
    assert payload["uid"] == 42


def test_tampered_signature_rejected() -> None:
    token = sign_session({"uid": 42}, SECRET, ttl_seconds=3600)
    body, _ = token.split(".", 1)
    assert verify_session(f"{body}.forged-signature", SECRET) is None


def test_wrong_secret_rejected() -> None:
    token = sign_session({"uid": 42}, SECRET, ttl_seconds=3600)
    assert verify_session(token, "other-secret") is None


def test_expired_rejected() -> None:
    token = sign_session({"uid": 42}, SECRET, ttl_seconds=-1)
    assert verify_session(token, SECRET) is None


def test_garbage_rejected() -> None:
    assert verify_session("not-a-token", SECRET) is None
    assert verify_session("", SECRET) is None


def test_uid_not_coerced_from_string() -> None:
    token = sign_session({"uid": "42"}, SECRET, ttl_seconds=3600)
    payload = verify_session(token, SECRET)
    assert payload is not None  # payload parses; type check happens in get_optional_user
    assert isinstance(payload["uid"], str)
