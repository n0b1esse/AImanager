"""Structured JSON logging with an allow-list of extra fields.

Extra fields not on ALLOW_EXTRA are dropped. Extra fields whose key looks
secret (token/secret/password/key/authorization) are replaced with [redacted].
Never pass raw request bodies or secrets into logging calls.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

SECRET_KEY_RE = re.compile(r"(token|secret|password|api[-_]?key|authorization|session[-_]?secret)", re.I)

ALLOW_EXTRA = frozenset(
    {
        "user_id",
        "shop_id",
        "review_id",
        "task_id",
        "action",
        "status",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "request_id",
        "actor_id",
        "target",
        "external_id",
    }
)

_RESERVED = frozenset(logging.LogRecord("n", 0, "p", 1, "msg", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in line or key == "message":
                continue
            if SECRET_KEY_RE.search(key):
                line[key] = "[redacted]"
            elif key in ALLOW_EXTRA:
                line[key] = value
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


def configure_logging(level: int | str = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
