"""Adapter: WildberriesClient -> queue.publish protocol.

Maps WB typed errors onto the queue's transient/client classes:
- Timeout/transport + 5xx + 429 -> WBTransientError (server outcome UNKNOWN;
  publication state is reconciled by the remote recheck, never by blind retry)
- definitive 4xx -> WBClientError (do not retry)
"""

from __future__ import annotations

from typing import Any

from wb_web.queue.publish import WBClientError, WBTransientError
from wb_web.wb.client import WildberriesClient
from wb_web.wb.errors import (
    WildberriesAuthenticationError,
    WildberriesBadRequestError,
    WildberriesConflictError,
    WildberriesNotFoundError,
    WildberriesPermissionError,
    WildberriesRateLimitError,
    WildberriesServerError,
    WildberriesTimeoutError,
    WildberriesTransportError,
    WildberriesValidationError,
)

_CLIENT_ERRORS = (
    WildberriesBadRequestError,
    WildberriesAuthenticationError,
    WildberriesConflictError,
    WildberriesNotFoundError,
    WildberriesPermissionError,
    WildberriesValidationError,
)
_TRANSIENT_ERRORS = (
    WildberriesTimeoutError,
    WildberriesTransportError,
    WildberriesServerError,
    WildberriesRateLimitError,
)


class WBReviewsAdapter:
    def __init__(self, client: WildberriesClient) -> None:
        self._client = client

    async def get_review(self, wb_review_id: str) -> dict[str, Any]:
        feedback = await self._client.get_feedback(wb_review_id)
        answer = feedback.answer
        return {
            "id": feedback.feedback_id,
            "answer": None
            if answer is None
            else {"text": answer.text, "state": answer.state, "editable": answer.editable},
        }

    async def post_answer(self, wb_review_id: str, text: str) -> None:
        try:
            await self._client.answer_feedback(wb_review_id, text)
        except _CLIENT_ERRORS as exc:
            raise WBClientError(str(exc)) from exc
        except _TRANSIENT_ERRORS as exc:
            raise WBTransientError(str(exc)) from exc
