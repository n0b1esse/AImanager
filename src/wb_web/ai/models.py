"""Provider-neutral AI request and response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class ReviewContext:
    store_name: str
    rating: int
    text: str = ""
    brand_name: str | None = None
    product_name: str | None = None
    vendor_code: str | None = None
    pros: str | None = None
    cons: str | None = None
    buyer_question: str | None = None


@dataclass(slots=True, frozen=True)
class AIProfile:
    provider: str = "ollama"
    model: str = "gemma3:4b"
    style: str = "friendly"
    response_length: str = "short"
    max_characters: int = 600
    temperature: float | None = 0.7
    use_emoji: bool = False
    mention_product: bool = False
    mention_brand: bool = False
    add_store_signature: bool = False
    avoid_repetitions: bool = True
    negative_requires_approval: bool = False
    custom_instructions: str = ""
    recent_answers: tuple[str, ...] = field(default_factory=tuple)
    # Niche communication packs resolved by the shop (tuple[NichePack, ...])
    packs: tuple = ()


@dataclass(slots=True, frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True, frozen=True)
class GenerationResult:
    text: str
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

