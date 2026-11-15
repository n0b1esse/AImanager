"""Shop routes. Tenant isolation enforced by CurrentShop / owner filters.

Secrets: WB tokens are Fernet-encrypted at rest; responses carry only a mask.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from wb_web.auth.deps import CurrentShop, CurrentUser, SessionDep
from wb_web.billing.access import ActiveAccess
from wb_web.core.security import SecretEncryptor, mask_secret
from wb_web.db.models import Shop
from wb_web.niches import load_packs

router = APIRouter(prefix="/shops", tags=["shops"])


class CreateShopBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    wb_token: str = Field(min_length=8, max_length=4000)
    niche_ids: list[str] = Field(default_factory=list)


def _mask_wb_token(ciphertext: str) -> str:
    try:
        return mask_secret(SecretEncryptor.from_settings().decrypt(ciphertext))
    except ValueError:
        return "••••unreadable"


def _public(shop: Shop) -> dict:
    return {
        "id": shop.id,
        "name": shop.name,
        "wb_seller_name": shop.wb_seller_name,
        "wb_token_masked": _mask_wb_token(shop.wb_token_encrypted),
        "niche_ids": shop.niche_ids,
        "mode": shop.mode,
        "automation_paused": shop.automation_paused,
        "is_active": shop.is_active,
        "created_at": shop.created_at.isoformat(),
    }


@router.post("", status_code=201)
async def create_shop(body: CreateShopBody, user: CurrentUser, session: SessionDep, _access: ActiveAccess) -> dict:
    name = body.name.strip()
    wb_token = body.wb_token.strip()
    available = set(load_packs())
    unknown = [nid for nid in body.niche_ids if nid not in available]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown niche_ids: {unknown}. available: {sorted(available)}",
        )
    encryptor = SecretEncryptor.from_settings()
    shop = Shop(
        owner_id=user.id,
        name=name,
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


@router.get("/{shop_id}")
async def get_shop(shop: CurrentShop) -> dict:
    return _public(shop)
