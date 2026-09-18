#!/bin/bash

# RaspRec Hardening Setup
# Optional but recommended for an UNATTENDED Pi at a remote location.
#   1. Hardware watchdog  -> auto-reboots the Pi if it hard-hangs
#   2. log2ram            -> writes logs to RAM, flushed periodically, to cut
#                            SD-card wear (SD corruption is the #1 killer of
#                            24/7 Raspberry Pis)

set -eo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
umask 077
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
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
    python3 scripts/camera_setup.py watchdog "$CONFIG_TXT" > "$TMP_DIR/config.txt"
    if ! cmp -s "$CONFIG_TXT" "$TMP_DIR/config.txt"; then
        sudo cp -p "$CONFIG_TXT" "$CONFIG_TXT.backup.$(date +%Y%m%d_%H%M%S).$$"
        sudo cp "$TMP_DIR/config.txt" "$CONFIG_TXT"
    fi
    echo '  Watchdog configured in managed [all] block; verify after reboot with ./diagnose.sh.'

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
# A tmpfs size is a ceiling, not a reservation; still leave headroom for its contents.
AVAILABLE_KB=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
LOG_KB=$(sudo du -sk /var/log | awk '{print $1}')
if [ "${AVAILABLE_KB:-0}" -lt 131072 ] || [ "$LOG_KB" -gt 49152 ]; then
    echo '  Skipping log2ram: require 128 MB available RAM and at most 48 MB existing logs.'
elif [ -f /etc/log2ram.conf ]; then
    if [ -x /usr/local/bin/log2ram ] && systemctl cat log2ram.service >/dev/null 2>&1; then
        echo '  Existing log2ram found; preserving its configuration. Review SIZE manually.'
    else
        echo '  WARNING: incomplete existing log2ram installation; repair manually.'
    fi
else
    echo '  Installing complete log2ram distribution (64 MB log ceiling)...'
    if curl -fsSL https://github.com/azlux/log2ram/archive/refs/tags/1.7.2.tar.gz -o "$TMP_DIR/log2ram.tar.gz" \
        && tar -xzf "$TMP_DIR/log2ram.tar.gz" -C "$TMP_DIR"; then
        sed -i 's/^SIZE=.*/SIZE=64M/' "$TMP_DIR/log2ram-1.7.2/log2ram.conf"
        if (cd "$TMP_DIR/log2ram-1.7.2" && sudo bash install.sh) \
            && [ -x /usr/local/bin/log2ram ] && [ -f /etc/log2ram.conf ] \
            && systemctl cat log2ram.service >/dev/null 2>&1; then
            echo '  log2ram installed; reboot to activate. Verify memory and log usage afterwards.'
        else
            sudo systemctl disable log2ram.service log2ram-daily.timer 2>/dev/null || true
            echo '  WARNING: log2ram installation failed; disabled to avoid claiming hardening.'
        fi
    else
        echo '  WARNING: log2ram download failed; skipped.'
    fi
fi

echo ""
echo "=== Hardening configured ==="
echo "Reboot to activate the hardware watchdog: sudo reboot"
echo ""
