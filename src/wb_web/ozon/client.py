"""Async client for Ozon Seller API (reviews namespace only)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

API_BASE = "https://api-seller.ozon.ru"


class OzonError(RuntimeError):
    pass


class OzonTransientError(OzonError):
    """Timeout / 429 / 5xx — outcome unknown, never blind-retry mutations."""


class OzonClientError(OzonError):
    """Definitive 4xx — retrying will not help."""


@dataclass(frozen=True, slots=True)
class OzonReview:
    review_id: str
    sku: int | None
    text: str
    rating: int | None
    status: str
    published_at: datetime | None
    comments_amount: int


@dataclass(frozen=True, slots=True)
class OzonReviewPage:
    reviews: tuple[OzonReview, ...]
    has_next: bool
    last_id: str


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_review(raw: dict[str, Any]) -> OzonReview | None:
    rid = raw.get("id")
    if not rid:
        return None
    try:
        rating = int(raw["rating"]) if raw.get("rating") is not None else None
    except (TypeError, ValueError):
        rating = None
    try:
        sku = int(raw["sku"]) if raw.get("sku") is not None else None
    except (TypeError, ValueError):
        sku = None
    try:
        comments = int(raw.get("comments_amount") or 0)
    except (TypeError, ValueError):
        comments = 0
    return OzonReview(
        review_id=str(rid),
        sku=sku,
        text=str(raw.get("text") or "").strip(),
        rating=rating,
        status=str(raw.get("status") or ""),
        published_at=_parse_dt(raw.get("published_at")),
        comments_amount=comments,
    )


class OzonClient:
    def __init__(
        self,
        client_id: str,
        api_key: str,
        *,
        base_url: str = API_BASE,
        timeout: float | httpx.Timeout = 20.0,
        max_attempts: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        client_id = client_id.strip()
        api_key = api_key.strip()
        if not client_id or not api_key:
            raise ValueError("Ozon Client-Id and Api-Key must not be empty")
        self._client_id = client_id
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._max_attempts = max(1, max_attempts)
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)

    @classmethod
    def from_shop(cls, shop) -> OzonClient:
        from wb_web.core.security import SecretEncryptor

        api_key = SecretEncryptor.from_settings().decrypt(shop.ozon_api_key_encrypted)
        return cls(client_id=shop.ozon_client_id, api_key=api_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> OzonClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Client-Id": self._client_id, "Api-Key": self._api_key, "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict[str, Any], *, retry: bool = True) -> httpx.Response:
        attempts = self._max_attempts if retry else 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http.post(f"{self._base}{path}", headers=self._headers, json=body)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < attempts:
                    continue
                raise OzonTransientError("Ozon request timed out") from exc
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < attempts:
                    continue
                raise OzonTransientError("Ozon transport error") from exc
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < attempts:
                    continue
                raise OzonTransientError(f"Ozon HTTP {response.status_code}")
            if response.status_code >= 400:
                raise OzonClientError(f"Ozon HTTP {response.status_code}: {response.text[:200]}")
            return response
        raise OzonTransientError(f"Ozon request failed: {last_exc}")

    async def list_reviews(
        self,
        *,
        status: str = "NEW",
        limit: int = 100,
        last_id: str | None = None,
        sort_dir: str = "DESC",
    ) -> OzonReviewPage:
        limit = max(20, min(100, limit))
        body: dict[str, Any] = {
            "filters": {"status": status},
            "limit": limit,
            "sort_dir": sort_dir,
        }
        if last_id:
            body["last_id"] = last_id
        payload = (await self._post("/v2/review/list", body)).json()
        raw_reviews = payload.get("reviews") if isinstance(payload, dict) else None
        reviews = tuple(
            r for r in (_parse_review(item) for item in (raw_reviews or [])) if r is not None
        )
        return OzonReviewPage(
            reviews=reviews,
            has_next=bool(payload.get("has_next", False)) if isinstance(payload, dict) else False,
            last_id=str(payload.get("last_id") or "") if isinstance(payload, dict) else "",
        )

    async def iter_new_reviews(self, max_pages: int = 20) -> AsyncIterator[OzonReview]:
        last_id: str | None = None
        for _ in range(max_pages):
            page = await self.list_reviews(status="NEW", limit=100, last_id=last_id)
            for review in page.reviews:
                yield review
            if not page.has_next:
                return
            last_id = page.last_id

    async def list_comments(self, review_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        payload = (
            await self._post("/v1/review/comment/list", {"review_id": review_id, "limit": limit})
        ).json()
        comments = payload.get("comments") if isinstance(payload, dict) else []
        return [c for c in (comments or []) if isinstance(c, dict)]

    async def has_own_comment(self, review_id: str) -> bool:
        comments = await self.list_comments(review_id)
        return any(c.get("is_owner") for c in comments)

    async def create_comment(self, review_id: str, text: str, *, mark_processed: bool = True) -> None:
        text = text.strip()
        if not 2 <= len(text) <= 5000:
            raise ValueError("comment text must be 2..5000 characters")
        # Never retried: a lost response would double-post a public comment.
        await self._post(
            "/v1/review/comment/create",
            {"review_id": review_id, "text": text, "mark_review_as_processed": mark_processed},
            retry=False,
        )
