#!/bin/bash

# RaspRec NVR Setup
# Starts the recording stack (MediaMTX + the nvr sidecar) with docker compose.
# Run this on the machine that holds the disk — an x86 box or a Pi 4/5 with an
# SSD — NOT on the camera Pi itself.

set -e

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../docker" && pwd)"
cd "$DOCKER_DIR"

echo "=== RaspRec NVR ==="
echo "Continuous recording, motion clips and browsing for your camera streams."
echo ""

# Docker with the compose plugin is the only prerequisite
if ! command -v docker &> /dev/null; then
    echo "✗ Docker is not installed."
    echo "  Install it with: curl -fsSL https://get.docker.com | sh"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "✗ The docker compose plugin is missing (you may have the old docker-compose)."
    echo "  Install it with: sudo apt-get install -y docker-compose-plugin"
    exit 1
fi
echo "✓ Docker $(docker --version | awk '{print $3}' | tr -d ,)"

# First run: drop the templates in place and stop so they can be filled in
NEEDS_EDIT=false

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "✓ Created docker/.env from the template"
    NEEDS_EDIT=true
fi

if [ ! -f config/cameras.yml ]; then
    cp config/cameras.example.yml config/cameras.yml
    echo "✓ Created docker/config/cameras.yml from the template"
    NEEDS_EDIT=true
fi

if [ "$NEEDS_EDIT" = true ]; then
    echo ""
    echo "Before starting, edit these two files:"
    echo "  docker/.env                 — web password (NVR_PASS), camera passwords, DATA_DIR"
    echo "  docker/config/cameras.yml   — one entry per camera"
    echo ""
    echo "Then run this script again."
    exit 0
fi

# Refuse to run with the placeholder password from the template
if grep -q '^NVR_PASS=change-me$' .env || grep -q '^NVR_PASS=$' .env; then
    echo "✗ NVR_PASS in docker/.env is still unset or the default."
    echo "  Set a real password — the web UI is the only thing guarding your recordings."
    exit 1
fi

echo ""
echo "Building and starting the stack..."
docker compose up -d --build

echo ""
echo "Waiting for the recorder to come up..."
sleep 5

if [ -z "$(docker compose ps -q nvr)" ] || [ "$(docker inspect -f '{{.State.Running}}' "$(docker compose ps -q nvr)")" != "true" ]; then
    echo "✗ The nvr container is not running. Logs:"
    docker compose logs --tail 40 nvr
    exit 1
fi

WEB_PORT=$(grep -E '^WEB_PORT=' .env | cut -d= -f2)
WEB_PORT=${WEB_PORT:-8080}

echo "✓ Running"
echo ""
echo "=== Access ==="
echo "  Local:     http://localhost:${WEB_PORT}"

if command -v tailscale &> /dev/null && tailscale status &> /dev/null; then
    TS_NAME=$(tailscale status --json 2>/dev/null | grep -m1 '"DNSName"' | cut -d'"' -f4 | sed 's/\.$//')
    if [ -n "$TS_NAME" ]; then
        echo "  Tailnet:   http://${TS_NAME}:${WEB_PORT}"
        echo "             (set BIND_ADDR=0.0.0.0 in .env to reach it over the tailnet)"
    fi
fi

echo ""
echo "  Log in with NVR_USER / NVR_PASS from docker/.env."
echo ""
echo "⚠ Do NOT forward this port on your router. Use Tailscale for remote access."
echo ""
echo "Useful commands:"
echo "  docker compose -f docker/compose.yml logs -f nvr    # follow the recorder"
echo "  ./scripts/nvr-up.sh                                 # re-run after editing cameras.yml"
echo "  docker compose -f docker/compose.yml down           # stop everything"
echo ""
echo "Editing cameras.yml requires a full 'up' (not 'restart') so mediamtx.yml is regenerated."
