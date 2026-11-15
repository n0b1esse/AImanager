import pytest

from wb_web.core.security import SecretEncryptor, hash_token, mask_secret


def test_encrypt_decrypt_roundtrip() -> None:
    key = SecretEncryptor.from_settings()
    ciphertext = key.encrypt("wb-token-12345")
    assert ciphertext != "wb-token-12345"
    assert key.decrypt(ciphertext) == "wb-token-12345"


def test_decrypt_with_wrong_key_fails() -> None:
    from cryptography.fernet import Fernet

    good = SecretEncryptor.from_settings()
    bad = SecretEncryptor(Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        bad.decrypt(good.encrypt("secret"))


def test_missing_key_raises() -> None:
    with pytest.raises(RuntimeError):
        SecretEncryptor("")


def test_hash_token_stable_and_distinct() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")


def test_mask_secret() -> None:
    assert mask_secret(None) == "••••"
    assert mask_secret("abcdefgh") == "••••efgh"
