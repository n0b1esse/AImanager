# AImanager (WB Review AI)

> Продавцы Wildberries получают AI-ответы на отзывы через свой LLM-ключ.
> Этот репозиторий — новый веб-сервис. Десктопная версия (`../WBReviewAIManager`) — архив-референс, не развивается.

## Суть продукта

Продавец WB регистрируется → подключает WB-токен + свой AI-провайдер (OpenAI / OpenAI-compatible / Ollama) → сервис синхронизирует отзывы, генерирует индивидуальные ответы в стилистике ниши магазина и публикует их после апрува (полурежим) или автоматом. 7 дней триала → подписка по лимитам.

- **Аудитория:** сначала КГ/КЗ, затем РФ (Фаза 5).
- **Монетизация:** подписка Starter / Pro / Business; паки ниш — дифференциация тарифов.
- **LLM:** BYOK (ключ клиента). Managed-тариф — позже.
- **Модель:** только веб, соло-разработка.

Подробный план и риски — в Obsidian: `01_Projects/AImanager/00_Дашборд.md` и в `docs/PLAN.md`.

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
  core/                # config, session (cookie), security (Fernet/hash), logging (JSON)
  db/                  # base.py (async engine), models.py
  auth/                # magic-link service/routes + deps (current_user, tenant scope)
  billing/             # access gate (trial/active/grace/read_only), PaymentProvider + manual activation
  admin/               # owner-only v1: summary/users/activate/kill-switch/deactivate + audit log
  shops/               # tenant-scoped /shops (WB token encrypted)
  queue/               # claim/lease + idempotent publish protocol
  worker.py            # background worker skeleton
  main.py              # /healthz, /readyz, request-id middleware
docs/                  # PLAN.md, FAZA_0_CHECKLIST.md, SECURITY.md
alembic/               # migrations
tests/                 # pytest
```

## Правила

- Секреты (WB-токен, AI-ключи клиента) — только Fernet-шифрованные; в логи/экспорт/БД в открытом виде не попадают.
- Публикация ответа — необратимое действие: review-gate, идемпотентность (unique review + publication marker + remote recheck), никакого слепого retry после неоднозначного timeout.
- Safety-правила промпта всегда выше любого нишевого пака.
