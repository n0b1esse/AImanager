"""Worker skeleton: poll approved reviews, publish idempotently.

Real WB/AI clients are wired in the WB-integration step; this is the mechanism
(claim → lease → publish protocol) with structured logging. Run:

    python -m wb_web.worker [--interval 5]
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from wb_web.db.base import get_sessionmaker
from wb_web.queue import WBReviewsClient, claim_next_task, publish_review

logger = logging.getLogger("wb_web.worker")


async def run_once(wb: WBReviewsClient | None = None) -> str | None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        review = await claim_next_task(session, lease_seconds=120)
        if review is None:
            return None
        if wb is None:
            logger.info("claimed review without WB client; skipping publish", extra={"review_id": review.id})
            return "claimed_no_client"
        result = await publish_review(session, review, wb)
        logger.info(
            "publish attempt finished",
            extra={"review_id": review.id, "action": result.outcome},
        )
        return result.outcome


async def main_loop(interval: float) -> None:
    logger.info("worker started", extra={"action": "worker_start"})
    while True:
        try:
            await run_once(None)
        except Exception:  # noqa: BLE001 — worker must survive bad iterations
            logger.exception("worker iteration failed")
        await asyncio.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="AImanager background worker")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    try:
        asyncio.run(main_loop(args.interval))
    except KeyboardInterrupt:
        logger.info("worker stopped", extra={"action": "worker_stop"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
