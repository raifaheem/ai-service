# Fly.io Deployment Guide

Деплой `ai` сервиса на [Fly.io](https://fly.io) c managed-зависимостями:
**Upstash Redis** (free) + **Qdrant Cloud** (free). TLS, домен, авто-рестарт,
healthchecks — встроены в Fly. Compose-стек не используется (для dev/VPS
он остаётся на месте).

## Стоимость

- **Fly.io** требует привязку карты при создании аккаунта, но даёт **~$5/мес trial credit** который автоматически покрывает мелкие машины. `shared-cpu-1x` × 512MB обходится в ~$3.88/мес — укладывается в кредит, фактически **$0** для нашего размера.
- **Upstash Redis** free: 256MB, 10k commands/day — навечно free.
- **Qdrant Cloud** free: 1GB cluster — навечно free.
- **GHCR** для образов: free для публичных пакетов.

Итого: $0/мес пока укладываешься в кредит и free tier'ы.

---

## Phase 0 — Setup (~45 минут)

### 1. Fly.io аккаунт + CLI

1. Регистрация: [fly.io/sign-up](https://fly.io/app/sign-up) (Google/GitHub OAuth ок).
2. Привяжи карту: **Dashboard → Billing** (без неё не дают создавать машины).
3. Установи `flyctl` на Windows (PowerShell):

   ```powershell
   iwr https://fly.io/install.ps1 -useb | iex
   # Перезапусти терминал, проверь:
   flyctl version
   ```

4. Логин:
   ```powershell
   flyctl auth login
   ```

### 2. Upstash Redis (free)

1. [upstash.com](https://upstash.com/) → Sign up (GitHub OAuth).
2. **Create Database**:
   - **Name:** `health-ai-redis`
   - **Type:** Regional (free tier)
   - **Region:** `eu-central-1` (Frankfurt — близко к нашему Fly региону `fra`)
   - **Eviction:** `noeviction` (важно — иначе rate-limit ключи будут вытесняться)
3. После создания скопируй **`UPSTASH_REDIS_REST_URL`** — это не подходит! Нам нужен стандартный Redis protocol.
4. На странице базы — раздел **Connect** → **Redis CLI** показывает строку вида:
   ```
   redis-cli --tls -u redis://default:ПАРОЛЬ@eu1-talented-XXXX.upstash.io:6379
   ```
   Преобразуй в URL формат с TLS схемой:
   ```
   REDIS_URL=rediss://default:ПАРОЛЬ@eu1-talented-XXXX.upstash.io:6379/0
   ```
   (Заметь — `rediss://` с двумя `s`, не `redis://`. Upstash требует TLS.)

### 3. Qdrant Cloud (free)

1. [cloud.qdrant.io](https://cloud.qdrant.io/) → Sign up (GitHub OAuth).
2. **Clusters → Create Cluster**:
   - **Provider:** AWS (free тир только на AWS)
   - **Region:** `eu-central-1` (Frankfurt)
   - **Configuration:** **Free** (1GB RAM, 4GB disk)
   - **Name:** `health-ai`
3. Дождись `Healthy` (1-2 мин).
4. Кликни на cluster → вкладка **API Keys → Create**:
   - **Name:** `health-ai-server`
   - **Role:** Read & Write
   - Скопируй ключ.
5. На странице cluster есть **Endpoint URL** вида `https://xxx-xxx.eu-central-1.aws.cloud.qdrant.io:6333`.

   ```
   QDRANT_URL=https://xxx-xxx.eu-central-1.aws.cloud.qdrant.io:6333
   QDRANT_API_KEY=ТВОЙ_КЛЮЧ
   ```

### 4. OpenAI + остальные секреты

Для Fly нужны только два токена (Redis-пароль даёт сам Upstash; Grafana мы
не поднимаем). В PowerShell используем встроенный криптогенератор —
`openssl` не нужен:

```powershell
function Rand-Hex($n) {
    $bytes = New-Object byte[] $n
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    ([BitConverter]::ToString($bytes) -replace '-','').ToLower()
}

Write-Host "SERVICE_TOKEN=$(Rand-Hex 32)"
Write-Host "METRICS_SCRAPE_TOKEN=$(Rand-Hex 32)"
```

> Если у тебя установлен Git for Windows (или ты в WSL/Linux/macOS),
> альтернатива через стандартный `openssl rand -hex 32` тоже работает.

### 5. Создать Fly app

В корне проекта:

```powershell
flyctl apps create health-ai-service --org personal
# если имя занято — подбери своё и поправь app = "..." в fly.toml
```

`fly.toml` уже в репозитории — он опирается на это имя.

### 6. Загрузить секреты в Fly

```powershell
flyctl secrets set `
  OPENAI_API_KEY="sk-proj-..." `
  SERVICE_TOKEN="ТВОЙ_HEX" `
  METRICS_SCRAPE_TOKEN="ТВОЙ_HEX" `
  REDIS_URL="rediss://default:ПАРОЛЬ@eu1-XXX.upstash.io:6379/0" `
  QDRANT_URL="https://xxx.eu-central-1.aws.cloud.qdrant.io:6333" `
  QDRANT_API_KEY="ТВОЙ_QDRANT_API_KEY" `
  ALLOWED_ORIGINS="https://health-ai-service.fly.dev"
```

> `REDIS_PASSWORD` отдельно **не нужен** на Fly: переменная нужна только
> docker-compose'у (для `--requirepass` в Redis-контейнере), а приложение
> читает пароль из `REDIS_URL` напрямую. Пароль уже встроен в URL между
> `default:` и `@` — на Upstash это автогенеренная строка.
>
> Каждый `flyctl secrets set` триггерит рестарт. На первом запуске машин ещё нет — рестарт ничего не ломает.

Можно дополнительно (если нужны JWT и Telegram алерты — но Fly Telegram алерты не настраиваются в этом гайде, см. ниже):

```powershell
# JWT (если Laravel выпускает токены)
flyctl secrets set JWT_AUDIENCE="..." JWT_ISSUER="..." JWT_PUBLIC_KEY="$(Get-Content jwt_public.pem -Raw)"
```

### 7. Первый deploy (с reusable GHCR-образом)

Если у тебя уже есть тег `v1.0.0` в GHCR (от предыдущих попыток):

```powershell
flyctl deploy --image ghcr.io/raifaheem/ai-service:v1.0.0
```

Если тегов ещё нет — Fly соберёт из Dockerfile удалённо:

```powershell
flyctl deploy --remote-only
```

Жди ~3-5 минут. Watch logs:
```powershell
flyctl logs
```

После `Health check on port 8001 has passed` — рабочее.

### 8. Засидить knowledge base в Qdrant Cloud

Один раз. Самый простой путь — выполнить `seed_direct.py` локально с прод-секретами:

```powershell
# Временно положи прод-секреты в .env.docker (он gitignored), запусти seed контейнер с overlay:
# OR (проще): запусти Python скрипт локально:
$env:QDRANT_URL = "https://xxx.eu-central-1.aws.cloud.qdrant.io:6333"
$env:QDRANT_API_KEY = "ТВОЙ_QDRANT_API_KEY"
$env:REDIS_URL = "rediss://default:ПАРОЛЬ@eu1-XXX.upstash.io:6379/0"
$env:REDIS_PASSWORD = "ПАРОЛЬ"
$env:OPENAI_API_KEY = "sk-proj-..."
$env:SERVICE_TOKEN = "any-valid-token-not-empty"

python scripts/seed_direct.py --manifest data/knowledge_base/manifest.json
```

Или удалённо через Fly SSH:
```powershell
flyctl ssh console
# внутри machine:
python scripts/seed_direct.py --manifest data/knowledge_base/manifest.json
exit
```

### 9. Проверить

```powershell
curl.exe https://health-ai-service.fly.dev/health
```

Должно быть `{"status":"ok",...}`. TLS уже работает — Fly auto-provisions Let's Encrypt.

```powershell
$token = "ТВОЙ_SERVICE_TOKEN"
curl.exe -X POST https://health-ai-service.fly.dev/v1/chat `
    -H "X-Service-Token: $token" `
    -H "X-User-Id: smoke-test" `
    -H "Content-Type: application/json" `
    -d '{\"message\":\"что такое мигрень?\",\"locale\":\"ru\"}'
```

---

## Phase 1 — CI auto-deploy

Workflow `fly-deploy` уже встроен в [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) — он запускается параллельно с `deploy` (SSH), но активен только когда задан секрет `FLY_API_TOKEN`.

1. Создай API-токен:
   ```powershell
   flyctl tokens create deploy -x 9999h --name "github-actions"
   # Скопируй вывод (длинная строка fm2_...)
   ```

2. GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `FLY_API_TOKEN`
   - Value: токен из шага 1

3. Push нового тега → CI build → CI fly-deploy → новая версия в проде:
   ```powershell
   git tag v1.0.1
   git push origin v1.0.1
   ```

CI делает: build multi-arch → push в GHCR (включая `:latest`) → `flyctl deploy --image ghcr.io/raifaheem/ai-service:v1.0.1 --strategy rolling`. Fly делает rolling restart (старая машина живёт пока новая не пройдёт healthcheck).

---

## Phase 2 — Мониторинг

Fly даёт встроенный Prometheus endpoint и базовую observability:

- **Dashboard:** https://fly.io/apps/health-ai-service → Metrics — request rate, latency, memory, CPU.
- **Logs:** `flyctl logs` (стрим) или https://fly.io/apps/health-ai-service/monitoring.
- **Наш `/metrics`** доступен через `https://health-ai-service.fly.dev/metrics` с `Authorization: Bearer $METRICS_SCRAPE_TOKEN`.

Для дашбордов с нашими метриками (`healthai_intent_total`, RAG hit rate и т.д.):

- **Grafana Cloud free** (https://grafana.com/products/cloud/) — 10k Prometheus series free.
- Создай Grafana Agent либо встроенный Prometheus scrape job в Grafana Cloud, направленный на `health-ai-service.fly.dev/metrics` с Bearer-токеном.
- Импортируй наш дашборд: [ops/grafana/dashboards/health-ai.json](../ops/grafana/dashboards/health-ai.json).

Telegram-алерты через Alertmanager не работают на Fly (требует второй контейнер). Альтернативы:
- **Fly Notifier** webhook → Telegram (TG bot мост через [shoutrrr](https://containrrr.dev/shoutrrr/)).
- **Better Stack** free uptime monitoring + Telegram (5 monitors free).
- **UptimeRobot** + email/SMS.

---

## Phase 3 — Rollback

```powershell
# Список релизов
flyctl releases

# Откат на предыдущий
flyctl releases rollback <release_version>

# или через CI: workflow_dispatch с предыдущим тегом
gh workflow run deploy.yml -f tag=v1.0.0
```

---

## Phase 4 — Бэкапы Qdrant Cloud

Free tier Qdrant Cloud **не даёт** snapshot API. Бэкапы делаешь локальным запуском `qdrant_backup.py` против cloud-кластера:

```powershell
$env:QDRANT_URL = "https://xxx.eu-central-1.aws.cloud.qdrant.io:6333"
$env:QDRANT_API_KEY = "ТВОЙ_КЛЮЧ"
python scripts/qdrant_backup.py --download-to .\backups\qdrant\ --keep-last 7
```

Можно повесить на Windows Task Scheduler — daily. Но честно: KB пересоздаётся `seed_direct.py` за 2 минуты из git, бэкапы не критичны.

---

## Phase 5 — Custom домен (опционально)

Если хочешь не `health-ai-service.fly.dev`, а свой:

```powershell
flyctl certs add api.твой-домен.com
# Покажет какие DNS-записи добавить (CNAME / A + AAAA).
# После добавления — Fly сам получит Let's Encrypt сертификат.

# Не забудь обновить ALLOWED_ORIGINS и CADDY_DOMAIN не нужен:
flyctl secrets set ALLOWED_ORIGINS="https://api.твой-домен.com,https://health-ai-service.fly.dev"
```

---

## Troubleshooting

| Симптом | Что делать |
|---|---|
| `flyctl deploy` падает на healthcheck | `flyctl logs` — обычно `OPENAI_API_KEY` неверный, `REDIS_URL` без `@`, или `_validate_prod_safety` ругается. `flyctl secrets list` покажет какие секреты заданы (значения скрыты) |
| `Connection refused` to Redis | Проверь что REDIS_URL начинается с `rediss://` (TLS), не `redis://` — Upstash требует TLS |
| `Connection refused` to Qdrant | Проверь что QDRANT_URL включает порт `:6333` и схему `https://` (не http) |
| `seed_direct.py` падает с `403` | QDRANT_API_KEY не прописан или роль key'а только Read (нужна Read & Write) |
| Cold start ~10-15 сек на первом запросе после паузы | Машина была остановлена через auto_stop_machines. Для платных приложений сделай `min_machines_running = 1` в fly.toml — будет стоить ~$4/мес постоянно |
| `Out of memory` в логах | Подними `memory = "1gb"` в fly.toml (~$8/мес, выходит за trial credit) |
| `Health check failed` через 30s | `grace_period = "30s"` в fly.toml может быть мало для холодного старта — увеличь до `60s` |

---

## Что отличается от VPS / self-host пути

| | VPS (Oracle/Hetzner) | Self-host (PC) | **Fly.io** |
|---|---|---|---|
| Redis | в compose | в compose | **Upstash managed** |
| Qdrant | в compose | в compose | **Qdrant Cloud managed** |
| TLS | Caddy + Let's Encrypt | Caddy + Let's Encrypt | **Fly auto** |
| Reverse proxy | Caddy в compose | Caddy в compose | **Fly proxy** |
| Метрики | Prometheus + Grafana self-hosted | Prometheus + Grafana self-hosted | **Fly + Grafana Cloud free** |
| Auto-update | SSH from CI | Watchtower polls GHCR | `flyctl deploy --image` from CI |
| CI deploy job | `deploy` (SSH) | (skipped) | `fly-deploy` |
| Конфиг | `.env.production` + compose overlay | то же | `fly.toml` + `flyctl secrets` |
| Скейлинг | вертикальный (один хост) | вертикальный | **`fly scale count N`** — горизонтально |
| Регион | один | один | **`primary_region` + replicas** |

`docker-compose.prod.yml`, `Caddyfile`, `ops/prometheus/`, `ops/alertmanager/`, `ops/grafana/`, `scripts/server_bootstrap.sh` — **не используются на Fly** но остаются в репо для VPS/self-host путей.
