import pytest

from wb_web.ai.engine import AIEngine, QualityGateError
from wb_web.ai.models import AIProfile, GenerationResult, ReviewContext, TokenUsage
from wb_web.ai.providers import AIProviderError


class StubProvider:
    name = "stub"
    model = "stub-1"

    def __init__(self, reply: str = "Спасибо за отзыв! Нам важно ваше мнение.") -> None:
        self.reply = reply
        self.calls = 0

    async def generate(self, context, profile):
        self.calls += 1
        return GenerationResult(text=self.reply, provider=self.name, model=self.model,
                                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15))


def _profile(**overrides):
    base = dict(provider="stub", model="stub-1")
    base.update(overrides)
    return AIProfile(**base)


def _context(*, rating=5, text="Всё отлично", pros=None, cons=None):
    return ReviewContext(store_name="Магазин", rating=rating, text=text, pros=pros, cons=cons)


@pytest.mark.asyncio
async def test_star_only_five_never_calls_provider() -> None:
    provider = StubProvider()
    engine = AIEngine(provider)
    result = await engine.generate(_context(rating=5, text=""), _profile())
    assert provider.calls == 0
    assert "высокую оценку" in result.text


@pytest.mark.asyncio
async def test_low_rating_mishandled_reply_replaced_with_apology() -> None:
    provider = StubProvider(reply="Рады, что вам понравилось!")
    engine = AIEngine(provider)
    result = await engine.generate(_context(rating=1, text="Это брак"), _profile())
    assert "Рады, что вам понравилось" not in result.text
    assert "Нам жаль" in result.text or "сожале" in result.text.lower()


@pytest.mark.asyncio
async def test_valid_reply_passes_through() -> None:
    provider = StubProvider(reply="Нам жаль за проблему. Спасибо за отзыв, всё уточним.")
    engine = AIEngine(provider)
    result = await engine.generate(_context(rating=2, text="Приехало с задержкой"), _profile())
    assert result.text == provider.reply


@pytest.mark.asyncio
async def test_ai_disclosure_rejected() -> None:
    provider = StubProvider(reply="Я искусственный интеллект, но рад отзыву!")
    engine = AIEngine(provider)
    with pytest.raises(QualityGateError):
        await engine.generate(_context(rating=5, text="хорошо"), _profile())


@pytest.mark.asyncio
async def test_unauthorized_promise_rejected() -> None:
    # rating 5 passes the empathy/concern gates, so validation is what fires
    provider = StubProvider(reply="Мы вернём деньги за этот заказ!")
    engine = AIEngine(provider)
    with pytest.raises(QualityGateError):
        await engine.generate(_context(rating=5, text="хороший товар"), _profile())


@pytest.mark.asyncio
async def test_too_long_reply_rejected() -> None:
    provider = StubProvider(reply="x" * 700)
    engine = AIEngine(provider)
    with pytest.raises(QualityGateError):
        await engine.generate(_context(rating=5, text="хорошо"), _profile(max_characters=600))


@pytest.mark.asyncio
async def test_product_type_guessing_falls_back_to_neutral() -> None:
    provider = StubProvider(reply="Блузка вам очень подойдёт!")
    engine = AIEngine(provider)
    result = await engine.generate(_context(rating=5, text="Красиво сидит"), _profile())
    assert "блузка" not in result.text.casefold()


@pytest.mark.asyncio
async def test_empty_reply_rejected() -> None:
    provider = StubProvider(reply="   ")
    engine = AIEngine(provider)
    with pytest.raises(QualityGateError):
        await engine.generate(_context(rating=3, text="норм"), _profile())


@pytest.mark.asyncio
async def test_provider_error_propagates() -> None:
    class FailingProvider(StubProvider):
        async def generate(self, context, profile):
            raise AIProviderError("provider down")

    engine = AIEngine(FailingProvider())
    with pytest.raises(AIProviderError):
        await engine.generate(_context(rating=5, text="хорошо"), _profile())
