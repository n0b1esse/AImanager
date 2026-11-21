"""TOTP helpers for admin 2FA (secrets Fernet-encrypted at rest)."""

from __future__ import annotations

import pyotp

from wb_web.core.security import SecretEncryptor

ISSUER = "Avto-Otvetchik"


def new_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return SecretEncryptor.from_settings().encrypt(secret)


def decrypt_secret(encrypted: str) -> str:
    return SecretEncryptor.from_settings().decrypt(encrypted)


def otpauth_uri(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def current_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def verify(secret: str, code: str) -> bool:
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return False
    return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
