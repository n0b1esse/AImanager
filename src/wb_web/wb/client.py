"""Asynchronous client for the official Wildberries seller API.

The endpoint paths and request shapes in this module follow the official
Customer Communication and Common API documentation.  Tokens are sent raw in
the ``Authorization`` header (there is no ``Bearer`` prefix).
"""

from __future__ import annotations

import asyncio
import email.utils
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from wb_web.wb.errors import (
    WildberriesAuthenticationError,
    WildberriesBadRequestError,
    WildberriesConflictError,
    WildberriesError,
    WildberriesNotFoundError,
    WildberriesPaymentRequiredError,
    WildberriesPermissionError,
    WildberriesRateLimitError,
    WildberriesResponseError,
    WildberriesServerError,
    WildberriesTimeoutError,
    WildberriesTransportError,
    WildberriesValidationError,
)
from wb_web.wb.types import (
    AnswerResult,
    Feedback,
    FeedbackAnswer,
    FeedbackPage,
    SellerInfo,
    TokenValidation,
    UnansweredCount,
)

COMMON_API_BASE = "https://common-api.wildberries.kg"
FEEDBACKS_API_BASE = "https://feedbacks-api.wildberries.kg"
FEEDBACKS_SANDBOX_BASE = "https://feedbacks-api-sandbox.wildberries.kg"

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry configuration for transient and explicitly rate-limited calls."""

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 15.0
    jitter_ratio: float = 0.2

    def delay(self, attempt: int) -> float:
        base = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        jitter = base * self.jitter_ratio * random.random()
        return base + jitter


class WildberriesClient:
    """Official WB API client for one seller token.

    A client instance is intentionally scoped to a single shop so request
    state and authentication cannot leak between connected accounts.
    """

    def __init__(
        self,
        token: str,
        *,
        sandbox: bool = False,
        timeout: float | httpx.Timeout = 20.0,
        retry_policy: RetryPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
        common_base_url: str = COMMON_API_BASE,
        feedbacks_base_url: str | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        clean_token = token.strip()
        if not clean_token:
            raise ValueError("Wildberries API token must not be empty")
        self._token = clean_token
        self._common_base = common_base_url.rstrip("/")
        selected_feedbacks_base = feedbacks_base_url or (
            FEEDBACKS_SANDBOX_BASE if sandbox else FEEDBACKS_API_BASE
        )
        self._feedbacks_base = selected_feedbacks_base.rstrip("/")
        self._retry = retry_policy or RetryPolicy()
        if self._retry.max_attempts < 1:
            raise ValueError("retry_policy.max_attempts must be at least 1")
        self._sleep = sleep
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "Avto-Otvetchik/0.1"},
        )

    async def __aenter__(self) -> WildberriesClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def get_seller_info(self) -> SellerInfo:
        payload = await self._request_json("GET", f"{self._common_base}/api/v1/seller-info")
        try:
            return SellerInfo.from_payload(self._mapping(payload, context="seller-info"))
        except (TypeError, ValueError) as exc:
            raise WildberriesResponseError(str(exc)) from exc

    async def validate_token(self, *, require_feedback_access: bool = True) -> TokenValidation:
        seller = await self.get_seller_info()
        feedback_access = False
        try:
            await self._request("GET", f"{self._feedbacks_base}/ping")
            feedback_access = True
        except WildberriesPermissionError:
            if require_feedback_access:
                raise
        return TokenValidation(seller=seller, feedback_access=feedback_access)

    async def get_unanswered_count(self) -> UnansweredCount:
        payload = await self._request_json(
            "GET", f"{self._feedbacks_base}/api/v1/feedbacks/count-unanswered"
        )
        data = self._mapping(self._unwrap(payload), context="count-unanswered.data")
        try:
            return UnansweredCount(
                total=int(data.get("countUnanswered", 0)),
                today=int(data.get("countUnansweredToday", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise WildberriesResponseError("Invalid unanswered feedback count") from exc

    async def count_feedbacks(
        self,
        *,
        is_answered: bool,
        date_from: datetime | int | None = None,
        date_to: datetime | int | None = None,
    ) -> int:
        params: dict[str, Any] = {"isAnswered": self._bool_param(is_answered)}
        self._add_date_range(params, date_from, date_to)
        payload = await self._request_json(
            "GET", f"{self._feedbacks_base}/api/v1/feedbacks/count", params=params
        )
        try:
            return int(self._unwrap(payload))
        except (TypeError, ValueError) as exc:
            raise WildberriesResponseError("Invalid feedback count") from exc

    async def list_feedbacks(
        self,
        *,
        is_answered: bool,
        take: int = 100,
        skip: int = 0,
        order: str = "dateDesc",
        nm_id: int | None = None,
        date_from: datetime | int | None = None,
        date_to: datetime | int | None = None,
    ) -> FeedbackPage:
        self._validate_page(take=take, skip=skip)
        if order not in {"dateAsc", "dateDesc"}:
            raise ValueError("order must be 'dateAsc' or 'dateDesc'")
        params: dict[str, Any] = {
            "isAnswered": self._bool_param(is_answered),
            "take": take,
            "skip": skip,
            "order": order,
        }
        if nm_id is not None:
            params["nmId"] = int(nm_id)
        self._add_date_range(params, date_from, date_to)
        payload = await self._request_json(
            "GET", f"{self._feedbacks_base}/api/v1/feedbacks", params=params
        )
        return self._parse_feedback_page(payload)

    async def iter_feedbacks(
        self,
        *,
        is_answered: bool,
        page_size: int = 100,
        order: str = "dateDesc",
        nm_id: int | None = None,
        date_from: datetime | int | None = None,
        date_to: datetime | int | None = None,
    ) -> AsyncIterator[Feedback]:
        skip = 0
        while True:
            page = await self.list_feedbacks(
                is_answered=is_answered,
                take=page_size,
                skip=skip,
                order=order,
                nm_id=nm_id,
                date_from=date_from,
                date_to=date_to,
            )
            for feedback in page.feedbacks:
                yield feedback
            if len(page.feedbacks) < page_size:
                return
            skip += len(page.feedbacks)

    async def list_archive_feedbacks(
        self,
        *,
        take: int = 100,
        skip: int = 0,
        order: str = "dateDesc",
        nm_id: int | None = None,
    ) -> FeedbackPage:
        self._validate_page(take=take, skip=skip)
        if order not in {"dateAsc", "dateDesc"}:
            raise ValueError("order must be 'dateAsc' or 'dateDesc'")
        params: dict[str, Any] = {"take": take, "skip": skip, "order": order}
        if nm_id is not None:
            params["nmId"] = int(nm_id)
        payload = await self._request_json(
            "GET", f"{self._feedbacks_base}/api/v1/feedbacks/archive", params=params
        )
        return self._parse_feedback_page(payload)

    async def get_feedback(self, feedback_id: str) -> Feedback:
        clean_id = feedback_id.strip()
        if not clean_id:
            raise ValueError("feedback_id must not be empty")
        payload = await self._request_json(
            "GET",
            f"{self._feedbacks_base}/api/v1/feedback",
            params={"id": clean_id},
        )
        data = self._mapping(self._unwrap(payload), context="feedback.data")
        try:
            return Feedback.from_payload(data)
        except ValueError as exc:
            raise WildberriesResponseError(str(exc)) from exc

    async def get_answer_status(self, feedback_id: str) -> FeedbackAnswer | None:
        """Return WB's current moderation/editability state for an answer."""

        return (await self.get_feedback(feedback_id)).answer

    async def answer_feedback(self, feedback_id: str, text: str) -> AnswerResult:
        """Publish one answer with duplicate and ambiguous-result protection.

        WB documents that the answer endpoint does *not* validate a feedback ID.
        The preflight GET therefore serves both as validation and as the first
        line of duplicate protection.  A transport/5xx result is ambiguous, so
        the review is fetched again before the error is raised or any retry is
        considered by a higher-level queue.
        """

        clean_id, clean_text = self._validate_answer(feedback_id, text)
        before = await self.get_feedback(clean_id)
        if before.is_answered:
            return AnswerResult(clean_id, published=False, already_answered=True)
        try:
            await self._request(
                "POST",
                f"{self._feedbacks_base}/api/v1/feedbacks/answer",
                json={"id": clean_id, "text": clean_text},
                retry_ambiguous=False,
            )
        except (WildberriesTransportError, WildberriesServerError) as original:
            try:
                after = await self.get_feedback(clean_id)
            except WildberriesError as verification_error:
                raise original from verification_error
            if after.answer and after.answer.text.strip() == clean_text:
                return AnswerResult(
                    clean_id,
                    published=True,
                    recovered_after_ambiguous_error=True,
                )
            if after.is_answered:
                # Another worker/account process may have won the race.  It is
                # still unsafe to retry, but we must not claim its reply as ours.
                return AnswerResult(
                    clean_id,
                    published=False,
                    already_answered=True,
                    recovered_after_ambiguous_error=True,
                )
            raise original
        return AnswerResult(clean_id, published=True)

    async def edit_feedback_answer(self, feedback_id: str, text: str) -> AnswerResult:
        clean_id, clean_text = self._validate_answer(feedback_id, text)
        current = await self.get_feedback(clean_id)
        if not current.answer:
            raise WildberriesConflictError("The review has no answer to edit")
        if not current.answer.editable:
            raise WildberriesConflictError(
                "The answer cannot be edited (WB permits one edit within 60 days)"
            )
        try:
            await self._request(
                "PATCH",
                f"{self._feedbacks_base}/api/v1/feedbacks/answer",
                json={"id": clean_id, "text": clean_text},
                retry_ambiguous=False,
            )
        except (WildberriesTransportError, WildberriesServerError) as original:
            try:
                after = await self.get_feedback(clean_id)
            except WildberriesError as verification_error:
                raise original from verification_error
            if after.answer and after.answer.text.strip() == clean_text:
                return AnswerResult(
                    clean_id,
                    published=True,
                    recovered_after_ambiguous_error=True,
                )
            raise original
        return AnswerResult(clean_id, published=True)

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = await self._request(method, url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise WildberriesResponseError(
                "Wildberries API returned non-JSON data",
                status_code=response.status_code,
                request_id=self._request_id(response),
            ) from exc

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        retry_ambiguous: bool = True,
    ) -> httpx.Response:
        method = method.upper()
        # Explicit 429 responses are safe to retry even for POST/PATCH because
        # the server rejected the attempt before processing it.  Transport and
        # 5xx outcomes are potentially ambiguous for mutations and are retried
        # only for idempotent/read calls.
        attempts = self._retry.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": self._token},
                )
            except httpx.TimeoutException as exc:
                if retry_ambiguous and attempt < attempts:
                    await self._sleep(self._retry.delay(attempt))
                    continue
                raise WildberriesTimeoutError() from exc
            except httpx.TransportError as exc:
                if retry_ambiguous and attempt < attempts:
                    await self._sleep(self._retry.delay(attempt))
                    continue
                raise WildberriesTransportError() from exc

            if response.status_code == 429:
                retry_after = self._retry_after(response)
                if attempt < self._retry.max_attempts:
                    await self._sleep(retry_after or self._retry.delay(attempt))
                    continue
                raise WildberriesRateLimitError(
                    status_code=429,
                    request_id=self._request_id(response),
                    details=self._safe_error_body(response),
                    retry_after=retry_after,
                )

            if (
                500 <= response.status_code <= 599
                and retry_ambiguous
                and attempt < attempts
            ):
                await self._sleep(self._retry.delay(attempt))
                continue
            self._raise_for_status(response)
            return response
        raise AssertionError("retry loop exhausted unexpectedly")

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        body = self._safe_error_body(response)
        message = self._error_message(body)
        kwargs = {
            "status_code": status,
            "request_id": self._request_id(response, body),
            "details": body,
        }
        if status == 400:
            raise WildberriesBadRequestError(message, **kwargs)
        if status == 401:
            raise WildberriesAuthenticationError(message, **kwargs)
        if status == 402:
            raise WildberriesPaymentRequiredError(message, **kwargs)
        if status == 403:
            raise WildberriesPermissionError(message, **kwargs)
        if status == 404:
            raise WildberriesNotFoundError(message, **kwargs)
        if status == 409:
            raise WildberriesConflictError(message, **kwargs)
        if status == 422:
            raise WildberriesValidationError(message, **kwargs)
        if status == 429:
            raise WildberriesRateLimitError(
                message,
                **kwargs,
                retry_after=self._retry_after(response),
            )
        if status >= 500:
            raise WildberriesServerError(message, **kwargs)
        raise WildberriesError(message, **kwargs)

    def _unwrap(self, payload: Any) -> Any:
        if isinstance(payload, Mapping) and "error" in payload:
            if bool(payload.get("error")):
                raise WildberriesResponseError(
                    self._error_message(payload),
                    details=payload.get("additionalErrors"),
                )
            return payload.get("data")
        return payload

    def _parse_feedback_page(self, payload: Any) -> FeedbackPage:
        data = self._mapping(self._unwrap(payload), context="feedbacks.data")
        items = data.get("feedbacks", [])
        if not isinstance(items, list):
            raise WildberriesResponseError("feedbacks.data.feedbacks is not a list")
        try:
            feedbacks = tuple(Feedback.from_payload(self._mapping(item, context="feedback")) for item in items)
        except ValueError as exc:
            raise WildberriesResponseError(str(exc)) from exc
        return FeedbackPage(
            feedbacks=feedbacks,
            unanswered_count=self._optional_int(data.get("countUnanswered")),
            archive_count=self._optional_int(data.get("countArchive")),
        )

    @staticmethod
    def _validate_page(*, take: int, skip: int) -> None:
        if not 1 <= take <= 5000:
            raise ValueError("take must be between 1 and 5000")
        if not 0 <= skip <= 199_990:
            raise ValueError("skip must be between 0 and 199990")

    @staticmethod
    def _validate_answer(feedback_id: str, text: str) -> tuple[str, str]:
        clean_id = feedback_id.strip()
        clean_text = text.strip()
        if not clean_id:
            raise ValueError("feedback_id must not be empty")
        if not 2 <= len(clean_text) <= 5000:
            raise ValueError("answer text must contain between 2 and 5000 characters")
        return clean_id, clean_text

    @staticmethod
    def _add_date_range(
        params: dict[str, Any],
        date_from: datetime | int | None,
        date_to: datetime | int | None,
    ) -> None:
        if date_from is not None:
            params["dateFrom"] = WildberriesClient._timestamp(date_from)
        if date_to is not None:
            params["dateTo"] = WildberriesClient._timestamp(date_to)
        if params.get("dateFrom") is not None and params.get("dateTo") is not None:
            if params["dateFrom"] > params["dateTo"]:
                raise ValueError("date_from must not be after date_to")

    @staticmethod
    def _timestamp(value: datetime | int) -> int:
        if isinstance(value, datetime):
            return int(value.timestamp())
        return int(value)

    @staticmethod
    def _bool_param(value: bool) -> str:
        return "true" if value else "false"

    @staticmethod
    def _mapping(payload: Any, *, context: str) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise WildberriesResponseError(f"{context} is not an object")
        return payload

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_error_body(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            text = response.text.strip()
            return text[:1000] if text else None

    @staticmethod
    def _error_message(body: Any) -> str:
        if isinstance(body, Mapping):
            for key in ("detail", "errorText", "title", "message"):
                value = body.get(key)
                if value:
                    return str(value)
        if isinstance(body, str) and body:
            return body
        return "Wildberries API request failed"

    @staticmethod
    def _request_id(response: httpx.Response, body: Any = None) -> str | None:
        if isinstance(body, Mapping) and body.get("requestId"):
            return str(body["requestId"])
        for name in ("x-request-id", "request-id"):
            if response.headers.get(name):
                return response.headers[name]
        return None

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(value)
                now = datetime.now(retry_at.tzinfo)
                return max(0.0, (retry_at - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None


__all__ = [
    "COMMON_API_BASE",
    "FEEDBACKS_API_BASE",
    "FEEDBACKS_SANDBOX_BASE",
    "RetryPolicy",
    "WildberriesClient",
]
