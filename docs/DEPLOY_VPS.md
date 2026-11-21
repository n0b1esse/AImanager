# Деплой Авто-Отвеччика на VPS (avto-otvetchik.com)

Прод-стек: `Caddy` (TLS + статика лендинга) → `FastAPI` (приложение) + `worker` + `Postgres`.
Всё в Docker Compose, один домен, HTTPS выпускается автоматически.

Файлы:
- `Caddyfile` — статика `site/` + прокси приложения, авто-TLS.
- `docker-compose.prod.yml` — `db` + `app` + `worker` + `caddy`.
- `.env.example` — шаблон секретов (копируется в `.env`, который не коммитится).

---

## 0. Что делает владелец (внешние действия)

| # | Действие | Где | Статус |
|---|---|---|---|
| 1 | **DNS**: A-записи `avto-otvetchik.com` и `www` → IP VPS (убрать парковку GoDaddy) | GoDaddy DNS | ❌ сейчас домен указывает на парковку (13.248.243.5 / 76.223.105.230) |
| 2 | **VPS**: доступ по SSH (IP, пользователь, ключ) + открытые 80/443 | хостинг | ❌ нет доступа у агента |
| 3 | **Почта**: рабочие `no-reply@` и `hi@` + SMTP-доступ | см. раздел 4 | ❌ не настроена |
| 4 | **OWNER_EMAILS**: email владельца для админки | `.env` | ❌ не задан |
| 5 | **Юридические страницы**: оферта, политика конфиденциальности | `site/` | ❌ нет (черновик — юристу) |
| 6 | **Цены** `PRICE_STARTER/PRO/BUSINESS` в валюте | `.env` | ❌ не заданы |

Без пунктов 1–3 сайт и вход по magic-link не заработают на домене.

---

## 1. VPS: подготовка (один раз)

Рекомендуется Ubuntu 24.04, 2 vCPU / 4 GB, 20+ GB диска.

> Если деплой идёт под **root**, воркфлоу сам поставит Docker/git и создаст `/opt/avto-otvetchik` — шаги ниже можно пропустить, достаточно добавить публичный ключ в `/root/.ssh/authorized_keys`.
> Для **не-root** пользователя Docker нужно поставить заранее и добавить пользователя в группу `docker`, иначе `docker compose` не запустится.

```bash
# Docker + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # перелогиньтесь после этого

# Порты
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Каталог проекта: `/opt/avto-otvetchik` (дальше — оттуда).

```bash
sudo mkdir -p /opt/avto-otvetchik
sudo chown $USER:$USER /opt/avto-otvetchik
git clone https://github.com/n0b1esse/AImanager.git /opt/avto-otvetchik
cd /opt/avto-otvetchik
```

## 2. DNS

В GoDaddy: DNS Management → удалить парковочные A-записи, добавить:

```
A     @     <IP_VPS>     (TTL 600)
CNAME www   avto-otvetchik.com
```

Проверка (после обновления DNS, до 5–15 мин):

```bash
dig +short avto-otvetchik.com        # должен быть IP_VPS
dig +short www.avto-otvetchik.com
```

Пока DNS не переключён, Caddy не сможет выпустить TLS — деплой можно начать, но `https` заработает после DNS.

## 3. Секреты (.env)

```bash
cp .env.example .env
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY='+Fernet.generate_key().decode())"
python3 -c "import secrets; print('SESSION_SECRET='+secrets.token_hex(32))"
```

Заполнить в `.env`:

```dotenv
APP_ENV=production
POSTGRES_PASSWORD=<стойкий пароль>
DATABASE_URL=postgresql+asyncpg://wbweb:wbweb@localhost:5432/wbweb
FERNET_KEY=<сгенерировано>
SESSION_SECRET=<сгенерировано>
SENTRY_DSN=<если есть>
OWNER_EMAILS=<ваш email, получит админку и 2FA>
ADMIN_SUPPORT_EMAILS=<опционально, read-only>
PUBLIC_BASE_URL=https://avto-otvetchik.com
MAIL_FROM=Авто-Отвеччик <no-reply@avto-otvetchik.com>
SMTP_HOST=<хост почтового провайдера>
SMTP_PORT=587
SMTP_USER=<логин>
SMTP_PASSWORD=<пароль>
SMTP_STARTTLS=true
PRICE_STARTER=<цена>
PRICE_PRO=<цена>
PRICE_BUSINESS=<цена>
```

⚠️ `SESSION_SECRET` — именно такое имя (в старом шаблоне было `SECRET_SESSION` — не сработало бы).
⚠️ `FERNET_KEY` нельзя менять после записи токенов клиентов: старые токены перестанут расшифровываться.

## 4. Почта (magic-link и письма)

Для входа по ссылке нужен реальный SMTP. Варианты:

- **Транзакционный провайдер** (рекомендую): Resend, Brevo, Mailgun, SendGrid — дают SMTP-логин/пароль и хороший deliverability.
- **GoDaddy Email / Workspace** — если уже куплена вместе с доменом.
- **Почта VPS напрямую** — не рекомендую (попадание в спам, нужны SPF/DKIM/DMARC, reverse DNS).

После подключения провайдера добавьте в DNS его SPF/DKIM-записи (обычно TXT), иначе письма уйдут в спам.

Проверка отправки (с VPS):

```bash
docker compose -f docker-compose.prod.yml run --rm app python -c \
  "import asyncio; from wb_web.core.mailer import send_magic_link; \
   print(asyncio.run(send_magic_link(to='you@example.com', \
   link='https://avto-otvetchik.com/auth/link?token=x', ttl_minutes=15)))"
