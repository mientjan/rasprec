#!/bin/bash

# RaspRec Setup Script
# Sets up a stable, SECURE RTSP/WebRTC camera stream on a Raspberry Pi using
# MediaMTX (native rpiCamera source) + Tailscale (WireGuard VPN, no open ports).
#
# This replaces the old cvlc pipeline, which leaked memory and dropped the
# stream after hours/days.

set -eo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
# Preflight is read-only and happens before apt, user or service changes.
command -v python3 >/dev/null || { echo "Python 3 is required (install python3 first)."; exit 1; }
PREFLIGHT=$(python3 scripts/camera_setup.py preflight)
MTX_ARCH=$(printf '%s\n' "$PREFLIGHT" | sed -n '1p')
MEMORY_MAX=$(printf '%s\n' "$PREFLIGHT" | sed -n '2p')
CAMERA_ROOT=""
source scripts/camera-transaction.sh
umask 077
TMP_DIR=$(mktemp -d)
TRANSACTION=false
cleanup() {
    status=$?
    trap - EXIT
    if [ "$TRANSACTION" = true ]; then camera_rollback; fi
    rm -rf "$TMP_DIR"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "=== RaspRec Setup ==="
echo "Secure RTSP/WebRTC camera streaming for Raspberry Pi (MediaMTX + Tailscale)"
echo ""

# Check if this is an update or fresh install
if [ -f "/etc/systemd/system/mediamtx.service" ]; then
    echo "🔄 Existing installation detected - performing update/reinstall"
    echo "   Previous configurations will be backed up with timestamp"
else
    echo "🆕 Fresh installation detected"
fi
echo ""

# ---------------------------------------------------------------------------
# 1. Detect Raspberry Pi model + performance tier
# ---------------------------------------------------------------------------
PI_MODEL=$(tr -d '\0' < /proc/device-tree/model)
echo "Detected: $PI_MODEL"
case "$PI_MODEL" in
    *"Pi 5"*) PI_PERFORMANCE=software ;;
    *"Pi 4"*) PI_PERFORMANCE=maximum ;;
    *"Pi 3"*) PI_PERFORMANCE=high ;;
    *"Pi Zero 2"*) PI_PERFORMANCE=medium ;;
    *"Pi Zero"*) PI_PERFORMANCE=low ;;
    *) PI_PERFORMANCE=medium ;;
esac

# ---------------------------------------------------------------------------
# 2. Check camera availability + detect model
# ---------------------------------------------------------------------------
echo "Checking camera availability..."
CAMERA_TYPE="unknown"
CAM_TOOL=""
command -v rpicam-still &> /dev/null && CAM_TOOL="rpicam-still"
[ -z "$CAM_TOOL" ] && command -v libcamera-still &> /dev/null && CAM_TOOL="libcamera-still"

if [ -n "$CAM_TOOL" ]; then
    CAMERA_INFO=$($CAM_TOOL --list-cameras 2>/dev/null || true)
    if grep -q "Available cameras" <<< "$CAMERA_INFO"; then
        echo "✓ Camera detected via $CAM_TOOL"
        if echo "$CAMERA_INFO" | grep -qi "imx219"; then CAMERA_TYPE="v2"; echo "  Camera v2 (IMX219)"
        elif echo "$CAMERA_INFO" | grep -qi "ov5647"; then CAMERA_TYPE="v1"; echo "  Camera v1 (OV5647)"
        elif echo "$CAMERA_INFO" | grep -qi "imx477"; then CAMERA_TYPE="hq"; echo "  HQ Camera (IMX477)"
        else echo "  Camera detected (model unknown)"; fi
    else
        echo "WARNING: No cameras detected via $CAM_TOOL"
        echo "  Check: 1) camera cable  2) camera_auto_detect=1 in config.txt  3) reboot"
        read -p "Continue anyway? (y/N): " -n 1 -r; echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    fi
else
    echo "WARNING: Neither rpicam-still nor libcamera-still found."
    echo "Modern Raspberry Pi OS (Bookworm+) ships rpicam-apps by default."
    read -p "Continue anyway? (y/N): " -n 1 -r; echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
fi

# libcamera uses CMA, not the legacy gpu_mem split. Leave boot memory unchanged.
echo "Service memory limit: ${MEMORY_MAX} MB (override: CAMERA_MEMORY_MAX_MB)"

# ---------------------------------------------------------------------------
# 4. Choose streaming user
# ---------------------------------------------------------------------------
if [ -z "$CAMERA_USER" ]; then
    ACTUAL_USER="${SUDO_USER:-$USER}"
    echo ""
    echo "Camera streaming requires a user account (must be in the 'video' group)."
    echo "1. Use current user ($ACTUAL_USER)"
    echo "2. Create dedicated camera user"
    read -r -p "Choose (1/2) or enter custom username: " USER_CHOICE
    case "$USER_CHOICE" in
        1) CAMERA_USER="$ACTUAL_USER" ;;
        2) read -r -p "Enter username for dedicated camera user: " CAMERA_USER
           [ -z "$CAMERA_USER" ] && { echo "ERROR: Username required"; exit 1; } ;;
        "") echo "ERROR: Choose an option or enter a username"; exit 1 ;;
        *) CAMERA_USER="$USER_CHOICE" ;;
    esac
