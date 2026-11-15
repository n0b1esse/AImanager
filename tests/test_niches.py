from wb_web.ai.composer import SAFETY_RULES, compose_prompt
from wb_web.niches import get_packs_for_shop, load_packs, needs_manual_review


def test_base_pack_loads() -> None:
    packs = load_packs()
    assert "base" in packs
    assert packs["base"].forbidden_claims  # sanity: safety content present


def test_empty_selection_returns_base() -> None:
    packs = get_packs_for_shop([])
    assert [p.id for p in packs] == ["base"]


def test_unknown_selection_returns_base() -> None:
    packs = get_packs_for_shop(["nonexistent-niche"])
    assert [p.id for p in packs] == ["base"]


def test_composer_safety_first() -> None:
    packs = get_packs_for_shop(None)
    prompt = compose_prompt("Всё супер!", rating=5, product_name="Тест", packs=packs)
    assert prompt.startswith(SAFETY_RULES.split("\n")[0])
    assert "Отзыв покупателя" in prompt
    assert "Тест" in prompt


def test_composer_includes_pack_rules() -> None:
    packs = get_packs_for_shop(None)
    prompt = compose_prompt("Товар бракованный", packs=packs)
    assert "Нишевый пак" in prompt
    assert "Универсальный пак" in prompt


def test_escalation_keyword_triggers_manual_review() -> None:
    packs = get_packs_for_shop(None)
    assert needs_manual_review("Хочу оформить возврат денег", packs)
    assert not needs_manual_review("Отличный товар, всем советую", packs)
