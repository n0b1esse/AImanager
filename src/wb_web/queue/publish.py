"""Idempotent publication of review answers.

Protocol (mirrored from the desktop rules, see SECURITY.md):
1. Only rows in state 'approved' (or 'publishing' being reclaimed) may publish.
2. REMOTE RECHECK first: if WB already shows an answer, mark published — never re-POST.
3. Safety gate: 2..5000 chars; anything else -> 'failed' without a network call.
4. Idempotency marker (aim:{shop_id}:{wb_review_id}) + state='publishing' is COMMITTED
   before the POST. On ambiguous failure the row stays in 'publishing' and the next
   run reclaims it via the remote recheck instead of blindly retrying.
5. 4xx/5xx-as-client-error -> 'failed' (no blind retry). Timeout/transport -> stay
   'publishing', reconciled by the next recheck.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from wb_web.db.models import Review

MIN_ANSWER_LEN = 2
MAX_ANSWER_LEN = 5000


class WBTransientError(Exception):
    """Timeout / lost connection / DNS etc. Outcome on the server is UNKNOWN."""


class WBClientError(Exception):
    """Definitive client-side rejection (4xx). Retrying will not help."""


class WBReviewsClient(Protocol):
    async def get_review(self, wb_review_id: str) -> dict[str, Any]:
        """Return the WB feedback object (contains 'answer': {...} | None)."""
        ...

    async def post_answer(self, wb_review_id: str, text: str) -> None:
        """POST /api/v1/feedbacks/answer. 204 = success."""
        ...


@dataclass(frozen=True)
class PublishResult:
    outcome: str  # published | remote_already_answered | safety_rejected | failed_client | ambiguous | not_ready
    review_id: int


def publication_marker_for(review: Review) -> str:
    return f"aim:{review.shop_id}:{review.wb_review_id}"


def _safety_problem(text: str) -> str | None:
    stripped = (text or "").strip()
    if len(stripped) < MIN_ANSWER_LEN:
        return "answer shorter than 2 characters"
    if len(stripped) > MAX_ANSWER_LEN:
        return f"answer longer than {MAX_ANSWER_LEN} characters"
    return None


async def _mark(session: AsyncSession, review: Review, **fields: Any) -> None:
    for key, value in fields.items():
        setattr(review, key, value)
    await session.commit()


async def publish_review(
    session: AsyncSession,
    review: Review,
    wb: WBReviewsClient,
) -> PublishResult:
    if review.state not in ("approved", "publishing"):
        return PublishResult("not_ready", review.id)

    # 1) Remote recheck — the publication-status source of truth.
    remote = await wb.get_review(review.wb_review_id)
    remote_answer = (remote.get("answer") or {}) if isinstance(remote, dict) else {}
    if str(remote_answer.get("text", "")).strip():
        await _mark(
            session,
            review,
            state="published",
            published_at=datetime.now(UTC),
            error=None,
            publication_marker=review.publication_marker or publication_marker_for(review),
        )
        return PublishResult("remote_already_answered", review.id)

    # 2) Safety gate — before any state mutation.
    problem = _safety_problem(review.generated_text or "")
    if problem:
        await _mark(session, review, state="failed", error=f"safety: {problem}")
        return PublishResult("safety_rejected", review.id)

    # 3) Commit marker + publishing state BEFORE the network call.
    await _mark(
        session,
        review,
        state="publishing",
        publication_marker=publication_marker_for(review),
        error=None,
    )

    # 4) The single mutation attempt.
    try:
        await wb.post_answer(review.wb_review_id, (review.generated_text or "").strip())
    except WBTransientError as exc:
        # Server outcome UNKNOWN. Stay in 'publishing'; next recheck reconciles.
        await _mark(session, review, error=f"transient: {type(exc).__name__}")
        return PublishResult("ambiguous", review.id)
    except WBClientError as exc:
        await _mark(session, review, state="failed", error=f"client: {exc}")
        return PublishResult("failed_client", review.id)

    await _mark(session, review, state="published", published_at=datetime.now(UTC), error=None)
    return PublishResult("published", review.id)
