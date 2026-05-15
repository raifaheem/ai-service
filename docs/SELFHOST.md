# Self-host Deployment (Windows / Docker Desktop / WSL2)

Полностью бесплатный путь развёртывания: всё крутится на твоём ПК, GitHub
Actions собирает образ → пушит в GHCR → Watchtower на твоей машине автоматом
подтягивает свежую версию. Никаких VM, никакой подписки, $0/мес.

Если ищешь VPS-путь (Oracle/Hetzner) — см. [DEPLOY.md](DEPLOY.md).

---

## Что тебе нужно

| Что | Зачем | Стоимость |
|---|---|---|
| ПК на Windows 10/11 с 16GB+ RAM | Хост для всех контейнеров | у тебя уже есть |
| Стабильный домашний интернет с публичным IP | Caddy получает Let's Encrypt по 80/443 | у тебя уже есть |
| Доступ к настройкам роутера | Проброс портов 80 и 443 на твой ПК | у тебя уже есть |
| Docker Desktop for Windows | Запуск всего стека | бесплатно для personal use |
| Аккаунт DuckDNS | Бесплатный публичный субдомен | бесплатно |
| Аккаунт OpenAI с API-key | LLM | от $5 минимум на pay-as-you-go |
| Telegram бот | Алерты | бесплатно |
| GitHub Personal Access Token | Watchtower pulls с GHCR | бесплатно |

**Если у тебя CGNAT** (мобильный интернет, некоторые провайдеры) — порты
пробросить не получится. Тогда в конце гайда переключайся на Cloudflare
Tunnel (нужен будет домен в Cloudflare — около $8/год).

---

## Phase 0 — Setup (одноразово, ~1 час)

### 1. Docker Desktop

