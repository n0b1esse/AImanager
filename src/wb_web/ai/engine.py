"""High-level response generation and deterministic safety checks."""

from __future__ import annotations

import re

from wb_web.ai.models import AIProfile, GenerationResult, ReviewContext
from wb_web.ai.prompt import contains_product_type
from wb_web.ai.providers import BaseAIProvider
from wb_web.ai.understanding import (
    analyze_review,
    has_explicit_positive_feedback,
    reply_addresses_concerns,
)


class QualityGateError(RuntimeError):
    pass


_AI_DISCLOSURE = re.compile(r"\b(искусственн\w+ интеллект|нейросет\w+|я\s+как\s+ии|language model)\b", re.I)
_UNAUTHORIZED_PROMISE = re.compile(
    r"\b(верн[её]м\s+деньги|оформим\s+возврат|компенсиру\w+|заменим\s+товар|пришл[её]м\s+замену)\b",
    re.I,
)
_DANGEROUS_REVIEW = re.compile(
    r"\b(мошеннич\w+|суд\w*|прокуратур\w*|полици\w*|адвокат\w*|иск\w*|"
    r"опасн\w*|травм\w*|ожог\w*|отрав\w*|загорел\w*|брак\w*|подделк\w*|"
    r"возврат\w*\s+денег|не\s+вернули\s+деньги)\b",
    re.I,
)
_APOLOGY_OR_REGRET = re.compile(r"\b(нам\s+жаль|мне\s+жаль|сожалеем|извиняемся|приносим\s+извинени\w*)\b", re.I)
_CHEERFUL_REPLY = re.compile(r"\b(рады,?\s+что|понравил\w*|довольн\w*)\b", re.I)


def _universal_fallback(context: ReviewContext, profile: AIProfile) -> str:
    signals = analyze_review(context)
    if context.rating <= 2 or signals.contains_negative_feedback:
        options = {
            "very_short": "Нам жаль, что ваш опыт не оправдал ожиданий.",
            "short": "Нам жаль, что ваш опыт не оправдал ожиданий. Спасибо за обратную связь.",
            "medium": "Нам жаль, что ваш опыт не оправдал ожиданий. Спасибо, что поделились отзывом. Ваше мнение важно для нас.",
            "detailed": "Нам жаль, что ваш опыт не оправдал ожиданий. Спасибо, что сообщили о проблеме. Мы внимательно относимся к отзывам покупателей. Ваше мнение важно для нас.",
        }
    else:
        options = {
            "very_short": "Спасибо за отзыв!",
            "short": "Спасибо за ваш отзыв. Нам важно ваше мнение.",
            "medium": "Спасибо, что поделились впечатлением. Нам важно ваше мнение. Мы внимательно относимся к обратной связи.",
            "detailed": "Спасибо, что поделились впечатлением. Нам важно каждое мнение. Мы внимательно относимся к отзывам и благодарны вам за обратную связь.",
        }
    order = (profile.response_length, "short", "very_short")
    for length in order:
        text = options[length]
        if len(text) <= profile.max_characters:
            return text
    return options["very_short"]


def _concern_fallback(context: ReviewContext, profile: AIProfile) -> str:
    """Give a safe, product-neutral reply that acknowledges explicit issues."""
    signals = analyze_review(context)
    phrases = {
        "прозрачность материала": "прозрачностью материала",
        "недостаточный утягивающий эффект": "недостаточным утягивающим эффектом",
        "электризация и притягивание ворса": (
            "электризацией материала, из-за которой к нему липнет ворс"
        ),
        "неподходящая посадка или размер": "неподходящей посадкой или размером",
        "дефект или плохое качество": "дефектом или качеством",
        "неприятный запах": "неприятным запахом",
        "неполная комплектация": "неполной комплектацией",
        "неисправность": "неисправностью",
    }
    concerns = [phrases[item] for item in signals.concerns]
    if len(concerns) == 1:
        topics = concerns[0]
    elif len(concerns) == 2:
        topics = f"{concerns[0]} и {concerns[1]}"
    else:
        topics = f"{', '.join(concerns[:-1])} и {concerns[-1]}"

    options = {
        "very_short": f"Нам жаль, что вы столкнулись с {concerns[0]}.",
        "short": (
            f"Нам жаль, что вы столкнулись с {topics}. "
            "Спасибо за конкретные замечания."
        ),
        "medium": (
            f"Нам жаль, что вы столкнулись с {topics}. "
            "Спасибо, что подробно описали эти моменты. Ваш отзыв важен для нас."
        ),
        "detailed": (
            f"Нам жаль, что вы столкнулись с {topics}. "
            "Спасибо, что подробно описали эти моменты. Такая обратная связь помогает понять, "
            "что именно вас смутило."
        ),
    }
    order = (profile.response_length, "short", "very_short", "medium", "detailed")
    for length in dict.fromkeys(order):
        text = options[length]
        if len(text) <= profile.max_characters:
            return text
    return options["very_short"]


