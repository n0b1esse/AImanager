"""Security primitives: secret encryption (Fernet) and token hashing.

Rules (mirrored from desktop SECURITY.md):
- WB tokens and AI provider keys are stored ONLY as Fernet ciphertext.
- Plaintext never appears in logs, exceptions, exports, or API responses.
- Magic-link tokens are stored as SHA-256 hashes, never as raw values.
"""

from __future__ import annotations

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from wb_web.core.config import get_settings


class SecretEncryptor:
    def __init__(self, key: str) -> None:
        if not key:
            raise RuntimeError(
                "FERNET_KEY is not set. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self._fernet = Fernet(key.encode())

    @classmethod
    def from_settings(cls) -> SecretEncryptor:
        return cls(get_settings().fernet_key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:  # do not leak ciphertext in the message
            raise ValueError("Failed to decrypt stored secret (key rotated or data corrupt?)") from exc


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def mask_secret(value: str | None, visible: int = 4) -> str:
    """Masked representation for UI/admin: '••••' + last visible chars. Never for logs."""
    if not value:
        return "••••"
    return "••••" + value[-visible:]
