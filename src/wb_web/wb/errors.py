"""Typed errors raised by the official Wildberries API integration.

The token and request headers are deliberately never stored on errors.
"""

from __future__ import annotations

from typing import Any


class WildberriesError(Exception):
    """Base class for errors safe to surface in the desktop UI.

    The token and request headers are deliberately never stored on the error.
    """

    default_message = "Wildberries API request failed"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.status_code = status_code
        self.request_id = request_id
        self.details = details


class WildberriesTransportError(WildberriesError):
    default_message = "Could not connect to Wildberries API"


class WildberriesTimeoutError(WildberriesTransportError):
    default_message = "Wildberries API request timed out"


class WildberriesBadRequestError(WildberriesError):
    default_message = "Wildberries API rejected the request"


class WildberriesAuthenticationError(WildberriesError):
    default_message = "Wildberries API token is invalid or expired"


class WildberriesPaymentRequiredError(WildberriesError):
    default_message = "Wildberries API requires payment for this operation"


class WildberriesPermissionError(WildberriesError):
    default_message = "The token does not grant access to questions and reviews"


class WildberriesNotFoundError(WildberriesError):
    default_message = "The requested Wildberries resource was not found"


class WildberriesConflictError(WildberriesError):
    default_message = "The review already has an answer"


class WildberriesValidationError(WildberriesError):
    default_message = "Wildberries API could not process the request"


class WildberriesRateLimitError(WildberriesError):
    default_message = "Wildberries API request limit was reached"

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class WildberriesServerError(WildberriesError):
    default_message = "Wildberries API is temporarily unavailable"


class WildberriesResponseError(WildberriesError):
    default_message = "Wildberries API returned an invalid response"
