# health-ai-service — On-call Runbook

Lookup-first, copy-paste-friendly. Every scenario follows **Symptom → Check → Action → Postmortem hook**. Keep entries concise; link to source rather than duplicating it.

---

## 1. OpenAI is down or flapping

**Symptom:** `/v1/chat` returns 502/503 with `code` ∈ `{openai_rate_limit, openai_auth, openai_connection, openai_api_status, service_degraded}`. `/health` returns `degraded` with `openai_circuit: open`. Error rate climbs in Prometheus (`healthai_requests_total{status="5xx"}`).

**Check:**
```bash
# Live circuit state
curl -H "X-Service-Token: $TOKEN" http://localhost:8001/health | jq .checks

# Recent OpenAI usage (was the breaker actually flipped, or is it just slow?)
curl -H "X-Service-Token: $TOKEN" http://localhost:8001/metrics | grep healthai_circuit_breaker_state
```
- `openai_circuit_breaker_state{name="openai"} = 2.0` ⇒ open.
- 3+ failures in a 60s window trip the breaker; it stays open until `recovery_timeout=60s` of quiet.

**Action:**
- **If OpenAI status page shows incident:** wait. The breaker auto-transitions to `half_open` after 60s and closes on the first success. No manual intervention needed.
- **If quota exhausted (`openai_rate_limit`):** check OpenAI billing dashboard. Bumping `OPENAI_MAX_RETRIES` does **not** help — the breaker counts retries. Increase the org quota or rotate to a backup key.
- **If auth (`openai_auth`):** `OPENAI_API_KEY` was rotated or revoked. Update the secret and restart the deployment (`docker compose up -d --force-recreate ai`).
- **Force-close the breaker** (only if you've verified the upstream is healthy and just want to skip the recovery window):
  ```python
  # Inside the container
  python -c "import asyncio; from app.services.circuit_breaker import openai_breaker; asyncio.run(openai_breaker.reset())"
  ```

**Postmortem hook:** every breaker-open emits `Circuit breaker 'openai' opened after N failures` at WARN. Grep logs for the timestamp.

---

## 2. Redis OOM / unavailable

**Symptom:** `/health` returns `redis: unavailable` or `redis: timeout`. Subsequent chat requests **continue serving** (rate limit and idempotency fail open at the call sites; owner check on chat is fail-closed by design as of [audit fix](../app/routers/chat.py)). Triage sessions cannot be advanced (state lives in Redis only).

**Check:**
```bash
# Inside the redis container
redis-cli INFO memory | grep used_memory_human
redis-cli INFO clients
redis-cli DBSIZE
```
- Compare `used_memory_human` against the prod limit (`512M` per `docker-compose.prod.yml`).
- High `connected_clients` with low `DBSIZE` ⇒ leaked connections (check `REDIS_MAX_CONNECTIONS`).

**Action:**
- **Memory exhausted:** the eviction policy is `noeviction` by default — writes will fail. Switch temporarily: `redis-cli CONFIG SET maxmemory-policy allkeys-lru`. Schedule a real fix (lower `REDIS_TTL_SECONDS`, raise the memory cap).
- **Pruning hot keys:** `redis-cli --scan --pattern 'healthai:emb:*' | head -1000 | xargs redis-cli DEL` — embedding cache is regenerable, drop it first.
- **Restart:** `docker compose restart redis`. AOF replay rebuilds state. Conversation turns lost during downtime are unrecoverable (see Backup policy in CLAUDE.md — Redis is intentionally ephemeral).

**Postmortem hook:** circuit breaker, rate-limit, and audit log entries land their own writes; if those fail silently, application errors don't surface — search Prometheus for `healthai_requests_total` rate drops correlated with `/health` reporting redis: unavailable.

---

## 3. Qdrant unavailable / collection corrupt

**Symptom:** `/health` returns `qdrant: unavailable` or `qdrant_circuit: open`. RAG silently fails open (chat still answers, just without sources); `rag_used: false` on every response is the visible signal.

**Check:**
```bash
# Inside the ai container
curl -s http://qdrant:6333/collections/medical_articles | jq .result.status
# Should be "green"; "yellow" = optimization in progress, "red" = corrupt
```
- `total_chunks` from `/v1/rag/stats` (dev routes) zero on a collection that should have ~120 chunks ⇒ data loss.

**Action:**
- **Restart:** `docker compose restart qdrant`. Volume is persisted; collection survives.
- **Restore from snapshot** (only when collection is corrupt or wiped):
  ```bash
  # 1. List snapshots in /backups/qdrant/
  ls -lh ./backups/qdrant/
  # 2. Upload via Qdrant API
  curl -X POST "http://qdrant:6333/collections/medical_articles/snapshots/upload" \
    -F "snapshot=@./backups/qdrant/medical_articles-2026-04-21-...snapshot"
  # OR use the qdrant-client Python helper:
  python -c "from qdrant_client import QdrantClient; QdrantClient('http://qdrant:6333').recover_snapshot('medical_articles', 'file:///backups/qdrant/<file>')"
  ```
- **Re-seed from scratch:**
  ```bash
  docker compose --profile seed run --rm seed
  ```

**Postmortem hook:** the `qdrant_breaker` records into Redis `cb:qdrant`. If the breaker opened, Prometheus gauge `healthai_circuit_breaker_state{name="qdrant"}` jumps to 2.

---

## 4. Rate-limit storm / DDoS-like traffic

**Symptom:** Spike in `healthai_requests_total{status="429"}`. One or more `user_id`s hammering the chat endpoint. Redis CPU climbs (zset operations).

**Check:**
```bash
# Top users by current rate-limit zset cardinality
redis-cli --scan --pattern 'healthai:rl:*' | while read k; do
  echo "$(redis-cli ZCARD "$k") $k"
done | sort -rn | head -20
```

**Action:**
- **Single abusive user:** confirm the user_id, then drop their bucket: `redis-cli DEL healthai:rl:<user_id>`. They'll be back at zero immediately — escalate at the Laravel layer (block the user, not the AI service).
- **Distributed traffic spike:** lower `RATE_LIMIT_PER_MINUTE` temporarily via env-var override + restart. 5-minute window. Document in incident log.
- **At the edge:** if Laravel is the public ingress, rate-limit there. The AI service limit is per-user; without an upstream cap, an attacker with N stolen JWTs can N× the floor.

---

## 5. Idempotency cache fill

**Symptom:** Redis memory pressure correlated with `idem:*` keys.

**Check:**
```bash
redis-cli --scan --pattern 'healthai:idem:*' | wc -l
redis-cli --scan --pattern 'healthai:idem:*' | head -5 | xargs redis-cli MEMORY USAGE
```
Each entry is `~2-5 KB` (response body + fingerprint), 10-minute TTL. 100K entries = ~250 MB.

**Action:**
- **Drop the lot:** `redis-cli --scan --pattern 'healthai:idem:*' | xargs redis-cli DEL`. Clients lose dedup for in-flight retries; tolerable trade-off.
- **Shorten TTL:** edit `_IDEMPOTENCY_TTL_SECONDS` in [app/services/memory.py](../app/services/memory.py) and redeploy. Trade-off: smaller dedup window for retries.

---

## 6. JWT key rotation

**Symptom:** Planned rotation, or compromised key needing immediate replacement.

**Action:**
1. **Generate a new RS256 key pair** at the auth issuer (Laravel side).
2. **Stage the new public key** as a secondary `JWT_PUBLIC_KEY`. As of writing, the service supports a single key — implement key rotation on the issuer side first (publish `kid` in JWT header, point clients at the new key), then flip `JWT_PUBLIC_KEY` here.
3. **For service tokens** (the rotation IS supported): add the new value to the comma-separated `SERVICE_TOKEN`, redeploy. Once all consumers (Laravel, scrapers, CI) are using the new value, drop the old one and redeploy again.
4. **Verify** with `JWT_AUDIENCE` / `JWT_ISSUER` still set (in prod they're required). After rotation, restart, then `curl -H "Authorization: Bearer <new-token>" http://localhost:8001/health` should return 200.

---

## 7. Service won't start

**Symptom:** Container exits during lifespan startup. `docker compose logs ai` shows a `RuntimeError` or `ValueError`.

**Common causes:**
- **`RuntimeError: ... vector size N, but embedding model requires M`** — the Qdrant collection was created with a different embedding model. Either: (a) recreate the collection (`curl -X DELETE http://qdrant:6333/collections/medical_articles` + reseed), or (b) flip `OPENAI_EMBEDDING_MODEL` back to the original. Production fail-fast is intentional.
- **`ValueError: ENABLE_DEV_ROUTES must be false when APP_ENV=production`** — set `ENABLE_DEV_ROUTES=false` in `.env.production`. Dev routes are gated by both this and a default-False (L3 audit fix) so accidental opt-in is a clear signal, not silent acceptance.
- **`ValueError: REDIS_URL must include a password in production`** — production safety guard. Add `:password@` to the URL.
- **`ValueError: SERVICE_TOKEN contains placeholder value(s)`** — generate a real random token: `openssl rand -hex 32`.
- **`ValueError: JWT_AUDIENCE and JWT_ISSUER are required`** — when `JWT_PUBLIC_KEY` is set in prod, both must be set too. If you don't run direct-client JWT auth (only Laravel via service token), unset `JWT_PUBLIC_KEY`.
- **`ValueError: ALLOWED_ORIGINS=* is not allowed when APP_ENV=production`** — list the consumer origins explicitly.

---

## 8. Chat stream hangs

**Symptom:** Client opens `/v1/chat/stream`, gets `meta` event, no `delta` events for >30s, then connection times out.

**Check:**
- `docker compose logs ai | grep stream` — the stream-cancel hook logs `Client disconnected mid-stream, aborting <id>`. If you see those, the *client* dropped the connection (network blip, proxy timeout).
- Otherwise, the LLM call is hung. OpenAI breaker should open after 3 occurrences (`OPENAI_TIMEOUT_SECONDS=30`).

**Action:**
- Check OpenAI status page.
- Verify the proxy in front of the service doesn't buffer SSE — response headers include `X-Accel-Buffering: no`; if Nginx still buffers, add `proxy_buffering off;` for the location.

---

## Useful one-liners

```bash
# Tail the audit stream (Redis) — last 100 events
redis-cli XRANGE healthai:audit - + COUNT 100

# Drop a single user's conversation
redis-cli DEL healthai:conv:<id>:turns healthai:conv:<id>:owner healthai:conv:<id>:summary healthai:conv:<id>:summary_meta healthai:conv:<id>:meta

# Force-close all circuit breakers
docker compose exec ai python -c "import asyncio; from app.services.circuit_breaker import openai_breaker, qdrant_breaker; asyncio.run(asyncio.gather(openai_breaker.reset(), qdrant_breaker.reset()))"
```

---

## 9. Rollback to a previous release

**Symptom:** A freshly deployed tag misbehaves in prod — degraded `/health`, regression in answers, alert storm.

**Check:**
```bash
# Which tag is the running container built from?
ssh appuser@$VM 'cd /opt/health-ai && git describe --tags --exact-match HEAD 2>/dev/null || git rev-parse --short HEAD'

# What does the last known-good release look like?
git tag --sort=-v:refname | head -5
```

**Action:**
```bash
ssh appuser@$VM
cd /opt/health-ai
PREV=v1.0.0   # known-good
git fetch --tags
git checkout "$PREV"
export IMAGE_TAG="$PREV"
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production pull ai
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d
# Confirm health
curl -fsS http://localhost:8001/health | jq .status
```

The image at `$PREV` is still on GHCR (we never delete published tags), so this works as long as the tag exists. Total downtime ~5-10s for the container restart.

**Postmortem hook:** save `docker compose logs --tail=200 ai` from the broken release before restarting — otherwise the json-file driver rotates the evidence away.

---

## 10. Caddy can't get a TLS certificate

**Symptom:** `https://$CADDY_DOMAIN/` fails with `ERR_CERT_AUTHORITY_INVALID` or `NET::ERR_CERT_COMMON_NAME_INVALID`. `docker compose logs caddy` shows ACME challenge failure.

**Check:**
```bash
# Is port 80 reachable from the public internet? (Let's Encrypt HTTP-01)
curl -fsS http://$CADDY_DOMAIN/.well-known/acme-challenge/ping  # expects 404, not timeout

# Does the DuckDNS subdomain resolve to the VM's current public IP?
dig +short $CADDY_DOMAIN
curl -s ifconfig.me
```

**Action:**
- **Resolution mismatch:** run `/opt/health-ai/scripts/duckdns_update.sh` manually as appuser; check `/var/log/health-ai-duckdns.log`. If `KO`, `DUCKDNS_TOKEN` is invalid — regenerate at duckdns.org and update `.env.production`.
- **Port 80 not reachable:** UFW or Oracle Security List is blocking. `sudo ufw status verbose` should show 80/tcp ALLOW. Oracle Cloud also has its own ingress rules in the VCN — open 80 + 443 in the Security List for the VM's subnet.
- **Rate-limited by Let's Encrypt:** LE has a 5-failure/hour limit per hostname. Wait an hour, then retry. To force renewal: `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

**Postmortem hook:** the cert lives in the `caddy-data` volume — `docker volume inspect health-ai_caddy-data`. Don't delete it preemptively; renewal handles it within 30 days of expiry.

---

## 11. Telegram alerts not arriving

**Symptom:** Prometheus shows firing alerts (`ALERTS{alertstate="firing"}` non-empty) but no Telegram message. Or alerts arrived once and then stopped.

**Check:**
```bash
# Alertmanager-bot health
docker compose logs alertmanager-bot --tail=50

# Alertmanager itself
docker compose logs alertmanager --tail=50

# Is the bot still authorized? Send /start from the registered chat.
```

**Action:**
- **`401 Unauthorized` from Telegram API:** `TELEGRAM_BOT_TOKEN` was revoked or rotated. Generate a new token via @BotFather → update `.env.production` → `docker compose up -d alertmanager-bot`.
- **`chat not found`:** `TELEGRAM_CHAT_ID` is wrong (or the bot was removed from the chat). Send any message to the bot, then `curl https://api.telegram.org/bot$TOKEN/getUpdates` and read `result[*].message.chat.id`.
- **Silent for some severities:** check `ops/alertmanager/alertmanager.yml` routes — the `severity: warning` path has a 12h repeat_interval, so a single warning won't re-fire for 12 hours.
