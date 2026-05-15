# Deployment Guide — health-ai-service

Step-by-step procedure for taking a fresh checkout to a running prod instance.
Architecture: Oracle Cloud Free Tier (Ampere A1, arm64) + DuckDNS + Caddy/Let's
Encrypt + self-hosted Prometheus/Grafana + Alertmanager → Telegram. Total
recurring cost: $0.

For day-2 ops (rollback, incident response, secret rotation) see
[RUNBOOK.md](RUNBOOK.md).

---

## One-time setup (~1 hour)

### 1. Oracle Cloud VM

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) (credit card needed for
   identity, no charges on Always Free).
2. **Compute → Instances → Create instance**:
   - Shape: **VM.Standard.A1.Flex** (Ampere ARM)
   - OCPU: **4**, memory: **24 GB** (max free allowance)
   - Image: **Ubuntu 22.04 LTS**
   - VCN: use default; ensure the public subnet allows inbound 22 / 80 / 443.
3. Download the SSH key during creation, save somewhere durable.
4. **Reserve a static public IP**: after the instance boots, go to the VM →
   Attached VNICs → Primary VNIC → IPv4 Addresses → Edit → **Reserved Public IP**.
   Without this the IP changes on every stop/start.
5. In the VCN security list, add ingress rules: TCP 80 from 0.0.0.0/0, TCP 443
   from 0.0.0.0/0. (Port 22 is allowed by default.)

### 2. DuckDNS

