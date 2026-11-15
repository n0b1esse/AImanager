"""Server-rendered PRG frontend (no JS framework). Actions = plain forms.

Every mutating handler re-checks the subscription access gate server-side;
the HTML merely reflects state. Errors land on the next page as ?msg=…&kind=…
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_web import __version__
from wb_web.admin import totp as admin_totp
from wb_web.admin.audit import record_admin_action
from wb_web.admin.deps import (
    ADMIN_2FA_COOKIE,
    ADMIN_2FA_TTL_SECONDS,
    admin_2fa_cookie_valid,
    get_or_create_admin_user,
    owner_emails,
    role_for_email,
)
from wb_web.ai.generate import AIRequestError, DailyLimitExceeded, generate_for_review
from wb_web.auth.deps import get_optional_user
from wb_web.auth.service import request_magic_link
from wb_web.billing.access import resolve_access
from wb_web.billing.activate import activate_subscription
from wb_web.core.config import get_settings
from wb_web.core.events import track
from wb_web.core.security import SecretEncryptor, mask_secret
from wb_web.core.session import sign_session
from wb_web.db.base import get_session
from wb_web.db.models import AdminAuditLog, Review, Shop, Subscription, User
from wb_web.niches import load_packs
from wb_web.queue import publish_review
from wb_web.stats import admin_sales, client_stats
from wb_web.wb.adapter import WBReviewsAdapter
from wb_web.wb.sync import build_client_for_shop, sync_shop_reviews, verify_shop_connection

logger = logging.getLogger("wb_web.web")

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

STATE_LABELS = {
    "new": "новая",
    "generated": "ответ готов",
    "approved": "одобрено",
    "published": "опубликовано",
    "skipped": "пропущено",
    "failed": "ошибка",
    "publishing": "публикуется",
}

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class WebRedirect(Exception):
    def __init__(self, url: str) -> None:
        self.url = url


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _with_msg(url: str, msg: str, kind: str = "ok") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return _redirect(f"{url}{sep}msg={quote(msg)}&kind={kind}")


async def _web_user(request: Request, session: SessionDep) -> User:
    user = await get_optional_user(request, session)
    if user is None:
        raise WebRedirect("/login")
    return user


async def _ctx(request: Request, session: SessionDep, **kwargs):
    user = await _web_user(request, session)
    access = await resolve_access(user, session)
    return user, access, {
        "user": user,
        "access": access,
        "is_owner": user.email.lower() in owner_emails(),
        "is_admin": role_for_email(user.email) is not None,
        "flash": request.query_params.get("msg"),
        "flash_kind": request.query_params.get("kind", "ok"),
        "version": __version__,
        **kwargs,
    }


def _require_access(access) -> None:
    if not access.allowed:
        raise WebRedirect("/app/billing?msg=subscription_required&kind=warn")


def _qr_svg(data: str) -> str:
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(data, image_factory=factory, box_size=8, border=2)
    buffer = io.BytesIO()
    img.save(buffer)
    return buffer.getvalue().decode()


def _set_admin_2fa_cookie(response: RedirectResponse, admin_id: int) -> None:
    settings = get_settings()
    token = sign_session(
        {"adm": admin_id, "purpose": "admin2fa"},
        settings.session_secret,
        ADMIN_2FA_TTL_SECONDS,
    )
    response.set_cookie(
        ADMIN_2FA_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=ADMIN_2FA_TTL_SECONDS,
        path="/",
    )


async def _admin_guard(request: Request, session: AsyncSession):
    user = await _web_user(request, session)
    admin = await get_or_create_admin_user(session, user.email)
    if admin is None:
        raise WebRedirect(f"/app?msg={quote('Недостаточно прав')}&kind=err")
    if not admin.totp_enabled:
        raise WebRedirect("/app/admin/setup")
    if not admin_2fa_cookie_valid(request, admin):
        raise WebRedirect("/app/admin/2fa")
    return user, admin


# ---------- public ----------

@router.get("/")
async def root(request: Request, session: SessionDep):
    user = await get_optional_user(request, session)
    return _redirect("/app" if user else "/login")


@router.get("/login")
async def login_page(request: Request, session: SessionDep):
    user = await get_optional_user(request, session)
    if user is not None:
        return _redirect("/app")
    ctx = {
        "user": None,
        "access": None,
        "flash": request.query_params.get("msg"),
        "flash_kind": request.query_params.get("kind", "ok"),
        "is_owner": False,
    }
    return templates.TemplateResponse(request, "login.html", ctx)


@router.post("/login")
async def login_submit(request: Request, session: SessionDep, email: Annotated[str, Form()]):
    settings = get_settings()
    try:
        raw = await request_magic_link(session, email)
        dev_link = f"/auth/link?token={raw}" if settings.app_env != "production" else None
        ctx = {
            "user": None,
            "access": None,
            "flash": None,
            "sent": email.strip().lower(),
            "dev_link": dev_link,
            "is_owner": False,
        }
        return templates.TemplateResponse(request, "login.html", ctx)
    except ValueError:
        ctx = {
            "user": None,
            "access": None,
            "flash": "Некорректный email",
            "flash_kind": "err",
            "is_owner": False,
        }
        return templates.TemplateResponse(request, "login.html", ctx, status_code=400)


# ---------- app ----------

@router.get("/app")
async def dashboard(request: Request, session: SessionDep):
    user, access, ctx = await _ctx(request, session)

    async def _count(state: str) -> int:
        return int(
            (
                await session.execute(
                    select(func.count(Review.id))
                    .join(Shop, Shop.id == Review.shop_id)
                    .where(Shop.owner_id == user.id, Review.state == state)
                )
            ).scalar_one()
        )

    stats = {
        "new": await _count("new"),
        "generated": await _count("generated"),
        "approved": await _count("approved"),
    }
    shops = (
        await session.execute(select(Shop).where(Shop.owner_id == user.id).order_by(Shop.id))
    ).scalars().all()
    ctx.update({"stats": stats, "shops": shops})
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/app/shops")
async def shops_page(request: Request, session: SessionDep):
    user, access, ctx = await _ctx(request, session)
    shops = (
        await session.execute(select(Shop).where(Shop.owner_id == user.id).order_by(Shop.id))
    ).scalars().all()
    token_masks = {}
    for shop in shops:
        try:
            token_masks[shop.id] = mask_secret(
                SecretEncryptor.from_settings().decrypt(shop.wb_token_encrypted)
            )
        except ValueError:
            token_masks[shop.id] = "••••unreadable"
    ctx.update({"shops": shops, "niches": load_packs(), "token_masks": token_masks})
    return templates.TemplateResponse(request, "shops.html", ctx)


@router.post("/app/shops")
async def shops_create(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()],
    wb_token: Annotated[str, Form()],
    niche_ids: Annotated[list[str] | None, Form()] = None,
):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    niche_ids = niche_ids or []
    name = name.strip()
    wb_token = wb_token.strip()
    if not name or len(wb_token) < 8:
        return _with_msg("/app/shops", "Название пустое или токен короче 8 символов", "err")
    available = set(load_packs())
    unknown = [n for n in niche_ids if n not in available]
    if unknown:
        return _with_msg("/app/shops", f"Неизвестные ниши: {', '.join(unknown)}", "err")
    shop = Shop(
        owner_id=user.id,
        name=name,
        wb_token_encrypted=SecretEncryptor.from_settings().encrypt(wb_token),
        niche_ids=niche_ids,
        mode="manual",
    )
    session.add(shop)
    await session.commit()
    await track(session, event="shop.create", user_id=user.id, shop_id=shop.id)
    return _with_msg("/app/shops", "Магазин подключён")


async def _owned_shop(session: SessionDep, user: User, shop_id: int) -> Shop | None:
    return (
        await session.execute(select(Shop).where(Shop.id == shop_id, Shop.owner_id == user.id))
    ).scalar_one_or_none()


@router.post("/app/shops/{shop_id}/verify")
async def shop_verify(request: Request, session: SessionDep, shop_id: int):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    shop = await _owned_shop(session, user, shop_id)
    if shop is None:
        return _with_msg("/app/shops", "Магазин не найден", "err")
    try:
        result = await verify_shop_connection(shop)
        shop.wb_seller_name = result.get("seller_name")
        await session.commit()
        return _with_msg("/app/shops", f"Подключение работает: {result.get('display_name')}")
    except Exception as exc:  # noqa: BLE001 — operator-safe message only
        return _with_msg("/app/shops", f"WB недоступен: {exc.__class__.__name__}", "err")


@router.post("/app/shops/{shop_id}/sync")
async def shop_sync(request: Request, session: SessionDep, shop_id: int):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    shop = await _owned_shop(session, user, shop_id)
    if shop is None:
        return _with_msg("/app/shops", "Магазин не найден", "err")
    try:
        result = await sync_shop_reviews(session, shop)
        return _with_msg(
            "/app/shops",
            f"Синхронизировано: получено {result.fetched}, новых {result.created}, обновлено {result.updated}",
        )
    except Exception as exc:  # noqa: BLE001
        return _with_msg("/app/shops", f"Синхронизация не удалась: {exc.__class__.__name__}", "err")


@router.get("/app/shops/{shop_id}/ai")
async def shop_ai(request: Request, session: SessionDep, shop_id: int):
    user, access, ctx = await _ctx(request, session)
    shop = await _owned_shop(session, user, shop_id)
    if shop is None:
        return _with_msg("/app/shops", "Магазин не найден", "err")
    try:
        api_key_masked = (
            mask_secret(SecretEncryptor.from_settings().decrypt(shop.ai_api_key_encrypted))
            if shop.ai_api_key_encrypted
            else None
        )
    except ValueError:
        api_key_masked = "••••unreadable"
    ctx.update({"shop": shop, "api_key_masked": api_key_masked})
    return templates.TemplateResponse(request, "shop_ai.html", ctx)


@router.post("/app/shops/{shop_id}/ai")
async def shop_ai_save(
    request: Request,
    session: SessionDep,
    shop_id: int,
    ai_provider: Annotated[str, Form()] = "openai",
    model: Annotated[str, Form()] = "",
    base_url: Annotated[str, Form()] = "",
    api_key: Annotated[str, Form()] = "",
    instructions: Annotated[str, Form()] = "",
    style: Annotated[str, Form()] = "friendly",
    response_length: Annotated[str, Form()] = "short",
    max_characters: Annotated[int, Form()] = 600,
    temperature: Annotated[str, Form()] = "",
    use_emoji: Annotated[str | None, Form()] = None,
    daily_limit: Annotated[int, Form()] = 200,
    negative_requires_approval: Annotated[str | None, Form()] = None,
):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    shop = await _owned_shop(session, user, shop_id)
    if shop is None:
        return _with_msg("/app/shops", "Магазин не найден", "err")

    shop.ai_provider = ai_provider if ai_provider in {"openai", "openai_compatible", "ollama"} else "openai"
    shop.ai_model = model.strip()
    shop.ai_base_url = base_url.strip() or None
    key = api_key.strip()
    if key:
        shop.ai_api_key_encrypted = SecretEncryptor.from_settings().encrypt(key)
    shop.ai_instructions = instructions.strip() or None
    shop.ai_style = style
    shop.ai_response_length = response_length
    shop.ai_max_characters = max(32, min(2000, int(max_characters)))
    raw_temp = temperature.strip()
    shop.ai_temperature = float(raw_temp) if raw_temp else None
    shop.ai_use_emoji = bool(use_emoji)
    shop.ai_daily_limit = max(0, min(10000, int(daily_limit)))
    shop.ai_negative_requires_approval = bool(negative_requires_approval)
    await session.commit()
    return _with_msg(f"/app/shops/{shop_id}/ai", "Настройки ИИ сохранены")


@router.get("/app/reviews")
async def reviews_page(
    request: Request,
    session: SessionDep,
    state: Annotated[str | None, Query()] = None,
    shop_id: Annotated[int | None, Query()] = None,
):
    user, access, ctx = await _ctx(request, session)
    stmt = (
        select(Review)
        .join(Shop, Shop.id == Review.shop_id)
        .where(Shop.owner_id == user.id)
        .order_by(Review.id.desc())
        .limit(100)
    )
    if state:
        stmt = stmt.where(Review.state == state)
    if shop_id is not None:
        stmt = stmt.where(Review.shop_id == shop_id)
    reviews = (await session.execute(stmt)).scalars().all()
    shops = (
        await session.execute(select(Shop).where(Shop.owner_id == user.id))
    ).scalars().all()
    ctx.update(
        {
            "reviews": [_review_dict(review) for review in reviews],
            "shop_names": {shop.id: shop.name for shop in shops},
            "shops": shops,
            "state": state,
            "shop_id": shop_id,
        }
    )
    return templates.TemplateResponse(request, "reviews.html", ctx)


def _review_dict(review: Review) -> dict:
    return {
        "id": review.id,
        "shop_id": review.shop_id,
        "wb_review_id": review.wb_review_id,
        "state": review.state,
        "state_label": STATE_LABELS.get(review.state, review.state),
        "rating": review.rating,
        "text": review.text,
        "generated_text": review.generated_text,
        "error": review.error,
    }


async def _owned_review(session: SessionDep, user: User, shop_id: int, review_id: int) -> Review | None:
    shop = await _owned_shop(session, user, shop_id)
    if shop is None:
        return None
    return (
        await session.execute(
            select(Review).where(Review.id == review_id, Review.shop_id == shop.id)
        )
    ).scalar_one_or_none()


@router.post("/app/shops/{shop_id}/reviews/{review_id}/generate")
async def review_generate(request: Request, session: SessionDep, shop_id: int, review_id: int):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    review = await _owned_review(session, user, shop_id, review_id)
    if review is None:
        return _with_msg("/app/reviews", "Отзыв не найден", "err")
    shop = await _owned_shop(session, user, shop_id)
    try:
        await generate_for_review(session, shop, review)  # type: ignore[arg-type]
        return _with_msg("/app/reviews", "Ответ сгенерирован — проверьте и одобрите")
    except DailyLimitExceeded as exc:
        return _with_msg("/app/reviews", f"Дневной лимит ИИ исчерпан: {exc}", "warn")
    except AIRequestError as exc:
        return _with_msg("/app/reviews", str(exc), "err")
    except Exception as exc:  # noqa: BLE001 — provider/transport; token-free by design
        return _with_msg("/app/reviews", f"Ошибка генерации: {exc.__class__.__name__}", "err")


@router.post("/app/shops/{shop_id}/reviews/{review_id}/edit")
async def review_edit(
    request: Request,
    session: SessionDep,
    shop_id: int,
    review_id: int,
    text: Annotated[str, Form()] = "",
):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    review = await _owned_review(session, user, shop_id, review_id)
    if review is None:
        return _with_msg("/app/reviews", "Отзыв не найден", "err")
    text = text.strip()
    if not text:
        return _with_msg("/app/reviews", "Пустой текст", "err")
    if len(text) > 5000:
        return _with_msg("/app/reviews", "Ответ длиннее 5000 символов", "err")
    review.generated_text = text
    if review.state == "new":
        review.state = "generated"
    await session.commit()
    return _with_msg("/app/reviews", "Текст ответа сохранён")


@router.post("/app/shops/{shop_id}/reviews/{review_id}/approve")
async def review_approve(request: Request, session: SessionDep, shop_id: int, review_id: int):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    review = await _owned_review(session, user, shop_id, review_id)
    if review is None:
        return _with_msg("/app/reviews", "Отзыв не найден", "err")
    if review.state != "generated" or not (review.generated_text or "").strip():
        return _with_msg("/app/reviews", "Одобрять можно только готовый ответ", "err")
    review.state = "approved"
    await session.commit()
    await track(session, event="review.approved", user_id=user.id, shop_id=shop_id)
    return _with_msg("/app/reviews", "Ответ одобрен. Публикация — только по кнопке «Опубликовать».")


@router.post("/app/shops/{shop_id}/reviews/{review_id}/skip")
async def review_skip(request: Request, session: SessionDep, shop_id: int, review_id: int):
    user, access, _ = await _ctx(request, session)
    review = await _owned_review(session, user, shop_id, review_id)
    if review is None:
        return _with_msg("/app/reviews", "Отзыв не найден", "err")
    review.state = "skipped"
    await session.commit()
    await track(session, event="review.skipped", user_id=user.id, shop_id=shop_id)
    return _with_msg("/app/reviews", "Отзыв пропущен")


@router.post("/app/shops/{shop_id}/reviews/{review_id}/publish")
async def review_publish(request: Request, session: SessionDep, shop_id: int, review_id: int):
    user, access, _ = await _ctx(request, session)
    _require_access(access)
    review = await _owned_review(session, user, shop_id, review_id)
    if review is None:
        return _with_msg("/app/reviews", "Отзыв не найден", "err")
    if review.state != "approved":
        return _with_msg("/app/reviews", "Публикация доступна только из статуса «одобрено»", "err")
    shop = await _owned_shop(session, user, shop_id)
    client = build_client_for_shop(shop)  # type: ignore[arg-type]
    try:
        result = await publish_review(session, review, WBReviewsAdapter(client))
        if result.outcome == "published":
            await track(session, event="review.published", user_id=user.id, shop_id=shop_id)
            return _with_msg("/app/reviews", "Ответ опубликован в Wildberries")
        if result.outcome == "remote_already_answered":
            return _with_msg("/app/reviews", "На стороне WB уже есть ответ — отмечен опубликованным")
        if result.outcome == "ambiguous":
            return _with_msg(
                "/app/reviews",
                "Связь оборвалась при публикации: ответ мог быть опубликован. Проверьте статус кнопкой «Синхронизировать» в магазине.",
                "warn",
            )
        return _with_msg("/app/reviews", f"Публикация: {result.outcome}", "warn")
    except Exception as exc:  # noqa: BLE001 — safe class name only
        return _with_msg("/app/reviews", f"WB недоступен: {exc.__class__.__name__}", "err")
    finally:
        await client.aclose()


@router.get("/app/stats")
async def stats_page(request: Request, session: SessionDep):
    user, access, ctx = await _ctx(request, session)
    stats = await client_stats(session, user.id)
    ctx.update({"stats": stats})
    return templates.TemplateResponse(request, "stats.html", ctx)


@router.get("/app/billing")
async def billing_page(request: Request, session: SessionDep):
    user, access, ctx = await _ctx(request, session)
    subscription = (
        await session.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one_or_none()
    ctx.update({"subscription": subscription})
    return templates.TemplateResponse(request, "billing.html", ctx)


@router.post("/app/billing/request")
async def billing_request(request: Request, session: SessionDep, plan: Annotated[str, Form()] = "starter"):
    user, access, _ = await _ctx(request, session)
    logger.info("subscription request", extra={"user_id": user.id, "action": plan})
    return _with_msg("/app/billing", "Запрос отправлен. Оператор свяжется с вами по email.")


@router.get("/app/admin/setup")
async def admin_setup_page(request: Request, session: SessionDep):
    user = await _web_user(request, session)
    admin = await get_or_create_admin_user(session, user.email)
    if admin is None:
        return _with_msg("/app", "Недостаточно прав", "err")
    if admin.totp_enabled:
        return _redirect("/app/admin")
    if not admin.totp_secret_encrypted:
        secret = admin_totp.new_secret()
        admin.totp_secret_encrypted = admin_totp.encrypt_secret(secret)
        await session.commit()
    secret = admin_totp.decrypt_secret(admin.totp_secret_encrypted)
    uri = admin_totp.otpauth_uri(user.email, secret)
    ctx = await _ctx(request, session)
    ctx[2].update({"secret": secret, "qr_svg": _qr_svg(uri), "otpauth_uri": uri, "role": admin.role})
    return templates.TemplateResponse(request, "admin_setup.html", ctx[2])


@router.post("/app/admin/setup")
async def admin_setup_confirm(
    request: Request,
    session: SessionDep,
    code: Annotated[str, Form()] = "",
):
    user = await _web_user(request, session)
    admin = await get_or_create_admin_user(session, user.email)
    if admin is None:
        return _with_msg("/app", "Недостаточно прав", "err")
    if admin.totp_enabled:
        return _redirect("/app/admin")
    if not admin.totp_secret_encrypted:
        return _with_msg("/app/admin/setup", "Секрет не создан, обновите страницу", "err")
    secret = admin_totp.decrypt_secret(admin.totp_secret_encrypted)
    if not admin_totp.verify(secret, code):
        return _with_msg("/app/admin/setup", "Неверный код, попробуйте ещё раз", "err")
    admin.totp_enabled = True
    await session.commit()
    response = _with_msg("/app/admin", "2FA включена")
    _set_admin_2fa_cookie(response, admin.id)
    return response


@router.get("/app/admin/2fa")
async def admin_2fa_page(request: Request, session: SessionDep):
    user = await _web_user(request, session)
    admin = await get_or_create_admin_user(session, user.email)
    if admin is None:
        return _with_msg("/app", "Недостаточно прав", "err")
    if not admin.totp_enabled:
        return _redirect("/app/admin/setup")
    if admin_2fa_cookie_valid(request, admin):
        return _redirect("/app/admin")
    ctx = await _ctx(request, session)
    ctx[2].update({"role": admin.role})
    return templates.TemplateResponse(request, "admin_2fa.html", ctx[2])


@router.post("/app/admin/2fa")
async def admin_2fa_verify(
    request: Request,
    session: SessionDep,
    code: Annotated[str, Form()] = "",
):
    user = await _web_user(request, session)
    admin = await get_or_create_admin_user(session, user.email)
    if admin is None:
        return _with_msg("/app", "Недостаточно прав", "err")
    if not admin.totp_enabled:
        return _redirect("/app/admin/setup")
    secret = admin_totp.decrypt_secret(admin.totp_secret_encrypted)
    if not admin_totp.verify(secret, code):
        return _with_msg("/app/admin/2fa", "Неверный код", "err")
    response = _redirect("/app/admin")
    _set_admin_2fa_cookie(response, admin.id)
    return response


@router.get("/app/admin")
async def admin_page(request: Request, session: SessionDep):
    user, access, ctx = await _ctx(request, session)
    admin = await get_or_create_admin_user(session, user.email)
    if admin is None:
        return _with_msg("/app", "Недостаточно прав", "err")
    if not admin.totp_enabled:
        return _redirect("/app/admin/setup")
    if not admin_2fa_cookie_valid(request, admin):
        return _redirect("/app/admin/2fa")
    users_total = (await session.execute(select(func.count(User.id)))).scalar_one()
    shops_total = (await session.execute(select(func.count(Shop.id)))).scalar_one()
    plan_counts = dict(
        (await session.execute(select(Subscription.plan, func.count(Subscription.id)).group_by(Subscription.plan))).all()
    )
    users = (
        await session.execute(select(User).order_by(User.id.desc()).limit(50))
    ).scalars().all()
    subs = {
        row.user_id: row
        for row in (
            await session.execute(select(Subscription).where(Subscription.user_id.in_([u.id for u in users])))
        ).scalars()
    }
    rows = []
    for u in users:
        s = subs.get(u.id)
        rows.append(
            {
                "id": u.id,
                "email": u.email,
                "is_active": u.is_active,
                "subscription": None
                if s is None
                else {
                    "plan": s.plan,
                    "status": s.status,
                    "current_period_end": s.current_period_end.isoformat() if s.current_period_end else None,
                },
            }
        )
    audit_entries = (
        await session.execute(select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(10))
    ).scalars().all()
    sales = await admin_sales(session)
    admin = await get_or_create_admin_user(session, user.email)
    if admin is not None:
        ctx["admin_role"] = admin.role
    ctx.update(
        {
            "summary": {"users_total": users_total, "shops_total": shops_total, "plans": plan_counts},
            "rows": rows,
            "audit_entries": audit_entries,
            "sales": sales,
        }
    )
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.post("/app/admin/users/{user_id}/activate")
async def admin_activate(
    request: Request,
    session: SessionDep,
    user_id: int,
    plan: Annotated[str, Form()] = "starter",
    months: Annotated[int, Form()] = 1,
):
    user, access, _ = await _ctx(request, session)
    user, admin = await _admin_guard(request, session)
    if admin.role != "owner":
        return _with_msg("/app/admin", "Роль support: доступ только для просмотра", "err")
    try:
        await activate_subscription(
            session,
            actor_id=user.id,
            target_user_id=user_id,
            plan=plan if plan in {"starter", "pro", "business"} else "starter",
            months=max(1, min(24, int(months))),
            ip=request.client.host if request.client else None,
        )
    except ValueError as exc:
        return _with_msg("/app/admin", str(exc), "err")
    return _with_msg("/app/admin", f"Пользователь #{user_id} активирован ({plan}, {months} мес.)")


@router.post("/app/admin/users/{user_id}/toggle")
async def admin_toggle(
    request: Request,
    session: SessionDep,
    user_id: int,
    active: Annotated[str, Form()] = "false",
):
    user, admin = await _admin_guard(request, session)
    if admin.role != "owner":
        return _with_msg("/app/admin", "Роль support: доступ только для просмотра", "err")
    target = await session.get(User, user_id)
    if target is None:
        return _with_msg("/app/admin", "Пользователь не найден", "err")
    if role_for_email(target.email) == "owner":
        return _with_msg("/app/admin", "Нельзя деактивировать owner", "err")
    target.is_active = str(active).lower() == "true"
    await record_admin_action(
        session,
        actor_id=user.id,
        action="user.reactivate" if target.is_active else "user.deactivate",
        target=f"user:{target.id}",
        detail={"is_active": target.is_active},
        ip=request.client.host if request.client else None,
    )
    return _with_msg("/app/admin", f"Пользователь #{user_id}: {'включён' if target.is_active else 'отключён'}")
