"""Atomic review-claim with lease (single statement; SKIP LOCKED on Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.core.security import generate_token
from wb_web.db.models import Review

CLAIMABLE_STATES = ("approved",)


async def claim_next_task(
    session: AsyncSession,
    *,
    lease_seconds: int = 120,
    states: tuple[str, ...] = CLAIMABLE_STATES,
) -> Review | None:
    """Claim the oldest reclaimable review. Returns the claimed row or None.

    Atomic: the subquery picks the row and this statement transitions it; two
    competing workers can never claim the same review. On Postgres the inner
    SELECT uses FOR UPDATE SKIP LOCKED.
    """
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=lease_seconds)
    token = generate_token(16)
    dialect = session.get_bind().dialect.name
    for_update = "FOR UPDATE SKIP LOCKED" if dialect == "postgresql" else ""

    state_list = ", ".join(f"'{s}'" for s in states)  # states are internal constants, not user input
    sql = text(
        f"""
        UPDATE reviews
        SET claimed_at = :now, claim_expires_at = :expires, claim_token = :token
        WHERE id = (
            SELECT id FROM reviews
            WHERE state IN ({state_list})
              AND (claimed_at IS NULL OR claim_expires_at < :now)
            ORDER BY id
            LIMIT 1
            {for_update}
        )
        AND state IN ({state_list})
        RETURNING id
        """
    )
    result = await session.execute(sql, {"now": now, "expires": expires, "token": token})
    row = result.first()
    if row is None:
        await session.commit()  # release the open transaction
        return None
    await session.commit()
    session.expire_all()
    claimed = await session.get(Review, row[0])
    return claimed
