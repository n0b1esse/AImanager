# Деплой AImanager на Vercel

У нас два артефакта, и у них разные предикаты:

| Артефакт | Где живёт | Preset / Root |
|---|---|---|
| **Лендинг waitlist** — статика | Vercel (проект «landing») | preset: **Other**, Root Directory: **site** |
| **Веб-приложение** (FastAPI + Jinja + worker) | VPS (`docker-compose.yml`) — так задумано для прода. Но «посмотреть как выглядит» можно и на Vercel (проект «app») | preset: **FastAPI**, Root Directory: **./** |

Как в UI Vercel (то, что вы видели на скриншоте): на странице импорта
«Importing from GitHub» замените **Application Preset** с «FastAPI» на «Other»
и поставьте **Root Directory → site** — так статика задеплоится без сборки.

## Сценарий 1 — лендинг (рекомендую, 1 минута)

1. <https://vercel.com/new> → импортируйте `n0b1esse/AImanager`.
2. Application Preset: **Other**.
3. **Root Directory**: `site` (Edit → впишите `site`).
4. Build Command / Install Command / Output Directory: оставьте пустыми (или `.`).
5. Deploy → получите URL.

## Сценарий 2 — приложение (демо, чтобы увидеть UI)

FastAPI на Vercel — это Python serverless. Для демо нужно три вещи:

1. **Managed Postgres** — заводите бесплатную базу на [Neon](https://neon.tech) (2 минуты) или Vercel Postgres. Получаете `DATABASE_URL` вида `postgresql+asyncpg://user:pass@host/db?ssl=require`.
2. **Env-переменные в проекте Vercel** (Settings → Environment Variables):
   ```
   DATABASE_URL=postgresql+asyncpg://…
   APP_ENV=development            # dev-режим: magic-link показывается на странице входа, без SMTP
   FERNET_KEY=<Fernet.generate_key()>
   SESSION_SECRET=<random hex 32+ байт>
   PYTHONIOENCODING=utf-8
   ```
   **Не задавайте `OWNER_EMAILS`** — иначе любой зашедший увидит админку. Admin-панель на демо просто не нужна.
3. На странице импорта: preset **FastAPI**, Root Directory **./** (по умолчанию), Deploy.
   Vercel увидит `pyproject.toml` (`[tool.vercel] entrypoint = "wb_web.main:app"`) и `requirements.txt`, поставит зависимости.

Что будет работать на демо: вход (dev magic-link на странице), магазины, очередь, настройки ИИ, UI генерации/апрува/публикации (WB живой — если впишете свой токен, кнопка «Опубликовать» реально публикует), `/app/stats`, `/app/billing`, админка — отключена. Чего не будет: авто-генерации в фоне (worker только на VPS) и persistence на холодный старт, если функция уснёт надолго (данные в Neon сохраняются, просто первый запрос дольше).

**Не является продом:** на публичном демо ходит любой желающий; вчерашние данные демо не приватны для других посетителей — не загружайте настоящие токены. Для прода — VPS и свой домен.

## Проверка локально перед деплоем

```powershell
pip install -e ".[dev]"
python -m pytest   # 114 passed
```

То же приложение поднимается локально: `uvicorn wb_web.main:app --reload` → <http://127.0.0.1:8000>.
