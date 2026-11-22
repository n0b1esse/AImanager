import asyncio

from wb_web.ai.managed import build_managed_provider
from wb_web.ai.models import AIProfile, GenerationResult, ReviewContext, TokenUsage
from wb_web.ai.providers import AIProviderError
from wb_web.db.base import get_sessionmaker
from wb_web.db.models import LLMProvider


class _FakeProvider:
    def __init__(self, *, api_key: str, model: str, base_url: str, client=None) -> None:
        self.model = model

    async def generate(self, context: ReviewContext, profile: AIProfile) -> GenerationResult:
        if self.model == "bad":
            raise AIProviderError("429 rate limited")
        return GenerationResult(
            text="Ответ", provider="fake", model=self.model, usage=TokenUsage(total_tokens=7)
        )


def test_managed_provider_falls_through(monkeypatch) -> None:
    monkeypatch.setattr("wb_web.ai.managed.OpenAICompatibleProvider", _FakeProvider)

    async def _run() -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            session.add(
                LLMProvider(
                    label="first", base_url="http://a/v1", api_key_encrypted="",
                    models=["bad"], priority=0, enabled=True,
                )
            )
            session.add(
                LLMProvider(
                    label="second", base_url="http://b/v1", api_key_encrypted="",
                    models=["good"], priority=1, enabled=True,
                )
            )
            await session.commit()
            provider = await build_managed_provider(session)
            result = await provider.generate(
                ReviewContext(store_name="Магазин", rating=5, text="Всё отлично"), AIProfile()
            )
            assert result.model == "good"
            assert result.usage.total_tokens == 7

    asyncio.run(_run())


def test_managed_provider_without_endpoints_raises() -> None:
    async def _run() -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            try:
                await build_managed_provider(session)
            except AIProviderError as exc:
                assert "не настроен" in str(exc)
            else:
                raise AssertionError("expected AIProviderError")

    asyncio.run(_run())
