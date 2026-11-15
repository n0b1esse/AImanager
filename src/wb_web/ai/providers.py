"""Async providers for OpenAI, OpenAI-compatible APIs, and Ollama."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from wb_web.ai.models import AIProfile, GenerationResult, ReviewContext, TokenUsage
from wb_web.ai.prompt import build_input, build_instructions


class AIProviderError(RuntimeError):
    """Provider error safe to show without leaking credentials."""


class BaseAIProvider(ABC):
    name: str

    def __init__(self, *, model: str, timeout: float = 90.0, client: httpx.AsyncClient | None = None):
        self.model = model
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    @abstractmethod
    async def generate(self, context: ReviewContext, profile: AIProfile) -> GenerationResult:
        raise NotImplementedError

    async def check(self) -> None:
        profile = AIProfile(provider=self.name, model=self.model, max_characters=80, temperature=0)
        context = ReviewContext(store_name="Проверка подключения", rating=5, text="Всё хорошо")
        await self.generate(context, profile)


def _usage(payload: dict[str, Any], *, responses_api: bool = False) -> TokenUsage:
    if responses_api:
        input_tokens = int(payload.get("input_tokens") or 0)
        output_tokens = int(payload.get("output_tokens") or 0)
    else:
        input_tokens = int(payload.get("prompt_tokens") or 0)
        output_tokens = int(payload.get("completion_tokens") or 0)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(payload.get("total_tokens") or input_tokens + output_tokens),
    )


def _raise_provider_error(response: httpx.Response) -> None:
    try:
        body = response.json()
        detail = body.get("error", {}).get("message") or body.get("message") or response.reason_phrase
    except (ValueError, AttributeError):
        detail = response.reason_phrase
    if response.status_code in {401, 403}:
        detail = "ключ отклонён или не имеет необходимых прав"
    elif response.status_code == 429:
        detail = "достигнут лимит запросов провайдера"
    raise AIProviderError(f"ИИ-провайдер вернул HTTP {response.status_code}: {detail}")


class OpenAIProvider(BaseAIProvider):
    """Official OpenAI Responses API provider."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 90.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(model=model, timeout=timeout, client=client)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def generate(self, context: ReviewContext, profile: AIProfile) -> GenerationResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": build_instructions(profile),
            "input": build_input(context, profile),
            "max_output_tokens": max(64, min(1024, profile.max_characters * 2)),
            "store": False,
        }
        if profile.temperature is not None:
            payload["temperature"] = profile.temperature
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{self.base_url}/responses",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Не удалось подключиться к OpenAI: {exc.__class__.__name__}") from exc
        if response.is_error:
            _raise_provider_error(response)
        body = response.json()
        text = body.get("output_text") or ""
        if not text:
            for item in body.get("output", []):
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        text += content.get("text", "")
        if not text.strip():
            raise AIProviderError("OpenAI не вернул текст ответа")
        return GenerationResult(
            text=text.strip(),
            provider=self.name,
            model=self.model,
            usage=_usage(body.get("usage") or {}, responses_api=True),
            latency_ms=round((time.perf_counter() - started) * 1000),
            raw_metadata={"response_id": body.get("id")},
        )


class OpenAICompatibleProvider(BaseAIProvider):
    """Provider implementing the de-facto OpenAI chat-completions contract."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 90.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(model=model, timeout=timeout, client=client)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def generate(self, context: ReviewContext, profile: AIProfile) -> GenerationResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_instructions(profile)},
                {"role": "user", "content": build_input(context, profile)},
            ],
            "max_tokens": max(64, min(1024, profile.max_characters * 2)),
            "stream": False,
        }
        if profile.temperature is not None:
            payload["temperature"] = profile.temperature
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Не удалось подключиться к ИИ-провайдеру: {exc.__class__.__name__}") from exc
        if response.is_error:
            _raise_provider_error(response)
        body = response.json()
        try:
            text = body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AIProviderError("Совместимый API вернул неожиданный формат") from exc
        return GenerationResult(
            text=text,
            provider=self.name,
            model=self.model,
            usage=_usage(body.get("usage") or {}),
            latency_ms=round((time.perf_counter() - started) * 1000),
            raw_metadata={"response_id": body.get("id")},
        )


class OllamaProvider(BaseAIProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(model=model, timeout=timeout, client=client)
        self.base_url = base_url.rstrip("/")

    async def generate(self, context: ReviewContext, profile: AIProfile) -> GenerationResult:
        # Review replies are short; the former 2x character budget allowed
        # Ollama to spend a long time producing unused continuation tokens.
        options: dict[str, Any] = {
            "num_predict": max(80, min(256, round(profile.max_characters * 0.45)))
        }
        if profile.temperature is not None:
            options["temperature"] = profile.temperature
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "think": False,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": build_instructions(profile)},
                        {"role": "user", "content": build_input(context, profile)},
                    ],
                    "options": options,
                },
            )
        except httpx.HTTPError as exc:
            raise AIProviderError("Ollama недоступна; проверьте, что локальный сервер запущен") from exc
        if response.is_error:
            _raise_provider_error(response)
        body = response.json()
        text = str((body.get("message") or {}).get("content") or "").strip()
        if not text:
            raise AIProviderError("Ollama не вернула текст ответа")
        input_tokens = int(body.get("prompt_eval_count") or 0)
        output_tokens = int(body.get("eval_count") or 0)
        return GenerationResult(
            text=text,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(input_tokens, output_tokens, input_tokens + output_tokens),
            latency_ms=round((time.perf_counter() - started) * 1000),
            raw_metadata={"done_reason": body.get("done_reason")},
        )

