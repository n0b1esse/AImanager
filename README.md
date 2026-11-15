# WB Review AI — веб-SaaS

> Продавцы Wildberries получают AI-ответы на отзывы через свой LLM-ключ.
> Этот репозиторий — новый веб-сервис. Десктопная версия (`../WBReviewAIManager`) — архив-референс, не развивается.

## Суть продукта

Продавец WB регистрируется → подключает WB-токен + свой AI-провайдер (OpenAI / OpenAI-compatible / Ollama) → сервис синхронизирует отзывы, генерирует индивидуальные ответы в стилистике ниши магазина и публикует их после апрува (полурежим) или автоматом. 7 дней триала → подписка по лимитам.

- **Аудитория:** сначала КГ/КЗ, затем РФ (Фаза 5).
- **Монетизация:** подписка Starter / Pro / Business; паки ниш — дифференциация тарифов.
- **LLM:** BYOK (ключ клиента). Managed-тариф — позже.
- **Модель:** только веб, соло-разработка.

Подробный план и риски — в Obsidian: `01_Projects/WBReviewAIManager/00_Дашборд.md` и в `docs/PLAN.md`.

## Стек

Python 3.12+ · FastAPI (async) · PostgreSQL + SQLAlchemy 2 async · Alembic · Jinja2 + HTMX + Tailwind · APScheduler · Fernet (секреты) · magic-link auth · Docker Compose + Caddy · Sentry.

## Быстрый старт (разработка)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest
uvicorn wb_web.main:app --reload
```

## Структура

```text
src/wb_web/            # приложение
  main.py              # FastAPI-приложение, /healthz
  ...                  # auth, billing, shops, reviews, niches (Фазы 1–3)
docs/                  # PLAN.md, FAZA_0_CHECKLIST.md, docs по модели
tests/                 # pytest
```

## Правила

- Секреты (WB-токен, AI-ключи клиента) — только Fernet-шифрованные; в логи/экспорт/БД в открытом виде не попадают.
- Публикация ответа — необратимое действие: review-gate, идемпотентность (unique review + publication marker + remote recheck), никакого слепого retry после неоднозначного timeout.
- Safety-правила промпта всегда выше любого нишевого пака.
