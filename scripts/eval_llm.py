"""Golden-set evaluation for a managed (OpenAI-compatible) LLM endpoint.

Usage:
  EVAL_BASE_URL=https://api.z.ai/api/paas/v4 \
  EVAL_KEY=... EVAL_MODEL=glm-4.7-flash EVAL_LABEL=zai-glm-flash \
  python scripts/eval_llm.py

Runs the real production prompt + quality gate from wb_web.ai, over 20
Russian marketplace reviews. Prints a per-case table and a JSON summary.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from statistics import mean

import httpx

from wb_web.ai.engine import (
    _APOLOGY_OR_REGRET,
    _CHEERFUL_REPLY,
    QualityGateError,
    _concern_fallback,
    _universal_fallback,
    requires_manual_review,
    validate_generated_text,
)
from wb_web.ai.models import AIProfile, ReviewContext
from wb_web.ai.prompt import build_instructions, contains_product_type
from wb_web.ai.providers import OpenAICompatibleProvider
from wb_web.ai.understanding import (
    analyze_review,
    has_explicit_positive_feedback,
    reply_addresses_concerns,
)
from wb_web.niches import get_packs_for_shop

GOLDEN = Path(__file__).with_name("golden_set.json")


def _profile(niche_ids: list[str], model: str) -> AIProfile:
    return AIProfile(
        provider="openai_compatible",
        model=model,
        style="friendly",
        response_length="short",
        max_characters=600,
        temperature=0.4,
        packs=tuple(get_packs_for_shop(niche_ids)),
        negative_requires_approval=False,
    )


def _would_fallback(text: str, context: ReviewContext, profile: AIProfile) -> tuple[bool, str]:
    """Replicate the engine's decision without a second provider call."""
    signals = analyze_review(context)
    missing_empathy = context.rating <= 2 and (
        not _APOLOGY_OR_REGRET.search(text) or _CHEERFUL_REPLY.search(text)
    )
    positive = has_explicit_positive_feedback(context)
    mishandles = signals.contains_negative_feedback and (
        not _APOLOGY_OR_REGRET.search(text)
        or (_CHEERFUL_REPLY.search(text) and not positive)
    )
    misses = not reply_addresses_concerns(text, signals)
    if contains_product_type(text) or missing_empathy or mishandles or misses:
        final = (
            _concern_fallback(context, profile)
            if signals.concerns
            else _universal_fallback(context, profile)
        )
        return True, validate_generated_text(final, profile, context)
    return False, validate_generated_text(text, profile, context)


async def main() -> None:
    base_url = os.environ["EVAL_BASE_URL"]
    key = os.environ["EVAL_KEY"]
    model = os.environ["EVAL_MODEL"]
    label = os.getenv("EVAL_LABEL", model)

    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    provider = OpenAICompatibleProvider(
        api_key=key, model=model, base_url=base_url,
        client=httpx.AsyncClient(timeout=90.0),
    )
    # Touch the prompt builder so its rules are exercised (not just imported).
    _ = build_instructions(_profile([], model))

    results = []
    for case in cases:
        context = ReviewContext(
            store_name="Тестовый магазин",
            rating=case["rating"],
            text=case["text"],
            product_name=None,
            vendor_code=None,
            pros=case.get("pros"),
            cons=case.get("cons"),
            buyer_question=case.get("buyer_question"),
        )
        profile = _profile(case.get("niche_ids") or [], model)
        row = {
            "id": case["id"], "note": case["note"], "rating": case["rating"],
            "expect_manual": case["expect_manual"],
        }
        try:
            raw = await provider.generate(context, profile)
            row["raw"] = raw.text
            row["latency_ms"] = raw.latency_ms
            row["tokens"] = raw.usage.total_tokens
            fallback, final = _would_fallback(raw.text, context, profile)
            row["fallback"] = fallback
            row["final"] = final
            row["ok"] = True
        except QualityGateError as exc:
            row.update(ok=False, fallback=False, final="", error=f"gate: {exc}")
        except Exception as exc:  # noqa: BLE001 — report provider failures per case
            row.update(ok=False, fallback=False, final="", error=f"{type(exc).__name__}: {exc}")

        row["manual_ok"] = requires_manual_review(context, profile) == case["expect_manual"]
        results.append(row)
        print(
            f"{row['id']:>2} | {'OK' if row.get('ok') else 'ERR':>3} | "
            f"fb={str(row.get('fallback'))[:1]} | manual={str(row['manual_ok'])[:1]} | "
            f"{row['note'][:38]:38} | {row.get('final', row.get('error', ''))[:70]}"
        )

    ok = [r for r in results if r.get("ok")]
    summary = {
        "label": label,
        "model": model,
        "cases": len(results),
        "ok": len(ok),
        "fallback_used": sum(1 for r in ok if r.get("fallback")),
        "gate_errors": sum(1 for r in results if str(r.get("error", "")).startswith("gate:")),
        "provider_errors": sum(1 for r in results if r.get("error") and not str(r["error"]).startswith("gate:")),
        "manual_review_matches": sum(1 for r in results if r["manual_ok"]),
        "avg_latency_ms": round(mean([r["latency_ms"] for r in ok if r.get("latency_ms")])) if ok else 0,
        "total_tokens": sum(r.get("tokens", 0) for r in ok),
    }
    print("\nSUMMARY " + json.dumps(summary, ensure_ascii=False))
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    out = Path(f"eval_results_{safe_label}.json")
    out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out.resolve()}")

    await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
