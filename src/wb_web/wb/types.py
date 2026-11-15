"""Small dependency-free domain models for WB API responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class SellerInfo:
    name: str
    seller_id: str
    tin: str | None = None
    trade_mark: str | None = None
    country: str | None = None

    @property
    def display_name(self) -> str:
        return self.trade_mark or self.name

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SellerInfo:
        name = _optional_str(payload.get("name"))
        seller_id = _optional_str(payload.get("sid"))
        if not name or not seller_id:
            raise ValueError("seller-info response does not contain name and sid")
        return cls(
            name=name,
            seller_id=seller_id,
            tin=_optional_str(payload.get("tin")),
            trade_mark=_optional_str(payload.get("tradeMark")),
            country=_optional_str(payload.get("country")),
        )


@dataclass(frozen=True, slots=True)
class ProductDetails:
    nm_id: int | None = None
    imt_id: int | None = None
    product_name: str | None = None
    supplier_article: str | None = None
    supplier_name: str | None = None
    brand_name: str | None = None
    size: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> ProductDetails:
        data = payload or {}

        def optional_int(value: Any) -> int | None:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        return cls(
            nm_id=optional_int(data.get("nmId")),
            imt_id=optional_int(data.get("imtId")),
            product_name=_optional_str(data.get("productName")),
            supplier_article=_optional_str(data.get("supplierArticle")),
            supplier_name=_optional_str(data.get("supplierName")),
            brand_name=_optional_str(data.get("brandName")),
            size=_optional_str(data.get("size")),
        )


@dataclass(frozen=True, slots=True)
class FeedbackAnswer:
    text: str
    state: str | None = None
    editable: bool = False

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> FeedbackAnswer | None:
        if not payload:
            return None
        text = _optional_str(payload.get("text"))
        if not text:
            return None
        return cls(
            text=text,
            state=_optional_str(payload.get("state")),
            editable=bool(payload.get("editable", False)),
        )


@dataclass(frozen=True, slots=True)
class Feedback:
    feedback_id: str
    text: str = ""
    pros: str = ""
    cons: str = ""
    rating: int | None = None
    created_at: datetime | None = None
    answer: FeedbackAnswer | None = None
    product: ProductDetails = field(default_factory=ProductDetails)
    user_name: str | None = None
    state: str | None = None
    was_viewed: bool = False
    can_return_product: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_answered(self) -> bool:
        return self.answer is not None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Feedback:
        feedback_id = _optional_str(payload.get("id"))
        if not feedback_id:
            raise ValueError("feedback response does not contain id")
        rating: int | None
        try:
            rating = int(payload["productValuation"]) if payload.get("productValuation") is not None else None
        except (TypeError, ValueError):
            rating = None
        return cls(
            feedback_id=feedback_id,
            text=_optional_str(payload.get("text")) or "",
            pros=_optional_str(payload.get("pros")) or "",
            cons=_optional_str(payload.get("cons")) or "",
            rating=rating,
            created_at=_parse_datetime(payload.get("createdDate")),
            answer=FeedbackAnswer.from_payload(payload.get("answer")),
            product=ProductDetails.from_payload(payload.get("productDetails")),
            user_name=_optional_str(payload.get("userName")),
            state=_optional_str(payload.get("state")),
            was_viewed=bool(payload.get("wasViewed", False)),
            can_return_product=bool(payload.get("isAbleReturnProductOrders", False)),
            raw=dict(payload),
        )


@dataclass(frozen=True, slots=True)
class FeedbackPage:
    feedbacks: tuple[Feedback, ...]
    unanswered_count: int | None = None
    archive_count: int | None = None


@dataclass(frozen=True, slots=True)
class UnansweredCount:
    total: int
    today: int


@dataclass(frozen=True, slots=True)
class TokenValidation:
    seller: SellerInfo
    feedback_access: bool


@dataclass(frozen=True, slots=True)
class AnswerResult:
    feedback_id: str
    published: bool
    already_answered: bool = False
    recovered_after_ambiguous_error: bool = False