fi
[[ "$CAMERA_USER" =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || { echo "Invalid system username"; exit 1; }
echo "Streaming user: $CAMERA_USER"

if ! id "$CAMERA_USER" &>/dev/null; then
    echo "Creating user '$CAMERA_USER'..."
    sudo useradd -m -s /bin/bash "$CAMERA_USER"
fi
if getent group video > /dev/null 2>&1; then
    sudo usermod -a -G video "$CAMERA_USER"
    echo "✓ '$CAMERA_USER' is in the video group"
fi

# ---------------------------------------------------------------------------
# 5. Stream credentials (defense-in-depth on top of Tailscale)
# ---------------------------------------------------------------------------
echo ""
echo "Set a username/password required to VIEW the stream."
echo "(This is a second layer behind the Tailscale VPN.)"
IFS= read -r -p "  Stream username [view]: " STREAM_USER
STREAM_USER=${STREAM_USER:-view}
while true; do
    IFS= read -r -s -p "  Stream password: " STREAM_PASS; echo
    [ -n "$STREAM_PASS" ] && break
    echo "  Password cannot be empty."
done

# ---------------------------------------------------------------------------
# 6. Pick resolution / bitrate / fps for the detected hardware
# ---------------------------------------------------------------------------
case "$PI_PERFORMANCE" in
    software) WIDTH=1280; HEIGHT=720; BITRATE=2000000; FRAMERATE=24 ;;
    maximum) WIDTH=1920; HEIGHT=1080; BITRATE=3000000; FRAMERATE=30 ;;
    high)    if [ "$CAMERA_TYPE" = "v2" ] || [ "$CAMERA_TYPE" = "hq" ]; then
                 WIDTH=1920; HEIGHT=1080; BITRATE=3000000; FRAMERATE=24
             else WIDTH=1280; HEIGHT=720; BITRATE=2000000; FRAMERATE=24; fi ;;
    medium)  WIDTH=1280; HEIGHT=720;  BITRATE=2000000; FRAMERATE=24 ;;
    low)     WIDTH=720;  HEIGHT=480;  BITRATE=1000000; FRAMERATE=15 ;;
    *)       WIDTH=1280; HEIGHT=720;  BITRATE=2000000; FRAMERATE=24 ;;
esac
echo ""
echo "Stream settings: ${WIDTH}x${HEIGHT} @ ${FRAMERATE}fps, ${BITRATE} bps"

# Stage a pinned, checksum-verified release and safely serialized configuration.
echo "Installing dependencies..."
sudo apt-get update
sudo apt-get install -y curl tar ffmpeg bc python3-yaml
MTX_TAG=v1.21.0
ARCHIVE="mediamtx_${MTX_TAG}_${MTX_ARCH}.tar.gz"
BASE_URL="https://github.com/bluenviron/mediamtx/releases/download/${MTX_TAG}"
curl -fsSL "$BASE_URL/$ARCHIVE" -o "$TMP_DIR/$ARCHIVE"
curl -fsSL "$BASE_URL/checksums.sha256" -o "$TMP_DIR/checksums.sha256"
python3 scripts/camera_setup.py checksum "$TMP_DIR/$ARCHIVE" "$TMP_DIR/checksums.sha256"
tar -xzf "$TMP_DIR/$ARCHIVE" -C "$TMP_DIR" mediamtx
[ "$("$TMP_DIR/mediamtx" --version)" = "$MTX_TAG" ] || { echo "Unexpected binary version"; exit 1; }
export STREAM_USER STREAM_PASS WIDTH HEIGHT FRAMERATE BITRATE
python3 scripts/camera_setup.py render mediamtx.yml > "$TMP_DIR/mediamtx.yml"
sed -e "s/CAMERA_USER_PLACEHOLDER/${CAMERA_USER}/" \
    -e "s/CAMERA_MEMORY_MAX_PLACEHOLDER/${MEMORY_MAX}M/" mediamtx.service > "$TMP_DIR/mediamtx.service"

camera_snapshot
TRANSACTION=true
if [ "$OLD_ACTIVE" = true ]; then sudo systemctl stop mediamtx; fi
if [ "$LEGACY_ACTIVE" = true ]; then sudo systemctl stop rtsp-camera; fi
sudo install -d /usr/local/bin /usr/local/etc /etc/systemd/system
sudo install -m 0755 "$TMP_DIR/mediamtx" /usr/local/bin/mediamtx
sudo install -o root -g video -m 0640 "$TMP_DIR/mediamtx.yml" /usr/local/etc/mediamtx.yml
sudo install -m 0644 "$TMP_DIR/mediamtx.service" /etc/systemd/system/mediamtx.service
sudo systemctl daemon-reload
sudo systemctl reset-failed mediamtx || true
sudo systemctl enable mediamtx
sudo systemctl start mediamtx
python3 scripts/camera_setup.py probe "$TMP_DIR/mediamtx.yml"
unset STREAM_PASS
TRANSACTION=false
# Only retire the legacy service/watchdog after verified video; retain unit for rollback history.
if [ -f /etc/systemd/system/rtsp-camera.service ]; then
    sudo systemctl disable rtsp-camera
    # Snapshot intentionally belongs to the invoking user, not root.
    # shellcheck disable=SC2024
    if sudo crontab -l > "$TMP_DIR/old-crontab" 2>/dev/null; then
        (grep -v "camera-monitor.sh" "$TMP_DIR/old-crontab" || true) | sudo crontab -
    fi
fi
echo "MediaMTX is serving authenticated, decodable video."

# ---------------------------------------------------------------------------
# 10. Remote access mode — Tailscale (optional)
#
# The stream works on the local network without Tailscale. Tailscale is only
# needed to reach the camera from OUTSIDE the house. Set INSTALL_TAILSCALE=yes
# or =no beforehand to skip this prompt (useful for unattended installs).
# ---------------------------------------------------------------------------
echo ""
echo "=== Remote access ==="
echo "The stream already works on the local network."
echo "Tailscale is only needed to view the camera from outside the house."
echo ""
echo "  yes - install Tailscale (view from anywhere, no open router ports)"
echo "  no  - LAN only (view from devices on this same network)"
echo ""

if [ -n "$INSTALL_TAILSCALE" ]; then
    echo "INSTALL_TAILSCALE=$INSTALL_TAILSCALE (from environment)"
    TS_CHOICE="$INSTALL_TAILSCALE"
else
    read -p "Install optional Tailscale for direct remote access? (y/N): " -n 1 -r; echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then TS_CHOICE="yes"; else TS_CHOICE="no"; fi
fi

TAILSCALE_ENABLED=false
if [[ "$TS_CHOICE" =~ ^([Yy]|yes|YES|true|1)$ ]]; then
    chmod +x setup-tailscale.sh
    ./setup-tailscale.sh
    TAILSCALE_ENABLED=true
else
    echo ""
    echo "Skipped — running in LAN-only mode."
    echo "Direct viewing is local. For secure cloud recording without a VPN: ./setup-publisher.sh"
    echo "Run ./setup-tailscale.sh any time later to add remote access."
fi

# ---------------------------------------------------------------------------
# 11. Optional hardening (watchdog + SD-card wear reduction)
# ---------------------------------------------------------------------------
echo ""
if [ -n "$INSTALL_HARDENING" ]; then
    echo "INSTALL_HARDENING=$INSTALL_HARDENING (from environment)"
    HARDEN_CHOICE="$INSTALL_HARDENING"
else
    read -p "Apply reliability hardening (hardware watchdog + log2ram)? (Y/n): " -n 1 -r; echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then HARDEN_CHOICE="no"; else HARDEN_CHOICE="yes"; fi
fi

if [[ "$HARDEN_CHOICE" =~ ^([Yy]|yes|YES|true|1)$ ]]; then
    chmod +x setup-hardening.sh
    ./setup-hardening.sh
else
    echo "Skipped. Run ./setup-hardening.sh later (recommended for remote Pis)."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
LAN_IP=$(hostname -I | awk '{print $1}')
echo ""
if [ "$TAILSCALE_ENABLED" = true ]; then
    echo "=== Setup Complete (remote access enabled) ==="
else
    echo "=== Setup Complete (LAN-only mode) ==="
fi
echo "Stream path: /cam   (user: ${STREAM_USER})"
echo ""
echo "On the local network:"
echo "  RTSP:   rtsp://${STREAM_USER}:<password>@${LAN_IP}:8554/cam"
echo "  WebRTC: http://${LAN_IP}:8889/cam"
echo ""
if [ "$TAILSCALE_ENABLED" = true ]; then
    echo "From anywhere (over Tailscale) — see the URL printed by"
    echo "setup-tailscale.sh, e.g. rtsp://<tailscale-name>:8554/cam"
else
    echo "Direct remote viewing is not enabled. Cloud publishing is configured separately."
    echo "For secure cloud recording without Tailscale, run: ./setup-publisher.sh"
fi
echo ""
echo "Manage:  sudo systemctl status mediamtx"
echo "Logs:    sudo journalctl -u mediamtx -f"
echo "Diag:    ./diagnose.sh"
echo ""
echo "SECURITY WARNING: do not forward ports 8554/8889 on the router, and do not"
echo "expose them to the internet. RTSP has no transport encryption, and an"
echo "exposed camera port is found by internet scanners within hours."
echo "For cloud recording, use ./setup-publisher.sh (outbound, verified RTMPS)."