```

(или просто запросить вход на странице `/login` и проверить ящик).

## 5. Деплой

```bash
cd /opt/avto-otvetchik
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Что произойдёт: соберётся образ, `alembic upgrade head` применит миграции, поднимутся `app`, `worker`, `caddy`; Caddy выпустит Let's Encrypt сертификаты.

Логи: `docker compose -f docker-compose.prod.yml logs -f app caddy`

### Авто-деплой из GitHub

`.github/workflows/deploy.yml` деплоится **автоматически при каждом push в `main`** (только после зелёных тестов), а также по тегу `v*` и кнопке «Run workflow». Воркфлоу сам клонирует репозиторий в `/opt/avto-otvetchik`, записывает `.env` из secrets, подтягивает код (`git pull`), пересобирает и перезапускает Compose, затем проверяет `/healthz`. Пока не заданы `SSH_HOST_VPS`/`SSH_USER_VPS`, деплой пропускается с предупреждением — CI остаётся зелёным. Ручной `.env` из раздела 3 в этом случае не нужен.

Secrets репозитория (GitHub → Settings → Secrets and variables → Actions):

| Secret | Значение | Кто заполняет |
|---|---|---|
| `SSH_HOST_VPS` | IP VPS | владелец |
| `SSH_USER_VPS` | пользователь SSH | владелец |
| `SSH_KEY_VPS` | приватный ключ целиком | уже в secrets |
| `SSH_PORT_VPS` | `22` | уже в secrets |
| `FERNET_KEY` / `SESSION_SECRET` / `POSTGRES_PASSWORD` | сгенерированы | уже в secrets |
| `OWNER_EMAILS` | email владельца | владелец |
| `MAIL_FROM` | `Авто-Отвеччик <no-reply@avto-otvetchik.com>` | по умолчанию в воркфлоу |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS` | данные почтового провайдера | владелец |
| `SENTRY_DSN`, `ADMIN_SUPPORT_EMAILS`, `PRICE_*` | опционально | владелец |

Публичный ключ для `authorized_keys` на VPS (владелец добавляет):

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPefEjLtYH1NfleR6qGSrjwmG0qMqn3m1METgfDl5Hx/ github-actions-avto-otvetchik
```

## 6. Проверка

```bash
curl -sS https://avto-otvetchik.com/healthz     # {"status":"ok",...}
curl -sS https://avto-otvetchik.com/readyz      # {"status":"ready"}
```

В браузере:
1. `https://avto-otvetchik.com/` — лендинг (статика).
2. `/login` → запросить ссылку на `OWNER_EMAILS` → письмо приходит → вход.
3. Первый вход владельца → `/app/admin/setup` → QR для TOTP (1Password/2FA) → админка.
4. Проверить, что `http://` редиректит на `https://` (Caddy делает сам).

## 7. Обновление и откат

```bash
# обновление
git pull
docker compose -f docker-compose.prod.yml up -d --build

# откат на предыдущий коммит
git checkout <sha>
docker compose -f docker-compose.prod.yml up -d --build
```

## 8. Бэкапы

```bash
# ежедневно (cron)
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U wbweb wbweb | gzip > /opt/backups/wbweb-$(date +%F).sql.gz
```

Хранить минимум 7 дней, раз в месяц — пробный restore в отдельную БД.

## 9. Чеклист перед публичным запуском

- [ ] DNS A `@` и `www` → IP VPS, парковка убрана.
- [ ] `https://avto-otvetchik.com` открывается, сертификат валиден.
- [ ] Письма magic-link доходят (не в спам), `hi@` принимает письма.
- [ ] `OWNER_EMAILS` задан, 2FA настроена.
- [ ] Оферта и политика конфиденциальности опубликованы (ссылки в footer).
- [ ] Цены заданы, тарифы на сайте актуальны.
- [ ] Бэкапы настроены, restore проверен.
- [ ] Sentry DSN (опционально), UptimeRobot на `/healthz`.
