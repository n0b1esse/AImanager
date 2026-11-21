"""Plan catalog shared by templates and the signup flow.

Internal keys stay ``starter | pro | business`` — they are already stored in
subscriptions, used by admin actions and covered by tests. What changes is the
customer-facing meaning: the axis is the number of shop cabinets, not answer
volume.
"""

from __future__ import annotations

PLANS: dict[str, dict[str, str]] = {
    "starter": {
        "name": "Старт",
        "cabinets": "1–3",
        "cabinet_label": "кабинета",
        "tagline": "Для одного продавца или небольшой команды",
        "answers": "300 ответов в месяц",
    },
    "pro": {
        "name": "Рост",
        "cabinets": "4–5",
        "cabinet_label": "кабинетов",
        "tagline": "Когда магазинов стало несколько",
        "answers": "1 500 ответов в месяц",
    },
    "business": {
        "name": "Масштаб",
        "cabinets": "6+",
        "cabinet_label": "кабинетов",
        "tagline": "Для агентств и крупных каталогов",
        "answers": "5 000 ответов в месяц",
    },
}

DEFAULT_PLAN = "starter"


def normalize_plan(value: str | None) -> str | None:
    """Return a known plan key or None. Never trust a raw query/form value."""
    if not value:
        return None
    key = value.strip().lower()
    return key if key in PLANS else None


def get_plan(key: str | None) -> dict[str, str]:
    return PLANS[normalize_plan(key) or DEFAULT_PLAN]
