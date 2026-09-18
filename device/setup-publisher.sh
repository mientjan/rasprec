#!/bin/bash
# Explicit opt-in setup; never modifies existing Tailscale or MediaMTX settings.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v systemctl >/dev/null || { echo "systemd is required." >&2; exit 1; }
sudo apt-get update
sudo apt-get install -y python3-av ca-certificates
sudo install -d -m 0755 /usr/local/lib/rasprec
sudo install -m 0755 "$HERE/scripts/publish.py" /usr/local/lib/rasprec/publish.py
sudo /usr/bin/python3 /usr/local/lib/rasprec/publish.py --configure
sudo install -m 0644 "$HERE/rasprec-publisher.service" /etc/systemd/system/rasprec-publisher.service
sudo systemctl daemon-reload
sudo systemctl enable rasprec-publisher
sudo systemctl restart rasprec-publisher
echo "Publisher installed. Check: sudo journalctl -u rasprec-publisher -n 20"
echo "The Pi initiates outbound TLS; no Tailscale or router port forwarding is required."
echo "A running service alone is not proof of delivery: verify this camera on the website."
