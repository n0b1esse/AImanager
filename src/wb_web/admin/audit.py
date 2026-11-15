"""Admin audit log: every privileged action lands here.

`detail` is sanitized before write: secret-looking keys are replaced with
[redacted] (recursive, depth-limited). Never pass raw secrets as values.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.db.models import AdminAuditLog

_SECRET_KEY_RE = re.compile(r"(token|secret|password|api[-_]?key|authorization|session)", re.I)
_MAX_DEPTH = 4


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        return {
            str(k): ("[redacted]" if _SECRET_KEY_RE.search(str(k)) else sanitize(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(v, depth + 1) for v in value]
    return value


async def record_admin_action(
    session: AsyncSession,
    *,
    actor_id: int,
    action: str,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AdminAuditLog:
    entry = AdminAuditLog(
        actor_admin_id=actor_id,
        action=action[:64],
        target=(target[:200] if target else None),
        detail=sanitize(detail or {}),
        ip=(ip[:64] if ip else None),
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry
