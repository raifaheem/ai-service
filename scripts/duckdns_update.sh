#!/usr/bin/env bash
# duckdns_update.sh — push current public IP to DuckDNS.
#
# Reads DUCKDNS_DOMAIN and DUCKDNS_TOKEN from /opt/health-ai/.env.production.
# Runs every 5 min via /etc/cron.d/health-ai (installed by server_bootstrap.sh).
# Output appended to /var/log/health-ai-duckdns.log.
#
# DuckDNS's docs recommend an empty `ip=` so their server auto-detects the
# source IP from the request — saves us calling an external "what's my IP".

set -euo pipefail

ENV_FILE="/opt/health-ai/.env.production"
if [ ! -f "$ENV_FILE" ]; then
    echo "[$(date -Iseconds)] $ENV_FILE missing — skip" >&2
    exit 0
fi

# shellcheck disable=SC1090
. "$ENV_FILE"

if [ -z "${DUCKDNS_DOMAIN:-}" ] || [ -z "${DUCKDNS_TOKEN:-}" ]; then
    echo "[$(date -Iseconds)] DUCKDNS_DOMAIN or DUCKDNS_TOKEN unset — skip" >&2
    exit 0
fi

# DuckDNS expects just the subdomain portion (e.g. `health-ai` for
# `health-ai.duckdns.org`). Strip the suffix if the operator left it in.
SUBDOMAIN="${DUCKDNS_DOMAIN%.duckdns.org}"

response="$(curl -fsS -m 10 "https://www.duckdns.org/update?domains=${SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=")"
echo "[$(date -Iseconds)] duckdns: $response"

# DuckDNS returns "OK" on success and "KO" on auth/domain error. Exit non-zero
# on KO so cron mail (if configured) flags the failure.
case "$response" in
    OK) exit 0 ;;
    *)  exit 1 ;;
esac
