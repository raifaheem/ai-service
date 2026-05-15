#!/usr/bin/env bash
# server_bootstrap.sh — idempotent provisioning for a fresh Oracle Cloud
# Ampere A1 VM (Ubuntu 22.04 LTS, arm64). Safe to re-run after the first
# deploy: every step checks current state first.
#
# Run as root or with sudo. Reads /opt/health-ai/.env.production for the
# template values it needs (DUCKDNS_*, METRICS_SCRAPE_TOKEN).
#
#   curl -fsSL https://raw.githubusercontent.com/raifaheem/ai-service/main/scripts/server_bootstrap.sh | sudo bash
#
# After running:
#   1. scp .env.production root@VM:/opt/health-ai/.env.production
#   2. Add the SSH public key for GitHub Actions to /home/appuser/.ssh/authorized_keys
#   3. Push a v* tag — deploy.yml takes over.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/raifaheem/ai-service.git}"
APP_DIR="/opt/health-ai"
APP_USER="appuser"
APP_GROUP="appuser"

log() { echo "[bootstrap] $*"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "must run as root (try: sudo bash $0)" >&2
        exit 1
    fi
}

ensure_packages() {
    log "installing apt packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg ufw cron git jq

    # Docker CE from upstream — Ubuntu's docker.io is too old for compose v2's
    # `pull_policy` and modern healthcheck syntax.
    if ! command -v docker >/dev/null 2>&1; then
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
            | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
            > /etc/apt/sources.list.d/docker.list
        apt-get update -qq
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
}

ensure_firewall() {
    log "configuring UFW (22/80/443 in, default deny)"
    ufw --force reset >/dev/null
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp comment "ssh"
    ufw allow 80/tcp comment "http (caddy ACME challenge + redirect)"
    ufw allow 443/tcp comment "https (caddy)"
    ufw --force enable
}

ensure_appuser() {
    if ! id "$APP_USER" >/dev/null 2>&1; then
        log "creating $APP_USER"
        useradd -m -s /bin/bash "$APP_USER"
    fi
    usermod -aG docker "$APP_USER"
    # Ensure .ssh exists for GitHub Actions key install.
    sudo -u "$APP_USER" mkdir -p "/home/$APP_USER/.ssh"
    sudo -u "$APP_USER" chmod 700 "/home/$APP_USER/.ssh"
    sudo -u "$APP_USER" touch "/home/$APP_USER/.ssh/authorized_keys"
    sudo -u "$APP_USER" chmod 600 "/home/$APP_USER/.ssh/authorized_keys"
}

ensure_repo() {
    log "syncing repo into $APP_DIR"
    if [ ! -d "$APP_DIR/.git" ]; then
        git clone "$REPO_URL" "$APP_DIR"
    else
        git -C "$APP_DIR" fetch --tags --prune
    fi
    chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
}

ensure_scrape_token_file() {
    # The Prometheus scrape config reads /etc/prometheus/scrape_token from
    # inside its container, which is bind-mounted from
    # $APP_DIR/ops/prometheus/scrape_token. Write that file from the env var
    # so Prometheus's `credentials_file` finds it on the first start.
    local env_file="$APP_DIR/.env.production"
    if [ ! -f "$env_file" ]; then
        log "skipping scrape_token: $env_file not present yet (scp it after bootstrap)"
        return 0
    fi
    local token
    token="$(grep -E '^METRICS_SCRAPE_TOKEN=' "$env_file" | head -n1 | cut -d= -f2- || true)"
    if [ -z "$token" ] || [ "$token" = "REPLACE_ME" ]; then
        log "METRICS_SCRAPE_TOKEN not set — Prometheus scrape will return 401 until rotated"
        return 0
    fi
    local target="$APP_DIR/ops/prometheus/scrape_token"
    printf '%s' "$token" > "$target"
    chown "$APP_USER:$APP_GROUP" "$target"
    chmod 640 "$target"
    log "wrote $target"
}

ensure_cron() {
    log "installing host cron entries (Qdrant backup, DuckDNS update)"
    # Use a dedicated cron file so re-running this script doesn't duplicate
    # entries via `crontab -l | grep -v ... | crontab -` games.
    cat >/etc/cron.d/health-ai <<EOF
# Managed by scripts/server_bootstrap.sh — do not edit by hand.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

# Daily Qdrant snapshot at 03:00 UTC; keeps the last 7.
0 3 * * * $APP_USER cd $APP_DIR && /usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production --profile backup run --rm qdrant-backup >> /var/log/health-ai-backup.log 2>&1

# DuckDNS keepalive — Oracle gives a static IP, but if the VM is recreated
# the IP changes. Updating every 5 min costs ~2KB/day and removes a manual step.
*/5 * * * * $APP_USER /opt/health-ai/scripts/duckdns_update.sh >> /var/log/health-ai-duckdns.log 2>&1
EOF
    chmod 644 /etc/cron.d/health-ai
    touch /var/log/health-ai-backup.log /var/log/health-ai-duckdns.log
    chown "$APP_USER:$APP_GROUP" /var/log/health-ai-backup.log /var/log/health-ai-duckdns.log
}

ensure_systemd_unit() {
    log "installing systemd unit (auto-start compose stack on boot)"
    cat >/etc/systemd/system/health-ai.service <<EOF
[Unit]
Description=health-ai compose stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=true
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env.production
ExecStart=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d
ExecStop=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production down

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable health-ai.service
    log "to start: sudo systemctl start health-ai (run AFTER .env.production is in place)"
}

main() {
    require_root
    ensure_packages
    ensure_firewall
    ensure_appuser
    ensure_repo
    ensure_scrape_token_file
    ensure_cron
    ensure_systemd_unit
    log "DONE. Next steps:"
    log "  1. scp .env.production $APP_USER@\$(hostname -I | awk '{print \$1}'):$APP_DIR/.env.production"
    log "  2. Append your GitHub Actions SSH public key to /home/$APP_USER/.ssh/authorized_keys"
    log "  3. sudo systemctl start health-ai"
    log "  4. Once /health is ok, seed the KB:"
    log "     sudo -u $APP_USER docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production --profile seed run --rm seed"
}

main "$@"