1. Скачай с [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
2. Установи с галкой **WSL 2 backend** (рекомендуется).
3. Запусти Docker Desktop, дождись зелёного статуса в трее.
4. **Settings → Resources**: выдели минимум **6 GB RAM** и **4 CPU**.
5. **Settings → Resources → Advanced**: убедись что Docker Desktop стартует с
   Windows (`Start Docker Desktop when you sign in to your computer`).

Проверь в PowerShell:
```powershell
docker --version
docker compose version
```

### 2. DuckDNS субдомен

1. [duckdns.org](https://www.duckdns.org/) → Sign in (GitHub OAuth).
2. Создай субдомен — например `faheem-health-ai` → `faheem-health-ai.duckdns.org`.
3. Узнай свой публичный IP: открой в браузере [api.ipify.org](https://api.ipify.org).
4. Впиши этот IP в поле «current ip» рядом с твоим доменом → **Update IP**.
5. Скопируй **token** (UUID сверху страницы) — понадобится в `.env.production`.

### 3. Проброс портов на роутере

Каждый роутер разный, но логика одинаковая:

1. Войди в админку роутера — обычно [192.168.1.1](http://192.168.1.1) или
   [192.168.0.1](http://192.168.0.1). Логин/пароль — на наклейке роутера.
2. Найди раздел **Port Forwarding** / **NAT** / **Виртуальные серверы**.
3. **Узнай локальный IP твоего ПК** в PowerShell:
   ```powershell
   ipconfig | Select-String "IPv4"
   # Возьми тот что в диапазоне 192.168.x.x
   ```
4. Добавь два правила:

   | Внешний порт | Протокол | Внутренний IP | Внутренний порт |
   |---|---|---|---|
   | 80 | TCP | 192.168.1.X (твой ПК) | 80 |
   | 443 | TCP | 192.168.1.X (твой ПК) | 443 |

5. **Зафиксируй локальный IP твоего ПК** в DHCP роутера (раздел DHCP Reservations / Address Reservation) — чтобы он не сменился при перезагрузке.

**Проверь что порты открыты**: на сайте [canyouseeme.org](https://canyouseeme.org/)
введи **80**. Должно быть **Success** (после того как поднимем Caddy на Шаге 8).
Если показывает **Error: No connection** даже когда Caddy работает — значит
провайдер использует CGNAT, и нужен Cloudflare Tunnel вместо проброса портов
(см. конец документа).

### 4. Telegram бот

1. В Telegram → [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Введи имя и username (обязательно с `bot` в конце).
3. BotFather пришлёт **HTTP API token**. Сохрани.
4. Открой своего бота → отправь ему `/start`.
5. В браузере:
   ```
   https://api.telegram.org/bot<ТВОЙ_ТОКЕН>/getUpdates
   ```
   Найди `"chat":{"id":123456789` — это **TELEGRAM_CHAT_ID**.

### 5. GitHub Personal Access Token (для Watchtower)

Watchtower должен пуллить образы с GHCR. Создаём токен только для чтения пакетов:

1. [github.com/settings/tokens/new](https://github.com/settings/tokens/new) (classic).
2. **Note:** `health-ai watchtower`
3. **Expiration:** 90 days (продлевать каждые 3 месяца) или **No expiration** если не хочешь возиться.
4. **Scopes:** только **`read:packages`** — больше ничего!
5. **Generate token** → скопируй (показывается один раз).

### 6. Сгенерировать секреты

В PowerShell в папке проекта:

```powershell
# openssl приходит с Git for Windows. Проверь:
openssl version
# если нет — поставь Git for Windows: https://git-scm.com/download/win

Write-Host "SERVICE_TOKEN=$(openssl rand -hex 32)"
Write-Host "METRICS_SCRAPE_TOKEN=$(openssl rand -hex 32)"
Write-Host "REDIS_PASSWORD=$(openssl rand -hex 24)"
Write-Host "GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)"
```

Сохрани во временный файл.

### 7. Собрать `.env.production`

```powershell
Copy-Item .env.production.example .env.production
notepad .env.production
```

Заполни значения:

| Поле | Значение |
|---|---|
| `OPENAI_API_KEY` | твой `sk-proj-...` |
| `SERVICE_TOKEN` | сгенерированный в Шаге 6 |
| `METRICS_SCRAPE_TOKEN` | сгенерированный в Шаге 6 |
| `REDIS_URL` | `redis://:ТВОЙ_REDIS_PASSWORD@redis:6379/0` |
| `REDIS_PASSWORD` | сгенерированный в Шаге 6 |
| `ALLOWED_ORIGINS` | `https://faheem-health-ai.duckdns.org` |
| `CADDY_DOMAIN` | `faheem-health-ai.duckdns.org` |
| `DUCKDNS_DOMAIN` | `faheem-health-ai.duckdns.org` |
| `DUCKDNS_TOKEN` | из Шага 2 |
| `GRAFANA_ADMIN_PASSWORD` | сгенерированный в Шаге 6 |
| `TELEGRAM_BOT_TOKEN` | из Шага 4 |
| `TELEGRAM_CHAT_ID` | из Шага 4 |
| **`IMAGE_TAG=latest`** | **ВАЖНО для self-host — Watchtower следит за `latest`** |
| `GHCR_USERNAME` | твой GitHub login (например `raifaheem`) |
| `GHCR_TOKEN` | PAT из Шага 5 |

`JWT_*` оставь пустыми (используется только `SERVICE_TOKEN`).

`.env.production` уже в `.gitignore` — никогда не коммить.

### 8. Запустить стек

```powershell
# В корне проекта:
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d

# Подожди ~30 сек, проверь:
curl http://localhost:8001/health
```

**Особенность Windows**: `volumes: ./app:/app/app:ro` из dev-compose будет
работать как bind-mount — это OK, читается код из репо. На Windows иногда
требуется в Docker Desktop **Settings → Resources → File Sharing** добавить
папку с проектом.

### 9. Засидить knowledge base

Один раз — Qdrant пустой при первом старте:

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production --profile seed run --rm seed
```

### 10. Дождаться TLS-сертификата от Caddy

```powershell
docker compose logs -f caddy
# Жди строку "certificate obtained successfully" — обычно 10-60 сек.
# Если зависает на acme-challenge — проброс портов не работает.
# Ctrl+C когда увидишь сертификат.
```

### 11. Проверить из интернета

С телефона (мобильный интернет, не Wi-Fi!) открой:
```
https://faheem-health-ai.duckdns.org/health
```

Должно быть `{"status":"ok",...}` с зелёным замком в адресной строке.

Открой **Grafana**: `https://faheem-health-ai.duckdns.org/grafana/`  
Логин: `admin` / `GRAFANA_ADMIN_PASSWORD` из `.env.production`.

### 12. Тестовый чат

С локальной машины:

```powershell
$token = (Get-Content .env.production | Select-String '^SERVICE_TOKEN=').ToString().Split('=',2)[1]

curl.exe -X POST https://faheem-health-ai.duckdns.org/v1/chat `
    -H "X-Service-Token: $token" `
    -H "X-User-Id: smoke-test" `
    -H "Content-Type: application/json" `
    -d '{\"message\":\"что такое мигрень?\",\"locale\":\"ru\"}'
```

---

## Phase 1 — Auto-deploy через Watchtower

Это уже настроено в [docker-compose.prod.yml](../docker-compose.prod.yml):
сервис `watchtower` каждую минуту проверяет GHCR на новые версии
`ghcr.io/raifaheem/ai-service:latest` и перезапускает контейнер `ai` если
появилась новая.

**Релиз = git tag**:
```powershell
git add .
git commit -m "release: v1.0.0"
git push origin master:main
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions:
1. **build-and-publish** — собирает arm64+amd64 → пушит в GHCR с тегами
   `v1.0.0`, `1.0.0`, `1.0`, `1`, **и `latest`** (~5-8 мин).
2. **deploy** — пропускается (SSH_HOST не задан → no-op).
3. Через ~60 секунд после публикации в GHCR Watchtower на твоём ПК тянет
   `:latest` и перезапускает `ai` контейнер. Скачивание + рестарт ~30 сек.

**Проверь логи Watchtower** что он работает:
```powershell
docker compose logs --tail=50 watchtower
```

---

## Phase 2 — Авто-старт при загрузке Windows

Docker Desktop сам стартует с Windows (галка из Шага 1). Чтобы compose
автоматически поднимал твой стек когда Docker готов:

### Способ А: `restart: always` (уже в compose) + Docker Desktop autostart

Это уже работает: все контейнеры имеют `restart: always` в prod-overlay,
поэтому когда Docker Desktop запускается, контейнеры тоже стартуют. Но если
ты делал `docker compose down`, нужно один раз `up -d`.

### Способ Б: Task Scheduler — гарантированный `compose up` при логине

1. Открой **Task Scheduler** (Win+R → `taskschd.msc`).
2. **Create Task** (не Basic Task):
   - **Name:** `health-ai compose up`
   - **Triggers** → New → **At log on** (твой юзер) → **Delay task for 1 minute** (чтобы Docker успел запуститься)
   - **Actions** → New → **Start a program**:
     - Program: `powershell.exe`
     - Arguments: `-NoProfile -Command "cd 'C:\Users\Faheem\PycharmProjects\ai-service'; docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d"`
   - **Conditions:** сними галку «Start the task only if the computer is on AC power».
3. Save.

### Способ В: WSL2-only (продвинутый)

Если поставил Docker через WSL2 (без Docker Desktop), используй systemd
внутри WSL2 + автозапуск WSL через Task Scheduler.

---

## Phase 3 — DuckDNS auto-update

Если у тебя **dynamic IP** (большинство домашних провайдеров меняют IP
раз в несколько дней), нужно регулярно дёргать DuckDNS.

### Task Scheduler (Windows-native)

1. **Task Scheduler → Create Task**:
   - **Name:** `health-ai DuckDNS update`
   - **Triggers** → New → **Daily** → recur every **1 day** → **Repeat every 5 minutes** for a duration of 1 day.
   - **Actions** → New → **Start a program**:
     - Program: `powershell.exe`
     - Arguments: `-NoProfile -Command "Invoke-WebRequest -Uri 'https://www.duckdns.org/update?domains=faheem-health-ai&token=ТВОЙ_DUCKDNS_TOKEN&ip=' -UseBasicParsing | Out-Null"`
2. Save.

### Или через cron в WSL2

```bash
# wsl
crontab -e
# Добавь:
*/5 * * * * curl -s "https://www.duckdns.org/update?domains=faheem-health-ai&token=ТВОЙ_TOKEN&ip=" > /dev/null
```

---

## Phase 4 — Бэкапы Qdrant

Knowledge base в Qdrant — единственное что нельзя восстановить мгновенно
(хотя `seed_direct.py` пересоздаёт из git за ~2 минуты).

### Task Scheduler

Создай задачу `health-ai qdrant backup` — daily в 03:00:

- Program: `powershell.exe`
- Arguments: `-NoProfile -Command "cd 'C:\Users\Faheem\PycharmProjects\ai-service'; docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production --profile backup run --rm qdrant-backup"`

Снапшоты складываются в `./backups/qdrant/` и автоматически ротируются
(последние 7) — это уже встроено в [scripts/qdrant_backup.py](../scripts/qdrant_backup.py).

---

## Если у тебя CGNAT (порты не пробрасываются)

Если **canyouseeme.org** показывает No connection на порт 80 даже когда
Caddy работает — провайдер использует Carrier-Grade NAT. Тогда:

### Альтернатива: Cloudflare Tunnel

1. Купи дешёвый домен (~$8/год на NameCheap) или возьми бесплатный `.eu.org`
   (займёт неделю на одобрение).
2. Добавь домен в Cloudflare DNS (тоже бесплатно).
3. **Cloudflare Zero Trust** → Networks → Tunnels → Create a tunnel:
   - Type: Cloudflared
   - Скачай Windows-installer и запусти на своём ПК.
4. В тоннеле добавь Public Hostname:
   - Subdomain: `health-ai` (или любой)
   - Domain: `твой-cloudflare-домен.com`
   - Service: `http://localhost:8001` ← напрямую на ai, минуя Caddy
5. Убери `caddy` из `docker-compose.prod.yml` (или просто не пуллай) —
   Cloudflare сама терминирует TLS.
6. Останови проброс портов 80/443 на роутере — не нужен.

Финальный URL: `https://health-ai.твой-cloudflare-домен.com`. Cloudflare
сама делает TLS, защищает от DDoS, прячет твой публичный IP.

---

## Troubleshooting

| Симптом | Что делать |
|---|---|
| `curl localhost:8001/health` не работает | `docker compose ps` — проверь что `ai` running; `docker compose logs ai` |
| `https://...duckdns.org` зависает | Проверь проброс портов: [canyouseeme.org](https://canyouseeme.org/) → порт 80. Если No → CGNAT, нужен CF Tunnel |
| Caddy не получает сертификат | Скорее всего DuckDNS показывает старый IP. Открой [duckdns.org](https://www.duckdns.org/) и сверь IP с `api.ipify.org` |
| Watchtower не обновляет образ | `docker compose logs watchtower` — обычно неверный `GHCR_TOKEN` или истёк (зайди в settings/tokens, regenerate) |
| Telegram alerts не приходят | `docker compose logs alertmanager-bot` — проверь TOKEN и CHAT_ID |
| `ai` контейнер падает с `Cannot connect to redis` | Подожди 30 сек после `up -d` — redis healthcheck начинается раньше чем passes. Если не помогает — проверь `REDIS_PASSWORD` совпадает в `REDIS_URL` |
| Docker ест 90% RAM | В Docker Desktop Settings → Resources уменьши Memory limit |

Подробные сценарии инцидентов — [RUNBOOK.md](RUNBOOK.md).

---

## Что отличается от VPS-варианта

| | VPS (Oracle/Hetzner) | Self-host (твой ПК) |
|---|---|---|
| Деплой | GitHub Actions → SSH → `docker compose pull && up` | GitHub Actions → GHCR → Watchtower polls |
| Auto-start | `systemd` unit | Task Scheduler / Docker Desktop autostart |
| Бэкап | host `cron` daily | Task Scheduler daily |
| DNS update | host `cron` каждые 5 мин | Task Scheduler каждые 5 мин |
| Firewall | UFW на VM | Роутер + Windows Defender Firewall |
| Электричество | $0 (входит в VPS) | ~200-400 руб/мес у тебя |
| Доступность | ~99.9% (если VPS не падает) | ~99% (зависит от твоего интернета и того, что ПК включен) |

Когда захочешь переехать на VPS — следуй [DEPLOY.md](DEPLOY.md). Всё что
ты настроил (DuckDNS, Telegram, секреты) переносится 1:1. SSH-deploy job в
[deploy.yml](../.github/workflows/deploy.yml) сам подхватит когда добавишь
секреты `SSH_HOST` / `SSH_USER` / `SSH_PRIVATE_KEY` в GitHub.