1. Sign in at [duckdns.org](https://www.duckdns.org) with a GitHub/Google account.
2. Reserve a subdomain (e.g. `health-ai-prod`).
3. Set its IP to the VM's reserved public IP from step 1.4.
4. Save the **token** (UUID near the top of the page) for `.env.production`.

### 3. Telegram bot

1. In Telegram, talk to [@BotFather](https://t.me/BotFather):
   - `/newbot` → choose a name and username
   - Save the HTTP API token.
2. Send any message to your new bot.
3. `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` → read
   `result[0].message.chat.id`. Save it for `.env.production`.

### 4. Generate secrets on your local machine

```bash
echo "SERVICE_TOKEN=$(openssl rand -hex 32)"
echo "REDIS_PASSWORD=$(openssl rand -base64 32 | tr -d /=+ | cut -c1-32)"
echo "METRICS_SCRAPE_TOKEN=$(openssl rand -hex 32)"
echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 24)"
```

### 5. Create `.env.production` locally

```bash
cp .env.production.example .env.production
# Fill in every REPLACE_ME from the values you generated.
```

Key fields: `SERVICE_TOKEN`, `METRICS_SCRAPE_TOKEN`, `REDIS_URL` (embed
`REDIS_PASSWORD`), `REDIS_PASSWORD`, `OPENAI_API_KEY`, `CADDY_DOMAIN`,
`DUCKDNS_DOMAIN`, `DUCKDNS_TOKEN`, `GRAFANA_ADMIN_PASSWORD`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALLOWED_ORIGINS` (include
`https://${CADDY_DOMAIN}`).

### 6. Bootstrap the VM

SSH in as `ubuntu` (Oracle's default user) and run the idempotent bootstrap:

```bash
ssh -i ~/.ssh/oracle_key.pem ubuntu@<VM_IP>
sudo bash -c "curl -fsSL https://raw.githubusercontent.com/raifaheem/ai-service/main/scripts/server_bootstrap.sh | bash"
```

This installs Docker + Compose plugin, configures UFW (22/80/443), creates the
`appuser` account, clones the repo into `/opt/health-ai`, installs cron jobs
(Qdrant daily backup, DuckDNS keepalive), and registers the
`health-ai.service` systemd unit. Re-running is safe.

### 7. Push `.env.production` to the VM

```bash
scp -i ~/.ssh/oracle_key.pem .env.production ubuntu@<VM_IP>:/tmp/
ssh ubuntu@<VM_IP> 'sudo mv /tmp/.env.production /opt/health-ai/.env.production \
    && sudo chown appuser:appuser /opt/health-ai/.env.production \
    && sudo chmod 600 /opt/health-ai/.env.production'
```

Then re-run the bootstrap once to render the Prometheus scrape token file:

```bash
ssh ubuntu@<VM_IP> 'sudo bash /opt/health-ai/scripts/server_bootstrap.sh'
```

### 8. Add the GitHub Actions deploy key

```bash
# Locally — generate a deploy keypair just for CI deploys.
ssh-keygen -t ed25519 -f ~/.ssh/health-ai-deploy -C 'github-actions-deploy' -N ''

# Append the public key to appuser's authorized_keys on the VM.
ssh ubuntu@<VM_IP> "sudo tee -a /home/appuser/.ssh/authorized_keys < <(cat)" < ~/.ssh/health-ai-deploy.pub
```

In the GitHub repo, **Settings → Secrets and variables → Actions → New
repository secret**:

| Name              | Value                              |
|-------------------|------------------------------------|
| `SSH_HOST`        | VM's reserved public IP            |
| `SSH_USER`        | `appuser`                          |
| `SSH_PRIVATE_KEY` | contents of `~/.ssh/health-ai-deploy` (private half) |

### 9. Start the stack and seed the KB

```bash
ssh ubuntu@<VM_IP>
sudo systemctl start health-ai
sudo systemctl status health-ai   # should be active (exited)

# Confirm /health is reachable on the VM
curl -fsS http://localhost:8001/health | jq .

# Seed the knowledge base into Qdrant (one-shot, idempotent).
sudo -u appuser bash -c 'cd /opt/health-ai && docker compose \
    -f docker-compose.yml -f docker-compose.prod.yml \
    --env-file .env.production \
    --profile seed run --rm seed'
```

When Caddy first starts, watch its logs (`docker compose logs -f caddy`) — the
Let's Encrypt HTTP-01 challenge takes 10-60s to complete and the cert lands in
the `caddy-data` volume.

### 10. Verify end-to-end

```bash
# From your laptop:
curl -fsS https://health-ai-prod.duckdns.org/health | jq .

curl -fsS https://health-ai-prod.duckdns.org/v1/chat \
    -X POST \
    -H "X-Service-Token: $SERVICE_TOKEN" \
    -H "X-User-Id: smoke-test" \
    -H "Content-Type: application/json" \
    -d '{"message":"что такое мигрень?","locale":"ru"}' | jq .

# Grafana at https://health-ai-prod.duckdns.org/grafana
# Login: admin / $GRAFANA_ADMIN_PASSWORD → dashboard "health-ai overview"
```

---

## Day-to-day: shipping a release

1. **Merge to `main`** via PR (CI must pass — pytest with 80% coverage, ruff,
   mypy, bandit, pip-audit).
2. **Tag and push**:
   ```bash
   git checkout main && git pull
   git tag v1.0.1
   git push origin v1.0.1
   ```
3. GitHub Actions takes over:
   - **build-and-publish** — builds the image for `linux/amd64` + `linux/arm64`,
     smoke-tests on the runner, pushes to GHCR.
   - **deploy** — SSHes into the VM, `git checkout $TAG`, `docker compose pull
     ai`, `up -d`, polls `/health` until `status=ok` (60s budget).
4. **Verify** by hitting the public URL or watching Grafana.

The whole flow takes ~5-8 minutes (most of it is the multi-arch build —
arm64 layers go through QEMU emulation).

### Manual redeploy (no new commit)

To redeploy an existing tag (e.g. after rotating a secret):
- GitHub UI → Actions → Deploy → **Run workflow** → enter the tag name.
- Or via gh CLI: `gh workflow run deploy.yml -f tag=v1.0.0`.

---

## Rollback

See [RUNBOOK §9](RUNBOOK.md#9-rollback-to-a-previous-release). Short form:

```bash
ssh appuser@<VM_IP>
cd /opt/health-ai
PREV=v1.0.0
git fetch --tags && git checkout "$PREV"
export IMAGE_TAG="$PREV"
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production pull ai
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d
```

---

## Secret rotation

All secrets live in `/opt/health-ai/.env.production` on the VM. Rotation is
manual edit + restart:

```bash
ssh appuser@<VM_IP>
cd /opt/health-ai
sudo nano .env.production    # edit the value
sudo systemctl restart health-ai
```

`SERVICE_TOKEN` supports zero-downtime rotation — list both old and new
comma-separated, deploy, switch consumers, deploy again with only the new.

`METRICS_SCRAPE_TOKEN` rotation requires also rewriting
`ops/prometheus/scrape_token`:
```bash
echo -n "$NEW_TOKEN" > /opt/health-ai/ops/prometheus/scrape_token
docker compose up -d prometheus
```

`TELEGRAM_BOT_TOKEN` rotation: just edit `.env.production` + restart
`alertmanager-bot`.

---

## What's intentionally NOT automated

- **First-time DuckDNS subdomain creation** — manual one-shot at duckdns.org.
- **Off-host backup transport** (S3/rclone) — out of scope for the zero-cost
  setup. KB is regenerable from the git repo via `seed_direct.py`; Redis is
  intentionally ephemeral (see CLAUDE.md "Backup policy").
- **Multi-region failover** — single Oracle VM. Downtime during deploy is
  ~5-10s (pull + restart).
- **Sentry / external error aggregation** — covered by Prometheus + alertmanager
  for now. Revisit at v1.1.