def requires_manual_review(context: ReviewContext, profile: AIProfile) -> bool:
    if profile.negative_requires_approval and context.rating <= 3:
        return True
    combined = " ".join(
        value for value in (context.text, context.pros, context.cons, context.buyer_question) if value
    )
    return bool(_DANGEROUS_REVIEW.search(combined))


def validate_generated_text(
    text: str,
    profile: AIProfile,
    context: ReviewContext | None = None,
) -> str:
    clean = text.strip().strip('"«»')
    if not clean:
        raise QualityGateError("ИИ вернул пустой ответ")
    if len(clean) > profile.max_characters:
        raise QualityGateError(
            f"Ответ длиннее разрешённого лимита: {len(clean)} из {profile.max_characters} символов"
        )
    if _AI_DISCLOSURE.search(clean):
        raise QualityGateError("Ответ раскрывает использование искусственного интеллекта")
    if _UNAUTHORIZED_PROMISE.search(clean):
        raise QualityGateError("Ответ содержит неразрешённое обещание возврата или компенсации")
    if not profile.use_emoji and any(ord(char) > 0xFFFF for char in clean):
        raise QualityGateError("Ответ содержит эмодзи, хотя они отключены")
    if context is not None:
        compact_answer = re.sub(r"[\W_]+", "", clean.casefold())
        identifiers = [context.vendor_code]
        product_name = (context.product_name or "").strip()
        # A short category can be allowed in the answer; only block full card
        # titles so the shop can still say e.g. "блузка" when appropriate.
        if len(product_name.split()) >= 2:
            identifiers.append(product_name)
        for value in identifiers:
            compact_value = re.sub(r"[\W_]+", "", (value or "").casefold())
            if len(compact_value) >= 5 and compact_value in compact_answer:
                raise QualityGateError(
                    "Ответ содержит артикул или полное название карточки товара"
                )
        if contains_product_type(clean):
            raise QualityGateError(
                "Ответ называет тип товара; используйте универсальную формулировку"
            )
    return clean


class AIEngine:
    def __init__(self, provider: BaseAIProvider) -> None:
        self.provider = provider

    async def generate(self, context: ReviewContext, profile: AIProfile) -> GenerationResult:
        if not 1 <= context.rating <= 5:
            raise ValueError("Rating must be between 1 and 5")
        has_review_details = any(
            value and value.strip()
            for value in (context.text, context.pros, context.cons, context.buyer_question)
        )
        if context.rating == 5 and not has_review_details:
            # A star-only 5-star review contains no issue to investigate. Do
            # not ask the buyer for details or let an LLM misread the rating.
            answer = "Спасибо за высокую оценку! Рады, что вам понравилось."
            clean = validate_generated_text(answer, profile, context)
            return GenerationResult(
                text=clean,
                provider=self.provider.name,
                model=self.provider.model,
            )
        result = await self.provider.generate(context, profile)
        signals = analyze_review(context)
        missing_empathy = context.rating <= 2 and (
            not _APOLOGY_OR_REGRET.search(result.text)
            or _CHEERFUL_REPLY.search(result.text)
        )
        has_supported_positive_note = has_explicit_positive_feedback(context)
        mishandles_negative_feedback = signals.contains_negative_feedback and (
            not _APOLOGY_OR_REGRET.search(result.text)
            or (_CHEERFUL_REPLY.search(result.text) and not has_supported_positive_note)
        )
        misses_explicit_concerns = not reply_addresses_concerns(result.text, signals)
        if (
            contains_product_type(result.text)
            or missing_empathy
            or mishandles_negative_feedback
            or misses_explicit_concerns
        ):
            # Small local models may hallucinate an item or misread a low
            # rating. Replace that response with wording grounded in all
            # explicitly detected concerns instead of publishing an unrelated answer.
            fallback = (
                _concern_fallback(context, profile)
                if signals.concerns
                else _universal_fallback(context, profile)
            )
            clean = validate_generated_text(fallback, profile, context)
        else:
            clean = validate_generated_text(result.text, profile, context)
        if clean == result.text:
            return result
        return GenerationResult(
            text=clean,
            provider=result.provider,
            model=result.model,
            usage=result.usage,
            latency_ms=result.latency_ms,
            raw_metadata=result.raw_metadata,
        )

    async def close(self) -> None:
        await self.provider.close()

