"""Small, deterministic signals for grounding replies in explicit feedback."""

from __future__ import annotations

import re
from dataclasses import dataclass

from wb_web.ai.models import ReviewContext


@dataclass(frozen=True, slots=True)
class ReviewSignals:
    concerns: tuple[str, ...]
    contains_negative_feedback: bool


_CONCERN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "прозрачность материала",
        re.compile(r"\b(?:просвеч\w*|прозрачн\w*)\b|\bвидно\s+(?:белье|тело|кожу)\b", re.I),
    ),
    (
        "недостаточный утягивающий эффект",
        re.compile(r"\b(?:утяжк\w*|утягива\w*|утяг\w*)\b", re.I),
    ),
    (
        "электризация и притягивание ворса",
        re.compile(
            r"\b(?:статическ\w*|электриз\w*)\b|"
            r"\b(?:собирает|цепляет|притягивает)\b.{0,45}\b(?:все|пыль|шерсть|волос\w*|ворс|мусор)\b",
            re.I,
        ),
    ),
    (
        "неподходящая посадка или размер",
        re.compile(r"\b(?:не\s+подош\w*|маломер\w*|большемер\w*|жмет|тесн\w*|велик\w*)\b", re.I),
    ),
    (
        "дефект или плохое качество",
        re.compile(r"\b(?:брак\w*|дефект\w*|плох\w+\s+качеств\w*|порвал\w*|сломал\w*)\b", re.I),
    ),
    (
        "неприятный запах",
        re.compile(r"\b(?:резк\w*|неприятн\w*|химическ\w*)\s+запах\w*|\bвоня\w*", re.I),
    ),
    (
        "неполная комплектация",
        re.compile(r"\b(?:не\s+хватает|не\s+положили|отсутств\w*|неполна\w*)\b.{0,40}\b(?:комплект\w*|детал\w*|част\w*)", re.I),
    ),
    (
        "неисправность",
        re.compile(r"\b(?:не\s+работа\w*|перестал\w*\s+работа\w*|неисправн\w*)\b", re.I),
    ),
)

_NEGATIVE_FEEDBACK = re.compile(
    r"\b(?:плох\w*|ужасн\w*|разочарован\w*|недовольн\w*|неудобн\w*|"
    r"не\s+(?:понрав\w*|подош\w*|оправда\w*|удобн\w*|рекоменд\w*)|"
    r"некачественн\w*|недостаточн\w*|отказ\w*)\b",
    re.I,
)


def _is_negated(text: str, start: int) -> bool:
    """Ignore a detected fault when the buyer explicitly says it is absent."""
    prefix = text[max(0, start - 16) : start]
    return bool(re.search(r"\bне\s+(?:особо\s+)?$", prefix, re.I))


def analyze_review(context: ReviewContext) -> ReviewSignals:
    """Extract only explicit concern categories; never infer a product type."""
    fields = (context.text, context.pros or "", context.cons or "", context.buyer_question or "")
    text = " ".join(value for value in fields if value).casefold().replace("ё", "е")
    concerns: list[str] = []
    for label, pattern in _CONCERN_PATTERNS:
        if any(match and not _is_negated(text, match.start()) for match in pattern.finditer(text)):
            concerns.append(label)
    negative = bool(concerns or _NEGATIVE_FEEDBACK.search(text))
    return ReviewSignals(tuple(concerns), negative)


def reply_addresses_concerns(reply: str, signals: ReviewSignals) -> bool:
    """Require the reply to acknowledge each explicit concern category."""
    if not signals.concerns:
        return True
    answer = reply.casefold().replace("ё", "е")
    for concern, pattern in _CONCERN_PATTERNS:
        if concern in signals.concerns and not pattern.search(answer):
            return False
    return True


_EXPLICIT_POSITIVE_FEEDBACK = re.compile(
    r"\b(?:понравил\w*|красив\w*|отличн\w*|прекрасн\w*|хорош\w*|"
    r"качественн\w*|удобн\w*|рекоменд\w*|довольн\w*|супер\w*)\b",
    re.I,
)


def has_explicit_positive_feedback(context: ReviewContext) -> bool:
    """Whether the buyer's own words support a positive note in the reply."""
    fields = (context.text, context.pros or "", context.cons or "")
    text = " ".join(value for value in fields if value).casefold().replace("ё", "е")
    for match in _EXPLICIT_POSITIVE_FEEDBACK.finditer(text):
        if not _is_negated(text, match.start()):
            return True
    return False
