"""Adapter: OzonClient -> queue.publish protocol (same shape as WBReviewsAdapter)."""

from __future__ import annotations

from typing import Any

from wb_web.db.models import Shop
from wb_web.ozon.client import OzonClient, OzonClientError, OzonTransientError
from wb_web.queue.publish import WBClientError, WBTransientError


class OzonReviewsAdapter:
    def __init__(self, client: OzonClient) -> None:
        self._client = client

    @classmethod
    def for_shop(cls, shop: Shop) -> OzonReviewsAdapter:
        return cls(OzonClient.from_shop(shop))

    async def get_review(self, review_id: str) -> dict[str, Any]:
        try:
            comments = await self._client.list_comments(review_id)
        except OzonTransientError as exc:
            raise WBTransientError(str(exc)) from exc
        except OzonClientError as exc:
            raise WBClientError(str(exc)) from exc
        own = next((c for c in comments if c.get("is_owner")), None)
        return {
            "id": review_id,
            "answer": None if own is None else {"text": own.get("text", ""), "state": "published"},
        }

    async def post_answer(self, review_id: str, text: str) -> None:
        try:
            await self._client.create_comment(review_id, text, mark_processed=True)
        except OzonTransientError as exc:
            raise WBTransientError(str(exc)) from exc
        except OzonClientError as exc:
            raise WBClientError(str(exc)) from exc
