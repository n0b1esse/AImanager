"""Email delivery.

Backends:
- smtp (when SMTP_HOST is set): real delivery via aiosmtplib (STARTTLS by default)
- console (default dev): prints the message to logs so the flow is testable without a mailbox

Never log secrets; the magic link is a one-time bearer token — treat it like one.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from wb_web.core.config import get_settings

logger = logging.getLogger("wb_web.mailer")


def build_magic_link_email(*, to: str, link: str, ttl_minutes: int) -> EmailMessage:
    message = EmailMessage()
    message["From"] = get_settings().mail_from
    message["To"] = to
    message["Subject"] = "Вход в AImanager"
    message.set_content(
        f"""Здравствуйте!

Вы запросили вход в AImanager. Ссылка одноразовая и действует {ttl_minutes} минут:

{link}

Если вы не запрашивали вход — просто проигнорируйте это письмо.

— AImanager
"""
    )
    return message


async def send_email(message: EmailMessage) -> str:
    settings = get_settings()
    if not settings.smtp_host:
        # Console backend: visible in server logs for local development
        logger.warning(
            "[MAIL console] to=%s subject=%r body_preview=%r",
            message.get("To"),
            message.get("Subject"),
            (message.get_content() or "")[:400],
        )
        return "console"

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=settings.smtp_starttls,
        timeout=15,
    )
    logger.info("email sent", extra={"action": message.get("Subject")})
    return "smtp"


async def send_magic_link(*, to: str, link: str, ttl_minutes: int) -> str:
    message = build_magic_link_email(to=to, link=link, ttl_minutes=ttl_minutes)
    return await send_email(message)
