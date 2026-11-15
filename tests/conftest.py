"""Test fixtures: isolated SQLite DB per pytest session + env for secrets."""

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_TMP = Path(tempfile.mkdtemp(prefix="wbweb-test-"))

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP / 'test.db'}"
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
os.environ["SESSION_SECRET"] = "test-secret-key-material-32-bytes!!"

import pytest  # noqa: E402

from wb_web.db.base import get_engine  # noqa: E402
from wb_web.db.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
async def _schema_and_cleanup():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
