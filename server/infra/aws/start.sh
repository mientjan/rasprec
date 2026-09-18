#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mountpoint -q /srv/rasprec || { echo "Required data volume is not mounted." >&2; exit 1; }
[ "$(stat -c %a /etc/rasprec/nvr.env)" = 600 ] ||
    { echo "/etc/rasprec/nvr.env must have mode 600." >&2; exit 1; }
[ "$(stat -c %u /etc/rasprec/nvr.env)" = 0 ] ||
    { echo "/etc/rasprec/nvr.env must be owned by root." >&2; exit 1; }
export NVR_ENV_FILE=/etc/rasprec/nvr.env
cd "$ROOT"
compose=(docker compose --env-file "$NVR_ENV_FILE" -f compose.yml -f compose.aws.yml)
# quiet validation never prints expanded secrets
"${compose[@]}" config --quiet
mkdir -p /srv/rasprec/recordings /srv/rasprec/caddy /srv/rasprec/acme
install -d -m 0700 /etc/rasprec/letsencrypt
"${compose[@]}" build
"${compose[@]}" run --rm nvr-config
# Serve HTTP certificate challenges before starting TLS-only ingest.
"${compose[@]}" up -d --no-deps caddy
bash "$ROOT/infra/aws/certificates.sh"
"${compose[@]}" up -d --force-recreate
install -m 0644 "$ROOT/infra/aws/rasprec-certificates.service" /etc/systemd/system/
install -m 0644 "$ROOT/infra/aws/rasprec-certificates.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rasprec-certificates.timer
