#!/bin/bash

# RaspRec Tailscale Setup
# Installs Tailscale (WireGuard mesh VPN) so the camera can be reached securely
# from your phone WITHOUT forwarding any router ports. The stream stays invisible
# to the public internet — only devices on your tailnet can reach it.

set -e

echo "=== RaspRec Tailscale Setup ==="
echo "Secure remote access with zero open router ports."
echo ""

# Install Tailscale if not already present
if command -v tailscale &> /dev/null; then
    echo "✓ Tailscale already installed ($(tailscale version | head -1))"
else
    echo "Installing Tailscale (official repository)..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "✓ Tailscale installed"
fi

# Enable the daemon so it survives reboots
sudo systemctl enable --now tailscaled

echo ""
echo "Bringing Tailscale up with SSH enabled..."
echo "  --ssh  lets you SSH into this Pi from anywhere on your tailnet"
echo "         (remote admin / log viewing, still no open router ports)."
echo ""
echo "A browser login URL will be printed below. Open it on any device and"
echo "sign in with the SAME account you use on your phone."
echo ""

# --ssh: remote admin over the tailnet. --accept-dns keeps MagicDNS names working.
sudo tailscale up --ssh --accept-dns=true

echo ""
echo "=== Tailscale is up ==="

# Show the MagicDNS name + tailnet IP for building the viewing URL
TS_NAME=$(tailscale status --json 2>/dev/null | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 | sed 's/\.$//')
TS_IP=$(tailscale ip -4 2>/dev/null | head -1)

echo ""
echo "View the camera from your phone (on the same Tailscale account):"
if [ -n "$TS_NAME" ]; then
    echo "  RTSP (VLC app):   rtsp://${TS_NAME}:8554/cam"
    echo "  WebRTC (browser): http://${TS_NAME}:8889/cam"
fi
if [ -n "$TS_IP" ]; then
    echo "  or by IP:         rtsp://${TS_IP}:8554/cam"
fi
echo ""
echo "SECURITY REMINDERS:"
echo "  - Do NOT forward ports 8554/8889 on your mother's router. Not needed."
echo "  - Lock down your tailnet with an ACL so only YOUR devices reach this Pi:"
echo "      https://login.tailscale.com/admin/acls"
echo "  - Optionally disable key expiry for this device so it never needs"
echo "    re-authentication (Machines -> this Pi -> Disable key expiry)."
echo ""
