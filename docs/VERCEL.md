# Деплой AImanager на Vercel

## Что на Vercel, что не на Vercel

| Часть | Где живёт | Почему |
|---|---|---|
| Лендинг waitlist (`site/`) | **Vercel** (статика) | Один HTML+CSS, мгновенные деплои, бесплатный SSL |
| Веб-приложение (FastAPI + Jinja + worker) | **VPS** (`docker-compose.yml`) | Нужен Postgres + постоянный worker + Fernet-ключ; serverless «для демо» даст несохраняющуюся БД и вечные таймауты на WB sync |
| `AImanager` API | тот же VPS | там же живёт очередь публикации |

## Шаги (1–2 минуты, один раз)

1. Открыть <https://vercel.com/new> и войти через GitHub (аккаунт `n0b1esse`).
2. **Import Git Repository** → выбрать `n0b1esse/AImanager`.
3. На экране настроек проекта:
   - **Root Directory**: `site` (кнопка «Edit» → вписать `site`)
   - **Framework Preset**: Other (это статика, без сборки и без Install Command)
   - **Build Command**: пусто · **Output Directory**: `.`
4. Deploy → через ~20 секунд получите URL вида `aimanager-xxxx.vercel.app`.

## После деплоя

- [ ] Заменить `waitlist@example.com` в `site/index.html` (и в `mailto:` в скрипте внизу) на ваш реальный email → commit → Vercel пересоберёт сам.
- [ ] Заменить в футере и в `<meta description>` брендовые тексты при необходимости.
- [ ] В настройках проекта Vercel добавить свой домен (например `aimanager.kg`) → там же выдадут DNS-записи для регистратора. После этого — сразу в приложение по ссылке вида `https://<домен>/login`.

## Когда понадобится задеплоить и приложение

Если захотите полный продукт на Vercel — это возможно (FastAPI как Python serverless), но:
нужен managed Postgres (Neon / Vercel Postgres), обязателен `requirements.txt`, отключается воркер
(генерация только по кнопке, без авто-генерации в фоне), 10-секундный лимит функции на запрос (WB sync
может не успеть). Поэтому для пилота приложение держим на VPS, на Vercel — лендинг.
