"""Shop routes. Tenant isolation enforced by CurrentShop / owner filters.

Secrets: WB tokens are Fernet-encrypted at rest; responses carry only a mask.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from wb_web.auth.deps import CurrentShop, CurrentUser, SessionDep
from wb_web.billing.access import ActiveAccess
from wb_web.core.security import SecretEncryptor, mask_secret
from wb_web.db.models import Shop
from wb_web.niches import load_packs
from wb_web.wb.errors import (
    WildberriesAuthenticationError,
    WildberriesError,
    WildberriesRateLimitError,
)
from wb_web.wb.sync import sync_shop_reviews, verify_shop_connection

router = APIRouter(prefix="/shops", tags=["shops"])


class CreateShopBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    marketplace: Literal["wb", "ozon"] = "wb"
    wb_token: str = Field(default="", max_length=4000)
    ozon_client_id: str = Field(default="", max_length=64)
    ozon_api_key: str = Field(default="", max_length=4000)
    niche_ids: list[str] = Field(default_factory=list)


def _public(shop: Shop) -> dict:
    def _mask(encrypted: str) -> str | None:
        if not encrypted:
            return None
        try:
            return mask_secret(SecretEncryptor.from_settings().decrypt(encrypted))
        except ValueError:
            return "••••unreadable"

    return {
        "id": shop.id,
        "name": shop.name,
        "marketplace": shop.marketplace,
        "wb_seller_name": shop.wb_seller_name,
        "wb_token_masked": _mask(shop.wb_token_encrypted),
        "ozon_client_id_masked": (shop.ozon_client_id[:2] + "…" + shop.ozon_client_id[-2:]) if shop.ozon_client_id else None,
        "ozon_api_key_masked": _mask(shop.ozon_api_key_encrypted),
        "niche_ids": shop.niche_ids,
        "mode": shop.mode,
        "automation_paused": shop.automation_paused,
        "is_active": shop.is_active,
        "created_at": shop.created_at.isoformat(),
    }


@router.post("", status_code=201)
async def create_shop(body: CreateShopBody, user: CurrentUser, session: SessionDep, _access: ActiveAccess) -> dict:
    name = body.name.strip()
    available = set(load_packs())
    unknown = [nid for nid in body.niche_ids if nid not in available]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown niche_ids: {unknown}. available: {sorted(available)}",
        )
    encryptor = SecretEncryptor.from_settings()
    if body.marketplace == "ozon":
        client_id = body.ozon_client_id.strip()
        api_key = body.ozon_api_key.strip()
        if len(client_id) < 3 or len(api_key) < 8:
            raise HTTPException(status_code=400, detail="ozon: Client-Id (>=3 символов) и Api-Key (>=8) обязательны")
        shop = Shop(
            owner_id=user.id,
            name=name,
            marketplace="ozon",
            wb_token_encrypted="",
            ozon_client_id=client_id,
            ozon_api_key_encrypted=encryptor.encrypt(api_key),
            niche_ids=body.niche_ids,
            mode="manual",
        )
    else:
        wb_token = body.wb_token.strip()
        if len(wb_token) < 8:
            raise HTTPException(status_code=400, detail="wb: token (>=8 символов) обязателен")
        shop = Shop(
            owner_id=user.id,
            name=name,
            marketplace="wb",
            wb_token_encrypted=encryptor.encrypt(wb_token),
            niche_ids=body.niche_ids,
            mode="manual",
        )
    session.add(shop)
    await session.commit()
    await session.refresh(shop)
    return _public(shop)


@router.get("")
async def list_shops(user: CurrentUser, session: SessionDep) -> dict:
    result = await session.execute(select(Shop).where(Shop.owner_id == user.id).order_by(Shop.id))
    return {"shops": [_public(shop) for shop in result.scalars()]}


def _wb_http_error(exc: WildberriesError) -> HTTPException:
    if isinstance(exc, WildberriesAuthenticationError):
        return HTTPException(status_code=400, detail="WB token is invalid or expired")
    if isinstance(exc, WildberriesRateLimitError):
        retry_after = getattr(exc, "retry_after", None)
        return HTTPException(
            status_code=429,
            detail={"error": "wb_rate_limit", "retry_after": retry_after},
        )
    return HTTPException(status_code=502, detail="Wildberries API request failed")


@router.post("/{shop_id}/verify")
async def verify_shop(shop: CurrentShop, session: SessionDep, _access: ActiveAccess) -> dict:
    try:
        if shop.marketplace == "ozon":
            from wb_web.ozon.sync import verify_ozon_connection

            result = await verify_ozon_connection(shop)
        else:
            result = await verify_shop_connection(shop)
    except WildberriesError as exc:
        raise _wb_http_error(exc) from exc
    except Exception as exc:  # ozon client errors
        raise HTTPException(status_code=502, detail=f"Marketplace API error: {exc.__class__.__name__}") from exc
    if result.get("seller_name"):
        shop.wb_seller_name = result["seller_name"]
        await session.commit()
    return result


@router.post("/{shop_id}/sync")
async def sync_shop(shop: CurrentShop, session: SessionDep, _access: ActiveAccess) -> dict:
    try:
        if shop.marketplace == "ozon":
            from wb_web.ozon.sync import sync_ozon_reviews

            result = await sync_ozon_reviews(session, shop)
        else:
            result = await sync_shop_reviews(session, shop)
    except WildberriesError as exc:
        raise _wb_http_error(exc) from exc
    except Exception as exc:  # ozon client errors
        raise HTTPException(status_code=502, detail=f"Marketplace API error: {exc.__class__.__name__}") from exc
    return {"fetched": result.fetched, "created": result.created, "updated": result.updated}


@router.get("/{shop_id}")
async def get_shop(shop: CurrentShop) -> dict:
    return _public(shop)


class AIConfigBody(BaseModel):
    provider: Literal["openai", "openai_compatible", "ollama"] | None = None
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=4000)  # write-only; "" clears
    instructions: str | None = Field(default=None, max_length=4000)
    style: Literal["friendly", "formal", "neutral", "premium", "warm", "concise"] | None = None
    response_length: Literal["very_short", "short", "medium", "detailed"] | None = None
    max_characters: int | None = Field(default=None, ge=32, le=2000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    use_emoji: bool | None = None
    daily_limit: int | None = Field(default=None, ge=0, le=10000)
    negative_requires_approval: bool | None = None


def _ai_public(shop: Shop) -> dict:
    key_set = bool(shop.ai_api_key_encrypted)
    masked = None
    if key_set:
        try:
            masked = mask_secret(SecretEncryptor.from_settings().decrypt(shop.ai_api_key_encrypted))
        except ValueError:
            masked = "����unreadable"
    return {
        "provider": shop.ai_provider,
        "model": shop.ai_model,
        "base_url": shop.ai_base_url,
        "api_key_configured": key_set,
        "api_key_masked": masked,
        "instructions": shop.ai_instructions,
        "style": shop.ai_style,
        "response_length": shop.ai_response_length,
        "max_characters": shop.ai_max_characters,
        "temperature": shop.ai_temperature,
        "use_emoji": shop.ai_use_emoji,
        "daily_limit": shop.ai_daily_limit,
        "negative_requires_approval": shop.ai_negative_requires_approval,
    }


_FIELD_MAP = {
    "provider": "ai_provider",
    "model": "ai_model",
    "instructions": "ai_instructions",
    "style": "ai_style",
    "response_length": "ai_response_length",
    "max_characters": "ai_max_characters",
    "temperature": "ai_temperature",
    "use_emoji": "ai_use_emoji",
    "daily_limit": "ai_daily_limit",
    "negative_requires_approval": "ai_negative_requires_approval",
}


@router.get("/{shop_id}/ai")
async def get_ai_config(shop: CurrentShop) -> dict:
    return _ai_public(shop)


@router.patch("/{shop_id}/ai")
async def update_ai_config(
    shop: CurrentShop, body: AIConfigBody, session: SessionDep, _access: ActiveAccess
) -> dict:
    data = body.model_dump(exclude_unset=True)
    if "api_key" in data:
        raw_key = (data.pop("api_key") or "").strip()
        shop.ai_api_key_encrypted = "" if raw_key == "" else SecretEncryptor.from_settings().encrypt(raw_key)
    if "base_url" in data:
        raw_url = (data.pop("base_url") or "").strip()
        shop.ai_base_url = raw_url or None
    for key, attr in _FIELD_MAP.items():
        if key in data:
            setattr(shop, attr, data[key])
    await session.commit()
    await session.refresh(shop)
    return _ai_public(shop)
