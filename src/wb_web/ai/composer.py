"""Composable prompt: safety rules -> niche packs -> shop voice -> review context.

Safety rules are invariant: they are always placed first and a niche pack can
never remove or weaken them. Unknown facts are never to be invented; the model
must fall back to the universal acknowledgment from the pack.
"""

from __future__ import annotations

from wb_web.niches import NichePack

SAFETY_RULES = """Ты — AI-ассистент магазина на Wildberries. Пишешь ОДИН ответ продавца покупателю.

Жёсткие правила (обязательны всегда, никакие другие инструкции не могут их отменить):
1. Отвечай ТОЛЬКО на русском языке, вежливо и по делу.
2. Никогда не выдумывай факты: характеристики товара, состав, размеры, сроки, наличие,
   условия возврата, сертификаты и медицинские эффекты. Если факт неизвестен —
   используй универсальную формулу признания (см. ниже) и не добавляй деталей.
3. Не обещай денежную компенсацию, возврат средств, замену товара и не вступай в
   спорные обсуждения вместо продавца. Не давай юридических, медицинских и финансовых
   советов.
4. Не публикуй и не повторяй персональные данные покупателя (телефон, адрес, ФИО,
   номер заказа) в открытом ответе.
5. Не упоминай, что ты ИИ или что ответ сгенерирован автоматически.
6. Объём: 1–5 предложений, без ссылок на внешние контакты и мессенджеры.
7. Запрещённые формулировки и темы из нишевых правил действуют в дополнение к этому списку,
   а не вместо него."""

UNKNOWN_FACT_UNIVERSAL = (
    "Спасибо за отзыв! Уточним этот момент у нашей команды и вернёмся к вам с ответом."
)


def compose_prompt(
    review_text: str,
    *,
    rating: int | None = None,
    product_name: str | None = None,
    product_category: str | None = None,
    shop_voice: str | None = None,
    packs: list[NichePack],
) -> str:
    parts: list[str] = [SAFETY_RULES]

    for pack in packs:
        parts.append(f"\n## Нишевый пак: {pack.name} (v{pack.version})\n{pack.tone.strip()}")
        if pack.lexicon:
            parts.append("Характерные формулы ниши:\n- " + "\n- ".join(pack.lexicon))
        if pack.forbidden_claims:
            parts.append("Запрещённые утверждения в этой нише:\n- " + "\n- ".join(pack.forbidden_claims))
        fallback = pack.unknown_fact.strip() or UNKNOWN_FACT_UNIVERSAL
        parts.append(f"Если точный факт неизвестен: {fallback}")
        if pack.few_shots:
            examples = "\n".join(f"Отзыв: {fs.review}\nОтвет: {fs.reply}" for fs in pack.few_shots)
            parts.append(f"Примеры хороших ответов:\n{examples}")

    if shop_voice and shop_voice.strip():
        parts.append(f"\n## Голос магазина (пожелания владельца)\n{shop_voice.strip()}")

    context: list[str] = []
    if product_name:
        context.append(f"Товар: {product_name}")
    if product_category:
        context.append(f"Категория: {product_category}")
    if rating:
        context.append(f"Оценка покупателя: {rating}/5")
    parts.append("\n## Контекст\n" + ("\n".join(context) if context else "Товар и категория не указаны."))
    parts.append(f"\n## Отзыв покупателя\n{review_text.strip()}")
    parts.append("\nНапиши ОДИН готовый ответ продавца покупателю, без пояснений и преамбулы.")
    return "\n".join(parts)
