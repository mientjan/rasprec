#!/bin/bash

# RaspRec Hardening Setup
# Optional but recommended for an UNATTENDED Pi at a remote location.
#   1. Hardware watchdog  -> auto-reboots the Pi if it hard-hangs
#   2. log2ram            -> writes logs to RAM, flushed periodically, to cut
#                            SD-card wear (SD corruption is the #1 killer of
#                            24/7 Raspberry Pis)

set -e

echo "=== RaspRec Hardening Setup ==="
echo ""

# Detect the boot config path (Bookworm+ uses /boot/firmware)
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_TXT=/boot/firmware/config.txt
elif [ -f /boot/config.txt ]; then
    CONFIG_TXT=/boot/config.txt
else
    CONFIG_TXT=""
fi

###############################################
# 1. Hardware watchdog
echo "[1/2] Hardware watchdog"
if [ -n "$CONFIG_TXT" ]; then
    if ! grep -q "^dtparam=watchdog=on" "$CONFIG_TXT"; then
        echo "  Enabling BCM watchdog in $CONFIG_TXT (needs reboot to load)"
        echo "dtparam=watchdog=on" | sudo tee -a "$CONFIG_TXT" > /dev/null
    else
        echo "  ✓ BCM watchdog already enabled in $CONFIG_TXT"
    fi
else
    echo "  WARNING: no config.txt found — skipping BCM watchdog overlay"
fi

# Tell systemd to pet the watchdog and reboot on hang
WD_CONF=/etc/systemd/system.conf.d/watchdog.conf
sudo mkdir -p /etc/systemd/system.conf.d
sudo tee "$WD_CONF" > /dev/null <<'EOF'
# RaspRec: reboot the Pi if systemd stops responding for 15s
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
EOF
echo "  ✓ systemd RuntimeWatchdogSec=15 configured ($WD_CONF)"

###############################################
# 2. log2ram (reduce SD-card wear)
echo ""
echo "[2/2] log2ram (SD-card wear reduction)"
if command -v log2ram &> /dev/null || [ -f /etc/log2ram.conf ]; then
    echo "  ✓ log2ram already installed"
else
    echo "  Installing log2ram..."
    curl -fsSL https://raw.githubusercontent.com/azlux/log2ram/master/install.sh | sudo bash || {
        echo "  WARNING: log2ram install failed — skipping (non-fatal)"
    }
fi

echo ""
echo "=== Hardening configured ==="
echo "Reboot to activate the hardware watchdog: sudo reboot"
echo ""
