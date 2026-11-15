"""Product analytics events (append-only). Never breaks the request path."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.db.models import ProductEvent

logger = logging.getLogger("wb_web.events")


async def track(
    session: AsyncSession,
    *,
    event: str,
    user_id: int | None = None,
    shop_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    try:
        session.add(
            ProductEvent(
                event=event[:64],
                user_id=user_id,
                shop_id=shop_id,
                event_meta=meta or {},
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001 — analytics must not break the app
        logger.warning("product event track failed", extra={"action": event})
