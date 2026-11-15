"""SQLAlchemy models. Multi-tenant: user (tenant) -> shops -> reviews.

Secrets (WB token, AI provider key) are stored as Fernet ciphertext strings.
Review states: new -> queued -> generated -> approved -> published (or skipped/failed).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    shops: Mapped[list[Shop]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    subscription: Mapped[Subscription | None] = relationship(back_populates="user", uselist=False)


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Fernet ciphertext. Never plaintext.
    wb_token_encrypted: Mapped[str] = mapped_column(Text())
    wb_seller_name: Mapped[str | None] = mapped_column(String(200))
    # Selected niche pack ids, e.g. ["clothing", "cosmetics"]. Empty -> universal base pack.
    niche_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(16), default="manual")  # manual | semi | auto
    is_active: Mapped[bool] = mapped_column(default=True)
    automation_paused: Mapped[bool] = mapped_column(default=False)  # kill switch from admin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    owner: Mapped[User] = relationship(back_populates="shops")
    reviews: Mapped[list[Review]] = relationship(back_populates="shop", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    # trial | starter | pro | business
    plan: Mapped[str] = mapped_column(String(16), default="trial")
    # active | grace | read_only | cancelled
    status: Mapped[str] = mapped_column(String(16), default="active")
    provider: Mapped[str] = mapped_column(String(32), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(128))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="subscription")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("shop_id", "wb_review_id", name="uq_review_shop_wb_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    wb_review_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(16), default="new", index=True)
    rating: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text())
    product_name: Mapped[str | None] = mapped_column(String(300))
    product_category: Mapped[str | None] = mapped_column(String(200))
    generated_text: Mapped[str | None] = mapped_column(Text)
    review_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at_wb: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    shop: Mapped[Shop] = relationship(back_populates="reviews")
