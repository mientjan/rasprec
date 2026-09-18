#!/bin/bash
# Host-only renewal; no credentials in argv, and never enables plaintext ingest.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mountpoint -q /srv/rasprec || { echo "Required data volume is not mounted." >&2; exit 1; }
exec 9>/run/rasprec-certificates.lock
flock 9
export NVR_ENV_FILE=/etc/rasprec/nvr.env
cd "$ROOT"
compose=(docker compose --env-file "$NVR_ENV_FILE" -f compose.yml -f compose.aws.yml)
certificate=/etc/rasprec/letsencrypt/live/ingest/fullchain.pem
before=""
if [ -f "$certificate" ]; then before="$(sha256sum "$certificate" | cut -d' ' -f1)"; fi
if [ "${1:-}" = --renew ]; then
    "${compose[@]}" run --rm certbot renew --cert-name ingest --quiet
elif [ $# -eq 0 ]; then
    "${compose[@]}" run --rm certbot
else
    echo "Usage: certificates.sh [--renew]" >&2
    exit 1
fi
# Missing / expired certificates fail closed.
openssl x509 -checkend 0 -noout -in "$certificate"
after="$(sha256sum "$certificate" | cut -d' ' -f1)"
if [ "$before" != "$after" ] && [ -n "$("${compose[@]}" ps -q mediamtx)" ]; then
    # Reloading certificates by restart also invalidates existing publishing connections.
    "${compose[@]}" restart mediamtx
fi
